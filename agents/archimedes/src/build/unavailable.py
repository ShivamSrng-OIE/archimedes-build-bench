"""Safe behavior when the current official protocol exposes no build service."""

from __future__ import annotations

from ..errors import BuildFeedbackError
from .feedback import BuildFeedbackResult


class UnavailableBuildFeedbackProvider:
    available = False
    request_limit = 0

    def __init__(self, reason: str = "official build-feedback interface was not detected"):
        self.reason_unavailable = reason

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        raise BuildFeedbackError(self.reason_unavailable)
