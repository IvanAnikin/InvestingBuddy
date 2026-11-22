/**
 * V3.21 slice 21.5 (ADR-112) — reading the "Qualification evidence" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   certification.verification.qualification_evidence_block
 *     → section(competitive_position).qualification_evidence
 *     {version, state: certificates_found | none_found, total, third_party_corroborated,
 *      share_third_party_corroborated (0–1 | null), states{state: count},
 *      certificates[] of {certification, standard, body, body_key, body_country,
 *          stl_member, date, date_state, state, finding_id, evidence_ids,
 *          corroborating_urls[]},
 *      by_body[] of {body, body_key, stl_member, count, dates[], states{}},
 *      any_stl_member, stl_members_named[],
 *      stl_register: {source, source_url, origin, retrieved_at, directory_issue,
 *          directory_date, members_count},
 *      web_search: {state: disabled | ran | not_needed, searches, max_searches},
 *      not_verifiable[], note}
 *
 * A certificate that is not corroborated is never shown as false: its state says what
 * could and could not be checked.
 */

import { type VerificationState, readVerificationState } from "./realAsset";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(str).filter((s): s is string => s !== null) : [];

export type QualificationCertificate = {
  certification: string | null;
  standard: string | null;
  body: string | null;
  bodyCountry: string | null;
  stlMember: boolean;
  date: string | null;
  state: VerificationState;
  corroboratingUrls: string[];
};

export type QualificationBodyRow = {
  body: string | null;
  stlMember: boolean;
  count: number;
  dates: string[];
};

export type StlRegister = {
  source: string | null;
  sourceUrl: string | null;
  origin: string | null;
  retrievedAt: string | null;
  directoryIssue: string | null;
  directoryDate: string | null;
};

export type QualificationEvidenceBlock = {
  total: number;
  corroborated: number;
  share: number | null;
  certificates: QualificationCertificate[];
  byBody: QualificationBodyRow[];
  anyStlMember: boolean;
  stlMembersNamed: string[];
  stlRegister: StlRegister | null;
  webSearch: { state: string; searches: number } | null;
  notVerifiable: string[];
  note: string | null;
};

function readCertificate(r: Record<string, unknown>): QualificationCertificate {
  return {
    certification: str(r.certification),
    standard: str(r.standard),
    body: str(r.body),
    bodyCountry: str(r.body_country),
    stlMember: r.stl_member === true,
    date: str(r.date),
    state: readVerificationState(r.state),
    corroboratingUrls: strings(r.corroborating_urls),
  };
}

function readBodyRow(r: Record<string, unknown>): QualificationBodyRow {
  return {
    body: str(r.body),
    stlMember: r.stl_member === true,
    count: num(r.count) ?? 0,
    dates: strings(r.dates),
  };
}

function readRegister(raw: unknown): StlRegister | null {
  if (!isRecord(raw)) return null;
  return {
    source: str(raw.source),
    sourceUrl: str(raw.source_url),
    origin: str(raw.origin),
    retrievedAt: str(raw.retrieved_at),
    directoryIssue: str(raw.directory_issue),
    directoryDate: str(raw.directory_date),
  };
}

export function readQualificationEvidenceBlock(raw: unknown): QualificationEvidenceBlock | null {
  if (!isRecord(raw)) return null;
  const state = str(raw.state);
  if (state !== "certificates_found" && state !== "none_found") return null;
  const web = isRecord(raw.web_search) ? raw.web_search : null;
  return {
    total: num(raw.total) ?? 0,
    corroborated: num(raw.third_party_corroborated) ?? 0,
    share: num(raw.share_third_party_corroborated),
    certificates: records(raw.certificates).map(readCertificate),
    byBody: records(raw.by_body).map(readBodyRow),
    anyStlMember: raw.any_stl_member === true,
    stlMembersNamed: strings(raw.stl_members_named),
    stlRegister: readRegister(raw.stl_register),
    webSearch: web ? { state: str(web.state) ?? "disabled", searches: num(web.searches) ?? 0 } : null,
    notVerifiable: strings(raw.not_verifiable),
    note: str(raw.note),
  };
}

export const VERIFICATION_LABELS: Record<VerificationState, string> = {
  third_party_corroborated: "Third-party corroborated",
  issuer_claim: "Company's own statement",
  body_unrecognised: "Body not recognised",
  unverified: "Not verified",
};
