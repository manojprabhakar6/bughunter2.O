import json
import subprocess
import sys

from app.analyzers.analyzer_types import AnalyzerUnavailable

RUFF_RULES = "E,F,B,ASYNC,BLE"
ANALYZER_PROCESS_TIMEOUT_SECONDS = 30


def _run_ruff(*arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [sys.executable, "-m", "ruff", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=ANALYZER_PROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AnalyzerUnavailable(
            "The Python code analyzer could not complete."
        ) from error


def _diagnostics(output: str) -> list[dict]:
    try:
        diagnostics = json.loads(output)
    except json.JSONDecodeError as error:
        raise AnalyzerUnavailable(
            "The Python code analyzer returned invalid results."
        ) from error
    if not isinstance(diagnostics, list):
        raise AnalyzerUnavailable(
            "The Python code analyzer returned an unexpected result."
        )
    return diagnostics
