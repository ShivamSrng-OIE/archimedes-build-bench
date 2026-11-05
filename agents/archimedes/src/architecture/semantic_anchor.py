"""Semantic anchors: conservative records of what each hazard construct is for.

A repair that removes or guards an architecture-specific construct is allowed
only when its anchor shows the purpose, the invariants to keep and a portable
alternative, with enough confidence.
"""

from __future__ import annotations

from ..schemas import SemanticAnchor
from .hazard_scanner import ArchitectureHazard

_ANCHOR_TEMPLATES: dict[str, tuple[str, list[str], list[str]]] = {
    "compiler_flag": (
        "request an architecture-specific compiler optimization or ABI mode",
        [
            "package behavior must remain unchanged when an optional optimization is unavailable",
            "the source architecture must retain its compatible compiler option",
        ],
        [
            "guard the option with a compiler-target check",
            "omit the optional optimization on incompatible targets",
        ],
    ),
    "architecture_macro": (
        "select an implementation for a compiler-reported target architecture",
        [
            "exactly one compatible implementation path must remain selectable",
            "the generic behavior must not depend on an unrelated architecture macro",
        ],
        [
            "select an existing generic implementation",
            "add a target-specific guard around an equivalent implementation",
        ],
    ),
    "packaging_conditional": (
        "select package metadata or build behavior by target architecture",
        [
            "dependency and artifact rules must match the target architecture",
            "existing supported architectures must keep their current behavior",
        ],
        [
            "extend the existing architecture condition",
            "use a capability check when the package format supports one",
        ],
    ),
    "intrinsic_header": (
        "provide an architecture-specialized implementation through compiler intrinsics",
        [
            "observable function results and side effects must be preserved",
            "unsupported targets must not compile the specialized header or instructions",
        ],
        [
            "select an existing scalar or generic implementation",
            "guard the intrinsic implementation behind a target capability check",
        ],
    ),
    "inline_assembly": (
        "implement a low-level operation with target-specific instructions",
        [
            "inputs, outputs, clobbers, ordering, and side effects must be preserved",
            "unsupported assemblers must not parse the target-specific instruction sequence",
        ],
        [
            "use an existing portable C implementation",
            "use a compiler intrinsic with equivalent ordering and side effects",
        ],
    ),
    "abi_assumption": (
        "encode a size, alignment, byte-order, or atomic-width assumption",
        [
            "data representation and externally visible behavior must remain compatible",
            "the implementation must use properties observed for the actual target ABI",
        ],
        [
            "derive the property from standard types or build-time feature detection",
            "select an existing ABI-neutral implementation",
        ],
    ),
}


def extract_anchor(
    hazard: ArchitectureHazard, *, evidence_ids: list[str] | None = None
) -> SemanticAnchor | None:
    """Build the semantic anchor for one hazard from its category template.

    The template states the purpose, the invariants to preserve and the
    portable alternatives for that kind of construct. It does not claim that
    such an alternative exists in this repository; candidate generation still
    has to prove an exact edit with direct evidence.

    Args:
        hazard: The hazard found by the scanner.
        evidence_ids: Ledger IDs of the evidence recorded for the hazard.

    Returns:
        The anchor, with the hazard's confidence, or ``None`` if the category
        has no template.
    """
    template = _ANCHOR_TEMPLATES.get(hazard.category)
    if template is None:
        return None
    purpose, invariants, alternatives = template
    return SemanticAnchor(
        site=f"{hazard.path}:{hazard.line}",
        symbol=hazard.matched_text,
        implementation_description=(
            f"observed {hazard.category.replace('_', ' ')}: {hazard.matched_text}"
        ),
        functional_purpose=purpose,
        semantic_invariants=list(invariants),
        architecture_specific_mechanism=hazard.category,
        portable_alternatives=list(alternatives),
        confidence=hazard.confidence,
        evidence_ids=list(evidence_ids or []),
    )


def validate_anchor(anchor: SemanticAnchor) -> list[str]:
    """List the gaps that make it unsafe to repair the construct an anchor describes.

    Args:
        anchor: The anchor to check.

    Returns:
        One message for each problem: no functional purpose, no invariants, no
        portable alternative, or confidence below 0.80. An empty list means
        the anchor allows a repair.
    """
    findings: list[str] = []
    if not anchor.functional_purpose.strip():
        findings.append("functional purpose is unknown")
    if not anchor.semantic_invariants:
        findings.append("semantic invariants are absent")
    if not anchor.portable_alternatives:
        findings.append("no portable alternative is identified")
    if anchor.confidence < 0.80:
        findings.append("semantic anchor confidence is below the repair threshold")
    return findings
