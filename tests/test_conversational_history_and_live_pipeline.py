import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from supportmaster.models.support_case import SupportCase
from supportmaster.persistence import SQLiteRunStore
from supportmaster.web import SupportMasterHandler, AUTHOR_TO_STAGE, _CASE_QA_SESSIONS
from supportmaster.memory.case_store import CaseMemoryStore


class MockSupportMasterHandler(SupportMasterHandler):
    def __init__(self, path, method="GET", body=b"", headers=None):
        self.path = path
        self.command = method
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.headers = headers or {}
        self.response_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.response_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def log_message(self, format, *args):
        pass


class ConversationalHistoryAndLivePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_runs.db"
        self.memory_db_path = Path(self.temp_dir.name) / "test_memory.db"
        os.environ["SUPPORTMASTER_RUN_DB"] = str(self.db_path)
        os.environ["SUPPORTMASTER_MEMORY_DB"] = str(self.memory_db_path)
        _CASE_QA_SESSIONS.clear()

        # Seed sample cases
        self.store = SQLiteRunStore(self.db_path)
        self.case1 = SupportCase(
            case_id="SUP-1001",
            title="Invoice calculation overflow on enterprise tenants",
            description="Users encounter OutOfMemoryError when running tax calculation for large accounts.",
            tenant_id="default",
            source_system="JIRA",
        )
        self.store.save_case(self.case1)

        # Seed FTS5 memory
        self.mem_store = CaseMemoryStore(self.memory_db_path)
        self.mem_store.record(
            case_id="SUP-0999",
            tenant_id="default",
            title="Invoice export memory leak",
            description="OutOfMemoryError in invoice csv serialization worker",
            root_cause="Unbounded stream buffer",
            resolution_summary="Added buffered chunking and flushed periodically",
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        _CASE_QA_SESSIONS.clear()

    def test_author_to_stage_mapping(self):
        self.assertEqual(AUTHOR_TO_STAGE["ticket_analysis_agent"], "INTAKE")
        self.assertEqual(AUTHOR_TO_STAGE["investigation_agent"], "INVESTIGATION")
        self.assertEqual(AUTHOR_TO_STAGE["duplicate_work_agent"], "DUPLICATE_GATES")
        self.assertEqual(AUTHOR_TO_STAGE["code_change_agent"], "REMEDIATION")
        self.assertEqual(AUTHOR_TO_STAGE["validation_agent"], "VERIFICATION")
        self.assertEqual(AUTHOR_TO_STAGE["publish_agent"], "PUBLISH")

    @patch("threading.Thread")
    def test_chat_fire_and_stream(self, mock_thread):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        body = json.dumps({"message": "Invoice calculation fails on EU region", "model": "gemini-2.5-flash"}).encode("utf-8")
        headers = {
            "Content-Length": str(len(body)),
            "X-SupportMaster-API-Key": "secret|operator|demo-acme|RUN_EXECUTE",
        }
        handler = MockSupportMasterHandler("/api/chat", method="POST", body=body, headers=headers)
        handler.do_POST()

        self.assertEqual(handler.response_code, 202)
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(response["status"], "STARTED")
        self.assertTrue("run_id" in response)
        self.assertTrue("case_id" in response)
        self.assertTrue(mock_thread_instance.start.called)

    def test_case_evidence_qa_endpoint(self):
        body = json.dumps({"question": "What is the symptom of this case?"}).encode("utf-8")
        headers = {
            "Content-Length": str(len(body)),
            "X-SupportMaster-API-Key": "secret|operator|demo-acme|RUN_EXECUTE",
        }
        handler = MockSupportMasterHandler("/api/cases/SUP-1001/ask", method="POST", body=body, headers=headers)
        handler.do_POST()

        self.assertEqual(handler.response_code, 200)
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue("answer" in response)
        self.assertEqual(response["case_id"], "SUP-1001")

        # Verify session history recorded
        self.assertEqual(len(_CASE_QA_SESSIONS[("SUP-1001", "default")]), 1)

    def test_case_related_endpoint_and_lineage(self):
        # Create a child rerun case
        rerun_case = SupportCase(
            case_id="SUP-1002",
            title="Rerun: Invoice calculation overflow",
            description="## Prior Summary\nRe-running with new customer export logs.\n## Operator Note\nCustomer confirmed 4.18.3.",
            tenant_id="default",
            source_system="RERUN",
            metadata={"parent_case_id": "SUP-1001"},
        )
        self.store.save_case(rerun_case)

        # Check related on parent case (should find child rerun + FTS memory)
        headers = {
            "X-SupportMaster-API-Key": "secret|operator|demo-acme|AUDIT_READ",
        }
        handler = MockSupportMasterHandler("/api/cases/SUP-1001/related", method="GET", headers=headers)
        handler.do_GET()

        self.assertEqual(handler.response_code, 200)
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue("related" in response)
        related = response["related"]
        self.assertTrue(any(r["case_id"] == "SUP-1002" and r["relationship"] == "CHILD_RERUN" for r in related))
        self.assertTrue(any(r["case_id"] == "SUP-0999" and r["relationship"] == "SIMILAR_PATTERN" for r in related))

        # Check related on child case (should find parent rerun)
        handler2 = MockSupportMasterHandler("/api/cases/SUP-1002/related", method="GET", headers=headers)
        handler2.do_GET()
        self.assertEqual(handler2.response_code, 200)
        response2 = json.loads(handler2.wfile.getvalue().decode("utf-8"))
        self.assertTrue(any(r["case_id"] == "SUP-1001" and r["relationship"] == "PARENT_RERUN" for r in response2["related"]))


if __name__ == "__main__":
    unittest.main()
