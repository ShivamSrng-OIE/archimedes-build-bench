"""Explicit weighted familiarity scoring and discovery-mode policy."""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Architecture
from .profile import ArchitectureProfile

_WEIGHTS = {
    "architecture_identity": 0.15,
    "metadata_completeness": 0.15,
    "toolchain_evidence": 0.15,
    "repository_coverage": 0.10,
    "portable_rule_coverage": 0.15,
    "knowledge_coverage": 0.10,
    "semantic_analogy": 0.10,
    "model_agreement": 0.10,
}


@dataclass(frozen=True, slots=True)
class FamiliarityAssessment:
    score: float
    band: str
    requires_discovery: bool
    components: dict[str, float]


def assess_familiarity(
    profile: ArchitectureProfile,
    *,
    repository_coverage: float = 0.0,
    portable_rule_coverage: float = 0.0,
    semantic_analogy: float = 0.0,
    model_agreement: float = 0.0,
) -> FamiliarityAssessment:
    """Calculate a transparent heuristic familiarity score in the range 0..1."""
    known_facts = sum(1 for fact in profile.facts.values() if fact.value is not None)
    completeness = known_facts / max(1, len(profile.facts))
    components = {
        "architecture_identity": 1.0 if profile.identity.canonical is not Architecture.UNKNOWN else 0.0,
        "metadata_completeness": completeness,
        "toolchain_evidence": 1.0 if profile.facts.get("compiler_family", None) and profile.facts["compiler_family"].value else 0.0,
        "repository_coverage": min(max(repository_coverage, 0.0), 1.0),
        "portable_rule_coverage": min(max(portable_rule_coverage, 0.0), 1.0),
        "knowledge_coverage": 0.8 if profile.identity.canonical is not Architecture.UNKNOWN else 0.0,
        "semantic_analogy": min(max(semantic_analogy, 0.0), 1.0),
        "model_agreement": min(max(model_agreement, 0.0), 1.0),
    }
    score = round(sum(components[key] * weight for key, weight in _WEIGHTS.items()), 4)
    band = "familiar" if score >= 0.80 else "moderate" if score >= 0.60 else "unfamiliar"
    return FamiliarityAssessment(score, band, score < 0.60, components)
