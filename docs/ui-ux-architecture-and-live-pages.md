# SupportMaster: UI/UX Architecture, Live Interfaces & Experience Specification

> **Document Purpose:** Comprehensive technical breakdown of all live user-facing web interfaces, backend API routes, visual design systems, interaction workflows, and strategic UI/UX evolution roadmap for the **SupportMaster** platform.

---

## 1. System Overview & Live Route Topology

SupportMaster delivers a unified, enterprise-grade control center for autonomous L3 support engineering. The application provides three primary operational perspectives:
1. **🚀 Workflow Launcher & Scenario Quick-Loader** (`/` - Tab 1)
2. **💬 ADK Live Multi-Agent Reasoning Chat & Execution Trace** (`/` - Tab 2)
3. **🗂️ Operator Case Workspace & HITL Governance Console** (`/workspace`)

### Route Map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HTTP SERVER (0.0.0.0:8001)                        │
└───────┬─────────────────────────────┬───────────────────────────────┬───────┘
        │                             │                               │
┌───────▼────────────────┐   ┌────────▼───────────────┐   ┌───────────▼───────────┐
│     PAGE INTERFACES    │   │      API ENDPOINTS     │   │     HEALTH PROBES     │
├────────────────────────┤   ├────────────────────────┤   ├───────────────────────┤
│ GET /                  │   │ POST /api/chat         │   │ GET /health/live      │
│  - Tab 1: Launcher     │   │ GET  /api/fixtures     │   │ GET /health/ready     │
│  - Tab 2: ADK Chat     │   │ GET  /api/fixtures/:id │   └───────────────────────┘
│ GET /workspace         │   │ GET  /api/cases        │
│  - Pipeline Stepper    │   │ GET  /api/cases/:id    │
│  - HITL Review Queue   │   │ GET  /api/cases/:id/act│
│  - Audit Telemetry     │   │ GET  /api/reviews      │
└────────────────────────┘   │ POST /api/reviews/:dec │
                             │ GET  /api/reviews/metr │
                             │ GET  /api/events/:runId│
                             │ POST /api/settings/auto│
                             │ POST /api/connectors/* │
                             └────────────────────────┘
```

---

## 2. Detailed Breakdown of Live Pages & Interfaces

### 2.1. Unified Control Center (`GET /`)

The entry page provides an ambient dark glassmorphic interface with a persistent sticky navigation header and a dual-view switcher.

#### Top Navigation Bar
- **Brand Logo & Version Badge:** `ADK 2.7` gradient pill badge next to the `SupportMaster` gradient title.
- **View Switcher Tabs:**
  - `🚀 Workflow Launcher`: Switches instantly to the scenario execution panel.
  - `💬 ADK Live Chat & Reasoning`: Switches to the conversational diagnostic console.
  - `🗂️ Operator Workspace`: Direct navigation link to `/workspace`.
- **System Health Indicators:** Live green dot (`● Live`) linking to `/health/live` and blue dot (`● Ready`) linking to `/health/ready`.

---

#### View 1: 🚀 Workflow Launcher Panel (`#view-launcher`)

Designed for automated batch runs, golden-path demonstrations, and single-click incident reproductions.

1. **Reasoning Model Selector:**
   - Native styled dropdown (`<select id="model">`) supporting:
     - `Gemini 2.5 Flash (Recommended - Balanced Reasoning & Speed)`
     - `Gemini 2.5 Pro (Deep Code Synthesis & Complex AST Analysis)`
     - `Gemini 2.5 Flash Lite (Low-Latency Fast Triage)`
     - `Gemma 3 27B IT (Local / Open Weights Advisory)`
2. **Scenario Quick-Loader Buttons (`#templates-list`):**
   - Automatically queries `GET /api/fixtures` on page load.
   - Renders interactive pill chips for all seeded realistic test cases:
     - `⚡ Acme Invoice Failure (SUP-4821)` (SaaS billing / token expiration bug)
     - `⚡ Database Deadlock on Concurrent Webhooks`
     - `⚡ Redis Connection Pool Exhaustion`
     - `⚡ OAuth Callback 500 on Null Refresh Token`
   - Clicking any chip asynchronously fetches the fixture payload via `GET /api/fixtures/:name` and automatically formats and populates the ticket description textarea.
3. **Support Ticket Input Area (`#issue`):**
   - JetBrains Mono monospaced editor for raw Jira/Zendesk tickets, reproduction steps, stack traces, and customer impact statements.
4. **Execution CTA Button (`button[type="submit"]`):**
   - Gradient blue action button (`#3b82f6` ➔ `#2563eb`) with hover elevation and active press states.
5. **Execution Results Card (`.results-card`):**
   - Renders formatted execution events, agent thoughts, tool call receipts, synthesized diffs, and verification assertions upon run completion.

---

#### View 2: 💬 ADK Live Multi-Agent Reasoning Chat (`#view-chat`)

Designed for interactive, step-by-step diagnostic conversations, internal reasoning transparency, and tool inspection.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  ADK Multi-Agent Reasoning Chat                        [Model Selector ▼]   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  [SM] SupportMaster (ADK Multi-Agent)                                      │
│       Hello Operator! I am SupportMaster, your autonomous L3 support        │
│       engineering agent powered by Google ADK and Gemini.                   │
│       [1. Intake] [2. Investigation] [3. Duplicate Gates] ...               │
│                                                                             │
│                                           [OP] Diagnose Acme SSO Failure    │
│                                                invoice calculation bug.     │
│                                                                             │
│  [SM] SupportMaster (Gemini 2.5 Flash)                                      │
│       ┌─ 🧠 ADK Multi-Agent Execution Trace & Verification ──────────────┐  │
│       │  [Intake Agent] Accepted incident SUP-4821.                      │  │
│       │  [Tool Call] workspace_search(query="calculate_invoice_tax")     │  │
│       │  [Gate Check] duplicate_work_gate -> PASSED (0 duplicates)       │  │
│       │  [Remediation] Synthesized unified patch in invoice_engine.py    │  │
│       │  [Verification] Test suite passed (4/4 assertions OK).           │  │
│       └──────────────────────────────────────────────────────────────────┘  │
│       Synthesized remediation patch for invoice calculation overflow...     │
│       [🗂️ View Case in Operator Workspace]                                  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  Quick Prompts: [⚡ Acme SSO Failure] [🔍 Redis Leak] [🛡️ Verify Gates]     │
│  ┌───────────────────────────────────────────────────────────┐ ┌──────────┐ │
│  │ Type a support incident, question, or diagnostic task...  │ │   Send   │ │
│  └───────────────────────────────────────────────────────────┘ └──────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

1. **Header Toolbar:**
   - Real-time Model Selector specific to the chat session.
   - Status subtitle indicating active ADK reasoning engine.
2. **Interactive Message Feed (`#chat-messages`):**
   - **Operator Bubbles (`.msg-row.user`):** Right-aligned royal blue gradient bubbles with user avatar (`OP`).
   - **SupportMaster Bubbles (`.msg-row.agent`):** Left-aligned glass cards with agent avatar (`SM`) and model identifier tag.
3. **Multi-Agent Pipeline Stage Flow (`.stage-flow`):**
   - Visual badges highlighting each step of the pipeline:
     - `1. Intake` ➔ `2. Investigation` ➔ `3. Duplicate Gates` ➔ `4. Remediation` ➔ `5. Verification` ➔ `6. Publish`
4. **Collapsible Reasoning Accordion (`<details class="reasoning-box">`):**
   - Expandable brain accordion (`🧠 ADK Multi-Agent Execution Trace & Verification`) displaying:
     - Internal thoughts and planning steps of specialized stage agents.
     - Tool invocations (`workspace_search`, `google_search`, `git_apply_patch`, `run_tests`).
     - Grounding citations and source file lines.
5. **Real-Time Typing / Thinking Indicator (`.typing-indicator`):**
   - Three animated pulsing dots displayed while the background worker processes the LLM and tool calls.
6. **Quick Prompt Chips (`.prompt-chips`):**
   - Pre-baked incident prompts that automatically dispatch diagnostic workflows with one click.
7. **Cross-View Link Button:**
   - Direct button on agent responses linking into the `/workspace` case timeline.

---

### 2.2. Operator Case Workspace & Governance Console (`GET /workspace`)

The centralized management console for tenant-scoped cases, safety gates, and human-in-the-loop approvals.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  SupportMaster Case Workspace                        [✓ Autonomous Mode]    │
│  Tenant-scoped execution dashboard & gates           [← Control Panel]      │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │ TOTAL CASES  │  │ OPEN REVIEWS │  │  APPROVALS   │  │ EXPIRING (24h)  │  │
│  │      12      │  │      1       │  │      8       │  │        0        │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────────────────────────┤
│  ⚠️ ACTION REQUIRED: HUMAN REVIEW PENDING                                   │
│  Reason: Proposed remediation modifies high-risk payment authorization module.│
│  [ Reviewer: Alice Smith ] [ Decision: APPROVE ▼ ] [ Scopes: [✓] PR_WRITE ] │
│  [ Resume Token: ********** ] [ Submit Authorization Decision ]             │
├─────────────────────────────────────────────────────────────────────────────┤
│  ACTIVE CASE PIPELINE                                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ ⚡ Acme SSO Invoice Calculation Bug (SUP-4821)         [RESOLVED]     │  │
│  │ Case ID: SUP-4821 | Tenant: default | Source: JIRA | Stage: COMPLETED │  │
│  │                                                                       │  │
│  │ Description:                                                          │  │
│  │ Users on Enterprise SSO plan encounter 500 error during tax compute.  │  │
│  │                                                                       │  │
│  │ Recommended Next Action: Automated verification passed. Ready to close│  │
│  │                                                                       │  │
│  │ Safety Gate Verification Status:                                      │  │
│  │ [DUPLICATE GATE: PASSED] [IMPL AUTH: PASSED] [VALIDATION: PASSED]     │  │
│  │                                                                       │  │
│  │ Workflow Stages Pipeline:                                             │  │
│  │ ● INTAKE: COMPLETE — Support case was accepted and normalized.        │  │
│  │ ● INVESTIGATION: READY — Evidence gathered from workspace AST.        │  │
│  │ ● PLANNING: READY — Root cause identified in invoice_engine.py:L142.  │  │
│  │ ● RESOLUTION: VERIFIED — Unified patch applied and tests passed.      │  │
│  │                                                                       │  │
│  │ Verifiable Audit Telemetry Log:                                       │  │
│  │ - WORKFLOW_COMPLETED ................................... 18:24:12 UTC │  │
│  │ - VERIFICATION_PASSED .................................. 18:24:10 UTC │  │
│  │ - PATCH_APPLIED ........................................ 18:24:08 UTC │  │
│  │                                                                       │  │
│  │ [💬 Open in ADK Live Chat]                                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Key Workspace Modules:

1. **Executive Metrics Grid (`.metrics-grid`):**
   - **Total Cases:** Real-time tally of all intake cases for the active tenant.
   - **Open Review Tasks:** Live counter with pulsating amber border when human approvals are pending.
   - **Total Approvals:** Count of granted operator clearances.
   - **Expiring Tasks (24h):** Tasks approaching TTL expiration.
2. **Autonomous Auto-Approve Toggle (`#auto-approve-toggle`):**
   - Checkbox allowing operators to switch between **Strict Human-in-the-Loop Gate Approval** and **Autonomous Self-Approval** for demo environments.
3. **Pending Review Queue (`#review-queue`):**
   - Displays interactive decision cards for blocked or safety-paused workflows.
   - Includes reviewer attribution, decision dropdown (`APPROVE` / `REJECT`), granular scope checkboxes (`RUN_EXECUTE`, `PR_WRITE`, `NOTIFY`), resume token authentication, and audit comments.
4. **Active Case Cards (`.case-card`):**
   - **Metadata Ribbon:** Title, Case ID, Tenant ID, Source System (`JIRA`, `ZENDESK`, `MANUAL`), and Current Stage.
   - **Status Badge:** Color-coded (`open`, `completed`, `safety-stop`).
   - **Action Banner:** Proactive diagnostic recommendation from the Planning Agent.
   - **Gate Verification Badges:** Real-time state of all safety skeleton gates.
   - **Pipeline Flow Stepper (`.timeline-flow`):** Vertical progress timeline with color-coded status dots (`complete`, `partial`, `safety-stop`).
   - **Telemetry Audit Log:** Latest chronological events with microsecond timestamps from the SQLite append-only audit ledger.

---

## 3. UI Design System & Aesthetic Specifications

SupportMaster adheres to modern, accessible web design principles with custom vanilla CSS tokens.

### 3.1. Color Palette Tokens

| Token | Hex / Value | Semantic Role |
|---|---|---|
| `--bg-color` | `#030712` | Deep obsidian background |
| `--card-bg` | `rgba(17, 24, 39, 0.75)` | Dark glassmorphic card backdrop |
| `--card-border` | `rgba(55, 65, 81, 0.5)` | Subtle translucent border |
| `--accent-blue` | `#3b82f6` | Primary action & brand accent |
| `--accent-cyan` | `#06b6d4` | Tool calls, eyebrows & highlights |
| `--accent-purple`| `#8b5cf6` | Agent thoughts & reasoning borders |
| `--green-bright`| `#10b981` | Success states, passed gates, completed stages |
| `--amber-bright`| `#f59e0b` | Warnings, pauses, human review pending |
| `--red-bright`  | `#ef4444` | Safety stops, policy violations, errors |
| `--text-primary`| `#f3f4f6` | High-contrast primary text |
| `--text-secondary`| `#9ca3af` | Secondary labels and metadata |
| `--text-muted`  | `#6b7280` | Subtle timestamps and helper notes |

### 3.2. Typography

- **Headings & Badges:** `'Outfit', system-ui, sans-serif` (Weights: 600, 700, 800)
- **Body & Controls:** `'Inter', system-ui, sans-serif` (Weights: 400, 500, 600)
- **Code, Diffs, Reasoners & Audit:** `'JetBrains Mono', monospace` (Weights: 400, 500)

### 3.3. Micro-Animations & Interactions

- **Glassmorphism Backdrop:** `backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);`
- **View Transitions:** Smooth CSS `@keyframes fadeIn` on tab switching.
- **Button Hover Elevation:** `transform: translateY(-1px); box-shadow: 0 4px 16px rgba(59, 130, 246, 0.3);`
- **Typing Pulsing Dots:** 1.4s infinite ease-in-out scaling animation.

---

## 4. End-to-End User Experience Flows

### Flow 1: One-Click Golden Path Incident Reproduction
```
[Select Gemini 2.5 Flash] 
       │
[Click "Acme Invoice Failure" Chip] ➔ (Textarea auto-populates with SUP-4821)
       │
[Click "Run SupportMaster Workflow"]
       │
[System executes 6-stage pipeline in isolated SQLite workspace]
       │
[Operator switches to /workspace] ➔ (Inspects verified timeline, gates & audit log)
```

### Flow 2: Interactive ADK Reasoning & Chat
```
[Switch to "ADK Live Chat & Reasoning" Tab]
       │
[Click Prompt Chip: "🔍 Redis Connection Leak"]
       │
[Typing Indicator Pulses] ➔ [Agent streams thoughts & tool calls]
       │
[Expand "🧠 Reasoning Trace"] ➔ (Inspect AST search matches and gate verdicts)
       │
[Click "💬 View Case in Workspace"] ➔ (Deep dive into full case history)
```

### Flow 3: Human-in-the-Loop Governance & Resumption
```
[Workflow detects high-risk mutation / sensitive permission]
       │
[Autonomous Safety Gate triggers: RUN_PAUSED_FOR_HUMAN_REVIEW]
       │
[Amber Alert badge pulses in /workspace Review Queue]
       │
[Operator selects APPROVED scopes & inputs Resume Token]
       │
[Workflow atomically resumes in background with new idempotency key]
```

---

## 5. UI/UX Evolution & Enhancement Roadmap

Based on this architecture, the following high-impact enhancements are slated for future iterations:

1. **Split-Screen Dual Workspace:**
   - Side-by-side view featuring interactive ADK Chat on the left and live-updating Case Pipeline Stepper on the right.
2. **Integrated Monaco / Prism Diff Editor:**
   - Syntax-highlighted side-by-side unified git diff viewer for synthesized remediation patches with inline line-by-line change acceptance.
3. **Interactive Graph Topologies:**
   - Visual DAG rendering of discovered workspace repositories, AST call hierarchies, and duplicate ticket clusters using SVG/D3.
4. **Live SSE Streaming in Chat:**
   - Real-time token-by-token text streaming directly into the ADK Chat bubbles via the existing `/api/events/{run_id}` Server-Sent Events channel.
5. **Tenant & Environment Switcher:**
   - Header dropdown allowing instant switching between enterprise tenant boundaries and sandbox simulation workspaces.
