"""Configurable organization context used by functional workflow decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkspaceConnection(BaseModel):
    """One read-only VCS workspace connection owned by a tenant.

    ``secret_ref`` is a *reference* (``env:NAME`` or
    ``secretmanager://project/secret``), never a raw token; it is write-only
    through the API and redacted on any read.
    """

    provider: Literal["github", "bitbucket", "gitlab"]
    workspace_id: str = Field(min_length=1, max_length=200)
    secret_ref: str = Field(min_length=1, max_length=500)
    scope: Literal["READ_ONLY"] = "READ_ONLY"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiscoveryPolicy(BaseModel):
    """Tenant-tunable bounds for repository workspace discovery."""

    enabled: bool = False
    max_candidates_per_run: int = Field(default=8, ge=1, le=32)
    max_disambiguation_repos: int = Field(default=3, ge=1, le=10)
    max_listed_repos: int = Field(default=50, ge=1, le=500)
    max_workspace_calls: int = Field(default=24, ge=1, le=200)
    code_search_enabled: bool = True
    cache_ttl_seconds: int = Field(default=900, ge=0, le=86_400)


class WorkflowPolicy(BaseModel):
    """Organization policy inputs; deterministic gates still enforce safety."""

    required_evidence_sources: list[str] = Field(default_factory=list)
    require_duplicate_check: bool = True
    require_implementation_approval: bool = True
    require_publication_approval: bool = True
    require_production_approval: bool = True
    allow_autonomous_code_change: bool = False
    allowed_external_actions: list[str] = Field(default_factory=list)
    escalation_thresholds: dict[str, str] = Field(default_factory=dict)


class PipelineTopology(BaseModel):
    """Configuration declaring which pluggable capability nodes run for a tenant."""

    enabled_capability_nodes: list[str] = Field(
        default_factory=lambda: [
            "ticket_intake",
            "evidence_gathering",
            "repository_discovery",
            "repository_investigation",
            "code_change",
            "ci_validation",
            "notification",
        ]
    )
    optional_nodes_disabled: list[str] = Field(default_factory=list)
    policy_version: str = "topology-v1"


class AdapterBindingEntry(BaseModel):
    """Binding of one capability node to a registered adapter and credentials."""

    adapter_id: str = Field(min_length=1, max_length=100)
    connection_ref: str = Field(default="", max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)


class AdapterBindingsConfig(BaseModel):
    """Map of capability_node_id -> AdapterBindingEntry."""

    bindings: dict[str, AdapterBindingEntry] = Field(default_factory=dict)
    policy_version: str = "bindings-v1"


class OrganizationProfile(BaseModel):
    """A tenant's support vocabulary, routing, and workflow preferences."""

    organization_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=300)
    status: Literal["ACTIVE", "SUSPENDED"] = "ACTIVE"
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    environments: list[str] = Field(default_factory=lambda: ["development", "staging", "production"])
    severity_levels: list[str] = Field(default_factory=lambda: ["low", "medium", "high", "critical"])
    priority_levels: list[str] = Field(default_factory=lambda: ["low", "normal", "high", "urgent"])
    escalation_rules: dict[str, list[str]] = Field(default_factory=dict)
    ownership_rules: dict[str, str] = Field(default_factory=dict)
    repository_mappings: dict[str, str] = Field(default_factory=dict)
    workspace_connections: list[WorkspaceConnection] = Field(default_factory=list)
    discovery_policy: DiscoveryPolicy = Field(default_factory=DiscoveryPolicy)
    pipeline_topology: PipelineTopology = Field(default_factory=PipelineTopology)
    adapter_bindings: AdapterBindingsConfig = Field(default_factory=AdapterBindingsConfig)
    terminology: dict[str, str] = Field(default_factory=dict)
    response_style: Literal["CONCISE", "STANDARD", "DETAILED"] = "STANDARD"
    workflow_policy: WorkflowPolicy = Field(default_factory=WorkflowPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
