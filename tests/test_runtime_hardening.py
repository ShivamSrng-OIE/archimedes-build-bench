from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.config import ModelBackend, RuntimeConfig
from src.errors import ConfigurationError


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



if __name__ == "__main__":
    unittest.main()
