// Telling a research ARGUMENT apart from a RECORD-completeness entry.
//
// The deterministic layer writes fixed, generated forms into slots a reader
// takes as analysis:
//
//   Blocking gap: Required field missing: identity.isin
//   Blocking gap: Required section absent: self_critique (field: …)
//   Legal entity verification not complete: identity.lei absent.
//   fundamentals.ebitda_mln
//
// They flow into `bear_case.key_unknowns` and, through it, into the committee
// chair's `primary_open_questions`. That is how a reader-facing report came to
// offer, as its single most important open question about a company, whether
// `identity.isin` had been sourced.
//
// The predicate matches the FORM the backend emits, never what a line means.
// It was checked against the live reports for four issuers (PNDORA, CFR, MRNA,
// MONC): it caught every one of their record entries and none of the 137 gaps
// their council agents wrote. Nothing is discarded — a matched entry is routed
// to the research-confidence section, which is what it actually describes.
//
// It lives in its own module because both the narrative reader
// (`reportView.ts`) and the investor-section reader (`reportSections.ts`) need
// it, and importing it from either would make those two circular.

const RECORD_GAP_PREFIX =
  /^\s*(blocking gap\b|required field missing\b|required section absent\b|missing required field\b|legal entity verification not complete\b)/i;

/** A bare dotted machine path, e.g. `self_critique.strongest_bear_case`. */
const BARE_MACHINE_PATH = /^[a-z0-9_]+(\.[a-z0-9_]+)+\.?$/i;

export function isRecordGapStatement(text: string): boolean {
  const value = text.trim();
  if (!value) return false;
  return RECORD_GAP_PREFIX.test(value) || BARE_MACHINE_PATH.test(value);
}

/** Split a list into analytical points and record-completeness entries. */
export function partitionRecordGaps(points: string[]): {
  analytical: string[];
  recordGaps: string[];
} {
  const analytical: string[] = [];
  const recordGaps: string[] = [];
  for (const point of points) {
    (isRecordGapStatement(point) ? recordGaps : analytical).push(point);
  }
  return { analytical, recordGaps };
}
