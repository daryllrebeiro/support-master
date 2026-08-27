# SupportMaster on Google Cloud

This guide deploys SupportMaster to **Cloud Run** with the Gemini API key in
**Secret Manager** — satisfying the hackathon requirement for "at least one
Google Cloud service" and giving you a public hosted URL plus an easy video
proof shot.

## Services used

| Google Cloud service | Role in SupportMaster |
|---|---|
| Cloud Run | Hosts the web UI, model picker, workspace, and API |
| Secret Manager | Stores `GOOGLE_API_KEY` (never baked into the image) |
| Cloud Build | Builds the container image from the repo |
| Artifact Registry | Stores the built image |

## Prerequisites

1. A GCP project with billing enabled.
2. `gcloud` CLI installed and authenticated:
   ```powershell
   gcloud auth login
   gcloud config set project <your-project-id>
   ```
3. A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
   exported in your shell: `$env:GOOGLE_API_KEY = "..."`

## One-command deploy

```powershell
.\scripts\deploy-cloudrun.ps1 -ProjectId <your-project-id> -Region us-central1
```

The script enables APIs, creates/updates the secret, builds via Cloud Build,
grants the runtime service account secret access, deploys to Cloud Run, and
prints the hosted URL.

## Manual deploy (equivalent steps)

```powershell
# 1. Enable APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
    secretmanager.googleapis.com artifactregistry.googleapis.com

# 2. Store the API key in Secret Manager
"GOOGLE_API_KEY" | gcloud secrets create google-api-key --data-file=- --replication-policy=automatic

# 3. Build the image
gcloud builds submit --tag us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/supportmaster:latest

# 4. Deploy
gcloud run deploy supportmaster `
    --image us-central1-docker.pkg.dev/<project>/cloud-run-source-deploy/supportmaster:latest `
    --region us-central1 `
    --allow-unauthenticated `
    --set-secrets GOOGLE_API_KEY=google-api-key:latest `
    --set-env-vars SUPPORTMASTER_AUTH_MODE=OPTIONAL,SUPPORTMASTER_MODEL=gemini-3.5-flash,PORT=8001 `
    --port 8001
```

## After deployment — capture these for the submission form

1. **Hosted project URL**: printed by the deploy script, or run
   `gcloud run services describe supportmaster --region us-central1 --format="value(status.url)"`.
   Paste it into the Devpost form. The demo auth mode is OPTIONAL; if you
   switch to `REQUIRED`, put the demo credentials in the testing instructions.
2. **Video proof shot (~5 seconds)**: show either
   - `gcloud run services list`, or
   - the Cloud Console → Cloud Run → supportmaster page,
   
   while the app is live. This is the required "proof your backend runs on
   Google Cloud".
3. **Smoke test**: open `<url>/health/live` and `<url>/workspace` to confirm.

## Security notes for judges

- The Gemini key lives only in Secret Manager, mounted at request time by the
  Cloud Run runtime service account (`secretAccessor` role).
- The container runs as non-root user 8888.
- `SUPPORTMASTER_AUTH_MODE=OPTIONAL` is set for judge convenience; production
  deployments should use `REQUIRED` with scoped API keys (see README Phase 11).
- All mutations remain behind workflow authorization gates regardless of auth mode.

## Cost control

The deployment uses `--min-instances 0 --max-instances 2` so idle cost is zero
and worst-case spend stays bounded during judging week.