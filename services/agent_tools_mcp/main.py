"""
agent_tools MCP server — exposes the tenant-mix optimizer's custom Cloud
Functions to the Agent Builder agent as MCP tools.

WHY THIS EXISTS: the Agent Designer (no-code) surface our agent lives on only
supports MCP / Search / Data Store tools — there is NO OpenAPI tool type there
(that's a different surface). So our deterministic Cloud Functions reach the
agent through MCP, the same mechanism the MongoDB tool already uses.

DESIGN:
- This is the single MCP home for ALL our custom agent tools (Option A). Each
  tool is a THIN PROXY that POSTs to the corresponding deployed Cloud Function,
  which stays the single source of truth for logic. v1 exposes query_tenants +
  recommend_intervention; draft_outreach + simulate_tenant_response slot in here
  as they are built (add a function + @mcp.tool, redeploy — no console change).
- Transport: streamable HTTP, mounted at /mcp. stateless_http=True so any Cloud
  Run instance can serve any request (autoscaling-safe, no session affinity).

ENV:
  QUERY_TENANTS_URL          — the deployed query-tenants function URL.
  RECOMMEND_INTERVENTION_URL — the deployed recommend-intervention function URL.
  PORT — set by Cloud Run (default 8080); consumed by uvicorn in the Dockerfile.
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# The SDK's DNS-rebinding protection validates the Host header against an
# allow-list defaulting to localhost only — it rejects Cloud Run's *.run.app
# Host with "Invalid Host header". That protection guards LOCAL servers from
# browser-based DNS-rebinding attacks; this is a deployed service behind Cloud
# Run's TLS proxy, so we disable it. (Exact-match allowed_hosts can't cover both
# of Cloud Run's URL forms, so disabling is the clean choice here.)
mcp = FastMCP(
    "agent-tools",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)

_HTTP_TIMEOUT = 30.0  # recommend -> cox -> mongo chain; generous for cold starts


def _post(url_env: str, payload: dict) -> dict:
    """POST payload to the Cloud Function named by the env var; return its JSON.

    Surfaces upstream errors as a plain {"error": ...} dict so the agent gets a
    usable message instead of an opaque transport failure.
    """
    url = os.environ[url_env]
    try:
        resp = httpx.post(url, json=payload, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "error": f"upstream {e.response.status_code}",
            "detail": e.response.text[:500],
        }
    except httpx.HTTPError as e:
        return {"error": f"request to {url_env} failed: {e}"}


@mcp.tool()
def query_tenants(
    status: str | None = None,
    category: str | None = None,
    hazard_above: float | None = None,
    lease_expiring_within_months: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """Find tenants matching a filter, ranked by current risk (highest first).

    Use this to answer "who's at risk?", "which tenants should I worry about?",
    list tenants by category/status, or resolve a tenant by browsing the list.
    All filters are optional; omit them to list everyone by risk.

    Returns {tenants: [{tenant_id, name, category, lease_end, current_hazard}],
    total_matched, returned, offset, truncated, next_offset}. current_hazard is a
    0-1 risk rank (higher = more at risk).

    IMPORTANT — do not hide the scale of risk: the list is paged (default 20). If
    `truncated` is true, total_matched is LARGER than what you were shown — tell
    the manager the true total (e.g. "showing the 20 highest-risk of 47 at risk")
    and treat a large at-risk count as a portfolio-wide warning, not business as
    usual. To see more, call again with offset = next_offset.

    Args:
        status: "active" or "exited".
        category: tenant category, exact match (e.g. "food_court").
        hazard_above: only tenants with current_hazard >= this (0-1).
        lease_expiring_within_months: leases ending within this many months.
        limit: page size (default 20, max 100).
        offset: page start (default 0); pass a prior next_offset to page on.
    """
    flt: dict = {}
    if status is not None:
        flt["status"] = status
    if category is not None:
        flt["category"] = category
    if hazard_above is not None:
        flt["hazard_above"] = hazard_above
    if lease_expiring_within_months is not None:
        flt["lease_expiring_within_months"] = lease_expiring_within_months
    if limit is not None:
        flt["limit"] = limit
    if offset is not None:
        flt["offset"] = offset
    return _post("QUERY_TENANTS_URL", {"filter": flt})


@mcp.tool()
def recommend_intervention(tenant_id: str) -> dict:
    """Recommend an intervention for a single tenant.

    Returns the final recommended action (monitor / renew / renegotiate; note
    that 'replace' is never auto-emitted — it is always a human decision), the
    pre-escalation base_action, which alert flags escalated it (escalated_by),
    any human-only signals worth weighing a replace (consider_replace), suggested
    lease terms, a confidence level, and a factual reasoning line for the agent
    to narrate. Call this when the user asks what to do about a tenant, whether
    to renew/renegotiate, or for a recommendation on an at-risk tenant.

    Args:
        tenant_id: The tenant identifier, e.g. "TENANT_DEMO_001".
    """
    return _post("RECOMMEND_INTERVENTION_URL", {"tenant_id": tenant_id})


# Streamable-HTTP ASGI app served by uvicorn (see Dockerfile). MCP endpoint: /mcp
app = mcp.streamable_http_app()
