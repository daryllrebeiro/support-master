# SupportMaster: Recording-Day Zero-Debug Runbook

**Purpose:** Comprehensive, step-by-step execution protocol for the Sunday recording session (target runtime: 3:20, maximum cap: 4:00). Follow this exact script to eliminate live debugging and ensure a flawless video take.

---

## 1. Sunday Pre-Flight Checklist (Run 30 mins before recording)

Execute these checks before starting OBS/Screen Recorder:

- [ ] **Python Environment**: Ensure virtual environment is active:
  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover -s tests -v
  ```
- [ ] **Clean Demo DB State**: Reset SQLite stores to a known-clean state:
  ```powershell
  .\.venv\Scripts\python.exe -m supportmaster.demo reset
  ```
- [ ] **Local Server Running**: Start the web service in Terminal Tab 1:
  ```powershell
  .\.venv\Scripts\python.exe -m supportmaster.web
  ```
- [ ] **Verify Endpoints**: Open in Chrome (110% zoom, bookmarks bar hidden):
  - `http://127.0.0.1:8001/` (Picker / Ticket Intake)
  - `http://127.0.0.1:8001/workspace` (Live Operator Workspace)
  - `http://127.0.0.1:8001/health/live` (Health Probe)
- [ ] **Google Cloud Tab (Tab 4)**: Open GCP Console → Cloud Run → Service `supportmaster` in `us-central1` showing `RUNNING` status and active URL.
- [ ] **Copy-Paste Scratchpad**: Have text snippets open in a side window (never live-type during recording).

---

## 2. Scratchpad Copy-Paste Snippets

### Snippet A: Inbound Ticket (FIN-1847)
```text
Title: Invoice Export OOM Crash on Large Accounts
Description: Generating annual invoice exports fails with OutOfMemoryError for accounts with >50k transactions. Workers crash immediately and trigger high-priority alerts.
Product: BillingEngine
Environment: Production
Severity: Critical
```

### Snippet B: Operator Co-Pilot Chat Question (Scene 4)
```text
What are the risks and test targets associated with approving this streaming patch?
```

---

## 3. Scene-by-Scene Recording Protocol

### SCENE 1 — Cold Open: Autonomous Intake (0:00 – 0:20)
- **Screen**: Chrome Tab 1 (`http://127.0.0.1:8001/`).
- **Action**:
  1. Paste Snippet A into the ticket form.
  2. Click **Run SupportMaster**.
  3. Events immediately stream via SSE.
- **Voiceover**: *"This is SupportMaster — an autonomous customer-support engineer that just took a P1 production incident and is investigating it in real time."*

### SCENE 2 — Parallel Investigation & FTS5 Memory (0:20 – 0:55)
- **Screen**: Chrome Tab 2 (`http://127.0.0.1:8001/workspace`).
- **Action**:
  1. Show workflow timeline lighting up: Ticket Intake → Advisory Gemma Triage → Duplicate Gate → Parallel Fan-out.
  2. Highlight `InvestigationAgent` querying SQLite FTS5 cross-run memory and `RepositoryDiscoveryAgent` identifying candidate repositories.
- **Voiceover**: *"SupportMaster's duplicate gate proves this is novel work. Then investigation and repository discovery run in parallel across vendor tools, pulling historical context from cross-run memory."*

### SCENE 3 — Root Cause with Evidence Links (0:55 – 1:30)
- **Screen**: Scroll down `/workspace` to **Root Cause Analysis**.
- **Action**:
  1. Highlight confidence level (`STRONGLY_SUPPORTED`).
  2. Point to evidence citations linking to `invoice_export.py`.
- **Voiceover**: *"The root cause agent cannot guess — claims must link directly to verified signals before proposing a scoped patch."*

### SCENE 4 — Safety Gate & Interactive Co-Pilot Review (1:30 – 2:10)
- **Screen**: `/workspace` Review Queue modal / Co-pilot pane.
- **Action**:
  1. Show `Implementation Gate` in `PENDING_REVIEW` state.
  2. Paste Snippet B into the Co-pilot chat.
  3. Co-pilot responds with diff analysis, risk evaluation, and targeted test command (`test_invoice_export`).
  4. Click **Approve Scoped Implementation** (granting `IMPLEMENTATION`, keeping `PUBLISH` locked).
- **Voiceover**: *"Before touching code, a human reviews. Instead of a blind approve button, our Safety Review Co-pilot answers operator questions about diffs, failure modes, and test coverage before granting a scoped authorization."*

### SCENE 5 — Verified Execution & Self-Healing (2:10 – 2:45)
- **Screen**: Terminal Tab 2 (Split Screen or cut to PowerShell).
- **Action**:
  1. Run the verified engineering command:
     ```powershell
     .\scripts\golden-path.ps1
     ```
  2. Show output: Git preflight check → Scoped patch applied to `invoice_export.py` → Real `unittest` subprocess runs and passes (3/3 tests) → Commit `supportmaster/sup-golden` with signed `ExternalOperationReceipt`s.
- **Voiceover**: *"The execution layer applies the patch strictly inside approved paths, executes real regression tests in subprocesses, and records signed audit receipts for every mutation."*

### SCENE 6 — Google Cloud Infrastructure Proof (2:45 – 3:10)
- **Screen**: Chrome Tab 4 (Google Cloud Console).
- **Action**:
  1. Show Cloud Run console with `supportmaster` active revision.
  2. Show Secret Manager holding `google-api-key`.
  3. Switch to live Cloud Run URL (`https://<service-url>.a.run.app/health/live`) returning HTTP 200 `{"status":"HEALTHY"}`.
- **Voiceover**: *"The entire production backend runs on Google Cloud — served via Cloud Run, secured with Secret Manager, built via Cloud Build, and powered by Gemini 3.5 Flash."*

### SCENE 7 — Architecture Diagram & Close (3:10 – 3:25)
- **Screen**: Full-screen view of `docs/architecture-diagram.svg` or README diagram.
- **Voiceover**: *"SupportMaster: 21 specialized agents, deterministic graph gates, and verifiable receipts. Built for the Taskmaster track because it finishes the whole job safely."*

---

## 4. Fallback Contingency Protocols

| Failure Mode during Take | Immediate Contingency |
| :--- | :--- |
| **GCP / Network Latency Spike** | Cut immediately to Terminal Tab 2 running `.\scripts\golden-path.ps1` (100% deterministic, 0ms network latency). |
| **Database Lock / Stale Run State** | Execute `.\.venv\Scripts\python.exe -m supportmaster.demo reset` (re-seeds in 200ms). |
| **Cloud Run Token Expiry** | Use pre-captured Cloud Run Dashboard screenshot (placed in `docs/cloudrun-proof.png`) for Scene 6. |

---

## 5. Synchronization Cross-Check with `docs/demo-video-script.md`

- **Scene Breakdown**: 7 scenes in runbook directly align 1-to-1 with `demo-video-script.md`.
- **Target Runtime**: 3 minutes 25 seconds (leaves 35-second safety margin under the 4:00 hard cap).
- **Models Referenced**: Gemini 3.5 Flash (reasoning) + Gemma 3 27B (triage) + Google ADK 2.7.0.
