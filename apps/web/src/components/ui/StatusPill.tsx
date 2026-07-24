export type PillColor =
  | "gray"
  | "green"
  | "red"
  | "amber"
  | "blue"
  | "purple"
  | "cyan";

const STYLES: Record<PillColor, string> = {
  gray: "bg-white/[0.06] text-slate-300 border-white/15",
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-400/25",
  red: "bg-rose-500/15 text-rose-300 border-rose-400/25",
  amber: "bg-amber-500/15 text-amber-300 border-amber-400/25",
  blue: "bg-sky-500/15 text-sky-300 border-sky-400/25",
  purple: "bg-violet-500/15 text-violet-300 border-violet-400/25",
  cyan: "bg-cyan-500/15 text-cyan-300 border-cyan-400/25",
};

/**
 * Small translucent status pill used for badges, statuses and tags across the
 * dark UI. Purely presentational — it renders whatever label it is given and
 * never implies a BUY/SELL/HOLD/WATCH recommendation.
 */
export default function StatusPill({
  label,
  color = "gray",
  className = "",
  testId,
}: {
  label: string;
  color?: PillColor;
  className?: string;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${STYLES[color]} ${className}`.trim()}
    >
      {label}
    </span>
  );
}
