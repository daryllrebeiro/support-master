"""Deterministic planning helpers; they recommend, never authorize mutation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..models.remediation import RemediationPlan, RemediationStep
from ..models.root_cause import RootCauseAnalysis, RootCauseHypothesis
from ..models.support_case import SupportCase
from ..models.investigation_artifacts import InvestigationSummary


class PlanningService:
    """Build conservative planning artifacts from investigation outputs."""

    def assess_root_cause(
        self,
        case: SupportCase,
        investigation: InvestigationSummary,
    ) -> RootCauseAnalysis:
        repository_available = bool(investigation.repository_signals)
        evidence_available = bool(investigation.evidence_links or investigation.incident_correlations or repository_available)
        gaps = [item.evidence_type for item in investigation.missing_evidence]
        if not evidence_available or investigation.investigation_status == "BLOCKED":
            hypothesis = RootCauseHypothesis(
                hypothesis="The underlying cause is not determined from the available evidence.",
                classification="UNKNOWN",
                confidence="LOW",
                verification_gaps=gaps or ["Additional technical evidence is required."],
            )
            return RootCauseAnalysis(
                root_cause_determined=False,
                explanation="No sufficient, directly supporting investigation evidence is available.",
                hypotheses=[hypothesis],
                remaining_unknowns=gaps or ["Root cause is unknown."],
                recommended_verification=["Collect the missing evidence and rerun investigation."],
                recommended_next_agent="MORE_INFORMATION_REQUIRED",
            )

        signals: list[str] = []
        supporting: list[str] = []
        if investigation.incident_correlations:
            incident = investigation.incident_correlations[0]
            signals.append(f"A correlated incident affects service {incident.service}.")
            supporting.append(incident.incident_id)
        if investigation.repository_signals:
            signal = investigation.repository_signals[0]
            signals.append(signal.summary)
            supporting.append(signal.path or signal.repository)
        if investigation.related_cases:
            supporting.extend(match.case_id for match in investigation.related_cases[:3])
        classification = "STRONGLY_SUPPORTED" if repository_available and not gaps else "POSSIBLE"
        confidence = "HIGH" if classification == "STRONGLY_SUPPORTED" else ("MEDIUM" if repository_available else "LOW")
        hypothesis = RootCauseHypothesis(
            hypothesis=f"Observed case behavior is traced to {signals[0] if signals else 'identified component defect'}.",
            classification=classification,
            confidence=confidence,
            supporting_evidence=supporting,
            verification_gaps=gaps,
        )
        return RootCauseAnalysis(
            root_cause_determined=(classification == "STRONGLY_SUPPORTED"),
            primary_root_cause="; ".join(signals) or f"Verified defect in {case.service or 'identified component'}.",
            confidence=confidence,
            classification=classification,
            explanation=" ".join(signals) or "Investigation signals directly substantiate root-cause mechanism.",
            hypotheses=[hypothesis],
            confirmed_facts=[f"Evidence record {link.record_id} is available." for link in investigation.evidence_links],
            inferred_facts=signals,
            remaining_unknowns=gaps,
            recommended_verification=["Execute validation test suite against verified fix."],
            recommended_next_agent="FIX_PLANNING_AGENT" if not gaps else "EVIDENCE_AGENT",
        )

    def plan_remediation(
        self,
        case: SupportCase,
        root_cause: RootCauseAnalysis,
        investigation: InvestigationSummary,
    ) -> RemediationPlan:
        critical_gaps = [item.evidence_type for item in investigation.missing_evidence if item.importance == "CRITICAL"]
        uncertain = root_cause.classification not in {"CONFIRMED", "STRONGLY_SUPPORTED"}
        if critical_gaps or uncertain:
            return RemediationPlan(
                remediation_status="NEEDS_MORE_EVIDENCE",
                objective="Establish a verified causal explanation before proposing a mutating fix.",
                root_cause=root_cause.primary_root_cause,
                proposed_approach="Collect and validate the missing evidence before implementation planning.",
                unresolved_questions=critical_gaps or root_cause.remaining_unknowns,
                testing_strategy=["Reproduce the original case and capture direct technical evidence."],
                implementation_allowed=False,
                next_action="GATHER_MORE_EVIDENCE",
            )
        component = case.service or case.product or "affected component"
        step = RemediationStep(
            step=1,
            action=f"Inspect and correct the verified failure path in {component}.",
            change_type="CODE",
            priority="HIGH" if case.priority in {"urgent", "critical"} else "MEDIUM",
            rationale="The step addresses the confirmed root-cause evidence.",
            expected_result="The original failure no longer occurs under the documented reproduction.",
            risk="The change may affect adjacent workflows; preserve backward-compatible behavior.",
            validation="Run the original reproduction, regression tests, and relevant integration checks.",
        )
        return RemediationPlan(
            remediation_status="READY",
            objective=f"Correct the verified failure affecting {component}.",
            root_cause=root_cause.primary_root_cause,
            proposed_approach="Implement the smallest reversible change supported by direct evidence.",
            remediation_steps=[step],
            affected_components=[component],
            risks=[step.risk],
            testing_strategy=[step.validation],
            regression_scenarios=["Existing successful behavior must remain unchanged."],
            rollout_considerations=["Use a reviewed branch and staged rollout where available."],
            implementation_allowed=False,
            next_action="IMPLEMENT_FIX",
        )

    def build(self, case: SupportCase, investigation: InvestigationSummary) -> tuple[RootCauseAnalysis, RemediationPlan]:
        root_cause = self.assess_root_cause(case, investigation)
        return root_cause, self.plan_remediation(case, root_cause, investigation)
