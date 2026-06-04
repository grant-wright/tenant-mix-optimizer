"""
cox_ph_predict — Google Cloud Function (HTTP)

Scores a single tenant's current exit risk using the fitted Cox time-varying
survival model. Called by the Agent Builder agent via the get_hazard tool.

POST {"tenant_id": "TENANT_001"}

Returns hazard scores (sigmoid + percentile), alert flags for the two-layer
design, and the top 3 feature contributions with branch labels.

Featuriser note: the trailing-window logic in _featurise() mirrors
fit_cox_model.py:build_counting_process. If you change window lengths or
column names in the training script, update this function to match.
"""

import json
import logging
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path

import functions_framework
import numpy as np
import pandas as pd
from pymongo import MongoClient

# ---------------------------------------------------------------------------
# Feature spec — must match fit_cox_model.py:FEATURES exactly
# ---------------------------------------------------------------------------

FEATURE_NAMES = [
    "rts_12mo_mean",
    "sales_trend_12mo",
    "late_pay_count_12mo",
    "trading_shortfall_3mo",
    "stock_depth_3mo",
]

FEATURE_META = {
    "rts_12mo_mean":         {"branch": "revenue"},
    "sales_trend_12mo":      {"branch": "revenue"},
    "late_pay_count_12mo":   {"branch": "operational"},
    "trading_shortfall_3mo": {"branch": "operational"},
    "stock_depth_3mo":       {"branch": "leading"},
}

MODEL_PATH = Path(__file__).parent / "model" / "cox_model.pkl"

# ---------------------------------------------------------------------------
# Module-level singletons — cold start pays the cost once; warm invocations
# reuse them. Cloud Functions keeps the process alive between requests.
# ---------------------------------------------------------------------------

_bundle = None
_mongo_client = None


def _load_bundle() -> dict:
    global _bundle
    if _bundle is None:
        with open(MODEL_PATH, "rb") as f:
            _bundle = pickle.load(f)
    return _bundle


def _get_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(os.environ["MONGODB_URI"])
    return _mongo_client[os.environ.get("MONGODB_DB", "tenant_mix")]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch(db, tenant_id: str) -> tuple:
    tenant = db["tenants"].find_one({"tenant_id": tenant_id}, {"_id": 0})
    if tenant is None:
        raise KeyError(f"tenant_id not found: {tenant_id}")
    obs = list(db["observations"].find({"tenant_id": tenant_id}, {"_id": 0}))
    if not obs:
        raise ValueError(f"No observations for tenant: {tenant_id}")
    return tenant, obs


# ---------------------------------------------------------------------------
# Featurise — trailing-window features from observation history
# ---------------------------------------------------------------------------

def _featurise(obs: list) -> dict:
    odf = pd.DataFrame(sorted(obs, key=lambda o: o["month"]))

    # Model covariates (must match fit_cox_model.py window sizes exactly)
    odf["rts_12mo_mean"] = (
        odf["rent_to_sales_ratio"].rolling(12, min_periods=1).mean()
    )
    odf["sales_trend_12mo"] = (
        odf["sales_total"] / odf["sales_total"].shift(12) - 1.0
    ).fillna(0.0)
    odf["late_pay_count_12mo"] = (
        odf["late_payment_flag"].astype(int).rolling(12, min_periods=1).sum()
    )
    odf["trading_shortfall_3mo"] = (
        odf["trading_hours_shortfall"].rolling(3, min_periods=1).mean()
    )
    odf["stock_depth_3mo"] = (
        odf["stock_depth_index"].rolling(3, min_periods=1).mean()
    )

    # Alert-layer signals (not model inputs; returned as flags for the agent)
    odf["enquiry_recent_6mo"] = (
        odf["relief_or_exit_enquiry"].astype(int).rolling(6, min_periods=1).max()
    )
    odf["credit_trend_3mo_mean"] = (
        odf["credit_trend_3mo"].rolling(3, min_periods=1).mean()
    )

    return odf.iloc[-1].to_dict()


# ---------------------------------------------------------------------------
# Score — log partial hazard → sigmoid + percentile rank
# ---------------------------------------------------------------------------

def _score(model, train_log_ph: np.ndarray, features: dict) -> tuple[float, float]:
    X = pd.DataFrame([{f: features[f] for f in FEATURE_NAMES}])
    log_ph = float(model.predict_log_partial_hazard(X).iloc[0])

    # Sigmoid: bounded 0-1, centre = average-risk tenant in training population
    sigmoid = float(1.0 / (1.0 + np.exp(-log_ph)))

    # Percentile rank: fraction of training tenants with a lower log partial hazard
    percentile = float((train_log_ph < log_ph).mean())

    return sigmoid, percentile


# ---------------------------------------------------------------------------
# Top features — coef × value, sorted by absolute magnitude
# ---------------------------------------------------------------------------

def _top_features(model, features: dict, n: int = 3) -> list:
    params = model.params_
    contributions = []
    for name in FEATURE_NAMES:
        coef = float(params.get(name, 0.0))
        value = float(features.get(name, 0.0))
        raw = coef * value
        contributions.append({
            "feature": name,
            "value": round(value, 4),
            "contribution": round(abs(raw), 4),
            "direction": (
                "increases_risk" if raw > 0
                else "decreases_risk" if raw < 0
                else "neutral"
            ),
            "branch": FEATURE_META[name]["branch"],
        })

    contributions.sort(key=lambda x: x["contribution"], reverse=True)
    return contributions[:n]


# ---------------------------------------------------------------------------
# HTTP entry point
# ---------------------------------------------------------------------------

@functions_framework.http
def cox_ph_predict(request):
    if request.method != "POST":
        return (json.dumps({"error": "POST required"}), 405,
                {"Content-Type": "application/json"})

    body = request.get_json(silent=True) or {}
    tenant_id = str(body.get("tenant_id", "")).strip()
    if not tenant_id:
        return (json.dumps({"error": "tenant_id is required"}), 400,
                {"Content-Type": "application/json"})

    try:
        bundle = _load_bundle()
        db = _get_db()
        _, obs = _fetch(db, tenant_id)
        features = _featurise(obs)
        sigmoid, percentile = _score(bundle["model"], bundle["train_log_ph"], features)
        top = _top_features(bundle["model"], features)

        payload = {
            "tenant_id": tenant_id,
            "hazard_percentile": round(percentile, 3),
            "hazard_sigmoid": round(sigmoid, 3),
            "alert_flags": {
                "enquiry_recent_6mo": bool(features.get("enquiry_recent_6mo", 0)),
                "credit_trend_3mo_mean": round(float(features.get("credit_trend_3mo_mean", 0.0)), 4),
            },
            "top_features": top,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        return (json.dumps(payload), 200, {"Content-Type": "application/json"})

    except KeyError as e:
        logging.warning("Not found: %s", e)
        return (json.dumps({"error": str(e)}), 404,
                {"Content-Type": "application/json"})
    except Exception:
        logging.exception("Unhandled error for tenant_id=%s", tenant_id)
        return (json.dumps({"error": "internal error"}), 500,
                {"Content-Type": "application/json"})
