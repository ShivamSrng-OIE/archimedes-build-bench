"""Create a deterministic Build-Bench Development Validation worktree bundle."""

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_ids(replay_root: Path) -> list[str]:
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


def _repo_files(repo: Path) -> list[Path]:
    if not repo.is_dir():
        raise ValueError(f"missing final worktree: {repo}")
    files: list[Path] = []
    for root, dirs, names in os.walk(repo, followlinks=False):
        root_path = Path(root)
        for name in sorted(dirs):
            path = root_path / name
            if path.is_symlink():
                raise ValueError(f"symlinks are not allowed: {path}")
        for name in sorted(names):
            path = root_path / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular files are not allowed: {path}")
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(repo).as_posix())


def create_bundle(replay_root: Path, output: Path) -> dict[str, object]:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_bundle(args.replay_root.resolve(), args.output.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
