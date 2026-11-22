/**
 * V3.21.4 (ADR-111) — reading the "Industry context" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/industry_data/context.industry_context_block
 *     → section(industry_and_market).industry_context
 *     {version, state, layer, focus, focus_label, country_note, note,
 *      countries[] of {iso3, name, basis},
 *      figures[] of {observation_id, source_id, source_name, dataset_key, series_id,
 *                    indicator_code, label, display_name, country, period, value, unit,
 *                    retrieved_at, vintage_at, source_ref, licence, attribution, role},
 *      changes[] of {series_id, country, label, years, from_period, from_value,
 *                    to_period, to_value, unit, change_pct, input_ids[], definition},
 *      missing[] of {country, source_id, series_id, label, reason},
 *      notes[], sources[] of {source_id, name, enabled, inert_reason}}
 *
 * A figure without a finite value is dropped here rather than shown as 0: the producer
 * never emits one, and the reader does not invent one either.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

const finite = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.map(str).filter((x): x is string => x !== null) : []);

const recs = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isRecord) : []);

export type IndustryFigure = {
  observationId: string | null;
  sourceId: string;
  sourceName: string | null;
  seriesId: string;
  label: string | null;
  displayName: string | null;
  country: string | null;
  period: string;
  value: number;
  unit: string;
  retrievedAt: string | null;
  vintageAt: string | null;
  sourceRef: string | null;
  licence: string | null;
  attribution: string | null;
};

export type IndustryChange = {
  seriesId: string;
  country: string | null;
  label: string | null;
  years: number;
  fromPeriod: string;
  toPeriod: string;
  changePct: number;
  definition: string | null;
};

export type IndustryMissing = {
  country: string | null;
  label: string | null;
  reason: string;
};

export type IndustryContextBlock = {
  state: string;
  layer: string | null;
  focusLabel: string | null;
  countries: { iso3: string; name: string; basis: string | null }[];
  countryNote: string | null;
  note: string | null;
  figures: IndustryFigure[];
  changes: IndustryChange[];
  missing: IndustryMissing[];
  notes: string[];
  sources: { sourceId: string; name: string | null; enabled: boolean; inertReason: string | null }[];
};

const STATES = new Set(["available", "no_data", "no_country"]);

export function readIndustryContextBlock(v: unknown): IndustryContextBlock | null {
  if (!isRecord(v) || typeof v.state !== "string" || !STATES.has(v.state)) return null;
  return {
    state: v.state,
    layer: str(v.layer),
    focusLabel: str(v.focus_label),
    countries: recs(v.countries)
      .map((c) => ({ iso3: str(c.iso3) ?? "", name: str(c.name) ?? str(c.iso3) ?? "", basis: str(c.basis) }))
      .filter((c) => c.iso3),
    countryNote: str(v.country_note),
    note: str(v.note),
    figures: recs(v.figures)
      .map((f): IndustryFigure | null => {
        const value = finite(f.value);
        const seriesId = str(f.series_id);
        const period = str(f.period);
        const unit = str(f.unit);
        const sourceId = str(f.source_id);
        if (value === null || !seriesId || !period || !unit || !sourceId) return null;
        return {
          observationId: str(f.observation_id),
          sourceId,
          sourceName: str(f.source_name),
          seriesId,
          label: str(f.label),
          displayName: str(f.display_name),
          country: str(f.country),
          period,
          value,
          unit,
          retrievedAt: str(f.retrieved_at),
          vintageAt: str(f.vintage_at),
          sourceRef: str(f.source_ref),
          licence: str(f.licence),
          attribution: str(f.attribution),
        };
      })
      .filter((f): f is IndustryFigure => f !== null),
    changes: recs(v.changes)
      .map((c): IndustryChange | null => {
        const pct = finite(c.change_pct);
        const years = finite(c.years);
        const seriesId = str(c.series_id);
        const fromPeriod = str(c.from_period);
        const toPeriod = str(c.to_period);
        if (pct === null || years === null || !seriesId || !fromPeriod || !toPeriod) return null;
        return {
          seriesId,
          country: str(c.country),
          label: str(c.label),
          years,
          fromPeriod,
          toPeriod,
          changePct: pct,
          definition: str(c.definition),
        };
      })
      .filter((c): c is IndustryChange => c !== null),
    missing: recs(v.missing)
      .map((m) => ({ country: str(m.country), label: str(m.label), reason: str(m.reason) ?? "" }))
      .filter((m) => m.reason),
    notes: strs(v.notes),
    sources: recs(v.sources)
      .map((s) => ({
        sourceId: str(s.source_id) ?? "",
        name: str(s.name),
        enabled: s.enabled === true,
        inertReason: str(s.inert_reason),
      }))
      .filter((s) => s.sourceId),
  };
}
