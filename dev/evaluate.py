"""Run the synthetic cases in dev/synthetic_cases under one Archimedes configuration.

The harness runs the Agent on each case in a temporary workspace and records whether it edited
the expected file and chose the expected repair family. It never runs package builds, so it says
nothing about build success; only the organizer's Validator can confirm that.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
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

_SENSITIVE_OUTPUT = re.compile(
    r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_-]?key\s*[:=]\s*|"
    r"password\s*[:=]\s*|token\s*[:=]\s*)[^\s,;]+"
)


def safe_error_summary(stderr: str, *, max_characters: int = 3000) -> str | None:
    """Return the end of the Agent's stderr with credential values masked.

    Values after `Authorization:`, `api_key=`, `password=` and `token=` style prefixes are
    replaced with `[REDACTED]` before trimming, so result tables and artifact folders never
    hold a secret that the Agent happened to print.

    Args:
        stderr: The Agent process's captured standard error.
        max_characters: How many characters to keep from the end of the redacted text.

    Returns:
        The last `max_characters` characters of the redacted text, or None when `stderr`
        is empty or only whitespace.
    """
    if not stderr.strip():
        return None
    redacted = _SENSITIVE_OUTPUT.sub(lambda match: f"{match.group(1)}[REDACTED]", stderr)
    return redacted[-max_characters:]


def git_commit() -> str:
    """Return the commit hash checked out in this repository.

    Returns:
        The output of `git rev-parse HEAD`, or "uncommitted" when git fails (for example,
        outside a Git checkout).
    """
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "uncommitted"


def load_configuration(name: str) -> dict[str, str]:
    """Load the environment overrides for one configuration from dev/configurations.json.

    Args:
        name: The configuration letter, "A" to "K".

    Returns:
        The configuration's environment variables as strings, without its "description" entry.

    Raises:
        SystemExit: If `name` is not defined in the configuration file.
    """
    all_configs = json.loads(CONFIGURATIONS.read_text(encoding="utf-8"))
    if name not in all_configs:
        raise SystemExit(f"unknown configuration {name!r}; choose one of {', '.join(all_configs)}")
    return {key: str(value) for key, value in all_configs[name].items() if key != "description"}


def select_cases(names: list[str]) -> list[Path]:
    """Pick the synthetic case folders to run.

    A folder under dev/synthetic_cases counts as a case only if it contains a case.json file.

    Args:
        names: Case folder names to run, in the order given. An empty list means all cases.

    Returns:
        The case folders, in the order of `names`, or sorted by name when `names` is empty.

    Raises:
        SystemExit: If any name does not match a case folder.
    """
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
    artifact_dir: Path | None = None,
    case_timeout_seconds: float = 120,
) -> dict[str, object]:
    """Run the Agent on one synthetic case and return its result row.

    The case's repo/, case.json and failure.log are copied into a fresh temporary workspace
    laid out like the competition one (input/, work/repo/, output/). The Agent is started as
    `python -m src.main` from agents/archimedes with `BB_WORKSPACE` pointing at it. The
    environment is built from the current process, then FEATURE_DEFAULTS, then `config_env`,
    so the configuration always wins. The workspace is deleted afterwards unless
    `artifact_dir` is set.

    Args:
        case_dir: The synthetic case folder.
        configuration: The configuration letter, recorded in the row and in file names.
        config_env: Environment overrides from `load_configuration`.
        seed: Run label recorded with each result row; the Agent itself is deterministic.
        trace_dir: If set, output/execution-trace.jsonl is copied here as
            `<case_id>-<configuration>.jsonl`.
        artifact_dir: If set, the whole workspace is copied here as
            `<case_id>-<configuration>`, with a numeric suffix if that folder already
            exists, plus agent-error-summary.log when the Agent wrote to stderr.
        case_timeout_seconds: Wall-clock limit for the Agent process, in seconds.

    Returns:
        One result row: the Agent's metrics.json fields, plus case metadata, the model
        settings, the exit code, whether the expected causal file was modified and the
        expected repair family selected, the feature flags, the Git commit, a timestamp,
        the case.json SHA-256 and any saved trace or artifact paths.
        `verified_build_success` is always None.

    Raises:
        subprocess.TimeoutExpired: If the Agent runs longer than `case_timeout_seconds`.
    """
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
        completed = subprocess.run(
            [sys.executable, "-m", "src.main"], cwd=AGENT, env=environment,
            capture_output=True,
            text=True,
            timeout=case_timeout_seconds,
            check=False,
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
        persisted_artifacts: str | None = None
        if artifact_dir is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            base = artifact_dir / f"{metadata['case_id']}-{configuration}"
            destination = base
            suffix = 1
            while destination.exists():
                suffix += 1
                destination = artifact_dir / f"{base.name}-{suffix}"
            shutil.copytree(workspace, destination)
            summary = safe_error_summary(completed.stderr)
            if summary:
                (destination / "agent-error-summary.log").write_text(
                    summary, encoding="utf-8"
                )
            persisted_artifacts = str(destination)
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
            "agent_error_summary": (
                safe_error_summary(completed.stderr) if completed.returncode else None
            ),
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
            "timestamp": datetime.now(UTC).isoformat(),
            "seed": seed,
            "case_content_sha256": hashlib.sha256((case_dir / "case.json").read_bytes()).hexdigest(),
            "trace_path": persisted_trace,
            "artifact_path": persisted_artifacts,
        }


def write_results(path: Path, rows: list[dict[str, object]]) -> None:
    """Write result rows as CSV or JSON Lines, chosen by the file extension.

    A `.csv` path gets one column per key seen in any row, sorted by name. Any other
    extension gets one JSON object per line with sorted keys. Parent folders are created.

    Args:
        path: The output file. It is overwritten if it exists.
        rows: The result rows from `run_case`.

    Returns:
        None.
    """
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
    """Run the selected synthetic cases and write one result row per case.

    Command-line options:
        --configuration: Configuration letter from dev/configurations.json, A to K.
            Default "K" (full Archimedes with whatever model backend the caller set).
        --case: Synthetic case folder name. Repeat it to run several cases; omit it to
            run every case.
        --output: Result file. A `.csv` extension writes CSV, anything else writes JSON
            Lines. Default dev/results.jsonl.
        --seed: Run label recorded with each result row. Default 0.
        --trace-dir: Folder to copy each case's execution trace into.
        --artifact-dir: Folder to keep each case's whole workspace in, for inspection.
        --case-timeout-seconds: Time limit for each Agent run. Default 120; raise it
            for slow local models.

    Returns:
        0 once the results file is written.

    Raises:
        SystemExit: If the configuration or a case name is unknown.
        subprocess.TimeoutExpired: If any Agent run exceeds the time limit.
    """
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
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="retain each development workspace for source/diagnostic inspection",
    )
    parser.add_argument(
        "--case-timeout-seconds",
        type=float,
        default=120,
        help="per-case agent-process timeout (increase for slow local models)",
    )
    args = parser.parse_args()
    config_env = load_configuration(args.configuration)
    rows = [
        run_case(
            path,
            args.configuration,
            config_env,
            args.seed,
            args.trace_dir,
            args.artifact_dir,
            args.case_timeout_seconds,
        )
        for path in select_cases(args.cases)
    ]
    write_results(args.output, rows)
    print(f"wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
