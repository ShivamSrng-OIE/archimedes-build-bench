"""Package the final worktrees from one replay into a Development Validation Worktree Bundle.

The ZIP is byte-for-byte reproducible: entries are sorted, timestamps are fixed and file modes
are normalized. It is uploaded to the organizer's Development Validation service and is
separate from the Agent submission ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = "0.1"
SUITE_ID = "development-recommended-v1"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
REPOSITORY_METADATA_NAMES = {
    ".bzr",
    ".fslckout",
    ".hg",
    ".pc",
    ".svn",
    "CVS",
    "_darcs",
}


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


def _case_ids(replay_root: Path) -> list[str]:
    """Read the Case IDs from a replay's replay-summary.json and check they can be bundled.

    The list is taken from the summary's "results" key, or "cases" if "results" is absent.

    Args:
        replay_root: The replay output folder.

    Returns:
        The Case IDs, sorted.

    Raises:
        ValueError: If the summary has no Case list, an entry lacks a string `case_id`, any
            Case did not finish with status "completed", or the IDs are empty or repeated.
    """
    summary = json.loads((replay_root / "replay-summary.json").read_text(encoding="utf-8"))
    cases = summary.get("results", summary.get("cases"))
    if not isinstance(cases, list):
        raise ValueError("replay-summary.json must contain a cases list")
    ids: list[str] = []
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise ValueError("every replay summary case must have a string case_id")
        if item.get("status") != "completed":
            raise ValueError(f"case {item['case_id']} did not complete")
        ids.append(item["case_id"])
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("case IDs must be non-empty and unique")
    return sorted(ids)


def _is_repository_metadata(relative_path: Path) -> bool:
    """Tell whether a path lies inside version-control or quilt metadata.

    Such files (.git*, .hg, .svn, .pc and the others in REPOSITORY_METADATA_NAMES) are not
    package source, and the Development Validation service rejects bundles that contain them.

    Args:
        relative_path: A path relative to the worktree root.

    Returns:
        True if any part of the path starts with ".git" or is in REPOSITORY_METADATA_NAMES.
    """
    return any(
        part.startswith(".git") or part in REPOSITORY_METADATA_NAMES
        for part in relative_path.parts
    )


def _repo_files(repo: Path) -> list[Path]:
    """List the files to bundle from one final worktree, refusing anything unusual.

    Repository metadata folders are pruned from the walk, so nothing inside them is
    checked or included. The symlink and regular-file checks run before the metadata
    filter, so even a symlinked .git folder or .gitignore file stops packaging.

    Args:
        repo: The Case's work/repo folder.

    Returns:
        The regular files to include, sorted by their POSIX path relative to `repo`.

    Raises:
        ValueError: If `repo` is missing, or the walk finds a symlink or a non-regular
            file.
    """
    if not repo.is_dir():
        raise ValueError(f"missing final worktree: {repo}")
    files: list[Path] = []
    for root, dirs, names in os.walk(repo, followlinks=False):
        root_path = Path(root)
        retained_dirs: list[str] = []
        for name in sorted(dirs):
            path = root_path / name
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed: {path}")
            if not _is_repository_metadata(path.relative_to(repo)):
                retained_dirs.append(name)
        dirs[:] = retained_dirs
        for name in sorted(names):
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular files are not allowed: {path}")
            if not _is_repository_metadata(path.relative_to(repo)):
                files.append(path)
    return sorted(files, key=lambda path: path.relative_to(repo).as_posix())


def create_bundle(replay_root: Path, output: Path) -> dict[str, object]:
    """Write the Worktree Bundle ZIP for every Case in a replay.

    The layout follows the Development Validation portal's contract: a root manifest.json
    (schema "0.1", suite "development-recommended-v1", one entry per Case) and each Case's
    full source tree under cases/<case-id>/worktree/input/. Every entry uses the fixed
    1980-01-01 timestamp and Unix mode 0644, or 0755 for executable files, so the same
    replay always gives the same bytes.

    Args:
        replay_root: The replay output folder holding replay-summary.json and cases/.
        output: The ZIP file to write. It is overwritten if it exists; parent folders
            are created.

    Returns:
        A summary with the ZIP's absolute "path", "sha256", size in "bytes", the bundled
        "case_ids" and the "file_count" including manifest.json.

    Raises:
        ValueError: If the replay summary or any worktree fails the checks in `_case_ids`
            or `_repo_files`.
    """
    case_ids = _case_ids(replay_root)
    entries: list[tuple[PurePosixPath, Path]] = []
    manifest_cases: list[dict[str, object]] = []
    for case_id in case_ids:
        repo = replay_root / "cases" / case_id / "work" / "repo"
        worktree = PurePosixPath("cases") / case_id / "worktree"
        prefix = worktree / "input"
        files = _repo_files(repo)
        manifest_cases.append(
            {
                "case_id": case_id,
                "worktree": worktree.as_posix(),
            }
        )
        entries.extend((prefix / path.relative_to(repo).as_posix(), path) for path in files)

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "cases": manifest_cases,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        manifest_info = zipfile.ZipInfo("manifest.json", FIXED_ZIP_TIME)
        manifest_info.create_system = 3
        manifest_info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(manifest_info, manifest_bytes, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for archive_path, source in sorted(entries, key=lambda item: item[0].as_posix()):
            info = zipfile.ZipInfo(archive_path.as_posix(), FIXED_ZIP_TIME)
            info.create_system = 3
            mode = 0o755 if os.access(source, os.X_OK) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(
                info,
                source.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )

    return {
        "path": str(output.resolve()),
        "sha256": _sha256(output),
        "bytes": output.stat().st_size,
        "case_ids": case_ids,
        "file_count": len(entries) + 1,
    }


def main() -> int:
    """Build a Worktree Bundle from the command line and print its summary as JSON.

    Command-line options:
        --replay-root: The replay output folder to package (required).
        --output: The ZIP file to write (required).

    Returns:
        0 once the bundle is written.

    Raises:
        ValueError: If the replay cannot be bundled; see `create_bundle`.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_bundle(args.replay_root.resolve(), args.output.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
