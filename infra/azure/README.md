# InvestingBuddy — Azure Infrastructure

## Status: Phase 12 — Bicep complete; ready to provision

Provisioned:
- `ib-stg-rg` — resource group, `westeurope`
- `ib-stg-openai` — Azure OpenAI S0, endpoint `https://ib-stg-openai-d52d2.openai.azure.com/`
- Deployment: `gpt-4.1-mini` v2025-04-14, GlobalStandard, 10K TPM

Bicep written and ready to deploy (Phase A):
- `ib-stg-kv` — Key Vault Standard
- `ib-stg-logs` / `ib-stg-insights` — Log Analytics + Application Insights
- `ib-stg-api-plan` / `ib-stg-api` — App Service B2 (Python 3.12)
- `ib-stg-web-plan` / `ib-stg-web` — App Service B1 (Node 22)
- `ib-stg-db` — PostgreSQL 16 Flexible Server Standard_B1ms
- `ibstgstorage` — Storage Account LRS

Not yet provisioned: Azure AI Search (Phase 4+).

---

## Region

**Primary region: `westeurope` (Netherlands)**

- Lowest latency for EU users (platform focuses on European public companies)
- GDPR compliant — user data stays in the EU
- All required services available: App Service, PostgreSQL, Key Vault,
  Application Insights, Azure OpenAI (GPT-4o), AI Search
- `northeurope` (Ireland) reserved as future DR/secondary region

---

## Infrastructure Approach

**Bicep** — native Azure DSL, no state file, idempotent, GitHub Actions native.

Not chosen:
- CLI scripts: not idempotent, hard to review
- Terraform: over-engineered for Azure-only MVP
- Azure Developer CLI: too opinionated, less customizable

---

## Environments

| Environment | Resource Group | Branch | Status |
|---|---|---|---|
| Staging | `ib-stg-rg` | `main` | Bicep written; deploy pending OIDC + secrets setup |
| Production | `ib-prod-rg` | `release/*` | Future — Phase 5+ |

---

## Naming Convention

Pattern: `ib-{env}-{resource}` (lowercase, hyphens)

Exceptions:
- Storage Account: `ib{env}storage` (no hyphens — Azure limit)
- Key Vault names must be globally unique

---

## Staging Resource List (`ib-stg-rg`)

### Core Resources (Bicep ready — Phase A)

| Name | Type | SKU | Purpose | Status |
|---|---|---|---|---|
| `ib-stg-rg` | Resource Group | — | Container for all staging resources | **Provisioned** |
| `ib-stg-logs` | Log Analytics Workspace | PerGB2018 | Required by App Insights | Bicep ready |
| `ib-stg-insights` | Application Insights | — | Monitoring, alerting | Bicep ready |
| `ib-stg-kv` | Key Vault | Standard | Staging secrets | Bicep ready |
| `ib-stg-plan` | App Service Plan | **B1 Linux (shared)** | Compute for API + Web | Bicep ready |
| `ib-stg-api` | App Service (Python 3.12) | — | FastAPI backend | Bicep ready |
| `ib-stg-web` | App Service (Node 22) | — | Next.js frontend | Bicep ready |
| `ib-stg-db` | PostgreSQL Flexible Server 16 | Standard_B1ms | Main database | Bicep ready |
| `ibstgstorage` | Storage Account (LRS) | Standard | Blob storage | Bicep ready |

### Phase 7 (provisioned)

| Name | Type | SKU | Status |
|---|---|---|---|
| `ib-stg-openai` | Azure OpenAI | S0 | **Provisioned** — `gpt-4.1-mini` v2025-04-14, GlobalStandard, 10K TPM |

### Phase 4+ (provision when needed)

| Name | Type | Purpose |
|---|---|---|
| `ib-stg-search` | Azure AI Search | RAG / vector search |

### Future Resources (Phase 5+, do not provision now)

| Name | Type | Purpose |
|---|---|---|
| `ib-stg-bus` | Azure Service Bus | Background job queue |
| `ib-stg-func` | Azure Function App | Scheduled jobs |

---

## Bicep File Structure

```
infra/azure/
├── README.md                     # this file
├── main.bicep                    # top-level module — calls all sub-modules + RBAC
├── parameters/
│   └── staging.bicepparam        # staging parameter values (no secrets)
└── modules/
    ├── monitoring.bicep          # Log Analytics Workspace + Application Insights
    ├── keyvault.bicep            # Key Vault Standard (RBAC in main.bicep)
    ├── storage.bicep             # Storage Account LRS + container (RBAC in main.bicep)
    ├── postgres.bicep            # PostgreSQL 16 Flexible Server
    └── appservice.bicep          # App Service Plans + App Services (API + Web)
```

RBAC assignments (Key Vault Secrets User, Storage Blob Data Contributor) are in `main.bicep`
rather than modules — this avoids circular dependencies between appservice → keyvault outputs.

---

## Resource Specifications

### Shared App Service Plan (`ib-stg-plan`)
- SKU: B1 (1 vCore, 1.75 GB RAM) — shared between API and Web
- Linux reserved: true
- Both `ib-stg-api` and `ib-stg-web` run on this plan
- Scale-up: change `sku.name` in `modules/appservice.bicep` (B1 → B2 or P1v3)

### API App Service (`ib-stg-api`)
- Runtime: Python 3.12 on Linux
- Plan: `ib-stg-plan` (shared B1)
- Always On: enabled
- Health check path: `/health`
- Startup: `gunicorn -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 2 --timeout 120 app.main:app`
- Managed Identity: system-assigned
- Key Vault refs: `database-url`, `secret-key`, `openai-api-key`, `staging-basic-auth`

### Web App Service (`ib-stg-web`)
- Runtime: Node.js 22 on Linux
- Plan: `ib-stg-plan` (shared B1)
- Startup: `next start`
- Managed Identity: system-assigned
- `NEXT_PUBLIC_API_BASE_URL` baked into build at CI time

### PostgreSQL Flexible Server (`ib-stg-db`)
- Version: PostgreSQL 16
- SKU: Standard_B1ms (1 vCore, 2 GB RAM)
- Storage: 32 GB auto-grow
- Backup retention: 7 days
- High availability: Disabled (staging only)
- Admin user: `ibadmin`
- Admin password: stored in Key Vault (`ib-stg-kv`) as `db-password`
- Database name: `investingbuddy`
- Firewall: Azure services allowed (0.0.0.0 rule)

### Storage Account (`ibstgstorage`)
- Kind: StorageV2 (General Purpose v2)
- Replication: LRS (staging)
- Public blob access: Disabled
- Container: `investingbuddy-documents` (private)
- Access: API managed identity via Storage Blob Data Contributor role

### Key Vault (`ib-stg-kv`)
- SKU: Standard
- Permission model: Azure RBAC (not legacy access policies)
- Soft delete: 90 days; purge protection: enabled
- RBAC assignments (via main.bicep):
  - API managed identity → `Key Vault Secrets User`
  - Web managed identity → `Key Vault Secrets User`
  - GitHub Actions SP → `Key Vault Secrets Officer` (optional, set `githubActionsPrincipalId`)

### Key Vault Secrets (populate after provisioning)

| Secret Name | Value |
|---|---|
| `database-url` | `postgresql+psycopg://ibadmin:<pwd>@ib-stg-db.postgres.database.azure.com:5432/investingbuddy?sslmode=require` |
| `db-password` | DB admin password |
| `secret-key` | Random 32-byte hex (`openssl rand -hex 32`) |
| `openai-api-key` | Azure OpenAI key from `ib-stg-openai` |
| `staging-basic-auth` | `username:password` for HTTP Basic Auth middleware |

---

## App Service Environment Variables (Staging)

### Backend (`ib-stg-api`)

| Variable | Source | Value |
|---|---|---|
| `APP_ENV` | Direct | `staging` |
| `LLM_PROVIDER` | Direct | `mock` (change to `azure_openai` for LLM testing) |
| `FINANCIAL_DATA_PROVIDER` | Direct | `mock` |
| `AZURE_OPENAI_API_VERSION` | Direct | `2025-01-01-preview` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Direct | `gpt-4.1-mini` |
| `AZURE_OPENAI_ENDPOINT` | Direct | `https://ib-stg-openai-d52d2.openai.azure.com/` |
| `AZURE_STORAGE_CONTAINER_NAME` | Direct | `investingbuddy-documents` |
| `DATABASE_URL` | Key Vault ref | `@Microsoft.KeyVault(SecretUri=...)` |
| `SECRET_KEY` | Key Vault ref | `@Microsoft.KeyVault(SecretUri=...)` |
| `AZURE_OPENAI_API_KEY` | Key Vault ref | `@Microsoft.KeyVault(SecretUri=...)` |
| `STAGING_BASIC_AUTH` | Key Vault ref | `@Microsoft.KeyVault(SecretUri=...)` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Direct | from `ib-stg-insights` |

### Frontend (`ib-stg-web`)

| Variable | Source | Value |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Baked at build time | `https://ib-stg-api.azurewebsites.net` |
| `NODE_ENV` | Direct | `production` |

---

## Authentication: OIDC (No Long-Lived Credentials)

GitHub Actions authenticates to Azure via OpenID Connect federated credentials.
No `AZURE_CREDENTIALS` JSON secret is stored in GitHub.

Setup steps (manual, one-time):
1. Create App Registration `ib-github-actions-stg` in Azure AD
2. Create Service Principal for the App Registration
3. Add federated credential for `main` branch (subject: `repo:IvanAnikin/InvestingBuddy:ref:refs/heads/main`)
4. Assign `Contributor` role on `ib-stg-rg`
5. After KV is provisioned, re-run Bicep with `githubActionsPrincipalId` set to assign `Key Vault Secrets Officer`
6. Store three values as GitHub Secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`

See `docs/DEPLOYMENT.md` for full OIDC setup commands.

---

## Required GitHub Secrets

| Secret | Purpose | Created By |
|---|---|---|
| `AZURE_CLIENT_ID` | OIDC App Registration client ID | Azure AD → App Registration |
| `AZURE_TENANT_ID` | Azure AD tenant ID | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | Target subscription | `az account show --query id -o tsv` |
| `AZURE_STAGING_DB_PASSWORD` | DB admin password (provisioning Bicep only) | `openssl rand -hex 16` |

After provisioning, `AZURE_STAGING_DB_PASSWORD` is only needed to re-run Bicep.
The API reads the DB password from Key Vault via managed identity at runtime.

---

## Azure CLI Local Setup

The Azure CLI is installed in a dedicated Python virtual environment at `~/.venvs/azure-cli`.
Do **not** use Homebrew — it does not work reliably on this Mac.

```bash
source ~/.venvs/azure-cli/bin/activate
az version      # should show 2.87.0+
az account show # confirm correct subscription before any az command
```

---

## Pre-Provisioning Checklist

### Phase 7 (complete)
- [x] `~/.venvs/azure-cli` venv created
- [x] `az version` confirms CLI working
- [x] Logged in and correct subscription confirmed
- [x] `ib-stg-rg` created in `westeurope`
- [x] `ib-stg-openai` Azure OpenAI S0 created
- [x] `gpt-4.1-mini` v2025-04-14 deployment created

### Phase A Core Staging

#### Infrastructure Code (Phase 12 — complete)
- [x] `infra/azure/main.bicep` — full module wiring + RBAC assignments
- [x] `infra/azure/parameters/staging.bicepparam` — reads DB password from env var
- [x] `infra/azure/modules/monitoring.bicep`
- [x] `infra/azure/modules/keyvault.bicep`
- [x] `infra/azure/modules/storage.bicep`
- [x] `infra/azure/modules/postgres.bicep`
- [x] `infra/azure/modules/appservice.bicep`
- [x] `.github/workflows/deploy-api-staging.yml` — activated with OIDC + health check
- [x] `.github/workflows/deploy-web-staging.yml` — activated with OIDC + smoke check

#### Azure AD Setup (manual — before running Bicep)
- [ ] App Registration `ib-github-actions-stg` created
- [ ] Service Principal created for App Registration
- [ ] Federated credential configured for `main` branch
- [ ] `Contributor` role assigned on `ib-stg-rg`

#### GitHub Secrets (manual — before deployment workflows work)
- [ ] `AZURE_CLIENT_ID` set
- [ ] `AZURE_TENANT_ID` set
- [ ] `AZURE_SUBSCRIPTION_ID` set
- [ ] `AZURE_STAGING_DB_PASSWORD` generated and set

#### Provisioning (after above complete)
- [ ] `az deployment group create` executed against `ib-stg-rg`
- [ ] All outputs verified (API URL, web URL, DB FQDN)
- [ ] Key Vault secrets populated (5 secrets)
- [ ] KV Secrets Officer RBAC added for GitHub Actions SP
- [ ] `alembic upgrade head` run on staging DB
- [ ] Staging smoke tests pass (all 6 checks)

---

## Estimated Monthly Cost (Staging, without AI Search)

### Current (Option B — shared B1 plan)

| Resource | SKU | Est. USD/month |
|---|---|---|
| Shared App Service Plan (API + Web) | B1 | ~$14 |
| PostgreSQL Flexible | Standard_B1ms | ~$17 |
| Storage Account | LRS, minimal use | ~$1 |
| Key Vault | Standard, minimal ops | ~$1 |
| App Insights + Log Analytics | Pay-per-use (staging) | ~$5 |
| Azure OpenAI | S0 (already provisioned) | ~$5 |
| **Total** | | **~$43/month** |

### Previous estimate (before cost optimisation)

| Resource | SKU | Est. USD/month |
|---|---|---|
| API App Service Plan | B2 (separate) | ~$60 |
| Web App Service Plan | B1 (separate) | ~$14 |
| Other resources | (same) | ~$29 |
| **Total** | | **~$103/month** |

**Saving: ~$60/month.** A shared B1 plan is appropriate for early staging with
internal-only traffic. Scale up via `sku.name` in `modules/appservice.bicep` when needed.

When Azure AI Search is added (Phase 4), expect an additional $50–250/month.

### Stop billing without destroying resources

```bash
source ~/.venvs/azure-cli/bin/activate
az webapp stop --resource-group ib-stg-rg --name ib-stg-api
az webapp stop --resource-group ib-stg-rg --name ib-stg-web
# PostgreSQL and Key Vault continue to bill at low rate (~$18/month)
```
