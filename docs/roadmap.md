# SupportMaster Enhancement Roadmap

Ranked by **value ÷ effort** with the Aug 31 deadline in mind. Tier 1 items are
submission-relevant; Tier 2+ are post-hackathon product direction.

---

## Tier 1 — Before the deadline (bonus points & compliance polish)

### 1. Gemma integration (bonus points: "additional Google AI models")
**Effort:** ~2–4 hours · **Value:** explicit bonus checklist item
Add Gemma as a lightweight triage/classification model alongside Gemini:
- New adapter in `supportmaster/models/` calling Gemma via
  `genai.Client` (AI Studio) or Vertex AI Model Garden endpoint.
- Use it for cheap, high-volume steps: ticket severity classification,
  duplicate-candidate pre-ranking, evidence-gap labeling.
- Keep Gemini 3.5 Flash for reasoning-heavy stages.
- Surface it in the model picker as a secondary "triage model" setting.
- Update Devpost description + video overlay: `Gemini 3.5 + Gemma`.

### 2. Vertex AI Agent Engine deployment (optional upgrade over plain Cloud Run)
**Effort:** ~half a day · **Value:** strongest possible "agent runs on Google Cloud" story
ADK agents deploy natively to Vertex AI Agent Engine (`agent_engines.create`).
Deploying the gated workflow there gives managed sessions and a console-visible
agent artifact. If time is tight, keep Cloud Run (already sufficient) and list
this as future work.

### 3. Cloud Logging / Trace export for existing OTel spans
**Effort:** ~1–2 hours · **Value:** observability proof on camera
The telemetry layer already emits spans; add an optional exporter that ships
them to Cloud Trace/Logging when `GOOGLE_CLOUD_PROJECT` is set. One more
Google Cloud service used, zero workflow changes.

---

## Tier 2 — First post-hackathon sprint (memory & scale)

### 4. Vector memory upgrade (aligns with the Aug 27 workshop)
Replace the TF-IDF/FTS5 similarity index with real embeddings:
- **Option A (fastest):** AlloyDB / Cloud SQL Postgres + `pgvector`, reusing
  the existing persistence-adapter seam (`SUPPORTMASTER_RUN_DB`).
- **Option B (managed):** Vertex AI Vector Search for large corpora.
- Embed past resolutions with `text-embedding-*`; retrieve top-k similar
  fixes during investigation. The memory interface already isolates storage,
  so this is an adapter swap plus a backfill script.

### 5. Firestore for multi-tenant run state
Swap SQLiteRunStore's backend for Firestore behind the same interface:
true horizontal scale for the durable queue (or pair Cloud Tasks/Pub/Sub with
Cloud Run Jobs for the worker path). Keep SQLite as the offline/demo default.

### 6. Genkit assessment (new service surface, not a rewrite)
Genkit would only add value for a *separate* lightweight flow (e.g., a public
status page or customer-response generator) — the core workflow should stay
on ADK. Not recommended before the deadline; revisit if a second service
emerges.

### 7. Antigravity SDK assessment
The hackathon framework rule is already satisfied by ADK + GenAI SDK, so
Antigravity adds compliance value only as an *additional* integration. Evaluate
post-event for editor-side agentic workflows; do not spend deadline week here.

---

## Tier 3 — Product direction (post-hackathon)

| Idea | Why it matters |
|---|---|
| Live Jira/Zendesk/GitHub connectors behind feature flags | Move from dry-run adapters to real mutations with tenant-scoped OAuth |
| Scheduled drift detection | Nightly runs that compare repo behavior against known RCA patterns |
| Multi-repo investigation fan-out | Scale repository agent across monorepo boundaries |
| Customer-facing status portal | Read-only view of case timelines for ticket reporters |
| Evaluation leaderboard | Track gate precision/recall across fixture suites over time |
| Fine-tuned Gemma triage model | Distill gate decisions into a fast local classifier |

---

## Sequencing recommendation

Deadline week: **#1 (Gemma)** → finish submission → then #4 (vector memory) as
the flagship post-hackathon improvement, since it compounds every other
feature and matches the final workshop's theme.