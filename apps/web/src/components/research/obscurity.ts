/**
 * V3.20 slice 6.3 — reading the "Obscurity" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   discovery.obscurity.obscurity_block → section(valuation_context).obscurity
 *     {version, score | null, components_available, components_total, note,
 *      components[] of {key, value, points | null, state, source, as_of, basis, note}}
 *
 * A component with state "not_available" was left out of the score, never counted as 0.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export type ObscurityComponent = {
  key: string;
  value: string | null;
  points: number | null;
  available: boolean;
  source: string | null;
  asOf: string | null;
  basis: string | null;
  note: string | null;
};

export type ObscurityBlock = {
  score: number | null;
  available: number | null;
  total: number | null;
  components: ObscurityComponent[];
  note: string | null;
};

const valueText = (v: unknown): string | null => {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return str(v);
};

function readComponent(r: Record<string, unknown>): ObscurityComponent | null {
  const key = str(r.key);
  if (key === null) return null;
  return {
    key,
    value: valueText(r.value),
    points: num(r.points),
    available: r.state === "available",
    source: str(r.source),
    asOf: str(r.as_of),
    basis: str(r.basis),
    note: str(r.note),
  };
}

export function readObscurityBlock(raw: unknown): ObscurityBlock | null {
  if (!isRecord(raw) || !Array.isArray(raw.components)) return null;
  return {
    score: num(raw.score),
    available: num(raw.components_available),
    total: num(raw.components_total),
    components: raw.components
      .filter(isRecord)
      .map(readComponent)
      .filter((c): c is ObscurityComponent => c !== null),
    note: str(raw.note),
  };
}
