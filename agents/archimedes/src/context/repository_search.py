"""Sorted, size-limited inventory of the worktree and regex search over its text files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..constants import MAX_TEXT_FILE_BYTES
from ..workspace.repository_files import MAX_REPOSITORY_FILES, iter_repository_files

_BUILD_FILES = {
    "CMakeLists.txt", "Makefile", "configure", "configure.ac", "meson.build",
    "BUILD", "BUILD.bazel", "WORKSPACE", "Cargo.toml", "pyproject.toml", "setup.py",
    "rules", "configure.in", "meson_options.txt",
}
@dataclass(frozen=True, slots=True)
class SearchSnippet:
    """A few lines of source around one search match.

    Attributes:
        path: Repository-relative POSIX path of the file.
        line_start: 1-based first line of the window.
        line_end: 1-based last line of the window.
        text: The window's lines, cut to 5,000 characters.
        match_reason: Why the search was run, shown in prompt headers.
        score: Ranking score; earlier matches score higher, never below 0.1.
    """

    path: str
    line_start: int
    line_end: int
    text: str
    match_reason: str
    score: float


@dataclass(slots=True)
class RepositoryInventory:
    """A structural summary of the worktree.

    All paths are repository-relative and use ``/``.

    Attributes:
        files: Every regular file visited, in walk order.
        extensions: File count per lower-cased suffix; ``<none>`` for files
            without one.
        build_files: Build-system files such as ``CMakeLists.txt``,
            ``Makefile*``, ``configure.ac``, ``meson.build``, ``debian/rules``,
            ``*.cmake`` and ``*.spec``.
        packaging_files: RPM ``.spec`` files and everything under ``debian/``.
        test_locations: Files with a path component named ``test``.
        source_directories: Directories named ``src``, ``source``, ``lib`` or
            ``include``.
        architecture_directories: Directories whose path mentions an
            architecture or a generic or portable variant.
    """

    files: list[str] = field(default_factory=list)
    extensions: dict[str, int] = field(default_factory=dict)
    build_files: list[str] = field(default_factory=list)
    packaging_files: list[str] = field(default_factory=list)
    test_locations: list[str] = field(default_factory=list)
    source_directories: list[str] = field(default_factory=list)
    architecture_directories: list[str] = field(default_factory=list)


def inventory_repository(repo: Path, *, max_files: int = 20_000) -> RepositoryInventory:
    """Walk the worktree and sort its files into build, packaging and test groups.

    Symlinks are never followed; see ``iter_repository_files``.

    Args:
        repo: The worktree root.
        max_files: Stop after this many files; 20,000 by default.

    Returns:
        The inventory.
    """
    inventory = RepositoryInventory()
    dirs: set[str] = set()
    for path in iter_repository_files(repo, max_files=max_files):
        relative = path.relative_to(repo).as_posix()
        inventory.files.append(relative)
        suffix = path.suffix.lower() or "<none>"
        inventory.extensions[suffix] = inventory.extensions.get(suffix, 0) + 1
        dirs.add(path.parent.relative_to(repo).as_posix())
        if path.name in _BUILD_FILES or path.suffix in {".cmake", ".spec"} or path.name.startswith("Makefile"):
            inventory.build_files.append(relative)
        if path.suffix == ".spec" or relative.startswith("debian/"):
            inventory.packaging_files.append(relative)
        if "test" in {part.lower() for part in path.parts}:
            inventory.test_locations.append(relative)
    inventory.source_directories = sorted(
        directory for directory in dirs if directory.split("/")[-1].lower() in {"src", "source", "lib", "include"}
    )
    inventory.architecture_directories = sorted(
        directory for directory in dirs
        if re.search(r"(?:x86|amd64|arm|aarch|riscv|ppc|powerpc|loong|generic|portable)", directory, re.IGNORECASE)
    )
    return inventory


def search_regex(
    repo: Path, pattern: str, *, reason: str = "regex", max_results: int = 50
) -> list[SearchSnippet]:
    """Search the worktree's text files for a case-insensitive pattern.

    Each matching line yields a window of two lines before and after it. Files
    over 2,000,000 bytes, binary files and unreadable files are skipped.

    Args:
        repo: The worktree root.
        pattern: Regular expression, matched line by line, ignoring case.
        reason: Label stored in each snippet's ``match_reason``.
        max_results: Stop after this many matches; 50 by default.

    Returns:
        The snippets in file and line order.
    """
    regex = re.compile(pattern, re.IGNORECASE)
    results: list[SearchSnippet] = []
    for path in iter_repository_files(repo, max_files=MAX_REPOSITORY_FILES):
        if len(results) >= max_results:
            break
        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if regex.search(line):
                start, end = max(0, index - 2), min(len(lines), index + 3)
                results.append(SearchSnippet(
                    path.relative_to(repo).as_posix(), start + 1, end,
                    "\n".join(lines[start:end])[:5000], reason,
                    max(0.1, 1.0 - len(results) * 0.01),
                ))
                if len(results) >= max_results:
                    break
    return results
