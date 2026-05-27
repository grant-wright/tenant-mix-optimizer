# Tenant Mix Optimizer

> **Hackathon submission — Google Cloud Rapid Agent Hackathon, June 2026**

An AI agent that monitors shopping-centre tenant health, predicts at-risk leases using survival analysis, and drafts targeted retention interventions — with human approval before any outreach is sent.

---

## Architecture

```
MongoDB Atlas (tenant data)
        │
        ▼
Google Agent Builder ── Gemini API
        │
        ├── query_tenants tool
        ├── get_hazard tool
        └── draft_outreach tool
                │
                ▼
        Human-in-the-Loop approval
                │
                ▼
        sent_actions (MongoDB)
```

*Full architecture diagram — see `docs/architecture.md` (coming Day 6)*

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent runtime | Google Agent Builder (Vertex AI) |
| LLM | Gemini 2.0 Flash / Pro |
| Database | MongoDB Atlas M0 (free tier) |
| Survival model | Python `lifelines` — Cox Proportional Hazards |
| Backend tools | Python 3.12, Google Cloud Functions |
| Secrets | Google Secret Manager |
| Infrastructure | Google Cloud (project: `rapid-agent-tenant-mix`) |

---

## Setup

### Prerequisites

- Python 3.12+
- Google Cloud SDK (`gcloud`)
- MongoDB Atlas account with M0 cluster
- `gh` CLI (optional, for repo management)

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/tenant-mix-optimizer.git
cd tenant-mix-optimizer
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your real credentials
```

### 3. Google Cloud setup

```bash
# Run the setup script (requires gcloud auth first)
pwsh scripts/gcp_setup.ps1
```

### 4. Seed MongoDB

```bash
python scripts/mongo_seed.py
```

---

## Run

*Agent invocation instructions — coming Day 6*

---

## Demo Video

*Link — coming Day 13*

---

## Licence

[MIT](LICENSE)
