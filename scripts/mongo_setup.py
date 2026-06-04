"""
MongoDB Atlas setup script — creates tenant_mix database and collections.
Run AFTER creating your Atlas M0 cluster and setting MONGODB_URI in .env

Usage:
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
            "required": ["tenant_id", "name", "category", "sqft", "rent_per_sqft",
                         "lease_start", "mall_context", "status"],
            "properties": {
                "tenant_id":       {"bsonType": "string"},
                "name":            {"bsonType": "string"},
                "category":        {"bsonType": "string"},
                "sqft":            {"bsonType": ["int", "double"]},
                "rent_per_sqft":   {"bsonType": ["int", "double"]},
                "lease_start":     {"bsonType": "string"},
                "lease_end":       {"bsonType": ["string", "null"]},
                "mall_context":    {"bsonType": "string"},
                "zone":            {"bsonType": ["string", "null"]},
                "is_anchor":       {"bsonType": "bool"},
                "persona": {
                    "bsonType": "object",
                    "required": ["operator_type"],
                    "properties": {
                        "operator_type":             {"bsonType": "string"},
                        "risk_appetite":             {"bsonType": "string"},
                        "loyalty_propensity_score":  {"bsonType": ["double", "int"]},
                        "contentious_renegotiations":{"bsonType": "int"},
                        "smooth_renewals":           {"bsonType": "int"},
                    },
                },
                "status":     {"bsonType": "string",
                               "enum": ["active", "exited"]},
                "exit_date":  {"bsonType": ["string", "null"]},
            },
        }
    },
    "observations": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["tenant_id", "month"],
            "properties": {
                "tenant_id":              {"bsonType": "string"},
                "month":                  {"bsonType": "string",
                                           "description": "YYYY-MM"},
                # Branch 1 — revenue
                "sales_total":            {"bsonType": ["double", "int", "null"]},
                "sales_per_sqft":         {"bsonType": ["double", "int", "null"]},
                "foot_traffic_estimate":  {"bsonType": ["double", "int", "null"]},
                "rent_to_sales_ratio":    {"bsonType": ["double", "int", "null"]},
                # Branch 2 — operational
                "late_payment_flag":      {"bsonType": ["bool", "null"]},
                "trading_hours_shortfall":{"bsonType": ["double", "int", "null"]},
                # Branch 3 — leading/relationship
                "relief_or_exit_enquiry": {"bsonType": ["bool", "null"]},
                "stock_depth_index":      {"bsonType": ["double", "int", "null"]},
                # Branch 4 — external (alert layer; not a Cox covariate)
                "credit_trend_3mo":       {"bsonType": ["double", "int", "null"]},
            },
        }
    },
    "pending_actions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["action_id", "tenant_id", "intervention", "status", "created_at"],
            "properties": {
                "action_id":        {"bsonType": "string"},
                "tenant_id":        {"bsonType": "string"},
                "intervention":     {"bsonType": "string",
                                     "enum": ["renew", "renegotiate", "replace", "monitor"]},
                "draft_subject":    {"bsonType": ["string", "null"]},
                "draft_body":       {"bsonType": ["string", "null"]},
                "agent_reasoning":  {"bsonType": ["string", "null"]},
                "hazard_at_drafting":{"bsonType": ["double", "null"]},
                "top_features":     {"bsonType": ["array", "null"]},
                "status":           {"bsonType": "string",
                                     "enum": ["draft", "approved", "rejected"]},
                "created_at":       {"bsonType": "date"},
            },
        }
    },
    "sent_actions": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["action_id", "tenant_id", "intervention", "approved_by", "approved_at"],
            "properties": {
                "action_id":      {"bsonType": "string"},
                "tenant_id":      {"bsonType": "string"},
                "intervention":   {"bsonType": "string"},
                "final_subject":  {"bsonType": ["string", "null"]},
                "final_body":     {"bsonType": ["string", "null"]},
                "approved_by":    {"bsonType": "string"},
                "approved_at":    {"bsonType": "date"},
                "approval_notes": {"bsonType": ["string", "null"]},
            },
        }
    },
    "tenant_responses": {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["action_id", "tenant_id", "response", "simulated_at"],
            "properties": {
                "action_id":      {"bsonType": "string"},
                "tenant_id":      {"bsonType": "string"},
                "response":       {"bsonType": "string",
                                   "enum": ["accept", "counter", "decline"]},
                "counter_terms":  {"bsonType": ["object", "null"]},
                "reasoning":      {"bsonType": ["string", "null"]},
                "emotional_tone": {"bsonType": ["string", "null"]},
                "simulated_at":   {"bsonType": "date"},
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

    # Quick connectivity check — count_documents forces a round-trip to Atlas.
    result = db["tenants"].count_documents({})
    print(f"\ntenants count: {result}  (connection OK)")
    client.close()


if __name__ == "__main__":
    main()
