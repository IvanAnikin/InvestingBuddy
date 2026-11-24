/**
 * Vector charts for the PDFs (react-pdf SVG primitives). Each chart draws only the
 * points it is given; an empty series renders an honest "no data" panel instead.
 */

import {
  Defs,
  Line,
  LinearGradient,
  Path,
  Rect,
  Stop,
  Svg,
  Text as SvgText,
  Circle,
  G,
} from "@react-pdf/renderer";
import { C } from "./theme";
import { compact, labelIndexes, monthYear, niceTicks } from "./format";

const AXIS = { fill: C.ink3, fontSize: 6.5, fontFamily: "Inter" } as const;

function Empty({ width, height, text }: { width: number; height: number; text: string }) {
  return (
    <Svg width={width} height={height}>
      <Rect x={0} y={0} width={width} height={height} rx={4} fill={C.surface} />
      <SvgText x={width / 2} y={height / 2 + 2} textAnchor="middle" style={AXIS}>
        {text}
      </SvgText>
    </Svg>
  );
}

export type PricePoint = { d: string; c: number };

/** End-of-day closes as an area line; coloured by the sign of the change over the window. */
export function PriceChart({
  points,
  width,
  height,
  emptyText = "No price series available",
}: {
  points: PricePoint[];
  width: number;
  height: number;
  emptyText?: string;
}) {
  if (points.length < 2) return <Empty width={width} height={height} text={emptyText} />;
  const padL = 30, padR = 8, padT = 8, padB = 16;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const closes = points.map((p) => p.c);
  const ticks = niceTicks(Math.min(...closes), Math.max(...closes), 4);
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const x = (i: number) => padL + (i / (points.length - 1)) * w;
  const y = (v: number) => padT + h - ((v - lo) / (hi - lo || 1)) * h;
  const up = closes[closes.length - 1] >= closes[0];
  const stroke = up ? C.positive : C.negative;
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(p.c).toFixed(2)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(2)},${padT + h} L${padL},${padT + h} Z`;
  const last = points[points.length - 1];
  return (
    <Svg width={width} height={height}>
      <Defs>
        <LinearGradient id="pf" x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0" stopColor={stroke} stopOpacity={0.28} />
          <Stop offset="1" stopColor={stroke} stopOpacity={0} />
        </LinearGradient>
      </Defs>
      {ticks.map((t) => (
        <G key={`t${t}`}>
          <Line x1={padL} x2={padL + w} y1={y(t)} y2={y(t)} stroke={C.line} strokeWidth={0.5} />
          <SvgText x={padL - 4} y={y(t) + 2} textAnchor="end" style={AXIS}>
            {compact(t)}
          </SvgText>
        </G>
      ))}
      <Path d={area} fill="url(#pf)" />
      <Path d={line} stroke={stroke} strokeWidth={1.2} fill="none" />
      <Circle cx={x(points.length - 1)} cy={y(last.c)} r={2.2} fill={stroke} />
      {labelIndexes(points.length, 6).map((i) => (
        <SvgText key={`x${i}`} x={x(i)} y={height - 4} textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"} style={AXIS}>
          {monthYear(points[i].d)}
        </SvgText>
      ))}
    </Svg>
  );
}

export type FinancialBar = { label: string; revenue: number | null; netIncome: number | null };

/** Revenue and net income per fiscal year, grouped bars around a zero line. */
export function FinancialBars({ rows, width, height }: { rows: FinancialBar[]; width: number; height: number }) {
  const values = rows.flatMap((r) => [r.revenue, r.netIncome]).filter((v): v is number => v !== null);
  if (rows.length === 0 || values.length === 0) {
    return <Empty width={width} height={height} text="No annual filed figures available" />;
  }
  const padL = 30, padR = 6, padT = 12, padB = 16;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values), 4);
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const y = (v: number) => padT + h - ((v - lo) / (hi - lo || 1)) * h;
  const slot = w / rows.length;
  const bw = Math.min(16, slot * 0.3);
  return (
    <Svg width={width} height={height}>
      {ticks.map((t) => (
        <G key={`t${t}`}>
          <Line x1={padL} x2={padL + w} y1={y(t)} y2={y(t)} stroke={t === 0 ? C.lineStrong : C.line} strokeWidth={t === 0 ? 0.8 : 0.5} />
          <SvgText x={padL - 4} y={y(t) + 2} textAnchor="end" style={AXIS}>
            {compact(t, 1)}
          </SvgText>
        </G>
      ))}
      {rows.map((r, i) => {
        const cx = padL + slot * i + slot / 2;
        const bars: { v: number | null; x: number; fill: string }[] = [
          { v: r.revenue, x: cx - bw - 1, fill: C.accent },
          { v: r.netIncome, x: cx + 1, fill: (r.netIncome ?? 0) >= 0 ? C.positive : C.negative },
        ];
        return (
          <G key={r.label}>
            {bars.map((b, j) =>
              b.v === null ? null : (
                <Rect
                  key={j}
                  x={b.x}
                  y={Math.min(y(b.v), y(0))}
                  width={bw}
                  height={Math.max(0.8, Math.abs(y(b.v) - y(0)))}
                  rx={1.5}
                  fill={b.fill}
                />
              ),
            )}
            {r.revenue !== null && (
              <SvgText x={cx - bw / 2 - 1} y={y(r.revenue) - 3} textAnchor="middle" style={{ ...AXIS, fill: C.ink2 }}>
                {compact(r.revenue, 1)}
              </SvgText>
            )}
            <SvgText x={cx} y={height - 4} textAnchor="middle" style={AXIS}>
              {r.label}
            </SvgText>
          </G>
        );
      })}
    </Svg>
  );
}

export type CapBar = { label: string; value: number | null; tone: "eligible" | "excluded" | "unverified" };

const TONE = { eligible: C.positive, excluded: C.ink3, unverified: C.warning } as const;

/** Market capitalisation per candidate on one scale, with the size mandate as a dashed line. */
export function MarketCapBars({
  rows,
  mandate,
  width,
}: {
  rows: CapBar[];
  mandate: number | null;
  width: number;
}) {
  const known = rows.filter((r) => r.value !== null) as (CapBar & { value: number })[];
  if (known.length === 0) return <Empty width={width} height={40} text="No verified market capitalisation" />;
  const rowH = 13;
  const padL = 92, padR = 50, padT = 4;
  const height = padT + rows.length * rowH + 14;
  const w = width - padL - padR;
  // A company many times the mandate would flatten every other bar; the scale stops at
  // twice the mandate and a longer bar is marked as clipped (its value is still printed).
  const top = mandate ? Math.min(Math.max(...known.map((r) => r.value)), mandate * 2) : Math.max(...known.map((r) => r.value));
  const sx = (v: number) => padL + (Math.min(v, top) / (top || 1)) * w;
  return (
    <Svg width={width} height={height}>
      {rows.map((r, i) => {
        const yy = padT + i * rowH;
        const clipped = r.value !== null && r.value > top;
        return (
          <G key={`${r.label}${i}`}>
            <SvgText x={padL - 6} y={yy + 8} textAnchor="end" style={{ ...AXIS, fill: C.ink2 }}>
              {r.label}
            </SvgText>
            <Rect x={padL} y={yy + 2} width={w} height={8} rx={2} fill={C.surface} />
            {r.value !== null && (
              <Rect x={padL} y={yy + 2} width={Math.max(1, sx(r.value) - padL)} height={8} rx={2} fill={TONE[r.tone]} opacity={r.tone === "excluded" ? 0.55 : 0.9} />
            )}
            <SvgText x={padL + w + 4} y={yy + 8} style={{ ...AXIS, fill: r.value === null ? C.ink3 : C.ink2 }}>
              {r.value === null ? "unverified" : `${compact(r.value)}${clipped ? " ›" : ""}`}
            </SvgText>
          </G>
        );
      })}
      {mandate !== null && mandate <= top && (
        <G>
          <Line x1={sx(mandate)} x2={sx(mandate)} y1={padT} y2={padT + rows.length * rowH + 2} stroke={C.accent} strokeWidth={0.8} strokeDasharray="2,2" />
          <SvgText x={sx(mandate)} y={height - 2} textAnchor="middle" style={{ ...AXIS, fill: C.accent }}>
            {`mandate ${compact(mandate, 1)}`}
          </SvgText>
        </G>
      )}
    </Svg>
  );
}
