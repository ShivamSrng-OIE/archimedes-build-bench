"""Confidence fusion with source-dependent caps, and the confidence gate for patches."""

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
    """Map a confidence value to its coarse band.

    Args:
        value: Confidence from 0 to 1.

    Returns:
        ``VERY_LOW`` below 0.20, ``LOW`` below 0.40, ``MODERATE`` below 0.70,
        ``HIGH`` below 0.90, otherwise ``VERY_HIGH``.
    """
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
    """Combine evidence items as independent sources.

    Args:
        items: The evidence to combine.

    Returns:
        ``1 - product(1 - reliability * strength)``, or 0 for no items.
    """
    return 1.0 - math.prod(1.0 - item.reliability * item.strength for item in items)


def fuse_confidence(
    supporting: list[EvidenceItem], contradicting: list[EvidenceItem]
) -> tuple[float, ConfidenceBand, ClaimStatus]:
    """Turn supporting and contradicting evidence into a confidence and status.

    The raw value is ``0.5 + 0.5 * (support - contradiction)``, clamped to
    0.01..0.99. Support made only of model output is capped at 0.65, and
    support made only of static analysis at 0.75, so neither can reach the
    high-consequence threshold on its own.

    Args:
        supporting: Evidence for the claim.
        contradicting: Evidence against the claim.

    Returns:
        The confidence, its band, and the status: ``CONTRADICTED`` when a
        strong contradicting item exists and contradiction is at least as
        large as support, ``CONFIRMED`` at 0.90 or more with non-model support
        and no strong contradiction, ``SUPPORTED`` when there is any support,
        otherwise ``OPEN``.
    """
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
    """Decide whether a claim is strong enough to justify a patch.

    Args:
        confidence: The claim's fused confidence.
        consequence: How much damage acting on a wrong claim could do.
        status: The claim's status.

    Returns:
        False for a contradicted claim. Otherwise True when the confidence
        reaches 0.90 for critical claims, 0.80 for high-consequence claims, or
        0.60 for low and medium ones.
    """
    if status is ClaimStatus.CONTRADICTED:
        return False
    if consequence is ConsequenceLevel.CRITICAL:
        return confidence >= 0.90
    if consequence is ConsequenceLevel.HIGH:
        return confidence >= 0.80
    return confidence >= 0.60
