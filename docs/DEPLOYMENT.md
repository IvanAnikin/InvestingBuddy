# Deployment

## Status: Phase 7 — Azure OpenAI provisioned for local dev; Phase A staging not yet provisioned

---

## Environment Overview

| Environment | Purpose | Resource Group | Status |
|---|---|---|---|
| Local | Development | Docker Compose | Available from Phase 1 |
| Staging | Pre-production testing | `ib-stg-rg` | Resource group created; Azure OpenAI provisioned (Phase 7); App Service/DB not yet provisioned |
| Production | Live platform | `ib-prod-rg` | Phase 5+ |

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

## Azure Infrastructure Plan

Full details: [`infra/azure/README.md`](../infra/azure/README.md)

### Region

`westeurope` (Netherlands) — lowest latency for EU users, GDPR compliant.

### Infrastructure Approach

Bicep — native Azure DSL, idempotent, GitHub Actions native, no state file.

### Naming Convention

`ib-{env}-{resource}` (e.g. `ib-stg-api`, `ib-stg-db`, `ib-stg-kv`)

Storage Account exception: `ib{env}storage` (no hyphens)

---

## Azure Resources — Staging (`ib-stg-rg`)

### Phase A Core (not yet provisioned)

| Name | Type | SKU | Purpose |
|---|---|---|---|
| `ib-stg-rg` | Resource Group | — | Container for all staging resources |
| `ib-stg-logs` | Log Analytics Workspace | PerGB2018 | Required by Application Insights |
| `ib-stg-insights` | Application Insights | — | Monitoring and alerting |
| `ib-stg-kv` | Key Vault | Standard | Secrets — DB password, app secrets |
| `ib-stg-api-plan` | App Service Plan | B2 Linux | Compute for FastAPI |
| `ib-stg-api` | App Service (Python 3.12) | — | FastAPI backend |
| `ib-stg-web-plan` | App Service Plan | B1 Linux | Compute for Next.js |
| `ib-stg-web` | App Service (Node 22) | — | Next.js frontend |
| `ib-stg-db` | PostgreSQL Flexible Server 16 | Standard_B1ms | Main database |
| `ibstgstorage` | Storage Account (LRS) | Standard | Blob storage for documents |

### Phase 7 (provisioned — local real-LLM dev)

| Name | Type | SKU | Status | Notes |
|---|---|---|---|---|
| `ib-stg-openai` | Azure OpenAI | S0 | **Provisioned** | Endpoint: `https://ib-stg-openai-d52d2.openai.azure.com/`; deployment: `gpt-4.1-mini` v2025-04-14 |

### Phase 4+ (provision when needed)

| Name | Type | Purpose |
|---|---|---|
| `ib-stg-search` | Azure AI Search | RAG / vector search |

### Phase 5+ (future)

| Name | Type | Purpose |
|---|---|---|
| `ib-stg-bus` | Azure Service Bus | Background job queue |
| `ib-stg-func` | Azure Function App | Scheduled jobs |

---

## CI/CD Workflows

### Current (Phase 1–3.5)

```
.github/workflows/
├── api-ci.yml           On push/PR to main (apps/api/**): ruff + pytest
└── web-ci.yml           On push/PR to main (apps/web/**): typecheck + lint + build
```

CI runs on every pull request and push to `main`. Both workflows are path-filtered.

### Phase A (planned — not yet active)

```
.github/workflows/
├── deploy-api-staging.yml    Merge to main → deploy to ib-stg-api (PLACEHOLDER)
└── deploy-web-staging.yml    Merge to main → deploy to ib-stg-web (PLACEHOLDER)
```

Deployment workflows are committed as commented-out placeholders.
Activate only after staging resources are provisioned and checklist is complete.

### GitHub Actions Authentication

OIDC federated credentials — no long-lived `AZURE_CREDENTIALS` JSON secret.

```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

Requires `permissions: id-token: write` in the workflow job.

---

## Environment Variables

Copy `.env.example` to `.env`. The defaults work for local Docker development.

### Backend (`apps/api`)

| Variable | Required | Phase | Notes |
|---|---|---|---|
| `DATABASE_URL` | Yes | 1+ | PostgreSQL async connection string |
| `APP_ENV` | No | 1+ | `development` / `staging` / `production` |
| `SECRET_KEY` | Yes (prod) | 1+ | Random secret — never hardcode |
| `AZURE_OPENAI_ENDPOINT` | No | 7+ | `https://ib-stg-openai-d52d2.openai.azure.com/` (local `.env` only) |
| `AZURE_OPENAI_API_KEY` | No | 7+ | Local `.env` only; Key Vault reference in staging/prod |
| `AZURE_OPENAI_API_VERSION` | No | 7+ | `2025-01-01-preview` (required for gpt-4.1-mini) |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | No | 7+ | `gpt-4.1-mini` |
| `LLM_PROVIDER` | No | 7+ | `mock` (CI default); `azure_openai` (local real-LLM testing) |
| `AZURE_STORAGE_CONNECTION_STRING` | No | 3+ | Use Managed Identity in staging |
| `AZURE_STORAGE_CONTAINER_NAME` | No | 3+ | `investingbuddy-documents` |
| `AZURE_SEARCH_ENDPOINT` | No | 4+ | |
| `AZURE_SEARCH_API_KEY` | No | 4+ | Use Managed Identity in staging |
| `AZURE_SEARCH_INDEX_NAME` | No | 4+ | `investingbuddy-research` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | No | A+ | Injected via App Service config |

### Frontend (`apps/web`)

| Variable | Required | Phase | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | No | 1+ | Defaults to `http://localhost:8000` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | No | 7+ | |
| `CLERK_SECRET_KEY` | No | 7+ | Stored in Key Vault |

---

## GitHub Actions Secrets Required

### Phase A (staging deployment)

| Secret | Purpose | How to Get |
|---|---|---|
| `AZURE_CLIENT_ID` | OIDC App Registration client ID | Azure AD → App Registrations → `ib-github-actions-stg` |
| `AZURE_TENANT_ID` | Azure AD tenant ID | Azure AD → Overview |
| `AZURE_SUBSCRIPTION_ID` | Target subscription | Azure portal → Subscriptions |
| `AZURE_STAGING_DB_PASSWORD` | DB provisioning only | Generate: `openssl rand -hex 16` |

### Phase 4+ (when OpenAI/Search added)

| Secret | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Only if managed identity not used |
| `AZURE_SEARCH_API_KEY` | Only if managed identity not used |

---

## Secrets Strategy

| Where | What |
|---|---|
| `.env` (local, gitignored) | Local development credentials |
| `.env.example` (committed) | Variable names with empty/example values |
| GitHub Actions Secrets | OIDC credentials + provisioning secrets |
| Azure Key Vault (`ib-stg-kv`) | Runtime secrets for staging |
| App Service Configuration | Env vars — non-secret values direct, secrets as Key Vault references |

**Never commit:** `.env`, API keys, Azure credentials, database passwords, subscription IDs.

**Prefer managed identity** over connection-string secrets for all Azure service-to-service access.

---

## Branch and Deployment Strategy

```
feature/*   → PR → CI (lint + test + build) → merge to main
main        → CI → staging deployment (Phase A, after provisioning)
release/*   → production deployment (Phase 5+)
```

Never commit directly to `main` once deployment is active.

---

## Estimated Monthly Cost (Staging)

| Resource | SKU | Est. USD/month |
|---|---|---|
| API App Service | B2 | ~$60 |
| Web App Service | B1 | ~$14 |
| PostgreSQL Flexible | Standard_B1ms | ~$17 |
| Storage Account | LRS, minimal use | ~$1 |
| Key Vault | Standard | ~$1 |
| App Insights + Log Analytics | Pay-per-use | ~$5 |
| **Total (Phase A, no AI)** | | **~$98/month** |

Azure OpenAI + AI Search (Phase 4) will add $50–250/month depending on token volume.

---

## Azure CLI Local Setup

The Azure CLI is installed in a dedicated Python venv at `~/.venvs/azure-cli`.
Do **not** use Homebrew. Do **not** use the project's `apps/api/.venv`.

**Important:** Python 3.14 does not have a pre-built `cryptography` wheel. Use
`--prefer-binary` to force pip to select a compatible binary wheel instead of trying
to compile from source (which requires Rust and will fail without it).

```bash
python3 -m venv ~/.venvs/azure-cli
source ~/.venvs/azure-cli/bin/activate
pip install --upgrade pip
pip install --prefer-binary azure-cli   # --prefer-binary required on Python 3.14

az version
az login
az account show
```

**Before every Azure task**, activate first:

```bash
source ~/.venvs/azure-cli/bin/activate
az version
az account show
```

Full setup details and security constraints: [`infra/azure/README.md`](../infra/azure/README.md)

---

## Provisioning Status

### Phase 7 (complete)
- [x] `~/.venvs/azure-cli` venv created with `pip install --prefer-binary azure-cli`
- [x] `az login` completed and correct subscription confirmed
- [x] `ib-stg-rg` resource group created in `westeurope`
- [x] `ib-stg-openai` Azure OpenAI resource created (S0, `westeurope`)
- [x] `gpt-4.1-mini` v2025-04-14 deployment created (GlobalStandard, 10K TPM)
- [x] Local `.env` populated with endpoint, key, version, deployment name
- [x] `langchain-openai` installed via `pip install -e ".[llm]"` in `apps/api/.venv`
- [x] 8/8 real Azure OpenAI integration tests pass

### Phase A — Core Staging (not yet provisioned)
See full checklist: [`infra/azure/README.md`](../infra/azure/README.md)

- [ ] App Registration `ib-github-actions-stg` created with federated credential
- [ ] GitHub Secrets set: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- [ ] `ib-stg-kv`, `ib-stg-api`, `ib-stg-web`, `ib-stg-db`, `ibstgstorage` provisioned
- [ ] Cost estimate (~$98/month) accepted
- [ ] Staging-only provisioning confirmed (not production)

---

## Provisioning

When the checklist is complete, use the provisioning prompt in
[`infra/azure/README.md`](../infra/azure/README.md) to trigger the next
implementation step.
