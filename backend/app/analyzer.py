import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.c_sandbox import simulate_c_sandbox
from app.schemas import Finding, Language, SimulationResult
from app.simulator import simulate_python, verify_expected_output

RUFF_RULES = "E,F,B"
ANALYZER_PROCESS_TIMEOUT_SECONDS = 30
C_DIAGNOSTIC = re.compile(
    r":(?P<line>\d+):(?P<column>\d+): "
    r"(?P<severity>fatal error|error|warning|note): (?P<message>.+)"
)


class AnalyzerUnavailable(Exception):
    pass


@dataclass
class AnalysisResult:
    corrected_code: str
    findings: list[Finding]
    simulation: SimulationResult


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


def _source_offset(
    line_offsets: list[int],
    position: tuple[int, int],
) -> int:
    return line_offsets[position[0] - 1] + position[1]


def _apply_source_edits(
    source: str,
    edits: list[tuple[int, int, str]],
) -> str | None:
    parts: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        if start < cursor:
            return None
        parts.extend((source[cursor:start], replacement))
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts)


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


def _repair_incomplete_python_syntax(
    source: str,
) -> tuple[str, list[Finding]] | None:
    corrected = source
    findings: list[Finding] = []

    for _ in range(8):
        try:
            ast.parse(corrected)
        except SyntaxError as error:
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


def _repair_python_identity_comparisons(
    source: str,
    tree: ast.AST,
    *,
    apply_fixes: bool,
) -> tuple[str, list[Finding]]:
    lines = source.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    def ast_offset(node: ast.AST, *, end: bool = False) -> int:
        line_number = getattr(node, "end_lineno" if end else "lineno")
        byte_column = getattr(node, "end_col_offset" if end else "col_offset")
        line = lines[line_number - 1]
        character_column = len(line.encode("utf-8")[:byte_column].decode("utf-8"))
        return line_offsets[line_number - 1] + character_column

    tokens = [
        (
            token,
            line_offsets[token.start[0] - 1] + token.start[1],
            line_offsets[token.end[0] - 1] + token.end[1],
        )
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
    ]
    token_starts = [start for _, start, _ in tokens]
    edits: list[tuple[int, int, str]] = []
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for index, operation in enumerate(node.ops):
            if not isinstance(operation, (ast.Is, ast.IsNot)):
                continue
            literal = next(
                (
                    operand
                    for operand in operands[index : index + 2]
                    if isinstance(operand, ast.Constant)
                    and operand.value is not None
                    and operand.value is not True
                    and operand.value is not False
                ),
                None,
            )
            if literal is None:
                continue

            if not apply_fixes:
                findings.append(
                    Finding(
                        code="LOGIC_IDENTITY_COMPARISON",
                        message=(
                            "This compares a value with 'is'. Confirm identity is "
                            "intended, or provide an expected-output test to verify "
                            "a value-comparison fix."
                        ),
                        line=node.lineno,
                        column=node.col_offset + 1,
                    )
                )
                continue

            left_end = ast_offset(operands[index], end=True)
            right_start = ast_offset(operands[index + 1])
            token_start = bisect_left(token_starts, left_end)
            token_end = bisect_right(token_starts, right_start)
            operator_tokens = [
                token_range
                for token_range in tokens[token_start:token_end]
                if token_range[2] <= right_start
            ]
            identity_index = next(
                (
                    token_index
                    for token_index, (token, _, _) in enumerate(operator_tokens)
                    if token.type == tokenize.NAME and token.string == "is"
                ),
                None,
            )
            if identity_index is None:
                continue

            identity_token, identity_start, identity_end = operator_tokens[
                identity_index
            ]
            replacement = "!=" if isinstance(operation, ast.IsNot) else "=="
            edits.append((identity_start, identity_end, replacement))
            if isinstance(operation, ast.IsNot):
                not_token = next(
                    (
                        token_range
                        for token_range in operator_tokens[identity_index + 1 :]
                        if token_range[0].type == tokenize.NAME
                        and token_range[0].string == "not"
                    ),
                    None,
                )
                if not_token is None:
                    edits.pop()
                    continue
                if source[identity_end : not_token[1]].isspace():
                    edits[-1] = (identity_start, not_token[2], replacement)
                else:
                    edits.append((not_token[1], not_token[2], ""))
            findings.append(
                Finding(
                    code="LOGIC_IDENTITY_COMPARISON",
                    message=(
                        "Changed the identity comparison to a value comparison as a "
                        "best-effort fix; verify the intended semantics."
                    ),
                    line=identity_token.start[0],
                    column=identity_token.start[1] + 1,
                    fixed=True,
                    verified=False,
                )
            )

    if not edits:
        return source, findings

    corrected = _apply_source_edits(source, edits)
    if corrected is None:
        return source, [
            finding.model_copy(update={"fixed": False}) for finding in findings
        ]
    try:
        ast.parse(corrected)
    except SyntaxError:
        return source, [
            finding.model_copy(update={"fixed": False}) for finding in findings
        ]
    return corrected, findings


def _ast_character_offset(
    source: str,
    node: ast.AST,
    *,
    end: bool = False,
    line_offsets: list[int] | None = None,
) -> int:
    lines = source.splitlines(keepends=True)
    if line_offsets is None:
        line_offsets = [0]
        for line in lines:
            line_offsets.append(line_offsets[-1] + len(line))
    line_number = getattr(node, "end_lineno" if end else "lineno")
    byte_column = getattr(node, "end_col_offset" if end else "col_offset")
    line = lines[line_number - 1]
    character_column = len(line.encode("utf-8")[:byte_column].decode("utf-8"))
    return line_offsets[line_number - 1] + character_column


def _repair_python_goal_logic(
    source: str,
    tree: ast.AST,
    program_goal: str,
) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []
    nodes = list(ast.walk(tree))
    goal = program_goal.casefold()
    edits: list[tuple[int, int, str]] = []
    line_offsets = [0]
    for line in source.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    for node in nodes:
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            for index, statement in enumerate(statements[:-1]):
                if isinstance(
                    statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)
                ):
                    unreachable = statements[index + 1]
                    finding = Finding(
                        code="LOGIC_UNREACHABLE_CODE",
                        message=(
                            "This statement follows a return, raise, break, or "
                            "continue in the same block and cannot be reached."
                        ),
                        line=getattr(unreachable, "lineno", None),
                        column=getattr(unreachable, "col_offset", 0) + 1,
                    )
                    if unreachable.lineno != statement.lineno:
                        start = line_offsets[unreachable.lineno - 1]
                        last_line = getattr(statements[-1], "end_lineno", None)
                        if last_line is not None:
                            end = line_offsets[last_line]
                            edits.append((start, end, ""))
                            finding = finding.model_copy(
                                update={
                                    "message": (
                                        "Removed statements that followed an "
                                        "unconditional exit; review the best-effort "
                                        "cleanup."
                                    ),
                                    "fixed": True,
                                    "verified": False,
                                }
                            )
                    findings.append(finding)
                    break

    if re.search(r"\b(average|mean)\b", goal):
        has_mean_call = any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id.casefold() in {"mean", "fmean"}
                or isinstance(node.func, ast.Attribute)
                and node.func.attr.casefold() in {"mean", "fmean"}
            )
            for node in nodes
        )
        has_division = any(
            isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv))
            for node in nodes
        )
        if not has_mean_call and not has_division:
            finding = Finding(
                code="LOGIC_GOAL_AVERAGE_REVIEW",
                message=(
                    "The goal suggests an average; a best-effort correction could "
                    "not be inferred from this source."
                ),
            )
            for node in nodes:
                if (
                    isinstance(node, ast.Return)
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "sum"
                    and len(node.value.args) == 1
                    and isinstance(node.value.args[0], ast.Name)
                    and not node.value.keywords
                ):
                    values = node.value.args[0].id
                    edits.append(
                        (
                            _ast_character_offset(
                                source,
                                node.value,
                                line_offsets=line_offsets,
                            ),
                            _ast_character_offset(
                                source,
                                node.value,
                                end=True,
                                line_offsets=line_offsets,
                            ),
                            f"(sum({values}) / len({values}) if len({values}) else 0)",
                        )
                    )
                    finding = finding.model_copy(
                        update={
                            "message": (
                                "Added a best-effort average calculation with a "
                                "zero result for empty input; verify this assumption."
                            ),
                            "fixed": True,
                            "verified": False,
                        }
                    )
                    break
            findings.append(finding)

    if re.search(r"\b(sort|sorting|sorted)\b", goal):
        has_sort_call = any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == "sorted"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "sort"
            )
            for node in nodes
        )
        has_comparison = any(isinstance(node, ast.Compare) for node in nodes)
        if not has_sort_call and not has_comparison:
            finding = Finding(
                code="LOGIC_GOAL_SORT_REVIEW",
                message=(
                    "The goal suggests sorting; a best-effort correction could "
                    "not be inferred from this source."
                ),
            )
            for node in nodes:
                if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
                    values = node.value.id
                    reverse = bool(re.search(r"\b(descending|reverse)\b", goal))
                    argument = (
                        f"sorted({values}, reverse=True)"
                        if reverse
                        else f"sorted({values})"
                    )
                    edits.append(
                        (
                            _ast_character_offset(
                                source,
                                node.value,
                                line_offsets=line_offsets,
                            ),
                            _ast_character_offset(
                                source,
                                node.value,
                                end=True,
                                line_offsets=line_offsets,
                            ),
                            argument,
                        )
                    )
                    finding = finding.model_copy(
                        update={
                            "message": (
                                "Applied a best-effort sort to the returned values "
                                f"in {'descending' if reverse else 'ascending'} order; "
                                "verify the intended order."
                            ),
                            "fixed": True,
                            "verified": False,
                        }
                    )
                    break
            findings.append(finding)

    if re.search(
        r"\b(max|maximum|min|minimum|largest|smallest|highest|lowest)\b",
        goal,
    ):
        has_extreme_call = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"min", "max"}
            for node in nodes
        )
        has_comparison = any(isinstance(node, ast.Compare) for node in nodes)
        if not has_extreme_call and not has_comparison:
            wants_maximum = bool(re.search(r"\b(max|maximum|largest|highest)\b", goal))
            operation = "max" if wants_maximum else "min"
            finding = Finding(
                code="LOGIC_GOAL_EXTREME_REVIEW",
                message=(
                    "The goal suggests selecting an extreme value; a best-effort "
                    "correction could not be inferred from this source."
                ),
            )
            for node in nodes:
                if (
                    isinstance(node, ast.Return)
                    and isinstance(node.value, ast.Subscript)
                    and isinstance(node.value.value, ast.Name)
                    and isinstance(node.value.slice, ast.Constant)
                    and node.value.slice.value == 0
                ):
                    values = node.value.value.id
                    edits.append(
                        (
                            _ast_character_offset(
                                source,
                                node.value,
                                line_offsets=line_offsets,
                            ),
                            _ast_character_offset(
                                source,
                                node.value,
                                end=True,
                                line_offsets=line_offsets,
                            ),
                            f"{operation}({values})",
                        )
                    )
                    finding = finding.model_copy(
                        update={
                            "message": (
                                f"Applied a best-effort {operation} selection to "
                                "the returned collection; verify empty-input behavior."
                            ),
                            "fixed": True,
                            "verified": False,
                        }
                    )
                    break
            findings.append(finding)

    corrected = _apply_source_edits(source, edits)
    if corrected is None:
        return source, [
            finding.model_copy(update={"fixed": False, "verified": None})
            for finding in findings
        ]
    try:
        ast.parse(corrected)
    except SyntaxError:
        return source, [
            finding.model_copy(update={"fixed": False, "verified": None})
            for finding in findings
        ]
    return corrected, findings


def _analyze_python(
    source: str,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
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
        simulate_python(
            corrected_code,
            simulation_input,
            syntax_tree=corrected_tree,
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


def _repair_c_goal_logic(
    source: str,
    program_goal: str,
) -> tuple[str, list[Finding]]:
    corrected = source
    findings: list[Finding] = []
    goal = program_goal.casefold()

    def scalar_array_functions(code: str):
        pattern = re.compile(
            r"(?P<header>\b(?P<return>void|int|long|float|double|"
            r"(?:int|long|float|double)\s*\*)\s*"
            r"(?P<name>\w+)\s*\(\s*(?P<type>int|long|float|double)\s+"
            r"(?:\*\s*)?(?P<array>\w+)\s*(?:\[\s*\])?\s*,\s*"
            r"(?:int|size_t)\s+(?P<count>\w+)\s*\)\s*\{)"
            r"(?P<body>[^{}]*)(?P<close>\})",
            re.DOTALL,
        )
        return pattern.finditer(code)

    if re.search(r"\b(average|mean)\b", goal):
        finding = Finding(
            code="LOGIC_GOAL_AVERAGE_REVIEW",
            message=(
                "The goal suggests an average; a best-effort C correction "
                "could not be inferred from this source."
            ),
        )
        if not re.search(r"/", corrected):
            function_pattern = re.compile(
                r"(?P<header>\b(?:float|double)\s+\w+\s*\([^)]*\)\s*\{)"
                r"(?P<body>[^{}]*)(?P<close>\})",
                re.DOTALL,
            )
            for match in function_pattern.finditer(corrected):
                body = match.group("body")
                return_match = re.search(
                    r"(?m)^(?P<indent>[ \t]*)return\s+"
                    r"(?P<total>sum|total)\s*;\s*$",
                    body,
                    re.IGNORECASE,
                )
                count_match = re.search(
                    r"\b(?:int|long|size_t)\s+"
                    r"(?P<count>count|n|size|length|num_values)\b",
                    match.group(0),
                    re.IGNORECASE,
                )
                if return_match is None or count_match is None:
                    continue
                count = count_match["count"]
                total = return_match["total"]
                indent = return_match["indent"]
                replacement = (
                    f"{indent}if ({count} <= 0) return 0.0;\n"
                    f"{indent}return (double){total} / {count};"
                )
                start = match.start("body") + return_match.start()
                end = match.start("body") + return_match.end()
                corrected = corrected[:start] + replacement + corrected[end:]
                finding = Finding(
                    code="LOGIC_GOAL_AVERAGE_REVIEW",
                    message=(
                        "Applied a best-effort average calculation and assumed "
                        "zero for empty input; verify the intended behavior."
                    ),
                    line=source.count("\n", 0, start) + 1,
                    fixed=True,
                    verified=False,
                )
                break
        findings.append(finding)

    if re.search(r"\b(sort|sorting|sorted)\b", goal):
        finding = Finding(
            code="LOGIC_GOAL_SORT_REVIEW",
            message=(
                "The goal suggests sorting; a best-effort C correction "
                "could not be inferred from this source."
            ),
        )
        has_sort_logic = bool(
            re.search(r"\bqsort\s*\(", corrected)
            or re.search(
                r"\b(?:for|while)\s*\([^)]*[<>][^)]*\)[^{]*\{[^{}]*"
                r"\[[^\]]+\]\s*[<>]",
                corrected,
                re.DOTALL,
            )
        )
        if not has_sort_logic:
            for match in scalar_array_functions(corrected):
                body = match.group("body")
                if match.group("return") != "void" and not re.search(
                    r"\breturn\s+" + re.escape(match.group("array")) + r"\s*;",
                    body,
                ):
                    continue
                if re.search(r"\bbh_(?:i|j|tmp)\b", match.group(0)):
                    continue
                array = match.group("array")
                count = match.group("count")
                value_type = match.group("type")
                comparator = (
                    "<" if re.search(r"\b(descending|reverse)\b", goal) else ">"
                )
                loop = (
                    "\n    for (int bh_i = 0; bh_i < "
                    f"{count}; ++bh_i) {{\n"
                    f"        for (int bh_j = 0; bh_j + 1 < {count} - bh_i; "
                    "++bh_j) {\n"
                    f"            if ({array}[bh_j] {comparator} "
                    f"{array}[bh_j + 1]) {{\n"
                    f"                {value_type} bh_tmp = {array}[bh_j];\n"
                    f"                {array}[bh_j] = {array}[bh_j + 1];\n"
                    f"                {array}[bh_j + 1] = bh_tmp;\n"
                    "            }\n"
                    "        }\n"
                    "    }\n"
                )
                return_line = re.search(
                    r"(?m)^[ \t]*return\s+" + re.escape(array) + r"\s*;[ \t]*$",
                    body,
                )
                insertion = return_line.start() if return_line else len(body.rstrip())
                body_start = match.start("body")
                corrected = (
                    corrected[: body_start + insertion]
                    + loop
                    + corrected[body_start + insertion :]
                )
                finding = Finding(
                    code="LOGIC_GOAL_SORT_REVIEW",
                    message=(
                        "Applied a best-effort in-place bubble sort to the detected "
                        "numeric array; verify the array length and sort order."
                    ),
                    line=source.count("\n", 0, body_start) + 1,
                    fixed=True,
                    verified=False,
                )
                break
        findings.append(finding)

    if re.search(
        r"\b(max|maximum|min|minimum|largest|smallest|highest|lowest)\b",
        goal,
    ):
        wants_maximum = bool(re.search(r"\b(max|maximum|largest|highest)\b", goal))
        operation = "max" if wants_maximum else "min"
        finding = Finding(
            code="LOGIC_GOAL_EXTREME_REVIEW",
            message=(
                "The goal suggests selecting an extreme value; a best-effort C "
                "correction could not be inferred from this source."
            ),
        )
        if not re.search(r"[<>]", corrected):
            for match in scalar_array_functions(corrected):
                if match.group("return") not in {"int", "long", "float", "double"}:
                    continue
                array = match.group("array")
                count = match.group("count")
                value_type = match.group("type")
                body = match.group("body")
                return_line = re.search(
                    r"(?m)^(?P<indent>[ \t]*)return\s+"
                    + re.escape(array)
                    + r"\s*\[\s*0\s*\]\s*;\s*$",
                    body,
                )
                if return_line is None or re.search(r"\bbh_(?:best|i)\b", body):
                    continue
                indent = return_line["indent"]
                replacement = (
                    f"{indent}if ({count} <= 0) return 0;\n"
                    f"{indent}{value_type} bh_best = {array}[0];\n"
                    f"{indent}for (int bh_i = 1; bh_i < {count}; ++bh_i) {{\n"
                    f"{indent}    if ({array}[bh_i] {'>' if operation == 'max' else '<'} "
                    f"bh_best) bh_best = {array}[bh_i];\n"
                    f"{indent}}}\n"
                    f"{indent}return bh_best;"
                )
                start = match.start("body") + return_line.start()
                end = match.start("body") + return_line.end()
                corrected = corrected[:start] + replacement + corrected[end:]
                finding = Finding(
                    code="LOGIC_GOAL_EXTREME_REVIEW",
                    message=(
                        f"Applied a best-effort {operation} loop to the detected "
                        "numeric array; verify empty-input behavior."
                    ),
                    line=source.count("\n", 0, start) + 1,
                    fixed=True,
                    verified=False,
                )
                break
        findings.append(finding)

    return corrected, findings


def _analyze_c(
    source: str,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
) -> AnalysisResult:
    compiler = shutil.which("clang") or shutil.which("gcc")
    if compiler is None:
        raise AnalyzerUnavailable(
            "C analysis requires GCC or Clang to be installed on the server."
        )

    with tempfile.TemporaryDirectory(prefix="bug-hunter-") as directory:
        code_path = Path(directory) / "submission.c"
        code_path.write_text(source, encoding="utf-8")
        result = _run_c_compiler(compiler, code_path)
        findings = _c_findings(result.stderr)
        corrected_code = source
        candidate_compiled = False

        if result.returncode == 0:
            candidate, logic_findings = _repair_c_goal_logic(source, program_goal)
            if candidate != source:
                code_path.write_text(candidate, encoding="utf-8")
                candidate_check = _run_c_compiler(compiler, code_path)
                if candidate_check.returncode == 0:
                    corrected_code = candidate
                    candidate_compiled = True
                    findings.extend(logic_findings)
                else:
                    findings.extend(
                        finding.model_copy(
                            update={
                                "message": (
                                    f"{finding.message} The C compiler rejected "
                                    "the proposed edit."
                                ),
                                "fixed": False,
                            }
                        )
                        for finding in logic_findings
                    )
            else:
                findings.extend(logic_findings)
        else:
            candidate = _repair_incomplete_c_printf(source)
            if candidate is not None:
                code_path.write_text(candidate, encoding="utf-8")
                repaired = _run_c_compiler(compiler, code_path)
                if repaired.returncode == 0:
                    corrected_code = candidate
                    candidate_compiled = True
                    remaining = Counter(
                        (finding.code, finding.message)
                        for finding in _c_findings(repaired.stderr)
                    )
                    repaired_findings: list[Finding] = []
                    for finding in findings:
                        signature = (finding.code, finding.message)
                        still_present = remaining[signature] > 0
                        if still_present:
                            remaining[signature] -= 1
                        repaired_findings.append(
                            finding.model_copy(
                                update={
                                    "fixed": not still_present,
                                    "verified": False if not still_present else None,
                                }
                            )
                        )
                    findings = repaired_findings

    if result.returncode != 0 and not findings and not candidate_compiled:
        raise AnalyzerUnavailable(
            "The C compiler failed without a readable diagnostic."
        )

    if expected_output is None:
        simulation = SimulationResult(
            status="unsupported",
            message=(
                "C code is compiler-checked only unless an expected-output "
                "test is enabled."
            ),
        )
    elif result.returncode == 0 or candidate_compiled:
        simulation = verify_expected_output(
            simulate_c_sandbox(corrected_code, simulation_input),
            expected_output,
        )
    else:
        simulation = verify_expected_output(
            SimulationResult(
                status="unsupported",
                message=("C compiler diagnostics prevented the sandbox output test."),
            ),
            expected_output,
        )

    if corrected_code != source:
        findings = [
            finding.model_copy(update={"verified": False}) if finding.fixed else finding
            for finding in findings
        ]
    if expected_output is not None and simulation.test_passed is True:
        findings = [
            finding.model_copy(update={"verified": True}) if finding.fixed else finding
            for finding in findings
        ]
    if expected_output is not None and simulation.test_passed is not True:
        if corrected_code != source:
            if simulation.test_passed is False:
                corrected_code = source
                findings = [
                    finding.model_copy(update={"fixed": False, "verified": False})
                    for finding in findings
                ]
                simulation = simulation.model_copy(
                    update={
                        "message": (
                            "The candidate C correction was rejected because its "
                            f"sandbox output did not match. {simulation.message}"
                        )
                    }
                )
            else:
                simulation = simulation.model_copy(
                    update={
                        "message": (
                            "Best-effort C corrections are shown as unverified. "
                            f"{simulation.message}"
                        )
                    }
                )
        findings.append(
            Finding(
                code=(
                    "TEST_OUTPUT_MISMATCH"
                    if simulation.test_passed is False
                    else "TEST_NOT_VERIFIED"
                ),
                message=(
                    "The sandbox output did not match the supplied expected output."
                    if simulation.test_passed is False
                    else (
                        "The C expected-output test could not be verified; "
                        "best-effort corrections are shown as unverified."
                    )
                ),
            )
        )

    return AnalysisResult(
        corrected_code=corrected_code,
        findings=findings,
        simulation=simulation,
    )


def _run_c_compiler(
    compiler: str,
    code_path: Path,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                compiler,
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-fsyntax-only",
                str(code_path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=ANALYZER_PROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AnalyzerUnavailable("The C code analyzer could not complete.") from error


def _c_findings(output: str) -> list[Finding]:
    findings: list[Finding] = []
    for output_line in output.splitlines():
        match = C_DIAGNOSTIC.search(output_line)
        if match is None:
            continue
        severity = match.group("severity")
        findings.append(
            Finding(
                code=f"C_{severity.upper().replace(' ', '_')}",
                message=match.group("message"),
                line=int(match.group("line")),
                column=int(match.group("column")),
            )
        )
    return findings


def _repair_incomplete_c_printf(source: str) -> str | None:
    lines = source.splitlines()
    comments = [
        line
        for line in lines
        if line.strip()
        and (
            line.lstrip().startswith("//")
            or line.lstrip().startswith("/*")
            and line.rstrip().endswith("*/")
        )
    ]
    code_lines = [line for line in lines if line.strip() and line not in comments]
    if len(code_lines) != 1:
        return None

    match = re.fullmatch(
        r'\s*printf\s*\(\s*"(?P<text>[^"\r\n]*)\s*',
        code_lines[0],
    )
    if match is None:
        return None
    text = match["text"]
    trailing_backslashes = len(text) - len(text.rstrip("\\"))
    if trailing_backslashes % 2:
        return None

    comment_block = "\n".join(comments)
    prefix = f"{comment_block}\n\n" if comments else ""
    return (
        f"{prefix}#include <stdio.h>\n\n"
        "int main(void) {\n"
        f'    printf("{text}");\n'
        "    return 0;\n"
        "}\n"
    )


def analyze_code(
    source: str,
    language: Language,
    program_goal: str = "",
    simulation_input: str = "",
    expected_output: str | None = None,
) -> AnalysisResult:
    if language == "python":
        return _analyze_python(
            source,
            program_goal,
            simulation_input,
            expected_output,
        )
    if language == "c":
        return _analyze_c(source, program_goal, simulation_input, expected_output)
    raise ValueError(f"Unsupported language: {language}")
