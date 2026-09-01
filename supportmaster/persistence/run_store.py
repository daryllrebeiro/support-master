"""SQLite-backed run snapshots, events, review tasks, and resume tokens."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Literal

from ..models.control import AuthorizationScope
from ..models.durable_task import (
    DurableTask,
    ReplayPlan,
    RunControl,
    RunControlStatus,
    TaskCheckpoint,
    TaskStatus,
)
from ..models.human_review import HumanReviewDecision, HumanReviewTask
from ..models.run_event import RunEvent, RunSnapshot
from ..models.organization import OrganizationProfile
from ..models.investigation_artifacts import InvestigationSummary
from ..models.planning import PlanningAssessment
from ..models.resolution_bundle import ResolutionBundle
from ..models.support_case import SupportCase
from ..telemetry.contracts import TelemetryEvent
from ..workflow_state import (
    SupportMasterState,
    issue_human_authorization,
)


class ConcurrentUpdateError(RuntimeError):
    """Raised when a stale state version attempts to overwrite a run."""


class TenantAccessError(PermissionError):
    """Raised when a caller attempts to cross tenant boundaries."""


class _ClosingConnection(sqlite3.Connection):
    """Connection context manager that also closes on Windows."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        try:
            super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class SQLiteRunStore:
    """Small durable control-plane store using only Python's sqlite3 module."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_events_run_id_idx
                    ON run_events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS support_cases (
                    case_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    external_id TEXT,
                    status TEXT NOT NULL,
                    case_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, source_system, external_id)
                );
                CREATE INDEX IF NOT EXISTS support_cases_tenant_idx
                    ON support_cases(tenant_id, created_at);
                CREATE TABLE IF NOT EXISTS organizations (
                    organization_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS investigation_summaries (
                    case_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS planning_assessments (
                    case_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    assessment_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resolution_bundles (
                    case_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    correlation_id TEXT,
                    event_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telemetry_events_run_id_idx
                    ON telemetry_events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS review_tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    resume_token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_tasks_run_id_idx
                    ON review_tasks(run_id, status);
                CREATE TABLE IF NOT EXISTS run_controls (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_queue (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_queue_ready_idx
                    ON task_queue(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS task_queue_run_id_idx
                    ON task_queue(run_id, status);
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS task_checkpoints_task_idx
                    ON task_checkpoints(task_id, sequence);
                CREATE TABLE IF NOT EXISTS case_archives (
                    archive_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    case_count INTEGER NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    archive_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS case_archives_tenant_idx
                    ON case_archives(tenant_id, created_at);
                CREATE TABLE IF NOT EXISTS archived_cases (
                    case_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    archive_id TEXT NOT NULL,
                    archived_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS archived_cases_tenant_idx
                    ON archived_cases(tenant_id, archive_id);
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_run(self, state: SupportMasterState) -> RunSnapshot:
        payload = state.model_dump(mode="json")
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, version, state_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (state.run_id, 0, json.dumps(payload), now, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO run_controls(run_id, status, reason, updated_at) VALUES (?, ?, ?, ?)",
                (state.run_id, "RUNNABLE", None, now),
            )
            self._append_event(connection, state.run_id, "RUN_CREATED", {"version": 0})
        return RunSnapshot(run_id=state.run_id, version=0, state=payload)

    def save_case(self, case: SupportCase) -> SupportCase:
        payload = case.model_dump(mode="json")
        with self._connect() as connection:
            if case.external_id:
                existing = connection.execute(
                    "SELECT case_id FROM support_cases WHERE tenant_id=? AND source_system=? AND external_id=?",
                    (case.tenant_id, case.source_system, case.external_id),
                ).fetchone()
                if existing:
                    existing_id = existing[0]
                    connection.execute(
                        "UPDATE support_cases SET status=?, case_json=?, updated_at=? WHERE case_id=?",
                        (case.status, json.dumps(payload), case.updated_at.isoformat(), existing_id),
                    )
                    return case

            connection.execute(
                "INSERT INTO support_cases(case_id, tenant_id, source_system, external_id, status, case_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status, case_json=excluded.case_json, updated_at=excluded.updated_at",
                (
                    case.case_id,
                    case.tenant_id,
                    case.source_system,
                    case.external_id,
                    case.status,
                    json.dumps(payload),
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                ),
            )
        return case

    def save_organization(self, profile: OrganizationProfile) -> OrganizationProfile:
        payload = profile.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO organizations(organization_id, status, profile_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(organization_id) DO UPDATE SET status=excluded.status, profile_json=excluded.profile_json, updated_at=excluded.updated_at",
                (
                    profile.organization_id,
                    profile.status,
                    json.dumps(payload),
                    profile.created_at.isoformat(),
                    profile.updated_at.isoformat(),
                ),
            )
        return profile

    def get_organization(self, organization_id: str) -> OrganizationProfile:
        with self._connect() as connection:
            row = connection.execute("SELECT profile_json FROM organizations WHERE organization_id=?", (organization_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown organization: {organization_id}")
        return OrganizationProfile.model_validate(json.loads(row["profile_json"]))

    def list_organizations(self, *, status: str | None = None) -> list[OrganizationProfile]:
        query = "SELECT profile_json FROM organizations"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY organization_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [OrganizationProfile.model_validate(json.loads(row["profile_json"])) for row in rows]

    def save_investigation_summary(self, summary: InvestigationSummary) -> InvestigationSummary:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO investigation_summaries(case_id, tenant_id, summary_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET tenant_id=excluded.tenant_id, summary_json=excluded.summary_json, updated_at=excluded.updated_at",
                (summary.case_id, summary.tenant_id, summary.model_dump_json(), datetime.now(timezone.utc).isoformat()),
            )
        return summary

    def get_investigation_summary(self, case_id: str, *, tenant_id: str | None = None) -> InvestigationSummary:
        with self._connect() as connection:
            row = connection.execute("SELECT summary_json, tenant_id FROM investigation_summaries WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown investigation summary for case: {case_id}")
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Case {case_id} does not belong to tenant {tenant_id}.")
        return InvestigationSummary.model_validate(json.loads(row["summary_json"]))

    def save_planning_assessment(self, assessment: PlanningAssessment) -> PlanningAssessment:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO planning_assessments(case_id, tenant_id, assessment_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET tenant_id=excluded.tenant_id, assessment_json=excluded.assessment_json, updated_at=excluded.updated_at",
                (assessment.case_id, assessment.tenant_id, assessment.model_dump_json(), datetime.now(timezone.utc).isoformat()),
            )
        return assessment

    def get_planning_assessment(self, case_id: str, *, tenant_id: str | None = None) -> PlanningAssessment:
        with self._connect() as connection:
            row = connection.execute("SELECT assessment_json, tenant_id FROM planning_assessments WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown planning assessment for case: {case_id}")
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Case {case_id} does not belong to tenant {tenant_id}.")
        return PlanningAssessment.model_validate(json.loads(row["assessment_json"]))

    def save_resolution_bundle(self, bundle: ResolutionBundle) -> ResolutionBundle:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO resolution_bundles(case_id, tenant_id, bundle_json, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(case_id) DO UPDATE SET tenant_id=excluded.tenant_id, bundle_json=excluded.bundle_json, updated_at=excluded.updated_at",
                (bundle.case_id, bundle.tenant_id, bundle.model_dump_json(), datetime.now(timezone.utc).isoformat()),
            )
        return bundle

    def get_resolution_bundle(self, case_id: str, *, tenant_id: str | None = None) -> ResolutionBundle:
        with self._connect() as connection:
            row = connection.execute("SELECT bundle_json, tenant_id FROM resolution_bundles WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown resolution bundle for case: {case_id}")
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Case {case_id} does not belong to tenant {tenant_id}.")
        return ResolutionBundle.model_validate(json.loads(row["bundle_json"]))

    def get_case(self, case_id: str, *, tenant_id: str | None = None) -> SupportCase:
        with self._connect() as connection:
            row = connection.execute("SELECT case_json, tenant_id FROM support_cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown support case: {case_id}")
        if tenant_id is not None and row["tenant_id"] != tenant_id:
            raise TenantAccessError(f"Case {case_id} does not belong to tenant {tenant_id}.")
        return SupportCase.model_validate(json.loads(row["case_json"]))

    def find_case_by_external_id(self, tenant_id: str, source_system: str, external_id: str) -> SupportCase | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT case_json FROM support_cases WHERE tenant_id=? AND source_system=? AND external_id=?",
                (tenant_id, source_system, external_id),
            ).fetchone()
        return SupportCase.model_validate(json.loads(row["case_json"])) if row else None

    def list_cases(self, tenant_id: str, *, status: str | None = None) -> list[SupportCase]:
        query = "SELECT case_json FROM support_cases WHERE tenant_id=?"
        params: list[Any] = [tenant_id]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY created_at"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [SupportCase.model_validate(json.loads(row["case_json"])) for row in rows]

    def list_runs_for_case(self, case_id: str, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT run_id, state_json, updated_at FROM runs ORDER BY updated_at DESC").fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            state = json.loads(row["state_json"])
            if state.get("case_id") != case_id:
                continue
            if tenant_id is not None and state.get("tenant_id", "default") != tenant_id:
                raise TenantAccessError(f"Case {case_id} does not belong to tenant {tenant_id}.")
            results.append({"run_id": row["run_id"], "status": state.get("terminal_status"), "updated_at": row["updated_at"]})
        return results

    def load_snapshot(self, run_id: str) -> RunSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, version, state_json, updated_at FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown SupportMaster run: {run_id}")
        return RunSnapshot(
            run_id=row["run_id"],
            version=row["version"],
            state=json.loads(row["state_json"]),
            updated_at=row["updated_at"],
        )

    def load_state(self, run_id: str) -> SupportMasterState:
        return SupportMasterState.model_validate(self.load_snapshot(run_id).state)

    def load_snapshot_for_tenant(self, run_id: str, tenant_id: str) -> RunSnapshot:
        snapshot = self.load_snapshot(run_id)
        if snapshot.state.get("tenant_id", "default") != tenant_id:
            raise TenantAccessError(f"Run {run_id} does not belong to tenant {tenant_id}.")
        return snapshot

    def load_state_for_tenant(self, run_id: str, tenant_id: str) -> SupportMasterState:
        return SupportMasterState.model_validate(self.load_snapshot_for_tenant(run_id, tenant_id).state)

    def save_state(
        self,
        state: SupportMasterState,
        *,
        expected_version: int | None = None,
        event_type: str = "STATE_SAVED",
    ) -> RunSnapshot:
        payload = state.model_dump(mode="json")
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT version FROM runs WHERE run_id=?",
                (state.run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown SupportMaster run: {state.run_id}")
            current_version = int(row["version"])
            if expected_version is not None and current_version != expected_version:
                raise ConcurrentUpdateError(
                    f"Run {state.run_id} is at version {current_version}, expected {expected_version}."
                )
            next_version = current_version + 1
            connection.execute(
                "UPDATE runs SET version=?, state_json=?, updated_at=? WHERE run_id=?",
                (next_version, json.dumps(payload), now, state.run_id),
            )
            self._append_event(
                connection,
                state.run_id,
                event_type,
                {"version": next_version, "terminal_outcome": state.terminal_outcome},
            )
        return RunSnapshot(
            run_id=state.run_id,
            version=next_version,
            state=payload,
            updated_at=now,
        )

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        with self._connect() as connection:
            event_id = self._append_event(connection, run_id, event_type, payload or {})
        return self._event(run_id, event_id)

    def list_events(self, run_id: str) -> list[RunEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, run_id, event_type, payload_json, recorded_at FROM run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            RunEvent(
                sequence=row["sequence"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    def append_telemetry_event(self, event: TelemetryEvent) -> TelemetryEvent:
        """Persist one structured event idempotently."""
        if not event.run_id:
            raise ValueError("Durable telemetry events require run_id.")
        payload = event.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO telemetry_events(run_id, event_id, correlation_id, event_json, recorded_at) VALUES (?, ?, ?, ?, ?)",
                (event.run_id, event.event_id, event.correlation_id, json.dumps(payload), event.timestamp.isoformat()),
            )
        return event

    def list_telemetry(self, run_id: str) -> list[TelemetryEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM telemetry_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [TelemetryEvent.model_validate(json.loads(row["event_json"])) for row in rows]

    def enqueue_task(
        self,
        run_id: str,
        *,
        task_name: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> DurableTask:
        """Insert a task once; repeated idempotency keys return the original task."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        now = self._now()
        task = DurableTask(
            run_id=run_id,
            task_name=task_name,
            idempotency_key=idempotency_key,
            payload=payload or {},
            max_attempts=max_attempts,
            available_at=available_at or datetime.now(timezone.utc),
        )
        with self._connect() as connection:
            run = connection.execute(
                "SELECT run_id FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Unknown SupportMaster run: {run_id}")
            existing = connection.execute(
                "SELECT * FROM task_queue WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["run_id"] != run_id:
                    raise ValueError("Idempotency key belongs to a different run.")
                return self._task_from_row(existing)
            connection.execute(
                "INSERT INTO task_queue(task_id, run_id, task_name, idempotency_key, payload_json, status, attempt_count, max_attempts, available_at, lease_owner, lease_expires_at, last_error, result_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    task.run_id,
                    task.task_name,
                    task.idempotency_key,
                    json.dumps(task.payload),
                    task.status,
                    task.attempt_count,
                    task.max_attempts,
                    task.available_at.isoformat(),
                    None,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "TASK_ENQUEUED",
                {"task_id": task.task_id, "task_name": task.task_name},
            )
        return task

    def get_task(self, task_id: str) -> DurableTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown durable task: {task_id}")
        return self._task_from_row(row)

    def active_queue_depth(self) -> int:
        """Return queued and running task count for admission control."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS depth FROM task_queue WHERE status IN ('PENDING', 'RETRY_WAIT', 'RUNNING')"
            ).fetchone()
        return int(row["depth"])

    def claim_next_task(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> DurableTask | None:
        """Atomically claim the oldest runnable task and renew its lease."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least one.")
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE task_queue SET status='RETRY_WAIT', lease_owner=NULL, lease_expires_at=NULL, available_at=?, last_error=?, updated_at=? WHERE status='RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at<=? AND attempt_count<max_attempts",
                (now_text, "Worker lease expired; task requeued.", now_text, now_text),
            )
            connection.execute(
                "UPDATE task_queue SET status='FAILED', lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE status='RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at<=? AND attempt_count>=max_attempts",
                ("Worker lease expired after the final attempt.", now_text, now_text),
            )
            row = connection.execute(
                "SELECT task_queue.* FROM task_queue JOIN run_controls ON run_controls.run_id=task_queue.run_id WHERE task_queue.status IN ('PENDING','RETRY_WAIT') AND task_queue.available_at<=? AND run_controls.status IN ('RUNNABLE','RUNNING') ORDER BY task_queue.created_at, task_queue.task_id LIMIT 1",
                (now_text,),
            ).fetchone()
            if row is None:
                return None
            next_attempt = int(row["attempt_count"]) + 1
            connection.execute(
                "UPDATE task_queue SET status='RUNNING', attempt_count=?, lease_owner=?, lease_expires_at=?, updated_at=? WHERE task_id=? AND status IN ('PENDING','RETRY_WAIT')",
                (next_attempt, worker_id, lease_text, now_text, row["task_id"]),
            )
            connection.execute(
                "UPDATE run_controls SET status='RUNNING', updated_at=? WHERE run_id=? AND status='RUNNABLE'",
                (now_text, row["run_id"]),
            )
            self._append_event(
                connection,
                row["run_id"],
                "TASK_CLAIMED",
                {"task_id": row["task_id"], "worker_id": worker_id, "attempt": next_attempt},
            )
            claimed = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (row["task_id"],)
            ).fetchone()
        return self._task_from_row(claimed)

    def heartbeat_task(
        self,
        task_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> bool:
        now = datetime.now(timezone.utc)
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE task_queue SET lease_expires_at=?, updated_at=? WHERE task_id=? AND status='RUNNING' AND lease_owner=?",
                (lease_text, now.isoformat(), task_id, worker_id),
            )
            return cursor.rowcount == 1

    def checkpoint_task(
        self,
        task_id: str,
        worker_id: str,
        payload: dict[str, Any],
    ) -> TaskCheckpoint:
        """Persist a task checkpoint and append an audit event atomically."""
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, lease_owner, status FROM task_queue WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown durable task: {task_id}")
            if row["status"] != "RUNNING" or row["lease_owner"] != worker_id:
                raise ConcurrentUpdateError("Task lease is not owned by this worker.")
            sequence_row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM task_checkpoints WHERE task_id=?",
                (task_id,),
            ).fetchone()
            checkpoint = TaskCheckpoint(
                task_id=task_id,
                run_id=row["run_id"],
                sequence=int(sequence_row["next_sequence"]),
                payload=payload,
            )
            connection.execute(
                "INSERT INTO task_checkpoints(checkpoint_id, task_id, run_id, sequence, payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    checkpoint.checkpoint_id,
                    checkpoint.task_id,
                    checkpoint.run_id,
                    checkpoint.sequence,
                    json.dumps(payload),
                    now,
                ),
            )
            self._append_event(
                connection,
                row["run_id"],
                "TASK_CHECKPOINTED",
                checkpoint.model_dump(mode="json"),
            )
        return checkpoint

    def list_task_checkpoints(self, task_id: str) -> list[TaskCheckpoint]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_checkpoints WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [
            TaskCheckpoint(
                checkpoint_id=row["checkpoint_id"],
                task_id=row["task_id"],
                run_id=row["run_id"],
                sequence=row["sequence"],
                payload=json.loads(row["payload_json"]),
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    def complete_task(
        self,
        task_id: str,
        worker_id: str,
        result: dict[str, Any] | None = None,
    ) -> DurableTask:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown durable task: {task_id}")
            if row["status"] == "SUCCEEDED":
                return self._task_from_row(row)
            if row["status"] != "RUNNING" or row["lease_owner"] != worker_id:
                raise ConcurrentUpdateError("Task lease is not owned by this worker.")
            payload = result or {}
            connection.execute(
                "UPDATE task_queue SET status='SUCCEEDED', result_json=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=? AND status='RUNNING' AND lease_owner=?",
                (json.dumps(payload), now, task_id, worker_id),
            )
            self._append_event(
                connection,
                row["run_id"],
                "TASK_COMPLETED",
                {"task_id": task_id, "result": payload},
            )
            completed = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task_from_row(completed)

    def fail_task(
        self,
        task_id: str,
        worker_id: str,
        error: str,
        *,
        retryable: bool = True,
        retry_delay_seconds: float = 0.0,
    ) -> DurableTask:
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown durable task: {task_id}")
            if row["status"] != "RUNNING" or row["lease_owner"] != worker_id:
                raise ConcurrentUpdateError("Task lease is not owned by this worker.")
            should_retry = retryable and int(row["attempt_count"]) < int(row["max_attempts"])
            status: TaskStatus = "RETRY_WAIT" if should_retry else "FAILED"
            available = now + timedelta(seconds=max(0.0, retry_delay_seconds))
            connection.execute(
                "UPDATE task_queue SET status=?, available_at=?, lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE task_id=? AND status='RUNNING' AND lease_owner=?",
                (status, available.isoformat(), error, now.isoformat(), task_id, worker_id),
            )
            self._append_event(
                connection,
                row["run_id"],
                "TASK_RETRY_SCHEDULED" if should_retry else "TASK_FAILED",
                {"task_id": task_id, "error": error, "retryable": should_retry},
            )
            updated = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task_from_row(updated)

    def defer_task(self, task_id: str, worker_id: str, *, reason: str) -> DurableTask:
        """Release a lease without consuming an attempt, for pause/resume."""
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown durable task: {task_id}")
            if row["status"] != "RUNNING" or row["lease_owner"] != worker_id:
                raise ConcurrentUpdateError("Task lease is not owned by this worker.")
            connection.execute(
                "UPDATE task_queue SET status='RETRY_WAIT', available_at=?, lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE task_id=?",
                (now, reason, now, task_id),
            )
            self._append_event(
                connection,
                row["run_id"],
                "TASK_DEFERRED",
                {"task_id": task_id, "reason": reason},
            )
            updated = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
        return self._task_from_row(updated)

    def cancel_task(self, task_id: str, worker_id: str, *, reason: str) -> DurableTask:
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown durable task: {task_id}")
            if row["status"] == "CANCELLED":
                return self._task_from_row(row)
            if row["status"] != "RUNNING" or row["lease_owner"] != worker_id:
                raise ConcurrentUpdateError("Task lease is not owned by this worker.")
            connection.execute(
                "UPDATE task_queue SET status='CANCELLED', lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE task_id=?",
                (reason, now, task_id),
            )
            self._append_event(connection, row["run_id"], "TASK_CANCELLED", {"task_id": task_id, "reason": reason})
            updated = connection.execute("SELECT * FROM task_queue WHERE task_id=?", (task_id,)).fetchone()
        return self._task_from_row(updated)

    def get_run_control(self, run_id: str) -> RunControl:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id, status, reason, updated_at FROM run_controls WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown SupportMaster run: {run_id}")
        return RunControl(
            run_id=row["run_id"],
            status=row["status"],
            reason=row["reason"],
            updated_at=row["updated_at"],
        )

    def set_run_control(self, run_id: str, status: RunControlStatus, *, reason: str | None = None) -> RunControl:
        now = self._now()
        with self._connect() as connection:
            exists = connection.execute("SELECT run_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if exists is None:
                raise KeyError(f"Unknown SupportMaster run: {run_id}")
            connection.execute(
                "INSERT INTO run_controls(run_id, status, reason, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, updated_at=excluded.updated_at",
                (run_id, status, reason, now),
            )
            if status in {"CANCEL_REQUESTED", "CANCELLED"}:
                connection.execute(
                    "UPDATE task_queue SET status='CANCELLED', last_error=?, updated_at=? WHERE run_id=? AND status IN ('PENDING','RETRY_WAIT')",
                    (reason or "Run cancellation requested.", now, run_id),
                )
            self._append_event(connection, run_id, f"RUN_{status}", {"reason": reason})
        return self.get_run_control(run_id)

    def pause_run(self, run_id: str, *, reason: str = "Operator pause requested.") -> RunControl:
        return self.set_run_control(run_id, "PAUSED", reason=reason)

    def resume_durable_run(self, run_id: str, *, reason: str = "Operator resume requested.") -> RunControl:
        return self.set_run_control(run_id, "RUNNABLE", reason=reason)

    def request_cancel(self, run_id: str, *, reason: str = "Operator cancellation requested.") -> RunControl:
        return self.set_run_control(run_id, "CANCEL_REQUESTED", reason=reason)

    def mark_run_completed(self, run_id: str) -> RunControl:
        return self.set_run_control(run_id, "COMPLETED")

    def mark_run_failed(self, run_id: str, *, reason: str) -> RunControl:
        return self.set_run_control(run_id, "FAILED", reason=reason)

    def replay_run(self, run_id: str, *, until_sequence: int | None = None) -> ReplayPlan:
        """Create a read-only replay plan from the immutable event stream."""
        snapshot = self.load_snapshot(run_id)
        events = self.list_events(run_id)
        sequences = [
            event.sequence
            for event in events
            if until_sequence is None or event.sequence <= until_sequence
        ]
        plan = ReplayPlan(
            run_id=run_id,
            source_version=snapshot.version,
            event_sequences=sequences,
        )
        self.append_event(run_id, "RUN_REPLAY_PLANNED", plan.model_dump(mode="json"))
        return plan

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> DurableTask:
        return DurableTask(
            task_id=row["task_id"],
            run_id=row["run_id"],
            task_name=row["task_name"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"],
            available_at=row["available_at"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            last_error=row["last_error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_review_task(
        self,
        run_id: str,
        *,
        reason: str,
        blocking_reasons: Iterable[str] = (),
        required_actions: Iterable[str] = (),
        evidence_keys: Iterable[str] = (),
        allowed_scopes: Iterable[AuthorizationScope] = (),
        resume_condition: str,
        ttl_seconds: int = 3600,
    ) -> tuple[HumanReviewTask, str]:
        token = secrets.token_urlsafe(32)
        task = HumanReviewTask(
            run_id=run_id,
            reason=reason,
            blocking_reasons=list(blocking_reasons),
            required_actions=list(required_actions),
            evidence_keys=list(evidence_keys),
            allowed_scopes=list(allowed_scopes),
            resume_condition=resume_condition,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO review_tasks(task_id, run_id, status, task_json, resume_token_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    run_id,
                    task.status,
                    task.model_dump_json(),
                    self._hash_token(token),
                    now,
                    now,
                ),
            )
            self._append_event(connection, run_id, "HUMAN_REVIEW_OPENED", task.model_dump(mode="json"))
        state = self.load_state(run_id)
        state.pending_human_review = task
        state.terminal_status = "HUMAN_REVIEW_REQUIRED"
        state.terminal_outcome = "PAUSED_FOR_HUMAN_REVIEW"
        self.save_state(state, event_type="RUN_PAUSED_FOR_HUMAN_REVIEW")
        return task, token

    def get_review_task(self, task_id: str) -> HumanReviewTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_json FROM review_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown human-review task: {task_id}")
        task = HumanReviewTask.model_validate(json.loads(row["task_json"]))
        if task.expires_at and task.expires_at <= datetime.now(timezone.utc) and task.status == "OPEN":
            self._set_task_status(task, "EXPIRED")
        return task

    def list_review_tasks(self, tenant_id: str, *, status: str | None = None) -> list[HumanReviewTask]:
        """List review tasks whose durable run belongs to one tenant."""
        query = "SELECT review_tasks.task_json, runs.state_json FROM review_tasks JOIN runs ON runs.run_id = review_tasks.run_id"
        params: list[Any] = []
        if status:
            query += " WHERE review_tasks.status=?"
            params.append(status)
        query += " ORDER BY review_tasks.created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        tasks: list[HumanReviewTask] = []
        for row in rows:
            state = json.loads(row["state_json"])
            if state.get("tenant_id", "default") != tenant_id:
                continue
            tasks.append(HumanReviewTask.model_validate(json.loads(row["task_json"])))
        return tasks

    def decide_review_task(
        self,
        task_id: str,
        *,
        reviewer: str,
        decision: Literal["APPROVE", "REJECT"],
        resume_token: str,
        approved_scopes: Iterable[AuthorizationScope] = (),
        comment: str = "",
    ) -> HumanReviewTask:
        task = self.get_review_task(task_id)
        self._verify_token(task_id, resume_token)
        if task.status != "OPEN":
            raise ValueError(f"Review task is not open: {task.status}")
        scopes = list(approved_scopes)
        if not set(scopes).issubset(set(task.allowed_scopes)):
            raise ValueError("Approval scope exceeds the review task's allowed scopes.")
        if decision == "REJECT" and scopes:
            raise ValueError("Rejected review tasks cannot issue approval scopes.")
        if not reviewer.strip():
            raise ValueError("A reviewer identity is required.")
        task.decision = HumanReviewDecision(
            task_id=task.task_id,
            reviewer=reviewer,
            decision=decision,
            approved_scopes=scopes,
            comment=comment,
        )
        task.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
        self._save_task(task)
        self.append_event(task.run_id, "HUMAN_REVIEW_DECIDED", task.model_dump(mode="json"))
        return task

    def resume_run(self, run_id: str, task_id: str, resume_token: str) -> SupportMasterState:
        task = self.get_review_task(task_id)
        self._verify_token(task_id, resume_token)
        if task.run_id != run_id:
            raise ValueError("Review task does not belong to this run.")
        if task.status != "APPROVED" or task.decision is None:
            raise ValueError("Only an approved review task can resume a run.")
        state = self.load_state(run_id)
        for scope in task.decision.approved_scopes:
            issue_human_authorization(
                state,
                scope=scope,
                approval_id=task.decision.decision_id,
                expires_at=task.expires_at,
            )
        state.human_review_history.append(task.decision)
        state.pending_human_review = None
        state.terminal_status = None
        state.terminal_outcome = None
        task.status = "RESUMED"
        self._save_task(task)
        self.save_state(state, event_type="RUN_RESUMED")
        self.append_event(run_id, "HUMAN_REVIEW_RESUMED", {"task_id": task_id})
        return state

    def _verify_token(self, task_id: str, token: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT resume_token_hash FROM review_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if row is None or not secrets.compare_digest(row["resume_token_hash"], self._hash_token(token)):
            raise ValueError("Invalid resume token.")

    def _save_task(self, task: HumanReviewTask) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE review_tasks SET status=?, task_json=?, updated_at=? WHERE task_id=?",
                (task.status, task.model_dump_json(), self._now(), task.task_id),
            )

    def _set_task_status(self, task: HumanReviewTask, status: str) -> None:
        task.status = status  # type: ignore[assignment]
        self._save_task(task)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO run_events(run_id, event_type, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
            (run_id, event_type, json.dumps(payload), self._now()),
        )
        return int(cursor.lastrowid)

    def _event(self, run_id: str, sequence: int) -> RunEvent:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sequence, run_id, event_type, payload_json, recorded_at FROM run_events WHERE sequence=? AND run_id=?",
                (sequence, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run event: {sequence}")
        return RunEvent(
            sequence=row["sequence"],
            run_id=row["run_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            recorded_at=row["recorded_at"],
        )

    def save_archive(self, record: Any) -> None:
        """Persist a rolling zip archive record."""
        payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else record
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO case_archives(archive_id, tenant_id, filename, filepath, case_count, start_date, end_date, size_bytes, archive_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(archive_id) DO UPDATE SET size_bytes=excluded.size_bytes, archive_json=excluded.archive_json",
                (
                    payload["archive_id"],
                    payload["tenant_id"],
                    payload["filename"],
                    payload["filepath"],
                    payload["case_count"],
                    payload["start_date"],
                    payload["end_date"],
                    payload["size_bytes"],
                    json.dumps(payload),
                    payload.get("created_at") or now,
                ),
            )

    def list_archives(self, tenant_id: str) -> list[dict[str, Any]]:
        """List all rolling archives for a tenant sorted by newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT archive_json FROM case_archives WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [json.loads(r["archive_json"]) for r in rows]

    def get_archive(self, tenant_id: str, archive_id: str) -> dict[str, Any] | None:
        """Retrieve archive record by ID ensuring strict tenant boundary isolation."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT archive_json FROM case_archives WHERE tenant_id=? AND archive_id=?",
                (tenant_id, archive_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["archive_json"])

    def mark_cases_archived(self, tenant_id: str, case_ids: list[str], archive_id: str) -> None:
        """Index cases as archived in SQLite."""
        now = self._now()
        with self._connect() as connection:
            for cid in case_ids:
                connection.execute(
                    "INSERT OR REPLACE INTO archived_cases(case_id, tenant_id, archive_id, archived_at) VALUES (?, ?, ?, ?)",
                    (cid, tenant_id, archive_id, now),
                )

    def get_archived_case_ids(self, tenant_id: str) -> list[str]:
        """List all case IDs already associated with an archive for a tenant."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT case_id FROM archived_cases WHERE tenant_id=?",
                (tenant_id,),
            ).fetchall()
        return [r["case_id"] for r in rows]

