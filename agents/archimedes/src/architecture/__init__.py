"""Static analysis of what the target architecture is and what in the package depends on it.

It covers architecture-name aliases, the hazard scanner, semantic anchors, the
architecture profile and its familiarity score, discovery from static
evidence, and the serialised architecture constitution. Nothing here runs
repository code or any external tool.
"""

from .aliases import normalize_architecture

__all__ = ["normalize_architecture"]
