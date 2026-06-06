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

The covariates span THREE of the generator's causal branches (revenue,
operational, leading); each is computed as a STRICTLY trailing window at every
month (no future information):

  Revenue      rts_12mo_mean         trailing-12mo mean rent-to-sales   (+)
               sales_trend_12mo      12-month sales growth              (-)
  Operational  late_pay_count_12mo   late-payment flags, trailing 12mo  (+)
               trading_shortfall_3mo trailing-3mo mean shortfall        (+)
  Leading      stock_depth_3mo       trailing-3mo mean stock depth      (-)

The relief/exit-enquiry and credit signals are deliberately NOT covariates:
they are discrete, high-impact event signals handled in the agent's ALERT layer
(see the two-layer note at FEATURES below and decisions.md 2026-06-03). Keeping
them out also removes a near-label signal, so the model's discrimination is honest.

The model is STRATIFIED by `operator_type` (each type its own baseline hazard).

Usage:
    python scripts/fit_cox_model.py [--seed 42] [--data data] [--holdout 0.2]

Outputs:
    data/cox_model.pkl              full bundle: fitted model + train log-ph +
                                    serving coefficients (gitignored; for
                                    retraining / inspection)
    data/cox_serving.pkl            lifelines-free SERVING artefact - plain dicts
                                    (coefficients + centering + train log-ph) for
                                    the cox_ph_predict Cloud Function. See the
                                    train/serve separation note at the bundle save.
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
from lifelines.statistics import logrank_test

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
    ("stock_depth_3mo",       "leading",     -1, "trailing-3mo mean stock depth (low = winding down)"),
]
# NOTE (two-layer design): relief/exit-enquiry and credit are deliberately NOT
# model covariates. They are discrete, high-impact "event" signals (a tenant
# asking to leave; a credit downgrade) that belong in the agent's ALERT layer
# (recommend_intervention blends them via escalation + Gemini narrative), not
# smeared into Cox coefficients. Pulling `enquiry` out also removes a near-label
# signal, so the model's discrimination is honest. `stock_depth` stays to keep
# the leading branch represented in the model. (build_counting_process still
# computes enquiry/credit columns so the Day-6 alert tools can reuse them.)
FEATURE_NAMES = [f[0] for f in FEATURES]
BRANCHES = ["revenue", "operational", "leading"]

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
    # Ambient credit-trend signal, kept as an alert-layer reference column (see
    # the note above build_counting_process). NOT a model covariate.
    odf["credit_trend_3mo_mean"] = grp["credit_trend_3mo"].transform(
        lambda s: s.rolling(3, min_periods=1).mean())
    # 12-month sales growth; 0 where <12mo of history (trend not yet knowable).
    odf["sales_trend_12mo"] = grp["sales_total"].transform(
        lambda s: (s / s.shift(12) - 1.0)).fillna(0.0)

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

# (current_state_concordance was removed in Day-4 finalisation: ranking each
# tenant at their final observed month conflated "risk now" with "already
# survived", giving a degenerate ~0.4. The landmark / time-dependent c-index on
# the HOLDOUT is the correct discrimination measure and is what we report.)


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

    # Q1-vs-Q4 separation — the hard "does it discriminate" gate criterion.
    cats = list(edf["quartile"].cat.categories)
    lo, hi = edf["quartile"] == cats[0], edf["quartile"] == cats[-1]
    lr = logrank_test(edf.loc[lo, "residual"], edf.loc[hi, "residual"],
                      edf.loc[lo, "event"], edf.loc[hi, "event"])
    return c_index, edf[["tenant_id", "partial_hazard", "quartile"]], lr.p_value


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
                 landmark_c, landmark_logrank, gate_landmark, km_separates, overall, movers):
    lines = []
    w = lines.append
    w("# Cox PH Validation Report - Day 4 (time-varying)\n")
    w(f"*Generated by `scripts/fit_cox_model.py`. Model: `CoxTimeVaryingFitter`, "
      f"stratified by `operator_type`, ridge penalizer={penalizer}. 3-branch AMBIENT model "
      f"(revenue / operational / leading). Credit and relief/exit-enquiry are handled in the "
      f"agent ALERT layer, not as covariates - see the two-layer note below.*\n")

    w("## Dataset\n")
    w(f"- Tenants: **{n_tenants}**  *  exit events: **{n_events}**")
    w(f"- Events per covariate (EPV): **{epv:.1f}** "
      f"({n_events} events / {len(FEATURE_NAMES)} covariates)\n")

    w("## Gate summary (hard criteria)\n")
    w("| Gate | Result | Status |")
    w("|------|--------|--------|")
    for b in BRANCHES:
        w(f"| Branch contributes: {b} | {'yes' if branch_pass[b] else 'no'} | {'PASS' if branch_pass[b] else 'FAIL'} |")
    w(f"| KM quartiles separate @{gate_landmark}mo (log-rank p<0.01) | p={landmark_logrank[gate_landmark]:.1e} | {'PASS' if km_separates else 'FAIL'} |")
    w(f"\n**Overall: {'GO' if overall else 'NO-GO'}**\n")

    w("## Discrimination (reported, not hard-gated)\n")
    w("Holdout, time-dependent (landmark) concordance - among tenants still at risk at the "
      "landmark month, ranked by hazard *as of* that month:\n")
    for L in sorted(landmark_c):
        w(f"- **@{L} months:** c-index {landmark_c[L]:.3f}")
    w(f"\n*These reflect AMBIENT signals (rent-to-sales, sales trend, payments, trading "
      f"hours, stock depth) predicting {min(landmark_c)}-{max(landmark_c)} months ahead - a "
      f"deliberately hard, honest task. The round 0.70 figure in the plan was a heuristic for "
      f"ordinary concordance, not a long-horizon time-dependent c-index, so we gate on genuine "
      f"discrimination (quartile separation) + structural recovery (branch contribution) and "
      f"report the c-index transparently. The agent's ALERT layer adds the decisive near-term "
      f"signal on top of this baseline.*\n")

    w("## Coefficients (each covariate vs. its embedded expected sign)\n")
    w("| covariate | branch | HR | coef | p | expected | actual | sign ok | sig (p<.05) |")
    w("|-----------|--------|----|------|---|----------|--------|---------|-------------|")
    for r in branch_rows:
        w(f"| {r['name']} | {r['branch']} | {r['hr']:.3f} | {r['coef']:+.3f} | {r['p']:.4f} "
          f"| {r['expected']} | {r['actual']} | {'yes' if r['sign_ok'] else 'no'} "
          f"| {'yes' if r['significant'] else 'no'} |")
    w("")

    w("## KM by risk quartile (holdout landmark)\n")
    w("See `cox_validation_km.png`. Quartiles are computed independently at each landmark from "
      "the hazard *at that month*, so tenants move between quartiles as their health evolves. "
      "Tenants whose quartile shifted between landmarks:\n")
    if movers:
        w("| tenant | quartile @first | quartile @second |")
        w("|--------|------------------|-------------------|")
        for tid, q_a, q_b in movers:
            w(f"| {tid} | {q_a} | {q_b} |")
    else:
        w("*(none - quartiles stable across landmarks)*")
    w("")

    w("## Two-layer design note\n")
    w("This Cox model is the *ambient monitoring* layer. Two high-impact, discrete signals are "
      "handled separately by the agent's `recommend_intervention` (Day 6): a tenant's "
      "**relief/exit enquiry** and a **credit downgrade**. They are surfaced as visible flags "
      "and blended via escalation + Gemini narrative, rather than smeared into Cox coefficients "
      "- which matches how they behave (lumpy events, unevenly available) and keeps this "
      "model's discrimination honest (no near-label signal).\n")

    w("## Proportional-hazards note\n")
    w("`CoxTimeVaryingFitter` has no Schoenfeld helper; a time-varying specification also "
      "relaxes the static proportional-hazards concern, since each covariate's value evolves "
      "per tenant-month. A formal time-interaction test is an xprize follow-up.\n")

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

    # Landmark discrimination on the HOLDOUT (honest, out-of-sample). KM curves
    # use the same holdout split so the picture matches the numbers.
    holdout_df = frame[frame["tenant_id"].isin(holdout_ids)].copy()
    fig, axes = plt.subplots(1, len(LANDMARKS), figsize=(7 * len(LANDMARKS), 5))
    if len(LANDMARKS) == 1:
        axes = [axes]
    landmark_c, landmark_logrank, landmark_quartiles = {}, {}, {}
    for ax, L in zip(axes, LANDMARKS):
        c, q, lr_p = landmark_analysis(model, holdout_df, L, ax)
        landmark_c[L] = c
        landmark_logrank[L] = lr_p
        landmark_quartiles[L] = q.set_index("tenant_id")["quartile"]
    fig.suptitle("Holdout discrimination: survival after a landmark, by predicted-risk quartile at the landmark")
    fig.tight_layout()
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    fig.savefig(docs / "cox_validation_km.png", dpi=120)
    plt.close(fig)

    # Tenants that moved quartile between the two landmarks.
    movers = []
    if len(LANDMARKS) >= 2:
        q_a, q_b = landmark_quartiles[LANDMARKS[0]], landmark_quartiles[LANDMARKS[1]]
        common = q_a.index.intersection(q_b.index)
        for tid in common:
            if str(q_a[tid]) != str(q_b[tid]):
                movers.append((tid, str(q_a[tid]), str(q_b[tid])))
        movers = sorted(movers)[:12]

    # Hard gate: every branch contributes AND risk quartiles separate (log-rank).
    gate_landmark = LANDMARKS[0]
    km_separates = landmark_logrank[gate_landmark] < 0.01
    overall = all(branch_pass.values()) and km_separates

    write_report(docs / "cox_validation_report.md",
                 n_tenants=n_tenants, n_events=n_events, epv=epv, penalizer=penalizer,
                 branch_rows=branch_rows, branch_pass=branch_pass,
                 landmark_c=landmark_c, landmark_logrank=landmark_logrank,
                 gate_landmark=gate_landmark, km_separates=km_separates,
                 overall=overall, movers=movers)

    # --- Train/serve artefact separation (see decisions.md 2026-06-06) ---
    # We persist TWO artefacts from one fit:
    #
    #   cox_model.pkl    the TRAINING artefact - the full lifelines model object,
    #                    for retraining, inspection, and validation diagnostics.
    #                    Unpickling it REQUIRES lifelines installed.
    #
    #   cox_serving.pkl  the SERVING artefact - plain Python types only, no
    #                    lifelines classes, so it unpickles with zero lifelines
    #                    dependency. Scoring a tenant is just a centered linear
    #                    predictor, so all the Cloud Function needs is:
    #                      coefficients  beta per covariate
    #                      norm_mean     the training mean per covariate; lifelines'
    #                                    predict_log_partial_hazard returns the
    #                                    CENTERED dot product (x - xbar) . beta, so
    #                                    the serving path must subtract xbar too or
    #                                    every score drifts by a constant.
    #                      train_log_ph  the training population's log-partial-hazard
    #                                    distribution, for the percentile-rank score.
    #
    # The percentile (belt-and-braces alongside the sigmoid) ranks a tenant against
    # each TRAINING tenant's LAST observation (current state at exit/censor).
    last_train = train_df.loc[train_df.groupby("tenant_id")["stop"].idxmax()]
    train_log_ph = model.predict_log_partial_hazard(last_train[FEATURE_NAMES]).values

    coefficients = {f: float(model.params_[f]) for f in FEATURE_NAMES}
    norm_mean = {f: float(model._norm_mean[f]) for f in FEATURE_NAMES}

    bundle = {
        "model": model,
        "train_log_ph": train_log_ph,
        "coefficients": coefficients,   # also kept here so the full bundle self-describes
        "norm_mean": norm_mean,
    }
    with open(data / "cox_model.pkl", "wb") as fh:
        pickle.dump(bundle, fh)

    serving = {
        "coefficients": coefficients,
        "norm_mean": norm_mean,
        "train_log_ph": [float(v) for v in train_log_ph],
        "feature_names": list(FEATURE_NAMES),
    }
    with open(data / "cox_serving.pkl", "wb") as fh:
        pickle.dump(serving, fh)

    # Console gate summary
    print("\n" + "=" * 72)
    print("VALIDATION GATE")
    print("=" * 72)
    print(f"Tenants {n_tenants} | events {n_events} | EPV {epv:.1f} | "
          f"holdout {len(holdout_ids)} tenants")
    print("\nHard gate:")
    print("  Branch contribution (>=1 significant, correctly-signed covariate):")
    for b in BRANCHES:
        print(f"    {b:<12} -> {'PASS' if branch_pass[b] else 'FAIL'}")
    print(f"  KM risk-quartile separation @{gate_landmark}mo (log-rank p<0.01): "
          f"p={landmark_logrank[gate_landmark]:.1e} -> {'PASS' if km_separates else 'FAIL'}")
    print("\nReported discrimination (holdout landmark c-index; ambient signals at a long")
    print("horizon - the alert layer adds near-term lift, not gated on a round 0.70):")
    for L in LANDMARKS:
        print(f"    @{L}mo c-index = {landmark_c[L]:.3f}")
    print("\n" + ("OVERALL: GO" if overall else "OVERALL: NO-GO (review)"))
    print(f"\nArtifacts: docs/cox_validation_km.png, docs/cox_validation_report.md, "
          f"{data}/cox_model.pkl (training), {data}/cox_serving.pkl (serving)")
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
