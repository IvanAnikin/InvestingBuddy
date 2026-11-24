/**
 * Pure formatting and chart-scale helpers for the PDFs. No figure is produced here:
 * every function only prints or scales a number it is given, and a missing number
 * prints as an em dash, never as zero.
 */

export const DASH = "—";

/** 980061482 → "0.98bn"; 212404000 → "212.4m"; 15.6 → "15.60". */
export function compact(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(digits)}tn`;
  if (a >= 1e9) return `${sign}${(a / 1e9).toFixed(digits)}bn`;
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(1)}m`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(1)}k`;
  return `${sign}${a.toFixed(2)}`;
}

export function money(v: number | null | undefined, ccy?: string | null, digits = 2): string {
  const n = compact(v, digits);
  return n === DASH ? DASH : ccy ? `${ccy} ${n}` : n;
}

export function price(v: number | null | undefined, ccy?: string | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const n = v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return ccy ? `${n} ${ccy}` : n;
}

export function pct(v: number | null | undefined, digits = 1, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH;
  const s = signed && v > 0 ? "+" : "";
  return `${s}${v.toFixed(digits)}%`;
}

/** Growth of `newer` over `older` in percent, or null when either is missing or older ≤ 0. */
export function growthPct(newer: number | null | undefined, older: number | null | undefined): number | null {
  if (newer == null || older == null || !Number.isFinite(newer) || !Number.isFinite(older) || older <= 0) {
    return null;
  }
  return ((newer - older) / older) * 100;
}

/** "2026-11-24" → "24 Nov 2026"; anything unparseable is returned as given. */
export function day(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export function monthYear(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
}

/** Round step (1, 2, 2.5, 5 × 10^n) giving about `count` intervals across [min, max]. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => st >= raw) ?? 10 * mag;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let t = start; t <= max + step * 0.5; t += step) ticks.push(Number(t.toPrecision(12)));
  return ticks;
}

/** Evenly spaced indexes into a series of `length` for `n` axis labels (first and last kept). */
export function labelIndexes(length: number, n: number): number[] {
  if (length <= 0) return [];
  if (length <= n) return Array.from({ length }, (_, i) => i);
  const out = new Set<number>();
  for (let i = 0; i < n; i++) out.add(Math.round((i * (length - 1)) / (n - 1)));
  return [...out];
}

/** Clip a string to `max` characters with an ellipsis. */
export function clip(text: string | null | undefined, max: number): string {
  if (!text) return "";
  const t = text.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).trimEnd()}…`;
}

/** A filesystem-safe file name part. */
export function fileSlug(text: string | null | undefined): string {
  return (text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "export";
}
