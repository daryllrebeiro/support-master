"""Phase 32 M5: discovery-scoped read-only workspace tools."""

import unittest
from datetime import datetime, timedelta, timezone

from supportmaster.integrations.workspace_providers import FakeWorkspaceProvider
from supportmaster.models.discovery import (
    CodeMatch,
    RepoRef,
    RepositoryDescriptor,
)
from supportmaster.tools.workspace_tools import build_workspace_tools


class _FakeState:
    def __init__(self, data: dict) -> None:
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class _FakeToolContext:
    def __init__(self, data: dict) -> None:
        self.state = _FakeState(data)


def _provider() -> FakeWorkspaceProvider:
    return FakeWorkspaceProvider(
        provider_name="github",
        workspace_id="acme",
        repositories=[
            RepositoryDescriptor(
                ref=RepoRef(provider="github", workspace_id="acme", repo="billing-api"),
                languages={"Python": 1.0},
                last_commit_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
        ],
        files={("billing-api", "src/export/invoice_csv.py"): "def export():\n"},
        code_matches=[
            CodeMatch(
                ref=RepoRef(provider="github", workspace_id="acme", repo="billing-api"),
                path="src/export/invoice_csv.py",
                snippet="def export():",
            )
        ],
    )


def _state_with_selection() -> dict:
    return {
        "tenant_id": "tenant-a",
        "repository_discovery": {
            "selected": [
                {
                    "provider": "github",
                    "workspace_id": "acme",
                    "repo": "billing-api",
                }
            ]
        },
    }


class WorkspaceToolScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _provider()
        self.tools = build_workspace_tools({"github:acme": self.provider})
        self.read_tool = self.tools[0].func
        self.search_tool = self.tools[1].func
        self.ctx = _FakeToolContext(_state_with_selection())

    def test_read_inside_scope_returns_content(self) -> None:
        result = self.read_tool(
            provider="github",
            workspace_id="acme",
            repo="billing-api",
            path="src/export/invoice_csv.py",
            tool_context=self.ctx,
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertIn("def export", result["content"])

    def test_search_inside_scope_returns_matches(self) -> None:
        result = self.search_tool(
            provider="github",
            workspace_id="acme",
            repo="billing-api",
            query="export",
            tool_context=self.ctx,
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["matches"][0]["path"], "src/export/invoice_csv.py")

    def test_repo_outside_discovery_scope_is_rejected(self) -> None:
        result = self.read_tool(
            provider="github",
            workspace_id="acme",
            repo="secret-infra",
            path="secrets.env",
            tool_context=self.ctx,
        )
        self.assertEqual(result["error"], "REPO_NOT_IN_DISCOVERY_SCOPE")
        self.assertEqual(self.provider.calls_made, 0)

    def test_unknown_connection_is_reported_without_raising(self) -> None:
        state = {
            "repository_discovery": {
                "selected": [
                    {
                        "provider": "gitlab",
                        "workspace_id": "other",
                        "repo": "svc",
                    }
                ]
            }
        }
        result = self.read_tool(
            provider="gitlab",
            workspace_id="other",
            repo="svc",
            path="README.md",
            tool_context=_FakeToolContext(state),
        )
        self.assertEqual(result["error"], "CONNECTION_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()