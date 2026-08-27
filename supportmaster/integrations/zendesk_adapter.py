"""Zendesk ticket adapter implementing CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from ..models.support_case import SupportCase
from .contracts import IssueRecord
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class ZendeskAdapter:
    """Translation-only adapter connecting SupportMaster to Zendesk Support API."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        gateway: IntegrationGateway | None = None,
        tenant_id: str = "default",
    ) -> None:
        self.transport = transport
        self.gateway = gateway or IntegrationGateway()
        self.tenant_id = tenant_id

    def fetch_case(self, case_id: str) -> tuple[SupportCase | None, ExternalOperationReceipt]:
        case: SupportCase | None = None

        def operation() -> ExternalOperationReceipt:
            nonlocal case
            code, payload = self.transport.request("GET", f"/api/v2/tickets/{quote(case_id, safe='')}.json")
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="ZENDESK_FETCH_TICKET",
                    requested_action="fetch_case",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("error", "Ticket not found")),
                )
            ticket = payload.get("ticket", {})
            subject = ticket.get("subject", "Untitled Zendesk ticket")
            desc = ticket.get("description", subject)
            case = SupportCase(
                case_id=str(ticket.get("id", case_id)),
                external_id=str(ticket.get("id", case_id)),
                tenant_id=self.tenant_id,
                source_system="ZENDESK",
                title=subject,
                description=str(desc),
                status="RECEIVED",
                customer_impact=ticket.get("priority", "normal"),
            )
            return ExternalOperationReceipt(
                operation_type="ZENDESK_FETCH_TICKET",
                requested_action="fetch_case",
                status="SUCCEEDED",
                external_id=case_id,
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target=case_id,
            operation_type="ZENDESK_FETCH_TICKET",
            requested_action="fetch_case",
            operation=operation,
        )
        return (case if receipt.status == "SUCCEEDED" else None), receipt

    def search_issues(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        items: list[IssueRecord] = []

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request("GET", "/api/v2/search.json", {"query": f"type:ticket {query}"})
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="ZENDESK_SEARCH",
                    requested_action="search_issues",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("error", "Search failed")),
                )
            for ticket in payload.get("results", []):
                items.append(
                    IssueRecord(
                        key=str(ticket.get("id", "")),
                        title=ticket.get("subject", ""),
                        status=ticket.get("status", "UNKNOWN"),
                        url=ticket.get("url"),
                    )
                )
            return ExternalOperationReceipt(
                operation_type="ZENDESK_SEARCH",
                requested_action="search_issues",
                status="SUCCEEDED",
                external_id=str(len(items)),
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target="zendesk_search",
            operation_type="ZENDESK_SEARCH",
            requested_action="search_issues",
            operation=operation,
            payload={"query": query},
        )
        return items if receipt.status == "SUCCEEDED" else [], receipt

    def post_comment(self, case_id: str, body: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "PUT",
                f"/api/v2/tickets/{quote(case_id, safe='')}.json",
                {"ticket": {"comment": {"body": body, "public": False}}},
            )
            return ExternalOperationReceipt(
                operation_type="ZENDESK_COMMENT",
                requested_action="post_comment",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Comment failed")),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="ZENDESK_COMMENT",
            requested_action="post_comment",
            operation=operation,
            payload={"case_id": case_id, "body": body},
        )

    def update_case_status(self, case_id: str, status: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "PUT",
                f"/api/v2/tickets/{quote(case_id, safe='')}.json",
                {"ticket": {"status": status.lower()}},
            )
            return ExternalOperationReceipt(
                operation_type="ZENDESK_STATUS_UPDATE",
                requested_action="update_case_status",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"status": status, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Status update failed")),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="ZENDESK_STATUS_UPDATE",
            requested_action="update_case_status",
            operation=operation,
            payload={"case_id": case_id, "status": status},
        )
