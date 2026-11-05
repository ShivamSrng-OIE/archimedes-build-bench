"""What is actually known about an architecture, kept separate from what is not."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Architecture, ArchitectureFact, ArchitectureIdentity, EvidenceItem


@dataclass(slots=True)
class ArchitectureProfile:
    """The known facts about one architecture.

    Attributes:
        identity: The architecture's canonical and raw names.
        facts: Facts by key, such as ``word_size`` or ``abi``. An unknown fact
            has the value ``None``.
        ood_signals: Reasons the architecture looks out of distribution, such
            as an unknown alias.
    """

    identity: ArchitectureIdentity
    facts: dict[str, ArchitectureFact] = field(default_factory=dict)
    ood_signals: list[str] = field(default_factory=list)


def construct_profile(
    identity: ArchitectureIdentity, evidence: list[EvidenceItem] | None = None
) -> ArchitectureProfile:
    """Build a profile that holds only facts the identity or evidence supports.

    A known 64-bit architecture gets ``word_size`` 64. Endianness, ABI and
    compiler family start as unknown, as does word size for an unknown
    architecture, which also adds an out-of-distribution signal.

    Args:
        identity: The architecture to describe.
        evidence: Ledger items; those whose summary mentions "architecture"
            are linked to the word-size fact.

    Returns:
        The new profile.
    """
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
