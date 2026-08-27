"""Linear issue tracker adapter implementing CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from ..models.support_case import SupportCase
from .contracts import IssueRecord
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class LinearAdapter:
    """Translation-only adapter connecting SupportMaster to Linear GraphQL/REST API."""

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
            code, payload = self.transport.request("GET", f"/issues/{quote(case_id, safe='')}")
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="LINEAR_FETCH_ISSUE",
                    requested_action="fetch_case",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("error", "Issue not found")),
                )
            title = payload.get("title", "Untitled Linear issue")
            desc = payload.get("description", title)
            case = SupportCase(
                case_id=payload.get("identifier", case_id),
                external_id=payload.get("id", case_id),
                tenant_id=self.tenant_id,
                source_system="LINEAR",
                title=title,
                description=str(desc),
                status="RECEIVED",
                customer_impact=payload.get("impact", "Standard impact"),
            )
            return ExternalOperationReceipt(
                operation_type="LINEAR_FETCH_ISSUE",
                requested_action="fetch_case",
                status="SUCCEEDED",
                external_id=case_id,
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target=case_id,
            operation_type="LINEAR_FETCH_ISSUE",
            requested_action="fetch_case",
            operation=operation,
        )
        return (case if receipt.status == "SUCCEEDED" else None), receipt

    def search_issues(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        items: list[IssueRecord] = []

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request("GET", "/issues", {"query": query})
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="LINEAR_SEARCH_ISSUES",
                    requested_action="search_issues",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("error", "Search failed")),
                )
            for issue in payload.get("data", payload.get("items", [])):
                items.append(
                    IssueRecord(
                        key=issue.get("identifier", issue.get("id", "")),
                        title=issue.get("title", ""),
                        status=issue.get("state", {}).get("name", "UNKNOWN") if isinstance(issue.get("state"), dict) else str(issue.get("state", "UNKNOWN")),
                        url=issue.get("url"),
                    )
                )
            return ExternalOperationReceipt(
                operation_type="LINEAR_SEARCH_ISSUES",
                requested_action="search_issues",
                status="SUCCEEDED",
                external_id=str(len(items)),
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target="linear_search",
            operation_type="LINEAR_SEARCH_ISSUES",
            requested_action="search_issues",
            operation=operation,
            payload={"query": query},
        )
        return items if receipt.status == "SUCCEEDED" else [], receipt

    def post_comment(self, case_id: str, body: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "POST",
                f"/issues/{quote(case_id, safe='')}/comments",
                {"body": body},
            )
            return ExternalOperationReceipt(
                operation_type="LINEAR_COMMENT",
                requested_action="post_comment",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Comment failed")),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="LINEAR_COMMENT",
            requested_action="post_comment",
            operation=operation,
            payload={"case_id": case_id, "body": body},
        )

    def update_case_status(self, case_id: str, status: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "PATCH",
                f"/issues/{quote(case_id, safe='')}",
                {"state": status},
            )
            return ExternalOperationReceipt(
                operation_type="LINEAR_UPDATE_STATUS",
                requested_action="update_case_status",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"status": status, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("error", "Status update failed")),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="LINEAR_UPDATE_STATUS",
            requested_action="update_case_status",
            operation=operation,
            payload={"case_id": case_id, "status": status},
        )
