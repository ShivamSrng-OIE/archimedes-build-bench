"""Deterministic build-log normalization and causal failure analysis."""

from .log_parser import analyze_build_log

__all__ = ["analyze_build_log"]
