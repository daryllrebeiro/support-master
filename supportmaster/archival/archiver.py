"""Rolling Case Archiver for bundling completed support issue artifacts into dated zip archives."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid
import zipfile

from pydantic import BaseModel, Field

from ..persistence.run_store import SQLiteRunStore


class CaseArtifactBundle(BaseModel):
    """Encapsulates all runtime artifacts generated during a case investigation."""

    case_id: str
    tenant_id: str
    title: str = "Support Case"
    external_id: str | None = None
    service: str | None = None
    severity: str = "P2"
    status: str = "COMPLETED"
    issue_description: str = ""
    root_cause: dict[str, Any] = Field(default_factory=dict)
    remediation_plan: dict[str, Any] = Field(default_factory=dict)
    code_patch_diff: str = ""
    validation_results: dict[str, Any] = Field(default_factory=dict)
    gate_history: list[dict[str, Any]] = Field(default_factory=list)
    pull_request_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ArchiveRecord(BaseModel):
    """Metadata record for a compressed multi-case rolling archive."""

    archive_id: str = Field(default_factory=lambda: f"arch-{uuid.uuid4().hex[:12]}")
    tenant_id: str
    filename: str
    filepath: str
    case_count: int
    start_date: str
    end_date: str
    size_bytes: int
    case_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RollingCaseArchiver:
    """Manages case directory creation, artifact persistence, and rolling 10-case zip consolidation."""

    def __init__(
        self,
        run_store: SQLiteRunStore,
        base_dir: str | Path | None = None,
        batch_size: int = 10,
    ) -> None:
        self.run_store = run_store
        self.base_dir = Path(base_dir or os.getenv("SUPPORTMASTER_DATA_DIR", "data"))
        self.batch_size = batch_size

    def _get_cases_dir(self, tenant_id: str) -> Path:
        path = self.base_dir / "cases" / tenant_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _get_archives_dir(self, tenant_id: str) -> Path:
        path = self.base_dir / "archives" / tenant_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_case_bundle(self, bundle: CaseArtifactBundle) -> Path:
        """Save a case's full diagnostic, patch, and gate artifacts to its case folder."""
        case_dir = self._get_cases_dir(bundle.tenant_id) / bundle.case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        # 1. Primary case definition and ticket
        (case_dir / "case.json").write_text(
            json.dumps(bundle.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

        # 2. Raw ticket description
        if bundle.issue_description:
            (case_dir / "issue_ticket.md").write_text(
                bundle.issue_description,
                encoding="utf-8",
            )

        # 3. Source code patch / diff
        if bundle.code_patch_diff:
            (case_dir / "patch.diff").write_text(
                bundle.code_patch_diff,
                encoding="utf-8",
            )

        # 4. Investigation & Planning assessments
        investigation_payload = {
            "root_cause": bundle.root_cause,
            "remediation_plan": bundle.remediation_plan,
        }
        (case_dir / "investigation_plan.json").write_text(
            json.dumps(investigation_payload, indent=2),
            encoding="utf-8",
        )

        # 5. Validation assertions & test outcomes
        if bundle.validation_results:
            (case_dir / "validation.json").write_text(
                json.dumps(bundle.validation_results, indent=2),
                encoding="utf-8",
            )

        # 6. Cryptographic gate receipts and decision trail
        if bundle.gate_history:
            (case_dir / "gate_history.json").write_text(
                json.dumps(bundle.gate_history, indent=2),
                encoding="utf-8",
            )

        return case_dir

    def get_unarchived_case_ids(self, tenant_id: str) -> list[str]:
        """Inspect the filesystem and run_store for completed unarchived cases."""
        cases_dir = self._get_cases_dir(tenant_id)
        if not cases_dir.exists():
            return []

        archived_ids = set(self.run_store.get_archived_case_ids(tenant_id))
        all_case_dirs = [
            p.name for p in cases_dir.iterdir()
            if p.is_dir() and (p / "case.json").exists()
        ]
        unarchived = [cid for cid in all_case_dirs if cid not in archived_ids]
        # Sort deterministically by folder modification / creation time
        unarchived.sort(key=lambda cid: (cases_dir / cid).stat().st_mtime)
        return unarchived

    def create_archive_bundle(
        self,
        tenant_id: str,
        case_ids: list[str],
        batch_number: int | None = None,
    ) -> ArchiveRecord | None:
        """Consolidate given case directories into a parent folder and create a dated zip file."""
        if not case_ids:
            return None

        cases_dir = self._get_cases_dir(tenant_id)
        archives_dir = self._get_archives_dir(tenant_id)
        existing_archives_count = len(self.run_store.list_archives(tenant_id))
        batch_idx = batch_number or (existing_archives_count + 1)

        # Read timestamps from cases to construct the date-based name
        timestamps: list[datetime] = []
        for cid in case_ids:
            case_file = cases_dir / cid / "case.json"
            if case_file.exists():
                try:
                    data = json.loads(case_file.read_text(encoding="utf-8"))
                    created = data.get("created_at") or data.get("resolved_at")
                    if created:
                        timestamps.append(datetime.fromisoformat(created))
                except Exception:
                    pass

        now = datetime.now(timezone.utc)
        if timestamps:
            start_date = min(timestamps).strftime("%Y%m%d")
            end_date = max(timestamps).strftime("%Y%m%d")
        else:
            start_date = now.strftime("%Y%m%d")
            end_date = now.strftime("%Y%m%d")

        archive_filename = f"archive_{start_date}_to_{end_date}_batch_{batch_idx:03d}.zip"
        archive_path = archives_dir / archive_filename

        # Consolidate into batch staging parent folder
        batch_folder_name = f"batch_{start_date}_to_{end_date}_{batch_idx:03d}"
        temp_batch_dir = archives_dir / f".tmp_{batch_folder_name}_{uuid.uuid4().hex[:6]}"
        temp_batch_dir.mkdir(parents=True, exist_ok=True)

        try:
            for cid in case_ids:
                src = cases_dir / cid
                if src.exists() and src.is_dir():
                    shutil.copytree(src, temp_batch_dir / cid, dirs_exist_ok=True)

            # Write manifest into the zip archive root
            manifest = {
                "tenant_id": tenant_id,
                "batch_number": batch_idx,
                "case_count": len(case_ids),
                "case_ids": case_ids,
                "start_date": start_date,
                "end_date": end_date,
                "archived_at": now.isoformat(),
            }
            (temp_batch_dir / "ARCHIVE_MANIFEST.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )

            # Create the zip archive
            with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(temp_batch_dir):
                    for file in files:
                        full_path = Path(root) / file
                        rel_path = full_path.relative_to(temp_batch_dir)
                        zipf.write(full_path, arcname=str(rel_path))

            file_size = archive_path.stat().st_size

            record = ArchiveRecord(
                tenant_id=tenant_id,
                filename=archive_filename,
                filepath=str(archive_path.resolve()),
                case_count=len(case_ids),
                start_date=start_date,
                end_date=end_date,
                size_bytes=file_size,
                case_ids=case_ids,
                created_at=now,
            )

            # Persist record in SQLite
            self.run_store.save_archive(record)
            self.run_store.mark_cases_archived(tenant_id, case_ids, record.archive_id)

            # Prune individual uncompressed case folders that are now archived
            for cid in case_ids:
                src = cases_dir / cid
                if src.exists() and src.is_dir():
                    shutil.rmtree(src, ignore_errors=True)

            return record
        finally:
            if temp_batch_dir.exists():
                shutil.rmtree(temp_batch_dir, ignore_errors=True)

    def check_and_archive(self, tenant_id: str) -> list[ArchiveRecord]:
        """Check if pending unarchived cases have reached the rolling batch threshold (default 10)."""
        unarchived = self.get_unarchived_case_ids(tenant_id)
        created_records: list[ArchiveRecord] = []

        while len(unarchived) >= self.batch_size:
            batch = unarchived[: self.batch_size]
            unarchived = unarchived[self.batch_size :]
            record = self.create_archive_bundle(tenant_id, batch)
            if record:
                created_records.append(record)

        return created_records

    def force_archive(self, tenant_id: str) -> ArchiveRecord | None:
        """Manually compress whatever unarchived completed cases exist into an archive."""
        unarchived = self.get_unarchived_case_ids(tenant_id)
        if not unarchived:
            return None
        return self.create_archive_bundle(tenant_id, unarchived)
