from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "InvestingBuddy API"
    app_env: str = "development"
    debug: bool = False

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


settings = Settings()
