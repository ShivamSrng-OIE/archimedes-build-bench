"""Central confidence fusion, caps, and consequence-aware decision policy."""

from __future__ import annotations

import math

from ..schemas import (
    ClaimStatus,
    ConfidenceBand,
    ConsequenceLevel,
    EvidenceItem,
    EvidenceKind,
)

_LLM_KINDS = {EvidenceKind.LLM_INFERENCE, EvidenceKind.MODEL_SELF_CONSISTENCY}
_HEURISTIC_KINDS = {EvidenceKind.STATIC_ANALYSIS}


def confidence_band(value: float) -> ConfidenceBand:
    if value < 0.20:
        return ConfidenceBand.VERY_LOW
    if value < 0.40:
        return ConfidenceBand.LOW
    if value < 0.70:
        return ConfidenceBand.MODERATE
    if value < 0.90:
        return ConfidenceBand.HIGH
    return ConfidenceBand.VERY_HIGH


def _aggregate(items: list[EvidenceItem]) -> float:
    return 1.0 - math.prod(1.0 - item.reliability * item.strength for item in items)


def fuse_confidence(
    supporting: list[EvidenceItem], contradicting: list[EvidenceItem]
) -> tuple[float, ConfidenceBand, ClaimStatus]:
    """Fuse independent evidence and impose non-negotiable source-dependent caps."""
    support = _aggregate(supporting)
    contradiction = _aggregate(contradicting)
    raw = min(0.99, max(0.01, 0.5 + 0.5 * (support - contradiction)))
    kinds = {item.kind for item in supporting}
    if supporting and kinds.issubset(_LLM_KINDS):
        raw = min(raw, 0.65)
    if supporting and kinds.issubset(_HEURISTIC_KINDS):
        raw = min(raw, 0.75)
    high_contradiction = any(item.reliability * item.strength >= 0.80 for item in contradicting)
    non_llm = any(item.kind not in _LLM_KINDS for item in supporting)
    if high_contradiction and contradiction >= support:
        status = ClaimStatus.CONTRADICTED
    elif raw >= 0.90 and non_llm and not high_contradiction:
        status = ClaimStatus.CONFIRMED
    elif supporting:
        status = ClaimStatus.SUPPORTED
    else:
        status = ClaimStatus.OPEN
    return raw, confidence_band(raw), status


def claim_justifies_patch(
    confidence: float, consequence: ConsequenceLevel, status: ClaimStatus
) -> bool:
    """Operationalize confidence thresholds for repair-gating decisions."""
    if status is ClaimStatus.CONTRADICTED:
        return False
    if consequence is ConsequenceLevel.CRITICAL:
        return confidence >= 0.90
    if consequence is ConsequenceLevel.HIGH:
        return confidence >= 0.80
    return confidence >= 0.60
