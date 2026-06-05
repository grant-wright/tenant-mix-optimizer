# =============================================================================
# 02c-get-url.ps1
# Retrieve the deployed cox-ph-predict function URL and run a smoke test.
# Date: 2026-06-04
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "cox-ph-predict"

$URL = gcloud functions describe $FUNCTION `
    --gen2 `
    --region=$REGION `
    --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not retrieve URL. Is the function deployed? Run 02b-deploy.ps1 first."
    exit 1
}

Write-Host "Function URL: $URL"
Write-Host ""
Write-Host "Smoke test (Atelier Margot - TENANT_DEMO_001):"
Write-Host "---"

$body = '{"tenant_id": "TENANT_DEMO_001"}'
$response = Invoke-RestMethod -Method POST -Uri $URL `
    -ContentType "application/json" `
    -Body $body

$response | ConvertTo-Json -Depth 5
