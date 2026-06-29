# Deployment

## Status: Phase 12.2 — API deployed and live; frontend deployment in progress; RBAC + KV + OIDC pending permissions fix

---

## Environment Overview

| Environment | Purpose | Resource Group | Status |
|---|---|---|---|
| Local | Development | Docker Compose | Available from Phase 1 |
| Staging | Pre-production testing | `ib-stg-rg` | API live 2026-06-28; frontend deployment in progress; RBAC + KV + OIDC pending |
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

### Phase A Core (provisioned 2026-06-27)

| Name | Type | SKU | Location | Status |
|---|---|---|---|---|
| `ib-stg-rg` | Resource Group | — | westeurope | **Provisioned** |
| `ib-stg-logs` | Log Analytics Workspace | PerGB2018 | westeurope | **Provisioned** |
| `ib-stg-insights` | Application Insights | — | westeurope | **Provisioned** |
| `ib-stg-kv` | Key Vault | Standard | westeurope | **Provisioned** — secrets pending |
| `ib-stg-plan` | App Service Plan | B1 Linux (shared) | westeurope | **Provisioned** |
| `ib-stg-api` | App Service (Python 3.12) | — | westeurope | **Live** — Phase 12.2 ZIP deploy (2026-06-28) |
| `ib-stg-web` | App Service (Node 22) | — | westeurope | **Deploying** — run-from-package in progress |
| `ib-stg-psql` | PostgreSQL Flexible Server 16 | Standard_B1ms | **northeurope** ¹ | **Provisioned** — migrations 001–004 applied 2026-06-28 |
| `ibstgstorage` | Storage Account (LRS) | Standard | westeurope | **Provisioned** |

¹ Named `ib-stg-psql` (not `ib-stg-db`): a failed westeurope attempt left a ghost ARM reservation for `ib-stg-db`; westeurope is also offer-restricted for PostgreSQL on this MSDN subscription. `northeurope` (Ireland) is EU/GDPR compliant. FQDN: `ib-stg-psql.postgres.database.azure.com`

### Phase 7 (provisioned — local real-LLM dev)

| Name | Type | SKU | Status | Notes |
|---|---|---|---|---|
| `ib-stg-openai` | Azure OpenAI | S0 | **Provisioned** | Endpoint: `https://ib-stg-openai-d52d2.openai.azure.com/`; deployment: `gpt-4.1-mini` v2025-04-14 |

### Phase 4+ (provision when needed)

| Name | Type | Purpose |
|---|---|---|
| `ib-stg-search` | Azure AI Search | RAG / vector search |

---

## Staging URLs (after provisioning)

> **Status (2026-06-28): API is live** via manual ZIP deploy. OIDC and KV references are skipped for now — app settings are configured directly in App Service (temporary). Frontend deployment in progress.
>
> **Smoke test results:**
> - `GET /health` → `{"status":"ok","environment":"staging","version":"0.1.0"}` ✓
> - `GET /api/v1/companies` (no auth) → 401 ✓
> - `GET /api/v1/companies` (with auth) → 200 ✓
> - `POST /api/v1/companies` → 200, UUID returned, DB write confirmed ✓
> - `GET /docs` (no auth) → 401 ✓
> - Analysis workflow → starts correctly; fails at schema validation (see known limitations)

| Service | URL |
|---|---|
| API | `https://ib-stg-api.azurewebsites.net` |
| API Health | `https://ib-stg-api.azurewebsites.net/health` |
| API Swagger | `https://ib-stg-api.azurewebsites.net/api/docs` |
| Web | `https://ib-stg-web.azurewebsites.net` |
| Admin | `https://ib-stg-web.azurewebsites.net/admin` |

**Security note:** Staging URLs are not public-safe until access control is configured.
See [Security Limitations](#security-limitations) below.

---

## Provisioning (Phase A)

### Prerequisites

Before running Bicep:
1. Azure CLI activated: `source ~/.venvs/azure-cli/bin/activate`
2. Correct subscription confirmed: `az account show`
3. `ib-stg-rg` exists in `westeurope` ✓ (already provisioned)
4. Generate DB password: `export AZURE_STAGING_DB_PASSWORD=$(openssl rand -hex 16)`
5. (Optional) Create App Registration `ib-github-actions-stg` for OIDC — see OIDC Setup below

### Run Bicep Deployment

```bash
source ~/.venvs/azure-cli/bin/activate
az account show   # confirm correct subscription

export AZURE_STAGING_DB_PASSWORD=$(openssl rand -hex 16)

az deployment group create \
  --resource-group ib-stg-rg \
  --template-file infra/azure/main.bicep \
  --parameters infra/azure/parameters/staging.bicepparam \
  --parameters dbAdminPassword="$AZURE_STAGING_DB_PASSWORD" \
  --mode Incremental \
  --name "phase12-staging-$(date +%Y%m%d-%H%M%S)"

# Save the DB password in Key Vault immediately after provisioning
az keyvault secret set \
  --vault-name ib-stg-kv \
  --name db-password \
  --value "$AZURE_STAGING_DB_PASSWORD"
```

### Post-Provisioning: Populate Key Vault Secrets

After the Bicep deployment completes, populate these Key Vault secrets:

```bash
source ~/.venvs/azure-cli/bin/activate

# Database connection URL (async driver for psycopg3)
az keyvault secret set \
  --vault-name ib-stg-kv \
  --name database-url \
  --value "postgresql+psycopg://ibadmin:${AZURE_STAGING_DB_PASSWORD}@ib-stg-psql.postgres.database.azure.com:5432/investingbuddy?sslmode=require"

# Random secret key for the API
az keyvault secret set \
  --vault-name ib-stg-kv \
  --name secret-key \
  --value "$(openssl rand -hex 32)"

# Azure OpenAI API key (from ib-stg-openai resource)
az keyvault secret set \
  --vault-name ib-stg-kv \
  --name openai-api-key \
  --value "<get-from-azure-portal>"

# Staging Basic Auth (format: username:password)
az keyvault secret set \
  --vault-name ib-stg-kv \
  --name staging-basic-auth \
  --value "admin:<generate-password>"
```

---

## Running Migrations on Staging

Alembic migrations are **not run automatically** by the deployment workflow.
Run manually after first provisioning or when new migrations are added.

### Option 1: Via Azure App Service SSH (recommended)

```bash
# Open a remote shell on the deployed API container
az webapp ssh --resource-group ib-stg-rg --name ib-stg-api

# Inside the shell:
cd /home/site/wwwroot
source .venv/bin/activate
alembic upgrade head
alembic current
```

### Option 2: Via local machine with DB firewall rule

```bash
# Add your IP to the DB firewall temporarily
YOUR_IP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create \
  --resource-group ib-stg-rg \
  --server-name ib-stg-psql \
  --name local-dev \
  --start-ip-address $YOUR_IP \
  --end-ip-address $YOUR_IP

# Run migrations from local machine
cd apps/api
source .venv/bin/activate
DATABASE_URL="postgresql+psycopg://ibadmin:<password>@ib-stg-psql.postgres.database.azure.com:5432/investingbuddy?sslmode=require" \
  alembic upgrade head

# Remove the temporary firewall rule
az postgres flexible-server firewall-rule delete \
  --resource-group ib-stg-rg \
  --server-name ib-stg-psql \
  --name local-dev --yes
```

### Verify migrations applied

```bash
alembic history     # show all migrations
alembic current     # show current head
```

Expected result after Phase 12: migration `004` is the current head.

---

## CI/CD Workflows

### Active CI (all branches)

```
.github/workflows/
├── api-ci.yml       On push/PR to main (apps/api/**): ruff + pytest
└── web-ci.yml       On push/PR to main (apps/web/**): typecheck + lint + build
```

### Active Deployment (merge to main → staging)

```
.github/workflows/
├── deploy-api-staging.yml    API changes → deploy to ib-stg-api + health check
└── deploy-web-staging.yml    Web changes → deploy to ib-stg-web + smoke check
```

Both deployment workflows:
- Use OIDC federated credentials (no long-lived `AZURE_CREDENTIALS` JSON)
- Require `permissions: id-token: write`
- Run a post-deploy smoke check (health endpoint / HTTP 200)
- **Do not run Alembic migrations** — run manually after schema changes

### GitHub Actions Authentication (OIDC)

No long-lived `AZURE_CREDENTIALS` JSON is stored in GitHub. OIDC uses ephemeral tokens:

```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

Requires `permissions: id-token: write` in the workflow job.

---

## OIDC Setup (one-time manual step)

Before the deployment workflows can authenticate, create the App Registration:

```bash
source ~/.venvs/azure-cli/bin/activate

# 1. Create App Registration
az ad app create --display-name "ib-github-actions-stg"

# 2. Note the App ID (clientId) and Object ID from output
APP_ID=$(az ad app list --display-name "ib-github-actions-stg" --query "[0].appId" -o tsv)
OBJECT_ID=$(az ad app list --display-name "ib-github-actions-stg" --query "[0].id" -o tsv)

# 3. Create a service principal for the app
az ad sp create --id $APP_ID

# 4. Get the service principal object ID (different from App Registration object ID)
SP_OBJECT_ID=$(az ad sp show --id $APP_ID --query "id" -o tsv)

# 5. Assign Contributor role on ib-stg-rg
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
az role assignment create \
  --role "Contributor" \
  --assignee-object-id $SP_OBJECT_ID \
  --assignee-principal-type ServicePrincipal \
  --scope /subscriptions/$SUBSCRIPTION_ID/resourceGroups/ib-stg-rg

# 6. Add federated credential for main branch
az ad app federated-credential create \
  --id $OBJECT_ID \
  --parameters '{
    "name": "github-actions-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:IvanAnikin/InvestingBuddy:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'

echo "CLIENT_ID=$APP_ID"
echo "TENANT_ID=$(az account show --query tenantId -o tsv)"
echo "SUBSCRIPTION_ID=$SUBSCRIPTION_ID"
# Store these three values as GitHub repository secrets (Settings → Secrets → Actions)
# Never commit them to the repository.
```

Then re-run Bicep with `githubActionsPrincipalId=$SP_OBJECT_ID` to grant KV Secrets Officer role.

---

## Environment Variables

Copy `.env.example` to `.env`. The defaults work for local Docker development.

### Backend (`apps/api`)

| Variable | Required | Phase | Notes |
|---|---|---|---|
| `DATABASE_URL` | Yes | 1+ | PostgreSQL async connection string |
| `APP_ENV` | No | 1+ | `development` / `staging` / `production` |
| `SECRET_KEY` | Yes (prod) | 1+ | Random secret — never hardcode |
| `STAGING_BASIC_AUTH` | No | 12+ | Staging access control: `user:pass`. Key Vault ref in staging. |
| `AZURE_OPENAI_ENDPOINT` | No | 7+ | `https://ib-stg-openai-d52d2.openai.azure.com/` (local `.env` only) |
| `AZURE_OPENAI_API_KEY` | No | 7+ | Local `.env` only; Key Vault reference in staging/prod |
| `AZURE_OPENAI_API_VERSION` | No | 7+ | `2025-01-01-preview` (required for gpt-4.1-mini) |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | No | 7+ | `gpt-4.1-mini` |
| `LLM_PROVIDER` | No | 7+ | `mock` (CI default); `azure_openai` (local real-LLM testing) |
| `AZURE_STORAGE_CONNECTION_STRING` | No | 3+ | Use Managed Identity in staging |
| `AZURE_STORAGE_CONTAINER_NAME` | No | 3+ | `investingbuddy-documents` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | No | 12+ | Injected via App Service config |

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
| `AZURE_CLIENT_ID` | OIDC App Registration client ID | See OIDC Setup above |
| `AZURE_TENANT_ID` | Azure AD tenant ID | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | Target subscription | `az account show --query id -o tsv` |
| `AZURE_STAGING_DB_PASSWORD` | DB admin password (provisioning Bicep only) | `openssl rand -hex 16` |

### Phase 4+ (when OpenAI/Search via CI — currently Key Vault managed)

| Secret | Purpose |
|---|---|
| (none currently) | Secrets are Key Vault refs in App Service config |

---

## Secrets Strategy

| Where | What |
|---|---|
| `.env` (local, gitignored) | Local development credentials |
| `.env.example` (committed) | Variable names with empty/example values |
| GitHub Actions Secrets | OIDC credentials + `AZURE_STAGING_DB_PASSWORD` (provisioning only) |
| Azure Key Vault (`ib-stg-kv`) | `database-url`, `secret-key`, `openai-api-key`, `staging-basic-auth` |
| App Service Configuration | Non-secret values direct; secrets as `@Microsoft.KeyVault()` references |

**Never commit:** `.env`, API keys, Azure credentials, database passwords, subscription IDs.

**Prefer managed identity** over connection-string secrets for all Azure service-to-service access.

---

## Staging Smoke Tests

After provisioning and running migrations, verify:

```bash
BASE=https://ib-stg-api.azurewebsites.net

# 1. API health
curl -u admin:<password> $BASE/health

# 2. Swagger docs reachable
curl -u admin:<password> -o /dev/null -w "%{http_code}" $BASE/api/docs

# 3. Create a test company
curl -u admin:<password> -X POST $BASE/api/v1/companies \
  -H "Content-Type: application/json" \
  -d '{"ticker":"TEST","exchange":"OSE","name":"Smoke Test AS"}'

# 4. Trigger analysis with mock provider (no Azure OpenAI needed)
curl -u admin:<password> -X POST $BASE/api/v1/workflows/company-analysis/run \
  -H "Content-Type: application/json" \
  -d '{"ticker":"TEST","exchange":"OSE","provider_name":"mock","use_llm":false}'

# 5. List reports
curl -u admin:<password> $BASE/api/v1/reports

# 6. Check frontend
curl -o /dev/null -w "%{http_code}" https://ib-stg-web.azurewebsites.net
curl -o /dev/null -w "%{http_code}" https://ib-stg-web.azurewebsites.net/admin
```

---

## Security Limitations

### Current state (Phase 12)

- HTTP Basic Auth middleware is active when `STAGING_BASIC_AUTH` is set in Key Vault.
- This is a minimal control — not production-grade authentication.
- Staging URLs are internal-only. Do not share publicly.

### Planned (Phase 12+)

- [ ] Entra External ID (MSAL) authentication for admin users
- [ ] IP restriction on App Service access control settings
- [ ] Clerk JWT authentication on API routes (`/api/v1/admin/*`)
- [ ] Network isolation (VNet integration) for DB and Storage

**Until Entra auth is added:** Do not share staging URLs outside the development team.
**Staging is not public-safe.** Internal admin use only.

---

## Estimated Monthly Cost (Staging)

### Current estimate (Phase A, Option B — shared B1 plan)

| Resource | SKU | Est. USD/month |
|---|---|---|
| Shared App Service Plan (API + Web) | B1 | ~$14 |
| PostgreSQL Flexible | Standard_B1ms | ~$17 |
| Storage Account | LRS, minimal use | ~$1 |
| Key Vault | Standard | ~$1 |
| App Insights + Log Analytics | Pay-per-use | ~$5 |
| Azure OpenAI | S0, 10K TPM (already provisioned) | ~$5 (minimal use) |
| **Total (Phase A, shared B1)** | | **~$43/month** |

### Previous estimate (before cost optimisation)

| Resource | SKU | Est. USD/month |
|---|---|---|
| API App Service Plan | B2 (separate) | ~$60 |
| Web App Service Plan | B1 (separate) | ~$14 |
| Other resources | (same) | ~$29 |
| **Total** | | **~$103/month** |

**Saving: ~$60/month** by sharing one B1 plan instead of maintaining a B2 + B1.

### Why B1 is sufficient for early staging

Early staging is low-traffic — typically one to three internal users running manual tests.
A B1 plan (1 vCore, 1.75 GB RAM) runs both the FastAPI backend and Next.js frontend
comfortably at this scale. The API uses gunicorn with 2 workers (configurable via
`appCommandLine`); the frontend serves pre-built static pages from `.next/`.

### Scale-up path

When staging traffic grows or load testing is needed:

1. **B1 → B2 (same plan, more resources):** Edit `sku.name = 'B2'` in
   `infra/azure/modules/appservice.bicep` and redeploy Bicep. ~$60/month total.
2. **Split plans (API B2 + Web B1):** Revert to two separate plan resources if the
   API needs dedicated resources. ~$74/month total.
3. **P1v3 (premium, auto-scale):** For production-level load. ~$130+/month.

Azure AI Search (Phase 4) will add $50–250/month depending on token volume.

### Cleanup (stop billing)

```bash
source ~/.venvs/azure-cli/bin/activate

# Stop App Services to save compute costs (keeps resources, pauses compute billing)
# Note: the App Service Plan itself continues to bill even when apps are stopped.
# To stop plan billing, delete or scale to Free tier.
az webapp stop --resource-group ib-stg-rg --name ib-stg-api
az webapp stop --resource-group ib-stg-rg --name ib-stg-web

# Or delete all Phase A resources (keep ib-stg-openai which is Phase 7)
# WARNING: This deletes the database. Back up first.
# az group delete --name ib-stg-rg --yes --no-wait
```

---

## Azure CLI Local Setup

The Azure CLI is installed in a dedicated Python venv at `~/.venvs/azure-cli`.
Do **not** use Homebrew. Do **not** use the project's `apps/api/.venv`.

**Before every Azure task**, activate first:

```bash
source ~/.venvs/azure-cli/bin/activate
az version
az account show
```

Full setup details: [`infra/azure/README.md`](../infra/azure/README.md)

---

## Branch and Deployment Strategy

```
feature/*   → PR → CI (lint + test + build) → merge to main
main        → CI → staging deployment (deploy-api-staging + deploy-web-staging)
release/*   → production deployment (Phase 5+)
```

Never commit directly to `main` once deployment is active.

---

## Provisioning Status

### Phase 7 (complete)
- [x] `~/.venvs/azure-cli` venv created
- [x] `az login` completed and correct subscription confirmed
- [x] `ib-stg-rg` resource group created in `westeurope`
- [x] `ib-stg-openai` Azure OpenAI resource created (S0, `westeurope`)
- [x] `gpt-4.1-mini` v2025-04-14 deployment created (GlobalStandard, 10K TPM)
- [x] Local `.env` populated with endpoint, key, version, deployment name
- [x] 8/8 real Azure OpenAI integration tests pass

### Phase A — Core Staging

#### Completed 2026-06-27
- [x] `infra/azure/main.bicep` — module wiring + conditional RBAC (`skipRbac` param)
- [x] `infra/azure/modules/monitoring.bicep` — Log Analytics + App Insights
- [x] `infra/azure/modules/keyvault.bicep` — Key Vault Standard (RBAC mode)
- [x] `infra/azure/modules/storage.bicep` — StorageV2 LRS + private container
- [x] `infra/azure/modules/postgres.bicep` — PostgreSQL 16 Flexible Server
- [x] `infra/azure/modules/appservice.bicep` — shared B1 plan, API + Web
- [x] `deploy-api-staging.yml` — OIDC + health check
- [x] `deploy-web-staging.yml` — OIDC + smoke check
- [x] `ib-stg-plan`, `ib-stg-api`, `ib-stg-web` — provisioned (westeurope)
- [x] `ib-stg-kv`, `ib-stg-logs`, `ib-stg-insights`, `ibstgstorage` — provisioned (westeurope)
- [x] `ib-stg-psql` — provisioned (northeurope, named `ib-stg-psql` not `ib-stg-db`)

#### Phase 12.2 — Manual deploy (OIDC skipped) — 2026-06-28
- [x] `apps/api/app/workflows/company_analysis.py` — fixed `parents[5]` → `parents[4]` (IndexError in Oryx extraction path)
- [x] `apps/api/app/main.py` — `hmac.compare_digest` for timing-safe Basic Auth
- [x] `apps/api/app/core/config.py` — `secret_key` field added
- [x] `apps/api/requirements.txt` — created with pinned pip freeze for Oryx build
- [x] App Service app settings configured directly (no KV references — temporary)
- [x] `WEBSITE_WARMUP_PATH=/health` set on ib-stg-api (Azure warmup probe gets 200)
- [x] `WEBSITES_CONTAINER_START_TIME_LIMIT=1800` set on both apps (Oryx extraction timeout)
- [x] API ZIP deployed via `az webapp deploy --type zip` → Oryx pip install + gunicorn startup
- [x] Alembic migrations 001–004 run via local machine with temp firewall rule
- [x] Smoke tests: health ✓, auth ✓, company CRUD ✓
- [x] Frontend: `WEBSITE_RUN_FROM_PACKAGE=1` + full ZIP deploy in progress

#### Known limitations (Phase 12.2)
- Analysis workflow fails at `report_validation_service` because `packages/research-contracts/` is not bundled in the API ZIP. Paths resolve incorrectly in the Oryx extraction dir. Fix: bundle schema in ZIP or use env var override.
- OIDC not configured — deploy requires manual `az webapp deploy` (not GitHub Actions).
- App settings (secrets) stored directly in App Service config — not via KV references. Temporary.
- Frontend on shared B1 plan causes SCM container interference during concurrent deployments.

#### Blocked — Needs Owner role on `ib-stg-rg` (current account is Contributor-only)
- [ ] Re-run Bicep with `param skipRbac = false` to apply managed identity → KV/Storage role assignments
- [ ] Key Vault secrets populated: `database-url`, `db-password`, `secret-key`, `openai-api-key`, `staging-basic-auth`
- [ ] Migrate App Service app settings from direct values → `@Microsoft.KeyVault()` references

#### Blocked — Needs Entra ID Application Developer role
- [ ] App Registration `ib-github-actions-stg` created with federated credential for `main` branch
- [ ] GitHub Secrets set: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`

#### Pending (unblocks after above)
- [ ] GitHub Actions deployment triggered (push to `main`)
- [ ] Bundle `packages/research-contracts/` in API ZIP and fix path resolution
