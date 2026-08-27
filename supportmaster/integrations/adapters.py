"""Injectable integration adapters with no implicit network access."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from ..models.control import ExternalOperationReceipt
from .contracts import CIStatus, IncidentRecord, IssueRecord, MetricSample
from .policy import IntegrationGateway


class IssueTrackerAdapter(Protocol):
    def search(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        ...

    def add_comment(self, issue_key: str, body: str) -> ExternalOperationReceipt:
        ...


class CIAdapter(Protocol):
    def trigger(self, pipeline: str, *, commit_sha: str, parameters: Mapping[str, str] | None = None) -> ExternalOperationReceipt:
        ...

    def status(self, run_id: str) -> tuple[CIStatus, ExternalOperationReceipt]:
        ...


class MonitoringAdapter(Protocol):
    def incidents(self, service: str) -> tuple[list[IncidentRecord], ExternalOperationReceipt]:
        ...

    def metric(self, name: str, *, service: str) -> tuple[list[MetricSample], ExternalOperationReceipt]:
        ...


class NotificationAdapter(Protocol):
    def send(self, channel: str, message: str) -> ExternalOperationReceipt:
        ...


class InMemoryIssueTrackerAdapter:
    """Deterministic issue adapter for tests and local dry-run demonstrations."""

    def __init__(
        self,
        issues: Sequence[IssueRecord] = (),
        *,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.gateway = gateway or IntegrationGateway()
        self.issues = {issue.key: issue for issue in issues}
        self.comments: list[dict[str, str]] = []

    def search(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        matches: list[IssueRecord] = []

        def operation() -> ExternalOperationReceipt:
            normalized = query.casefold()
            matches.extend(
                issue
                for issue in self.issues.values()
                if normalized in f"{issue.key} {issue.title}".casefold()
            )
            return ExternalOperationReceipt(
                operation_type="ISSUE_SEARCH",
                requested_action="search_issues",
                status="SUCCEEDED",
                external_id=str(len(matches)),
                details={"query_length": str(len(query))},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target="issue_tracker",
            operation_type="ISSUE_SEARCH",
            requested_action="search_issues",
            operation=operation,
            payload={"query": query},
        )
        return matches if receipt.status == "SUCCEEDED" else [], receipt

    def add_comment(self, issue_key: str, body: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            if issue_key not in self.issues:
                return ExternalOperationReceipt(
                    operation_type="ISSUE_COMMENT",
                    requested_action="add_comment",
                    status="FAILED",
                    error="Issue does not exist.",
                )
            self.comments.append({"issue_key": issue_key, "body": body})
            return ExternalOperationReceipt(
                operation_type="ISSUE_COMMENT",
                requested_action="add_comment",
                status="SUCCEEDED",
                external_id=issue_key,
                details={"body_bytes": str(len(body.encode("utf-8")))},
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=issue_key,
            operation_type="ISSUE_COMMENT",
            requested_action="add_comment",
            operation=operation,
            payload={"issue_key": issue_key, "body": body},
        )

    # -- Capability Protocol compatibility --------------------------------
    def search_issues(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        return self.search(query)

    def post_comment(self, case_id: str, body: str) -> ExternalOperationReceipt:
        return self.add_comment(case_id, body)

    def fetch_case(self, case_id: str):
        from ..models.support_case import SupportCase

        issue = self.issues.get(case_id)
        if issue is None:
            receipt = ExternalOperationReceipt(
                operation_type="ISSUE_FETCH",
                requested_action="fetch_case",
                status="FAILED",
                error=f"Issue {case_id} does not exist.",
            )
            return None, receipt

        case = SupportCase(
            case_id=issue.key,
            external_id=issue.key,
            title=issue.title,
            description=issue.title,
            customer_impact="Customer reported issue.",
            status="RECEIVED",
        )
        receipt = ExternalOperationReceipt(
            operation_type="ISSUE_FETCH",
            requested_action="fetch_case",
            status="SUCCEEDED",
            external_id=issue.key,
        )
        return case, receipt

    def update_case_status(self, case_id: str, status: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            if case_id not in self.issues:
                return ExternalOperationReceipt(
                    operation_type="ISSUE_STATUS_UPDATE",
                    requested_action="update_case_status",
                    status="FAILED",
                    error=f"Issue {case_id} does not exist.",
                )
            issue = self.issues[case_id]
            self.issues[case_id] = IssueRecord(
                key=issue.key,
                title=issue.title,
                status=status,
                url=issue.url,
                labels=issue.labels,
                metadata=issue.metadata,
            )
            return ExternalOperationReceipt(
                operation_type="ISSUE_STATUS_UPDATE",
                requested_action="update_case_status",
                status="SUCCEEDED",
                external_id=case_id,
                details={"status": status},
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="ISSUE_STATUS_UPDATE",
            requested_action="update_case_status",
            operation=operation,
            payload={"case_id": case_id, "status": status},
        )


class InMemoryCIAdapter:
    """Deterministic CI adapter with explicit trigger permissions."""

    def __init__(self, *, gateway: IntegrationGateway | None = None) -> None:
        self.gateway = gateway or IntegrationGateway()
        self.runs: dict[str, CIStatus] = {}

    def trigger(
        self,
        pipeline: str,
        *,
        commit_sha: str,
        parameters: Mapping[str, str] | None = None,
    ) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            run_id = str(uuid4())
            self.runs[run_id] = CIStatus(
                run_id=run_id,
                status="QUEUED",
                commit_sha=commit_sha,
                details={"pipeline": pipeline, **dict(parameters or {})},
            )
            return ExternalOperationReceipt(
                operation_type="CI_TRIGGER",
                requested_action="trigger_pipeline",
                status="SUCCEEDED",
                external_id=run_id,
                details={"pipeline": pipeline, "commit_sha": commit_sha},
            )

        return self.gateway.execute(
            permission="TRIGGER_CI",
            target=pipeline,
            operation_type="CI_TRIGGER",
            requested_action="trigger_pipeline",
            operation=operation,
            payload={"pipeline": pipeline, "commit_sha": commit_sha, "parameters": dict(parameters or {})},
        )

    def trigger_ci(
        self,
        pipeline: str,
        *,
        commit_sha: str,
        parameters: Mapping[str, str] | None = None,
    ) -> tuple[str | None, ExternalOperationReceipt]:
        receipt = self.trigger(pipeline, commit_sha=commit_sha, parameters=parameters)
        return receipt.external_id if receipt.status == "SUCCEEDED" else None, receipt

    def status(self, run_id: str) -> tuple[CIStatus, ExternalOperationReceipt]:
        status = self.runs.get(run_id, CIStatus(run_id=run_id, status="UNKNOWN"))

        def operation() -> ExternalOperationReceipt:
            return ExternalOperationReceipt(
                operation_type="CI_STATUS",
                requested_action="read_pipeline_status",
                status="SUCCEEDED" if run_id in self.runs else "FAILED",
                external_id=run_id,
                error=None if run_id in self.runs else "CI run does not exist.",
            )

        receipt = self.gateway.execute(
            permission="READ_CI",
            target=run_id,
            operation_type="CI_STATUS",
            requested_action="read_pipeline_status",
            operation=operation,
        )
        return status if receipt.status == "SUCCEEDED" else CIStatus(run_id=run_id, status="UNKNOWN"), receipt

    def read_ci_status(self, run_id: str) -> tuple[CIStatus, ExternalOperationReceipt]:
        return self.status(run_id)


class InMemoryMonitoringAdapter:
    """Deterministic monitoring adapter for evidence collection."""

    def __init__(
        self,
        incidents: Sequence[IncidentRecord] = (),
        metrics: Sequence[MetricSample] = (),
        *,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.gateway = gateway or IntegrationGateway()
        self._incidents = list(incidents)
        self._metrics = list(metrics)

    def incidents(self, service: str) -> tuple[list[IncidentRecord], ExternalOperationReceipt]:
        matches = [incident for incident in self._incidents if incident.service == service]

        def operation() -> ExternalOperationReceipt:
            return ExternalOperationReceipt(
                operation_type="MONITORING_INCIDENTS",
                requested_action="read_incidents",
                status="SUCCEEDED",
                external_id=str(len(matches)),
                details={"service": service},
            )

        receipt = self.gateway.execute(
            permission="READ_MONITORING",
            target=service,
            operation_type="MONITORING_INCIDENTS",
            requested_action="read_incidents",
            operation=operation,
        )
        return matches if receipt.status == "SUCCEEDED" else [], receipt

    def metric(self, name: str, *, service: str) -> tuple[list[MetricSample], ExternalOperationReceipt]:
        matches = [
            metric
            for metric in self._metrics
            if metric.metric == name and metric.dimensions.get("service", service) == service
        ]

        def operation() -> ExternalOperationReceipt:
            return ExternalOperationReceipt(
                operation_type="MONITORING_METRIC",
                requested_action="read_metric",
                status="SUCCEEDED",
                external_id=str(len(matches)),
                details={"metric": name, "service": service},
            )

        receipt = self.gateway.execute(
            permission="READ_MONITORING",
            target=service,
            operation_type="MONITORING_METRIC",
            requested_action="read_metric",
            operation=operation,
        )
        return matches if receipt.status == "SUCCEEDED" else [], receipt


class InMemoryNotificationAdapter:
    """Notification adapter that is blocked by default in dry-run mode."""

    def __init__(self, *, gateway: IntegrationGateway | None = None) -> None:
        self.gateway = gateway or IntegrationGateway()
        self.messages: list[dict[str, str]] = []

    def send(self, channel: str, message: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            self.messages.append({"channel": channel, "message": message})
            return ExternalOperationReceipt(
                operation_type="NOTIFICATION_SEND",
                requested_action="send_notification",
                status="SUCCEEDED",
                external_id=channel,
                details={"message_bytes": str(len(message.encode("utf-8")))},
            )

        return self.gateway.execute(
            permission="SEND_NOTIFICATIONS",
            target=channel,
            operation_type="NOTIFICATION_SEND",
            requested_action="send_notification",
            operation=operation,
            payload={"channel": channel, "message": message},
        )

    def send_notification(
        self, request: str | Any, channel: str | None = None
    ) -> ExternalOperationReceipt:
        if isinstance(request, str):
            target_channel = channel or "general"
            msg = request
        else:
            target_channel = getattr(request, "channel", channel or "general")
            msg = getattr(request, "message", str(request))
        return self.send(target_channel, msg)
