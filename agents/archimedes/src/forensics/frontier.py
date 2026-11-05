"""Map failure families to failure-frontier stages and compare frontiers between builds.

The frontier is how far a build got before it failed. Comparing the frontier before and
after an edit tells the controller whether the edit helped, did nothing or made it worse.
"""

from __future__ import annotations

from enum import Enum

from ..schemas import FailureFamily, FailureFrontier, FrontierStage


class FrontierMovement(str, Enum):
    """How a new build's failure frontier moved compared with the previous one.

    Attributes:
        PROGRESS: The build failed at a later stage.
        PARTIAL_PROGRESS: Same stage, but a different error signature.
        INEFFECTIVE: Same stage and the same error signature.
        REGRESSION: The build failed at an earlier stage.
        SUCCESS: The build succeeded.
    """

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
    """Build a failure frontier for a classified failure.

    Args:
        family: The primary failure family. ``BUILD_PREPARATION`` maps to the
            environment stage, and every other family maps to its matching stage.
        signature: The normalized error signature of the primary diagnostic.
        excerpt: A short log excerpt around the diagnostic.
        primary_file: The repository file that caused the failure, if known.

    Returns:
        The frontier, with confidence 0.90 for a known stage and 0.20 when the stage
        is unknown.
    """
    stage = _FAMILY_STAGE[family]
    return FailureFrontier(
        stage=stage,
        error_signature=signature,
        primary_file=primary_file,
        log_excerpt=excerpt,
        confidence=0.90 if stage is not FrontierStage.UNKNOWN else 0.20,
    )


def compare_frontiers(before: FailureFrontier, after: FailureFrontier) -> FrontierMovement:
    """Decide whether a new build moved the failure frontier forward, back or not at all.

    Stages are ordered integers, so "later" and "earlier" are plain comparisons.
    When the stage is the same, the error signatures decide between
    ``INEFFECTIVE`` and ``PARTIAL_PROGRESS``.

    Args:
        before: The frontier of the build before the edit.
        after: The frontier of the build after the edit.

    Returns:
        The movement: ``SUCCESS`` if ``after`` reached the success stage, otherwise
        ``PROGRESS``, ``REGRESSION``, ``INEFFECTIVE`` or ``PARTIAL_PROGRESS``.
    """
    if after.stage is FrontierStage.SUCCESS:
        return FrontierMovement.SUCCESS
    if after.stage > before.stage:
        return FrontierMovement.PROGRESS
    if after.stage < before.stage:
        return FrontierMovement.REGRESSION
    if after.error_signature == before.error_signature:
        return FrontierMovement.INEFFECTIVE
    return FrontierMovement.PARTIAL_PROGRESS
