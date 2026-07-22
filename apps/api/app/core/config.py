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


settings = Settings()
