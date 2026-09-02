// What KIND of statement is this?
//
// A reader-facing report mixes two things that must not share a heading: what
// could change the company's economics, and how confident we are about any of
// it. "Net debt rose while equity fell, which raises refinancing risk" is the
// first. "No independent news coverage was retrieved" is the second. Both are
// true, both matter, and putting the second under "what could pressure value"
// tells a reader that the research process is a risk to the business.
//
// This module is the single routing rule. It is deterministic, uses no model,
// and answers with one of nine kinds.
//
// Two inputs decide it, in this order:
//
//   1. ROLE. The Source Quality Critic's subject IS the evidence. Whatever it
//      writes and whichever direction it gives it, its output describes how
//      much weight a conclusion can carry — not the conclusion. Source weakness
//      changes CONFIDENCE in a valuation; it does not change a company's value.
//   2. WORDING. Any agent can write an evidence statement. A sentence whose
//      primary assertion is that something was not found, not disclosed or not
//      established is a research limitation regardless of who wrote it or what
//      direction they attached.
//
// Calibrated against the live council output for PNDORA, CFR, MRNA and MONC:
// the wording rule must catch every evidence statement in `risks_or_gaps`
// (where all of them live) and none of the economic implications.

import { isRecordGapStatement } from "./recordGaps";

export type InvestorSignal =
  | "economic_support"
  | "economic_pressure"
  | "resilience"
  | "fragility"
  | "catalyst"
  | "company_risk"
  | "investor_question"
  | "research_limitation"
  | "technical_gap";

/** The one agent whose subject is the evidence rather than the business. */
export const EVIDENCE_ROLE_AGENTS = new Set(["source_quality_critic"]);

// ---------------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------------

/**
 * Something is asserted to be absent. Deliberately narrow: these are the forms
 * the council actually writes, not every negation in English.
 */
const ABSENCE = new RegExp(
  [
    "\\b(no|not|nothing|never)\\b",
    "\\black(s|ing)?\\b",
    "\\babsence\\b",
    "\\bwithout\\b",
    "\\bmissing\\b",
    "\\bunavailable\\b",
    "\\bundisclosed\\b",
    "\\binsufficient\\b",
    "\\bunestablished\\b",
    "\\bcannot be\\b",
    "\\bunable to\\b",
    "\\blimited\\b",
  ].join("|"),
  "i",
);

/**
 * ...and the thing that is absent is EVIDENCE, not an economic quantity.
 *
 * "No segment breakdown was disclosed" is a research limitation. "No pricing
 * power" is a business finding. The difference is the noun.
 */
const EVIDENCE_SUBJECT = new RegExp(
  [
    "\\bdata\\b",
    "\\bevidence\\b",
    "\\bdisclosur",
    "\\bdisclosed\\b",
    "\\binformation\\b",
    "\\bbreakdown",
    "\\bdetail(s|ed)?\\b",
    "\\bcoverage\\b",
    "\\bsource(s|d)?\\b",
    "\\bcitation",
    "\\bfiling(s)?\\b",
    "\\btranscript",
    "\\breported\\b",
    "\\bretrieved\\b",
    "\\bprovided\\b",
    "\\bavailable\\b",
    "\\bestablished\\b",
    "\\bmetrics\\b",
    "\\bfigures\\b",
    "\\bstatement(s)?\\b",
    "\\bclassification\\b",
    "\\bmultiples\\b",
    "\\bfinancials\\b",
  ].join("|"),
  "i",
);

/**
 * The consequence lands on KNOWLEDGE, not on the business.
 *
 * This is the sharper test, and the one that catches what a noun list misses.
 * "Lack of clarity on reporting currency impedes comparability" names no
 * evidence noun, but what it says is limited is our ability to compare — not
 * the company's economics. A statement whose stated effect is on assessment,
 * confidence, visibility or comparability is a research limitation whatever
 * its subject.
 *
 * The object matters: "limiting overall margin expansion" is an economic
 * consequence and must not match, which is why the epistemic noun is required.
 */
const EPISTEMIC_CONSEQUENCE = new RegExp(
  [
    // Up to three words may sit between the verb and the epistemic noun. The
    // live discovery council writes "data gaps prevent risk assessment",
    // "limits near-term visibility" and "obscure risk profile" — the noun is
    // still what is being limited, and requiring it to be adjacent let every
    // one of those through as an economic downside.
    "(limit|restrict|prevent|impede|hamper|reduce|constrain|obscure)\\w*\\s+" +
      "(?:[\\w-]+\\s+){0,3}" +
      "(ability|assessment|evaluation|analysis|confidence|understanding|" +
      "visibility|insight|insights|comparability|interpretation|conclusion|" +
      "profile)",
    "cannot be (assessed|established|verified|determined|evaluated|analy[sz]ed|" +
      "compared|quantified)",
    // "...have not been assessed", "not yet computed", "not yet assessed".
    //
    // The consequence lands on the RESEARCH, not on the business. Every
    // company-risk slot on the live PNDORA, CFR and MRNA reports opened with
    // one of these — "Research incomplete: 30 blocking gaps in the research
    // package. Business model, competitive position, and management quality
    // have not been assessed." was the FIRST thing the Key Risks section
    // offered a reader for all three issuers.
    //
    // The verb list is deliberately epistemic. "Margin has not yet recovered"
    // is a business finding and does not match; "margin has not been assessed"
    // is a statement about this platform's coverage and does.
    "\\bnot\\s+(yet\\s+)?(been\\s+)?(assessed|researched|evaluated|analy[sz]ed|" +
      "computed|verified|determined|established|quantified|reviewed|sourced)\\b",
    "\\bresearch (is )?incomplete\\b",
    // "Sector-specific regulatory risks require T2/T3 research." — a research
    // TASK, written into a risk slot.
    "\\brequires?\\s+T\\d",
    "\\brequires?\\s+(further|additional|more)\\s+research\\b",
    "create(s)? uncertainty",
    "impedes comparability",
    "limits? confidence",
    "not (directly )?comparable",
  ].join("|"),
  "i",
);

/**
 * Statements ABOUT the evidence that carry no absence word.
 *
 * "Catalyst coverage rests on the issuer's own channel alone" asserts nothing
 * is missing — it describes where the evidence came from, which is a research
 * limitation all the same.
 */
const EVIDENCE_SUBJECT_PHRASE = new RegExp(
  [
    "coverage rests on",
    "rests on .{0,30}(channel|source)",
    "single (channel|source)",
    "issuer'?s own channel",
    "aggregator[- ]tier",
    "source[- ]tier",
    "source quality",
    "evidence (is|was) bounded",
    "citation",
    "not annualized and not directly comparable",
    "not comparable to annual",
    // Evidence PRESENCE, framed as a finding. On an evidence-starved issuer
    // the chair offered "Recent stock closing price is available as a factual
    // data point" and "Company identity is confirmed via SEC filings" as its
    // strongest POSITIVE evidence. Both are true; neither is a reason a
    // business might become more valuable.
    "(is|are|was|were)\\s+available\\b",
    "available as a (factual )?data point",
    "confirmed (via|by|through)\\s+(sec|the\\s+)?(filing|filings|disclosure)",
    "identity (and profile )?(is|are)\\s+confirmed",
    "data point",
    // Reporting inconsistency is a finding about the RECORD, not the company.
    "(discrepanc|inconsisten)\\w*\\s+(between|in)\\s+.{0,40}(figures|revenue|data|reporting|scope)",
    "(scope|reporting) inconsistenc",
    // A GAP, when it is a gap in the RESEARCH, whatever grammar surrounds it.
    // The live US-biotech council wrote "Multiple blocking research gaps" as a
    // bare noun phrase with no verb for the epistemic rule to catch, and it
    // rendered under "Could pressure value" for four candidates.
    //
    // Naming the gap's SUBJECT is what makes this safe. "gap" alone is an
    // ordinary business word — "a widening gap between reported and adjusted
    // margin", "a funding gap opens in FY2027" — and treating every gap as an
    // evidence gap routed those away as well.
    "\\b(blocking|research|evidence|coverage|data)\\s+gaps?\\b",
    "\\bgaps?\\s+in\\s+(the\\s+)?(research|evidence|data|coverage|record)\\b",
  ].join("|"),
  "i",
);

/**
 * True when a statement's subject is the EVIDENCE rather than the business.
 *
 * Every `risks_or_gaps` item the live council produced for PNDORA and CFR is
 * one of these; none of its economic implications is.
 */
export function isEvidenceStatement(text: string): boolean {
  const value = (text || "").trim();
  if (!value) return false;
  if (EVIDENCE_SUBJECT_PHRASE.test(value)) return true;
  if (EPISTEMIC_CONSEQUENCE.test(value)) return true;
  return ABSENCE.test(value) && EVIDENCE_SUBJECT.test(value);
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

export interface SignalContext {
  /** The council agent that produced it. */
  agent: string;
  /**
   * Where it came from — the slot decides what it would be IF it survives the
   * evidence checks above.
   */
  slot:
    | "implication"
    | "chair_positive"
    | "chair_negative"
    | "chair_resilience"
    | "chair_fragility"
    | "risk_or_gap"
    | "company_risk"
    | "catalyst";
  /** The agent's own direction, for implications. */
  direction?: string;
}

/**
 * Classify one statement. Role first, wording second, slot last.
 *
 * Nothing here is a judgement about how GOOD a statement is — only about what
 * kind of statement it is, so it can be shown under the right heading.
 */
export function classifySignal(
  text: string,
  context: SignalContext,
): InvestorSignal {
  if (isRecordGapStatement(text)) return "technical_gap";
  if (isEvidenceStatement(text)) return "research_limitation";
  if (EVIDENCE_ROLE_AGENTS.has(context.agent)) return "research_limitation";

  switch (context.slot) {
    case "chair_positive":
      return "economic_support";
    case "chair_negative":
      return "economic_pressure";
    case "chair_resilience":
      return "resilience";
    case "chair_fragility":
      return "fragility";
    case "company_risk":
      return "company_risk";
    case "catalyst":
      return "catalyst";
    case "risk_or_gap":
      // A gap slot that is NOT about the evidence is a genuine unresolved
      // question about the business.
      return "investor_question";
    case "implication":
    default:
      if (context.direction === "supportive") return "economic_support";
      if (context.direction === "pressuring") return "economic_pressure";
      // "mixed" and "neutral" lean neither way. Forcing them into a column
      // would be this layer deciding what an agent declined to decide.
      return "investor_question";
  }
}

/** True when a signal belongs in the investment reading rather than confidence. */
export function isEconomicSignal(signal: InvestorSignal): boolean {
  return (
    signal === "economic_support" ||
    signal === "economic_pressure" ||
    signal === "resilience" ||
    signal === "fragility" ||
    signal === "catalyst" ||
    signal === "company_risk"
  );
}
