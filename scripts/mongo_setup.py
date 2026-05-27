"""
MongoDB Atlas setup script — creates tenant_mix database and collections.
Run AFTER creating your Atlas M0 cluster and setting MONGODB_URI in .env

Usage:
    pip install pymongo python-dotenv
    python scripts/mongo_setup.py
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid

load_dotenv()

URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("MONGODB_DB", "tenant_mix")

COLLECTIONS = {
    "tenants": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenant_id", "name", "category", "lease_start"],
            "properties": {
                "tenant_id":   {"bsonType": "string"},
                "name":        {"bsonType": "string"},
                "category":    {"bsonType": "string",
                                "description": "e.g. food_beverage, fashion, anchor"},
                "lease_start": {"bsonType": "date"},
                "lease_end":   {"bsonType": ["date", "null"]},
                "status":      {"bsonType": "string",
                                "enum": ["active", "at_risk", "churned", "renewed"]},
            },
        }
    },
    "observations": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenant_id", "observed_at"],
            "properties": {
                "tenant_id":      {"bsonType": "string"},
                "observed_at":    {"bsonType": "date"},
                "foot_traffic":   {"bsonType": ["double", "null"]},
                "sales_psf":      {"bsonType": ["double", "null"]},
                "occupancy_cost_pct": {"bsonType": ["double", "null"]},
            },
        }
    },
    "pending_actions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenant_id", "created_at", "action_type", "draft"],
            "properties": {
                "tenant_id":   {"bsonType": "string"},
                "created_at":  {"bsonType": "date"},
                "action_type": {"bsonType": "string",
                                "enum": ["outreach", "rent_review", "lease_renewal"]},
                "draft":       {"bsonType": "string"},
                "hazard_score":{"bsonType": ["double", "null"]},
                "approved":    {"bsonType": ["bool", "null"]},
            },
        }
    },
    "sent_actions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenant_id", "sent_at", "action_type", "content"],
            "properties": {
                "tenant_id":   {"bsonType": "string"},
                "sent_at":     {"bsonType": "date"},
                "action_type": {"bsonType": "string"},
                "content":     {"bsonType": "string"},
                "approved_by": {"bsonType": ["string", "null"]},
            },
        }
    },
}


def main():
    client = MongoClient(URI)
    db = client[DB_NAME]

    for name, validator in COLLECTIONS.items():
        try:
            db.create_collection(name, validator={"$jsonSchema": validator["$jsonSchema"]})
            print(f"  created  {name}")
        except CollectionInvalid:
            print(f"  exists   {name} (skipped)")

    print(f"\nCollections in '{DB_NAME}':")
    for c in db.list_collection_names():
        print(f"  - {c}")

    # Quick connectivity check
    result = db["tenants"].count_documents({})
    print(f"\ntenants count: {result}  (connection OK)")
    client.close()


if __name__ == "__main__":
    main()
