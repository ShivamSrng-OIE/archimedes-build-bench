"""The official workspace layout.

It has the read-only ``input/``, the writable ``work/repo/`` worktree and
``output/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import WorkspaceError


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Resolved locations of the workspace directories.

    Attributes:
        root: The resolved workspace root.
        input: Read-only Case metadata and failure evidence.
        work: Parent of the worktree.
        repo: The writable package worktree.
        output: Where the result and diagnostics are written.
        probe_temp: Scratch directory for probes, ``output/probes``.
    """

    root: Path
    input: Path
    work: Path
    repo: Path
    output: Path
    probe_temp: Path

    @classmethod
    def resolve(cls, root: Path, *, create_output: bool = True) -> WorkspacePaths:
        """Resolve the workspace and check that its directories stay inside the root.

        ``input/`` and ``work/repo/`` must already exist; only ``output/`` and
        ``output/probes`` may be created here.

        Args:
            root: The workspace root, usually from ``BB_WORKSPACE``.
            create_output: Whether to create and check the output directories.

        Returns:
            The resolved paths.

        Raises:
            WorkspaceError: If the root or a required directory is missing or
                unreadable, a directory resolves outside the root, an output
                path is a symlink, or the output directories cannot be created.
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
