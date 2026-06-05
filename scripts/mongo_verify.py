"""
Read-only post-seed verification for MongoDB Atlas.

Confirms the live cluster matches the local data/ snapshot after a reseed,
and spot-checks that the Day-6 alert-layer fields (enquiry_type, credit_band,
credit_notches_changed) actually landed on the demo cast's latest month.

Because mongo_seed.py is upsert-only (never deletes), a regenerated dataset
whose tenant lifespans shifted can leave ORPHANED (tenant_id, month) docs
behind. This script catches that: if the live observation count exceeds the
local file count, stale docs are present and a clean reseed is needed.

Usage:
    python scripts/mongo_verify.py             # verify against data/
    python scripts/mongo_verify.py --data data_train
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
import os
from pymongo import MongoClient

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")

DEMO_IDS = {
    "TENANT_DEMO_001": "Atelier Margot",
    "TENANT_DEMO_002": "Pancho's Tacos",
    "TENANT_DEMO_003": "Crystal Mobile",
    "TENANT_DEMO_004": "Pages & Co",
}


def load_json(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(data_dir: str) -> None:
    data_path = Path(data_dir)
    file_tenants = load_json(data_path / "tenants.json")
    file_obs = load_json(data_path / "observations.json")

    client = MongoClient(URI)
    db = client[DB_NAME]

    live_tenants = db["tenants"].count_documents({})
    live_obs = db["observations"].count_documents({})

    print(f"Source file : {data_path.resolve()}")
    print(f"{'':12} {'file':>8} {'live':>8}")
    print(f"tenants      {len(file_tenants):>8} {live_tenants:>8}")
    print(f"observations {len(file_obs):>8} {live_obs:>8}")

    drift = live_obs - len(file_obs)
    if drift != 0:
        print(
            f"\n[WARN] observation count drift = {drift:+d}. "
            "Upsert-only seed cannot remove orphaned (tenant_id, month) docs.\n"
            "       Fix: clean reseed (drop observations, then mongo_seed.py)."
        )
    else:
        print("\n[OK] observation counts match — no orphans.")

    print("\nDemo cast — latest month alert fields:")
    print(f"{'tenant':24} {'month':>6} {'enquiry_type':>16} {'credit_band':>12} {'notches':>8}")
    for tid, name in DEMO_IDS.items():
        latest = db["observations"].find_one(
            {"tenant_id": tid}, sort=[("month", -1)]
        )
        if latest is None:
            print(f"{name:24} {'--- not found ---'}")
            continue
        print(
            f"{name:24} {latest.get('month', '?'):>6} "
            f"{str(latest.get('enquiry_type')):>16} "
            f"{str(latest.get('credit_band')):>12} "
            f"{str(latest.get('credit_notches_changed')):>8}"
        )

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify live Mongo against local data")
    parser.add_argument("--data", default="data", help="Source data folder (default: data/)")
    args = parser.parse_args()
    verify(args.data)
