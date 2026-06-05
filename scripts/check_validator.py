"""
Read-only check that the LIVE MongoDB validators match what mongo_setup.py
declares — i.e. that a collMod sync actually landed on Atlas.

Motivated by a real gap: mongo_setup.py used to skip existing collections, so
the Day-6 alert-layer widening (credit_band / enquiry_type enums on
observations) never reached the live validator. This script reads each
collection's live $jsonSchema and prints the enum-bearing fields so drift is
visible at a glance.

Usage:
    python scripts/check_validator.py
"""

import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")

# Fields whose live enum we want to eyeball after a sync.
ENUM_FIELDS = {
    "observations": ["credit_band", "enquiry_type"],
    "tenants": ["status"],
    "pending_actions": ["intervention", "status"],
}


def main() -> None:
    client = MongoClient(URI)
    db = client[DB_NAME]

    for coll, fields in ENUM_FIELDS.items():
        spec = next(db.list_collections(filter={"name": coll}), None)
        if spec is None:
            print(f"[MISSING] {coll}")
            continue
        schema = spec.get("options", {}).get("validator", {}).get("$jsonSchema", {})
        props = schema.get("properties", {})
        print(f"\n{coll}:")
        for f in fields:
            enum = props.get(f, {}).get("enum", "<no enum / field absent>")
            print(f"  {f:24} enum = {enum}")

    # The notches field has no enum but should exist after the Day-6 widening.
    obs_props = (
        next(db.list_collections(filter={"name": "observations"}), {})
        .get("options", {})
        .get("validator", {})
        .get("$jsonSchema", {})
        .get("properties", {})
    )
    print(
        f"\nobservations.credit_notches_changed present: "
        f"{'credit_notches_changed' in obs_props}"
    )

    client.close()


if __name__ == "__main__":
    main()
