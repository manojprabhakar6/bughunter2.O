import re
import subprocess
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from app.analyzers.analyzer_types import AnalysisResult, AnalyzerUnavailable
from app.analyzers.bug_detector import _repair_c_goal_logic
from app.schemas import Finding, SimulationResult
from app.simulator import verify_expected_output

ANALYZER_PROCESS_TIMEOUT_SECONDS = 30
C_DIAGNOSTIC = re.compile(
    r":(?P<line>\d+):(?P<column>\d+): "
    r"(?P<severity>fatal error|error|warning|note): (?P<message>.+)"
)


def _analyze_c(
    source: str,
    program_goal: str,
    simulation_input: str,
    expected_output: str | None,
    *,
    compiler: str | None,
    sandbox_runner,
) -> AnalysisResult:
    if compiler is None:
        raise AnalyzerUnavailable(
            "C analysis requires GCC or Clang to be installed on the server."
        )

    with tempfile.TemporaryDirectory(prefix="bug-hunter-") as directory:
        code_path = Path(directory) / "submission.c"
        code_path.write_text(source, encoding="utf-8")
        initial_result = _run_c_compiler(compiler, code_path)
        result = initial_result
        corrected_code = source
        observed_findings = _c_findings(initial_result.stderr)

        # A file may contain many independent syntax mistakes. Keep repairing
        # compiler-confirmed issues with a hard cap for predictable latency.
        for _ in range(32):
            if result.returncode == 0:
                break
            candidate = _repair_c_compiler_diagnostic(corrected_code, result.stderr)
            if candidate is None or candidate == corrected_code:
                break
            code_path.write_text(candidate, encoding="utf-8")
            candidate_result = _run_c_compiler(compiler, code_path)
            observed_findings.extend(_c_findings(candidate_result.stderr))
            current_error_signatures = Counter(
                (finding.code, finding.message)
                for finding in _c_findings(result.stderr)
                if finding.code in {"C_ERROR", "C_FATAL_ERROR"}
            )
            candidate_error_signatures = Counter(
                (finding.code, finding.message)
                for finding in _c_findings(candidate_result.stderr)
                if finding.code in {"C_ERROR", "C_FATAL_ERROR"}
            )
            current_warnings = sum(
                finding.code == "C_WARNING" for finding in _c_findings(result.stderr)
            )
            candidate_warnings = sum(
                finding.code == "C_WARNING"
                for finding in _c_findings(candidate_result.stderr)
            )
            # A successful repair can expose errors hidden behind the first
            # parser failure, so accept it when the reported error changes or
            # a warning was removed, even if GCC reports more errors next.
            if (
                candidate_result.returncode == 0
                or any(
                    candidate_error_signatures[signature] < count
                    for signature, count in current_error_signatures.items()
                )
                or candidate_warnings < current_warnings
            ):
                corrected_code = candidate
                result = candidate_result
            else:
                break

        candidate_compiled = result.returncode == 0
        logic_findings: list[Finding] = []
        if result.returncode != 0:
            candidate = _repair_incomplete_c_printf(corrected_code)
            if candidate is not None:
                code_path.write_text(candidate, encoding="utf-8")
                repaired_result = _run_c_compiler(compiler, code_path)
                observed_findings.extend(_c_findings(repaired_result.stderr))
                if repaired_result.returncode == 0:
                    corrected_code = candidate
                    result = repaired_result
                    candidate_compiled = True

        for _ in range(16):
            if result.returncode != 0:
                break
            candidate = _repair_c_warning_diagnostic(corrected_code, result.stderr)
            if candidate is None or candidate == corrected_code:
                break
            code_path.write_text(candidate, encoding="utf-8")
            candidate_result = _run_c_compiler(compiler, code_path)
            observed_findings.extend(_c_findings(candidate_result.stderr))
            current_warnings = sum(
                finding.code == "C_WARNING" for finding in _c_findings(result.stderr)
            )
            candidate_warnings = sum(
                finding.code == "C_WARNING"
                for finding in _c_findings(candidate_result.stderr)
            )
            if candidate_result.returncode == 0 and candidate_warnings < current_warnings:
                corrected_code = candidate
                result = candidate_result
                candidate_compiled = True
            else:
                break

        if result.returncode == 0:
            candidate, proposed_logic_findings = _repair_c_goal_logic(
                corrected_code,
                program_goal,
            )
            if candidate != corrected_code:
                code_path.write_text(candidate, encoding="utf-8")
                candidate_result = _run_c_compiler(compiler, code_path)
                observed_findings.extend(_c_findings(candidate_result.stderr))
                if candidate_result.returncode == 0:
                    corrected_code = candidate
                    result = candidate_result
                    candidate_compiled = True
                    logic_findings = proposed_logic_findings
                else:
                    logic_findings = [
                        finding.model_copy(
                            update={
                                "message": (
                                    f"{finding.message} The C compiler rejected "
                                    "the proposed edit."
                                ),
                                "fixed": False,
                            }
                        )
                        for finding in proposed_logic_findings
                    ]

    if result.returncode != 0 and not observed_findings and not logic_findings:
        raise AnalyzerUnavailable(
            "The C compiler failed without a readable diagnostic."
        )

    final_c_findings = _c_findings(result.stderr)
    final_counts = Counter((finding.code, finding.message) for finding in final_c_findings)
    findings: list[Finding] = []
    observed_signatures: set[tuple[str, str, int | None, int | None]] = set()
    for finding in observed_findings:
        unique_signature = (finding.code, finding.message, finding.line, finding.column)
        if unique_signature in observed_signatures:
            continue
        observed_signatures.add(unique_signature)
        signature = (finding.code, finding.message)
        still_present = final_counts[signature] > 0
        if still_present:
            final_counts[signature] -= 1
        findings.append(
            finding.model_copy(
                update={
                    "fixed": not still_present,
                    "verified": False if not still_present else None,
                }
            )
        )
    findings.extend(logic_findings)

    if result.returncode == 0 or candidate_compiled:
        simulation = sandbox_runner(corrected_code, simulation_input)
        if expected_output is not None:
            simulation = verify_expected_output(simulation, expected_output)
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


def _repair_c_compiler_diagnostic(source: str, output: str) -> str | None:
    """Apply one narrow C repair suggested by a compiler diagnostic."""
    lines = source.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    for output_line in output.splitlines():
        match = C_DIAGNOSTIC.search(output_line)
        if match is None or match.group("severity") not in {"error", "fatal error"}:
            continue
        line_number = int(match.group("line"))
        column = int(match.group("column"))
        message = match.group("message")
        line_index = line_number - 1
        if not 0 <= line_index < len(lines):
            continue

        if "end of input" in message:
            candidate = _repair_missing_c_closing_braces(source)
            if candidate is not None:
                return candidate

        if "expected" in message and ";" in message and "before" in message:
            previous = line_index - 1
            while previous >= 0 and not lines[previous].strip():
                previous -= 1
            if previous >= 0:
                statement = lines[previous].rstrip("\r\n")
                stripped = statement.rstrip()
                if (
                    stripped
                    and not stripped.startswith("#")
                    and not stripped.endswith((";", "{", "}", ":"))
                    and "//" not in stripped
                ):
                    insertion = len(statement.rstrip())
                    lines[previous] = (
                        statement[:insertion]
                        + ";"
                        + statement[insertion:]
                        + lines[previous][len(statement):]
                    )
                    return "".join(lines)

            # A compact one-line program can have the statement and the next
            # token on the same source line. Insert at the compiler location.
            token_before = re.search(r"before\s+['‘](?P<token>[^'’]+)['’]", message)
            if token_before is None:
                continue
            next_token = token_before.group("token")
            line = lines[line_index]
            target = max(0, column - 1)
            if not line.startswith(next_token, target):
                target = line.find(next_token, target)
            if target < 0:
                continue
            insertion = len(line[:target].rstrip())
            lines[line_index] = (
                line[:insertion] + ";" + line[insertion:]
            )
            return "".join(lines)

        if (
            "expected '}' before 'else'" in message
            or 'expected "}" before "else"' in message
        ):
            line = lines[line_index]
            else_match = re.search(r"\belse\b", line)
            if else_match is not None:
                indent = line[: len(line) - len(line.lstrip(" \t"))]
                insertion = line_offsets[line_index] + else_match.start()
                return source[:insertion] + "}\n" + indent + source[insertion:]

        if "lvalue required as left operand of assignment" in message:
            line = lines[line_index]
            assignment = re.search(
                r"(?P<left>[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*%\s*\d+)\s*=(?!=)",
                line,
            )
            if assignment is not None:
                offset = line_offsets[line_index] + assignment.end() - 1
                return source[:offset] + "==" + source[offset + 1 :]

        undeclared = re.search(r"['‘](\w+)['’].*undeclared", message)
        if undeclared is None:
            continue
        unknown_name = undeclared.group(1)
        line = lines[line_index]
        start = column - 1
        token_match = re.match(re.escape(unknown_name) + r"\b", line[start:])
        if token_match is None:
            continue
        suggestion = re.search(r"did you mean ['‘](\w+)['’]", message)
        if suggestion is not None:
            replacement = suggestion.group(1)
        else:
            declared_names = set(
                re.findall(
                    r"\b(?:unsigned\s+|signed\s+)?(?:char|short|int|long|float|double|size_t|bool|_Bool)\s+\*?\s*(\w+)",
                    source,
                )
            )
            close_matches = [
                name
                for name in declared_names
                if name != unknown_name
                and SequenceMatcher(None, unknown_name, name).ratio() >= 0.75
            ]
            if len(close_matches) != 1:
                unused_names = set(
                    re.findall(r"unused variable ['‘](\w+)['’]", output)
                )
                if len(unused_names) != 1:
                    continue
                replacement = next(iter(unused_names))
                if replacement not in declared_names:
                    continue
            else:
                replacement = close_matches[0]
        start_offset = line_offsets[line_index] + start
        end_offset = start_offset + len(unknown_name)
        return source[:start_offset] + replacement + source[end_offset:]

    return None


def _unmatched_c_open_braces(source: str) -> list[tuple[int, int, int]]:
    stack: list[tuple[int, int, int]] = []
    state = "code"
    escaped = False
    index = 0
    line_number = 1
    line_start = 0
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if current in "\r\n":
                state = "code"
        elif state == "block_comment":
            if current == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"string", "character"}:
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif (state == "string" and current == '"') or (
                state == "character" and current == "'"
            ):
                state = "code"
        elif current == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif current == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif current == '"':
            state = "string"
        elif current == "'":
            state = "character"
        elif current == "{":
            line_prefix = source[line_start:index]
            indent = len(line_prefix) - len(line_prefix.lstrip(" \t"))
            stack.append((index, line_number, indent))
        elif current == "}" and stack:
            stack.pop()
        if current == "\n":
            line_number += 1
            line_start = index + 1
        index += 1
    return stack


def _repair_missing_c_closing_braces(source: str) -> str | None:
    unmatched = _unmatched_c_open_braces(source)
    if not unmatched:
        return None

    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    for line_index, line in enumerate(lines):
        return_line = re.match(r"^(?P<indent>[ \t]*)return\b", line)
        if return_line is None:
            continue
        return_offset = offsets[line_index]
        opens_before_return = _unmatched_c_open_braces(source[:return_offset])
        if len(opens_before_return) < 2:
            continue
        _, _, opener_indent = opens_before_return[-1]
        indent = return_line.group("indent")
        if len(indent) > opener_indent:
            continue
        repaired = (
            source[:return_offset]
            + indent
            + "}\n"
            + source[return_offset:]
        )
        remaining = len(_unmatched_c_open_braces(repaired))
        if remaining:
            ending = "" if repaired.endswith(("\n", "\r")) else "\n"
            repaired += ending + ("}" * remaining) + "\n"
        return repaired

    ending = "" if source.endswith(("\n", "\r")) else "\n"
    return source + ending + ("}" * len(unmatched)) + "\n"


def _repair_c_warning_diagnostic(source: str, output: str) -> str | None:
    """Repair a small set of unambiguous type and condition warnings."""
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    for output_line in output.splitlines():
        diagnostic = C_DIAGNOSTIC.search(output_line)
        if diagnostic is None or diagnostic.group("severity") != "warning":
            continue
        line_index = int(diagnostic.group("line")) - 1
        if not 0 <= line_index < len(lines):
            continue
        line = lines[line_index]
        message = diagnostic.group("message")

        if "format '%d' expects argument of type 'int *'" in message:
            scanf_call = re.search(
                r'scanf\s*\(\s*"(?:[^"\\]|\\.)*%d(?:[^"\\]|\\.)*"\s*,\s*(?P<name>[A-Za-z_]\w*)\s*\)',
                line,
            )
            if scanf_call is not None:
                start = offsets[line_index] + scanf_call.start("name")
                end = offsets[line_index] + scanf_call.end("name")
                return source[:start] + "&" + source[start:end] + source[end:]

        if "initialization makes integer from pointer" in message:
            numeric_string = re.search(
                r"\b(?:signed\s+|unsigned\s+)?(?:short\s+|long\s+long\s+|long\s+)?"
                r"int\s+\w+\s*=\s*\"(?P<value>[+-]?\d+)\"",
                line,
            )
            if numeric_string is not None:
                start = offsets[line_index] + numeric_string.start("value") - 1
                end = offsets[line_index] + numeric_string.end("value") + 1
                return source[:start] + numeric_string["value"] + source[end:]

        if "assignment used as truth value" in message or (
            "suggest parentheses around assignment" in message
        ):
            assignment = re.search(
                r"\bif\s*\(\s*(?P<name>[A-Za-z_]\w*)\s*=(?!=)",
                line,
            )
            if assignment is not None:
                offset = offsets[line_index] + assignment.end() - 1
                return source[:offset] + "==" + source[offset + 1 :]

    return None


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
