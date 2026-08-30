// Deterministic presentation formatting.
//
// Anything rendered by a component that runs BOTH on the server and in the
// browser must format identically in both places, or React's hydration pass
// finds different text and throws (#418). The host-default overloads of
// `toLocaleDateString` / `toLocaleString` cannot satisfy that: they read the
// runtime's own locale and time zone, and the Azure Linux container and a
// reader's browser do not agree on either.
//
// The date case is the sharper one. Locale changes the SHAPE of the output,
// but the time zone can change the CALENDAR DAY: a timestamp recorded at
// 22:30 UTC is already "tomorrow" for a reader in Prague, so the server and
// the browser genuinely disagree about which date to print. Pinning both ends
// that.
//
// The pinned zone is UTC because these timestamps are records of when the
// server did something, and every other surface — the technical report view,
// the API payloads, the logs — already speaks UTC. Showing a reader's local
// day here would put a different date beside the same event elsewhere in the
// product. `isoTimestamp` exposes the exact instant for a `title`, so nothing
// is lost by choosing a canonical day.

const DISPLAY_LOCALE = "en-US";
const DISPLAY_TIME_ZONE = "UTC";

const DATE_FORMAT = new Intl.DateTimeFormat(DISPLAY_LOCALE, {
  timeZone: DISPLAY_TIME_ZONE,
  year: "numeric",
  month: "numeric",
  day: "numeric",
});

const DATE_TIME_FORMAT = new Intl.DateTimeFormat(DISPLAY_LOCALE, {
  timeZone: DISPLAY_TIME_ZONE,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * A calendar date, identical on every host. Returns "—" for an absent or
 * unparseable value rather than "Invalid Date".
 */
export function formatDate(
  value: string | number | Date | null | undefined,
): string {
  const d = toDate(value);
  return d ? DATE_FORMAT.format(d) : "—";
}

/** A date and time (UTC, 24-hour), identical on every host. */
export function formatDateTime(
  value: string | number | Date | null | undefined,
): string {
  const d = toDate(value);
  return d ? `${DATE_TIME_FORMAT.format(d)} UTC` : "—";
}

/**
 * The exact instant, for a `title` attribute beside a formatted date. The
 * displayed day is canonical; this is how a reader recovers the precise time
 * without the product having to guess their zone.
 */
export function isoTimestamp(
  value: string | number | Date | null | undefined,
): string | undefined {
  const d = toDate(value);
  return d ? d.toISOString() : undefined;
}

/**
 * A number with grouping separators, identical on every host. The host default
 * would render 32549 as "32,549" or "32 549" depending on the runtime locale —
 * the same hydration hazard as dates, one step quieter because it only bites
 * readers whose locale differs from the server's.
 */
export function formatNumber(
  value: number,
  options: Intl.NumberFormatOptions = { maximumFractionDigits: 2 },
): string {
  return new Intl.NumberFormat(DISPLAY_LOCALE, options).format(value);
}
