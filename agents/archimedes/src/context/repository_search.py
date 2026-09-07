"""Deterministic bounded repository inventory and source search APIs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..constants import MAX_TEXT_FILE_BYTES

_BUILD_FILES = {
    "CMakeLists.txt", "Makefile", "configure", "configure.ac", "meson.build",
    "BUILD", "BUILD.bazel", "WORKSPACE", "Cargo.toml", "pyproject.toml", "setup.py",
    "rules", "configure.in", "meson_options.txt",
}
_SKIP = {".git", ".hg", ".svn", "node_modules", "__pycache__"}


@dataclass(frozen=True, slots=True)
class SearchSnippet:
    path: str
    line_start: int
    line_end: int
    text: str
    match_reason: str
    score: float


@dataclass(slots=True)
class RepositoryInventory:
    files: list[str] = field(default_factory=list)
    extensions: dict[str, int] = field(default_factory=dict)
    build_files: list[str] = field(default_factory=list)
    packaging_files: list[str] = field(default_factory=list)
    test_locations: list[str] = field(default_factory=list)
    source_directories: list[str] = field(default_factory=list)
    architecture_directories: list[str] = field(default_factory=list)


def inventory_repository(repo: Path, *, max_files: int = 20_000) -> RepositoryInventory:
    """Create sorted structural context without following external symlink content."""
    inventory = RepositoryInventory()
    dirs: set[str] = set()
    for path in sorted(repo.rglob("*")):
        if len(inventory.files) >= max_files:
            break
        if any(part in _SKIP for part in path.parts) or path.is_symlink() or not path.is_file():
            continue
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
        if re.search(r"(?:x86|amd64|arm|aarch|riscv|ppc|powerpc|loong|generic|portable)", directory, re.I)
    )
    return inventory


def search_regex(
    repo: Path, pattern: str, *, reason: str = "regex", max_results: int = 50
) -> list[SearchSnippet]:
    """Return bounded source windows for a caller-supplied regular expression."""
    regex = re.compile(pattern, re.I)
    results: list[SearchSnippet] = []
    for path in sorted(repo.rglob("*")):
        if len(results) >= max_results:
            break
        if path.is_symlink() or not path.is_file() or any(part in _SKIP for part in path.parts):
            continue
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


def find_exact_text(repo: Path, text: str, *, max_results: int = 50) -> list[SearchSnippet]:
    return search_regex(repo, re.escape(text), reason=f"exact text: {text}", max_results=max_results)


def find_portable_fallbacks(repo: Path, *, max_results: int = 50) -> list[SearchSnippet]:
    terms = r"(?<![A-Za-z0-9])(?:generic|portable|fallback|scalar|reference|software|no[_-]?simd)(?![A-Za-z0-9])"
    return search_regex(repo, terms, reason="portable fallback", max_results=max_results)
