"""Score and order repair candidates with the fixed weights in ``CANDIDATE_WEIGHTS``.

Keeping the scoring in one place makes ablation runs easy to compare, because every
configuration ranks candidates the same way.
"""

from __future__ import annotations

from ..constants import CANDIDATE_WEIGHTS
from ..schemas import PatchCandidate


def score_candidate(candidate: PatchCandidate) -> float:
    """Return a candidate's weighted score minus its risk.

    Each named component in ``candidate.score_components`` is clamped to 0..1 and
    multiplied by its weight from ``CANDIDATE_WEIGHTS``; a missing component counts as
    0. The candidate's risk, also clamped to 0..1, is then subtracted.

    Args:
        candidate: The repair candidate to score.

    Returns:
        The score, between -1.0 and 1.0. Higher is better.
    """
    components = candidate.score_components
    score = sum(
        CANDIDATE_WEIGHTS[name] * min(1.0, max(0.0, components.get(name, 0.0)))
        for name in CANDIDATE_WEIGHTS
    )
    return score - min(1.0, max(0.0, candidate.risk))


def rank_candidates(candidates: list[PatchCandidate]) -> list[PatchCandidate]:
    """Sort candidates from best to worst, with a fully deterministic order.

    Candidates are ordered by score (highest first), then by confidence (highest
    first), then by ID, so equal scores never produce a run-to-run difference.

    Args:
        candidates: The candidates to rank. The list itself is not modified.

    Returns:
        A new list with the same candidates, best first.
    """
    return sorted(candidates, key=lambda item: (-score_candidate(item), -item.confidence, item.id))
