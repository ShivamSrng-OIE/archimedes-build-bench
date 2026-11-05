"""Stage-tagged logging to stderr with credentials removed from every message.

Redaction works on patterns, so it does not need to know the secret value.
"""

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
    """Replace credential values in text with ``[REDACTED]``.

    It covers ``Authorization: Bearer ...`` headers, ``key=value`` or
    ``key: value`` pairs whose key mentions an API key, token, password,
    credential or secret, and ``api_key``/``token`` URL query parameters.

    Args:
        value: Text that may contain credentials.

    Returns:
        The text with each matched credential value replaced.
    """
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    """Log formatter that runs ``redact_secrets`` over every formatted message."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record and remove any credentials from the result.

        Args:
            record: The log record to format.

        Returns:
            The formatted, redacted message.
        """
        return redact_secrets(super().format(record))


def configure_logging(level: str) -> None:
    """Set the root log level and make every root handler redact credentials.

    The first call adds one stderr handler. Later calls only change the level
    and reapply the formatter to the existing handlers, so no message is
    printed twice.

    Args:
        level: A standard logging level name such as ``INFO``.

    Returns:
        None.
    """
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
    """Log one line of the form ``[stage] message key=value ...`` at INFO level.

    Args:
        logger: Logger to write to.
        stage: Pipeline stage shown in brackets.
        message: Short description of the event.
        **fields: Extra values, written in sorted key order.

    Returns:
        None.
    """
    suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    logger.info("[%s] %s%s", stage, message, f" {suffix}" if suffix else "")
