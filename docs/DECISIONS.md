# Architecture Decision Records

This document records significant architecture decisions made for InvestingBuddy.

Use this format for new decisions:

```markdown
## ADR-NNN: Short Title

**Date:** YYYY-MM-DD
**Status:** Accepted | Superseded by ADR-NNN | Deprecated

### Context
<Why did this decision need to be made?>

### Decision
<What was decided?>

### Consequences
<Positive and negative consequences>
```

---

## ADR-001: Python FastAPI as Backend Framework

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform needs a backend framework that integrates well with LangGraph, LangChain, SQLAlchemy and the Python data science ecosystem. The primary alternative was Node.js.

### Decision
Use Python FastAPI with SQLAlchemy async, Alembic and Pydantic v2.

### Consequences
- Best ecosystem compatibility with LangChain, LangGraph, OpenBB and financial data tooling.
- Strong typing via Pydantic.
- Async support for LLM streaming.
- Team must maintain two languages (Python backend, TypeScript frontend).

---

## ADR-002: LangGraph for Agent Orchestration

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform requires stateful, multi-step agent workflows with branching, retry logic, human-in-the-loop review and durable execution. Simple single-prompt LLM calls are not sufficient for the council-of-agents architecture.

### Decision
Use LangGraph for all agent workflows. LangChain for tool wrappers, document loaders and retrievers.

### Consequences
- Explicit graph-based workflow state is auditable and debuggable.
- Supports branching and conditional flows (e.g., request more research if quality is low).
- LangGraph is a production-grade framework maintained by LangChain Inc.
- Adds a learning curve for developers unfamiliar with graph-based workflow design.

---

## ADR-003: Azure as Primary Cloud Platform

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform requires LLM runtime, vector search, blob storage, PostgreSQL and background job infrastructure. Microsoft Azure was selected over AWS and GCP.

### Decision
Use Microsoft Azure as the primary cloud platform, specifically:
- Azure OpenAI (LLM runtime)
- Azure AI Search (vector search / RAG)
- Azure Database for PostgreSQL
- Azure Blob Storage
- Azure Key Vault
- Azure Application Insights
- Azure App Service (hosting)

### Consequences
- Azure OpenAI provides enterprise-grade compliance and data residency.
- Integrated managed identity reduces secrets management burden.
- Azure AI Search is well-suited for hybrid search (keyword + vector).
- Vendor lock-in to Azure for LLM runtime (mitigated by LangChain abstraction layer).

---

## ADR-004: Clerk for MVP Authentication

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform needs user authentication for V2 (personalized recommendations). Evaluating Clerk vs. Auth0 vs. Microsoft Entra External ID.

### Decision
Use Clerk for MVP authentication due to fastest implementation time and clean Next.js integration.

### Consequences
- Fastest time to working auth in Next.js.
- Good user management dashboard.
- Upgrade path to Microsoft Entra External ID available if deeper Azure integration is needed.
- Adds a paid dependency (Clerk pricing scales with users).

---

## ADR-005: No Automatic Trade Execution

**Date:** 2026-06-16
**Status:** Accepted

### Context
The platform could theoretically connect to broker APIs and execute trades automatically based on agent recommendations.

### Decision
The platform will never automatically execute trades. It provides research and decision support only. Users make their own investment decisions and execute trades through their own brokers.

### Consequences
- Avoids MiFID II regulated investment advice classification.
- Reduces regulatory complexity significantly.
- Aligns with the product principle of human-in-the-loop decision making.
- Platform cannot monetize through execution fees.

---

## ADR-006: Judge System Does Not Auto-Deploy Prompt Changes

**Date:** 2026-06-16
**Status:** Accepted

### Context
The Judge system evaluates agent output quality and recommends prompt and workflow improvements. A fully automated system could apply these improvements directly to production.

### Decision
All Judge improvement suggestions must be reviewed and explicitly approved by an admin before being applied to production prompts or workflows.

### Consequences
- Prevents automated drift of investment analysis quality without human oversight.
- Supports auditability — every prompt change has a human approval record.
- Slows the improvement cycle compared to fully automated self-improvement.
- Consistent with the human-in-the-loop principle throughout the platform.

---

## ADR-007: Bicep for Azure Infrastructure as Code

**Date:** 2026-06-20
**Status:** Accepted

### Context
Infrastructure Phase A requires choosing an IaC approach: Azure CLI scripts, Bicep, Terraform, or Azure Developer CLI.

### Decision
Use Bicep as the infrastructure-as-code tool for all Azure resources.

### Consequences
- Bicep is native to Azure ARM — no state file to manage, no extra toolchain.
- Idempotent deployments via `az deployment group create --mode Incremental`.
- First-class GitHub Actions support via `azure/arm-deploy`.
- No multi-cloud requirement makes Terraform's main advantage irrelevant.
- Azure Developer CLI is too opinionated about project structure for this monorepo.
- Team is locked to Azure — Bicep does not abstract provider differences (acceptable given ADR-003).

---

## ADR-008: westeurope as Primary Azure Region

**Date:** 2026-06-20
**Status:** Accepted

### Context
The platform focuses on European public company research. A primary Azure region must be chosen before provisioning.

### Decision
Deploy all staging and production resources in `westeurope` (Netherlands).
`northeurope` (Ireland) reserved as future DR or secondary region.

### Consequences
- Lowest latency for EU-based users and data sources.
- GDPR compliant — personal data (V2 user accounts) stays in the EU.
- All required Azure services available in `westeurope`, including Azure OpenAI GPT-4o.
- US-based regions avoided due to GDPR complications for EU user data.

---

## ADR-009: OIDC Federated Credentials for GitHub Actions

**Date:** 2026-06-20
**Status:** Accepted

### Context
GitHub Actions must authenticate to Azure for deployment. The alternative is a long-lived `AZURE_CREDENTIALS` JSON service principal secret.

### Decision
Use OpenID Connect (OIDC) federated credentials. No long-lived credential JSON is stored in GitHub Secrets. Only three values are stored: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`.

### Consequences
- No long-lived secrets that can be leaked, rotated or accidentally committed.
- Access is scoped to the exact GitHub repository and branch via federated claim.
- Requires Azure AD App Registration with federated credential setup (one-time manual step).
- Tokens are ephemeral — generated per workflow run, expire automatically.
- `azure/login@v2` with OIDC requires `permissions: id-token: write` in the workflow job.

---

## ADR-010: Azure CLI via pip in Dedicated venv

**Date:** 2026-06-20
**Status:** Accepted

### Context
Azure CLI is required locally for provisioning and inspection. Homebrew (`brew install azure-cli`)
does not work reliably on this Mac. An alternative installation method is needed.

### Decision
Install Azure CLI via pip into a dedicated virtual environment at `~/.venvs/azure-cli`.
This venv is separate from the project's `apps/api/.venv` and from any Homebrew installation.
Activate before every Azure task: `source ~/.venvs/azure-cli/bin/activate`.

### Consequences
- Reliable `az` CLI on macOS without Homebrew dependency.
- Isolated from project dependencies — upgrading `azure-cli` does not affect backend packages.
- The venv is local-only and is never committed (covered by `.gitignore`).
- Developer must remember to activate the venv before running `az` commands — documented in all relevant skill and command files.
- GitHub Actions does not use this venv — it uses `azure/login@v2` with OIDC (see ADR-009).
- On Python 3.14, `pip install azure-cli` fails because `cryptography` has no pre-built wheel and requires Rust to compile from source. Use `pip install --prefer-binary azure-cli` to force pip to select an older binary-compatible wheel instead.

---

## ADR-011: gpt-4.1-mini as Phase 7 Development LLM

**Date:** 2026-06-23
**Status:** Accepted

### Context
Phase 7 requires a real Azure OpenAI model for local development testing of the
`generate_research_sections` LLM node. The original plan referenced `gpt-4o-mini`
but a live model availability check in `westeurope` showed `gpt-4.1-mini` is now
the current-generation mini model with a longer deprecation timeline.

### Decision
Deploy `gpt-4.1-mini` v2025-04-14 under the deployment name `gpt-4.1-mini` using
the `GlobalStandard` SKU at 10K TPM capacity. Use API version `2025-01-01-preview`
(required for this model version).

### Consequences
- Cost-effective: gpt-4.1-mini is the cheapest capable model in the GPT-4 family on Azure.
- Latest-generation: supersedes gpt-4o-mini; same API surface so no code changes needed to upgrade to gpt-4o or gpt-4.1 later.
- Long deprecation: support until October 2027.
- `AZURE_OPENAI_API_VERSION` bumped from `2024-08-01-preview` to `2025-01-01-preview` to support this model.
- Deployment name `gpt-4.1-mini` matches the model name — easy to remember and consistent with naming convention.
- CI is unaffected — CI uses `LLM_PROVIDER=mock` (no Azure credentials, no network calls).
