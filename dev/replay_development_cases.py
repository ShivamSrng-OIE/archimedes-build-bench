"""Run Archimedes over the released Build-Bench Development Cases and keep each final worktree.

For each Case this builds the competition workspace (input/, work/repo/, output/), unpacks the
source, runs the Agent and records which files it changed. It does not build packages; the kept
worktrees can be bundled for the organizer's Development Validation service.
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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReplayResult:
    """The outcome of replaying one Case.

    Attributes:
        case_id: The Case's `case_id` from case.json.
        direction: The Case's `platform.direction` (which architecture pair it covers),
            or "unknown" when case.json does not say.
        status: The Agent's reported status (for example "completed"), or one of the
            replay's own statuses: "agent_error", "result_mismatch", "timeout" or
            "preparation_error".
        agent_exit_code: The Agent process's exit code, or None if it timed out or never
            started.
        elapsed_seconds: Wall-clock time for the whole Case, rounded to milliseconds.
        modified_paths: The paths the Agent listed in agent-result.json.
        actual_modified_paths: The paths whose content really changed in the worktree,
            sorted, including added and deleted files.
        error: A short explanation when the replay did not end normally, otherwise None.
    """

    case_id: str
    direction: str
    status: str
    agent_exit_code: int | None
    elapsed_seconds: float
    modified_paths: tuple[str, ...]
    actual_modified_paths: tuple[str, ...]
    error: str | None = None


def _json_object(path: Path) -> dict[str, Any]:
    """Read a JSON file that must contain an object.

    Args:
        path: The JSON file to read.

    Returns:
        The parsed object.

    Raises:
        ValueError: If the file's top-level value is not an object.
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    """Hash a file in 1 MiB chunks so large files are not read into memory at once.

    Args:
        path: The file to hash.

    Returns:
        The file's SHA-256 digest as lowercase hex.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file_hashes(root: Path) -> dict[str, str]:
    """Hash every regular file under a folder, for comparing a worktree before and after.

    Symlinks, folders and files that cannot be read are skipped, so the result only
    covers content the Agent could have edited as a normal file.

    Args:
        root: The folder to walk, usually work/repo.

    Returns:
        A map from each file's POSIX path relative to `root` to its SHA-256 digest.
    """
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            hashes[path.relative_to(root).as_posix()] = _sha256(path)
        except OSError:
            continue
    return hashes


def _wsl_path(path: Path) -> str:
    """Translate a Windows drive path into the path WSL sees, such as /mnt/e/archimedes.

    Args:
        path: A path on a lettered Windows drive. It is resolved first.

    Returns:
        The matching `/mnt/<drive>/...` path.

    Raises:
        ValueError: If the resolved path has no single-letter drive (for example, a UNC
            path or a POSIX path).
    """
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if not drive or len(drive) != 1:
        raise ValueError(f"cannot translate path to WSL: {resolved}")
    relative = resolved.as_posix()[len(resolved.drive) :].lstrip("/")
    return f"/mnt/{drive}/{relative}"


def _extract_debian_source(source: Path, repo_dir: Path, dpkg_source_root: Path) -> None:
    """Unpack a Debian source package into the worktree with `dpkg-source -x` under WSL.

    The command runs in the "Ubuntu" WSL distribution, using a copy of dpkg-dev and
    libdpkg-perl that was extracted into `dpkg_source_root` rather than installed, so no
    root access is needed. PERL5LIB, PATH and LD_LIBRARY_PATH point into that copy.

    Args:
        source: The Case's source folder, which must hold exactly one .dsc file.
        repo_dir: Where to unpack the source. It must not exist yet.
        dpkg_source_root: Root of the extracted packages, containing usr/bin/dpkg-source
            and usr/share/perl5.

    Returns:
        None.

    Raises:
        ValueError: If there is not exactly one .dsc file, or the extracted dpkg-source
            copy is incomplete.
        RuntimeError: If dpkg-source exits with a nonzero code. The message includes the
            last 2,000 characters of its output.
    """
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
    """Find every Case in the dataset by looking for cases/*/*/case.json.

    Args:
        dataset_root: The unpacked Development Cases folder.

    Returns:
        A map from each `case_id` to the folder holding its case.json.

    Raises:
        ValueError: If a case.json has no `case_id`, two Cases share an ID, or no Cases
            are found.
    """
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
    """Lay out one Case's competition workspace under `destination`.

    The source named by case.json's `paths.source_input` goes into work/repo: it is
    unpacked with dpkg-source when it holds a .dsc file, and copied as is otherwise.
    case.json and the failure log named by `paths.target_failure_log` are copied into
    input/, keeping the log's relative path. An empty output/ is created.

    Args:
        case_dir: The Case folder from `discover_cases`.
        destination: The new workspace folder. It must not exist yet.
        dpkg_source_root: Root of the extracted dpkg-dev copy, passed to
            `_extract_debian_source`.

    Returns:
        None.

    Raises:
        ValueError: If case.json lacks valid source or log paths, a path points outside
            the Case folder, the source or log is missing, or extraction fails its checks.
        RuntimeError: If dpkg-source fails.
        FileExistsError: If the workspace folders already exist.
    """
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
    """Prepare one Case, run the Agent on it and report what changed.

    The worktree is hashed before and after the run, so the result shows the files that
    really changed, not just the ones the Agent claims. A "completed" run whose
    `modified_paths` disagree with the real changes is reported as "result_mismatch". The
    Agent's combined stdout and stderr are saved as agent-console.log in the Case's
    workspace, and the workspace is kept under output_root/cases/<case_id>.

    Args:
        case_dir: The Case folder from `discover_cases`.
        output_root: The replay's output folder.
        agent_dir: The Agent folder (the one holding agent.yaml and src/). It is used as
            the working directory and as PYTHONPATH.
        python: The Python interpreter used to run `-m src.main`.
        dpkg_source_root: Root of the extracted dpkg-dev copy.
        timeout_seconds: Wall-clock limit for the Agent process, in seconds.

    Returns:
        The Case's result. Once case.json has been read, every failure, including a
        timeout or a preparation error, is returned as a result rather than raised, so
        one bad Case cannot stop the replay.

    Raises:
        KeyError: If case.json has no `case_id`. `discover_cases` already rejects such
            Cases, so this does not happen in a normal replay.
        ValueError: If case.json is not a JSON object.
    """
    metadata = _json_object(case_dir / "case.json")
    case_id = str(metadata["case_id"])
    platform = metadata.get("platform")
    direction = str(platform.get("direction", "unknown")) if isinstance(platform, dict) else "unknown"
    destination = output_root / "cases" / case_id
    started = time.monotonic()

    try:
        _prepare_workspace(case_dir, destination, dpkg_source_root)
        repo_dir = destination / "work" / "repo"
        original_hashes = _regular_file_hashes(repo_dir)
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
                (),
                "agent-result.json is missing",
            )
        result = _json_object(result_path)
        modified = result.get("modified_paths", [])
        modified_paths = tuple(str(item) for item in modified) if isinstance(modified, list) else ()
        final_hashes = _regular_file_hashes(repo_dir)
        actual_modified_paths = tuple(
            sorted(
                path
                for path in original_hashes.keys() | final_hashes.keys()
                if original_hashes.get(path) != final_hashes.get(path)
            )
        )
        status = str(result.get("status", "unknown"))
        if status == "completed" and tuple(sorted(modified_paths)) != actual_modified_paths:
            return ReplayResult(
                case_id,
                direction,
                "result_mismatch",
                completed.returncode,
                round(time.monotonic() - started, 3),
                modified_paths,
                actual_modified_paths,
                "agent-result modified_paths do not match the final worktree",
            )
        return ReplayResult(
            case_id,
            direction,
            status,
            completed.returncode,
            round(time.monotonic() - started, 3),
            modified_paths,
            actual_modified_paths,
        )
    except subprocess.TimeoutExpired:
        return ReplayResult(
            case_id,
            direction,
            "timeout",
            None,
            round(time.monotonic() - started, 3),
            (),
            (),
            f"agent exceeded {timeout_seconds:g} seconds",
        )
    except Exception as error:  # Catch everything so one broken Case cannot stop the replay.
        return ReplayResult(
            case_id,
            direction,
            "preparation_error",
            None,
            round(time.monotonic() - started, 3),
            (),
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
    """Write replay-summary.json with the run's inputs, counts and per-Case results.

    The summary records the SHA-256 of the dataset's manifest.json and the Agent's
    agent.yaml, so two replays can be checked to have used the same inputs. Results are
    sorted by Case ID.

    Args:
        output_root: The replay's output folder; the summary is written here.
        dataset_root: The Development Cases folder.
        agent_dir: The Agent folder.
        python: The interpreter that ran the Agent.
        results: One result per replayed Case, in any order.

    Returns:
        None.
    """
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
        "modified_case_count": sum(bool(item.actual_modified_paths) for item in ordered),
        "results": [asdict(item) for item in ordered],
    }
    (output_root / "replay-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the replay's command-line options.

    Command-line options:
        --dataset-root: The unpacked Development Cases folder (required).
        --output-root: A new folder for the replay output (required). It must not exist.
        --agent: The Agent folder. Default agents/archimedes.
        --python: The interpreter used to run the Agent. Default: the one running this
            script.
        --dpkg-source-root: Root of the locally extracted dpkg-dev and libdpkg-perl
            packages. Default runs/tools/dpkg-dev/root.
        --case: A Case ID to replay. Repeat it for several; omit it to replay all Cases.
        --jobs: How many Cases to run at once. Default 4.
        --timeout-seconds: Time limit for each Agent run. Default 120.

    Args:
        argv: The arguments to parse, or None to read them from sys.argv.

    Returns:
        The parsed options. Values are not validated here; `main` does that.
    """
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
    """Replay the selected Cases in parallel and write replay-summary.json.

    See `parse_args` for the options. A line is printed for each Case as it finishes,
    then a total. A replay only records what the Agent changed; it does not show that
    any package builds.

    Args:
        argv: The command-line arguments, or None to read them from sys.argv.

    Returns:
        0 if every Case finished with status "completed", otherwise 1.

    Raises:
        SystemExit: If the output folder already exists, --jobs or --timeout-seconds is
            not positive, the Python interpreter is missing, or a requested Case ID is
            unknown.
        ValueError: If the dataset has no Cases, a duplicate ID or a case.json without
            an ID.
    """
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
        f"modified {sum(bool(result.actual_modified_paths) for result in results)} worktrees."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
