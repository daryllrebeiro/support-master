"""Phase F: Gemma triage adapter — advisory, fail-open classification."""

import os
import unittest
from unittest.mock import patch

from supportmaster.triage import (
    TRIAGE_MODEL,
    TriageResult,
    classify_heuristic,
    classify_ticket,
)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    def generate_content(self, *, model, contents, config):  # noqa: ARG002
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.models = _FakeModels(response)


class HeuristicTests(unittest.TestCase):
    def test_critical_keywords_escalate(self) -> None:
        result = classify_heuristic("Production outage: customers cannot login")
        self.assertEqual(result.severity, "CRITICAL")
        self.assertEqual(result.engine, "heuristic")

    def test_memory_error_maps_to_high(self) -> None:
        result = classify_heuristic(
            "java.lang.OutOfMemoryError during invoice export"
        )
        self.assertEqual(result.severity, "HIGH")
        self.assertEqual(result.category, "bug_report")

    def test_duplicate_phrasing_is_flagged(self) -> None:
        result = classify_heuristic("This is a duplicate of SUP-1234, same issue")
        self.assertTrue(result.duplicate_suspected)

    def test_blank_ticket_is_low(self) -> None:
        self.assertEqual(classify_heuristic("   ").severity, "LOW")


class ClassifyTicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(os.environ, {}, clear=False)
        self._env_patcher.start()
        os.environ.pop("GOOGLE_API_KEY", None)

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def test_no_api_key_falls_back_to_heuristic(self) -> None:
        result = classify_ticket("Export crashes with OutOfMemoryError")
        self.assertEqual(result.engine, "heuristic")
        self.assertIsNone(result.model_name)

    def test_gemma_response_is_parsed_and_stamped(self) -> None:
        payload = (
            '{"severity": "HIGH", "category": "bug_report", '
            '"duplicate_suspected": false, "rationale": "OOM on export path"}'
        )
        client = _FakeClient(_FakeResponse(payload))
        result = classify_ticket("OutOfMemoryError", client=client)
        self.assertEqual(result.engine, "gemma")
        self.assertEqual(result.model_name, TRIAGE_MODEL)
        self.assertEqual(result.severity, "HIGH")

    def test_gemma_failure_fails_open_to_heuristic(self) -> None:
        client = _FakeClient(RuntimeError("429 quota exceeded"))
        result = classify_ticket("data loss in production", client=client)
        self.assertEqual(result.engine, "heuristic")
        self.assertEqual(result.severity, "CRITICAL")

    def test_malformed_gemma_output_fails_open(self) -> None:
        client = _FakeClient(_FakeResponse("not json at all"))
        result = classify_ticket("timeout when exporting", client=client)
        self.assertEqual(result.engine, "heuristic")


class AdvisoryOnlyTests(unittest.TestCase):
    def test_triage_result_carries_no_authorization_fields(self) -> None:
        fields = set(TriageResult.model_fields)
        forbidden = {"authorization", "grant", "route", "gate", "approved"}
        self.assertFalse(fields & forbidden)


if __name__ == "__main__":
    unittest.main()