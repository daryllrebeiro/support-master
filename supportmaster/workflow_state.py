"""Typed state shared by the SupportMaster orchestration graph.

The existing agents write these values through their ``output_key`` settings.
Keeping the keys in one contract lets orchestration nodes make decisions from
structured state instead of parsing agent prose.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .models.audit import WorkflowAudit
from .models.code_change import CodeChangeResult
from .models.control import (
    AuthorizationGrant,
    ExternalOperationReceipt,
    GateDecisionRecord,
    PolicyDecision,
    TerminalOutcome,
)
from .models.customer_response import CustomerResponse
from .models.discovery import DiscoveryResult
from .models.duplicate_work import DuplicateWorkAnalysis
from .models.evidence import EvidenceAnalysis
from .models.evidence_record import EvidenceBundle, EvidenceRecord
from .models.escalation import EscalationAnalysis
from .models.github_publish import GitHubPublishResult
from .models.implementation import ImplementationResult
from .models.investigation import InvestigationPlan
from .models.human_review import HumanReviewDecision, HumanReviewTask
from .models.publish import PublishPlan
from .models.remediation import RemediationPlan
from .models.repository import RepositoryAnalysis
from .models.resolution import ResolutionAnalysis
from .models.review import ReviewAnalysis
from .models.root_cause import RootCauseAnalysis
from .models.test_result import TestResult
from .models.ticket import TicketAnalysis
from .models.support_case import SupportCase
from .models.organization import OrganizationProfile
from .models.investigation_artifacts import InvestigationSummary
from .models.planning import PlanningAssessment
from .models.resolution_bundle import ResolutionBundle
from .execution.contracts import EngineeringExecutionResult
from .models.validation import ValidationAnalysis
from .models.workflow_control import WorkflowControl
from .models.workflow_summary import WorkflowSummary
from .orchestration.contracts import ForkJoinResult


GateName = Literal[
    "DUPLICATE_WORK",
    "REVIEW",
    "VALIDATION",
    "AUDIT",
    "IMPLEMENTATION_AUTHORIZATION",
    "PUBLISH_AUTHORIZATION",
    "EXTERNAL_OPERATION",
    "HUMAN_REVIEW",
    "ORCHESTRATION",
]
GateRoute = Literal[
    "CONTINUE",
    "STOP",
    "REQUEST_INFORMATION",
    # Retained for compatibility with older persisted events. New gates
    # never emit this route; blocked automation terminates with SAFETY_STOP.
    "HUMAN_REVIEW_REQUIRED",
    "SAFETY_STOP",
    "READY_FOR_IMPLEMENTATION",
    "READY_FOR_PUBLISH",
    "COMPLETED",
]
TerminalStatus = Literal["COMPLETED", "BLOCKED", "SAFETY_STOP", "HUMAN_REVIEW_REQUIRED"]


class AutonomousStop(BaseModel):
    """Machine-readable terminal result for a fail-closed autonomous run."""

    status: Literal["SAFETY_STOP"] = "SAFETY_STOP"
    gate: GateName
    reason: str
    blocking_reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    evidence_keys: list[str] = Field(default_factory=list)
    autonomous_continuation_allowed: bool = False


OUTPUT_KEY_TO_STATE_FIELD: dict[str, str] = {
    "ticket_analysis": "ticket_analysis",
    "investigation_plan": "investigation_plan",
    "duplicate_work_analysis": "duplicate_work_analysis",
    "evidence_analysis": "evidence_analysis",
    "repository_discovery": "repository_discovery",
    "repository_analysis": "repository_analysis",
    "root_cause_analysis": "root_cause_analysis",
    "remediation_plan": "remediation_plan",
    "review_analysis": "review_analysis",
    "code_change_result": "code_change_result",
    "implementation_result": "implementation_result",
    "validation_analysis": "validation_analysis",
    "test_result": "test_result",
    "publish_plan": "publish_plan",
    "github_publish_result": "github_publish_result",
    "resolution_analysis": "resolution_analysis",
    "customer_response": "customer_response",
    "workflow_audit": "workflow_audit",
    "escalation_analysis": "escalation_analysis",
    "workflow_summary": "workflow_summary",
    "workflow_control": "workflow_control",
    "autonomous_stop": "autonomous_stop",
}


class SupportMasterState(BaseModel):
    """State contract for the agent outputs and orchestration decisions."""

    model_config = ConfigDict(extra="allow")

    ticket_analysis: Optional[TicketAnalysis] = None
    investigation_plan: Optional[InvestigationPlan] = None
    duplicate_work_analysis: Optional[DuplicateWorkAnalysis] = None
    evidence_analysis: Optional[EvidenceAnalysis] = None
    evidence_bundle: Optional[EvidenceBundle] = None
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)
    repository_discovery: Optional[DiscoveryResult] = None
    repository_analysis: Optional[RepositoryAnalysis] = None
    root_cause_analysis: Optional[RootCauseAnalysis] = None
    remediation_plan: Optional[RemediationPlan] = None
    review_analysis: Optional[ReviewAnalysis] = None
    code_change_result: Optional[CodeChangeResult] = None
    implementation_result: Optional[ImplementationResult] = None
    validation_analysis: Optional[ValidationAnalysis] = None
    test_result: Optional[TestResult] = None
    publish_plan: Optional[PublishPlan] = None
    github_publish_result: Optional[GitHubPublishResult] = None
    resolution_analysis: Optional[ResolutionAnalysis] = None
    customer_response: Optional[CustomerResponse] = None
    workflow_audit: Optional[WorkflowAudit] = None
    escalation_analysis: Optional[EscalationAnalysis] = None
    workflow_summary: Optional[WorkflowSummary] = None
    workflow_control: Optional[WorkflowControl] = None
    autonomous_stop: Optional[AutonomousStop] = None

    # Control-plane lifecycle and traceability. These fields are deliberately
    # separate from the LLM-produced workflow outputs above.
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    case_id: Optional[str] = None
    support_case: Optional[SupportCase] = None
    tenant_id: str = "default"
    initiated_by: str | None = None
    organization_id: str = "default"
    organization_profile: Optional[OrganizationProfile] = None
    investigation_summary: Optional[InvestigationSummary] = None
    planning_assessment: Optional[PlanningAssessment] = None
    engineering_execution: Optional[EngineeringExecutionResult] = None
    resolution_bundle: Optional[ResolutionBundle] = None
    ticket_id: Optional[str] = None
    current_stage: Optional[str] = None
    policy_version: str = "v1"
    terminal_outcome: Optional[TerminalOutcome] = None
    gate_history: list[GateDecisionRecord] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    authorizations: list[AuthorizationGrant] = Field(default_factory=list)
    operation_receipts: list[ExternalOperationReceipt] = Field(default_factory=list)
    pending_human_review: Optional[HumanReviewTask] = None
    human_review_history: list[HumanReviewDecision] = Field(default_factory=list)
    fork_join_results: list[ForkJoinResult] = Field(default_factory=list)
    integration_results: dict[str, Any] = Field(default_factory=dict)

    last_gate_decision: Optional["GateDecision"] = Field(default=None)
    terminal_status: Optional[TerminalStatus] = None
    autonomous_best_effort: bool = False
    uncertainty_flags: list[str] = Field(default_factory=list)


class GateDecision(BaseModel):
    """Deterministic routing result emitted by an orchestration gate."""

    gate: GateName
    route: GateRoute
    reason: str
    blocking_reasons: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    evidence_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def append_gate_history(
    state: MutableMapping[str, Any],
    decision: GateDecision,
    *,
    policy_version: str = "v1",
) -> GateDecisionRecord:
    """Append a deterministic gate decision to the run audit trail.

    Orchestration nodes should call this alongside updating
    ``last_gate_decision``. The list is append-only by workflow convention;
    later phases will persist it as an immutable event stream.
    """
    record = GateDecisionRecord(
        gate=decision.gate,
        route=decision.route,
        reason=decision.reason,
        blocking_reasons=decision.blocking_reasons,
        required_actions=decision.required_actions,
        evidence_keys=decision.evidence_keys,
        warnings=decision.warnings,
        policy_version=policy_version,
    )
    history = state.get("gate_history") or []
    history.append(record.model_dump(mode="json"))
    state["gate_history"] = history
    return record


def append_policy_decision(
    state: MutableMapping[str, Any],
    decision: PolicyDecision,
) -> PolicyDecision:
    """Record a policy result before any future executor can consume it."""
    decisions = state.get("policy_decisions") or []
    decisions.append(decision.model_dump(mode="json"))
    state["policy_decisions"] = decisions
    return decision


def issue_authorization(
    state: MutableMapping[str, Any],
    *,
    scope: Literal[
        "INVESTIGATION",
        "IMPLEMENTATION",
        "PUBLISH",
        "PRODUCTION",
        "CUSTOMER_RESPONSE",
        "CLOSE_TICKET",
    ],
    decision: PolicyDecision,
    gate_decision_id: str | None = None,
) -> AuthorizationGrant:
    """Issue a scoped grant only after a deterministic ALLOW decision."""
    if decision.disposition != "ALLOW":
        raise ValueError("Only ALLOW policy decisions may issue authorizations.")
    grant = AuthorizationGrant(
        run_id=state.get("run_id"),
        scope=scope,
        policy_version=decision.policy_version,
        gate_decision_id=gate_decision_id,
        evidence_keys=decision.evidence_keys,
    )
    authorizations = state.get("authorizations") or []
    authorizations.append(grant.model_dump(mode="json"))
    state["authorizations"] = authorizations
    return grant


def append_operation_receipts(
    state: MutableMapping[str, Any],
    receipts: list[ExternalOperationReceipt],
) -> None:
    """Persist verified external-operation evidence in workflow state."""
    existing = state.get("operation_receipts") or []
    existing.extend(receipt.model_dump(mode="json") for receipt in receipts)
    state["operation_receipts"] = existing


def issue_human_authorization(
    state: MutableMapping[str, Any],
    *,
    scope: Literal[
        "INVESTIGATION",
        "IMPLEMENTATION",
        "PUBLISH",
        "PRODUCTION",
        "CUSTOMER_RESPONSE",
        "CLOSE_TICKET",
    ],
    approval_id: str,
    expires_at: Any = None,
) -> AuthorizationGrant:
    """Record a human-scoped grant; this does not bypass deterministic gates."""
    if isinstance(state, MutableMapping):
        run_id = state.get("run_id")
        policy_version = state.get("policy_version", "v1")
        authorizations = state.get("authorizations") or []
    else:
        run_id = getattr(state, "run_id", None)
        policy_version = getattr(state, "policy_version", "v1")
        authorizations = getattr(state, "authorizations", []) or []
    grant = AuthorizationGrant(
        run_id=run_id,
        scope=scope,
        human_approval_id=approval_id,
        expires_at=expires_at,
        policy_version=policy_version,
    )
    if isinstance(state, MutableMapping):
        authorizations.append(grant.model_dump(mode="json"))
        state["authorizations"] = authorizations
    else:
        authorizations.append(grant)
        state.authorizations = authorizations
    return grant


# Resolve forward references used by SupportMasterState.
SupportMasterState.model_rebuild()
