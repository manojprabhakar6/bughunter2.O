import ast
import builtins
import io
import json
import re
import tokenize
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher

from app.analyzers.source_utils import _apply_source_edits, _ast_character_offset, _source_offset
from app.schemas import Finding


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

    dictionary_fields: dict[str, set[str]] = {}
    dictionary_field_values: dict[tuple[str, str], str] = {}
    for node in nodes:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        keys = {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            dictionary_fields[target.id] = keys
            for key, value in zip(node.value.keys, node.value.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Name)
                ):
                    dictionary_field_values[(target.id, key.value)] = value.id

    for node in nodes:
        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            continue
        known_keys = dictionary_fields.get(node.value.id)
        if not known_keys or node.slice.value in known_keys:
            continue
        candidates = [
            key
            for key in known_keys
            if SequenceMatcher(None, node.slice.value, key).ratio() >= 0.75
        ]
        if len(candidates) != 1:
            continue
        corrected_key = candidates[0]
        edits.append(
            (
                _ast_character_offset(source, node.slice, line_offsets=line_offsets),
                _ast_character_offset(
                    source, node.slice, end=True, line_offsets=line_offsets
                ),
                json.dumps(corrected_key),
            )
        )
        findings.append(
            Finding(
                code="LOGIC_DICTIONARY_KEY_MISMATCH",
                message=(
                    f"Changed dictionary key '{node.slice.value}' to '{corrected_key}' "
                    "to match the keys defined for this record."
                ),
                line=node.lineno,
                column=node.col_offset + 1,
                fixed=True,
                verified=False,
            )
        )

    defined_names = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    defined_names.update(
        node.name
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    defined_names.update(
        argument.arg
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    )
    for node in nodes:
        if isinstance(node, ast.Import):
            defined_names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            defined_names.update(alias.asname or alias.name for alias in node.names)

    undefined_names = {
        node.id
        for node in nodes
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in defined_names
        and not hasattr(builtins, node.id)
    }
    average_goal = bool(re.search(r"\b(average|mean)\b", goal))
    zero_division_targets = {
        target.id
        for node in nodes
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.BinOp)
        and isinstance(node.value.op, ast.Div)
        and isinstance(node.value.right, ast.Constant)
        and type(node.value.right.value) is int
        and node.value.right.value == 0
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for unknown_name in undefined_names:
        close_matches = [
            name
            for name in defined_names
            if name != unknown_name
            and SequenceMatcher(None, unknown_name, name).ratio() >= 0.75
        ]
        if len(close_matches) != 1:
            continue
        replacement = close_matches[0]
        if average_goal and replacement in zero_division_targets:
            # The average-specific repair below considers this pair together
            # with the division-by-zero correction to avoid contradictory edits.
            continue
        for node in nodes:
            if isinstance(node, ast.Name) and node.id == unknown_name:
                edits.append(
                    (
                        _ast_character_offset(source, node, line_offsets=line_offsets),
                        _ast_character_offset(
                            source, node, end=True, line_offsets=line_offsets
                        ),
                        replacement,
                    )
                )
        findings.append(
            Finding(
                code="LOGIC_UNDEFINED_NAME_TYPO",
                message=(
                    f"Changed undefined name '{unknown_name}' to the unique close "
                    f"match '{replacement}'. Review this inferred spelling fix."
                ),
                line=next(
                    node.lineno
                    for node in nodes
                    if isinstance(node, ast.Name)
                    and node.id == unknown_name
                    and isinstance(node.ctx, ast.Load)
                ),
                fixed=True,
                verified=False,
            )
        )

    numeric_record_fields: set[tuple[str, str]] = set()
    numeric_input_types: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for index, operand in enumerate(operands):
            if not isinstance(operand, ast.Name):
                continue
            neighbor = operands[index - 1] if index else None
            if neighbor is None and index + 1 < len(operands):
                neighbor = operands[index + 1]
            if not (
                isinstance(neighbor, ast.Constant)
                and type(neighbor.value) in {int, float}
            ):
                continue
            detected_type = "int" if type(neighbor.value) is int else "float"
            if numeric_input_types.get(operand.id) == "float" or detected_type == "float":
                numeric_input_types[operand.id] = "float"
            else:
                numeric_input_types[operand.id] = "int"

    for node in nodes:
        if isinstance(node, ast.Compare):
            has_number = any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, int | float)
                for value in ast.walk(node)
            )
            if has_number:
                numeric_record_fields.update(
                    (value.value.id, value.slice.value)
                    for value in ast.walk(node)
                    if isinstance(value, ast.Subscript)
                    and isinstance(value.value, ast.Name)
                    and isinstance(value.slice, ast.Constant)
                    and isinstance(value.slice.value, str)
                )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if any(
                isinstance(value, ast.Name) and value.id in {"total", "sum"}
                for value in ast.walk(node)
            ):
                numeric_record_fields.update(
                    (value.value.id, value.slice.value)
                    for value in ast.walk(node)
                    if isinstance(value, ast.Subscript)
                    and isinstance(value.value, ast.Name)
                    and isinstance(value.slice, ast.Constant)
                    and isinstance(value.slice.value, str)
                )

    for node in nodes:
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "input"
        ):
            continue
        variable = node.targets[0].id
        matching_fields = [
            (record, field)
            for (record, field), value_name in dictionary_field_values.items()
            if value_name == variable
            and (record, field) in numeric_record_fields
        ]
        if not matching_fields and variable not in numeric_input_types:
            continue
        conversion = numeric_input_types.get(variable) or (
            "int" if any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "int"
                for call in nodes
            ) else "float"
        )
        start = _ast_character_offset(source, node.value, line_offsets=line_offsets)
        end = _ast_character_offset(
            source, node.value, end=True, line_offsets=line_offsets
        )
        edits.extend(((start, start, f"{conversion}("), (end, end, ")")))
        findings.append(
            Finding(
                code="LOGIC_INPUT_TYPE_MISMATCH",
                message=(
                    f"Converted input for '{variable}' to {conversion} because the "
                    "record field is later used in numeric operations."
                ),
                line=node.lineno,
                column=node.col_offset + 1,
                fixed=True,
                verified=False,
            )
        )

    has_average_function = any(
        isinstance(node, ast.FunctionDef)
        and any(word in node.name.casefold() for word in ("average", "mean"))
        for node in nodes
    )
    if re.search(r"\b(average|mean)\b", goal) or has_average_function:
        for node in nodes:
            if not (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Call)
                and isinstance(node.right.func, ast.Name)
                and node.right.func.id == "len"
                and len(node.right.args) == 1
                and isinstance(node.right.args[0], ast.Name)
            ):
                continue
            collection = node.right.args[0].id
            if any(
                isinstance(parent, ast.IfExp)
                and parent.body is node
                and isinstance(parent.test, ast.Name)
                and parent.test.id == collection
                for parent in nodes
            ):
                continue
            if not any(
                isinstance(loop, ast.For)
                and isinstance(loop.iter, ast.Name)
                and loop.iter.id == collection
                for loop in nodes
            ):
                continue
            start = _ast_character_offset(source, node, line_offsets=line_offsets)
            end = _ast_character_offset(
                source, node, end=True, line_offsets=line_offsets
            )
            expression = source[start:end]
            edits.append((start, end, f"({expression} if {collection} else 0)"))
            findings.append(
                Finding(
                    code="LOGIC_EMPTY_COLLECTION_AVERAGE",
                    message=(
                        f"Added an empty-{collection} check to prevent division by "
                        "zero when calculating an average. The fallback is 0; review "
                        "whether that is the desired behavior."
                    ),
                    line=node.lineno,
                    column=node.col_offset + 1,
                    fixed=True,
                    verified=False,
                )
            )

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
        average_findings: list[Finding] = []
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
        if not has_mean_call:
            for node in nodes:
                if not (
                    isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.Div)
                    and isinstance(node.right, ast.Constant)
                    and type(node.right.value) is int
                    and node.right.value == 0
                ):
                    continue

                def additive_names(expression: ast.AST) -> list[str] | None:
                    if isinstance(expression, ast.Name):
                        return [expression.id]
                    if isinstance(expression, ast.BinOp) and isinstance(
                        expression.op, ast.Add
                    ):
                        left_names = additive_names(expression.left)
                        right_names = additive_names(expression.right)
                        if left_names is not None and right_names is not None:
                            return left_names + right_names
                    return None

                values = additive_names(node.left)
                distinct_values = list(dict.fromkeys(values or []))
                if values is None or len(distinct_values) < 2:
                    continue

                edits.append(
                    (
                        _ast_character_offset(
                            source,
                            node.right,
                            line_offsets=line_offsets,
                        ),
                        _ast_character_offset(
                            source,
                            node.right,
                            end=True,
                            line_offsets=line_offsets,
                        ),
                        str(len(values)),
                    )
                )
                average_findings.append(
                    Finding(
                        code="LOGIC_GOAL_AVERAGE_REVIEW",
                        message=(
                            "Changed division by zero to divide the detected sum by "
                            f"its {len(values)} input values. This goal-based fix is "
                            "best-effort; verify the intended inputs and behavior."
                        ),
                        line=node.lineno,
                        column=node.col_offset + 1,
                        fixed=True,
                        verified=False,
                    )
                )
                break

            if average_findings:
                assigned = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                }
                loaded = {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                }
                misspelled_references = loaded - assigned
                for assignment in nodes:
                    if not (
                        isinstance(assignment, ast.Assign)
                        and len(assignment.targets) == 1
                        and isinstance(assignment.targets[0], ast.Name)
                        and isinstance(assignment.value, ast.BinOp)
                        and isinstance(assignment.value.op, ast.Div)
                        and isinstance(assignment.value.right, ast.Constant)
                        and type(assignment.value.right.value) is int
                        and assignment.value.right.value == 0
                    ):
                        continue
                    assigned_name = assignment.targets[0].id
                    close_matches = [
                        name
                        for name in misspelled_references
                        if name != assigned_name
                        and SequenceMatcher(None, assigned_name, name).ratio() >= 0.75
                    ]
                    if len(close_matches) != 1:
                        continue
                    intended_name = close_matches[0]
                    for name_node in ast.walk(tree):
                        if isinstance(name_node, ast.Name) and name_node.id == assigned_name:
                            edits.append(
                                (
                                    _ast_character_offset(
                                        source,
                                        name_node,
                                        line_offsets=line_offsets,
                                    ),
                                    _ast_character_offset(
                                        source,
                                        name_node,
                                        end=True,
                                        line_offsets=line_offsets,
                                    ),
                                    intended_name,
                                )
                            )
                    average_findings.append(
                        Finding(
                            code="LOGIC_GOAL_AVERAGE_REVIEW",
                            message=(
                                f"Matched the assigned name '{assigned_name}' to the "
                                f"referenced name '{intended_name}' in the average "
                                "calculation. Review this inferred name correction."
                            ),
                            line=assignment.targets[0].lineno,
                            column=assignment.targets[0].col_offset + 1,
                            fixed=True,
                            verified=False,
                        )
                    )
                    break

            findings.extend(average_findings)

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
