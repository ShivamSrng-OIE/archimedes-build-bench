"""Everything that touches the official workspace layout.

It resolves ``input/``, ``work/repo/`` and ``output/``, loads the Case and its
failure log, walks the repository, records which helper tools exist, and holds
the path policy that decides which files may be edited.
"""

from .paths import WorkspacePaths

__all__ = ["WorkspacePaths"]
