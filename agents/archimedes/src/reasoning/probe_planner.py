"""Information-value ranking of predefined, safe diagnostic probes."""

from __future__ import annotations

from ..schemas import ConsequenceLevel, Probe

_CONSEQUENCE_WEIGHT = {
    ConsequenceLevel.LOW: 0.5,
    ConsequenceLevel.MEDIUM: 1.0,
    ConsequenceLevel.HIGH: 1.5,
    ConsequenceLevel.CRITICAL: 2.0,
}


def information_value(
    *, hypothesis_mass: float, discrimination_strength: float,
    consequence: ConsequenceLevel, normalized_cost: float,
) -> float:
    """Compute a transparent heuristic, not a claimed Bayesian optimizer."""
    cost = max(0.01, normalized_cost)
    return max(0.0, hypothesis_mass) * max(0.0, discrimination_strength) * _CONSEQUENCE_WEIGHT[consequence] / cost


def rank_probes(probes: list[Probe]) -> list[Probe]:
    return sorted(
        (probe for probe in probes if probe.allowed and probe.side_effect_risk <= 0.20),
        key=lambda item: (
            -(item.estimated_information_gain / max(0.01, item.estimated_cost)),
            item.side_effect_risk,
            item.id,
        ),
    )


def select_probe(probes: list[Probe], *, minimum_value: float = 0.10) -> Probe | None:
    ranked = rank_probes(probes)
    if not ranked:
        return None
    selected = ranked[0]
    value = selected.estimated_information_gain / max(0.01, selected.estimated_cost)
    return selected if value >= minimum_value else None
