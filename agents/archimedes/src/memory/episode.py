"""Compact runtime episode memory; no cross-case persistence is assumed."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Hypothesis, Probe, RepairAttempt


@dataclass(slots=True)
class EpisodeMemory:
    observations: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    attempts: list[RepairAttempt] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    def compact_summary(self, *, max_characters: int = 8000) -> str:
        lines = ["Observations:", *self.observations[-20:], "Hypotheses:"]
        lines.extend(
            f"{item.id} score={item.posterior_score:.3f} status={item.status.value}: {item.root_cause}"
            for item in sorted(self.hypotheses, key=lambda value: -value.posterior_score)[:5]
        )
        lines.append("Attempts:")
        lines.extend(
            f"attempt={item.attempt_number} candidate={item.selected_candidate_id} decision={item.decision.value}"
            for item in self.attempts[-5:]
        )
        return "\n".join(lines)[:max_characters]
