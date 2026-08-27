"""Read-only VCS workspace providers for repository discovery."""

from .base import FakeWorkspaceProvider, WorkspaceProvider, default_activity_window
from .registry import (
    SecretResolver,
    TransportFactory,
    WorkspaceListCache,
    build_workspace_providers,
    resolve_secret,
)

__all__ = [
    "FakeWorkspaceProvider",
    "SecretResolver",
    "TransportFactory",
    "WorkspaceListCache",
    "WorkspaceProvider",
    "build_workspace_providers",
    "default_activity_window",
    "resolve_secret",
]