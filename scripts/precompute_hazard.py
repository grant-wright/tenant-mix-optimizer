"""
precompute_hazard.py — write current_hazard onto every tenant doc.

WHAT IT DOES: for each tenant that has observations, calls the deployed
cox_ph_predict Cloud Function (single source of truth for scoring +
featurisation) and stores the returned hazard_percentile as `current_hazard`,
plus `hazard_computed_at` (ISO timestamp), on the tenant document in Mongo.

WHY: query_tenants returns an at-risk list ranked by current_hazard. Scoring all
~78 tenants live on every query would mean ~78 round-trips per call. Precomputing
once turns "who's at risk?" into a single fast Mongo query, and freezes the demo
hazards so the story is repeatable between runs. (See decisions.md 2026-06-07.)

WHEN TO RE-RUN: after new observations land, after a model refresh
(cox_serving.pkl), or after reseeding the data. Idempotent — a re-run overwrites.

WHEN WRITTEN: 2026-06-07 (Day 8, Stint 20).

USAGE (prefer the runner, which fetches the cox URL for you):
    scripts/run_precompute_hazard.ps1
Or directly, with both env vars set (MONGODB_URI from .env):
    $env:COX_PH_PREDICT_URL = "https://...run.app"; python scripts/precompute_hazard.py

Env: MONGODB_URI (.env), MONGODB_DB (default tenant_mix), COX_PH_PREDICT_URL.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")
COX_URL = os.environ.get("COX_PH_PREDICT_URL")

HTTP_TIMEOUT = 30  # generous for a cold start on the first call


def _score(tenant_id: str) -> float | None:
    """POST to cox_ph_predict; return hazard_percentile, or None on a skip."""
    data = json.dumps({"tenant_id": tenant_id}).encode("utf-8")
    req = urllib.request.Request(
        COX_URL, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read())
        return float(body["hazard_percentile"])
    except urllib.error.HTTPError as e:
        # 404 = tenant/obs not found; surface the reason but keep going.
        detail = e.read().decode("utf-8", "replace")[:200]
        print(f"  skip {tenant_id}: HTTP {e.code} {detail}")
        return None
    except Exception as e:  # noqa: BLE001 — one bad tenant must not abort the run
        print(f"  skip {tenant_id}: {e}")
        return None


def main() -> int:
    if not COX_URL:
        print("ERROR: COX_PH_PREDICT_URL is not set. Use run_precompute_hazard.ps1, "
              "or set it to the deployed cox-ph-predict URL.")
        return 1

    client = MongoClient(URI)
    db = client[DB_NAME]

    tenant_ids = sorted(db["tenants"].distinct("tenant_id"))
    print(f"Scoring {len(tenant_ids)} tenants in '{DB_NAME}' via cox_ph_predict ...\n")

    now_iso = datetime.now(timezone.utc).isoformat()
    updated = 0
    skipped = 0
    for tid in tenant_ids:
        percentile = _score(tid)
        if percentile is None:
            skipped += 1
            continue
        db["tenants"].update_one(
            {"tenant_id": tid},
            {"$set": {"current_hazard": round(percentile, 3),
                      "hazard_computed_at": now_iso}},
        )
        updated += 1
        print(f"  {tid}: current_hazard = {percentile:.3f}")

    print(f"\nDone. updated: {updated}  skipped: {skipped}  total: {len(tenant_ids)}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
