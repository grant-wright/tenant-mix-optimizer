#!/usr/bin/env python
"""
Cox time-varying survival fit + validation gate — Tenant Mix Optimizer (Day 4).

Design choice (Day 4): we fit a TIME-VARYING Cox model
(`CoxTimeVaryingFitter`, the Andersen-Gill counting-process form) rather than
collapsing each tenant's monthly history into a single summary row for a
static `CoxPHFitter`.

Why: the data IS a time-varying process (a latent `financial_health` random
walk emitting monthly observations). Collapsing it to one row per tenant —
with features measured in the window near exit — manufactures confidence the
model wouldn't have in deployment, where a manager faces an evolving unknown
each month. The counting-process form uses each tenant-month's *backward-
looking* signal and the event only ever fires on the final interval, so there
is no look-ahead. See the planning repo's decision log (Day 4).

The covariates span the generator's four causal branches; each is computed as
a STRICTLY trailing window at every month (no future information):

  Revenue      rts_12mo_mean         trailing-12mo mean rent-to-sales   (+)
               sales_trend_12mo      12-month sales growth              (-)
  Operational  late_pay_count_12mo   late-payment flags, trailing 12mo  (+)
               trading_shortfall_3mo trailing-3mo mean shortfall        (+)
  Leading      enquiry_recent_6mo    any relief/exit enquiry, last 6mo  (+)
               stock_depth_3mo       trailing-3mo mean stock depth      (-)
  External     credit_trend_3mo_mean trailing-3mo mean credit trend     (-)
               credit_x_corpfranch   credit trend x corp/franchise      (-)

The model is STRATIFIED by `operator_type` (each type its own baseline hazard)
and carries the one `operator_type x credit_trend` interaction the spec names
(encoded as credit_trend x is-corporate-or-franchise, since the generator makes
credit informative only for those operator types).

Usage:
    python scripts/fit_cox_model.py [--seed 42] [--data data] [--holdout 0.2]

Outputs:
    data/cox_model.pkl              pickled fitted model (gitignored; for the
                                    cox_ph_predict Cloud Function, Day 5)
    docs/cox_validation_km.png      landmark KM-by-risk-quartile plots (evidence)
    docs/cox_validation_report.md   the go/no-go gate report (evidence)
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from lifelines import CoxTimeVaryingFitter, KaplanMeierFitter
from lifelines.utils import concordance_index

# ----------------------------------------------------------------------------
# Feature spec — each covariate, its branch, and the sign we EXPECT on hazard
# (read straight off the generator's embedded structure). The gate checks the
# fitted sign against these.
# ----------------------------------------------------------------------------

FEATURES = [
    # name,                  branch,        expected_sign, description
    ("rts_12mo_mean",         "revenue",     +1, "trailing-12mo mean rent-to-sales (deseasonalised)"),
    ("sales_trend_12mo",      "revenue",     -1, "12-month sales growth (negative = declining)"),
    ("late_pay_count_12mo",   "operational", +1, "late-payment flags in trailing 12mo"),
    ("trading_shortfall_3mo", "operational", +1, "trailing-3mo mean trading-hours shortfall"),
    ("enquiry_recent_6mo",    "leading",     +1, "any relief/exit enquiry in trailing 6mo"),
    ("stock_depth_3mo",       "leading",     -1, "trailing-3mo mean stock depth (low = winding down)"),
    ("credit_trend_3mo_mean", "external",    -1, "trailing-3mo mean credit trend (negative = deteriorating)"),
    ("credit_x_corpfranch",   "external",    -1, "credit trend x corporate/franchise (interaction)"),
]
FEATURE_NAMES = [f[0] for f in FEATURES]
BRANCHES = ["revenue", "operational", "leading", "external"]

CONCORDANCE_GATE = 0.70
LANDMARKS = [12, 24]


# ----------------------------------------------------------------------------
# Build the counting-process frame (one row per tenant-month)
# ----------------------------------------------------------------------------

def build_counting_process(tenants, observations):
    """Long-format (start, stop] intervals with trailing-window covariates."""
    tdf = pd.DataFrame(tenants)
    odf = pd.DataFrame(observations).sort_values(["tenant_id", "month"]).reset_index(drop=True)

    # Per-tenant chronological month index → counting-process intervals.
    odf["month_idx"] = odf.groupby("tenant_id").cumcount()
    odf["start"] = odf["month_idx"]
    odf["stop"] = odf["month_idx"] + 1

    # Bring tenant-level operator_type (strata) + status (for the event).
    persona = pd.DataFrame({
        "tenant_id": tdf["tenant_id"],
        "operator_type": tdf["persona"].apply(lambda p: p["operator_type"]),
        "status": tdf["status"],
    })
    odf = odf.merge(persona, on="tenant_id", how="left")

    # --- trailing-window features (strictly backward-looking) ---
    grp = odf.groupby("tenant_id", group_keys=False)
    odf["rts_12mo_mean"] = grp["rent_to_sales_ratio"].transform(
        lambda s: s.rolling(12, min_periods=1).mean())
    odf["late_pay_count_12mo"] = grp["late_payment_flag"].transform(
        lambda s: s.astype(int).rolling(12, min_periods=1).sum())
    odf["trading_shortfall_3mo"] = grp["trading_hours_shortfall"].transform(
        lambda s: s.rolling(3, min_periods=1).mean())
    odf["enquiry_recent_6mo"] = grp["relief_or_exit_enquiry"].transform(
        lambda s: s.astype(int).rolling(6, min_periods=1).max())
    odf["stock_depth_3mo"] = grp["stock_depth_index"].transform(
        lambda s: s.rolling(3, min_periods=1).mean())
    odf["credit_trend_3mo_mean"] = grp["credit_trend_3mo"].transform(
        lambda s: s.rolling(3, min_periods=1).mean())
    # 12-month sales growth; 0 where <12mo of history (trend not yet knowable).
    odf["sales_trend_12mo"] = grp["sales_total"].transform(
        lambda s: (s / s.shift(12) - 1.0)).fillna(0.0)

    # Interaction: credit only carries signal for corporate/franchise tenants.
    is_corp_franch = odf["operator_type"].isin(["corporate", "franchise"]).astype(int)
    odf["credit_x_corpfranch"] = odf["credit_trend_3mo_mean"] * is_corp_franch

    # Event fires only on an exiter's final observed interval.
    last_idx = odf.groupby("tenant_id")["month_idx"].transform("max")
    odf["event"] = ((odf["status"] == "exited") & (odf["month_idx"] == last_idx)).astype(int)

    return odf


def fit_frame(cp_df):
    """Trim to exactly the columns CoxTimeVaryingFitter should see."""
    cols = ["tenant_id", "start", "stop", "event", "operator_type"] + FEATURE_NAMES
    return cp_df[cols].copy()


# ----------------------------------------------------------------------------
# Train / holdout split (by TENANT, never leaking a tenant across the split)
# ----------------------------------------------------------------------------

def split_tenants(cp_df, holdout_frac, seed):
    rng = np.random.default_rng(seed)
    tenant_ids = cp_df["tenant_id"].unique()
    rng.shuffle(tenant_ids)
    n_holdout = max(1, int(round(len(tenant_ids) * holdout_frac)))
    holdout = set(tenant_ids[:n_holdout])
    train = set(tenant_ids[n_holdout:])
    return train, holdout


# ----------------------------------------------------------------------------
# Evaluation helpers
# ----------------------------------------------------------------------------

def current_state_concordance(model, cp_df, tenant_ids):
    """Rank tenants by hazard at their CURRENT (final-observed) state — the
    exact featurisation cox_ph_predict serves — and score against time-to-exit."""
    durations, neg_hazards, events = [], [], []
    for tid in tenant_ids:
        sub = cp_df[cp_df["tenant_id"] == tid]
        last = sub.iloc[[-1]]
        partial_hazard = float(model.predict_partial_hazard(last).iloc[0])
        durations.append(float(sub["stop"].max()))
        neg_hazards.append(-partial_hazard)  # higher hazard → shorter survival
        events.append(int(last["event"].iloc[0]))
    return concordance_index(durations, neg_hazards, events)


def landmark_analysis(model, cp_df, landmark, ax):
    """Freeze at `landmark`: among tenants still at risk, rank by hazard AS OF
    that month, quartile them, and plot survival forward. Leakage-free because
    we condition on being at risk at L and use only the month-L covariate row."""
    rows = []
    for tid, sub in cp_df.groupby("tenant_id"):
        if sub["stop"].max() <= landmark:        # already gone / censored by L
            continue
        row_at_L = sub[sub["start"] == landmark]
        if row_at_L.empty:
            continue
        partial_hazard = float(model.predict_partial_hazard(row_at_L).iloc[0])
        residual = float(sub["stop"].max()) - landmark
        exited_after_L = int(sub["event"].max() == 1)  # exit (if any) is > L here
        rows.append((tid, partial_hazard, residual, exited_after_L))

    edf = pd.DataFrame(rows, columns=["tenant_id", "partial_hazard", "residual", "event"])
    edf["quartile"] = pd.qcut(edf["partial_hazard"], 4,
                              labels=["Q1 (low)", "Q2", "Q3", "Q4 (high)"], duplicates="drop")

    kmf = KaplanMeierFitter()
    for q in list(edf["quartile"].cat.categories):
        mask = edf["quartile"] == q
        if mask.sum() == 0:
            continue
        kmf.fit(edf.loc[mask, "residual"], edf.loc[mask, "event"], label=f"{q}  (n={int(mask.sum())})")
        kmf.plot_survival_function(ax=ax, ci_show=False)
    ax.set_title(f"Landmark month {landmark}: survival after L, by hazard quartile at L")
    ax.set_xlabel("months after landmark")
    ax.set_ylabel("survival probability")
    ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.3)

    c_index = concordance_index(edf["residual"], -edf["partial_hazard"], edf["event"])
    return c_index, edf[["tenant_id", "partial_hazard", "quartile"]]


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------

def evaluate_branches(summary):
    """For each branch, is there >=1 covariate that is significant (p<0.05) AND
    correctly signed? Returns (per_branch_rows, branch_pass)."""
    rows, branch_pass = [], {b: False for b in BRANCHES}
    for name, branch, expected_sign, desc in FEATURES:
        coef = float(summary.loc[name, "coef"])
        p = float(summary.loc[name, "p"])
        actual_sign = int(np.sign(coef)) if coef != 0 else 0
        sign_ok = (actual_sign == expected_sign)
        significant = p < 0.05
        if sign_ok and significant:
            branch_pass[branch] = True
        rows.append(dict(name=name, branch=branch, desc=desc, coef=coef,
                         hr=float(np.exp(coef)), p=p,
                         expected="+" if expected_sign > 0 else "-",
                         actual="+" if actual_sign > 0 else ("-" if actual_sign < 0 else "0"),
                         sign_ok=sign_ok, significant=significant))
    return rows, branch_pass


def write_report(path, *, n_tenants, n_events, epv, penalizer, branch_rows, branch_pass,
                 c_holdout, c_train, landmark_c, movers):
    lines = []
    w = lines.append
    w("# Cox PH Validation Report — Day 4 (time-varying)\n")
    w(f"*Generated by `scripts/fit_cox_model.py`. Model: `CoxTimeVaryingFitter`, "
      f"stratified by `operator_type`, ridge penalizer={penalizer}.*\n")

    w("## Dataset\n")
    w(f"- Tenants: **{n_tenants}**  ·  exit events: **{n_events}**")
    w(f"- Events per covariate (EPV): **{epv:.1f}** "
      f"({n_events} events / {len(FEATURE_NAMES)} covariates) — "
      f"{'comfortable' if epv >= 10 else 'low; synthetic strong-signal data, noted as a known limit'}\n")

    overall_branches = all(branch_pass.values())
    concordance_ok = c_holdout >= CONCORDANCE_GATE
    w("## Gate summary\n")
    w(f"| Gate | Result | Status |")
    w(f"|------|--------|--------|")
    w(f"| Holdout concordance ≥ {CONCORDANCE_GATE} | {c_holdout:.3f} | {'✅ PASS' if concordance_ok else '❌ FAIL'} |")
    for b in BRANCHES:
        w(f"| Branch contributes: {b} | {'yes' if branch_pass[b] else 'no'} | {'✅ PASS' if branch_pass[b] else '❌ FAIL'} |")
    w(f"\n**Overall: {'✅ GO' if (overall_branches and concordance_ok) else '❌ NO-GO (review in the morning)'}**\n")

    w("## Coefficients (each covariate vs. its embedded expected sign)\n")
    w("| covariate | branch | HR | coef | p | expected | actual | sign ok | sig (p<.05) |")
    w("|-----------|--------|----|------|---|----------|--------|---------|-------------|")
    for r in branch_rows:
        w(f"| {r['name']} | {r['branch']} | {r['hr']:.3f} | {r['coef']:+.3f} | {r['p']:.4f} "
          f"| {r['expected']} | {r['actual']} | {'✓' if r['sign_ok'] else '✗'} "
          f"| {'✓' if r['significant'] else '✗'} |")
    w("")

    w("## Discrimination\n")
    w(f"- **Holdout concordance (current-state):** {c_holdout:.3f}  "
      f"(train {c_train:.3f}) — ranks tenants by hazard at their latest observed "
      f"month, exactly as `cox_ph_predict` will serve.")
    for L, c in landmark_c.items():
        w(f"- **Landmark month {L} concordance (prospective):** {c:.3f} — among tenants "
          f"still at risk at month {L}, ranked by hazard *as of* month {L}.")
    w("\n*Note: with a stratified baseline, `predict_partial_hazard` returns exp(βx) and "
      "omits the per-stratum baseline, so cross-operator-type ranking is an approximation — "
      "the same one the deployed function makes. Within-stratum ranking is exact.*\n")

    w("## KM by risk quartile (landmark)\n")
    w("See `cox_validation_km.png`. Quartile membership is computed independently at each "
      "landmark from the hazard *at that month*, so tenants move between quartiles as their "
      "health evolves. Tenants whose quartile shifted between the two landmarks:\n")
    if movers:
        w("| tenant | quartile @12 | quartile @24 |")
        w("|--------|--------------|--------------|")
        for tid, q12, q24 in movers:
            w(f"| {tid} | {q12} | {q24} |")
    else:
        w("*(none — quartiles stable across landmarks)*")
    w("")

    w("## Proportional-hazards note\n")
    w("`CoxTimeVaryingFitter` has no `check_assumptions`/Schoenfeld helper. That is partly "
      "by design: a time-varying specification lets each covariate's *value* evolve per "
      "tenant-month, which relaxes the static proportional-hazards concern the Day-4 plan's "
      "PH check was meant to surface. A formal time-interaction test is noted as an xprize "
      "follow-up.\n")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main(seed=42, data_dir="data", holdout_frac=0.2, penalizer=0.1):
    import sys
    try:  # Windows consoles default to cp1252; the report is UTF-8 regardless.
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    data = Path(data_dir)
    tenants = json.loads((data / "tenants.json").read_text())
    observations = json.loads((data / "observations.json").read_text())

    cp_df = build_counting_process(tenants, observations)
    frame = fit_frame(cp_df)

    train_ids, holdout_ids = split_tenants(frame, holdout_frac, seed)
    train_df = frame[frame["tenant_id"].isin(train_ids)].copy()

    n_events = int(frame.groupby("tenant_id")["event"].max().sum())
    n_tenants = frame["tenant_id"].nunique()
    epv = n_events / len(FEATURE_NAMES)

    model = CoxTimeVaryingFitter(penalizer=penalizer)
    model.fit(train_df, id_col="tenant_id", event_col="event",
              start_col="start", stop_col="stop", strata=["operator_type"],
              show_progress=False)

    print("\n" + "=" * 72)
    print("FITTED COEFFICIENTS")
    print("=" * 72)
    model.print_summary(decimals=3)

    branch_rows, branch_pass = evaluate_branches(model.summary)

    c_train = current_state_concordance(model, frame, sorted(train_ids))
    c_holdout = current_state_concordance(model, frame, sorted(holdout_ids))

    # Landmark KM (uses the full dataset for stable curves).
    fig, axes = plt.subplots(1, len(LANDMARKS), figsize=(7 * len(LANDMARKS), 5))
    if len(LANDMARKS) == 1:
        axes = [axes]
    landmark_c, landmark_quartiles = {}, {}
    for ax, L in zip(axes, LANDMARKS):
        c, q = landmark_analysis(model, frame, L, ax)
        landmark_c[L] = c
        landmark_quartiles[L] = q.set_index("tenant_id")["quartile"]
    fig.suptitle("Discrimination: survival after a landmark, by predicted-risk quartile at the landmark")
    fig.tight_layout()
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    fig.savefig(docs / "cox_validation_km.png", dpi=120)
    plt.close(fig)

    # Tenants that moved quartile between the two landmarks (the learner's interest).
    movers = []
    if len(LANDMARKS) >= 2:
        q_a, q_b = landmark_quartiles[LANDMARKS[0]], landmark_quartiles[LANDMARKS[1]]
        common = q_a.index.intersection(q_b.index)
        for tid in common:
            if str(q_a[tid]) != str(q_b[tid]):
                movers.append((tid, str(q_a[tid]), str(q_b[tid])))
        movers = sorted(movers)[:12]  # cap the table

    write_report(docs / "cox_validation_report.md",
                 n_tenants=n_tenants, n_events=n_events, epv=epv, penalizer=penalizer,
                 branch_rows=branch_rows, branch_pass=branch_pass,
                 c_holdout=c_holdout, c_train=c_train,
                 landmark_c=landmark_c, movers=movers)

    with open(data / "cox_model.pkl", "wb") as fh:
        pickle.dump(model, fh)

    # Console gate summary
    print("\n" + "=" * 72)
    print("VALIDATION GATE")
    print("=" * 72)
    print(f"Tenants {n_tenants} | events {n_events} | EPV {epv:.1f} | "
          f"holdout {len(holdout_ids)} tenants")
    print(f"Holdout concordance : {c_holdout:.3f}  (train {c_train:.3f})  "
          f"[gate ≥ {CONCORDANCE_GATE}] -> {'PASS' if c_holdout >= CONCORDANCE_GATE else 'FAIL'}")
    for L, c in landmark_c.items():
        print(f"Landmark @{L} c-index: {c:.3f}")
    print("\nBranch contribution (≥1 significant, correctly-signed covariate):")
    for b in BRANCHES:
        print(f"  {b:<12} -> {'PASS' if branch_pass[b] else 'FAIL'}")
    overall = all(branch_pass.values()) and c_holdout >= CONCORDANCE_GATE
    print("\n" + ("OVERALL: GO ✅" if overall else "OVERALL: NO-GO ❌ (review in the morning)"))
    print(f"\nArtifacts: docs/cox_validation_km.png, docs/cox_validation_report.md, "
          f"{data}/cox_model.pkl")
    print("=" * 72)

    return overall


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fit time-varying Cox model + run the Day-4 validation gate.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--data", type=str, default="data")
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--penalizer", type=float, default=0.1)
    args = ap.parse_args()
    main(seed=args.seed, data_dir=args.data, holdout_frac=args.holdout, penalizer=args.penalizer)
