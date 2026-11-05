"""Apply a patch candidate's edits to the worktree all at once, and roll them back.

Each edit names the exact text it expects to find. Every edit is checked in memory
against the path policy, the file hash, UTF-8 decoding and a binary check before any
file is written, and each file is then replaced through a temporary file and an atomic
rename.
"""

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
    """Return the SHA-256 digest of some bytes.

    Args:
        data: The bytes to hash.

    Returns:
        The digest as a lowercase hex string.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class AppliedEdit:
    """Record of one file written by ``apply_candidate``, kept so it can be undone.

    Attributes:
        path: Absolute path of the edited file.
        relative_path: The path as given in the planned edit, relative to the repository.
        before: The file's original bytes, or None if the edit created the file.
        after_hash: SHA-256 of the bytes that were written.
        before_hash: SHA-256 of the original bytes, or None for a created file.
        changed_line_start: First changed line, counted from 1.
        changed_line_end: Last changed line, counted from 1.
    """

    path: Path
    relative_path: str
    before: bytes | None
    after_hash: str
    before_hash: str | None
    changed_line_start: int
    changed_line_end: int


@dataclass(slots=True)
class EditTransaction:
    """The set of files written by one ``apply_candidate`` call.

    Attributes:
        edits: The applied edits, in the order they were written.
        committed: True while the edits are in place. ``rollback_transaction`` sets it
            to False once they have been reverted.
    """

    edits: list[AppliedEdit]
    committed: bool = True


def _decode_text(data: bytes, path: Path) -> str:
    """Decode file bytes as UTF-8 text, refusing binary content.

    A NUL byte in the first 8 KiB is treated as a sign of a binary file.

    Args:
        data: The file's bytes.
        path: The file path, used only in error messages.

    Returns:
        The decoded text.

    Raises:
        PatchValidationError: If the data looks binary or is not valid UTF-8.
    """
    if b"\x00" in data[:8192]:
        raise PatchValidationError(f"binary text edit rejected: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PatchValidationError(f"non-UTF-8 text edit rejected: {path}") from exc


def _apply_operation(edit: PlannedEdit, original: str) -> tuple[str, int, int]:
    """Compute a file's new text for one planned edit, without writing anything.

    The expected and replacement text are converted to the file's own line endings
    (CRLF, CR or LF, chosen by what the file already uses), so an edit written with
    LF still matches a CRLF file and does not mix line endings into it.

    Args:
        edit: The edit to apply. Its operation is ``create_file``, ``replace_lines``,
            ``insert_before`` or ``insert_after``.
        original: The file's current text; empty for a file that does not exist yet.

    Returns:
        A tuple of the new text and the first and last changed line numbers,
        counted from 1.

    Raises:
        PatchValidationError: If ``create_file`` targets a non-empty file, the edit has
            no ``expected_text``, the expected text does not occur exactly once, or the
            operation is not supported.
    """
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
    """Replace a file's content through a temporary file and an atomic rename.

    The temporary file is created in the same directory so ``os.replace`` stays on one
    file system, and it is flushed and fsynced before the rename. Readers see either
    the old file or the new one, never a partly written file.

    Args:
        path: The file to write. Its parent directory may be created, but not
            grandparents.
        data: The complete new content.
        mode: Permission bits to copy onto the new file, or None to keep the
            temporary file's default.

    Returns:
        None.

    Raises:
        OSError: If the directory cannot be created or the file cannot be written
            or renamed. The temporary file is removed in that case.
    """
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
    """Apply every edit in a candidate, or none of them.

    All edits are checked in memory first: the path must pass the path policy, each
    file may be touched by only one edit, ``create_file`` must not hit an existing
    file, an ``expected_hash`` must match the current bytes, and the expected text
    must occur exactly once. Only then are the files written. If a write fails, the
    files already written are rolled back.

    Args:
        candidate: The proposed repair whose edits should be written.
        path_policy: Decides which repository paths may be changed.

    Returns:
        A transaction recording each file's old content and new hash, so the change
        can be rolled back later.

    Raises:
        PathPolicyError: If an edit's path is not allowed.
        PatchValidationError: If any edit fails a check above, a file is binary or
            not UTF-8, two edits target the same file, or a write fails.
    """
    prepared: list[tuple[Path, str, bytes | None, bytes, int, int, int | None]] = []
    seen: set[Path] = set()
    for edit in candidate.planned_edits:
        creating = edit.operation == "create_file"
        path = path_policy.validate_relative(edit.path, creating=creating)
        if path in seen:
            raise PatchValidationError("multiple operations on one file require pre-composition")
        seen.add(path)
        before = path.read_bytes() if path.exists() else None
        if creating and before is not None:
            raise PatchValidationError(f"create_file target already exists: {edit.path}")
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
    """Undo a transaction, restoring each file's original content.

    Files are restored in reverse order. Before touching a file, its current hash is
    compared with the hash recorded when it was written, so a file changed by someone
    else since then is never overwritten. Created files are deleted. Calling this on
    a transaction that was already rolled back does nothing.

    Args:
        transaction: The transaction returned by ``apply_candidate``. Its
            ``committed`` flag is set to False on success.

    Returns:
        None.

    Raises:
        PatchValidationError: If a file is missing or no longer matches the content
            this transaction wrote. Files later in the edit list may already have
            been restored when this happens.
    """
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
