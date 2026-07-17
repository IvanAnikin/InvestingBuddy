# Deployment

## Status: Phase 12 — Azure Staging Infrastructure Provisioned (Bicep + GitHub Actions active)

---

## Environment Overview

| Environment | Purpose | Resource Group | Status |
|---|---|---|---|
| Local | Development | Docker Compose | Available from Phase 1 |
| Staging | Pre-production testing | `ib-stg-rg` | Bicep written; App Service, DB, KV, Storage ready to provision |
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

### Phase A Core (Bicep written — ready to provision)

| Name | Type | SKU | Purpose | Status |
|---|---|---|---|---|
| `ib-stg-rg` | Resource Group | — | Container for all staging resources | **Provisioned** |
| `ib-stg-logs` | Log Analytics Workspace | PerGB2018 | Required by Application Insights | Bicep ready |
| `ib-stg-insights` | Application Insights | — | Monitoring and alerting | Bicep ready |
| `ib-stg-kv` | Key Vault | Standard | Secrets — DB password, app secrets | Bicep ready |
| `ib-stg-plan` | App Service Plan | **B1 Linux (shared)** | Compute for API + Web (cost-optimised) | Bicep ready |
| `ib-stg-api` | App Service (Python 3.12) | — | FastAPI backend | Bicep ready |
| `ib-stg-web` | App Service (Node 22) | — | Next.js frontend | Bicep ready |
| `ib-stg-db` | PostgreSQL Flexible Server 16 | Standard_B1ms | Main database | Bicep ready |
| `ibstgstorage` | Storage Account (LRS) | Standard | Blob storage for documents | Bicep ready |

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

> **Status: not yet provisioned.** No Phase A resources have been deployed.
> The URLs below will only resolve after `az deployment group create` is run.
> Staging smoke tests cannot be run until provisioning is complete.

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
  --value "postgresql+psycopg://ibadmin:${AZURE_STAGING_DB_PASSWORD}@ib-stg-db.postgres.database.azure.com:5432/investingbuddy?sslmode=require"

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
  --name ib-stg-db \
  --rule-name local-dev \
  --start-ip-address $YOUR_IP \
  --end-ip-address $YOUR_IP

# Run migrations from local machine
cd apps/api
source .venv/bin/activate
DATABASE_URL="postgresql+psycopg://ibadmin:<password>@ib-stg-db.postgres.database.azure.com:5432/investingbuddy?sslmode=require" \
  alembic upgrade head

# Remove the temporary firewall rule
az postgres flexible-server firewall-rule delete \
  --resource-group ib-stg-rg \
  --name ib-stg-db \
  --rule-name local-dev --yes
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
- Use App Service publish profile credentials (stored as GitHub secrets)
- Run a **SHA-verified** post-deploy smoke check that confirms the new build is
  serving — API via `/health` `commit_sha` (Phase 19.2.1), web via `/api/version`
  `commit_sha` + stale-homepage markers (Phase 22.3.1) — never a bare HTTP 200
- **Do not run Alembic migrations** — run manually after schema changes

### GitHub Actions Authentication (Publish Profile)

Publish profiles are downloaded from the Azure Portal App Service blade and stored as
GitHub repository secrets. They contain Kudu deployment credentials scoped to a single
App Service instance.

```yaml
- uses: azure/webapps-deploy@v3
  with:
    app-name: ib-stg-api
    publish-profile: ${{ secrets.AZURE_WEBAPP_PUBLISH_PROFILE_API }}
    package: deploy-api.zip
```

**To rotate:** Download a fresh publish profile from Azure Portal → App Service →
"Get publish profile", then update the GitHub secret via `gh secret set`.

**Future:** Switch to OIDC federated credentials once `ib-github-actions-stg` App
Registration is created (requires Entra ID Application Developer role — currently blocked).

---

## Staging Runtime Configuration (`ib-stg-api`)

### Gunicorn workers — 1 worker on B1 (intentional)

`ib-stg-api` runs a **single gunicorn worker** on the B1 App Service Plan. This is
a deliberate reliability trade-off, not an oversight:

- **B1 memory headroom** — B1 has ~1.75 GB RAM. The LangGraph / LangChain agent
  stack loads a large dependency graph at import time; a second worker roughly
  doubles resident memory and risks OOM restarts on B1.
- **Heavy startup** — cold boot already runs Oryx `pip install` + a slow
  first-import of the agent runtime. Multiple workers multiply cold-start cost.
- **Staging prioritises reliability over concurrency** — staging serves smoke
  tests and admin QA, not production traffic, so a single worker is sufficient.

**Do not raise this to `--workers 2` on B1.** Only move to 2+ workers **after**
scaling the plan to **B2 / S1 or higher**, where the extra memory headroom exists.
The startup command is intentionally pinned to `--workers 1` for the current stack.

### Deploy health-check hardening (Phase 19.2.1)

The API deploy previously could report a **false green**: Azure sometimes routed
the `/health` probe to the **old** container while the new one was still building
during async recycle, so the smoke check saw HTTP 200 from stale code.

The deploy workflow now verifies the **new** container is actually serving:

1. **Build marker** — the workflow writes `build_info.json` (commit SHA, build id,
   build time) into the deployment ZIP. `app/core/build_info.py` reads it at
   startup and `/health` exposes it as `commit_sha` / `build_id` (additive,
   backward-compatible fields — `status`, `environment`, `version` are unchanged).
   `build_info.json` carries only public build identifiers — **no secrets** — and
   is git-ignored (generated at deploy time only).
2. **SHA-matched, stable polling** — the smoke check polls `/health`, parses
   `commit_sha`, and requires **3 consecutive** responses matching the workflow's
   `github.sha` before passing. A single lucky hit on the old container no longer
   passes the gate. It times out (~9 min, tuned for B1 single-worker cold start)
   with a clear failure instead of a false success.

### Oryx / runtime failure detection

The real Phase 19.2 failure was a **transient Oryx virtualenv error**: `uvicorn`
was missing at container runtime (`antenv` failed to build). Re-running the same
deploy fixed it. The smoke check now scans the `/health` response for common
boot-failure signatures and fails clearly with remediation guidance:

- `ModuleNotFoundError` / `No module named 'uvicorn'`
- broken/missing `antenv`
- container exit/crash / generic "Application Error"

On detection it prints the signature, HTTP status, and served commit SHA (no
secrets, no app settings values) and advises **re-running the deploy** — which
clears the transient Oryx failure. It never silently passes because the old
container answered.

### Web deploy cache hardening (Phase 22.3.1)

`ib-stg-web` runs the Next.js standalone bundle with `WEBSITE_RUN_FROM_PACKAGE=1`
and `alwaysOn=false`. Under those settings the **statically prerendered homepage
`/` could keep serving the previous build** after a deploy until a manual
`az webapp restart` flushed it — while dynamic routes like `/admin` updated
immediately. Phase 22.3.1 mirrors the API's SHA-verified pattern for the web app:

1. **Build metadata baked into the bundle** — `deploy-web-staging.yml` injects
   `NEXT_PUBLIC_COMMIT_SHA` / `NEXT_PUBLIC_BUILD_ID` / `NEXT_PUBLIC_BUILD_TIME` /
   `NEXT_PUBLIC_APP_ENV` at build time. Next.js statically inlines `NEXT_PUBLIC_*`,
   so the values are available at runtime on App Service with **no** runtime app
   setting. These are public build identifiers only — **no secrets**.
2. **`/api/version` endpoint** — exposes `{ app, commit_sha, build_id, build_time,
   environment }` (`src/lib/build-info.ts`, `force-dynamic` + `no-store`), so the
   deployed web commit can be verified from the app itself.
3. **Stale-homepage prevention + detection** — `src/app/page.tsx` is
   `force-dynamic` so `/` always reflects the mounted bundle, and the root layout
   embeds `<meta name="x-ib-build-commit">` so a stale prerender is detectable.
4. **SHA-matched, stable polling** — the smoke check requires **3 consecutive**
   `/api/version` responses matching `github.sha`, then checks `/` and `/admin`
   return `200` with the dark-UI marker (`bg-[#060913]`) and that `/` embeds the
   current build commit. A `403` "Site Disabled" is surfaced explicitly. It never
   false-greens on a stale worker.
5. **Best-effort post-deploy restart** — the workflow restarts `ib-stg-web` after
   deploy **only** when an optional `AZURE_CREDENTIALS` service principal is set
   (resource group `ib-stg-rg`, discovered via `az webapp list`). A true restart
   needs the Azure ARM API — Kudu / the publish profile **cannot** restart the
   site — so with only a publish profile the step is skipped cleanly and the
   smoke check remains the enforcement. Provision `AZURE_CREDENTIALS` (Website
   Contributor on `ib-stg-web`) once RBAC/OIDC is granted to automate it.

Manual flush if a stale homepage is ever reported:

```bash
az webapp restart --resource-group ib-stg-rg --name ib-stg-web
```

---

## OIDC Setup (future — blocked on Entra permissions)

Once the Entra ID Application Developer role is granted, replace publish profiles with OIDC:

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
| `NEWS_PROVIDER_NAME` | No | 24.1 | Catalyst news provider selector: unset/`none` → offline `NullNewsProvider` (default, safe); `gdelt` → no-key GDELT adapter; any other name → env-key `ConfigurableWebNewsProvider` (needs the two below) |
| `NEWS_API_KEY` | No | 24.1 | Secret for env-key news providers — **never commit**; Key Vault reference in staging |
| `NEWS_API_BASE_URL` | No | 24.1 | Search endpoint for `ConfigurableWebNewsProvider` (also `NEWS_SEARCH_ENDPOINT`) |
| `NEWS_MAX_RESULTS` | No | 24.1 | Result cap per query (default 10) |
| `NEWS_LOOKBACK_DAYS` | No | 24.1 | Default news lookback window (default 90) |
| `NEWS_TIMEOUT_SECONDS` | No | 24.1 | Per-request timeout (default 8) |

> **Phase 24.1 news provider is optional and non-blocking.** With none of the `NEWS_*` vars set, catalyst discovery still runs (SEC filings + curated/discovered company press-release feeds) and the report shows an explicit "news provider not configured" warning — the deploy still passes and coverage may stay `limited`. No paid provider is required; no secret is committed.

> **Phase 24.1.1 — no-key news activation (recommended).** To add generic news/industry context on staging without any secret, set only `NEWS_PROVIDER_NAME=gdelt` (plus optional `NEWS_MAX_RESULTS`/`NEWS_LOOKBACK_DAYS`/`NEWS_TIMEOUT_SECONDS`) and restart the API. GDELT needs **no `NEWS_API_KEY`**. `NEWS_LOOKBACK_DAYS` scopes the news/press-release lookback (SEC filings stay at 90 days). Company-owned press-release feeds (Apple/Amazon curated URLs corrected in 24.1.1) contribute T1 events even with no news provider configured.

### Frontend (`apps/web`)

| Variable | Required | Phase | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | No | 1+ | Defaults to `http://localhost:8000` |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | No | 7+ | |
| `CLERK_SECRET_KEY` | No | 7+ | Stored in Key Vault |

---

## GitHub Actions Secrets Required

### Phase 12 (active — publish profile auth)

| Secret | Purpose | How to Get |
|---|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Kudu deploy credentials for `ib-stg-api` | Azure Portal → `ib-stg-api` → "Get publish profile" |
| `AZURE_WEBAPP_PUBLISH_PROFILE_WEB` | Kudu deploy credentials for `ib-stg-web` | Azure Portal → `ib-stg-web` → "Get publish profile" |

### Phase A — future OIDC (blocked on Entra permissions)

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

# 7. Verify the deployed web build (Phase 22.3.1) — commit_sha should match the
#    latest deployed GitHub SHA on main; build identifiers only, no secrets.
curl -s https://ib-stg-web.azurewebsites.net/api/version
```

### Phase 25 — Market Candidate Discovery smoke (internal only)

```bash
BASE=https://ib-stg-api.azurewebsites.net

# 1. Start a bounded discovery run (curated seed, free_real). Keep it small.
curl -u admin:<password> -X POST $BASE/api/v1/market-discovery/runs \
  -H "Content-Type: application/json" \
  -d '{"provider_name":"free_real","universe_source":"manual_tickers","tickers":["AAPL","MSFT","NVDA"],"lookback_days":90}'
# → returns run id + status (completed / completed_with_warnings) + counts

# 2. List runs and inspect the candidate queue
curl -u admin:<password> $BASE/api/v1/market-discovery/runs
curl -u admin:<password> "$BASE/api/v1/market-discovery/runs/<run_id>/candidates?sort=candidate_score"

# 3. Candidate detail + promote to full analysis
curl -u admin:<password> $BASE/api/v1/market-discovery/candidates/<candidate_id>
curl -u admin:<password> -X POST $BASE/api/v1/market-discovery/candidates/<candidate_id>/run-analysis
# → links analysis_report_id; open /admin/reports/<id> to verify validation metadata

# 4. Oversized universe must be rejected (422) — never an uncontrolled scan
curl -u admin:<password> -o /dev/null -w "%{http_code}" -X POST $BASE/api/v1/market-discovery/runs \
  -H "Content-Type: application/json" \
  -d '{"universe_source":"manual_tickers","tickers":["A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P"]}'
```

Browser smoke: open `https://ib-stg-web.azurewebsites.net/admin/discovery`, run a
curated-seed scan (5–7 tickers), confirm ranked candidates, no BUY/SELL/HOLD/WATCH
or price-target/fair-value text, every candidate human-review-required, and that
"Run Full Analysis" opens a report under `/admin/reports/<id>`.

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

### Phase A — Core Staging (Bicep written; run checklist before `az deployment group create`)
- [x] `infra/azure/main.bicep` complete — calls all 5 modules
- [x] `infra/azure/modules/monitoring.bicep` — Log Analytics + App Insights
- [x] `infra/azure/modules/keyvault.bicep` — Key Vault Standard
- [x] `infra/azure/modules/storage.bicep` — StorageV2 LRS + container
- [x] `infra/azure/modules/postgres.bicep` — PostgreSQL 16 Flexible Server
- [x] `infra/azure/modules/appservice.bicep` — API B2 + Web B1
- [x] `deploy-api-staging.yml` — active (uncommented, health check included)
- [x] `deploy-web-staging.yml` — active (uncommented, smoke check included)
- [ ] App Registration `ib-github-actions-stg` created with federated credential
- [ ] GitHub Secrets set: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
- [ ] `az deployment group create` executed against `ib-stg-rg`
- [ ] Key Vault secrets populated (`database-url`, `secret-key`, `openai-api-key`, `staging-basic-auth`)
- [ ] `alembic upgrade head` run on staging DB
- [ ] Staging smoke tests pass
