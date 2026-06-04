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
| Backend tools | Python 3.11, Google Cloud Functions |
| Secrets | Google Secret Manager |
| Infrastructure | Google Cloud (project: `rapid-agent-tenant-mix`) |

---

## Setup

### Prerequisites

- Python 3.11 (matches the Cloud Functions deploy runtime)
- Google Cloud SDK (`gcloud`)
- MongoDB Atlas account with M0 cluster
- `gh` CLI (optional, for repo management)

### 1. Clone, create a virtual environment, and install

```bash
git clone https://github.com/grant-wright/tenant-mix-optimizer.git
cd tenant-mix-optimizer

# Create an isolated environment so this project's deps stay off the system Python
python -m venv .venv

# Activate it:
#   Windows (PowerShell):  .venv\Scripts\Activate.ps1
#   Windows (cmd):         .venv\Scripts\activate.bat
#   macOS / Linux:         source .venv/bin/activate

pip install -r requirements.txt
```

> The `.venv/` folder is gitignored — each machine creates its own. If you skip
> activation, prefix commands with the venv's interpreter, e.g.
> `.venv\Scripts\python.exe scripts/generate_synthetic_data.py`.

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
python scripts/mongo_setup.py
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
