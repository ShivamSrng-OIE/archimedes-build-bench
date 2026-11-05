"""Single source of truth for architecture-name normalization."""

from __future__ import annotations

import re

from ..schemas import Architecture, ArchitectureIdentity

_ALIASES = {
    "x86_64": Architecture.X86_64,
    "x86-64": Architecture.X86_64,
    "amd64": Architecture.X86_64,
    "x64": Architecture.X86_64,
    "aarch64": Architecture.AARCH64,
    "arm64": Architecture.AARCH64,
    "riscv64": Architecture.RISCV64,
    "riscv64gc": Architecture.RISCV64,
}


def normalize_architecture(raw: str) -> ArchitectureIdentity:
    """Normalize known aliases while retaining an unknown raw identity as evidence."""
    cleaned = re.sub(r"\s+", "", raw.strip().lower()).replace("_unknown", "")
    canonical = _ALIASES.get(cleaned, Architecture.UNKNOWN)
    return ArchitectureIdentity(canonical=canonical, raw=raw, aliases_observed=[cleaned])
