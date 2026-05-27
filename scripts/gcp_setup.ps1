# GCP project setup for Tenant Mix Optimizer
# Run AFTER: gcloud auth login && gcloud auth application-default login
# ------------------------------------------------------------------

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$BILLING_ACCOUNT = ""  # Fill in after: gcloud billing accounts list

# ── 1. Create project ──────────────────────────────────────────────
gcloud projects create $PROJECT_ID --name="Rapid Agent Tenant Mix"
gcloud config set project $PROJECT_ID

# ── 2. Link billing ────────────────────────────────────────────────
# First list your billing accounts to find the ID:
#   gcloud billing accounts list
# Then uncomment and fill in:
# gcloud billing projects link $PROJECT_ID --billing-account=$BILLING_ACCOUNT

# ── 3. Enable APIs needed for Day 2 ────────────────────────────────
gcloud services enable `
    aiplatform.googleapis.com `
    dialogflow.googleapis.com `
    cloudfunctions.googleapis.com `
    run.googleapis.com `
    secretmanager.googleapis.com `
    cloudbuild.googleapis.com `
    artifactregistry.googleapis.com `
    --project=$PROJECT_ID

Write-Host "`n--- Verify: project exists ---"
gcloud projects describe $PROJECT_ID

Write-Host "`n--- Verify: APIs enabled ---"
gcloud services list --enabled --project=$PROJECT_ID --filter="name:aiplatform OR name:dialogflow OR name:cloudfunctions OR name:secretmanager"
