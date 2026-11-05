"""Build the initial set of causal hypotheses for a failure and keep their posterior
scores up to date as build feedback falsifies some of them."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..architecture.hazard_scanner import ArchitectureHazard
from ..schemas import (
    FailureAnalysis,
    FailureFamily,
    Hypothesis,
    HypothesisStatus,
    RepairFamily,
)


@dataclass(slots=True)
class HypothesisSet:
    """An ordered collection of competing explanations for one failure.

    Attributes:
        hypotheses: The hypotheses in creation order. Each carries a prior and a
            posterior score, a repair family and a status.
    """

    hypotheses: list[Hypothesis] = field(default_factory=list)

    def ranked(self) -> list[Hypothesis]:
        """Return the hypotheses from most to least likely.

        Returns:
            A new list sorted by posterior score (highest first), with ties broken
            by hypothesis ID so the order is deterministic.
        """
        return sorted(self.hypotheses, key=lambda item: (-item.posterior_score, item.id))

    def normalize(self) -> None:
        """Rescale the posteriors of non-falsified hypotheses so they sum to 1.

        Negative posteriors count as 0 in the total. If the total is 0, the scores
        are left unchanged to avoid dividing by zero.

        Returns:
            None.
        """
        active = [item for item in self.hypotheses if item.status is not HypothesisStatus.FALSIFIED]
        total = sum(max(0.0, item.posterior_score) for item in active)
        if total:
            for item in active:
                item.posterior_score /= total

    def falsify(self, hypothesis_id: str) -> None:
        """Mark one hypothesis as falsified and renormalize the rest.

        The falsified hypothesis gets a posterior of 0, and its probability mass is
        spread over the remaining hypotheses by ``normalize``. An unknown ID changes
        no status but still triggers renormalization.

        Args:
            hypothesis_id: The ID of the hypothesis that build feedback contradicted.

        Returns:
            None.
        """
        for item in self.hypotheses:
            if item.id == hypothesis_id:
                item.status = HypothesisStatus.FALSIFIED
                item.posterior_score = 0.0
        self.normalize()


def generate_initial_hypotheses(
    failure: FailureAnalysis, hazards: list[ArchitectureHazard]
) -> HypothesisSet:
    """Create the starting hypotheses for a failure from the diagnostic and hazards.

    Alternatives are added only where the evidence is really ambiguous. Three shapes
    are possible: when a scanned ``compiler_flag`` hazard appears in the primary
    diagnostic, the editable build file is the strong favourite (0.85) over an
    environment or packaging source and a nested dependency; when a linking failure
    mentions "atomic", three causes (missing atomic runtime library, unlinked
    object, unsupported atomic width) share the weight; otherwise the set holds a
    repository-configuration cause (0.55) against an external toolchain or
    environment cause (0.45).

    Args:
        failure: The forensic analysis of the initial build log, including the
            primary diagnostic and failure family.
        hazards: The architecture hazards found by the static scan of the repository.

    Returns:
        A new hypothesis set whose priors and posteriors start out equal and sum
        to 1.
    """
    diagnostic = failure.primary_diagnostic.lower()
    matching_flag = next(
        (hazard for hazard in hazards if hazard.category == "compiler_flag" and hazard.matched_text.lower() in diagnostic),
        None,
    )
    if matching_flag:
        hypotheses = [
            Hypothesis(
                "hypothesis-001",
                f"architecture-specific flag {matching_flag.matched_text} is injected by editable build configuration at {matching_flag.path}:{matching_flag.line}",
                FailureFamily.COMPILATION,
                0.85,
                0.85,
                repair_family=RepairFamily.CONDITIONAL_COMPILER_FLAG,
                falsification_conditions=["exact flag is absent from the causal build declaration"],
                status=HypothesisStatus.SUPPORTED,
            ),
            Hypothesis(
                "hypothesis-002",
                "the failing flag is additionally injected by the environment or packaging layer",
                FailureFamily.COMPILATION,
                0.10,
                0.10,
                repair_family=RepairFamily.PACKAGING_RULE,
                falsification_conditions=["dry-run command has no external flag source"],
            ),
            Hypothesis(
                "hypothesis-003",
                "a nested dependency independently injects the architecture-specific flag",
                FailureFamily.COMPILATION,
                0.05,
                0.05,
                repair_family=RepairFamily.ARCHITECTURE_GUARD,
                falsification_conditions=["failing command belongs to the top-level package"],
            ),
        ]
        return HypothesisSet(hypotheses)

    atomic = failure.family is FailureFamily.LINKING and "atomic" in diagnostic
    if atomic:
        return HypothesisSet([
            Hypothesis(
                "hypothesis-001", "target toolchain requires an explicit atomic runtime library",
                FailureFamily.LINKING, 0.45, 0.45, repair_family=RepairFamily.ATOMIC_LINKAGE,
                falsification_conditions=["link probe succeeds without an atomic runtime library"],
            ),
            Hypothesis(
                "hypothesis-002", "the atomic symbol is missing because a required source object was not linked",
                FailureFamily.LINKING, 0.35, 0.35, repair_family=RepairFamily.MULTI_FILE_SYNCHRONIZATION,
                falsification_conditions=["symbol is compiler-emitted and absent from all project objects"],
            ),
            Hypothesis(
                "hypothesis-003", "the code selects an unsupported atomic width for the target ABI",
                FailureFamily.LINKING, 0.20, 0.20, repair_family=RepairFamily.ABI_TYPE_FIX,
                falsification_conditions=["target compiler reports the atomic width as lock-free"],
            ),
        ])

    return HypothesisSet([
        Hypothesis(
            "hypothesis-001",
            "the first causal diagnostic traces to architecture-sensitive repository configuration",
            failure.family,
            0.55,
            0.55,
            repair_family=RepairFamily.UNKNOWN,
            falsification_conditions=["repository search finds no causal architecture-sensitive site"],
        ),
        Hypothesis(
            "hypothesis-002",
            "the failure originates in an external toolchain, dependency, or environment assumption",
            failure.family,
            0.45,
            0.45,
            repair_family=RepairFamily.NO_SAFE_FIX,
            falsification_conditions=["an editable causal site is directly observed"],
        ),
    ])
