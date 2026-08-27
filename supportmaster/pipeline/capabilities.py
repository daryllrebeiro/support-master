"""Capability-based interfaces for modular pipeline adapters.

Design Invariants:
1. Capability-based, not vendor-based. Stages depend on CanX protocols.
2. Narrow, single responsibility per protocol.
3. Every operation returns an ExternalOperationReceipt for auditable receipts.
4. Adapters perform zero reasoning — translation and vendor interaction only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..integrations.contracts import CIStatus, IncidentRecord, IssueRecord, MetricSample
from ..models.control import ExternalOperationReceipt
from ..models.discovery import CodeMatch, FileBlob, RepositoryDescriptor
from ..models.pipeline import NotificationRequest, PullRequestResult, TestRunResult
from ..models.support_case import SupportCase


@runtime_checkable
class CanFetchCase(Protocol):
    """Capability to fetch a normalized support case by ID or key."""

    def fetch_case(self, case_id: str) -> tuple[SupportCase | None, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanUpdateCaseStatus(Protocol):
    """Capability to update the status of a case in an external issue tracker."""

    def update_case_status(self, case_id: str, status: str) -> ExternalOperationReceipt:
        ...


@runtime_checkable
class CanPostComment(Protocol):
    """Capability to post a comment to an external case or issue."""

    def post_comment(self, case_id: str, body: str) -> ExternalOperationReceipt:
        ...


@runtime_checkable
class CanSearchIssues(Protocol):
    """Capability to search issues/tickets in an issue tracker."""

    def search_issues(self, query: str) -> tuple[list[IssueRecord], ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanListRepositories(Protocol):
    """Capability to list workspace repositories."""

    def list_repositories(self) -> tuple[list[RepositoryDescriptor], ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanSearchCode(Protocol):
    """Capability to search code within repositories."""

    def search_code(
        self, query: str, repos: list[str] | None = None
    ) -> tuple[list[CodeMatch], ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanReadFile(Protocol):
    """Capability to read a file from a repository at a given ref/commit."""

    def read_file(
        self, repo: str, path: str, ref: str | None = None
    ) -> tuple[str | FileBlob, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanOpenPullRequest(Protocol):
    """Capability to open a pull request or merge request."""

    def open_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
    ) -> tuple[PullRequestResult, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanRunTests(Protocol):
    """Capability to execute tests and report canonical test run results."""

    def run_tests(
        self,
        repo: str,
        commit_sha: str,
        test_targets: list[str] | None = None,
    ) -> tuple[TestRunResult, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanReadCIStatus(Protocol):
    """Capability to query CI pipeline run status."""

    def read_ci_status(self, run_id: str) -> tuple[CIStatus, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanTriggerCI(Protocol):
    """Capability to trigger a CI pipeline."""

    def trigger_ci(
        self,
        pipeline: str,
        *,
        commit_sha: str,
        parameters: Mapping[str, str] | None = None,
    ) -> tuple[str | None, ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanReadMonitoringSignal(Protocol):
    """Capability to inspect incidents and metrics from monitoring systems."""

    def incidents(self, service: str) -> tuple[list[IncidentRecord], ExternalOperationReceipt]:
        ...

    def metric(self, name: str, *, service: str) -> tuple[list[MetricSample], ExternalOperationReceipt]:
        ...


@runtime_checkable
class CanSendNotification(Protocol):
    """Capability to dispatch messages to notifications channels."""

    def send_notification(
        self, request: NotificationRequest | str, channel: str | None = None
    ) -> ExternalOperationReceipt:
        ...
