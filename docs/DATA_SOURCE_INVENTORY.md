# Data Source Inventory

External information sources the agents use to evaluate a **segment** (thesis / theme) and a **company**. Generated 2026-09-04 from a read-only inspection of `main` at `4b60e07`, cross-checked against the live `ib-stg-api` app settings.

Companion spreadsheet: `DATA_SOURCE_INVENTORY.xlsx`.

## How to read this

- **Live fetch** — the platform makes a real network call to this source during a research run.
- **Reference only** — the connector is network-free at report time. It emits a bounded *source reference* (which official venue publishes what for this theme) plus an explicit, honest gap saying the live figures/records were not fetched. This is a deliberate design choice, not a missing feature: a reference is a research-priority pointer, never a fact, a catalyst, or a signal.
- **Scaffolded / Planned** — no evidence is produced at all; the source surfaces as a declared gap rather than silent absence.
- **Evaluates** — whether the source informs a company analysis, a segment/thesis scan, or both.

### Totals

| | Count |
|---|---|
| Registry sources (`GET /api/v1/sources/registry`) | 39 enabled + 2 scaffolded + 1 planned |
| Of those, live-fetching | 8 (SEC EDGAR, company IR, issuer documents, Nasdaq Nordic, eMarket Storage, Stooq, GDELT, GLEIF) |
| Reference-only | 28 (6 disclosure venues, 15 macro, 8 event, 1 local-language press) |
| AI / processing services | 3 (Azure OpenAI, Azure Document Intelligence, translation — the last is OFF) |
| Rows in this inventory | 54 |

## Category summary

| Category | Sources | What this category answers |
|---|---|---|
| 1. Issuer primary (T1) | 2 | What did the company itself say and publish? |
| 2. Regulator / filing venue (T2) | 10 | What has the company been legally required to disclose, and where? |
| 3. Market & price data (T5) | 3 | What has the market price done, and what are the headline fundamentals? |
| 4. News & media (T4/T5) | 4 | What is being reported about the company and its industry right now? |
| 5. Macro / commodity / policy reference (T2-T3) | 15 | What are the macro, commodity and policy conditions the segment sits in? |
| 6. Event-trigger reference venue (T2/T5) | 8 | Where would demand, innovation or regulatory activity show up for this theme? |
| 7. Identity & reference data (T2) | 3 | Which legal entity is this, and which companies are even in scope? |
| 8. AI / document-processing services | 3 | Who does the reasoning and reads the documents? |
| 9. Internal derivation (T6, no external call) | 3 | What does the platform derive itself — and label as derived? |
| 10. Planned / scaffolded (no evidence today) | 3 | What is declared but not yet delivering evidence? |

## Full inventory

### 1. Issuer primary (T1)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| Company IR / Newsroom press-release feeds | `Per-issuer RSS/Atom on the issuer's own domain (13-issuer verified allowlist, e.g. apple.com, pandoragroup.com, richemont.com)` | T1_primary_company_source | Company | The issuer's own announcements: results releases, trading statements, corporate actions. Primary catalyst signal and the highest-trust narrative evidence for a single company. | Live fetch (RSS/Atom parse, SSRF-guarded) | LIVE (SOURCE_CONNECTOR_ENABLED=true) | None |
| Issuer document hosts (annual / half-year report PDFs) | `document_domains per verified issuer + issuer CDNs (extension-less URLs supported)` | T1_primary_filing | Company | Downloads the issuer's own annual report / interim report / URD as PDF or HTML, then extracts period-scoped financial facts and tables that fill the report's canonical financial slots. | Live fetch + native text/table extraction, OCR fallback | LIVE (PRIMARY_DOCUMENT_INGESTION_ENABLED=true) | None |

<details><summary>Code paths</summary>

- **Company IR / Newsroom press-release feeds** — `apps/api/app/services/sources/connectors/company_ir.py; integrations/providers/company_press_release_provider.py; services/sources/verified_issuer_sources.py`
- **Issuer document hosts (annual / half-year report PDFs)** — `apps/api/app/services/sources/document_discovery.py; document_fetcher.py; primary_document_extractor.py; financial_table_reconstructor.py`

</details>

### 2. Regulator / filing venue (T2)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| SEC EDGAR — submissions | `https://data.sec.gov/submissions/CIK{cik}.json` | T2_regulator_or_gov (content T1) | Company | Recent filing stream (8-K, 10-Q, 10-K, 6-K, 20-F, DEF 14A, S-*). Drives the catalyst agent and supplies the company profile / SIC sector for US issuers. | Live REST (30 req/min, 0.2s min interval) | LIVE | None (User-Agent required) |
| SEC EDGAR — XBRL company facts | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` | T2_regulator_or_gov | Company | Normalized XBRL fundamentals — revenue, net income, EPS, assets, debt, cash, shares outstanding (DEI). The T2 backbone of the financial-data agent for US issuers. | Live REST | LIVE | None (User-Agent required) |
| SEC EDGAR — ticker index | `https://www.sec.gov/files/company_tickers.json` | T2_regulator_or_gov | Company | Ticker to CIK resolution. Gate for everything else on data.sec.gov; a missing CIK is recorded as an honest failure, never guessed. | Live REST (cached) | LIVE | None |
| SEC EDGAR — filing archives | `https://www.sec.gov/Archives/edgar/data/...` | T1_primary_filing | Company | The primary filing document itself (10-K/20-F HTML or PDF), fetched for full-text extraction of financial facts and segment/scope context. | Live fetch + extraction | LIVE | None |
| Nasdaq Nordic company news (regulated disclosures) | `https://api.news.eu.nasdaq.com/news/query.action (docs: view./attachment.news.eu.nasdaq.com)` | T2_regulator_or_gov | Company | BOUNDED LIVE regulated announcements for Copenhagen / Stockholm / Helsinki / Oslo issuers: publication timestamp, headline, venue category and the official document URL. Asserts no materiality or direction. | Live fetch (exact-host allowlist, lookback + item + byte + wall-clock caps) | LIVE (SOURCE_LIVE_DISCLOSURES_ENABLED=true) | None |
| eMarket Storage (CONSOB-authorised, Italy) | `https://www.emarketstorage.it/it/comunicati-finanziari` | T2_regulator_or_gov | Company | BOUNDED LIVE Italian regulated disclosures for issuers with a curated venue id (e.g. MONC). Both the Italian and English edition of an announcement are retained with their own URL and language. | Live fetch (exact-host allowlist, bounded) | LIVE (SOURCE_LIVE_DISCLOSURES_ENABLED=true) | None |
| UK FCA National Storage Mechanism | `FCA NSM / RNS venue landing page for a verified GB issuer` | T2_regulator_or_gov | Company | A T2 regulator-transport SOURCE REFERENCE to the issuer's UK disclosure venue (metadata only) plus an honest gap. Investigated for live retrieval and explicitly NOT bypassed: the NSM portal returns 403. | Reference only (network-free at report time) | LIVE as a reference; content not fetched | None |
| Euronext Regulated Information (FR / NL, AMF / AFM) | `Euronext regulated-disclosure venue landing page` | T2_regulator_or_gov | Company | T2 venue SOURCE REFERENCE for Euronext Paris / Amsterdam issuers. Live retrieval did not qualify (live.euronext.com renders news into a modal); French documents also need Phase 30 translation. | Reference only | LIVE as a reference; content not fetched | None |
| Deutsche Börse / Bundesanzeiger (BaFin, Germany) | `Deutsche Börse + Bundesanzeiger venue landing page` | T2_regulator_or_gov | Company | T2 venue SOURCE REFERENCE for Xetra / Frankfurt issuers. German documents require translation before ingestion. | Reference only | LIVE as a reference; content not fetched | None |
| SIX Swiss Exchange Regulatory Disclosures | `SIX Exchange Regulation venue landing page` | T2_regulator_or_gov | Company | T2 venue SOURCE REFERENCE for Swiss issuers. No public per-issuer disclosure API exists; Swiss ad-hoc announcements are reached through the issuer-primary path instead. | Reference only | LIVE as a reference; content not fetched | None |

<details><summary>Code paths</summary>

- **SEC EDGAR — submissions** — `apps/api/app/integrations/providers/sec_recent_filings_provider.py; sec_edgar_provider.py`
- **SEC EDGAR — XBRL company facts** — `apps/api/app/integrations/providers/sec_edgar_fundamentals.py; integrations/sec_fundamentals_normalizer.py`
- **SEC EDGAR — ticker index** — `apps/api/app/integrations/providers/sec_edgar_fundamentals.py; services/sources/sec_issuer_registry.py`
- **SEC EDGAR — filing archives** — `apps/api/app/services/sources/sec_filing_documents.py`
- **Nasdaq Nordic company news (regulated disclosures)** — `apps/api/app/services/sources/venue_disclosures.py; connectors/nordic_disclosures.py`
- **eMarket Storage (CONSOB-authorised, Italy)** — `apps/api/app/services/sources/venue_disclosures.py; connectors/borsa_italiana.py`
- **UK FCA National Storage Mechanism** — `apps/api/app/services/sources/connectors/uk_fca_nsm.py`
- **Euronext Regulated Information (FR / NL, AMF / AFM)** — `apps/api/app/services/sources/connectors/euronext_regulated_info.py`
- **Deutsche Börse / Bundesanzeiger (BaFin, Germany)** — `apps/api/app/services/sources/connectors/deutsche_boerse.py`
- **SIX Swiss Exchange Regulatory Disclosures** — `apps/api/app/services/sources/connectors/six_swiss.py`

</details>

### 3. Market & price data (T5)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| Stooq (price history) | `https://stooq.com/q/d/l/?s={symbol}&d1=&d2=&i=d` | T5_api_aggregator | Company | Free daily OHLCV history, no key. Default price leg of the `free_real` provider stack. Feeds latest close, the 52-week range, and the momentum/trend labels. | Live bulk CSV download | LIVE (default provider stack `free_real`) | None |
| EODHD (price / market data) | `https://eodhd.com/api (/eod endpoint)` | T5_api_aggregator | Company | Alternative price-history leg (`eodhd_price_only` stack) and, on a paid plan, fundamentals. Deliberately price-only in practice — no paid /fundamentals call is made. | Live REST | IDLE — configured code path, no key set in the live env | API key (EODHD_API_KEY) |
| Exchange venue profile pages (NYSE / Nasdaq) | `https://www.nyse.com/quote/{sym}, https://www.nasdaq.com/market-activity/stocks/{sym}` | T3_industry_specialist | Company | Query hints and candidate profile URLs only — the exchange page is never scraped. Exchange pages are NOT regulators and are never promoted to T1/T2. | Hint generation (no fetch) | LIVE (hints only) | None |

<details><summary>Code paths</summary>

- **Stooq (price history)** — `apps/api/app/integrations/providers/stooq_provider.py; integrations/providers/free_real_provider.py`
- **EODHD (price / market data)** — `apps/api/app/integrations/providers/eodhd_provider.py; eodhd_price_only_provider.py`
- **Exchange venue profile pages (NYSE / Nasdaq)** — `apps/api/app/integrations/exchange_source_registry.py`

</details>

### 4. News & media (T4/T5)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| GDELT 2.1 DOC API | `https://api.gdeltproject.org/api/v2/doc/doc` | T5_api_aggregator (article resolves to its outlet's tier) | Both | Keyless news search for a company AND for its industry. Results split into company-specific catalysts and industry-context items; industry news is forced to `macro_sector` and never treated as a direct company catalyst. | Live REST (bounded: max results, lookback days, timeout) | LIVE (NEWS_PROVIDER_NAME=gdelt) | None |
| Generic configured news / search API | `NEWS_API_BASE_URL (env-supplied JSON search endpoint)` | T5_api_aggregator | Both | Optional pluggable adapter for any keyed news API. Non-blocking by design — any failure yields an empty result, never an error. | Live REST | NOT CONFIGURED (GDELT is selected instead) | API key (NEWS_API_KEY) |
| Local-language business press (Les Échos, Handelsblatt, Milano Finanza, Børsen) | `lesechos.fr, handelsblatt.com, milanofinanza.it, borsen.dk` | T4_quality_media | Company | A bounded T4 venue SOURCE REFERENCE (with a genuine local-language description) for verified FR / DE / IT / DA issuers. Article content is NOT fetched; the reference is stamped needs-human-review and deliberately lowers source confidence. | Reference only | LIVE as a reference; content not fetched | None |
| Trusted-media tier map | `reuters.com, bloomberg.com, wsj.com, ft.com, cnbc.com, apnews.com, nytimes.com, marketwatch.com, barrons.com, forbes.com; businesswire/prnewswire/globenewswire -> T3` | T4_quality_media / T3 | Both | Not a fetch target — a host-to-tier resolver. Promotes an aggregator hit (T5) to T4 only when the resolved article host is a reputable outlet. Low-quality / stock-prediction domains are rejected outright. | Classification only (no fetch) | LIVE | None |

<details><summary>Code paths</summary>

- **GDELT 2.1 DOC API** — `apps/api/app/integrations/providers/free_news_provider.py (GdeltNewsProvider)`
- **Generic configured news / search API** — `apps/api/app/integrations/providers/free_news_provider.py (EnvConfiguredNewsProvider)`
- **Local-language business press (Les Échos, Handelsblatt, Milano Finanza, Børsen)** — `apps/api/app/services/sources/connectors/local_language_press.py`
- **Trusted-media tier map** — `apps/api/app/integrations/exchange_source_registry.py (TRUSTED_MEDIA_DOMAINS, LOW_QUALITY_DOMAIN_MARKERS)`

</details>

### 5. Macro / commodity / policy reference (T2-T3)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| FRED (Federal Reserve Bank of St. Louis) | `https://fred.stlouisfed.org/` | T2_regulator_or_gov | Segment | US and selected global macro series — CPI/PCE inflation, policy and market rates, FX, GDP, industrial production, employment. | Reference only (network-free at report time) | LIVE as a reference (SOURCE_MACRO_ENABLED=true) | None |
| IMF Data (World Economic Outlook) | `https://www.imf.org/en/Data` | T2_regulator_or_gov | Segment | Global macro aggregates — GDP growth, inflation, current-account and fiscal balances, commodity price indices. | Reference only | LIVE as a reference | None |
| Eurostat | `https://ec.europa.eu/eurostat` | T2_regulator_or_gov | Segment | EU macro and industry statistics — HICP inflation, GDP, industrial production, unemployment, external trade. | Reference only | LIVE as a reference | None |
| World Bank Commodity Markets (Pink Sheet) | `https://www.worldbank.org/en/research/commodity-markets` | T2_regulator_or_gov | Segment | Global commodity price benchmarks — energy (crude, gas, coal), metals and minerals (copper, aluminium, nickel, zinc, iron ore, lead, tin), agriculture. | Reference only | LIVE as a reference | None |
| National statistics offices / central banks | `https://www.bis.org/cbanks.htm` | T2_regulator_or_gov | Segment | Country-level CPI, GDP, industrial production, employment, policy rates and trade balances, via the BIS central-bank directory. | Reference only | LIVE as a reference | None |
| USGS Mineral Commodity Summaries | `https://www.usgs.gov/centers/national-minerals-information-center/mineral-commodity-summaries` | T3_industry_specialist | Segment | Mineral supply statistics — production, reserves and net import reliance for copper, lithium, cobalt, nickel, rare earths, uranium. | Reference only | LIVE as a reference | None |
| US EIA (Energy Information Administration) | `https://www.eia.gov/` | T2_regulator_or_gov | Segment | US and international energy statistics — crude, natural gas, coal, nuclear/uranium, electricity generation and power-sector data. | Reference only | LIVE as a reference | None |
| IEA (International Energy Agency) | `https://www.iea.org/` | T3_industry_specialist | Segment | Global energy statistics — electricity demand, generation mix, nuclear and renewables capacity, grids, energy-transition indicators. | Reference only | LIVE as a reference | None |
| IRENA (Renewable Energy) | `https://www.irena.org/` | T3_industry_specialist | Segment | Global renewable-energy capacity and generation trends — solar, wind, hydrogen, energy transition. | Reference only | LIVE as a reference | None |
| ENTSO-E Transparency Platform | `https://transparency.entsoe.eu/` | T3_industry_specialist | Segment | European electricity transmission statistics — generation, cross-border flows, load, grid transparency. | Reference only | LIVE as a reference | None |
| USTR / EU TARIC (tariffs) | `https://ustr.gov/` | T2_regulator_or_gov | Segment | US and EU tariff / trade-policy references — USTR tariff actions and the EU TARIC schedule (duties, classifications, import-export measures). | Reference only | LIVE as a reference | None |
| UN Comtrade | `https://comtrade.un.org/` | T2_regulator_or_gov | Segment | UN merchandise-trade statistics — reported flows by reporter, partner and commodity classification. | Reference only | LIVE as a reference | None |
| NATO defence expenditure | `https://www.nato.int/cps/en/natohq/topics_49198.htm` | T2_regulator_or_gov | Segment | NATO member defence spending and burden-sharing — spend as a share of GDP and the major-equipment share. | Reference only | LIVE as a reference | None |
| SIPRI Military Expenditure Database | `https://www.sipri.org/databases/milex` | T3_industry_specialist | Segment | National and regional military spending series plus arms-industry / arms-transfer indicators. | Reference only | LIVE as a reference | None |
| OECD (industrial policy & trade) | `https://www.oecd.org/` | T2_regulator_or_gov | Segment | Industrial policy, subsidies and state aid, trade/tariff analysis, energy-transition and grid-investment indicators. | Reference only | LIVE as a reference | None |

<details><summary>Code paths</summary>

- **FRED (Federal Reserve Bank of St. Louis)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **IMF Data (World Economic Outlook)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **Eurostat** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **World Bank Commodity Markets (Pink Sheet)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **National statistics offices / central banks** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **USGS Mineral Commodity Summaries** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **US EIA (Energy Information Administration)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **IEA (International Energy Agency)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **IRENA (Renewable Energy)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **ENTSO-E Transparency Platform** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **USTR / EU TARIC (tariffs)** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **UN Comtrade** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **NATO defence expenditure** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **SIPRI Military Expenditure Database** — `apps/api/app/services/sources/connectors/macro_reference.py`
- **OECD (industrial policy & trade)** — `apps/api/app/services/sources/connectors/macro_reference.py`

</details>

### 6. Event-trigger reference venue (T2/T5)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| EU TED (Tenders Electronic Daily) | `https://ted.europa.eu/` | T2_regulator_or_gov | Segment | Which EU/EEA procurement and contract-award notices a theme has — defence, infrastructure, rail, energy, grid, construction. A WEAK research-priority signal; never a specific award, contractor or amount. | Reference only (network-free at report time) | LIVE as a reference (SOURCE_EVENT_ENABLED=true) | None |
| USAspending.gov | `https://www.usaspending.gov/` | T2_regulator_or_gov | Segment | US federal award and contract data by theme — defence, infrastructure, energy, grid, rail, grants. Weak signal only; no contract number or amount is emitted. | Reference only | LIVE as a reference | None |
| Google Patents | `https://patents.google.com/` | T5_api_aggregator | Segment | Aggregated worldwide index of patent applications and grants for an innovation / R&D theme. No patent number, assignee, or legal / validity conclusion is ever emitted. | Reference only | LIVE as a reference | None |
| USPTO (PatentsView) | `https://www.uspto.gov/` | T2_regulator_or_gov | Segment | US patent applications and grants for an innovation / R&D theme. The keyed PatentsView API is deliberately NOT used. | Reference only | LIVE as a reference | None |
| EPO Espacenet | `https://worldwide.espacenet.com/` | T2_regulator_or_gov | Segment | European and worldwide patent filings for an innovation / R&D theme. The keyed EPO OPS API is deliberately NOT used. | Reference only | LIVE as a reference | None |
| FERC (Federal Energy Regulatory Commission) | `https://www.ferc.gov/` | T2_regulator_or_gov | Segment | Which US energy / grid / transmission / pipeline / LNG docket categories a theme has. No docket, applicant, or regulatory-outcome conclusion is emitted. | Reference only | LIVE as a reference | None |
| US NRC (Nuclear Regulatory Commission) | `https://www.nrc.gov/` | T2_regulator_or_gov | Segment | Which US nuclear reactor licensing / permit docket categories a nuclear-power theme has. No approval conclusion is drawn. | Reference only | LIVE as a reference | None |
| US EPA (Environmental Protection Agency) | `https://www.epa.gov/` | T2_regulator_or_gov | Segment | Which US environmental / emissions / industrial permitting programmes an environmental theme has. No permit outcome is asserted. | Reference only | LIVE as a reference | None |

<details><summary>Code paths</summary>

- **EU TED (Tenders Electronic Daily)** — `apps/api/app/services/sources/connectors/event_reference.py`
- **USAspending.gov** — `apps/api/app/services/sources/connectors/event_reference.py`
- **Google Patents** — `apps/api/app/services/sources/connectors/event_reference.py`
- **USPTO (PatentsView)** — `apps/api/app/services/sources/connectors/event_reference.py`
- **EPO Espacenet** — `apps/api/app/services/sources/connectors/event_reference.py`
- **FERC (Federal Energy Regulatory Commission)** — `apps/api/app/services/sources/connectors/event_reference.py`
- **US NRC (Nuclear Regulatory Commission)** — `apps/api/app/services/sources/connectors/event_reference.py`
- **US EPA (Environmental Protection Agency)** — `apps/api/app/services/sources/connectors/event_reference.py`

</details>

### 7. Identity & reference data (T2)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| GLEIF (Legal Entity Identifier) | `https://api.gleif.org/api/v1/lei-records` | T2_regulator_or_gov | Company | Legal-entity identity enrichment: LEI lookup by legal name or LEI, name-guarded so a near-match never overwrites the issuer. Best-effort and non-fatal. | Live REST (60 req/min) | LIVE | None |
| Theme company registry (segment universe bootstrap) | `In-repo curated table of real listed issuers by theme` | T3 (curated reference) | Segment | The bounded starting universe for a thesis-driven segment scan: real, publicly-listed issuers grouped by research theme. Hard-capped at 25 (absolute max 50) — never an uncontrolled full-market scan. | In-repo data (no network, no LLM) | LIVE | None |
| Verified issuer source registry | `In-repo allowlist of 13 issuers: official domain, IR URL, annual-reports URL, press-release URL, document domains` | T1/T2 (curated allowlist) | Company | The fetch AUTHORITY for issuer-primary retrieval. Nothing on an issuer's domain is fetched unless the host is on this allowlist — it is what makes the issuer path SSRF-safe and non-fabricated. | In-repo data (no network) | LIVE | None |

<details><summary>Code paths</summary>

- **GLEIF (Legal Entity Identifier)** — `apps/api/app/integrations/providers/gleif_provider.py; integrations/company_profile_enrichment.py`
- **Theme company registry (segment universe bootstrap)** — `apps/api/app/services/market_universe_builder.py (THEME_COMPANY_REGISTRY)`
- **Verified issuer source registry** — `apps/api/app/services/sources/verified_issuer_sources.py`

</details>

### 8. AI / document-processing services

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| Azure OpenAI (gpt-4.1-mini) | `https://ib-stg-openai-d52d2.openai.azure.com/ (API version 2025-01-01-preview)` | n/a — reasoning runtime, not an evidence source | Both | Runs the three councils: the single-company analysis council (bull/bear/risk/valuation-guard/chair), the discovery council that buckets a segment's candidates by research priority, and the Deep Field Review comparative council. Every output is citation-bound and safety-gated; the model never supplies a financial fact. | Live REST via langchain-openai, token-paced at 60,000 TPM | LIVE (LLM_COUNCIL_ENABLED / LLM_DISCOVERY_COUNCIL_ENABLED / LLM_FIELD_REVIEW_COUNCIL_ENABLED = true) | API key (AZURE_OPENAI_API_KEY) |
| Azure AI Document Intelligence (OCR) | `https://ib-stg-docintel.cognitiveservices.azure.com/` | n/a — extraction service | Company | OCR fallback for scanned, image-only primary documents where native PDF text extraction returns nothing. Bounded page selection, decompression-bomb guard, hard timeouts. Never fabricates text — degrades to an honest `ocr_unavailable`. | Live REST (fixed endpoint, no caller-supplied URL) | LIVE (PRIMARY_DOCUMENT_OCR_ENABLED=true; F0 tier, ~3.5MB upload limit) | API key (AZURE_DOCUMENT_INTELLIGENCE_API_KEY) |
| Translation provider | `Azure OpenAI (when translation_provider='llm')` | n/a | Company | Machine-assisted translation of local-language (FR / DE / IT / DA) primary documents. Bounded excerpt-level only — a whole filing is never sent. | Live REST when enabled | OFF (SOURCE_TRANSLATION_ENABLED not set; provider defaults to `fake`) | Reuses the Azure OpenAI key |

<details><summary>Code paths</summary>

- **Azure OpenAI (gpt-4.1-mini)** — `apps/api/app/services/llm/azure_openai_client.py; council.py; discovery_council.py; field_review_council.py; token_pacer.py`
- **Azure AI Document Intelligence (OCR)** — `apps/api/app/services/sources/ocr_provider.py`
- **Translation provider** — `apps/api/app/services/sources/translation.py`

</details>

### 9. Internal derivation (T6, no external call)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| TrendSignalEngine | `Internal — computed from Stooq / EODHD price history` | T6_model_estimate | Company | Momentum and trend labels derived from price history. Explicitly a model estimate, never a sourced fact and never a BUY/SELL/HOLD signal. | Pure computation | LIVE | None |
| Market metrics enrichment | `Internal — SEC DEI shares x latest close` | T6_model_estimate | Company | Derived market cap, enterprise value and P/E, each labelled a DERIVED ESTIMATE with its cited inputs. EBITDA, EV/EBITDA and beta are deliberately NOT derived and stay missing. | Pure computation (no network) | LIVE | None |
| Discovery scoring engine | `Internal — momentum + catalyst + fundamentals + source quality + completeness - risk` | T6_model_estimate | Segment | Ranks segment candidates for internal research prioritisation only. Aggregator-only evidence is down-weighted and never promoted. Never a recommendation. | Pure computation | LIVE | None |

<details><summary>Code paths</summary>

- **TrendSignalEngine** — `apps/api/app/integrations/trend_signal_engine.py`
- **Market metrics enrichment** — `apps/api/app/integrations/market_metrics_enrichment.py`
- **Discovery scoring engine** — `apps/api/app/services/discovery_scoring_service.py; scoring_engine.py`

</details>

### 10. Planned / scaffolded (no evidence today)

| Source | Endpoint / host | Tier | Evaluates | What it is used for | Retrieval mode | Status in live env | Credentials |
|---|---|---|---|---|---|---|---|
| SEDAR+ (Canada) | `Canadian issuer filing venue` | T2_regulator_or_gov | Company | SCAFFOLDED. Connector class exists and is wired but returns an honest gap only — never a fabricated filing. Live fetch is a Phase 29B follow-up. | None (honest gap) | SCAFFOLDED — produces no evidence | None |
| ASX Announcements (Australia) | `ASX company announcements` | T2_regulator_or_gov | Company | SCAFFOLDED. Emits an honest gap; no fabricated JORC / Appendix 5B data. | None (honest gap) | SCAFFOLDED — produces no evidence | None |
| OpenBB Platform | `OpenBB SDK` | T5_api_aggregator | Both | PLANNED. Aggregator toolkit placeholder for broader symbol search and macro pulls. No implementation; surfaces as a source gap rather than silent absence. | None | PLANNED — not implemented | Freemium key when built |

<details><summary>Code paths</summary>

- **SEDAR+ (Canada)** — `apps/api/app/services/sources/connectors/scaffolds.py`
- **ASX Announcements (Australia)** — `apps/api/app/services/sources/connectors/scaffolds.py`
- **OpenBB Platform** — `apps/api/app/integrations/providers/openbb_provider.py; services/sources/registry.py`

</details>

## Source tier legend

Every piece of evidence keeps its real tier end to end. An aggregator hit is never promoted to a primary source, and a model-derived value is never presented as a sourced fact.

| Code | Rank | Label | Description |
|---|---|---|---|
| `T1_primary_filing` | 1 | Primary filing | The company's own regulatory filing content (10-K, 20-F, annual report). Highest-trust factual evidence. |
| `T1_primary_company_source` | 1 | Primary company source | The issuer's own non-filing primary material (IR page, press release). Ranks alongside a primary filing. |
| `T2_regulator_or_gov` | 2 | Regulator or government | A regulator or government body as the transport or publisher (SEC EDGAR, GLEIF, a statistics office). |
| `T3_industry_specialist` | 3 | Industry specialist | A specialist agency or standards body for a domain (USGS, IEA, ENTSO-E). Exchange venue pages also map here. |
| `T4_quality_media` | 4 | Quality media | Reputable, editorially-accountable media coverage. |
| `T5_api_aggregator` | 5 | API aggregator | A data aggregator/API that repackages an upstream source (EODHD, Stooq, GDELT). Down-weighted unless the underlying source is known. |
| `T6_model_estimate` | 6 | Model estimate | A value derived by a model or heuristic. Never a primary fact; must carry its derivation method. |

## Live configuration (`ib-stg-api`, read 2026-09-04)

Which of the above is actually switched on. Secrets are never listed here — only flag names and non-secret values.

| Setting | Value | Effect |
|---|---|---|
| `SOURCE_CONNECTOR_ENABLED` | `true` | Master gate for the connector framework and live issuer/SEC fetching. |
| `SOURCE_LIVE_DISCLOSURES_ENABLED` | `true` | Enables BOUNDED LIVE retrieval from Nasdaq Nordic and eMarket Storage. |
| `SOURCE_MACRO_ENABLED` | `true` | Enables the 15 reference-only macro / commodity / policy sources. |
| `SOURCE_EVENT_ENABLED` | `true` | Enables the 8 reference-only procurement / patent / permit venues. |
| `SOURCE_DOCUMENT_EXTRACTION_ENABLED` | `true` | Enables native HTML/PDF text and table extraction from fetched documents. |
| `PRIMARY_DOCUMENT_INGESTION_ENABLED` | `true` | Enables fetching the issuer's own annual / interim report documents. |
| `PRIMARY_DOCUMENT_OCR_ENABLED` | `true` | Enables the Azure Document Intelligence OCR fallback for scanned PDFs. |
| `SOURCE_TRANSLATION_ENABLED` | `(unset -> false)` | Local-language translation stays OFF; TRANSLATION_PROVIDER defaults to `fake`. |
| `LLM_COUNCIL_ENABLED` | `true` | Single-company analysis council. |
| `LLM_DISCOVERY_COUNCIL_ENABLED` | `true` | Segment / discovery-run council. |
| `LLM_FIELD_REVIEW_COUNCIL_ENABLED` | `true` | Deep Field Review comparative council. |
| `LLM_PROVIDER_COUNCIL` | `azure_openai` | Council backend. `fake` is the only backend used in tests. |
| `LLM_COUNCIL_TPM_CAPACITY` | `60000` | Token-pacer capacity for the Azure OpenAI deployment. |
| `NEWS_PROVIDER_NAME` | `gdelt` | Keyless GDELT news adapter is the active news provider. |
| `FINANCIAL_DATA_PROVIDER` | `mock` | Legacy Phase-4 selector. NOT the path real research uses. |
| `DISCOVERY_DEFAULT_PROVIDER` | `(unset -> free_real)` | The provider stack real research actually uses: Stooq prices + SEC EDGAR fundamentals. |
| `LLM_PROVIDER` | `mock` | Legacy Phase-7 research-sections client. The councils use LLM_PROVIDER_COUNCIL instead. |

> **Note on `FINANCIAL_DATA_PROVIDER=mock` and `LLM_PROVIDER=mock`.** Both are legacy (Phase 4 / Phase 7) selectors and are *not* the path a real research run takes. Real runs use `DISCOVERY_DEFAULT_PROVIDER=free_real` (Stooq + SEC EDGAR) for data and `LLM_PROVIDER_COUNCIL=azure_openai` for reasoning. Reading the two `mock` values as "the platform runs on mock data" is the single easiest mistake to make here.

## Segment vs. company: which sources do what

**Evaluating a segment / thesis** — `market_universe_builder` bounds the universe from the curated theme registry (max 25 issuers), `free_real` pulls Stooq prices and SEC fundamentals per ticker, GDELT supplies industry-context news, the 15 macro and 8 event references frame the conditions and point at where activity would show up, `discovery_scoring_service` ranks candidates deterministically, and the Azure OpenAI discovery council buckets them by research priority (research_next / monitor_for_evidence / insufficient_data / reject_for_now). The council emits buckets, not a ranking.

**Evaluating a company** — the verified-issuer allowlist authorises fetching the issuer's own IR feed and report documents (T1); SEC EDGAR supplies filings and XBRL fundamentals (T2); the regulated-disclosure venues add announcements (live for Nordic/Italian issuers, reference-only elsewhere); Stooq/EODHD supply price history (T5); GLEIF resolves legal identity; GDELT adds company news; Document Intelligence OCRs scanned PDFs; and the Azure OpenAI analysis council writes the bull case, bear case, risk analysis, valuation guard and chair synthesis — every claim citation-bound to one of the above.

## Guarantees that constrain every row

- No financial number is ever invented. Every financial claim carries a source, date, currency and retrieval timestamp; an unavailable figure is recorded as an honest gap.
- No source-terms bypass. Three venues (SIX Swiss, Euronext Paris, LSE/FCA NSM) were investigated for live retrieval and did not qualify; the 403 and the proof-of-work challenge are **not** worked around — they are recorded as limitations.
- No SSRF surface. No user-supplied URL is ever fetched. SEC uses fixed hosts keyed by ticker/CIK, issuer fetching is bound to the verified-issuer allowlist, and live venue retrieval uses exact-host allowlists.
- No secrets in the registry. `assert_registry_safe` serialises the whole registry payload and fails loudly on anything that looks like a credential.
- Every reference-only source produces a declared gap rather than silence, so a missing source is visible in the report instead of being indistinguishable from an absent one.

