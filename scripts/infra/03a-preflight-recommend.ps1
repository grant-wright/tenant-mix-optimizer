# =============================================================================
# 03a-preflight-recommend.ps1
# Pre-deploy checks for the recommend-intervention Cloud Function.
# Verifies prerequisites before spending build time.
#
# Unlike cox-ph-predict, this function has NO model artefact. It DOES depend on
# cox-ph-predict being deployed (it calls it over HTTP), so this preflight also
# confirms cox is live and captures its URL. IAM + the MONGODB_URI secret are
# shared with cox (set up in 01-iam-setup.ps1) — no new IAM step needed.
#
# Run before 03b-deploy-recommend.ps1. Fix any FAIL items before proceeding.
# Date: 2026-06-06
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$errors = 0

Write-Host "=== Pre-deploy checks for recommend-intervention ===`n"

# 1. Source files present (no model artefact for this function)
foreach ($f in @("functions/recommend_intervention/main.py", "functions/recommend_intervention/requirements.txt")) {
    if (Test-Path $f) {
        Write-Host "$PASS  Source file found: $f"
    } else {
        Write-Host "$FAIL  Source file missing: $f"
        $errors++
    }
}

# 2. Secret exists in Secret Manager (shared with cox-ph-predict)
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

# 4. Dependency — cox-ph-predict is deployed and we can read its URL
$COX_URL = gcloud functions describe cox-ph-predict `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -eq 0 -and $COX_URL -match "^https://") {
    Write-Host "$PASS  Dependency cox-ph-predict is deployed"
    Write-Host "       COX_PH_PREDICT_URL = $COX_URL"
} else {
    Write-Host "$FAIL  cox-ph-predict not found — deploy it first (02b-deploy.ps1)"
    Write-Host "       recommend-intervention calls it over HTTP and cannot run without it."
    $errors++
}

Write-Host ""
if ($errors -eq 0) {
    Write-Host "All checks passed. Ready to run 03b-deploy-recommend.ps1"
} else {
    Write-Host "$errors check(s) failed. Fix the issues above before deploying."
    exit 1
}
