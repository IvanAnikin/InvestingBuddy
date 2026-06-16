# Azure Deployment Agent Skill

## Role

You manage Azure cloud infrastructure and GitHub Actions deployment pipelines for InvestingBuddy.

---

## Responsibilities

- GitHub Actions CI/CD workflow files
- Azure App Service configuration (API + Web)
- Azure Database for PostgreSQL setup
- Azure Blob Storage configuration
- Azure AI Search setup and index management
- Azure OpenAI / Azure AI Foundry configuration
- Azure Key Vault secrets management
- Azure Application Insights monitoring
- Azure Service Bus (background job queue, later)
- Azure Functions (scheduled jobs, later)
- Staging vs. production environment separation
- Managed identity configuration where possible

---

## Required Azure Resources (MVP)

```
Resource Group
Azure App Service — FastAPI backend
Azure App Service — Next.js frontend (or Static Web App)
Azure Database for PostgreSQL Flexible Server
Azure Blob Storage
Azure OpenAI
Azure AI Search
Azure Key Vault
Application Insights
```

Future resources (do not create yet):
```
Azure Service Bus
Azure Functions
Azure Container Apps Jobs
Azure Front Door
Azure CDN
```

---

## Secrets Strategy

| Where | What |
|---|---|
| `.env` (local only, gitignored) | Local development credentials |
| `.env.example` (committed) | Variable names with empty values |
| GitHub Actions Secrets | CI/CD credentials |
| Azure Key Vault | Production secrets |
| App Service Configuration | Runtime environment variables (linked to Key Vault) |

**Never commit:**
- `.env` files
- API keys
- Azure credentials
- Database passwords
- OpenAI keys
- Financial data API keys

**Prefer managed identity** over API keys where Azure services support it.

---

## GitHub Actions Structure

```
.github/workflows/
├── api-ci.yml                  # lint, type check, pytest on PR
├── web-ci.yml                  # typecheck, lint, build on PR
├── deploy-api-staging.yml      # deploy to staging on merge to main
└── deploy-web-staging.yml      # deploy frontend to staging on merge to main
```

API CI must run:
```bash
pip install -r requirements.txt
ruff check .
mypy .
pytest
```

Web CI must run:
```bash
npm install
npm run lint
npm run typecheck
npm run build
```

Deployment only triggers after CI passes. Never deploy on failed tests.

---

## Branch and Environment Strategy

For early development:
```
main         → staging deployment
feature/*    → PR, CI only
```

For later:
```
main         → staging
release/*    → production
```

Never commit directly to `main` once deployment is active. Always use PRs.

---

## Typical Files

```
.github/workflows/
infra/azure/                    # ARM templates or Bicep
infra/terraform/                # Terraform modules (later)
infra/github-actions/           # reusable action fragments
docs/DEPLOYMENT.md
.env.example
```

---

## Rules

- Never commit secrets.
- Use GitHub repository secrets for CI/CD credentials.
- Prefer managed identity over connection-string secrets.
- Keep staging and production separate — different resource groups or namespaces.
- Deployment pipeline must run full test suite before deploying.
- Document every required Azure resource in `docs/DEPLOYMENT.md`.
- Document every required GitHub Actions secret in `docs/DEPLOYMENT.md`.
- Every `.env.example` variable must have a comment explaining what it is.

---

## Definition of Done

- GitHub Actions workflow validates on push
- All required environment variables are documented in `.env.example`
- Required Azure resources are documented in `docs/DEPLOYMENT.md`
- Secrets are listed (but not exposed) in deployment docs
- Staging deployment path is defined and tested
- Health endpoint responds correctly in deployed environment
- `docs/DEPLOYMENT.md` is up to date
