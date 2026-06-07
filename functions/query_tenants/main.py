"""
query_tenants - Google Cloud Function (HTTP)

Filters the tenant population and returns a ranked list of summaries. Called by
the Agent Builder agent via the query_tenants tool (the demo opens with
"who's at risk?" -> this returns the at-risk table).

POST {"filter": {... any subset ...}}   (a bare {...} filter object also works)

Filter keys (all optional; spec-tenant-mix-optimizer.md "query_tenants"):
  status: "active" | "exited"
  category: string                         (exact match on tenant.category)
  hazard_above: float 0-1                  (current_hazard >= this)
  lease_expiring_within_months: int        (lease_end within N months of sim-now)
  limit: int                               (page size, default 20, capped 100)
  offset: int                              (page start, default 0 -> "next page")

Returns: a page of summaries sorted by current_hazard desc, PLUS the full match
count so a truncated list is never silently hidden:
  {
    "tenants": [{"tenant_id", "name", "category", "lease_end", "current_hazard"}],
    "total_matched": int,   # how many tenants match the filter in total
    "returned": int,        # how many are in THIS page
    "offset": int,
    "truncated": bool,      # True if more matches exist beyond this page
    "next_offset": int|null # pass back as offset to fetch the next page
  }

WHY total_matched + truncated (Grant, 2026-06-07): a silently capped at-risk
list is most dangerous exactly when it matters most — a portfolio-wide distress
event (anchor exit, downturn) is when the manager MUST see that the at-risk list
is bigger than the page shown. The agent narrates the scale ("top 20 of 47 at
risk") and can page with next_offset. See decisions.md 2026-06-07.

DESIGN (see decisions.md 2026-06-07 "query_tenants design"):
- This function is DETERMINISTIC and carries no ML dependency. It does NOT score
  tenants. It reads the precomputed `current_hazard` field written onto each
  tenant doc by scripts/precompute_hazard.py (which scores via cox_ph_predict).
  Ranking 78 tenants live would mean 78 scoring round-trips per query; precompute
  makes "who's at risk?" a single fast Mongo query. (current_hazard == the
  cox_ph_predict hazard_percentile as of the last precompute run.)
- "lease_expiring_within_months" is relative to SIM-NOW (the latest observation
  month across the population), not wall-clock now — the data is a fixed
  simulated timeline. recommend_intervention uses the same sim-now notion.

Env: MONGODB_URI (secret), MONGODB_DB (default tenant_mix).
"""

import json
import logging
import os
from datetime import datetime, timezone

import functions_framework
from pymongo import ASCENDING, DESCENDING, MongoClient

DEFAULT_LIMIT = 20
MAX_LIMIT = 100      # guard rail: cap a single page so a bad call can't pull all

# ---------------------------------------------------------------------------
# Singletons — cold start pays once; warm invocations reuse.
# ---------------------------------------------------------------------------

_mongo_client = None


def _get_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(os.environ["MONGODB_URI"])
    return _mongo_client[os.environ.get("MONGODB_DB", "tenant_mix")]


# ---------------------------------------------------------------------------
# Sim-now + month arithmetic (lease_expiring filter)
# ---------------------------------------------------------------------------

def _sim_now(db) -> str | None:
    """Latest observation month across the population ('YYYY-MM') = sim 'now'."""
    latest = db["observations"].find_one(
        {}, {"_id": 0, "month": 1}, sort=[("month", DESCENDING)],
    )
    return latest["month"] if latest else None


def _add_months(year_month: str, n: int) -> str:
    """'YYYY-MM' + n months -> 'YYYY-MM'."""
    y, m = (int(p) for p in year_month.split("-")[:2])
    total = (y * 12 + (m - 1)) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# Build the Mongo query from the filter object
# ---------------------------------------------------------------------------

def _build_query(db, flt: dict) -> dict:
    query: dict = {}

    status = flt.get("status")
    if status in ("active", "exited"):
        query["status"] = status

    category = flt.get("category")
    if category:
        query["category"] = category

    hazard_above = flt.get("hazard_above")
    if hazard_above is not None:
        # current_hazard >= bound. Tenants without the precomputed field never
        # match $gte, so they fall out of a hazard-filtered query (correct).
        query["current_hazard"] = {"$gte": float(hazard_above)}

    months = flt.get("lease_expiring_within_months")
    if months is not None:
        now = _sim_now(db)
        if now is not None:
            cutoff_month = _add_months(now, int(months))
            # lease_end is 'YYYY-MM-DD'; '<cutoff>-99' is an upper bound that
            # includes every day of the cutoff month. Exclude null lease_end.
            query["lease_end"] = {"$ne": None, "$lte": f"{cutoff_month}-99"}

    return query


# ---------------------------------------------------------------------------
# HTTP entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def query_tenants(request):
    if request.method != "POST":
        return (json.dumps({"error": "POST required"}), 405,
                {"Content-Type": "application/json"})

    body = request.get_json(silent=True) or {}
    # Accept either {"filter": {...}} or a bare {...} filter object.
    flt = body.get("filter", body) if isinstance(body, dict) else {}
    if not isinstance(flt, dict):
        flt = {}

    try:
        db = _get_db()
        query = _build_query(db, flt)

        limit = max(1, min(int(flt.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
        offset = max(0, int(flt.get("offset", 0)))

        # total_matched ignores the page window — the load-bearing signal so a
        # truncated at-risk list is never silently hidden (see module docstring).
        total_matched = db["tenants"].count_documents(query)

        cursor = (
            db["tenants"]
            .find(query, {
                "_id": 0, "tenant_id": 1, "name": 1, "category": 1,
                "lease_end": 1, "current_hazard": 1,
            })
            # Secondary sort on tenant_id makes paging deterministic when several
            # tenants share a current_hazard (stable page boundaries).
            .sort([("current_hazard", DESCENDING), ("tenant_id", ASCENDING)])
            .skip(offset)
            .limit(limit)
        )

        results = []
        for t in cursor:
            haz = t.get("current_hazard")
            results.append({
                "tenant_id": t.get("tenant_id"),
                "name": t.get("name"),
                "category": t.get("category"),
                "lease_end": t.get("lease_end"),
                "current_hazard": round(float(haz), 3) if haz is not None else None,
            })

        returned = len(results)
        truncated = (offset + returned) < total_matched
        payload = {
            "tenants": results,
            "total_matched": total_matched,
            "returned": returned,
            "offset": offset,
            "truncated": truncated,
            "next_offset": (offset + returned) if truncated else None,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        return (json.dumps(payload), 200, {"Content-Type": "application/json"})

    except Exception:
        logging.exception("Unhandled error in query_tenants; filter=%s", flt)
        return (json.dumps({"error": "internal error"}), 500,
                {"Content-Type": "application/json"})
