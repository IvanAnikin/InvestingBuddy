/**
 * V3.21.6 — reading the "Trade-press mentions" block (ADR-113).
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   pipeline.change_events_step.trade_press_mentions_section
 *     → section(growth_and_catalysts).trade_press_mentions
 *     {state: "found", window_days, as_of, note,
 *      mentions[] of {outlet, date, title, url, layers[], language, source_tier,
 *                     retrieved_at}}
 *
 * Every string stays a plain text node; a URL is a link only when it is https.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

export type TradePressMention = {
  outlet: string | null;
  date: string | null;
  title: string;
  url: string | null;
  layers: string[];
  language: string | null;
};

export type TradePressMentionsBlock = {
  windowDays: number | null;
  asOf: string | null;
  mentions: TradePressMention[];
  note: string | null;
};

/** "utilities_grid_services" → "utilities grid services". */
export const layerLabel = (key: string): string => key.replace(/_/g, " ");

export function readTradePressMentionsBlock(raw: unknown): TradePressMentionsBlock | null {
  if (!isRecord(raw) || raw.state !== "found" || !Array.isArray(raw.mentions)) return null;
  const mentions: TradePressMention[] = [];
  for (const m of raw.mentions) {
    if (!isRecord(m)) continue;
    const title = str(m.title);
    if (title === null) continue;
    mentions.push({
      outlet: str(m.outlet),
      date: str(m.date),
      title,
      url: httpsUrl(m.url),
      layers: Array.isArray(m.layers)
        ? m.layers.filter((x): x is string => typeof x === "string" && x.trim() !== "")
        : [],
      language: str(m.language),
    });
  }
  if (mentions.length === 0) return null;
  return {
    windowDays:
      typeof raw.window_days === "number" && Number.isFinite(raw.window_days)
        ? raw.window_days
        : null,
    asOf: str(raw.as_of),
    mentions,
    note: str(raw.note),
  };
}
