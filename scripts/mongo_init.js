// mongosh equivalent of mongo_setup.py
// Usage: mongosh "<your-connection-string>" --file scripts/mongo_init.js

const db = db.getSiblingDB("tenant_mix");

const collections = ["tenants", "observations", "pending_actions", "sent_actions"];

collections.forEach(name => {
  const existing = db.getCollectionNames().includes(name);
  if (!existing) {
    db.createCollection(name);
    print(`created  ${name}`);
  } else {
    print(`exists   ${name} (skipped)`);
  }
});

print("\nCollections:");
db.getCollectionNames().forEach(c => print(`  - ${c}`));
print("\nConnection OK");
