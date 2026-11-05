"""Approximate token, call and cost accounting for model use, with no provider SDK."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BudgetExhaustedError


def approximate_tokens(text: str) -> int:
    """Estimate the token count of text at about four characters per token.

    Args:
        text: The text to measure.

    Returns:
        The estimate, at least 1.
    """
    return max(1, (len(text) + 3) // 4)


@dataclass(slots=True)
class UsageBudget:
    """Model usage limits for one run and the usage so far.

    Attributes:
        max_calls: Most model HTTP requests allowed.
        max_strong_calls: Most STRONG-tier requests allowed.
        max_tokens: Most input plus output tokens allowed.
        max_cost_usd: Cost ceiling in US dollars, or ``None`` for no ceiling.
        input_rate_per_million: Price of one million input tokens, or ``None``.
        output_rate_per_million: Price of one million output tokens, or
            ``None``.
        calls: Requests made so far.
        strong_calls: STRONG-tier requests made so far.
        input_tokens: Input tokens used so far.
        output_tokens: Output tokens used so far.
    """

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
        """Return the cost of the tokens used so far.

        Returns:
            The cost in US dollars, or ``None`` if either rate is unknown.
        """
        if self.input_rate_per_million is None or self.output_rate_per_million is None:
            return None
        return (
            self.input_tokens * self.input_rate_per_million
            + self.output_tokens * self.output_rate_per_million
        ) / 1_000_000

    def authorize(
        self,
        *,
        strong: bool,
        estimated_input_tokens: int,
        reserve_output_tokens: int = 1000,
        reserve_calls: int = 1,
    ) -> None:
        """Check, before a call, that it cannot push usage over any limit.

        The check assumes the worst case for every reserved request: the full
        estimated input plus ``reserve_output_tokens`` of output.

        Args:
            strong: Whether the call uses the STRONG tier.
            estimated_input_tokens: Estimated input tokens per request.
            reserve_output_tokens: Output tokens to reserve per request.
            reserve_calls: Requests to reserve, 2 when a JSON repair retry may
                follow.

        Returns:
            None.

        Raises:
            BudgetExhaustedError: If the call, strong-call, token or cost
                limit would be exceeded.
        """
        if self.calls + reserve_calls > self.max_calls:
            raise BudgetExhaustedError("total model-call budget exhausted")
        if strong and self.strong_calls + reserve_calls > self.max_strong_calls:
            raise BudgetExhaustedError("strong-model call budget exhausted")
        if (
            self.input_tokens
            + self.output_tokens
            + estimated_input_tokens * reserve_calls
            + reserve_output_tokens * reserve_calls
            > self.max_tokens
        ):
            raise BudgetExhaustedError("approximate token budget exhausted")
        if self.max_cost_usd is not None and self.input_rate_per_million is not None and self.output_rate_per_million is not None:
            projected = (
                (self.input_tokens + estimated_input_tokens * reserve_calls)
                * self.input_rate_per_million
                + (self.output_tokens + reserve_output_tokens * reserve_calls)
                * self.output_rate_per_million
            ) / 1_000_000
            if projected > self.max_cost_usd:
                raise BudgetExhaustedError("estimated monetary budget would be exceeded")

    def record(
        self, *, strong: bool, input_tokens: int, output_tokens: int, calls: int = 1
    ) -> None:
        """Add a finished call's usage to the totals.

        Args:
            strong: Whether the call used the STRONG tier.
            input_tokens: Input tokens used; negative values count as 0.
            output_tokens: Output tokens used; negative values count as 0.
            calls: HTTP requests the call made.

        Returns:
            None.
        """
        self.calls += calls
        self.strong_calls += calls if strong else 0
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
