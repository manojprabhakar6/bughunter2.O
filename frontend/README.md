# Bug Hunter frontend

A dependency-free frontend styled after the supplied Bug Hunter screenshots.
It includes email sign-in/registration, a separate dashboard overview and
Python/C analysis workspace, source-file selection and drag-and-drop, result
findings and code comparison, submission history, and API settings.
The analysis workspace requires a short description of the program's intended
behavior. Reports retain this goal and include conservative Python logic
warnings alongside syntax/compiler diagnostics; such warnings are suggestions
to review, not guaranteed detections or automatic logic repairs.

Analysis reports show findings with line/column locations, the original-to-
corrected diff, and corrected code together. Clean results receive a positive
message; downloadable original and corrected files retain their `.py` or `.c`
extension. Simple missing delimiters, unterminated single-line strings, and a
recognized `int(inputenter ...)` Python input-prompt typo are corrected only
when the entire source parses afterward. A standalone incomplete C `printf`
snippet can be wrapped in a minimal program when GCC or Clang then accepts it;
other ambiguous errors stay visible for review. Python also applies Ruff's safe
automatic fixes. A comparison such as `value is 0` is corrected only when an
expected-output test is supplied and the restricted preview confirms the result.
Mismatched or unsupported previews leave candidate changes unapplied. Without
a test, identity-comparison warnings remain advisory. Unresolved diagnostics
and logic warnings remain visible; static analysis cannot guarantee that every
bug is detected or corrected.
Use **Continue in editor** to work from the corrected version, or
**Reanalyze original** to refresh an existing report while preserving its
original source. Individual saved analyses can be deleted from the history page.

Python reports include a restricted preview for supported syntax. Enter one
test value per `input()` call in **Optional preview inputs**. To verify Python
corrections, enable the expected-output check and enter the expected printed
output. The preview interprets only a documented subset with bounded steps and
output; unsupported constructs are reported instead of run. For C, enter
standard input and enable the expected-output check to run the compiled program
in the network-disabled Docker sandbox. C runtime tests require the local
`bug-hunter-c-sandbox:1` image; without it, the test is reported as unverified
and no sandbox-based correction is accepted. C is never run outside that
sandbox. Static analysis and sandboxed examples cannot guarantee semantic
correctness.

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
python -m uvicorn app.main:app --reload
```

Then start this frontend from `frontend`:

```powershell
python server.py
```

Open `http://bug-hunter.localhost:5173/login`. This branded hostname resolves
to your own computer; it is not a public website and still uses a development
port. The frontend calls the FastAPI backend at `http://127.0.0.1:8000` by
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
