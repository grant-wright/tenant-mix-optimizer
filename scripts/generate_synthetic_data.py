#!/usr/bin/env python
"""
Synthetic tenant data generator — Tenant Mix Optimizer.

Implements the v5 "branched generator" design: a single latent
`financial_health` random walk drives four INDEPENDENT, landlord-observable
signal branches plus the exit outcome. The Cox PH model (Day 4) recovers the
structure embedded here.

Branches (see docs/manuals + the planning repo's synthetic-data-strategy.md v5):
  1. Revenue      — sales, sales_per_sqft, rent_to_sales_ratio, foot_traffic
  2. Operational  — late_payment_flag (strong), trading_hours_shortfall (moderate)
  3. Leading      — relief_or_exit_enquiry (weak + randomised lead time, anti-leak),
                    stock_depth_index (moderate)
  4. External     — credit_trend_3mo (third-party; informative for corporate/
                    franchise, thin for owner-operators)

The outcome (exit) descends from the latent directly, NOT from the features.

Usage:
    python scripts/generate_synthetic_data.py [--seed 42] [--background 70] [--out data]

Outputs (JSON arrays):
    data/tenants.json        one record per tenant (static + persona + lease + status)
    data/observations.json   one record per tenant-month (the four-branch features)
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

N_MONTHS = 36
SIM_END = (2026, 6)  # last observed month (sim "now")

# Latent random walk
DRIFT = -0.005
WALK_NOISE = 0.04

# Exit hazard from latent: p_exit(month) = sigmoid(EXIT_BASE + EXIT_K * stress)
EXIT_BASE = -5.8
EXIT_K = 4.5

# Coupling: logistic slope/intercept and additive-noise levels per the design.
LATE_INTERCEPT, LATE_SLOPE = -1.0, 4.0          # operational, STRONG
TRADING_GAIN, TRADING_NOISE = 0.6, 0.08         # operational, moderate
STOCK_GAIN, STOCK_NOISE = 0.5, 0.10             # leading, moderate
CREDIT_NOISE_CORP = 0.015                       # external, moderate (corp/franchise)
CREDIT_NOISE_THIN = 0.012                       # external, near-zero signal (owner-op)

# Anti-leakage for relief_or_exit_enquiry
ENQUIRY_LEAD_MIN, ENQUIRY_LEAD_MAX = 2, 9       # months before exit
ENQUIRY_FALSE_NEG = 0.30                         # exiters with no prior enquiry
ENQUIRY_FALSE_POS_RATE = 0.02                    # monthly, for chronically-stressed survivors

# Seasonality: calendar-month multiplier (retail peaks Nov/Dec).
SEASON_BY_MONTH = {
    1: 0.85, 2: 0.88, 3: 0.97, 4: 1.00, 5: 1.02, 6: 1.00,
    7: 0.98, 8: 1.00, 9: 1.02, 10: 1.08, 11: 1.18, 12: 1.38,
}

# Category configs. sales_psf / rent are monthly $/sqft. healthy_rts = baseline
# rent-to-sales band. ops = operator_type sampling weights. season_amp scales
# how strongly the category feels seasonality.
CATEGORIES = {
    "apparel_boutique":        dict(sales_psf=(22, 38), sqft=(800, 1600),  healthy_rts=(0.12, 0.15), season_amp=1.0,
                                    ops={"owner_operator": 0.70, "franchise": 0.20, "corporate": 0.10}),
    "food_court":              dict(sales_psf=(35, 60), sqft=(300, 700),   healthy_rts=(0.08, 0.12), season_amp=0.8,
                                    ops={"owner_operator": 0.20, "franchise": 0.60, "corporate": 0.20}),
    "full_service_restaurant": dict(sales_psf=(28, 50), sqft=(1500, 3500), healthy_rts=(0.10, 0.15), season_amp=0.7,
                                    ops={"owner_operator": 0.50, "franchise": 0.30, "corporate": 0.20}),
    "phone_repair":            dict(sales_psf=(20, 40), sqft=(150, 400),   healthy_rts=(0.06, 0.10), season_amp=0.5,
                                    ops={"owner_operator": 0.30, "franchise": 0.60, "corporate": 0.10}),
    "bookstore":               dict(sales_psf=(15, 28), sqft=(1000, 2500), healthy_rts=(0.07, 0.11), season_amp=0.9,
                                    ops={"owner_operator": 0.60, "franchise": 0.20, "corporate": 0.20}),
    "salon_beauty":            dict(sales_psf=(25, 45), sqft=(400, 900),   healthy_rts=(0.10, 0.16), season_amp=0.6,
                                    ops={"owner_operator": 0.60, "franchise": 0.30, "corporate": 0.10}),
    "specialty_jewellery":     dict(sales_psf=(30, 70), sqft=(300, 700),   healthy_rts=(0.04, 0.07), season_amp=1.1,
                                    ops={"owner_operator": 0.70, "franchise": 0.15, "corporate": 0.15}),
    "chain_apparel":           dict(sales_psf=(28, 48), sqft=(1500, 4000), healthy_rts=(0.10, 0.14), season_amp=1.0,
                                    ops={"owner_operator": 0.05, "franchise": 0.35, "corporate": 0.60}),
}

ANCHOR_CATEGORIES = {
    "anchor_dept_store":  dict(sales_psf=(12, 20), sqft=(8000, 15000), healthy_rts=(0.03, 0.05), season_amp=1.0),
    "anchor_supermarket": dict(sales_psf=(18, 28), sqft=(6000, 12000), healthy_rts=(0.02, 0.04), season_amp=0.4),
}

ZONES = ["north", "south", "east", "west"]

RISK_BASE = {"cautious": 0.30, "balanced": 0.50, "bold": 0.20}


# ----------------------------------------------------------------------------
# Small maths helpers
# ----------------------------------------------------------------------------

def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def stress_of(health: float) -> float:
    return float(np.clip(1.0 - health, 0.0, 1.0))


def health_mult(health: float) -> float:
    """Sales multiplier from latent health (clamped to a sane range)."""
    return float(np.clip(health, 0.30, 1.50))


def months_back(end=SIM_END, n=N_MONTHS):
    """Ascending list of (year, month) ending at `end`, length n."""
    y, m = end
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def month_str(ym) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


def first_of_month(ym) -> date:
    return date(ym[0], ym[1], 1)


def add_months(ym, k):
    y, m = ym
    total = (y * 12 + (m - 1)) + k
    return (total // 12, total % 12 + 1)


# ----------------------------------------------------------------------------
# Latent + branch generation
# ----------------------------------------------------------------------------

def gen_health_path(rng, n, start=1.0, zone_anchor=None, anchor_coupling=0.12):
    """Random walk with drift; optionally nudged by the zone anchor's health."""
    h = np.empty(n)
    h[0] = start
    for t in range(1, n):
        step = DRIFT + rng.normal(0.0, WALK_NOISE)
        if zone_anchor is not None:
            # When the anchor is below its own baseline, neighbours feel it.
            step += anchor_coupling * (zone_anchor[t] - 1.0)
        h[t] = h[t - 1] + step
    return h


def sample_exit_index(rng, health_path):
    """First month the latent-driven hazard fires, else None (censored/active)."""
    for t in range(len(health_path)):
        p = sigmoid(EXIT_BASE + EXIT_K * stress_of(health_path[t]))
        if rng.random() < p:
            return t
    return None


def credit_score(health: float) -> float:
    """Map latent health to a notional credit score (~300–850)."""
    return 650.0 + 180.0 * (health - 1.0)


def build_observations(rng, calendar, health, static):
    """Compute the four-branch monthly features from the health path."""
    n = len(calendar)
    base_psf = static["base_psf"]
    rent_psf = static["rent_psf"]
    sqft = static["sqft"]
    season_amp = static["season_amp"]
    operator_type = static["operator_type"]
    zone_anchor = static.get("zone_anchor")

    obs = []
    for t, ym in enumerate(calendar):
        h = health[t]
        stress = stress_of(h)
        season = 1.0 + (SEASON_BY_MONTH[ym[1]] - 1.0) * season_amp

        # Branch 1 — revenue
        sales_psf = base_psf * health_mult(h) * season
        sales_total = sales_psf * sqft
        rent_to_sales = (rent_psf * sqft) / sales_total  # == rent_psf / sales_psf
        anchor_mod = 1.0 if zone_anchor is None else float(np.clip(0.7 + 0.3 * zone_anchor[t], 0.4, 1.2))
        foot_traffic = static["foot_base"] * season * anchor_mod * (1.0 + rng.normal(0, 0.05))

        # Branch 2 — operational (landlord-observed)
        late_payment = rng.random() < sigmoid(LATE_INTERCEPT + LATE_SLOPE * stress)
        trading_hours_shortfall = float(np.clip(TRADING_GAIN * stress + rng.normal(0, TRADING_NOISE), 0.0, 1.0))

        # Branch 3 — leading (relief_or_exit_enquiry injected later, anti-leak)
        stock_depth_index = float(np.clip(1.0 - STOCK_GAIN * stress + rng.normal(0, STOCK_NOISE), 0.0, 1.0))

        # Branch 4 — external / third-party
        if operator_type in ("corporate", "franchise"):
            if t >= 3:
                raw = (credit_score(health[t]) - credit_score(health[t - 3])) / 100.0
            else:
                raw = 0.0
            credit_trend = float(np.clip(raw + rng.normal(0, CREDIT_NOISE_CORP), -0.40, 0.40))
        else:
            credit_trend = float(rng.normal(0, CREDIT_NOISE_THIN))  # thin file: ~no signal

        obs.append({
            "month": month_str(ym),
            "sales_total": round(sales_total, 2),
            "sales_per_sqft": round(sales_psf, 3),
            "foot_traffic_estimate": int(max(0, foot_traffic)),
            "rent_to_sales_ratio": round(rent_to_sales, 4),
            "late_payment_flag": bool(late_payment),
            "trading_hours_shortfall": round(trading_hours_shortfall, 3),
            "relief_or_exit_enquiry": False,  # set by inject_enquiries()
            "stock_depth_index": round(stock_depth_index, 3),
            "credit_trend_3mo": round(credit_trend, 4),
        })
    return obs


def inject_enquiries(rng, obs, health, exit_index):
    """
    Leading behavioural signal, coupled to CHRONIC stress with a randomised
    lead time — never fired the month before exit (anti-leakage).
    """
    n = len(obs)
    if exit_index is not None:
        if rng.random() > ENQUIRY_FALSE_NEG:  # most exiters enquire first
            lead = int(rng.integers(ENQUIRY_LEAD_MIN, ENQUIRY_LEAD_MAX + 1))
            idx = exit_index - lead
            if 0 <= idx < n:
                obs[idx]["relief_or_exit_enquiry"] = True
    else:
        # False positives: chronically-stressed survivors sometimes enquire.
        for t in range(6, n):
            chronic = np.mean([stress_of(health[k]) for k in range(t - 6, t)])
            if chronic > 0.45 and rng.random() < ENQUIRY_FALSE_POS_RATE * (1 + chronic):
                obs[t]["relief_or_exit_enquiry"] = True


# ----------------------------------------------------------------------------
# Persona + static attributes
# ----------------------------------------------------------------------------

def sample_operator_type(rng, category):
    ops = CATEGORIES[category]["ops"]
    return str(rng.choice(list(ops.keys()), p=list(ops.values())))


def sample_risk_appetite(rng, category, operator_type):
    base = dict(RISK_BASE)
    if category in ("bookstore", "specialty_jewellery"):
        base["cautious"] += 0.15
        base["bold"] -= 0.15
    if category in ("phone_repair",):
        base["bold"] += 0.10
        base["cautious"] -= 0.10
    # renormalise (guard against tiny negatives)
    base = {k: max(0.01, v) for k, v in base.items()}
    tot = sum(base.values())
    keys = list(base.keys())
    return str(rng.choice(keys, p=[base[k] / tot for k in keys]))


def make_static(rng, category, operator_type, zone, zone_anchor):
    cfg = CATEGORIES[category]
    base_psf = float(rng.uniform(*cfg["sales_psf"]))
    sqft = int(rng.uniform(*cfg["sqft"]))
    baseline_rts = float(rng.uniform(*cfg["healthy_rts"]))
    rent_psf = base_psf * baseline_rts  # baseline rent-to-sales lands in the healthy band
    return {
        "category": category,
        "operator_type": operator_type,
        "zone": zone,
        "zone_anchor": zone_anchor,
        "base_psf": base_psf,
        "sqft": sqft,
        "rent_psf": rent_psf,
        "season_amp": cfg["season_amp"],
        "foot_base": float(rng.uniform(4000, 12000)),
    }


def assemble_tenant(tenant_id, name, static, calendar, obs, exit_index,
                    operator_type, risk_appetite, loyalty, lease_months_remaining,
                    is_anchor=False):
    """Build the tenant record (static + persona + lease + status)."""
    # Lease dates derived deterministically from term + months-remaining.
    lease_len_years = 10 if is_anchor else 3
    lease_end_ym = add_months(SIM_END, lease_months_remaining)
    lease_end = first_of_month(lease_end_ym)
    lease_start = first_of_month(add_months(lease_end_ym, -12 * lease_len_years))

    if exit_index is not None:
        status = "exited"
        exit_date = first_of_month(calendar[exit_index]).isoformat()
        # truncate observations at exit
        obs = obs[: exit_index + 1]
    else:
        status = "active"
        exit_date = None

    return {
        "tenant_id": tenant_id,
        "name": name,
        "category": static["category"],
        "sqft": static["sqft"],
        "rent_per_sqft": round(static["rent_psf"], 2),
        "lease_start": lease_start.isoformat(),
        "lease_end": lease_end.isoformat(),
        "mall_context": "northern_default",
        "zone": static["zone"],
        "is_anchor": is_anchor,
        "persona": {
            "operator_type": operator_type,
            "risk_appetite": risk_appetite,
            "loyalty_propensity_score": round(loyalty, 2),
            "contentious_renegotiations": 0,
            "smooth_renewals": 0,
        },
        "status": status,
        "exit_date": exit_date,
    }, obs


# ----------------------------------------------------------------------------
# Demo cast — hard-coded so the demo is repeatable
# ----------------------------------------------------------------------------

def ramp(n, start, end, tail=12):
    """Flat-ish then declining/rising path: steady for (n-tail), then linear to `end`."""
    head = np.full(n - tail, start)
    tailv = np.linspace(start, end, tail)
    return np.concatenate([head, tailv])


def build_demo_tenants(rng, calendar):
    """The four narrative tenants with hand-set health paths + forced flags."""
    n = len(calendar)
    out = []

    # --- Atelier Margot — boutique apparel, HIGH risk, renegotiate ---
    static = make_static(rng, "apparel_boutique", "owner_operator", "east", None)
    static["base_psf"], static["sqft"] = 30.0, 1200
    static["rent_psf"] = 30.0 * 0.135  # rts baseline ~13.5%, creeps to ~14%+
    health = ramp(n, 1.00, 0.78, tail=12)
    obs = build_observations(rng, calendar, health, static)
    for k in range(-3, 0):
        if rng.random() < 0.4:
            obs[k]["late_payment_flag"] = True
    obs[-2]["relief_or_exit_enquiry"] = True  # asked about a softer rent step
    t, o = assemble_tenant("TENANT_DEMO_001", "Atelier Margot", static, calendar, obs,
                           None, "owner_operator", "balanced", 0.80, lease_months_remaining=4)
    t["persona"]["smooth_renewals"] = 1
    out.append((t, o))

    # --- Pancho's Tacos — food court, VERY HIGH risk, replace ---
    static = make_static(rng, "food_court", "franchise", "north", None)
    static["base_psf"], static["sqft"] = 45.0, 450
    static["rent_psf"] = 45.0 * 0.11
    health = ramp(n, 0.95, 0.50, tail=18)
    obs = build_observations(rng, calendar, health, static)
    for k in range(-3, 0):
        obs[k]["late_payment_flag"] = True
    obs[-2]["relief_or_exit_enquiry"] = True  # enquired about early termination
    for k in range(-4, 0):
        obs[k]["stock_depth_index"] = round(float(np.clip(0.35 + rng.normal(0, 0.05), 0, 1)), 3)
    t, o = assemble_tenant("TENANT_DEMO_002", "Pancho's Tacos", static, calendar, obs,
                           None, "franchise", "balanced", 0.50, lease_months_remaining=7)
    t["persona"]["contentious_renegotiations"] = 1
    out.append((t, o))

    # --- Crystal Mobile — phone repair, MODERATE, monitor ---
    static = make_static(rng, "phone_repair", "franchise", "south", None)
    static["base_psf"], static["sqft"] = 28.0, 250
    static["rent_psf"] = 28.0 * 0.09
    health = np.full(n, 0.88) + rng.normal(0, 0.015, n)
    obs = build_observations(rng, calendar, health, static)
    for ob in obs:
        ob["late_payment_flag"] = False          # pays on time
        ob["trading_hours_shortfall"] = round(float(abs(rng.normal(0, 0.02))), 3)
    t, o = assemble_tenant("TENANT_DEMO_003", "Crystal Mobile", static, calendar, obs,
                           None, "franchise", "cautious", 0.40, lease_months_remaining=15)
    out.append((t, o))

    # --- Pages & Co — bookstore, LOW, no action ---
    static = make_static(rng, "bookstore", "owner_operator", "west", None)
    static["base_psf"], static["sqft"] = 22.0, 1500
    static["rent_psf"] = 22.0 * 0.09
    health = np.full(n, 1.06) + rng.normal(0, 0.015, n)
    obs = build_observations(rng, calendar, health, static)
    for ob in obs:
        ob["late_payment_flag"] = False
    t, o = assemble_tenant("TENANT_DEMO_004", "Pages & Co", static, calendar, obs,
                           None, "owner_operator", "cautious", 0.85, lease_months_remaining=20)
    t["persona"]["smooth_renewals"] = 2
    out.append((t, o))

    return out


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------

def build(seed=42, n_background=70, out_dir="data"):
    rng = np.random.default_rng(seed)
    calendar = months_back()
    n = len(calendar)

    tenants, observations = [], []

    # 1) Anchors — one per zone, distinct type; their health ripples to neighbours.
    zone_anchor_health = {}
    anchor_cats = list(ANCHOR_CATEGORIES.keys())
    for i, zone in enumerate(ZONES):
        cat = anchor_cats[i % len(anchor_cats)]
        cfg = ANCHOR_CATEGORIES[cat]
        # One anchor (north) is dying — drives the demo "anchor closed" ripple.
        if zone == "north":
            health = ramp(n, 0.95, 0.45, tail=20)
        else:
            health = gen_health_path(rng, n, start=1.05)
        zone_anchor_health[zone] = health
        static = {
            "category": cat, "operator_type": "corporate", "zone": zone, "zone_anchor": None,
            "base_psf": float(rng.uniform(*cfg["sales_psf"])), "sqft": int(rng.uniform(*cfg["sqft"])),
            "season_amp": cfg["season_amp"], "foot_base": float(rng.uniform(20000, 40000)),
        }
        static["rent_psf"] = static["base_psf"] * float(rng.uniform(*cfg["healthy_rts"]))
        obs = build_observations(rng, calendar, health, static)
        exit_index = sample_exit_index(rng, health)
        inject_enquiries(rng, obs, health, exit_index)
        t, o = assemble_tenant(f"ANCHOR_{zone.upper()}", f"{cat.replace('_', ' ').title()} ({zone})",
                               static, calendar, obs, exit_index, "corporate", "cautious", 0.70,
                               lease_months_remaining=int(rng.integers(6, 30)), is_anchor=True)
        tenants.append(t)
        observations.append((t["tenant_id"], o))

    # 2) Demo cast (hard-coded)
    for t, o in build_demo_tenants(rng, calendar):
        tenants.append(t)
        observations.append((t["tenant_id"], o))

    # 3) Background tenants
    cat_names = list(CATEGORIES.keys())
    for i in range(n_background):
        category = str(rng.choice(cat_names))
        zone = str(rng.choice(ZONES))
        operator_type = sample_operator_type(rng, category)
        risk = sample_risk_appetite(rng, category, operator_type)
        loyalty = float(np.clip(rng.normal(0.55, 0.18), 0.05, 0.95))
        static = make_static(rng, category, operator_type, zone, zone_anchor_health[zone])
        health = gen_health_path(rng, n, start=float(rng.uniform(0.92, 1.08)),
                                 zone_anchor=zone_anchor_health[zone])
        obs = build_observations(rng, calendar, health, static)
        exit_index = sample_exit_index(rng, health)
        inject_enquiries(rng, obs, health, exit_index)
        t, o = assemble_tenant(f"TENANT_{i + 1:03d}", f"Tenant {i + 1}", static, calendar, obs,
                               exit_index, operator_type, risk, loyalty,
                               lease_months_remaining=int(rng.integers(1, 30)))
        tenants.append(t)
        observations.append((t["tenant_id"], o))

    # Flatten observations to one record per tenant-month
    obs_records = []
    for tid, olist in observations:
        for ob in olist:
            obs_records.append({"tenant_id": tid, **ob})

    # Write
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tenants.json").write_text(json.dumps(tenants, indent=2))
    (out / "observations.json").write_text(json.dumps(obs_records, indent=2))

    summarise(tenants, obs_records)
    return tenants, obs_records


# ----------------------------------------------------------------------------
# Sanity-check summary
# ----------------------------------------------------------------------------

def summarise(tenants, obs_records):
    n_t = len(tenants)
    n_anchor = sum(t["is_anchor"] for t in tenants)
    n_exit = sum(t["status"] == "exited" for t in tenants)
    print(f"\n{'='*64}\nGENERATED: {n_t} tenants ({n_anchor} anchors), "
          f"{len(obs_records)} observations")
    print(f"Exits: {n_exit}/{n_t}  ({100*n_exit/n_t:.0f}%)  - events for Cox PH")

    # Branch sanity: mean feature for exited vs active tenants (last-12mo mean).
    exited_ids = {t["tenant_id"] for t in tenants if t["status"] == "exited"}
    by_t = {}
    for r in obs_records:
        by_t.setdefault(r["tenant_id"], []).append(r)

    def grp_mean(field, exited):
        vals = []
        for tid, rows in by_t.items():
            if (tid in exited_ids) != exited:
                continue
            tail = rows[-12:]
            vals.extend(float(x[field]) for x in tail)
        return np.mean(vals) if vals else float("nan")

    print(f"\n{'feature':<26}{'exited (mean)':>15}{'active (mean)':>15}  (expect separation)")
    for f in ["rent_to_sales_ratio", "late_payment_flag", "trading_hours_shortfall",
              "relief_or_exit_enquiry", "stock_depth_index", "credit_trend_3mo"]:
        print(f"{f:<26}{grp_mean(f, True):>15.4f}{grp_mean(f, False):>15.4f}")

    print(f"\nDemo cast (final observed month):")
    for t in tenants:
        if t["tenant_id"].startswith("TENANT_DEMO"):
            rows = by_t[t["tenant_id"]]
            last = rows[-1]
            print(f"  {t['name']:<16} rts={last['rent_to_sales_ratio']:.3f}  "
                  f"late={int(last['late_payment_flag'])}  "
                  f"stock={last['stock_depth_index']:.2f}  "
                  f"credit={last['credit_trend_3mo']:+.3f}  "
                  f"status={t['status']}")
    print('='*64)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate synthetic tenant data (v5 branched model).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--background", type=int, default=70)
    ap.add_argument("--out", type=str, default="data")
    args = ap.parse_args()
    build(seed=args.seed, n_background=args.background, out_dir=args.out)
