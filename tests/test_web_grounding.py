"""Phase B: web-grounded duplicate + evidence search with citation policy."""

import unittest

from supportmaster.agents.duplicate_work_agent import duplicate_work_agent
from supportmaster.agents.evidence_agent import evidence_agent
from supportmaster.workflows.publishing_gate_workflow import (
    create_publishing_gate_workflow,
)


class WebGroundingInstructionTests(unittest.TestCase):
    def test_evidence_agent_declares_web_search_policy(self) -> None:
        instruction = evidence_agent.instruction
        self.assertIn("WEB SEARCH POLICY", instruction)
        self.assertIn("Google web search", instruction)
        self.assertIn("EXTERNAL", instruction)
        self.assertIn("source URL", instruction)

    def test_duplicate_agent_declares_web_search_policy(self) -> None:
        instruction = duplicate_work_agent.instruction
        self.assertIn("WEB SEARCH POLICY", instruction)
        self.assertIn("DUPLICATE_CANDIDATE", instruction)
        self.assertIn("EXTERNAL", instruction)

    def test_policy_keeps_internal_gates_authoritative(self) -> None:
        for agent in (evidence_agent, duplicate_work_agent):
            self.assertIn(
                "internal",
                agent.instruction.lower(),
                f"{agent.name} policy must defer to internal gates",
            )


class WebGroundingGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = create_publishing_gate_workflow("gemini-3.6-flash")
        self.graph = self.workflow.graph
        assert self.graph is not None

    def _agent_nodes(self) -> dict:
        return {
            node.name: node
            for node in self.graph.nodes
            if hasattr(node, "tools")
        }

    def test_evidence_and_duplicate_agents_carry_google_search(self) -> None:
        agents = self._agent_nodes()
        for name in {"evidence_agent", "duplicate_work_agent"}:
            tools = getattr(agents[name], "tools", [])
            names = {getattr(tool, "name", "") for tool in tools}
            self.assertIn(
                "google_search",
                names,
                f"{name} must expose the google_search tool",
            )

    def test_no_other_agent_gains_web_search(self) -> None:
        agents = self._agent_nodes()
        for name, node_obj in agents.items():
            if name in {"evidence_agent", "duplicate_work_agent"}:
                continue
            names = {
                getattr(tool, "name", "")
                for tool in getattr(node_obj, "tools", [])
            }
            self.assertNotIn(
                "google_search",
                names,
                f"{name} must not gain web grounding",
            )

    def test_fan_out_topology_unchanged(self) -> None:
        self.assertEqual(
            self.graph.get_next_pending_nodes("duplicate_work_gate", "CONTINUE"),
            ["evidence_agent", "repository_agent"],
        )
        self.assertEqual(
            self.graph.get_next_pending_nodes("duplicate_work_gate", "SAFETY_STOP"),
            ["autonomous_safety_stop"],
        )


if __name__ == "__main__":
    unittest.main()