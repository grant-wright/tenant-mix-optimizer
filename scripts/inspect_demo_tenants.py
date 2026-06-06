#!/usr/bin/env python
"""
Inspect the four curated demo tenants' lease/rent fields + latest alert signals.

What it does: loads data/{tenants,observations}.json, finds the demo tenants
(tenant_id contains "DEMO"), and prints the fields the recommend_intervention
terms calc + policy need — current rent_per_sqft, sqft, category, lease_end, and
the latest-month rent_to_sales_ratio, credit_band, enquiry_type.

When/why: written 2026-06-06 (Day 7) to ground the deterministic suggested_terms
calc and confirm the lease-soon override doesn't silently fire on the cast.
Read-only; a kept artifact for re-checking the demo data after any reseed.

Run: python scripts/inspect_demo_tenants.py
"""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path("data")


def latest_obs(observations, tenant_id):
    rows = [o for o in observations if o["tenant_id"] == tenant_id]
    rows.sort(key=lambda o: o["month"])
    return rows[-1] if rows else None


def main():
    tenants = json.loads((DATA / "tenants.json").read_text())
    observations = json.loads((DATA / "observations.json").read_text())
    demo = [t for t in tenants if "DEMO" in t["tenant_id"]]
    demo.sort(key=lambda t: t["tenant_id"])

    for t in demo:
        last = latest_obs(observations, t["tenant_id"]) or {}
        print(f"{t['tenant_id']}  {t['name']}")
        print(f"    category={t['category']}  sqft={t['sqft']}  rent_per_sqft={t['rent_per_sqft']}")
        print(f"    lease_start={t['lease_start']}  lease_end={t['lease_end']}  status={t['status']}")
        print(f"    latest month={last.get('month')}  rent_to_sales_ratio={last.get('rent_to_sales_ratio')}")
        print(f"    credit_band={last.get('credit_band')}  notches={last.get('credit_notches_changed')}  "
              f"enquiry_type={last.get('enquiry_type')}  enquiry={last.get('relief_or_exit_enquiry')}")
        print()


if __name__ == "__main__":
    main()
