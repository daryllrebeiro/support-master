"""Phase C: diagnose-before-retry self-healing loop."""

import unittest

from supportmaster.agents.code_change_agent import code_change_agent
from supportmaster.workflows.publishing_gate_workflow import (
    create_publishing_gate_workflow,
    diagnose_validation_failure,
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


class SelfHealingGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = create_publishing_gate_workflow("gemini-3.6-flash")
        self.graph = self.workflow.graph
        assert self.graph is not None

    def test_retry_routes_through_failure_diagnosis(self) -> None:
        self.assertEqual(
            self.graph.get_next_pending_nodes(
                "validation_testing_gate", "RETRY_IMPLEMENTATION"
            ),
            ["failure_diagnosis"],
        )

    def test_diagnosis_feeds_back_into_code_change(self) -> None:
        self.assertEqual(
            self.graph.get_next_pending_nodes("failure_diagnosis", None),
            ["code_change_agent"],
        )

    def test_ready_for_publish_path_unchanged(self) -> None:
        self.assertEqual(
            self.graph.get_next_pending_nodes(
                "validation_testing_gate", "READY_FOR_PUBLISH"
            ),
            ["publish_agent"],
        )


class DiagnosisNodeTests(unittest.TestCase):
    def test_first_retry_directs_reproduce_and_isolate(self) -> None:
        context = _FakeContext(
            {
                "validation_failures": [
                    {"attempt": 1, "warnings": ["TESTS_FAILED"]}
                ]
            }
        )
        result = diagnose_validation_failure(context)
        self.assertEqual(result["attempt"], 1)
        self.assertIn("REPRODUCE_AND_ISOLATE", result["directive"])
        self.assertIn("Do NOT repeat", result["directive"])
        self.assertEqual(
            context.state["healing_diagnosis"]["attempt"], 1
        )

    def test_second_retry_narrows_diff_scope(self) -> None:
        context = _FakeContext(
            {
                "validation_failures": [
                    {"attempt": 1, "warnings": ["TESTS_FAILED"]},
                    {"attempt": 2, "warnings": ["SCOPE_DRIFT"]},
                ]
            }
        )
        result = diagnose_validation_failure(context)
        self.assertEqual(result["attempt"], 2)
        self.assertIn("NARROW_DIFF_SCOPE", result["directive"])

    def test_third_retry_directs_alternative_approach(self) -> None:
        context = _FakeContext(
            {
                "validation_failures": [
                    {"attempt": 1, "warnings": ["A"]},
                    {"attempt": 2, "warnings": ["B"]},
                    {"attempt": 3, "warnings": ["C"]},
                ]
            }
        )
        result = diagnose_validation_failure(context)
        self.assertEqual(result["attempt"], 3)
        self.assertIn("ALTERNATIVE_APPROACH", result["directive"])

    def test_prior_warnings_are_capped_at_three(self) -> None:
        warnings = [{"attempt": i, "warnings": [f"W{i}"]} for i in range(1, 6)]
        context = _FakeContext({"validation_failures": warnings})
        result = diagnose_validation_failure(context)
        self.assertEqual(len(result["prior_failure_warnings"]), 3)
        self.assertEqual(result["prior_failure_warnings"][-1], ["W5"])

    def test_empty_failures_still_produce_baseline_diagnosis(self) -> None:
        context = _FakeContext({})
        result = diagnose_validation_failure(context)
        self.assertEqual(result["attempt"], 0)
        self.assertIn("REPRODUCE_AND_ISOLATE", result["directive"])


class CodeChangeInstructionTests(unittest.TestCase):
    def test_instruction_declares_self_healing_context(self) -> None:
        instruction = code_change_agent.instruction
        self.assertIn("SELF-HEALING CONTEXT", instruction)
        self.assertIn("healing_diagnosis", instruction)
        self.assertIn("prior_failure_warnings", instruction)

    def test_instruction_requires_strategy_change_on_retry(self) -> None:
        instruction = code_change_agent.instruction
        self.assertIn("DIFFERENTLY", instruction)
        self.assertIn("do not repeat the previous strategy", instruction)


if __name__ == "__main__":
    unittest.main()