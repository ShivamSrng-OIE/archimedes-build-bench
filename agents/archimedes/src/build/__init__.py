"""Capability-safe official build-feedback boundary."""

from .feedback import BuildFeedbackProvider, BuildFeedbackResult
from .unavailable import UnavailableBuildFeedbackProvider

__all__ = ["BuildFeedbackProvider", "BuildFeedbackResult", "UnavailableBuildFeedbackProvider"]
