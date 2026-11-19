import ast
import io
import re
import tokenize
import tempfile
from collections import Counter
from pathlib import Path

from app.analyzers.analyzer_types import AnalysisResult, AnalyzerUnavailable
from app.analyzers.bug_detector import _repair_python_goal_logic, _repair_python_identity_comparisons
from app.analyzers.ruff_analyzer import ANALYZER_PROCESS_TIMEOUT_SECONDS, RUFF_RULES, _diagnostics, _run_ruff
from app.analyzers.source_utils import _apply_source_edits, _source_offset
from app.schemas import Finding, SimulationResult
from app.simulator import simulate_python, verify_expected_output


def _repair_python_syntax_step(source: str, error: SyntaxError) -> str | None:
    lines = source.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    last_non_whitespace = len(source.rstrip())

    if error.lineno is not None:
        line_index = error.lineno - 1
        if 0 <= line_index < len(lines):
            line = lines[line_index]
            if "expected ':'" in error.msg or "':' expected" in error.msg:
                control_line = re.match(
                    r"^(?P<indent>[\t ]*)(?:if|elif|else|for|while|def|class|try|except|finally|with)\b",
                    line,
                )
                if control_line is not None:
                    body = line.rstrip("\r\n")
                    newline = line[len(body):]
                    if not body.rstrip().endswith(":"):
                        try:
                            comment = next(
                                (
                                    token
                                    for token in tokenize.generate_tokens(
                                        io.StringIO(body).readline
                                    )
                                    if token.type == tokenize.COMMENT
                                ),
                                None,
                            )
                        except (tokenize.TokenError, IndentationError):
                            comment = None
                        comment_start = comment.start[1] if comment else len(body)
                        before_comment = body[:comment_start]
                        trimmed = before_comment.rstrip()
                        lines[line_index] = (
                            trimmed
                            + ":"
                            + before_comment[len(trimmed):]
                            + body[comment_start:]
                            + newline
                        )
                        return "".join(lines)

            joined_statements = re.search(
                r"\)(?P<space>[\t ]*)(?P<assignment>[A-Za-z_]\w*\s*=)",
                line,
            )
            if joined_statements is not None:
                indent = re.match(r"^[\t ]*", line).group(0)
                split_at = joined_statements.start("assignment")
                return (
                    source[: line_offsets[line_index] + split_at]
                    + "\n"
                    + indent
                    + source[line_offsets[line_index] + split_at :]
                )
            if (
                "==" in error.msg
                and "instead of '='" in error.msg
                and re.match(r"^[\t ]*(?:if|elif|while)\b", line)
            ):
                try:
                    equals = [
                        token
                        for token in tokenize.generate_tokens(io.StringIO(line).readline)
                        if token.type == tokenize.OP and token.string == "="
                    ]
                except (tokenize.TokenError, IndentationError):
                    equals = []
                if len(equals) == 1:
                    token = equals[0]
                    start = line_offsets[line_index] + token.start[1]
                    end = line_offsets[line_index] + token.end[1]
                    return source[:start] + "==" + source[end:]
            input_prompt = re.search(
                r"(?P<int_prefix>\bint\s*\(\s*)inputenter\s+"
                r"(?P<prompt>[^()\r\n]+?)\s*(?P<close>\))(?P<suffix>\s*(?:#.*)?(?:\r?\n)?)$",
                line,
                re.IGNORECASE,
            )
            if input_prompt is not None:
                replacement = (
                    line[: input_prompt.start()]
                    + input_prompt["int_prefix"]
                    + f'input("enter {input_prompt["prompt"].strip()}")'
                    + input_prompt["close"]
                    + input_prompt["suffix"]
                )
                lines[line_index] = replacement
                return "".join(lines)

    stack: list[str] = []
    trailing_comment_offset: int | None = None
    string_closer = ""
    string_insertion_offset: int | None = None
    matching_openers = {")": "(", "]": "[", "}": "{"}
    matching_closers = {"(": ")", "[": "]", "{": "}"}

    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                end_offset = _source_offset(line_offsets, token.end)
                if end_offset >= last_non_whitespace:
                    trailing_comment_offset = _source_offset(line_offsets, token.start)
            if token.type != tokenize.OP:
                continue
            if token.string in matching_closers:
                stack.append(token.string)
            elif token.string in matching_openers and (
                not stack or stack.pop() != matching_openers[token.string]
            ):
                return None
    except tokenize.TokenError as token_error:
        message, position = token_error.args
        if message.startswith("unterminated string literal"):
            line_number, column = position
            quote_offset = _source_offset(line_offsets, (line_number, column)) - 1
            quote = source[quote_offset : quote_offset + 1]
            if (
                quote in {"'", '"'}
                and not source.startswith(quote * 3, quote_offset)
                and 0 < line_number <= len(lines)
            ):
                line_end = line_offsets[line_number]
                while line_end > 0 and source[line_end - 1] in "\r\n":
                    line_end -= 1
                string_closer = quote
                string_insertion_offset = line_end
                closer_end = line_end
                while closer_end > quote_offset and source[closer_end - 1].isspace():
                    closer_end -= 1
                closer_start = closer_end
                while closer_start > quote_offset:
                    closer = source[closer_start - 1]
                    if (
                        closer not in matching_openers
                        or not stack
                        or stack[-1] != matching_openers[closer]
                    ):
                        break
                    stack.pop()
                    closer_start -= 1
                if closer_start < closer_end:
                    string_insertion_offset = closer_start
    except IndentationError:
        pass

    if not stack and not string_closer:
        return None

    closers = "".join(matching_closers[opener] for opener in reversed(stack))
    insertion_offset = string_insertion_offset
    if insertion_offset is None:
        insertion_offset = (
            trailing_comment_offset
            if trailing_comment_offset is not None
            else len(source)
        )
    corrected = (
        source[:insertion_offset] + string_closer + closers + source[insertion_offset:]
    )
    return corrected


def _repair_missing_line_closers(source: str, error: SyntaxError) -> str | None:
    """Close unmatched delimiters on the reported line when parsing advances."""
    if error.lineno is None:
        return None
    lines = source.splitlines(keepends=True)
    error_line_index = error.lineno - 1
    if not 0 <= error_line_index < len(lines):
        return None

    matching_openers = {")": "(", "]": "[", "}": "{"}
    matching_closers = {"(": ")", "[": "]", "{": "}"}
    for line_index in (error_line_index,):
        line = lines[line_index]
        stack: list[str] = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(line).readline):
                if token.type != tokenize.OP:
                    continue
                if token.string in matching_closers:
                    stack.append(token.string)
                elif token.string in matching_openers:
                    if not stack or stack.pop() != matching_openers[token.string]:
                        stack = []
                        break
        except tokenize.TokenError as token_error:
            if "EOF in multi-line statement" not in str(token_error.args[0]):
                continue
        except IndentationError:
            continue
        if not stack:
            continue

        body = line.rstrip("\r\n")
        newline = line[len(body):]
        insertion = len(body.rstrip())
        try:
            for token in tokenize.generate_tokens(io.StringIO(body).readline):
                if token.type == tokenize.COMMENT:
                    insertion = min(insertion, token.start[1])
                    break
        except tokenize.TokenError as token_error:
            if "EOF in multi-line statement" not in str(token_error.args[0]):
                continue
        except IndentationError:
            continue

        closers = "".join(matching_closers[opener] for opener in reversed(stack))
        candidate_lines = lines.copy()
        candidate_lines[line_index] = (
            body[:insertion] + closers + body[insertion:] + newline
        )
        candidate = "".join(candidate_lines)
        try:
            ast.parse(candidate)
        except SyntaxError as candidate_error:
            if (
                candidate_error.lineno == error.lineno
                and candidate_error.offset == error.offset
                and candidate_error.msg == error.msg
            ):
                continue
        return candidate
    return None


def _repair_incomplete_python_syntax(
    source: str,
) -> tuple[str, list[Finding]] | None:
    corrected = source
    findings: list[Finding] = []

    for _ in range(8):
        try:
            ast.parse(corrected)
        except SyntaxError as error:
            candidate = _repair_missing_line_closers(corrected, error)
            if candidate is None or candidate == corrected:
                candidate = _repair_python_syntax_step(corrected, error)
            if candidate is None or candidate == corrected:
                return None
            findings.append(
                Finding(
                    code="SYNTAX_ERROR",
                    message=error.msg,
                    line=error.lineno,
                    column=error.offset,
                    fixed=True,
                )
            )
            corrected = candidate
        else:
            return (corrected, findings) if findings else None

    return None


def _analyze_python(
    source: str,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
    *,
    preview_runner=None,
) -> AnalysisResult:
    original_source = source
    syntax_findings: list[Finding] = []
    try:
        syntax_tree = ast.parse(source)
    except SyntaxError as error:
        repair = _repair_incomplete_python_syntax(source)
        if repair is None:
            finding = Finding(
                code="SYNTAX_ERROR",
                message=error.msg,
                line=error.lineno,
                column=error.offset,
            )
            simulation = verify_expected_output(
                SimulationResult(
                    status="unsupported",
                    message="Preview is unavailable until the Python source parses.",
                ),
                expected_output,
            )
            findings = [
                finding,
                Finding(
                    code="LOGIC_ANALYSIS_SKIPPED",
                    message=(
                        "Logic checks were skipped because syntax errors prevent "
                        "the source from being parsed. Fix the syntax and reanalyze."
                    ),
                ),
            ]
            if expected_output is not None:
                findings.append(
                    Finding(
                        code="TEST_NOT_VERIFIED",
                        message=(
                            "The expected-output test could not run because the "
                            "source has unrepairable syntax errors."
                        ),
                    )
                )
            return AnalysisResult(
                corrected_code=source,
                findings=findings,
                simulation=simulation,
            )
        source, syntax_findings = repair

    syntax_tree = ast.parse(source)
    source, identity_findings = _repair_python_identity_comparisons(
        source,
        syntax_tree,
        apply_fixes=True,
    )
    syntax_tree = ast.parse(source)
    source, logic_findings = _repair_python_goal_logic(
        source,
        syntax_tree,
        program_goal,
    )
    syntax_tree = ast.parse(source)
    logic_findings.extend(identity_findings)

    with tempfile.TemporaryDirectory(prefix="bug-hunter-") as directory:
        code_path = Path(directory) / "submission.py"
        code_path.write_text(source, encoding="utf-8")

        initial = _run_ruff(
            "check",
            "--output-format=json",
            "--select",
            RUFF_RULES,
            "--ignore",
            "F632",
            str(code_path),
        )
        if initial.returncode not in (0, 1):
            raise AnalyzerUnavailable(
                "The Python code analyzer failed to inspect the submission."
            )
        initial_diagnostics = _diagnostics(initial.stdout)

        fixed_diagnostics: list[dict] = []
        corrected_code = source
        if initial_diagnostics:
            fixed = _run_ruff(
                "check",
                "--fix",
                "--output-format=json",
                "--select",
                RUFF_RULES,
                "--ignore",
                "F632",
                str(code_path),
            )
            if fixed.returncode not in (0, 1):
                raise AnalyzerUnavailable(
                    "The Python code fixer failed to inspect the submission."
                )
            fixed_diagnostics = _diagnostics(fixed.stdout)
            corrected_code = code_path.read_text(encoding="utf-8")
            if not source.startswith(("\r", "\n")):
                corrected_code = corrected_code.lstrip("\r\n")

    findings: list[Finding] = syntax_findings
    remaining_diagnostics = Counter(
        (diagnostic.get("code", ""), diagnostic.get("message", ""))
        for diagnostic in fixed_diagnostics
    )
    for diagnostic in initial_diagnostics:
        signature = (diagnostic.get("code", ""), diagnostic.get("message", ""))
        location = diagnostic.get("location") or {}
        still_present = remaining_diagnostics[signature] > 0
        if still_present:
            remaining_diagnostics[signature] -= 1
        findings.append(
            Finding(
                code=signature[0],
                message=signature[1],
                line=location.get("row"),
                column=location.get("column"),
                fixed=not still_present,
            )
        )

    for diagnostic in fixed_diagnostics:
        signature = (diagnostic.get("code", ""), diagnostic.get("message", ""))
        if remaining_diagnostics[signature] == 0:
            continue
        remaining_diagnostics[signature] -= 1
        location = diagnostic.get("location") or {}
        findings.append(
            Finding(
                code=signature[0],
                message=signature[1],
                line=location.get("row"),
                column=location.get("column"),
            )
        )

    findings.extend(logic_findings)
    corrected_tree = ast.parse(corrected_code)
    simulation = verify_expected_output(
        (
            preview_runner(corrected_code, simulation_input)
            if preview_runner is not None
            else simulate_python(
                corrected_code,
                simulation_input,
                syntax_tree=corrected_tree,
            )
        ),
        expected_output,
    )
    if expected_output is not None and simulation.test_passed is True:
        findings = [
            finding.model_copy(update={"verified": True}) if finding.fixed else finding
            for finding in findings
        ]
    if expected_output is not None:
        identity_message = (
            "The value-comparison fix matched the supplied expected output."
            if simulation.test_passed is True
            else (
                "The candidate value-comparison fix was rejected because its "
                "preview did not match the expected output."
                if simulation.test_passed is False
                else (
                    "Applied a best-effort value-comparison fix; the restricted "
                    "preview could not verify it."
                )
            )
        )
        findings = [
            finding.model_copy(
                update={
                    "message": identity_message,
                    "verified": simulation.test_passed,
                }
            )
            if finding.code == "LOGIC_IDENTITY_COMPARISON" and finding.fixed
            else finding
            for finding in findings
        ]
    if expected_output is not None and simulation.test_passed is not True:
        rejected_corrections = corrected_code != original_source
        if simulation.test_passed is False and rejected_corrections:
            corrected_code = original_source
            findings = [
                finding.model_copy(update={"fixed": False, "verified": False})
                for finding in findings
            ]
            reason = (
                "The candidate corrections were rejected because their preview "
                "did not match the expected output."
            )
            simulation = simulation.model_copy(
                update={"message": f"{reason} {simulation.message}"}
            )
        elif simulation.test_passed is None and rejected_corrections:
            simulation = simulation.model_copy(
                update={
                    "message": (
                        "Best-effort corrections are shown as unverified because "
                        f"the preview could not check the supplied output. {simulation.message}"
                    )
                }
            )
        test_code = (
            "TEST_OUTPUT_MISMATCH"
            if simulation.test_passed is False
            else "TEST_NOT_VERIFIED"
        )
        test_message = (
            "The preview output did not match the supplied expected output."
            if simulation.test_passed is False
            else (
                "The expected-output test couldn't be completed by the restricted "
                "Python preview; best-effort corrections remain labeled unverified."
            )
        )
        findings.append(Finding(code=test_code, message=test_message))

    return AnalysisResult(
        corrected_code=corrected_code,
        findings=findings,
        simulation=simulation,
    )
