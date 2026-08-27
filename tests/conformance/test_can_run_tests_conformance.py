"""Conformance test suite for CanRunTests capability adapters."""

from __future__ import annotations

from typing import Mapping
import unittest

from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.models.pipeline import TestRunResult
from supportmaster.pipeline.capabilities import CanRunTests


class FakeTestRunnerAdapter:
    def __init__(self, default_status: str = "PASSED") -> None:
        self.default_status = default_status

    def run_tests(
        self,
        repo: str,
        commit_sha: str,
        test_targets: list[str] | None = None,
    ) -> tuple[TestRunResult, ExternalOperationReceipt]:
        receipt = ExternalOperationReceipt(
            operation_type="TEST_EXECUTION",
            requested_action="run_tests",
            status="SUCCEEDED",
            external_id=commit_sha,
            details={"repo": repo, "targets": ",".join(test_targets or [])},
        )
        result = TestRunResult(
            suite_name=f"{repo}-tests",
            status=self.default_status,  # type: ignore[arg-type]
            passed_count=10 if self.default_status == "PASSED" else 0,
            failed_count=1 if self.default_status == "FAILED" else 0,
            duration_ms=120.5,
            receipt=receipt,
        )
        return result, receipt


class CanRunTestsConformanceTests(unittest.TestCase):
    def test_can_run_tests_contract(self) -> None:
        adapter = FakeTestRunnerAdapter(default_status="PASSED")
        self.assertIsInstance(adapter, CanRunTests)

        result, receipt = adapter.run_tests("web-app", "abcdef123456", ["tests/test_auth.py"])
        self.assertIsInstance(receipt, ExternalOperationReceipt)
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertIsInstance(result, TestRunResult)
        self.assertEqual(result.status, "PASSED")
        self.assertEqual(result.passed_count, 10)


if __name__ == "__main__":
    unittest.main()
