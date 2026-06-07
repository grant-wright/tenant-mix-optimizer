# =============================================================================
# 04b-deploy-mcp.ps1
# Deploy the agent-tools MCP server to Cloud Run (container built from Dockerfile).
#
# Prerequisites: 04a-preflight-mcp.ps1 must pass first (in particular,
# recommend-intervention must be deployed — this script reads its URL and injects
# it as RECOMMEND_INTERVENTION_URL so the MCP tool can proxy to it).
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

# Fetch the recommend-intervention URL to inject (the MCP tool proxies to it).
$REC_URL = gcloud functions describe recommend-intervention `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -ne 0 -or $REC_URL -notmatch "^https://") {
    Write-Host "Could not read recommend-intervention URL. Run 04a-preflight-mcp.ps1 first."
    exit 1
}
Write-Host "Injecting RECOMMEND_INTERVENTION_URL = $REC_URL"
Write-Host "Deploying $SERVICE to Cloud Run in $REGION (3-5 minutes)..."

gcloud run deploy $SERVICE `
    --source=$SOURCE `
    --region=$REGION `
    --allow-unauthenticated `
    --memory=512Mi `
    --set-env-vars="RECOMMEND_INTERVENTION_URL=$REC_URL" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 04c-get-url-mcp.ps1 to retrieve the /mcp URL + smoke test."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build logs in the GCP Console."
    exit 1
}
