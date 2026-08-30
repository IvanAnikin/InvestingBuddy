import type { TrendSeriesView } from "./reportView";

/**
 * A single reconstructed multi-period series.
 *
 * Rules this chart follows, because a chart is the easiest place in a financial
 * product to imply something untrue:
 *
 *  - It draws only periods the report actually holds. Nothing is interpolated,
 *    extrapolated or projected.
 *  - A series with one period is not a trend, and is rendered as a single
 *    labelled value rather than a flat line that looks like stability.
 *  - A series the backend marked NOT comparable is not drawn at all — the
 *    values are listed instead, with the reason. A line between two figures
 *    that are not comparable is a false statement in visual form.
 *  - Scope (Group vs segment), period type and unit are stated on the series,
 *    every time.
 *
 * Pure SVG, rendered on the server: no charting library, no client JavaScript.
 */

const VIEW_W = 320;
const VIEW_H = 72;
const PAD_Y = 8;

function formatValue(value: number | null): string {
  if (value === null) return "n/a";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function humanizeMetric(metric: string): string {
  return metric
    .replace(/[._]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function TrendChart({ series }: { series: TrendSeriesView }) {
  const points = series.points;
  const values = points
    .map((p) => p.value)
    .filter((v): v is number => v !== null);

  const drawable = series.comparable && values.length >= 2;

  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;

  const coords = points.map((p, i) => {
    const x = points.length === 1 ? VIEW_W / 2 : (i / (points.length - 1)) * VIEW_W;
    const y =
      p.value === null
        ? null
        : VIEW_H - PAD_Y - ((p.value - min) / span) * (VIEW_H - PAD_Y * 2);
    return { x, y, ...p };
  });

  const path = coords
    .filter((c) => c.y !== null)
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${(c.y as number).toFixed(1)}`)
    .join(" ");

  return (
    <li className="rounded-lg border border-[color:var(--ib-line)] p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-[color:var(--ib-ink)]">
          {humanizeMetric(series.metric)}
        </span>
        {series.scope && (
          <span className="rounded border border-[color:var(--ib-line)] px-1.5 py-0.5 text-[10px] text-[color:var(--ib-ink-3)]">
            {series.scope}
          </span>
        )}
        {series.unit && (
          <span className="text-xs text-[color:var(--ib-ink-3)]">
            {series.unit}
          </span>
        )}
        {series.periodType && series.periodType !== "annual" && (
          <span className="rounded border border-amber-400/25 px-1.5 py-0.5 text-[10px] text-amber-200">
            {series.periodType}
          </span>
        )}
      </div>

      {drawable && (
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          preserveAspectRatio="none"
          className="mt-3 h-16 w-full"
          aria-hidden="true"
          focusable="false"
        >
          <path
            d={path}
            fill="none"
            stroke="var(--ib-accent)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
          {coords
            .filter((c) => c.y !== null)
            .map((c) => (
              <circle
                key={c.period}
                cx={c.x}
                cy={c.y as number}
                r="2.5"
                fill="var(--ib-accent)"
              />
            ))}
        </svg>
      )}

      {/* The values are always visible text: they are the accessible content of
          the chart above, and the only content when a series is not drawable. */}
      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1.5">
        {points.map((p) => (
          <div key={p.period}>
            <dt className="text-[10px] uppercase tracking-wide text-[color:var(--ib-ink-3)]">
              {p.period}
            </dt>
            <dd className="font-mono text-sm text-[color:var(--ib-ink-2)]">
              {formatValue(p.value)}
            </dd>
          </div>
        ))}
      </dl>

      {!series.comparable && (
        <p className="mt-2.5 text-xs leading-relaxed text-amber-300/90">
          Not charted — these periods are not comparable
          {series.comparabilityReasons.length > 0
            ? `: ${series.comparabilityReasons.join("; ")}`
            : "."}
        </p>
      )}
      {series.comparable && points.length === 1 && (
        <p className="mt-2.5 text-xs text-[color:var(--ib-ink-3)]">
          One period only — not enough to show a trend.
        </p>
      )}
      {series.missingPeriods.length > 0 && (
        <p className="mt-2 text-xs text-amber-300/90">
          Missing: {series.missingPeriods.join(", ")}
        </p>
      )}
    </li>
  );
}
