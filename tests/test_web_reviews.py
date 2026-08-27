import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from supportmaster.web import SupportMasterHandler
from supportmaster.persistence import SQLiteRunStore
from supportmaster.workflow_state import SupportMasterState
from supportmaster.intake import CaseIntakeService
from supportmaster.organization import OrganizationContextService
from supportmaster.models.organization import OrganizationProfile

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


class WebReviewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_run.db"
        self.session_db_path = Path(self.temp_dir.name) / "test_session.db"
        
        self.old_run_db = os.environ.get("SUPPORTMASTER_RUN_DB")
        self.old_session_db = os.environ.get("SUPPORTMASTER_SESSION_DB")
        self.old_keys = os.environ.get("SUPPORTMASTER_API_KEYS")
        self.old_auth_mode = os.environ.get("SUPPORTMASTER_AUTH_MODE")
        
        os.environ["SUPPORTMASTER_RUN_DB"] = str(self.db_path)
        os.environ["SUPPORTMASTER_SESSION_DB"] = str(self.session_db_path)
        os.environ["SUPPORTMASTER_AUTH_MODE"] = "REQUIRED"
        os.environ["SUPPORTMASTER_API_KEYS"] = (
            "secret|operator|demo-acme|RUN_EXECUTE,AUDIT_READ;"
            "secret-bad|operator|demo-bad|RUN_EXECUTE"
        )
        
        # Override global AUTHENTICATOR settings to pick up new mock keys
        from supportmaster.security import load_security_settings, Authenticator
        import supportmaster.web
        self.old_authenticator = supportmaster.web.AUTHENTICATOR
        supportmaster.web.AUTHENTICATOR = Authenticator(load_security_settings())
        
        self.store = SQLiteRunStore(self.db_path)
        OrganizationContextService(self.store).save(
            OrganizationProfile(
                organization_id="demo-acme",
                display_name="Acme",
                products=["Identity Gateway"],
                services=["Identity Gateway"],
            )
        )
        
        self.case = CaseIntakeService(self.store).ingest(
            {"title": "Invoice fails", "description": "OutOfMemoryError", "id": "CASE-1"},
            source_system="manual",
            tenant_id="demo-acme"
        ).case
        
        self.state = SupportMasterState(run_id="run-1", tenant_id="demo-acme")
        self.state.case_id = self.case.case_id
        self.store.create_run(self.state)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        import supportmaster.web
        supportmaster.web.AUTHENTICATOR = self.old_authenticator
        
        if self.old_run_db is not None:
            os.environ["SUPPORTMASTER_RUN_DB"] = self.old_run_db
        else:
            os.environ.pop("SUPPORTMASTER_RUN_DB", None)
            
        if self.old_session_db is not None:
            os.environ["SUPPORTMASTER_SESSION_DB"] = self.old_session_db
        else:
            os.environ.pop("SUPPORTMASTER_SESSION_DB", None)
            
        if self.old_keys is not None:
            os.environ["SUPPORTMASTER_API_KEYS"] = self.old_keys
        else:
            os.environ.pop("SUPPORTMASTER_API_KEYS", None)
            
        if self.old_auth_mode is not None:
            os.environ["SUPPORTMASTER_AUTH_MODE"] = self.old_auth_mode
        else:
            os.environ.pop("SUPPORTMASTER_AUTH_MODE", None)

    def test_get_fixtures_list(self) -> None:
        headers = {
            "X-SupportMaster-API-Key": "secret"
        }
        handler = MockSupportMasterHandler("/api/fixtures", method="GET", headers=headers)
        handler.do_GET()
        
        self.assertEqual(handler.response_code, 200)
        res = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("fixtures", res)
        self.assertIn("healthcare_notifications", res["fixtures"])

    def test_get_fixture_detail(self) -> None:
        headers = {
            "X-SupportMaster-API-Key": "secret"
        }
        handler = MockSupportMasterHandler("/api/fixtures/retail_inventory", method="GET", headers=headers)
        handler.do_GET()
        
        self.assertEqual(handler.response_code, 200)
        res = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertEqual(res["case_id"], "STORE-204")

    def test_get_fixture_invalid_traversal(self) -> None:
        headers = {
            "X-SupportMaster-API-Key": "secret"
        }
        handler = MockSupportMasterHandler("/api/fixtures/.._cases_retail_inventory", method="GET", headers=headers)
        handler.do_GET()
        self.assertEqual(handler.response_code, 404)

    @patch("supportmaster.web.run_resumed_worker_sync")
    def test_post_review_decide_and_resume(self, mock_run_sync) -> None:
        task, token = self.store.create_review_task(
            "run-1",
            reason="Review implementation details",
            allowed_scopes=["PUBLISH"],
            resume_condition="Approved publish plan"
        )
        
        self.store.enqueue_task(
            "run-1",
            task_name="adk_workflow",
            idempotency_key="run-1:adk_workflow",
            payload={"issue": "reproduction text", "model_name": "gemini-3.5-flash"},
        )
        
        headers = {
            "X-SupportMaster-API-Key": "secret"
        }
        payload = {
            "reviewer": "Alice",
            "decision": "APPROVE",
            "resume_token": token,
            "approved_scopes": ["PUBLISH"],
            "comment": "Scope matches plan"
        }
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Length"] = str(len(body))
        
        handler = MockSupportMasterHandler(
            f"/api/reviews/{task.task_id}/decide",
            method="POST",
            body=body,
            headers=headers
        )
        handler.do_POST()
        
        resp_body = handler.wfile.getvalue().decode("utf-8")
        self.assertEqual(handler.response_code, 200, f"Expected 200, got {handler.response_code}. Body: {resp_body}")
        res = json.loads(resp_body)
        self.assertEqual(res["status"], "RESUMED")
        
        db_task = self.store.get_review_task(task.task_id)
        self.assertEqual(db_task.status, "RESUMED")
        self.assertEqual(db_task.decision.reviewer, "Alice")
        
        state = self.store.load_state("run-1")
        self.assertEqual(state.authorizations[0].scope, "PUBLISH")

    def test_post_review_decide_cross_tenant_denied(self) -> None:
        task, token = self.store.create_review_task(
            "run-1",
            reason="Review details",
            allowed_scopes=["PUBLISH"],
            resume_condition="Approved"
        )
        
        headers = {
            "X-SupportMaster-API-Key": "secret-bad"
        }
        payload = {
            "reviewer": "Mallory",
            "decision": "APPROVE",
            "resume_token": token,
            "approved_scopes": ["PUBLISH"],
            "comment": "Steal control"
        }
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Length"] = str(len(body))
        
        handler = MockSupportMasterHandler(
            f"/api/reviews/{task.task_id}/decide",
            method="POST",
            body=body,
            headers=headers
        )
        handler.do_POST()
        
        self.assertEqual(handler.response_code, 400)
        res = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertIn("tenant", res["error"].lower())


    def test_post_review_decide_csrf_protection(self) -> None:
        task, token = self.store.create_review_task(
            "run-1",
            reason="Review details",
            allowed_scopes=["PUBLISH"],
            resume_condition="Approved"
        )
        
        payload = {
            "reviewer": "Alice",
            "decision": "APPROVE",
            "resume_token": token,
            "approved_scopes": ["PUBLISH"],
            "comment": "CSRF test"
        }
        body = json.dumps(payload).encode("utf-8")
        
        # 1. Request with Cookie but missing X-CSRF-Token header -> Rejected
        headers_no_token = {
            "X-SupportMaster-API-Key": "secret",
            "Cookie": "csrf-token=csrf12345",
            "Content-Length": str(len(body))
        }
        handler_no_token = MockSupportMasterHandler(
            f"/api/reviews/{task.task_id}/decide",
            method="POST",
            body=body,
            headers=headers_no_token
        )
        handler_no_token.do_POST()
        self.assertEqual(handler_no_token.response_code, 403)
        res_no_token = json.loads(handler_no_token.wfile.getvalue().decode("utf-8"))
        self.assertIn("csrf", res_no_token["error"].lower())

        # 2. Request with Cookie and incorrect X-CSRF-Token header -> Rejected
        headers_wrong_token = {
            "X-SupportMaster-API-Key": "secret",
            "Cookie": "csrf-token=csrf12345",
            "X-CSRF-Token": "csrf54321",
            "Content-Length": str(len(body))
        }
        handler_wrong_token = MockSupportMasterHandler(
            f"/api/reviews/{task.task_id}/decide",
            method="POST",
            body=body,
            headers=headers_wrong_token
        )
        handler_wrong_token.do_POST()
        self.assertEqual(handler_wrong_token.response_code, 403)

        # 3. Request with Cookie and correct X-CSRF-Token header -> Accepted
        headers_correct_token = {
            "X-SupportMaster-API-Key": "secret",
            "Cookie": "csrf-token=csrf12345",
            "X-CSRF-Token": "csrf12345",
            "Content-Length": str(len(body))
        }
        with patch("supportmaster.web.run_resumed_worker_sync") as mock_resume:
            handler_correct_token = MockSupportMasterHandler(
                f"/api/reviews/{task.task_id}/decide",
                method="POST",
                body=body,
                headers=headers_correct_token
            )
            handler_correct_token.do_POST()
            self.assertEqual(handler_correct_token.response_code, 200)


if __name__ == "__main__":
    unittest.main()
