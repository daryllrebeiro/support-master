# SupportMaster — Repository Workspace Discovery (Phase 32)

**Goal:** given a `SupportCase` + tenant org profile, connect to the tenant's
GitHub / Bitbucket / GitLab *workspace* (org / project / group), automatically
resolve **which repositories are relevant** to the ticket, and hand that scoped
repo set into the existing investigation → root-cause → remediation →
gated-execution pipeline.

Everything here is **read-only**. No mutation capability is added; the
Implementation/Publication gates keep sole authority over any change to code.

---

## 0. Current-state audit (verified against the code)

These facts drive every decision below:

| Fact | Where | Consequence |
| --- | --- | --- |
| Adapters are `Protocol` classes returning `(result, ExternalOperationReceipt)` and every call flows through `IntegrationGateway.execute(...)` (permission allow-list, `DRY_RUN`, payload cap, circuit breaker, telemetry) | `supportmaster/integrations/adapters.py`, `integrations/policy.py` | Workspace providers must be Protocols wrapped by the same gateway; **no new permission model** |
| `IntegrationPermission` already contains `READ_REPOSITORY`, and `DRY_RUN` permits all four READ permissions | `integrations/contracts.py`, `integrations/policy.py` | Reuse `READ_REPOSITORY` for every workspace read; zero policy-code change |
| A live HTTPS JSON transport with bounded response sizes already exists | `integrations/http.py` (`UrllibJsonTransport`) | Providers take an injected `JsonHttpTransport`; no new HTTP stack |
| The investigation fan-out is `(evidence, repository) → investigation_evidence_join → investigation_evidence_fan_in` | `workflows/publishing_gate_workflow.py` (edges, lines ~341–364) | Discovery slots into this fan-out as a third branch feeding the Repository Agent |
| `OrganizationProfile.repository_mappings: dict[str, str]` exists but has **no consumer anywhere** | `models/organization.py` (only definition site in the package) | Discovery becomes the first real consumer of the Phase-13 static mapping |
| `case_memory` (FTS5) stores `title, description, root_cause, resolution_summary, tags` — **no repo column** | `memory/case_store.py` | Needs a guarded `ALTER TABLE` migration to record `resolved_repos` |
| `RepositoryCandidate(repository, source, confidence, evidence)` already exists as the *LLM-facing* schema | `models/repository.py` | Discovery needs a **distinct** model (`DiscoveredRepository`) to avoid colliding with the agent-output schema |
| State contract is `SupportMasterState(extra="allow")` with an `OUTPUT_KEY_TO_STATE_FIELD` map; orchestration nodes write state directly | `workflow_state.py` | Discovery adds one typed field + one map entry; the deterministic node writes it like the gates do |
| Org API is `POST /api/organizations` under `ORG_ADMIN`, and it force-overwrites `payload["organization_id"] = auth.principal.tenant_id` | `web.py` (~line 1558) | Connections ride inside the tenant's own profile → tenant boundary enforced by construction; do **not** add a path-parameterized tenant endpoint |
| Live HTTP adapters wrap fakes 1:1 ("same policy and receipt contracts as fakes") | `integrations/http_adapters.py` | Each VCS provider ships as fake-first, then an HTTP-backed twin |
| Phases are tracked lettered (A–F) in `docs/implementation-phases.md`; this feature is tracked separately as "Phase 32" | docs | This plan lives in its own doc and updates README/state-contract at the end |

### Deliberate deviations from the original design sketch

1. **Package name.** `supportmaster/integrations/workspace/` was proposed, but
   `supportmaster/workspace/` already exists as the *operator read-model*
   package (`CaseWorkspaceSnapshot`). The module paths wouldn't collide, but
   humans will confuse them. We use **`supportmaster/integrations/workspace_providers/`**.
2. **ABC → `Protocol`.** Every existing adapter family uses structural
   `Protocol` typing so fakes need no inheritance. Providers follow suit.
3. **Dataclasses → pydantic models.** All cross-stage contracts here are
   pydantic (`model_dump(mode="json")` feeds state/run snapshots). Shared DTOs
   stay pydantic for consistency and free validation/redaction hooks.

---

## 1. Architecture overview

```
                         ┌──────────────────────────────────────────────┐
SupportCase + org ──────▶│ Repository Discovery (deterministic service) │
profile                  │  1 static mapping      (HIGH conf)           │
                         │  2 cross-run memory    (HIGH conf)           │
                         │  3 workspace metadata  (MED conf)            │
                         │  4 targeted code search(deterministic call,  │
                         │     ticket-derived query)                    │
                         │  5 LLM disambiguation  (bounded, non-auth)   │
                         └───────────────┬──────────────────────────────┘
                                         │ DiscoveryResult (state)
                     ┌───────────────────┼─────────────────────┐
                     ▼                   ▼                     ▼
              Evidence Agent   Disambiguation agent    Repository Agent
              (unchanged)      (only if > N ties)      (scoped to result;
                     │                   │                 optional workspace
                     └───────────────────┴──────────── tools in M5)
                                         ▼
                          Deterministic Join Gate → RootCause → …
                          (gates unchanged; M5 adds grant↔repo identity check)
```

New code (all under existing packages):

```
supportmaster/integrations/workspace_providers/
    __init__.py            # public exports
    base.py                # WorkspaceProvider Protocol + shared DTOs + FakeWorkspaceProvider
    github_provider.py     # HttpGitHubWorkspaceProvider
    bitbucket_provider.py  # HttpBitbucketWorkspaceProvider
    gitlab_provider.py     # HttpGitLabWorkspaceProvider
    registry.py            # tenant profile → provider instances (secret resolve, cache, breakers)

supportmaster/investigation/discovery.py       # DiscoveryService (the ranked pipeline)
supportmaster/models/discovery.py              # RepoRef, DiscoveredRepository, DiscoveryResult …
supportmaster/workflows/discovery_nodes.py     # repository_discovery_node + routing helper
supportmaster/tools/workspace_tools.py         # tenant-scoped read-only ADK tools (M5)
```

Modified files:

```
supportmaster/models/organization.py          # WorkspaceConnection, DiscoveryPolicy fields
supportmaster/workflow_state.py               # discovery_result field + OUTPUT_KEY map entry
supportmaster/workflows/publishing_gate_workflow.py  # flag-gated edge rewiring
supportmaster/memory/case_store.py            # resolved_repos column (guarded migration)
supportmaster/memory/retriever.py             # record/retrieve resolved_repos
supportmaster/workflows/terminal_nodes.py     # persist resolved repos at memory_record_node
supportmaster/control_gates.py                # grant ↔ discovered-repo identity check (M5)
supportmaster/web.py                          # secret_ref redaction on org save/read
supportmaster/quality.py                      # DISCOVERY quality category (M5)
supportmaster/release.py                      # secret-ref/scope readiness checks (M5)
docs/state-contract.md, README.md, docs/gcp-deployment.md
```

---

## 2. Workstream 1 — `WorkspaceProvider` contract (`base.py`)

Shared DTOs (pydantic, provider-neutral so downstream never branches on
`provider_name`):

```python
ProviderName = Literal["github", "bitbucket", "gitlab"]

class RepoRef(BaseModel):
    provider: ProviderName
    workspace_id: str          # org / workspace / group slug
    repo: str                  # repo slug within the workspace

class RepositoryDescriptor(BaseModel):
    ref: RepoRef
    description: str = ""
    topics: list[str] = Field(default_factory=list)
    default_branch: str = ""
    languages: dict[str, float] = Field(default_factory=dict)   # name -> share
    last_commit_at: datetime | None = None
    archived: bool = False
    size_kb: int = 0

class RepoPage(BaseModel):
    repositories: list[RepositoryDescriptor]
    next_cursor: str | None = None

class CodeMatch(BaseModel):
    ref: RepoRef
    path: str
    line: int | None = None
    snippet: str = ""          # redacted before entering state (see §8)

class FileBlob(BaseModel):
    ref: RepoRef
    path: str
    ref_name: str | None = None
    content: str = ""          # size-capped by the transport

class ActivityEvent(BaseModel):
    ref: RepoRef
    kind: Literal["COMMIT", "PULL_REQUEST"]
    occurred_at: datetime
    summary: str = ""

class WorkspaceConnectionError(RuntimeError): ...
```

Provider protocol (structural, mirroring `IssueTrackerAdapter`):

```python
class WorkspaceProvider(Protocol):
    provider_name: ClassVar[ProviderName]
    connection_id: str        # "{provider}:{workspace_id}" — receipt target suffix

    def list_repositories(self, *, cursor: str | None = None) -> tuple[RepoPage, ExternalOperationReceipt]: ...
    def get_repository(self, repo_ref: RepoRef) -> tuple[RepositoryDescriptor, ExternalOperationReceipt]: ...
    def search_code(self, repo_ref: RepoRef, query: str) -> tuple[list[CodeMatch], ExternalOperationReceipt]: ...
    def search_workspace_code(self, query: str) -> tuple[list[CodeMatch], ExternalOperationReceipt]: ...  # degrades (§3)
    def read_file(self, repo_ref: RepoRef, path: str, ref: str | None = None) -> tuple[FileBlob, ExternalOperationReceipt]: ...
    def recent_activity(self, repo_ref: RepoRef, since: datetime) -> tuple[list[ActivityEvent], ExternalOperationReceipt]: ...
```

Every method returns a receipt because each implementation funnels through
`IntegrationGateway.execute(permission="READ_REPOSITORY", target=f"{connection_id}/{repo}", ...)`.
That single choice gives us, for free: DRY_RUN safety, payload caps, circuit
breaking, metrics (`supportmaster.integrations.operations`), and redacted
telemetry events — exactly the "safe reads permitted by default, receipted"
posture of the existing `ReadOnlyIntegrationBundle`.

`FakeWorkspaceProvider` (in `base.py`, same file as the contract, like the
in-memory adapters) is constructed from plain Python dicts of repos/files and
supports scripted failures (`fail_next=N calls`) for breaker tests. It powers
all unit tests and the offline golden path.

---

## 3. Workstream 2 — Provider implementations

Each `Http*WorkspaceProvider` wraps `JsonHttpTransport` + gateway, following
the `http_adapters.py` twin-of-fake pattern. Endpoint mapping:

| Capability | GitHub | Bitbucket | GitLab |
| --- | --- | --- | --- |
| List repos | `GET /orgs/{org}/repos?page=&per_page=` | `GET /2.0/repositories/{workspace}?page=&pagelen=` | `GET /api/v4/groups/{id}/projects?pagination=keyset` |
| Repo metadata | `GET /repos/{o}/{r}` | `GET /2.0/repositories/{w}/{r}` | `GET /api/v4/projects/{id}` |
| Workspace-wide code search | `GET /search/code?q=…+org:{org}` | `GET /2.0/search/code?q=…` (workspace-scoped) | Advanced Search API (**Premium+**) |
| Per-repo code search | same `q=…+repo:{o}/{r}` | `GET /2.0/repositories/{w}/{r}/search/code` | `GET /api/v4/projects/{id}/search?q=` |
| Recent activity | commits + PRs list endpoints | commits + PRs | commits + MRs |

Auth headers per provider (`Authorization: Bearer` / `token`, Basic for
Bitbucket app passwords) are set by the transport factory in `registry.py`;
tokens are **never** attached to DTOs, receipts, logs, or state.

**Graceful degradation rule (normative):**
`search_workspace_code()` first attempts the native workspace-wide endpoint.
On HTTP 401/403/404/`PLAN_UNSUPPORTED`-style responses it returns
`(matches=[], receipt(status="PARTIAL", details={"degraded":"fan_out_required"}))`
and the **DiscoveryService** (not the provider) decides whether to fan out
bounded per-repo `search_code` calls over the current candidate short-list
(§4 step 4). Providers never raise past the gateway; the gateway converts
exceptions to `FAILED` receipts and trips the breaker.

Tier detection is cached per connection so a GitLab Free tenant pays the
failed-probe cost once per TTL window, not per ticket.

---

## 4. Workstream 3 — `DiscoveryService` (`investigation/discovery.py`)

A pure-Python service (no ADK dependency) so it is unit-testable without a
graph. Constructor takes: org profile, providers (from registry), memory
retriever, model-caller for disambiguation, and settings.

Ranked pipeline — cheapest/most-deterministic first:

1. **Static mapping hit** — resolve `case.product/service/component` keys
   against `organization_profile.repository_mappings`. Hit ⇒ candidate
   `source="STATIC_MAPPING"`, confidence `HIGH`. *(First consumer ever of this
   field.)*
2. **Cross-run memory hit** — `CaseContextRetriever.get_context`-style FTS5
   query on symptom/product/component text; collect `resolved_repos` from hits
   ⇒ `source="HISTORICAL_CASE"`, confidence `HIGH` (requires the §7 migration).
3. **Workspace metadata match** — paged `list_repositories()` (TTL-cached per
   connection, cap `list_pages_cap` pages / `max_listed_repos` repos), scored
   by: keyword overlap (name/topics/description vs case keywords), language
   match vs declared tech stack, `recent_activity()` recency boost.
   ⇒ `source="WORKSPACE_METADATA"`, confidence `MEDIUM`.
4. **Targeted code search** — top-N candidates (N =
   `discovery_policy.max_candidates_per_run`, default 8) searched with terms
   extracted from the ticket (error strings, stack symbols, endpoint names,
   config keys — reuse the Evidence Agent's extraction approach; extraction
   helper lives in `investigation/discovery.py` so both callers share it).
   Hits ⇒ `source="CODE_SEARCH"`; confidence scaled by match specificity
   (exact symbol > generic keyword). Honors `code_search_enabled=false`.
5. **LLM disambiguation (bounded, non-authoritative)** — only when > 
   `max_disambiguation_repos` (default 3) candidates remain at comparable
   confidence. Implemented as a tiny ADK agent (`discovery_disambiguation_agent`,
   output_schema `DisambiguationDecision`) that can **only order/filter** the
   already-discovered candidate list — its schema has no field capable of
   introducing a repo, and its output never reaches an authorization gate.
6. **Emit** `DiscoveryResult` (§5) and append every receipt to
   `operation_receipts` via `append_operation_receipts`.

**Bounds & failure behavior**

- Hard caps enforced in-loop: listed repos ≤ 50 (configurable), searched repos
  ≤ `max_candidates_per_run`, total workspace calls per run ≤
  `max_workspace_calls` (default 24) — exceeding the call budget stops
  discovery at whatever ranking exists and records
  `method_trace += ["budget_exhausted"]`.
- One `CircuitBreaker` per connection (from `operations/circuit_breaker.py`),
  passed into that connection's gateway. Any breaker opening ⇒
  `degraded=True`, remaining signals skipped, discovery **fails closed to
  static-mapping + memory candidates only**, and a warning lands in
  `uncertainty_flags`. Discovery never blocks the run silently and never
  fails the run.
- Metadata cache: process-local `{connection_id: (expires_at, descriptors)}`
  honoring `cache_ttl_seconds` (default 900 s).

**Multi-provider tenants:** the service fans steps 3–4 across **all** of the
tenant's connections and merges into one ranked list; every
`DiscoveredRepository` carries `ref.provider` + `ref.workspace_id` so the
Repository Agent (and, later, publish) knows which credential/adapter owns
that repo.

---

## 5. Workstream 4 — Models & state contract

New `supportmaster/models/discovery.py`:

```python
class DiscoveredRepository(BaseModel):
    ref: RepoRef
    name: str
    sources: list[Literal["STATIC_MAPPING","HISTORICAL_CASE","WORKSPACE_METADATA","CODE_SEARCH"]]
    confidence: Literal["LOW","MEDIUM","HIGH"]
    score: float = 0.0
    evidence: list[str] = Field(default_factory=list)     # provenance strings
    matched_paths: list[str] = Field(default_factory=list) # code-search hits (paths only)

class DisambiguationDecision(BaseModel):
    ordered_refs: list[RepoRef]          # subset of input, never new entries
    dropped_refs: list[RepoRef] = Field(default_factory=list)
    rationale: str = ""

class DiscoveryResult(BaseModel):
    connections_used: list[str] = Field(default_factory=list)  # ["github:acme-corp"]
    candidates: list[DiscoveredRepository] = Field(default_factory=list)
    selected: list[RepoRef] = Field(default_factory=list)      # what Repository Agent used
    method_trace: list[str] = Field(default_factory=list)      # e.g. "static_mapping:miss"
    workspace_calls_made: int = 0
    degraded: bool = False
    policy_version: str = "v1"
    created_at: datetime = ...
```

(`DiscoveredRepository` is intentionally separate from the LLM-facing
`RepositoryCandidate` in `models/repository.py`.)

`workflow_state.py`: add
`repository_discovery: Optional[DiscoveryResult] = None` and map entry
`"repository_discovery": "repository_discovery"` (the hardening suite enforces
output-key == field-name, hence the rename from the sketch's
`discovery_result`). The deterministic node writes it directly (like gates
write `last_gate_decision`), so no agent `output_key` change is required for
M2; the map entry documents the key for run-snapshot consumers.

`docs/state-contract.md`: add a "Repository discovery" paragraph describing
the field, the receipt discipline (`integration_results["workspace_discovery"]`
via `record_integration_result`), and the fail-closed-to-static-mapping rule.

---

## 6. Workstream 5 — Workflow wiring (flag-gated)

In `create_publishing_gate_workflow(...)`, when
`os.getenv("SUPPORTMASTER_DISCOVERY_ENABLED", "").lower() in {"1","true","yes"}`
(read through a small `config.py` helper `discovery_enabled()`):

```
START → ticket → investigation → duplicate → duplicate_work_gate
    ├─ CONTINUE: (evidence, repository_discovery_node)      # fan-out of 3→2 branches
    │     repository_discovery_node ─┬─ "NEEDS_DISAMBIGUATION" → discovery_disambiguation_agent → repository
    │                                └─ "CONTINUE"            → repository
    │     (evidence, repository) → investigation_evidence_join → … unchanged
    └─ SAFETY_STOP: autonomous_safety_stop                    # unchanged
```

- `repository_discovery_node` (`workflows/discovery_nodes.py`) is a
  deterministic `@node`: builds/loads providers for `ctx.state.tenant_id`,
  runs `DiscoveryService.discover(case, profile, ticket_analysis,
  investigation_plan)`, writes `ctx.state["discovery_result"]`, appends
  receipts, sets `ctx.route`.
- When the flag is **off**, the graph is built exactly as today (legacy edges),
  guaranteeing zero behavior change for existing tenants. The flag is read at
  workflow-construction time, consistent with how `publication_executor` is
  injected.
- `max_concurrency` stays ≥ 2; the fan-out grows by one read-only branch.

Repository Agent instruction gains a **DISCOVERY CONTEXT** section (M2):
consume `state["discovery_result"]`; treat `selected` refs as the permitted
investigation scope; `search_performed=true` only when
`method_trace` records actual code-search hits; all existing anti-fabrication
rules unchanged. In M5 the agent additionally receives read-only workspace
tools (`tools/workspace_tools.py`, built like `build_memory_tool()`) limited
to `read_file`/`search_code` on `selected` refs — the tool wrapper rejects any
ref outside the discovered set.

---

## 7. Workstream 6 — Memory signal (`resolved_repos`)

- `memory/case_store.py`: add `resolved_repos TEXT` (JSON array of
  `"provider:workspace/repo"` strings) to `case_memory`. Migration is guarded:
  inspect `PRAGMA table_info(case_memory)` and `ALTER TABLE ADD COLUMN` when
  missing (existing DBs upgrade in place; fresh DBs get it in `CREATE TABLE`).
- `record_resolution(..., resolved_repos: list[str] | None = None)` persists
  them; `retrieve_similar` returns them on `SimilarCase`.
- `terminal_nodes.memory_record_node` populates `resolved_repos` from the
  resolution bundle / publish result so future cases feed discovery step 2.
- Tenant isolation is inherited: every query is already `tenant_id`-scoped.

---

## 8. Workstream 7 — Org configuration & API

`models/organization.py` additions:

```python
class WorkspaceConnection(BaseModel):
    provider: Literal["github", "bitbucket", "gitlab"]
    workspace_id: str = Field(min_length=1, max_length=200)
    secret_ref: str = Field(min_length=1, max_length=500)   # e.g. "secretmanager://acme/github-ro"
    scope: Literal["READ_ONLY"] = "READ_ONLY"               # fixed this phase
    created_at: datetime = ...

class DiscoveryPolicy(BaseModel):
    enabled: bool = False                                    # per-tenant kill switch
    max_candidates_per_run: int = Field(default=8, ge=1, le=32)
    max_disambiguation_repos: int = Field(default=3, ge=1, le=10)
    max_listed_repos: int = Field(default=50, ge=1, le=500)
    max_workspace_calls: int = Field(default=24, ge=1, le=200)
    code_search_enabled: bool = True
    cache_ttl_seconds: int = Field(default=900, ge=0, le=86400)

# on OrganizationProfile:
workspace_connections: list[WorkspaceConnection] = Field(default_factory=list)
discovery_policy: DiscoveryPolicy = Field(default_factory=DiscoveryPolicy)
```

API discipline (`web.py`):

- Keep the single `POST /api/organizations` surface (ORG_ADMIN). Do **not**
  add `/api/organizations/{tenant}/...` — the handler already forces
  `organization_id = auth.principal.tenant_id`, which satisfies the tenant-
  boundary rule by construction; a path parameter would only introduce a
  mismatch risk to re-check.
- `secret_ref` is **write-only**: the success response (and any future GET)
  returns each connection with `secret_ref` replaced by `"***REDACTED***"`.
  Clients update a connection by re-submitting the full ref.
- Effective enablement = global env flag AND `discovery_policy.enabled`.

Secret resolution (`registry.py`): a `SecretResolver` callable injected at the
composition root supports `env:NAME` and `secretmanager://project/secret`
schemes (Cloud Run wiring documented in `docs/gcp-deployment.md`). Resolution
failures produce a `BLOCKED` receipt and disable that connection for the run —
never a traceback, never a logged token. `telemetry/redaction.py` gains
patterns for the three providers' token header shapes.

Least-privilege enforcement: at connection-save time, if the provider exposes
scope introspection reachable with the token (GitHub fine-grained PAT
permissions endpoint), verify read-only and reject write/admin-scoped tokens
with a 400; otherwise record a `TOKEN_SCOPE_UNVERIFIED` warning event. Over-
scoped tokens are a deployment misconfiguration to warn on, never silently
accepted.

---

## 9. Workstream 8 — Gate integration (M5, deterministic)

Extend `evaluate_implementation_authorization_gate` in `control_gates.py`:
when discovery is enabled for the run, the remediation plan's target repo
identity (`provider:workspace/repo`) must appear in
`discovery_result.selected` (or, for flag-off/static-only runs, in
`repository_mappings`). Mismatch ⇒ blocking reason
`REPO_NOT_IN_DISCOVERY_SCOPE` → `SAFETY_STOP`, exactly like today's
insufficient-evidence handling. Optionally carry
`authorized_repos: list[str]` on `AuthorizationGrant` (new optional field,
backward compatible) so the publication executor can re-check identity the way
it already re-checks grants before each mutation. The LLM disambiguation step
remains structurally incapable of widening this set.

---

## 10. Security & AGENTS.md compliance mapping

| Rule | Application in this phase |
| --- | --- |
| **Tenant boundary on every endpoint/service touching case data** | Discovery loads the org profile only through the run's `tenant_id`-scoped store calls; the org endpoint forces `organization_id` from the principal; `CaseWorkspaceService`-style reads remain untouched; workspace tools reject refs whose connection wasn't built from the same tenant profile. |
| **`escape()` all dynamic HTML** | Any operator-visible rendering of repo names/descriptions/snippets added to `web.py` pages goes through `html.escape` before interpolation (snippets are additionally redacted server-side). |
| **Idempotency keys on resumed queue tasks** | If discovery work is ever enqueued on the durable worker (resume path), the enqueue uses `f"{run_id}:adk_workflow:resume-{uuid4().hex[:8]}"` — same convention as existing resume keys. |

Additional standing guarantees reused, not reinvented: read-only receipts for
every external call, DRY_RUN blocks mutations by default, circuit breakers
fail closed, secrets never in state/logs/telemetry, append-only gate history.

---

## 11. Testing plan (no network, no Gemini — house style)

New/extended test files:

- `tests/test_workspace_providers.py` — fake provider paging cursors,
  metadata mapping, code-search degrade path (native miss → PARTIAL receipt),
  breaker trip on scripted failures, receipt presence on every call, transport
  injection (assert exact request paths/headers with a stub transport).
- `tests/test_repository_discovery.py` — ranking order across the five
  signals; caps honored (`max_listed_repos`, `max_workspace_calls`);
  multi-provider merge; breaker ⇒ `degraded=True` + static-only fallback;
  TTL cache avoids repeat listing; disambiguation invoked only above the tie
  threshold and cannot add unknown refs (feed a hostile fake model).
- `tests/test_web_workspace_connections.py` — ORG_ADMIN required; tenant
  forced from principal; `secret_ref` redacted in responses; malformed
  connection rejected; scope introspection warn/reject paths.
- `tests/test_memory_loop.py` (extend) — `resolved_repos` round-trip +
  migration on a pre-existing DB file.
- `tests/test_control_gates.py` (extend) — grant rejected when plan targets a
  repo outside `discovery_result.selected`; allowed when inside; unchanged
  behavior when discovery absent.
- `tests/test_golden_path_demo.py` (extend) — golden path runs end-to-end
  against `FakeWorkspaceProvider` with discovery enabled: static-miss case
  resolved purely via code search.
- Fixtures: `fixtures/discovery/` containing (a) a case whose only resolving
  signal is a code-search hit, (b) a two-connection merge scenario, (c) fake
  workspace payloads consumed by `FakeWorkspaceProvider`.

Quality pack (`supportmaster/quality.py`): new `DISCOVERY` category with named
checks — `static_mapping_hit`, `code_search_only_resolution`,
`degraded_fallback`, `multi_provider_merge`, `grant_repo_identity_mismatch_rejected`.

Release readiness (`supportmaster/release.py`): when discovery is enabled,
verify every `secret_ref` resolves through the configured resolver and every
connection has `scope == "READ_ONLY"` before a non-anonymous deployment passes.

---

## 12. Rollout sequencing

| Milestone | Contents | Flag state | Est. effort | Risk |
| --- | --- | --- | --- | --- |
| **M1 — Contract slice** | `base.py` DTOs + Protocol + `FakeWorkspaceProvider`; `HttpGitHubWorkspaceProvider`; `registry.py` skeleton; unit tests | Nothing wired | ~1.5 d | LOW (reuses GitHub auth patterns from publish) |
| **M2 — Discovery online** | `models/discovery.py`; `DiscoveryService` steps 1–4 + bounds/cache/breakers; `repository_discovery_node`; state fields; receipts; memory `resolved_repos` migration; flag-gated graph rewiring; Repository Agent instruction update | `SUPPORTMASTER_DISCOVERY_ENABLED`, default **off** | ~2.5 d | MEDIUM (graph rewiring) |
| **M3 — Remaining providers** | Bitbucket + GitLab providers incl. tier-degrade probes; per-provider tests | off→on per tenant | ~1.5 d | LOW-MED |
| **M4 — Merge + disambiguation** | Multi-provider merge polish; `discovery_disambiguation_agent`; hostile-input tests | on | ~1 d | LOW |
| **M5 — Scope enforcement + tooling** | Grant↔repo identity check; `tools/workspace_tools.py` for Repository Agent deep reads; quality-pack category; release checks; fixtures + golden-path extension; docs (README "Phase 32 adds…", `state-contract.md`, `gcp-deployment.md` Secret Manager notes) | on | ~2 d | MEDIUM |

Acceptance criteria per milestone are the corresponding test files passing
plus: M2 shows `method_trace` + receipts in a run snapshot with the flag on
and byte-identical legacy behavior with it off; M5 shows the golden path
resolving a static-miss case offline and rejecting an out-of-scope grant.

## 13. Explicitly deferred

- Publishing PRs/MRs to Bitbucket/GitLab (publish stays GitHub-only via the
  verified executor; evidence provider ≠ publish target this phase).
- Write-scoped workspace connections (branch/PR creation through providers).
- Semantic/embedding code search (lexical provider-native search only).
- Cross-service dependency graphs / cross-tenant discovery.

## 14. Risk register

| Risk | Mitigation |
| --- | --- |
| Provider API rate limits during listing | TTL cache, page caps, call budget, per-connection breakers, fail-closed to static mapping |
| GitLab Advanced Search unavailable on Free tier | Native-probe + cached tier detection + bounded per-repo fan-out |
| Disambiguation LLM hallucinating repos | Output schema can only reorder/filter; node validates every returned ref ∈ input set; never feeds gates |
| Secret leakage via state/telemetry | Write-only `secret_ref`, resolver-injected tokens, redaction patterns, receipt `details` restricted to counts/statuses |
| Graph regression for existing tenants | Feature flag builds legacy edges verbatim; golden-path + full suite run in both flag states |
| Static mapping drift (stale `repository_mappings`) | Discovery treats it as one signal among four; metadata + code-search signals can outrank it |