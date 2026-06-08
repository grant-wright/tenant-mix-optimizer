# =============================================================================
# 06b-deploy-mongodb-mcp.ps1
# Deploy MongoDB's OFFICIAL MCP server to Cloud Run (compliance — see 06a header).
#
# Prerequisites: 06a-preflight-mongodb-mcp.ps1 must pass first.
#
# We deploy MongoDB's public image directly (no Dockerfile / Cloud Build):
#   mongodb/mongodb-mcp-server:latest
# Vendor-maintained, faster deploys, less to own. Cloud Run can pull public
# Docker Hub images. (If Docker Hub rate-limits bite, mirror the image into
# Artifact Registry and point --image there — not expected at hackathon volume.)
#
# Config is passed as CONTAINER ARGS (flag names are confirmed in the README;
# env-var spellings for httpHost/httpPort are not, so args are the safe path):
#   --transport http   -> streamable HTTP (not stdio) so the agent can reach it
#   --httpHost 0.0.0.0  -> bind all interfaces (default 127.0.0.1 fails on Run)
#   --httpPort 8080     -> match Cloud Run's --port
#   --readOnly          -> no writes/deletes (safe in the demo; we only read)
# The connection string comes from the EXISTING MONGODB_URI secret, mapped to the
# env var the server reads (MDB_MCP_CONNECTION_STRING) — never a plaintext value.
# --allow-unauthenticated matches the Day-2 no-auth MongoDB MCP / agent-tools MCP.
#
# SINGLE-INSTANCE PIN (--min-instances=1 --max-instances=1): the MongoDB MCP
# server is STATEFUL — it holds MCP session state in memory. Cloud Run defaults to
# many instances and round-robins requests, so a follow-up call (initialized /
# tools/call) can land on a different instance than `initialize` and fail with
# "session not found" (observed first deploy, 2026-06-08). Pinning to one always-
# warm instance keeps every request in the same process and sessions alive across
# agent turns. (Our own agent-tools MCP avoids this via FastMCP stateless_http=True;
# the vendor server has no such mode, so we pin instead.)
#
# KNOWN RISKS to watch (06c smoke test surfaces them before console wiring):
#  - Endpoint path may be /mcp or "/"; 06c probes /mcp first. (Confirmed /mcp.)
#  - DNS-rebinding / "421 Invalid Host header" bit FastMCP on Cloud Run (Stint 19);
#    MongoDB's server did NOT hit it (confirmed 2026-06-08).
#  - The image entrypoint may already pass defaults; if args conflict, we may need
#    --command. (Args worked as-is, 2026-06-08.)
# Expected duration: ~1-2 minutes (image pull, no build). Date: 2026-06-08
# =============================================================================

$PROJECT_ID = "rapid-agent-tenant-mix"
$REGION     = "australia-southeast1"
$SERVICE    = "mongodb-mcp"
$IMAGE      = "docker.io/mongodb/mongodb-mcp-server:latest"

Write-Host "Deploying $SERVICE from $IMAGE to Cloud Run in $REGION (1-2 minutes)..."

gcloud run deploy $SERVICE `
    --image=$IMAGE `
    --region=$REGION `
    --allow-unauthenticated `
    --port=8080 `
    --memory=512Mi `
    --min-instances=1 `
    --max-instances=1 `
    --set-secrets="MDB_MCP_CONNECTION_STRING=MONGODB_URI:latest" `
    --args="--transport,http,--httpHost,0.0.0.0,--httpPort,8080,--readOnly" `
    --project=$PROJECT_ID

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nDeploy succeeded. Run 06c-get-url-mongodb-mcp.ps1 to retrieve the /mcp URL + smoke test."
} else {
    Write-Host "`nDeploy failed. Check the Cloud Build/Run logs in the GCP Console, or:"
    Write-Host "  gcloud run services logs read $SERVICE --region=$REGION --project=$PROJECT_ID"
    exit 1
}
