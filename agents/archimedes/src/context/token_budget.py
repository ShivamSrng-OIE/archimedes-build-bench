"""Approximate model token/call/cost accounting without provider SDKs."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BudgetExhaustedError


def approximate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


@dataclass(slots=True)
class UsageBudget:
    max_calls: int
    max_strong_calls: int
    max_tokens: int
    max_cost_usd: float | None = None
    input_rate_per_million: float | None = None
    output_rate_per_million: float | None = None
    calls: int = 0
    strong_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def estimated_cost(self) -> float | None:
        if self.input_rate_per_million is None or self.output_rate_per_million is None:
            return None
        return (
            self.input_tokens * self.input_rate_per_million
            + self.output_tokens * self.output_rate_per_million
        ) / 1_000_000

    def authorize(self, *, strong: bool, estimated_input_tokens: int, reserve_output_tokens: int = 1000) -> None:
        """Fail before an optional call that would exceed a configured resource gate."""
        if self.calls >= self.max_calls:
            raise BudgetExhaustedError("total model-call budget exhausted")
        if strong and self.strong_calls >= self.max_strong_calls:
            raise BudgetExhaustedError("strong-model call budget exhausted")
        if self.input_tokens + self.output_tokens + estimated_input_tokens + reserve_output_tokens > self.max_tokens:
            raise BudgetExhaustedError("approximate token budget exhausted")
        if self.max_cost_usd is not None and self.input_rate_per_million is not None and self.output_rate_per_million is not None:
            projected = (
                (self.input_tokens + estimated_input_tokens) * self.input_rate_per_million
                + (self.output_tokens + reserve_output_tokens) * self.output_rate_per_million
            ) / 1_000_000
            if projected > self.max_cost_usd:
                raise BudgetExhaustedError("estimated monetary budget would be exceeded")

    def record(self, *, strong: bool, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.strong_calls += int(strong)
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
