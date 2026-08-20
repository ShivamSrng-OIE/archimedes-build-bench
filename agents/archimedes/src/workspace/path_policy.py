"""Fail-closed policy for all proposed package-tree modifications."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..errors import PathPolicyError
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


@dataclass(frozen=True, slots=True)
class PathPolicy:
    repo_root: Path
    allowed_patterns: tuple[str, ...] | None = None

    @classmethod
    def from_metadata(
        cls, repo_root: Path, metadata: dict[str, JSONValue]
    ) -> "PathPolicy":
        """Honor an optional published allowed_paths list without inventing it when absent."""
        value: JSONValue | None = metadata.get("allowed_paths")
        policy = metadata.get("policy")
        if value is None and isinstance(policy, dict):
            value = policy.get("allowed_paths")
        if value is None:
            return cls(repo_root)
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise PathPolicyError("declared allowed_paths must be a non-empty string array")
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
        """Resolve a model-proposed relative path beneath work/repo.

        Raises:
            PathPolicyError: If the path is absolute, traverses, targets protected
                metadata, leaves the repository through a symlink, or is a directory.
        """
        normalized = proposed.replace("\\", "/")
        pure = PurePosixPath(normalized)
        if not normalized or pure.is_absolute() or Path(proposed).is_absolute():
            raise PathPolicyError(f"absolute or empty path rejected: {proposed!r}")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise PathPolicyError(f"path traversal rejected: {proposed!r}")
        if any(part.lower() in _FORBIDDEN_PARTS for part in pure.parts):
            raise PathPolicyError(f"protected path rejected: {proposed!r}")
        if any(part.lower() == ".env" or part.lower().startswith(".env.") for part in pure.parts):
            raise PathPolicyError(f"secret-bearing environment path rejected: {proposed!r}")
        if any("evaluator" in part.lower() or "hidden" in part.lower() for part in pure.parts):
            raise PathPolicyError(f"evaluator-like path rejected: {proposed!r}")
        if not self._is_declared_allowed(pure):
            raise PathPolicyError(f"path is outside the Case allowed_paths: {proposed!r}")

        root = self.repo_root.resolve(strict=True)
        candidate = root.joinpath(*pure.parts)
        parent = candidate.parent.resolve(strict=False)
        if os.path.commonpath((str(root), str(parent))) != str(root):
            raise PathPolicyError(f"path escapes repository: {proposed!r}")
        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            if os.path.commonpath((str(root), str(resolved))) != str(root):
                raise PathPolicyError(f"symlink target escapes repository: {proposed!r}")
            if resolved.is_dir():
                raise PathPolicyError(f"directory edits are not allowed: {proposed!r}")
        elif not creating:
            raise PathPolicyError(f"target file does not exist: {proposed!r}")
        return candidate

    def relative_path(self, path: Path) -> str:
        """Return an audited POSIX-style path relative to the package repository."""
        try:
            return path.resolve(strict=False).relative_to(self.repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise PathPolicyError(f"path is outside repository: {path}") from exc
