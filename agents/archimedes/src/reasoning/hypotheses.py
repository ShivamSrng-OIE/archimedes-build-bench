"""Deterministic initial causal hypotheses and posterior maintenance."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..architecture.hazard_scanner import ArchitectureHazard
from ..schemas import FailureAnalysis, FailureFamily, Hypothesis, HypothesisStatus, RepairFamily


@dataclass(slots=True)
class HypothesisSet:
    hypotheses: list[Hypothesis] = field(default_factory=list)

    def ranked(self) -> list[Hypothesis]:
        return sorted(self.hypotheses, key=lambda item: (-item.posterior_score, item.id))

    def normalize(self) -> None:
        active = [item for item in self.hypotheses if item.status is not HypothesisStatus.FALSIFIED]
        total = sum(max(0.0, item.posterior_score) for item in active)
        if total:
            for item in active:
                item.posterior_score /= total

    def falsify(self, hypothesis_id: str) -> None:
        for item in self.hypotheses:
            if item.id == hypothesis_id:
                item.status = HypothesisStatus.FALSIFIED
                item.posterior_score = 0.0
        self.normalize()


def generate_initial_hypotheses(
    failure: FailureAnalysis, hazards: list[ArchitectureHazard]
) -> HypothesisSet:
    """Generate real alternatives when ambiguity exists; do not pad a known cause."""
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
