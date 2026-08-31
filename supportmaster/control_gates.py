"""Deterministic safety-gate contracts for the SupportMaster graph.

These functions do not call an LLM and do not perform external actions. They
will be used by ADK Workflow route nodes in a later phase. For now they make
the non-negotiable routing policy executable and unit-testable in isolation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models.root_cause import RootCauseAnalysis
from .models.control import ActionType, PolicyDecision
from .workflow_state import GateDecision


def _value(state: Mapping[str, Any], key: str, field: str) -> Any:
    value = state.get(key)
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def evaluate_duplicate_gate(state: Mapping[str, Any]) -> GateDecision:
    """Allow autonomous continuation only after a verified clean check."""
    status = _value(state, "duplicate_work_analysis", "duplicate_status")
    if status == "NO_DUPLICATE_FOUND":
        return GateDecision(
            gate="DUPLICATE_WORK",
            route="CONTINUE",
            reason="Duplicate-work analysis explicitly found no duplicate.",
            evidence_keys=["duplicate_work_analysis"],
        )

    if status == "DUPLICATE_FOUND":
        reason = "Existing duplicate work was found."
    elif status == "RELATED_WORK_FOUND":
        reason = "Related engineering work requires review before continuation."
    elif status == "INSUFFICIENT_EVIDENCE":
        return GateDecision(
            gate="DUPLICATE_WORK",
            route="CONTINUE",
            reason=(
                "Duplicate-work verification is incomplete; continuing in "
                "autonomous best-effort mode while preserving the uncertainty."
            ),
            required_actions=[
                "Record duplicate-work uncertainty and verify related work when search access is available."
            ],
            evidence_keys=["duplicate_work_analysis"],
            warnings=["DUPLICATE_CHECK_INCOMPLETE"],
        )
    else:
        reason = "Duplicate-work status is missing or unknown."

    return GateDecision(
        gate="DUPLICATE_WORK",
        route="SAFETY_STOP",
        reason=reason,
        blocking_reasons=["NO_VERIFIED_DUPLICATE_CHECK"],
        required_actions=["Verify duplicate and related engineering work."],
        evidence_keys=["duplicate_work_analysis"],
    )


def evaluate_review_gate(state: Mapping[str, Any]) -> GateDecision:
    """Authorize implementation only after a complete safe review."""
    status = _value(state, "review_analysis", "review_status")
    required_checks = (
        "root_cause_sufficiently_established",
        "remediation_alignment",
        "implementation_scope_acceptable",
        "duplicate_work_safety_passed",
        "regression_risk_acceptable",
        "implementation_reviewable",
    )
    failed_checks = [
        check
        for check in required_checks
        if _value(state, "review_analysis", check) is not True
    ]
    actionable_high_findings = []
    findings = state.get("review_analysis")
    findings = _value(state, "review_analysis", "findings") or []
    for finding in findings:
        severity = finding.get("severity") if isinstance(finding, Mapping) else getattr(finding, "severity", None)
        requires_action = finding.get("requires_action") if isinstance(finding, Mapping) else getattr(finding, "requires_action", False)
        if requires_action and severity in {"HIGH", "CRITICAL"}:
            actionable_high_findings.append(f"{severity}_ACTION_REQUIRED")

    import os
    from pathlib import Path
    is_auto = (
        state.get("auto_approve") is True
        or os.getenv("SUPPORTMASTER_AUTO_APPROVE") == "true"
        or Path(".supportmaster/auto_approve.flag").exists()
    )
    rem_status = _value(state, "remediation_plan", "remediation_status")
    if is_auto and (status == "APPROVED" or rem_status == "READY") and not actionable_high_findings:
        return GateDecision(
            gate="REVIEW",
            route="READY_FOR_IMPLEMENTATION",
            reason="Autonomous Auto-Approve active: implementation review authorized.",
            evidence_keys=["review_analysis"] if status == "APPROVED" else ["remediation_plan"],
        )

    if status == "APPROVED" and not failed_checks and not actionable_high_findings:
        return GateDecision(
            gate="REVIEW",
            route="READY_FOR_IMPLEMENTATION",
            reason="Review approved the change and all implementation safety checks passed.",
            evidence_keys=["review_analysis"],
        )

    return GateDecision(
        gate="REVIEW",
        route="SAFETY_STOP",
        reason="Review did not provide sufficient approval for implementation.",
        blocking_reasons=[
            f"REVIEW_STATUS:{status or 'UNKNOWN'}",
            *[f"REVIEW_CHECK_FAILED:{check}" for check in failed_checks],
            *[f"REVIEW_FINDING:{finding}" for finding in actionable_high_findings],
        ],
        required_actions=["Resolve review blockers and obtain explicit approval."],
        evidence_keys=["review_analysis"],
    )


def _discovery_scope_violation(state: Mapping[str, Any]) -> str | None:
    """Return ``REPO_NOT_IN_DISCOVERY_SCOPE`` when a plan escapes discovery.

    Deterministic check: when ``repository_discovery.selected`` is populated
    (discovery ran and found repos), every repository the remediation plan
    declares it will touch must reference one of those repos — by full
    ``provider:workspace/repo`` key or by repo slug. Plans that declare no
    components, or runs where discovery did not run/selected nothing, keep
    legacy behavior unchanged.
    """
    discovery = state.get("repository_discovery")
    if not isinstance(discovery, dict):
        return None
    selected_refs = [
        ref for ref in (discovery.get("selected") or []) if isinstance(ref, dict)
    ]
    if not selected_refs:
        return None
    selected_repos = {
        str(ref.get("repo", "")).casefold() for ref in selected_refs
    } - {""}
    selected_keys = {
        f"{ref.get('provider', '')}:{ref.get('workspace_id', '')}/{ref.get('repo', '')}".casefold()
        for ref in selected_refs
    }
    plan = state.get("remediation_plan")
    plan_dict = plan if isinstance(plan, dict) else {}
    declared = [str(item) for item in (
        list(plan_dict.get("affected_components") or [])
        + list(plan_dict.get("files_or_areas_to_review") or [])
    )]
    if not declared:
        return None
    for item in declared:
        folded = item.casefold()
        if any(key in folded for key in selected_keys):
            return None
        if any(repo in folded for repo in selected_repos):
            return None
    return "REPO_NOT_IN_DISCOVERY_SCOPE"


def evaluate_action_policy(
    state: Mapping[str, Any],
    action: ActionType,
    *,
    policy_version: str = "v1",
) -> PolicyDecision:
    """Authorize a high-impact action using deterministic evidence only.

    This is intentionally independent of the LLM control agents. A policy
    decision is a prerequisite for a future executor; it does not perform an
    external operation itself.
    """
    duplicate_status = _value(
        state, "duplicate_work_analysis", "duplicate_status"
    )

    if action == "PRODUCTION":
        return PolicyDecision(
            action=action,
            disposition="PAUSE",
            reason="Production actions require explicit human authorization.",
            blocking_reasons=["PRODUCTION_ACTION_REQUIRES_HUMAN_APPROVAL"],
            required_actions=["Obtain scoped human approval for the production action."],
            policy_version=policy_version,
        )

    if action == "INVESTIGATION":
        return PolicyDecision(
            action=action,
            disposition="ALLOW",
            reason="Investigation is read-only and may proceed autonomously.",
            policy_version=policy_version,
        )

    if action in {"IMPLEMENTATION", "PUBLISH"} and duplicate_status != "NO_DUPLICATE_FOUND":
        reason = (
            "Duplicate-work verification is incomplete."
            if duplicate_status == "INSUFFICIENT_EVIDENCE"
            else "Duplicate-work verification did not establish a safe clean result."
        )
        disposition = (
            "REQUEST_INFORMATION"
            if duplicate_status == "INSUFFICIENT_EVIDENCE"
            else "DENY"
        )
        return PolicyDecision(
            action=action,
            disposition=disposition,
            reason=reason,
            blocking_reasons=["NO_VERIFIED_DUPLICATE_CHECK"],
            required_actions=["Complete and verify duplicate-work search."],
            evidence_keys=["duplicate_work_analysis"],
            policy_version=policy_version,
        )

    if action == "IMPLEMENTATION":
        review = evaluate_review_gate(state)
        if review.route != "READY_FOR_IMPLEMENTATION":
            return PolicyDecision(
                action=action,
                disposition="DENY",
                reason="Implementation review has not authorized source modification.",
                blocking_reasons=review.blocking_reasons or ["IMPLEMENTATION_REVIEW_NOT_APPROVED"],
                required_actions=review.required_actions,
                evidence_keys=review.evidence_keys,
                policy_version=policy_version,
            )
        # Phase 32: when workspace discovery ran for this case, a grant may
        # only cover repositories the investigation actually discovered.
        violation = _discovery_scope_violation(state)
        if violation:
            return PolicyDecision(
                action=action,
                disposition="DENY",
                reason=(
                    "Remediation targets a repository outside the "
                    "discovered investigation scope."
                ),
                blocking_reasons=[violation],
                required_actions=[
                    "Re-run discovery/investigation so the target repository "
                    "is part of this case's discovered repo set."
                ],
                evidence_keys=["repository_discovery", "remediation_plan"],
                policy_version=policy_version,
            )
        return PolicyDecision(
            action=action,
            disposition="ALLOW",
            reason="Verified duplicate check and implementation review authorize modification.",
            evidence_keys=["duplicate_work_analysis", "review_analysis"],
            policy_version=policy_version,
        )

    if action == "PUBLISH":
        validation = evaluate_validation_gate(state)
        publish = _value(state, "publish_plan", "publication_allowed") is True
        if validation.route != "READY_FOR_PUBLISH" or not publish:
            blockers = list(validation.blocking_reasons)
            if not publish:
                blockers.append("PUBLISH_PLAN_NOT_AUTHORIZED")
            return PolicyDecision(
                action=action,
                disposition="DENY",
                reason="Publishing requires verified validation, tests, and an authorized publish plan.",
                blocking_reasons=blockers,
                required_actions=["Pass validation/testing and obtain publish authorization."],
                evidence_keys=["validation_analysis", "test_result", "publish_plan"],
                policy_version=policy_version,
            )
        return PolicyDecision(
            action=action,
            disposition="ALLOW",
            reason="Validation, testing, duplicate safety, and publish authorization passed.",
            evidence_keys=["duplicate_work_analysis", "validation_analysis", "test_result", "publish_plan"],
            policy_version=policy_version,
        )

    if action == "CLOSE_TICKET":
        return PolicyDecision(
            action=action,
            disposition="PAUSE",
            reason="Ticket closure requires explicit resolution evidence and human policy approval.",
            blocking_reasons=["TICKET_CLOSURE_REQUIRES_EXPLICIT_APPROVAL"],
            required_actions=["Review resolution evidence before closing the ticket."],
            policy_version=policy_version,
        )

    # Customer communication can be generated, but closure/deployment claims
    # remain constrained by the response and audit gates.
    return PolicyDecision(
        action=action,
        disposition="ALLOW",
        reason="Customer response generation is non-mutating and remains subject to final audit.",
        policy_version=policy_version,
    )


def _authorization_gate_decision(
    state: Mapping[str, Any],
    *,
    action: ActionType,
    gate: str,
) -> tuple[PolicyDecision, GateDecision]:
    """Convert an action policy result into a graph-safe route decision."""
    policy = evaluate_action_policy(state, action)
    if policy.disposition == "ALLOW":
        route = "READY_FOR_IMPLEMENTATION" if action == "IMPLEMENTATION" else "READY_FOR_PUBLISH"
        return policy, GateDecision(
            gate=gate,
            route=route,
            reason=policy.reason,
            evidence_keys=policy.evidence_keys,
        )
    return policy, GateDecision(
        gate=gate,
        route="SAFETY_STOP",
        reason=policy.reason,
        blocking_reasons=policy.blocking_reasons,
        required_actions=policy.required_actions,
        evidence_keys=policy.evidence_keys,
    )


def evaluate_implementation_authorization_gate(
    state: Mapping[str, Any],
) -> tuple[PolicyDecision, GateDecision]:
    """Authorize source modification after review and duplicate safety."""
    return _authorization_gate_decision(
        state,
        action="IMPLEMENTATION",
        gate="IMPLEMENTATION_AUTHORIZATION",
    )


def evaluate_publish_authorization_gate(
    state: Mapping[str, Any],
) -> tuple[PolicyDecision, GateDecision]:
    """Authorize external publication only after the publish plan exists."""
    return _authorization_gate_decision(
        state,
        action="PUBLISH",
        gate="PUBLISH_AUTHORIZATION",
    )


def evaluate_validation_gate(state: Mapping[str, Any]) -> GateDecision:
    """Authorize publishing only after validation and testing both pass."""
    validation_status = _value(state, "validation_analysis", "overall_status")
    test_status = _value(state, "test_result", "overall_status")
    validation_ready = _value(
        state, "validation_analysis", "implementation_ready_for_review"
    ) is True
    tests_executed = _value(state, "test_result", "tests_executed") is True
    testing_complete = _value(
        state, "test_result", "required_testing_completed"
    ) is True
    if (
        validation_status == "PASSED"
        and test_status == "PASSED"
        and validation_ready
        and tests_executed
        and testing_complete
    ):
        return GateDecision(
            gate="VALIDATION",
            route="READY_FOR_PUBLISH",
            reason="Validation and testing both passed.",
            evidence_keys=["validation_analysis", "test_result"],
        )

    blockers = []
    if validation_status != "PASSED":
        blockers.append(f"VALIDATION_STATUS:{validation_status or 'UNKNOWN'}")
    if test_status != "PASSED":
        blockers.append(f"TEST_STATUS:{test_status or 'UNKNOWN'}")
    if not validation_ready:
        blockers.append("VALIDATION_NOT_READY_FOR_REVIEW")
    if not tests_executed:
        blockers.append("TESTS_NOT_EXECUTED")
    if not testing_complete:
        blockers.append("REQUIRED_TESTING_INCOMPLETE")
    return GateDecision(
        gate="VALIDATION",
        route="SAFETY_STOP",
        reason="Publishing is blocked until validation and tests both pass.",
        blocking_reasons=blockers,
        required_actions=["Complete and pass required validation and testing."],
        evidence_keys=["validation_analysis", "test_result"],
    )


def evaluate_audit_gate(state: Mapping[str, Any]) -> GateDecision:
    """Permit completion only after an approved final audit."""
    audit_status = _value(state, "workflow_audit", "audit_status")
    if audit_status == "APPROVED":
        return GateDecision(
            gate="AUDIT",
            route="COMPLETED",
            reason="Final audit approved the workflow result.",
            evidence_keys=["workflow_audit"],
        )

    return GateDecision(
        gate="AUDIT",
        route="SAFETY_STOP",
        reason="Final audit did not approve completion.",
        blocking_reasons=[f"AUDIT_STATUS:{audit_status or 'UNKNOWN'}"],
        required_actions=["Resolve audit findings before declaring completion."],
        evidence_keys=["workflow_audit"],
    )


def harden_root_cause_analysis(
    analysis: RootCauseAnalysis | Mapping[str, Any],
    *,
    repository_available: bool = False,
) -> RootCauseAnalysis:
    """Prevent unsupported HIGH-confidence RCA claims.

    Root-cause analysis runs before implementation and validation, so a HIGH
    confidence claim requires a confirmed classification, direct facts, no
    unresolved questions, and an identified repository. Otherwise confidence
    is conservatively reduced and the classification remains unconfirmed.
    """
    normalized = (
        analysis
        if isinstance(analysis, RootCauseAnalysis)
        else RootCauseAnalysis.model_validate(analysis)
    )
    if normalized.confidence != "HIGH":
        return normalized

    high_confidence_supported = (
        normalized.root_cause_determined
        and normalized.classification == "CONFIRMED"
        and bool(normalized.confirmed_facts)
        and not normalized.remaining_unknowns
        and repository_available
    )
    if high_confidence_supported:
        return normalized

    downgraded_confidence = (
        "MEDIUM"
        if normalized.classification == "STRONGLY_SUPPORTED"
        and normalized.confirmed_facts
        else "LOW"
    )
    updates: dict[str, Any] = {
        "confidence": downgraded_confidence,
        "classification": (
            normalized.classification
            if normalized.classification in {"POSSIBLE", "UNKNOWN", "REJECTED"}
            else "POSSIBLE"
        ),
    }
    if not repository_available:
        updates["remaining_unknowns"] = [
            *normalized.remaining_unknowns,
            "Repository/code evidence was not available for confirmation.",
        ]
    return normalized.model_copy(update=updates)
