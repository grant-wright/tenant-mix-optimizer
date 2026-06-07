# =============================================================================
# 05c-get-url-query.ps1
# Retrieve the deployed query-tenants URL and run smoke tests.
#
# Exercises the filter + the truncation signal. Assumes current_hazard has been
# precomputed (scripts/run_precompute_hazard.ps1), else the ranking is all-null.
#   1. {}                      -> everyone, top 20 by risk; check total_matched.
#   2. {hazard_above: 0.6}     -> the at-risk slice; check truncated/total_matched.
#   3. {status: active, limit: 3} -> tiny page to force truncated=true.
# Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "query-tenants"

$URL = gcloud functions describe $FUNCTION `
    --gen2 `
    --region=$REGION `
    --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not retrieve URL. Is the function deployed? Run 05b-deploy-query.ps1 first."
    exit 1
}

Write-Host "Function URL: $URL`n"

$cases = @(
    @{ name = "All tenants, top 20 by risk";        body = '{"filter": {}}' },
    @{ name = "At risk (hazard_above 0.6)";          body = '{"filter": {"hazard_above": 0.6}}' },
    @{ name = "Active, page size 3 (forces truncate)"; body = '{"filter": {"status": "active", "limit": 3}}' }
)

foreach ($c in $cases) {
    Write-Host "--- Smoke test: $($c.name) ---"
    try {
        $response = Invoke-RestMethod -Method POST -Uri $URL -ContentType "application/json" -Body $c.body
        Write-Host ("total_matched={0}  returned={1}  truncated={2}  next_offset={3}" -f `
            $response.total_matched, $response.returned, $response.truncated, $response.next_offset)
        $response.tenants | ForEach-Object {
            Write-Host ("  {0,-18} {1,-22} hazard={2}" -f $_.tenant_id, $_.name, $_.current_hazard)
        }
    } catch {
        Write-Host "Request failed: $_"
    }
    Write-Host ""
}
