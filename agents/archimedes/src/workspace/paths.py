"""Centralized representation of the competition workspace contract."""

from __future__ import annotations

from dataclasses import dataclass
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
        resolved = root.expanduser().resolve(strict=False)
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
        if create_output:
            paths.output.mkdir(parents=True, exist_ok=True)
            paths.probe_temp.mkdir(parents=True, exist_ok=True)
        return paths
