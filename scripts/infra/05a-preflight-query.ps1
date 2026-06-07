# =============================================================================
# 05a-preflight-query.ps1
# Pre-deploy checks for the query-tenants Cloud Function (gen2).
#
# query-tenants reads tenants from Mongo and returns a risk-ranked, paged list.
# It does NOT call cox-ph-predict (it reads the precomputed current_hazard field
# written by scripts/precompute_hazard.py), so — unlike recommend — there is no
# function dependency to check here. It DOES need the shared MONGODB_URI secret
# and the compute SA's access to it (set up in 01-iam-setup.ps1).
#
# Run before 05b-deploy-query.ps1. Fix any FAIL items before proceeding.
# Expected duration: ~10-15s. Date: 2026-06-07
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$errors = 0

Write-Host "=== Pre-deploy checks for query-tenants ===`n"

# 1. Source files present (no model artefact, no cox dependency)
foreach ($f in @("functions/query_tenants/main.py", "functions/query_tenants/requirements.txt")) {
    if (Test-Path $f) {
        Write-Host "$PASS  Source file found: $f"
    } else {
        Write-Host "$FAIL  Source file missing: $f"
        $errors++
    }
}

# 2. Secret exists in Secret Manager (shared with cox + recommend)
gcloud secrets describe MONGODB_URI --project=$PROJECT_ID 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "$PASS  Secret MONGODB_URI exists in Secret Manager"
} else {
    Write-Host "$FAIL  Secret MONGODB_URI not found"
    Write-Host "       Fix: run the secret creation step from 01-iam-setup.ps1"
    $errors++
}

# 3. IAM — compute SA has secretmanager.secretAccessor on the secret
$iamCheck = gcloud secrets get-iam-policy MONGODB_URI --project=$PROJECT_ID --format="value(bindings.members)" 2>&1
if ($iamCheck -match "compute@developer") {
    Write-Host "$PASS  Compute SA has secretAccessor on MONGODB_URI"
} else {
    Write-Host "$FAIL  Compute SA missing secretAccessor on MONGODB_URI"
    Write-Host "       Fix: re-run 01-iam-setup.ps1"
    $errors++
}

# 4. Reminder — current_hazard should be populated so the ranking is meaningful.
Write-Host "$PASS  (reminder) run scripts/run_precompute_hazard.ps1 before/after deploy"
Write-Host "       so tenants have current_hazard — query-tenants ranks on it."

Write-Host ""
if ($errors -eq 0) {
    Write-Host "All checks passed. Ready to run 05b-deploy-query.ps1"
} else {
    Write-Host "$errors check(s) failed. Fix the issues above before deploying."
    exit 1
}
