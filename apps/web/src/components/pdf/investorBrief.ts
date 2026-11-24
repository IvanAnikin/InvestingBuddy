/**
 * The investor brief's view model: what the one-to-two page PDF shows, read from the
 * stored report and the report's market data. Nothing is re-derived or re-worded: the
 * sentences are the report's own (with their finding labels), and every figure carries
 * the source and date it came with. Pure, so it is testable without rendering.
 */

import type { Report, ReportMarketData } from "@/types/api";
import type {
  DomainSection,
  ProfessionalFinding,
  ProfessionalResearch,
} from "@/components/research/v3Research";
import { clip, day, growthPct, money, pct, price } from "./format";
import type { FinancialBar, PricePoint } from "./charts";

export type Kpi = { label: string; value: string; sub: string | null; tone?: "positive" | "negative" | null };
export type BriefItem = { label: string | null; text: string; meta: string | null };

export type InvestorBrief = {
  name: string;
  ticker: string | null;
  exchange: string | null;
  reportId: string;
  reportDate: string | null;
  reviewStatus: string | null;
  kpis: Kpi[];
  price: {
    points: PricePoint[];
    caption: string;
    unavailable: string | null;
  };
  financials: { rows: FinancialBar[]; caption: string; unavailable: string | null };
  thesis: string | null;
  summary: string[];
  strengths: BriefItem[];
  catalysts: BriefItem[];
  risks: BriefItem[];
  openQuestions: string[];
  verdict: string | null;
  confidence: string | null;
  sizeFit: string | null;
  sources: string[];
  disclaimer: string;
};

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

const REASON_WORDS: Record<string, string> = {
  no_api_key: "the price provider is not configured",
  unknown_symbol: "the price provider does not know this listing",
  no_prices: "the price provider returned no closes",
  fetch_failed: "the source could not be read",
  no_ticker: "the report names no listing",
  no_cik: "no SEC registrant was found for the ticker",
  not_a_us_filer: "the company does not file 10-Ks with the SEC",
  no_annual_facts: "no annual figures are tagged in the filings",
};

const reasonWords = (r: string | null | undefined): string =>
  (r && REASON_WORDS[r]) ?? (r ? r.replace(/_/g, " ") : "not available");

function domain(research: ProfessionalResearch, key: string): DomainSection | null {
  const s = research.sections.find((x) => x.key === key);
  return s && s.kind === "domain" ? s : null;
}

function findingsByLabel(research: ProfessionalResearch): Map<string, ProfessionalFinding> {
  const out = new Map<string, ProfessionalFinding>();
  for (const s of research.sections) {
    if (s.kind !== "domain") continue;
    for (const f of s.findings) if (f.label) out.set(f.label, f);
  }
  return out;
}

const item = (f: ProfessionalFinding, max = 240): BriefItem => ({
  label: f.label,
  text: clip(f.statement, max),
  meta: [f.label, f.domainLabel, f.sourcePublishedAt && day(f.sourcePublishedAt)].filter(Boolean).join(" · ") || null,
});

/** The report's stored market capitalisation (valuation_context), with its date and method. */
function storedMarketCap(report: Report): { value: number; currency: string; asOf: string | null; method: string | null } | null {
  const v3 = rec(rec(report.source_summary_json)?.v3_research);
  const sections = rec(v3?.professional_research)?.sections;
  if (!Array.isArray(sections)) return null;
  for (const s of sections) {
    const mc = rec(rec(rec(s)?.enterprise_value)?.market_cap);
    const value = num(mc?.value);
    if (mc && value !== null) {
      return { value, currency: str(mc.currency) ?? "USD", asOf: str(mc.as_of), method: str(mc.method) };
    }
  }
  return null;
}

const METHOD_WORDS: Record<string, string> = {
  sec_shares_x_price: "SEC shares × close",
  exchange_directory: "exchange directory",
  public_float_floor: "public float (floor)",
};

export function buildInvestorBrief(
  report: Report,
  research: ProfessionalResearch,
  market: ReportMarketData | null,
): InvestorBrief {
  const name = research.subject?.name ?? market?.subject.name ?? report.title;
  const ticker = research.subject?.ticker ?? market?.subject.ticker ?? null;
  const exchange = market?.subject.exchange ?? null;
  const labels = findingsByLabel(research);

  // --- KPI tiles ---------------------------------------------------------------
  const kpis: Kpi[] = [];
  const fitSection = domain(research, "thesis_fit");
  const cap =
    storedMarketCap(report) ??
    (fitSection?.sizeFit?.marketCapUsd != null
      ? { value: fitSection.sizeFit.marketCapUsd, currency: "USD", asOf: null, method: null }
      : null);
  if (cap) {
    kpis.push({
      label: "Market cap",
      value: money(cap.value, cap.currency),
      sub: [cap.asOf && `as of ${day(cap.asOf)}`, cap.method && (METHOD_WORDS[cap.method] ?? cap.method)].filter(Boolean).join(" · "),
    });
  }
  const p = market?.price;
  if (p && p.state === "ok" && p.last_close != null) {
    kpis.push({
      label: "Last close",
      value: price(p.last_close, p.currency),
      sub: p.last_date ? day(p.last_date) : null,
    });
    if (p.change_pct != null && p.first_date) {
      kpis.push({
        label: "Price change",
        value: pct(p.change_pct),
        sub: `since ${day(p.first_date)}${p.adjusted ? " · split-adjusted" : ""}`,
        tone: p.change_pct >= 0 ? "positive" : "negative",
      });
    }
  }
  const fin = market?.financials;
  const rows = fin && fin.state === "ok" ? fin.rows : [];
  const latest = rows[rows.length - 1];
  const prior = rows[rows.length - 2];
  if (latest?.revenue != null) {
    const g = growthPct(latest.revenue, prior?.revenue);
    kpis.push({
      label: "Revenue (FY)",
      value: money(latest.revenue, fin?.currency ?? "USD", 1),
      sub: [`FY ending ${day(latest.fiscal_year_end)}`, g !== null && `${pct(g)} YoY`].filter(Boolean).join(" · "),
      tone: g === null ? null : g >= 0 ? "positive" : "negative",
    });
  }
  if (latest?.net_income != null) {
    const margin = latest.revenue ? (latest.net_income / latest.revenue) * 100 : null;
    kpis.push({
      label: "Net income (FY)",
      value: money(latest.net_income, fin?.currency ?? "USD", 1),
      sub: margin !== null ? `${pct(margin, 1, false)} net margin` : null,
      tone: latest.net_income >= 0 ? "positive" : "negative",
    });
  }
  const v = research.verdict;
  if (v) {
    kpis.push({
      label: "Research verdict",
      value: v.recommendation,
      sub: [v.compositeScore !== null && `composite ${v.compositeScore}/5`, v.confidence.band && `confidence ${v.confidence.band}`]
        .filter(Boolean)
        .join(" · ") || null,
    });
  }
  const val = research.valuation;
  if (val && val.range.state === "computed" && val.range.low !== null && val.range.high !== null) {
    kpis.push({
      label: "Valuation range",
      value: `${price(val.range.low)}–${price(val.range.high)}`,
      sub: `${val.currency ?? ""} per share${val.price?.asOf ? ` · price ref. ${day(val.price.asOf)}` : ""}`.trim(),
    });
  }

  // --- Charts ---------------------------------------------------------------------
  const pricePart = {
    points: p && p.state === "ok" ? p.points : [],
    caption:
      p && p.state === "ok" && p.first_date && p.last_date
        ? `${p.currency ?? ""} ${p.adjusted ? "close adjusted for splits and dividends" : "close"}, ${day(p.first_date)} – ${day(p.last_date)} · ${p.source ?? "end-of-day prices"}`.trim()
        : "Share price",
    unavailable: !p ? "Market data could not be loaded" : p.state === "ok" ? null : `No price series: ${reasonWords(p.reason)}`,
  };
  const finPart = {
    rows: rows.map((r) => ({
      label: `FY${r.fiscal_year_end.slice(2, 4)}`,
      revenue: r.revenue,
      netIncome: r.net_income,
    })),
    caption: `Revenue and net income, ${fin?.currency ?? "USD"} · ${fin?.source ?? "annual filings"}`,
    unavailable: !fin ? "Market data could not be loaded" : fin.state === "ok" ? null : `No annual figures: ${reasonWords(fin.reason)}`,
  };

  // --- Narrative ------------------------------------------------------------------
  const synthesis = research.sections.find((s) => s.kind === "synthesis");
  const summary =
    synthesis && synthesis.kind === "synthesis"
      ? synthesis.sentences.slice(0, 4).map((x) => clip(x.labels.length ? `${x.text} [${x.labels.join(", ")}]` : x.text, 320))
      : [];
  const fit = domain(research, "thesis_fit");
  const change = research.sections.find((s) => s.kind === "change");

  const catalystLabels = change && change.kind === "change" ? change.catalystLabels : [];
  let catalysts = catalystLabels.map((l) => labels.get(l)).filter((f): f is ProfessionalFinding => !!f).map((f) => item(f));
  if (catalysts.length === 0) {
    const growth = domain(research, "growth_and_catalysts")?.findings ?? [];
    const positive = growth.filter((f) => f.direction === "supportive" || f.direction === "positive");
    catalysts = (positive.length ? positive : growth).map((f) => item(f));
  }

  const bear = research.selfCritique?.bearCase ?? [];
  let risks = bear
    .map((b) => (b.label ? labels.get(b.label) : undefined))
    .filter((f): f is ProfessionalFinding => !!f)
    .map((f) => item(f));
  if (risks.length === 0) risks = (domain(research, "risks_and_counter_thesis")?.findings ?? []).map((f) => item(f));

  const strengths = [...labels.values()].filter((f) => f.direction === "supportive").map((f) => item(f));

  const openQuestions = research.sections
    .flatMap((s) => (s.kind === "domain" ? s.openQuestions : []))
    .map((q) => q.text)
    .filter((t): t is string => !!t)
    .slice(0, 3)
    .map((t) => clip(t, 280));

  // Confidence: the verdict's when computed, otherwise the findings' own average.
  let confidence: string | null = null;
  if (v?.confidence.band) {
    confidence = `Verdict confidence ${v.confidence.band}${v.confidence.value !== null ? ` (${v.confidence.value})` : ""}`;
  } else {
    const cs = [...labels.values()].map((f) => Number(f.confidence)).filter((n) => Number.isFinite(n));
    if (cs.length) {
      const avg = cs.reduce((a, b) => a + b, 0) / cs.length;
      confidence = `Average finding confidence ${avg.toFixed(2)} across ${cs.length} cited findings`;
    }
  }

  const sources = new Set<string>();
  if (p?.source_url) sources.add(p.source_url);
  for (const r of rows) for (const u of r.source_urls ?? []) sources.add(u);

  return {
    name,
    ticker,
    exchange,
    reportId: report.id,
    reportDate: report.created_at ?? null,
    reviewStatus: report.review_status ?? report.status ?? null,
    kpis: kpis.slice(0, 8),
    price: pricePart,
    financials: finPart,
    thesis: fit?.thesis?.text ?? null,
    summary,
    strengths: strengths.slice(0, 4),
    catalysts: catalysts.slice(0, 4),
    risks: risks.slice(0, 4),
    openQuestions,
    verdict: v ? `${v.recommendation}${v.meaning ? ` — ${v.meaning}` : ""}` : null,
    confidence,
    sizeFit: fit?.sizeFit?.note ?? null,
    sources: [...sources].slice(0, 8),
    disclaimer:
      research.disclaimer ??
      "Internal research draft. Not investment advice and not a recommendation. Human review required.",
  };
}
