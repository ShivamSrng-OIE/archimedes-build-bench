"""Cheap checks run on untrusted JSON text before it is handed to ``json.loads``."""

from __future__ import annotations

MAX_JSON_NESTING = 128


def reject_excessive_json_nesting(
    text: str, *, max_depth: int = MAX_JSON_NESTING
) -> None:
    """Refuse JSON text whose arrays and objects nest deeper than a limit.

    Python's C JSON decoder does not raise ``RecursionError`` at the same depth
    on every build, so a single pass over the characters makes the limit the
    same everywhere. Brackets inside string literals are skipped. Syntax errors
    are left for the decoder to report.

    Args:
        text: The JSON text to check.
        max_depth: Deepest nesting allowed, 128 by default.

    Returns:
        None.

    Raises:
        ValueError: If the nesting goes deeper than ``max_depth``.
    """
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError(
                    f"JSON nesting exceeds the supported depth of {max_depth}"
                )
        elif character in "]}":
            depth = max(0, depth - 1)
