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
    # Output-token budget for ONE discovery-council agent call. Unlike the single
    # company council — whose per-agent JSON is a fixed-size qualitative shape —
    # the discovery council's JSON contract carries a ``candidate_notes`` array
    # with ONE entry per candidate, so its output size grows with the candidate
    # count. Sharing the company council's flat ``llm_max_output_tokens`` (1200)
    # truncated the reply mid-object on realistic multi-candidate runs, which
    # surfaced as a PERMANENT ``LLMJsonError`` (unparseable JSON is never
    # retried, and the one-shot repair reuses the same budget, so it failed
    # identically). The effective budget is therefore SCALED:
    #     min(cap, base + per_candidate * candidate_count)
    # ``base`` covers the fixed shell (summary <=600 chars, run_notes, gaps,
    # safety notes); ``per_candidate`` covers one candidate_note (whose
    # ``rationale`` is capped at <=150 chars in the prompt contract, i.e. roughly
    # 75 output tokens) with ~2x headroom; ``cap`` bounds worst-case cost and
    # latency. The default cap comfortably covers the default
    # ``llm_discovery_council_max_candidates`` (25) and exists to stop a raised
    # candidate cap from making a single call unbounded. Does NOT affect the
    # company council (``llm_max_output_tokens`` is unchanged).
    llm_discovery_max_output_tokens_base: int = 1200
    llm_discovery_max_output_tokens_per_candidate: int = 200
    llm_discovery_max_output_tokens_cap: int = 5000

    # ── LLM discovery council reliability / retry (Phase 32A Slice 6A) ─────
    # Master gate for the discovery-council reliability bundle: transient-error
    # retries, critical-agent reserved budget, and the deterministic
    # discovery-chair fallback. OFF by default so the discovery-council path is
    # byte-for-byte identical to today — a single attempt per agent, no
    # fallback. When ON, ``run_discovery_council`` runs an initial pass plus a
    # bounded, priority-ordered retry pass for TRANSIENTLY-failed agents (429 /
    # 5xx / timeout only — never a schema/safety failure, and never
    # ``LLMJsonError``, which is permanent by design), honoring a provider
    # ``retry-after`` (capped) with exponential backoff + jitter, under a total
    # wall-time budget that reserves capacity for run_red_team +
    # discovery_chair; when the LLM chair still does not complete it attaches a
    # deterministic, non-consensus discovery-chair summary. Mirrors the company
    # council's Slice-4 bundle (``llm_council_retry_*`` above) but with its OWN,
    # more generous budget: unlike the company council (which runs INLINE in
    # the HTTP request, bound by the ~230s Azure gateway timeout), the discovery
    # council runs as an ASYNC background job with no request-timeout
    # constraint, so it can afford a materially larger total budget + critical
    # reserve. This changes ONLY execution reliability: it never fabricates
    # evidence, never produces a recommendation/rating/price-target, and
    # candidates_* / run_quality stay honest.
    llm_discovery_council_retry_enabled: bool = False
    # Extra attempts (beyond the initial pass) for an OPTIONAL agent that failed
    # transiently. Total attempts for an optional agent = 1 + this.
    llm_discovery_council_retry_max_retries: int = 2
    # Extra attempts (beyond the initial pass) for a CRITICAL agent
    # (run_coordinator, risk_gatekeeper, run_red_team, discovery_chair).
    llm_discovery_council_retry_critical_max_retries: int = 3
    # Exponential-backoff base (seconds) before a retry: base * 2**(attempt-1),
    # capped by the max below, plus jitter in [0, base).
    llm_discovery_council_retry_base_backoff_seconds: float = 1.0
    # Hard ceiling (seconds) on a single computed backoff wait.
    llm_discovery_council_retry_max_backoff_seconds: float = 20.0
    # Hard ceiling (seconds) on an honored provider ``retry-after`` value, so a
    # hostile / large header can never blow the wall-time budget.
    llm_discovery_council_retry_max_retry_after_seconds: float = 30.0
    # HARD total discovery-council wall-time cap (seconds). All retries live
    # under this deadline. Materially higher than the company council's 150s:
    # the discovery council is not bound by the inline gateway timeout.
    llm_discovery_council_retry_total_budget_seconds: float = 300.0
    # Wall-time (seconds) reserved out of the total budget for the two protected
    # agents (run_red_team + discovery_chair) so earlier agents draining the
    # budget cannot starve the adversarial check and the synthesis. Materially
    # higher than the company council's 45s, matching the larger total budget.
    llm_discovery_council_retry_critical_reserve_seconds: float = 60.0
    # Inter-agent pacing (seconds) inside the discovery council's INITIAL pass
    # when the retry bundle is ON. The initial pass is already strictly
    # sequential (no asyncio.gather), but with no pacing all eight agents fire
    # back-to-back at the same Azure deployment within a few seconds, which — on
    # a large evidence pack, alongside real concurrent staging traffic — trips
    # the provider's short-window token/request-rate limits. A small fixed wait
    # between consecutive agents spreads the load; it costs at most
    # (agents - 1) * delay seconds, negligible against the 300s total budget.
    # Applies to the discovery council ONLY (the shared
    # ``retry_engine.run_with_retries`` defaults to 0.0 = no pacing, so the
    # company and field-review councils are unchanged), never after the last
    # agent, never when the wait would cross the deadline, and never in the
    # retry pass (which has its own jittered backoff). 0.0 disables pacing.
    llm_discovery_council_initial_pass_delay_seconds: float = 1.5

    # ── Deep Field Review (Phase 32A Slice 6D) ─────────────────────────────
    # A THIRD, separate council. It is NOT the discovery council (which triages a
    # candidate LIST before any analysis exists) and NOT the single-company
    # council (which analyses ONE company). The Deep Field Review runs AFTER two
    # or more candidates from the SAME discovery run already have a completed
    # full analysis, and compares those ALREADY-PERSISTED reports to produce an
    # internal RESEARCH-PRIORITY shortlist. It reads persisted data only — it
    # never re-runs an analysis, never fetches new data, and never produces a
    # rating, price target, fair value, or return projection.
    #
    # Gated by BOTH ``llm_council_enabled`` (the shared client gate) AND
    # ``llm_field_review_council_enabled``. OFF by default: with either flag off
    # no LLM call is made and no fake output is ever produced in production.
    llm_field_review_council_enabled: bool = False
    # Hard cap on companies included in ONE field-review pack (bounds prompt size
    # + cost). Companies beyond the cap are excluded with an honest reason.
    llm_field_review_council_max_companies: int = 12
    # Field-review contract version. Bump when the agent set or output schema
    # changes. Independent of the other two councils' versions.
    llm_field_review_council_version: str = "v1"
    # Minimum number of candidates with a usable completed full analysis before a
    # COMPARATIVE review is meaningful. Below this the service returns an explicit
    # ``insufficient_analyzed_candidates`` state — it never silently proceeds.
    field_review_min_candidates: int = 2

    # Bounded retry policy for the field-review council. Mirrors the Slice 4
    # single-company knobs but with a larger wall-budget: the field review runs as
    # an ASYNC background job (not inline in a request), so it is not bound by the
    # ~230s Azure gateway timeout. It is still STRICTLY bounded — attempt caps, a
    # total deadline, and capped jittered backoff. Never an unbounded loop.
    llm_field_review_council_retry_enabled: bool = True
    # Extra attempts (beyond the initial pass) for a transiently-failed agent.
    llm_field_review_council_max_retries: int = 2
    # Extra attempts for a CRITICAL agent (field_red_team + field_chair).
    llm_field_review_council_critical_max_retries: int = 3
    # Exponential-backoff base (seconds): base * 2**(attempt-1), capped below,
    # plus jitter in [0, base).
    llm_field_review_council_retry_base_backoff_seconds: float = 1.0
    # Hard ceiling (seconds) on a single computed backoff wait.
    llm_field_review_council_retry_max_backoff_seconds: float = 20.0
    # Hard ceiling (seconds) on an honored provider ``retry-after`` value, so a
    # hostile / large header can never blow the wall-time budget.
    llm_field_review_council_retry_max_retry_after_seconds: float = 30.0
    # HARD total wall-time cap (seconds) for the whole field-review council.
    llm_field_review_council_total_budget_seconds: float = 600.0
    # Wall-time (seconds) reserved out of the total budget for the two protected
    # agents (field_red_team + field_chair) so earlier agents draining the budget
    # cannot starve the adversarial check and the synthesis.
    llm_field_review_council_critical_reserve_seconds: float = 120.0

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

    # ── Procurement / tender event-trigger layer (Phase 29D.1) ─────────────
    # Gate for the reference-only procurement / tender EVENT layer (EU TED,
    # USAspending.gov). Independent of ``source_macro_enabled`` and OFF by default
    # so CI and the existing behaviour are unchanged — with the flag off,
    # ``collect_theme_event_evidence`` is completely dark (no event evidence, no
    # event gaps). When ON, that collector emits bounded T2 procurement / tender
    # SOURCE REFERENCES (which tenders / awards a venue publishes for a theme) plus
    # an honest ``data_not_sourced`` gap for each — never a fabricated tender,
    # award, contractor, amount, contract number, or date. Every reference is a
    # WEAK, needs-human-review internal research-priority signal, never a
    # materiality claim or trade signal. NO network is used at report time and NO
    # API key is introduced; the connectors point only at fixed, public,
    # token-free official venue landing pages.
    source_event_enabled: bool = False
    # Hard cap on procurement / tender event references collected for one
    # theme/region (bounds prompt size + keeps one venue from dominating the pack).
    source_event_max_items: int = 3

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
    # truncated, never fully buffered. Phase 32A Slice 5B.2 staging validation
    # found the prior 5 MB default silently truncated real large annual-report
    # PDFs (e.g. a genuine 25 MB IFRS annual report) mid-download — a truncated
    # PDF's trailer/xref table (at the END of the file) is corrupted, so it
    # fails to parse and gets misclassified as "scanned, no text layer" rather
    # than "download was cut off". 35 MB comfortably covers real annual-report
    # PDF sizes while staying explicitly bounded (not unbounded).
    source_document_extraction_max_bytes: int = 35_000_000
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

    # ── Category-aware evidence budgets (Phase 32A Slice 2) ────────────────
    # The Phase 29B.2 budgeter above is category-BLIND: it de-dups then re-ranks
    # ALL items by tier and truncates, so a flood of catalyst/news events can
    # crowd structured SEC/XBRL financial facts out of the pack (the AAPL
    # "financial data unavailable" defect). This flag turns on a category-aware
    # selection path that (a) tier-splits the SEC fundamentals blob into
    # correctly-tiered items at build time, (b) carries news materiality onto the
    # pack, and (c) reserves a floor of financial-fact slots + caps price/trend
    # and news categories so news volume can never consume the whole pack. OFF by
    # default → the evidence pack + budgeter behave byte-for-byte as before (dark
    # -safe). This flag ONLY changes behaviour where the Phase 29B.2 budgeter
    # already runs (``source_connector_enabled`` — i.e. staging). No new network,
    # no fabrication, no schema/migration change.
    llm_council_evidence_budgets_enabled: bool = False
    # Guaranteed number of SEC/XBRL financial-fact slots reserved before the
    # global fill, so higher-tier catalysts cannot evict every financial datapoint.
    llm_council_evidence_financial_floor: int = 3
    # Ceiling on price / market / trend (T5/T6) metric items.
    llm_council_evidence_price_trend_cap: int = 3
    # Combined ceiling across all news-ish categories (quality media + aggregator).
    llm_council_evidence_news_cap: int = 8
    # Stricter ceiling on low-tier (T5/T6) aggregator news specifically.
    llm_council_evidence_low_tier_news_cap: int = 4

    # ── Source translation (Phase 30A — foundation) ────────────────────────
    # Bounded, machine-assisted translation of a single non-English evidence
    # excerpt into English so a council can read a foreign-language T1 primary
    # source for research context. OFF by default: with the flag off, the
    # translation provider factory returns the deterministic *fake* provider and
    # nothing is wired into the council / report / evidence collection (that is
    # Phase 30A Task 2). A translation is NEVER presented as official — every
    # result is machine-assisted, needs human review, and carries an honest
    # warning; the original text + source URL are always preserved. Whole
    # documents are never translated — only one bounded excerpt at a time.
    source_translation_enabled: bool = False
    # Hard cap on characters per translated excerpt (bounds both the input and
    # the output so a whole filing is never sent to a translation provider).
    source_translation_max_chars: int = 400
    # Maximum number of excerpts translated for one company/source (bounds cost
    # and keeps one foreign-language source from dominating the pack).
    source_translation_max_excerpts: int = 3
    # Which translation backend to use: "fake" (deterministic honest placeholder,
    # the default and the only one used in tests/CI) or "llm" (composes the shared
    # LLM client). The "llm" provider is only ever resolved when this is "llm"
    # AND ``source_translation_enabled`` is True AND an LLM client is available.
    translation_provider: str = "fake"

    # ── Internal research memo builder (Phase 31) ──────────────────────────
    # Gate for the DETERMINISTIC, source-aware internal research MEMO — a
    # citation-bound synthesis of the ALREADY-ASSEMBLED report content + LLM
    # council metadata + gaps. OFF by default so the existing report body is
    # byte-for-byte unchanged: with the flag off no ``research_memo`` block is
    # attached and legacy output is identical. When ON, ``_build_research_memo``
    # reads ONLY the assembled ``report_content`` sections + ``council_result``
    # metadata (no ORM query, no LLM call, no external fetch, no recompute) and
    # emits a structured memo whose ``what_is_missing`` is prominent and whose
    # sections degrade honestly when evidence is thin (no primary facts / no
    # council / blocked extraction) — never fabricating a figure. The memo NEVER
    # produces a rating (BUY/SELL/HOLD/WATCH) or a valuation conclusion (price
    # target / fair value / intrinsic value / upside / downside); those literal
    # forbidden terms appear ONLY inside its ``disallowed_outputs`` notice, which
    # is exempt from the safety scanner. The memo is additive (not a required
    # section) so ``schema_valid`` is unaffected, and it is always
    # human-review-required and never publication-ready.
    source_research_memo_enabled: bool = False

    # ── LLM council reliability / retry (Phase 32A Slice 4) ────────────────
    # Master gate for the council reliability bundle: transient-error retries,
    # critical-agent reserved budget, and the deterministic committee-chair
    # fallback. OFF by default so the council path is byte-for-byte identical to
    # today — a single attempt per agent, no fallback, chair failure yields a
    # null committee_label. When ON, ``run_council`` runs an initial pass plus a
    # bounded, priority-ordered retry pass for TRANSIENTLY-failed agents (429 /
    # 5xx / timeout only — never a schema/safety failure), honoring a provider
    # ``retry-after`` (capped) with exponential backoff + jitter, under a strict
    # TOTAL wall-time budget that reserves capacity for red_team + committee_chair;
    # and when the LLM chair still does not complete it attaches a deterministic,
    # non-consensus committee summary (committee_label="insufficient_data", empty
    # key_points, no citations). This changes ONLY execution reliability: it never
    # flips publication_ready (stays False) or human_review_required (stays True),
    # never fabricates evidence, and failed agents still create no citations.
    llm_council_retry_enabled: bool = False
    # Extra attempts (beyond the initial pass) for an OPTIONAL agent that failed
    # transiently. Total attempts for an optional agent = 1 + this.
    llm_council_max_retries: int = 2
    # Extra attempts (beyond the initial pass) for a CRITICAL agent (financial_
    # analyst, source_quality_critic, red_team, committee_chair, and valuation_
    # guard when the pack carries financial evidence).
    llm_council_critical_max_retries: int = 3
    # Exponential-backoff base (seconds) before a retry: base * 2**(attempt-1),
    # capped by the max below, plus jitter in [0, base).
    llm_council_retry_base_backoff_seconds: float = 1.0
    # Hard ceiling (seconds) on a single computed backoff wait.
    llm_council_retry_max_backoff_seconds: float = 20.0
    # Hard ceiling (seconds) on an honored provider ``retry-after`` value, so a
    # hostile / large header can never blow the wall-time budget.
    llm_council_retry_max_retry_after_seconds: float = 30.0
    # HARD total council wall-time cap (seconds). All retries live under this
    # deadline; it must stay comfortably below the ~230s Azure gateway timeout
    # because the single-company council runs inline in the request handler.
    llm_council_total_budget_seconds: float = 150.0
    # Wall-time (seconds) reserved out of the total budget for the two protected
    # agents (red_team + committee_chair) so earlier agents draining the budget
    # cannot starve the adversarial check and the synthesis.
    llm_council_critical_reserve_seconds: float = 45.0

    # ── Report source/citation persistence + reconciliation (Phase 32A Slice 3)
    # Gate for PERSISTING the source/citation lineage of a report and RECONCILING
    # the honest source counts on the final-report appendix. OFF by default so the
    # existing report body + appendix wording are byte-for-byte unchanged: with the
    # flag off the draft-citation backfill stays the historic no-op, the appendix
    # loader is unchanged (report_id filter only), and no council claim→evidence
    # citations are persisted. When ON: (a) the company-analysis draft links its
    # deterministic profile/price/SEC-XBRL citations to the report it produced
    # (idempotent UPDATE scoped by this run's agent_run_id); (b) the final report
    # carries its lineage (company_id + created_by_agent_run_id from the source
    # report or workflow state — never fabricated); (c) the appendix loader falls
    # back to the lineage agent_run when no citation matches by report_id (so the
    # draft's deterministic citations surface WITHOUT duplicating rows); (d) each
    # COMPLETED council agent's cited evidence (E# → canonical Source + Citation)
    # is persisted in the SAME transaction, deduped by a synthesized content_hash
    # so re-runs never accumulate duplicate sources; and (e) the appendix reports
    # SIX honest side-by-side counts (never summed). No schema/migration change, no
    # new network fetch, no fabrication: metadata-only references are persisted as
    # references and NEVER counted or labelled as financial facts. publication_ready
    # stays False and human_review_required stays True.
    report_citation_persistence_enabled: bool = False

    # ── Primary-document ingestion (Phase 32A Slice 5 — foundation only) ────
    # FOUNDATION ONLY: these knobs (plus the ``extracted_documents`` /
    # ``extracted_facts`` tables + ORM models added this slice) prepare bounded
    # ingestion of an issuer's OWN primary documents (annual report / registration
    # document) into structured, citation-bound rows so LATER slices can feed the
    # council real T1 primary evidence — not only metadata + price/model data.
    # NOTHING is wired yet: with every flag off (the default) the existing
    # extraction / connector / council paths are byte-for-byte unchanged and these
    # knobs are inert. When a later slice turns ingestion on it stays bounded,
    # allowlist-only (no arbitrary-URL fetch surface), never fabricates a filing —
    # a blocked / JS-gated / scanned document degrades to an honest gap — and every
    # extracted fact is human-review-required and never a recommendation.
    primary_document_ingestion_enabled: bool = False
    # Optional OCR pass for scanned (image-only) PDFs. OFF by default AND gated
    # behind the master flag; kept separate so OCR (which needs a raster path added
    # in a later slice) can never be enabled implicitly.
    primary_document_ocr_enabled: bool = False
    # Hard byte ceiling for a single fetched document — a larger document is
    # rejected/truncated, never fully buffered (bounds memory).
    primary_document_max_download_bytes: int = 8_000_000
    # Maximum number of leading pages read from a PDF (bounds native extraction
    # cost). Pages beyond this are ignored.
    primary_document_max_pdf_pages: int = 40
    # Maximum number of pages rastered + OCR'd when OCR is enabled (kept far
    # smaller than the native page cap because OCR is much more expensive).
    primary_document_max_ocr_pages: int = 5
    # Per-document fetch timeout budget (seconds).
    primary_document_fetch_timeout_seconds: int = 15
    # Per-document text-extraction timeout budget (seconds).
    primary_document_extraction_timeout_seconds: int = 20
    # HARD per-document total timeout (seconds): fetch + extract + parse for ONE
    # document must complete inside this.
    primary_document_total_timeout_seconds: int = 45
    # AGGREGATE ingestion wall-time budget (seconds) across ALL documents in one
    # request. MUST stay small: ingestion runs BEFORE the council's ~150s
    # wall-budget and the whole request must fit the ~230s Azure gateway timeout,
    # so ingestion + council together must stay comfortably under it.
    primary_document_ingestion_budget_seconds: int = 60
    # Hard cap on documents ingested for one issuer in a single request (bounds
    # cost + keeps one issuer from draining the aggregate budget).
    primary_document_max_docs_per_issuer: int = 3
    # Maximum number of bounded excerpts produced from one document.
    primary_document_max_excerpts_per_document: int = 8
    # Hard cap on characters per excerpt (a whole filing is never carried around;
    # aligned with ``llm_council_evidence_max_chars_per_item``).
    primary_document_max_excerpt_chars: int = 1200
    # Minimum extraction confidence [0..1] for a parsed primary fact to be kept as
    # a validated fact; lower-confidence text is retained excerpt-only.
    primary_document_min_extraction_confidence: float = 0.6
    # Pillow decompression-bomb guard for the future OCR raster path: refuse to
    # decode an image larger than this many pixels.
    primary_document_max_image_pixels: int = 40_000_000
    # Evidence-pack integration (later slice): guaranteed floor + hard cap on the
    # number of primary-document facts contributed to the council evidence pack.
    primary_document_evidence_floor: int = 1
    primary_document_evidence_cap: int = 6
    # Phase 32A Slice 5 (3c-iii): freshness window (hours) for REUSING a previously
    # extracted primary document across a report regeneration. A persisted
    # ``extracted`` document whose ``retrieved_at`` is within this TTL is rebuilt
    # from its stored excerpts + validated facts and reused (no re-fetch /
    # re-extract); older documents are re-fetched. Only ever consulted when BOTH
    # ``primary_document_ingestion_enabled`` and
    # ``report_citation_persistence_enabled`` are on — with either off there is no
    # reuse lookup and the path is byte-identical.
    primary_document_reuse_ttl_hours: int = 24

    # ── Primary-document reachability + secure fetch (Phase 32A Slice 5B.1) ──
    # Slice 5A shipped the ingestion foundation but reached 0 successful live
    # extractions: US issuers had no SEC filing-BODY path at all, and modern
    # issuer IR pages are JS-rendered so an <a href>-only scan found nothing.
    # These knobs bound the fixes. All of them are inert unless
    # ``primary_document_ingestion_enabled`` (the master flag) is on.
    #
    # Resolve-then-connect IP pinning (closes the ADR-014 DNS-rebinding TOCTOU).
    # ON by default because it is a security fix, but kept as a kill-switch: with
    # it off the Slice 5A behaviour (resolve + check, unpinned connect) applies
    # and the degradation is recorded honestly rather than silently.
    primary_document_pin_dns_enabled: bool = True
    # Hard cap on document candidates kept from ONE issuer page across ALL
    # discovery strategies (bounds parsing + ranking cost).
    primary_document_max_discovery_candidates: int = 12
    # Ordered, comma-separated discovery strategies. Bounded, non-browser methods
    # only — there is deliberately no crawler and no headless browser here.
    primary_document_discovery_strategies: str = (
        "anchors,json_ld,next_data,embedded_json"
    )
    # NOTE: there is deliberately NO "JSON endpoint fetch" knob. The discovery
    # layer's ``find_json_endpoints`` only REPORTS same-origin endpoints it saw on
    # a page; nothing fetches them, so a setting bounding such a hop would be
    # documenting a capability that does not exist.
    # Official SEC filing-BODY retrieval for US issuers. Supplements — never
    # replaces — the SEC/XBRL structured facts, which stay authoritative.
    primary_document_sec_body_enabled: bool = True
    # Hard cap on SEC filing bodies fetched for one issuer in a single request.
    primary_document_sec_max_bodies: int = 2
    # Client-side throttle between SEC requests (milliseconds). SEC asks
    # automated clients to declare a User-Agent and stay under ~10 requests/sec;
    # this is deliberately more conservative than the published ceiling.
    sec_request_min_interval_ms: int = 120

    # ── Real OCR: Azure Document Intelligence (Phase 32A Slice 5B.2) ─────────
    # Only ever consulted when ``primary_document_ocr_enabled`` (Slice 5,
    # default False) is also True. With the endpoint left empty (the default),
    # ``get_ocr_provider()`` falls back to ``NoOpOcrProvider`` even if the flag
    # is on — this lets the flag be flipped safely before the Azure resource
    # is provisioned. Never hardcode. Load from Azure Key Vault in
    # staging/production; managed identity (``DefaultAzureCredential``) is
    # preferred over the API key when both are unset/set respectively.
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_api_key: str = ""
    # Hard cap for ONE OCR call (submit + poll), carved OUT OF — never added on
    # top of — ``primary_document_total_timeout_seconds`` (45s).
    primary_document_ocr_timeout_seconds: int = 20
    # Fixed poll interval (seconds) for the Azure long-running-operation status
    # check. Injectable in tests so nothing really sleeps.
    primary_document_ocr_poll_interval_seconds: float = 1.0
    # Cross-document cap on how many documents may go through OCR in a single
    # report-generation request. Smaller than ``primary_document_max_docs_per_issuer``
    # (3) because OCR is materially more expensive than native extraction.
    primary_document_max_ocr_documents_per_run: int = 2
    # Bounded retry count for a single transient (429 / 5xx) OCR failure.
    # Never unbounded — mirrors the ``_INDEX_ATTEMPTS_PER_DOCUMENT`` pattern.
    primary_document_ocr_max_retries: int = 1
    # Below this confidence [0..1], OCR excerpts are retained as bounded
    # evidence but never become validated-fact candidates.
    primary_document_ocr_min_confidence: float = 0.4


settings = Settings()
