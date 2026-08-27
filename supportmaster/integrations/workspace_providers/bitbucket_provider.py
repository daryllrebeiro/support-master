"""Read-only Bitbucket workspace provider (Cloud REST 2.0)."""

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


class HttpBitbucketWorkspaceProvider(support.HttpWorkspaceProviderBase):
    """Bitbucket Cloud provider for one workspace connection.

    The injected transport carries the credential (an OAuth access token with
    ``repository:read`` works with the shared Bearer-token transport).
    """

    provider_name: ClassVar[ProviderName] = "bitbucket"

    # -- listing -------------------------------------------------------------

    def _list_repos_request(self, cursor: str | None) -> tuple[str, dict[str, str]]:
        params = {"pagelen": "50"}
        if cursor:
            params["page"] = cursor
        return f"/2.0/repositories/{self._encode(self.workspace_id)}", params

    def _parse_repo_page(self, payload: dict[str, Any]) -> RepoPage:
        repositories: list[RepositoryDescriptor] = []
        for raw in payload.get("values") or []:
            if not isinstance(raw, dict):
                continue
            slug = str(raw.get("slug") or raw.get("name") or "")
            if not slug:
                continue
            ref = RepoRef(provider="bitbucket", workspace_id=self.workspace_id, repo=slug)
            language = raw.get("language")
            pushed = _parse_time(raw.get("updated_on"))
            repositories.append(
                RepositoryDescriptor(
                    ref=ref,
                    description=str(raw.get("description") or ""),
                    topics=[],
                    default_branch=str(((raw.get("mainbranch") or {}).get("name")) or ""),
                    languages={str(language): 1.0} if language else {},
                    last_commit_at=pushed,
                    archived=bool(raw.get("archived", False)),
                    size_kb=int((raw.get("size") or 0) / 1024),
                )
            )
        next_cursor = payload.get("next")
        return RepoPage(
            repositories=repositories,
            next_cursor=str(next_cursor) if next_cursor else None,
        )

    # -- metadata ---------------------------------------------------------------

    def _repo_metadata_request(self, repo: str) -> tuple[str, dict[str, str]]:
        return f"/2.0/repositories/{self._encode(self.workspace_id)}/{self._encode(repo)}", {}

    def _parse_repository(self, ref: RepoRef, payload: dict[str, Any]) -> RepositoryDescriptor:
        language = payload.get("language")
        return RepositoryDescriptor(
            ref=ref,
            description=str(payload.get("description") or ""),
            default_branch=str(((payload.get("mainbranch") or {}).get("name")) or ""),
            languages={str(language): 1.0} if language else {},
            last_commit_at=_parse_time(payload.get("updated_on")),
            archived=bool(payload.get("archived", False)),
            size_kb=int((payload.get("size") or 0) / 1024),
        )

    # -- code search ---------------------------------------------------------------

    def _code_search_request(self, query: str, repo: str | None) -> tuple[str, dict[str, str]]:
        if repo:
            return (
                f"/2.0/repositories/{self._encode(self.workspace_id)}/{self._encode(repo)}/search/code",
                {"search_query": query},
            )
        return (
            f"/2.0/workspaces/{self._encode(self.workspace_id)}/search/code",
            {"search_query": query},
        )

    def _parse_code_matches(
        self, payload: dict[str, Any], repo_hint: str | None
    ) -> list[CodeMatch]:
        matches: list[CodeMatch] = []
        for raw in payload.get("values") or []:
            if not isinstance(raw, dict):
                continue
            file_info = raw.get("file") or {}
            path = str(file_info.get("path") or "")
            if not path:
                continue
            repo_name = repo_hint or ""
            links_repo = ((file_info.get("links") or {}).get("self") or {}).get("href", "")
            if not repo_name and "/repositories/" in str(links_repo):
                tail = str(links_repo).split("/repositories/", 1)[1]
                parts = [part for part in tail.split("/") if part]
                if len(parts) >= 2:
                    repo_name = parts[1]
            snippet = ""
            for content_match in raw.get("content_matches") or []:
                for line in (content_match.get("lines") or [])[:1]:
                    snippet = " ".join(str(segment.get("text", "")) for segment in (line.get("segments") or []))[:500]
                    break
                break
            matches.append(
                CodeMatch(
                    ref=RepoRef(provider="bitbucket", workspace_id=self.workspace_id, repo=repo_name or "unknown"),
                    path=path,
                    line=None,
                    snippet=snippet,
                )
            )
        return matches

    # -- file blob --------------------------------------------------------------------

    def _read_file_request(self, repo: str, path: str, ref: str | None) -> tuple[str, dict[str, str]]:
        commit = self._encode(ref) if ref else "HEAD"
        return (
            f"/2.0/repositories/{self._encode(self.workspace_id)}/{self._encode(repo)}/src/{commit}/{quote(path)}",
            {},
        )

    def _parse_file_blob(
        self, repo_ref: RepoRef, path: str, ref: str | None, payload: dict[str, Any]
    ) -> FileBlob:
        # Bitbucket src endpoint returns raw text; the JSON transport wraps it
        # best-effort, so accept either a raw string field or an error dict.
        content = ""
        if isinstance(payload, dict):
            raw = payload.get("raw")
            if isinstance(raw, str):
                content = raw
        return FileBlob(ref=repo_ref, path=path, ref_name=ref, content=content)

    # -- activity ------------------------------------------------------------------------

    def _activity_requests(self, repo: str, since: datetime) -> list[tuple[str, dict[str, str], str]]:
        iso_since = since.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        base = f"/2.0/repositories/{self._encode(self.workspace_id)}/{self._encode(repo)}"
        return [
            (f"{base}/commits", {"include": "", "fields": "values.hash,values.date,values.message"}, "COMMIT"),
            (
                f"{base}/pullrequests",
                {"state": "ALL", "sort": "-updated_on", "fields": "values.title,values.updated_on"},
                "PULL_REQUEST",
            ),
        ]

    def _parse_activity(
        self, payload: dict[str, Any], kind: str, repo: str
    ) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for raw in payload.get("values") or []:
            if not isinstance(raw, dict):
                continue
            occurred = _parse_time(raw.get("date") or raw.get("updated_on"))
            summary = str(raw.get("message") or raw.get("title") or "")[:200]
            if occurred is None:
                continue
            events.append(
                ActivityEvent(
                    ref=RepoRef(provider="bitbucket", workspace_id=self.workspace_id, repo=repo),
                    kind=kind,  # type: ignore[arg-type]
                    occurred_at=occurred,
                    summary=summary,
                )
            )
        return events