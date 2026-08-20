"""Small provenance-preserving model-context assembly."""

from __future__ import annotations

from .repository_search import SearchSnippet


def select_snippets(snippets: list[SearchSnippet], *, max_characters: int = 16_000) -> str:
    """Rank and pack snippets without silently truncating provenance headers."""
    selected: list[str] = []
    used = 0
    for snippet in sorted(snippets, key=lambda item: (-item.score, item.path, item.line_start)):
        block = f"--- {snippet.path}:{snippet.line_start} ({snippet.match_reason}) ---\n{snippet.text}\n"
        if used + len(block) > max_characters:
            continue
        selected.append(block)
        used += len(block)
    return "\n".join(selected)
