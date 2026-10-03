from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.architecture.probes import execute_probe
from src.config import ModelBackend, RuntimeConfig
from src.errors import ConfigurationError, ProbeError
from src.schemas import Probe
from src.workspace.capabilities import CapabilityRegistry


class RuntimeHardeningTests(unittest.TestCase):
    def test_configuration_rejects_nonfinite_and_negative_cost_values(self) -> None:
        names = (
            "ARCHIMEDES_MODEL_TIMEOUT_SECONDS",
            "ARCHIMEDES_MAX_ESTIMATED_COST_USD",
            "ARCHIMEDES_INPUT_COST_PER_MILLION",
            "ARCHIMEDES_OUTPUT_COST_PER_MILLION",
        )
        for name, value in (
            (names[0], "nan"),
            (names[1], "inf"),
            (names[2], "-1"),
            (names[3], "bad"),
        ):
            with (
                self.subTest(name=name, value=value),
                patch.dict(os.environ, {name: value}, clear=True),
                self.assertRaises(ConfigurationError),
            ):
                RuntimeConfig.from_environment()

    def test_local_backend_accepts_ipv6_loopback_and_rejects_userinfo(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ARCHIMEDES_MODEL_BACKEND": ModelBackend.LOCAL_OPENAI_COMPATIBLE.value,
                "ARCHIMEDES_MODEL_BASE_URL": "http://[::1]:11434/v1/",
            },
            clear=True,
        ):
            config = RuntimeConfig.from_environment()
        self.assertEqual(config.model_base_url, "http://[::1]:11434/v1")

        with (
            patch.dict(
                os.environ,
                {
                    "ARCHIMEDES_MODEL_BACKEND": ModelBackend.LOCAL_OPENAI_COMPATIBLE.value,
                    "ARCHIMEDES_MODEL_BASE_URL": "http://user:secret@127.0.0.1:11434/v1",
                },
                clear=True,
            ),
            self.assertRaises(ConfigurationError),
        ):
            RuntimeConfig.from_environment()

        for invalid_url in (
            "http://127.0.0.1:invalid/v1",
            "http://127.0.0.1:70000/v1",
            "http://127.0.0.1 /v1",
        ):
            with (
                self.subTest(url=invalid_url),
                patch.dict(
                    os.environ,
                    {
                        "ARCHIMEDES_MODEL_BACKEND": ModelBackend.LOCAL_OPENAI_COMPATIBLE.value,
                        "ARCHIMEDES_MODEL_BASE_URL": invalid_url,
                    },
                    clear=True,
                ),
                self.assertRaises(ConfigurationError),
            ):
                RuntimeConfig.from_environment()

    def test_cost_limit_requires_complete_rates_and_log_level_is_validated(
        self,
    ) -> None:
        invalid_environments = (
            {"ARCHIMEDES_MAX_ESTIMATED_COST_USD": "1"},
            {"ARCHIMEDES_INPUT_COST_PER_MILLION": "1"},
            {"ARCHIMEDES_LOG_LEVEL": "verbose"},
            {
                "ARCHIMEDES_MODEL_BACKEND": ModelBackend.REMOTE_OPENAI_COMPATIBLE.value,
                "ARCHIMEDES_FAST_MODEL": "",
                "ARCHIMEDES_STRONG_MODEL": "strong",
            },
        )
        for environment in invalid_environments:
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
                self.assertRaises(ConfigurationError),
            ):
                RuntimeConfig.from_environment()

    def test_probe_uses_discovered_executable_not_untrusted_supplied_path(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            probe = Probe(
                id="probe-1",
                probe_type="make_dry_run",
                description="bounded dry run",
                command=["/untrusted/make", "-n"],
            )
            capabilities = CapabilityRegistry({"make": "/trusted/bin/make"})
            completed = subprocess.CompletedProcess([], 0, "ok", "")
            with patch(
                "src.architecture.probes.subprocess.run", return_value=completed
            ) as run:
                result = execute_probe(probe, capabilities=capabilities, repo_root=repo)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(run.call_args.args[0], ["/trusted/bin/make", "-n"])

    def test_probe_rejects_missing_working_directory_and_nonpositive_timeout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            capabilities = CapabilityRegistry({"make": "/trusted/bin/make"})
            missing = Probe(
                id="probe-missing",
                probe_type="make_dry_run",
                description="missing working directory",
                command=["make", "-n"],
                working_directory="missing",
            )
            with self.assertRaises(ProbeError):
                execute_probe(missing, capabilities=capabilities, repo_root=repo)

            invalid_timeout = Probe(
                id="probe-timeout",
                probe_type="make_dry_run",
                description="invalid timeout",
                command=["make", "-n"],
                timeout_seconds=0,
            )
            with self.assertRaises(ProbeError):
                execute_probe(
                    invalid_timeout, capabilities=capabilities, repo_root=repo
                )


if __name__ == "__main__":
    unittest.main()
