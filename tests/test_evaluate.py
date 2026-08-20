from __future__ import annotations

import unittest
from inspect import signature

from dev.evaluate import run_case, safe_error_summary


class EvaluateHarnessTests(unittest.TestCase):
    def test_error_summary_is_bounded_and_redacts_credentials(self) -> None:
        value = safe_error_summary(
            "x" * 4000 + "\nAuthorization: Bearer private-value\nAPI_KEY=also-private\n"
        )
        self.assertIsNotNone(value)
        assert value is not None
        self.assertLessEqual(len(value), 3000)
        self.assertNotIn("private-value", value)
        self.assertNotIn("also-private", value)

    def test_empty_error_summary_is_absent(self) -> None:
        self.assertIsNone(safe_error_summary(" \n"))

    def test_case_timeout_has_a_bounded_default(self) -> None:
        self.assertEqual(signature(run_case).parameters["case_timeout_seconds"].default, 120)


if __name__ == "__main__":
    unittest.main()
