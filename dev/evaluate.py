"""Run trusted synthetic cases under one controlled Archimedes configuration.

This development harness does not invoke arbitrary package build scripts. It measures agent
orchestration and synthetic causal/repair-family agreement. Official verified build success must
come from the future official validator and remains the primary competition metric.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agents" / "archimedes"
CASES = ROOT / "dev" / "synthetic_cases"
CONFIGURATIONS = ROOT / "dev" / "configurations.json"
STARTER_KIT_VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
FEATURE_DEFAULTS = {
    "ENABLE_DETERMINISTIC_FORENSICS": "true",
    "ENABLE_CLAIM_CONFIDENCE": "true",
    "ENABLE_ARCHITECTURE_DISCOVERY": "true",
    "ENABLE_DELTA_GRAPH": "true",
    "ENABLE_SEMANTIC_ANCHORS": "true",
    "ENABLE_ACTIVE_PROBES": "true",
    "ENABLE_REPAIR_RULES": "true",
    "ENABLE_FAILURE_FRONTIER": "true",
    "ARCHIMEDES_CONTEXT_MODE": "archimedes_selected_context",
}


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "uncommitted"


def load_configuration(name: str) -> dict[str, str]:
    all_configs = json.loads(CONFIGURATIONS.read_text(encoding="utf-8"))
    if name not in all_configs:
        raise SystemExit(f"unknown configuration {name!r}; choose one of {', '.join(all_configs)}")
    return {key: str(value) for key, value in all_configs[name].items() if key != "description"}


def select_cases(names: list[str]) -> list[Path]:
    available = {path.name: path for path in CASES.iterdir() if (path / "case.json").is_file()}
    if not names:
        return [available[name] for name in sorted(available)]
    missing = sorted(set(names) - available.keys())
    if missing:
        raise SystemExit(f"unknown synthetic cases: {', '.join(missing)}")
    return [available[name] for name in names]


def run_case(
    case_dir: Path,
    configuration: str,
    config_env: dict[str, str],
    seed: int,
    trace_dir: Path | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="archimedes-eval-") as temp_name:
        workspace = Path(temp_name)
        input_dir = workspace / "input"
        repo_dir = workspace / "work" / "repo"
        output_dir = workspace / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir()
        shutil.copytree(case_dir / "repo", repo_dir)
        shutil.copy2(case_dir / "case.json", input_dir / "case.json")
        shutil.copy2(case_dir / "failure.log", input_dir / "failure.log")
        metadata = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))

        environment = os.environ.copy()
        environment.update(FEATURE_DEFAULTS)
        environment.update(config_env)
        environment["BB_WORKSPACE"] = str(workspace)
        environment["ARCHIMEDES_RANDOM_SEED"] = str(seed)
        completed = subprocess.run(
            [sys.executable, "-m", "src.main"], cwd=AGENT, env=environment,
            capture_output=True, text=True, timeout=120, check=False,
        )
        metrics_path = output_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        result_path = output_dir / "agent-result.json"
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
        trace_path = output_dir / "execution-trace.jsonl"
        persisted_trace: str | None = None
        if trace_dir is not None and trace_path.is_file():
            trace_dir.mkdir(parents=True, exist_ok=True)
            destination = trace_dir / f"{metadata['case_id']}-{configuration}.jsonl"
            shutil.copy2(trace_path, destination)
            persisted_trace = str(destination)
        modified = result.get("modified_paths", [])
        expected_file = metadata["expected_causal_file"]
        tier_mode = environment.get("ARCHIMEDES_MODEL_TIER", "auto")
        if tier_mode == "strong":
            model_label = environment.get("ARCHIMEDES_STRONG_MODEL", "mock-strong")
        elif tier_mode == "fast":
            model_label = environment.get("ARCHIMEDES_FAST_MODEL", "mock-fast")
        else:
            model_label = (
                f"fast={environment.get('ARCHIMEDES_FAST_MODEL', 'mock-fast')};"
                f"strong={environment.get('ARCHIMEDES_STRONG_MODEL', 'mock-strong')}"
            )
        return {
            **metrics,
            "case_id": metadata["case_id"],
            "configuration": configuration,
            "model": model_label,
            "model_backend": environment.get("ARCHIMEDES_MODEL_BACKEND", "mock"),
            "model_tier": tier_mode,
            "source_arch": metadata["source_architecture"],
            "target_arch": metadata["target_architecture"],
            "failure_family": metadata["failure_family"],
            "agent_exit_code": completed.returncode,
            "expected_causal_mechanism_identified": expected_file in modified,
            "expected_repair_family_selected": metrics.get("selected_repair_family") == metadata["expected_repair_family"],
            "verified_build_success": None,
            "git_commit": git_commit(),
            "starter_kit_version": STARTER_KIT_VERSION,
            "temperature": 0,
            "feature_flags": json.dumps(
                {
                    key: environment[key]
                    for key in sorted(FEATURE_DEFAULTS)
                    if key.startswith("ENABLE_") or key == "ARCHIMEDES_CONTEXT_MODE"
                },
                sort_keys=True,
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "case_content_sha256": hashlib.sha256((case_dir / "case.json").read_bytes()).hexdigest(),
            "trace_path": persisted_trace,
        }


def write_results(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        fields = sorted({key for row in rows for key in row})
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", default="K", choices=list("ABCDEFGHIJK"))
    parser.add_argument("--case", action="append", default=[], dest="cases")
    parser.add_argument("--output", type=Path, default=ROOT / "dev" / "results.jsonl")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--trace-dir",
        type=Path,
        help="copy each local execution trace to this development-only directory",
    )
    args = parser.parse_args()
    config_env = load_configuration(args.configuration)
    rows = [
        run_case(path, args.configuration, config_env, args.seed, args.trace_dir)
        for path in select_cases(args.cases)
    ]
    write_results(args.output, rows)
    print(f"wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
