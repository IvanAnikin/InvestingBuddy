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


settings = Settings()
