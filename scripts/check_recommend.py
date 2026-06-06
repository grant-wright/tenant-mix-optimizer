#!/usr/bin/env python
"""
Offline check for the recommend_intervention Cloud Function.

Two jobs:
  1. DRIFT GUARD — assert the function's OCCUPANCY_CEILING matches the generator's
     CATEGORIES[...]["healthy_rts"] tops. These are two copies of the same benchmark
     (the function can't import the generator), so this catches silent drift.
  2. POLICY TRACE — exercise the deterministic policy on the four demo tenants with
     canned hazard (from eval_demo_local.py) + lease/rent state (from
     inspect_demo_tenants.py), stubbing the HTTP + Mongo calls. Asserts the expected
     intervention / base_action / escalated_by / confidence for each.

No MongoDB, no network. Run: python scripts/check_recommend.py
Written 2026-06-06 (Day 7). See decisions.md 2026-06-06 + reference/demo-cast-scoring-analysis.md.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# Stub functions_framework so main.py imports without the package installed.
_ff = types.ModuleType("functions_framework")
_ff.http = lambda f: f
sys.modules["functions_framework"] = _ff

sys.path.insert(0, str(Path(__file__).parent.parent / "functions" / "recommend_intervention"))
import main  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import generate_synthetic_data as gen  # noqa: E402


# --------------------------------------------------------------------------
# 1. Drift guard — OCCUPANCY_CEILING vs generator healthy_rts tops
# --------------------------------------------------------------------------

def check_drift_guard() -> bool:
    expected = {}
    for cat, cfg in {**gen.CATEGORIES, **gen.ANCHOR_CATEGORIES}.items():
        expected[cat] = cfg["healthy_rts"][1]   # top of the healthy band = ceiling
    ok = True
    for cat, ceiling in expected.items():
        got = main.OCCUPANCY_CEILING.get(cat)
        if got != ceiling:
            print(f"  DRIFT: {cat} generator ceiling={ceiling} but function={got}")
            ok = False
    extra = set(main.OCCUPANCY_CEILING) - set(expected)
    if extra:
        print(f"  DRIFT: function has unknown categories {sorted(extra)}")
        ok = False
    print(f"drift guard: {'PASS' if ok else 'FAIL'} ({len(expected)} categories)")
    return ok


# --------------------------------------------------------------------------
# 2. Policy trace — canned demo-cast inputs
# --------------------------------------------------------------------------

# hazard_percentile + alert_flags, from scripts/eval_demo_local.py (2026-06-06)
HAZARD = {
    "TENANT_DEMO_001": {"hazard_percentile": 0.324, "alert_flags": {
        "enquiry": {"type": "rent_relief", "recent_6mo": True},
        "credit": {"band": "fair", "notches_changed_6mo": -1, "trend_3mo_mean": -0.1881}}},
    "TENANT_DEMO_002": {"hazard_percentile": 0.71, "alert_flags": {
        "enquiry": {"type": "early_termination", "recent_6mo": True},
        "credit": {"band": "weak", "notches_changed_6mo": -1, "trend_3mo_mean": -0.3367}}},
    "TENANT_DEMO_003": {"hazard_percentile": 0.093, "alert_flags": {
        "enquiry": {"type": None, "recent_6mo": False},
        "credit": {"band": "strong", "notches_changed_6mo": 1, "trend_3mo_mean": 0.0953}}},
    "TENANT_DEMO_004": {"hazard_percentile": 0.036, "alert_flags": {
        "enquiry": {"type": None, "recent_6mo": False},
        "credit": {"band": "strong", "notches_changed_6mo": 0, "trend_3mo_mean": 0.0064}}},
}

# (tenant doc, now_month, recent_rts) from scripts/inspect_demo_tenants.py (2026-06-06)
LEASE = {
    "TENANT_DEMO_001": ({"category": "apparel_boutique", "rent_per_sqft": 4.05, "lease_end": "2026-10-01"}, "2026-06", 0.173),
    "TENANT_DEMO_002": ({"category": "food_court", "rent_per_sqft": 4.95, "lease_end": "2027-01-01"}, "2026-06", 0.22),
    "TENANT_DEMO_003": ({"category": "phone_repair", "rent_per_sqft": 2.52, "lease_end": "2027-09-01"}, "2026-06", 0.103),
    "TENANT_DEMO_004": ({"category": "bookstore", "rent_per_sqft": 1.98, "lease_end": "2028-02-01"}, "2026-06", 0.085),
}

# Expected outcomes (the demo story we want to hold).
EXPECT = {
    "TENANT_DEMO_001": {"base_action": "renew", "intervention": "renegotiate",
                        "escalated_by": ["enquiry:rent_relief"], "confidence": "medium"},
    "TENANT_DEMO_002": {"base_action": "renegotiate", "intervention": "renegotiate",
                        "escalated_by": [], "confidence": "high"},
    "TENANT_DEMO_003": {"base_action": "monitor", "intervention": "monitor",
                        "escalated_by": [], "confidence": "high"},
    "TENANT_DEMO_004": {"base_action": "monitor", "intervention": "monitor",
                        "escalated_by": [], "confidence": "high"},
}


class FakeRequest:
    method = "POST"

    def __init__(self, tenant_id):
        self._tid = tenant_id

    def get_json(self, silent=False):
        return {"tenant_id": self._tid}


def check_policy_trace() -> bool:
    # Stub the two external calls.
    main._get_hazard = lambda tid: HAZARD[tid]
    main._get_db = lambda: None
    main._fetch_lease_state = lambda db, tid: LEASE[tid]

    all_ok = True
    for tid in ["TENANT_DEMO_001", "TENANT_DEMO_002", "TENANT_DEMO_003", "TENANT_DEMO_004"]:
        body, status, _ = main.recommend_intervention(FakeRequest(tid))
        payload = json.loads(body)
        exp = EXPECT[tid]
        ok = status == 200 and all(payload.get(k) == v for k, v in exp.items())
        all_ok = all_ok and ok
        terms = payload.get("suggested_terms")
        terms_str = (f"rent {terms['rent_per_sqft']} (-{terms['reduction_pct']}%"
                     f"{' CAP' if terms.get('cap_binds') else ''})" if terms else "none")
        print(f"  {'OK ' if ok else 'XX '}{tid}  base={payload.get('base_action'):<11} "
              f"final={payload.get('intervention'):<11} esc={payload.get('escalated_by')} "
              f"conf={payload.get('confidence'):<6} terms={terms_str}")
        if not ok:
            print(f"      expected {exp}")
    print(f"policy trace: {'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == "__main__":
    print("=" * 60)
    a = check_drift_guard()
    print("-" * 60)
    b = check_policy_trace()
    print("=" * 60)
    sys.exit(0 if (a and b) else 1)
