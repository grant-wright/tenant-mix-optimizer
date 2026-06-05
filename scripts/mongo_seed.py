"""
Seed MongoDB Atlas with synthetic tenant + observation data.

Reads data/tenants.json and data/observations.json and writes them into the
tenant_mix database under one of three explicit MODES. There is no default
mode — you must declare intent, because the three converge the collection very
differently and the wrong one fails silently (see decisions below).

    clean   wipe both collections (delete_many, NOT drop — keeps validators +
            indexes), then load. The only mode that fully converges DB == file,
            because it removes orphaned docs. Use after regenerating the data
            (our generator's RNG stream is sensitive, so month keys shift on
            every regen — clean is the normal path here).

    update  refresh values for an EXISTING, stable key set. Pre-flight diffs
            file keys against live keys; if any key would have to be inserted
            (file-only) or any orphan exists (DB-only), it ABORTS and writes
            NOTHING. Use when you intend only to tweak field values and any
            key-set drift means a bug.

    upsert  add + update, never delete. Tolerates extra docs in the DB, so it
            leaves orphans behind — it warns about them but does not remove
            them. Rarely what you want; kept for completeness.

Usage:
    python scripts/mongo_seed.py --mode clean
    python scripts/mongo_seed.py --mode update
    python scripts/mongo_seed.py --mode upsert
    python scripts/mongo_seed.py --mode clean --dry-run     # plan only, write nothing
    python scripts/mongo_seed.py --mode update --data data_train

After any run, scripts/mongo_verify.py confirms live == file with no drift.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")

EXAMPLES = 5  # how many drifting keys to print when aborting


def load_json(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(keys: set) -> str:
    sample = list(sorted(map(str, keys)))[:EXAMPLES]
    more = f" … (+{len(keys) - EXAMPLES} more)" if len(keys) > EXAMPLES else ""
    return ", ".join(sample) + more


def seed(data_dir: str, mode: str, dry_run: bool) -> None:
    data_path = Path(data_dir)
    tenants = load_json(data_path / "tenants.json")
    observations = load_json(data_path / "observations.json")

    print(f"Source : {data_path.resolve()}")
    print(f"Mode   : {mode}{'  (dry-run)' if dry_run else ''}")
    print(f"Tenants: {len(tenants)}")
    print(f"Obs    : {len(observations)}")

    client = MongoClient(URI)
    db = client[DB_NAME]

    # --- key sets: file vs live (drives the update pre-flight + upsert warning) ---
    file_tenant_keys = {t["tenant_id"] for t in tenants}
    file_obs_keys = {(o["tenant_id"], o["month"]) for o in observations}
    live_tenant_keys = set(db["tenants"].distinct("tenant_id"))
    live_obs_keys = {
        (o["tenant_id"], o["month"])
        for o in db["observations"].find({}, {"_id": 0, "tenant_id": 1, "month": 1})
    }

    t_insert = file_tenant_keys - live_tenant_keys   # in file, not in DB
    t_orphan = live_tenant_keys - file_tenant_keys   # in DB, not in file
    o_insert = file_obs_keys - live_obs_keys
    o_orphan = live_obs_keys - file_obs_keys

    print("\nDrift vs live:")
    print(f"  tenants      — file-only: {len(t_insert):>4}   orphans (DB-only): {len(t_orphan):>4}")
    print(f"  observations — file-only: {len(o_insert):>4}   orphans (DB-only): {len(o_orphan):>4}")

    # --- update mode: stable-key contract — any drift is an anomaly → abort ---
    if mode == "update":
        problems = []
        if t_insert:
            problems.append(f"{len(t_insert)} tenant key(s) in file not in DB: {_fmt(t_insert)}")
        if t_orphan:
            problems.append(f"{len(t_orphan)} tenant orphan(s) in DB not in file: {_fmt(t_orphan)}")
        if o_insert:
            problems.append(f"{len(o_insert)} observation key(s) in file not in DB: {_fmt(o_insert)}")
        if o_orphan:
            problems.append(f"{len(o_orphan)} observation orphan(s) in DB not in file: {_fmt(o_orphan)}")
        if problems:
            print("\n[ABORT] --mode update requires a stable key set; nothing written:")
            for p in problems:
                print(f"  - {p}")
            print("  Fix: use --mode clean to converge (adds, updates, AND removes orphans).")
            client.close()
            if not dry_run:
                sys.exit(1)
            return
        print("\n[OK] key set is stable — safe to refresh values in place.")

    if dry_run:
        print("\n[dry-run] Nothing written.")
        client.close()
        return

    # --- clean mode: wipe first so no orphan survives (delete, not drop) ---
    if mode == "clean":
        t_del = db["tenants"].delete_many({}).deleted_count
        o_del = db["observations"].delete_many({}).deleted_count
        print(f"\n[clean] deleted — tenants: {t_del}  observations: {o_del}")

    # clean/upsert may need to insert; update is asserted stable so upsert=False is safe.
    allow_insert = mode in ("clean", "upsert")

    tenant_ops = [
        UpdateOne({"tenant_id": t["tenant_id"]}, {"$set": t}, upsert=allow_insert)
        for t in tenants
    ]
    t_res = db["tenants"].bulk_write(tenant_ops, ordered=False)
    print(f"\ntenants  — upserted: {t_res.upserted_count}  modified: {t_res.modified_count}  matched: {t_res.matched_count}")

    obs_ops = [
        UpdateOne({"tenant_id": o["tenant_id"], "month": o["month"]}, {"$set": o}, upsert=allow_insert)
        for o in observations
    ]
    o_res = db["observations"].bulk_write(obs_ops, ordered=False)
    print(f"observations — upserted: {o_res.upserted_count}  modified: {o_res.modified_count}  matched: {o_res.matched_count}")

    if mode == "upsert" and (t_orphan or o_orphan):
        print(
            f"\n[WARN] upsert left {len(t_orphan)} tenant + {len(o_orphan)} observation orphan(s) "
            "in place.\n       Use --mode clean to remove them; run mongo_verify.py to inspect."
        )

    client.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed MongoDB with synthetic data")
    parser.add_argument("--mode", required=True, choices=["clean", "update", "upsert"],
                        help="clean = wipe+load (converges); update = refresh values, "
                             "abort on key drift; upsert = add/update, leaves orphans")
    parser.add_argument("--data", default="data", help="Source data folder (default: data/)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing")
    args = parser.parse_args()
    seed(args.data, args.mode, args.dry_run)
