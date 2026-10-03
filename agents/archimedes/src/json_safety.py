"""Small, allocation-free guards applied before decoding untrusted JSON."""

from __future__ import annotations


MAX_JSON_NESTING = 128


def reject_excessive_json_nesting(
    text: str, *, max_depth: int = MAX_JSON_NESTING
) -> None:
    """Reject structurally deep JSON without counting brackets inside strings.

    Python's accelerated JSON decoder does not consistently raise ``RecursionError``
    at the same nesting level across builds.  A bounded lexical pass makes the
    resource limit deterministic; syntax validation remains the decoder's job.
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
