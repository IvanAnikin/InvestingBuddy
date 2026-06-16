# Deployment

## Status: Phase 1 — Local development only. Azure not yet provisioned.

---

## Environment Overview

| Environment | Purpose | Status |
|---|---|---|
| Local | Development | Docker Compose — available from Phase 1 |
| Staging | Pre-production testing | Azure App Service — Phase 2+ |
| Production | Live platform | Azure App Service — Phase 2+ |

---

## Local Development

### Prerequisites

- Docker Desktop (for PostgreSQL)
- Python 3.12+
- Node.js 22+

### Quick Start

```bash
# 1. Start PostgreSQL
docker compose up -d

# 2. Backend
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# 3. Frontend
cd apps/web
npm install
npm run dev
```

- Backend: <http://localhost:8000>
- Swagger UI: <http://localhost:8000/api/docs>
- Health check: <http://localhost:8000/health>
- Frontend: <http://localhost:3000>

---

## CI/CD Workflows

### Phase 1 (current)

```
.github/workflows/
├── api-ci.yml      On push/PR to main (apps/api/**): ruff + pytest
└── web-ci.yml      On push/PR to main (apps/web/**): typecheck + lint + build
```

CI runs on every pull request and push to `main`. Both workflows are path-filtered to avoid unnecessary runs.

### Phase 2+ (planned)

```
.github/workflows/
├── deploy-api-staging.yml      Merge to main → deploy to Azure App Service
└── deploy-web-staging.yml      Merge to main → deploy to Azure App Service
```

Production deployment will be manual until Phase 2 is complete.

---

## Required Azure Resources (MVP — not yet provisioned)

| Resource | Type | Purpose |
|---|---|---|
| `investingbuddy-rg` | Resource Group | Container for all resources |
| `investingbuddy-api` | App Service | FastAPI backend |
| `investingbuddy-web` | App Service / Static Web App | Next.js frontend |
| `investingbuddy-db` | PostgreSQL Flexible Server | Main database |
| `investingbuddystorage` | Storage Account | Blob storage for documents |
| `investingbuddy-search` | AI Search Service | Vector search / RAG |
| `investingbuddy-openai` | Azure OpenAI | LLM runtime |
| `investingbuddy-kv` | Key Vault | Production secrets |
| `investingbuddy-insights` | Application Insights | Monitoring |

Future (Phase 5+):
| Resource | Type | Purpose |
|---|---|---|
| `investingbuddy-bus` | Service Bus | Background job queue |
| `investingbuddy-func` | Function App | Scheduled jobs |

---

## Environment Variables

Copy `.env.example` to `.env`. The defaults work for local Docker development.

### Backend (`apps/api`)

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL async connection string |
| `APP_ENV` | No | `development` / `staging` / `production` |
| `SECRET_KEY` | Yes (prod) | Random secret — never hardcode |
| `AZURE_OPENAI_ENDPOINT` | Phase 2+ | |
| `AZURE_OPENAI_API_KEY` | Phase 2+ | Stored in Key Vault in prod |
| `AZURE_OPENAI_API_VERSION` | Phase 2+ | |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Phase 2+ | |
| `AZURE_STORAGE_CONNECTION_STRING` | Phase 3+ | |
| `AZURE_STORAGE_CONTAINER_NAME` | Phase 3+ | |
| `AZURE_SEARCH_ENDPOINT` | Phase 3+ | |
| `AZURE_SEARCH_API_KEY` | Phase 3+ | |
| `AZURE_SEARCH_INDEX_NAME` | Phase 3+ | |

### Frontend (`apps/web`)

| Variable | Required | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | No | Defaults to `http://localhost:8000` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Phase 2+ | |
| `CLERK_SECRET_KEY` | Phase 2+ | |

---

## GitHub Actions Secrets Required (Phase 2+)

```
AZURE_CREDENTIALS                    Service principal for deployment
AZURE_OPENAI_API_KEY
AZURE_STORAGE_CONNECTION_STRING
AZURE_SEARCH_API_KEY
DATABASE_URL_STAGING
CLERK_SECRET_KEY
```

---

## Secrets Strategy

| Where | What |
|---|---|
| `.env` (local, gitignored) | Local development credentials |
| `.env.example` (committed) | Variable names with empty/example values |
| GitHub Actions Secrets | CI/CD deployment credentials |
| Azure Key Vault | Production secrets |
| App Service Configuration | Runtime env vars (linked to Key Vault via managed identity) |

Never commit: `.env`, API keys, Azure credentials, database passwords.

---

## Branch and Deployment Strategy

```
feature/* → PR → CI (tests only) → merge to main
main      → CI → staging deployment (Phase 2+)
```

Production deployment is manual until Phase 2+ deployment automation is complete.

Never commit directly to `main` once deployment is active.
