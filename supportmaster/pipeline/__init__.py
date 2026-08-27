"""SupportMaster Modular Pipeline & Capability Architecture."""

from .capabilities import (
    CanFetchCase,
    CanUpdateCaseStatus,
    CanPostComment,
    CanListRepositories,
    CanSearchCode,
    CanReadFile,
    CanOpenPullRequest,
    CanRunTests,
    CanReadCIStatus,
    CanTriggerCI,
    CanReadMonitoringSignal,
    CanSendNotification,
)
from .node_kinds import CapabilityNode, CoreSkeletonNode
from .registry import AdapterRegistry, default_registry

__all__ = [
    "CanFetchCase",
    "CanUpdateCaseStatus",
    "CanPostComment",
    "CanListRepositories",
    "CanSearchCode",
    "CanReadFile",
    "CanOpenPullRequest",
    "CanRunTests",
    "CanReadCIStatus",
    "CanTriggerCI",
    "CanReadMonitoringSignal",
    "CanSendNotification",
    "CoreSkeletonNode",
    "CapabilityNode",
    "AdapterRegistry",
    "default_registry",
]
