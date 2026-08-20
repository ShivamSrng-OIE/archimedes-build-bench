"""Fail-closed placeholder for a future locally documented bb-build contract."""

from __future__ import annotations

from ..errors import BuildFeedbackError
from .feedback import BuildFeedbackResult


class BbBuildFeedbackProvider:
    """Expose no invocation until an official request/response schema is installed."""

    available = False
    request_limit = 0
    reason_unavailable = "bb-build protocol schema is unavailable; invocation format was not invented"

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        raise BuildFeedbackError(self.reason_unavailable)
