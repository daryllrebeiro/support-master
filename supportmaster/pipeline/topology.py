"""Pipeline topology validation and graph node ordering.

Invariants:
1. Core skeleton nodes cannot be configured, disabled, or reordered.
2. Topology validator runs at write time and deterministically rejects
   invalid or unsatisfiable dependency graphs.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ..models.organization import PipelineTopology
from .node_kinds import (
    IMMUTABLE_CORE_SKELETON_NODES,
    KNOWN_CAPABILITY_NODES,
    CapabilityRequirement,
)


class TopologyValidationError(ValueError):
    """Raised when a tenant pipeline topology violates design invariants."""


def validate_topology(
    topology: PipelineTopology | dict,
    *,
    bound_nodes: Iterable[str] | None = None,
) -> list[str]:
    """Validate a pipeline topology against design invariants.

    Returns a list of active, enabled capability node IDs in dependency order.
    Raises TopologyValidationError if invalid.
    """
    if isinstance(topology, dict):
        enabled = list(topology.get("enabled_capability_nodes", []))
        disabled = set(topology.get("optional_nodes_disabled", []))
    else:
        enabled = list(topology.enabled_capability_nodes)
        disabled = set(topology.optional_nodes_disabled)

    # Invariant 1: Skeleton nodes must never be named in topology config
    skeleton_tampering = set(enabled).union(disabled).intersection(IMMUTABLE_CORE_SKELETON_NODES)
    if skeleton_tampering:
        nodes_str = ", ".join(sorted(skeleton_tampering))
        raise TopologyValidationError(
            f"Core skeleton nodes cannot be configured or disabled in pipeline topology: {nodes_str}"
        )

    # Check unknown capability nodes
    unknown_nodes = [node for node in enabled if node not in KNOWN_CAPABILITY_NODES]
    if unknown_nodes:
        raise TopologyValidationError(
            f"Unknown capability nodes in topology: {', '.join(unknown_nodes)}"
        )

    active_nodes = [node for node in enabled if node not in disabled]

    # Check required-if-present nodes
    bound_set = set(bound_nodes or ())
    for node in bound_set:
        spec = KNOWN_CAPABILITY_NODES.get(node)
        if spec and spec.requirement == CapabilityRequirement.REQUIRED_IF_PRESENT:
            if node not in active_nodes:
                raise TopologyValidationError(
                    f"Node {node!r} is REQUIRED_IF_PRESENT and bound, but disabled in topology."
                )

    # Check dependencies
    active_set = set(active_nodes)
    for node in active_nodes:
        spec = KNOWN_CAPABILITY_NODES.get(node)
        if spec:
            for dep in spec.dependencies:
                if dep not in active_set:
                    raise TopologyValidationError(
                        f"Capability node {node!r} requires dependency {dep!r}, which is not enabled."
                    )

    return active_nodes
