"""Phase 32 M2: repository discovery pipeline and flag-gated graph wiring."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from supportmaster.investigation.discovery import (
    DiscoveryService,
    extract_search_terms,
)
from supportmaster.integrations.workspace_providers import FakeWorkspaceProvider
from supportmaster.memory.case_store import SimilarCase
from supportmaster.models.discovery import (
    CodeMatch,
    DisambiguationDecision,
    RepoRef,
    RepositoryDescriptor,
)
from supportmaster.models.organization import (
    DiscoveryPolicy,
    OrganizationProfile,
    WorkspaceConnection,
)
from supportmaster.models.support_case import SupportCase
from supportmaster.workflows.publishing_gate_workflow import (
    create_publishing_gate_workflow,
)


def _profile(**policy_overrides) -> OrganizationProfile:
    policy = DiscoveryPolicy(enabled=True, **policy_overrides)
    return OrganizationProfile(
        organization_id="acme",
        display_name="Acme",
        discovery_policy=policy,
        repository_mappings={
            "Invoice Export": "github:acme/billing-api",
        },
    )


def _case(**overrides) -> SupportCase:
    payload = dict(
        case_id="CASE-D1",
        tenant_id="tenant-a",
        title="Invoice export fails with OutOfMemoryError",
        description="Large CSV invoice exports crash the export worker with OutOfMemoryError.",
        product="Invoice Export",
        service="Billing",
        metadata={"technology": "Python"},
    )
    payload.update(overrides)
    return SupportCase.model_validate(payload)


def _provider(repo_names=("billing-api", "web", "docs"), **kwargs) -> FakeWorkspaceProvider:
    now = datetime.now(timezone.utc)
    repositories = [
        RepositoryDescriptor(
            ref=RepoRef(provider="github", workspace_id="acme", repo=name),
            description=f"{name} service",
            topics=[name],
            languages={"Python": 1.0},
            last_commit_at=now - timedelta(days=1),
        )
        for name in repo_names
    ]
    defaults = dict(
        provider_name="github",
        workspace_id="acme",
        repositories=repositories,
        code_matches=[
            CodeMatch(
                ref=RepoRef(provider="github", workspace_id="acme", repo="billing-api"),
                path="src/export/invoice_csv.py",
                snippet="invoice export streaming",
            )
        ],
    )
    defaults.update(kwargs)
    return FakeWorkspaceProvider(**defaults)


class _FakeRetriever:
    def __init__(self, cases: list[SimilarCase]) -> None:
        self.cases = cases
        self.queries: list[str] = []

    def retrieve_similar(self, query: str, tenant_id: str, top_k: int = 3):
        self.queries.append(query)
        return self.cases


class ExtractSearchTermsTests(unittest.TestCase):
    def test_prefers_specific_tokens_over_stopwords(self) -> None:
        terms = extract_search_terms(
            "InvoiceExportJob failed: java.lang.OutOfMemoryError in the export worker"
        )
        self.assertTrue(terms)
        self.assertNotIn("the", terms)
        self.assertTrue(any("OutOfMemory" in term or "InvoiceExportJob" in term for term in terms))

    def test_handles_none_inputs(self) -> None:
        self.assertEqual(extract_search_terms(None, "", None), [])


class StaticMappingSignalTests(unittest.TestCase):
    def test_product_match_resolves_static_candidate(self) -> None:
        service = DiscoveryService(providers=[], profile=_profile())
        result, receipts = service.discover(case=_case())
        keys = [ref.key() for ref in result.selected]
        self.assertIn("github:acme/billing-api", keys)
        top = next(item for item in result.candidates if item.ref.key() == "github:acme/billing-api")
        self.assertIn("STATIC_MAPPING", top.sources)
        self.assertEqual(top.confidence, "HIGH")
        self.assertTrue(any(trace.startswith("static_mapping:") for trace in result.method_trace))
        # No providers configured → no workspace calls.
        self.assertEqual(result.workspace_calls_made, 0)


class MemorySignalTests(unittest.TestCase):
    def test_prior_case_repos_become_candidates(self) -> None:
        retriever = _FakeRetriever(
            [
                SimilarCase(
                    case_id="CASE-OLD",
                    title="Invoice export OOM",
                    root_cause="memory",
                    resolution_summary="streamed",
                    similarity_rank=-5.0,
                    resolved_repos=["github:acme/billing-api"],
                )
            ]
        )
        service = DiscoveryService(providers=[], profile=_profile(), retriever=retriever)
        result, _ = service.discover(case=_case(), tenant_id="tenant-a")
        top = next(item for item in result.candidates if item.ref.key() == "github:acme/billing-api")
        self.assertIn("HISTORICAL_CASE", top.sources)


class WorkspaceMetadataTests(unittest.TestCase):
    def test_metadata_scoring_and_caps(self) -> None:
        many = tuple(f"repo-{index:02d}" for index in range(12))
        provider = _provider(repo_names=many)
        profile = _profile(max_listed_repos=5)
        service = DiscoveryService(providers=[provider], profile=profile)
        result, receipts = service.discover(case=_case(product=None, service=None))
        listed = [trace for trace in result.method_trace if trace.startswith("workspace_metadata:")]
        self.assertEqual(listed, ["workspace_metadata:5 repos"])
        self.assertLessEqual(result.workspace_calls_made, profile.discovery_policy.max_workspace_calls)

    def test_recency_and_keyword_overlap_rank_active_repo(self) -> None:
        provider = _provider()
        # Give the billing repo ticket-relevant topics so keyword overlap,
        # not just shared recency/language bonuses, separates the ranking.
        provider.repositories["billing-api"].topics = ["invoice", "export"]
        service = DiscoveryService(providers=[provider], profile=_profile())
        result, _ = service.discover(case=_case(product=None, service=None))
        billing = next(
            item for item in result.candidates if item.ref.repo == "billing-api"
        )
        docs = next(item for item in result.candidates if item.ref.repo == "docs")
        self.assertGreater(billing.score, docs.score)


class CodeSearchSignalTests(unittest.TestCase):
    def test_code_search_only_resolution_without_static_hit(self) -> None:
        provider = _provider()
        service = DiscoveryService(providers=[provider], profile=_profile())
        result, receipts = service.discover(
            case=_case(product=None, service=None),
            ticket_text="invoice_csv export crashes with OutOfMemoryError",
        )
        selected_keys = {ref.key() for ref in result.selected}
        self.assertIn("github:acme/billing-api", selected_keys)
        candidate = next(
            item for item in result.candidates if item.ref.key() == "github:acme/billing-api"
        )
        self.assertIn("CODE_SEARCH", candidate.sources)
        self.assertEqual(candidate.matched_paths, ["src/export/invoice_csv.py"])

    def test_code_search_can_be_disabled(self) -> None:
        provider = _provider()
        service = DiscoveryService(
            providers=[provider], profile=_profile(code_search_enabled=False)
        )
        result, _ = service.discover(
            case=_case(product=None, service=None),
            ticket_text="invoice_csv export crashes",
        )
        candidate = next(
            (item for item in result.candidates if item.ref.key() == "github:acme/billing-api"),
            None,
        )
        sources = candidate.sources if candidate else []
        self.assertNotIn("CODE_SEARCH", sources)


class DegradedFallbackTests(unittest.TestCase):
    def test_provider_failure_fails_closed_to_static_mapping(self) -> None:
        broken = _provider(fail_next=1)
        service = DiscoveryService(providers=[broken], profile=_profile())
        result, receipts = service.discover(case=_case())
        self.assertTrue(result.degraded)
        keys = [ref.key() for ref in result.selected]
        self.assertIn("github:acme/billing-api", keys)  # static mapping survives
        self.assertTrue(any(r.status == "FAILED" for r in receipts))


class MultiProviderMergeTests(unittest.TestCase):
    def test_candidates_merge_across_connections(self) -> None:
        github = _provider(repo_names=("billing-api",), code_matches=[])
        bitbucket = FakeWorkspaceProvider(
            provider_name="bitbucket",
            workspace_id="acme-legacy",
            repositories=[
                RepositoryDescriptor(
                    ref=RepoRef(provider="bitbucket", workspace_id="acme-legacy", repo="monolith"),
                    description="legacy monolith",
                    languages={"Java": 1.0},
                    last_commit_at=datetime.now(timezone.utc) - timedelta(days=2),
                )
            ],
            code_matches=[
                CodeMatch(
                    ref=RepoRef(provider="bitbucket", workspace_id="acme-legacy", repo="monolith"),
                    path="export/InvoiceCsv.java",
                    snippet="OutOfMemoryError while streaming invoice export",
                )
            ],
        )
        profile = _profile()
        profile.workspace_connections = [
            WorkspaceConnection(provider="github", workspace_id="acme", secret_ref="env:X"),
            WorkspaceConnection(provider="bitbucket", workspace_id="acme-legacy", secret_ref="env:Y"),
        ]
        service = DiscoveryService(providers=[github, bitbucket], profile=profile)
        result, _ = service.discover(
            case=_case(product=None, service=None),
            ticket_text="invoice export OutOfMemoryError",
        )
        providers_used = {item.ref.provider for item in result.candidates}
        self.assertIn("bitbucket", providers_used)
        self.assertIn("github", result.connections_used[0])
        monolith = next(item for item in result.candidates if item.ref.repo == "monolith")
        self.assertIn("CODE_SEARCH", monolith.sources)


class DisambiguationBoundedTests(unittest.TestCase):
    def test_disambiguator_only_invoked_above_threshold_and_cannot_add_refs(self) -> None:
        calls: list[list[str]] = []

        def hostile_disambiguator(query_text, candidates):
            calls.append([item.ref.key() for item in candidates])
            # Tries to smuggle an unknown repo into the selection.
            decision = DisambiguationDecision(
                ordered_refs=[
                    RepoRef(provider="github", workspace_id="acme", repo="not-discovered"),
                    *[item.ref for item in reversed(candidates)],
                ],
                rationale="hostile output",
            )
            return decision

        repos = tuple(f"svc-{index}" for index in range(6))
        provider = _provider(repo_names=repos, code_matches=[])
        service = DiscoveryService(
            providers=[provider],
            profile=_profile(),
            disambiguator=hostile_disambiguator,
        )
        result, _ = service.discover(case=_case(product=None, service=None))
        self.assertEqual(len(calls), 1, "disambiguation must run only once, above threshold")
        selected_keys = {ref.key() for ref in result.selected}
        self.assertNotIn("github:acme/not-discovered", selected_keys)
        self.assertTrue(all(key.startswith("github:acme/svc-") for key in selected_keys))
        self.assertIn("llm_disambiguation:used", result.method_trace)

    def test_no_disambiguation_below_threshold(self) -> None:
        provider = _provider(repo_names=("billing-api", "web"), code_matches=[])
        service = DiscoveryService(
            providers=[provider], profile=_profile(), disambiguator=lambda q, c: (_ for _ in ()).throw(AssertionError("must not run"))
        )
        result, _ = service.discover(case=_case(product=None, service=None))
        self.assertIn("llm_disambiguation:not_needed", result.method_trace)


class GraphWiringTests(unittest.TestCase):
    def test_flag_off_builds_legacy_graph(self) -> None:
        with patch.dict(os.environ, {"SUPPORTMASTER_DISCOVERY_ENABLED": ""}):
            workflow = create_publishing_gate_workflow("gemini-3.6-flash")
        names = {node.name for node in workflow.graph.nodes}
        self.assertNotIn("repository_discovery_node", names)

    def test_flag_on_inserts_discovery_node_before_repository_agent(self) -> None:
        env = {
            "SUPPORTMASTER_DISCOVERY_ENABLED": "true",
            "SUPPORTMASTER_MEMORY_DB": os.path.join(os.environ.get("TEMP", "/tmp"), "sm-disc-test.db"),
        }
        with patch.dict(os.environ, env):
            workflow = create_publishing_gate_workflow("gemini-3.6-flash")
        names = {node.name for node in workflow.graph.nodes}
        self.assertIn("repository_discovery_node", names)
        graph = workflow.graph
        self.assertEqual(
            graph.get_next_pending_nodes("repository_discovery_node", None),
            ["repository_agent"],
        )


if __name__ == "__main__":
    unittest.main()