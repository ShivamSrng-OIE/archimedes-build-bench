"""Bounded local execution traces with no network or runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .logging_utils import redact_secrets
from .schemas import JSONValue


_SENSITIVE_KEYS = ("api_key", "apikey", "authorization", "credential", "password", "secret", "token")


def _safe_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_secrets(value)[:1000]
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _safe_value(item)
            for key, item in list(value.items())[:50]
        }
    return redact_secrets(str(value))[:1000]


def _safe_attribute(key: object, value: object) -> tuple[str, JSONValue]:
    safe_key = str(key)[:100]
    if any(marker in safe_key.lower() for marker in _SENSITIVE_KEYS):
        return safe_key, "[REDACTED]"
    return safe_key, _safe_value(value)


@dataclass(slots=True)
class TraceRecorder:
    """Record a deterministic event sequence plus monotonic development timing.

    Trace events never contain raw source, logs, prompts, model responses, or credentials. Each
    event is appended as one JSON object so a partially completed run remains inspectable.
    """

    path: Path
    case_id: str
    enabled: bool = True
    max_events: int = 500
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("archimedes.trace"))
    _started: float = field(default_factory=time.monotonic, init=False)
    _sequence: int = field(default=0, init=False)
    _previous_stage: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.max_events = max(1, self.max_events)
        if self.enabled:
            self.path.write_text("", encoding="utf-8")

    @property
    def trace_id(self) -> str:
        digest = hashlib.sha256(self.case_id.encode("utf-8", errors="replace")).hexdigest()
        return f"trace-{digest[:16]}"

    def record(self, stage: str, action: str, **attributes: object) -> None:
        """Append one safe event and mirror it to the captured agent log."""
        if not self.enabled or self._sequence >= self.max_events:
            return
        self._sequence += 1
        event: dict[str, JSONValue] = {
            "schema_version": "0.1",
            "trace_id": self.trace_id,
            "case_id": self.case_id[:200],
            "sequence": self._sequence,
            "elapsed_ms": round((time.monotonic() - self._started) * 1000, 3),
            "stage": stage,
            "action": action,
            "attributes": dict(_safe_attribute(key, value) for key, value in sorted(attributes.items())),
        }
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
        self.logger.info("[trace] %s", encoded)

    def transition(self, stage: str, **attributes: object) -> None:
        previous = self._previous_stage
        self.record(stage, "state_transition", previous_stage=previous, **attributes)
        self._previous_stage = stage
