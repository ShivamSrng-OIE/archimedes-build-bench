"""The single authority on which worktree paths the Agent may edit.

The policy fails closed: any path it cannot prove safe is rejected with a
``PathPolicyError`` before the file is touched.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..errors import PathPolicyError
from ..json_safety import reject_excessive_json_nesting
from ..schemas import JSONValue

_FORBIDDEN_PARTS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_OFFICIAL_CASE_SCHEMA = "buildbench.case.v0.1"
_OFFICIAL_TASK_TYPE = "cross_arch_package_build_repair"
_MAX_CASE_METADATA_BYTES = 1_000_000


def _official_case_uses_input_prefix(repo_root: Path) -> bool:
    """Tell whether the worktree is a released Case whose package lives under ``input/``.

    In the released layout the worktree root holds ``case.json`` next to an
    ``input/`` directory with the package source. The file is trusted only if
    it is a regular file inside the worktree (not a symlink), is at most
    1,000,000 bytes, and declares schema ``buildbench.case.v0.1`` with task type
    ``cross_arch_package_build_repair``.

    Args:
        repo_root: The worktree root, ``work/repo``.

    Returns:
        True if edits should be limited to the ``input/`` subtree.
    """
    case_path = repo_root / "case.json"
    try:
        if case_path.is_symlink():
            return False
        resolved_root = repo_root.resolve(strict=True)
        resolved_case = case_path.resolve(strict=True)
        resolved_case.relative_to(resolved_root)
        if (
            not resolved_case.is_file()
            or resolved_case.stat().st_size > _MAX_CASE_METADATA_BYTES
            or not (resolved_root / "input").is_dir()
        ):
            return False
        text = resolved_case.read_text(encoding="utf-8")
        reject_excessive_json_nesting(text)
        payload = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False
    task = payload.get("task") if isinstance(payload, dict) else None
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == _OFFICIAL_CASE_SCHEMA
        and isinstance(task, dict)
        and task.get("type") == _OFFICIAL_TASK_TYPE
    )


@dataclass(frozen=True, slots=True)
class PathPolicy:
    """Rules that decide whether a proposed edit path is safe.

    Attributes:
        repo_root: The worktree root, ``work/repo``.
        allowed_patterns: Path prefixes or glob patterns the Case allows, or
            ``None`` when every path inside the worktree is allowed.
    """

    repo_root: Path
    allowed_patterns: tuple[str, ...] | None = None

    @classmethod
    def from_metadata(
        cls, repo_root: Path, metadata: dict[str, JSONValue]
    ) -> PathPolicy:
        """Build a policy from the Case's optional ``allowed_paths`` list.

        The list is read from ``allowed_paths`` or ``policy.allowed_paths``.
        When neither exists, the released Case layout limits edits to
        ``input/``; any other Case gets no extra restriction, since none was
        published.

        Args:
            repo_root: The worktree root, ``work/repo``.
            metadata: The Case metadata.

        Returns:
            The policy for this Case.

        Raises:
            PathPolicyError: If ``allowed_paths`` is not a non-empty list of
                strings, or an entry is absolute or contains ``..``.
        """
        value: JSONValue | None = metadata.get("allowed_paths")
        policy = metadata.get("policy")
        if value is None and isinstance(policy, dict):
            value = policy.get("allowed_paths")
        if value is None:
            if _official_case_uses_input_prefix(repo_root):
                return cls(repo_root, ("input",))
            return cls(repo_root)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise PathPolicyError(
                "declared allowed_paths must be a non-empty string array"
            )
        patterns: list[str] = []
        for item in value:
            assert isinstance(item, str)
            normalized = item.strip().replace("\\", "/")
            pure = PurePosixPath(normalized)
            if pure.is_absolute() or any(part in {"", ".."} for part in pure.parts):
                raise PathPolicyError(f"invalid declared allowed path: {item!r}")
            patterns.append(normalized)
        return cls(repo_root, tuple(patterns))

    def _is_declared_allowed(self, path: PurePosixPath) -> bool:
        """Check a path against the Case's allowed patterns.

        A pattern without glob characters allows that path and everything
        below it. A pattern with ``*``, ``?`` or ``[`` is matched with
        ``PurePosixPath.match``. ``.``, ``*``, ``**`` and ``**/*`` allow
        everything.

        Args:
            path: Repository-relative path to check.

        Returns:
            True if no patterns are declared or any pattern allows the path.
        """
        if self.allowed_patterns is None:
            return True
        relative = path.as_posix()
        for pattern in self.allowed_patterns:
            if pattern in {".", "*", "**", "**/*"}:
                return True
            prefix = pattern.rstrip("/")
            if not any(character in pattern for character in "*?["):
                if relative == prefix or relative.startswith(prefix + "/"):
                    return True
            elif path.match(pattern):
                return True
        return False

    def validate_relative(self, proposed: str, *, creating: bool = False) -> Path:
        """Check a proposed repository-relative path and return where it points.

        Paths come from rules and from the model, so each one is checked as
        untrusted input before any file is read or written.

        Args:
            proposed: Repository-relative path; backslashes are treated as
                ``/``.
            creating: Whether the edit creates the file, in which case the file
                may not exist yet.

        Returns:
            The absolute path inside the worktree.

        Raises:
            PathPolicyError: If the path is empty, absolute, longer than 4,096
                bytes, has a component over 255 bytes or a control character,
                uses ``.`` or ``..``, names a VCS or cache directory, a ``.env``
                file or anything containing ``evaluator`` or ``hidden``, lies
                outside the Case's allowed paths, escapes the worktree, is a
                symlink or a directory, or does not exist when not ``creating``.
        """
        normalized = proposed.replace("\\", "/")
        encoded = normalized.encode("utf-8", errors="surrogatepass")
        if len(encoded) > 4096 or any(
            ord(character) < 32 or ord(character) == 127 for character in normalized
        ):
            raise PathPolicyError(
                f"oversized or control-bearing path rejected: {proposed!r}"
            )
        pure = PurePosixPath(normalized)
        if any(
            len(part.encode("utf-8", errors="surrogatepass")) > 255
            for part in pure.parts
        ):
            raise PathPolicyError(f"oversized path component rejected: {proposed!r}")
        if not normalized or pure.is_absolute() or Path(proposed).is_absolute():
            raise PathPolicyError(f"absolute or empty path rejected: {proposed!r}")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise PathPolicyError(f"path traversal rejected: {proposed!r}")
        if any(part.lower() in _FORBIDDEN_PARTS for part in pure.parts):
            raise PathPolicyError(f"protected path rejected: {proposed!r}")
        if any(
            part.lower() == ".env" or part.lower().startswith(".env.")
            for part in pure.parts
        ):
            raise PathPolicyError(
                f"secret-bearing environment path rejected: {proposed!r}"
            )
        if any(
            "evaluator" in part.lower() or "hidden" in part.lower()
            for part in pure.parts
        ):
            raise PathPolicyError(f"evaluator-like path rejected: {proposed!r}")
        if not self._is_declared_allowed(pure):
            raise PathPolicyError(
                f"path is outside the Case allowed_paths: {proposed!r}"
            )

        try:
            root = self.repo_root.resolve(strict=True)
            candidate = root.joinpath(*pure.parts)
            parent = candidate.parent.resolve(strict=False)
        except OSError as exc:
            raise PathPolicyError(
                f"path cannot be resolved safely: {proposed!r}"
            ) from exc
        if os.path.commonpath((str(root), str(parent))) != str(root):
            raise PathPolicyError(f"path escapes repository: {proposed!r}")
        try:
            exists = candidate.exists()
            is_symlink = candidate.is_symlink()
        except OSError as exc:
            raise PathPolicyError(
                f"path cannot be inspected safely: {proposed!r}"
            ) from exc
        if is_symlink:
            raise PathPolicyError(f"symbolic-link edits are not allowed: {proposed!r}")
        if exists:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise PathPolicyError(
                    f"path cannot be resolved safely: {proposed!r}"
                ) from exc
            if os.path.commonpath((str(root), str(resolved))) != str(root):
                raise PathPolicyError(
                    f"symlink target escapes repository: {proposed!r}"
                )
            if resolved.is_dir():
                raise PathPolicyError(f"directory edits are not allowed: {proposed!r}")
        elif not creating:
            raise PathPolicyError(f"target file does not exist: {proposed!r}")
        return candidate

    def relative_path(self, path: Path) -> str:
        """Express an absolute path relative to the worktree, with ``/`` separators.

        Args:
            path: A path inside the worktree.

        Returns:
            The POSIX-style relative path.

        Raises:
            PathPolicyError: If the path resolves outside the worktree.
        """
        try:
            return (
                path.resolve(strict=False)
                .relative_to(self.repo_root.resolve())
                .as_posix()
            )
        except ValueError as exc:
            raise PathPolicyError(f"path is outside repository: {path}") from exc
