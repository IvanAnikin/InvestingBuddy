/**
 * V3.20 slice 5.2 — reading the "Electricity layers" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   electricity_step.electricity_layers_section (classifier.classify + note)
 *     → section(business_model).electricity_layers
 *       {taxonomy_version, status, primary_layer, primary_basis, note,
 *        layers[] / candidate_layers[] of
 *          {layer, display_name, sub_segments[], revenue_share, text_anchor_count,
 *           segments[], evidence[] of {anchor, matched, quote, source_kind, source_url,
 *                                      as_of, label}},
 *        revenue_by_layer {axis, period, currency, total_revenue, shares{layer: share},
 *                          mapped_share, segments[]} | null,
 *        exclusion {rule, display_name, anchor, power_share, threshold,
 *                   override_passed} | null}
 *
 * Shares are fractions (0.6 = 60 %). A URL is a link only when it is https.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const share = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1 ? v : null;

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(str).filter((s): s is string => s !== null) : [];

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type LayerEvidence = {
  quote: string;
  sourceKind: string | null;
  url: string | null;
  asOf: string | null;
};

export type LayerRow = {
  layer: string;
  displayName: string;
  subSegments: string[];
  revenueShare: number | null;
  segments: string[];
  evidence: LayerEvidence[];
};

export type RevenueByLayer = {
  axis: string | null;
  period: string | null;
  currency: string | null;
  mappedShare: number | null;
  shares: Record<string, number>;
};

export type LayerExclusion = {
  rule: string;
  displayName: string | null;
  powerShare: number | null;
  overridePassed: boolean;
};

export type ElectricityLayersBlock = {
  status: "classified" | "excluded" | "not_electricity" | "no_evidence";
  taxonomyVersion: string | null;
  primaryLayer: string | null;
  primaryBasis: string | null;
  layers: LayerRow[];
  candidateLayers: LayerRow[];
  revenueByLayer: RevenueByLayer | null;
  exclusion: LayerExclusion | null;
  note: string | null;
};

function readEvidence(r: Record<string, unknown>): LayerEvidence | null {
  const quote = str(r.quote);
  if (quote === null) return null;
  return { quote, sourceKind: str(r.source_kind), url: httpsUrl(r.source_url), asOf: str(r.as_of) };
}

function readLayer(r: Record<string, unknown>): LayerRow | null {
  const layer = str(r.layer);
  if (layer === null) return null;
  return {
    layer,
    displayName: str(r.display_name) ?? layer,
    subSegments: strings(r.sub_segments),
    revenueShare: share(r.revenue_share),
    segments: strings(r.segments),
    evidence: records(r.evidence)
      .map(readEvidence)
      .filter((e): e is LayerEvidence => e !== null),
  };
}

function readRevenue(r: unknown): RevenueByLayer | null {
  if (!isRecord(r)) return null;
  const shares: Record<string, number> = {};
  if (isRecord(r.shares)) {
    for (const [k, v] of Object.entries(r.shares)) {
      const s = share(v);
      if (s !== null) shares[k] = s;
    }
  }
  return {
    axis: str(r.axis),
    period: str(r.period),
    currency: str(r.currency),
    mappedShare: share(r.mapped_share),
    shares,
  };
}

function readExclusion(r: unknown): LayerExclusion | null {
  if (!isRecord(r)) return null;
  const rule = str(r.rule);
  if (rule === null) return null;
  return {
    rule,
    displayName: str(r.display_name),
    powerShare: share(r.power_share),
    overridePassed: r.override_passed === true,
  };
}

const STATUSES = ["classified", "excluded", "not_electricity", "no_evidence"] as const;

export function readElectricityLayersBlock(raw: unknown): ElectricityLayersBlock | null {
  if (!isRecord(raw)) return null;
  const status = STATUSES.find((s) => s === raw.status);
  if (status === undefined) return null;
  const rows = (v: unknown) =>
    records(v)
      .map(readLayer)
      .filter((l): l is LayerRow => l !== null);
  return {
    status,
    taxonomyVersion: str(raw.taxonomy_version),
    primaryLayer: str(raw.primary_layer),
    primaryBasis: str(raw.primary_basis),
    layers: rows(raw.layers),
    candidateLayers: rows(raw.candidate_layers),
    revenueByLayer: readRevenue(raw.revenue_by_layer),
    exclusion: readExclusion(raw.exclusion),
    note: str(raw.note),
  };
}

export const pct = (s: number | null): string =>
  s === null ? "—" : `${(s * 100).toFixed(s * 100 < 10 ? 1 : 0)} %`;
