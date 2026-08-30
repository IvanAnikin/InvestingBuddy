// ONE workflow, TWO presentations.
//
// The admin console and the `/research` product surface are two views over the
// SAME backend workflows. Everything that decides WHAT the backend is asked to
// do — provider vocabulary, request shape, defaults — lives here, so the two
// UIs cannot drift apart on the contract while differing on presentation.
//
// Nothing in this module talks to the network. It builds request payloads and
// nothing else, which is what makes it directly assertable in a contract test.

import type {
  Company,
  ThesisDiscoveryRunCreate,
  WorkflowRunRequest,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Data providers
// ---------------------------------------------------------------------------

/**
 * `value` is the backend provider identifier and is the ONLY thing sent over
 * the wire. The two labels are presentation: the admin console names providers
 * by their identifier (an operator recognises `eodhd_free_real`), the product
 * surface names them by what they are. A friendly label is never sent, and a
 * label is never parsed back into a value.
 */
export interface DataProviderOption {
  value: string;
  /** Label used in the admin console. */
  adminLabel: string;
  /** Label used on the product surfaces. */
  productLabel: string;
  note: string;
  tag?: "recommended" | "paid" | "price-only" | "us-only" | "legacy" | "offline";
  /** True for providers that fabricate offline placeholder data. */
  offline?: boolean;
  /** True when the discovery pipeline accepts this provider (backend allowlist). */
  discovery?: boolean;
}

/** The backend value for the recommended free real-data stack. */
export const PROVIDER_FREE_REAL = "free_real";
/** The backend value for the offline placeholder provider. */
export const PROVIDER_MOCK = "mock";

// Order mirrors the recommended real-data stack first, then the individual
// sub-providers, then the legacy / paid full providers last so the paid EODHD
// full provider is never selected by accident.
export const DATA_PROVIDERS: DataProviderOption[] = [
  {
    value: PROVIDER_FREE_REAL,
    adminLabel: "free_real — Free real data: SEC + price + trend",
    productLabel: "Free real data (recommended)",
    tag: "recommended",
    note: "Recommended real-data provider. Combines regulator filings, price data and internal trend signals. No paid access required.",
    discovery: true,
  },
  {
    value: "eodhd_free_real",
    adminLabel: "eodhd_free_real — EODHD price-only + SEC",
    productLabel: "EODHD price + regulator filings",
    note: "EODHD price data (no paid fundamentals) combined with regulator filings.",
    discovery: true,
  },
  {
    value: "eodhd_price_only",
    adminLabel: "eodhd_price_only — EODHD price-only",
    productLabel: "EODHD price only",
    tag: "price-only",
    note: "Price data only. No fundamentals.",
  },
  {
    value: "sec_edgar_fundamentals",
    adminLabel: "sec_edgar_fundamentals — SEC EDGAR fundamentals only",
    productLabel: "Regulator fundamentals only",
    tag: "us-only",
    note: "Structured statement facts only. Applies to SEC-registered issuers.",
  },
  {
    value: "stooq",
    adminLabel: "stooq — Stooq price data",
    productLabel: "Stooq price data",
    tag: "price-only",
    note: "Price data only.",
  },
  {
    value: "gleif",
    adminLabel: "gleif — GLEIF identity",
    productLabel: "GLEIF identity only",
    note: "Legal entity identity data only (GLEIF).",
  },
  {
    value: "sec_edgar",
    adminLabel: "sec_edgar — Legacy SEC EDGAR",
    productLabel: "Legacy SEC EDGAR",
    tag: "legacy",
    note: "Legacy SEC EDGAR provider. Prefer sec_edgar_fundamentals or free_real.",
  },
  {
    value: "eodhd",
    adminLabel: "eodhd — EODHD full provider (paid fundamentals required)",
    productLabel: "EODHD full provider (paid)",
    tag: "paid",
    note: "Requires paid EODHD Fundamentals access. On the free plan the /fundamentals call fails with 403. Use free real data instead.",
  },
  {
    value: PROVIDER_MOCK,
    adminLabel: "mock — Mock / offline CI-safe",
    productLabel: "Offline placeholder data",
    tag: "offline",
    note: "Offline placeholder data. Safe for CI and smoke tests. No external calls, and nothing it returns is real.",
    offline: true,
    discovery: true,
  },
];

export function findProvider(value: string): DataProviderOption | undefined {
  return DATA_PROVIDERS.find((p) => p.value === value);
}

/** Providers the discovery pipeline accepts (mirrors the backend allowlist). */
export const DISCOVERY_PROVIDERS = DATA_PROVIDERS.filter((p) => p.discovery);

/**
 * True only for a provider that fabricates data. The product surface uses this
 * to say so out loud — a reader must never mistake placeholder output for
 * research.
 */
export function isOfflineProvider(value: string): boolean {
  return findProvider(value)?.offline === true;
}

// ---------------------------------------------------------------------------
// Company analysis
// ---------------------------------------------------------------------------

export interface CompanyAnalysisInput {
  /**
   * The resolved Company record when one was selected. Sending its id is what
   * makes the request unambiguous: the backend resolves company_id FIRST and
   * only falls back to (ticker, exchange), so a selected company can never be
   * re-derived from display text or matched to the wrong listing.
   */
  company?: Pick<Company, "id" | "ticker" | "exchange"> | null;
  /** Typed identity, used when no Company record was selected (admin console). */
  ticker?: string;
  exchange?: string;
  /** Backend provider identifier — always a `DATA_PROVIDERS` value. */
  providerName: string;
  /**
   * The workflow's `use_llm` flag. It gates the `generate_research_sections`
   * node — the LLM-DRAFTED SECTIONS step — and nothing else. It does NOT
   * control the research council, which is a server-side setting
   * (`LLM_COUNCIL_ENABLED`) applied when the final report is generated.
   */
  useLlmSections: boolean;
  /** LLM backend for those sections. Ignored when `useLlmSections` is false. */
  llmProvider?: string;
  requireSchemaValid: boolean;
}

/**
 * Build the `POST /api/v1/workflows/company-analysis/run` payload.
 *
 * Identity is carried at full fidelity: when a Company was selected, its id AND
 * its canonical ticker/exchange all travel, so the request is self-describing
 * in a log or a test without changing which record the backend resolves.
 */
export function buildCompanyAnalysisRequest(
  input: CompanyAnalysisInput,
): WorkflowRunRequest {
  const ticker = (input.company?.ticker ?? input.ticker ?? "")
    .trim()
    .toUpperCase();
  const exchange = (input.company?.exchange ?? input.exchange ?? "")
    .trim()
    .toUpperCase();

  return {
    company_id: input.company?.id ?? undefined,
    ticker: ticker || undefined,
    exchange: exchange || undefined,
    provider_name: input.providerName,
    use_llm: input.useLlmSections,
    llm_provider: input.useLlmSections ? input.llmProvider : undefined,
    require_schema_valid: input.requireSchemaValid,
  };
}

/** LLM backends offered for the research-sections node. */
export const LLM_SECTION_PROVIDERS = ["mock", "azure_openai"];

// ---------------------------------------------------------------------------
// Thesis discovery
// ---------------------------------------------------------------------------

/**
 * Defaults shared by both discovery surfaces. They are the admin console's
 * long-standing values; the product surface inherits them rather than picking
 * its own, so an identical description produces an identical run either way.
 */
export const DISCOVERY_DEFAULTS = {
  maxUniverseSize: 25,
  maxCandidates: 10,
  lookbackDays: 90,
  providerName: PROVIDER_FREE_REAL,
} as const;

export interface ThesisDiscoveryInput {
  thesisText: string;
  region?: string;
  country?: string;
  sector?: string;
  /**
   * Industry is an EXPLICIT narrowing filter and is only ever set by a person.
   *
   * It is deliberately not auto-filled from the thesis parser. The backend
   * already derives an industry from the thesis text itself; echoing that
   * derived value back as a request filter adds nothing, and any drift between
   * what the parser detected a moment ago and what the text says now silently
   * narrows the universe. Uncertainty is resolved toward BREADTH.
   */
  industry?: string;
  maxUniverseSize?: number;
  maxCandidates?: number;
  lookbackDays?: number;
  providerName?: string;
}

/** Build the `POST /api/v1/market-discovery/thesis-runs` payload. */
export function buildThesisDiscoveryRequest(
  input: ThesisDiscoveryInput,
): ThesisDiscoveryRunCreate {
  const trimmed = (value: string | undefined): string | undefined => {
    const v = (value ?? "").trim();
    return v ? v : undefined;
  };

  return {
    thesis_text: input.thesisText.trim(),
    region: trimmed(input.region),
    country: trimmed(input.country),
    sector: trimmed(input.sector),
    industry: trimmed(input.industry),
    max_universe_size: input.maxUniverseSize ?? DISCOVERY_DEFAULTS.maxUniverseSize,
    max_candidates: input.maxCandidates ?? DISCOVERY_DEFAULTS.maxCandidates,
    lookback_days: input.lookbackDays ?? DISCOVERY_DEFAULTS.lookbackDays,
    provider_name: input.providerName ?? DISCOVERY_DEFAULTS.providerName,
  };
}

/**
 * The filter fields the thesis parser is allowed to auto-fill.
 *
 * Region, country and sector only — the same three the admin console fills.
 * Industry is absent by design; see `ThesisDiscoveryInput.industry`.
 */
export const AUTOFILLED_FILTERS = ["region", "country", "sector"] as const;
export type AutofilledFilter = (typeof AUTOFILLED_FILTERS)[number];
