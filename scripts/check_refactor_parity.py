#!/usr/bin/env python
"""
check_refactor_parity.py — verify the lifelines-free serving refactor changes
NOTHING about the model's output.

Written 2026-06-06 (Day 6->7). Two checks:

  A. Model identity — does a fresh fit on today's data_train reproduce the
     DEPLOYED model's coefficients / centering / train log-ph distribution?
     (Determines whether we deploy the fresh serving pkl or must export from the
     deployed bundle to preserve scores.)

  B. Scoring parity — for random feature rows, does the lifelines-free serving
     math (centered dot product) reproduce lifelines' predict_log_partial_hazard
     to floating point?

Run (read-only, ~5s):
    .venv/Scripts/python.exe scripts/check_refactor_parity.py
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path

import numpy as np

FEATURE_NAMES = [
    "rts_12mo_mean", "sales_trend_12mo", "late_pay_count_12mo",
    "trading_shortfall_3mo", "stock_depth_3mo",
]

DEPLOYED_BUNDLE = Path("functions/cox_ph_predict/model/cox_model.pkl")
FRESH_SERVING = Path("data_train/cox_serving.pkl")


def main() -> None:
    deployed = pickle.loads(DEPLOYED_BUNDLE.read_bytes())
    model = deployed["model"]
    dep_coef = {f: float(model.params_[f]) for f in FEATURE_NAMES}
    dep_norm = {f: float(model._norm_mean[f]) for f in FEATURE_NAMES}
    dep_tlp = np.asarray(deployed["train_log_ph"], dtype=float)

    fresh = pickle.loads(FRESH_SERVING.read_bytes())
    fr_coef = {f: float(fresh["coefficients"][f]) for f in FEATURE_NAMES}
    fr_norm = {f: float(fresh["norm_mean"][f]) for f in FEATURE_NAMES}
    fr_tlp = np.asarray(fresh["train_log_ph"], dtype=float)

    print("=" * 70)
    print("CHECK A — fresh fit vs DEPLOYED model")
    print("=" * 70)
    print(f"{'covariate':<24} {'deployed':>12} {'fresh':>12} {'|diff|':>12}")
    coef_maxdiff = 0.0
    for f in FEATURE_NAMES:
        d = abs(dep_coef[f] - fr_coef[f])
        coef_maxdiff = max(coef_maxdiff, d)
        print(f"{f:<24} {dep_coef[f]:>12.6f} {fr_coef[f]:>12.6f} {d:>12.2e}")
    norm_maxdiff = max(abs(dep_norm[f] - fr_norm[f]) for f in FEATURE_NAMES)
    print(f"\nmax |coef diff|      = {coef_maxdiff:.3e}")
    print(f"max |norm_mean diff| = {norm_maxdiff:.3e}")

    print(f"\ntrain_log_ph: deployed n={len(dep_tlp)}  fresh n={len(fr_tlp)}")
    if len(dep_tlp) == len(fr_tlp):
        # order-independent comparison of the distribution
        tlp_maxdiff = float(np.max(np.abs(np.sort(dep_tlp) - np.sort(fr_tlp))))
        print(f"max |train_log_ph diff| (sorted) = {tlp_maxdiff:.3e}")
    else:
        tlp_maxdiff = float("inf")
        print("train_log_ph lengths differ -> populations differ")

    identical = coef_maxdiff < 1e-9 and norm_maxdiff < 1e-9 and tlp_maxdiff < 1e-9
    print("\nVERDICT A:", "IDENTICAL — fresh serving pkl is safe to deploy"
          if identical else
          "DIVERGED — export serving pkl from the deployed bundle instead")

    print("\n" + "=" * 70)
    print("CHECK B — serving math vs lifelines (on the deployed model)")
    print("=" * 70)
    rng = np.random.default_rng(0)
    max_err = 0.0
    for _ in range(1000):
        feats = {f: float(rng.normal()) for f in FEATURE_NAMES}
        # lifelines path
        import pandas as pd
        X = pd.DataFrame([feats])
        ll = float(model.predict_log_partial_hazard(X).iloc[0])
        # serving path (centered dot, lifelines-free)
        serv = sum(dep_coef[f] * (feats[f] - dep_norm[f]) for f in FEATURE_NAMES)
        max_err = max(max_err, abs(ll - serv))
    print(f"max |lifelines - serving| over 1000 random rows = {max_err:.3e}")
    # show a sigmoid too, to confirm the downstream transform is unaffected
    print(f"(sigmoid is monotone in log_ph; e.g. log_ph=0 -> {1/(1+math.exp(0)):.3f})")
    print("VERDICT B:", "MATCH" if max_err < 1e-9 else "MISMATCH (investigate)")


if __name__ == "__main__":
    main()
