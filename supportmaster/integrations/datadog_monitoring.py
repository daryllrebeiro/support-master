"""Datadog monitoring adapter implementing CanReadMonitoringSignal."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from .contracts import IncidentRecord, MetricSample
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class DatadogMonitoringAdapter:
    """Translation-only adapter connecting SupportMaster to Datadog metrics and events API."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.transport = transport
        self.gateway = gateway or IntegrationGateway()

    def incidents(self, service: str) -> tuple[list[IncidentRecord], ExternalOperationReceipt]:
        records: list[IncidentRecord] = []

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "GET",
                "/api/v1/events",
                {"tags": f"service:{service},type:incident"},
            )
            if 200 <= code < 300:
                for ev in payload.get("events", []):
                    records.append(
                        IncidentRecord(
                            incident_id=str(ev.get("id", "")),
                            service=service,
                            severity=ev.get("alert_type", "warning").upper(),
                            summary=ev.get("text", ev.get("title", "")),
                            url=ev.get("url"),
                            started_at=datetime.fromtimestamp(ev.get("date_happened", 0), tz=timezone.utc),
                        )
                    )
            return ExternalOperationReceipt(
                operation_type="DATADOG_INCIDENTS",
                requested_action="read_incidents",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=str(len(records)),
                details={"service": service, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("errors", ["Events query failed"])),
            )

        receipt = self.gateway.execute(
            permission="READ_MONITORING",
            target=service,
            operation_type="DATADOG_INCIDENTS",
            requested_action="read_incidents",
            operation=operation,
        )
        return records if receipt.status == "SUCCEEDED" else [], receipt

    def metric(self, name: str, *, service: str) -> tuple[list[MetricSample], ExternalOperationReceipt]:
        samples: list[MetricSample] = []

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "GET",
                "/api/v1/query",
                {"query": f"avg:{name}{{service:{service}}}"},
            )
            if 200 <= code < 300:
                for series in payload.get("series", []):
                    for point in series.get("pointlist", []):
                        if len(point) >= 2 and point[1] is not None:
                            samples.append(
                                MetricSample(
                                    metric=name,
                                    value=float(point[1]),
                                    observed_at=datetime.fromtimestamp(point[0] / 1000.0, tz=timezone.utc),
                                    dimensions={"service": service},
                                )
                            )
            return ExternalOperationReceipt(
                operation_type="DATADOG_METRIC",
                requested_action="read_metric",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=str(len(samples)),
                details={"metric": name, "service": service, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Metric query failed")),
            )

        receipt = self.gateway.execute(
            permission="READ_MONITORING",
            target=service,
            operation_type="DATADOG_METRIC",
            requested_action="read_metric",
            operation=operation,
        )
        return samples if receipt.status == "SUCCEEDED" else [], receipt
