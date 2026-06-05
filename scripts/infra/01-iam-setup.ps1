# =============================================================================
# 01-iam-setup.ps1
# IAM grants required to deploy Cloud Functions gen2 in this project.
#
# Background: GCP changed the Cloud Build default service account in July 2024.
# New projects use the Compute Engine default SA as the build SA, not the legacy
# Cloud Build SA (PROJECT_NUMBER@cloudbuild.gserviceaccount.com). All build and
# secret-access roles must be granted to the compute default SA.
#
# Run once per project. Safe to re-run (add-iam-policy-binding is idempotent).
# Prerequisites: gcloud auth login && gcloud config set project rapid-agent-tenant-mix
# Date: 2026-06-04
# =============================================================================

$PROJECT_ID     = "rapid-agent-tenant-mix"
$PROJECT_NUMBER = "499995218644"
$BUILD_SA       = "$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

Write-Host "Granting Cloud Build roles to compute default SA: $BUILD_SA"

# Build-time permissions
gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$BUILD_SA" `
    --role="roles/cloudbuild.builds.builder" `
    --condition=None

gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$BUILD_SA" `
    --role="roles/logging.logWriter" `
    --condition=None

gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$BUILD_SA" `
    --role="roles/artifactregistry.writer" `
    --condition=None

gcloud projects add-iam-policy-binding $PROJECT_ID `
    --member="serviceAccount:$BUILD_SA" `
    --role="roles/storage.objectViewer" `
    --condition=None

# Runtime permission — required for --set-secrets to mount Secret Manager values
Write-Host "Granting Secret Manager accessor to runtime SA: $BUILD_SA"
gcloud secrets add-iam-policy-binding MONGODB_URI `
    --project=$PROJECT_ID `
    --member="serviceAccount:$BUILD_SA" `
    --role="roles/secretmanager.secretAccessor"

Write-Host "`nIAM setup complete."
