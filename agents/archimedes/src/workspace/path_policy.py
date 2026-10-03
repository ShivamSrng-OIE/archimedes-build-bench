"""Fail-closed policy for all proposed package-tree modifications."""

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
    """Recognize the public Case wrapper without trusting arbitrary metadata."""
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
        """Return an audited POSIX-style path relative to the package repository."""
        try:
            return (
                path.resolve(strict=False)
                .relative_to(self.repo_root.resolve())
                .as_posix()
            )
        except ValueError as exc:
            raise PathPolicyError(f"path is outside repository: {path}") from exc
