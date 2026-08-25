# Devpost Submission Form — Copy/Paste Answers

Everything below is drafted to paste directly into the submission form.
Placeholders marked `<...>` must be filled before submitting.

---

## 1. Project basics

- **Project name:** SupportMaster
- **Tagline:** The autonomous support engineer that proves its work before it ships it.
- **Category:** Taskmaster *(select this on the form)*

## 2. Required short answers

**Which Google SDK did you use?**

> Google Agent Development Kit (ADK, `google-adk==2.7.0`) as the core agent
> framework — SupportMaster's entire execution is a conditionally-routed ADK
> Workflow of 21 specialized agents with deterministic graph gates. We also use
> the Google GenAI SDK (`google-genai`) for the human-in-the-loop Safety Review
> Co-pilot chat. On Google Cloud, the backend runs on Cloud Run, with Secret
> Manager for the Gemini API key, Cloud Build for image builds, and Artifact
> Registry for container storage.

**What date did you start the project?**

> August 15, 2026 (first commit "Initial Agent"). All code was written during
> the hackathon submission period.

## 3. Written description (features / technologies / data sources / learnings)

### What it does

SupportMaster is an autonomous customer-support bug investigation and
resolution agent. Give it a support ticket and it verifies the work isn't a
duplicate, gathers evidence, searches historical issues and repositories,
determines the likely root cause, proposes or implements a fix, runs tests,
generates an RCA, and publishes only when deterministic safety gates permit it.

### Key features

- **21 specialized agents on one ADK Workflow** — intake, duplicate detection,
  evidence, investigation, repository search, root cause, remediation planning,
  implementation, validation, publication, escalation, customer response, and
  more — coordinated through conditionally-routed graph gates instead of LLM
  self-verification.
- **Self-healing execution loop** — if validation checks fail during
  implementation, error traces are recorded in state and routed back to the
  code-change agent for auto-correction (up to 3 retries), then rollback
  receipts restore the repository if all attempts fail.
- **Human-in-the-loop co-pilot chat** — operators don't just click Approve;
  they interrogate the review co-pilot about risks, diffs, and validation gaps
  (powered by the Google GenAI SDK) before unlocking safety-critical gates.
- **Cross-run memory** — every resolved outcome is indexed (SQLite FTS5 +
  TF-IDF similarity) so new investigations reuse past engineering fixes.
- **Deterministic safety gating** — duplicates stop the run; unverified fixes
  can never be claimed; publication goes through an injected verified executor;
  blocked runs terminate via `autonomous_safety_stop` with full receipts.
- **Production hardening** — durable task queue with leases/heartbeats/
  idempotency keys, tenant-scoped JWT-style API-key auth, rate limiting,
  circuit breakers, liveness/readiness probes, redacted telemetry with a
  tamper-evident hash-chain audit export, and SSE live agent-event streaming.
- **Multi-source intake** — manual, REST API, Jira webhooks, Zendesk webhooks,
  normalized into a vendor-neutral case contract per tenant.

### Technologies

Gemini 3.5 Flash (default model; picker supports up to Gemini 3.6 Flash) ·
Gemma 3 27B (advisory ticket triage via the Google GenAI SDK — bonus model
integration) · Google ADK · Google GenAI SDK · Gemini google_search grounding ·
Python 3.11 · Cloud Run · Secret Manager · Cloud Build · Artifact Registry ·
SQLite (ADK sessions, run state, task queue, FTS5 memory, telemetry) ·
Docker/Compose · OpenTelemetry-style spans · Server-Sent Events.

### Data sources

Customer-supplied support tickets (fixtures: SaaS auth failures, latency
degradation, invoice-export OOM), issue-tracker webhook payloads (Jira,
Zendesk), historical resolution memory index, organization context profiles
(products, services, severity vocabulary, escalation rules), and repository
search adapters. No external paid APIs are required; the offline demo runs
fully reproducibly without network access.

### What we learned

- LLM agents cannot be trusted to verify their own work — moving duplicate
  detection, authorization, validation, and publication into *deterministic
  graph gates* made autonomy safe enough to actually run unattended.
- Durable execution matters more than clever prompting: worker leases,
  idempotency keys, and checkpoints are what let a long multi-agent workflow
  survive process interruptions.
- Human-in-the-loop works best when the operator gets a conversational
  co-pilot over real state (diffs, gate history, failure logs), not a bare
  Approve button.
- Memory across runs turns a ticket resolver into an organizational asset:
  past resolutions measurably shorten new investigations.

## 4. Pre-existing / third-party code disclosure

> All application logic, agents, workflows, safety gates, persistence, and UI
> were written from scratch by our team during the hackathon period. We use
> the following pre-existing third-party components, used under their open
> licenses and disclosed here:
>
> - **google-adk 2.7.0** (Apache-2.0) — Google's Agent Development Kit,
>   used as the agent/workflow runtime framework.
> - **python-dotenv 1.2.2** (BSD-3-Clause) — environment variable loading.
> - Standard library only otherwise; no project templates or boilerplate
>   starters were used.

## 5. Startup Excellence prize (OPTIONAL)

If opting in, fill these on the form:

- Incorporated organization name: `<YOUR ORG NAME>`
- Corporate email: `<YOUR CORPORATE EMAIL>`
- Opt-in checkbox on the form: `<yes/no>`

## 6. Bonus social posts (#AllThingsAgenticHackathon)

Publish these on X and LinkedIn, then paste the post URLs back into the
submission form.

### X (Twitter) draft

> 🚀 Built SupportMaster for #AllThingsAgenticHackathon: an autonomous support
> engineer that investigates bugs, writes & tests fixes, and publishes ONLY
> when deterministic safety gates say it's earned it.
>
> 21 agents. One ADK workflow. Zero unverified claims.
> Runs on Gemini 3.5 + Cloud Run. 🧵👇 [link]

### LinkedIn draft

> Most "autonomous agents" are demos that fall apart the moment something
> fails. For the #AllThingsAgenticHackathon we built SupportMaster: a
> production-grade autonomous customer-support engineer.
>
> What makes it different:
> ✅ 21 specialized agents orchestrated by Google's ADK with deterministic
>    safety gates — not LLM self-verification
> ✅ Self-healing loop: failed tests route back to the code-change agent,
>    with rollback receipts if retries exhaust
> ✅ A conversational HITL co-pilot so humans interrogate risks before
>    approving safety-critical actions
> ✅ Cross-run memory that reuses past resolutions
> ✅ Deployed on Google Cloud Run with secrets in Secret Manager
>
> Autonomy isn't trusting the model. It's earning the right to act — with
> evidence, receipts, and gates. Full write-up + demo: <LINK>

## 7. Testing instructions (paste into form)

> Hosted URL: `<CLOUD RUN URL>` (no login required — demo auth mode).
> Try it: open `/workspace` for the operator dashboard, or submit a ticket at
> `/` using the scenario template buttons. Health probes: `/health/live`,
> `/health/ready`. Local spin-up: see README ("Verification" section) —
> `python -m supportmaster.demo reset && python -m supportmaster.demo run`
> runs fully offline without any API key.