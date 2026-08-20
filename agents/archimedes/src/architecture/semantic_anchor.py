"""Semantic-anchor validation and portable fallback discovery support."""

from __future__ import annotations

from ..schemas import SemanticAnchor


def validate_anchor(anchor: SemanticAnchor) -> list[str]:
    """Return deficiencies that make an architecture-specific repair unsafe."""
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
