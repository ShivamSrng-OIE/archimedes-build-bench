"""Failure-frontier construction and evidence-preserving comparison."""

from __future__ import annotations

from enum import Enum

from ..schemas import FailureFamily, FailureFrontier, FrontierStage


class FrontierMovement(str, Enum):
    PROGRESS = "progress"
    PARTIAL_PROGRESS = "partial_progress"
    INEFFECTIVE = "ineffective"
    REGRESSION = "regression"
    SUCCESS = "success"


_FAMILY_STAGE = {
    FailureFamily.ENVIRONMENT: FrontierStage.ENVIRONMENT,
    FailureFamily.BUILD_PREPARATION: FrontierStage.ENVIRONMENT,
    FailureFamily.DEPENDENCY: FrontierStage.DEPENDENCY,
    FailureFamily.CONFIGURATION: FrontierStage.CONFIGURE,
    FailureFamily.COMPILATION: FrontierStage.COMPILE,
    FailureFamily.LINKING: FrontierStage.LINK,
    FailureFamily.PACKAGING: FrontierStage.PACKAGE,
    FailureFamily.TEST: FrontierStage.TEST,
    FailureFamily.ARTIFACT: FrontierStage.ARTIFACT_VALIDATION,
    FailureFamily.UNKNOWN: FrontierStage.UNKNOWN,
}


def frontier_from_family(
    family: FailureFamily,
    *,
    signature: str,
    excerpt: str,
    primary_file: str | None = None,
) -> FailureFrontier:
    stage = _FAMILY_STAGE[family]
    return FailureFrontier(
        stage=stage,
        error_signature=signature,
        primary_file=primary_file,
        log_excerpt=excerpt,
        confidence=0.90 if stage is not FrontierStage.UNKNOWN else 0.20,
    )


def compare_frontiers(before: FailureFrontier, after: FailureFrontier) -> FrontierMovement:
    """Classify whether a new build advanced, changed, or regressed the causal frontier."""
    if after.stage is FrontierStage.SUCCESS:
        return FrontierMovement.SUCCESS
    if after.stage > before.stage:
        return FrontierMovement.PROGRESS
    if after.stage < before.stage:
        return FrontierMovement.REGRESSION
    if after.error_signature == before.error_signature:
        return FrontierMovement.INEFFECTIVE
    return FrontierMovement.PARTIAL_PROGRESS
