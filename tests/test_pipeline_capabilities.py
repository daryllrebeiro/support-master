"""Tests for Phase 33: Capability Protocols, Registry, and Node Kinds."""

from __future__ import annotations

import unittest

from supportmaster.integrations.adapters import (
    InMemoryCIAdapter,
    InMemoryIssueTrackerAdapter,
    InMemoryMonitoringAdapter,
    InMemoryNotificationAdapter,
)
from supportmaster.integrations.contracts import IssueRecord
from supportmaster.pipeline.capabilities import (
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
from supportmaster.pipeline.node_kinds import (
    IMMUTABLE_CORE_SKELETON_NODES,
    CapabilityNodeSpec,
    CapabilityRequirement,
    CoreSkeletonNode,
    KNOWN_CAPABILITY_NODES,
    NodeKind,
)
from supportmaster.pipeline.registry import AdapterRegistry


class PipelineCapabilitiesTests(unittest.TestCase):
    def test_core_skeleton_nodes_cannot_be_arbitrary(self) -> None:
        """Only declared immutable skeleton nodes can be instantiated."""
        node = CoreSkeletonNode(node_id="duplicate_work_gate", description="Duplicate gate")
        self.assertEqual(node.kind, NodeKind.CORE_SKELETON)
        self.assertEqual(node.node_id, "duplicate_work_gate")

        with self.assertRaises(ValueError):
            CoreSkeletonNode(node_id="arbitrary_custom_node", description="Invalid")

    def test_registry_registration_and_lookup(self) -> None:
        registry = AdapterRegistry()
        reg = registry.register(
            "jira",
            InMemoryIssueTrackerAdapter,
            capabilities=[CanFetchCase, CanPostComment, CanSearchIssues],
            interface_version="capability-v1",
            adapter_version="1.2.0",
            vendor="atlassian",
        )
        self.assertEqual(reg.adapter_id, "jira")
        self.assertEqual(reg.interface_version, "capability-v1")
        self.assertTrue(reg.supports(CanFetchCase))
        self.assertTrue(reg.supports(CanPostComment))
        self.assertFalse(reg.supports(CanOpenPullRequest))

        self.assertEqual(registry.get_adapter_class("jira"), InMemoryIssueTrackerAdapter)
        matching = registry.list_by_capability(CanFetchCase)
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].adapter_id, "jira")

    def test_known_capability_nodes(self) -> None:
        self.assertIn("ticket_intake", KNOWN_CAPABILITY_NODES)
        self.assertIn("repository_discovery", KNOWN_CAPABILITY_NODES)
        self.assertIn("ci_validation", KNOWN_CAPABILITY_NODES)
        self.assertEqual(
            KNOWN_CAPABILITY_NODES["ticket_intake"].requirement,
            CapabilityRequirement.REQUIRED_IF_PRESENT,
        )


if __name__ == "__main__":
    unittest.main()
