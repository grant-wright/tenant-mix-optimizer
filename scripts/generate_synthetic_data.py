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
                    typed enquiry_type (latent-derived intent), stock_depth_index (moderate)
  4. External     — a latent credit-score random walk (mean-reverting to a
                    health-implied target) -> credit_band, signed credit_notches_changed
                    (band movement: + improved, - downgraded), and the continuous
                    credit_trend_3mo. Generated for ALL tenants; owner-operators get a
                    thicker walk (thinner real files) but a real band — decisions.md §6.

Branches 3 (enquiry_type) and 4 (credit band/downgrade) are the ALERT layer — discrete
high-impact events surfaced as flags, NOT Cox covariates. Both derive from the same
latent `financial_health` as the ambient branches: a different *read* on health, not
noise. The 5 Cox covariates are unchanged, so no model refit is forced.

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

# External credit (alert layer). A latent credit score (FICO-like 300-850) mean-
# reverts each month toward a health-implied target, with monthly walk noise. The
# band derives from the score; a downgrade event = a band step-down. Generated for
# ALL tenants; owner-operators get a thicker walk (a nod to thinner real files) but
# a real band. See decisions.md 2026-06-05 §6.
CREDIT_REVERSION = 0.30                          # speed the score tracks its health target
CREDIT_WALK_CORP = 8.0                           # monthly walk noise (points), corp/franchise
CREDIT_WALK_OWNER = 18.0                         # thicker noise for owner-operators
CREDIT_BANDS = [                                 # (min score inclusive, label), best -> worst
    (750, "strong"), (680, "fair"), (620, "adequate"), (560, "weak"), (0, "distressed"),
]
BAND_ORDER = ["strong", "fair", "adequate", "weak", "distressed"]  # index = severity (worse = higher)

# Anti-leakage for relief_or_exit_enquiry
ENQUIRY_LEAD_MIN, ENQUIRY_LEAD_MAX = 2, 9       # months before exit
ENQUIRY_FALSE_NEG = 0.30                         # exiters with no prior enquiry
ENQUIRY_FALSE_POS_RATE = 0.02                    # monthly, for chronically-stressed survivors
ENQUIRY_TYPES = ["rent_relief", "downsize", "sublet", "early_termination"]

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

# Mall tiers — only used with --tier-mix (the training population). Scale
# productivity + traffic; rent scales with sales so the rent-to-sales band is
# preserved across tiers. A per-tenant proxy for "pooled across malls of
# different tiers". The demo mall (data/) stays single-tier.
TIERS = {
    "prime":    dict(sales_mult=1.40, foot_mult=1.50, weight=0.25),
    "suburban": dict(sales_mult=1.00, foot_mult=1.00, weight=0.50),
    "outlet":   dict(sales_mult=0.75, foot_mult=0.80, weight=0.25),
}


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


def months_back(end=SIM_END, n_months=N_MONTHS):
    """Ascending list of (year, month) ending at `end`, length n_months."""
    year, month = end
    calendar = []
    for _ in range(n_months):
        calendar.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(calendar))


def month_str(year_month) -> str:
    return f"{year_month[0]:04d}-{year_month[1]:02d}"


def first_of_month(year_month) -> date:
    return date(year_month[0], year_month[1], 1)


def add_months(year_month, offset):
    year, month = year_month
    total = (year * 12 + (month - 1)) + offset
    return (total // 12, total % 12 + 1)


# ----------------------------------------------------------------------------
# Latent + branch generation
# ----------------------------------------------------------------------------

def gen_health_path(rng, n_months, start=1.0, zone_anchor=None, anchor_coupling=0.12):
    """Random walk with drift; optionally nudged by the zone anchor's health."""
    health = np.empty(n_months)
    health[0] = start
    for month_idx in range(1, n_months):
        step = DRIFT + rng.normal(0.0, WALK_NOISE)
        if zone_anchor is not None:
            # When the anchor is below its own baseline, neighbours feel it.
            step += anchor_coupling * (zone_anchor[month_idx] - 1.0)
        health[month_idx] = health[month_idx - 1] + step
    return health


def sample_exit_index(rng, health_path):
    """First month the latent-driven hazard fires, else None (censored/active)."""
    for month_idx in range(len(health_path)):
        p_exit = sigmoid(EXIT_BASE + EXIT_K * stress_of(health_path[month_idx]))
        if rng.random() < p_exit:
            return month_idx
    return None


def target_credit_score(health: float) -> float:
    """Health-implied 'fair value' the latent credit score reverts toward (300-850)."""
    return float(np.clip(300.0 + 500.0 * health, 300.0, 850.0))


def gen_credit_path(rng, health_path, operator_type):
    """Latent credit score: a mean-reverting random walk toward the health target.

    Credit has its own momentum and lags health (a *different read* on it, not a
    restatement). Owner-operators get a thicker walk — a nod to thinner real files —
    but still a real band.
    """
    walk_noise = CREDIT_WALK_OWNER if operator_type == "owner_operator" else CREDIT_WALK_CORP
    n_months = len(health_path)
    score = np.empty(n_months)
    score[0] = target_credit_score(health_path[0]) + rng.normal(0, walk_noise)
    for month_idx in range(1, n_months):
        target = target_credit_score(health_path[month_idx])
        score[month_idx] = (score[month_idx - 1]
                            + CREDIT_REVERSION * (target - score[month_idx - 1])
                            + rng.normal(0, walk_noise))
    return np.clip(score, 300.0, 850.0)


def band_of_score(score: float) -> str:
    """Map a credit score to its band label."""
    for threshold, label in CREDIT_BANDS:
        if score >= threshold:
            return label
    return "distressed"


def sample_enquiry_type(rng, health: float) -> str:
    """Latent-derived enquiry intent: early_termination for the very unhealthy,
    rent_relief for the moderately struggling (decisions.md 2026-06-05 §6)."""
    stress = stress_of(health)
    weights = np.array([
        1.0 + 1.5 * (1.0 - stress),   # rent_relief — favoured when less desperate
        0.7,                          # downsize
        0.6,                          # sublet
        0.2 + 2.0 * stress * stress,  # early_termination — favoured when very unhealthy
    ])
    return str(rng.choice(ENQUIRY_TYPES, p=weights / weights.sum()))


def build_observations(rng, calendar, health_path, tenant_static):
    """Compute the four-branch monthly features from the health path."""
    base_psf = tenant_static["base_psf"]
    rent_psf = tenant_static["rent_psf"]
    sqft = tenant_static["sqft"]
    season_amp = tenant_static["season_amp"]
    operator_type = tenant_static["operator_type"]
    zone_anchor = tenant_static.get("zone_anchor")

    # External branch: latent credit-score walk -> band + discrete downgrade events.
    credit_path = gen_credit_path(rng, health_path, operator_type)
    prev_band = None

    observations = []
    for month_idx, year_month in enumerate(calendar):
        current_health = health_path[month_idx]
        stress = stress_of(current_health)
        season = 1.0 + (SEASON_BY_MONTH[year_month[1]] - 1.0) * season_amp

        # Branch 1 — revenue
        sales_psf = base_psf * health_mult(current_health) * season
        sales_total = sales_psf * sqft
        rent_to_sales = (rent_psf * sqft) / sales_total  # == rent_psf / sales_psf
        anchor_mod = 1.0 if zone_anchor is None else float(np.clip(0.7 + 0.3 * zone_anchor[month_idx], 0.4, 1.2))
        foot_traffic = tenant_static["foot_base"] * season * anchor_mod * (1.0 + rng.normal(0, 0.05))

        # Branch 2 — operational (landlord-observed)
        late_payment = rng.random() < sigmoid(LATE_INTERCEPT + LATE_SLOPE * stress)
        trading_hours_shortfall = float(np.clip(TRADING_GAIN * stress + rng.normal(0, TRADING_NOISE), 0.0, 1.0))

        # Branch 3 — leading (relief_or_exit_enquiry injected later, anti-leak)
        stock_depth_index = float(np.clip(1.0 - STOCK_GAIN * stress + rng.normal(0, STOCK_NOISE), 0.0, 1.0))

        # Branch 4 — external / third-party (alert layer; not a Cox covariate)
        band = band_of_score(credit_path[month_idx])
        # Signed band movement vs last month: + = improved (severity fell),
        # - = downgraded. Sign anchored to the score, like credit_trend_3mo.
        if prev_band is None:
            notches_changed = 0
        else:
            notches_changed = BAND_ORDER.index(prev_band) - BAND_ORDER.index(band)
        prev_band = band
        if month_idx >= 3:
            credit_trend = float(np.clip((credit_path[month_idx] - credit_path[month_idx - 3]) / 100.0, -0.40, 0.40))
        else:
            credit_trend = 0.0

        observations.append({
            "month": month_str(year_month),
            "sales_total": round(sales_total, 2),
            "sales_per_sqft": round(sales_psf, 3),
            "foot_traffic_estimate": int(max(0, foot_traffic)),
            "rent_to_sales_ratio": round(rent_to_sales, 4),
            "late_payment_flag": bool(late_payment),
            "trading_hours_shortfall": round(trading_hours_shortfall, 3),
            "relief_or_exit_enquiry": False,  # set by inject_enquiries()
            "enquiry_type": None,             # set by inject_enquiries() when an enquiry fires
            "stock_depth_index": round(stock_depth_index, 3),
            "credit_trend_3mo": round(credit_trend, 4),
            "credit_band": band,
            "credit_notches_changed": int(notches_changed),
        })
    return observations


def inject_enquiries(rng, observations, health_path, exit_index):
    """
    Leading behavioural signal, coupled to CHRONIC stress with a randomised
    lead time — never fired the month before exit (anti-leakage).
    """
    n_periods = len(observations)
    if exit_index is not None:
        if rng.random() > ENQUIRY_FALSE_NEG:  # most exiters enquire first
            lead_months = int(rng.integers(ENQUIRY_LEAD_MIN, ENQUIRY_LEAD_MAX + 1))
            enquiry_idx = exit_index - lead_months
            if 0 <= enquiry_idx < n_periods:
                observations[enquiry_idx]["relief_or_exit_enquiry"] = True
                observations[enquiry_idx]["enquiry_type"] = sample_enquiry_type(rng, health_path[enquiry_idx])
    else:
        # False positives: chronically-stressed survivors sometimes enquire.
        for month_idx in range(6, n_periods):
            chronic_stress = np.mean([stress_of(health_path[m]) for m in range(month_idx - 6, month_idx)])
            if chronic_stress > 0.45 and rng.random() < ENQUIRY_FALSE_POS_RATE * (1 + chronic_stress):
                observations[month_idx]["relief_or_exit_enquiry"] = True
                observations[month_idx]["enquiry_type"] = sample_enquiry_type(rng, health_path[month_idx])


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
    base = {level: max(0.01, weight) for level, weight in base.items()}
    total = sum(base.values())
    levels = list(base.keys())
    return str(rng.choice(levels, p=[base[level] / total for level in levels]))


def make_static(rng, category, operator_type, zone, zone_anchor, tier_mix=False):
    category_cfg = CATEGORIES[category]
    if tier_mix:
        tier = str(rng.choice(list(TIERS), p=[TIERS[t]["weight"] for t in TIERS]))
        sales_mult, foot_mult = TIERS[tier]["sales_mult"], TIERS[tier]["foot_mult"]
    else:
        tier, sales_mult, foot_mult = "northern_default", 1.0, 1.0
    base_psf = float(rng.uniform(*category_cfg["sales_psf"])) * sales_mult
    sqft = int(rng.uniform(*category_cfg["sqft"]))
    baseline_rts = float(rng.uniform(*category_cfg["healthy_rts"]))
    rent_psf = base_psf * baseline_rts  # rent scales with sales -> rts band preserved across tiers
    return {
        "category": category,
        "operator_type": operator_type,
        "zone": zone,
        "zone_anchor": zone_anchor,
        "mall_context": tier,
        "base_psf": base_psf,
        "sqft": sqft,
        "rent_psf": rent_psf,
        "season_amp": category_cfg["season_amp"],
        "foot_base": float(rng.uniform(4000, 12000)) * foot_mult,
    }


def assemble_tenant(tenant_id, name, tenant_static, calendar, observations, exit_index,
                    operator_type, risk_appetite, loyalty, lease_months_remaining,
                    is_anchor=False):
    """Build the tenant record (static + persona + lease + status)."""
    # Lease dates derived deterministically from term + months-remaining.
    lease_len_years = 10 if is_anchor else 3
    lease_end_year_month = add_months(SIM_END, lease_months_remaining)
    lease_end = first_of_month(lease_end_year_month)
    lease_start = first_of_month(add_months(lease_end_year_month, -12 * lease_len_years))

    if exit_index is not None:
        status = "exited"
        exit_date = first_of_month(calendar[exit_index]).isoformat()
        # truncate observations at exit
        observations = observations[: exit_index + 1]
    else:
        status = "active"
        exit_date = None

    return {
        "tenant_id": tenant_id,
        "name": name,
        "category": tenant_static["category"],
        "sqft": tenant_static["sqft"],
        "rent_per_sqft": round(tenant_static["rent_psf"], 2),
        "lease_start": lease_start.isoformat(),
        "lease_end": lease_end.isoformat(),
        "mall_context": tenant_static.get("mall_context", "northern_default"),
        "zone": tenant_static["zone"],
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
    }, observations


# ----------------------------------------------------------------------------
# Demo cast — hard-coded so the demo is repeatable
# ----------------------------------------------------------------------------

def ramp(n_months, start, end, tail_months=12):
    """Flat-ish then declining/rising path: steady for (n_months - tail), then linear to `end`."""
    head_vals = np.full(n_months - tail_months, start)
    tail_vals = np.linspace(start, end, tail_months)
    return np.concatenate([head_vals, tail_vals])


def build_demo_tenants(rng, calendar):
    """The four narrative tenants with hand-set health paths + forced flags."""
    n_months = len(calendar)
    demo_tenants = []

    # --- Atelier Margot — boutique apparel, HIGH risk, renegotiate ---
    tenant_static = make_static(rng, "apparel_boutique", "owner_operator", "east", None)
    tenant_static["base_psf"], tenant_static["sqft"] = 30.0, 1200
    tenant_static["rent_psf"] = 30.0 * 0.135  # rts baseline ~13.5%, creeps to ~14%+
    health_path = ramp(n_months, 1.00, 0.78, tail_months=12)
    observations = build_observations(rng, calendar, health_path, tenant_static)
    for month_offset in range(-3, 0):
        if rng.random() < 0.4:
            observations[month_offset]["late_payment_flag"] = True
    observations[-2]["relief_or_exit_enquiry"] = True  # asked about a softer rent step
    observations[-2]["enquiry_type"] = "rent_relief"
    tenant_record, observation_list = assemble_tenant(
        "TENANT_DEMO_001", "Atelier Margot", tenant_static, calendar, observations,
        None, "owner_operator", "balanced", 0.80, lease_months_remaining=4)
    tenant_record["persona"]["smooth_renewals"] = 1
    demo_tenants.append((tenant_record, observation_list))

    # --- Pancho's Tacos — food court, VERY HIGH risk, replace ---
    tenant_static = make_static(rng, "food_court", "franchise", "north", None)
    tenant_static["base_psf"], tenant_static["sqft"] = 45.0, 450
    tenant_static["rent_psf"] = 45.0 * 0.11
    health_path = ramp(n_months, 0.95, 0.50, tail_months=18)
    observations = build_observations(rng, calendar, health_path, tenant_static)
    for month_offset in range(-3, 0):
        observations[month_offset]["late_payment_flag"] = True
    observations[-2]["relief_or_exit_enquiry"] = True  # enquired about early termination
    observations[-2]["enquiry_type"] = "early_termination"
    for month_offset in range(-4, 0):
        observations[month_offset]["stock_depth_index"] = round(float(np.clip(0.35 + rng.normal(0, 0.05), 0, 1)), 3)
    tenant_record, observation_list = assemble_tenant(
        "TENANT_DEMO_002", "Pancho's Tacos", tenant_static, calendar, observations,
        None, "franchise", "balanced", 0.50, lease_months_remaining=7)
    tenant_record["persona"]["contentious_renegotiations"] = 1
    demo_tenants.append((tenant_record, observation_list))

    # --- Crystal Mobile — phone repair, MODERATE, monitor ---
    tenant_static = make_static(rng, "phone_repair", "franchise", "south", None)
    tenant_static["base_psf"], tenant_static["sqft"] = 28.0, 250
    tenant_static["rent_psf"] = 28.0 * 0.09
    health_path = np.full(n_months, 0.88) + rng.normal(0, 0.015, n_months)
    observations = build_observations(rng, calendar, health_path, tenant_static)
    for observation in observations:
        observation["late_payment_flag"] = False          # pays on time
        observation["trading_hours_shortfall"] = round(float(abs(rng.normal(0, 0.02))), 3)
    tenant_record, observation_list = assemble_tenant(
        "TENANT_DEMO_003", "Crystal Mobile", tenant_static, calendar, observations,
        None, "franchise", "cautious", 0.40, lease_months_remaining=15)
    demo_tenants.append((tenant_record, observation_list))

    # --- Pages & Co — bookstore, LOW, no action ---
    tenant_static = make_static(rng, "bookstore", "owner_operator", "west", None)
    tenant_static["base_psf"], tenant_static["sqft"] = 22.0, 1500
    tenant_static["rent_psf"] = 22.0 * 0.09
    health_path = np.full(n_months, 1.06) + rng.normal(0, 0.015, n_months)
    observations = build_observations(rng, calendar, health_path, tenant_static)
    for observation in observations:
        observation["late_payment_flag"] = False
    tenant_record, observation_list = assemble_tenant(
        "TENANT_DEMO_004", "Pages & Co", tenant_static, calendar, observations,
        None, "owner_operator", "cautious", 0.85, lease_months_remaining=20)
    tenant_record["persona"]["smooth_renewals"] = 2
    demo_tenants.append((tenant_record, observation_list))

    return demo_tenants


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------

def build(seed=42, n_background=70, out_dir="data", tier_mix=False):
    rng = np.random.default_rng(seed)
    calendar = months_back()
    n_months = len(calendar)

    tenants = []
    observations_by_tenant = []  # list of (tenant_id, observation_list)

    # 1) Anchors — one per zone, distinct type; their health ripples to neighbours.
    zone_anchor_health = {}
    anchor_cats = list(ANCHOR_CATEGORIES.keys())
    for zone_idx, zone in enumerate(ZONES):
        anchor_category = anchor_cats[zone_idx % len(anchor_cats)]
        anchor_cfg = ANCHOR_CATEGORIES[anchor_category]
        # One anchor (north) is dying — drives the demo "anchor closed" ripple.
        if zone == "north":
            health_path = ramp(n_months, 0.95, 0.45, tail_months=20)
        else:
            health_path = gen_health_path(rng, n_months, start=1.05)
        zone_anchor_health[zone] = health_path
        tenant_static = {
            "category": anchor_category, "operator_type": "corporate", "zone": zone, "zone_anchor": None,
            "base_psf": float(rng.uniform(*anchor_cfg["sales_psf"])), "sqft": int(rng.uniform(*anchor_cfg["sqft"])),
            "season_amp": anchor_cfg["season_amp"], "foot_base": float(rng.uniform(20000, 40000)),
        }
        tenant_static["rent_psf"] = tenant_static["base_psf"] * float(rng.uniform(*anchor_cfg["healthy_rts"]))
        observations = build_observations(rng, calendar, health_path, tenant_static)
        exit_index = sample_exit_index(rng, health_path)
        inject_enquiries(rng, observations, health_path, exit_index)
        tenant_record, observation_list = assemble_tenant(
            f"ANCHOR_{zone.upper()}", f"{anchor_category.replace('_', ' ').title()} ({zone})",
            tenant_static, calendar, observations, exit_index, "corporate", "cautious", 0.70,
            lease_months_remaining=int(rng.integers(6, 30)), is_anchor=True)
        tenants.append(tenant_record)
        observations_by_tenant.append((tenant_record["tenant_id"], observation_list))

    # 2) Demo cast (hard-coded)
    for tenant_record, observation_list in build_demo_tenants(rng, calendar):
        tenants.append(tenant_record)
        observations_by_tenant.append((tenant_record["tenant_id"], observation_list))

    # 3) Background tenants
    category_names = list(CATEGORIES.keys())
    for i in range(n_background):
        category = str(rng.choice(category_names))
        zone = str(rng.choice(ZONES))
        operator_type = sample_operator_type(rng, category)
        risk_appetite = sample_risk_appetite(rng, category, operator_type)
        loyalty = float(np.clip(rng.normal(0.55, 0.18), 0.05, 0.95))
        tenant_static = make_static(rng, category, operator_type, zone,
                                    zone_anchor_health[zone], tier_mix=tier_mix)
        health_path = gen_health_path(rng, n_months, start=float(rng.uniform(0.92, 1.08)),
                                      zone_anchor=zone_anchor_health[zone])
        observations = build_observations(rng, calendar, health_path, tenant_static)
        exit_index = sample_exit_index(rng, health_path)
        inject_enquiries(rng, observations, health_path, exit_index)
        tenant_record, observation_list = assemble_tenant(
            f"TENANT_{i + 1:03d}", f"Tenant {i + 1}", tenant_static, calendar, observations,
            exit_index, operator_type, risk_appetite, loyalty,
            lease_months_remaining=int(rng.integers(1, 30)))
        tenants.append(tenant_record)
        observations_by_tenant.append((tenant_record["tenant_id"], observation_list))

    # Flatten observations to one record per tenant-month
    observation_records = []
    for tenant_id, observation_list in observations_by_tenant:
        for observation in observation_list:
            observation_records.append({"tenant_id": tenant_id, **observation})

    # Write
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "tenants.json").write_text(json.dumps(tenants, indent=2))
    (out_path / "observations.json").write_text(json.dumps(observation_records, indent=2))

    summarise(tenants, observation_records)
    return tenants, observation_records


# ----------------------------------------------------------------------------
# Sanity-check summary
# ----------------------------------------------------------------------------

def summarise(tenants, observation_records):
    n_tenants = len(tenants)
    n_anchors = sum(tenant["is_anchor"] for tenant in tenants)
    n_exits = sum(tenant["status"] == "exited" for tenant in tenants)
    print(f"\n{'='*64}\nGENERATED: {n_tenants} tenants ({n_anchors} anchors), "
          f"{len(observation_records)} observations")
    print(f"Exits: {n_exits}/{n_tenants}  ({100*n_exits/n_tenants:.0f}%)  - events for Cox PH")

    # Branch sanity: mean feature for exited vs active tenants (last-12mo mean).
    exited_ids = {tenant["tenant_id"] for tenant in tenants if tenant["status"] == "exited"}
    obs_by_tenant = {}
    for record in observation_records:
        obs_by_tenant.setdefault(record["tenant_id"], []).append(record)

    def grp_mean(field, exited):
        values = []
        for tenant_id, rows in obs_by_tenant.items():
            if (tenant_id in exited_ids) != exited:
                continue
            recent = rows[-12:]
            values.extend(float(row[field]) for row in recent)
        return np.mean(values) if values else float("nan")

    print(f"\n{'feature':<26}{'exited (mean)':>15}{'active (mean)':>15}  (expect separation)")
    for feature in ["rent_to_sales_ratio", "late_payment_flag", "trading_hours_shortfall",
                    "relief_or_exit_enquiry", "stock_depth_index", "credit_trend_3mo"]:
        print(f"{feature:<26}{grp_mean(feature, True):>15.4f}{grp_mean(feature, False):>15.4f}")

    # Alert-layer sanity: credit-band spread + how many tenant-months carry a typed enquiry.
    band_counts = {label: 0 for _, label in CREDIT_BANDS}
    for record in observation_records:
        band_counts[record["credit_band"]] += 1
    enquiry_types = {t: 0 for t in ENQUIRY_TYPES}
    for record in observation_records:
        if record["enquiry_type"]:
            enquiry_types[record["enquiry_type"]] += 1
    print(f"\nCredit band (tenant-months): "
          + "  ".join(f"{label}={band_counts[label]}" for _, label in CREDIT_BANDS))
    print(f"Enquiry types (tenant-months): "
          + "  ".join(f"{t}={enquiry_types[t]}" for t in ENQUIRY_TYPES))

    print(f"\nDemo cast (final observed month):")
    for tenant in tenants:
        if tenant["tenant_id"].startswith("TENANT_DEMO"):
            rows = obs_by_tenant[tenant["tenant_id"]]
            last = rows[-1]
            enquiry = next((r["enquiry_type"] for r in reversed(rows[-6:]) if r["enquiry_type"]), "-")
            print(f"  {tenant['name']:<16} rts={last['rent_to_sales_ratio']:.3f}  "
                  f"late={int(last['late_payment_flag'])}  "
                  f"stock={last['stock_depth_index']:.2f}  "
                  f"band={last['credit_band']:<10} "
                  f"enquiry={enquiry:<16} "
                  f"status={tenant['status']}")
    print('='*64)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate synthetic tenant data (v5 branched model).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--background", type=int, default=70)
    ap.add_argument("--out", type=str, default="data")
    ap.add_argument("--tier-mix", action="store_true",
                    help="vary mall tier across tenants (use for the training population)")
    args = ap.parse_args()
    build(seed=args.seed, n_background=args.background, out_dir=args.out, tier_mix=args.tier_mix)
