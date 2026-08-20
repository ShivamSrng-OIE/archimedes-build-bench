"""Official build-feedback provider protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import BudgetExhaustedError
from ..schemas import FailureFrontier


@dataclass(frozen=True, slots=True)
class BuildFeedbackResult:
    success: bool
    raw_log: str
    frontier: FailureFrontier
    request_id: str | None = None


class BuildFeedbackProvider(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def reason_unavailable(self) -> str | None: ...

    @property
    def request_limit(self) -> int | None: ...

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult: ...


@dataclass(slots=True)
class BuildBudget:
    """Track an official provider limit without inventing a historical fixed count."""

    limit: int | None
    used: int = 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)

    def authorize(self) -> None:
        if self.limit is None:
            raise BudgetExhaustedError("official build request limit is unknown")
        if self.used >= self.limit:
            raise BudgetExhaustedError("official build request budget exhausted")

    def record(self) -> None:
        self.authorize()
        self.used += 1
