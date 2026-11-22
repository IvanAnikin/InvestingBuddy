/**
 * V3.20 slice 6.3 — "Obscurity": how few people are looking at the company, with every
 * component, its source and the points it contributed. Nothing here produces markup from
 * data.
 */

import type { ObscurityBlock } from "@/components/research/obscurity";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const LABELS: Record<string, string> = {
  analyst_count: "Analysts with estimates",
  passive_ownership_pct: "Held by US funds (% of shares)",
  theme_fund_holders: "Theme-named fund holders",
  english_news_volume: "English news, last 3 months",
  sector_mis_tag: "Sector label misses the business",
  primary_disclosure_language: "Filing language",
  independent_research_coverage: "Research providers covering it",
};

export function ObscuritySection({ block }: { block: ObscurityBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="obscurity">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Obscurity</h4>
      <p className="mt-1 text-sm text-[color:var(--ib-ink-2)]" data-testid="obscurity-score">
        {block.score !== null ? `${block.score} / 100` : "Not scored"}
        {block.available !== null && block.total !== null && (
          <span className={muted}>
            {" "}
            · from {block.available} of {block.total} components
          </span>
        )}
      </p>
      <div className="mt-2 max-w-full overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className={muted}>
            <tr>
              <th className={cell}>Component</th>
              <th className={cell}>Value</th>
              <th className={cell}>Points</th>
              <th className={cell}>Source</th>
            </tr>
          </thead>
          <tbody>
            {block.components.map((c) => (
              <tr key={c.key} className="border-t border-[color:var(--ib-line)]">
                <td className={cell}>{LABELS[c.key] ?? c.key}</td>
                <td className={cell}>{c.available ? (c.value ?? "—") : <span className={muted}>not available</span>}</td>
                <td className={cell}>{c.points !== null ? c.points.toFixed(2) : "—"}</td>
                <td className={`${cell} ${muted}`}>
                  {c.source ?? "—"}
                  {c.asOf && <>, {c.asOf}</>}
                  {c.note && <div>{c.note}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
