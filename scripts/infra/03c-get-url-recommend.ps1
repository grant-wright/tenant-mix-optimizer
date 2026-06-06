# =============================================================================
# 03c-get-url-recommend.ps1
# Retrieve the deployed recommend-intervention URL and run a smoke test.
#
# Smoke-tests two demo tenants that exercise both layers of the design:
#   - Atelier Margot (DEMO_001): ambient 'renew', alert layer escalates to
#     renegotiate (escalated_by = [enquiry:rent_relief]).
#   - Pancho's Tacos (DEMO_002): ambient already 'renegotiate'; suggested_terms
#     cap_binds = true (rent alone insufficient -> the replace conversation).
# Compare against reference/demo-cast-scoring-analysis.md (planning repo).
# Date: 2026-06-06
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "recommend-intervention"

$URL = gcloud functions describe $FUNCTION `
    --gen2 `
    --region=$REGION `
    --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not retrieve URL. Is the function deployed? Run 03b-deploy-recommend.ps1 first."
    exit 1
}

Write-Host "Function URL: $URL`n"

foreach ($t in @(
    @{ id = "TENANT_DEMO_001"; name = "Atelier Margot (alert-layer escalation)" },
    @{ id = "TENANT_DEMO_002"; name = "Pancho's Tacos (ambient catch + cap binds)" }
)) {
    Write-Host "--- Smoke test: $($t.name) — $($t.id) ---"
    $body = "{`"tenant_id`": `"$($t.id)`"}"
    try {
        $response = Invoke-RestMethod -Method POST -Uri $URL -ContentType "application/json" -Body $body
        $response | ConvertTo-Json -Depth 6
    } catch {
        Write-Host "Request failed: $_"
    }
    Write-Host ""
}
