#!/usr/bin/env python
"""
Feature experiment (Day 4) — can AMBIENT signals alone clear a 0.70 landmark
c-index, now that enquiry + credit live in the alert layer?

Non-destructive: reuses build_counting_process from fit_cox_model.py, adds a few
candidate trajectory features, and scores each feature set on the HOLDOUT
landmark c-index (@12 and @24). Prints a comparison table; changes nothing.

Usage:
    python scripts/experiment_features.py [--data data_train]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from lifelines import CoxTimeVaryingFitter
from lifelines.utils import concordance_index

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit_cox_model import build_counting_process, split_tenants  # noqa: E402

BASE = ["rts_12mo_mean", "sales_trend_12mo", "late_pay_count_12mo",
        "trading_shortfall_3mo", "stock_depth_3mo"]
LANDMARKS = [12, 24]


def add_candidates(cp_df):
    grp = cp_df.groupby("tenant_id", group_keys=False)
    cp_df["rts_trend_12mo"] = grp["rent_to_sales_ratio"].transform(
        lambda s: (s - s.shift(12))).fillna(0.0)            # abs change in rts over 12mo
    cp_df["rts_latest"] = cp_df["rent_to_sales_ratio"]      # current level, not the mean
    cp_df["stock_trend_6mo"] = grp["stock_depth_index"].transform(
        lambda s: (s - s.shift(6))).fillna(0.0)
    cp_df["foot_trend_12mo"] = grp["foot_traffic_estimate"].transform(
        lambda s: (s / s.shift(12) - 1.0)).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    return cp_df


def landmark_cindex(model, cp_df, L):
    rows = []
    for tid, sub in cp_df.groupby("tenant_id"):
        if sub["stop"].max() <= L:
            continue
        row_at_L = sub[sub["start"] == L]
        if row_at_L.empty:
            continue
        ph = float(model.predict_partial_hazard(row_at_L).iloc[0])
        residual = float(sub["stop"].max()) - L
        event = int(sub["event"].max() == 1)
        rows.append((ph, residual, event))
    edf = pd.DataFrame(rows, columns=["ph", "residual", "event"])
    if edf["event"].sum() == 0:
        return float("nan")
    return concordance_index(edf["residual"], -edf["ph"], edf["event"])


def fit_and_score(cp_df, train_ids, holdout_ids, features, penalizer):
    cols = ["tenant_id", "start", "stop", "event", "operator_type"] + features
    train_df = cp_df[cp_df["tenant_id"].isin(train_ids)][cols].copy()
    hold_df = cp_df[cp_df["tenant_id"].isin(holdout_ids)][cols].copy()
    model = CoxTimeVaryingFitter(penalizer=penalizer)
    model.fit(train_df, id_col="tenant_id", event_col="event",
              start_col="start", stop_col="stop", strata=["operator_type"],
              show_progress=False)
    return {L: landmark_cindex(model, hold_df, L) for L in LANDMARKS}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_train")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    data = Path(args.data)
    tenants = json.loads((data / "tenants.json").read_text())
    observations = json.loads((data / "observations.json").read_text())
    cp_df = add_candidates(build_counting_process(tenants, observations))
    train_ids, holdout_ids = split_tenants(cp_df, 0.2, args.seed)

    experiments = {
        "baseline (p=0.1)":      (BASE, 0.1),
        "baseline (p=0.05)":     (BASE, 0.05),
        "baseline (p=0.0)":      (BASE, 0.0),
        "+rts_trend":            (BASE + ["rts_trend_12mo"], 0.1),
        "+rts_latest":           (BASE + ["rts_latest"], 0.1),
        "+foot_trend":           (BASE + ["foot_trend_12mo"], 0.1),
        "+stock_trend":          (BASE + ["stock_trend_6mo"], 0.1),
        "+rts_trend +foot":      (BASE + ["rts_trend_12mo", "foot_trend_12mo"], 0.1),
        "+all trajectory":       (BASE + ["rts_trend_12mo", "foot_trend_12mo", "stock_trend_6mo"], 0.1),
        "+rts_trend +latest":    (BASE + ["rts_trend_12mo", "rts_latest"], 0.1),
    }

    print(f"\nHoldout landmark c-index ({len(holdout_ids)} holdout tenants) — "
          f"baseline = the honest 3-branch ambient model\n")
    print(f"{'feature set':<24}{'n':>3}{'@12':>9}{'@24':>9}")
    print("-" * 45)
    for name, (feats, pen) in experiments.items():
        scores = fit_and_score(cp_df, train_ids, holdout_ids, feats, pen)
        flag = "  <-- >=0.70 @24" if scores[24] >= 0.70 else ""
        print(f"{name:<24}{len(feats):>3}{scores[12]:>9.3f}{scores[24]:>9.3f}{flag}")
    print("\n(Selection on holdout; pick the most parsimonious set that lifts @24 toward 0.70.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
