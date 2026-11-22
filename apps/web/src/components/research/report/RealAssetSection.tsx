/**
 * V3.20 slice 5.4 — "Real assets": capacity in the unit the source used, expansions,
 * utilisation and qualification certificates, each from a cited finding. A field with no
 * finding behind it reads "not disclosed". Nothing here produces markup from data.
 */

import type { ReadingGroup, RealAssetBlock } from "@/components/research/realAsset";
import { VerificationBadge } from "@/components/research/report/QualificationEvidenceSection";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";

const fmt = (n: number): string => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

function Group({ title, group, testId }: { title: string; group: ReadingGroup; testId: string }) {
  return (
    <div className="mt-2 min-w-0" data-testid={testId}>
      <div className="text-sm font-medium text-[color:var(--ib-ink)]">{title}</div>
      {group.state === "not_disclosed" ? (
        <div className={muted}>not disclosed</div>
      ) : (
        <ul>
          {group.readings.map((r, i) => (
            <li key={i} className="text-sm text-[color:var(--ib-ink-2)] [overflow-wrap:anywhere]">
              {fmt(r.value)} {r.unit}
              {r.asOf && <span className={muted}> · {r.asOf}</span>}
              {r.quote && <div className={muted}>“{r.quote}”</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RealAssetSection({ block }: { block: RealAssetBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="real-asset">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Real assets</h4>
      <p className={muted}>Asset type: {block.assetType.replaceAll("_", " ")}</p>
      <Group title="Capacity" group={block.capacity} testId="real-asset-capacity" />
      <Group title="Announced expansions" group={block.expansions} testId="real-asset-expansions" />
      <Group title="Utilisation" group={block.utilisation} testId="real-asset-utilisation" />
      <div className="mt-2 min-w-0" data-testid="real-asset-certifications">
        <div className="text-sm font-medium text-[color:var(--ib-ink)]">Qualifications and certificates</div>
        {block.certifications.state === "not_disclosed" ? (
          <div className={muted}>not disclosed</div>
        ) : (
          <ul>
            {block.certifications.items.map((c, i) => (
              <li key={i} className="text-sm text-[color:var(--ib-ink-2)] [overflow-wrap:anywhere]">
                {c.certification}
                {c.standard && <> · {c.standard}</>}
                {c.body && <> · {c.body}</>}
                <span className={muted}> · {c.date ?? "date not disclosed"}</span>
                {c.verification && <VerificationBadge state={c.verification.state} />}
                {c.quote && <div className={muted}>“{c.quote}”</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
      {block.replacementValue.state === "disclosed" && (
        <div className="mt-2" data-testid="real-asset-replacement">
          <div className="text-sm font-medium text-[color:var(--ib-ink)]">Replacement value (as stated by the source)</div>
          {block.replacementValue.quotes.map((q, i) => (
            <div key={i} className={muted}>
              “{q}”
            </div>
          ))}
        </div>
      )}
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
