from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "InvestingBuddy API"
    app_env: str = "development"
    debug: bool = False

    # ── Observability / Logging (Phase 27.1D) ──────────────────────────────
    # Root log level for the app's stdout handler. INFO surfaces the structured
    # telemetry events (http_request, discovery_run_*, report_validation) in the
    # staging container log stream. Set to "WARNING" to reduce verbosity later.
    log_level: str = "INFO"
    # Emit one structured line per HTTP request (method/path/status/duration).
    # Never logs headers, bodies, query strings, or secrets. Toggle off to
    # silence per-request logging without touching code.
    request_logging_enabled: bool = True

    database_url: str = (
        "postgresql+psycopg://investingbuddy:investingbuddy@localhost:5432/investingbuddy"
    )

    # ── Financial Data Provider (Phase 4) ──────────────────────────────────
    # Which provider to use: "mock" | "eodhd" | "sec_edgar" | "stooq" | "openbb" | "gleif"
    # Default is "mock" so CI tests run with no external calls or credentials.
    financial_data_provider: str = "mock"

    # EODHD credentials — required only when financial_data_provider="eodhd".
    # Never hardcode. Load from Azure Key Vault in production.
    eodhd_api_key: str = ""
    eodhd_base_url: str = "https://eodhd.com/api"

    # ── Integration Tests (Phase 5) ─────────────────────────────────────────
    # Set to True to enable live network calls in tests (local only).
    # NEVER set to True in CI — CI must always run offline with mock provider.
    enable_integration_tests: bool = False

    # ── Staging Access Control (Phase 12) ─────────────────────────────────
    # When APP_ENV=staging, set this to "username:password" to enable HTTP
    # Basic Auth on all routes (except /health). Leave empty to disable.
    # Store value in Key Vault as 'staging-basic-auth' — never hardcode.
    staging_basic_auth: str = ""

    # ── LLM Provider (Phase 7) ──────────────────────────────────────────────
    # Which LLM client to use: "mock" | "azure_openai"
    # Default is "mock" so CI tests require no Azure credentials or network.
    llm_provider: str = "mock"

    # Azure OpenAI credentials — required only when llm_provider="azure_openai".
    # Never hardcode. Load from Azure Key Vault in staging/production.
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_deployment_name: str = ""

    # ── LLM Analysis Council (Phase 28A) ────────────────────────────────────
    # A real, controlled LLM council for SINGLE-company report synthesis. It is
    # internal-only, citation-bound and safety-gated. OFF by default so CI and a
    # plain deploy stay fully deterministic (report says "LLM: not used").
    #
    # When enabled AND a usable provider is configured, the Final Report
    # Generator builds a bounded evidence pack and runs the council; council
    # output is safety-validated before it is saved or displayed. If the flag is
    # off, or no provider resolves, the deterministic path is preserved unchanged
    # and no fake council output is ever produced.
    llm_council_enabled: bool = False
    # Which client backend to use for the council: "fake" | "azure_openai" | "openai".
    # "fake" is deterministic + offline and is the ONLY provider used in tests.
    llm_provider_council: str = "fake"
    # Model / deployment identifier. For azure_openai this is informational (the
    # deployment name below is what routes the call); for openai it is the model.
    llm_model: str = ""
    # Sampling + output controls. Low temperature keeps council output stable.
    llm_temperature: float = 0.1
    llm_max_output_tokens: int = 1200
    llm_request_timeout_seconds: int = 40
    # Hard cap on evidence items passed to the council (bounds prompt size + cost).
    llm_council_max_evidence_items: int = 40
    # Council contract version. Bump when the agent set or output schema changes.
    llm_council_version: str = "v1"
    # OpenAI-compatible fallback key — required only when llm_provider_council="openai".
    # Never hardcode. Never logged. Never exposed in /health.
    openai_api_key: str = ""

    # ── LLM Discovery Council (Phase 28B) ───────────────────────────────────
    # A real, controlled, run-LEVEL LLM council that reviews the whole candidate
    # set produced by ONE discovery run and decides which candidates deserve
    # deeper internal research, which need more evidence, and which to reject or
    # monitor. Internal-only, citation-bound, safety-gated. Manual admin-triggered
    # only — it never runs automatically after a discovery run.
    #
    # Gated by BOTH flags: the discovery council runs only when
    # ``llm_council_enabled`` (the shared client gate) AND
    # ``llm_discovery_council_enabled`` are true and a usable provider resolves.
    # If either is off, the council is disabled and no fake output is produced in
    # production; the deterministic discovery result is unchanged.
    llm_discovery_council_enabled: bool = False
    # Hard cap on the number of candidates included in the council evidence pack
    # (bounds prompt size + cost). Candidates beyond the cap are summarized-out.
    llm_discovery_council_max_candidates: int = 25
    # Discovery-council contract version. Bump when the agent set or output schema
    # changes. Independent of the single-company council version.
    llm_discovery_council_version: str = "v1"

    # ── Market Candidate Discovery (Phase 25) ──────────────────────────────
    # Internal-only, bounded market scan configuration. Discovery produces
    # internal research candidates ranked by an internal prioritization score.
    # It is NOT a recommendation engine and never runs an uncontrolled scan.
    # Defaults are conservative so a scan stays small and cheap.
    discovery_default_provider: str = "free_real"
    # Hard cap on the number of tickers processed in a single run. A run above
    # this size is rejected — protecting against an accidental full-market scan.
    discovery_max_universe_size: int = 15
    # Bounded concurrency for per-ticker processing. Kept small (1–2) to be a
    # polite consumer of free upstream sources (SEC EDGAR, GDELT, RSS feeds).
    discovery_max_concurrent_requests: int = 1
    # Default price/catalyst lookback window (days) used for signal extraction.
    discovery_lookback_days: int = 90
    # Per-ticker request timeout budget (seconds).
    discovery_request_timeout_seconds: int = 30
    # Optional cache TTL (hours) for repeated discovery scans. 0 disables caching.
    discovery_cache_ttl_hours: int = 0
    # Comma-separated curated seed universe (US mega-caps by default). Kept small
    # so the default run stays well within discovery_max_universe_size.
    discovery_seed_universe: str = "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA"

    # ── Source Connector Framework (Phase 29B) ─────────────────────────────
    # Gate for wiring the source-registry connectors (SEC EDGAR, company IR,
    # regulator scaffolds) into the single-company evidence pack. OFF by default
    # so CI and a plain deploy keep the exact Phase 29A behaviour (the evidence
    # pack still records planned-source gaps, but adds no connector evidence).
    # When ON, the evidence pack additionally includes bounded, tiered connector
    # EvidenceItems built from already-fetched deterministic data plus honest
    # SourceGap objects for unavailable / non-US sources. No new report-time
    # network calls are made — connectors re-express data the workflow already
    # retrieved. The read-only evidence-preview endpoint may do bounded live
    # fetches only when this flag is on.
    source_connector_enabled: bool = False
    # Hard cap on evidence items taken from ANY single connector (bounds prompt
    # size + cost, and keeps one source from dominating the pack).
    source_connector_max_items_per_source: int = 5
    # Per-connector live-fetch timeout budget (seconds). Only relevant to the
    # evidence-preview live path; deterministic report-time use makes no calls.
    source_connector_timeout_seconds: int = 10
    # ── Bounded live web fetcher (Phase 29B.1) ─────────────────────────────
    # These only ever apply to the read-only evidence-preview live path (the
    # deterministic report/council path makes no network calls — it re-expresses
    # already-fetched data plus code-defined verified-issuer registry metadata).
    # Hard byte ceiling for a single fetched page — a page larger than this is
    # truncated, never fully buffered (bounds memory + protects against a huge
    # response). ~1 MB is ample for an IR / annual-reports landing page.
    source_connector_max_bytes: int = 1_000_000
    # When True (default), the safe fetcher will only ever fetch HTTPS URLs whose
    # host is inside a verified issuer's ``allowed_domains`` allowlist. There is
    # no arbitrary-URL fetch surface; this flag exists so the guard can never be
    # silently loosened by config drift.
    source_connector_allowlist_only: bool = True
    # Hard cap on links extracted from a single fetched page (bounds annual-report
    # / press-release link discovery). Excess links are dropped, not followed.
    source_connector_max_links_per_page: int = 25

    # ── Macro reference layer (Phase 29C.1) ────────────────────────────────
    # Gate for the reference-only macro source layer (FRED, IMF, Eurostat, World
    # Bank commodity pink sheet, national statistics offices / central banks).
    # OFF by default so CI and the Phase 29A/29B behaviour are unchanged — with
    # the flag off, ``collect_theme_macro_evidence`` is completely dark (no macro
    # evidence, no macro gaps). When ON, that collector emits bounded T2 macro
    # SOURCE REFERENCES (which official dataset covers which indicators) plus an
    # honest ``data_not_sourced`` gap for each — never a fabricated figure, date,
    # or release. NO network is used at report time and NO API key is introduced
    # (FRED-style keys are deliberately not supported); the connectors point only
    # at fixed, public, token-free official dataset landing pages.
    source_macro_enabled: bool = False
    # Hard cap on macro source references collected for one theme/region (bounds
    # prompt size + keeps one macro source from dominating the pack).
    source_macro_max_items: int = 3

    # ── Primary-document extraction (Phase 29B.2) ──────────────────────────
    # Bounded extraction of an issuer's OWN annual-report / registration-document
    # text so LLM councils can reason from real T1 primary evidence — not only
    # metadata + price/model data. OFF by default: with the flag off, the exact
    # Phase 29B.1 behaviour is preserved (company-IR metadata evidence + honest
    # gaps only, no document fetch). When ON *and* ``source_connector_enabled``
    # is also on, the company-IR connector may fetch one already-discovered,
    # allowlisted annual-report document, extract a bounded set of excerpts, and
    # parse only high-confidence primary facts. Never fabricates a filing: a
    # blocked / JS-gated / scanned document degrades to an honest SourceGap.
    # Only ever fetches a URL that came from the code-defined verified-issuer
    # registry (or a link already extracted from an allowlisted page) — there is
    # no arbitrary-URL fetch surface.
    source_document_extraction_enabled: bool = False
    # Hard byte ceiling for a single fetched document. A larger document is
    # truncated, never fully buffered. ~5 MB covers a typical results PDF.
    source_document_extraction_max_bytes: int = 5_000_000
    # Per-document fetch timeout budget (seconds).
    source_document_extraction_timeout_seconds: int = 15
    # Maximum number of leading pages read from a PDF (bounds extraction cost;
    # no OCR in this phase — a scanned PDF returns an honest empty-text gap).
    source_document_extraction_max_pages: int = 20
    # Maximum number of bounded excerpts produced from one document.
    source_document_extraction_max_excerpts: int = 8
    # Hard cap on characters per excerpt (a whole filing is never carried around).
    source_document_extraction_max_chars_per_excerpt: int = 1200
    # Content types the document fetcher will accept. Anything else is rejected
    # with an honest gap (no partial download).
    source_document_extraction_allowed_content_types: str = (
        "application/pdf,text/html,text/plain"
    )

    # ── LLM council evidence budget (Phase 29B.2) ──────────────────────────
    # A deterministic budgeter compresses the evidence pack before it reaches the
    # council so larger primary-source packs cannot balloon the prompt and trip
    # the Azure OpenAI TPM quota (which was partially failing large AAPL packs).
    # It de-duplicates, prefers higher tiers + factual excerpts over metadata, and
    # bounds item count / total characters / per-item characters — while never
    # dropping all source gaps and always preserving stable citation ids.
    llm_council_evidence_max_items: int = 20
    llm_council_evidence_max_chars: int = 24000
    llm_council_evidence_max_chars_per_item: int = 1200


settings = Settings()
