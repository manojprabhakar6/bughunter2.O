import difflib
import hashlib
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.analyzer import AnalyzerUnavailable, analyze_code
from app.c_sandbox import simulate_c_sandbox
from app.database import connect, initialize_database
from app.schemas import (
    MAX_CODE_SIZE,
    MAX_EXPECTED_OUTPUT_LENGTH,
    MAX_PROGRAM_GOAL_LENGTH,
    MAX_SIMULATION_INPUT_LENGTH,
    AuthToken,
    CorrectedCodeInput,
    Finding,
    Language,
    LoginInput,
    RegisterInput,
    SimulationResult,
    Submission,
    SubmissionInput,
    UserPublic,
    validate_program_goal,
)
from app.simulator import simulate_python, verify_expected_output

PASSWORD_ITERATIONS = 310_000
SESSION_LIFETIME = timedelta(days=7)
DEMO_LIFETIME = timedelta(hours=2)
bearer_scheme = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    with connect() as connection:
        _cleanup_expired_demo_users(connection)
    yield


app = FastAPI(
    title="Bug Hunter API",
    description=(
        "Analyze Python and C source, compare safe corrections, "
        "and manage authenticated submissions."
    ),
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://bug-hunter.localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_expired_demo_users(connection: sqlite3.Connection) -> None:
    connection.execute(
        "DELETE FROM users WHERE is_demo = 1 AND demo_expires_at <= ?",
        (_now().isoformat(),),
    )


def _password_hash(password: str, salt: bytes) -> str:
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return derived_key.hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_token(
    connection: sqlite3.Connection,
    user_id: int,
    email: str,
    created_at: str,
    is_demo: bool = False,
    lifetime: timedelta = SESSION_LIFETIME,
) -> AuthToken:
    token = secrets.token_urlsafe(32)
    issued_at = _now()
    expires_at = issued_at + lifetime
    connection.execute(
        """
        INSERT INTO user_sessions (token_hash, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (_token_hash(token), user_id, issued_at.isoformat(), expires_at.isoformat()),
    )
    return AuthToken(
        access_token=token,
        expires_at=expires_at.isoformat(),
        user=UserPublic(
            id=user_id,
            email=email,
            created_at=created_at,
            is_demo=is_demo,
        ),
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Login is required to access this resource.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> dict[str, int | str]:
    if credentials is None:
        raise _unauthorized()

    with connect() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.email, users.created_at, users.is_demo,
                   user_sessions.expires_at
            FROM user_sessions
            JOIN users ON users.id = user_sessions.user_id
            WHERE user_sessions.token_hash = ?
            """,
            (_token_hash(credentials.credentials),),
        ).fetchone()

    if row is None:
        raise _unauthorized()
    if datetime.fromisoformat(row["expires_at"]) <= _now():
        if row["is_demo"]:
            with connect() as connection:
                connection.execute(
                    "DELETE FROM users WHERE id = ? AND is_demo = 1",
                    (row["id"],),
                )
        raise _unauthorized()
    return {
        "id": row["id"],
        "email": row["email"],
        "created_at": row["created_at"],
        "is_demo": bool(row["is_demo"]),
    }


def _comparison(original: str, corrected: str, language: str) -> str:
    extension = "c" if language == "c" else "py"

    def diff_lines(source: str) -> list[str]:
        return [
            line if line.endswith(("\n", "\r")) else f"{line}\n"
            for line in source.splitlines(keepends=True)
        ]

    original_lines = diff_lines(original)
    corrected_lines = diff_lines(corrected)
    if len(original_lines) == len(corrected_lines) and len(original_lines) >= 2_000:
        changed_lines = [
            index
            for index, (before, after) in enumerate(
                zip(original_lines, corrected_lines)
            )
            if before != after
        ]
        if not changed_lines:
            return ""

        context = 3
        hunks: list[list[int]] = []
        for index in changed_lines:
            start = max(index - context, 0)
            end = min(index + context + 1, len(original_lines))
            if hunks and start <= hunks[-1][1]:
                hunks[-1][1] = max(hunks[-1][1], end)
            else:
                hunks.append([start, end])

        def unified_range(start: int, end: int) -> str:
            length = end - start
            beginning = start + 1 if length else start
            return str(beginning) if length == 1 else f"{beginning},{length}"

        output = [f"--- original.{extension}\n", f"+++ corrected.{extension}\n"]
        for start, end in hunks:
            line_range = unified_range(start, end)
            output.append(f"@@ -{line_range} +{line_range} @@\n")
            for index in range(start, end):
                before = original_lines[index]
                after = corrected_lines[index]
                if before == after:
                    output.append(f" {before}")
                else:
                    output.extend((f"-{before}", f"+{after}"))
        return "".join(output)

    return "".join(
        difflib.unified_diff(
            original_lines,
            corrected_lines,
            fromfile=f"original.{extension}",
            tofile=f"corrected.{extension}",
        )
    )


def _submission_from_row(row: sqlite3.Row) -> Submission:
    return Submission(
        id=row["id"],
        filename=row["filename"],
        language=row["language"],
        program_goal=row["program_goal"],
        simulation_input=row["simulation_input"],
        expected_output=row["expected_output"],
        simulation=(
            SimulationResult(**json.loads(row["simulation_json"]))
            if row["simulation_json"]
            else SimulationResult(
                status="not_run",
                message=(
                    "No preview was saved for this older submission. Reanalyze "
                    "supported Python code to create one."
                ),
            )
        ),
        original_code=row["original_code"],
        corrected_code=row["corrected_code"],
        findings=[Finding(**finding) for finding in json.loads(row["findings_json"])],
        comparison=_comparison(
            row["original_code"],
            row["corrected_code"],
            row["language"],
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _analyze_or_raise(
    code: str,
    language: Language,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
):
    try:
        return analyze_code(
            code,
            language,
            program_goal,
            simulation_input,
            expected_output,
        )
    except AnalyzerUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


def _create_submission(
    code: str,
    filename: str,
    language: Language,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
    owner_id: int,
) -> Submission:
    result = _analyze_or_raise(
        code,
        language,
        program_goal,
        simulation_input,
        expected_output,
    )
    timestamp = _now().isoformat()
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO submissions (
                owner_id, filename, language, original_code, corrected_code,
                findings_json, program_goal, simulation_input, simulation_json,
                expected_output, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                filename,
                language,
                code,
                result.corrected_code,
                json.dumps([finding.model_dump() for finding in result.findings]),
                program_goal,
                simulation_input,
                json.dumps(result.simulation.model_dump()),
                expected_output,
                timestamp,
                timestamp,
            ),
        )
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _submission_from_row(row)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/auth/register",
    response_model=AuthToken,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: RegisterInput) -> AuthToken:
    salt = secrets.token_bytes(16)
    created_at = _now().isoformat()
    with connect() as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username, email, password_hash, password_salt, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload.email,
                    payload.email,
                    _password_hash(payload.password, salt),
                    salt.hex(),
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That login ID is already registered.",
            ) from error
        return _issue_token(
            connection,
            cursor.lastrowid,
            payload.email,
            created_at,
        )


@app.post(
    "/api/auth/demo",
    response_model=AuthToken,
    status_code=status.HTTP_201_CREATED,
)
def create_demo_session() -> AuthToken:
    created_at = _now().isoformat()
    demo_expires_at = (_now() + DEMO_LIFETIME).isoformat()
    email = f"demo-{secrets.token_urlsafe(12).lower()}@example.test"
    salt = secrets.token_bytes(16)
    with connect() as connection:
        _cleanup_expired_demo_users(connection)
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, email, password_hash, password_salt, created_at,
                is_demo, demo_expires_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (
                email,
                email,
                _password_hash(secrets.token_urlsafe(24), salt),
                salt.hex(),
                created_at,
                demo_expires_at,
            ),
        )
        c_demo_code = (
            "#include <stdio.h>\n\n"
            "int main(void) {\n"
            '    printf("Welcome to the C demo!\\n");\n'
            "    return 0;\n"
            "}\n"
        )
        samples = (
            (
                "demo_example.py",
                "python",
                "Print a welcome message for the Bug Hunter demo.",
                'import os\n\nprint("Welcome to the Bug Hunter demo!")\n',
                'print("Welcome to the Bug Hunter demo!")\n',
                [
                    Finding(
                        code="F401",
                        message="'os' imported but unused",
                        line=1,
                        column=8,
                        fixed=True,
                    )
                ],
            ),
            (
                "demo_example.c",
                "c",
                "Print a welcome message from a C program.",
                c_demo_code,
                c_demo_code,
                [],
            ),
        )
        for filename, language, program_goal, original, corrected, findings in samples:
            simulation = (
                simulate_python(corrected)
                if language == "python"
                else SimulationResult(
                    status="unsupported",
                    message=(
                        "C demo source is compiler-checked only; enable an "
                        "expected-output test to run it in the sandbox."
                    ),
                )
            )
            connection.execute(
                """
                INSERT INTO submissions (
                    owner_id, filename, language, original_code, corrected_code,
                    findings_json, program_goal, simulation_input, simulation_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    filename,
                    language,
                    original,
                    corrected,
                    json.dumps([finding.model_dump() for finding in findings]),
                    program_goal,
                    "",
                    json.dumps(simulation.model_dump()),
                    created_at,
                    created_at,
                ),
            )
        return _issue_token(
            connection,
            cursor.lastrowid,
            email,
            created_at,
            is_demo=True,
            lifetime=DEMO_LIFETIME,
        )


@app.post("/api/auth/login", response_model=AuthToken)
def login(payload: LoginInput) -> AuthToken:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, email, password_hash, password_salt, created_at, is_demo
            FROM users WHERE email = ? COLLATE NOCASE
            """,
            (payload.email,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "registration_required",
                    "message": "Email not found. Register to create an account.",
                    "redirectTo": "/register",
                },
            )

        salt = bytes.fromhex(row["password_salt"])
        candidate_hash = _password_hash(payload.password, salt)
        if not secrets.compare_digest(candidate_hash, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Login ID or password is incorrect.",
            )
        return _issue_token(
            connection,
            row["id"],
            row["email"],
            row["created_at"],
            is_demo=bool(row["is_demo"]),
        )


@app.get("/api/auth/me", response_model=UserPublic)
def current_user(
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> UserPublic:
    return UserPublic(
        id=user["id"],
        email=user["email"],
        created_at=user["created_at"],
        is_demo=user["is_demo"],
    )


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> Response:
    if credentials is None:
        raise _unauthorized()
    with connect() as connection:
        connection.execute(
            "DELETE FROM user_sessions WHERE token_hash = ?",
            (_token_hash(credentials.credentials),),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/submissions",
    response_model=Submission,
    status_code=status.HTTP_201_CREATED,
)
def create_submission(
    payload: SubmissionInput,
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> Submission:
    return _create_submission(
        payload.code,
        payload.filename,
        payload.language,
        payload.program_goal,
        payload.simulation_input,
        payload.expected_output,
        user["id"],
    )


@app.post(
    "/api/submissions/upload",
    response_model=Submission,
    status_code=status.HTTP_201_CREATED,
)
async def upload_submission(
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
    program_goal: Annotated[
        str,
        Form(
            alias="programGoal",
            min_length=3,
            max_length=MAX_PROGRAM_GOAL_LENGTH,
        ),
    ],
    simulation_input: Annotated[
        str,
        Form(
            alias="simulationInput",
            max_length=MAX_SIMULATION_INPUT_LENGTH,
        ),
    ] = "",
    expected_output: Annotated[
        str | None,
        Form(
            alias="expectedOutput",
            max_length=MAX_EXPECTED_OUTPUT_LENGTH,
        ),
    ] = None,
) -> Submission:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    extension = Path(filename).suffix.lower()
    languages: dict[str, Language] = {".py": "python", ".c": "c"}
    language = languages.get(extension)
    if language is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only Python .py and C .c files are accepted.",
        )

    try:
        content = await file.read(MAX_CODE_SIZE + 1)
    finally:
        await file.close()

    if len(content) > MAX_CODE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Source code must not exceed {MAX_CODE_SIZE} bytes.",
        )
    try:
        code = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source files must use UTF-8 encoding.",
        ) from error
    if not code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Source code cannot be empty.",
        )

    return _create_submission(
        code,
        filename,
        language,
        validate_program_goal(program_goal),
        simulation_input,
        expected_output,
        user["id"],
    )


@app.get("/api/submissions", response_model=list[Submission])
def list_submissions(
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> list[Submission]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM submissions
            WHERE owner_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (user["id"],),
        ).fetchall()
    return [_submission_from_row(row) for row in rows]


@app.get("/api/submissions/{submission_id}", response_model=Submission)
def get_submission(
    submission_id: int,
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> Submission:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ? AND owner_id = ?",
            (submission_id, user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Submission not found.")
    return _submission_from_row(row)


@app.put("/api/submissions/{submission_id}", response_model=Submission)
def replace_submission(
    submission_id: int,
    payload: SubmissionInput,
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> Submission:
    result = _analyze_or_raise(
        payload.code,
        payload.language,
        payload.program_goal,
        payload.simulation_input,
        payload.expected_output,
    )
    timestamp = _now().isoformat()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE submissions
            SET filename = ?, language = ?, original_code = ?, program_goal = ?,
                simulation_input = ?, expected_output = ?, simulation_json = ?,
                corrected_code = ?, findings_json = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (
                payload.filename,
                payload.language,
                payload.code,
                payload.program_goal,
                payload.simulation_input,
                payload.expected_output,
                json.dumps(result.simulation.model_dump()),
                result.corrected_code,
                json.dumps([finding.model_dump() for finding in result.findings]),
                timestamp,
                submission_id,
                user["id"],
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Submission not found.")
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
    return _submission_from_row(row)


@app.patch("/api/submissions/{submission_id}", response_model=Submission)
def update_corrected_code(
    submission_id: int,
    payload: CorrectedCodeInput,
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> Submission:
    timestamp = _now().isoformat()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT language, simulation_input, expected_output
            FROM submissions
            WHERE id = ? AND owner_id = ?
            """,
            (submission_id, user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Submission not found.")
        if row["language"] == "python":
            simulation = verify_expected_output(
                simulate_python(payload.corrected_code, row["simulation_input"]),
                row["expected_output"],
            )
        elif row["expected_output"] is not None:
            simulation = verify_expected_output(
                simulate_c_sandbox(
                    payload.corrected_code,
                    row["simulation_input"],
                ),
                row["expected_output"],
            )
        else:
            simulation = SimulationResult(
                status="unsupported",
                message=(
                    "C source is compiler-checked only unless an "
                    "expected-output test is enabled."
                ),
            )
        cursor = connection.execute(
            """
            UPDATE submissions
            SET corrected_code = ?, simulation_json = ?, updated_at = ?
            WHERE id = ? AND owner_id = ?
            """,
            (
                payload.corrected_code,
                json.dumps(simulation.model_dump()),
                timestamp,
                submission_id,
                user["id"],
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Submission not found.")
        row = connection.execute(
            "SELECT * FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
    return _submission_from_row(row)


@app.delete(
    "/api/submissions/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_submission(
    submission_id: int,
    user: Annotated[dict[str, int | str], Depends(get_current_user)],
) -> Response:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM submissions WHERE id = ? AND owner_id = ?",
            (submission_id, user["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Submission not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
