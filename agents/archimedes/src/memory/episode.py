"""The repair episode: what one run observed, tried and found contradicted.

It lives only in memory for the current Case; its compact summary is written
to ``repair-episode.json`` as ``episode_summary``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Hypothesis, Probe, RepairAttempt


@dataclass(slots=True)
class EpisodeMemory:
    """Observations, hypotheses, probes, attempts and contradictions of one run.

    Attributes:
        observations: Plain-text notes, such as build-feedback outcomes.
        hypotheses: Hypotheses considered during the run.
        probes: Probes that were run.
        attempts: Repair attempts, in order.
        contradictions: Notes on candidates that build feedback contradicted.
    """

    observations: list[str] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    attempts: list[RepairAttempt] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)

    def compact_summary(self, *, max_characters: int = 8000) -> str:
        """Summarise the episode as short text.

        It lists the last 20 observations, the five hypotheses with the highest
        posterior score and the last five attempts.

        Args:
            max_characters: Largest length of the summary; 8,000 by default.

        Returns:
            The summary, cut to ``max_characters``.
        """
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
