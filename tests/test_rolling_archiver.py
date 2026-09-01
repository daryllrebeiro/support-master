"""Tests for Rolling Case Archiver, zip consolidation, and tenant-scoped archival retrieval."""

import json
from pathlib import Path
import tempfile
import unittest
import uuid
import zipfile

from supportmaster.archival.archiver import (
    ArchiveRecord,
    CaseArtifactBundle,
    RollingCaseArchiver,
)
from supportmaster.persistence.run_store import SQLiteRunStore


class TestRollingCaseArchiver(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_runs.db"
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.store = SQLiteRunStore(self.db_path)
        self.archiver = RollingCaseArchiver(self.store, base_dir=self.data_dir, batch_size=10)
        self.tenant_a = "tenant-acme"
        self.tenant_b = "tenant-beta"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_mock_bundle(self, tenant_id: str, case_idx: int) -> CaseArtifactBundle:
        return CaseArtifactBundle(
            case_id=f"case-{case_idx:03d}-{uuid.uuid4().hex[:6]}",
            tenant_id=tenant_id,
            title=f"Incident #{case_idx} — Outage in Service",
            external_id=f"INC-{case_idx:03d}",
            service="backend-payment-engine",
            severity="P1",
            status="COMPLETED",
            issue_description=f"Stack trace and reproduction logs for incident {case_idx}",
            root_cause={"defect": f"Null pointer on line {case_idx * 10}"},
            remediation_plan={"approach": f"Add non-null guard in handler {case_idx}"},
            code_patch_diff=f"--- a/handler.py\n+++ b/handler.py\n@@ -10,1 +10,2 @@\n+ if not item: return\n",
            validation_results={"test_suite_passed": True, "assertions": 5},
            gate_history=[
                {"gate": "IMPLEMENTATION_AUTHORIZATION", "route": "READY_FOR_IMPLEMENTATION"},
                {"gate": "PUBLISH_AUTHORIZATION", "route": "APPROVED"},
            ],
        )

    def test_save_case_bundle_creates_expected_artifact_files(self) -> None:
        bundle = self._create_mock_bundle(self.tenant_a, 1)
        case_dir = self.archiver.save_case_bundle(bundle)

        self.assertTrue(case_dir.exists())
        self.assertTrue((case_dir / "case.json").exists())
        self.assertTrue((case_dir / "issue_ticket.md").exists())
        self.assertTrue((case_dir / "patch.diff").exists())
        self.assertTrue((case_dir / "investigation_plan.json").exists())
        self.assertTrue((case_dir / "validation.json").exists())
        self.assertTrue((case_dir / "gate_history.json").exists())

        saved_case_data = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_case_data["case_id"], bundle.case_id)
        self.assertEqual(saved_case_data["external_id"], "INC-001")

    def test_rolling_archive_automatically_creates_zip_when_ten_cases_reach_threshold(self) -> None:
        # Save 9 cases (below batch threshold of 10)
        for i in range(1, 10):
            bundle = self._create_mock_bundle(self.tenant_a, i)
            self.archiver.save_case_bundle(bundle)

        records = self.archiver.check_and_archive(self.tenant_a)
        self.assertEqual(len(records), 0)
        self.assertEqual(len(self.store.list_archives(self.tenant_a)), 0)

        # Save 10th case (hits threshold of 10)
        bundle_10 = self._create_mock_bundle(self.tenant_a, 10)
        self.archiver.save_case_bundle(bundle_10)

        records = self.archiver.check_and_archive(self.tenant_a)
        self.assertEqual(len(records), 1)
        archive_record = records[0]

        self.assertEqual(archive_record.case_count, 10)
        self.assertEqual(archive_record.tenant_id, self.tenant_a)
        self.assertTrue(archive_record.filename.startswith("archive_"))
        self.assertTrue(archive_record.filename.endswith("_batch_001.zip"))
        self.assertTrue(Path(archive_record.filepath).exists())

        # Verify zip archive contents
        with zipfile.ZipFile(archive_record.filepath, "r") as zipf:
            namelist = zipf.namelist()
            self.assertIn("ARCHIVE_MANIFEST.json", namelist)
            manifest = json.loads(zipf.read("ARCHIVE_MANIFEST.json").decode("utf-8"))
            self.assertEqual(manifest["case_count"], 10)
            self.assertEqual(manifest["tenant_id"], self.tenant_a)

            # Check that case files exist inside the zip
            for cid in manifest["case_ids"]:
                self.assertIn(f"{cid}/case.json", namelist)
                self.assertIn(f"{cid}/patch.diff", namelist)

        # Verify that uncompressed case folders have been pruned from active cases directory
        cases_dir = self.data_dir / "cases" / self.tenant_a
        for cid in archive_record.case_ids:
            self.assertFalse((cases_dir / cid).exists(), f"Scratch case directory {cid} should have been pruned after zipping.")

        # Verify SQLite index
        db_archives = self.store.list_archives(self.tenant_a)
        self.assertEqual(len(db_archives), 1)
        self.assertEqual(db_archives[0]["archive_id"], archive_record.archive_id)

    def test_tenant_boundary_isolation_on_archive_retrieval(self) -> None:
        # Create 10 cases for Tenant A and archive
        for i in range(1, 11):
            bundle_a = self._create_mock_bundle(self.tenant_a, i)
            self.archiver.save_case_bundle(bundle_a)
        record_a = self.archiver.check_and_archive(self.tenant_a)[0]

        # Create 10 cases for Tenant B and archive
        for j in range(1, 11):
            bundle_b = self._create_mock_bundle(self.tenant_b, j)
            self.archiver.save_case_bundle(bundle_b)
        record_b = self.archiver.check_and_archive(self.tenant_b)[0]

        # Tenant A listing only sees Tenant A's archives
        list_a = self.store.list_archives(self.tenant_a)
        self.assertEqual(len(list_a), 1)
        self.assertEqual(list_a[0]["archive_id"], record_a.archive_id)

        # Tenant B listing only sees Tenant B's archives
        list_b = self.store.list_archives(self.tenant_b)
        self.assertEqual(len(list_b), 1)
        self.assertEqual(list_b[0]["archive_id"], record_b.archive_id)

        # Tenant B cannot access Tenant A's archive
        self.assertIsNone(self.store.get_archive(self.tenant_b, record_a.archive_id))
        # Tenant A cannot access Tenant B's archive
        self.assertIsNone(self.store.get_archive(self.tenant_a, record_b.archive_id))

    def test_force_archive_manually_consolidates_pending_cases(self) -> None:
        # Save 3 cases
        for i in range(1, 4):
            bundle = self._create_mock_bundle(self.tenant_a, i)
            self.archiver.save_case_bundle(bundle)

        # Force archive before reaching 10 cases
        record = self.archiver.force_archive(self.tenant_a)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.case_count, 3)
        self.assertTrue(Path(record.filepath).exists())

        # Calling force_archive again when no pending cases exist returns None
        self.assertIsNone(self.archiver.force_archive(self.tenant_a))


if __name__ == "__main__":
    unittest.main()
