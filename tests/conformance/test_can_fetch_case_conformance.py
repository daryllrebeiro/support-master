"""Conformance test suite for CanFetchCase capability adapters."""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import InMemoryIssueTrackerAdapter
from supportmaster.integrations.contracts import IssueRecord
from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.models.support_case import SupportCase
from supportmaster.pipeline.capabilities import CanFetchCase


class CanFetchCaseConformanceTests(unittest.TestCase):
    def test_can_fetch_case_contract(self) -> None:
        adapter = InMemoryIssueTrackerAdapter(
            issues=[
                IssueRecord(key="PROJ-123", title="Broken checkout flow", status="OPEN"),
            ]
        )
        self.assertIsInstance(adapter, CanFetchCase)

        # Existing issue
        case, receipt = adapter.fetch_case("PROJ-123")
        self.assertIsInstance(receipt, ExternalOperationReceipt)
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertIsInstance(case, SupportCase)
        self.assertEqual(case.case_id, "PROJ-123")
        self.assertIn("checkout", case.description.lower())

        # Non-existent issue (fail-closed, return None + FAILED receipt)
        missing_case, missing_receipt = adapter.fetch_case("NONEXISTENT-999")
        self.assertIsInstance(missing_receipt, ExternalOperationReceipt)
        self.assertEqual(missing_receipt.status, "FAILED")
        self.assertIsNone(missing_case)


if __name__ == "__main__":
    unittest.main()
