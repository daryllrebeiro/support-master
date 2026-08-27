"""Read-only workspace tools exposed to ADK agents, scoped to discovery.

The Repository Agent may use these tools to deep-read files or run code
searches **only** inside repositories that this run's deterministic
discovery stage selected. The allowed set comes from session state
(``repository_discovery.selected``), never from model-supplied arguments,
and every call flows through a gateway-guarded ``WorkspaceProvider`` so it
is receipted like every other external read.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from ..integrations.workspace_providers import build_workspace_providers
from ..models.organization import OrganizationProfile


def _allowed_refs(state: dict[str, Any]) -> set[str]:
    """Full ``provider:workspace/repo`` keys this run may touch."""
    discovery = state.get("repository_discovery") or {}
    if not isinstance(discovery, dict):
        return set()
    refs = set()
    for ref in discovery.get("selected") or []:
        if not isinstance(ref, dict):
            continue
        key = (
            f"{ref.get('provider', '')}:{ref.get('workspace_id', '')}"
            f"/{ref.get('repo', '')}"
        )
        if key.rstrip(":/"):
            refs.add(key)
    return refs


def _providers_for_state(
    state: dict[str, Any],
    injected: dict[str, Any] | None,
) -> dict[str, Any]:
    if injected is not None:
        return injected
    profile = state.get("organization_profile")
    if not isinstance(profile, OrganizationProfile):
        return {}
    return {
        provider.connection_id: provider
        for provider in build_workspace_providers(profile)
    }


def _resolve_target(
    provider_name: str,
    workspace_id: str,
    repo: str,
    tool_context: ToolContext,
    injected: dict[str, Any] | None,
):
    """Validate scope and locate the owning provider for one repo ref."""
    state = dict(tool_context.state.to_dict())
    key = f"{provider_name}:{workspace_id}/{repo}"
    if key not in _allowed_refs(state):
        return None, {
            "error": "REPO_NOT_IN_DISCOVERY_SCOPE",
            "detail": (
                "Only repositories selected by this run's repository "
                "discovery may be read."
            ),
            "requested": key,
        }
    providers = _providers_for_state(state, injected)
    provider = providers.get(f"{provider_name}:{workspace_id}")
    if provider is None:
        return None, {
            "error": "CONNECTION_UNAVAILABLE",
            "detail": f"No workspace connection for {provider_name}:{workspace_id}.",
            "requested": key,
        }
    from ..models.discovery import RepoRef

    try:
        ref = RepoRef.model_validate(
            {"provider": provider_name, "workspace_id": workspace_id, "repo": repo}
        )
    except Exception:
        return None, {"error": "INVALID_REPO_REF", "requested": key}
    return (provider, ref), None


def build_workspace_tools(
    providers: dict[str, Any] | None = None,
) -> list[FunctionTool]:
    """Build ``read_discovered_file`` / ``search_discovered_code`` tools.

    ``providers`` maps ``"{provider}:{workspace_id}"`` connection ids to
    provider instances; when omitted they are built from the run's org
    profile on first use.
    """

    def read_discovered_file(
        provider: str,
        workspace_id: str,
        repo: str,
        path: str,
        tool_context: ToolContext,
    ) -> dict:
        """Read one file from a discovered repository (read-only).

        Args:
            provider: VCS provider of the connection ("github", "bitbucket",
                or "gitlab").
            workspace_id: Org/workspace/group slug that owns the repo.
            repo: Repository slug selected by repository discovery.
            path: File path inside the repository.
        Returns:
            A dict with ``content`` and receipt ``status``, or an ``error``
            payload when the repo is outside the discovered scope.
        """
        resolved, error = _resolve_target(
            provider, workspace_id, repo, tool_context, providers
        )
        if error is not None:
            return error
        target, ref = resolved
        blob, receipt = target.read_file(ref, path)
        return {
            "content": blob.content,
            "path": blob.path,
            "status": receipt.status,
        }

    def search_discovered_code(
        provider: str,
        workspace_id: str,
        repo: str,
        query: str,
        tool_context: ToolContext,
    ) -> dict:
        """Search code inside one discovered repository (read-only).

        Args:
            provider: VCS provider of the connection ("github", "bitbucket",
                or "gitlab").
            workspace_id: Org/workspace/group slug that owns the repo.
            repo: Repository slug selected by repository discovery.
            query: Code search terms (error symbols, identifiers, paths).
        Returns:
            A dict with ``matches`` (path/snippet/line entries) and receipt
            ``status``, or an ``error`` payload when out of scope.
        """
        resolved, error = _resolve_target(
            provider, workspace_id, repo, tool_context, providers
        )
        if error is not None:
            return error
        target, ref = resolved
        matches, receipt = target.search_code(ref, query)
        return {
            "matches": [
                {
                    "path": match.path,
                    "line": match.line,
                    "snippet": match.snippet,
                }
                for match in matches
            ],
            "status": receipt.status,
        }

    return [
        FunctionTool(func=read_discovered_file),
        FunctionTool(func=search_discovered_code),
    ]