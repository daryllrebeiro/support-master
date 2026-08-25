# SupportMaster demo runbook

## Prepare

Create `.venv`, install `requirements.txt`, and copy `.env.example` to `.env`.
The deterministic demo does not need `GOOGLE_API_KEY`.

## Preflight

Run `.\scripts\demo.ps1 check`. This executes the offline quality pack and
local release checks. It exits non-zero if a safety or regression check fails.

## Golden path

Run `.\scripts\demo.ps1 run`. Explain the output in this order:

1. The vendor-neutral case is normalized.
2. Investigation preserves tenant context and identifies evidence gaps.
3. The resolution gate refuses to claim an unverified fix.
4. Every stage is represented by an auditable check.

### Verified autonomous fix (one command)

Run `.\scripts\golden-path.ps1` to show the real execution layer against
`demo-target/`. Narrate in this order:

1. The IMPLEMENTATION authorization grant is verified before anything runs.
2. Git preflight confirms a clean baseline; the scoped patch touches only
   `invoice_export.py`.
3. The regression tests run for real and pass after the streaming fix.
4. The change is committed on branch `supportmaster/sup-golden`, and every
   operation prints a JSON receipt (CODE_CHANGE, TEST_EXECUTION, DEMO_COMMIT).

This command is offline-safe: no API key, no external mutation, no push.

## Workspace

Run `.\scripts\demo.ps1 serve` and open
http://127.0.0.1:8001/workspace. The workspace shows the case timeline, gate
statuses, and next action.

## Container path

Run `docker compose up --build` and open the same workspace URL. The container
uses optional authentication for local demonstration; production deployments
should provide required API-key authentication.

## Close

End with the safety message: SupportMaster can investigate and prepare work
autonomously, but implementation, publication, deployment, and closure remain
evidence- and authorization-gated.
