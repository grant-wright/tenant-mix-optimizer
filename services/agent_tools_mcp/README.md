# agent-tools MCP server

The single MCP home for the tenant-mix optimizer's **custom** agent tools
(Option A). The agent (built in Agent Designer) only supports MCP / Search /
Data Store tool types — there is no OpenAPI tool type on that surface — so our
deterministic Cloud Functions reach the agent through MCP.

Each tool is a **thin proxy** to the corresponding deployed Cloud Function (the
function stays the single source of truth for logic). Adding a tool = add a
Python function + `@mcp.tool()` here and redeploy; **no console change needed**
(the agent re-reads the tool list from the same MCP endpoint).

## Tools

| Tool | Proxies to | Env var |
|------|------------|---------|
| `recommend_intervention(tenant_id)` | `recommend-intervention` Cloud Function | `RECOMMEND_INTERVENTION_URL` |

(Planned: `draft_outreach`, `simulate_tenant_response`.)

## Transport

Streamable HTTP, mounted at **`/mcp`**, `stateless_http=True` (Cloud Run
autoscaling-safe). The endpoint to register in Agent Builder is the Cloud Run
service URL **+ `/mcp`**.

## Deploy

```
scripts/infra/04a-preflight-mcp.ps1   # checks source + recommend-intervention is live
scripts/infra/04b-deploy-mcp.ps1      # gcloud run deploy from this dir (Dockerfile build)
scripts/infra/04c-get-url-mcp.ps1     # prints the /mcp URL + MCP initialize smoke test
```

Region `australia-southeast1`, project `rapid-agent-tenant-mix`. Deployed
`--allow-unauthenticated` (matches the Day-2 no-auth MongoDB MCP pattern);
tightening auth is a later hardening pass.
