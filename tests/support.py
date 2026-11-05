"""Shared helpers for building test workspaces and runtime settings.

``runtime_config`` returns settings that use the mock model backend and never touch the
network, and ``make_workspace`` lays out the same input, worktree and output folders that
the Build-Bench runner provides under ``/workspace``.
"""

from __future__ import annotations

from pathlib import Path

from src.config import (
    ContextMode,
    FeatureFlags,
    ModelBackend,
    ModelTierMode,
    RuntimeConfig,
)

from . import AGENT

__all__ = ["AGENT", "make_workspace", "runtime_config"]


def runtime_config(workspace: Path, *, features: FeatureFlags | None = None) -> RuntimeConfig:
    """Build a runtime configuration that is safe for offline tests.

    The settings use the mock model backend, small call and token budgets, a fixed random
    seed and tracing turned on, so test runs are repeatable and need no credentials.

    Args:
        workspace: Root folder of the test workspace, used as ``workspace_root``.
        features: Feature flags to use. ``None`` means the default ``FeatureFlags()``,
            with every feature turned on.

    Returns:
        A ``RuntimeConfig`` ready to pass to ``RepairController``.
    """
    return RuntimeConfig(
        workspace_root=workspace,
        model_backend=ModelBackend.MOCK,
        model_base_url="http://127.0.0.1:11434/v1",
        model_api_key=None,
        fast_model="mock-fast",
        strong_model="mock-strong",
        model_tier_mode=ModelTierMode.AUTO,
        model_timeout_seconds=5.0,
        max_total_model_calls=10,
        max_strong_calls=3,
        max_total_tokens=20_000,
        max_estimated_cost_usd=None,
        input_cost_per_million=None,
        output_cost_per_million=None,
        max_repair_attempts=3,
        log_level="WARNING",
        trace_enabled=True,
        trace_max_events=500,
        context_mode=ContextMode.ARCHIMEDES_SELECTED_CONTEXT,
        features=features or FeatureFlags(),
    )


def make_workspace(root: Path, *, metadata: str = "{}", log: str = "") -> tuple[Path, Path]:
    """Create the Build-Bench workspace layout under a temporary folder.

    Args:
        root: Folder to build the workspace in. It must exist, and its ``input``,
            ``work/repo`` and ``output`` subfolders must not.
        metadata: Text written to ``input/case.json``. Defaults to an empty JSON object.
        log: Text written to ``input/failure.log``. Defaults to an empty log.

    Returns:
        A ``(repo, output)`` pair: the writable package worktree at ``work/repo`` and the
        ``output`` folder where the agent writes its results.

    Raises:
        FileExistsError: If one of the workspace subfolders already exists under ``root``.
    """
    input_dir = root / "input"
    repo = root / "work" / "repo"
    output = root / "output"
    input_dir.mkdir(parents=True)
    repo.mkdir(parents=True)
    output.mkdir()
    (input_dir / "case.json").write_text(metadata, encoding="utf-8")
    (input_dir / "failure.log").write_text(log, encoding="utf-8")
    return repo, output
