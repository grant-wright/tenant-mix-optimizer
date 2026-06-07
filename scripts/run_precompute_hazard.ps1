# run_precompute_hazard.ps1
# ---------------------------------------------------------------------------
# Convenience runner for scripts/precompute_hazard.py — writes current_hazard
# onto every tenant doc by scoring each via the deployed cox_ph_predict.
#
# It does the one annoying bit for you: looks up the live cox-ph-predict URL
# from gcloud and hands it to the Python script as $env:COX_PH_PREDICT_URL, so
# you don't have to copy/paste the URL. MONGODB_URI is read from .env by the
# Python script itself.
#
# Prerequisite: cox-ph-predict must be deployed (it is — rev cox-ph-predict-00005).
# Re-run after new observations, a model refresh, or a reseed. Idempotent.
# Written 2026-06-07 (Day 8, Stint 20). Expected duration: ~1-2 min (78 calls,
# first one pays a cold start).
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"

# Resolve the repo .venv interpreter (falls back to PATH python).
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPy   = Join-Path $repoRoot ".venv\Scripts\python.exe"
$py       = if (Test-Path $venvPy) { $venvPy } else { "python" }
$script   = Join-Path $PSScriptRoot "precompute_hazard.py"

# Fetch the deployed cox-ph-predict URL to inject.
$COX_URL = gcloud functions describe cox-ph-predict `
    --gen2 --region=$REGION --project=$PROJECT_ID `
    --format="value(serviceConfig.uri)" 2>&1
if ($LASTEXITCODE -ne 0 -or $COX_URL -notmatch "^https://") {
    Write-Output "Could not read cox-ph-predict URL. Is it deployed? ($COX_URL)"
    exit 1
}

Write-Output "python   : $py"
Write-Output "cox URL  : $COX_URL"
Write-Output ""

$env:COX_PH_PREDICT_URL = $COX_URL
& $py $script
exit $LASTEXITCODE
