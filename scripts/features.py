#!/usr/bin/env python
"""
Data contract for the Tenant Mix Optimizer pipeline.

Single source of truth for the fields every dataset must carry, plus the checks
that guard against train/serve skew. Used by `check_schema.py` and (Day 5) to
validate MongoDB-sourced records and the Cloud Function's input.

NOTE: feature *engineering* for the model lives with the model itself
(`fit_cox_model.py`'s `build_counting_process`, the time-varying counting-process
featuriser). This module is deliberately just the contract — Day-5 serving
helpers (current-state featurisation + 0-1 risk score) will reuse that featuriser
rather than re-implement one here.
"""
from __future__ import annotations

import json
from pathlib import Path

# ----------------------------------------------------------------------------
# The contract — keep in sync with the v5 spec and the generator output.
# ----------------------------------------------------------------------------

REQUIRED_TENANT_FIELDS = {
    "tenant_id", "name", "category", "sqft", "rent_per_sqft",
    "lease_start", "lease_end", "mall_context", "zone", "is_anchor",
    "persona", "status", "exit_date",
}

REQUIRED_OBSERVATION_FIELDS = {
    "tenant_id", "month",
    # Branch 1 — revenue
    "sales_total", "sales_per_sqft", "foot_traffic_estimate", "rent_to_sales_ratio",
    # Branch 2 — operational
    "late_payment_flag", "trading_hours_shortfall",
    # Branch 3 — leading
    "relief_or_exit_enquiry", "stock_depth_index",
    # Branch 4 — external (alert-layer signal, not a model covariate)
    "credit_trend_3mo",
}


# ----------------------------------------------------------------------------
# Contract validation
# ----------------------------------------------------------------------------

def validate_dataset(tenants, observations, name="dataset"):
    """Return a list of contract problems (empty = OK)."""
    problems = []
    if not tenants:
        problems.append(f"{name}: no tenants")
        return problems
    if not observations:
        problems.append(f"{name}: no observations")
        return problems

    missing_t = REQUIRED_TENANT_FIELDS - set(tenants[0].keys())
    missing_o = REQUIRED_OBSERVATION_FIELDS - set(observations[0].keys())
    extra_o = set(observations[0].keys()) - REQUIRED_OBSERVATION_FIELDS
    if missing_t:
        problems.append(f"{name}: tenants missing fields {sorted(missing_t)}")
    if missing_o:
        problems.append(f"{name}: observations missing fields {sorted(missing_o)}")
    if extra_o:
        problems.append(f"{name}: observations have UNEXPECTED fields {sorted(extra_o)} "
                        f"(update the contract in features.py if intended)")
    return problems


def assert_aligned(name_a, obs_a, name_b, obs_b):
    """Raise if two observation sets don't share an identical field set."""
    fields_a, fields_b = set(obs_a[0].keys()), set(obs_b[0].keys())
    if fields_a != fields_b:
        raise ValueError(
            f"Schema drift between '{name_a}' and '{name_b}': "
            f"only in {name_a}={sorted(fields_a - fields_b)}; "
            f"only in {name_b}={sorted(fields_b - fields_a)}")


def load_dataset(data_dir):
    """Load tenants.json + observations.json from a data directory."""
    data_path = Path(data_dir)
    tenants = json.loads((data_path / "tenants.json").read_text())
    observations = json.loads((data_path / "observations.json").read_text())
    return tenants, observations


if __name__ == "__main__":
    tenants, observations = load_dataset("data")
    problems = validate_dataset(tenants, observations, "data")
    print("contract problems:", problems or "none")
    print(f"{len(tenants)} tenants, {len(observations)} observations")
