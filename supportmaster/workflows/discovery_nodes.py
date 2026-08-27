"""Deterministic repository-discovery node for the investigation fan-out.

This node replaces no LLM judgment: it runs the ranked ``DiscoveryService``
pipeline, persists ``DiscoveryResult`` plus one receipt per workspace read,
and routes either straight to the Repository Agent or through the bounded
disambiguation agent when too many comparable candidates remain.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from google.adk.agents.context import Context
from google.adk.workflow import node

from ..config import discovery_enabled
from ..integrations.workspace_providers import build_workspace_providers
from ..investigation.discovery import Disambiguator, DiscoveryService
from ..models.support_case import SupportCase
from ..workflow_state import append_operation_receipts


def _default_service_factory(state: dict[str, Any]) -> DiscoveryService:
    """Build a discovery service from the run's own tenant org profile.

    Tenant boundary: the profile comes from session state, which the runtime
    populated from the authenticated operator's tenant — never from model
    output or request payloads.
    """
    profile = state.get("organization_profile")
    resolver = None
    if os.getenv("SUPPORTMASTER_WORKSPACE_SECRET_RESOLVER", "").strip() == "env":
        def resolver(secret_ref: str) -> str:  # pragma: no cover - trivial
            from ..integrations.workspace_providers.registry import resolve_secret

            return resolve_secret(secret_ref)
    providers = build_workspace_providers(profile, resolver=resolver)
    return DiscoveryService(providers=providers, profile=profile)


def build_repository_discovery_node(
    service_factory: Callable[[dict[str, Any]], DiscoveryService] | None = None,
    disambiguator: Disambiguator | None = None,
):
    """Create the discovery node; inject a factory/disambiguator for tests."""
    factory = service_factory or _default_service_factory

    @node(name="repository_discovery_node")
    def repository_discovery_node(ctx: Context) -> dict:
        state = ctx.state.to_dict()
        profile = state.get("organization_profile")
        policy_enabled = bool(getattr(profile, "discovery_policy", None) is not None and getattr(profile.discovery_policy, "enabled", False))
        if not (discovery_enabled() and policy_enabled):
            # Feature off: pass through without touching workspaces so legacy
            # static-mapping tenants see byte-identical behavior.
            ctx.route = "CONTINUE"
            return {"status": "DISABLED"}

        case_payload = state.get("support_case")
        case = SupportCase.model_validate(case_payload) if isinstance(case_payload, dict) else case_payload
        ticket_analysis = state.get("ticket_analysis")
        ticket_text = ""
        if isinstance(ticket_analysis, dict):
            ticket_text = " ".join(
                str(value)
                for value in (
                    ticket_analysis.get("summary"),
                    ticket_analysis.get("problem_statement"),
                )
                if value
            )

        service = factory(state)
        result, receipts = service.discover(
            case=case if isinstance(case, SupportCase) else None,
            ticket_text=ticket_text,
            tenant_id=str(state.get("tenant_id", "default")),
        )

        ctx.state["repository_discovery"] = result.model_dump(mode="json")
        append_operation_receipts(ctx.state, receipts)
        integration_results = state.get("integration_results") or {}
        integration_results["workspace_discovery"] = {
            "connections_used": result.connections_used,
            "candidates": [item.model_dump(mode="json") for item in result.candidates],
            "selected": [ref.model_dump(mode="json") for ref in result.selected],
            "method_trace": result.method_trace,
            "degraded": result.degraded,
        }
        ctx.state["integration_results"] = integration_results

        if result.degraded:
            flags = list(state.get("uncertainty_flags") or [])
            if "WORKSPACE_DISCOVERY_DEGRADED" not in flags:
                flags.append("WORKSPACE_DISCOVERY_DEGRADED")
            ctx.state["uncertainty_flags"] = flags

        max_selected = 3
        if getattr(profile, "discovery_policy", None) is not None:
            max_selected = profile.discovery_policy.max_disambiguation_repos
        comparable = [item for item in result.candidates if item.score > 0]
        if disambiguator is not None and len(comparable) > max_selected:
            ctx.route = "NEEDS_DISAMBIGUATION"
        else:
            ctx.route = "CONTINUE"
        return {
            "status": "COMPLETED",
            "selected": [ref.key() for ref in result.selected],
            "degraded": result.degraded,
            "workspace_calls_made": result.workspace_calls_made,
        }

    return repository_discovery_node


repository_discovery_node = build_repository_discovery_node()