from __future__ import annotations

from pathlib import Path

from . import AGENT

from src.config import ContextMode, FeatureFlags, ModelBackend, RuntimeConfig

__all__ = ["AGENT", "make_workspace", "runtime_config"]


def runtime_config(workspace: Path, *, features: FeatureFlags | None = None) -> RuntimeConfig:
    return RuntimeConfig(
        workspace_root=workspace,
        model_backend=ModelBackend.MOCK,
        model_base_url="http://127.0.0.1:11434/v1",
        model_api_key=None,
        fast_model="mock-fast",
        strong_model="mock-strong",
        model_timeout_seconds=5.0,
        max_total_model_calls=10,
        max_strong_calls=3,
        max_total_tokens=20_000,
        max_estimated_cost_usd=None,
        input_cost_per_million=None,
        output_cost_per_million=None,
        max_repair_attempts=3,
        enable_external_knowledge=False,
        log_level="WARNING",
        debug=False,
        trace_enabled=True,
        trace_max_events=500,
        context_mode=ContextMode.ARCHIMEDES_SELECTED_CONTEXT,
        random_seed=0,
        features=features or FeatureFlags(),
    )


def make_workspace(root: Path, *, metadata: str = "{}", log: str = "") -> tuple[Path, Path]:
    input_dir = root / "input"
    repo = root / "work" / "repo"
    output = root / "output"
    input_dir.mkdir(parents=True)
    repo.mkdir(parents=True)
    output.mkdir()
    (input_dir / "case.json").write_text(metadata, encoding="utf-8")
    (input_dir / "failure.log").write_text(log, encoding="utf-8")
    return repo, output
