"""Build stable error signatures so the same failure compares equal across attempts.

Paths under temporary directories, timestamps, line and column numbers, hex addresses and
long hex IDs change from build to build, so they are replaced with placeholders.
"""

from __future__ import annotations

import re

_TEMP_PATH = re.compile(
    r"(?:/[\w.-]+)*?/tmp/[\w./-]+|"
    r"[A-Za-z]:[\\/](?:[^\\/\s:]+[\\/])*?(?:Temp|tmp)[\\/][^\s:]+",
    re.IGNORECASE,
)
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+\b")
_LINE_COLUMN = re.compile(r"(?<=:)(?:\d+)(?::\d+)?(?=:)" )
_HEX_ADDRESS = re.compile(r"\b0x[0-9a-f]{6,}\b", re.IGNORECASE)
_LONG_ID = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_error_signature(diagnostic: str) -> str:
    """Replace the parts of a diagnostic that change between builds with placeholders.

    Temporary paths become ``<temp>``, ISO-style timestamps ``<timestamp>``, the
    ``:line:`` or ``:line:column:`` part of a compiler location ``<line>``, hex
    addresses of six or more digits ``<address>`` and hex runs of 16 or more digits
    ``<id>``. Symbol names, flags and file names are kept, since they identify the
    cause. For example, ``error: Bad exit status from /var/tmp/rpm-tmp.Xy12 (%build)``
    becomes ``error: bad exit status from <temp> (%build)``.

    Args:
        diagnostic: The primary diagnostic line, or any error text.

    Returns:
        The normalized text with whitespace collapsed, lowercased and cut to at most
        1,000 characters.
    """
    value = _TEMP_PATH.sub("<temp>", diagnostic)
    value = _TIMESTAMP.sub("<timestamp>", value)
    value = _LINE_COLUMN.sub("<line>", value)
    value = _HEX_ADDRESS.sub("<address>", value)
    value = _LONG_ID.sub("<id>", value)
    return _WHITESPACE.sub(" ", value).strip().lower()[:1000]
