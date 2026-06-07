# =============================================================================
# 04b-deploy-mcp.ps1
# Deploy the agent-tools MCP server to Cloud Run (container built from Dockerfile).
#
# Prerequisites: 04a-preflight-mcp.ps1 must pass first (in particular,
# query-tenants AND recommend-intervention must be deployed — this script reads
# their URLs and injects them as QUERY_TENANTS_URL / RECOMMEND_INTERVENTION_URL
# so the MCP tools can proxy to them).
#
# NOTE: --set-env-vars REPLACES the service's whole env set, so BOTH URLs must be
# passed here every time (omitting one would unset it). Add future tool URLs here.
#
# --source builds the container from services/agent_tools_mcp/Dockerfile via
# Cloud Build, then deploys it. --allow-unauthenticated matches the Day-2 no-auth
# MongoDB MCP pattern (auth is a later hardening pass).
# Expected duration: ~3-5 minutes (Cloud Build builds + pushes the image).
# Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$SERVICE    = "agent-tools-mcp"
$SOURCE     = "services/agent_tools_mcp"

# Fetch both Cloud Function URLs to inject (each MCP tool proxies to one).
$REC_URL = gcloud functions describe recommend-intervention `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -ne 0 -or $REC_URL -notmatch "^https://") {
    Write-Host "Could not read recommend-intervention URL. Run 04a-preflight-mcp.ps1 first."
    exit 1
}
$QRY_URL = gcloud functions describe query-tenants `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -ne 0 -or $QRY_URL -notmatch "^https://") {
    Write-Host "Could not read query-tenants URL. Run 04a-preflight-mcp.ps1 first."
    exit 1
}
Write-Host "Injecting QUERY_TENANTS_URL          = $QRY_URL"
Write-Host "Injecting RECOMMEND_INTERVENTION_URL = $REC_URL"
Write-Host "Deploying $SERVICE to Cloud Run in $REGION (3-5 minutes)..."

gcloud run deploy $SERVICE `
    --source=$SOURCE `
    --region=$REGION `
    --allow-unauthenticated `
    --memory=512Mi `
    --set-env-vars="QUERY_TENANTS_URL=$QRY_URL,RECOMMEND_INTERVENTION_URL=$REC_URL" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 04c-get-url-mcp.ps1 to retrieve the /mcp URL + smoke test."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build logs in the GCP Console."
    exit 1
}
