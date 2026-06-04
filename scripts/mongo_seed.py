"""
Seed MongoDB Atlas with synthetic tenant + observation data.

Reads data/tenants.json and data/observations.json, upserts into the
tenant_mix database. Safe to re-run: upsert on natural key means no
duplicates if you run it twice or after regenerating the data files.

Usage:
    python scripts/mongo_seed.py             # load data/
    python scripts/mongo_seed.py --dry-run   # print counts, touch nothing
    python scripts/mongo_seed.py --data data_train  # load a different folder
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
import os
from pymongo import MongoClient, UpdateOne

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")


def load_json(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def seed(data_dir: str, dry_run: bool) -> None:
    data_path = Path(data_dir)
    tenants = load_json(data_path / "tenants.json")
    observations = load_json(data_path / "observations.json")

    print(f"Source : {data_path.resolve()}")
    print(f"Tenants: {len(tenants)}")
    print(f"Obs    : {len(observations)}")

    if dry_run:
        print("\n[dry-run] Nothing written.")
        return

    client = MongoClient(URI)
    db = client[DB_NAME]

    # --- tenants (upsert on tenant_id) ---
    tenant_ops = [
        UpdateOne(
            {"tenant_id": t["tenant_id"]},
            {"$set": t},
            upsert=True,
        )
        for t in tenants
    ]
    t_result = db["tenants"].bulk_write(tenant_ops, ordered=False)
    print(f"\ntenants  — upserted: {t_result.upserted_count}  modified: {t_result.modified_count}")

    # --- observations (upsert on tenant_id + month) ---
    obs_ops = [
        UpdateOne(
            {"tenant_id": o["tenant_id"], "month": o["month"]},
            {"$set": o},
            upsert=True,
        )
        for o in observations
    ]
    o_result = db["observations"].bulk_write(obs_ops, ordered=False)
    print(f"observations — upserted: {o_result.upserted_count}  modified: {o_result.modified_count}")

    client.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed MongoDB with synthetic data")
    parser.add_argument("--data", default="data", help="Source data folder (default: data/)")
    parser.add_argument("--dry-run", action="store_true", help="Print counts only, write nothing")
    args = parser.parse_args()
    seed(args.data, args.dry_run)
