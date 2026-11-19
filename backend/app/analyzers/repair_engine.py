from collections.abc import Callable
from collections import Counter

from app.analyzers.ai_repair import AIRepairError, RepairRequest, propose_ai_repair

from app.analyzers.analyzer_types import AnalysisResult, AnalyzerUnavailable
from app.analyzers.ast_analyzer import _analyze_python
from app.analyzers.c_analyzer import _analyze_c
from app.schemas import Language
from app.schemas import Finding


def analyze_code(
    source: str,
    language: Language,
    program_goal: str = "",
    simulation_input: str = "",
    expected_output: str | None = None,
    *,
    compiler_locator: Callable[[str], str | None],
    sandbox_runner,
    python_runner=None,
) -> AnalysisResult:
    if language == "python":
        initial = _analyze_python(
            source,
            program_goal,
            simulation_input,
            expected_output,
            preview_runner=python_runner,
        )
        request = RepairRequest(
            source=source,
            language=language,
            program_goal=program_goal,
            findings=tuple(initial.findings),
            simulation_input=simulation_input,
            expected_output=expected_output,
        )
        try:
            proposal = propose_ai_repair(request)
        except AIRepairError as error:
            return _with_ai_notice(initial, str(error))
        if not proposal or proposal.source == source:
            return initial
        try:
            import ast

            ast.parse(proposal.source)
            candidate = _analyze_python(
                proposal.source,
                program_goal,
                simulation_input,
                expected_output,
                preview_runner=python_runner,
            )
        except SyntaxError:
            return _with_ai_notice(
                initial, "The AI rewrite did not pass Python syntax validation."
            )
        except AnalyzerUnavailable:
            return _with_ai_notice(
                initial, "The AI rewrite could not be checked by Python and Ruff."
            )
        return _accept_validated_proposal(
            initial, candidate, expected_output, proposal.summary
        )

    if language == "c":
        compiler = compiler_locator("clang") or compiler_locator("gcc")
        initial = _analyze_c(
            source,
            program_goal,
            simulation_input,
            expected_output,
            compiler=compiler,
            sandbox_runner=sandbox_runner,
        )
        request = RepairRequest(
            source=source,
            language=language,
            program_goal=program_goal,
            findings=tuple(initial.findings),
            simulation_input=simulation_input,
            expected_output=expected_output,
        )
        try:
            proposal = propose_ai_repair(request)
        except AIRepairError as error:
            return _with_ai_notice(initial, str(error))
        if not proposal or proposal.source == source:
            return initial
        try:
            candidate = _analyze_c(
                proposal.source,
                program_goal,
                simulation_input,
                expected_output,
                compiler=compiler,
                sandbox_runner=sandbox_runner,
            )
        except AnalyzerUnavailable:
            return _with_ai_notice(
                initial, "The AI rewrite could not be checked by the C compiler."
            )
        return _accept_validated_proposal(
            initial, candidate, expected_output, proposal.summary
        )
    raise ValueError(f"Unsupported language: {language}")


def _accept_validated_proposal(initial, candidate, expected_output, summary: str):
    """Use a model rewrite only after the selected language checker accepts it."""
    if candidate.corrected_code == "":
        return _with_ai_notice(initial, "The AI rewrite was empty and was rejected.")
    if any(
        finding.code in {"C_ERROR", "C_FATAL_ERROR"} and not finding.fixed
        for finding in candidate.findings
    ):
        return _with_ai_notice(
            initial, "The C compiler rejected the AI rewrite; it was not applied."
        )
    if expected_output is not None and candidate.simulation.test_passed is not True:
        return _with_ai_notice(
            initial,
            "The AI rewrite did not pass the expected-output preview and was rejected.",
        )

    unresolved = Counter(
        (finding.code, finding.message)
        for finding in candidate.findings
        if not finding.fixed
    )
    findings = []
    for finding in initial.findings:
        signature = (finding.code, finding.message)
        still_present = unresolved[signature] > 0
        if still_present:
            unresolved[signature] -= 1
        findings.append(
            finding.model_copy(
                update={
                    "fixed": not still_present,
                    "verified": candidate.simulation.test_passed
                    if expected_output is not None and not still_present
                    else (False if not still_present else None),
                }
            )
        )
    findings.append(
        Finding(
            code="AI_REWRITE",
            message=(
                (summary + " " if summary else "")
                + "AI proposed a source update from the program goal and findings. "
                "It passed the language-specific server checks; review the "
                "comparison and test the intended behavior."
            ),
            fixed=True,
            verified=candidate.simulation.test_passed
            if expected_output is not None
            else None,
        )
    )
    findings.extend(candidate.findings)
    return candidate.model_copy(update={"findings": findings})


def _with_ai_notice(initial: AnalysisResult, message: str) -> AnalysisResult:
    return initial.model_copy(
        update={
            "findings": [
                *initial.findings,
                Finding(code="AI_REPAIR_UNAVAILABLE", message=message),
            ]
        }
    )
