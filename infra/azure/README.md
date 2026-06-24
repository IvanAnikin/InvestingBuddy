# InvestingBuddy — Azure Infrastructure

## Status: Phase 7 complete — Azure OpenAI provisioned; Phase A core staging not yet provisioned

Provisioned (Phase 7):
- `ib-stg-rg` — resource group, `westeurope`
- `ib-stg-openai` — Azure OpenAI S0, endpoint `https://ib-stg-openai-d52d2.openai.azure.com/`
- Deployment: `gpt-4.1-mini` v2025-04-14, GlobalStandard, 10K TPM

Not yet provisioned: App Service, PostgreSQL, Key Vault, Storage, AI Search (Phase A).

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
| Staging | `ib-stg-rg` | `main` | Planned — not provisioned |
| Production | `ib-prod-rg` | `release/*` | Future — Phase 5+ |

---

## Naming Convention

Pattern: `ib-{env}-{resource}` (lowercase, hyphens)

Exceptions:
- Storage Account: `ib{env}storage` (no hyphens — Azure limit)
- Key Vault names must be globally unique — append short random suffix if needed

---

## Staging Resource List (`ib-stg-rg`)

### Core Resources (provision in Phase A)

| Name | Type | SKU | Purpose |
|---|---|---|---|
| `ib-stg-rg` | Resource Group | — | Container for all staging resources |
| `ib-stg-logs` | Log Analytics Workspace | PerGB2018 | Required by Application Insights |
| `ib-stg-insights` | Application Insights | — | Monitoring, alerting, cost tracking |
| `ib-stg-kv` | Key Vault | Standard | Staging secrets (DB password, OpenAI keys) |
| `ib-stg-api-plan` | App Service Plan | B2 Linux | Compute for FastAPI backend |
| `ib-stg-api` | App Service (Python 3.12) | — | FastAPI backend |
| `ib-stg-web-plan` | App Service Plan | B1 Linux | Compute for Next.js frontend |
| `ib-stg-web` | App Service (Node 22) | — | Next.js frontend |
| `ib-stg-db` | PostgreSQL Flexible Server 16 | Standard_B1ms | Main database |
| `ibstgstorage` | Storage Account (LRS) | Standard | Blob storage for documents |

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

## Resource Specifications

### API App Service (`ib-stg-api`)
- Runtime: Python 3.12 on Linux
- Plan: B2 (2 vCores, 3.5 GB RAM)
- Always On: enabled
- Health check path: `/health`
- Managed Identity: system-assigned (grants Key Vault Secrets User)
- Deployment: ZIP deploy via GitHub Actions

### Web App Service (`ib-stg-web`)
- Runtime: Node.js 22 on Linux
- Plan: B1 (1 vCore, 1.75 GB RAM)
- Managed Identity: system-assigned
- Deployment: ZIP deploy via GitHub Actions

### PostgreSQL Flexible Server (`ib-stg-db`)
- Version: PostgreSQL 16
- SKU: Standard_B1ms (1 vCore, 2 GB RAM)
- Storage: 32 GB auto-grow
- Backup retention: 7 days
- High availability: Disabled (staging only)
- Admin user: `ibadmin`
- Admin password: stored in Key Vault (`ib-stg-kv`) as `db-password`
- Database name: `investingbuddy`
- Firewall: Azure services + specific IP allowlist (no public internet)

### Storage Account (`ibstgstorage`)
- Kind: StorageV2 (General Purpose v2)
- Replication: LRS (Locally Redundant Storage — staging)
- Access: Managed Identity from `ib-stg-api` (no connection string required)
- Container: `investingbuddy-documents` (private, no public access)

### Key Vault (`ib-stg-kv`)
- SKU: Standard
- Permission model: Azure RBAC (not legacy access policies)
- Soft delete: enabled (90 days)
- Purge protection: enabled
- Access:
  - GitHub Actions Service Principal → `Key Vault Secrets Officer`
  - `ib-stg-api` Managed Identity → `Key Vault Secrets User`
  - `ib-stg-web` Managed Identity → `Key Vault Secrets User`

---

## App Service Environment Variables (Staging)

### Backend (`ib-stg-api`)

| Variable | Source | Notes |
|---|---|---|
| `APP_ENV` | Direct | `staging` |
| `DATABASE_URL` | Key Vault ref | Full async connection string |
| `SECRET_KEY` | Key Vault ref | Random 32-byte hex |
| `AZURE_STORAGE_CONTAINER_NAME` | Direct | `investingbuddy-documents` |
| `AZURE_SEARCH_INDEX_NAME` | Direct | `investingbuddy-research` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Key Vault ref | From `ib-stg-insights` |
| `WEBSITE_RUN_FROM_PACKAGE` | Direct | `1` |
| `AZURE_OPENAI_ENDPOINT` | Direct (Phase 4+) | Leave empty until Phase 4 |
| `AZURE_OPENAI_API_KEY` | Key Vault ref (Phase 4+) | Leave empty until Phase 4 |
| `AZURE_OPENAI_API_VERSION` | Direct (Phase 4+) | `2024-08-01-preview` |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Direct (Phase 4+) | Leave empty until Phase 4 |
| `AZURE_SEARCH_ENDPOINT` | Direct (Phase 4+) | Leave empty until Phase 4 |

### Frontend (`ib-stg-web`)

| Variable | Source | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Direct | `https://ib-stg-api.azurewebsites.net` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Direct (Phase 7+) | Leave empty until Phase 7 |
| `CLERK_SECRET_KEY` | Key Vault ref (Phase 7+) | Leave empty until Phase 7 |

---

## Authentication: OIDC (No Long-Lived Credentials)

GitHub Actions authenticates to Azure via OpenID Connect federated credentials —
no `AZURE_CREDENTIALS` JSON secret stored in GitHub.

Setup steps (manual, before provisioning):
1. Create an App Registration in Azure AD (name: `ib-github-actions-stg`)
2. Under the App Registration → Federated credentials → Add credential:
   - Organization: `IvanAnikin`
   - Repository: `InvestingBuddy`
   - Entity: `Branch`
   - Branch: `main`
   - Name: `github-actions-main`
3. Assign roles on `ib-stg-rg`:
   - `Contributor` — for resource management
   - `Key Vault Secrets Officer` — on `ib-stg-kv` specifically
4. Store these three values as GitHub Secrets (no subscription ID in repo):
   - `AZURE_CLIENT_ID` (App Registration client ID)
   - `AZURE_TENANT_ID` (Azure AD tenant ID)
   - `AZURE_SUBSCRIPTION_ID` (subscription — never commit to repo)

GitHub Actions login step:
```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

---

## Required GitHub Secrets

| Secret | Purpose | Created By |
|---|---|---|
| `AZURE_CLIENT_ID` | OIDC App Registration client ID | Azure AD → App Registration |
| `AZURE_TENANT_ID` | Azure AD tenant ID | Azure AD overview page |
| `AZURE_SUBSCRIPTION_ID` | Target subscription | Azure portal → Subscriptions |
| `AZURE_STAGING_DB_PASSWORD` | DB admin password (provisioning only) | Generate locally, store in KV |

Note: `AZURE_STAGING_DB_PASSWORD` is only needed during initial DB provisioning.
After that, the API reads the password from Key Vault via managed identity.

---

## Bicep File Structure (placeholder — not yet implemented)

```
infra/azure/
├── README.md                     # this file
├── main.bicep                    # top-level module, calls sub-modules
├── parameters/
│   └── staging.bicepparam        # staging parameter values
└── modules/
    ├── appservice.bicep          # App Service Plan + App Service
    ├── postgres.bicep            # PostgreSQL Flexible Server
    ├── storage.bicep             # Storage Account + container
    ├── keyvault.bicep            # Key Vault + RBAC assignments
    └── monitoring.bicep          # Log Analytics + Application Insights
```

---

## Estimated Monthly Cost (Staging, without OpenAI/Search)

| Resource | SKU | Est. USD/month |
|---|---|---|
| API App Service | B2 | ~$60 |
| Web App Service | B1 | ~$14 |
| PostgreSQL Flexible | Standard_B1ms | ~$17 |
| Storage Account | LRS, minimal use | ~$1 |
| Key Vault | Standard, minimal ops | ~$1 |
| App Insights + Log Analytics | Pay-per-use (staging) | ~$5 |
| **Subtotal** | | **~$98/month** |

When Azure OpenAI (GPT-4o) and AI Search are added in Phase 4, expect
an additional $50-250/month depending on token usage and index size.

---

## Azure CLI Local Setup

The Azure CLI is installed in a dedicated Python virtual environment at `~/.venvs/azure-cli`.
Do **not** use Homebrew — it does not work reliably on this Mac.
Do **not** use the project's `apps/api/.venv` — the Azure CLI must stay separate.
The `~/.venvs/azure-cli` directory is local-only and must never be committed.

### First-time install

**Note:** Python 3.14 has no pre-built `cryptography` wheel. Use `--prefer-binary` so
pip selects an older binary-compatible wheel instead of compiling from source (which
requires Rust/Cargo).

```bash
python3 -m venv ~/.venvs/azure-cli
source ~/.venvs/azure-cli/bin/activate
pip install --upgrade pip
pip install --prefer-binary azure-cli   # --prefer-binary required on Python 3.14

which az        # should show ~/.venvs/azure-cli/bin/az
az version
az login
az account show
```

### Standard activation (run before every Azure task)

```bash
source ~/.venvs/azure-cli/bin/activate
az version
az account show
```

Confirm the correct subscription is shown before running any `az` or Bicep command.

### What must never be committed

- `~/.venvs/` — local tooling venv
- Subscription IDs, tenant IDs, client IDs — store only in GitHub Secrets
- Azure credentials or service principal JSON
- Database passwords or generated secrets
- `.env`, `.env.staging`, `.env.production`

GitHub Actions authenticates via OIDC (not local credentials). Local `az login`
is for provisioning and inspection only — it does not affect CI/CD.

---

## Pre-Provisioning Checklist

### Phase 7 (complete)
- [x] `~/.venvs/azure-cli` venv created with `pip install --prefer-binary azure-cli`
- [x] `az version` confirms CLI working (`azure-cli 2.87.0`)
- [x] Logged in and correct subscription confirmed
- [x] `ib-stg-rg` created in `westeurope`
- [x] `ib-stg-openai` Azure OpenAI S0 created
- [x] `gpt-4.1-mini` v2025-04-14 deployment created (GlobalStandard, 10K TPM)
- [x] Local `.env` populated; 8/8 real Azure OpenAI integration tests pass

### Phase A Core Staging (next — complete before running Bicep)

#### Local Azure CLI
- [x] `~/.venvs/azure-cli` venv ready
- [ ] Subscription ID noted (store only in GitHub Secrets — never commit)

### Azure AD Setup
- [ ] App Registration `ib-github-actions-stg` created
- [ ] Federated credential configured for `main` branch
- [ ] `Contributor` role assigned on `ib-stg-rg` (or subscription)
- [ ] `Key Vault Secrets Officer` role will be assigned on KV after creation

### GitHub Secrets
- [ ] `AZURE_CLIENT_ID` set in GitHub repository secrets
- [ ] `AZURE_TENANT_ID` set in GitHub repository secrets
- [ ] `AZURE_SUBSCRIPTION_ID` set in GitHub repository secrets
- [ ] `AZURE_STAGING_DB_PASSWORD` generated and set (use: `openssl rand -hex 16`)

### Plan Review
- [ ] Resource naming approved: `ib-stg-*`
- [ ] Region confirmed: `westeurope`
- [ ] Infrastructure approach confirmed: Bicep
- [ ] SKUs reviewed and cost (~$98/month) accepted
- [ ] OIDC approach confirmed (no long-lived credential JSON)

---

## Next Step: Provisioning Prompt

After all checklist items above are complete, provide:
1. Your Azure Subscription ID (will be stored only in GitHub Secrets, not in any committed file)
2. Confirmation that `az account show` shows the correct subscription
3. Confirmation that checklist items above are done

Then run this prompt:

```
Orchestrator: Execute Infrastructure Phase A provisioning.
Environment: staging
Region: westeurope
Resource group: ib-stg-rg
Approach: Bicep
az login has been run — az account show confirms the correct subscription.
All checklist items in infra/azure/README.md are complete.

Proceed to:
1. Write all Bicep module files under infra/azure/
2. Provision the resource group and core resources (no OpenAI, no AI Search)
3. Configure managed identity access on Key Vault and Storage
4. Add GitHub Actions deployment workflows
```
