"""Deterministic terminal nodes for autonomous workflow outcomes."""

from __future__ import annotations

from google.adk.agents.context import Context
from google.adk.workflow import node

from ..workflow_state import AutonomousStop


def record_completed_run_to_memory(ctx: Context) -> dict:
    """Persist a completed run into cross-run memory (fail-open, never blocks)."""
    state = ctx.state.to_dict()
    if state.get("terminal_status") != "COMPLETED":
        return {}
    try:
        from ..memory.retriever import CaseContextRetriever

        case = state.get("support_case") or {}
        rca = state.get("root_cause_analysis") or {}
        CaseContextRetriever().record_resolution(
            case_id=str(case.get("case_id", "")),
            tenant_id=str(state.get("tenant_id", "default")),
            title=str(case.get("title", ""))[:500],
            description=str(case.get("description", ""))[:2000],
            root_cause=str(rca.get("primary_root_cause") or rca)[:2000],
            resolution_summary=str(state.get("workflow_summary_text", ""))[:2000],
        )
        ctx.state["memory_recorded"] = True
    except Exception as error:
        # Memory is an optimization, never a correctness dependency.
        ctx.state["memory_recorded"] = False
        ctx.state["memory_record_error"] = f"{type(error).__name__}: {error}"
    return {"memory_recorded": ctx.state.get("memory_recorded", False)}


memory_record_node = node(name="memory_record_node")(record_completed_run_to_memory)


@node(name="autonomous_safety_stop")
def autonomous_safety_stop(ctx: Context) -> dict:
    """Record a fail-closed stop without asking a human to resume the run."""
    decision = ctx.state.get("last_gate_decision") or {}
    stop = AutonomousStop(
        gate=decision.get("gate", "DUPLICATE_WORK"),
        reason=decision.get(
            "reason", "A mandatory safety gate did not pass."
        ),
        blocking_reasons=decision.get("blocking_reasons", []),
        required_actions=decision.get("required_actions", []),
        evidence_keys=decision.get("evidence_keys", []),
    )
    ctx.state["autonomous_stop"] = stop.model_dump()
    ctx.state["terminal_status"] = "SAFETY_STOP"
    ctx.state["terminal_outcome"] = "SAFETY_STOP"
    ctx.route = "SAFETY_STOP"
    return stop.model_dump()
