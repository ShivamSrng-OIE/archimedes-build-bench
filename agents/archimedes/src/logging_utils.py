"""Safe structured logging and credential redaction."""

from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|password|credential|secret)\s*[=:]\s*)[^\s&,}]+"
    ),
    re.compile(r"([?&](?:api[_-]?key|token)=)[^&\s]+", re.IGNORECASE),
)


def redact_secrets(value: str) -> str:
    """Redact common credential forms without requiring knowledge of the secret value."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def configure_logging(level: str) -> None:
    """Configure a compact stage-oriented stderr logger exactly once."""
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(RedactingFormatter("%(message)s"))
        return
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(message)s"))
    root.addHandler(handler)


def log_event(logger: logging.Logger, stage: str, message: str, **fields: object) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    logger.info("[%s] %s%s", stage, message, f" {suffix}" if suffix else "")
