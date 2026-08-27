# SupportMaster — Detailed Phased Implementation Plan

Companion to `docs/improvements-plan.md`. Every API reference below was
verified against the installed **google-adk 2.7.0**:

- `from google.adk.tools import google_search, FunctionTool, ToolContext`
- `ToolContext` is an alias of `google.adk.agents.context.Context` — the same
  object workflow nodes receive, so `tool_context.state["tenant_id"]` works.
- `FunctionTool` wraps plain callables; a parameter annotated
  `tool_context: ToolContext` is auto-injected by ADK.
- `_clone_agent(agent, model)` in `publishing_gate_workflow.py` accepts an
  update dict, so tools can be attached per-workflow-instance without editing
  the shared agent singletons.

Global rules for every phase:
1. One phase = one commit (easy revert).
2. After each phase run:
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests
   .\.venv\Scripts\python.exe -m supportmaster.quality
   ```
3. No phase may change existing output schemas or gate routing semantics.
4. All new tools are read-only; mutations stay behind existing grants.

---

## Phase A — Close the memory loop (GAP 2) · ~4 h · risk: LOW

**Goal:** agents retrieve past resolutions via a real tool call, and completed
runs are recorded back into memory.

### A1. Create `supportmaster/tools/__init__.py` and `supportmaster/tools/memory_tools.py`
```python
"""Read-only memory tools exposed to ADK agents."""
from __future__ import annotations

from google.adk.tools import FunctionTool, ToolContext

from ..memory.retriever import CaseContextRetriever


def build_memory_tool(store: CaseContextRetriever | None = None) -> FunctionTool:
    retriever = store or CaseContextRetriever()

    def search_past_resolutions(
        query: str, tool_context: ToolContext
    ) -> dict:
        """Search this tenant's past resolved cases for similar fixes.

        Args:
            query: Natural-language description of the current problem,
                including error signatures and components.
        Returns:
            {"context": formatted past-case block, "found": bool}
        """
        tenant_id = str(tool_context.state.get("tenant_id", "default"))
        block = retriever.get_context(query, tenant_id=tenant_id, top_k=3)
        return {"context": block, "found": bool(block)}

    return FunctionTool(func=search_past_resolutions)
```

### A2. Attach the tool in `publishing_gate_workflow.py`
Extend the clone helper and apply it to two agents only:
```python
def _clone_agent(agent: Agent, model_name: str, *, extra_tools: tuple = ()) -> Agent:
    update: dict = {"model": model_name}
    if extra_tools:
        update["tools"] = list(getattr(agent, "tools", []) or []) + list(extra_tools)
    cloned = agent.clone(update=update)
    cloned.parent_agent = None
    return cloned
```
In `create_publishing_gate_workflow`:
```python
memory_tool = build_memory_tool()
investigation = _clone_agent(investigation_agent, selected_model, extra_tools=(memory_tool,))
root_cause = _clone_agent(root_cause_agent, selected_model, extra_tools=(memory_tool,))
```

### A3. Instruction updates (append a section, change nothing else)
In `agents/investigation_agent.py` and `agents/root_cause_agent.py`, replace the
"search tools do NOT exist" framing with:
```
You have ONE available tool: search_past_resolutions(query).
Call it once with the strongest error signature + component keywords.
Treat returned cases as REFERENCE ONLY — verify applicability against
current evidence before raising confidence.
```

### A4. Record resolutions on completion
New node in `workflows/terminal_nodes.py`:
```python
@node(name="memory_record_node")
def memory_record_node(ctx: Context) -> dict:
    state = ctx.state.to_dict()
    if state.get("terminal_status") != "COMPLETED":
        return {}
    try:
        from ..memory.retriever import CaseContextRetriever
        case = state.get("support_case") or {}
        rca = state.get("root_cause_analysis") or {}
        CaseContextRetriever().record_resolution(
            case_id=str(case.get("case_id", "")),
            tenant_id=str(state.get("tenant_id", "default")),
            title=str(case.get("title", ""))[:500],
            description=str(case.get("description", ""))[:2000],
            root_cause=str(rca.get("summary") or rca)[:2000],
            resolution_summary=str(state.get("workflow_summary_text", ""))[:2000],
        )
        ctx.state["memory_recorded"] = True
    except Exception as error:          # fail-open: memory never blocks a run
        ctx.state["memory_recorded"] = False
        ctx.state["memory_record_error"] = f"{type(error).__name__}: {error}"
    return {"memory_recorded": ctx.state.get("memory_recorded", False)}
```
Wire into the graph: `(workflow_summary, memory_record_node, workflow_control)`
— insert between the existing summary and control nodes.

### A5. Tests — new file `tests/test_memory_loop.py`
- Tool returns `found=False` on empty store; returns block after one record.
- Tenant isolation: record under tenant-a, query as tenant-b → not found.
- Graph test: after a COMPLETED-path simulation, `memory_recorded is True`;
  SAFETY_STOP path leaves it unset.
- Fail-open test: monkeypatched raiser still completes the run.

**Exit criteria:** suite green; demo run twice shows second run retrieving the first.

---

## Phase B — Web-grounded evidence & duplicate search (GAP 1a) · ~4 h · risk: LOW-MED

**Goal:** evidence and duplicate agents perform real Google searches with citations.

### B1. Attach grounding in `create_publishing_gate_workflow`
```python
from google.adk.tools import google_search
evidence = _clone_agent(evidence_agent, selected_model, extra_tools=(google_search,))
duplicate = _clone_agent(duplicate_work_agent, selected_model, extra_tools=(google_search,))
```

### B2. Instruction updates
Append to both agents:
```
WEB SEARCH POLICY
You MAY use web search for PUBLIC information only: known-issue reports,
CVE/advisory databases, vendor documentation, changelogs, public trackers.
Every external fact MUST carry its source URL and be labeled EXTERNAL.
External findings can raise or lower hypotheses but can NEVER by themselves
confirm an internal root cause or clear a duplicate — internal evidence gates
remain authoritative.
```
In `duplicate_work_agent`, additionally: *"Search for the exact error signature
and stack-frame names; report any public issue that matches as
DUPLICATE_CANDIDATE with URL."*

### B3. Guardrail
No schema changes: search results flow into existing fields (`confirmed`,
`inferred`, `unknown`, duplicate warnings). The deterministic gates stay the
sole authority — grounding only enriches inputs.

### B4. Tests — extend `tests/test_duplicate_gate_workflow.py` + new assertions
- Cloned evidence/duplicate agents expose the google_search tool instance.
- Instructions contain "EXTERNAL" labeling rule.
- Offline behavior unchanged: quality pack still passes without network.

**Exit criteria:** green suite; manual live run shows cited URLs in evidence output.

---

## Phase C — Diagnose-before-retry self-healing (GAP 3) · ~3 h · risk: MEDIUM

**Goal:** each healing retry starts from an explicit failure diagnosis and an
escalating-strategy directive.

### C1. Optional state field
In `workflow_state.py` add (keep model permissive):
```python
healing_diagnosis: dict | None = None
```

### C2. New node in `publishing_gate_workflow.py`
```python
@node(name="failure_diagnosis")
def failure_diagnosis(ctx: Context) -> dict:
    failures = list(ctx.state.to_dict().get("validation_failures", []))
    attempt = len(failures)
    strategies = ["REPRODUCE_AND_ISOLATE", "NARROW_DIFF_SCOPE", "ALTERNATIVE_APPROACH"]
    diagnosis = {
        "attempt": attempt,
        "prior_failure_warnings": [f.get("warnings") for f in failures[-3:]],
        "directive": (
            "Do NOT repeat the previous strategy. "
            f"Escalate to: {strategies[min(attempt, len(strategies) - 1)]}."
        ),
    }
    ctx.state["healing_diagnosis"] = diagnosis
    return diagnosis
```

### C3. Rewire the retry edge
```python
{
    "READY_FOR_PUBLISH": publish,
    "SAFETY_STOP": autonomous_safety_stop,
    "RETRY_IMPLEMENTATION": failure_diagnosis,   # was: code_change
},
(failure_diagnosis, code_change),
```

### C4. Prompt injection
Add to `code_change_agent` instruction:
```
SELF-HEALING CONTEXT
{healing_diagnosis}
If present, obey directive: change approach; summarize what you will do
differently BEFORE producing the patch.
```

### C5. Tests — extend `tests/test_implementation_gate_workflow.py`
- First validation failure routes through `failure_diagnosis` (node present in graph).
- Second attempt's `healing_diagnosis.attempt == 1` and directive escalates.
- Third failure exhausts retries → rollback receipt unchanged (existing behavior preserved).

**Exit criteria:** green suite; graph topology test asserts new edge order.

---

## Phase D — Golden-path live demo fixture · ~5 h · risk: MEDIUM

**Goal:** one command demonstrates a REAL autonomous fix end-to-end.

### D1. Create `demo-target/` — tiny package with an injected bug
```
demo-target/
  pyproject.toml (or plain module)
  invoice_export.py      # stream_rows() intentionally materializes a list (the bug)
  test_invoice_export.py # fails today: asserts streaming generator behavior
```

### D2. Local adapters — `supportmaster/execution/local_demo.py`
- `LocalCodeChangeAdapter`: applies the LLM-proposed unified diff / targeted
  replacement strictly inside approved relative paths; refuses anything else.
- Reuse `SubprocessGitAdapter` (commit on branch `supportmaster/<case-id>` only).
- Reuse `TestRunnerAdapter` running `pytest demo-target -x`.

### D3. Wiring flag in `web.py` (and/or `demo.py`)
```python
if os.getenv("SUPPORTMASTER_DEMO_EXEC") == "1":
    from .execution.local_demo import build_local_demo_executor
    publication_executor = build_local_demo_executor(repository_path=Path("demo-target"))
result = asyncio.run(run_workflow(issue, model, publication_executor=publication_executor, ...))
```
(Plumb `publication_executor` through `run_workflow → create_root_agent`.)

### D4. Safety envelope
- Commits only; push requires an explicit PUBLISH grant (unchanged gate).
- Adapter hard-fails if diff touches paths outside `approved_paths`.
- All operations receipted via existing `ExternalOperationReceipt` flow.

### D5. Script `scripts/golden-path.ps1`
Reset DB → seed demo org → submit FIN-style ticket with
`SUPPORTMASTER_DEMO_EXEC=1` → print receipts + `git log demo-target`.
This becomes video Scene 5.

### D6. Tests — `tests/test_golden_path_demo.py`
- Tmp git repo: failing test → adapter applies scoped patch → runner passes →
  commit receipt SUCCESS; out-of-scope patch rejected with BLOCKED receipt.

**Exit criteria:** green suite; golden-path script produces a passing test + commit receipt locally.

---

## Phase E — Gemma triage bonus · ~3 h · risk: LOW

**Goal:** second Google model family integrated (explicit bonus item).

### E1. `supportmaster/models/triage.py`
```python
class GemmaTriageClient:
    """Best-effort severity/duplicate-prone classification via Gemma."""
    def __init__(self, client=None, model: str | None = None): ...
    def classify(self, case_text: str) -> TriageResult: ...  # severity, dup_risk, rationale
```
Model id from `SUPPORTMASTER_TRIAGE_MODEL` (default a Gemma id); uses the same
`genai.Client(api_key=...)` pattern as the co-pilot chat.

### E2. Hook at intake (`web.py` post-submit path), fail-open
Wrap in try/except; on any failure skip annotation. Store result on the case
metadata — never gates anything.

### E3. Surface it
Workspace badge "Gemma triage: HIGH severity risk"; Devpost copy updated.

### E4. Tests — mocked client returns fixed JSON; assert annotation stored; failure path skips silently.

**Exit criteria:** green suite; bonus checklist item claimable.

---

## Phase F — Cloud Trace exporter · ~2 h · risk: LOW

### F1. Optional dependency
`requirements-gcp.txt`: `opentelemetry-exporter-gcp-trace`. Document install;
do NOT add to base requirements (keeps offline installs light).

### F2. Exporter init in telemetry setup
When `GOOGLE_CLOUD_PROJECT` is set, register the GCP trace exporter alongside
existing sinks; otherwise no-op (current behavior).

### F3. Docs + video overlay note ("spans visible in Cloud Trace").

### F4. Test: exporter factory returns None without env var; smoke-test with fake project id using mocks.

**Exit criteria:** green suite; deployed service shows traces in Cloud Trace console.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Agent `.clone(update={"tools": ...})` semantics differ from expectation | Phase A step 1 includes a 10-minute spike test before building on it |
| google_search needs runtime key; offline tests must not call it | Tools attach at clone time; tests assert presence only, never invoke |
| State schema rejects new field | Add optional field explicitly (C1) rather than relying on extra="allow" |
| Golden-path adapter mutates wrong files | Approved-path enforcement + tmp-repo tests + branch-only commits |
| Feature creep past Aug 28 | Hard freeze: Phases A–C mandatory, D strongly recommended, E/F optional |

## Definition of done (per phase)
Green full suite · green quality pack · phase-specific new tests · one commit ·
README/docs touched only where user-visible · Devpost description line added
if judge-visible.