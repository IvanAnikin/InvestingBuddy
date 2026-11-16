/**
 * V3.20 slice 9.2 — the computed research verdict (ADR-098): the label beside its
 * meaning, the eight pillar scores with what each rests on, the caps that held it back,
 * and the disclaimer. Nothing here produces markup from data.
 */

import Surface from "@/components/product/Surface";
import type { ResearchVerdictBlock } from "@/components/research/researchVerdict";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const body = "mt-1 text-sm text-[color:var(--ib-ink-2)]";

export const VERDICT_DISCLAIMER =
  "Computed research triage, private use only. Not investment advice and not an " +
  "instruction to trade.";

const evidenceText = (e: ResearchVerdictBlock["pillars"][number]["evidence"][number]): string =>
  e.kind === "finding"
    ? `${e.label ?? "finding"} (${e.direction ?? "—"}${e.quality === "thin" ? ", open-web only" : ""})`
    : `${e.signal ?? e.block ?? "signal"}${e.observed !== null ? ` = ${e.observed}` : ""}` +
      (e.delta ? ` (${e.delta > 0 ? "+" : ""}${e.delta})` : "");

export function ResearchVerdictSection({ block }: { block: ResearchVerdictBlock | null }) {
  if (!block) return null;
  return (
    <Surface as="section" className="p-6 sm:p-7" testId="research-verdict">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research verdict
        </h2>
        <span
          className="rounded-full border border-[color:var(--ib-line-strong)] px-2.5 py-0.5 font-mono text-xs text-[color:var(--ib-ink)]"
          data-testid="research-verdict-label"
        >
          {block.recommendation}
        </span>
        {block.compositeScore !== null && (
          <span className={muted}>
            composite {block.compositeScore} / 5 · confidence {block.confidence.band ?? "—"}
            {block.confidence.value !== null && ` (${block.confidence.value})`}
          </span>
        )}
      </div>
      {block.meaning && <p className={body}>{block.meaning}</p>}
      {block.rationale && <p className={`${muted} mt-1 ib-breakable`}>{block.rationale}</p>}

      <div className="mt-4 max-w-full overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm text-[color:var(--ib-ink-2)]">
          <thead className={muted}>
            <tr>
              <th className="py-1 pr-3 font-medium">Pillar</th>
              <th className="py-1 pr-3 font-medium">Weight</th>
              <th className="py-1 pr-3 font-medium">Score</th>
              <th className="py-1 font-medium">Rests on</th>
            </tr>
          </thead>
          <tbody>
            {block.pillars.map((p) => (
              <tr key={p.key} className="border-t border-[color:var(--ib-line)] align-top">
                <td className="py-1 pr-3">{p.label}</td>
                <td className="py-1 pr-3 font-mono text-xs">
                  {p.weight !== null ? `${Math.round(p.weight * 100)}%` : "—"}
                </td>
                <td className="py-1 pr-3 font-mono">{p.scored ? p.score : "—"}</td>
                <td className="ib-breakable py-1 text-xs">
                  {p.scored ? (
                    p.evidence.map((e, i) => (
                      <span key={i}>
                        {i > 0 && "; "}
                        {e.sourceUrl ? (
                          <a
                            href={e.sourceUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline underline-offset-2"
                          >
                            {evidenceText(e)}
                          </a>
                        ) : (
                          evidenceText(e)
                        )}
                      </span>
                    ))
                  ) : (
                    <span className={muted}>not scored: no cited supportive or adverse evidence</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {block.caps.length > 0 && (
        <div className="mt-3">
          <p className={muted}>Held back by:</p>
          <ul className="ml-4 list-disc text-xs text-[color:var(--ib-ink-2)]">
            {block.caps.map((c) => (
              <li key={c.key}>{c.reason}</li>
            ))}
          </ul>
        </div>
      )}
      {block.watchlistTriggers.length > 0 && (
        <div className="mt-3">
          <p className={muted}>Evidence that would change it:</p>
          <ul className="ml-4 list-disc text-xs text-[color:var(--ib-ink-2)]">
            {block.watchlistTriggers.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}
      <p className={`${muted} mt-3`}>
        {block.note ?? ""} Rules version {block.version ?? "—"}.
      </p>
      <p className={`${muted} mt-1`} data-testid="research-verdict-disclaimer">
        {VERDICT_DISCLAIMER}
      </p>
    </Surface>
  );
}
