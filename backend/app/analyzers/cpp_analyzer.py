"""Standalone C++ compiler diagnostics; the public API does not enable C++ yet."""
import subprocess
import tempfile
from pathlib import Path

from app.analyzers.analyzer_types import AnalyzerUnavailable
from app.analyzers.c_analyzer import _c_findings
from app.schemas import Finding


def analyze_cpp(source: str, compiler: str | None) -> list[Finding]:
    """Run compiler-only C++ syntax checks when explicitly called."""
    if compiler is None:
        raise AnalyzerUnavailable("C++ analysis requires Clang++ or G++.")
    try:
        with tempfile.TemporaryDirectory(prefix="bug-hunter-cpp-") as directory:
            source_path = Path(directory) / "submission.cpp"
            source_path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [compiler, "-Wall", "-Wextra", "-Wpedantic", "-fsyntax-only", str(source_path)],
                capture_output=True, text=True, check=False, timeout=30,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AnalyzerUnavailable("The C++ code analyzer could not complete.") from error
    findings = _c_findings(result.stderr)
    if result.returncode != 0 and not findings:
        raise AnalyzerUnavailable("The C++ compiler failed without a readable diagnostic.")
    return findings
