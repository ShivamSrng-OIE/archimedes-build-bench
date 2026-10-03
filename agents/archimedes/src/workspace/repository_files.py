"""Deterministic, bounded traversal of untrusted package repositories."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path


SKIP_DIRECTORIES = {".git", ".hg", ".svn", "node_modules", "__pycache__"}
MAX_REPOSITORY_FILES = 20_000


def iter_repository_files(
    repo: Path, *, max_files: int = MAX_REPOSITORY_FILES
) -> Iterator[Path]:
    """Yield regular non-symlink files without materializing or escaping the tree."""
    if max_files <= 0:
        return
    root = repo.resolve(strict=True)
    yielded = 0
    for current, directories, filenames in os.walk(
        root, topdown=True, onerror=lambda _error: None, followlinks=False
    ):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(directories):
            candidate = current_path / name
            try:
                if name.lower() not in SKIP_DIRECTORIES and not candidate.is_symlink():
                    safe_directories.append(name)
            except OSError:
                continue
        directories[:] = safe_directories
        for name in sorted(filenames):
            path = current_path / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
            except OSError:
                continue
            yield path
            yielded += 1
            if yielded >= max_files:
                return
