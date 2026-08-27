"""Adapter bindings validation and capability resolution.

Invariant 4 & 5: Bindings pair enabled capability nodes with registered adapters.
Missing optional capabilities degrade gracefully (skip / log / escalate), never crash.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any, Sequence

from ..models.organization import AdapterBindingEntry, AdapterBindingsConfig
from .node_kinds import KNOWN_CAPABILITY_NODES
from .registry import AdapterRegistry, default_registry


class BindingValidationError(ValueError):
    """Raised when an adapter binding config violates registration or capability rules."""


def validate_bindings(
    bindings_config: AdapterBindingsConfig | dict,
    registry: AdapterRegistry | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    verify_env_secrets: bool = False,
) -> dict[str, AdapterBindingEntry]:
    """Validate adapter bindings against registry and capability declarations.

    Returns valid mapping of node_id -> AdapterBindingEntry.
    """
    reg = registry or default_registry
    env = environ if environ is not None else os.environ

    if isinstance(bindings_config, dict):
        raw_bindings = bindings_config.get("bindings", {})
        parsed_bindings = {
            node_id: (
                entry
                if isinstance(entry, AdapterBindingEntry)
                else AdapterBindingEntry.model_validate(entry)
            )
            for node_id, entry in raw_bindings.items()
        }
    else:
        parsed_bindings = bindings_config.bindings

    for node_id, entry in parsed_bindings.items():
        # Check node is known
        if node_id not in KNOWN_CAPABILITY_NODES:
            raise BindingValidationError(f"Cannot bind unknown capability node {node_id!r}.")

        # Check adapter is registered
        adapter_reg = reg.get_registration(entry.adapter_id)
        if adapter_reg is None:
            raise BindingValidationError(
                f"Adapter {entry.adapter_id!r} bound to {node_id!r} is not registered in AdapterRegistry."
            )

        # Check required capabilities
        node_spec = KNOWN_CAPABILITY_NODES[node_id]
        for req_cap in node_spec.required_capabilities:
            if not adapter_reg.supports(req_cap):
                raise BindingValidationError(
                    f"Adapter {entry.adapter_id!r} lacks required capability {req_cap.__name__} for node {node_id!r}."
                )

        # Validate connection secret ref if env:
        if entry.connection_ref.startswith("env:"):
            secret_name = entry.connection_ref.split(":", 1)[1]
            if not secret_name:
                raise BindingValidationError(
                    f"Invalid connection_ref {entry.connection_ref!r} for node {node_id!r}: empty env variable name."
                )
            if verify_env_secrets and not str(env.get(secret_name, "")).strip():
                raise BindingValidationError(
                    f"Environment secret {secret_name!r} for node {node_id!r} is unset."
                )

    return parsed_bindings


def resolve_effective_nodes_and_bindings(
    active_nodes: Sequence[str],
    bindings: Mapping[str, AdapterBindingEntry],
) -> dict[str, AdapterBindingEntry | None]:
    """Resolve the intersection of active topology nodes and valid adapter bindings."""
    resolved: dict[str, AdapterBindingEntry | None] = {}
    for node_id in active_nodes:
        resolved[node_id] = bindings.get(node_id)
    return resolved
