"""Helpers for explicit conflict tracking between claims and evidence."""

from __future__ import annotations

from ..schemas import Claim, EvidenceItem


def contradiction_is_direct(items: list[EvidenceItem]) -> bool:
    """Return true when at least one contradictory item crosses the direct-evidence gate."""
    return any(item.reliability * item.strength >= 0.80 for item in items)


def has_unresolved_contradiction(claim: Claim) -> bool:
    return bool(claim.contradicting_evidence_ids) and claim.confidence < 0.90
