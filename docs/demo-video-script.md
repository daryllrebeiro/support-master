# SupportMaster Demo Video Script (< 4 minutes)

**Goal:** judges see the agent working within 10 seconds, understand the
safety-gate differentiator by 1:30, and see Google Cloud proof before the end.
**Target runtime:** ~3:20 (leaves safety margin under the 4-minute cap).

## Pre-recording checklist

- [ ] App already running locally AND deployed URL live (start logged-in; no setup on camera)
- [ ] All long inputs pre-written in a scratch file (paste, never type)
- [ ] Screen recorder at 1080p+, clean desktop, browser zoom ~110%, notifications off
- [ ] Cloud Console open in a second tab, already authenticated
- [ ] Record each scene as a separate clip so one bad take doesn't cost the whole video
- [ ] On-screen text overlays prepared (list below)

---

## Scene-by-scene script

### SCENE 1 — Cold open: it's already working (0:00–0:15)
**Visual:** SupportMaster picker page already loaded. Cursor pastes the
invoice-export OOM ticket (FIN-1847) into the textarea, clicks **Run
SupportMaster**. Agent events start streaming instantly.
**VO:** "This is SupportMaster — an autonomous support engineer that just took
a P1 production bug and is investigating it right now."
**Overlay text:** `21 agents · 1 ADK workflow · deterministic safety gates`
> *Cut: no intro, no logo, no title screen.*

### SCENE 2 — What's happening inside (0:15–0:50)
**Visual:** Jump-cut to `/workspace` showing the case card: workflow timeline
lighting up stage by stage (intake → duplicate check → evidence → parallel
investigation). Use 2× speed through waiting sections.
**VO:** "A duplicate-work agent first proves this isn't known work. Then
evidence and repository agents investigate in parallel. Every stage lands in
an auditable timeline."
**Overlay text:** `Duplicate gate → Parallel investigation → Deterministic join`

### SCENE 3 — Root cause with receipts (0:50–1:25)
**Visual:** Scroll to root-cause assessment + remediation plan in the
workspace. Highlight confidence level (`STRONGLY_SUPPORTED`) and the evidence
links behind it.
**VO:** "The root-cause agent can only say 'strongly supported' when the
required signals exist. Every claim links back to evidence."
**Overlay text:** `No evidence → no claims`

### SCENE 4 — The differentiator: gates + co-pilot chat (1:25–2:05)
**Visual:** Implementation authorization gate blocks the run. Open the review
co-pilot chat, paste a question: *"What are the risks of approving this code
change?"* — co-pilot answers from real state (diff, validation gaps).
Then approve with scoped grants.
**VO:** "Before any mutation, a human reviews — but not with a blind Approve
button. Our co-pilot answers questions from the actual diff, gate history, and
failure logs. Approval is scoped: implementation yes, publish still gated."
**Overlay text:** `HITL co-pilot · scoped authorization grants`
> *This is the scene that wins Taskmaster. Give it time.*

### SCENE 5 — Self-healing execution (2:05–2:35)
**Visual:** Short clip of validation failure routing back to the code-change
agent (retry counter overlay), then passing checks. If a live take is hard,
use the recorded event log scrolling.
**VO:** "When tests fail, SupportMaster doesn't halt — it feeds the failure
traces back into the code-change loop, up to three retries, then rolls back
with a receipt if nothing sticks."
**Overlay text:** `Fail → diagnose → retry ×3 → rollback receipt`

### SCENE 6 — Google Cloud proof (2:35–3:00)
**Visual:** Split screen or quick cut: `gcloud run services list` in terminal /
Cloud Console Cloud Run page showing **supportmaster** service RUNNING, then
browser on the live hosted URL submitting the same ticket.
**VO:** "The whole backend runs on Google Cloud — Cloud Run serving the app,
Secret Manager holding the Gemini key, built with Cloud Build."
**Overlay text:** `Cloud Run · Secret Manager · Cloud Build · Gemini 3.5 Flash`
> *Required by the rules — do not skip, keep ≥5 seconds on screen.*

### SCENE 7 — Close (3:00–3:20)
**Visual:** Architecture diagram (from README) full-screen for 5 seconds, then
back to the workspace showing the completed run.
**VO:** "SupportMaster earns autonomy with evidence, receipts, and gates.
Taskmaster track — because it finishes the whole job, safely."
**Overlay text:** `github.com/daryllrebeiro/SupportMaster`
> *End card ≤5 seconds. No thank-yous, no team intros (that lives in the written description).*

---

## Editing rules (per hackathon pro tips)

1. Cut every pause >0.5s; jump cuts between sentences.
2. Speed up any waiting section 2–4× (agent thinking, builds).
3. Never type on camera — all inputs pasted or pre-filled.
4. One strong example end-to-end (FIN-1847); don't repeat features.
5. On-screen text carries key points; VO stays short.
6. Export 1080p, upload public/unlisted on YouTube, add chapter timestamps.

## Recording order suggestion

Record scenes 6 → 1 → 2 → 3 → 4 → 5 → 7. The GCP proof shot is independent;
recording the live-run scenes early lets you re-record only what breaks.