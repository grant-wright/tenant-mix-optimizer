# =============================================================================
# 02b-deploy.ps1
# Deploy cox-ph-predict Cloud Function gen2.
#
# Prerequisites: 01-iam-setup.ps1 and 02a-preflight-check.ps1 must pass first.
# Takes 2-4 minutes — Cloud Build installs Python dependencies and builds container.
# Date: 2026-06-04
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "cox-ph-predict"
$SOURCE     = "functions/cox_ph_predict"

Write-Host "Deploying $FUNCTION to $REGION (2-4 minutes)..."

gcloud functions deploy $FUNCTION `
    --gen2 `
    --runtime=python311 `
    --region=$REGION `
    --source=$SOURCE `
    --entry-point=cox_ph_predict `
    --trigger-http `
    --allow-unauthenticated `
    --memory=512MiB `
    --set-secrets="MONGODB_URI=MONGODB_URI:latest" `
    --set-env-vars="MONGODB_DB=tenant_mix" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 02c-get-url.ps1 to retrieve the endpoint URL."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build logs in the GCP Console."
    exit 1
}
