"""Shared plumbing for HTTP-backed workspace providers.

Each concrete provider supplies endpoint builders and response parsers;
this base owns the gateway/receipt discipline so every external call is
policy-checked, circuit-broken, and receipted exactly like the other
integration adapters. Read-only: only GET requests are ever issued.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import quote

from ...models.control import ExternalOperationReceipt
from ...models.discovery import (
    ActivityEvent,
    CodeMatch,
    FileBlob,
    ProviderName,
    RepoPage,
    RepoRef,
    RepositoryDescriptor,
)
from ..http import JsonHttpTransport
from ..policy import IntegrationGateway


def _receipt(
    operation_type: str,
    requested_action: str,
    *,
    status: str = "SUCCEEDED",
    external_id: str | None = None,
    details: dict[str, str] | None = None,
    error: str | None = None,
) -> ExternalOperationReceipt:
    return ExternalOperationReceipt(
        operation_type=operation_type,
        requested_action=requested_action,
        status=status,  # type: ignore[arg-type]
        external_id=external_id,
        details=details or {},
        error=error,
    )


def _ok(code: int) -> bool:
    return 200 <= code < 300


class HttpWorkspaceProviderBase:
    """Gateway-guarded read-only access to one tenant workspace connection."""

    provider_name: ClassVar[ProviderName]

    def __init__(
        self,
        *,
        workspace_id: str,
        transport: JsonHttpTransport,
        gateway: IntegrationGateway | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.transport = transport
        self.gateway = gateway or IntegrationGateway()
        self.connection_id = f"{self.provider_name}:{workspace_id}"

    # -- hooks implemented by concrete providers ---------------------------

    def _list_repos_request(self, cursor: str | None) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _parse_repo_page(self, payload: dict[str, Any]) -> RepoPage:
        raise NotImplementedError

    def _repo_metadata_request(self, repo: str) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _parse_repository(self, ref: RepoRef, payload: dict[str, Any]) -> RepositoryDescriptor:
        raise NotImplementedError

    def _code_search_request(self, query: str, repo: str | None) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _parse_code_matches(
        self, payload: dict[str, Any], repo_hint: str | None
    ) -> list[CodeMatch]:
        raise NotImplementedError

    def _activity_requests(self, repo: str, since: datetime) -> list[tuple[str, dict[str, str], str]]:
        """Return ``(path, params, kind)`` triples for commits/PR endpoints."""
        raise NotImplementedError

    def _parse_activity(
        self, payload: dict[str, Any], kind: str, repo: str
    ) -> list[ActivityEvent]:
        raise NotImplementedError

    # -- shared execution helpers ------------------------------------------

    def _get(
        self,
        *,
        target: str,
        operation_type: str,
        path: str,
        params: dict[str, str],
    ) -> tuple[int, Any, ExternalOperationReceipt]:
        holder: dict[str, object] = {}

        def operation() -> ExternalOperationReceipt:
            code, payload = self.transport.request("GET", path, params or None)
            holder["code"] = code
            holder["payload"] = payload
            return _receipt(
                operation_type,
                "read_workspace",
                status="SUCCEEDED" if _ok(code) else "FAILED",
                details={"http_status": str(code)},
                error=None if _ok(code) else str(payload.get("error", f"HTTP {code}")),
            )

        receipt = self.gateway.execute(
            permission="READ_REPOSITORY",
            target=target,
            operation_type=operation_type,
            requested_action="read_workspace",
            operation=operation,
        )
        code = int(holder.get("code", 0))
        # Providers return JSON arrays (GitHub/GitLab listing, GitLab search)
        # as often as objects; pass the decoded payload through untouched.
        return code, holder.get("payload"), receipt

    @staticmethod
    def _encode(segment: str) -> str:
        return quote(segment, safe="")

    # -- WorkspaceProvider surface -----------------------------------------

    def list_repositories(self, *, cursor: str | None = None) -> tuple[RepoPage, ExternalOperationReceipt]:
        path, params = self._list_repos_request(cursor)
        code, payload, receipt = self._get(
            target=f"{self.connection_id}/repos",
            operation_type="WORKSPACE_LIST_REPOS",
            path=path,
            params=params,
        )
        if not _ok(code):
            return RepoPage(), receipt
        return self._parse_repo_page(payload), receipt

    def get_repository(self, repo_ref: RepoRef) -> tuple[RepositoryDescriptor | None, ExternalOperationReceipt]:
        path, params = self._repo_metadata_request(repo_ref.repo)
        code, payload, receipt = self._get(
            target=f"{self.connection_id}/{repo_ref.repo}",
            operation_type="WORKSPACE_REPO_METADATA",
            path=path,
            params=params,
        )
        if not _ok(code):
            return None, receipt
        return self._parse_repository(repo_ref, payload), receipt

    def search_code(self, repo_ref: RepoRef, query: str) -> tuple[list[CodeMatch], ExternalOperationReceipt]:
        path, params = self._code_search_request(query, repo_ref.repo)
        code, payload, receipt = self._get(
            target=f"{self.connection_id}/{repo_ref.repo}/search",
            operation_type="WORKSPACE_CODE_SEARCH",
            path=path,
            params=params,
        )
        if not _ok(code):
            return [], receipt
        matches = self._parse_code_matches(payload, repo_ref.repo)
        for match in matches:
            match.ref = repo_ref
        return matches, receipt

    def search_workspace_code(self, query: str) -> tuple[list[CodeMatch], ExternalOperationReceipt]:
        path, params = self._code_search_request(query, None)
        code, payload, receipt = self._get(
            target=f"{self.connection_id}/search",
            operation_type="WORKSPACE_CODE_SEARCH",
            path=path,
            params=params,
        )
        if _ok(code):
            return self._parse_code_matches(payload, None), receipt
        if code in {401, 403, 404, 422}:
            # Plan/tier limitation: degrade instead of failing discovery.
            degraded = _receipt(
                "WORKSPACE_CODE_SEARCH",
                "read_workspace",
                status="PARTIAL",
                details={"degraded": "fan_out_required", "http_status": str(code)},
                error="Workspace-wide code search unavailable; fan out per repo.",
            )
            return [], degraded
        return [], receipt

    def read_file(self, repo_ref: RepoRef, path: str, ref: str | None = None) -> tuple[FileBlob | None, ExternalOperationReceipt]:
        blob_path, params = self._read_file_request(repo_ref.repo, path, ref)
        code, payload, receipt = self._get(
            target=f"{self.connection_id}/{repo_ref.repo}/file",
            operation_type="WORKSPACE_READ_FILE",
            path=blob_path,
            params=params,
        )
        if not _ok(code):
            return None, receipt
        return self._parse_file_blob(repo_ref, path, ref, payload), receipt

    def _read_file_request(self, repo: str, path: str, ref: str | None) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _parse_file_blob(
        self, repo_ref: RepoRef, path: str, ref: str | None, payload: dict[str, Any]
    ) -> FileBlob:
        raise NotImplementedError

    def recent_activity(self, repo_ref: RepoRef, since: datetime) -> tuple[list[ActivityEvent], ExternalOperationReceipt]:
        events: list[ActivityEvent] = []
        last_receipt: ExternalOperationReceipt | None = None
        any_success = False
        for path, params, kind in self._activity_requests(repo_ref.repo, since.astimezone(timezone.utc)):
            code, payload, receipt = self._get(
                target=f"{self.connection_id}/{repo_ref.repo}/activity",
                operation_type="WORKSPACE_ACTIVITY",
                path=path,
                params=params,
            )
            last_receipt = receipt
            if _ok(code):
                any_success = True
                events.extend(self._parse_activity(payload, kind, repo_ref.repo))
        if last_receipt is None:
            return [], _receipt("WORKSPACE_ACTIVITY", "read_workspace", status="FAILED", error="No activity endpoints configured.")
        if not any_success:
            return [], last_receipt
        combined = _receipt(
            "WORKSPACE_ACTIVITY",
            "read_workspace",
            status="SUCCEEDED" if any_success else "FAILED",
            external_id=str(len(events)),
            details={"repo": repo_ref.repo},
        )
        return events, combined