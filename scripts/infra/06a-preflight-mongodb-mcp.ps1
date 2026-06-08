# =============================================================================
# 06a-preflight-mongodb-mcp.ps1
# Pre-deploy checks for MongoDB's OFFICIAL MCP server on Cloud Run.
#
# WHY THIS EXISTS (compliance): the hackathon rules require the agent to
# "integrate a Partner Entity's MCP server" — for the MongoDB track that means
# MongoDB's own MCP server, not our custom agent-tools MCP. We had it working on
# Day 2 via ngrok; ngrok died (Stint 19) and was never restored. This redeploys
# it durably on Cloud Run. It coexists with query_tenants (curated paved path)
# and gives the agent ad-hoc read access to the DB + the observations collection.
# See ../../decisions.md 2026-06-08.
#
# Unlike 04a, there is NO local source to build — we deploy MongoDB's official
# public image (mongodb/mongodb-mcp-server:latest) directly. So this preflight
# checks the platform prerequisites instead: Cloud Run API + the MONGODB_URI
# secret (reused from cox-ph-predict) + Secret Manager access.
#
# Run before 06b-deploy-mongodb-mcp.ps1. Fix any FAIL before proceeding.
# Expected duration: ~10-20s. Date: 2026-06-08
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$SECRET     = "MONGODB_URI"
$PASS = "[PASS]"
$FAIL = "[FAIL]"
$errors = 0

Write-Host "=== Pre-deploy checks for MongoDB official MCP server ===`n"

# 1. Cloud Run API enabled (we deploy a Run service directly)
$runEnabled = gcloud services list --enabled --project=$PROJECT_ID `
    --filter="config.name=run.googleapis.com" --format="value(config.name)" 2>&1
if ($runEnabled -match "run.googleapis.com") {
    Write-Host "$PASS  Cloud Run API (run.googleapis.com) is enabled"
} else {
    Write-Host "$FAIL  Cloud Run API not enabled"
    Write-Host "       Fix: gcloud services enable run.googleapis.com --project=$PROJECT_ID"
    $errors++
}

# 2. Secret Manager API enabled
$smEnabled = gcloud services list --enabled --project=$PROJECT_ID `
    --filter="config.name=secretmanager.googleapis.com" --format="value(config.name)" 2>&1
if ($smEnabled -match "secretmanager.googleapis.com") {
    Write-Host "$PASS  Secret Manager API (secretmanager.googleapis.com) is enabled"
} else {
    Write-Host "$FAIL  Secret Manager API not enabled"
    Write-Host "       Fix: gcloud services enable secretmanager.googleapis.com --project=$PROJECT_ID"
    $errors++
}

# 3. The MONGODB_URI secret exists (the MCP server reads it as its connection
#    string — see 06b's --set-secrets). Reused from cox-ph-predict, so it should
#    already be present; this is a guard against a fresh project / deleted secret.
$secretName = gcloud secrets describe $SECRET --project=$PROJECT_ID `
    --format="value(name)" 2>&1
if ($LASTEXITCODE -eq 0 -and $secretName -match $SECRET) {
    Write-Host "$PASS  Secret '$SECRET' exists in Secret Manager"
} else {
    Write-Host "$FAIL  Secret '$SECRET' not found"
    Write-Host "       Create it: gcloud secrets create $SECRET --replication-policy=automatic --project=$PROJECT_ID"
    Write-Host "       Then add the Atlas URI as a version (from .env MONGODB_URI)."
    $errors++
}

Write-Host ""
Write-Host "Reminder (not auto-checked): Atlas Network Access must allow Cloud Run egress"
Write-Host "  (0.0.0.0/0 is already set — cox-ph-predict reads Atlas from this region)."
Write-Host ""
if ($errors -eq 0) {
    Write-Host "All checks passed. Ready to run 06b-deploy-mongodb-mcp.ps1"
} else {
    Write-Host "$errors check(s) failed. Fix the issues above before deploying."
    exit 1
}
