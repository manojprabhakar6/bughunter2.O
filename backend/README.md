# Bug Hunter backend

A Python API for authenticated code submissions, Python and C static analysis,
safe corrections where available, a restricted Python preview, and SQLite
persistence. Arbitrary submitted programs are never executed. API JSON uses
camelCase names to match the frontend.

## Setup

Run these commands from the `backend` directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

Python analysis uses Ruff, installed from `requirements.txt`. C analysis uses
GCC or Clang installed on the server and reports compiler diagnostics without
running or linking the uploaded program. If neither compiler is installed, C
submissions receive an explicit `503` response.

The SQLite database is created at `backend/data/bug_hunter.sqlite3`. Set
`BUG_HUNTER_DATABASE` to use a different database file. Versioned migrations
record schema changes in `schema_migrations`, preserve existing submissions,
and keep the language column open to additional languages. Interactive API
documentation is available at `http://127.0.0.1:8000/docs`.
The development frontend origins `http://localhost:5173` and
`http://127.0.0.1:5173` and
`http://bug-hunter.localhost:5173` are enabled for CORS.

## Accounts and authentication

- `POST /api/auth/register` — create an account with
  `{"email":"you@example.com","password":"at-least-8-characters"}`.
- `POST /api/auth/login` — return a bearer token for an existing account.
- `POST /api/auth/demo` — create a private, isolated two-hour demo session with
  example Python and C submissions. Expired demo accounts are deleted during
  startup, a new demo request, or an expired demo-token request.
- If the login ID does not exist, the API returns `404` with
  `detail.code: "registration_required"` and
  `detail.redirectTo: "/register"`.
  The frontend should navigate to its registration page and invite the user to
  register; the backend cannot navigate a browser page that it does not serve.
- `GET /api/auth/me` — get the authenticated account.
- `POST /api/auth/logout` — revoke the current token.

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes, never as plaintext.
Access tokens are opaque, stored hashed in the database, and expire after seven
days. Send them on protected requests as
`Authorization: Bearer <access_token>`. Each account can only view or modify
its own submissions.

## Code and submission API

- `POST /api/submissions` — analyze pasted source as JSON, including the
  required intended behavior, optional newline-separated `simulationInput`,
  and optional `expectedOutput` for Python or C, e.g.
  `{"code":"print('hello')","programGoal":"Print a greeting","language":"python","simulationInput":"","expectedOutput":"hello"}`.
- `POST /api/submissions/upload` — analyze a UTF-8 `.py` or `.c` file; the
  language is selected from its file extension. Include the required
  `programGoal`, and optionally `simulationInput` and `expectedOutput`
  multipart fields.
- `GET /api/submissions` and `GET /api/submissions/{id}` — list or retrieve
  saved code, findings, and the original/corrected unified diff. Response
  properties include `programGoal`, `originalCode`, `correctedCode`,
  `createdAt`, `updatedAt`, `comparison`, `simulationInput`, and `simulation`.
- `PUT /api/submissions/{id}` — replace the source and rerun analysis with its
  required `programGoal`.
- `PATCH /api/submissions/{id}` — save edited corrected code while retaining
  the original source.
- `DELETE /api/submissions/{id}` — delete a saved submission.

Python analysis checks syntax and Ruff `E`, `F`, and `B` diagnostics. It repairs
missing closing delimiters, simple unterminated single-line strings, and a
recognized `int(inputenter ...)` prompt typo only when the complete source
parses after repair. Source formatting and structure are otherwise preserved.
Unresolved linter findings and logic findings are returned for review. It also
reports conservative logic
warnings for identity comparisons with value literals, unreachable statements,
and a few omissions suggested by a goal mentioning an average, sorting, or an
extreme value. These are review prompts, not proof of incorrect behavior; the
identity-comparison correction is considered only when an expected-output test
is supplied. With that test, candidate Python corrections are accepted only
when the restricted preview matches the expected output; a mismatch or
unsupported preview rejects the candidate changes. Without an expected output,
identity-comparison findings remain advisory. If syntax errors prevent parsing,
logic checks are explicitly reported as skipped until the source can be parsed.
C analysis checks compiler diagnostics using `-fsyntax-only`; it reports issues
and preserves the submitted source without attempting general automatic
repairs. One constrained repair is available for a source containing only
comments and a single unterminated `printf` call. If an expected-output test is
provided, that candidate repair is accepted only when its output matches in the
isolated C sandbox; otherwise compiler success is the only repair check.

To enable C runtime tests, install Docker and build the local sandbox image from
the `backend` directory:

```powershell
docker build --tag bug-hunter-c-sandbox:1 --file sandbox/Dockerfile sandbox
```

The backend runs C only when `expectedOutput` is supplied and the sandbox image
is available. The container has no network, a read-only root filesystem, a
read-only mount containing only the temporary source file, no Linux
capabilities, a non-root user, 128 MiB memory, half a CPU, a 16-process limit,
compile/run time limits, and a 20,000-byte output cap. If Docker or the image is
unavailable, the expected-output test is reported as unverified and
sandbox-dependent repairs are rejected. The compiler-only check still works.
Container isolation reduces risk but is not a substitute for dedicated
multi-tenant sandbox infrastructure.

Each Python submission includes a bounded, AST-based preview supporting a
small set of expressions, assignments, conditionals, loops, built-ins, and
`print`/`input`. It does not use Python `exec` or `eval`; unsupported constructs
are reported instead of run. Supply one test value per `input()` call in
`simulationInput` and, optionally, the expected printed output in
`expectedOutput`; trailing newline differences are ignored. C programs are
compiler-checked first and run only for an explicitly enabled sandbox output
test. Static analysis and previews cannot guarantee semantic correctness or
find every logic bug.

Only Python and C are accepted. Source is limited to 5 MB; uploads must use
UTF-8 and have a `.py` or `.c` extension. Preview input is limited to 10,000
characters and expected output to 20,000 characters. Python execution uses
bounded steps and loop iterations; C execution uses the Docker sandbox limits
above.
