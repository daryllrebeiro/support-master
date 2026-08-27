"""Multi-vendor end-to-end integration test.

Verifies that two tenants — Tenant A using Jira + GitHub + GitHub Actions,
and Tenant B using Linear + GitLab + GitLab CI — execute through canonical
schemas and produce equivalent canonical output without vendor leaking into
decision logic.
"""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import (
    InMemoryCIAdapter,
    InMemoryIssueTrackerAdapter,
    InMemoryNotificationAdapter,
)
from supportmaster.integrations.contracts import IssueRecord
from supportmaster.integrations.http import JsonHttpTransport
from supportmaster.integrations.jira_adapter import JiraAdapter
from supportmaster.integrations.linear_adapter import LinearAdapter
from supportmaster.integrations.workspace_providers.base import FakeWorkspaceProvider
from supportmaster.models.discovery import RepoRef, RepositoryDescriptor
from supportmaster.models.organization import (
    AdapterBindingEntry,
    AdapterBindingsConfig,
    OrganizationProfile,
    PipelineTopology,
)
from supportmaster.pipeline.bindings import (
    resolve_effective_nodes_and_bindings,
    validate_bindings,
)
from supportmaster.pipeline.capabilities import (
    CanFetchCase,
    CanOpenPullRequest,
    CanReadCIStatus,
    CanSearchCode,
    CanTriggerCI,
)
from supportmaster.pipeline.registry import default_registry
from supportmaster.pipeline.topology import validate_topology


class MultiVendorE2ETests(unittest.TestCase):
    def test_multi_vendor_configurations_resolve_identically(self) -> None:
        """Tenant 1 (Jira+GitHub) and Tenant 2 (Linear+GitLab) both validate and resolve."""

        # Tenant 1: Atlassian + GitHub stack
        tenant1 = OrganizationProfile(
            organization_id="org_atlassian_github",
            display_name="Enterprise Team A",
            pipeline_topology=PipelineTopology(
                enabled_capability_nodes=[
                    "ticket_intake",
                    "evidence_gathering",
                    "repository_discovery",
                    "repository_investigation",
                    "code_change",
                    "ci_validation",
                    "notification",
                ]
            ),
            adapter_bindings=AdapterBindingsConfig(
                bindings={
                    "ticket_intake": AdapterBindingEntry(adapter_id="jira"),
                    "repository_discovery": AdapterBindingEntry(adapter_id="github"),
                    "ci_validation": AdapterBindingEntry(adapter_id="github_actions"),
                    "notification": AdapterBindingEntry(adapter_id="slack"),
                }
            ),
        )

        # Tenant 2: Linear + GitLab stack
        tenant2 = OrganizationProfile(
            organization_id="org_linear_gitlab",
            display_name="Modern Team B",
            pipeline_topology=PipelineTopology(
                enabled_capability_nodes=[
                    "ticket_intake",
                    "evidence_gathering",
                    "repository_discovery",
                    "repository_investigation",
                    "code_change",
                    "ci_validation",
                ],
                optional_nodes_disabled=["notification"],
            ),
            adapter_bindings=AdapterBindingsConfig(
                bindings={
                    "ticket_intake": AdapterBindingEntry(adapter_id="linear"),
                    "repository_discovery": AdapterBindingEntry(adapter_id="gitlab"),
                    "ci_validation": AdapterBindingEntry(adapter_id="gitlab_ci"),
                }
            ),
        )

        # Validate topologies
        active1 = validate_topology(tenant1.pipeline_topology)
        active2 = validate_topology(tenant2.pipeline_topology)

        self.assertIn("notification", active1)
        self.assertNotIn("notification", active2)

        # Validate bindings against default registry
        bindings1 = validate_bindings(tenant1.adapter_bindings, default_registry)
        bindings2 = validate_bindings(tenant2.adapter_bindings, default_registry)

        self.assertEqual(bindings1["ticket_intake"].adapter_id, "jira")
        self.assertEqual(bindings2["ticket_intake"].adapter_id, "linear")
        self.assertEqual(bindings1["repository_discovery"].adapter_id, "github")
        self.assertEqual(bindings2["repository_discovery"].adapter_id, "gitlab")

        # Resolve effective bindings
        resolved1 = resolve_effective_nodes_and_bindings(active1, bindings1)
        resolved2 = resolve_effective_nodes_and_bindings(active2, bindings2)

        self.assertIn("ci_validation", resolved1)
        self.assertIn("ci_validation", resolved2)
        self.assertEqual(resolved1["ci_validation"].adapter_id, "github_actions")
        self.assertEqual(resolved2["ci_validation"].adapter_id, "gitlab_ci")

    def test_canonical_data_parity_across_vendors(self) -> None:
        """Both Jira and Linear adapters normalize cases to canonical SupportCase."""
        class FakeTransport:
            def __init__(self, responder):
                self.responder = responder

            def request(self, method, path, payload=None):
                return self.responder(method, path, payload)

        fake_jira_transport = FakeTransport(
            responder=lambda method, path, payload: (
                200,
                {
                    "key": "SUP-101",
                    "fields": {
                        "summary": "Database connection pool exhaustion",
                        "description": "App crashes under 500 concurrent users.",
                        "customfield_impact": "High revenue loss",
                    },
                },
            )
        )
        jira_adapter = JiraAdapter(fake_jira_transport, tenant_id="tenant_jira")
        jira_case, jira_receipt = jira_adapter.fetch_case("SUP-101")
        self.assertEqual(jira_receipt.status, "SUCCEEDED")
        self.assertIsNotNone(jira_case)
        self.assertEqual(jira_case.title, "Database connection pool exhaustion")
        self.assertEqual(jira_case.source_system, "JIRA")

        fake_linear_transport = FakeTransport(
            responder=lambda method, path, payload: (
                200,
                {
                    "identifier": "ENG-202",
                    "title": "Database connection pool exhaustion",
                    "description": "App crashes under 500 concurrent users.",
                    "impact": "High revenue loss",
                },
            )
        )
        linear_adapter = LinearAdapter(fake_linear_transport, tenant_id="tenant_linear")
        linear_case, linear_receipt = linear_adapter.fetch_case("ENG-202")
        self.assertEqual(linear_receipt.status, "SUCCEEDED")
        self.assertIsNotNone(linear_case)
        self.assertEqual(linear_case.title, "Database connection pool exhaustion")
        self.assertEqual(linear_case.source_system, "LINEAR")

        # Both cases share identical schema shape and semantic content
        self.assertEqual(jira_case.title, linear_case.title)
        self.assertEqual(jira_case.description, linear_case.description)


if __name__ == "__main__":
    unittest.main()
