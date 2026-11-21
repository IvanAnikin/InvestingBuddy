/**
 * V3.20 slice 9.3 — the report's self-critique: the bear case (findings by label, unresolved
 * Red Team challenges in the Red Team's words), weakest links, data-quality warnings and
 * what the run did to look for the other side. Nothing here produces markup from data.
 */

import Surface from "@/components/product/Surface";
import type { SelfCritiqueBlock } from "@/components/research/selfCritique";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const list = "ml-4 list-disc space-y-1 text-sm text-[color:var(--ib-ink-2)]";

const words = (s: string | null): string => (s ? s.replace(/_/g, " ") : "");

export function SelfCritiqueSection({ block }: { block: SelfCritiqueBlock | null }) {
  if (!block) return null;
  return (
    <Surface as="section" className="p-6 sm:p-7" testId="self-critique">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">Self-critique</h2>
        <span className={muted} data-testid="self-critique-state">
          {block.complete ? "complete" : `incomplete — ${block.reason ?? "no bear case"}; not publication-ready`}
        </span>
      </div>

      <h3 className="mt-3 text-sm font-semibold text-[color:var(--ib-ink)]">The case against</h3>
      {block.bearCase.length === 0 ? (
        <p className={muted}>No counter-thesis, adverse or unresolved item is held.</p>
      ) : (
        <ul className={list}>
          {block.bearCase.map((b, i) => (
            <li key={`${b.kind}-${i}`} className="ib-breakable">
              {b.kind === "unresolved_challenge" ? (
                <>
                  Unresolved Red Team challenge{b.label && <> to {b.label}</>}
                  {b.weaknessClass && <span className={muted}> ({words(b.weaknessClass)})</span>}: {b.statement}
                </>
              ) : (
                <>
                  {b.kind === "counter_thesis_finding" ? "Counter-thesis finding" : "Adverse finding"}{" "}
                  <a href={b.label ? `#finding-${b.label}` : undefined} className="underline underline-offset-2">
                    {b.label}
                  </a>
                  {b.section && <span className={muted}> ({words(b.section)})</span>}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {block.bearCaseTotal > block.bearCase.length && (
        <p className={muted}>{block.bearCaseTotal - block.bearCase.length} further item(s) not shown.</p>
      )}
      {block.withheldForSafety > 0 && (
        <p className={muted}>{block.withheldForSafety} challenge(s) withheld by the safety scan.</p>
      )}

      {block.weakestLinks.length > 0 && (
        <>
          <h3 className="mt-3 text-sm font-semibold text-[color:var(--ib-ink)]">Weakest links</h3>
          <ul className={list}>
            {block.weakestLinks.map((w, i) => (
              <li key={i}>{w.text}</li>
            ))}
          </ul>
        </>
      )}
      {(block.dataQualityWarnings.length > 0 || block.platformEvidenceGaps > 0) && (
        <>
          <h3 className="mt-3 text-sm font-semibold text-[color:var(--ib-ink)]">Data-quality warnings</h3>
          <ul className={list}>
            {block.dataQualityWarnings.map((w, i) => (
              <li key={i}>
                {w.label} ({words(w.section)}): {w.reason}
              </li>
            ))}
            {block.platformEvidenceGaps > 0 && (
              <li>{block.platformEvidenceGaps} platform evidence gap(s): see the evidence-quality section.</li>
            )}
          </ul>
        </>
      )}
      {block.disconfirming && <p className={`${muted} mt-3`}>{block.disconfirming}</p>}
      {block.note && <p className={`${muted} mt-1`}>{block.note}</p>}
    </Surface>
  );
}
