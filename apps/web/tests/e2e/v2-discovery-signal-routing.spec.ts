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
