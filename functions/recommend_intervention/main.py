"""
recommend_intervention - Google Cloud Function (HTTP)

Turns a tenant's hazard score + alert flags + lease state into a recommended
action, via a deterministic, configurable policy. Called by the Agent Builder
agent via the recommend_intervention tool.

POST {"tenant_id": "TENANT_001"}

DESIGN (see decisions.md 2026-06-06 "recommend_intervention design"):
- This function is DETERMINISTIC and carries no ML dependency. It does NOT call
  Gemini. The manager-agent (already a Gemini agent) writes the prose narrative
  around the structured facts returned here. Numbers and the decision are
  auditable code; the LLM does language, not arithmetic.
- It does NOT re-score the tenant. It calls the deployed cox_ph_predict over HTTP
  (single source of truth for scoring + featurisation) and reads only lease/rent
  state from Mongo. That keeps featurisation in exactly one place.

Logic (action tiers ascending: monitor < renew < renegotiate < replace):
  1. base_action from hazard_percentile + lease timing (POLICY thresholds).
  2. floor escalation from alert_flags: final = max(base_action, floor).
       any enquiry -> renegotiate;  credit weak OR distressed -> renegotiate.
       NOTE: the function NEVER auto-emits 'replace'. replace is only ever a
       human decision (see decisions.md 2026-06-06 option b). A distressed
       credit band floors to renegotiate AND raises consider_replace.
  3. escalated_by = the flags that raised the action ABOVE base ([] if none).
       The live alert_flags are echoed regardless, so a non-escalating flag is
       still visible.
  4. suggested_terms (renew/renegotiate) computed deterministically from the
       tenant's current lease + the category occupancy-cost ceiling, with a cap
       on a single-step rent cut.
  5. consider_replace = explicit, auditable list of reasons a human should weigh
       escalating to replace (the function won't do it for them): a distressed
       credit band, and/or the rent-cut cap binding (rent alone is insufficient).

Env: MONGODB_URI (secret), MONGODB_DB (default tenant_mix), COX_PH_PREDICT_URL.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

import functions_framework
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Policy — the configurable business rules. In production these are tenant- or
# landlord-specific and live in config/a policy service; here they are a named,
# editable block so "configurable" is literally true. See decisions.md 2026-06-06.
# ---------------------------------------------------------------------------

ACTIONS = ["monitor", "renew", "renegotiate", "replace"]   # ascending severity
RANK = {a: i for i, a in enumerate(ACTIONS)}

POLICY = {
    # Ambient base action from hazard_percentile (a rank vs the training pop;
    # these cutpoints are a base-rate policy, not a distributional cliff).
    "monitor_below": 0.25,            # < this -> monitor
    "renegotiate_at_or_above": 0.65,  # >= this -> renegotiate (top third of risk)
    # Lease-soon override: lease ending within N months AND non-trivial risk
    # -> renegotiate (act when an at-risk tenant's lease is nearly up). The 0.40
    # bar is load-bearing for the demo (keeps Margot's alert-layer beat intact).
    "lease_soon_months": 12,
    "lease_soon_min_percentile": 0.40,
    # suggested_terms.
    "max_rent_cut_pct": 0.20,         # cap a single-step rent reduction
    "default_lease_years": 5,
    "default_escalator_pct": 2.0,
}

# Category occupancy-cost ceilings = top of the healthy rent-to-sales band. In
# production these come from an external benchmark service (ICSC/Moody's etc.);
# here they MIRROR the generator's CATEGORIES[...]["healthy_rts"] tops. A drift
# guard lives in scripts/check_recommend.py. See decisions.md 2026-06-06.
OCCUPANCY_CEILING = {
    "apparel_boutique": 0.15,
    "food_court": 0.12,
    "full_service_restaurant": 0.15,
    "phone_repair": 0.10,
    "bookstore": 0.11,
    "salon_beauty": 0.16,
    "specialty_jewellery": 0.07,
    "chain_apparel": 0.14,
    "anchor_dept_store": 0.05,
    "anchor_supermarket": 0.04,
}

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
# Fetch — hazard from cox_ph_predict (HTTP); lease/rent state from Mongo
# ---------------------------------------------------------------------------

def _get_hazard(tenant_id: str) -> dict:
    """Call the deployed cox_ph_predict for hazard_percentile + alert_flags."""
    url = os.environ["COX_PH_PREDICT_URL"]
    data = json.dumps({"tenant_id": tenant_id}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise KeyError(f"tenant_id not found: {tenant_id}")
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"cox_ph_predict returned {e.code}: {body}")


def _fetch_lease_state(db, tenant_id: str) -> tuple[dict, str, float | None]:
    """Tenant doc + the latest observation month (sim 'now') + recent rent-to-sales."""
    tenant = db["tenants"].find_one({"tenant_id": tenant_id}, {"_id": 0})
    if tenant is None:
        raise KeyError(f"tenant_id not found: {tenant_id}")
    obs = list(db["observations"].find(
        {"tenant_id": tenant_id}, {"_id": 0, "month": 1, "rent_to_sales_ratio": 1},
    ))
    if not obs:
        raise ValueError(f"No observations for tenant: {tenant_id}")
    obs.sort(key=lambda o: o["month"])
    now_month = obs[-1]["month"]
    recent = [o["rent_to_sales_ratio"] for o in obs[-3:] if o.get("rent_to_sales_ratio") is not None]
    recent_rts = sum(recent) / len(recent) if recent else None
    return tenant, now_month, recent_rts


# ---------------------------------------------------------------------------
# Policy — base action, floor escalation, terms, confidence
# ---------------------------------------------------------------------------

def _year_month(s: str) -> tuple[int, int]:
    """Parse 'YYYY-MM' or 'YYYY-MM-DD' -> (year, month)."""
    y, m = s.split("-")[:2]
    return int(y), int(m)


def _months_until(now_month: str, lease_end: str) -> int:
    ny, nm = _year_month(now_month)
    ly, lm = _year_month(lease_end)
    return (ly - ny) * 12 + (lm - nm)


def _base_action(percentile: float, months_to_lease_end: int) -> str:
    if percentile >= POLICY["renegotiate_at_or_above"]:
        return "renegotiate"
    lease_soon = months_to_lease_end <= POLICY["lease_soon_months"]
    if lease_soon and percentile >= POLICY["lease_soon_min_percentile"]:
        return "renegotiate"
    if percentile < POLICY["monitor_below"]:
        return "monitor"
    return "renew"


def _apply_floor(base: str, alert_flags: dict) -> tuple[str, list, list]:
    """final = max(base, floor); escalated_by = flags that raised it above base.

    consider_replace = advisory reasons a human should weigh escalating to
    replace. The function itself never auto-floors to replace off one signal —
    even a distressed credit band only floors to renegotiate (see decisions.md
    2026-06-06 option b). replace is always a human call.
    """
    floors = []  # (floor_action, label)
    consider_replace = []
    enq = alert_flags.get("enquiry", {})
    if enq.get("recent_6mo"):
        floors.append(("renegotiate", f"enquiry:{enq.get('type')}"))
    band = alert_flags.get("credit", {}).get("band")
    if band == "weak":
        floors.append(("renegotiate", "credit:weak"))
    elif band == "distressed":
        # Severe, but still not an auto-replace: floor to renegotiate and flag
        # replace for the human to weigh.
        floors.append(("renegotiate", "credit:distressed"))
        consider_replace.append("credit:distressed")

    final = base
    escalated_by = []
    for action, label in floors:
        if RANK[action] > RANK[base]:
            escalated_by.append(label)
        if RANK[action] > RANK[final]:
            final = action
    return final, escalated_by, consider_replace


def _suggested_terms(intervention: str, tenant: dict, recent_rts: float | None) -> dict | None:
    """Deterministic terms for renew/renegotiate; None for monitor/replace."""
    if intervention not in ("renew", "renegotiate"):
        return None

    current_psf = float(tenant.get("rent_per_sqft", 0.0))
    terms = {
        "rent_per_sqft": round(current_psf, 2),
        "lease_years": POLICY["default_lease_years"],
        "escalator_pct": POLICY["default_escalator_pct"],
        "reduction_pct": 0.0,
        "cap_binds": False,
    }
    if intervention == "renew":
        return terms  # healthy tenant -> hold current rent on a standard renewal

    # renegotiate: pull occupancy cost back toward the category ceiling.
    ceiling = OCCUPANCY_CEILING.get(tenant.get("category"))
    if ceiling and recent_rts and recent_rts > ceiling and current_psf > 0:
        raw_target = current_psf * (ceiling / recent_rts)
        floor_psf = current_psf * (1.0 - POLICY["max_rent_cut_pct"])
        target_psf = max(raw_target, floor_psf)         # cap the single-step cut
        terms["rent_per_sqft"] = round(target_psf, 2)
        terms["reduction_pct"] = round((1.0 - target_psf / current_psf) * 100.0, 1)
        terms["cap_binds"] = raw_target < floor_psf      # rent alone can't fix it
    return terms


def _confidence(percentile: float, alert_flags: dict, escalated_by: list) -> str:
    band = alert_flags.get("credit", {}).get("band")
    enq_type = alert_flags.get("enquiry", {}).get("type")
    notches = int(alert_flags.get("credit", {}).get("notches_changed_6mo", 0))
    strong_flag = band in ("weak", "distressed") or enq_type == "early_termination" or notches <= -2

    if percentile >= POLICY["renegotiate_at_or_above"] or strong_flag:
        return "high"
    if percentile < POLICY["monitor_below"] and not escalated_by and enq_type is None:
        return "high"   # confidently healthy
    if escalated_by or percentile >= POLICY["monitor_below"]:
        return "medium"
    return "low"


def _reasoning(percentile, base, final, escalated_by, terms, consider_replace) -> str:
    """A terse, factual, deterministic summary. The manager-agent writes the
    manager-facing prose; this is the structured fact line it narrates from."""
    parts = [f"Ambient risk percentile {percentile:.2f}; base action '{base}'."]
    if escalated_by:
        parts.append(f"Escalated to '{final}' by: {', '.join(escalated_by)}.")
    else:
        parts.append(f"No flag escalation; action stays '{final}'.")
    if terms and terms.get("reduction_pct"):
        msg = f"Suggested ~{terms['reduction_pct']:.0f}% rent reduction toward the category occupancy ceiling."
        if terms.get("cap_binds"):
            msg += " Reduction capped — rent alone is insufficient."
        parts.append(msg)
    if consider_replace:
        parts.append(f"Consider replace (human decision): {', '.join(consider_replace)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# HTTP entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def recommend_intervention(request):
    if request.method != "POST":
        return (json.dumps({"error": "POST required"}), 405,
                {"Content-Type": "application/json"})

    body = request.get_json(silent=True) or {}
    tenant_id = str(body.get("tenant_id", "")).strip()
    if not tenant_id:
        return (json.dumps({"error": "tenant_id is required"}), 400,
                {"Content-Type": "application/json"})

    try:
        hazard = _get_hazard(tenant_id)
        percentile = float(hazard["hazard_percentile"])
        alert_flags = hazard.get("alert_flags", {})

        db = _get_db()
        tenant, now_month, recent_rts = _fetch_lease_state(db, tenant_id)
        months_to_lease_end = _months_until(now_month, tenant["lease_end"])

        base = _base_action(percentile, months_to_lease_end)
        final, escalated_by, consider_replace = _apply_floor(base, alert_flags)
        terms = _suggested_terms(final, tenant, recent_rts)
        if terms and terms.get("cap_binds"):
            consider_replace.append("terms:rent_cap_binds")  # rent alone can't fix it
        confidence = _confidence(percentile, alert_flags, escalated_by)

        payload = {
            "tenant_id": tenant_id,
            "intervention": final,
            "base_action": base,
            "escalated_by": escalated_by,
            "consider_replace": consider_replace,  # human-only escalation signals
            "alert_flags": alert_flags,          # echoed so non-escalating flags stay visible
            "hazard_percentile": round(percentile, 3),
            "months_to_lease_end": months_to_lease_end,
            "suggested_terms": terms,
            "confidence": confidence,
            "reasoning": _reasoning(percentile, base, final, escalated_by, terms, consider_replace),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        return (json.dumps(payload), 200, {"Content-Type": "application/json"})

    except KeyError as e:
        logging.warning("Not found: %s", e)
        return (json.dumps({"error": str(e)}), 404,
                {"Content-Type": "application/json"})
    except Exception:
        logging.exception("Unhandled error for tenant_id=%s", tenant_id)
        return (json.dumps({"error": "internal error"}), 500,
                {"Content-Type": "application/json"})
