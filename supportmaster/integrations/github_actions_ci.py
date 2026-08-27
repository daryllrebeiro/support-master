"""GitHub Actions CI adapter implementing CanTriggerCI and CanReadCIStatus."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from .contracts import CIStatus
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class GitHubActionsCIAdapter:
    """Translation-only adapter connecting SupportMaster to GitHub Actions workflow dispatch API."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        owner: str,
        repo: str,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.transport = transport
        self.owner = owner
        self.repo = repo
        self.gateway = gateway or IntegrationGateway()

    def trigger_ci(
        self,
        pipeline: str,
        *,
        commit_sha: str,
        parameters: Mapping[str, str] | None = None,
    ) -> tuple[str | None, ExternalOperationReceipt]:
        run_id: str | None = None

        def operation() -> ExternalOperationReceipt:
            nonlocal run_id
            code, payload = self.transport.request(
                "POST",
                f"/repos/{self.owner}/{self.repo}/actions/workflows/{quote(pipeline, safe='')}/dispatches",
                {"ref": commit_sha, "inputs": dict(parameters or {})},
            )
            run_id = str(payload.get("id", commit_sha))
            return ExternalOperationReceipt(
                operation_type="GITHUB_ACTIONS_TRIGGER",
                requested_action="trigger_pipeline",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=run_id,
                details={"pipeline": pipeline, "commit_sha": commit_sha, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("message", "Trigger failed")),
            )

        receipt = self.gateway.execute(
            permission="TRIGGER_CI",
            target=f"{self.owner}/{self.repo}/{pipeline}",
            operation_type="GITHUB_ACTIONS_TRIGGER",
            requested_action="trigger_pipeline",
            operation=operation,
            payload={"pipeline": pipeline, "commit_sha": commit_sha},
        )
        return (run_id if receipt.status == "SUCCEEDED" else None), receipt

    def read_ci_status(self, run_id: str) -> tuple[CIStatus, ExternalOperationReceipt]:
        ci_status = CIStatus(run_id=run_id, status="UNKNOWN")

        def operation() -> ExternalOperationReceipt:
            nonlocal ci_status
            code, payload = self.transport.request(
                "GET",
                f"/repos/{self.owner}/{self.repo}/actions/runs/{quote(run_id, safe='')}",
            )
            if 200 <= code < 300:
                raw_status = payload.get("status", "")
                raw_conclusion = payload.get("conclusion", "")
                if raw_status == "completed":
                    status_mapped = "PASSED" if raw_conclusion == "success" else "FAILED"
                elif raw_status in {"in_progress", "queued"}:
                    status_mapped = "RUNNING" if raw_status == "in_progress" else "QUEUED"
                else:
                    status_mapped = "UNKNOWN"

                ci_status = CIStatus(
                    run_id=run_id,
                    status=status_mapped,
                    url=payload.get("html_url"),
                    commit_sha=payload.get("head_sha"),
                )
            return ExternalOperationReceipt(
                operation_type="GITHUB_ACTIONS_STATUS",
                requested_action="read_pipeline_status",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=run_id,
                details={"http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("message", "Read status failed")),
            )

        receipt = self.gateway.execute(
            permission="READ_CI",
            target=f"{self.owner}/{self.repo}/{run_id}",
            operation_type="GITHUB_ACTIONS_STATUS",
            requested_action="read_pipeline_status",
            operation=operation,
        )
        return ci_status, receipt
