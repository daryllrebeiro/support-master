"""Conformance test suite for CanReadMonitoringSignal capability adapters."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from supportmaster.integrations.adapters import InMemoryMonitoringAdapter
from supportmaster.integrations.contracts import IncidentRecord, MetricSample
from supportmaster.models.control import ExternalOperationReceipt
from supportmaster.pipeline.capabilities import CanReadMonitoringSignal


class CanReadMonitoringConformanceTests(unittest.TestCase):
    def test_can_read_monitoring_contract(self) -> None:
        incident = IncidentRecord(
            incident_id="INC-404",
            service="checkout",
            severity="HIGH",
            summary="Payment gateway timeout",
            started_at=datetime.now(timezone.utc),
        )
        sample = MetricSample(
            metric="error_rate",
            value=0.08,
            dimensions={"service": "checkout"},
        )
        adapter = InMemoryMonitoringAdapter(incidents=[incident], metrics=[sample])
        self.assertIsInstance(adapter, CanReadMonitoringSignal)

        incidents, inc_receipt = adapter.incidents("checkout")
        self.assertIsInstance(inc_receipt, ExternalOperationReceipt)
        self.assertEqual(inc_receipt.status, "SUCCEEDED")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].incident_id, "INC-404")

        metrics, met_receipt = adapter.metric("error_rate", service="checkout")
        self.assertIsInstance(met_receipt, ExternalOperationReceipt)
        self.assertEqual(met_receipt.status, "SUCCEEDED")
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0].value, 0.08)


if __name__ == "__main__":
    unittest.main()
