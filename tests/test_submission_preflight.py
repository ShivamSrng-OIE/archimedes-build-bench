"""Tests for dev/submission_preflight.py, the checklist run before uploading a submission.

They cover version agreement with agent.yaml, the readiness gates, model settings
checks, picking the newest ready result, archive freshness and digest attestations.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from src.constants import AGENT_VERSION

from dev.submission_preflight import (
    _archive_fresh,
    _digest_attestation,
    _latest_ready_result,
    _model_checks,
    _readiness_gates,
)
from runner.agent_submission import create_deterministic_zip

from . import AGENT


class SubmissionPreflightTests(unittest.TestCase):
    """Check the individual preflight helpers."""

    def test_runtime_version_matches_submission_manifest(self) -> None:
        """Check that AGENT_VERSION in src/constants.py matches the version in agent.yaml.

        A mismatch would make the reported version disagree with the packaged one.
        """
        manifest = (AGENT / "agent.yaml").read_text(encoding="utf-8")
        self.assertIn(f'version: "{AGENT_VERSION}"', manifest)

    def test_local_package_is_separate_from_hosted_and_final_gates(self) -> None:
        """Check that local and upload readiness do not depend on the Hosted Smoke result.

        Full evaluation readiness must need both a passed Hosted Smoke and final confirmation.
        """
        observed = {
            "submission_structure": True,
            "archive_matches_source": True,
            "team_registration": True,
            "submission_access": True,
            "hosted_smoke": False,
            "final_confirmation": False,
        }
        gates = _readiness_gates(observed)
        self.assertTrue(gates["local_submission_ready"])
        self.assertTrue(gates["upload_ready"])
        self.assertFalse(gates["full_evaluation_ready"])
        observed["hosted_smoke"] = True
        self.assertFalse(_readiness_gates(observed)["full_evaluation_ready"])
        observed["final_confirmation"] = True
        self.assertTrue(_readiness_gates(observed)["full_evaluation_ready"])

    def test_mock_default_is_not_competition_model_configuration(self) -> None:
        """Check that an empty environment, which falls back to the mock model, fails the checks.

        The mock backend is for tests only and must never look ready for competition.
        """
        checks = {item.name: item.passed for item in _model_checks({})}
        self.assertFalse(checks["real_model_backend"])
        self.assertFalse(checks["real_model_identifiers"])
        self.assertFalse(checks["model_base_url"])

    def test_remote_model_check_never_exposes_credential(self) -> None:
        """Check that a full remote setup passes and the API key never shows in any message.

        Preflight output is printed and saved, so it must not leak the bearer token.
        """
        secret = "never-print-this-value"
        checks = _model_checks({
            "ARCHIMEDES_MODEL_BACKEND": "remote_openai_compatible",
            "ARCHIMEDES_MODEL_BASE_URL": "https://model.example/v1",
            "ARCHIMEDES_MODEL_API_KEY": secret,
            "ARCHIMEDES_FAST_MODEL": "fast-v1",
            "ARCHIMEDES_STRONG_MODEL": "strong-v1",
        })
        self.assertTrue(all(item.passed for item in checks))
        self.assertNotIn(secret, "\n".join(item.message for item in checks))

    def test_latest_ready_result_uses_newest_result(self) -> None:
        """Check that the most recently written result.json wins, even if it failed.

        An older successful run must not hide a newer failure.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "001" / "result.json"
            new = root / "002" / "result.json"
            old.parent.mkdir()
            new.parent.mkdir()
            old.write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")
            new.write_text(json.dumps({"status": "failed"}), encoding="utf-8")
            old.touch()
            new.touch()
            payload, path = _latest_ready_result(root)
        self.assertEqual(path, new)
        self.assertEqual(payload, {"status": "failed"})

    def test_archive_freshness_ignores_member_order_but_not_content(self) -> None:
        """Check that a ZIP with the same members in reverse order still counts as fresh.

        Freshness compares each member's bytes and metadata with a fresh build of the
        source, so member order alone must not make an archive look stale.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = root / "agent"
            source = agent / "src"
            source.mkdir(parents=True)
            (agent / "agent.yaml").write_text(
                'schema_version: "0.1"\n'
                "agent:\n  name: archimedes\n  version: 1.0.0\n"
                'runtime:\n  type: managed\n  profile: "python-3.11"\n'
                "entrypoint:\n  - python\n  - -m\n  - src.main\n"
                'protocol:\n  version: "0.1"\n',
                encoding="utf-8",
            )
            (agent / "requirements.lock").write_text("", encoding="utf-8")
            (agent / "README.md").write_text("agent\n", encoding="utf-8")
            (source / "__init__.py").write_text("", encoding="utf-8")
            (source / "main.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            original = root / "original.zip"
            reordered = root / "reordered.zip"
            create_deterministic_zip(agent, original)
            with ZipFile(original) as archive:
                entries = [(archive.getinfo(name), archive.read(name)) for name in archive.namelist()]
            with ZipFile(reordered, "w", compression=ZIP_DEFLATED) as archive:
                for info, data in reversed(entries):
                    archive.writestr(info, data)
            fresh, _, digest = _archive_fresh(agent, reordered)
        self.assertTrue(fresh)
        self.assertIsNotNone(digest)

    def test_manual_qualification_must_match_current_archive_digest(self) -> None:
        """Check that a recorded Hosted Smoke digest passes only if it matches the archive.

        Letter case does not matter, but a different or malformed digest must fail.
        """
        digest = "a" * 64
        self.assertTrue(
            _digest_attestation("hosted", digest.upper(), digest, "Hosted Smoke Test").passed
        )
        self.assertFalse(
            _digest_attestation("hosted", "b" * 64, digest, "Hosted Smoke Test").passed
        )
        self.assertFalse(
            _digest_attestation("hosted", "not-a-digest", digest, "Hosted Smoke Test").passed
        )


if __name__ == "__main__":
    unittest.main()
