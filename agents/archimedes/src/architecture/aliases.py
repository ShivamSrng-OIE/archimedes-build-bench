"""The one place that maps raw architecture names such as ``amd64`` to canonical ones."""

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
    """Map a raw architecture name onto its canonical ``Architecture``.

    The name is lower-cased, whitespace is removed and any ``_unknown`` suffix
    is dropped before lookup. Unrecognised names become
    ``Architecture.UNKNOWN``, and the raw text is kept so later stages can
    still report what the Case said.

    Args:
        raw: The architecture name as found in the Case metadata.

    Returns:
        The canonical architecture, the raw name, and the cleaned name in
        ``aliases_observed``.
    """
    cleaned = re.sub(r"\s+", "", raw.strip().lower()).replace("_unknown", "")
    canonical = _ALIASES.get(cleaned, Architecture.UNKNOWN)
    return ArchitectureIdentity(canonical=canonical, raw=raw, aliases_observed=[cleaned])
