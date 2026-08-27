"""GitLab CI adapter implementing CanTriggerCI and CanReadCIStatus."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

from ..models.control import ExternalOperationReceipt
from .contracts import CIStatus
from .http import JsonHttpTransport
from .policy import IntegrationGateway


class GitLabCIAdapter:
    """Translation-only adapter connecting SupportMaster to GitLab Pipelines API."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        project_id: str,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.transport = transport
        self.project_id = project_id
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
                f"/api/v4/projects/{quote(self.project_id, safe='')}/pipeline",
                {"ref": commit_sha, "variables": [{"key": k, "value": v} for k, v in (parameters or {}).items()]},
            )
            run_id = str(payload.get("id", commit_sha))
            return ExternalOperationReceipt(
                operation_type="GITLAB_CI_TRIGGER",
                requested_action="trigger_pipeline",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=run_id,
                details={"pipeline": pipeline, "commit_sha": commit_sha, "http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("message", "Trigger failed")),
            )

        receipt = self.gateway.execute(
            permission="TRIGGER_CI",
            target=f"{self.project_id}/{pipeline}",
            operation_type="GITLAB_CI_TRIGGER",
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
                f"/api/v4/projects/{quote(self.project_id, safe='')}/pipelines/{quote(run_id, safe='')}",
            )
            if 200 <= code < 300:
                raw_status = payload.get("status", "").lower()
                status_map = {
                    "success": "PASSED",
                    "failed": "FAILED",
                    "running": "RUNNING",
                    "pending": "QUEUED",
                    "canceled": "CANCELLED",
                }
                status_mapped = status_map.get(raw_status, "UNKNOWN")
                ci_status = CIStatus(
                    run_id=run_id,
                    status=status_mapped,  # type: ignore[arg-type]
                    url=payload.get("web_url"),
                    commit_sha=payload.get("sha"),
                )
            return ExternalOperationReceipt(
                operation_type="GITLAB_CI_STATUS",
                requested_action="read_pipeline_status",
                status="SUCCEEDED" if 200 <= code < 300 else "FAILED",
                external_id=run_id,
                details={"http_status": str(code)},
                error=None if 200 <= code < 300 else str(payload.get("message", "Read status failed")),
            )

        receipt = self.gateway.execute(
            permission="READ_CI",
            target=f"{self.project_id}/{run_id}",
            operation_type="GITLAB_CI_STATUS",
            requested_action="read_pipeline_status",
            operation=operation,
        )
        return ci_status, receipt
