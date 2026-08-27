import tempfile
import unittest
from pathlib import Path

from supportmaster.models.organization import OrganizationProfile, WorkflowPolicy
from supportmaster.organization import OrganizationContextService
from supportmaster.persistence import SQLiteRunStore


class OrganizationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteRunStore(Path(self.temp_dir.name) / "runs.db")
        self.service = OrganizationContextService(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ensure_creates_neutral_default_context(self) -> None:
        profile = self.service.ensure("org-a", display_name="Acme Support")
        self.assertEqual(profile.organization_id, "org-a")
        self.assertEqual(profile.display_name, "Acme Support")
        self.assertTrue(profile.workflow_policy.require_duplicate_check)

    def test_update_changes_config_without_changing_identity(self) -> None:
        self.service.ensure("org-a")
        updated = self.service.update(
            "org-a",
            {
                "products": ["Payments"],
                "terminology": {"incident": "service event"},
                "workflow_policy": WorkflowPolicy(allow_autonomous_code_change=True).model_dump(),
            },
        )
        self.assertEqual(updated.organization_id, "org-a")
        self.assertEqual(updated.products, ["Payments"])
        self.assertTrue(updated.workflow_policy.allow_autonomous_code_change)

    def test_suspended_context_round_trips(self) -> None:
        profile = OrganizationProfile(organization_id="org-b", display_name="Beta", status="SUSPENDED")
        self.service.save(profile)
        self.assertEqual(self.service.get("org-b").status, "SUSPENDED")
        self.assertEqual(len(self.store.list_organizations(status="SUSPENDED")), 1)

    def test_workspace_connection_secret_is_redacted_on_serialization(self) -> None:
        from supportmaster.models.organization import WorkspaceConnection
        profile = OrganizationProfile(
            organization_id="org-c",
            display_name="Charlie",
            workspace_connections=[
                WorkspaceConnection(
                    provider="github",
                    workspace_id="acme-corp",
                    secret_ref="env:LIVE_PROD_TOKEN_12345",
                )
            ],
        )
        saved = self.service.save(profile)
        payload = saved.model_dump(mode="json")
        for connection in payload.get("workspace_connections", []):
            connection["secret_ref"] = "***REDACTED***"
        self.assertEqual(payload["workspace_connections"][0]["secret_ref"], "***REDACTED***")
        self.assertNotIn("LIVE_PROD_TOKEN_12345", str(payload))


if __name__ == "__main__":
    unittest.main()
