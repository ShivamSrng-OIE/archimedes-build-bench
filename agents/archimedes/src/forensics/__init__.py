"""Read a failed build log and work out what actually broke.

The package normalizes logs, picks the primary diagnostic, classifies the failure family,
computes a stable error signature and maps the result to a failure frontier. Only
``analyze_build_log`` is exported; the other modules are used through it.
"""

from .log_parser import analyze_build_log

__all__ = ["analyze_build_log"]
