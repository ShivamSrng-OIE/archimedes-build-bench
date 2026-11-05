"""The build-feedback provider used when no build service exists, including in hosted mode."""

from __future__ import annotations

from ..errors import BuildFeedbackError
from .feedback import BuildFeedbackResult


class UnavailableBuildFeedbackProvider:
    """A provider that never offers builds and refuses every request.

    Attributes:
        available: Always False.
        request_limit: Always 0.
        reason_unavailable: Why builds are not available.
    """

    available = False
    request_limit = 0

    def __init__(self, reason: str = "official build-feedback interface was not detected"):
        """Store the reason builds are unavailable.

        Args:
            reason: Short explanation reported to the controller and trace.

        Returns:
            None.
        """
        self.reason_unavailable = reason

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Refuse the build request.

        Args:
            expected_information_value: Ignored.

        Returns:
            Never returns.

        Raises:
            BuildFeedbackError: Always, with the stored reason.
        """
        raise BuildFeedbackError(self.reason_unavailable)
