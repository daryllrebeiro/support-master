"""Phase 32 M1/M3: workspace provider contracts, fakes, and HTTP providers."""

import unittest
from datetime import datetime, timedelta, timezone

from supportmaster.integrations.workspace_providers import (
    FakeWorkspaceProvider,
    WorkspaceListCache,
    build_workspace_providers,
    resolve_secret,
)
from supportmaster.integrations.workspace_providers.github_provider import (
    HttpGitHubWorkspaceProvider,
)
from supportmaster.integrations.workspace_providers.gitlab_provider import (
    HttpGitLabWorkspaceProvider,
)
from supportmaster.models.discovery import (
    ActivityEvent,
    CodeMatch,
    RepoRef,
    RepositoryDescriptor,
)
from supportmaster.models.organization import (
    DiscoveryPolicy,
    OrganizationProfile,
    WorkspaceConnection,
)


def _descriptor(repo: str, *, description: str = "", topics=None) -> RepositoryDescriptor:
    return RepositoryDescriptor(
        ref=RepoRef(provider="github", workspace_id="acme", repo=repo),
        description=description,
        topics=list(topics or []),
        default_branch="main",
        languages={"Python": 1.0},
        last_commit_at=datetime.now(timezone.utc) - timedelta(days=1),
    )


class _StubTransport:
    """Records requests and returns scripted responses."""

    def __init__(self, responses: dict[str, tuple[int, dict]] | None = None) -> None:
        self.responses = responses or {}
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, path: str, payload=None):
        self.requests.append((method, path))
        for prefix, response in self.responses.items():
            if path.startswith(prefix):
                return response
        return 404, {"error": f"no stub for {path}"}


class FakeProviderTests(unittest.TestCase):
    def _provider(self, **kwargs) -> FakeWorkspaceProvider:
        defaults = dict(
            workspace_id="acme",
            repositories=[_descriptor("billing-api"), _descriptor("web"), _descriptor("docs")],
            files={
                ("billing-api", "src/export/invoice_csv.py"): "def export_invoices():\n    ...\n"
            },
            code_matches=[
                CodeMatch(
                    ref=RepoRef(provider="github", workspace_id="acme", repo="billing-api"),
                    path="src/export/invoice_csv.py",
                    snippet="def export_invoices():",
                )
            ],
        )
        defaults.update(kwargs)
        return FakeWorkspaceProvider(**defaults)

    def test_list_repositories_pages_deterministically(self) -> None:
        provider = self._provider()
        page_one, receipt_one = provider.list_repositories()
        page_two, receipt_two = provider.list_repositories(cursor=page_one.next_cursor)
        names = [item.ref.repo for item in page_one.repositories + page_two.repositories]
        self.assertEqual(names, ["billing-api", "docs", "web"])
        self.assertIsNone(page_two.next_cursor)
        self.assertEqual(receipt_one.status, "SUCCEEDED")
        self.assertEqual(receipt_two.status, "SUCCEEDED")

    def test_every_call_returns_a_receipt(self) -> None:
        provider = self._provider()
        ref = RepoRef(provider="github", workspace_id="acme", repo="billing-api")
        _, list_receipt = provider.list_repositories()
        _, meta_receipt = provider.get_repository(ref)
        _, search_receipt = provider.search_code(ref, "export")
        _, file_receipt = provider.read_file(ref, "src/export/invoice_csv.py")
        _, activity_receipt = provider.recent_activity(
            ref, datetime.now(timezone.utc) - timedelta(days=7)
        )
        for receipt in (list_receipt, meta_receipt, search_receipt, file_receipt, activity_receipt):
            self.assertEqual(receipt.status, "SUCCEEDED")
            self.assertTrue(receipt.operation_type.startswith("WORKSPACE_"))

    def test_scripted_failures_trip_through_failed_receipts(self) -> None:
        provider = self._provider(fail_next=2)
        _, first = provider.list_repositories()
        _, second = provider.get_repository(
            RepoRef(provider="github", workspace_id="acme", repo="billing-api")
        )
        self.assertEqual(first.status, "FAILED")
        self.assertEqual(second.status, "FAILED")
        _, third = provider.list_repositories()
        self.assertEqual(third.status, "SUCCEEDED")

    def test_workspace_search_degrades_without_raising(self) -> None:
        provider = self._provider(degrade_workspace_search=True)
        matches, receipt = provider.search_workspace_code("export")
        self.assertEqual(matches, [])
        self.assertEqual(receipt.status, "PARTIAL")
        self.assertEqual(receipt.details.get("degraded"), "fan_out_required")


class HttpGitHubProviderTests(unittest.TestCase):
    def test_listing_uses_org_endpoint_and_parses_repos(self) -> None:
        transport = _StubTransport(
            {
                "/orgs/acme/repos": (
                    200,
                    [
                        {
                            "name": "billing-api",
                            "description": "Billing services",
                            "topics": ["billing"],
                            "default_branch": "main",
                            "language": "Python",
                            "pushed_at": "2026-08-20T10:00:00Z",
                            "archived": False,
                            "size": 2048,
                        }
                    ],
                )
            }
        )
        provider = HttpGitHubWorkspaceProvider(workspace_id="acme", transport=transport)
        page, receipt = provider.list_repositories()
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(len(page.repositories), 1)
        descriptor = page.repositories[0]
        self.assertEqual(descriptor.ref.repo, "billing-api")
        self.assertIn(("GET", "/orgs/acme/repos"), transport.requests)

    def test_repo_scoped_search_qualifies_query(self) -> None:
        transport = _StubTransport(
            {"/search/code": (200, {"items": [{"path": "src/a.py", "repository": {"name": "r"}}]})}
        )
        provider = HttpGitHubWorkspaceProvider(workspace_id="acme", transport=transport)
        matches, receipt = provider.search_code(
            RepoRef(provider="github", workspace_id="acme", repo="r"), "OutOfMemory"
        )
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(matches[0].ref.repo, "r")
        method, path = transport.requests[0]
        self.assertEqual(path, "/search/code")

    def test_workspace_search_degrades_on_plan_gated_status(self) -> None:
        transport = _StubTransport({"/search/code": (403, {"error": "plan unsupported"})})
        provider = HttpGitHubWorkspaceProvider(workspace_id="acme", transport=transport)
        matches, receipt = provider.search_workspace_code("anything")
        self.assertEqual(matches, [])
        self.assertEqual(receipt.status, "PARTIAL")
        self.assertEqual(receipt.details.get("degraded"), "fan_out_required")


class HttpGitLabProviderTests(unittest.TestCase):
    def test_group_listing_and_project_search_paths(self) -> None:
        transport = _StubTransport(
            {
                "/api/v4/groups/acme/projects": (
                    200,
                    [{"path": "svc/api", "description": "API", "default_branch": "main"}],
                ),
                "/api/v4/projects/": (
                    200,
                    [{"path": "src/x.py", "data": "def broken():", "line": 3}],
                ),
            }
        )
        provider = HttpGitLabWorkspaceProvider(workspace_id="acme", transport=transport)
        page, _ = provider.list_repositories()
        self.assertEqual(page.repositories[0].ref.repo, "svc/api")
        matches, receipt = provider.search_code(
            RepoRef(provider="gitlab", workspace_id="acme", repo="svc/api"), "broken"
        )
        self.assertEqual(receipt.status, "SUCCEEDED")
        self.assertEqual(matches[0].path, "src/x.py")
        self.assertEqual(matches[0].line, 3)


class RegistryTests(unittest.TestCase):
    def test_env_secret_resolution(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SM_TEST_TOKEN": "tok-123"}):
            self.assertEqual(resolve_secret("env:SM_TEST_TOKEN"), "tok-123")

    def test_bare_token_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_secret("ghp_rawsecretvalue")

    def test_unresolvable_connections_are_skipped(self) -> None:
        profile = OrganizationProfile(
            organization_id="acme",
            display_name="Acme",
            discovery_policy=DiscoveryPolicy(enabled=True),
            workspace_connections=[
                WorkspaceConnection(
                    provider="github",
                    workspace_id="acme",
                    secret_ref="env:DEFINITELY_NOT_SET_VAR_XYZ",
                )
            ],
        )
        providers = build_workspace_providers(profile)
        self.assertEqual(providers, [])


class WorkspaceListCacheTests(unittest.TestCase):
    def test_ttl_cache_roundtrip_and_expiry(self) -> None:
        cache = WorkspaceListCache(ttl_seconds=0)
        page = RepoPageWithOneRepo()
        cache.put("github:acme", page)
        # ttl 0 means immediately expired.
        self.assertIsNone(cache.get("github:acme"))
        cache.put("github:acme", page)
        cache.clear()
        self.assertIsNone(cache.get("github:acme"))


def RepoPageWithOneRepo():
    from supportmaster.models.discovery import RepoPage

    return RepoPage(
        repositories=[
            RepositoryDescriptor(
                ref=RepoRef(provider="github", workspace_id="acme", repo="x")
            )
        ]
    )


if __name__ == "__main__":
    unittest.main()