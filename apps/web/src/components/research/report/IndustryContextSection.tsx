/**
 * V3.21.4 (ADR-111) — "Industry context": dated, attributed statistics about the
 * electricity market of the company's listing / domicile country, chosen by its
 * electricity layer. Every figure shows its source, series id, period and retrieval
 * time; absent series are listed with the reason, never shown as zero. Nothing here
 * produces markup from data.
 */

import type { IndustryContextBlock } from "@/components/research/industryContext";
import { formatDate, formatNumber } from "@/lib/format";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const safeHref = (u: string | null): string | undefined => (u && /^https:\/\//.test(u) ? u : undefined);

const STATE_TEXT: Record<string, string> = {
  available: "",
  no_data: "No market statistic for these countries is in the platform's store yet.",
  no_country: "No listing or domicile country could be mapped to market statistics.",
};

export function IndustryContextSection({ block }: { block: IndustryContextBlock | null }) {
  if (!block) return null;
  const attributions = Array.from(new Set(block.figures.map((f) => f.attribution).filter((a): a is string => !!a)));
  return (
    <section className="mt-6" data-testid="industry-context">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Industry context
        {block.focusLabel && <span className={muted}> · {block.focusLabel}</span>}
      </h4>
      {block.countries.length > 0 && (
        <p className={muted}>
          {block.countries.map((c) => `${c.name}${c.basis ? ` (${c.basis})` : ""}`).join(", ")}
        </p>
      )}
      {STATE_TEXT[block.state] && (
        <p className="mt-1 text-sm text-[color:var(--ib-ink-2)]" data-testid="industry-context-state">
          {STATE_TEXT[block.state]}
        </p>
      )}
      {block.figures.length > 0 && (
        <div className="mt-2 overflow-x-auto">
          <table className="min-w-full text-sm text-[color:var(--ib-ink-2)]">
            <thead>
              <tr className={muted}>
                <th className="pr-3 text-left font-normal">Statistic</th>
                <th className="pr-3 text-left font-normal">Period</th>
                <th className="pr-3 text-right font-normal">Value</th>
                <th className="pr-3 text-left font-normal">Source · series</th>
                <th className="text-left font-normal">Retrieved</th>
              </tr>
            </thead>
            <tbody>
              {block.figures.map((f) => (
                <tr key={`${f.sourceId}-${f.seriesId}-${f.period}`} data-testid="industry-context-figure">
                  <td className="pr-3 align-top">
                    {f.label ?? f.displayName ?? f.seriesId}
                    {f.country && <span className={muted}> · {f.country}</span>}
                  </td>
                  <td className="pr-3 align-top">{f.period}</td>
                  <td className="pr-3 text-right align-top tabular-nums">
                    {formatNumber(f.value)} <span className={muted}>{f.unit}</span>
                  </td>
                  <td className="ib-breakable pr-3 align-top">
                    <a href={safeHref(f.sourceRef)} className="underline underline-offset-2" rel="noopener noreferrer" target="_blank">
                      {f.sourceName ?? f.sourceId}
                    </a>
                    <div className={muted}>{f.seriesId}</div>
                  </td>
                  <td className="align-top">
                    <span className={muted}>{formatDate(f.retrievedAt)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {block.changes.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-[color:var(--ib-ink-2)]">
          {block.changes.map((c) => (
            <li key={`${c.seriesId}-${c.years}`} data-testid="industry-context-change">
              {c.label ?? c.seriesId}
              {c.country && <span className={muted}> · {c.country}</span>}: {c.changePct > 0 ? "+" : ""}
              {formatNumber(c.changePct, { maximumFractionDigits: 1 })}% from {c.fromPeriod} to {c.toPeriod}
            </li>
          ))}
          {block.changes[0]?.definition && <li className={muted}>{block.changes[0].definition}.</li>}
        </ul>
      )}
      {block.missing.length > 0 && (
        <div className="mt-2" data-testid="industry-context-missing">
          <p className={muted}>Not shown:</p>
          <ul className={`ml-4 list-disc ${muted}`}>
            {block.missing.map((m, i) => (
              <li key={i}>
                {m.label ?? "statistic"}
                {m.country && ` (${m.country})`}: {m.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {block.notes.map((n, i) => (
        <p key={i} className={muted}>{n}</p>
      ))}
      {attributions.length > 0 && <p className={`${muted} mt-2`}>Sources: {attributions.join("; ")}.</p>}
      {block.countryNote && <p className={muted}>{block.countryNote}</p>}
      {block.note && <p className={muted}>{block.note}</p>}
    </section>
  );
}
