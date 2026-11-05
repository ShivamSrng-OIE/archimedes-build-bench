"""Pack search snippets into prompt text, each with a header naming its file and line."""

from __future__ import annotations

from .repository_search import SearchSnippet


def select_snippets(snippets: list[SearchSnippet], *, max_characters: int = 16_000) -> str:
    """Join the best snippets into one text block within a character limit.

    Snippets are taken by descending score, then path and line. A snippet that
    does not fit is skipped whole rather than cut, so every included block
    keeps its ``--- path:line (reason) ---`` header.

    Args:
        snippets: Candidate snippets.
        max_characters: Largest total size; 16,000 by default.

    Returns:
        The selected blocks separated by blank lines, or an empty string.
    """
    selected: list[str] = []
    used = 0
    for snippet in sorted(snippets, key=lambda item: (-item.score, item.path, item.line_start)):
        block = f"--- {snippet.path}:{snippet.line_start} ({snippet.match_reason}) ---\n{snippet.text}\n"
        if used + len(block) > max_characters:
            continue
        selected.append(block)
        used += len(block)
    return "\n".join(selected)
