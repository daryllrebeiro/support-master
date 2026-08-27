"""Read-only GitLab group workspace provider (REST v4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from ...models.discovery import (
    ActivityEvent,
    CodeMatch,
    FileBlob,
    ProviderName,
    RepoPage,
    RepoRef,
    RepositoryDescriptor,
)
from . import _http_support as support


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class HttpGitLabWorkspaceProvider(support.HttpWorkspaceProviderBase):
    """GitLab provider for one group connection.

    Workspace-wide code search uses the Advanced Search API, which requires
    GitLab Premium+; on Free tiers the endpoint answers 403/404 and the
    shared base degrades to a ``fan_out_required`` PARTIAL receipt so the
    discovery service can fall back to bounded per-project searches.
    """

    provider_name: ClassVar[ProviderName] = "gitlab"

    # -- listing ----------------------------------------------------------------

    def _list_repos_request(self, cursor: str | None) -> tuple[str, dict[str, str]]:
        params = {"per_page": "50", "simple": "true"}
        if cursor:
            params["page"] = cursor
        return f"/api/v4/groups/{self._encode(self.workspace_id)}/projects", params

    def _parse_repo_page(self, payload: dict[str, Any]) -> RepoPage:
        repositories: list[RepositoryDescriptor] = []
        for raw in payload if isinstance(payload, list) else []:
            if not isinstance(raw, dict):
                continue
            path_with_namespace = str(raw.get("path_with_namespace") or "")
            repo_path = str(raw.get("path") or raw.get("name") or "")
            if not repo_path:
                continue
            ref = RepoRef(provider="gitlab", workspace_id=self.workspace_id, repo=repo_path)
            language = None  # simple=true omits languages; metadata call fills it.
            repositories.append(
                RepositoryDescriptor(
                    ref=ref,
                    description=str(raw.get("description") or ""),
                    topics=[str(topic) for topic in (raw.get("topics") or [])],
                    default_branch=str(raw.get("default_branch") or ""),
                    languages={str(language): 1.0} if language else {},
                    last_commit_at=_parse_time(raw.get("last_activity_at")),
                    archived=bool(raw.get("archived", False)),
                    size_kb=0,
                )
            )
            del path_with_namespace
        return RepoPage(repositories=repositories)

    # -- metadata ------------------------------------------------------------------

    def _repo_metadata_request(self, repo: str) -> tuple[str, dict[str, str]]:
        return f"/api/v4/projects/{self._encode(repo)}", {}

    def _parse_repository(self, ref: RepoRef, payload: dict[str, Any]) -> RepositoryDescriptor:
        return RepositoryDescriptor(
            ref=ref,
            description=str(payload.get("description") or ""),
            topics=[str(topic) for topic in (payload.get("topics") or [])],
            default_branch=str(payload.get("default_branch") or ""),
            last_commit_at=_parse_time(payload.get("last_activity_at")),
            archived=bool(payload.get("archived", False)),
            size_kb=int((payload.get("statistics", {}) or {}).get("repository_size", 0) / 1024)
            if isinstance(payload.get("statistics"), dict)
            else 0,
        )

    # -- code search -------------------------------------------------------------------

    def _code_search_request(self, query: str, repo: str | None) -> tuple[str, dict[str, str]]:
        params = {"scope": "blobs", "search": query, "per_page": "20"}
        if repo:
            return f"/api/v4/projects/{self._encode(repo)}/search", params
        return "/api/v4/search", params

    def _parse_code_matches(
        self, payload: dict[str, Any], repo_hint: str | None
    ) -> list[CodeMatch]:
        matches: list[CodeMatch] = []
        for raw in payload if isinstance(payload, list) else []:
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or raw.get("file_path") or "")
            if not path:
                continue
            matches.append(
                CodeMatch(
                    ref=RepoRef(
                        provider="gitlab",
                        workspace_id=self.workspace_id,
                        repo=repo_hint or "unknown",
                    ),
                    path=path,
                    line=int(raw["line"]) if isinstance(raw.get("line"), int) else None,
                    snippet=str(raw.get("data") or "")[:500],
                )
            )
        return matches

    # -- file blob ------------------------------------------------------------------------

    def _read_file_request(self, repo: str, path: str, ref: str | None) -> tuple[str, dict[str, str]]:
        commit = self._encode(ref) if ref else "HEAD"
        return (
            f"/api/v4/projects/{self._encode(repo)}/repository/files/{quote(path, safe='')}/raw",
            {"ref": commit},
        )

    def _parse_file_blob(
        self, repo_ref: RepoRef, path: str, ref: str | None, payload: dict[str, Any]
    ) -> FileBlob:
        content = ""
        if isinstance(payload, dict):
            raw = payload.get("content")
            if isinstance(raw, str):
                content = raw
        return FileBlob(ref=repo_ref, path=path, ref_name=ref, content=content)

    # -- activity -----------------------------------------------------------------------------

    def _activity_requests(self, repo: str, since: datetime) -> list[tuple[str, dict[str, str], str]]:
        iso_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        base = f"/api/v4/projects/{self._encode(repo)}"
        return [
            (
                f"{base}/repository/commits",
                {"since": iso_since, "per_page": "10"},
                "COMMIT",
            ),
            (
                f"{base}/merge_requests",
                {"updated_after": iso_since, "per_page": "10"},
                "PULL_REQUEST",
            ),
        ]

    def _parse_activity(
        self, payload: dict[str, Any], kind: str, repo: str
    ) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for raw in payload if isinstance(payload, list) else []:
            if not isinstance(raw, dict):
                continue
            occurred = _parse_time(
                raw.get("committed_date") or raw.get("created_at") or raw.get("updated_at")
            )
            summary = str(raw.get("title") or "")[:200]
            if occurred is None:
                continue
            events.append(
                ActivityEvent(
                    ref=RepoRef(provider="gitlab", workspace_id=self.workspace_id, repo=repo),
                    kind=kind,  # type: ignore[arg-type]
                    occurred_at=occurred,
                    summary=summary,
                )
            )
        return events