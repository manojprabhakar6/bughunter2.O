import ast


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
