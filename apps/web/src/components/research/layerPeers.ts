/**
 * V3.20 slice 8.2 — reading the "Layer peers" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   valuation.layer_peers.layer_peers_block → section(valuation_context).layer_peers
 *     {layer, size_band[], peer_count, subject_ev_to_ebit | null, median_ev_to_ebit | null,
 *      median_basis | null, median_reason | null, note,
 *      peers[] of {ticker, exchange, as_of, market_cap_usd, market_cap_as_of, ev_to_ebit,
 *                  ev_currency, obscurity_score, size_ratio, comparable_size,
 *                  research_record_id}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export type LayerPeer = {
  ticker: string | null;
  exchange: string | null;
  asOf: string | null;
  marketCapUsd: number | null;
  evToEbit: number | null;
  evCurrency: string | null;
  obscurity: number | null;
  sizeRatio: number | null;
  comparableSize: boolean | null;
};

export type LayerPeersBlock = {
  layer: string | null;
  peers: LayerPeer[];
  subjectEvToEbit: number | null;
  median: number | null;
  medianBasis: string | null;
  medianReason: string | null;
  note: string | null;
};

export function readLayerPeersBlock(raw: unknown): LayerPeersBlock | null {
  if (!isRecord(raw) || !Array.isArray(raw.peers)) return null;
  const peers = raw.peers.filter(isRecord).map((r) => ({
    ticker: str(r.ticker),
    exchange: str(r.exchange),
    asOf: str(r.as_of) ?? str(r.market_cap_as_of),
    marketCapUsd: num(r.market_cap_usd),
    evToEbit: num(r.ev_to_ebit),
    evCurrency: str(r.ev_currency),
    obscurity: num(r.obscurity_score),
    sizeRatio: num(r.size_ratio),
    comparableSize: typeof r.comparable_size === "boolean" ? r.comparable_size : null,
  }));
  if (peers.length === 0) return null;
  return {
    layer: str(raw.layer),
    peers,
    subjectEvToEbit: num(raw.subject_ev_to_ebit),
    median: num(raw.median_ev_to_ebit),
    medianBasis: str(raw.median_basis),
    medianReason: str(raw.median_reason),
    note: str(raw.note),
  };
}
