"""Atomic writing of ``agent-result.json`` and the JSON diagnostic files in ``output/``."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from .schemas import AgentResult, JSONValue


def _json_default(value: object) -> JSONValue:
    """Convert values the ``json`` module cannot encode on its own.

    Args:
        value: An enum, path or dataclass instance.

    Returns:
        The enum's value, the path as a string, or the dataclass as a
        dictionary.

    Raises:
        TypeError: If the value is of any other type.
    """
    if isinstance(value, Enum):
        return cast(JSONValue, value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return cast(JSONValue, asdict(cast(Any, value)))
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: object) -> None:
    """Write a payload as indented, key-sorted JSON through an atomic rename.

    The data goes to a temporary file in the same directory, is flushed and
    fsynced, and then replaces ``path`` with ``os.replace``. A crash therefore
    leaves either the old file or the complete new one, never a partial file.

    Args:
        path: Destination file.
        payload: JSON-compatible data; enums, paths and dataclasses are
            converted.

    Returns:
        None.

    Raises:
        TypeError: If the payload contains a value that cannot be encoded.
        OSError: If the file cannot be written.
    """
    data = (json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def write_agent_result(output_dir: Path, result: AgentResult) -> Path:
    """Write the protocol 0.1 result to ``agent-result.json`` atomically.

    Args:
        output_dir: The workspace ``output/`` directory.
        result: The result to write.

    Returns:
        The path of the written file.
    """
    path = output_dir / "agent-result.json"
    _write_json(path, result.to_dict())
    return path


def write_diagnostic(output_dir: Path, name: str, payload: object) -> Path:
    """Write one diagnostic JSON file, such as the evidence ledger, to ``output/``.

    Args:
        output_dir: The workspace ``output/`` directory.
        name: A bare file name ending in ``.json``.
        payload: JSON-compatible data to write.

    Returns:
        The path of the written file.

    Raises:
        ValueError: If ``name`` contains a path separator or does not end in
            ``.json``.
    """
    if not name.endswith(".json") or "/" in name or "\\" in name:
        raise ValueError("diagnostic name must be one JSON basename")
    path = output_dir / name
    _write_json(path, payload)
    return path
