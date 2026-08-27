"""Jira issue tracker adapter implementing CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from ..models.support_case import SupportCase
from .contracts import IssueRecord
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class JiraAdapter:
    """Translation-only adapter connecting SupportMaster to Jira Cloud API."""

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
            code, payload = self.transport.request("GET", f"/rest/api/3/issue/{quote(case_id, safe='')}")
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="JIRA_FETCH_ISSUE",
                    requested_action="fetch_case",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("errorMessages", ["Issue not found"])),
                )
            fields = payload.get("fields", {})
            summary = fields.get("summary", "Untitled Jira issue")
            desc = fields.get("description", summary)
            if isinstance(desc, dict):  # Atlassian Document Format
                desc = summary
            case = SupportCase(
                case_id=payload.get("key", case_id),
                external_id=payload.get("key", case_id),
                tenant_id=self.tenant_id,
                source_system="JIRA",
                title=summary,
                description=str(desc),
                status="RECEIVED",
                customer_impact=fields.get("customfield_impact", "Standard impact"),
            )
            return ExternalOperationReceipt(
                operation_type="JIRA_FETCH_ISSUE",
                requested_action="fetch_case",
                status="SUCCEEDED",
                external_id=case_id,
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target=case_id,
            operation_type="JIRA_FETCH_ISSUE",
            requested_action="fetch_case",
            operation=operation,
        )
        return (case if receipt.status == "SUCCEEDED" else None), receipt

    def search_issues(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        items: list[IssueRecord] = []

        def operation() -> ExternalOperationReceipt:
            jql = f'text ~ "{query}"'
            code, payload = self.transport.request("GET", "/rest/api/3/search", {"jql": jql})
            if not (200 <= code < 300):
                return ExternalOperationReceipt(
                    operation_type="JIRA_SEARCH_ISSUES",
                    requested_action="search_issues",
                    status="FAILED",
                    details={"http_status": str(code)},
                    error=str(payload.get("errorMessages", ["Search failed"])),
                )
            for issue in payload.get("issues", []):
                fields = issue.get("fields", {})
                items.append(
                    IssueRecord(
                        key=issue.get("key", ""),
                        title=fields.get("summary", ""),
                        status=fields.get("status", {}).get("name", "UNKNOWN"),
                        url=issue.get("self"),
                    )
                )
            return ExternalOperationReceipt(
                operation_type="JIRA_SEARCH_ISSUES",
                requested_action="search_issues",
                status="SUCCEEDED",
                external_id=str(len(items)),
                details={"http_status": str(code)},
            )

        receipt = self.gateway.execute(
            permission="READ_ISSUES",
            target="jira_search",
            operation_type="JIRA_SEARCH_ISSUES",
            requested_action="search_issues",
            operation=operation,
            payload={"query": query},
        )
        return items if receipt.status == "SUCCEEDED" else [], receipt

    def post_comment(self, case_id: str, body: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "POST",
                f"/rest/api/3/issue/{quote(case_id, safe='')}/comment",
                {"body": body},
            )
            return ExternalOperationReceipt(
                operation_type="JIRA_COMMENT",
                requested_action="post_comment",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("errorMessages", ["Comment failed"])),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="JIRA_COMMENT",
            requested_action="post_comment",
            operation=operation,
            payload={"case_id": case_id, "body": body},
        )

    def update_case_status(self, case_id: str, status: str) -> ExternalOperationReceipt:
        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request(
                "POST",
                f"/rest/api/3/issue/{quote(case_id, safe='')}/transitions",
                {"transition": {"name": status}},
            )
            return ExternalOperationReceipt(
                operation_type="JIRA_TRANSITION",
                requested_action="update_case_status",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=case_id,
                details={"status": status, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("errorMessages", ["Transition failed"])),
            )

        return self.gateway.execute(
            permission="WRITE_ISSUES",
            target=case_id,
            operation_type="JIRA_TRANSITION",
            requested_action="update_case_status",
            operation=operation,
            payload={"case_id": case_id, "status": status},
        )
