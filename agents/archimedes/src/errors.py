"""Typed failures surfaced at Archimedes subsystem boundaries."""


class ArchimedesError(Exception):
    """Base class for expected agent failures."""


class ConfigurationError(ArchimedesError):
    """Runtime configuration is invalid or incomplete."""


class WorkspaceError(ArchimedesError):
    """The official workspace layout is missing or inaccessible."""


class CaseFormatError(ArchimedesError):
    """Case metadata or input log does not have a supported shape."""


class PathPolicyError(ArchimedesError):
    """A requested filesystem operation violates workspace policy."""


class ModelError(ArchimedesError):
    """A model provider could not complete a request safely."""


class ModelProtocolError(ModelError):
    """A model response did not satisfy the structured JSON contract."""


class PatchValidationError(ArchimedesError):
    """A structured edit failed a deterministic guard."""


class BuildFeedbackError(ArchimedesError):
    """The official build-feedback adapter failed."""


class BudgetExhaustedError(ArchimedesError):
    """A configured model, build, time, or monetary budget is exhausted."""
