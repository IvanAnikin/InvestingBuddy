/**
 * V3.20 slice 5.4 — reading the electricity "Real assets" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   electricity.real_asset.real_asset_block → section(operations).real_asset
 *     {asset_type, primary_layer, note,
 *      capacity / expansions / utilisation: {state, readings[] of
 *          {value, unit, unit_family?, matched?, finding_id, evidence_ids, as_of, quote}},
 *      certifications: {state, items[] of
 *          {certification, standard, body, date, date_state, finding_id, evidence_ids,
 *           as_of, quote, verification?}},
 *      replacement_value: {state, statements[] of {finding_id, quote, as_of}}}
 *
 * `state` is "disclosed" or "not_disclosed"; nothing is converted or estimated, and a
 * not_disclosed field is shown as such — never a placeholder value.
 *
 * V3.21 slice 21.5 (ADR-112): each certificate may carry
 *   certification.verification.certification_step → items[].verification
 *     {state: issuer_claim | third_party_corroborated | body_unrecognised | unverified,
 *      body_named, body: {name, country, stl_member, ...} | null, basis, corroboration[]}
 * Absent when the check is off, and on older reports.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type Disclosure = "disclosed" | "not_disclosed";

export type Reading = {
  value: number;
  unit: string;
  findingId: string | null;
  asOf: string | null;
  quote: string | null;
};

export type VerificationState =
  | "issuer_claim"
  | "third_party_corroborated"
  | "body_unrecognised"
  | "unverified";

export type CertificateVerification = {
  state: VerificationState;
  bodyName: string | null;
  stlMember: boolean;
  basis: string | null;
};

export type Certification = {
  certification: string;
  standard: string | null;
  body: string | null;
  date: string | null;
  findingId: string | null;
  quote: string | null;
  verification: CertificateVerification | null;
};

const VERIFICATION_STATES: readonly VerificationState[] = [
  "issuer_claim",
  "third_party_corroborated",
  "body_unrecognised",
  "unverified",
];

export function readVerificationState(v: unknown): VerificationState {
  return VERIFICATION_STATES.find((s) => s === v) ?? "unverified";
}

function readVerification(raw: unknown): CertificateVerification | null {
  if (!isRecord(raw)) return null;
  const v = raw;
  const body = isRecord(v.body) ? v.body : null;
  return {
    state: readVerificationState(v.state),
    bodyName: (body ? str(body.name) : null) ?? str(v.body_named),
    stlMember: body ? body.stl_member === true : false,
    basis: str(v.basis),
  };
}

export type ReadingGroup = { state: Disclosure; readings: Reading[] };

export type RealAssetBlock = {
  assetType: string;
  primaryLayer: string | null;
  capacity: ReadingGroup;
  expansions: ReadingGroup;
  utilisation: ReadingGroup;
  certifications: { state: Disclosure; items: Certification[] };
  replacementValue: { state: Disclosure; quotes: string[] };
  note: string | null;
};

const disclosure = (v: unknown): Disclosure => (v === "disclosed" ? "disclosed" : "not_disclosed");

function readReading(r: Record<string, unknown>): Reading | null {
  const value = num(r.value);
  const unit = str(r.unit);
  if (value === null || unit === null) return null;
  return { value, unit, findingId: str(r.finding_id), asOf: str(r.as_of), quote: str(r.quote) };
}

function readGroup(r: unknown): ReadingGroup {
  if (!isRecord(r)) return { state: "not_disclosed", readings: [] };
  const readings = records(r.readings)
    .map(readReading)
    .filter((x): x is Reading => x !== null);
  return { state: readings.length ? disclosure(r.state) : "not_disclosed", readings };
}

function readCertification(r: Record<string, unknown>): Certification | null {
  const certification = str(r.certification);
  if (certification === null) return null;
  return {
    certification,
    standard: str(r.standard),
    body: str(r.body),
    date: str(r.date),
    findingId: str(r.finding_id),
    quote: str(r.quote),
    verification: readVerification(r.verification),
  };
}

export function readRealAssetBlock(raw: unknown): RealAssetBlock | null {
  if (!isRecord(raw)) return null;
  const assetType = str(raw.asset_type);
  if (assetType === null) return null;
  const certs = isRecord(raw.certifications) ? raw.certifications : {};
  const items = records(certs.items)
    .map(readCertification)
    .filter((c): c is Certification => c !== null);
  const repl = isRecord(raw.replacement_value) ? raw.replacement_value : {};
  const quotes = records(repl.statements)
    .map((r) => str(r.quote))
    .filter((q): q is string => q !== null);
  return {
    assetType,
    primaryLayer: str(raw.primary_layer),
    capacity: readGroup(raw.capacity),
    expansions: readGroup(raw.expansions),
    utilisation: readGroup(raw.utilisation),
    certifications: { state: items.length ? disclosure(certs.state) : "not_disclosed", items },
    replacementValue: { state: quotes.length ? disclosure(repl.state) : "not_disclosed", quotes },
    note: str(raw.note),
  };
}
