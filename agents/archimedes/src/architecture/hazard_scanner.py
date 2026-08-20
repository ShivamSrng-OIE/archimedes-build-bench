"""Bounded static scanning for architecture-sensitive source constructs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..constants import MAX_TEXT_FILE_BYTES


@dataclass(frozen=True, slots=True)
class ArchitectureHazard:
    path: str
    line: int
    category: str
    matched_text: str
    excerpt: str
    confidence: float


_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("compiler_flag", re.compile(r"-(?:march|mcpu|mtune|mabi|mfpu)=[^\s'\"]+|-m(?:sse\d*(?:\.\d)?|avx\d*|neon)\b", re.I), 0.88),
    ("architecture_macro", re.compile(r"__(?:x86_64|amd64|i386|aarch64|arm|riscv|riscv_xlen)__?", re.I), 0.86),
    ("packaging_conditional", re.compile(r"%(?:ifarch|ifnarch)\b|(?:target|host)_arch\b", re.I), 0.82),
    ("intrinsic_header", re.compile(r"(?:immintrin|x86intrin|emmintrin|xmmintrin|arm_neon|riscv_vector)\.h", re.I), 0.92),
    ("inline_assembly", re.compile(r"\b(?:__asm__|asm)\s*(?:volatile\s*)?\("), 0.76),
    ("abi_assumption", re.compile(r"sizeof\s*\(\s*(?:long|void\s*\*)\s*\)|__int128|(?:little|big)[_-]?endian|atomic.*(?:32|64|128)", re.I), 0.68),
)

_SKIP_PARTS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}


def _is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def scan_architecture_hazards(repo: Path, *, max_results: int = 300) -> list[ArchitectureHazard]:
    """Scan regular text files deterministically without executing repository content."""
    hazards: list[ArchitectureHazard] = []
    for path in sorted(repo.rglob("*")):
        if len(hazards) >= max_results:
            break
        if not path.is_file() or any(part in _SKIP_PARTS for part in path.parts):
            continue
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
                    hazards.append(ArchitectureHazard(relative, number, category, match.group(0), line[:500], confidence))
                    if len(hazards) >= max_results:
                        break
    return hazards
