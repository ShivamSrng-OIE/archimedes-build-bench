"""Tests for the entry point in src/main.py and the exit codes it returns.

These cover the failure paths: a bad runtime setting must still produce a
structured agent-result.json, and an error while writing that file must not crash
the process.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.main import run

from .support import make_workspace


class MainEntrypointTests(unittest.TestCase):
    """Check how run() reports configuration and output errors."""

    def test_invalid_runtime_configuration_writes_structured_failure(self) -> None:
        """Check that an unknown ARCHIMEDES_LOG_LEVEL gives exit code 1 and a failed result.

        The platform reads agent-result.json, so even a startup error must leave a
        valid document with the reason and no modified paths.
        """
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            _, output = make_workspace(workspace)
            with patch.dict(
                os.environ,
                {
                    "BB_WORKSPACE": str(workspace),
                    "ARCHIMEDES_LOG_LEVEL": "not-a-level",
                },
                clear=True,
            ):
                exit_code = run()

            payload = json.loads(
                (output / "agent-result.json").read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["schema_version"], "0.1")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(
            payload["message"], "ARCHIMEDES_LOG_LEVEL must be a standard logging level"
        )
        self.assertEqual(payload["modified_paths"], [])

    def test_failure_result_write_error_does_not_escape(self) -> None:
        """Check that run() still returns 1 when writing the failure result raises OSError.

        A full disk while reporting a failure must not turn into an uncaught traceback.
        """
        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            make_workspace(workspace)
            with (
                patch.dict(
                    os.environ,
                    {
                        "BB_WORKSPACE": str(workspace),
                        "ARCHIMEDES_LOG_LEVEL": "not-a-level",
                    },
                    clear=True,
                ),
                patch("src.main.write_agent_result", side_effect=OSError("disk full")),
            ):
                exit_code = run()

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
