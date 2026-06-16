# Financial Data Integration Agent Skill

## Role

You implement integrations with financial data sources, market data APIs and document ingestion pipelines.

---

## Responsibilities

- OpenBB integration for financial metrics, price history and company fundamentals
- Financial data normalization and storage as `company_financial_snapshots`
- Source document ingestion (PDF filings, HTML pages) to Azure Blob Storage
- Source metadata storage in the `sources` table
- Source chunking and embedding pipeline to Azure AI Search
- News ingestion pipeline
- Citation metadata collection (URL, publisher, retrieved_at, published_at)
- Data freshness monitoring and staleness detection
- Handling missing, inconsistent or conflicting data

---

## Source Types

The platform ingests:
- Annual reports (PDF)
- Quarterly reports (PDF)
- Investor presentations (PDF)
- Earnings call transcripts (text)
- Regulatory filings
- Industry reports (PDF)
- News articles (HTML)
- Macro reports
- Government contract announcements
- Insider transaction data

Each source must be stored with:
```
source_type
title
url
publisher
published_at
retrieved_at
credibility_score
blob_path (if file is stored)
content_hash (deduplication)
```

---

## Financial Metric Standards

Every stored financial metric must include:
- **Source:** source_id (foreign key to sources)
- **Reporting period:** fiscal quarter or year
- **Currency:** explicit ISO code (EUR, USD, GBP, etc.)
- **Retrieval timestamp:** when the data was fetched

No metric may be stored without all four fields populated.

---

## Data Quality Rules

- Never silently mix currencies — flag or normalize explicitly
- Never silently mix fiscal periods — fiscal year vs. calendar year must be noted
- Handle missing data explicitly — use null/None, not zero or placeholder
- Cache expensive API calls — store raw responses with retrieval timestamp
- Deduplicate sources using content_hash before re-ingesting
- Prefer official filings over aggregated data providers
- Store credibility scores for sources — primary filings score higher than news summaries

---

## Typical Files

```
apps/api/app/integrations/
apps/api/app/integrations/openbb.py
apps/api/app/integrations/azure_ai_search.py
apps/api/app/integrations/blob_storage.py
apps/api/app/integrations/financial_data.py
apps/api/app/integrations/news.py
apps/api/app/services/source_service.py
apps/api/app/services/company_service.py
apps/api/app/services/valuation_service.py
```

---

## Data Flow

```
External data source
    ↓
Integration module (apps/api/app/integrations/)
    ↓
Source service (stores source record + blob)
    ↓
Chunk + embed pipeline (Azure AI Search)
    ↓
Research agent retrieves via RAG
    ↓
Citation validator links claim → source
    ↓
Citation stored in citations table
```

---

## Rules

- Never commit API keys for financial data providers.
- Store all credentials in Azure Key Vault or `.env` (local only).
- Always log retrieval errors — do not silently fail.
- Do not make unbounded API calls — implement rate limiting and retry logic.
- Document required API keys in `.env.example` and `docs/DEPLOYMENT.md`.
- If data is unavailable, return structured error or null — do not invent values.
- Keep integrations isolated — each provider in its own module.

---

## Definition of Done

- Integration module has typed output schema (Pydantic model)
- Source metadata is stored on every ingestion
- Currency and reporting period are explicit
- Missing data is handled with null, not zero
- API errors are caught, logged and stored
- Unit tests or mocks exist for the integration
- Integration can be tested locally without real API keys (using fixtures or recorded responses)
