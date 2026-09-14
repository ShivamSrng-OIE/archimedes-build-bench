"""Replay Archimedes over released Build-Bench Development Cases.

This utility does not build packages.  It prepares the public Case workspace
contract, invokes the Repair Agent, and preserves each complete final worktree
for upload to the organizer's Development Validation service.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class ReplayResult:
    case_id: str
    direction: str
    status: str
    agent_exit_code: int | None
    elapsed_seconds: float
    modified_paths: tuple[str, ...]
    error: str | None = None


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive or len(drive) != 1:
        raise ValueError(f"cannot translate path to WSL: {resolved}")
    relative = resolved.as_posix()[len(resolved.drive) :].lstrip("/")
    return f"/mnt/{drive}/{relative}"


def _extract_debian_source(source: Path, repo_dir: Path, dpkg_source_root: Path) -> None:
    descriptors = sorted(source.glob("*.dsc"))
    if len(descriptors) != 1:
        raise ValueError(f"expected exactly one .dsc under {source}; found {len(descriptors)}")
    perl_modules = dpkg_source_root / "usr" / "share" / "perl5"
    dpkg_source = dpkg_source_root / "usr" / "bin" / "dpkg-source"
    helper_bin = dpkg_source_root / "usr" / "bin"
    helper_lib = dpkg_source_root / "usr" / "lib" / "x86_64-linux-gnu"
    if not perl_modules.is_dir() or not dpkg_source.is_file():
        raise ValueError(f"extracted dpkg-source runtime is incomplete: {dpkg_source_root}")
    completed = subprocess.run(
        [
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--exec",
            "env",
            f"PERL5LIB={_wsl_path(perl_modules)}",
            f"PATH={_wsl_path(helper_bin)}:/usr/bin:/bin",
            f"LD_LIBRARY_PATH={_wsl_path(helper_lib)}",
            "perl",
            _wsl_path(dpkg_source),
            "-x",
            _wsl_path(descriptors[0]),
            _wsl_path(repo_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stdout.strip()[-2000:]
        raise RuntimeError(f"dpkg-source exited with {completed.returncode}: {detail}")


def discover_cases(dataset_root: Path) -> dict[str, Path]:
    cases_root = dataset_root / "cases"
    discovered: dict[str, Path] = {}
    for case_json in sorted(cases_root.glob("*/*/case.json")):
        metadata = _json_object(case_json)
        case_id = metadata.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"missing case_id: {case_json}")
        if case_id in discovered:
            raise ValueError(f"duplicate case_id: {case_id}")
        discovered[case_id] = case_json.parent
    if not discovered:
        raise ValueError(f"no Development Cases found under {cases_root}")
    return discovered


def _prepare_workspace(case_dir: Path, destination: Path, dpkg_source_root: Path) -> None:
    metadata = _json_object(case_dir / "case.json")
    paths = metadata.get("paths")
    if not isinstance(paths, dict):
        raise ValueError(f"missing paths object: {case_dir / 'case.json'}")
    source_rel = paths.get("source_input")
    log_rel = paths.get("target_failure_log")
    if not isinstance(source_rel, str) or not isinstance(log_rel, str):
        raise ValueError(f"invalid source/log paths: {case_dir / 'case.json'}")

    source = (case_dir / source_rel).resolve()
    failure_log = (case_dir / log_rel).resolve()
    case_root = case_dir.resolve()
    if case_root not in source.parents or case_root not in failure_log.parents:
        raise ValueError(f"Case path escapes its directory: {case_dir}")
    if not source.is_dir() or not failure_log.is_file():
        raise ValueError(f"Case source or failure log is missing: {case_dir}")

    input_dir = destination / "input"
    output_dir = destination / "output"
    repo_dir = destination / "work" / "repo"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    repo_dir.parent.mkdir()
    if list(source.glob("*.dsc")):
        _extract_debian_source(source, repo_dir, dpkg_source_root)
    else:
        shutil.copytree(source, repo_dir)
    shutil.copy2(case_dir / "case.json", input_dir / "case.json")
    target_log = input_dir / log_rel
    target_log.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(failure_log, target_log)


def replay_case(
    *,
    case_dir: Path,
    output_root: Path,
    agent_dir: Path,
    python: Path,
    dpkg_source_root: Path,
    timeout_seconds: float,
) -> ReplayResult:
    metadata = _json_object(case_dir / "case.json")
    case_id = str(metadata["case_id"])
    platform = metadata.get("platform")
    direction = str(platform.get("direction", "unknown")) if isinstance(platform, dict) else "unknown"
    destination = output_root / "cases" / case_id
    started = time.monotonic()

    try:
        _prepare_workspace(case_dir, destination, dpkg_source_root)
        environment = os.environ.copy()
        environment.update(
            {
                "BB_WORKSPACE": str(destination),
                "PYTHONPATH": str(agent_dir),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        completed = subprocess.run(
            [str(python), "-m", "src.main"],
            cwd=agent_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        (destination / "agent-console.log").write_text(completed.stdout, encoding="utf-8")
        result_path = destination / "output" / "agent-result.json"
        if completed.returncode != 0:
            return ReplayResult(
                case_id,
                direction,
                "agent_error",
                completed.returncode,
                round(time.monotonic() - started, 3),
                (),
                f"agent exited with code {completed.returncode}",
            )
        if not result_path.is_file():
            return ReplayResult(
                case_id,
                direction,
                "agent_error",
                completed.returncode,
                round(time.monotonic() - started, 3),
                (),
                "agent-result.json is missing",
            )
        result = _json_object(result_path)
        modified = result.get("modified_paths", [])
        modified_paths = tuple(str(item) for item in modified) if isinstance(modified, list) else ()
        status = str(result.get("status", "unknown"))
        return ReplayResult(
            case_id,
            direction,
            status,
            completed.returncode,
            round(time.monotonic() - started, 3),
            modified_paths,
        )
    except subprocess.TimeoutExpired:
        return ReplayResult(
            case_id,
            direction,
            "timeout",
            None,
            round(time.monotonic() - started, 3),
            (),
            f"agent exceeded {timeout_seconds:g} seconds",
        )
    except Exception as error:  # Per-Case isolation is deliberate here.
        return ReplayResult(
            case_id,
            direction,
            "preparation_error",
            None,
            round(time.monotonic() - started, 3),
            (),
            f"{type(error).__name__}: {error}",
        )


def write_summary(
    output_root: Path,
    *,
    dataset_root: Path,
    agent_dir: Path,
    python: Path,
    results: Sequence[ReplayResult],
) -> None:
    ordered = sorted(results, key=lambda item: item.case_id)
    summary = {
        "schema_version": "archimedes.development-replay.v0.1",
        "dataset": {
            "path": str(dataset_root),
            "manifest_sha256": _sha256(dataset_root / "manifest.json"),
        },
        "agent": {
            "path": str(agent_dir),
            "manifest_sha256": _sha256(agent_dir / "agent.yaml"),
        },
        "python": str(python),
        "case_count": len(ordered),
        "completed_count": sum(item.status == "completed" for item in ordered),
        "modified_case_count": sum(bool(item.modified_paths) for item in ordered),
        "results": [asdict(item) for item in ordered],
    }
    (output_root / "replay-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--agent", type=Path, default=Path("agents/archimedes"))
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--dpkg-source-root",
        type=Path,
        default=Path("runs/tools/dpkg-dev/root"),
        help="Root of locally extracted dpkg-dev and libdpkg-perl packages",
    )
    parser.add_argument("--case", action="append", default=[], dest="case_ids")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    dataset_root = args.dataset_root.resolve()
    output_root = args.output_root.resolve()
    agent_dir = args.agent.resolve()
    python = args.python.resolve()
    dpkg_source_root = args.dpkg_source_root.resolve()
    if output_root.exists():
        raise SystemExit(f"output already exists: {output_root}")
    if args.jobs < 1:
        raise SystemExit("--jobs must be positive")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if not python.is_file():
        raise SystemExit(f"Python executable not found: {python}")

    discovered = discover_cases(dataset_root)
    selected_ids = args.case_ids or sorted(discovered)
    missing = sorted(set(selected_ids) - set(discovered))
    if missing:
        raise SystemExit(f"unknown Case IDs: {', '.join(missing)}")
    output_root.mkdir(parents=True)

    results: list[ReplayResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(
                replay_case,
                case_dir=discovered[case_id],
                output_root=output_root,
                agent_dir=agent_dir,
                python=python,
                dpkg_source_root=dpkg_source_root,
                timeout_seconds=args.timeout_seconds,
            ): case_id
            for case_id in selected_ids
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"{result.case_id}: {result.status}; "
                f"modified={len(result.modified_paths)}; elapsed={result.elapsed_seconds:.3f}s",
                flush=True,
            )

    write_summary(
        output_root,
        dataset_root=dataset_root,
        agent_dir=agent_dir,
        python=python,
        results=results,
    )
    failures = [result for result in results if result.status != "completed"]
    print(
        f"Completed {len(results) - len(failures)}/{len(results)} Cases; "
        f"modified {sum(bool(result.modified_paths) for result in results)} worktrees."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
