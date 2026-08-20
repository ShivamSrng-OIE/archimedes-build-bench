"""Stable failure signatures for cross-attempt comparison."""

from __future__ import annotations

import re

_TEMP_PATH = re.compile(
    r"(?:/[\w.-]+)*?/tmp/[\w./-]+|"
    r"[A-Za-z]:[\\/](?:[^\\/\s:]+[\\/])*?(?:Temp|tmp)[\\/][^\s:]+",
    re.I,
)
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+\b")
_LINE_COLUMN = re.compile(r"(?<=:)(?:\d+)(?::\d+)?(?=:)" )
_HEX_ADDRESS = re.compile(r"\b0x[0-9a-f]{6,}\b", re.I)
_LONG_ID = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_WHITESPACE = re.compile(r"\s+")


def normalize_error_signature(diagnostic: str) -> str:
    """Remove unstable locations and identifiers while preserving causal tokens."""
    value = _TEMP_PATH.sub("<temp>", diagnostic)
    value = _TIMESTAMP.sub("<timestamp>", value)
    value = _LINE_COLUMN.sub("<line>", value)
    value = _HEX_ADDRESS.sub("<address>", value)
    value = _LONG_ID.sub("<id>", value)
    return _WHITESPACE.sub(" ", value).strip().lower()[:1000]
