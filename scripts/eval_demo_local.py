#!/usr/bin/env python
"""
eval_demo_local.py — run the REFACTORED cox_ph_predict scoring on the four demo
tenants locally (no MongoDB), using the function's own _featurise/_score/
_top_features. Two uses:

  1. Exercises the lifelines-free function code path end to end.
  2. Prints the expected scores so we have a baseline to match against the live
     smoke test after deploy (and a demo-prep reference).

Written 2026-06-06 (Day 6->7). Reads data/ (demo mall) + data_train/cox_serving.pkl.

Run (read-only, ~3s):
    .venv/Scripts/python.exe scripts/eval_demo_local.py
"""
from __future__ import annotations

import json
import pickle
import sys
import types
from pathlib import Path

# functions_framework is a Cloud Run runtime dependency, not in the local venv.
# It is only used as a no-op @functions_framework.http decorator, so stub it so
# we can import the real function module and test its scoring code path locally.
_ff = types.ModuleType("functions_framework")
_ff.http = lambda f: f
sys.modules["functions_framework"] = _ff

# Import the actual Cloud Function module so we test the deployed code path.
FUNC_DIR = Path("functions/cox_ph_predict").resolve()
sys.path.insert(0, str(FUNC_DIR))
import main  # noqa: E402

DEMO_IDS = ["TENANT_DEMO_001", "TENANT_DEMO_002", "TENANT_DEMO_003", "TENANT_DEMO_004"]


def main_eval() -> None:
    serving = pickle.loads(Path("data_train/cox_serving.pkl").read_bytes())
    tenants = {t["tenant_id"]: t for t in json.loads(Path("data/tenants.json").read_text())}
    obs_all = json.loads(Path("data/observations.json").read_text())
    by_tenant: dict[str, list] = {}
    for o in obs_all:
        by_tenant.setdefault(o["tenant_id"], []).append(o)

    for tid in DEMO_IDS:
        name = tenants.get(tid, {}).get("name", "?")
        feats = main._featurise(by_tenant[tid])
        sigmoid, pct = main._score(
            serving["coefficients"], serving["norm_mean"],
            serving["train_log_ph"], feats,
        )
        top = main._top_features(serving["coefficients"], feats)
        print("=" * 64)
        print(f"{tid}  —  {name}")
        print(f"  hazard_percentile = {round(pct, 3)}   hazard_sigmoid = {round(sigmoid, 3)}")
        print(f"  alert.enquiry: type={feats.get('alert_enquiry_type')} "
              f"recent_6mo={feats.get('alert_enquiry_recent_6mo')}")
        print(f"  alert.credit:  band={feats.get('alert_credit_band')} "
              f"notches_6mo={feats.get('alert_credit_notches_changed_6mo')} "
              f"trend_3mo={round(float(feats.get('alert_credit_trend_3mo_mean', 0.0)), 4)}")
        print("  top_features:")
        for tf in top:
            print(f"    {tf['feature']:<22} val={tf['value']:<10} "
                  f"contrib={tf['contribution']:<8} {tf['direction']} ({tf['branch']})")


if __name__ == "__main__":
    main_eval()
