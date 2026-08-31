"""Dependency-light investigation services with injectable source adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re
from typing import Any, Protocol

from ..models.evidence_record import EvidenceRecord
from ..integrations.contracts import IncidentRecord
from ..models.support_case import SupportCase
from ..models.investigation_artifacts import (
    EvidenceLink,
    IncidentCorrelation,
    InvestigationSummary,
    MissingEvidence,
    RelatedCaseMatch,
    RepositorySignal,
)


class RepositorySearch(Protocol):
    def search(self, query: str) -> Sequence[Mapping[str, Any]]: ...


class TokenRepositorySearch:
    """Safe deterministic repository search over injected file metadata."""

    def __init__(self, files: Iterable[Mapping[str, Any]] = ()) -> None:
        self.files = [dict(item) for item in files]

    def search(self, query: str) -> list[Mapping[str, Any]]:
        terms = _tokens(query)
        if not terms:
            return []
        scored: list[tuple[int, Mapping[str, Any]]] = []
        for item in self.files:
            haystack = " ".join(str(value) for value in item.values()).casefold()
            score = sum(term in haystack for term in terms)
            if score:
                scored.append((score, item))
        return [item for _, item in sorted(scored, key=lambda pair: (-pair[0], str(pair[1].get("path", ""))))]


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]{3,}", value.casefold()) if token not in {"the", "and", "for", "with", "from"}}


class InvestigationService:
    def __init__(self, store: Any) -> None:
        self.store = store

    def related_cases(self, case: SupportCase, *, limit: int = 10) -> list[RelatedCaseMatch]:
        current = _tokens(f"{case.title} {case.description}")
        matches: list[RelatedCaseMatch] = []
        for candidate in self.store.list_cases(case.tenant_id):
            if candidate.case_id == case.case_id:
                continue
            if case.external_id and candidate.external_id == case.external_id and case.source_system == candidate.source_system:
                matches.append(RelatedCaseMatch(case_id=candidate.case_id, external_id=candidate.external_id, relation="DUPLICATE", similarity=1.0, rationale="Same tenant, source system, and external case identifier."))
                continue
            other = _tokens(f"{candidate.title} {candidate.description}")
            similarity = len(current & other) / max(1, len(current | other))
            if similarity >= 0.15:
                relation = "RELATED" if similarity >= 0.35 else "POSSIBLE"
                matches.append(RelatedCaseMatch(case_id=candidate.case_id, external_id=candidate.external_id, relation=relation, similarity=round(similarity, 4), rationale="Shared normalized case terms."))
        return sorted(matches, key=lambda item: (-item.similarity, item.case_id))[:limit]

    def correlate_incidents(self, case: SupportCase, incidents: Iterable[IncidentRecord], *, window_hours: int = 24) -> list[IncidentCorrelation]:
        results: list[IncidentCorrelation] = []
        for incident in incidents:
            service_match = bool(case.service and incident.service.casefold() == case.service.casefold())
            product_match = bool(case.product and case.product.casefold() in incident.summary.casefold())
            if not service_match and not product_match:
                continue
            score = 0.9 if service_match and product_match else 0.7
            results.append(IncidentCorrelation(incident_id=incident.incident_id, service=incident.service, correlation="DIRECT" if score >= 0.9 else "POSSIBLE", score=score, rationale="Incident service or product matches the support case."))
        return sorted(results, key=lambda item: (-item.score, item.incident_id))

    def repository_signals(self, case: SupportCase, search: RepositorySearch | None = None) -> list[RepositorySignal]:
        if search is None:
            return []
        query = " ".join(sorted(_tokens(f"{case.title} {case.description}")))
        signals: list[RepositorySignal] = []
        for item in search.search(query)[:20]:
            signals.append(RepositorySignal(repository=str(item.get("repository", "unknown")), path=item.get("path"), symbol=item.get("symbol"), commit_sha=item.get("commit_sha"), summary=str(item.get("summary", item.get("content", "Repository match"))), confidence="HIGH" if item.get("commit_sha") else "MEDIUM"))
        return signals

    def missing_evidence(self, case: SupportCase, records: Iterable[EvidenceRecord]) -> list[MissingEvidence]:
        types = {record.source_type.casefold() for record in records}
        has_log_record = any("log" in source or "trace" in source for source in types)
        has_log_in_text = bool(re.search(r"(?:stack\s*trace|error\s*log|exception|java\.lang|traceback|at\s+[\w\.\$]+\([\w\.\$]+:\d+\)|exit code \d+|oomkilled|\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2})", case.description, re.IGNORECASE))
        missing: list[MissingEvidence] = []
        if not (has_log_record or has_log_in_text):
            missing.append(MissingEvidence(evidence_type="APPLICATION_LOGS", importance="IMPORTANT", reason="No runtime logs or traces are attached.", expected_information="Observed errors, timestamps, and execution context."))
        if not case.reproduction_steps:
            missing.append(MissingEvidence(evidence_type="REPRODUCTION_DATA", importance="IMPORTANT", reason="The case has no reproducible steps.", expected_information="A repeatable trigger and expected versus actual behavior."))
        if not case.environment:
            missing.append(MissingEvidence(evidence_type="ENVIRONMENT_DETAILS", importance="IMPORTANT", reason="The affected environment is unknown.", expected_information="Deployment, region, version, and runtime context."))
        if not case.service and not case.product:
            missing.append(MissingEvidence(evidence_type="AFFECTED_COMPONENT", importance="CRITICAL", reason="No product or service is identified.", expected_information="The system boundary to investigate."))
        return missing

    def summarize(self, case: SupportCase, *, records: Iterable[EvidenceRecord] = (), incidents: Iterable[IncidentRecord] = (), repository_search: RepositorySearch | None = None) -> InvestigationSummary:
        records_list = list(records)
        missing = self.missing_evidence(case, records_list)
        evidence_links = [EvidenceLink(record_id=record.record_id, relevance="Available for investigation.", confidence=record.confidence) for record in records_list]
        related = self.related_cases(case)
        correlations = self.correlate_incidents(case, incidents)
        repository = self.repository_signals(case, repository_search)
        blocked = any(item.importance == "CRITICAL" for item in missing)
        return InvestigationSummary(
            case_id=case.case_id,
            tenant_id=case.tenant_id,
            evidence_links=evidence_links,
            related_cases=related,
            incident_correlations=correlations,
            repository_signals=repository,
            missing_evidence=missing,
            investigation_status="BLOCKED" if blocked else ("READY" if not missing else "PARTIAL"),
            readiness_reason="Critical evidence is missing." if blocked else ("Required evidence is available." if not missing else "Investigation can continue while evidence gaps are tracked."),
        )
