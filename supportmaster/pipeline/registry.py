"""Adapter Registry for capability-based integration adapters.

Invariant 2 & 5: Single source of truth for registered adapters, capability
declarations, and interface versions. Explicit registration only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Type


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_id: str
    adapter_cls: type
    capabilities: tuple[type, ...]
    interface_version: str = "capability-v1"
    adapter_version: str = "1.0.0"
    vendor: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def supports(self, capability: type) -> bool:
        return any(
            issubclass(cap, capability) if isinstance(cap, type) else cap == capability
            for cap in self.capabilities
        )


class AdapterRegistry:
    """Registry managing available adapters and declared capabilities."""

    def __init__(self) -> None:
        self._registrations: dict[str, AdapterRegistration] = {}

    def register(
        self,
        adapter_id: str,
        adapter_cls: type,
        *,
        capabilities: Sequence[type] = (),
        interface_version: str = "capability-v1",
        adapter_version: str = "1.0.0",
        vendor: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> AdapterRegistration:
        if not adapter_id or not adapter_id.strip():
            raise ValueError("adapter_id cannot be empty.")
        normalized_id = adapter_id.strip().lower()
        registration = AdapterRegistration(
            adapter_id=normalized_id,
            adapter_cls=adapter_cls,
            capabilities=tuple(capabilities),
            interface_version=interface_version,
            adapter_version=adapter_version,
            vendor=vendor or normalized_id,
            metadata=dict(metadata or {}),
        )
        self._registrations[normalized_id] = registration
        return registration

    def get_registration(self, adapter_id: str) -> AdapterRegistration | None:
        return self._registrations.get(adapter_id.strip().lower())

    def get_adapter_class(self, adapter_id: str) -> type | None:
        reg = self.get_registration(adapter_id)
        return reg.adapter_cls if reg else None

    def list_by_capability(self, capability: type) -> list[AdapterRegistration]:
        return [
            reg
            for reg in self._registrations.values()
            if reg.supports(capability)
        ]

    def list_all(self) -> list[AdapterRegistration]:
        return sorted(self._registrations.values(), key=lambda r: r.adapter_id)

def register_builtin_adapters(registry: AdapterRegistry) -> None:
    """Register core built-in adapters."""
    from .capabilities import (
        CanFetchCase,
        CanListRepositories,
        CanOpenPullRequest,
        CanPostComment,
        CanReadCIStatus,
        CanReadFile,
        CanReadMonitoringSignal,
        CanSearchCode,
        CanSearchIssues,
        CanSendNotification,
        CanTriggerCI,
        CanUpdateCaseStatus,
    )
    from ..integrations.adapters import (
        InMemoryCIAdapter,
        InMemoryIssueTrackerAdapter,
        InMemoryMonitoringAdapter,
        InMemoryNotificationAdapter,
    )
    from ..integrations.datadog_monitoring import DatadogMonitoringAdapter
    from ..integrations.github_actions_ci import GitHubActionsCIAdapter
    from ..integrations.gitlab_ci import GitLabCIAdapter
    from ..integrations.jira_adapter import JiraAdapter
    from ..integrations.linear_adapter import LinearAdapter
    from ..integrations.slack_notification import SlackNotificationAdapter
    from ..integrations.workspace_providers.base import FakeWorkspaceProvider
    from ..integrations.workspace_providers.bitbucket_provider import HttpBitbucketWorkspaceProvider
    from ..integrations.workspace_providers.github_provider import HttpGitHubWorkspaceProvider
    from ..integrations.workspace_providers.gitlab_provider import HttpGitLabWorkspaceProvider
    from ..integrations.zendesk_adapter import ZendeskAdapter

    # Intake
    registry.register("jira", JiraAdapter, capabilities=[CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus], vendor="atlassian", adapter_version="1.0.0")
    registry.register("linear", LinearAdapter, capabilities=[CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus], vendor="linear", adapter_version="1.0.0")
    registry.register("zendesk", ZendeskAdapter, capabilities=[CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus], vendor="zendesk", adapter_version="1.0.0")
    registry.register("in_memory_issues", InMemoryIssueTrackerAdapter, capabilities=[CanFetchCase, CanPostComment, CanSearchIssues, CanUpdateCaseStatus], vendor="supportmaster", adapter_version="1.0.0")

    # Workspace / Repo
    registry.register("github", HttpGitHubWorkspaceProvider, capabilities=[CanListRepositories, CanSearchCode, CanReadFile, CanOpenPullRequest], vendor="github", adapter_version="1.0.0")
    registry.register("gitlab", HttpGitLabWorkspaceProvider, capabilities=[CanListRepositories, CanSearchCode, CanReadFile, CanOpenPullRequest], vendor="gitlab", adapter_version="1.0.0")
    registry.register("bitbucket", HttpBitbucketWorkspaceProvider, capabilities=[CanListRepositories, CanSearchCode, CanReadFile, CanOpenPullRequest], vendor="atlassian", adapter_version="1.0.0")
    registry.register("fake_workspace", FakeWorkspaceProvider, capabilities=[CanListRepositories, CanSearchCode, CanReadFile, CanOpenPullRequest], vendor="supportmaster", adapter_version="1.0.0")

    # CI
    registry.register("github_actions", GitHubActionsCIAdapter, capabilities=[CanTriggerCI, CanReadCIStatus], vendor="github", adapter_version="1.0.0")
    registry.register("gitlab_ci", GitLabCIAdapter, capabilities=[CanTriggerCI, CanReadCIStatus], vendor="gitlab", adapter_version="1.0.0")
    registry.register("in_memory_ci", InMemoryCIAdapter, capabilities=[CanTriggerCI, CanReadCIStatus], vendor="supportmaster", adapter_version="1.0.0")

    # Notification
    registry.register("slack", SlackNotificationAdapter, capabilities=[CanSendNotification], vendor="slack", adapter_version="1.0.0")
    registry.register("in_memory_notification", InMemoryNotificationAdapter, capabilities=[CanSendNotification], vendor="supportmaster", adapter_version="1.0.0")

    # Monitoring
    registry.register("datadog", DatadogMonitoringAdapter, capabilities=[CanReadMonitoringSignal], vendor="datadog", adapter_version="1.0.0")
    registry.register("in_memory_monitoring", InMemoryMonitoringAdapter, capabilities=[CanReadMonitoringSignal], vendor="supportmaster", adapter_version="1.0.0")


default_registry = AdapterRegistry()
register_builtin_adapters(default_registry)
