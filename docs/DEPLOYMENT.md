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

### Async discovery runs on the single B1 worker (Phase 25.1)

A `free_real` market-discovery run executes the full company-analysis workflow
per ticker, so a multi-ticker run can take minutes. Under the single B1 worker
the old synchronous `POST /api/v1/market-discovery/runs` could exceed the
gateway/proxy timeout and return **`504`** even though the backend kept running
and eventually persisted the run/candidates.

Phase 25.1 makes run creation **asynchronous**: `POST /runs` creates the run row
and returns `201` immediately (`status="pending"`), then processes tickers with
FastAPI `BackgroundTasks`. The background worker opens its **own** DB session
(never the request session, which is closed once the response is sent) and
commits progress after every ticker; `/admin/discovery` polls `GET /runs/{run_id}`
until a terminal status.

Operational notes:
- `BackgroundTasks` run **in the same worker process** and are **not durable**
  across an App Service restart / recycle. If the process restarts mid-run the
  run can stick in `running`; a new worker will restart it only after the 30-min
  stale threshold, or an admin can start a fresh run. This is acceptable for the
  Phase 25.1 MVP — a durable queue (Azure Service Bus + Functions) is deferred.
- This is **not** a reason to raise `--workers`; the single worker still handles
  background tasks. Adding workers does not make the tasks durable.
- No new env var and no migration are required for Phase 25.1.

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
   `/api/version` responses matching `github.sha`, then checks that `/` returns
   `200` with the dark-UI marker (`bg-[#060913]`) and embeds the current build
   commit. Since Phase 23 (Admin/Auth Hardening) `/admin` is auth-protected, so
   the check now asserts logged-out `/admin` **redirects `307` to `/login`**
   (proving the auth proxy is deployed — the pre-auth build served `200`),
   verifies build freshness via the public `/login` page's dark-UI marker, and
   asserts logged-out `/api/admin/proxy/*` returns `401`. A `403` "Site Disabled"
   is surfaced explicitly. It never false-greens on a stale worker.
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

## Staging Logging & Telemetry (Phase 27.1D)

Before Phase 27.1D, staging validation relied only on observed HTTP status codes
and persisted run status because `httpLogs.fileSystem` was disabled,
`containerStream.log` was often empty (INFO app logs were dropped under gunicorn),
and Application Insights was not wired. Phase 27.1D makes staging validation
**evidence-based** by emitting safe, structured log events to stdout — which
Azure App Service captures in the container log stream.

### What is logged (safe, structured, one line per event)

| Event | Emitted by | Fields |
|---|---|---|
| `http_request` | `app.core.request_logging` middleware | `method`, `path` (path only — never the query string), `status`, `duration_ms`, `request_id`, `route_family` |
| `discovery_run_started` | `market_discovery_service.process_run` | `run_id`, `mode`, `provider`, `universe_size`, `max_universe`, `max_candidates`, `lookback_days`, and parsed `region`/`country`/`sector`/`theme` (thesis runs) |
| `discovery_candidate` | `market_discovery_service.process_run` | `run_id`, `ticker`, `exchange`, `company_name_source`, `profile_source`, `fundamentals_source`, `sec_eligible`, `reason`, `safety_valid`, `human_review_required` |
| `discovery_run_completed` | `market_discovery_service.process_run` | `run_id`, `status`, `processed_count`, `candidate_count`, `error_count`, `warning_count`, `duration_ms` |
| `discovery_run_failed` | `market_discovery_service.process_discovery_run_by_id` | `run_id`, `exception_type`, safe `error` message |
| `report_validation` | `final_report_generator.validate_final_report` | `report_id`, `schema_valid`, `safety_valid`, `research_complete`, `publication_ready`, `human_review_required`, `forbidden_terms_count`, `missing_required_sections_count` |

`/health` also exposes safe deploy metadata: `status`, `environment`, `version`,
`commit_sha`, `build_id`, `app`, `build_time` — all public build identifiers.

### What is NEVER logged

The `Authorization` / `Cookie` / `Set-Cookie` / `X-API-Key` headers, OAuth tokens,
the Basic-Auth value, API keys, `DATABASE_URL` / connection strings, request or
response **bodies**, query strings, and **raw final-report content**. Report
validation logs booleans and counts only. The redaction helper
(`app.core.log_redaction`) neutralises any value whose key name looks like a
credential before it can reach a log line, and the structured formatter
(`app.core.structured_logging`) redacts sensitive field names and collapses
newlines so one event is always one line (no log forging).

**Third-party request-URL logs.** Setting the root logger to `INFO` also surfaces
INFO logs from libraries such as `httpx`, which log the full request URL —
`GET https://eodhd.com/api/eod/AAPL.US?api_token=<key> ...` — embedding the EODHD
key in the query string. Two guards prevent that leak: `httpx` / `httpcore` /
`urllib3` are capped at `WARNING` (so those request lines never emit), and a
`RedactingFilter` on the stdout handler scrubs token-bearing query params and
Authorization/Cookie echoes out of **every** record — including anything a
third-party library emits at WARNING/ERROR. Verify with the no-secrets grep below
(look specifically for `api_token=` returning only `***REDACTED***`, never a key).

### Controlling verbosity

Two app settings (no code change needed):

- `LOG_LEVEL` — default `INFO` surfaces every event above; set `WARNING` to keep
  only 5xx `http_request` lines, `discovery_run_failed`, and errors.
- `REQUEST_LOGGING_ENABLED` — default `true`; set `false` to silence the
  per-request `http_request` line while keeping discovery/report events.

```bash
source ~/.venvs/azure-cli/bin/activate   # this Mac: ~/.venvs/azure-cli/bin/az

# Reduce verbosity later (example only — adjust to taste):
az webapp config appsettings set --resource-group ib-stg-rg --name ib-stg-api \
  --settings LOG_LEVEL=WARNING
# Restart to apply:
az webapp restart --resource-group ib-stg-rg --name ib-stg-api
```

### Enable App Service filesystem logs + retention (optional)

The structured events already reach the **container log stream** with no extra
config. To ALSO capture them to the App Service filesystem (with a retention cap)
so they survive in `/home/LogFiles`:

```bash
# Enable filesystem application logging at Information level, cap at 35 MB.
az webapp log config --resource-group ib-stg-rg --name ib-stg-api \
  --application-logging filesystem --level information

# HTTP request logs to the filesystem with a retention window (days).
az webapp log config --resource-group ib-stg-rg --name ib-stg-api \
  --web-server-logging filesystem
az webapp config appsettings set --resource-group ib-stg-rg --name ib-stg-api \
  --settings WEBSITE_HTTPLOGGING_RETENTION_DAYS=7
```

### Stream logs live

```bash
# Live tail of the container / application log stream.
az webapp log tail --resource-group ib-stg-rg --name ib-stg-api
```

### Query recent logs (download + grep by event name)

```bash
# Download the current LogFiles as a zip, then grep for a named event.
az webapp log download --resource-group ib-stg-rg --name ib-stg-api \
  --log-file ib-stg-api-logs.zip
unzip -o ib-stg-api-logs.zip -d ib-stg-api-logs
grep -R "discovery_run_completed" ib-stg-api-logs || true
```

### Validate a discovery run using logs

1. Start a run from the admin UI (or `POST /api/v1/market-discovery/thesis-runs`).
2. `az webapp log tail ...` and confirm, in order:
   `discovery_run_started run_id=<id> …` → one `discovery_candidate … ticker=<T> …`
   line per ticker → `discovery_run_completed run_id=<id> status=<terminal> …`.
3. Cross-check `processed_count` / `candidate_count` / `error_count` in the
   `discovery_run_completed` line against the run row (`GET /runs/{id}`).

### Verify NO secrets are logged

```bash
# Any hit here (other than a REDACTED marker) is a defect — investigate.
grep -RiE "Authorization: Bearer|Set-Cookie:|DATABASE_URL=|api_token=[A-Za-z0-9]" \
  ib-stg-api-logs || echo "OK — no secret values found in logs"
```

> Do not run appsettings-changing commands (`az webapp config appsettings set`,
> `az webapp log config`) on staging without explicit approval — they alter the
> running configuration. The commands above are the reference recipe.

---

## Bounded Primary-Document Ingestion (Phase 32A Slice 5)

> **PR open — pre-staging.** Implemented on branch `phase-32a-slice5`; NOT yet
> merged / deployed / staging-validated. Do **not** treat this section as a
> closed/validated deployment record until the merge SHA + deployed SHA + staging
> validation result are on file. All flags ship **default-OFF**; the master flag
> will be **flipped ON on staging for validation** (human-approved `az` change) as
> a later step.

- **New migration `013`** (`013_add_extracted_documents.py`) creates
  `extracted_documents` + `extracted_facts` (reversible, additive, backfill-free).
  Run it on staging with the standard recipe (`## Running Migrations on Staging`
  above); confirm `alembic current` shows `013`. The two tables stay **empty**
  unless `PRIMARY_DOCUMENT_INGESTION_ENABLED` is on, so applying the migration is
  safe even before the flag is flipped. **Rollback:** `alembic downgrade -1`
  drops both tables.
- **New dependency:** `pdfplumber>=0.11,<0.12` (table-aware PDF text/layout
  extraction) — added to `pyproject.toml` + `requirements.txt` alongside the
  existing `pypdf`. Pure-Python; transitively brings pdfminer.six + Pillow (Pillow
  gates the future OCR raster path via a pixel cap). No system binaries; **no OCR
  binary** (tesseract / pdf2image / pymupdf are intentionally NOT added); resolves
  on the Azure App Service Python 3.12 runtime. Confirm it is present in the
  deployed image before enabling ingestion. **Dev caveat:** on a local Python 3.14
  venv, install with `--only-binary=:all:` (cryptography has no 3.14 source-build
  path there).
- **What changed:** with `PRIMARY_DOCUMENT_INGESTION_ENABLED=true` (and
  `SOURCE_CONNECTOR_ENABLED=true`), the source-connector phase inside
  `maybe_run_council` deepens Phase 29B.2 extraction (HTML tables/sections →
  native-PDF text + table extraction → OCR NoOp seam) under an aggregate wall
  budget, feeds validated primary-document facts into the council evidence pack
  (a `primary_document` floor + cap that does NOT weaken the Slice-2
  `financial_floor=3` / news caps), and — when
  `REPORT_CITATION_PERSISTENCE_ENABLED` is ALSO on — persists + reuses extraction
  in the new tables. With the master flag **OFF** (default) behaviour is **exactly
  Phase 29B.2 / Slice 4** — no deep fetch, no persistence, no reuse.
- **OCR is a NoOp seam this slice.** `PRIMARY_DOCUMENT_OCR_ENABLED` exists and is
  double-gated behind the master flag, but the only OCR provider shipped returns
  an empty `ocr_unavailable` result (never fabricated text). A real Azure Document
  Intelligence adapter is a deferred follow-up (needs resource provisioning +
  admin sign-off — see `docs/DECISIONS.md` ADR-014). Scanned / JS-gated issuer
  PDFs still degrade honestly to metadata-only / gaps.
- **New app settings (all safe non-secret defaults; master gate OFF):**

  | Setting | Default | Purpose |
  |---|---|---|
  | `PRIMARY_DOCUMENT_INGESTION_ENABLED` | `false` | Master gate. `false` → deep path never entered, byte-identical. |
  | `PRIMARY_DOCUMENT_OCR_ENABLED` | `false` | Optional OCR (NoOp seam only this slice); double-gated behind the master flag. |
  | `PRIMARY_DOCUMENT_MAX_DOWNLOAD_BYTES` | `8000000` | Hard byte ceiling per fetched document. |
  | `PRIMARY_DOCUMENT_MAX_PDF_PAGES` | `40` | Max leading PDF pages read. |
  | `PRIMARY_DOCUMENT_MAX_OCR_PAGES` | `5` | Max pages rastered + OCR'd. |
  | `PRIMARY_DOCUMENT_FETCH_TIMEOUT_SECONDS` | `15` | Per-document fetch timeout. |
  | `PRIMARY_DOCUMENT_EXTRACTION_TIMEOUT_SECONDS` | `20` | Per-document extraction timeout. |
  | `PRIMARY_DOCUMENT_TOTAL_TIMEOUT_SECONDS` | `45` | HARD per-document total (fetch + extract + parse). |
  | `PRIMARY_DOCUMENT_INGESTION_BUDGET_SECONDS` | `60` | AGGREGATE ingestion wall budget (stays under the ~230s gateway alongside the ~150s council). |
  | `PRIMARY_DOCUMENT_MAX_DOCS_PER_ISSUER` | `3` | Docs ingested per issuer per request. |
  | `PRIMARY_DOCUMENT_MAX_EXCERPTS_PER_DOCUMENT` | `8` | Excerpts per document. |
  | `PRIMARY_DOCUMENT_MAX_EXCERPT_CHARS` | `1200` | Chars per excerpt. |
  | `PRIMARY_DOCUMENT_MIN_EXTRACTION_CONFIDENCE` | `0.6` | Min confidence for a validated fact (else excerpt-only). |
  | `PRIMARY_DOCUMENT_MAX_IMAGE_PIXELS` | `40000000` | Pillow decompression-bomb guard for the OCR raster path. |
  | `PRIMARY_DOCUMENT_EVIDENCE_FLOOR` | `1` | Guaranteed primary-document facts in the pack (ON only with the master flag). |
  | `PRIMARY_DOCUMENT_EVIDENCE_CAP` | `6` | Hard cap on primary-document facts in the pack. |
  | `PRIMARY_DOCUMENT_REUSE_TTL_HOURS` | `24` | Freshness window for reusing a persisted extraction (both ingestion + citation-persistence flags on). |

  These are **tuning knobs, not secrets** — no real secret value is ever printed;
  all KEYS are in `.env.example` with default values. Leave
  `PRIMARY_DOCUMENT_INGESTION_ENABLED=false` until Slice 5 is validated.
  **Rollback:** set `PRIMARY_DOCUMENT_INGESTION_ENABLED=false` to return to the
  exact prior behaviour with no code change (the `013` tables can remain — they
  stay empty).
- **Security controls (see `docs/SECURITY.md`).** No new public endpoint and no
  user-supplied-URL surface; every fetch routes through the allowlist-gated
  hardened layer with an opt-in resolved-IP / DNS-rebinding guard (before & after
  redirects), a %PDF magic-byte check, a decompression-bomb + page/byte cap, and
  the Pillow pixel cap. Extracted text is treated as untrusted, inert data. No JS /
  browser / paywall / auth bypass. Logging is counts / status only — the Phase
  27.1D "Verify NO secrets are logged" grep applies unchanged.

### Staging validation checklist (run AFTER merge + deploy; migration applied; flag flipped ON under the human gate)

Do not mark Slice 5 ✅ until these pass. `PRIMARY_DOCUMENT_INGESTION_ENABLED` ships
`false`; apply migration `013`, then flip the master flag `true` on staging only
after merge/deploy approval, then:

- **A. Deploy identity.** API serves the merged commit SHA (stable polls); DB head
  advances to `013`; `AUTH_TEST_MODE` absent.
- **B. Flag state.** Confirm the master flag is the only intended Slice-5 change;
  the knobs read their safe defaults (or intended staging overrides); OCR flag off.
- **C. OFF-state regression.** With the master flag off, a fresh report is
  byte-compatible with Slice 4 (no deep fetch, no new appendix counts, no rows in
  `extracted_documents` / `extracted_facts`).
- **D. Digital-text issuer.** For an issuer with a native (non-scanned) primary
  document, deep extraction yields validated, page/table-located facts + citations
  with disclosed provenance; the evidence-pack `primary_document` floor/cap holds
  and the Slice-2 financial floor is not weakened.
- **E. Scanned / JS-gated issuer (e.g. Richemont).** Degrades honestly to
  metadata-only / `extraction_failed`; no fabricated facts; no citation from a
  failed / metadata-only extraction; OCR NoOp yields `ocr_unavailable`.
- **F. Persistence + reuse.** With citation-persistence ALSO on, rows persist
  (deduped by `content_hash`); a report regeneration within the TTL reuses the
  stored extraction (no re-fetch); counts are stable / not inflated.
- **G. Report integrity.** `schema_valid=true`, `safety_valid=true`,
  `human_review_required=true`, `publication_ready=false`; extracted facts are
  `needs_human_review`; no recommendation / valuation.
- **H. Security.** No user-supplied-URL surface; response + log secret grep clean;
  logs carry counts / status only (no bytes / extracted text / URLs with secrets).


---

## Document Reachability + Secure Fetching (Phase 32A Slice 5B.1)

> **PR open — pre-staging.** Branch `phase-32a-slice-5b1`. Not yet merged /
> deployed / staging-validated. Do **not** treat this as a closed deployment
> record until the merge SHA, deployed SHA, applied migration and staging
> validation result are on file.

- **Why this exists.** Slice 5A was staging-validated as a *foundation* with an
  explicit efficacy caveat: **0 successful native extractions across 7 issuers**,
  `extracted_documents` / `extracted_facts` both 0/0. Slice 5B.1 fixes the four
  root causes — no SEC filing-BODY path for US issuers, `<a href>`-only discovery
  against JS-rendered IR pages, failures persisting nothing at all, and the four
  PDF-inaccessibility modes collapsing into one opaque status. It also closes the
  two ADR-014 security residuals (DNS-rebinding TOCTOU, synchronous DNS).
- **Migration `014`** (`014_add_document_ingestion_attempts.py`, additive,
  reversible, backfill-free): new `document_ingestion_attempts` table.
  **Head `013` → `014`.** The table stays empty unless
  `PRIMARY_DOCUMENT_INGESTION_ENABLED` **and**
  `REPORT_CITATION_PERSISTENCE_ENABLED` are both on, so applying the migration is
  safe ahead of any flag change. Apply it with the runbook in *Running Migrations
  on Staging* (human-approved), and confirm with `alembic current`.
- **No new master flag.** Everything rides the existing
  `PRIMARY_DOCUMENT_INGESTION_ENABLED` (already ON in staging). With it **off**,
  every path is byte-identical to today.
- **New app settings (all safe non-secret defaults):**

  | Setting | Default | Purpose |
  |---|---|---|
  | `PRIMARY_DOCUMENT_PIN_DNS_ENABLED` | `true` | Resolve-then-connect IP pinning (closes the ADR-014 rebinding TOCTOU). Kill-switch only — turning it off reverts to the weaker Slice 5A check-then-connect behaviour. |
  | `PRIMARY_DOCUMENT_MAX_DISCOVERY_CANDIDATES` | `12` | Cap on document candidates kept from ONE issuer page across all strategies. |
  | `PRIMARY_DOCUMENT_DISCOVERY_STRATEGIES` | `anchors,json_ld,next_data,embedded_json` | Ordered, bounded, non-browser strategies. No crawler, no headless browser. |
  | `PRIMARY_DOCUMENT_SEC_BODY_ENABLED` | `true` | Official SEC filing-body retrieval (10-K / 20-F / 10-Q / 6-K / 8-K). Inert unless the master flag is on. |
  | `PRIMARY_DOCUMENT_SEC_MAX_BODIES` | `2` | Cap on SEC filing bodies fetched per issuer per request. |
  | `SEC_REQUEST_MIN_INTERVAL_MS` | `120` | Client-side SEC throttle. More conservative than SEC's published ~10 req/s ceiling. |

  **Rollback:** set `PRIMARY_DOCUMENT_INGESTION_ENABLED=false` to return to the
  exact prior behaviour with no code change (the `014` table can remain — it stays
  empty). To roll back only the pinning change, set
  `PRIMARY_DOCUMENT_PIN_DNS_ENABLED=false`.
- **Security controls (see `docs/SECURITY.md`).** Still no new public endpoint and
  no user-supplied-URL surface. The connection is now pinned to the validated
  address with `Host` + `sni_hostname` preserved (TLS and certificate hostname
  verification unchanged); the transport **fails closed** on an unpinned host and
  every redirect hop is re-validated and re-pinned. `status` / `failure_code` are
  closed vocabularies, so raw provider text, secrets, signed query strings,
  document bodies and OCR text can never reach the DB or the admin UI; only the
  HTTP status *class* is stored. No password is ever guessed, derived,
  brute-forced or stripped — the only password supplied is the empty one.

### Staging validation checklist (run AFTER merge + deploy + migration `014`)

Do not mark Slice 5B.1 ✅ until these pass.

- **A. Deploy identity.** API serves the merged commit SHA (3 stable polls); DB
  head advances `013` → `014`; `AUTH_TEST_MODE` absent; unauthenticated → 401.
- **B. Flag state.** `PRIMARY_DOCUMENT_INGESTION_ENABLED` unchanged (ON);
  `PRIMARY_DOCUMENT_OCR_ENABLED` still OFF; the 6 new knobs read their defaults;
  confirm only the intended keys changed.
- **C. OFF-state regression.** With `PRIMARY_DOCUMENT_INGESTION_ENABLED=false`, a
  fresh report is byte-compatible with the Slice 5A OFF baseline: no discovery, no
  SEC body fetch, and **zero rows** in `document_ingestion_attempts`.
- **D. AAPL — SEC filing body (the Slice 5A gap).** A fresh AAPL / US /
  `free_real` / LLM-enabled analysis discovers and fetches an official SEC filing
  body, extracts it natively, and persists an `extracted_documents` row with
  page/section provenance. **SEC/XBRL remains authoritative** — the document
  supplements it; the Slice-2 `financial_floor=3` is not weakened.
- **E. European issuer — discovery.** For at least one of CFR / BRBY / KER / MC /
  RMS, the non-browser strategies surface a real document candidate that the
  `<a href>`-only scan did not. Where a document is genuinely inaccessible
  (Richemont's encrypted PDFs), it is classified **`encrypted`** — not a generic
  failure — with no password bypass and no fabricated value.
- **F. Attempt visibility (the core fix).** `document_ingestion_attempts` contains
  a row for **every** attempt including failures, with an honest status and a
  sanitized failure code. Verify no raw exception text, no signed query string, no
  IP address and no exact HTTP status code appears in any column.
- **G. Idempotency.** Re-running the same analysis updates attempt rows in place
  for the same `(company_id, agent_run_id, url_hash)` rather than accumulating; a
  new run creates new rows; `extracted_documents` dedup by `content_hash` holds.
- **H. Security proof.** A private/reserved target is rejected; a redirect to a
  private IP is rejected and its body never fetched; response + log secret grep
  clean; logs carry counts / status only. **Prove pinning actually engaged:** the
  `document_ingestion_attempts.pinned` column must be `true` for successful
  fetches. Then toggle `PRIMARY_DOCUMENT_PIN_DNS_ENABLED` OFF and ON and confirm
  the fetch success rate is identical — pinning rewrites the socket target, and no
  test exercises real TLS/SNI against `sec.gov` or an issuer CDN.
- **H2. SEC access preconditions.** Confirm `company_identity.cik` is populated for
  AAPL — a NULL CIK makes the whole SEC body path a silent no-op. Confirm SEC
  returns 200 (not 403) for the declared `SEC_USER_AGENT`, and that its contact
  mailbox is real per SEC fair-access policy.
- **I. Timing.** Discovery + fetch + extraction durations recorded; total analysis
  stays well under the ~230s gateway (no 502/504); SEC throttle observed.

---

## LLM Council Reliability — Bounded Retry + Deterministic Chair Fallback (Phase 32A Slice 4)

> **PR open — pre-staging.** Not yet merged / deployed / staging-validated. Do
> **not** treat this section as a closed/validated deployment record until the
> merge SHA + deployed SHA + staging validation result are on file. The master
> flag ships **default-OFF** and will be **flipped ON on staging for validation**
> (human-approved `az` change) as a later step. Branch
> `phase-32a-slice4-council-reliability` (`5bbaaf4`).

- **No DB migration** (DB head stays `012`); **no new host, no new endpoint, no
  new secret.** No auth / publishing / SSRF change. Reliability affects council
  EXECUTION only: `publication_ready` stays `false`, `human_review_required`
  stays `true`, and failed agents still create no citations. With the master flag
  **off**, the council path is byte-for-byte identical to today (one attempt per
  agent, no retry, no fallback, null `committee_label` on chair failure).
- **New app settings (all safe non-secret defaults; master gate OFF):**

  | Setting | Default | Purpose |
  |---|---|---|
  | `LLM_COUNCIL_RETRY_ENABLED` | `false` | Master gate for the whole reliability bundle (transient-error retries + reserved critical budget + deterministic chair fallback). `false` → dark / byte-identical. |
  | `LLM_COUNCIL_MAX_RETRIES` | `2` | Extra attempts (beyond the first) for an OPTIONAL agent that failed transiently. |
  | `LLM_COUNCIL_CRITICAL_MAX_RETRIES` | `3` | Extra attempts for a CRITICAL agent (`financial_analyst`, `source_quality_critic`, `red_team`, `committee_chair`; + `valuation_guard` when the pack carries financial evidence). |
  | `LLM_COUNCIL_RETRY_BASE_BACKOFF_SECONDS` | `1.0` | Exponential-backoff base: `base * 2**(attempt-1)`, plus jitter in `[0, base)`. |
  | `LLM_COUNCIL_RETRY_MAX_BACKOFF_SECONDS` | `20.0` | Hard ceiling on a single computed backoff wait. |
  | `LLM_COUNCIL_RETRY_MAX_RETRY_AFTER_SECONDS` | `30.0` | Hard ceiling on an honored provider `retry-after`, so a hostile / large header can never blow the wall-time budget. |
  | `LLM_COUNCIL_TOTAL_BUDGET_SECONDS` | `150.0` | HARD total council wall-time cap — must stay well under the ~230s Azure gateway timeout because the single-company council runs INLINE in the request. |
  | `LLM_COUNCIL_CRITICAL_RESERVE_SECONDS` | `45.0` | Wall-time reserved out of the total budget for the two RESERVED agents (`red_team` + `committee_chair`) so earlier agents can't starve them. |

  These are **tuning knobs, not secrets** — no real secret value is ever printed.
  Leave `LLM_COUNCIL_RETRY_ENABLED=false` on staging until Slice 4 is validated.
  **Rollback:** set `LLM_COUNCIL_RETRY_ENABLED=false` to return to the exact prior
  behaviour with no code change.
- **Safe logging.** Retry events (`llm_agent_retry` / `llm_agent_retry_skipped` /
  `llm_committee_chair_fallback`) carry only ids / agent_name / attempt /
  error_type / duration_ms / backoff_ms / capped retry_after / counts — never
  prompts, completions, evidence, or credentials. The Phase 27.1D "Verify NO
  secrets are logged" grep applies unchanged.
- **Deliberately deferred (see `docs/DECISIONS.md` ADR-013):** concurrent council
  execution (would worsen Azure TPM 429s on the inline path) and per-agent
  evidence projection / prompt trimming (would risk evidence loss / reopen Slice
  2). Sequential execution + bounded retry + reserved budget is the Slice-4 lever.

### Staging validation checklist (run AFTER merge + deploy; flag flipped ON under the human gate)

Do not mark Slice 4 ✅ until these pass. `LLM_COUNCIL_RETRY_ENABLED` ships `false`;
flip it to `true` on staging only after merge/deploy approval, then:

- **A. Deploy identity.** API + Web serve the merged commit SHA (stable polls); DB head
  stays `012` (no migration); `AUTH_TEST_MODE` absent.
- **B. Flag state.** Confirm `LLM_COUNCIL_RETRY_ENABLED=true` is the only Slice-4 change;
  the 7 tuning knobs read their safe defaults (or intended staging overrides).
- **C. AAPL / US / free_real / LLM-enabled fresh run.** Evidence pack stays financially
  complete (Slice 2 unchanged); completed agents retained; transiently-failed agents
  retried; successful agents not re-run; council completion improves over the historical
  4/8 baseline **where provider capacity allows** (provider exhaustion is not a failure
  if D–F hold).
- **D. Committee Chair.** Either the LLM chair completes, **or** the deterministic fallback
  appears (`chair_fallback_used=true`, `committee_label="insufficient_data"`, no
  recommendation/valuation/price language, no citations).
- **E. Red Team.** Completes, or its absence is explicit in counts + warnings.
- **F. Report integrity.** `schema_valid=true`, `safety_valid=true`,
  `human_review_required=true`, `publication_ready=false`; report remains useful and
  visibly partial under partial failure; **no duplicate Source/Citation rows**; failed-agent
  placeholders create no citations.
- **G. CFR (metadata-only).** 8/8 path still functional where capacity permits;
  metadata-only references stay honest; no fabricated financial facts.
- **H. Logs.** Secret grep over the run's structured logs is clean (no prompts /
  completions / evidence / credentials / app-setting values); retry events carry only the
  safe scalar fields.
- **I. Idempotency.** Re-generating the final report yields stable counts and no duplicated
  agent outputs, Sources, or Citations.

## Internal Research Memo Builder (Phase 31 — FINAL phase)

> **Merged + deployed + staging-validated (`b89d5c5`, PR #69).** Full-stack deploy
> (API + Web both at `b89d5c5`, 3 stable polls each); DB head `011`; staging
> validation on file — see `docs/development/closures/phase-31.md`.

- **No DB migration** (DB head stays `011`); **no new host, no new endpoint, no
  new secret.** One new app setting `SOURCE_RESEARCH_MEMO_ENABLED` (default
  `false`; **kept ON on staging after validation**). When **off**, the final
  report is byte-identical to the prior behaviour; when **on**, the final report
  includes an internal `research_memo` section derived deterministically from data
  the system already holds (no new fetch/compute), internal-admin-only,
  `human_review_required=true` / `publication_ready=false`, no
  recommendation/valuation, no publish route. **Rollback:** set
  `SOURCE_RESEARCH_MEMO_ENABLED=false`.
- **Hotfix (`8cc21a6`, PR #70 — merged + deployed + staging-validated
  2026-07-30):** surfaces verified metadata-only primary-source references in the
  report/memo when no document text/facts are extracted. Full-stack deploy (Deploy
  API run `30521771452` + Deploy Web run `30521771446`, both success at
  `8cc21a6`); API `/health` + Web `/api/version` `commit_sha=8cc21a6` (3/3 stable).
  **No new app setting, no new host/endpoint/secret, no DB migration** (head stays
  `011`). Still no OCR / extraction added. See
  `docs/development/closures/phase-31-hotfix.md`.

## Language Detection + Machine-Translation Foundation (Phase 30A)

> **PR open — pre-staging.** Not yet merged / deployed / staging-validated. Do
> **not** treat this section as a closed/validated deployment record until the
> merge SHA + deployed SHA + staging validation result are on file.

- **No DB migration** (DB head stays `011`); **no new allowlisted host, no new
  endpoint, no new secret.** When `TRANSLATION_PROVIDER=llm`, translation reuses
  the **existing Azure OpenAI client** (the same one the LLM council already
  uses) — **no new model deployment, host, or secret** is introduced; logging is
  **text-free** (only counts + language codes, never the prompt / original /
  translated text).
- **New app settings (all OFF / conservative by default):**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_TRANSLATION_ENABLED` | `false` | Master gate for the language-detection + machine-translation foundation. `false` → completely dark (`translated_excerpts` empty `[]`, no `translated_evidence` report block); council pack + report body byte-identical. `true` → per non-English evidence excerpt, ONE bounded machine-assisted translation surfaced as `source_summary_json.llm_council.translated_excerpts` metadata + an optional `report_content["translated_evidence"]` block. **Bounded per-excerpt (never whole-document); original text + source URL preserved; machine-assisted, NOT an official translation; human review required.** |
  | `SOURCE_TRANSLATION_MAX_CHARS` | `400` | Hard char cap per translated excerpt (bounds both input and output). |
  | `SOURCE_TRANSLATION_MAX_EXCERPTS` | `3` | Max excerpts translated per company / source. |
  | `TRANSLATION_PROVIDER` | `fake` | Backend: `fake` (deterministic honest placeholder — the default, never fabricates fluent English) or `llm` (composes the existing Azure OpenAI client — no new host/secret; text-free logging). `llm` is only ever resolved when this is `llm` AND `SOURCE_TRANSLATION_ENABLED=true` AND an LLM client is available. |

  All four flag KEYS are already added to `.env.example` with default values —
  never a real secret. Leave `SOURCE_TRANSLATION_ENABLED=false` on staging until
  Phase 30A is validated. **Rollback:** set `SOURCE_TRANSLATION_ENABLED=false`
  (or `TRANSLATION_PROVIDER=fake`) to return to the exact prior behaviour with no
  code change.
- **Foundation only:** LLM-backed translation is OFF by default; Phase 30B
  (local-language evidence sources) will consume this layer. No auth change, no
  public publishing, no recommendation / valuation output, no publish route.

## Macro Reference Evidence Layer (Phase 29C.1)

> **PR open — pre-staging.** Not yet merged / deployed / staging-validated. Do
> **not** treat this section as a closed/validated deployment record until the
> merge SHA + deployed SHA + staging validation result are on file.

- **No DB migration** (DB head stays `011`); **no new allowlisted host, no new
  endpoint, no new secret** (the macro connectors are network-free and use **no
  API key** — FRED-style keys are deliberately not introduced).
- **New app settings (both OFF/conservative by default):**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_MACRO_ENABLED` | `false` | Master gate for the reference-only macro evidence layer. `false` → completely dark (no macro evidence, no macro gaps); discovery pack + report body byte-identical to Phase 29B. `true` → bounded T2 `macro_report` SOURCE REFERENCES + honest `data_not_sourced` gaps threaded into the discovery council (as `R#` run facts) and the optional company-report `industry_macro_context` block. **Reference-only — no live figures fetched.** |
  | `SOURCE_MACRO_MAX_ITEMS` | `3` | Hard cap on macro source references collected per theme/region. |

  Both flag KEYS are already added to `.env.example` with default values — never a
  real secret. Leave `SOURCE_MACRO_ENABLED=false` on staging until 29C.1 is
  validated. **Rollback:** set `SOURCE_MACRO_ENABLED=false` to return to exact
  Phase 29B behaviour with no code change.
- No auth change, no public publishing, no recommendation/valuation output, no
  publish route.

## Procurement / Tender Event-Trigger Reference Layer (Phase 29D.1)

> **PR open — pre-staging.** Not yet merged / deployed / staging-validated. Do
> **not** treat this section as a closed/validated deployment record until the
> merge SHA + deployed SHA + staging validation result are on file.

- **No DB migration** (DB head stays `011`); **no new allowlisted host, no new
  endpoint, no new secret** (the EU TED / USAspending event connectors are
  network-free and use **no API key**).
- **New app settings (both OFF/conservative by default), INDEPENDENT of the
  Phase 29C `SOURCE_MACRO_ENABLED` flag:**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_EVENT_ENABLED` | `false` | Master gate for the reference-only procurement/tender event-trigger layer. `false` → completely dark (no event evidence, no event gaps). `true` → per relevant theme, ONE bounded T2 procurement/tender SOURCE REFERENCE + an honest "live tenders/awards not fetched at report time" gap threaded into the discovery council (as `R#` run facts) and the optional company-report `industry_event_context` block. **Reference-only — no specific award/tender/contractor/amount/date fetched; WEAK signal, needs human review.** |
  | `SOURCE_EVENT_MAX_ITEMS` | `3` | Hard cap on event-trigger source references collected per theme/region. |

  Both flag KEYS are already added to `.env.example` with default values — never a
  real secret. Leave `SOURCE_EVENT_ENABLED=false` on staging until 29D.1 is
  validated. **Rollback:** set `SOURCE_EVENT_ENABLED=false` to return to the exact
  macro-only-layer behaviour with no code change.
- **Deliberate deferral:** live EU TED / USAspending tender/award FETCH is a Phase
  29D follow-up (reference-only this subphase). No auth change, no public
  publishing, no recommendation/valuation output, no publish route.

## Annual-Report Document Extraction + Primary-Fact Parsing (Phase 29B.2)

> **PR open — pre-staging.** Not yet merged / deployed / staging-validated. Do
> **not** treat this section as a closed/validated deployment record until the
> merge SHA + deployed SHA + staging validation result are on file.

- **No DB migration** — everything is code-defined; the DB head stays at `011`.
- **New dependency:** `pypdf>=4.0,<6` (pure-Python PDF text extraction, **no
  OCR**) — added to `pyproject.toml` + `requirements.txt`. Confirm it is present
  in the deployed image before enabling document extraction.
- **What changed:** with `SOURCE_DOCUMENT_EXTRACTION_ENABLED=true` **and**
  `SOURCE_CONNECTOR_ENABLED=true`, the `company_ir` connector fetches ONE
  discovered annual-report link, extracts bounded excerpts, and parses
  high-confidence primary facts into tiered `T1_primary_filing` evidence — so
  non-US councils reason from real primary text instead of metadata-only items. A
  deterministic **evidence budgeter** bounds the council pack to keep larger
  primary-source packs under the Azure OpenAI TPM ceiling. With the flag **OFF**
  (default) behaviour is **exactly Phase 29B.1** (metadata evidence + honest gaps,
  no document fetch).
- **New settings (all optional, default OFF / conservative):**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_DOCUMENT_EXTRACTION_ENABLED` | `false` | Master gate for fetching + extracting an issuer annual-report document (report-time + `evidence-preview`). `false` → exact Phase 29B.1 behaviour. |
  | `SOURCE_DOCUMENT_EXTRACTION_MAX_BYTES` | `5000000` | Hard byte ceiling for a fetched document. |
  | `SOURCE_DOCUMENT_EXTRACTION_TIMEOUT_SECONDS` | `15` | Per-document fetch timeout. |
  | `SOURCE_DOCUMENT_EXTRACTION_MAX_PAGES` | `20` | Max PDF pages read (no OCR). |
  | `SOURCE_DOCUMENT_EXTRACTION_MAX_EXCERPTS` | `8` | Max bounded excerpts kept. |
  | `SOURCE_DOCUMENT_EXTRACTION_MAX_CHARS_PER_EXCERPT` | `1200` | Per-excerpt char cap. |
  | `SOURCE_DOCUMENT_EXTRACTION_ALLOWED_CONTENT_TYPES` | `application/pdf,text/html,text/plain` | Content-types the fetcher accepts. |
  | `LLM_COUNCIL_EVIDENCE_MAX_ITEMS` | `20` | Deterministic evidence-budget item cap (keeps larger packs under Azure OpenAI TPM quota). |
  | `LLM_COUNCIL_EVIDENCE_MAX_CHARS` | `24000` | Evidence-budget total-char cap. |
  | `LLM_COUNCIL_EVIDENCE_MAX_CHARS_PER_ITEM` | `1200` | Evidence-budget per-item char cap. |

  All of these **flag KEYS are already added to `.env.example`** with empty /
  placeholder values — never a real secret.
- **Planned staging validation (run BEFORE marking closed):** with
  `SOURCE_CONNECTOR_ENABLED=true` + `SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`,
  (A) `POST /evidence-preview`
  `{ticker:"CFR",exchange:"SW",include_document_text:true}` →
  `document_extraction_performed:true`, `company_ir_annual_report_excerpt` +
  `company_ir_financial_fact` (`T1_primary_filing`, `needs_human_review:true`)
  items **or an honest gap**, secret-free; (B) a scanned/encrypted PDF → **no
  excerpts + honest `document_not_extractable` gap** (no OCR); (C) a French URD →
  items flagged `requires_translation` (no fabricated translation); (D) a non-US
  `from-company` final report with the council on →
  `source_summary_json.llm_council.primary_documents` carries a **text-free**
  summary, `safety_valid=true`, `human_review_required=true`,
  `publication_ready=false`; (E) grep the container log — **zero**
  `api_token`/secret occurrences and **no raw document text**; (F) confirm larger
  packs trip the TPM quota less often (evidence budgeter active) — any remaining
  gpt-4.1-mini TPM agent failures are an environmental limit, **not** a 29B.2
  defect.
- **Rollback:** set `SOURCE_DOCUMENT_EXTRACTION_ENABLED=false` (or
  `SOURCE_CONNECTOR_ENABLED=false`) to return to exact Phase 29B.1 behaviour with
  no code change.
- No auth change, no public publishing, no recommendation output, no publish route.

## Non-US Company IR Evidence (Phase 29B.1)

- **No DB migration** — everything is code-defined; the DB head stays at `011`.
- **What changed:** for a **verified issuer** (code-defined `verified_issuer_sources`
  allowlist — Richemont/Swatch/LVMH/Hermès/Kering/Burberry/Pandora/Moncler +
  BAE/ASML/SAP/Nestlé), the `company_ir` connector now emits bounded
  **metadata-only** T1 company-source evidence (IR profile / annual-reports index
  / press index) at report time **with no network call**, so non-US full-analysis
  packs carry citeable company evidence beyond price/model (T5/T6). With
  `SOURCE_CONNECTOR_ENABLED=true`, `evidence-preview` additionally does a
  **bounded, SSRF-safe** live fetch of the issuer's own annual-reports / newsroom
  pages (HTTPS + allowlisted domains only) to extract real annual-report / press
  links.
- **New settings (all optional, conservative defaults):**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_CONNECTOR_MAX_BYTES` | `1000000` | Hard byte ceiling per fetched page (live preview path only). |
  | `SOURCE_CONNECTOR_ALLOWLIST_ONLY` | `true` | Fetch only allowlisted issuer domains (guard against config drift). |
  | `SOURCE_CONNECTOR_MAX_LINKS_PER_PAGE` | `25` | Hard cap on links extracted per fetched page. |

- **Staging validation plan (29B.1):** with `SOURCE_CONNECTOR_ENABLED=true`,
  (A) `POST /evidence-preview` `{ticker:"KER",exchange:"PA"}` → company-IR
  metadata items (`content_source_tier:T1_primary_company_source`,
  `data_quality:metadata_only`) + `source_not_eligible` (SEC) + scaffold +
  `translation_required` gaps, secret-free; (B) `{ticker:"BA",exchange:"LSE"}` →
  BAE Systems company-IR evidence, **no Boeing**, SEC not eligible; (C) a
  `from-company` non-US final report with the council on → pack `known_gaps`
  carry the company-IR + scaffold gaps, `safety_valid=true`,
  `human_review_required=true`, `publication_ready=false`; (D) grep the container
  log — **zero** `api_token`/secret occurrences; (E) the report readable view
  shows unavailable sections without `[object Object]` and the T1/T2 checklist
  item stays unchecked on T5/T6-only reports.
- No auth change, no public publishing, no recommendation output, no publish route.

## Filing & Regulator Connectors — Batch 1 (Phase 29B)

- **No DB migration** — everything is code-defined; the DB head stays at `011`.
- **New settings (all optional, conservative defaults):**

  | Setting | Default | Meaning |
  |---|---|---|
  | `SOURCE_CONNECTOR_ENABLED` | `false` | Gate for feeding connector evidence into the evidence pack + council, and for live fetch in `evidence-preview`. `false` → exact Phase 29A behaviour (gaps recorded, no connector evidence, **no report-time network calls**). |
  | `SOURCE_CONNECTOR_MAX_ITEMS_PER_SOURCE` | `5` | Hard per-connector evidence cap. |
  | `SOURCE_CONNECTOR_TIMEOUT_SECONDS` | `10` | Per-connector live-fetch timeout (preview path only). |

- **Connector statuses now include `scaffolded`** — SEDAR+, ASX, UK FCA NSM,
  Euronext, Deutsche Börse, Nordic have real connector classes that return honest
  gaps but perform **no live fetch** (never a fabricated filing). `sec_edgar` and
  `company_ir` are the live-evidence connectors this phase.
- **New endpoint** `POST /api/v1/sources/evidence-preview` — read-only,
  admin/internal, behind the same staging Basic Auth + admin proxy as every other
  `/api/v1/sources/*` route. The request is **identity-only (no URL field)**; the
  live path (flag on) reaches **fixed known hosts only** (SEC EDGAR + curated
  verified-issuer feeds) — there is no open proxy / SSRF surface. Unknown
  `source_id`s → `400`.
- **Staging validation plan:** with `SOURCE_CONNECTOR_ENABLED=true` (LLM councils
  already enabled on staging for demo), (A) `POST /evidence-preview` for a US
  issuer (`AAPL`) → `live_fetch_performed:true`, SEC `T2`/`T1` items +
  company-IR `T1_primary_company_source` items, secret-free; (B) a non-US issuer
  (`UHR.SW`, `BA.LSE`) → zero evidence + `source_not_eligible` + scaffold gaps,
  **no Boeing confusion** for `BA.LSE`; (C) a from-company `AAPL` final report with
  the council on → evidence pack `known_gaps` carry connector + scaffold gaps,
  `safety_valid=true`, `human_review_required=true`, `publication_ready=false`;
  (D) grep the container log — **zero** `api_token`/secret occurrences.
- No auth change, no public publishing, no recommendation output, no publish route.

## Source Registry + Connector Framework (Phase 29A)

- **No DB migration** — the source registry (`app/services/sources/`) is
  code-defined and in-memory; the DB head stays at `011`.
- **No new environment variables and no new secrets are required.** Connector
  health is derived from *existing* settings without exposing any value — e.g.
  the `eodhd` connector reports `configured` / `not_configured` from
  `EODHD_API_KEY`; it never returns the key.
- Two new **read-only, secret-free** endpoints are added to the existing sources
  router: `GET /api/v1/sources/registry` and `GET /api/v1/sources/health`. Both
  are behind the same staging Basic Auth + admin proxy as every other
  `/api/v1/*` route (already allowlisted under the `/api/v1/sources` prefix).
- **Deploy smoke check:** after deploy, `GET /api/v1/sources/registry` should
  return `summary.enabled >= 6` and `summary.planned >= 20`, and neither
  endpoint's body should contain `api_token`, `bearer`, `authorization`, or a
  connection string (a backend test — `test_registry_endpoint_returns_sources_and_no_secrets`
  — enforces this, and `assert_registry_safe()` is a runtime backstop).
- No auth change, no public publishing, no recommendation output. `/admin/sources`
  is a read-only admin page (no settings editing in 29A).

## LLM Analysis Council (Phase 28A)

The single-company LLM council is **OFF by default** and **fully deterministic**
without it — a plain deploy needs no LLM credentials and every report honestly
reports `llm_used: false`.

### Feature flag + provider settings (app settings)

| Setting | Default | Meaning |
|---|---|---|
| `LLM_COUNCIL_ENABLED` | `false` | Master switch. `false` → deterministic path only |
| `LLM_PROVIDER_COUNCIL` | `fake` | `fake` (offline, tests only) \| `azure_openai` \| `openai` |
| `LLM_MODEL` | *(empty)* | Model id (OpenAI) / informational (Azure) |
| `LLM_TEMPERATURE` | `0.1` | Low → stable council output |
| `LLM_MAX_OUTPUT_TOKENS` | `1200` | Per-agent output cap |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `40` | Per-agent hard timeout |
| `LLM_COUNCIL_MAX_EVIDENCE_ITEMS` | `40` | Bounds evidence-pack size + cost |
| `LLM_COUNCIL_VERSION` | `v1` | Council contract version |
| `OPENAI_API_KEY` | *(empty)* | Only for `LLM_PROVIDER_COUNCIL=openai` |

The `azure_openai` council **reuses** the existing `AZURE_OPENAI_ENDPOINT` /
`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_API_VERSION` /
`AZURE_OPENAI_DEPLOYMENT_NAME` settings (Phase 7). No separate council
credentials. **Never** print, echo, or commit these values; store them in Key
Vault (`ib-stg-kv`). They are never exposed on `/health`.

### Enabling on staging (only with a securely-provided provider)

```bash
source ~/.venvs/azure-cli/bin/activate   # this Mac: ~/.venvs/azure-cli/bin/az

# Requires explicit approval — alters running config. Azure OpenAI creds must
# already exist in Key Vault / app settings (do NOT paste secrets on the CLI).
az webapp config appsettings set --resource-group ib-stg-rg --name ib-stg-api \
  --settings LLM_COUNCIL_ENABLED=true LLM_PROVIDER_COUNCIL=azure_openai
az webapp restart --resource-group ib-stg-rg --name ib-stg-api
```

### Validation from logs (safe events)

```bash
az webapp log tail --resource-group ib-stg-rg --name ib-stg-api
# Expect, for one final-report generation with the council enabled:
#   evidence_pack_built … evidence_item_count=<n>
#   llm_council_started provider=<p> model=<m> …
#   llm_agent_completed agent_name=<a> status=completed …   (x8, or agent_failed)
#   llm_council_completed agents_completed=<c> agents_failed=<f> …
```

Council events carry ids/provider/model/status/counts/duration **only** — never
prompts, completions, evidence excerpts, or credentials. The Phase 27.1D
"Verify NO secrets are logged" grep applies unchanged.

### Report expectations (enabled)

A generated final report should report `llm_used: true`, show provider/model
(no secrets) and the council sections, and keep `schema_valid=true`,
`safety_valid=true`, `human_review_required=true`, `publication_ready=false`.
Any forbidden rating/valuation language is quarantined by the council and, as a
backstop, would flip `safety_valid=false` without ever publishing.

---

## LLM Discovery Council (Phase 28B; async in Phase 28B.2)

The **run-level** discovery council reviews a whole discovery run's candidate set
and decides internal research **priority**. It is **OFF by default**, **manual
admin-triggered only** (never automatic), and needs **no DB migration** — the
review is stored under the run's existing `config_json` JSONB. A plain deploy is
unchanged.

**Phase 28B.2 — async execution.** The council now runs **asynchronously** (the
run-level analog of the Phase 25.1 async discovery-run pattern), so a large
candidate set under a low Azure OpenAI quota no longer rate-limits agents or
approaches the gateway timeout. `POST .../council-review` starts a background job
and returns immediately (`status=pending`); the agents run **sequentially** in a
FastAPI `BackgroundTask` on a **fresh DB session** with per-agent failure
isolation. Clients **poll** `GET .../council-review` until a terminal status
(`completed` / `completed_with_warnings` / `failed`); `/admin/discovery` polls
every 3 s and shows in-progress → completed. The value under
`config_json["discovery_council"]` is a **status envelope** wrapping the review;
a completed review stays readable after the flags are turned off. `BackgroundTasks`
are **process-local, not a durable queue** — an app restart mid-job surfaces as
stale/failed on the next poll (future work: Azure Queue / Celery / a durable
worker).

### Feature flag + settings (app settings)

| Setting | Default | Meaning |
|---|---|---|
| `LLM_DISCOVERY_COUNCIL_ENABLED` | `false` | Discovery-council switch. Requires `LLM_COUNCIL_ENABLED=true` as well |
| `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES` | `25` | Bounds candidates in the evidence pack (cost) |
| `LLM_DISCOVERY_COUNCIL_VERSION` | `v1` | Discovery-council contract version |

Gated by **both** `LLM_COUNCIL_ENABLED` (the shared 28A client gate) **and**
`LLM_DISCOVERY_COUNCIL_ENABLED`. It **reuses** the Phase 28A provider settings
(`LLM_PROVIDER_COUNCIL` / `LLM_MODEL` / `LLM_TEMPERATURE` /
`LLM_MAX_OUTPUT_TOKENS` / `LLM_REQUEST_TIMEOUT_SECONDS`) and the same provider
credentials (Azure OpenAI settings / `OPENAI_API_KEY`). No separate credentials.

### Enabling on staging (only with a securely-provided provider)

```bash
source ~/.venvs/azure-cli/bin/activate   # this Mac: ~/.venvs/azure-cli/bin/az

# Requires explicit approval — alters running config. Enable BOTH flags; the
# provider creds must already exist in Key Vault / app settings.
az webapp config appsettings set --resource-group ib-stg-rg --name ib-stg-api \
  --settings LLM_COUNCIL_ENABLED=true LLM_DISCOVERY_COUNCIL_ENABLED=true \
             LLM_PROVIDER_COUNCIL=azure_openai
az webapp restart --resource-group ib-stg-rg --name ib-stg-api
```

### Triggering + validation

The council is **not** run automatically. Trigger it per run:

```bash
# Phase 28B.2 (async): POST STARTS a background job and returns immediately with
# status=pending. Poll GET for the result — it does NOT block until the agents
# finish.
#   POST /api/v1/market-discovery/runs/{run_id}/council-review   → 200 {status:"pending"}
#         (?force=true re-runs a completed review; a running job returns its status)
#   GET  /api/v1/market-discovery/runs/{run_id}/council-review   → poll until terminal
# When DISABLED and no prior review: POST returns 409 ("Discovery council is
# disabled.") with NO LLM call, and GET returns {status:"disabled"}. A completed
# review stays readable via GET even after the flags are turned off.
az webapp log tail --resource-group ib-stg-rg --name ib-stg-api
# Expect, for one enabled async council-review job:
#   discovery_council_job_queued status=pending run_id=<id>
#   discovery_council_job_started status=running run_id=<id>
#   discovery_council_evidence_built … evidence_item_count=<n> candidate_count=<c>
#   discovery_council_started provider=<p> model=<m> …
#   discovery_council_agent_completed agent_name=<a> status=completed …  (x8, or agent_failed)
#   discovery_council_completed run_quality=<q> agents_completed=<c> safety_valid=true
#   discovery_council_job_completed status=completed agents_completed=<c> duration_ms=<d>
# (A second POST while running logs discovery_council_job_duplicate and starts no job.)
```

Council + job events carry ids/status/provider/model/counts/duration **only** — never
prompts, completions, evidence excerpts, or credentials (the Phase 27.1D "Verify
NO secrets are logged" grep applies unchanged). A stored review keeps
`safety_valid=true`, `human_review_required=true`, `publication_ready=false`, and
only ever uses the internal actions `research_next` / `monitor_for_evidence` /
`insufficient_data` / `reject_for_now` — never a recommendation, price target,
fair value, or upside/downside. No public publish route is added.

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
| `LOG_LEVEL` | No | 27.1D | Root log level for the stdout handler (default `INFO` surfaces structured telemetry; `WARNING` reduces verbosity). See "Staging Logging & Telemetry". |
| `REQUEST_LOGGING_ENABLED` | No | 27.1D | Emit one `http_request` line per request (default `true`). Never logs headers/bodies/query strings/secrets. |
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
| `BACKEND_API_BASE_URL` | Yes (staging) | 17+ | Server-only URL of the FastAPI backend the proxy calls |
| `BACKEND_BASIC_AUTH` | Yes (staging) | 17+ | `user:pass` matching `STAGING_BASIC_AUTH`; server-only, never `NEXT_PUBLIC_` |
| `AUTH_SECRET` | **Yes** | 23+ | Random string signing the admin session cookie. `openssl rand -base64 32`. Key Vault ref in staging. Never commit. |
| `AUTH_TRUST_HOST` | Yes (staging) | 23+ | `true` behind the App Service reverse proxy so OAuth redirect URIs resolve to the public host |
| `AUTH_URL` | No | 23+ | Optional explicit external origin, e.g. `https://ib-stg-web.azurewebsites.net` |
| `ADMIN_ALLOWED_EMAILS` | **Yes** | 23+ | Comma-separated allowlist of admin emails. Empty = nobody authorized. |
| `AUTH_GITHUB_ID` | Yes (staging) | 23+ | GitHub OAuth App client ID |
| `AUTH_GITHUB_SECRET` | Yes (staging) | 23+ | GitHub OAuth App client secret — Key Vault ref; never commit |
| `AUTH_TEST_MODE` | No | 23+ | `true` enables deterministic `/api/auth/dev-login` for **local/CI only** — MUST stay unset in staging/prod |

> **Phase 23 — Admin authentication.** `/admin/*` and `/api/admin/proxy/*` are
> protected by an authenticated, allowlisted admin session (httpOnly
> HMAC-signed cookie). `BACKEND_BASIC_AUTH` is now server-to-server defense only
> — the browser authenticates against the admin session before the proxy ever
> attaches it. Configure a GitHub OAuth App with callback
> `<AUTH_URL>/api/auth/callback/github`. Store `AUTH_SECRET` and
> `AUTH_GITHUB_SECRET` in Key Vault; never set `AUTH_TEST_MODE` in staging/prod.

**Staging web env checklist (Azure App Service `ib-stg-web` → Configuration):**
`AUTH_SECRET`, `AUTH_TRUST_HOST=true`, `AUTH_URL=https://ib-stg-web.azurewebsites.net`,
`ADMIN_ALLOWED_EMAILS=<comma-separated>`, `AUTH_GITHUB_ID`, `AUTH_GITHUB_SECRET`,
`BACKEND_API_BASE_URL`, `BACKEND_BASIC_AUTH`. Then **restart** `ib-stg-web`.
Verify: logged-out `/admin` → `/login`; logged-out `GET /api/admin/proxy/health`
→ 401; `/` and `/api/version` stay public.

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
