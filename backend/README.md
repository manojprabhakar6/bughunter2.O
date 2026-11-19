# Bug Hunter backend

A Python API for authenticated code submissions, Python and C static analysis,
safe corrections where available, restricted Python and C output previews, and
SQLite persistence. Submitted programs run only inside bounded, network-disabled
Docker sandboxes. API JSON uses camelCase names to match the frontend.

Analysis code lives under `app/analyzers`: `repair_engine.py` dispatches work,
`ast_analyzer.py` handles Python parsing, `ruff_analyzer.py` runs Ruff, and
`bug_detector.py` contains goal-guided checks. `c_analyzer.py` handles C compiler
diagnostics; `source_utils.py` and `analyzer_types.py` hold shared helpers and
types. `ai_repair.py` supports optional OpenAI and Anthropic rewrites, disabled
unless a provider, model, and server-side API key are configured. AI proposals
are checked again with Python parsing/Ruff or the C compiler before they are
returned. `cpp_analyzer.py` is a standalone compiler checker;
the public API currently accepts Python and C only.

## Setup

Run these commands from the `backend` directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8001
```

Python analysis uses Ruff, installed from `requirements.txt`. C analysis uses
GCC or Clang installed on the server and reports compiler diagnostics without
running or linking the uploaded program. If neither compiler is installed, C
submissions receive an explicit `503` response.

The SQLite database is created at `backend/data/bug_hunter.sqlite3`. Set
`BUG_HUNTER_DATABASE` to use a different database file. Versioned migrations
record schema changes in `schema_migrations`, preserve existing submissions,
and keep the language column open to additional languages. Interactive API
documentation is available at `http://127.0.0.1:8001/docs`.

### Optional AI-assisted repair

Set these variables in the backend server environment to enable full-source,
goal-aware repair proposals. Choose `openai` or `anthropic`, set a model
available to that provider, and provide its API key using the corresponding
server-side key variable:

```powershell
$env:BUG_HUNTER_AI_PROVIDER = "openai"
$env:BUG_HUNTER_AI_MODEL = "your-enabled-model"
$env:OPENAI_API_KEY = "your-api-key"
python -m uvicorn app.main:app --reload --port 8001
```

For Anthropic, set `BUG_HUNTER_AI_PROVIDER` to `anthropic` and set
`ANTHROPIC_API_KEY`. Never put provider keys in frontend code or commit them.
When enabled, source, program goal, diagnostics, and any supplied preview input
or expected output are sent to the selected provider. If a proposal cannot be obtained, parsed, or accepted by the
language-specific checker, the regular local analysis result is used. Expected
output tests must also pass before an AI rewrite is accepted. Python code is
checked with Python parsing and Ruff, then shown only through the restricted
preview; C code must pass GCC or Clang, and runtime tests use the isolated C
sandbox.
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

- `POST /api/submissions` — analyze pasted source as JSON, including optional
  `programGoal`, newline-separated `simulationInput`, and optional
  `expectedOutput` for Python or C, e.g.
  `{"code":"print('hello')","programGoal":"Print a greeting","language":"python","simulationInput":"","expectedOutput":"hello"}`.
- `POST /api/submissions/upload` — analyze a UTF-8 `.py` or `.c` file; the
  language is selected from its file extension. `programGoal`,
  `simulationInput`, and `expectedOutput` are optional
  multipart fields.
- `GET /api/submissions` and `GET /api/submissions/{id}` — list or retrieve
  saved code, findings, and the original/corrected unified diff. Response
  properties include `programGoal`, `originalCode`, `correctedCode`,
  `createdAt`, `updatedAt`, `comparison`, `simulationInput`, and `simulation`.
- `PUT /api/submissions/{id}` — replace the source and rerun analysis with an
  optional `programGoal`.
- `PATCH /api/submissions/{id}` — save edited corrected code while retaining
  the original source.
- `DELETE /api/submissions/{id}` — delete a saved submission.

Python analysis checks syntax and Ruff `E`, `F`, `B`, `ASYNC`, and `BLE`
diagnostics, including common async misuse and overly broad exception handling.
It repairs clear missing delimiters, a comparison written with `=` where Python
suggests `==`, simple unterminated single-line strings, and a recognized
`int(inputenter ...)` prompt typo only when the complete source parses after
repair. Goal-guided checks can also correct a clear dictionary-key typo, infer
numeric input conversion, and guard an average over an empty collection. These
inferred logic fixes are marked unverified until tested. Source formatting and
structure are otherwise preserved.
Unresolved linter findings and logic findings are returned for review. It also
reports conservative logic
warnings for identity comparisons with value literals, unreachable statements,
and a few omissions suggested by a goal mentioning an average, sorting, or an
extreme value. These are review prompts, not proof of incorrect behavior; the
identity-comparison correction is considered only when an expected-output test
is supplied. With that test, candidate Python corrections are accepted only
when the preview matches the expected output; a mismatch or
unsupported preview rejects the candidate changes. Without an expected output,
identity-comparison findings remain advisory. If syntax errors prevent parsing,
logic checks are explicitly reported as skipped until the source can be parsed.
C analysis checks compiler diagnostics using `-fsyntax-only` and rechecks
compiler-confirmed repairs for a missing statement semicolon or a clear typo in
an undeclared identifier. One constrained repair is available for a source
containing only comments and a single unterminated `printf` call. Other
ambiguous diagnostics remain visible for review. C source is compiled and run
in the isolated sandbox automatically; if expected output is provided, a
candidate repair is accepted only when the sandbox output matches.

To preview program output, install Docker and build both sandbox images from
the `backend` directory:

```powershell
docker build --tag bug-hunter-c-sandbox:1 --file sandbox/Dockerfile sandbox
docker build --tag bug-hunter-python-sandbox:1 --file sandbox/Dockerfile.python sandbox
```

After syntax/static analysis and compiler validation, Python and C programs run
automatically inside their respective sandboxes to show standard output. Enter
standard input in the analysis form for programs that call `input()` or read
from stdin. The expected-output option compares the observed output with the
value you provide. Each container has no network, a read-only root filesystem,
a read-only source mount, no Linux capabilities, a non-root user, 128 MiB
memory, half a CPU, a 16-process limit, execution time limits, and a 20,000-byte
output cap. If Docker or an image is unavailable, Python falls back to its
bounded AST preview for supported constructs; C reports that runtime preview
is unavailable while retaining compiler diagnostics.
Container isolation reduces risk but is not a substitute for dedicated
multi-tenant sandbox infrastructure.

Python submissions are syntax-checked and linted before preview. With the
Python sandbox image installed, the complete corrected program runs in the
isolated container. Without it, the bounded AST preview supports common
expressions, assignments, simple functions, conditionals, loops, built-ins, and
`print`/`input`; unsupported constructs are reported instead of run. C is
compiler-checked and then run automatically in its sandbox. Supply one test
value per `input()` call in `simulationInput` or standard input for C. Optionally
supply `expectedOutput` to compare the observed result; trailing newline
differences are ignored. Static analysis and sandbox previews cannot guarantee
semantic correctness or find every logic bug.

Only Python and C are accepted. Source is limited to 5 MB; uploads must use
UTF-8 and have a `.py` or `.c` extension. Preview input is limited to 10,000
characters and expected output to 20,000 characters. Python execution uses
bounded steps and loop iterations; C execution uses the Docker sandbox limits
above.
