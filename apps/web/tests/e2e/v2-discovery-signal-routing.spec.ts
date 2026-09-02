import { expect, test } from "@playwright/test";
import { isEvidenceStatement } from "../../src/components/research/investorSignal";
import {
  candidateConcerns,
  candidateResearchLimitations,
  candidateStrengths,
} from "../../src/components/research/discovery/candidateView";
import type { CouncilPriorityEntry } from "../../src/components/research/discoveryCouncilView";
import type { DiscoveryCandidate } from "../../src/types/api";

/**
 * "Sparse data" is a research limitation, not an economic downside.
 *
 * THE DEFECT. The discovery card rendered, under "Could pressure value":
 *
 *     Could pressure value:
 *     - Sparse data for this issuer
 *
 * That is semantically wrong — it tells a reader that this platform's coverage
 * is a reason the company might be worth less. And it was not only the
 * screening labels: run against a REAL local council over six European luxury
 * names, every one of its twelve `downside_drivers` was a statement about the
 * evidence ("Data gaps prevent risk assessment", "No price data limits
 * momentum insights"), because on an evidence-starved cohort that is genuinely
 * all the council can say.
 *
 * The strings below are that council's ACTUAL output, copied verbatim.
 */

/** Verbatim from a real local discovery-council run, 2026-09-01. */
const LIVE_DOWNSIDE_DRIVERS = [
  "Lack of data obscures financial health and risks",
  "No catalyst signals limit near-term insight",
  "Data gaps prevent risk assessment",
  "No price or trend data limits momentum analysis",
  "No data to assess financial or operational risks",
  "Absence of catalyst signals limits near-term visibility",
  "Missing financials obscure risk profile",
  "No price data limits momentum insights",
  "Data gaps prevent risk and financial assessment",
  "No trend or catalyst data available",
  "Missing financials and filings limit risk insight",
  "No price or trend data available",
];

/** Statements about the BUSINESS, which must NOT be routed away. */
const ECONOMIC_DRIVERS = [
  "Watch division operating margin fell to 3.4%, leaving almost no cushion",
  "Owned retail carries fixed occupancy cost, so a demand slowdown bites harder",
  "Input-cost pressure is compressing the gross margin",
  "Net debt above 2x book limits reinvestment capacity",
  "Discretionary demand is cyclical in the issuer's stated end markets",
  "Jewellery margin expansion could lift group profitability",
  "Net cash funds buybacks",
];

/**
 * Verbatim from the `risk_analysis` company-risk slots of the LIVE PNDORA, CFR
 * and MRNA reports generated on 2026-09-02 by the deployed corrective.
 *
 * Twenty-two of the twenty-four items across those three issuers are
 * statements about the RESEARCH, not about the business — and "Research
 * incomplete: 30 blocking gaps in the research package…" was the FIRST thing
 * the Key Risks section offered a reader for all three.
 */
const LIVE_COMPANY_RISK_SLOT = [
  "Research incomplete: 30 blocking gaps in the research package. Business model, competitive position, and management quality have not been assessed.",
  "Financial data is partial: 5 statement categories (ebit, free cash flow, net income, revenue, total assets) are sourced (T1_primary_filing, issuer_primary_document); 13 valuation inputs remain missing.",
  "Currency risk: reporting currency is 'not sourced'. FX exposure to investment base currency is unknown at this phase.",
  "Price volatility risk: price data available (249 data points from eodhd_price_only, T5_api_aggregator). Volatility, beta, and correlation to broader market indices not yet computed",
  "Market depth risk: Exchange is CO. Liquidity and bid-ask spread data not sourced.",
  "UNKNOWN: LEI (Legal Entity Identifier) not sourced — regulatory standing and compliance status cannot be verified via GLEIF.",
  "UNKNOWN: ISIN not sourced — exchange listing and regulatory compliance status cannot be confirmed.",
  "UNKNOWN: Regulatory environment in Denmark not yet assessed. Sector-specific regulatory risks require T2/T3 research.",
];

/**
 * The Key Risks FOOTNOTE, verbatim from the live PNDORA report. It is the last
 * route by which a source-tier code reached the company-risk section, and it
 * is unambiguously a statement about the research.
 */
const LIVE_RISK_SUMMARY =
  "Risk assessment for PNDORA (PNDORA), sector not sourced, Denmark. Total " +
  "risk flags: 19 (3 marked UNKNOWN due to missing data). Data quality: " +
  "identity/price T6_model_estimate, financial statement facts " +
  "T1_primary_filing. Assessment is incomplete — the issuer's own primary " +
  "filing is ingested, but the remaining statement lines and " +
  "identity/regulatory confirmation are still required before any investment " +
  "decision. This is an internal draft only.";

/** The only two GENUINE company risks in those same slots, both from MRNA. */
const LIVE_REAL_COMPANY_RISKS = [
  "Clinical development risk — pipeline assets may fail trials.",
  "Reimbursement and pricing pressure from payers.",
];

function candidate(labels: string[]): DiscoveryCandidate {
  return {
    id: "c1",
    discovery_run_id: "r1",
    ticker: "CFR",
    exchange: "SW",
    company_name: "Test Issuer",
    labels_json: labels,
    source_quality: "weak",
    missing_info_count: 9,
    blocking_gap_count: 1,
  } as unknown as DiscoveryCandidate;
}

function placement(over: Partial<CouncilPriorityEntry>): CouncilPriorityEntry {
  return {
    action: "insufficient_data",
    candidateRef: "C1",
    candidateId: "c1",
    ticker: "CFR",
    exchange: "SW",
    rationale: null,
    confidence: "low",
    upsideDrivers: [],
    downsideDrivers: [],
    resilience: null,
    keyFinancialSignal: null,
    strongestDimension: null,
    supporting: [],
    concerns: [],
    ...over,
  };
}

test.describe("evidence statements are not economic drivers", () => {
  test("every downside driver a real council wrote about the evidence is caught", () => {
    for (const statement of LIVE_DOWNSIDE_DRIVERS) {
      expect(
        isEvidenceStatement(statement),
        `not routed: ${statement}`,
      ).toBe(true);
    }
  });

  test("statements about the business are NOT caught", () => {
    for (const statement of ECONOMIC_DRIVERS) {
      expect(
        isEvidenceStatement(statement),
        `wrongly routed: ${statement}`,
      ).toBe(false);
    }
  });

  test("the card's downside list drops them and its limitations keep them", () => {
    const p = placement({ downsideDrivers: LIVE_DOWNSIDE_DRIVERS.slice(0, 2) });
    const c = candidate(["data_sparse", "research_incomplete"]);

    expect(candidateConcerns(c, p)).toEqual([]);
    const limitations = candidateResearchLimitations(c, p);
    expect(limitations).toContain("Sparse evidence for this issuer");
    expect(limitations).toContain("Screening evidence incomplete");
    // Nothing is discarded — the council's own words are still shown, filed
    // where they describe what they actually describe.
    for (const statement of LIVE_DOWNSIDE_DRIVERS.slice(0, 2)) {
      expect(limitations).toContain(statement);
    }
  });

  test("a genuine economic downside still reaches the downside list", () => {
    const p = placement({
      downsideDrivers: [
        "Watch division operating margin fell to 3.4%, leaving almost no cushion",
        "Data gaps prevent risk assessment",
      ],
    });
    expect(candidateConcerns(candidate([]), p)).toEqual([
      "Watch division operating margin fell to 3.4%, leaving almost no cushion",
    ]);
  });

  test("a screening label never becomes an economic driver", () => {
    // No council placement at all — the pre-council state, which is where the
    // live defect was visible.
    const c = candidate(["data_sparse", "research_incomplete"]);
    expect(candidateConcerns(c, null)).toEqual([]);
    expect(candidateStrengths(c, null)).toEqual([]);
    expect(candidateResearchLimitations(c, null)).toEqual([
      "Sparse evidence for this issuer",
      "Screening evidence incomplete",
    ]);
  });

  test("a positive screening label is still a strength", () => {
    const c = candidate(["positive_momentum_candidate", "fundamentals_available"]);
    expect(candidateStrengths(c, null)).toEqual([
      "Positive price momentum",
      "Fundamentals sourced",
    ]);
  });
});

test.describe("Key Risks carries company risk only", () => {
  test("every research-state item the live reports wrote is routed away", () => {
    for (const statement of LIVE_COMPANY_RISK_SLOT) {
      expect(
        isEvidenceStatement(statement),
        `not routed: ${statement.slice(0, 90)}`,
      ).toBe(true);
    }
  });

  test("the two genuine company risks are NOT routed away", () => {
    for (const statement of LIVE_REAL_COMPANY_RISKS) {
      expect(
        isEvidenceStatement(statement),
        `wrongly routed: ${statement}`,
      ).toBe(false);
    }
  });

  test("ordinary business findings that merely say 'not' stay company risks", () => {
    // The verb list is epistemic on purpose. These are about the business.
    for (const statement of [
      "Margin has not yet recovered to its pre-pandemic level.",
      "The dividend was not raised this year.",
      "Owned retail carries fixed occupancy cost that does not fall with revenue.",
      "Net debt has not been reduced since the acquisition closed.",
    ]) {
      expect(
        isEvidenceStatement(statement),
        `wrongly routed: ${statement}`,
      ).toBe(false);
    }
  });
});

test.describe("the Key Risks footnote", () => {
  test("the live risk-summary line is routed to research confidence", () => {
    expect(isEvidenceStatement(LIVE_RISK_SUMMARY)).toBe(true);
  });

  test("a summary that IS about the business would be kept", () => {
    expect(
      isEvidenceStatement(
        "Business risk is concentrated in channel mix and discretionary demand.",
      ),
    ).toBe(false);
  });
});
