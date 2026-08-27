import unittest

from supportmaster.control_gates import (
    evaluate_action_policy,
    evaluate_audit_gate,
    evaluate_duplicate_gate,
    evaluate_implementation_authorization_gate,
    evaluate_review_gate,
    evaluate_validation_gate,
)
from supportmaster.models.audit import WorkflowAudit
from supportmaster.models.duplicate_work import DuplicateWorkAnalysis
from supportmaster.models.review import ReviewAnalysis
from supportmaster.models.test_result import TestResult
from supportmaster.models.validation import ValidationAnalysis


def discovery_selected(*keys: str) -> dict:
    """Build a repository_discovery payload with the given repo keys."""
    selected = []
    for key in keys:
        provider, rest = key.split(":", 1)
        workspace, repo = rest.split("/", 1)
        selected.append(
            {"provider": provider, "workspace_id": workspace, "repo": repo}
        )
    return {"selected": selected, "degraded": False}


def duplicate_result(status: str) -> DuplicateWorkAnalysis:
    return DuplicateWorkAnalysis(
        duplicate_status=status,
        search_performed=True,
        conclusion="Test conclusion",
        recommended_action="Test action",
    )


def review_result(**updates: object) -> ReviewAnalysis:
    values: dict[str, object] = {
        "review_status": "APPROVED",
        "decision": "PROCEED_TO_HUMAN_REVIEW",
        "review_confidence": "HIGH",
        "original_problem": "Problem",
        "root_cause_reviewed": "Cause",
        "root_cause_sufficiently_established": True,
        "remediation_alignment": True,
        "implementation_scope_acceptable": True,
        "duplicate_work_safety_passed": True,
        "validation_sufficient": True,
        "original_problem_resolved": False,
        "regression_risk_acceptable": True,
        "implementation_reviewable": True,
        "review_summary": "Approved for implementation",
        "recommendation": "Proceed",
    }
    values.update(updates)
    return ReviewAnalysis(**values)


def validation_result(**updates: object) -> ValidationAnalysis:
    values: dict[str, object] = {
        "overall_status": "PASSED",
        "validation_confidence": "HIGH",
        "implementation_ready_for_review": True,
        "original_problem": "Problem",
        "expected_behavior": "Expected",
        "root_cause_addressed": True,
        "root_cause_validation_summary": "Validated",
        "regression_detected": False,
        "recommendation": "Proceed",
        "next_action": "APPROVE_FOR_REVIEW",
    }
    values.update(updates)
    return ValidationAnalysis(**values)


def test_result(**updates: object) -> TestResult:
    values: dict[str, object] = {
        "ticket_id": "TEST-1",
        "overall_status": "PASSED",
        "tests_executed": True,
        "required_testing_completed": True,
        "original_issue_reproduced": True,
        "original_issue_resolved": True,
        "regression_risk": "LOW",
        "resolution_verifiable": True,
    }
    values.update(updates)
    return TestResult(**values)


def audit_result(status: str) -> WorkflowAudit:
    return WorkflowAudit(
        audit_status=status,
        evidence_quality="SUFFICIENT",
        workflow_complete=status == "APPROVED",
        duplicate_gate_passed=True,
        validation_gate_passed=True,
        implementation_supported=True,
        publication_gate_passed=True,
        resolution_supported=True,
        customer_response_supported=True,
        traceability_complete=True,
        final_recommendation="Test recommendation",
    )


class ControlGateTests(unittest.TestCase):
    def test_duplicate_gate_requires_verified_clean_result(self) -> None:
        self.assertEqual(
            evaluate_duplicate_gate(
                {"duplicate_work_analysis": duplicate_result("NO_DUPLICATE_FOUND")}
            ).route,
            "CONTINUE",
        )
        for status in ("DUPLICATE_FOUND", "RELATED_WORK_FOUND"):
            with self.subTest(status=status):
                decision = evaluate_duplicate_gate(
                    {"duplicate_work_analysis": duplicate_result(status)}
                )
                self.assertEqual(decision.route, "SAFETY_STOP")
        incomplete = evaluate_duplicate_gate(
            {"duplicate_work_analysis": duplicate_result("INSUFFICIENT_EVIDENCE")}
        )
        self.assertEqual(incomplete.route, "CONTINUE")
        self.assertIn("DUPLICATE_CHECK_INCOMPLETE", incomplete.warnings)
        self.assertEqual(evaluate_duplicate_gate({}).route, "SAFETY_STOP")

    def test_review_gate_requires_all_safety_checks(self) -> None:
        self.assertEqual(
            evaluate_review_gate({"review_analysis": review_result()}).route,
            "READY_FOR_IMPLEMENTATION",
        )
        self.assertEqual(
            evaluate_review_gate(
                {"review_analysis": review_result(implementation_scope_acceptable=False)}
            ).route,
            "SAFETY_STOP",
        )
        self.assertEqual(evaluate_review_gate({}).route, "SAFETY_STOP")

    def test_validation_gate_requires_both_validation_and_tests(self) -> None:
        self.assertEqual(
            evaluate_validation_gate(
                {
                    "validation_analysis": validation_result(),
                    "test_result": test_result(),
                }
            ).route,
            "READY_FOR_PUBLISH",
        )
        self.assertEqual(
            evaluate_validation_gate(
                {
                    "validation_analysis": validation_result(),
                    "test_result": test_result(overall_status="NOT_RUN", tests_executed=False, required_testing_completed=False),
                }
            ).route,
            "SAFETY_STOP",
        )

    def test_audit_gate_blocks_everything_except_approved(self) -> None:
        self.assertEqual(
            evaluate_audit_gate({"workflow_audit": audit_result("APPROVED")}).route,
            "COMPLETED",
        )
        self.assertEqual(
            evaluate_audit_gate({"workflow_audit": audit_result("BLOCKED")}).route,
            "SAFETY_STOP",
        )
        self.assertEqual(
            evaluate_audit_gate(
                {"workflow_audit": audit_result("APPROVED_WITH_WARNINGS")}
            ).route,
            "SAFETY_STOP",
        )


class DiscoveryScopeTests(unittest.TestCase):
    """Phase 32: grants cannot cover repos outside the discovered set."""

    def _state(self, components: list[str], discovery: dict | None) -> dict:
        state = {
            "duplicate_work_analysis": duplicate_result("NO_DUPLICATE_FOUND"),
            "review_analysis": review_result(),
            "remediation_plan": {"affected_components": components},
        }
        if discovery is not None:
            state["repository_discovery"] = discovery
        return state

    def test_plan_targeting_discovered_repo_is_allowed(self) -> None:
        policy, decision = evaluate_implementation_authorization_gate(
            self._state(
                ["billing-api export worker"],
                discovery_selected("github:acme/billing-api"),
            )
        )
        self.assertEqual(policy.disposition, "ALLOW")
        self.assertEqual(decision.route, "READY_FOR_IMPLEMENTATION")

    def test_full_repo_key_component_is_allowed(self) -> None:
        policy, _ = evaluate_implementation_authorization_gate(
            self._state(
                ["github:acme/billing-api"],
                discovery_selected("github:acme/billing-api", "github:acme/web"),
            )
        )
        self.assertEqual(policy.disposition, "ALLOW")

    def test_out_of_scope_repo_is_rejected(self) -> None:
        policy, decision = evaluate_implementation_authorization_gate(
            self._state(
                ["payments-ledger"],
                discovery_selected("github:acme/billing-api"),
            )
        )
        self.assertEqual(policy.disposition, "DENY")
        self.assertIn("REPO_NOT_IN_DISCOVERY_SCOPE", policy.blocking_reasons)
        self.assertEqual(decision.route, "SAFETY_STOP")

    def test_no_discovery_keeps_legacy_behavior(self) -> None:
        policy, decision = evaluate_implementation_authorization_gate(
            self._state(["anything-at-all"], None)
        )
        self.assertEqual(policy.disposition, "ALLOW")
        self.assertEqual(decision.route, "READY_FOR_IMPLEMENTATION")

    def test_empty_selection_keeps_legacy_behavior(self) -> None:
        policy, _ = evaluate_implementation_authorization_gate(
            self._state(["anything-at-all"], {"selected": []})
        )
        self.assertEqual(policy.disposition, "ALLOW")

    def test_policy_helper_reports_violation_directly(self) -> None:
        policy = evaluate_action_policy(
            self._state(
                ["unrelated"],
                discovery_selected("github:acme/billing-api"),
            ),
            "IMPLEMENTATION",
        )
        self.assertEqual(policy.disposition, "DENY")


if __name__ == "__main__":
    unittest.main()
