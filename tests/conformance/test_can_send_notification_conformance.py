"""Conformance test suite for CanSendNotification capability adapters."""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import InMemoryNotificationAdapter
from supportmaster.integrations.policy import IntegrationGateway, IntegrationPolicy
from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.models.pipeline import NotificationRequest
from supportmaster.pipeline.capabilities import CanSendNotification


class CanSendNotificationConformanceTests(unittest.TestCase):
    def test_can_send_notification_contract(self) -> None:
        # Dry-run policy blocks by default
        dry_adapter = InMemoryNotificationAdapter()
        self.assertIsInstance(dry_adapter, CanSendNotification)
        blocked_receipt = dry_adapter.send_notification("System update completed", channel="ops")
        self.assertIsInstance(blocked_receipt, ExternalOperationReceipt)
        self.assertEqual(blocked_receipt.status, "BLOCKED")

        # Live allowed policy
        live_gateway = IntegrationGateway(
            policy=IntegrationPolicy(
                mode="LIVE",
                allowed_permissions=["SEND_NOTIFICATIONS"],
            )
        )
        adapter = InMemoryNotificationAdapter(gateway=live_gateway)
        req = NotificationRequest(channel="ops", message="Build succeeded", severity="INFO")
        receipt = adapter.send_notification(req)
        self.assertIsInstance(receipt, ExternalOperationReceipt)
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(receipt.external_id, "ops")


if __name__ == "__main__":
    unittest.main()
