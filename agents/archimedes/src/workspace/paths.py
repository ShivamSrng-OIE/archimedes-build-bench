"""Centralized representation of the competition workspace contract."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from ..errors import WorkspaceError


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    input: Path
    work: Path
    repo: Path
    output: Path
    probe_temp: Path

    @classmethod
    def resolve(cls, root: Path, *, create_output: bool = True) -> "WorkspacePaths":
        """Resolve workspace paths and validate the immutable/mutable boundaries.

        The input and work/repo directories must already exist. Only output and its
        probe subdirectory may be created by this abstraction.
        """
        try:
            resolved = root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError(f"workspace root is unavailable: {root}") from exc
        paths = cls(
            root=resolved,
            input=resolved / "input",
            work=resolved / "work",
            repo=resolved / "work" / "repo",
            output=resolved / "output",
            probe_temp=resolved / "output" / "probes",
        )
        if not paths.input.is_dir():
            raise WorkspaceError(f"workspace input directory is missing: {paths.input}")
        if not paths.repo.is_dir():
            raise WorkspaceError(f"workspace repository is missing: {paths.repo}")
        for label, path in (("input", paths.input), ("repository", paths.repo)):
            try:
                target = path.resolve(strict=True)
                if os.path.commonpath((str(resolved), str(target))) != str(resolved):
                    raise WorkspaceError(f"workspace {label} escapes the workspace root: {path}")
            except OSError as exc:
                raise WorkspaceError(f"workspace {label} is unavailable: {path}") from exc
        if create_output:
            if paths.output.is_symlink() or paths.probe_temp.is_symlink():
                raise WorkspaceError("workspace output paths must not be symbolic links")
            try:
                paths.output.mkdir(parents=True, exist_ok=True)
                paths.probe_temp.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise WorkspaceError(f"workspace output is unavailable: {paths.output}") from exc
            for label, path in (("output", paths.output), ("probe output", paths.probe_temp)):
                try:
                    target = path.resolve(strict=True)
                    if os.path.commonpath((str(resolved), str(target))) != str(resolved):
                        raise WorkspaceError(
                            f"workspace {label} escapes the workspace root: {path}"
                        )
                except OSError as exc:
                    raise WorkspaceError(f"workspace {label} is unavailable: {path}") from exc
        return paths
