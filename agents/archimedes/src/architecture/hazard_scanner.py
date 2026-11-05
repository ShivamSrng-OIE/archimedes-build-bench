"""Regex scan of the worktree for architecture-specific constructs ("hazards").

Six categories are recognised, each with a fixed base confidence: compiler
flags, architecture macros, packaging conditionals, intrinsic headers, inline
assembly and ABI assumptions. Files are only read, never executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..constants import MAX_TEXT_FILE_BYTES
from ..workspace.repository_files import iter_repository_files


@dataclass(frozen=True, slots=True)
class ArchitectureHazard:
    """One architecture-specific construct found on one line.

    Attributes:
        path: Repository-relative POSIX path of the file.
        line: 1-based line number.
        category: One of the six hazard categories, such as ``compiler_flag``.
        matched_text: The exact text the pattern matched.
        excerpt: The whole line, cut to 500 characters.
        confidence: Base confidence for the category, from 0.68 to 0.92.
    """

    path: str
    line: int
    category: str
    matched_text: str
    excerpt: str
    confidence: float


_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "compiler_flag",
        re.compile(
            r"-(?:march|mcpu|mtune|mabi|mfpu)=[^\s'\"]+|-m(?:sse\d*(?:\.\d)?|avx\d*|neon)\b",
            re.IGNORECASE,
        ),
        0.88,
    ),
    (
        "architecture_macro",
        re.compile(r"__(?:x86_64|amd64|i386|aarch64|arm|riscv|riscv_xlen)__?", re.IGNORECASE),
        0.86,
    ),
    (
        "packaging_conditional",
        re.compile(r"%(?:ifarch|ifnarch)\b|(?:target|host)_arch\b", re.IGNORECASE),
        0.82,
    ),
    (
        "intrinsic_header",
        re.compile(
            r"(?:immintrin|x86intrin|emmintrin|xmmintrin|arm_neon|riscv_vector)\.h",
            re.IGNORECASE,
        ),
        0.92,
    ),
    ("inline_assembly", re.compile(r"\b(?:__asm__|asm)\s*(?:volatile\s*)?\("), 0.76),
    (
        "abi_assumption",
        re.compile(
            r"sizeof\s*\(\s*(?:long|void\s*\*)\s*\)|__int128|(?:little|big)[_-]?endian|atomic.*(?:32|64|128)",
            re.IGNORECASE,
        ),
        0.68,
    ),
)


def _is_probably_binary(data: bytes) -> bool:
    """Guess whether file content is binary.

    Args:
        data: The file's bytes.

    Returns:
        True if a NUL byte appears in the first 8,192 bytes.
    """
    return b"\x00" in data[:8192]


def scan_architecture_hazards(
    repo: Path, *, max_results: int = 300
) -> list[ArchitectureHazard]:
    """Find architecture hazards in the worktree's text files.

    Files are visited in sorted order; files over 2,000,000 bytes, binary files
    and unreadable files are skipped. Each line yields at most one hazard per
    category.

    Args:
        repo: The worktree root.
        max_results: Stop after this many hazards; 300 by default.

    Returns:
        The hazards in file and line order.
    """
    hazards: list[ArchitectureHazard] = []
    if max_results <= 0:
        return hazards
    for path in iter_repository_files(repo):
        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if _is_probably_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        relative = path.relative_to(repo).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for category, pattern, confidence in _PATTERNS:
                match = pattern.search(line)
                if match:
                    hazards.append(
                        ArchitectureHazard(
                            relative,
                            number,
                            category,
                            match.group(0),
                            line[:500],
                            confidence,
                        )
                    )
                    if len(hazards) >= max_results:
                        return hazards
    return hazards
