"""Compatibility facade for the modular analyzer package."""
import shutil

from app.analyzers.analyzer_types import AnalysisResult, AnalyzerUnavailable
from app.analyzers.ast_analyzer import (
    _analyze_python,
    _repair_incomplete_python_syntax,
    _repair_missing_line_closers,
    _repair_python_syntax_step,
)
from app.analyzers.bug_detector import (
    _repair_c_goal_logic,
    _repair_python_goal_logic,
    _repair_python_identity_comparisons,
)
from app.analyzers.c_analyzer import (
    _analyze_c,
    _c_findings,
    _repair_c_compiler_diagnostic,
    _repair_incomplete_c_printf,
    _run_c_compiler,
)
from app.analyzers.repair_engine import analyze_code as _dispatch_analysis
from app.analyzers.ruff_analyzer import (
    ANALYZER_PROCESS_TIMEOUT_SECONDS,
    RUFF_RULES,
    _diagnostics,
    _run_ruff,
)
from app.analyzers.source_utils import (
    _apply_source_edits,
    _ast_character_offset,
    _source_offset,
)
from app.c_sandbox import simulate_c_sandbox
from app.schemas import Finding, Language, SimulationResult
from app.simulator import simulate_python, verify_expected_output


def analyze_code(
    source: str,
    language: Language,
    program_goal: str = "",
    simulation_input: str = "",
    expected_output: str | None = None,
) -> AnalysisResult:
    return _dispatch_analysis(
        source, language, program_goal, simulation_input, expected_output,
        compiler_locator=shutil.which, sandbox_runner=simulate_c_sandbox,
    )
