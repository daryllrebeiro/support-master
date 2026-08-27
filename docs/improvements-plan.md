# SupportMaster — Codebase Deep-Dive: Improvements & Features Plan

Grounded in a full read of the workflow graph, agents, memory, execution, and
web layers. Ranked by impact on the two goals: **true autonomy** and **winning
Taskmaster**.

---

## What the deep-dive found

### Strengths to protect (do not refactor these before the deadline)
- The gated ADK graph (`publishing_gate_workflow.py`) is genuinely novel:
  duplicate → fan-out → join → RCA → authorization → self-healing execution →
  publish authorization → verified executor → audit.
- Execution adapters (`execution/adapters.py`) are real: subprocess Git,
  GitHub PR create/verify, test runner — all receipted.
- Durable queue, tenant auth, CSRF, rate limiting, tamper-evident audit chain.

### Three structural gaps (each is a winning opportunity)

**GAP 1 — Zero agent tools.** All 21 agents are prompt-only structured-output
reasoners (`Agent(...)` with `output_schema`, no `tools=`). The investigation
agent's own instructions admit it: *"Those tools are NOT available yet...
DO NOT perform searches."* For an autonomous-agent hackathon this is the
single biggest lever: tools turn text generators into agents that act.

**GAP 2 — Orphaned memory.** `memory/retriever.py::CaseContextRetriever`
(`get_context(query, tenant_id)`, `record_resolution(...)`) is complete and
tested but **never called by the workflow**. The "cross-run memory"
differentiator currently runs on nothing.

**GAP 3 — Blind self-healing retries.** `validation_testing_gate` routes
`RETRY_IMPLEMENTATION` back to `code_change` with failures appended to state,
but there is no explicit diagnose step; the retry can repeat the same mistake.

---

## P0 — Highest impact, deadline-safe (pick 2 of 3 if time-boxed)

### 1. Give agents real Google tools (~1 day, transformative)

**a) Web-grounded evidence search (evidence_agent, duplicate_work_agent):**
```python
from google.adk.tools import google_search
evidence_agent = Agent(..., tools=[google_search])
```
Update instructions: *"You MAY use web search to find public reports of this
error signature, known CVEs, vendor docs, and prior art. Cite URLs. Never
present a search result as internal confirmation."*
Judges see the agent actually searching — instant credibility.

**b) Memory as a FunctionTool (investigation_agent, root_cause_agent):**
```python
from google.adk.tools import FunctionTool
from ..memory.retriever import CaseContextRetriever

def search_past_resolutions(query: str) -> dict:
    """Search resolved cases from this tenant's history."""
    block = CaseContextRetriever().get_context(query, tenant_id=ctx_tenant)
    return {"context": block or "No similar past resolutions found."}
```
Wire via toolset closure carrying `tenant_id` from session state. This closes
GAP 2 *and* gives agents an agentic tool call in one move.

**c) Record resolutions at the end:** in `workflow_summary` stage (or its
node wrapper), call `record_resolution(...)` with case title, RCA, and
resolution summary. Memory becomes a closed loop: resolve → remember → reuse.

**d) Optional: BuiltInCodeExecutor on validation_agent** for sandboxed check
scripts. Only if time remains; the TestRunnerAdapter already covers the story.

### 2. Diagnose-before-retry self-healing (~2–4 hours)

Insert a lightweight deterministic node between the gate and `code_change` on
the RETRY edge (or enrich the code_change instruction):
```python
@node(name="failure_diagnosis")
def failure_diagnosis(ctx: Context) -> dict:
    failures = ctx.state.to_dict().get("validation_failures", [])
    ctx.state["healing_diagnosis"] = {
        "attempt": len(failures),
        "prior_failures": [f.get("warnings") for f in failures[-3:]],
        "directive": "Change approach; do not repeat the failed strategy.",
    }
    return ctx.state["healing_diagnosis"]
```
Route `"RETRY_IMPLEMENTATION": failure_diagnosis → code_change`. Add
`{healing_diagnosis}` to the code-change prompt. Escalating-strategy retries
look dramatically smarter on video.

### 3. Golden-path live demo fixture (~half day, huge judge impact)

A repo-local demo target where the full autonomy path actually fires:
- A tiny `demo-target/` Python package with an injected bug + failing test.
- A `LocalCodeChangeAdapter` + `SubprocessGitAdapter` + `TestRunnerAdapter`
  wired via env flag `SUPPORTMASTER_DEMO_EXEC=1`.
- One command runs intake → investigation → fix → tests pass → commit on a
  branch (no push without explicit grant).
This gives Scene 5 of the video a *real* end-to-end autonomous fix instead of
event-log narration.

---

## P1 — Strong adds if time allows

| # | Improvement | Effort | Why it wins |
|---|---|---|---|
| 4 | **Gemma triage model** (severity/duplicate pre-rank via GenAI SDK) | 2–4 h | Explicit bonus checklist item; cheap second model |
| 5 | **Cloud Trace/Logging exporter** behind `GOOGLE_CLOUD_PROJECT` | 1–2 h | Third GCP service; observability shot for video |
| 6 | **Per-agent scorecard strip in workspace** (tokens, latency, tool calls per stage from existing telemetry) | 3–4 h | Makes multi-agent cost/behavior visible to judges |
| 7 | **Duplicate-gate web grounding** (same google_search tool, different instruction: search public issue trackers for known duplicates) | included in #1a | Turns "duplicate detection" from heuristic to evidenced |
| 8 | **Escalation package polish**: auto-draft the human-action escalation email from verified state | 2–3 h | Shows judgment about what autonomy should *not* do |

## P2 — Post-deadline (already detailed in roadmap.md)
Vector memory (pgvector/AlloyDB), Vertex AI Agent Engine deploy, Firestore
state backend, Genkit sidecar service, Antigravity evaluation.

---

## Guardrails — what NOT to do this week
- Don't restructure the workflow graph or state schema (153 green tests are an asset).
- Don't add tools that mutate external systems without the existing grant flow — keep every new tool read-only or receipted.
- Don't swap SQLite for a cloud DB before submission; adapter seams make this a post-event win.
- Any new feature must ship with a fixture or unit test so the quality pack stays green.

## Suggested build order (Aug 25–28)
1. **Aug 25 PM:** #1b+#1c memory wiring (lowest risk, closes GAP 2) → run quality pack.
2. **Aug 26 AM:** #1a google_search grounding on evidence + duplicate agents.
3. **Aug 26 PM:** #2 failure-diagnosis node.
4. **Aug 27:** #3 golden-path demo fixture (also feeds the video).
5. **Aug 28:** #4 Gemma triage if green; otherwise freeze features and finish form/video.