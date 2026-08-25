"""Phase A: cross-run memory loop — tool retrieval, recording, and graph wiring."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from supportmaster.memory.case_store import CaseMemoryStore
from supportmaster.memory.retriever import CaseContextRetriever
from supportmaster.tools.memory_tools import build_memory_tool
from supportmaster.workflows.publishing_gate_workflow import (
    create_publishing_gate_workflow,
)
from supportmaster.workflows.terminal_nodes import (
    record_completed_run_to_memory,
)


class _FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self._data = dict(data or {})

    def to_dict(self) -> dict:
        return dict(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value


class _FakeContext:
    def __init__(self, data: dict | None = None) -> None:
        self.state = _FakeState(data)


class MemoryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "memory.db"
        self.retriever = CaseContextRetriever(
            CaseMemoryStore(db_path=self.db_path)
        )
        self.tool = build_memory_tool(store=self.retriever)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _invoke(self, query: str, tenant_id: str) -> dict:
        context = _FakeContext({"tenant_id": tenant_id})
        return self.tool.func(query, context)  # type: ignore[attr-defined]

    def test_empty_store_returns_not_found(self) -> None:
        result = self._invoke("OutOfMemoryError invoice export", "tenant-a")
        self.assertFalse(result["found"])
        self.assertEqual(result["context"], "")

    def test_recorded_resolution_is_retrievable(self) -> None:
        self.retriever.record_resolution(
            case_id="CASE-1",
            tenant_id="tenant-a",
            title="Invoice export OutOfMemoryError",
            description="Large CSV export exhausts JVM heap.",
            root_cause="Export pipeline materializes full dataset in memory.",
            resolution_summary="Switched serialization to streaming cursor.",
        )
        result = self._invoke("invoice export heap", "tenant-a")
        self.assertTrue(result["found"])
        self.assertIn("CASE-1", result["context"])
        self.assertIn("streaming cursor", result["context"])

    def test_memory_is_tenant_scoped(self) -> None:
        self.retriever.record_resolution(
            case_id="CASE-1",
            tenant_id="tenant-a",
            title="Secret internal incident",
            description="Internal-only details.",
            root_cause="Internal cause.",
            resolution_summary="Internal fix.",
        )
        other_tenant = self._invoke("internal incident", "tenant-b")
        self.assertFalse(other_tenant["found"])

    def test_tool_name_is_stable_for_model_binding(self) -> None:
        self.assertEqual(self.tool.name, "search_past_resolutions")


class MemoryGraphWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = create_publishing_gate_workflow("gemini-3.6-flash")
        self.graph = self.workflow.graph
        assert self.graph is not None

    def test_memory_record_node_is_wired_after_summary(self) -> None:
        self.assertEqual(
            self.graph.get_next_pending_nodes("workflow_summary_agent", None),
            ["memory_record_node"],
        )
        self.assertEqual(
            self.graph.get_next_pending_nodes("memory_record_node", None),
            ["workflow_control_agent"],
        )

    def test_investigation_and_root_cause_agents_carry_memory_tool(self) -> None:
        tool_agents = {
            node.name: getattr(node, "tools", [])
            for node in self.graph.nodes
            if hasattr(node, "tools")
        }
        for agent_name in {"investigation_agent", "root_cause_agent"}:
            tools = tool_agents.get(agent_name, [])
            names = {getattr(tool, "name", "") for tool in tools}
            self.assertIn(
                "search_past_resolutions",
                names,
                f"{agent_name} must expose the memory tool",
            )

    def test_other_agents_remain_tool_free(self) -> None:
        for node in self.graph.nodes:
            if not hasattr(node, "tools"):
                continue
            if node.name in {"investigation_agent", "root_cause_agent"}:
                continue
            self.assertFalse(
                getattr(node, "tools", []),
                f"{node.name} unexpectedly gained tools",
            )


class MemoryRecordNodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "memory.db"
        self.env_patcher = patch.dict(
            os.environ, {"SUPPORTMASTER_MEMORY_DB": str(self.db_path)}
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp.cleanup()

    def _completed_state(self) -> dict:
        return {
            "terminal_status": "COMPLETED",
            "tenant_id": "tenant-a",
            "support_case": {
                "case_id": "CASE-9",
                "title": "Export OOM",
                "description": "Large exports fail.",
            },
            "root_cause_analysis": {
                "primary_root_cause": "Dataset materialized in memory.",
            },
            "workflow_summary_text": "Fix validated and published.",
        }

    def test_completed_run_is_recorded_to_memory(self) -> None:
        context = _FakeContext(self._completed_state())
        result = record_completed_run_to_memory(context)
        self.assertTrue(result["memory_recorded"])
        store = CaseMemoryStore(db_path=self.db_path)
        cases = store.retrieve_similar("export oom", "tenant-a")
        self.assertTrue(any(c.case_id == "CASE-9" for c in cases))

    def test_safety_stop_does_not_record(self) -> None:
        state = self._completed_state()
        state["terminal_status"] = "SAFETY_STOP"
        context = _FakeContext(state)
        result = record_completed_run_to_memory(context)
        self.assertEqual(result, {})
        store = CaseMemoryStore(db_path=self.db_path)
        self.assertEqual(store.retrieve_similar("export oom", "tenant-a"), [])

    def test_memory_failure_is_fail_open(self) -> None:
        with patch.object(
            CaseContextRetriever,
            "record_resolution",
            side_effect=RuntimeError("disk full"),
        ):
            context = _FakeContext(self._completed_state())
            result = record_completed_run_to_memory(context)
        self.assertFalse(result["memory_recorded"])
        self.assertIn("RuntimeError", context.state["memory_record_error"])


if __name__ == "__main__":
    unittest.main()