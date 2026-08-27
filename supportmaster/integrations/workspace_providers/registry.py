"""Tenant workspace-connection registry.

Turns an ``OrganizationProfile``'s ``workspace_connections`` into concrete,
gateway-guarded provider instances. Secrets are resolved through an injected
``SecretResolver`` at composition time and never stored on the provider, in
state, receipts, logs, or telemetry. Resolution failures disable that one
connection for the run instead of failing discovery.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from ...models.discovery import ProviderName, RepoPage
from ...models.organization import OrganizationProfile
from ..http import UrllibJsonTransport
from .base import FakeWorkspaceProvider

DEFAULT_BASE_URLS: dict[str, str] = {
    "github": "https://api.github.com",
    "bitbucket": "https://api.bitbucket.org",
    "gitlab": "https://gitlab.com",
}

SecretResolver = Callable[[str], str]
TransportFactory = Callable[[str, str, str], Any]


def resolve_secret(secret_ref: str, *, resolver: SecretResolver | None = None) -> str:
    """Resolve a connection's ``secret_ref`` to a token.

    Supported schemes:
      - ``env:NAME`` — read from the process environment.
      - anything else — delegated to the injected resolver (e.g. Secret
        Manager). A bare token is rejected so raw secrets never end up in
        org profiles.
    """
    if secret_ref.startswith("env:"):
        name = secret_ref.split(":", 1)[1]
        value = os.getenv(name, "")
        if not value:
            raise ValueError(f"Environment secret {name!r} is not set.")
        return value
    if resolver is None:
        raise ValueError(
            "secret_ref requires an injected secret resolver "
            "(only 'env:' references resolve without one)."
        )
    return resolver(secret_ref)


def _default_transport_factory(provider: str, workspace_id: str, token: str):
    base_url = DEFAULT_BASE_URLS.get(provider)
    if not base_url:
        raise ValueError(f"Unsupported workspace provider: {provider!r}")
    return UrllibJsonTransport(base_url, bearer_token=token)


def build_workspace_providers(
    profile: OrganizationProfile | None,
    *,
    resolver: SecretResolver | None = None,
    transport_factory: TransportFactory | None = None,
) -> list[Any]:
    """Build one provider per usable workspace connection.

    Connections whose secrets cannot be resolved are skipped (recorded by the
    caller through receipts); an empty result means discovery degrades to
    static-mapping + memory signals only.
    """
    if profile is None:
        return []
    factory = transport_factory or _default_transport_factory
    from ...models.organization import WorkspaceConnection

    providers: list[Any] = []
    for connection in profile.workspace_connections:
        if not isinstance(connection, WorkspaceConnection):
            continue
        try:
            token = resolve_secret(connection.secret_ref, resolver=resolver)
            transport = factory(connection.provider, connection.workspace_id, token)
        except Exception:
            continue
        provider_class = _PROVIDER_CLASSES.get(connection.provider)
        if provider_class is None:
            continue
        providers.append(
            provider_class(workspace_id=connection.workspace_id, transport=transport)
        )
    return providers


_PROVIDER_CLASSES: dict[str, type] = {}


def _register_http_providers() -> None:
    from .bitbucket_provider import HttpBitbucketWorkspaceProvider
    from .github_provider import HttpGitHubWorkspaceProvider
    from .gitlab_provider import HttpGitLabWorkspaceProvider

    _PROVIDER_CLASSES.update(
        {
            "github": HttpGitHubWorkspaceProvider,
            "bitbucket": HttpBitbucketWorkspaceProvider,
            "gitlab": HttpGitLabWorkspaceProvider,
        }
    )


_register_http_providers()


class WorkspaceListCache:
    """Process-local TTL cache over ``list_repositories`` pages per connection."""

    def __init__(self, ttl_seconds: int = 900) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self._entries: dict[str, tuple[float, RepoPage]] = {}

    def get(self, connection_id: str) -> RepoPage | None:
        entry = self._entries.get(connection_id)
        if entry is None:
            return None
        expires_at, page = entry
        if time.monotonic() >= expires_at:
            self._entries.pop(connection_id, None)
            return None
        return page

    def put(self, connection_id: str, page: RepoPage) -> None:
        self._entries[connection_id] = (
            time.monotonic() + self.ttl_seconds,
            page,
        )

    def clear(self) -> None:
        self._entries.clear()


def fake_provider_from_profile_entry(
    *,
    provider: ProviderName,
    workspace_id: str,
    **kwargs,
) -> FakeWorkspaceProvider:
    """Build a fake provider bound to one connection id (tests/golden path)."""
    return FakeWorkspaceProvider(provider_name=provider, workspace_id=workspace_id, **kwargs)