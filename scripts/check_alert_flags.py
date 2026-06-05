#!/usr/bin/env python
"""
Offline smoke-test for the cox_ph_predict alert layer (Day-6 two-layer design).

Exercises the Cloud Function's `_featurise()` against the local data files and
rebuilds the `alert_flags` block exactly as the HTTP handler would — WITHOUT
needing MongoDB, the model bundle, or a deployed Function. It's the cheap guard
that the generator's new typed-enquiry + credit-band signals flow through the
featuriser into the contract shape the agent's `get_hazard` tool expects
(decisions.md 2026-06-05 §5).

`functions_framework` (a deploy-only dependency) is stubbed so the pure
featuriser imports under the project venv.

Usage:
    python scripts/check_alert_flags.py            # check data/ (the demo mall)
    python scripts/check_alert_flags.py --data data_train

Exits non-zero if the four demo tenants don't carry their expected alert story.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

# Stub the deploy-only dependency before importing the Function module.
_ff = types.ModuleType("functions_framework")
_ff.http = lambda fn: fn
sys.modules["functions_framework"] = _ff

_FUNC_DIR = Path(__file__).resolve().parent.parent / "functions" / "cox_ph_predict"
sys.path.insert(0, str(_FUNC_DIR))
from main import _featurise  # noqa: E402  (after the stub + path insert)


def build_alert_flags(features: dict) -> dict:
    """Mirror of the handler's alert_flags assembly (main.py:cox_ph_predict)."""
    return {
        "enquiry": {
            "type": features.get("alert_enquiry_type"),
            "recent_6mo": bool(features.get("alert_enquiry_recent_6mo", False)),
        },
        "credit": {
            "band": features.get("alert_credit_band"),
            "notches_changed_6mo": int(features.get("alert_credit_notches_changed_6mo", 0)),
            "trend_3mo_mean": round(float(features.get("alert_credit_trend_3mo_mean", 0.0)), 4),
        },
    }


# What the hand-built demo cast should assert about the alert layer. Bands are
# left to the generator (they ride a noisy walk), so we only pin the enquiry story
# — the part the narrative depends on.
DEMO_EXPECTATIONS = {
    "TENANT_DEMO_001": {"name": "Atelier Margot", "enquiry_type": "rent_relief"},
    "TENANT_DEMO_002": {"name": "Pancho's Tacos", "enquiry_type": "early_termination"},
    "TENANT_DEMO_003": {"name": "Crystal Mobile", "enquiry_type": None},
    "TENANT_DEMO_004": {"name": "Pages & Co",     "enquiry_type": None},
}


def main(data_dir: str) -> int:
    observations = json.loads((Path(data_dir) / "observations.json").read_text(encoding="utf-8"))
    by_tenant: dict[str, list] = {}
    for obs in observations:
        by_tenant.setdefault(obs["tenant_id"], []).append(obs)

    print(f"alert_flags from {data_dir}/ (demo cast):\n")
    problems = []
    for tenant_id, expected in DEMO_EXPECTATIONS.items():
        if tenant_id not in by_tenant:
            problems.append(f"{tenant_id} ({expected['name']}) missing from {data_dir}/")
            continue
        flags = build_alert_flags(_featurise(by_tenant[tenant_id]))
        print(f"  {expected['name']:<16} {json.dumps(flags)}")
        got = flags["enquiry"]["type"]
        if got != expected["enquiry_type"]:
            problems.append(
                f"{expected['name']}: enquiry.type={got!r}, expected {expected['enquiry_type']!r}")

    if problems:
        print("\nFAIL:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nPASS: demo cast alert flags match the narrative.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline smoke-test for cox_ph_predict alert flags")
    parser.add_argument("--data", default="data", help="Source data folder (default: data/)")
    args = parser.parse_args()
    raise SystemExit(main(args.data))
