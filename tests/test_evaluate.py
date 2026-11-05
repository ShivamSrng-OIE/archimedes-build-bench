"""Tests for helpers in ``dev/evaluate.py``, the offline experiment harness."""

from __future__ import annotations

import unittest
from inspect import signature

from dev.evaluate import run_case, safe_error_summary


class EvaluateHarnessTests(unittest.TestCase):
    """Tests for error summaries and per-Case timeouts in the harness."""

    def test_error_summary_is_bounded_and_redacts_credentials(self) -> None:
        """Check that a long error text is cut to 3,000 characters and that bearer tokens and
        ``API_KEY`` values are removed. Error summaries are stored in results files, so they must
        not leak secrets.
        """
        value = safe_error_summary(
            "x" * 4000 + "\nAuthorization: Bearer private-value\nAPI_KEY=also-private\n"
        )
        self.assertIsNotNone(value)
        assert value is not None
        self.assertLessEqual(len(value), 3000)
        self.assertNotIn("private-value", value)
        self.assertNotIn("also-private", value)

    def test_empty_error_summary_is_absent(self) -> None:
        """Check that whitespace-only error text gives ``None`` instead of an empty summary."""
        self.assertIsNone(safe_error_summary(" \n"))

    def test_case_timeout_has_a_bounded_default(self) -> None:
        """Check that ``run_case`` defaults to a 120-second timeout per Case, so one stuck Case
        cannot stall a whole run.
        """
        self.assertEqual(signature(run_case).parameters["case_timeout_seconds"].default, 120)


if __name__ == "__main__":
    unittest.main()
