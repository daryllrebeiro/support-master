"""Tests for Phase 35: Pipeline Topology Configuration & Validation."""

from __future__ import annotations

import unittest

from supportmaster.models.organization import PipelineTopology
from supportmaster.pipeline.topology import TopologyValidationError, validate_topology


class PipelineTopologyTests(unittest.TestCase):
    def test_default_topology_is_valid(self) -> None:
        topology = PipelineTopology()
        active = validate_topology(topology)
        self.assertIn("ticket_intake", active)
        self.assertIn("repository_discovery", active)
        self.assertIn("code_change", active)

    def test_disabling_optional_nodes(self) -> None:
        topology = PipelineTopology(
            enabled_capability_nodes=["ticket_intake", "repository_discovery", "repository_investigation", "code_change", "notification"],
            optional_nodes_disabled=["notification"],
        )
        active = validate_topology(topology)
        self.assertNotIn("notification", active)
        self.assertIn("ticket_intake", active)

    def test_rejects_skeleton_node_in_enabled(self) -> None:
        """Attempting to name a core skeleton node in enabled list is rejected."""
        topology = PipelineTopology(
            enabled_capability_nodes=["ticket_intake", "duplicate_work_gate", "repository_discovery"],
        )
        with self.assertRaises(TopologyValidationError) as ctx:
            validate_topology(topology)
        self.assertIn("Core skeleton nodes cannot be configured", str(ctx.exception))

    def test_rejects_skeleton_node_in_disabled(self) -> None:
        """Attempting to disable a core skeleton node is rejected."""
        topology = PipelineTopology(
            enabled_capability_nodes=["ticket_intake", "repository_discovery"],
            optional_nodes_disabled=["publish_authorization_gate"],
        )
        with self.assertRaises(TopologyValidationError) as ctx:
            validate_topology(topology)
        self.assertIn("Core skeleton nodes cannot be configured", str(ctx.exception))

    def test_rejects_missing_prerequisite_dependency(self) -> None:
        """Enabling code_change without repository_investigation is rejected."""
        topology = PipelineTopology(
            enabled_capability_nodes=["ticket_intake", "code_change"],
        )
        with self.assertRaises(TopologyValidationError) as ctx:
            validate_topology(topology)
        self.assertIn("requires dependency", str(ctx.exception))

    def test_rejects_disabling_bound_required_node(self) -> None:
        """A REQUIRED_IF_PRESENT node cannot be disabled if bound."""
        topology = PipelineTopology(
            enabled_capability_nodes=["ticket_intake", "repository_discovery", "repository_investigation"],
            optional_nodes_disabled=["repository_discovery"],
        )
        with self.assertRaises(TopologyValidationError) as ctx:
            validate_topology(topology, bound_nodes=["repository_discovery"])
        self.assertIn("REQUIRED_IF_PRESENT", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
