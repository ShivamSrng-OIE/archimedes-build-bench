"""Adapter for a platform-provided ``bb-build`` command, used only when the Case declares it.

The command is run without arguments in the worktree, and its stdout must be a
small protocol 0.1 JSON object.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import BuildFeedbackError
from ..forensics.log_parser import analyze_build_log
from ..json_safety import reject_excessive_json_nesting
from .feedback import BuildFeedbackResult

_MAX_TASK_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_BUILD_TIMEOUT_SECONDS = 3_600


def _reject_nonfinite_constant(value: str) -> None:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity`` while decoding JSON.

    Args:
        value: The constant name the decoder found.

    Returns:
        Never returns.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_object(
    data: bytes, *, description: str, max_bytes: int = _MAX_RESPONSE_BYTES
) -> dict[str, Any]:
    """Decode bytes that must hold a single JSON object.

    Args:
        data: The raw bytes.
        description: What the bytes are, used in error messages.
        max_bytes: Largest accepted size; 64 KiB by default.

    Returns:
        The decoded object.

    Raises:
        BuildFeedbackError: If the data is too large, is not UTF-8 JSON within
            the nesting limit and free of non-finite numbers, or is not an
            object.
    """
    if len(data) > max_bytes:
        raise BuildFeedbackError(f"{description} exceeds the maximum allowed size")
    try:
        text = data.decode("utf-8")
        reject_excessive_json_nesting(text)
        value = json.loads(text, parse_constant=_reject_nonfinite_constant)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise BuildFeedbackError(f"{description} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BuildFeedbackError(f"{description} root must be an object")
    return value


def _bounded_timeout() -> int:
    """Work out how long to wait for one ``bb-build`` run.

    ``BB_BUILD_GATEWAY_TIMEOUT_SECONDS`` (default 900) is clamped to 1..3,600
    seconds, and 5 seconds are added so the gateway can time out and answer
    before the Agent gives up on it.

    Returns:
        The subprocess timeout in seconds.
    """
    raw = os.environ.get("BB_BUILD_GATEWAY_TIMEOUT_SECONDS", "900")
    try:
        value = int(raw)
    except ValueError:
        value = 900
    return min(max(value, 1), _MAX_BUILD_TIMEOUT_SECONDS) + 5


@dataclass(slots=True)
class BbBuildFeedbackProvider:
    """Build feedback through ``bb-build``, used only when both sides offer it.

    The current public Starter Kit does not provide this command. The provider
    is available only when the executable is on ``PATH`` and the Case's
    ``task.json`` declares the protocol 0.1 ``bb-build`` capability; ``main``
    never creates it in hosted mode.

    Attributes:
        executable: Full path of the ``bb-build`` command.
        repo_root: Worktree the command runs in.
        request_limit: Most builds the Case allows; 0 when unavailable.
        available: Whether builds may be requested.
        reason_unavailable: Why builds are not available, or ``None``.
        timeout_seconds: Time limit for one ``bb-build`` run.
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
    ) -> BbBuildFeedbackProvider:
        """Create the provider, reading the capability from ``input/task.json``.

        ``task.json`` must be a regular file inside ``input/`` (not a symlink),
        at most 1,000,000 bytes, with ``schema_version`` ``0.1`` and a
        ``build_feedback`` object whose ``command`` is ``bb-build`` and whose
        ``attempt_limit`` is an integer from 1 to 100. Any problem leaves the
        provider unavailable with the reason recorded; nothing is raised.

        Args:
            executable: Full path of the ``bb-build`` command.
            input_dir: The workspace ``input/`` directory.
            repo_root: The worktree root.

        Returns:
            The provider, available or not.
        """
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
                raise BuildFeedbackError(
                    "task metadata exceeds the maximum allowed size"
                )
            payload = _strict_json_object(
                resolved_task.read_bytes(),
                description="task metadata",
                max_bytes=_MAX_TASK_BYTES,
            )
            feedback = payload.get("build_feedback")
            if not isinstance(feedback, dict):
                raise BuildFeedbackError(reason)
            if feedback.get("command") != "bb-build":
                raise BuildFeedbackError(
                    "task metadata declares an unsupported build command"
                )
            limit = feedback.get("attempt_limit")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 100
            ):
                raise BuildFeedbackError(
                    "bb-build attempt_limit must be an integer from 1 to 100"
                )
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
        """Run ``bb-build`` once and turn its answer into a failure frontier.

        Exit status 0 with ``ok`` true is a usable answer; exit status 1 is
        accepted only to read the error from an ``ok`` false answer. The
        returned log excerpt (or message, or status) is run through the same
        log forensics as the original failure.

        Args:
            expected_information_value: How much the caller expects to learn;
                accepted for interface compatibility and not used.

        Returns:
            Whether the build succeeded, the log text, its frontier and an
            attempt-based request ID.

        Raises:
            BuildFeedbackError: If the provider is unavailable, the command
                cannot run or times out, its output is not a valid protocol 0.1
                answer, the service rejects the request, or the attempt fields
                disagree with the declared limit.
        """
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
        payload = _strict_json_object(completed.stdout, description="bb-build response")
        if completed.returncode not in {0, 1}:
            raise BuildFeedbackError(
                "bb-build command failed without a protocol response"
            )
        if payload.get("schema_version") != "0.1" or not isinstance(
            payload.get("ok"), bool
        ):
            raise BuildFeedbackError("bb-build response does not satisfy protocol 0.1")
        if payload["ok"] is not True:
            error = payload.get("error")
            safe_error = (
                error
                if isinstance(error, str) and len(error) <= 100
                else "request_failed"
            )
            raise BuildFeedbackError(f"bb-build rejected the request: {safe_error}")
        if completed.returncode != 0:
            raise BuildFeedbackError(
                "bb-build success response used a failing exit status"
            )

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
