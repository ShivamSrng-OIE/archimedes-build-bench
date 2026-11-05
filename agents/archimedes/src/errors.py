"""Exception types the Agent raises on purpose.

``main.run`` maps any ``ArchimedesError`` to exit code 1 and anything else to
exit code 2, so these classes separate known failures from internal bugs.
"""


class ArchimedesError(Exception):
    """Base class for failures the Agent expects and reports cleanly."""


class ConfigurationError(ArchimedesError):
    """Raised when an environment setting is missing, malformed or inconsistent."""


class WorkspaceError(ArchimedesError):
    """Raised when the workspace directories are missing or escape the root."""


class CaseFormatError(ArchimedesError):
    """Raised when ``case.json`` or the failure log cannot be read in a supported form."""


class PathPolicyError(ArchimedesError):
    """Raised when a path may not be read or edited under the path policy."""


class ModelError(ArchimedesError):
    """Raised when a model request fails at the transport or HTTP level."""


class ModelProtocolError(ModelError):
    """Raised when a model reply or answer breaks the expected JSON contract."""


class PatchValidationError(ArchimedesError):
    """Raised when an edit fails a precondition or cannot be written atomically."""


class BuildFeedbackError(ArchimedesError):
    """Raised when a build-feedback request fails or returns an unusable answer."""


class BudgetExhaustedError(ArchimedesError):
    """Raised when a call would exceed a model, build, token or cost budget."""
