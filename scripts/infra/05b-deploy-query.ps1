# =============================================================================
# 05b-deploy-query.ps1
# Deploy the query-tenants Cloud Function (gen2).
#
# Prerequisites: 05a-preflight-query.ps1 must pass first. No function dependency
# (query-tenants reads the precomputed current_hazard from Mongo; it does not
# call cox-ph-predict), so this only injects the Mongo connection.
#
# Memory: 256MiB. Pure stdlib + pymongo, a single find/count — no pandas/lifelines.
# Takes 2-4 minutes — Cloud Build installs deps and builds the container.
# Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "query-tenants"
$SOURCE     = "functions/query_tenants"

Write-Host "Deploying $FUNCTION to $REGION (2-4 minutes)..."

gcloud functions deploy $FUNCTION `
    --gen2 `
    --runtime=python311 `
    --region=$REGION `
    --source=$SOURCE `
    --entry-point=query_tenants `
    --trigger-http `
    --allow-unauthenticated `
    --memory=256MiB `
    --set-secrets="MONGODB_URI=MONGODB_URI:latest" `
    --set-env-vars="MONGODB_DB=tenant_mix" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 05c-get-url-query.ps1 to retrieve the URL + smoke test."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build logs in the GCP Console."
    exit 1
}
