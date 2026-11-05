"""Optional build feedback: asking a platform build service whether a repair worked.

Hosted evaluation offers no such service, so the controller then uses
``UnavailableBuildFeedbackProvider`` and never requests a build.
"""

from .feedback import BuildFeedbackProvider, BuildFeedbackResult
from .unavailable import UnavailableBuildFeedbackProvider

__all__ = ["BuildFeedbackProvider", "BuildFeedbackResult", "UnavailableBuildFeedbackProvider"]
