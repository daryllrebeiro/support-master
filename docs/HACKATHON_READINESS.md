# SupportMaster — Hackathon Submission Readiness

**Hackathon:** All Things Agentic (#AllThingsAgenticHackathon)
**Deadline:** August 31, 5:00 PM PT — **everything locks after that; do not touch the repo, video, or linked materials until winners are announced.**
**Category (locked choice):** Taskmaster
**Last audited:** August 25, 2026

---

## 1. Master Checklist Audit

Legend: ✅ done · 🟡 partially done / needs verification · ❌ missing · 🔧 fixed in this repo update

| # | Checklist item | Status | Evidence / Notes |
|---|---|---|---|
| 1 | New project built during submission period | ✅ | Git history starts **2026-08-15** ("Initial Agent"), all work after submission window opened. |
| 2 | Uses **Gemini 3.5 or newer** | 🔧 | Default was `gemini-2.5-flash` → now `gemini-3.5-flash` (`config.py`, `.env.example`, picker UI). Catalog is 3.5+ only. |
| 3 | Uses at least one Google agent framework | ✅ | **ADK** (`google-adk==2.7.0`) drives the whole gated workflow; **GenAI SDK** powers the review co-pilot chat. |
| 4 | Uses at least one Google Cloud service | 🔧→⏳ | Code + deploy script ready (**Cloud Run**, Secret Manager, Cloud Build, Artifact Registry). **You must actually run the deploy** — see §2.1. |
| 5 | One category selected | ✅ | Taskmaster (README "Hackathon Track"). |
| 6 | Teammates added & accepted invites | ⏳ | Verify on Devpost → Settings → Teammates. Every member must have ACCEPTED. |
| 7 | Demo video: public, <4 min, shows agent working + GCP proof | ⏳ | Script ready in `docs/demo-video-script.md`. Must include a Cloud Console / `gcloud run services list` proof shot. |
| 8 | Code repo linked; if private, grant access to testing@devpost.com and cloudhackathons@google.com | ⏳ | Repo: `github.com/daryllrebeiro/SupportMaster`. Confirm it is **public**, or add both emails as collaborators. |
| 9 | Architecture diagram uploaded + spin-up instructions in README | 🔧 | Mermaid diagram added to README top; spin-up = `.env.example` copy + demo commands + Cloud Run one-liner. |
| 10 | Hosted project URL included (credentials in testing instructions if login-gated) | ⏳ | Deploy with `scripts/deploy-cloudrun.ps1`, then paste URL into the form. Current deploy uses OPTIONAL auth (no credentials needed). |
| 11 | Which Google SDK used + project start date answered | 🔧 | Draft answers in `docs/devpost-submission.md` §2. |
| 12 | Text description: features, technologies, data sources, learnings | 🔧 | Full draft in `docs/devpost-submission.md` §3 — copy/paste into the form. |
| 13 | Pre-existing / third-party code disclosed | 🔧 | Disclosure written in `docs/devpost-submission.md` §4. |
| 14 | Startup Excellence prize opt-in (+ org name & corporate email) | ⏳ | Optional. If entering: fill placeholders in `docs/devpost-submission.md` §5 and opt in on the form. |
| 15 | Bonus: public write-up / social post (#AllThingsAgenticHackathon on X + LinkedIn) | 🔧→⏳ | Post drafts ready in `docs/devpost-submission.md` §6 — publish them, then link the URLs on the form. |
| 16 | Bonus: additional Google AI models (Gemma, Veo, Lyria) integrated | ❌→📋 | Not yet integrated. Concrete Gemma integration plan in `docs/roadmap.md` §3 (highest-value bonus). |

---

## 2. Blocker Remediation (do these first)

### 2.1 Deploy to Google Cloud Run (~30–60 min) — *disqualifier if skipped*

The checklist requires a real Google Cloud service. Everything is prepared:

```powershell
# Prereqs: gcloud auth login, billing enabled, Gemini key exported
$env:GOOGLE_API_KEY = "<your-gemini-api-key>"
.\scripts\deploy-cloudrun.ps1 -ProjectId <your-project-id> -Region us-central1
```

Then:
- [ ] Copy the printed hosted URL into the Devpost form
- [ ] Smoke-test `<url>/health/live`, `<url>/workspace`, and one live workflow run
- [ ] Record the 5-second Cloud Console proof segment for the video
- [ ] Full guide: `docs/gcp-deployment.md`

### 2.2 Model compliance — DONE in this update

- `DEFAULT_MODEL` = `gemini-3.5-flash`; picker catalog = `gemini-3.5-flash-lite`, `gemini-3.5-flash`, `gemini-3.6-flash`
- `.env.example`, README, and web fallback updated; 153 tests pass

### 2.3 Repo visibility (~5 min)

- [ ] Confirm `github.com/daryllrebeiro/SupportMaster` is public, **or**
- [ ] Invite `testing@devpost.com` and `cloudhackathons@google.com` as collaborators

### 2.4 Devpost housekeeping (~15 min)

- [ ] Every teammate has ACCEPTED their invite
- [ ] Category = Taskmaster is selected on the form
- [ ] Upload architecture diagram (export the mermaid diagram from README to PNG, or screenshot it rendered on GitHub)
- [ ] Paste hosted URL + testing instructions
- [ ] Copy form answers from `docs/devpost-submission.md`

---

## 3. Countdown Plan (Aug 25 → Aug 31)

| Day | Goal |
|---|---|
| **Mon Aug 25** | Run Cloud Run deploy (§2.1). Fix any deploy issues today. |
| **Tue Aug 26** | Record video clips per `docs/demo-video-script.md` (record short segments separately). Publish social posts (§6 of devpost doc). |
| **Wed Aug 27** | Attend final workshop *"Architecting Agent Memory"* (9 AM or 9 PM PT) — cite it in your write-up if you apply anything. Edit video: jump cuts, captions, <4 min. |
| **Thu Aug 28** | Fill the entire Devpost form using `docs/devpost-submission.md`. Upload diagram. Link repo + video + hosted URL. Optional: Gemma bonus integration from roadmap. |
| **Fri Aug 29** | Friend-review pass: someone else reads the Official Rules + FAQ and checks every checklist item against the form. Test the hosted URL end-to-end one more time. |
| **Sat Aug 30** | Buffer day. Final submit. Do NOT wait for Aug 31 evening. |
| **Sun Aug 31** | Deadline 5 PM PT. After submission: freeze everything — no repo pushes, no video edits, no link changes until winners are announced. |

---

## 4. Video Requirements Recap (details in `docs/demo-video-script.md`)

- Public (YouTube unlisted-or-public), under 4 minutes
- Shows the agent actually working (live workflow run)
- Includes proof the backend runs on Google Cloud (Cloud Console / gcloud shot)
- Working app visible within the first 10–15 seconds; no setup/loading screens
- No live typing — paste inputs, cut waits, use jump cuts and on-screen text

## 5. What Judges Will See (strength summary)

- 21 specialized agents on an ADK conditionally-routed Workflow with deterministic gates
- Self-healing execution loop (validation failure → auto-retry ×3 → rollback receipt)
- HITL co-pilot chat for safety-critical approvals
- Cross-run SQLite FTS5 memory reusing past resolutions
- Durable task queue (leases, heartbeats, idempotency keys), tenant-scoped security, tamper-evident audit chain
- 153 passing offline tests + reproducible fixtures (SUP-4821, AUTH-001, PERF-042, DATA-007)
- Deployed on Cloud Run with Secret Manager key handling