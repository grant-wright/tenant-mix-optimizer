# =============================================================================
# 06c-get-url-mongodb-mcp.ps1
# Retrieve the deployed MongoDB MCP server URL and smoke-test the endpoint.
#
# Prints the Cloud Run URL + the MCP endpoint to register in Agent Designer
# (Add tool -> MCP Server -> this URL, Authentication: None).
#
# Smoke test: sends an MCP `initialize` handshake and checks for a valid MCP
# response (serverInfo / protocolVersion). Streamable HTTP can answer as JSON or
# SSE, so we string-match the raw body rather than parse it. This is the gate
# that caught the Cloud Run host-header bug in Stint 19 BEFORE console wiring —
# do not skip it.
#
# The MongoDB MCP server's streamable endpoint path isn't documented, so we try
# /mcp first and fall back to the service root.
#
# SCOPE OF THIS CHECK: liveness only — a single `initialize` POST. It proves the
# server is up and speaks MCP. It does NOT prove session continuity or a real DB
# read (a stateful MCP session needs full Streamable-HTTP handling — see
# ../../../mcp-servers-on-cloud-run.md §6). For deep verification use the OFFICIAL
# MCP Inspector CLI (a real client), not a hand-rolled probe:
#   npx -y @modelcontextprotocol/inspector --cli "<URL>/mcp" --transport http --method tools/list
#   npx -y @modelcontextprotocol/inspector --cli "<URL>/mcp" --transport http `
#       --method tools/call --tool-name list-databases
# Expected duration: ~5-10s. Date: 2026-06-08
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$SERVICE    = "mongodb-mcp"

$URL = gcloud run services describe $SERVICE `
    --region=$REGION `
    --project=$PROJECT_ID `
    --format="value(status.url)" 2>&1

if ($LASTEXITCODE -ne 0 -or $URL -notmatch "^https://") {
    Write-Host "Could not retrieve URL. Is the service deployed? Run 06b-deploy-mongodb-mcp.ps1 first."
    exit 1
}

Write-Host "Cloud Run URL : $URL`n"

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

$candidates = @("$URL/mcp", "$URL/")
$passed = $false
foreach ($endpoint in $candidates) {
    Write-Host "--- Smoke test: MCP initialize -> $endpoint ---"
    try {
        $resp = Invoke-WebRequest -Method POST -Uri $endpoint `
            -ContentType "application/json" `
            -Headers @{ "Accept" = "application/json, text/event-stream" } `
            -Body $initBody -UseBasicParsing
        if ($resp.Content -match '"serverInfo"' -or $resp.Content -match '"protocolVersion"' -or $resp.Content -match "mongodb") {
            Write-Host "[PASS] Server responded to initialize. Register THIS endpoint:"
            Write-Host "       $endpoint   (MCP Server, Authentication: None)"
            $passed = $true
            break
        } else {
            Write-Host "[WARN] HTTP $($resp.StatusCode) but body did not look like an MCP server. Raw body:"
            Write-Host $resp.Content
        }
    } catch {
        Write-Host "[..]   No luck at $endpoint : $_"
    }
}

if (-not $passed) {
    Write-Host "`n[FAIL] No endpoint answered the MCP handshake."
    Write-Host "       If you saw '421 Invalid Host header', it's the DNS-rebinding guard (Stint 19) —"
    Write-Host "       check the README/logs for an allowed-hosts setting."
    Write-Host "       Logs: gcloud run services logs read $SERVICE --region=$REGION --project=$PROJECT_ID"
    exit 1
}
