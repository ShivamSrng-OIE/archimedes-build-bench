"""Strict development-side audit before uploading an Archimedes Agent version.

This does not replace ``bb ready`` or the hosted Smoke Test. It prevents a locally valid archive
from being mistaken for a competition-ready LLM Agent and never prints credential values.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runner.agent_submission import (  # noqa: E402
    SubmissionError,
    check_submission,
    create_deterministic_zip,
)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    message: str


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_fresh(agent: Path, archive: Path) -> tuple[bool, str, str | None]:
    if not archive.is_file():
        return False, "submission archive is missing", None
    actual_digest = _digest(archive)
    with tempfile.TemporaryDirectory(prefix="archimedes-preflight-") as temporary:
        expected = Path(temporary) / "expected.zip"
        create_deterministic_zip(agent, expected)
        with zipfile.ZipFile(expected) as expected_zip, zipfile.ZipFile(archive) as actual_zip:
            expected_names = expected_zip.namelist()
            actual_names = actual_zip.namelist()
            same_names = set(expected_names) == set(actual_names)
            contents_match = same_names and all(
                expected_zip.read(name) == actual_zip.read(name) for name in expected_names
            )
            metadata_match = same_names and all(
                (
                    expected_zip.getinfo(name).date_time,
                    expected_zip.getinfo(name).external_attr,
                    expected_zip.getinfo(name).compress_type,
                )
                == (
                    actual_zip.getinfo(name).date_time,
                    actual_zip.getinfo(name).external_attr,
                    actual_zip.getinfo(name).compress_type,
                )
                for name in expected_names
            )
    if not contents_match or not metadata_match:
        return False, "archive members do not match the current deterministic Agent source", actual_digest
    return True, "archive members exactly match the current deterministic Agent source", actual_digest


def _model_checks(environment: dict[str, str]) -> list[Check]:
    backend = environment.get("ARCHIMEDES_MODEL_BACKEND", "mock").strip().lower()
    fast = environment.get("ARCHIMEDES_FAST_MODEL", "mock-fast").strip()
    strong = environment.get("ARCHIMEDES_STRONG_MODEL", "mock-strong").strip()
    base_url = environment.get("ARCHIMEDES_MODEL_BASE_URL", "").strip()
    api_key_present = bool(environment.get("ARCHIMEDES_MODEL_API_KEY", "").strip())
    real_backend = backend in {"local_openai_compatible", "remote_openai_compatible"}
    real_names = bool(fast and strong) and not any(
        value.lower().startswith("mock") for value in (fast, strong)
    )
    credentials_ok = backend == "local_openai_compatible" or (
        backend == "remote_openai_compatible" and api_key_present
    )
    credential_message = (
        "credential requirements are satisfied for the selected backend"
        if credentials_ok
        else (
            "remote backend has no injected API credential"
            if backend == "remote_openai_compatible"
            else "select a real backend before validating credential injection"
        )
    )
    return [
        Check(
            "real_model_backend",
            real_backend,
            "real OpenAI-compatible backend selected" if real_backend else "backend is mock or unsupported",
        ),
        Check(
            "real_model_identifiers",
            real_names,
            "FAST and STRONG model identifiers are non-mock"
            if real_names
            else "FAST and STRONG real model identifiers are required",
        ),
        Check(
            "model_base_url",
            bool(base_url),
            "model base URL is configured" if base_url else "model base URL is not configured",
        ),
        Check(
            "model_credential_injection",
            credentials_ok,
            credential_message,
        ),
    ]


def _docker_checks(environment: dict[str, str]) -> list[Check]:
    docker = shutil.which("docker")
    if docker is None:
        return [Check("docker", False, "Docker CLI is unavailable")]
    info = _run([docker, "info"])
    checks = [Check("docker", info.returncode == 0, "Docker daemon is available" if info.returncode == 0 else "Docker daemon is unavailable")]
    images = {
        "agent_image": environment.get("BB_AGENT_IMAGE", "python:3.11.9-slim-bookworm"),
        "validator_image": environment.get(
            "BB_VALIDATOR_IMAGE",
            "ghcr.io/terriyyy/buildbench-validator-runtime@sha256:"
            "11151f88f6b12c578b9c9fafd0420d6da27c6969b5909aaa4c51e78dd9acd7c7",
        ),
        "example_assets_image": environment.get(
            "BB_EXAMPLE_ASSETS_IMAGE",
            "ghcr.io/terriyyy/buildbench-example-assets@sha256:"
            "98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54",
        ),
        "cleanup_image": environment.get("BB_CLEANUP_IMAGE", "ubuntu:24.04"),
    }
    for name, image in images.items():
        result = _run([docker, "image", "inspect", image])
        checks.append(
            Check(
                name,
                result.returncode == 0,
                f"configured image is locally available: {image}"
                if result.returncode == 0
                else f"configured image is unavailable: {image}",
            )
        )
    free_bytes = shutil.disk_usage(ROOT).free
    checks.append(
        Check(
            "disk_space",
            free_bytes >= 3 * 1024**3,
            "at least 3 GiB free disk space is available"
            if free_bytes >= 3 * 1024**3
            else "less than 3 GiB free disk space is available",
        )
    )
    return checks


def _latest_ready_result(ready_root: Path) -> tuple[dict[str, Any] | None, Path | None]:
    results = sorted(
        ready_root.glob("*/result.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.as_posix()),
    )
    if not results:
        return None, None
    latest = results[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, latest
    return payload if isinstance(payload, dict) else None, latest


def _ready_check(ready_root: Path, archive_digest: str | None) -> Check:
    payload, path = _latest_ready_result(ready_root)
    if payload is None or path is None:
        return Check("official_ready", False, "no readable bb ready result exists")
    artifacts = payload.get("artifacts")
    artifact_digest: str | None = None
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        value = artifacts[0].get("sha256")
        artifact_digest = value if isinstance(value, str) else None
    passed = (
        payload.get("status") == "succeeded"
        and payload.get("exit_code") == 0
        and archive_digest is not None
        and artifact_digest == archive_digest
    )
    return Check(
        "official_ready",
        passed,
        "latest bb ready succeeded and its artifact digest matches"
        if passed
        else "latest bb ready did not succeed with a matching archive digest",
    )


def _digest_attestation(
    name: str, supplied: str | None, archive_digest: str | None, label: str
) -> Check:
    normalized = supplied.strip().lower() if supplied else ""
    valid = bool(re.fullmatch(r"[0-9a-f]{64}", normalized))
    passed = bool(
        valid
        and archive_digest
        and hmac.compare_digest(normalized, archive_digest.lower())
    )
    if passed:
        message = f"{label} was confirmed for the current archive digest"
    elif supplied:
        message = f"{label} digest is invalid or does not match the current archive"
    else:
        message = f"{label} has not been confirmed for the current archive digest"
    return Check(name, passed, message)


def audit(
    *,
    agent: Path,
    archive: Path,
    environment: dict[str, str],
    team_registered: bool,
    submission_access_confirmed: bool,
    competition_model_access_confirmed: bool,
    model_smoke_sha256: str | None,
    hosted_smoke_sha256: str | None,
) -> dict[str, Any]:
    checks: list[Check] = []
    try:
        report = check_submission(agent)
        checks.append(Check("submission_structure", True, "official static submission checks pass"))
        report_payload: dict[str, Any] | None = report.as_dict()
    except (OSError, UnicodeError, SubmissionError) as error:
        checks.append(Check("submission_structure", False, f"submission check failed: {error}"))
        report_payload = None

    status = _run(["git", "status", "--porcelain", "--", str(agent.relative_to(ROOT))])
    clean = status.returncode == 0 and not status.stdout.strip()
    checks.append(
        Check(
            "agent_source_committed",
            clean,
            "Agent source is committed and clean" if clean else "Agent source has uncommitted changes",
        )
    )

    archive_digest: str | None = None
    try:
        fresh, message, archive_digest = _archive_fresh(agent, archive)
    except (OSError, UnicodeError, SubmissionError) as error:
        fresh, message = False, f"archive validation failed: {error}"
    checks.append(Check("archive_matches_source", fresh, message))
    checks.extend(_model_checks(environment))
    checks.append(
        Check(
            "competition_model_access",
            competition_model_access_confirmed,
            (
                "organizer-compatible model delivery was manually confirmed"
                if competition_model_access_confirmed
                else "organizer-compatible model/network/credential delivery has not been confirmed"
            ),
        )
    )
    checks.append(
        _digest_attestation(
            "real_model_smoke",
            model_smoke_sha256,
            archive_digest,
            "real-model smoke run",
        )
    )
    checks.extend(_docker_checks(environment))
    checks.append(_ready_check(ROOT / "runs" / "ready", archive_digest))
    checks.extend(
        [
            Check(
                "team_registration",
                team_registered,
                "Team registration was manually confirmed"
                if team_registered
                else "Team registration has not been confirmed",
            ),
            Check(
                "submission_access",
                submission_access_confirmed,
                "submission access was manually confirmed"
                if submission_access_confirmed
                else "public or invited-pilot submission access has not been confirmed",
            ),
            _digest_attestation(
                "hosted_smoke",
                hosted_smoke_sha256,
                archive_digest,
                "Hosted Smoke Test",
            ),
        ]
    )
    by_name = {item.name: item.passed for item in checks}
    source_ready = all(
        by_name[name]
        for name in ("submission_structure", "agent_source_committed", "archive_matches_source")
    )
    local_ready = source_ready and all(
        by_name[name]
        for name in (
            "real_model_backend",
            "real_model_identifiers",
            "model_base_url",
            "model_credential_injection",
            "competition_model_access",
            "real_model_smoke",
            "docker",
            "agent_image",
            "validator_image",
            "example_assets_image",
            "cleanup_image",
            "disk_space",
            "official_ready",
        )
    )
    upload_ready = local_ready and by_name["team_registration"] and by_name["submission_access"]
    full_evaluation_ready = upload_ready and by_name["hosted_smoke"]
    return {
        "schema_version": "0.1",
        "status": "ready" if full_evaluation_ready else "not_ready",
        "gates": {
            "source_and_archive_ready": source_ready,
            "local_submission_ready": local_ready,
            "upload_ready": upload_ready,
            "full_evaluation_ready": full_evaluation_ready,
        },
        "archive": {
            "path": str(archive.resolve()),
            "sha256": archive_digest,
        },
        "agent": report_payload,
        "checks": [asdict(item) for item in checks],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, default=ROOT / "agents" / "archimedes")
    parser.add_argument("--archive", type=Path, default=ROOT / "dist" / "agent-submission.zip")
    parser.add_argument("--team-registered", action="store_true")
    parser.add_argument("--submission-access-confirmed", action="store_true")
    parser.add_argument("--competition-model-access-confirmed", action="store_true")
    parser.add_argument("--model-smoke-sha256")
    parser.add_argument("--hosted-smoke-sha256")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(
        agent=args.agent.resolve(),
        archive=args.archive.resolve(),
        environment=dict(os.environ),
        team_registered=args.team_registered,
        submission_access_confirmed=args.submission_access_confirmed,
        competition_model_access_confirmed=args.competition_model_access_confirmed,
        model_smoke_sha256=args.model_smoke_sha256,
        hosted_smoke_sha256=args.hosted_smoke_sha256,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for name, passed in result["gates"].items():
            print(f"{'PASS' if passed else 'FAIL'} {name}")
        for check in result["checks"]:
            if not check["passed"]:
                print(f"  - {check['name']}: {check['message']}")
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
