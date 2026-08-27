"""Conformance test suite for CanReadCIStatus capability adapters."""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import InMemoryCIAdapter
from supportmaster.integrations.contracts import CIStatus
from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.pipeline.capabilities import CanReadCIStatus, CanTriggerCI


class CanReadCIConformanceTests(unittest.TestCase):
    def test_can_read_ci_and_trigger_contract(self) -> None:
        from supportmaster.integrations.policy import IntegrationGateway, IntegrationPolicy

        # Default dry-run blocks trigger
        dry_adapter = InMemoryCIAdapter()
        self.assertIsInstance(dry_adapter, CanReadCIStatus)
        self.assertIsInstance(dry_adapter, CanTriggerCI)

        blocked_id, blocked_receipt = dry_adapter.trigger_ci("main-ci", commit_sha="abc1234")
        self.assertEqual(blocked_receipt.status, "BLOCKED")
        self.assertIsNone(blocked_id)

        # Live policy allows trigger
        live_gateway = IntegrationGateway(
            policy=IntegrationPolicy(mode="LIVE", allowed_permissions=["TRIGGER_CI", "READ_CI"])
        )
        adapter = InMemoryCIAdapter(gateway=live_gateway)
        run_id, trigger_receipt = adapter.trigger_ci("main-ci", commit_sha="abc1234")
        self.assertIsInstance(trigger_receipt, ExternalOperationReceipt)
        self.assertEqual(trigger_receipt.status, "SUCCEEDED")
        self.assertIsNotNone(run_id)

        status, status_receipt = adapter.read_ci_status(run_id)  # type: ignore[arg-type]
        self.assertIsInstance(status_receipt, ExternalOperationReceipt)
        self.assertEqual(status_receipt.status, "SUCCEEDED")
        self.assertIsInstance(status, CIStatus)
        self.assertEqual(status.status, "QUEUED")


if __name__ == "__main__":
    unittest.main()
