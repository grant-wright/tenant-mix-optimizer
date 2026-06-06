# =============================================================================
# 03b-deploy-recommend.ps1
# Deploy the recommend-intervention Cloud Function (gen2).
#
# Prerequisites: 03a-preflight-recommend.ps1 must pass first (in particular,
# cox-ph-predict must already be deployed — this script reads its URL and injects
# it as COX_PH_PREDICT_URL so recommend-intervention can call it).
#
# Memory: 256MiB. This function has no pandas/lifelines (just functions-framework
# + pymongo + stdlib), so it does not need the 512MiB cox uses.
# Takes 2-4 minutes — Cloud Build installs deps and builds the container.
# Date: 2026-06-06
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "recommend-intervention"
$SOURCE     = "functions/recommend_intervention"

# Fetch the cox-ph-predict URL to inject (recommend calls it over HTTP).
$COX_URL = gcloud functions describe cox-ph-predict `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -ne 0 -or $COX_URL -notmatch "^https://") {
    Write-Host "Could not read cox-ph-predict URL. Run 03a-preflight-recommend.ps1 first."
    exit 1
}
Write-Host "Injecting COX_PH_PREDICT_URL = $COX_URL"
Write-Host "Deploying $FUNCTION to $REGION (2-4 minutes)..."

gcloud functions deploy $FUNCTION `
    --gen2 `
    --runtime=python311 `
    --region=$REGION `
    --source=$SOURCE `
    --entry-point=recommend_intervention `
    --trigger-http `
    --allow-unauthenticated `
    --memory=256MiB `
    --set-secrets="MONGODB_URI=MONGODB_URI:latest" `
    --set-env-vars="MONGODB_DB=tenant_mix,COX_PH_PREDICT_URL=$COX_URL" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 03c-get-url-recommend.ps1 to retrieve the URL + smoke test."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build logs in the GCP Console."
    exit 1
}
