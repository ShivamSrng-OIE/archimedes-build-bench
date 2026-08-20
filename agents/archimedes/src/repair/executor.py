"""Atomic structured text edits guarded by content, hash, path, and binary checks."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import PatchValidationError
from ..schemas import PatchCandidate, PlannedEdit
from ..workspace.path_policy import PathPolicy


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class AppliedEdit:
    path: Path
    relative_path: str
    before: bytes | None
    after_hash: str
    before_hash: str | None
    changed_line_start: int
    changed_line_end: int


@dataclass(slots=True)
class EditTransaction:
    edits: list[AppliedEdit]
    committed: bool = True


def _decode_text(data: bytes, path: Path) -> str:
    if b"\x00" in data[:8192]:
        raise PatchValidationError(f"binary text edit rejected: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchValidationError(f"non-UTF-8 text edit rejected: {path}") from exc


def _apply_operation(edit: PlannedEdit, original: str) -> tuple[str, int, int]:
    if edit.operation == "create_file":
        if original:
            raise PatchValidationError(f"create_file target already contains data: {edit.path}")
        return edit.replacement, 1, max(1, edit.replacement.count("\n") + 1)
    if edit.expected_text is None:
        raise PatchValidationError(f"{edit.operation} requires expected_text: {edit.path}")
    newline = "\r\n" if "\r\n" in original else "\r" if "\r" in original else "\n"
    expected = edit.expected_text.replace("\r\n", "\n").replace("\r", "\n")
    replacement = edit.replacement.replace("\r\n", "\n").replace("\r", "\n")
    expected = expected.replace("\n", newline)
    replacement = replacement.replace("\n", newline)
    occurrences = original.count(expected)
    if occurrences != 1:
        raise PatchValidationError(
            f"expected text must occur exactly once in {edit.path}; found {occurrences}"
        )
    prefix, suffix = original.split(expected, 1)
    start = prefix.count("\n") + 1
    if edit.operation == "replace_lines":
        result = prefix + replacement + suffix
    elif edit.operation == "insert_before":
        result = prefix + replacement + expected + suffix
    elif edit.operation == "insert_after":
        result = prefix + expected + replacement + suffix
    else:
        raise PatchValidationError(f"unsupported structured operation: {edit.operation}")
    return result, start, start + max(0, replacement.count(newline))


def _atomic_write(path: Path, data: bytes, mode: int | None) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, stat.S_IMODE(mode))
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def apply_candidate(candidate: PatchCandidate, path_policy: PathPolicy) -> EditTransaction:
    """Validate every operation in memory, then atomically write the complete candidate."""
    prepared: list[tuple[Path, str, bytes | None, bytes, int, int, int | None]] = []
    seen: set[Path] = set()
    for edit in candidate.planned_edits:
        creating = edit.operation == "create_file"
        path = path_policy.validate_relative(edit.path, creating=creating)
        if path in seen:
            raise PatchValidationError("multiple operations on one file require pre-composition")
        seen.add(path)
        before = path.read_bytes() if path.exists() else None
        if edit.expected_hash and sha256(before or b"") != edit.expected_hash.lower():
            raise PatchValidationError(f"file hash changed before edit: {edit.path}")
        original = _decode_text(before or b"", path)
        updated, start, end = _apply_operation(edit, original)
        mode = path.stat().st_mode if path.exists() else None
        prepared.append((path, edit.path, before, updated.encode("utf-8"), start, end, mode))

    applied: list[AppliedEdit] = []
    try:
        for path, relative, before, after, start, end, mode in prepared:
            _atomic_write(path, after, mode)
            applied.append(AppliedEdit(path, relative, before, sha256(after), sha256(before) if before is not None else None, start, end))
    except OSError as exc:
        transaction = EditTransaction(applied)
        rollback_transaction(transaction)
        raise PatchValidationError(f"atomic write failed: {exc}") from exc
    return EditTransaction(applied)


def rollback_transaction(transaction: EditTransaction) -> None:
    """Revert only files still matching this transaction's recorded after-hash."""
    if not transaction.committed:
        return
    for edit in reversed(transaction.edits):
        if not edit.path.exists() or sha256(edit.path.read_bytes()) != edit.after_hash:
            raise PatchValidationError(f"cannot rollback externally modified file: {edit.relative_path}")
        if edit.before is None:
            edit.path.unlink()
        else:
            _atomic_write(edit.path, edit.before, edit.path.stat().st_mode)
    transaction.committed = False
