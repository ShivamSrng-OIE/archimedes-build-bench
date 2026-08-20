"""Atomic protocol and bounded development-diagnostic output."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from .schemas import AgentResult, JSONValue


def _json_default(value: object) -> JSONValue:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)  # type: ignore[arg-type]
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_json(path: Path, payload: object) -> None:
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
    """Write the official protocol 0.1 result atomically to output/."""
    path = output_dir / "agent-result.json"
    _write_json(path, result.to_dict())
    return path


def write_diagnostic(output_dir: Path, name: str, payload: object) -> Path:
    if not name.endswith(".json") or "/" in name or "\\" in name:
        raise ValueError("diagnostic name must be one JSON basename")
    path = output_dir / name
    _write_json(path, payload)
    return path
