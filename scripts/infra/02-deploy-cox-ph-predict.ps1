# =============================================================================
# ⚠️  SUPERSEDED — DO NOT RUN. Kept as an audit artifact only.
#
# This monolithic deploy script was replaced on 2026-06-04 by the fine-grained
# sequence below, because running the whole deploy in one step hung repeatedly
# and gave no signal about which sub-step failed:
#       02a-preflight-check.ps1  →  02b-deploy.ps1  →  02c-get-url.ps1
# Run those instead. See session-log Stint 11 + the fine-grained-ops-steps note.
# =============================================================================
#
# 02-deploy-cox-ph-predict.ps1
# Deploy the cox-ph-predict Cloud Function (gen2, Python 3.11).
#
# Prerequisites:
#   - Run 01-iam-setup.ps1 first (once per project)
#   - Copy the model bundle before deploying:
#       cp data_train/cox_model.pkl functions/cox_ph_predict/model/cox_model.pkl
#   - MONGODB_URI secret must exist in Secret Manager (created by 01-iam-setup or manually)
#   - gcloud auth login && gcloud config set project rapid-agent-tenant-mix
# Date: 2026-06-04
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$FUNCTION   = "cox-ph-predict"
$SOURCE     = "functions/cox_ph_predict"

Write-Host "Deploying $FUNCTION to $REGION..."

gcloud functions deploy $FUNCTION `
    --gen2 `
    --runtime=python311 `
    --region=$REGION `
    --source=$SOURCE `
    --entry-point=cox_ph_predict `
    --trigger-http `
    --allow-unauthenticated `
    --set-secrets="MONGODB_URI=MONGODB_URI:latest" `
    --set-env-vars="MONGODB_DB=tenant_mix" `
    --project=$PROJECT_ID

Write-Host "`nDeploy complete. Get the URL with:"
Write-Host "  gcloud functions describe $FUNCTION --gen2 --region=$REGION --project=$PROJECT_ID --format='value(serviceConfig.uri)'"
