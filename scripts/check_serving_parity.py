#!/usr/bin/env python
"""
check_serving_parity.py — definitive pre-deploy gate for the lifelines-free
serving artefact.

Written 2026-06-06 (Day 6->7). Proves that the SERVING pkl (plain dicts:
coefficients + norm_mean) reproduces its source FULL-MODEL's lifelines
predict_log_partial_hazard to floating point, so the Cloud Function can drop
lifelines entirely.

Two assertions:
  1. The serving pkl's stored coefficients / norm_mean equal the full model's
     params_ / _norm_mean exactly (no drift in the export step).
  2. Over many random feature rows, the lifelines-free centered dot product
     (x - xbar) . beta equals lifelines' predict_log_partial_hazard to 0.0.

Background: lifelines CENTERS covariates by the training mean before the dot
product. A naive sum(coef*feat) is off by the constant xbar . beta and would
shift every hazard_sigmoid / hazard_percentile. This guards against that.

Run (read-only, ~5s):
    .venv/Scripts/python.exe scripts/check_serving_parity.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FEATURE_NAMES = [
    "rts_12mo_mean", "sales_trend_12mo", "late_pay_count_12mo",
    "trading_shortfall_3mo", "stock_depth_3mo",
]

FULL_BUNDLE = Path("data_train/cox_model.pkl")     # training artefact (has the model)
SERVING_PKL = Path("data_train/cox_serving.pkl")   # serving artefact (plain dicts)


def main() -> int:
    model = pickle.loads(FULL_BUNDLE.read_bytes())["model"]
    serving = pickle.loads(SERVING_PKL.read_bytes())
    coef = serving["coefficients"]
    norm = serving["norm_mean"]

    print("=" * 70)
    print("Assertion 1 — serving pkl values == full model values")
    print("=" * 70)
    coef_err = max(abs(coef[f] - float(model.params_[f])) for f in FEATURE_NAMES)
    norm_err = max(abs(norm[f] - float(model._norm_mean[f])) for f in FEATURE_NAMES)
    print(f"max |coef diff|      = {coef_err:.3e}")
    print(f"max |norm_mean diff| = {norm_err:.3e}")
    a1 = coef_err < 1e-12 and norm_err < 1e-12
    print("->", "OK" if a1 else "DRIFT (investigate)")

    print("\n" + "=" * 70)
    print("Assertion 2 — centered dot (serving) == lifelines, random rows")
    print("=" * 70)
    rng = np.random.default_rng(0)
    max_err = 0.0
    for _ in range(2000):
        feats = {f: float(rng.normal()) for f in FEATURE_NAMES}
        ll = float(model.predict_log_partial_hazard(pd.DataFrame([feats])).iloc[0])
        serv = sum(coef[f] * (feats[f] - norm[f]) for f in FEATURE_NAMES)
        max_err = max(max_err, abs(ll - serv))
    print(f"max |lifelines - serving| over 2000 rows = {max_err:.3e}")
    a2 = max_err < 1e-9
    print("->", "OK" if a2 else "MISMATCH (investigate)")

    ok = a1 and a2
    print("\n" + ("PASS — serving pkl is faithful; safe to deploy lifelines-free."
                  if ok else "FAIL — do not deploy."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
