# =============================================================================
# 04a-preflight-mcp.ps1
# Pre-deploy checks for the agent-tools MCP server (Cloud Run).
#
# This server is a thin MCP proxy in front of our custom Cloud Functions. v1
# proxies query-tenants + recommend-intervention, so this preflight confirms
# BOTH functions are live and captures their URLs (injected as QUERY_TENANTS_URL
# and RECOMMEND_INTERVENTION_URL at deploy). Also confirms the Cloud Run API is
# enabled (Functions used it indirectly, but we deploy a Run service directly here).
#
# Run before 04b-deploy-mcp.ps1. Fix any FAIL items before proceeding.
# Expected duration: ~10-20s. Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$errors = 0

Write-Host "=== Pre-deploy checks for agent-tools MCP server ===`n"

# 1. Source files present
foreach ($f in @(
    "services/agent_tools_mcp/main.py",
    "services/agent_tools_mcp/requirements.txt",
    "services/agent_tools_mcp/Dockerfile"
)) {
    if (Test-Path $f) {
        Write-Host "$PASS  Source file found: $f"
    } else {
        Write-Host "$FAIL  Source file missing: $f"
        $errors++
    }
}

# 2. Cloud Run API enabled
$runEnabled = gcloud services list --enabled --project=$PROJECT_ID `
    --filter="config.name=run.googleapis.com" --format="value(config.name)" 2>&1
if ($runEnabled -match "run.googleapis.com") {
    Write-Host "$PASS  Cloud Run API (run.googleapis.com) is enabled"
} else {
    Write-Host "$FAIL  Cloud Run API not enabled"
    Write-Host "       Fix: gcloud services enable run.googleapis.com --project=$PROJECT_ID"
    $errors++
}

# 3. Dependency — recommend-intervention is deployed and we can read its URL
$REC_URL = gcloud functions describe recommend-intervention `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -eq 0 -and $REC_URL -match "^https://") {
    Write-Host "$PASS  Dependency recommend-intervention is deployed"
    Write-Host "       RECOMMEND_INTERVENTION_URL = $REC_URL"
} else {
    Write-Host "$FAIL  recommend-intervention not found — deploy it first (03b-deploy-recommend.ps1)"
    Write-Host "       The MCP server proxies to it and cannot serve the tool without it."
    $errors++
}

# 4. Dependency — query-tenants is deployed and we can read its URL
$QRY_URL = gcloud functions describe query-tenants `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -eq 0 -and $QRY_URL -match "^https://") {
    Write-Host "$PASS  Dependency query-tenants is deployed"
    Write-Host "       QUERY_TENANTS_URL = $QRY_URL"
} else {
    Write-Host "$FAIL  query-tenants not found — deploy it first (05b-deploy-query.ps1)"
    Write-Host "       The MCP server proxies to it and cannot serve the tool without it."
    $errors++
}

Write-Host ""
if ($errors -eq 0) {
    Write-Host "All checks passed. Ready to run 04b-deploy-mcp.ps1"
} else {
    Write-Host "$errors check(s) failed. Fix the issues above before deploying."
    exit 1
}
