# =============================================================================
# 04c-get-url-mcp.ps1
# Retrieve the deployed agent-tools MCP server URL and smoke-test the endpoint.
#
# Prints the Cloud Run URL and the /mcp endpoint to register in Agent Builder
# (Add tool -> MCP Server -> this /mcp URL, Authentication: None).
#
# Smoke test: sends an MCP `initialize` request to /mcp and checks the server
# responds as our "agent-tools" server. Streamable HTTP can answer as JSON or
# SSE, so we string-match the raw body rather than parse it.
# Expected duration: ~5-10s. Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$SERVICE    = "agent-tools-mcp"

$URL = gcloud run services describe $SERVICE `
    --region=$REGION `
    --project=$PROJECT_ID `
    --format="value(status.url)" 2>&1

if ($LASTEXITCODE -ne 0 -or $URL -notmatch "^https://") {
    Write-Host "Could not retrieve URL. Is the service deployed? Run 04b-deploy-mcp.ps1 first."
    exit 1
}

$MCP_URL = "$URL/mcp"
Write-Host "Cloud Run URL : $URL"
Write-Host "MCP endpoint  : $MCP_URL   <-- register THIS in Agent Builder (MCP Server, no auth)`n"

Write-Host "--- Smoke test: MCP initialize handshake ---"
$initBody = @{
    jsonrpc = "2.0"
    id      = 1
    method  = "initialize"
    params  = @{
        protocolVersion = "2025-06-18"
        capabilities    = @{}
        clientInfo      = @{ name = "smoke"; version = "0" }
    }
} | ConvertTo-Json -Depth 6

try {
    $resp = Invoke-WebRequest -Method POST -Uri $MCP_URL `
        -ContentType "application/json" `
        -Headers @{ "Accept" = "application/json, text/event-stream" } `
        -Body $initBody -UseBasicParsing
    if ($resp.Content -match "agent-tools" -or $resp.Content -match '"serverInfo"') {
        Write-Host "[PASS] Server responded to initialize as 'agent-tools'."
    } else {
        Write-Host "[WARN] Got HTTP $($resp.StatusCode) but body did not name the server. Raw body:"
        Write-Host $resp.Content
    }
} catch {
    Write-Host "[FAIL] initialize request failed: $_"
    Write-Host "       Check the Cloud Run logs: gcloud run services logs read $SERVICE --region=$REGION --project=$PROJECT_ID"
    exit 1
}
