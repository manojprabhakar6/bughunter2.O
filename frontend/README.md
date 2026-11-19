# Bug Hunter frontend

A dependency-free frontend styled after the supplied Bug Hunter screenshots.
It includes email sign-in/registration, a separate dashboard overview and
Python/C analysis workspace, source-file selection and drag-and-drop, result
findings and code comparison, submission history, and API settings.
The analysis workspace accepts an optional short description of the program's
intended behavior. Reports retain this goal when supplied and include conservative Python logic
warnings alongside syntax/compiler diagnostics; such warnings are suggestions
to review, not guaranteed detections or automatic logic repairs.
The backend can optionally use an OpenAI or Anthropic model for goal-aware
full-source repairs when a provider, model, and server-side API key are
configured. Submitted source, goal, and diagnostics are then sent to that
provider. Source, goal, diagnostics, and optional test data are sent to the
provider. Every proposed Python rewrite is checked with Python parsing and Ruff;
C rewrites must pass GCC or Clang before they are shown. See the backend README
for server configuration.

Analysis reports show findings with line/column locations, the original-to-
corrected diff, and corrected code together. Clean results receive a positive
message; downloadable original and corrected files retain their `.py` or `.c`
extension. Clear missing delimiters, a comparison written with `=` where Python
suggests `==`, unterminated single-line strings, and a recognized
`int(inputenter ...)` Python input-prompt typo are corrected only when the entire
source parses afterward. Goal-guided Python checks can also correct a clear
dictionary-key typo, infer numeric input conversion, and guard an average over
an empty collection; inferred logic fixes are marked unverified until tested.
A C missing-statement-semicolon fix or clear undeclared-name typo is retained
only when GCC or Clang accepts the updated source. A standalone incomplete C
`printf` snippet can also be wrapped in a minimal program when the compiler
accepts it; other ambiguous errors stay visible for review. Python also applies Ruff's safe
automatic fixes. A comparison such as `value is 0` is corrected only when an
expected-output test is supplied and the restricted preview confirms the result.
Mismatched or unsupported previews leave candidate changes unapplied. Without
a test, identity-comparison warnings remain advisory. Unresolved diagnostics
and logic warnings remain visible; static analysis cannot guarantee that every
bug is detected or corrected.
Use **Continue in editor** to work from the corrected version, or
**Reanalyze original** to refresh an existing report while preserving its
original source. Individual saved analyses can be deleted from the history page.

Python and C programs are previewed automatically after server-side analysis.
Enter one value per `input()` call for Python, or standard input for C, in
**Optional preview inputs**. Enable expected-output verification to compare the
actual output against your expected output. Both languages run only inside
network-disabled Docker sandboxes with resource limits. Build the local images
`bug-hunter-python-sandbox:1` and `bug-hunter-c-sandbox:1` using the backend
README instructions. Without the Python image, supported Python syntax uses the
bounded AST preview; without the C image, C output is unavailable but compiler
diagnostics remain. Static analysis and sandboxed examples cannot guarantee
semantic correctness.

The editor uses Python/C syntax colors, line numbers, four-space Tab
indentation, matching bracket insertion, and language-aware Enter indentation
for both languages. It switches to plain text for large sources to stay
responsive. The selected language changes only through the language selector;
an uploaded file must match that selection. The bell opens recent analysis
notifications for the current browser session; selecting one reopens its saved
report. These are in-app notifications, not operating-system push alerts.

## Run

Start the backend from `backend` in a separate terminal:

```powershell
python -m uvicorn app.main:app --reload --port 8001
```

Then start this frontend from `frontend`:

```powershell
python server.py
```

Open `http://bug-hunter.localhost:5173/login`. This branded hostname resolves
to your own computer; it is not a public website and still uses a development
port. The frontend calls the FastAPI backend at `http://127.0.0.1:8001` by
default. Change the backend URL on the Settings page if it runs elsewhere.

Choose **Try the interactive demo** on the login or registration page for a
private, two-hour demo account with example Python and C analyses. Each visitor
gets a separate workspace; expired demo accounts and their submissions are
deleted when the backend starts, when a new demo is created, or when an expired
demo token is used.

On an analysis report, use **Download original** or **Download corrected** to
save standard UTF-8 `.py` or `.c` files. They can be opened in any text editor
or IDE.

Code uploads are limited to `.py` and `.c` files up to 5 MB. Analysis results
and preview outputs are saved in the account's history.
