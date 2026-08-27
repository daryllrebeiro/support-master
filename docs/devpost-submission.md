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

Gemini 3.5 Flash (default reasoning model; catalog supports Gemini 3.5 Flash Lite and Gemini 3.6 Flash) ·
Gemma 3 27B (`gemma-3-27b-it` for lightweight advisory ticket triage via the Google GenAI SDK — official bonus model
integration) · Google Agent Development Kit (ADK, `google-adk==2.7.0`) · Google GenAI SDK (`google-genai`) ·
Gemini `google_search` grounding · Python 3.11 · Google Cloud Run · Secret Manager · Cloud Build ·
Artifact Registry · SQLite (ADK workflow sessions, durable task queue, FTS5 memory index, telemetry) ·
Docker multi-stage runner · OpenTelemetry-compatible spans · Server-Sent Events (SSE).

### Data sources

Inbound customer support tickets across multiple domains (fixtures for SaaS auth failures, gateway latency
degradation, invoice export memory exhaustion), issue-tracker webhook payloads (Jira, Linear, Zendesk),
historical resolution memory index (SQLite FTS5 virtual tables), organization context profiles
(products, services, vocabulary, escalation matrices), and repository search adapters (GitHub, GitLab, Bitbucket).
Zero paid third-party dependencies required; offline demo and test suites run completely deterministically.

### What we learned & Concrete Engineering Tradeoffs

1. **LLM Self-Verification is Insufficient for Production**: In early iterations, having agents verify their own code changes led to subtle hallucinated test passes. Moving duplicate detection, authorization grants, test validation, and publication into *deterministic graph gates* (`evaluate_action_policy`) made autonomy safe enough to execute unattended.
2. **Decoupling Reasoning from Vendors (Phases 33–40 Refactor)**: We originally had vendor-specific logic inside stage agents. We completely restructured this into a platform-wide rule: *one adapter-agnostic reasoning agent per pipeline stage, thin translation-only adapters behind capability protocols* (`CanFetchCase`, `CanSearchCode`, `CanRunTests`, `CanTriggerCI`). An automated AST guardrail (`test_adapter_gate_isolation.py`) enforces that no adapter can import or mutate gate states.
3. **Diagnose-Before-Retry Self-Healing**: Blindly retrying code changes causes repetitive failure loops. We built a deterministic `failure_diagnosis` node that inspects previous failure warnings and feeds escalating strategy directives (`REPRODUCE_AND_ISOLATE` → `NARROW_DIFF_SCOPE` → `ALTERNATIVE_APPROACH`) before each retry, rolling back cleanly with a Git rollback receipt if 3 retries exhaust.
4. **Conversational HITL Co-Pilot vs. Blind Approvals**: Human operators often reject autonomous actions if they don't understand the diff. Powering the Safety Review Co-pilot with Google GenAI over live workflow state (diffs, gate history, test traces) gave operators the confidence to grant scoped permissions (`IMPLEMENTATION` vs `PUBLISH`).

## 4. Track & Special Prize Eligibility

- **Primary Category**: **Taskmaster** ("a complete workflow, not just a chatbot... one that takes action... proves it can do the heavy lifting for you").
- **Special Prize Eligible**: **Best Architectural Design** (strict multi-agent decoupling, immutable safety skeleton, AST guardrails, durable SQLite task queue leases/heartbeats, write-only credential redaction, and capability protocol matrix).
- **Special Prize Eligible**: **Individual / Hobbyist** (project built and submitted by an independent developer).
- **Bonus Model Integration**: **Gemma** (`gemma-3-27b-it` advisory ticket classifier in `supportmaster/triage.py`).

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