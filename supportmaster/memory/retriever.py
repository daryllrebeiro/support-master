"""Context retriever: builds past-case context blocks for injection into agents."""

from __future__ import annotations

from .case_store import CaseMemoryStore, SimilarCase


class CaseContextRetriever:
    """
    Retrieves relevant past resolved cases from the memory store and formats
    them as a structured context block ready for injection into agent prompts.
    """

    HEADER = (
        "## Relevant Past Cases (Retrieved from Memory)\n"
        "The following similar cases have been resolved in the past. "
        "Use them as reference — do NOT assume they are identical.\n\n"
    )
    FOOTER = "\n---\n"

    def __init__(self, store: CaseMemoryStore | None = None) -> None:
        self._store = store or CaseMemoryStore()

    def get_context(self, query: str, tenant_id: str, top_k: int = 3) -> str:
        """
        Return a formatted context string for injection into an investigation prompt,
        or an empty string if no relevant cases are found.
        """
        cases = self._store.retrieve_similar(query, tenant_id, top_k=top_k)
        if not cases:
            return ""
        blocks = "\n".join(c.to_context_block() for c in cases)
        return self.HEADER + blocks + self.FOOTER

    def record_resolution(
        self,
        *,
        case_id: str,
        tenant_id: str,
        title: str,
        description: str,
        root_cause: str,
        resolution_summary: str,
        tags: list[str] | None = None,
        resolved_repos: list[str] | None = None,
    ) -> None:
        """Persist a newly resolved case to the memory store.

        ``resolved_repos`` (``provider:workspace/repo`` keys) feeds future
        repository discovery's HISTORICAL_CASE signal.
        """
        self._store.record(
            case_id=case_id,
            tenant_id=tenant_id,
            title=title,
            description=description,
            root_cause=root_cause,
            resolution_summary=resolution_summary,
            tags=tags,
            resolved_repos=resolved_repos,
        )
