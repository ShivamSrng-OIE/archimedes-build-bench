"""Compatibility facade for build-output analysis."""

from .log_parser import analyze_build_log, identify_primary_diagnostic, normalize_log

__all__ = ["analyze_build_log", "identify_primary_diagnostic", "normalize_log"]
