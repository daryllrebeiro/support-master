"""Typed contracts for read-only repository workspace discovery.

These models are provider-neutral: downstream stages consume
``DiscoveryResult`` without branching on which VCS (GitHub, Bitbucket,
GitLab) produced a candidate. Every external workspace call that produces
this data is receipted through ``IntegrationGateway`` by the providers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


ProviderName = Literal["github", "bitbucket", "gitlab"]

DiscoverySource = Literal[
    "STATIC_MAPPING",
    "HISTORICAL_CASE",
    "WORKSPACE_METADATA",
    "CODE_SEARCH",
]

DiscoveryConfidence = Literal["LOW", "MEDIUM", "HIGH"]


class RepoRef(BaseModel):
    """Identity of one repository inside one tenant workspace connection."""

    provider: ProviderName
    workspace_id: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1, max_length=300)

    def key(self) -> str:
        """Stable ``provider:workspace/repo`` identity used in grants/memory."""
        return f"{self.provider}:{self.workspace_id}/{self.repo}"


class RepositoryDescriptor(BaseModel):
    """Cheap structural metadata used to rank candidate repositories."""

    ref: RepoRef
    description: str = ""
    topics: list[str] = Field(default_factory=list)
    default_branch: str = ""
    languages: dict[str, float] = Field(default_factory=dict)
    last_commit_at: datetime | None = None
    archived: bool = False
    size_kb: int = Field(default=0, ge=0)


class RepoPage(BaseModel):
    """One page of ``list_repositories`` output."""

    repositories: list[RepositoryDescriptor] = Field(default_factory=list)
    next_cursor: str | None = None


class CodeMatch(BaseModel):
    """One code-search hit. Snippets are redacted before entering state."""

    ref: RepoRef
    path: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    snippet: str = ""


class FileBlob(BaseModel):
    """A single file read from a repository at an optional ref."""

    ref: RepoRef
    path: str = Field(min_length=1, max_length=1000)
    ref_name: str | None = None
    content: str = ""


class ActivityEvent(BaseModel):
    """Recent commit/PR activity used as a recency relevance signal."""

    ref: RepoRef
    kind: Literal["COMMIT", "PULL_REQUEST"]
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str = ""


class DiscoveredRepository(BaseModel):
    """One ranked candidate with full scoring provenance for the RCA trail."""

    ref: RepoRef
    name: str
    sources: list[DiscoverySource] = Field(default_factory=list)
    confidence: DiscoveryConfidence = "LOW"
    score: float = Field(default=0.0, ge=0.0)
    evidence: list[str] = Field(default_factory=list)
    matched_paths: list[str] = Field(default_factory=list)


class DisambiguationDecision(BaseModel):
    """Bounded LLM output: can only reorder/filter already-discovered refs.

    The schema intentionally has no field capable of introducing a repository
    that the deterministic pipeline did not already find.
    """

    ordered_refs: list[RepoRef] = Field(default_factory=list)
    dropped_refs: list[RepoRef] = Field(default_factory=list)
    rationale: str = ""


class DiscoveryResult(BaseModel):
    """Persisted per-run outcome of the repository discovery stage."""

    connections_used: list[str] = Field(default_factory=list)
    candidates: list[DiscoveredRepository] = Field(default_factory=list)
    selected: list[RepoRef] = Field(default_factory=list)
    method_trace: list[str] = Field(default_factory=list)
    workspace_calls_made: int = Field(default=0, ge=0)
    degraded: bool = False
    policy_version: str = "v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))