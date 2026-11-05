"""The build-feedback interface, its result type, and the build request budget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import BudgetExhaustedError
from ..schemas import FailureFrontier


@dataclass(frozen=True, slots=True)
class BuildFeedbackResult:
    """The outcome of one requested build.

    Attributes:
        success: Whether the build passed.
        raw_log: The log text returned by the build service.
        frontier: Where the new build stopped.
        request_id: Identifier of the request, if the service gave one.
    """

    success: bool
    raw_log: str
    frontier: FailureFrontier
    request_id: str | None = None


class BuildFeedbackProvider(Protocol):
    """Interface every build-feedback provider offers to the controller."""

    @property
    def available(self) -> bool:
        """Tell whether builds may be requested.

        Returns:
            True if ``request`` can be called.
        """
        ...

    @property
    def reason_unavailable(self) -> str | None:
        """Explain why builds are not available.

        Returns:
            A short reason, or ``None`` when builds are available.
        """
        ...

    @property
    def request_limit(self) -> int | None:
        """Return how many builds the Case allows.

        Returns:
            The limit, or ``None`` if it is unknown.
        """
        ...

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Request one build of the current worktree.

        Args:
            expected_information_value: How much the caller expects to learn
                from the build, from 0 to 1.

        Returns:
            The build outcome.

        Raises:
            BuildFeedbackError: If the build cannot be requested or its answer
                is unusable.
        """
        ...


@dataclass(slots=True)
class BuildBudget:
    """Count build requests against the limit the provider reports.

    No default limit is assumed: when the provider does not report one, no
    build is allowed.

    Attributes:
        limit: Most builds allowed, or ``None`` if unknown.
        used: Builds requested so far.
    """

    limit: int | None
    used: int = 0

    @property
    def remaining(self) -> int | None:
        """Return how many builds are left.

        Returns:
            The number left, never below 0, or ``None`` if the limit is
            unknown.
        """
        return None if self.limit is None else max(0, self.limit - self.used)

    def authorize(self) -> None:
        """Check that one more build may be requested.

        Returns:
            None.

        Raises:
            BudgetExhaustedError: If the limit is unknown or already reached.
        """
        if self.limit is None:
            raise BudgetExhaustedError("official build request limit is unknown")
        if self.used >= self.limit:
            raise BudgetExhaustedError("official build request budget exhausted")

    def record(self) -> None:
        """Count one build request after checking the budget again.

        Returns:
            None.

        Raises:
            BudgetExhaustedError: If the limit is unknown or already reached.
        """
        self.authorize()
        self.used += 1
