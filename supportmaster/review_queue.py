"""Tenant-scoped operator projection for durable human-review tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models.human_review import HumanReviewTask
from .persistence import SQLiteRunStore


class ReviewQueueSnapshot(BaseModel):
    tenant_id: str
    open_count: int = 0
    tasks: list[HumanReviewTask] = Field(default_factory=list)


class ReviewQueueMetrics(BaseModel):
    tenant_id: str
    total: int = 0
    total_cases: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    approvals: int = 0
    rejections: int = 0
    open_count: int = 0
    expiring_count: int = 0


class ReviewQueueService:
    def __init__(self, store: SQLiteRunStore) -> None:
        self.store = store

    def snapshot(self, tenant_id: str, *, status: str | None = None) -> ReviewQueueSnapshot:
        tasks = self.store.list_review_tasks(tenant_id, status=status)
        return ReviewQueueSnapshot(tenant_id=tenant_id, open_count=sum(task.status == "OPEN" for task in tasks), tasks=tasks)

    def metrics(self, tenant_id: str) -> ReviewQueueMetrics:
        from datetime import datetime, timedelta, timezone

        tasks = self.store.list_review_tasks(tenant_id)
        cases = self.store.list_cases(tenant_id)
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        now = datetime.now(timezone.utc)
        expiring = sum(
            task.status == "OPEN" and task.expires_at is not None and now <= task.expires_at <= now + timedelta(hours=24)
            for task in tasks
        )
        return ReviewQueueMetrics(
            tenant_id=tenant_id,
            total=len(tasks),
            total_cases=len(cases),
            by_status=counts,
            approvals=sum(task.status in {"APPROVED", "RESUMED"} for task in tasks),
            rejections=sum(task.status == "REJECTED" for task in tasks),
            open_count=sum(task.status == "OPEN" for task in tasks),
            expiring_count=expiring,
        )
