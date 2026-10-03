"""Capability-gated adapter for the platform-owned ``bb-build`` command."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import BuildFeedbackError
from ..forensics.log_parser import analyze_build_log
from .feedback import BuildFeedbackResult


_MAX_TASK_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_BUILD_TIMEOUT_SECONDS = 3_600


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_object(
    data: bytes, *, description: str, max_bytes: int = _MAX_RESPONSE_BYTES
) -> dict[str, Any]:
    if len(data) > max_bytes:
        raise BuildFeedbackError(f"{description} exceeds the maximum allowed size")
    try:
        value = json.loads(
            data.decode("utf-8"), parse_constant=_reject_nonfinite_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BuildFeedbackError(f"{description} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BuildFeedbackError(f"{description} root must be an object")
    return value


def _bounded_timeout() -> int:
    raw = os.environ.get("BB_BUILD_GATEWAY_TIMEOUT_SECONDS", "900")
    try:
        value = int(raw)
    except ValueError:
        value = 900
    return min(max(value, 1), _MAX_BUILD_TIMEOUT_SECONDS) + 5


@dataclass(slots=True)
class BbBuildFeedbackProvider:
    """Invoke only an explicitly declared and discovered platform capability.

    The current public Starter Kit does not expose this command. This adapter stays
    unavailable unless both the Case metadata and the executable independently
    advertise the exact protocol-0.1 capability.
    """

    executable: str
    repo_root: Path
    request_limit: int
    available: bool
    reason_unavailable: str | None
    timeout_seconds: int

    @classmethod
    def discover(
        cls, *, executable: str, input_dir: Path, repo_root: Path
    ) -> "BbBuildFeedbackProvider":
        reason = "Case metadata does not declare the bb-build protocol-0.1 capability"
        request_limit = 0
        task_path = input_dir / "task.json"
        try:
            resolved_input = input_dir.resolve(strict=True)
            if task_path.is_symlink():
                raise BuildFeedbackError("task metadata must not be a symbolic link")
            resolved_task = task_path.resolve(strict=True)
            resolved_task.relative_to(resolved_input)
            if not resolved_task.is_file():
                raise BuildFeedbackError("task metadata is not a regular file")
            size = resolved_task.stat().st_size
            if size > _MAX_TASK_BYTES:
                raise BuildFeedbackError("task metadata exceeds the maximum allowed size")
            payload = _strict_json_object(
                resolved_task.read_bytes(),
                description="task metadata",
                max_bytes=_MAX_TASK_BYTES,
            )
            feedback = payload.get("build_feedback")
            if not isinstance(feedback, dict):
                raise BuildFeedbackError(reason)
            if feedback.get("command") != "bb-build":
                raise BuildFeedbackError("task metadata declares an unsupported build command")
            limit = feedback.get("attempt_limit")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise BuildFeedbackError("bb-build attempt_limit must be an integer from 1 to 100")
            if payload.get("schema_version") != "0.1":
                raise BuildFeedbackError("bb-build requires task schema_version 0.1")
            request_limit = limit
            reason = ""
        except (OSError, ValueError, BuildFeedbackError) as exc:
            reason = str(exc)
        return cls(
            executable=executable,
            repo_root=repo_root,
            request_limit=request_limit,
            available=request_limit > 0,
            reason_unavailable=reason or None,
            timeout_seconds=_bounded_timeout(),
        )

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        del expected_information_value
        if not self.available:
            raise BuildFeedbackError(
                self.reason_unavailable or "bb-build feedback is unavailable"
            )
        try:
            completed = subprocess.run(
                [self.executable],
                cwd=self.repo_root,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BuildFeedbackError(
                f"bb-build invocation failed: {type(exc).__name__}"
            ) from exc
        payload = _strict_json_object(
            completed.stdout, description="bb-build response"
        )
        if completed.returncode not in {0, 1}:
            raise BuildFeedbackError("bb-build command failed without a protocol response")
        if payload.get("schema_version") != "0.1" or not isinstance(payload.get("ok"), bool):
            raise BuildFeedbackError("bb-build response does not satisfy protocol 0.1")
        if payload["ok"] is not True:
            error = payload.get("error")
            safe_error = error if isinstance(error, str) and len(error) <= 100 else "request_failed"
            raise BuildFeedbackError(f"bb-build rejected the request: {safe_error}")
        if completed.returncode != 0:
            raise BuildFeedbackError("bb-build success response used a failing exit status")

        attempt = payload.get("attempt")
        attempt_limit = payload.get("attempt_limit")
        status = payload.get("status")
        message = payload.get("message")
        log_excerpt = payload.get("log_excerpt")
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 1 <= attempt <= self.request_limit
            or isinstance(attempt_limit, bool)
            or not isinstance(attempt_limit, int)
            or attempt_limit != self.request_limit
            or not isinstance(status, str)
            or not status.strip()
            or not isinstance(message, str)
            or not isinstance(log_excerpt, str)
        ):
            raise BuildFeedbackError("bb-build response fields are invalid")
        raw_log = log_excerpt or message or status
        failure = analyze_build_log(raw_log)
        return BuildFeedbackResult(
            success=status == "succeeded",
            raw_log=raw_log,
            frontier=failure.frontier,
            request_id=f"bb-build-attempt-{attempt}",
        )
