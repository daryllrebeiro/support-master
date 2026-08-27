"""Core skeleton vs capability node classification.

Invariants:
1. Core skeleton nodes are immutable and non-configurable. They ship fixed.
   No tenant config, adapter, or capability node can disable, reorder around,
   or influence their decision logic.
2. Capability nodes are pluggable and resolved from tenant topology and bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence, Type


class NodeKind(str, Enum):
    CORE_SKELETON = "CORE_SKELETON"
    CAPABILITY = "CAPABILITY"


class CapabilityRequirement(str, Enum):
    """Requirement policy for a capability node in a pipeline topology."""

    REQUIRED_IF_PRESENT = "REQUIRED_IF_PRESENT"
    OPTIONAL = "OPTIONAL"


# Fixed set of core skeleton nodes that cannot be configured or disabled.
IMMUTABLE_CORE_SKELETON_NODES: frozenset[str] = frozenset(
    {
        "duplicate_work_gate",
        "investigation_evidence_join",
        "investigation_evidence_fan_in",
        "implementation_review_gate",
        "implementation_authorization_gate",
        "validation_testing_gate",
        "publish_authorization_gate",
        "verified_publication_executor",
        "final_audit_gate",
        "autonomous_safety_stop",
        "failure_diagnosis",
    }
)


@dataclass(frozen=True)
class CoreSkeletonNode:
    """Represents a fixed, immutable safety gate or skeleton router."""

    node_id: str
    description: str
    kind: NodeKind = NodeKind.CORE_SKELETON

    def __post_init__(self) -> None:
        if self.node_id not in IMMUTABLE_CORE_SKELETON_NODES:
            raise ValueError(
                f"Node {self.node_id!r} is not an authorized core skeleton node."
            )


@dataclass(frozen=True)
class CapabilityNodeSpec:
    """Specification of a pluggable capability node."""

    node_id: str
    description: str
    required_capabilities: tuple[Type, ...]
    requirement: CapabilityRequirement = CapabilityRequirement.OPTIONAL
    dependencies: tuple[str, ...] = ()
    kind: NodeKind = NodeKind.CAPABILITY


# Registry of known capability node specifications
KNOWN_CAPABILITY_NODES: dict[str, CapabilityNodeSpec] = {
    "ticket_intake": CapabilityNodeSpec(
        node_id="ticket_intake",
        description="Fetch and normalize case tickets from issue tracker",
        required_capabilities=(),
        requirement=CapabilityRequirement.REQUIRED_IF_PRESENT,
        dependencies=(),
    ),
    "evidence_gathering": CapabilityNodeSpec(
        node_id="evidence_gathering",
        description="Gather evidence from public docs or web sources",
        required_capabilities=(),
        requirement=CapabilityRequirement.OPTIONAL,
        dependencies=(),
    ),
    "repository_discovery": CapabilityNodeSpec(
        node_id="repository_discovery",
        description="Discover candidate repositories from workspace provider",
        required_capabilities=(),
        requirement=CapabilityRequirement.REQUIRED_IF_PRESENT,
        dependencies=(),
    ),
    "repository_investigation": CapabilityNodeSpec(
        node_id="repository_investigation",
        description="Investigate source code in discovered repositories",
        required_capabilities=(),
        requirement=CapabilityRequirement.REQUIRED_IF_PRESENT,
        dependencies=("repository_discovery",),
    ),
    "code_change": CapabilityNodeSpec(
        node_id="code_change",
        description="Synthesize code modifications",
        required_capabilities=(),
        requirement=CapabilityRequirement.REQUIRED_IF_PRESENT,
        dependencies=("repository_investigation",),
    ),
    "ci_validation": CapabilityNodeSpec(
        node_id="ci_validation",
        description="Run test suite and CI validation",
        required_capabilities=(),
        requirement=CapabilityRequirement.OPTIONAL,
        dependencies=("code_change",),
    ),
    "notification": CapabilityNodeSpec(
        node_id="notification",
        description="Send status notifications on progress or completion",
        required_capabilities=(),
        requirement=CapabilityRequirement.OPTIONAL,
        dependencies=(),
    ),
    "monitoring_correlation": CapabilityNodeSpec(
        node_id="monitoring_correlation",
        description="Correlate metrics and incident alerts with case",
        required_capabilities=(),
        requirement=CapabilityRequirement.OPTIONAL,
        dependencies=(),
    ),
}

CapabilityNode = CapabilityNodeSpec
