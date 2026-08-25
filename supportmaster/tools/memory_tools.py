"""Tenant-scoped memory retrieval exposed to ADK agents as a read-only tool."""

from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

from ..memory.retriever import CaseContextRetriever


def build_memory_tool(store: CaseContextRetriever | None = None) -> FunctionTool:
    """Build the ``search_past_resolutions`` tool bound to one memory store.

    The tool is read-only and tenant-scoped: the tenant is taken from session
    state (``tenant_id``), never from model-supplied arguments, so an agent
    cannot cross tenant boundaries through the tool.
    """
    retriever = store or CaseContextRetriever()

    def search_past_resolutions(query: str, tool_context: ToolContext) -> dict:
        """Search this tenant's past resolved cases for similar fixes.

        Args:
            query: Natural-language description of the current problem,
                including error signatures, components, and keywords.
        Returns:
            A dict with ``found`` (bool) and ``context`` (formatted block of
            up to three similar past cases, empty string when none match).
        """
        tenant_id = str(tool_context.state.get("tenant_id", "default"))
        block = retriever.get_context(query, tenant_id=tenant_id, top_k=3)
        return {"found": bool(block), "context": block}

    return FunctionTool(func=search_past_resolutions)