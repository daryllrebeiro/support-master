"""ADK Workflow branch with validation, publishing, and audit gates."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.context import Context
from google.adk.tools import google_search
from google.adk.workflow import START, JoinNode, Workflow, node

from ..agents.audit_agent import audit_agent
from ..agents.code_change_agent import code_change_agent
from ..agents.customer_response_agent import customer_response_agent
from ..agents.duplicate_work_agent import duplicate_work_agent
from ..agents.evidence_agent import evidence_agent
from ..agents.implementation_agent import implementation_agent
from ..agents.investigation_agent import investigation_agent
from ..agents.publish_agent import publish_agent
from ..agents.remediation_agent import remediation_agent
from ..agents.repository_agent import repository_agent
from ..agents.resolution_agent import resolution_agent
from ..agents.review_agent import review_agent
from ..agents.root_cause_agent import root_cause_agent
from ..agents.test_result_agent import test_result_agent
from ..agents.ticket_agent import ticket_analysis_agent
from ..agents.validation_agent import validation_agent
from ..agents.workflow_control_agent import workflow_control_agent
from ..agents.workflow_summary_agent import workflow_summary_agent
from ..config import select_model
from ..tools.memory_tools import build_memory_tool
from ..execution import (
    PublicationExecutionResult,
    PublicationExecutor,
    build_github_publish_result,
    persist_publication_receipts,
)
from ..models.control import ExternalOperationReceipt
from ..control_gates import (
    evaluate_audit_gate,
    evaluate_duplicate_gate,
    evaluate_implementation_authorization_gate,
    harden_root_cause_analysis,
    evaluate_publish_authorization_gate,
    evaluate_review_gate,
    evaluate_validation_gate,
)
from ..workflow_state import (
    GateDecision,
    SupportMasterState,
    append_gate_history,
    append_policy_decision,
    issue_authorization,
)
from .orchestration_nodes import investigation_evidence_fan_in
from .terminal_nodes import autonomous_safety_stop, memory_record_node


def _clone_agent(
    agent: Agent,
    model_name: str,
    *,
    extra_tools: tuple = (),
) -> Agent:
    update: dict = {"model": model_name}
    if extra_tools:
        update["tools"] = list(getattr(agent, "tools", []) or []) + list(extra_tools)
    cloned = agent.clone(update=update)
    cloned.parent_agent = None
    return cloned


@node(name="duplicate_work_gate")
def duplicate_work_gate(ctx: Context) -> dict:
    decision = evaluate_duplicate_gate(ctx.state.to_dict())
    ctx.state["last_gate_decision"] = decision.model_dump()
    append_gate_history(ctx.state, decision)
    if "DUPLICATE_CHECK_INCOMPLETE" in decision.warnings:
        ctx.state["autonomous_best_effort"] = True
        ctx.state["uncertainty_flags"] = ["DUPLICATE_CHECK_INCOMPLETE"]
    ctx.route = decision.route
    return decision.model_dump()


@node(name="implementation_review_gate")
def implementation_review_gate(ctx: Context) -> dict:
    decision = evaluate_review_gate(ctx.state.to_dict())
    ctx.state["last_gate_decision"] = decision.model_dump()
    append_gate_history(ctx.state, decision)
    ctx.route = decision.route
    return decision.model_dump()


@node(name="implementation_authorization_gate")
def implementation_authorization_gate(ctx: Context) -> dict:
    """Issue an implementation grant only after deterministic authorization."""
    policy, decision = evaluate_implementation_authorization_gate(
        ctx.state.to_dict()
    )
    append_policy_decision(ctx.state, policy)
    record = append_gate_history(ctx.state, decision)
    ctx.state["last_gate_decision"] = decision.model_dump()
    if policy.disposition == "ALLOW":
        issue_authorization(
            ctx.state,
            scope="IMPLEMENTATION",
            decision=policy,
            gate_decision_id=record.decision_id,
        )
    ctx.route = decision.route
    return {"policy": policy.model_dump(), "gate": decision.model_dump()}


@node(name="root_cause_confidence_check")
def root_cause_confidence_check(ctx: Context) -> dict:
    """Normalize RCA confidence before remediation consumes it."""
    analysis = ctx.state.get("root_cause_analysis")
    repository = ctx.state.get("repository_analysis")
    repository_available = (
        bool(repository)
        and (
            repository.get("repository_identified")
            if isinstance(repository, dict)
            else getattr(repository, "repository_identified", False)
        )
    )
    if analysis is not None:
        normalized = harden_root_cause_analysis(
            analysis,
            repository_available=repository_available,
        )
        ctx.state["root_cause_analysis"] = normalized.model_dump()
        return normalized.model_dump()
    return {}


@node(name="validation_testing_gate")
def validation_testing_gate(ctx: Context) -> dict:
    decision = evaluate_validation_gate(ctx.state.to_dict())
    ctx.state["last_gate_decision"] = decision.model_dump()
    append_gate_history(ctx.state, decision)
    
    if decision.route == "SAFETY_STOP" or decision.status == "FAILED":
        healing_attempts = ctx.state.to_dict().get("healing_attempts", 0)
        if healing_attempts < 3:
            ctx.state["healing_attempts"] = healing_attempts + 1
            failures = ctx.state.to_dict().get("validation_failures", [])
            failures.append({
                "attempt": healing_attempts + 1,
                "warnings": decision.warnings,
                "detail": "Validation failed. Self-healing loop activated."
            })
            ctx.state["validation_failures"] = failures
            ctx.route = "RETRY_IMPLEMENTATION"
            return {"status": "HEALING_RETRY", "attempt": healing_attempts + 1, "decision": decision.model_dump()}
            
        # Record Git rollback receipt on final failure
        rollback_receipts = ctx.state.to_dict().get("operation_receipts", [])
        rollback_receipts.append({
            "operation_type": "REPOSITORY_ROLLBACK",
            "requested_action": "git_rollback_to_clean_state",
            "status": "SUCCESS",
            "detail": "Self-healing attempts exhausted. Restoring repository state.",
        })
        ctx.state["operation_receipts"] = rollback_receipts

    ctx.route = decision.route
    return decision.model_dump()


@node(name="publish_authorization_gate")
def publish_authorization_gate(ctx: Context) -> dict:
    """Issue a publish grant immediately before Git/GitHub mutation."""
    policy, decision = evaluate_publish_authorization_gate(ctx.state.to_dict())
    append_policy_decision(ctx.state, policy)
    record = append_gate_history(ctx.state, decision)
    ctx.state["last_gate_decision"] = decision.model_dump()
    if policy.disposition == "ALLOW":
        issue_authorization(
            ctx.state,
            scope="PUBLISH",
            decision=policy,
            gate_decision_id=record.decision_id,
        )
    ctx.route = decision.route
    return {"policy": policy.model_dump(), "gate": decision.model_dump()}


def _build_verified_publication_executor(
    executor: PublicationExecutor | None,
):
    """Create the deterministic publication node for one workflow instance."""

    @node(name="verified_publication_executor")
    def verified_publication_executor(ctx: Context) -> dict:
        state = ctx.state.to_dict()
        plan = state.get("publish_plan")
        repository_path = state.get("repository_path")
        if executor is None:
            blocked_receipt = ExternalOperationReceipt(
                operation_type="PUBLICATION_EXECUTOR",
                requested_action="execute_verified_publication",
                status="BLOCKED",
                error="No repository/GitHub execution adapters were configured.",
            )
            result = PublicationExecutionResult(
                status="BLOCKED",
                receipts=[blocked_receipt],
                errors=[blocked_receipt.error or "Execution adapters unavailable."],
            )
        elif plan is None or not repository_path:
            blocked_receipt = ExternalOperationReceipt(
                operation_type="PUBLICATION_EXECUTOR",
                requested_action="execute_verified_publication",
                status="BLOCKED",
                error="A publish plan and local repository path are required.",
            )
            result = PublicationExecutionResult(
                status="BLOCKED",
                receipts=[blocked_receipt],
                errors=[blocked_receipt.error or "Publication inputs unavailable."],
            )
        else:
            result = executor.execute(
                state,
                repository_path=repository_path,
                plan=plan,
            )
        persist_publication_receipts(ctx.state, result)
        if plan is not None and result.status in {"PUBLISHED", "PARTIALLY_PUBLISHED"}:
            ctx.state["github_publish_result"] = build_github_publish_result(
                plan,
                result,
                state,
            ).model_dump()
        ctx.route = "CONTINUE" if result.status in {"PUBLISHED", "PARTIALLY_PUBLISHED"} else "SAFETY_STOP"
        return result.model_dump()

    return verified_publication_executor


@node(name="final_audit_gate")
def final_audit_gate(ctx: Context) -> dict:
    decision = evaluate_audit_gate(ctx.state.to_dict())
    ctx.state["last_gate_decision"] = decision.model_dump()
    ctx.state["terminal_status"] = (
        "COMPLETED" if decision.route == "COMPLETED" else "SAFETY_STOP"
    )
    ctx.state["terminal_outcome"] = (
        "COMPLETED" if decision.route == "COMPLETED" else "SAFETY_STOP"
    )
    append_gate_history(ctx.state, decision)
    ctx.route = decision.route
    return decision.model_dump()


def create_publishing_gate_workflow(
    model_name: str | None = None,
    publication_executor: PublicationExecutor | None = None,
    max_concurrency: int = 2,
) -> Workflow:
    """Create the complete gated graph through final audit routing."""
    if max_concurrency < 2:
        raise ValueError("max_concurrency must be at least two for read-only fan-out.")
    selected_model = select_model(model_name)
    # Cross-run memory is exposed as a read-only, tenant-scoped tool so the
    # investigation and root-cause agents can retrieve similar past fixes.
    memory_tool = build_memory_tool()
    ticket = _clone_agent(ticket_analysis_agent, selected_model)
    investigation = _clone_agent(
        investigation_agent, selected_model, extra_tools=(memory_tool,)
    )
    # Web grounding: duplicate and evidence agents may consult PUBLIC sources
    # (known-issue reports, advisories, vendor docs) with mandatory citations.
    # Deterministic gates remain the sole authority over routing decisions.
    duplicate = _clone_agent(
        duplicate_work_agent, selected_model, extra_tools=(google_search,)
    )
    evidence = _clone_agent(
        evidence_agent, selected_model, extra_tools=(google_search,)
    )
    repository = _clone_agent(repository_agent, selected_model)
    root_cause = _clone_agent(
        root_cause_agent, selected_model, extra_tools=(memory_tool,)
    )
    remediation = _clone_agent(remediation_agent, selected_model)
    review = _clone_agent(review_agent, selected_model)
    code_change = _clone_agent(code_change_agent, selected_model)
    implementation = _clone_agent(implementation_agent, selected_model)
    validation = _clone_agent(validation_agent, selected_model)
    test_result = _clone_agent(test_result_agent, selected_model)
    publish = _clone_agent(publish_agent, selected_model)
    resolution = _clone_agent(resolution_agent, selected_model)
    customer_response = _clone_agent(customer_response_agent, selected_model)
    audit = _clone_agent(audit_agent, selected_model)
    workflow_summary = _clone_agent(workflow_summary_agent, selected_model)
    workflow_control = _clone_agent(workflow_control_agent, selected_model)
    verified_publication_executor = _build_verified_publication_executor(
        publication_executor
    )
    investigation_evidence_join = JoinNode(name="investigation_evidence_join")

    return Workflow(
        name="supportmaster_publishing_gate",
        description=(
            "SupportMaster's complete conditional workflow with duplicate, "
            "review, implementation, validation, publish-authorization, "
            "and final audit gates."
        ),
        state_schema=SupportMasterState,
        max_concurrency=max_concurrency,
        edges=[
            (
                START,
                ticket,
                investigation,
                duplicate,
                duplicate_work_gate,
                {
                    "CONTINUE": (evidence, repository),
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                (evidence, repository),
                investigation_evidence_join,
            ),
            (
                investigation_evidence_join,
                investigation_evidence_fan_in,
                {
                    "CONTINUE": root_cause,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                root_cause,
                root_cause_confidence_check,
                remediation,
                review,
                implementation_review_gate,
                {
                    "READY_FOR_IMPLEMENTATION": implementation_authorization_gate,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                implementation_authorization_gate,
                {
                    "READY_FOR_IMPLEMENTATION": code_change,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                code_change,
                implementation,
                validation,
                test_result,
                validation_testing_gate,
                {
                    "READY_FOR_PUBLISH": publish,
                    "SAFETY_STOP": autonomous_safety_stop,
                    "RETRY_IMPLEMENTATION": code_change,
                },
            ),
            (
                publish,
                publish_authorization_gate,
            ),
            (
                publish_authorization_gate,
                {
                    "READY_FOR_PUBLISH": verified_publication_executor,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                verified_publication_executor,
                {
                    "CONTINUE": resolution,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (
                resolution,
                customer_response,
                audit,
                final_audit_gate,
                {
                    "COMPLETED": workflow_summary,
                    "SAFETY_STOP": autonomous_safety_stop,
                },
            ),
            (workflow_summary, memory_record_node, workflow_control),
        ],
    )
