"""Bounded per-case research metrics and development cost telemetry."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from .schemas import JSONValue


@dataclass(slots=True)
class CaseMetrics:
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
        self.elapsed_seconds = round(time.monotonic() - self.started_monotonic, 6)

    def to_dict(self) -> dict[str, JSONValue]:
        payload = asdict(self)
        payload.pop("started_monotonic", None)
        return payload
