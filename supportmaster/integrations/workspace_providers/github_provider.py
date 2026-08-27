"""Read-only GitHub org workspace provider (REST v3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import quote

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


class HttpGitHubWorkspaceProvider(support.HttpWorkspaceProviderBase):
    """GitHub provider for one organization connection.

    The injected transport carries the credential (fine-grained PATs work
    with the shared Bearer-token transport). Only read endpoints are used.
    """

    provider_name: ClassVar[ProviderName] = "github"

    # -- listing ------------------------------------------------------------

    def _list_repos_request(self, cursor: str | None) -> tuple[str, dict[str, str]]:
        params = {"per_page": "50"}
        if cursor:
            params["page"] = cursor
        return f"/orgs/{self._encode(self.workspace_id)}/repos", params

    def _parse_repo_page(self, payload: Any) -> RepoPage:
        # The listing endpoint returns a bare JSON array; search-style
        # responses wrap items under "items".
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = payload["items"]
        else:
            items = []
        repositories: list[RepositoryDescriptor] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            repo_name = str(raw.get("name", ""))
            if not repo_name:
                continue
            ref = RepoRef(
                provider="github",
                workspace_id=self.workspace_id,
                repo=repo_name,
            )
            language = raw.get("language")
            repositories.append(
                RepositoryDescriptor(
                    ref=ref,
                    description=str(raw.get("description") or ""),
                    topics=[str(topic) for topic in (raw.get("topics") or [])],
                    default_branch=str(raw.get("default_branch") or ""),
                    languages={str(language): 1.0} if language else {},
                    last_commit_at=_parse_time(raw.get("pushed_at")),
                    archived=bool(raw.get("archived", False)),
                    size_kb=int(raw.get("size") or 0),
                )
            )
        return RepoPage(repositories=repositories)

    # -- metadata -----------------------------------------------------------

    def _repo_metadata_request(self, repo: str) -> tuple[str, dict[str, str]]:
        return (
            f"/repos/{self._encode(self.workspace_id)}/{self._encode(repo)}",
            {},
        )

    def _parse_repository(self, ref: RepoRef, payload: Any) -> RepositoryDescriptor:
        data = payload if isinstance(payload, dict) else {}
        language = data.get("language")
        return RepositoryDescriptor(
            ref=ref,
            description=str(data.get("description") or ""),
            topics=[str(topic) for topic in (data.get("topics") or [])],
            default_branch=str(data.get("default_branch") or ""),
            languages={str(language): 1.0} if language else {},
            last_commit_at=_parse_time(data.get("pushed_at")),
            archived=bool(data.get("archived", False)),
            size_kb=int(data.get("size") or 0),
        )

    # -- code search ----------------------------------------------------------

    def _code_search_request(self, query: str, repo: str | None) -> tuple[str, dict[str, str]]:
        qualifier = f"repo:{self.workspace_id}/{repo}" if repo else f"org:{self.workspace_id}"
        return "/search/code", {"q": f"{query} {qualifier}", "per_page": "20"}

    def _parse_code_matches(
        self, payload: Any, repo_hint: str | None
    ) -> list[CodeMatch]:
        if isinstance(payload, dict):
            raw_items = payload.get("items") or []
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raw_items = []
        matches: list[CodeMatch] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            repository = raw.get("repository") or {}
            repo_name = str(
                (repository.get("name") if isinstance(repository, dict) else "")
                or repo_hint
                or ""
            )
            if not repo_name:
                continue
            path = str(raw.get("path") or "")
            if not path:
                continue
            snippet = ""
            for fragment in raw.get("text_matches") or []:
                if isinstance(fragment, dict) and fragment.get("fragment"):
                    snippet = str(fragment["fragment"])[:500]
                    break
            matches.append(
                CodeMatch(
                    ref=RepoRef(provider="github", workspace_id=self.workspace_id, repo=repo_name),
                    path=path,
                    line=None,
                    snippet=snippet,
                )
            )
        return matches

    # -- file blob -------------------------------------------------------------

    def _read_file_request(self, repo: str, path: str, ref: str | None) -> tuple[str, dict[str, str]]:
        params: dict[str, str] = {}
        if ref:
            params["ref"] = ref
        return (
            f"/repos/{self._encode(self.workspace_id)}/{self._encode(repo)}/contents/{quote(path)}",
            params,
        )

    def _parse_file_blob(
        self, repo_ref: RepoRef, path: str, ref: str | None, payload: Any
    ) -> FileBlob:
        import base64

        content = ""
        if isinstance(payload, dict):
            encoded = payload.get("content")
            if isinstance(encoded, str) and payload.get("encoding") == "base64":
                try:
                    content = base64.b64decode(encoded.encode("ascii")).decode("utf-8", errors="replace")
                except Exception:
                    content = ""
        return FileBlob(ref=repo_ref, path=path, ref_name=ref, content=content)

    # -- activity ----------------------------------------------------------------

    def _activity_requests(self, repo: str, since: datetime) -> list[tuple[str, dict[str, str], str]]:
        iso_since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        return [
            (
                f"/repos/{self._encode(self.workspace_id)}/{self._encode(repo)}/commits",
                {"since": iso_since, "per_page": "10"},
                "COMMIT",
            ),
            (
                f"/repos/{self._encode(self.workspace_id)}/{self._encode(repo)}/pulls",
                {"sort": "updated", "direction": "desc", "state": "all", "per_page": "10"},
                "PULL_REQUEST",
            ),
        ]

    def _parse_activity(
        self, payload: Any, kind: str, repo: str
    ) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for raw in payload if isinstance(payload, list) else []:
            if not isinstance(raw, dict):
                continue
            if kind == "COMMIT":
                commit = raw.get("commit") or {}
                committer = commit.get("committer") or {}
                occurred = _parse_time(committer.get("date") if isinstance(committer, dict) else None)
                summary = str(commit.get("message") or "")[:200]
            else:
                occurred = _parse_time(raw.get("updated_at"))
                summary = str(raw.get("title") or "")[:200]
            if occurred is None:
                continue
            events.append(
                ActivityEvent(
                    ref=RepoRef(provider="github", workspace_id=self.workspace_id, repo=repo),
                    kind=kind,  # type: ignore[arg-type]
                    occurred_at=occurred,
                    summary=summary,
                )
            )
        return events
