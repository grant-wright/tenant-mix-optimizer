# =============================================================================
# 02a-preflight-check.ps1
# Pre-deploy checks for cox-ph-predict Cloud Function.
# Verifies all prerequisites are in place before spending 3+ minutes on a build.
#
# Run before 02b-deploy.ps1. Fix any FAIL items before proceeding.
# Date: 2026-06-04
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$errors = 0

Write-Host "=== Pre-deploy checks for cox-ph-predict ===`n"

# 1. Serving bundle in place (lifelines-free cox_serving.pkl, NOT the full model)
$pkl = "functions/cox_ph_predict/model/cox_serving.pkl"
if (Test-Path $pkl) {
    $size = (Get-Item $pkl).Length
    Write-Host "$PASS  Serving bundle found ($pkl, $([math]::Round($size/1KB, 1)) KB)"
} else {
    Write-Host "$FAIL  Serving bundle missing: $pkl"
    Write-Host "       Fix: cp data_train/cox_serving.pkl functions/cox_ph_predict/model/cox_serving.pkl"
    $errors++
}

# 2. Source files present
foreach ($f in @("functions/cox_ph_predict/main.py", "functions/cox_ph_predict/requirements.txt")) {
    if (Test-Path $f) {
        Write-Host "$PASS  Source file found: $f"
    } else {
        Write-Host "$FAIL  Source file missing: $f"
        $errors++
    }
}

# 3. Secret exists in Secret Manager
$secret = gcloud secrets describe MONGODB_URI --project=$PROJECT_ID 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "$PASS  Secret MONGODB_URI exists in Secret Manager"
} else {
    Write-Host "$FAIL  Secret MONGODB_URI not found in Secret Manager"
    Write-Host "       Fix: run the secret creation step from 01-iam-setup.ps1"
    $errors++
}

# 4. IAM — compute SA has secretmanager.secretAccessor on the secret
$iamCheck = gcloud secrets get-iam-policy MONGODB_URI --project=$PROJECT_ID --format="value(bindings.members)" 2>&1
if ($iamCheck -match "compute@developer") {
    Write-Host "$PASS  Compute SA has secretAccessor on MONGODB_URI"
} else {
    Write-Host "$FAIL  Compute SA missing secretAccessor on MONGODB_URI"
    Write-Host "       Fix: re-run 01-iam-setup.ps1"
    $errors++
}

Write-Host ""
if ($errors -eq 0) {
    Write-Host "All checks passed. Ready to run 02b-deploy.ps1"
} else {
    Write-Host "$errors check(s) failed. Fix the issues above before deploying."
    exit 1
}
