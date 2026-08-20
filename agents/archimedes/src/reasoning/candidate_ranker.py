"""Central, ablation-friendly repair candidate scoring."""

from __future__ import annotations

from ..constants import CANDIDATE_WEIGHTS
from ..schemas import PatchCandidate


def score_candidate(candidate: PatchCandidate) -> float:
    components = candidate.score_components
    score = sum(
        CANDIDATE_WEIGHTS[name] * min(1.0, max(0.0, components.get(name, 0.0)))
        for name in CANDIDATE_WEIGHTS
    )
    return score - min(1.0, max(0.0, candidate.risk))


def rank_candidates(candidates: list[PatchCandidate]) -> list[PatchCandidate]:
    return sorted(candidates, key=lambda item: (-score_candidate(item), -item.confidence, item.id))
