"""Audit the Agent submission ZIP locally and record the hosted steps confirmed for it.

Local checks cannot reach the organizer's model channel, so a passing Hosted Smoke Test and
the irreversible final confirmation are supplied by hand as the SHA-256 of the ZIP they
applied to. The audit never prints credential values.
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

from runner.agent_submission import (
    SubmissionError,
    check_submission,
    create_deterministic_zip,
)


@dataclass(frozen=True, slots=True)
class Check:
    """The result of one named audit check.

    Attributes:
        name: A stable identifier, such as "archive_matches_source".
        passed: Whether the check passed.
        message: A one-line explanation for a person reading the report.
    """

    name: str
    passed: bool
    message: str


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command from the repository root and capture its output as text.

    Args:
        arguments: The program and its arguments.

    Returns:
        The finished process. A nonzero exit code is not treated as an error.
    """
    return subprocess.run(
        arguments,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _digest(path: Path) -> str:
    """Return a file's SHA-256 digest.

    Args:
        path: The file to hash.

    Returns:
        The digest as lowercase hex.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive_fresh(agent: Path, archive: Path) -> tuple[bool, str, str | None]:
    """Check that the ZIP on disk was built from the current Agent source.

    A fresh ZIP is built in a temporary folder with the Starter Kit's
    `create_deterministic_zip` and compared member by member with `archive`: the same
    names, the same bytes, and the same timestamp, permission bits and compression method.
    Because the members are compared and not the raw ZIP bytes, a ZIP whose data was
    compressed by a different zlib build still matches.

    Args:
        agent: The Agent folder.
        archive: The submission ZIP to check.

    Returns:
        A tuple of (passed, message, digest), where digest is the SHA-256 of `archive`, or
        None when the file does not exist.

    Raises:
        SubmissionError: If the Starter Kit cannot build a ZIP from `agent`.
        OSError: If a file cannot be read or written.
    """
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
    """Check that the development model settings name a real, fully configured backend.

    Nothing is contacted; only the settings are inspected. For the API key, only its
    presence is checked and its value is never reported. A local backend needs no key,
    a remote one does.

    Args:
        environment: The environment variables to inspect, normally a copy of os.environ.

    Returns:
        Four checks: "real_model_backend", "real_model_identifiers" (FAST and STRONG
        model names set and not starting with "mock"), "model_base_url" and
        "model_credential_injection".
    """
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
    """Check that Docker, the Starter Kit images and enough disk space are available.

    The image names come from `BB_AGENT_IMAGE`, `BB_VALIDATOR_IMAGE`,
    `BB_EXAMPLE_ASSETS_IMAGE` and `BB_CLEANUP_IMAGE`, falling back to the pinned defaults
    below. Images are only looked up locally with `docker image inspect`, never pulled.

    Args:
        environment: The environment variables to read image overrides from.

    Returns:
        A single failed "docker" check if the Docker CLI is not on PATH. Otherwise a
        "docker" daemon check, one check per image, and a "disk_space" check that needs
        at least 3 GiB free on the repository's drive.
    """
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
    """Find and read the most recent `bb ready` result.json under runs/ready.

    "Most recent" means the newest file modification time, with the path as a tie-breaker.

    Args:
        ready_root: The folder holding one subfolder per `bb ready` run.

    Returns:
        A tuple of (payload, path). Both are None when no result exists. The payload is
        None but the path is set when the newest file cannot be read or is not a JSON
        object.
    """
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
    """Check that the latest `bb ready` run succeeded and produced this exact ZIP.

    Args:
        ready_root: The folder holding one subfolder per `bb ready` run.
        archive_digest: SHA-256 of the submission ZIP on disk, or None if it is missing.

    Returns:
        An "official_ready" check that passes only if the latest result has status
        "succeeded", exit code 0, and a first artifact whose SHA-256 equals
        `archive_digest`.
    """
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
    """Check that a hand-supplied SHA-256 matches the current ZIP.

    This is how a person records that a hosted step (the Hosted Smoke Test or the final
    portal confirmation) was done for this exact ZIP. The value is trimmed and lowercased,
    must be 64 hex characters, and is compared in constant time.

    Args:
        name: The check's name, such as "hosted_smoke".
        supplied: The digest given on the command line, or None if it was not given.
        archive_digest: SHA-256 of the submission ZIP on disk, or None if it is missing.
        label: A readable name for the step, used in the message.

    Returns:
        A check that passes only when `supplied` is a valid digest equal to
        `archive_digest`.
    """
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


def _readiness_gates(by_name: dict[str, bool]) -> dict[str, bool]:
    """Combine individual check results into the competition readiness gates.

    Local packaging and hosted qualification are separate gates: a ZIP can be ready to
    upload long before it has passed the Hosted Smoke Test and been confirmed as final.
    Only the checks named below feed the gates; the others are informational.

    Args:
        by_name: Each check's name mapped to whether it passed. Must include
            "submission_structure", "archive_matches_source", "team_registration",
            "submission_access", "hosted_smoke" and "final_confirmation".

    Returns:
        The gates "source_and_archive_ready" and "local_submission_ready" (both: the
        static checks pass and the ZIP matches the source), "upload_ready" (plus team
        registration and submission access), and "full_evaluation_ready" (plus Hosted
        Smoke and final confirmation for this ZIP).

    Raises:
        KeyError: If one of the required check names is missing.
    """
    source_ready = by_name["submission_structure"] and by_name["archive_matches_source"]
    upload_ready = (
        source_ready
        and by_name["team_registration"]
        and by_name["submission_access"]
    )
    return {
        "source_and_archive_ready": source_ready,
        "local_submission_ready": source_ready,
        "upload_ready": upload_ready,
        "full_evaluation_ready": (
            upload_ready
            and by_name["hosted_smoke"]
            and by_name["final_confirmation"]
        ),
    }


def audit(
    *,
    agent: Path,
    archive: Path,
    environment: dict[str, str],
    team_registered: bool,
    submission_access_confirmed: bool,
    hosted_smoke_sha256: str | None,
    final_confirmed_sha256: str | None = None,
) -> dict[str, Any]:
    """Run every preflight check on the Agent and its submission ZIP.

    The checks are: the Starter Kit's static submission check, a clean Git state for the
    Agent folder, the ZIP matching the current source, model settings (only when
    `ARCHIMEDES_MODEL_BACKEND` is set), Docker and images, the latest `bb ready` result,
    and the manual confirmations. Only some of them decide the gates; see
    `_readiness_gates`.

    Args:
        agent: The Agent folder. It must be inside the repository.
        archive: The submission ZIP to audit.
        environment: Environment variables to read model and image settings from.
        team_registered: Whether team registration was confirmed by hand.
        submission_access_confirmed: Whether submission access on the portal was
            confirmed by hand.
        hosted_smoke_sha256: SHA-256 of the ZIP that passed the Hosted Smoke Test, or None.
        final_confirmed_sha256: SHA-256 of the ZIP confirmed as final on the portal, or
            None.

    Returns:
        A report with "schema_version", "status" ("ready" only when
        full_evaluation_ready holds, otherwise "not_ready"), "gates", the ZIP's path and
        SHA-256 under "archive", the static check report under "agent" (None if it
        failed), and every check under "checks".

    Raises:
        ValueError: If `agent` is not inside the repository root.
    """
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
    # The organizer injects the real model settings only during Hosted Smoke and Full
    # Evaluation, so local model settings are reported here but never decide a gate.
    if environment.get("ARCHIMEDES_MODEL_BACKEND"):
        checks.extend(_model_checks(environment))
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
            _digest_attestation(
                "final_confirmation",
                final_confirmed_sha256,
                archive_digest,
                "final portal confirmation",
            ),
        ]
    )
    by_name = {item.name: item.passed for item in checks}
    gates = _readiness_gates(by_name)
    return {
        "schema_version": "0.1",
        "status": "ready" if gates["full_evaluation_ready"] else "not_ready",
        "gates": gates,
        "archive": {
            "path": str(archive.resolve()),
            "sha256": archive_digest,
        },
        "agent": report_payload,
        "checks": [asdict(item) for item in checks],
    }


def main() -> int:
    """Run the preflight audit from the command line.

    Command-line options:
        --agent: The Agent folder. Default agents/archimedes.
        --archive: The submission ZIP. Default dist/agent-submission.zip.
        --team-registered: Record that team registration was confirmed.
        --submission-access-confirmed: Record that portal submission access was confirmed.
        --hosted-smoke-sha256: SHA-256 of the ZIP that passed the Hosted Smoke Test.
        --final-confirmed-sha256: SHA-256 of the ZIP confirmed as final on the portal.
        --json: Print the full report as JSON. Without it, print PASS or FAIL for each
            gate and the message of every failed check.

    Returns:
        0 if the report status is "ready", otherwise 1.

    Raises:
        ValueError: If the Agent folder is not inside the repository root.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, default=ROOT / "agents" / "archimedes")
    parser.add_argument("--archive", type=Path, default=ROOT / "dist" / "agent-submission.zip")
    parser.add_argument("--team-registered", action="store_true")
    parser.add_argument("--submission-access-confirmed", action="store_true")
    parser.add_argument("--hosted-smoke-sha256")
    parser.add_argument("--final-confirmed-sha256")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(
        agent=args.agent.resolve(),
        archive=args.archive.resolve(),
        environment=dict(os.environ),
        team_registered=args.team_registered,
        submission_access_confirmed=args.submission_access_confirmed,
        hosted_smoke_sha256=args.hosted_smoke_sha256,
        final_confirmed_sha256=args.final_confirmed_sha256,
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
