"""Tolerant, bounded ingestion of an official or synthetic Build-Bench case."""

from __future__ import annotations

import json
from pathlib import Path

from ..architecture.aliases import normalize_architecture
from ..constants import MAX_LOG_CHARACTERS
from ..errors import CaseFormatError
from ..schemas import CaseData, JSONValue
from .paths import WorkspacePaths

_METADATA_NAMES = (
    "task.json",
    "case.json",
    "metadata.json",
    "case-metadata.json",
    "input.json",
)
_LOG_NAMES = (
    "initial-build.log",
    "target-failure.log",
    "logs/target-failure.log",
    "build.log",
    "failure.log",
    "log.txt",
)
_RELEASED_CASE_SCHEMA = "buildbench.case.v0.1"
_RELEASED_TASK_TYPE = "cross_arch_package_build_repair"
_MAX_METADATA_BYTES = 1_000_000
_LOG_HEAD_BYTES = min(256_000, MAX_LOG_CHARACTERS // 4)


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _bounded_inline_log(value: str) -> str:
    if len(value) <= MAX_LOG_CHARACTERS:
        return value
    tail = MAX_LOG_CHARACTERS - _LOG_HEAD_BYTES
    return (
        value[:_LOG_HEAD_BYTES]
        + "\n[... middle of oversized build log omitted ...]\n"
        + value[-tail:]
    )


def _read_bounded_log(path: Path) -> str:
    """Preserve setup and the terminal failure without loading an oversized log."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size <= MAX_LOG_CHARACTERS:
            data = stream.read(MAX_LOG_CHARACTERS)
        else:
            tail_bytes = MAX_LOG_CHARACTERS - _LOG_HEAD_BYTES
            head = stream.read(_LOG_HEAD_BYTES)
            stream.seek(-tail_bytes, 2)
            tail = stream.read(tail_bytes)
            data = head + b"\n[... middle of oversized build log omitted ...]\n" + tail
    return data.decode("utf-8", errors="replace")


def _load_metadata(input_dir: Path) -> dict[str, JSONValue]:
    for name in _METADATA_NAMES:
        path = input_dir / name
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(input_dir.resolve(strict=True))
            if not resolved.is_file():
                continue
            if resolved.stat().st_size > _MAX_METADATA_BYTES:
                raise CaseFormatError(
                    f"case metadata {path.name} exceeds {_MAX_METADATA_BYTES} bytes"
                )
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise CaseFormatError(f"cannot inspect case metadata {path.name}: {exc}") from exc
        try:
            value = json.loads(
                resolved.read_text(encoding="utf-8"),
                parse_constant=_reject_nonfinite_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
            raise CaseFormatError(f"cannot read case metadata {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise CaseFormatError(f"case metadata {path.name} must contain an object")
        return value
    return {}


def _first_string(metadata: dict[str, JSONValue], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for container_name in ("architectures", "platform"):
        container = metadata.get(container_name)
        if not isinstance(container, dict):
            continue
        for name in names:
            value = container.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _nested_string(
    metadata: dict[str, JSONValue],
    containers: tuple[str, ...],
    names: tuple[str, ...],
) -> str | None:
    for container_name in containers:
        container = metadata.get(container_name)
        if not isinstance(container, dict):
            continue
        for name in names:
            value = container.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _released_case_identity(repo_dir: Path) -> tuple[str | None, str | None, str | None]:
    """Read only the documented identity fields from a released worktree case file."""
    candidate = repo_dir / "case.json"
    try:
        if candidate.is_symlink():
            return None, None, None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_dir.resolve(strict=True))
        if not resolved.is_file() or resolved.stat().st_size > _MAX_METADATA_BYTES:
            return None, None, None
        value = json.loads(
            resolved.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, None, None
    if not isinstance(value, dict) or value.get("schema_version") != _RELEASED_CASE_SCHEMA:
        return None, None, None
    task = value.get("task")
    platform = value.get("platform")
    if (
        not isinstance(task, dict)
        or task.get("type") != _RELEASED_TASK_TYPE
        or not isinstance(platform, dict)
    ):
        return None, None, None

    def documented_string(container: dict[str, JSONValue], name: str) -> str | None:
        field = container.get(name)
        return field.strip() if isinstance(field, str) and field.strip() else None

    return (
        documented_string(platform, "source_arch"),
        documented_string(platform, "target_arch"),
        documented_string(value, "case_id"),
    )


def _load_log(input_dir: Path, metadata: dict[str, JSONValue]) -> str:
    inline = metadata.get("build_log") or metadata.get("failure_log")
    if isinstance(inline, str):
        return _bounded_inline_log(inline)
    configured = (
        metadata.get("initial_build_log")
        or metadata.get("build_log_path")
        or _nested_string(
            metadata,
            ("task",),
            ("initial_failure_evidence", "initial_build_log", "build_log_path"),
        )
        or _nested_string(
            metadata,
            ("paths",),
            ("target_failure_log", "initial_build_log", "build_log_path"),
        )
    )
    names = ((configured,) if isinstance(configured, str) else ()) + _LOG_NAMES
    for name in names:
        path = input_dir / name
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(input_dir.resolve(strict=True))
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            try:
                return _read_bounded_log(resolved)
            except OSError:
                continue
    return ""


def load_case(paths: WorkspacePaths) -> CaseData:
    """Load available metadata/log fields while leaving unknown architecture unknown."""
    metadata = _load_metadata(paths.input)
    source = _first_string(metadata, ("source_architecture", "source_arch", "source"))
    target = _first_string(metadata, ("target_architecture", "target_arch", "target"))
    case_id = _first_string(metadata, ("case_id", "id", "name"))
    if source is None or target is None or case_id is None:
        released_source, released_target, released_case_id = _released_case_identity(
            paths.repo
        )
        source = source or released_source
        target = target or released_target
        case_id = case_id or released_case_id
    return CaseData(
        case_id=case_id or paths.root.name,
        source_architecture=normalize_architecture(source or "unknown"),
        target_architecture=normalize_architecture(target or "unknown"),
        build_log=_load_log(paths.input, metadata),
        metadata=metadata,
    )
