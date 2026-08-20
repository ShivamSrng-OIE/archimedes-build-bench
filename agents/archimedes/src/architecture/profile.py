"""Evidence-backed construction of source and target architecture profiles."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Architecture, ArchitectureFact, ArchitectureIdentity, EvidenceItem


@dataclass(slots=True)
class ArchitectureProfile:
    identity: ArchitectureIdentity
    facts: dict[str, ArchitectureFact] = field(default_factory=dict)
    ood_signals: list[str] = field(default_factory=list)


def construct_profile(
    identity: ArchitectureIdentity, evidence: list[EvidenceItem] | None = None
) -> ArchitectureProfile:
    """Construct only facts justified by architecture identity or supplied evidence."""
    items = evidence or []
    profile = ArchitectureProfile(identity=identity)
    if identity.canonical in {Architecture.X86_64, Architecture.AARCH64, Architecture.RISCV64}:
        profile.facts["word_size"] = ArchitectureFact(
            "word_size", 64, "canonical architecture identity", 0.95,
            [item.id for item in items if "architecture" in item.summary.lower()],
        )
    else:
        profile.facts["word_size"] = ArchitectureFact("word_size", None, "unknown", 0.0)
        profile.ood_signals.append("unknown architecture alias")
    profile.facts.setdefault("endianness", ArchitectureFact("endianness", None, "unknown", 0.0))
    profile.facts.setdefault("abi", ArchitectureFact("abi", None, "unknown", 0.0))
    profile.facts.setdefault("compiler_family", ArchitectureFact("compiler_family", None, "unknown", 0.0))
    return profile
