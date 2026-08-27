"""Provider-neutral read-only workspace access contract.

``WorkspaceProvider`` mirrors the existing injected-adapter pattern
(``IssueTrackerAdapter``, ``CIAdapter``, ...): a structural ``Protocol``
whose every method funnels through an ``IntegrationGateway`` so each call
returns an ``ExternalOperationReceipt`` and inherits DRY_RUN, payload-cap,
circuit-breaker, and telemetry behavior.

This phase is strictly read-only; there is deliberately no write method.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import ClassVar, Iterable

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
from ..policy import IntegrationGateway


class WorkspaceProvider:
    """Structural base describing the read-only workspace surface."""

    provider_name: ClassVar[ProviderName]
    connection_id: str


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


class FakeWorkspaceProvider:
    """Deterministic in-memory provider for tests and offline golden paths.

    Constructed from plain Python structures — no network. ``fail_next``
    scripts the next N calls to fail so circuit-breaker behavior can be
    exercised without monkeypatching.
    """

    provider_name: ClassVar[ProviderName] = "github"

    def __init__(
        self,
        *,
        provider_name: ProviderName = "github",
        workspace_id: str,
        repositories: Iterable[RepositoryDescriptor] = (),
        files: dict[tuple[str, str], str] | None = None,
        code_matches: list[CodeMatch] | None = None,
        activity: list[ActivityEvent] | None = None,
        gateway: IntegrationGateway | None = None,
        fail_next: int = 0,
        degrade_workspace_search: bool = False,
    ) -> None:
        self.provider_name = provider_name  # type: ignore[assignment]
        self.workspace_id = workspace_id
        self.connection_id = f"{provider_name}:{workspace_id}"
        self.gateway = gateway or IntegrationGateway()
        self.repositories = {descriptor.ref.repo: descriptor for descriptor in repositories}
        self.files = {key: value for key, value in (files or {}).items()}
        self.code_matches = list(code_matches or [])
        self.activity = list(activity or [])
        self.fail_next = fail_next
        self.degrade_workspace_search = degrade_workspace_search
        self.calls_made = 0

    # -- internal helpers -------------------------------------------------

    def _maybe_fail(self) -> ExternalOperationReceipt | None:
        """Consume one scripted failure, if any, as a FAILED receipt."""
        if self.fail_next > 0:
            self.fail_next -= 1
            return _receipt(
                "WORKSPACE_READ",
                "read_workspace",
                status="FAILED",
                error="Scripted provider failure.",
            )
        return None

    def _execute(self, target: str, operation_type: str, operation):
        return self.gateway.execute(
            permission="READ_REPOSITORY",
            target=target,
            operation_type=operation_type,
            requested_action="read_workspace",
            operation=operation,
        )

    @staticmethod
    def _ok(receipt: ExternalOperationReceipt) -> bool:
        return receipt.status == "SUCCEEDED"

    # -- WorkspaceProvider surface ----------------------------------------

    def list_repositories(self, *, cursor: str | None = None) -> tuple[RepoPage, ExternalOperationReceipt]:
        failure = self._maybe_fail()
        if failure is not None:
            return RepoPage(), failure

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            ordered = sorted(self.repositories.values(), key=lambda item: item.ref.repo)
            start = int(cursor) if cursor else 0
            page_size = 2
            window = ordered[start : start + page_size]
            next_cursor = (
                str(start + page_size) if start + page_size < len(ordered) else None
            )
            return _receipt(
                "WORKSPACE_LIST_REPOS",
                "list_repositories",
                external_id=str(len(window)),
                details={"cursor": cursor or "", "next": next_cursor or ""},
            ), RepoPage(repositories=list(window), next_cursor=next_cursor)

        result: tuple[ExternalOperationReceipt, RepoPage] | None = None

        def wrapped() -> ExternalOperationReceipt:
            nonlocal result
            receipt, page = operation()
            result = (receipt, page)
            return receipt

        receipt = self._execute(f"{self.connection_id}/repos", "WORKSPACE_LIST_REPOS", wrapped)
        page = result[1] if result is not None and self._ok(receipt) else RepoPage()
        return page, receipt

    def get_repository(self, repo_ref: RepoRef) -> tuple[RepositoryDescriptor | None, ExternalOperationReceipt]:
        failure = self._maybe_fail()
        if failure is not None:
            return None, failure
        holder: dict[str, object] = {}

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            descriptor = self.repositories.get(repo_ref.repo)
            holder["descriptor"] = descriptor
            return _receipt(
                "WORKSPACE_REPO_METADATA",
                "get_repository",
                status="SUCCEEDED" if descriptor else "FAILED",
                external_id=repo_ref.repo,
                error=None if descriptor else "Repository does not exist.",
            )

        receipt = self._execute(f"{self.connection_id}/{repo_ref.repo}", "WORKSPACE_REPO_METADATA", operation)
        descriptor = holder.get("descriptor")
        return (descriptor if isinstance(descriptor, RepositoryDescriptor) and self._ok(receipt) else None), receipt

    def search_code(
        self,
        repo_ref_or_query: RepoRef | str,
        query_or_repos: str | Sequence[str] | None = None,
        *,
        repos: Sequence[str] | None = None,
    ) -> tuple[list[CodeMatch], ExternalOperationReceipt]:
        if isinstance(repo_ref_or_query, str):
            query = repo_ref_or_query
            filter_repos = repos if repos is not None else query_or_repos
            allowed_repos = set(filter_repos) if isinstance(filter_repos, (list, tuple, set)) else None
            failure = self._maybe_fail()
            if failure is not None:
                return [], failure
            matches: list[CodeMatch] = []

            def operation() -> ExternalOperationReceipt:
                self.calls_made += 1
                normalized = query.casefold()
                matches.extend(
                    match
                    for match in self.code_matches
                    if (allowed_repos is None or match.ref.repo in allowed_repos)
                    and (
                        normalized in match.path.casefold()
                        or normalized in match.snippet.casefold()
                        or not normalized.strip()
                    )
                )
                return _receipt(
                    "WORKSPACE_CODE_SEARCH",
                    "search_code",
                    external_id=str(len(matches)),
                    details={"query": query},
                )

            receipt = self._execute(f"{self.connection_id}/search", "WORKSPACE_CODE_SEARCH", operation)
            return (matches if self._ok(receipt) else []), receipt

        # Legacy RepoRef first argument
        repo_ref = repo_ref_or_query
        query = str(query_or_repos or "")
        failure = self._maybe_fail()
        if failure is not None:
            return [], failure
        matches: list[CodeMatch] = []

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            normalized = query.casefold()
            matches.extend(
                match
                for match in self.code_matches
                if match.ref.repo == repo_ref.repo
                and (
                    normalized in match.path.casefold()
                    or normalized in match.snippet.casefold()
                    or not normalized.strip()
                )
            )
            return _receipt(
                "WORKSPACE_CODE_SEARCH",
                "search_code",
                external_id=str(len(matches)),
                details={"repo": repo_ref.repo},
            )

        receipt = self._execute(f"{self.connection_id}/{repo_ref.repo}/search", "WORKSPACE_CODE_SEARCH", operation)
        return (matches if self._ok(receipt) else []), receipt

    def search_workspace_code(self, query: str) -> tuple[list[CodeMatch], ExternalOperationReceipt]:
        failure = self._maybe_fail()
        if failure is not None:
            return [], failure
        if self.degrade_workspace_search:
            receipt = _receipt(
                "WORKSPACE_CODE_SEARCH",
                "search_workspace_code",
                status="PARTIAL",
                details={"degraded": "fan_out_required"},
                error="Workspace-wide search unavailable on this plan.",
            )
            return [], receipt
        matches: list[CodeMatch] = []

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            normalized = query.casefold()
            matches.extend(
                match
                for match in self.code_matches
                if normalized in match.path.casefold()
                or normalized in match.snippet.casefold()
                or not normalized.strip()
            )
            return _receipt(
                "WORKSPACE_CODE_SEARCH",
                "search_workspace_code",
                external_id=str(len(matches)),
            )

        receipt = self._execute(f"{self.connection_id}/search", "WORKSPACE_CODE_SEARCH", operation)
        return (matches if self._ok(receipt) else []), receipt

    def read_file(
        self,
        repo_ref_or_name: RepoRef | str,
        path: str,
        ref: str | None = None,
    ) -> tuple[FileBlob | None, ExternalOperationReceipt]:
        repo_name = repo_ref_or_name.repo if isinstance(repo_ref_or_name, RepoRef) else str(repo_ref_or_name)
        repo_ref = (
            repo_ref_or_name
            if isinstance(repo_ref_or_name, RepoRef)
            else RepoRef(provider=self.provider_name, workspace=self.workspace_id, repo=repo_name)
        )
        failure = self._maybe_fail()
        if failure is not None:
            return None, failure
        holder: dict[str, object] = {}

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            content = self.files.get((repo_name, path))
            holder["blob"] = FileBlob(ref=repo_ref, path=path, ref_name=ref, content=content or "")
            return _receipt(
                "WORKSPACE_READ_FILE",
                "read_file",
                status="SUCCEEDED" if content is not None else "FAILED",
                external_id=f"{repo_name}:{path}",
                error=None if content is not None else "File does not exist.",
            )

        receipt = self._execute(f"{self.connection_id}/{repo_name}/file", "WORKSPACE_READ_FILE", operation)
        blob = holder.get("blob")
        return (blob if isinstance(blob, FileBlob) and self._ok(receipt) else None), receipt

    def open_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
    ):
        from ...models.pipeline import PullRequestResult

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            pr_id = "42"
            url = f"https://{self.provider_name}.internal/{self.workspace_id}/{repo}/pull/{pr_id}"
            return _receipt(
                "WORKSPACE_OPEN_PR",
                "open_pull_request",
                status="SUCCEEDED",
                external_id=pr_id,
                details={"url": url, "head": head_branch, "base": base_branch},
            )

        receipt = self.gateway.execute(
            permission="WRITE_REPOSITORY",
            target=f"{self.connection_id}/{repo}",
            operation_type="WORKSPACE_OPEN_PR",
            requested_action="open_pull_request",
            operation=operation,
            payload={"repo": repo, "title": title, "head": head_branch, "base": base_branch},
        )
        result = PullRequestResult(
            repository=repo,
            pull_request_id="42",
            url=f"https://{self.provider_name}.internal/{self.workspace_id}/{repo}/pull/42",
            head_branch=head_branch,
            base_branch=base_branch,
            status="OPEN" if self._ok(receipt) else "DRAFT",
        )
        return result, receipt

    def recent_activity(self, repo_ref: RepoRef, since: datetime) -> tuple[list[ActivityEvent], ExternalOperationReceipt]:
        failure = self._maybe_fail()
        if failure is not None:
            return [], failure
        events: list[ActivityEvent] = []

        def operation() -> ExternalOperationReceipt:
            self.calls_made += 1
            events.extend(
                event
                for event in self.activity
                if event.ref.repo == repo_ref.repo and event.occurred_at >= since
            )
            return _receipt(
                "WORKSPACE_ACTIVITY",
                "recent_activity",
                external_id=str(len(events)),
                details={"repo": repo_ref.repo},
            )

        receipt = self._execute(f"{self.connection_id}/{repo_ref.repo}/activity", "WORKSPACE_ACTIVITY", operation)
        return (events if self._ok(receipt) else []), receipt


def default_activity_window(days: int = 30) -> datetime:
    """Convenience window used by discovery when scoring recency."""
    return datetime.now(timezone.utc) - timedelta(days=days)