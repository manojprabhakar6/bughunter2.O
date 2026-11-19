from collections.abc import Callable

from app.analyzers.analyzer_types import AnalysisResult, AnalyzerUnavailable
from app.analyzers.ast_analyzer import _analyze_python
from app.analyzers.c_analyzer import _analyze_c
from app.schemas import Language


def analyze_code(
    source: str,
    language: Language,
    program_goal: str = "",
    simulation_input: str = "",
    expected_output: str | None = None,
    *,
    compiler_locator: Callable[[str], str | None],
    sandbox_runner,
) -> AnalysisResult:
    if language == "python":
        return _analyze_python(
            source, program_goal, simulation_input, expected_output
        )
    if language == "c":
        compiler = compiler_locator("clang") or compiler_locator("gcc")
        return _analyze_c(
            source,
            program_goal,
            simulation_input,
            expected_output,
            compiler=compiler,
            sandbox_runner=sandbox_runner,
        )
    raise ValueError(f"Unsupported language: {language}")
