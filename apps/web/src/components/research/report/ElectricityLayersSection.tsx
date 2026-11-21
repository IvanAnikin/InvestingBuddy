/**
 * V3.20 slice 5.2 — "Electricity layers": where in the electricity stack the company sits,
 * with the revenue share by layer when its segments allow it, and the quoted words each
 * layer rests on. Nothing here produces markup from data.
 */

import type { ElectricityLayersBlock, LayerRow } from "@/components/research/electricityLayers";
import { pct } from "@/components/research/electricityLayers";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";

const STATUS_TEXT: Record<ElectricityLayersBlock["status"], string> = {
  classified: "",
  excluded: "Outside the electricity mandate",
  not_electricity: "No electricity layer is supported by the held disclosure.",
  no_evidence: "No description or segment revenue is held for this company yet.",
};

const BASIS_TEXT: Record<string, string> = {
  segment_revenue: "largest share of reported segment revenue",
  text_anchors: "most supporting terms in the business description",
};

function LayerItem({ row, primary }: { row: LayerRow; primary: boolean }) {
  return (
    <li className="mt-2 min-w-0" data-testid="electricity-layer">
      <div className="text-sm text-[color:var(--ib-ink)]">
        <span className="font-medium">{row.displayName}</span>
        {primary && <span className={`ml-2 ${muted}`}>primary</span>}
        {row.revenueShare !== null && (
          <span className="ml-2">{pct(row.revenueShare)} of segment revenue</span>
        )}
      </div>
      {row.subSegments.length > 0 && (
        <div className={muted}>{row.subSegments.map((s) => s.replaceAll("_", " ")).join(", ")}</div>
      )}
      {row.segments.length > 0 && <div className={muted}>segments: {row.segments.join(", ")}</div>}
      {row.evidence.slice(0, 2).map((e, i) => (
        <blockquote key={i} className={`mt-1 border-l-2 border-[color:var(--ib-line)] pl-2 ${muted} [overflow-wrap:anywhere]`}>
          “{e.quote}”
          {e.url ? (
            <>
              {" "}
              <a className="underline" href={e.url} rel="noopener noreferrer" target="_blank">
                source
              </a>
            </>
          ) : (
            e.sourceKind && <> ({e.sourceKind.replaceAll("_", " ")})</>
          )}
          {e.asOf && <>, {e.asOf}</>}
        </blockquote>
      ))}
    </li>
  );
}

export function ElectricityLayersSection({ block }: { block: ElectricityLayersBlock | null }) {
  if (!block) return null;
  const rev = block.revenueByLayer;
  return (
    <section className="mt-6" data-testid="electricity-layers">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Electricity layers</h4>
      {block.status !== "classified" && (
        <p className="mt-1 max-w-2xl text-sm text-[color:var(--ib-ink-2)]" data-testid="electricity-layers-state">
          {STATUS_TEXT[block.status]}
          {block.exclusion && (
            <>
              {" "}
              ({block.exclusion.displayName ?? block.exclusion.rule}; disclosed power-related share{" "}
              {pct(block.exclusion.powerShare)})
            </>
          )}
        </p>
      )}
      {block.status === "classified" && (
        <>
          {block.primaryBasis && (
            <p className={`mt-1 ${muted}`}>
              Primary layer by {BASIS_TEXT[block.primaryBasis] ?? block.primaryBasis}
              {rev && rev.period && (
                <>
                  {" "}
                  ({rev.period}
                  {rev.currency ? `, ${rev.currency}` : ""}; {pct(rev.mappedShare)} of segment revenue mapped)
                </>
              )}
              .
            </p>
          )}
          {block.exclusion?.overridePassed && (
            <p className={muted}>
              Matched the {block.exclusion.displayName ?? block.exclusion.rule} exclusion, kept because power-related
              segments are {pct(block.exclusion.powerShare)} of revenue.
            </p>
          )}
          <ul>
            {block.layers.map((row) => (
              <LayerItem key={row.layer} row={row} primary={row.layer === block.primaryLayer} />
            ))}
          </ul>
        </>
      )}
      {block.status === "excluded" && block.candidateLayers.length > 0 && (
        <ul>
          {block.candidateLayers.map((row) => (
            <LayerItem key={row.layer} row={row} primary={false} />
          ))}
        </ul>
      )}
      {block.note && (
        <p className={`mt-2 max-w-2xl ${muted}`}>
          {block.note}
          {block.taxonomyVersion && <> Taxonomy {block.taxonomyVersion}.</>}
        </p>
      )}
    </section>
  );
}
