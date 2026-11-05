"""Load a Case's metadata and failing build log from ``/workspace/input``.

It accepts the flat Starter Kit task format, the released
``buildbench.case.v0.1`` format and the synthetic development Cases. Every read
is size-limited, and an architecture that cannot be found stays unknown.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..architecture.aliases import normalize_architecture
from ..constants import MAX_LOG_CHARACTERS
from ..errors import CaseFormatError
from ..json_safety import reject_excessive_json_nesting
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
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity`` while decoding JSON.

    Args:
        value: The constant name the decoder found.

    Returns:
        Never returns.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _bounded_inline_log(value: str) -> str:
    """Shorten a log given inline in the metadata to ``MAX_LOG_CHARACTERS``.

    Args:
        value: The full log text.

    Returns:
        The log unchanged if it fits, otherwise its first ``_LOG_HEAD_BYTES``
        characters and its last characters joined by an omission marker.
    """
    if len(value) <= MAX_LOG_CHARACTERS:
        return value
    tail = MAX_LOG_CHARACTERS - _LOG_HEAD_BYTES
    return (
        value[:_LOG_HEAD_BYTES]
        + "\n[... middle of oversized build log omitted ...]\n"
        + value[-tail:]
    )


def _read_bounded_log(path: Path) -> str:
    """Read a log file, keeping only its head and tail when it is too large.

    The head holds the build setup and the tail holds the terminal error, which
    are the parts forensics needs. An oversized file is never read in full.

    Args:
        path: The log file to read.

    Returns:
        The decoded text, with invalid UTF-8 replaced, and with an omission
        marker in place of the middle when the file is over
        ``MAX_LOG_CHARACTERS`` bytes.

    Raises:
        OSError: If the file cannot be read.
    """
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
    """Load the first metadata file found in the input directory.

    Names in ``_METADATA_NAMES`` are tried in order. A file that resolves
    outside ``input_dir`` (for example through a symlink) is treated as an
    error rather than skipped.

    Args:
        input_dir: The workspace ``input/`` directory.

    Returns:
        The decoded metadata object, or an empty dictionary if no metadata file
        exists.

    Raises:
        CaseFormatError: If a metadata file is larger than 1,000,000 bytes,
            escapes ``input_dir``, cannot be read, is not valid JSON within the
            nesting limit, or is not a JSON object.
    """
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
            raise CaseFormatError(
                f"cannot inspect case metadata {path.name}: {exc}"
            ) from exc
        try:
            text = resolved.read_text(encoding="utf-8")
            reject_excessive_json_nesting(text)
            value = json.loads(
                text,
                parse_constant=_reject_nonfinite_constant,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
            ValueError,
            RecursionError,
        ) as exc:
            raise CaseFormatError(
                f"cannot read case metadata {path.name}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise CaseFormatError(f"case metadata {path.name} must contain an object")
        return value
    return {}


def _first_string(metadata: dict[str, JSONValue], names: tuple[str, ...]) -> str | None:
    """Find the first non-empty string field among several possible names.

    Top-level fields are checked first, then the same names inside the
    ``architectures`` and ``platform`` objects.

    Args:
        metadata: The Case metadata.
        names: Field names to try, in order.

    Returns:
        The stripped value, or ``None`` if none of the names holds a non-empty
        string.
    """
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
    """Find the first non-empty string field inside named sub-objects.

    Args:
        metadata: The Case metadata.
        containers: Names of the sub-objects to search, in order.
        names: Field names to try in each sub-object, in order.

    Returns:
        The stripped value, or ``None`` if nothing matches.
    """
    for container_name in containers:
        container = metadata.get(container_name)
        if not isinstance(container, dict):
            continue
        for name in names:
            value = container.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _released_case_identity(
    repo_dir: Path,
) -> tuple[str | None, str | None, str | None]:
    """Read the source, target and Case ID from a released ``case.json`` in the worktree.

    Used only when the input metadata lacks these fields. The file is trusted
    only if it is a regular file inside the worktree (not a symlink), is at
    most 1,000,000 bytes, has ``schema_version`` ``buildbench.case.v0.1`` and
    task type ``cross_arch_package_build_repair``. Only the three documented
    identity fields are taken from it.

    Args:
        repo_dir: The worktree root, ``work/repo``.

    Returns:
        The source architecture, target architecture and Case ID; each is
        ``None`` when missing, and all three are ``None`` when the file is
        absent or not trusted.
    """
    candidate = repo_dir / "case.json"
    try:
        if candidate.is_symlink():
            return None, None, None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_dir.resolve(strict=True))
        if not resolved.is_file() or resolved.stat().st_size > _MAX_METADATA_BYTES:
            return None, None, None
        text = resolved.read_text(encoding="utf-8")
        reject_excessive_json_nesting(text)
        value = json.loads(text, parse_constant=_reject_nonfinite_constant)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        return None, None, None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != _RELEASED_CASE_SCHEMA
    ):
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
        """Return a stripped non-empty string field, or ``None``.

        Args:
            container: The object to read from.
            name: The field name.

        Returns:
            The stripped string, or ``None`` if the field is missing, empty or
            not a string.
        """
        field = container.get(name)
        return field.strip() if isinstance(field, str) and field.strip() else None

    return (
        documented_string(platform, "source_arch"),
        documented_string(platform, "target_arch"),
        documented_string(value, "case_id"),
    )


def _load_log(input_dir: Path, metadata: dict[str, JSONValue]) -> str:
    """Find and read the failing build log.

    An inline ``build_log`` or ``failure_log`` string wins. Otherwise a path
    named in the metadata (top level, ``task`` or ``paths``) is tried first,
    then the conventional names in ``_LOG_NAMES``. Paths that resolve outside
    ``input_dir`` or cannot be read are skipped.

    Args:
        input_dir: The workspace ``input/`` directory.
        metadata: The Case metadata.

    Returns:
        The size-limited log text, or an empty string if no log was found.
    """
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
    """Load the Case metadata, architectures, ID and failing build log.

    Fields from the input metadata take priority; the released ``case.json``
    in the worktree only fills in what is missing. A missing architecture
    becomes ``Architecture.UNKNOWN`` rather than a guess, and a missing Case ID
    falls back to the workspace directory name.

    Args:
        paths: The resolved workspace.

    Returns:
        The loaded Case.

    Raises:
        CaseFormatError: If the input metadata file is present but unusable.
    """
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
