"""Canonical data models and contracts for the modular pipeline architecture."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field

from .control import ExternalOperationReceipt
from .discovery import CodeMatch, FileBlob, RepositoryDescriptor
from .support_case import SupportCase


class PullRequestResult(BaseModel):
    """Canonical representation of an opened pull/merge request."""

    repository: str
    pull_request_id: str
    url: str
    head_branch: str
    base_branch: str
    status: Literal["OPEN", "MERGED", "CLOSED", "DRAFT"] = "OPEN"
    metadata: dict[str, str] = Field(default_factory=dict)


class TestRunResult(BaseModel):
    """Canonical representation of a test execution run."""

    suite_name: str
    status: Literal["PASSED", "FAILED", "BLOCKED", "SKIPPED"]
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    duration_ms: float = 0.0
    failures: list[str] = Field(default_factory=list)
    output: str = ""
    receipt: ExternalOperationReceipt | None = None


class NotificationRequest(BaseModel):
    """Canonical notification payload."""

    channel: str
    recipient: str | None = None
    subject: str = ""
    message: str
    severity: Literal["INFO", "WARNING", "CRITICAL"] = "INFO"
    metadata: dict[str, str] = Field(default_factory=dict)


class CIStatusResult(BaseModel):
    """Canonical CI status report."""

    run_id: str
    pipeline: str
    status: Literal["QUEUED", "RUNNING", "PASSED", "FAILED", "CANCELLED", "UNKNOWN"]
    url: str | None = None
    commit_sha: str | None = None
    details: dict[str, str] = Field(default_factory=dict)
