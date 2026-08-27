"""Deterministic repository discovery over tenant workspaces.

Ranked-candidate pipeline, cheapest/most-deterministic signals first; model
reasoning only at the end to order an already-bounded candidate set. Every
external call goes through a gateway-guarded ``WorkspaceProvider`` and is
receipted. On provider failure discovery fails closed to static-mapping +
memory candidates — it never blocks or fails the run.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

from ..models.control import ExternalOperationReceipt
from ..models.discovery import (
    DiscoveredRepository,
    DisambiguationDecision,
    DiscoveryConfidence,
    DiscoveryResult,
    DiscoverySource,
    RepoPage,
    RepoRef,
    RepositoryDescriptor,
)
from ..models.organization import OrganizationProfile
from ..models.support_case import SupportCase

# Re-exported so workflow nodes can build one service-level cache.
from ..integrations.workspace_providers import WorkspaceListCache  # noqa: F401


Disambiguator = Callable[[str, list[DiscoveredRepository]], DisambiguationDecision]

_STOPWORDS = frozenset(
    """a an and are as at be been but by can cannot could did do does for from
    get got had has have how i if in into is it its me my not of on or our out
    should so than that the their them then there these they this to too us was
    we were what when which who why will with would you your""".split()
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]{2,}")


def extract_search_terms(*texts: str | None, limit: int = 12) -> list[str]:
    """Extract distinctive search terms from ticket/case text.

    Deterministic lexical extraction reused by discovery's code-search phase;
    longer, rarer tokens (error symbols, endpoint names, config keys) rank
    ahead of generic words.
    """
    counts: dict[str, int] = {}
    for text in texts:
        if not text:
            continue
        for token in _TOKEN_PATTERN.findall(text):
            lowered = token.lower().strip(".-_")
            if lowered in _STOPWORDS or len(lowered) < 4:
                continue
            counts[token] = counts.get(token, 0) + 1
    # Prefer longer tokens (more specific), then frequency.
    ranked = sorted(counts.items(), key=lambda item: (-len(item[0]), -item[1]))
    return [token for token, _ in ranked[:limit]]


def _casefold_set(values: Iterable[str]) -> set[str]:
    return {value.casefold() for value in values if value}


class DiscoveryService:
    """Runs the five-signal discovery pipeline for one case."""

    ACTIVITY_WINDOW_DAYS = 30

    def __init__(
        self,
        *,
        providers: Iterable[object] = (),
        profile: OrganizationProfile | None = None,
        retriever: object | None = None,
        cache: WorkspaceListCache | None = None,
        disambiguator: Disambiguator | None = None,
    ) -> None:
        self.providers = list(providers)
        self.profile = profile
        self.retriever = retriever
        self.cache = cache or WorkspaceListCache(
            ttl_seconds=(profile.discovery_policy.cache_ttl_seconds if profile else 900)
        )
        self.disambiguator = disambiguator

    # -- public entry point --------------------------------------------------

    def discover(
        self,
        *,
        case: SupportCase | None = None,
        ticket_text: str = "",
        tenant_id: str = "default",
    ) -> tuple[DiscoveryResult, list[ExternalOperationReceipt]]:
        policy = self.profile.discovery_policy if self.profile else None
        max_listed = policy.max_listed_repos if policy else 50
        max_candidates = policy.max_candidates_per_run if policy else 8
        max_selected = policy.max_disambiguation_repos if policy else 3
        code_search_enabled = policy.code_search_enabled if policy else True
        max_calls = policy.max_workspace_calls if policy else 24

        result = DiscoveryResult()
        receipts: list[ExternalOperationReceipt] = []
        budget = {"calls": max_calls}
        candidates: dict[str, DiscoveredRepository] = {}

        query_terms = extract_search_terms(
            ticket_text,
            case.title if case else None,
            case.description if case else None,
            case.actual_behavior if case else None,
        )
        context_values = [*(query_terms[:6]), *( [case.service, case.product] if case else [] )]
        query_text = " ".join(str(value) for value in context_values if value)

        # Signal 1 — static mapping (deterministic, highest confidence).
        static_hits = self._static_mapping_hits(case)
        for key, evidence in static_hits.items():
            ref = self._ref_from_key(key)
            if ref is None:
                continue
            self._add_candidate(
                candidates,
                ref=ref,
                name=ref.repo,
                source="STATIC_MAPPING",
                score=10.0,
                confidence="HIGH",
                evidence=[evidence],
            )
        result.method_trace.append(f"static_mapping:{len(static_hits)} hits")

        # Signal 2 — cross-run memory (deterministic, high confidence).
        memory_hits = self._memory_hits(query_text or (case.title if case else ""), tenant_id)
        for key, evidence in memory_hits.items():
            ref = self._ref_from_key(key)
            if ref is None:
                continue
            self._add_candidate(
                candidates,
                ref=ref,
                name=ref.repo,
                source="HISTORICAL_CASE",
                score=8.0,
                confidence="HIGH",
                evidence=[evidence],
            )
        result.method_trace.append(f"cross_run_memory:{len(memory_hits)} hits")

        # Signals 3–4 need live connections.
        descriptors_by_connection: dict[str, list[RepositoryDescriptor]] = {}
        usable_providers = list(self.providers)
        result.connections_used = [getattr(provider, "connection_id", "?") for provider in usable_providers]

        for provider in usable_providers:
            page, receipt = self._list_repositories(provider, budget, max_listed)
            receipts.append(receipt)
            if receipt.status != "SUCCEEDED":
                result.degraded = True
                continue
            descriptors_by_connection[provider.connection_id] = page.repositories[:max_listed]

        if not descriptors_by_connection and usable_providers:
            result.method_trace.append("workspace_metadata:unavailable")
        else:
            listed_count = sum(len(items) for items in descriptors_by_connection.values())
            result.method_trace.append(f"workspace_metadata:{listed_count} repos")

        # Signal 3 — structural metadata scoring. Service/product names join
        # the ticket-derived terms so named services rank their repos.
        metadata_terms = list(query_terms)
        if case is not None:
            metadata_terms.extend(
                str(value) for value in (case.service, case.product) if value
            )
        for provider in usable_providers:
            descriptors = descriptors_by_connection.get(provider.connection_id, [])
            scored = self._score_metadata(descriptors, metadata_terms, case)
            for descriptor, score, evidence in scored:
                self._add_candidate(
                    candidates,
                    ref=descriptor.ref,
                    name=descriptor.ref.repo,
                    source="WORKSPACE_METADATA",
                    score=score,
                    confidence="MEDIUM" if score > 0 else "LOW",
                    evidence=evidence,
                )

        # Signal 4 — targeted code search over the current short-list. The
        # first few extracted terms are tried in order; a term that produces
        # hits ends that provider's search (bounded by the call budget).
        if code_search_enabled and query_terms and budget["calls"] > 0:
            searched = 0
            hits_total = 0
            fanned_out = False
            for provider in usable_providers:
                if budget["calls"] <= 0:
                    break
                provider_hits = 0
                for term in query_terms[:3]:
                    if budget["calls"] <= 0 or provider_hits:
                        break
                    matches, receipt = provider.search_workspace_code(term)
                    receipts.append(receipt)
                    if receipt.status == "PARTIAL" and receipt.details.get("degraded") == "fan_out_required":
                        fanned_out = True
                        for descriptor in descriptors_by_connection.get(provider.connection_id, [])[:max_candidates]:
                            if budget["calls"] <= 0:
                                break
                            repo_matches, repo_receipt = provider.search_code(descriptor.ref, term)
                            receipts.append(repo_receipt)
                            searched += 1
                            self._record_code_hits(candidates, repo_matches, query_terms)
                            provider_hits += len(repo_matches)
                        continue
                    searched += 1
                    self._record_code_hits(candidates, matches, query_terms)
                    provider_hits += len(matches)
                hits_total += provider_hits
            trace = f"code_search:{hits_total} hits"
            if fanned_out:
                trace += " (fanned_out)"
            result.method_trace.append(trace)

        # Rank, optionally disambiguate, select.
        ranked = sorted(candidates.values(), key=lambda item: (-item.score, item.ref.key()))
        comparable = [item for item in ranked if item.score > 0]
        if (
            self.disambiguator is not None
            and len(comparable) > max_selected
            and budget["calls"] >= 0
        ):
            decision = self.disambiguator(query_text, comparable)
            known = {item.ref.key(): item for item in comparable}
            ordered = [known[item.key()] for item in decision.ordered_refs if item.key() in known]
            dropped = {item.key() for item in decision.dropped_refs}
            ranked = ordered + [item for item in comparable if item.ref.key() not in {i.ref.key() for i in ordered} and item.ref.key() not in dropped]
            low_confidence = [item for item in ranked if item.score <= 0]
            ranked = ranked + low_confidence
            result.method_trace.append("llm_disambiguation:used")
        else:
            result.method_trace.append("llm_disambiguation:not_needed")

        result.candidates = ranked
        result.selected = [item.ref for item in ranked[:max_selected] if item.score > 0]
        if not result.selected and ranked:
            result.selected = [ranked[0].ref]
        result.workspace_calls_made = sum(
            1 for receipt in receipts if receipt.operation_type.startswith("WORKSPACE_")
        )
        return result, receipts

    # -- signal implementations -------------------------------------------------

    def _static_mapping_hits(self, case: SupportCase | None) -> dict[str, str]:
        mappings = (self.profile.repository_mappings if self.profile else {}) or {}
        if not mappings or case is None:
            return {}
        lookup = {key.casefold(): value for key, value in mappings.items()}
        hits: dict[str, str] = {}
        for label, value in (
            ("product", case.product),
            ("service", case.service),
            ("component", str((case.metadata or {}).get("component") or "")),
        ):
            if value and value.casefold() in lookup:
                repo_key = lookup[value.casefold()]
                hits.setdefault(repo_key, f"{label}={value!r} matched repository_mappings")
        return hits

    def _memory_hits(self, query: str, tenant_id: str) -> dict[str, str]:
        if not query.strip() or self.retriever is None:
            return {}
        try:
            similar = self.retriever.retrieve_similar(query, tenant_id, top_k=3)
        except Exception:
            return []
        hits: dict[str, str] = {}
        for item in similar:
            repos = getattr(item, "resolved_repos", None) or []
            for key in repos:
                hits.setdefault(str(key), f"prior case {item.case_id} resolved here")
        return hits

    def _list_repositories(self, provider, budget: dict[str, int], max_listed: int):
        """Page through ``list_repositories`` under the call/list budgets."""
        cached = self.cache.get(provider.connection_id)
        if cached is not None:
            return cached, _cached_receipt(provider.connection_id)
        descriptors: list[RepositoryDescriptor] = []
        cursor: str | None = None
        last_receipt: ExternalOperationReceipt | None = None
        while len(descriptors) < max_listed and budget["calls"] > 0:
            budget["calls"] -= 1
            page, receipt = provider.list_repositories(cursor=cursor)
            last_receipt = receipt
            if receipt.status != "SUCCEEDED":
                return RepoPage(repositories=descriptors), receipt
            descriptors.extend(page.repositories)
            cursor = page.next_cursor
            if not cursor:
                break
        if last_receipt is None:
            return RepoPage(), _skipped_receipt()
        combined_page = RepoPage(repositories=descriptors[:max_listed])
        if last_receipt.status == "SUCCEEDED":
            self.cache.put(provider.connection_id, combined_page)
        return combined_page, last_receipt

    def _score_metadata(
        self,
        descriptors: list[RepositoryDescriptor],
        query_terms: list[str],
        case: SupportCase | None,
    ) -> list[tuple[RepositoryDescriptor, float, list[str]]]:
        terms = _casefold_set(query_terms)
        results: list[tuple[RepositoryDescriptor, float, list[str]]] = []
        for descriptor in descriptors:
            haystack_parts = [
                descriptor.ref.repo,
                descriptor.description,
                " ".join(descriptor.topics),
            ]
            haystack = " ".join(haystack_parts).casefold()
            overlap = sum(1 for term in terms if term in haystack)
            language_bonus = 0.0
            if case is not None:
                runtime = (case.metadata or {}).get("technology") or case.application_version or ""
                if runtime and any(lang.casefold() in str(runtime).casefold() for lang in descriptor.languages):
                    language_bonus = 1.5
            recency_bonus = 0.0
            if descriptor.last_commit_at is not None:
                age_days = (
                    datetime.now(timezone.utc) - descriptor.last_commit_at
                ).total_seconds() / 86_400
                if age_days <= self.ACTIVITY_WINDOW_DAYS:
                    recency_bonus = 1.0
            archived_penalty = -2.0 if descriptor.archived else 0.0
            score = overlap * 1.0 + language_bonus + recency_bonus + archived_penalty
            evidence: list[str] = []
            if overlap:
                evidence.append(f"keyword overlap on {overlap} term(s)")
            if language_bonus:
                evidence.append("language matches case runtime")
            if recency_bonus:
                evidence.append("active within 30 days")
            if descriptor.archived:
                evidence.append("archived")
            results.append((descriptor, round(score, 2), evidence))
        return results

    def _record_code_hits(
        self,
        candidates: dict[str, DiscoveredRepository],
        matches: list,
        query_terms: list[str],
    ) -> None:
        primary = query_terms[0].casefold() if query_terms else ""
        by_repo: dict[str, list[str]] = {}
        for match in matches:
            by_repo.setdefault(match.ref.key(), []).append(match.path)
        for key, paths in by_repo.items():
            ref = next(
                (m.ref for m in matches if m.ref.key() == key), None
            )
            if ref is None:
                continue
            specific = sum(1 for path in paths if primary and primary in path.casefold())
            score = 3.0 * len(paths) + 2.0 * specific
            self._add_candidate(
                candidates,
                ref=ref,
                name=ref.repo,
                source="CODE_SEARCH",
                score=score,
                confidence="HIGH" if specific else "MEDIUM",
                evidence=[f"code-search hit(s): {', '.join(paths[:3])}"],
                matched_paths=paths[:10],
            )

    # -- helpers -------------------------------------------------------------------

    @staticmethod
    def _ref_from_key(key: str) -> RepoRef | None:
        parts = key.split(":", 1)
        if len(parts) != 2 or "/" not in parts[1]:
            return None
        workspace, repo = parts[1].split("/", 1)
        if not workspace or not repo:
            return None
        try:
            return RepoRef(provider=parts[0], workspace_id=workspace, repo=repo)  # type: ignore[arg-type]
        except Exception:
            return None

    @staticmethod
    def _add_candidate(
        candidates: dict[str, DiscoveredRepository],
        *,
        ref: RepoRef,
        name: str,
        source: DiscoverySource,
        score: float,
        confidence: DiscoveryConfidence,
        evidence: list[str],
        matched_paths: list[str] | None = None,
    ) -> None:
        existing = candidates.get(ref.key())
        if existing is None:
            candidates[ref.key()] = DiscoveredRepository(
                ref=ref,
                name=name,
                sources=[source],
                confidence=confidence,
                score=score,
                evidence=list(evidence),
                matched_paths=list(matched_paths or []),
            )
            return
        if source not in existing.sources:
            existing.sources.append(source)
        existing.score += score
        existing.evidence.extend(evidence)
        for path in matched_paths or []:
            if path not in existing.matched_paths:
                existing.matched_paths.append(path)
        rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if rank[confidence] > rank[existing.confidence]:
            existing.confidence = confidence


def _cached_receipt(connection_id: str) -> ExternalOperationReceipt:
    return ExternalOperationReceipt(
        operation_type="WORKSPACE_LIST_REPOS",
        requested_action="read_workspace",
        status="SUCCEEDED",
        details={"cache": "hit", "connection": connection_id},
    )


def _skipped_receipt() -> ExternalOperationReceipt:
    return ExternalOperationReceipt(
        operation_type="WORKSPACE_LIST_REPOS",
        requested_action="read_workspace",
        status="BLOCKED",
        details={"reason": "call_budget_exhausted"},
        error="Workspace call budget exhausted; discovery stopped early.",
    )