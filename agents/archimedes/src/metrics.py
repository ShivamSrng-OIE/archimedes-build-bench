"""Per-Case counters and scores written to ``metrics.json`` for development analysis."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from .schemas import JSONValue


@dataclass(slots=True)
class CaseMetrics:
    """Counters and scores collected over one run.

    Attributes:
        case_id: The Case identifier.
        source_arch: Canonical source architecture name.
        target_arch: Canonical target architecture name.
        failure_family: Failure family chosen by forensics.
        selected_repair_family: Repair family of the applied candidate, or
            ``none``.
        causal_file_identified: Whether the run kept a change to at least one
            file.
        success: Whether build feedback reported a passing build.
        initial_frontier: Frontier stage of the input log as an integer, or -1
            when unknown or when frontier tracking is off.
        best_frontier: Furthest stage reached in any build feedback.
        attempts: Number of candidates applied.
        build_requests: Number of builds requested.
        model_calls: Number of model HTTP requests made, including JSON
            repair retries.
        strong_calls: Number of STRONG-tier model requests.
        input_tokens: Model input tokens used.
        output_tokens: Model output tokens used.
        estimated_cost: Estimated model cost in US dollars, or ``None`` when no
            cost rates are configured.
        probe_count: Number of probes run.
        semantic_anchor_count: Number of semantic anchors extracted.
        invalid_patch_count: Number of candidates rejected by a critic or
            failing an edit precondition.
        rollback_count: Number of applied edits that were rolled back.
        policy_rejection_count: Number of rejected candidates that had
            policy-critic findings.
        patch_lines: Lines covered by the edits that were kept.
        modified_files: Number of files changed by the edits that were kept.
        familiarity_score: Target familiarity score, from 0 to 1.
        critical_claim_confidence: Confidence of the applied candidate.
        hypothesis_count: Number of hypotheses generated.
        hypotheses_falsified: Number of hypotheses falsified by build feedback.
        elapsed_seconds: Run time in seconds, set by ``finish``.
        started_monotonic: Monotonic clock value when the object was created;
            left out of ``to_dict``.
    """

    case_id: str
    source_arch: str
    target_arch: str
    failure_family: str = "unknown"
    selected_repair_family: str = "none"
    causal_file_identified: bool = False
    success: bool = False
    initial_frontier: int = -1
    best_frontier: int = -1
    attempts: int = 0
    build_requests: int = 0
    model_calls: int = 0
    strong_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None
    probe_count: int = 0
    semantic_anchor_count: int = 0
    invalid_patch_count: int = 0
    rollback_count: int = 0
    policy_rejection_count: int = 0
    patch_lines: int = 0
    modified_files: int = 0
    familiarity_score: float = 0.0
    critical_claim_confidence: float = 0.0
    hypothesis_count: int = 0
    hypotheses_falsified: int = 0
    elapsed_seconds: float = 0.0
    started_monotonic: float = field(default_factory=time.monotonic, repr=False)

    def finish(self) -> None:
        """Set ``elapsed_seconds`` from the time since the object was created.

        Returns:
            None.
        """
        self.elapsed_seconds = round(time.monotonic() - self.started_monotonic, 6)

    def to_dict(self) -> dict[str, JSONValue]:
        """Convert the metrics to a JSON-ready dictionary.

        Returns:
            Every field except ``started_monotonic``, which only has meaning
            inside this process.
        """
        payload = asdict(self)
        payload.pop("started_monotonic", None)
        return payload
