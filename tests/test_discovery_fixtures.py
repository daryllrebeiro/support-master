"""Phase 32 M5: fixtures/discovery scenarios run through DiscoveryService."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from supportmaster.investigation.discovery import DiscoveryService
from supportmaster.integrations.workspace_providers import FakeWorkspaceProvider
from supportmaster.models.discovery import CodeMatch, RepoRef, RepositoryDescriptor
from supportmaster.models.organization import OrganizationProfile
from supportmaster.models.support_case import SupportCase

FIXTURES = Path(__file__).parents[1] / "fixtures" / "discovery"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _build_provider(spec: dict) -> FakeWorkspaceProvider:
    now = datetime.now(timezone.utc)
    repositories = [
        RepositoryDescriptor(
            ref=RepoRef(
                provider=spec["provider"], workspace_id=spec["workspace_id"], repo=repo
            ),
            description=f"{repo} service",
            topics=[repo],
            languages={"Python": 1.0, "Java": 1.0},
            last_commit_at=now - timedelta(days=1),
        )
        for repo in spec.get("repositories", [])
    ]
    code_matches = [
        CodeMatch(
            ref=RepoRef(
                provider=spec["provider"],
                workspace_id=spec["workspace_id"],
                repo=match["repo"],
            ),
            path=match["path"],
            snippet=match["snippet"],
        )
        for match in spec.get("code_matches", [])
    ]
    return FakeWorkspaceProvider(
        provider_name=spec["provider"],
        workspace_id=spec["workspace_id"],
        repositories=repositories,
        code_matches=code_matches,
    )


class DiscoveryFixtureTests(unittest.TestCase):
    def test_code_search_only_fixture_resolves_without_static_mapping(self) -> None:
        fixture = _load("code-search-only.json")
        profile = OrganizationProfile.model_validate(fixture["organization_profile"])
        service = DiscoveryService(
            providers=[_build_provider(fixture["fake_workspace"])],
            profile=profile,
        )
        result, receipts = service.discover(
            case=SupportCase.model_validate(fixture["case"]),
            ticket_text=fixture["ticket_text"],
            tenant_id="tenant-a",
        )
        keys = [ref.key() for ref in result.selected]
        self.assertIn(fixture["expected"]["selected_contains"], keys)
        candidate = next(
            item
            for item in result.candidates
            if item.ref.key() == fixture["expected"]["selected_contains"]
        )
        self.assertIn(fixture["expected"]["sources_include"], candidate.sources)
        self.assertTrue(receipts)

    def test_multi_provider_merge_fixture_merges_candidates(self) -> None:
        fixture = _load("multi-provider-merge.json")
        profile = OrganizationProfile.model_validate(fixture["organization_profile"])
        providers = [_build_provider(spec) for spec in fixture["fake_workspaces"]]
        service = DiscoveryService(providers=providers, profile=profile)
        result, _ = service.discover(
            case=SupportCase.model_validate(fixture["case"]),
            ticket_text=fixture["ticket_text"],
            tenant_id="tenant-a",
        )
        candidate_providers = {item.ref.provider for item in result.candidates}
        for expected_provider in fixture["expected"]["providers_in_candidates"]:
            self.assertIn(expected_provider, candidate_providers)
        keys = [ref.key() for ref in result.selected]
        self.assertIn(fixture["expected"]["selected_contains"], keys)


if __name__ == "__main__":
    unittest.main()