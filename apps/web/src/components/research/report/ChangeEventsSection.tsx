/**
 * V3.20 slice 6.2 — "Change events": dated events in the issuer's own disclosures and
 * SEC Form 4 insider purchase clusters, each with the document it came from. Nothing
 * here produces markup from data.
 */

import type { ChangeEventsBlock } from "@/components/research/changeEvents";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const fmt = (n: number | null, digits = 0): string =>
  n === null
    ? "—"
    : n.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });

export function ChangeEventsSection({ block }: { block: ChangeEventsBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="change-events">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Change events</h4>
      {block.events.length > 0 && (
        <div className="mt-2 max-w-full overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className={muted}>
              <tr>
                <th className={cell}>Date</th>
                <th className={cell}>Event</th>
                <th className={cell}>Disclosure</th>
              </tr>
            </thead>
            <tbody>
              {block.events.map((e, i) => (
                <tr key={`${e.triggerType}-${e.date}-${i}`}
                    className="border-t border-[color:var(--ib-line)]">
                  <td className={`${cell} whitespace-nowrap`}>{e.date ?? "—"}</td>
                  <td className={cell}>{e.label ?? e.triggerType}</td>
                  <td className={`${cell} [overflow-wrap:anywhere]`}>
                    {e.url ? (
                      <a href={e.url} target="_blank" rel="noopener noreferrer"
                         className="underline">
                        {e.title ?? "document"}
                      </a>
                    ) : (
                      (e.title ?? "—")
                    )}
                    {e.documentType && <span className={muted}> · {e.documentType}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {block.actions.length > 0 && (
        <div className="mt-3 max-w-full overflow-x-auto" data-testid="corporate-actions">
          <p className="text-xs font-semibold text-[color:var(--ib-ink-2)]">
            Corporate actions (major-matters reports)
          </p>
          <table className="mt-1 w-full min-w-[560px] text-left text-sm">
            <thead className={muted}>
              <tr>
                <th className={cell}>Filed</th>
                <th className={cell}>Action</th>
                <th className={cell}>Terms as stated</th>
              </tr>
            </thead>
            <tbody>
              {block.actions.map((a, i) => (
                <tr key={`${a.receiptNo ?? a.kind}-${i}`}
                    className="border-t border-[color:var(--ib-line)]">
                  <td className={`${cell} whitespace-nowrap`}>{a.date ?? "—"}</td>
                  <td className={`${cell} [overflow-wrap:anywhere]`}>
                    {a.url ? (
                      <a href={a.url} target="_blank" rel="noopener noreferrer"
                         className="underline">
                        {a.label ?? a.kind}
                      </a>
                    ) : (
                      (a.label ?? a.kind)
                    )}
                  </td>
                  <td className={`${cell} min-w-0 [overflow-wrap:anywhere] ${muted}`}>
                    {a.terms.length > 0
                      ? a.terms.map((t) => `${t.name}: ${t.value}`).join(" · ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {block.actionsSource && (
            <p className={`mt-1 ${muted}`}>
              {block.actionsSource}
              {block.actionsTruncated && " (bounded: not every filing was read)"}.
            </p>
          )}
        </div>
      )}
      {block.clusters.map((c, i) => (
        <div key={`cluster-${i}`} className="mt-3 text-sm text-[color:var(--ib-ink-2)]"
             data-testid="insider-cluster">
          <p>
            Insider cluster buying, {c.startDate ?? "?"} to {c.endDate ?? "?"}:{" "}
            {fmt(c.insiders)} insiders, {fmt(c.purchases)} open-market purchases,{" "}
            {fmt(c.shares)} shares
            {c.valueUsd !== null && <>, USD {fmt(c.valueUsd)}</>}.
          </p>
          {c.names.length > 0 && (
            <p className={muted}>
              {c.names.map((n) => (n.relationship ? `${n.name} (${n.relationship})` : n.name))
                .join("; ")}
            </p>
          )}
          {c.urls.length > 0 && (
            <p className={muted}>
              Form 4:{" "}
              {c.urls.map((u, j) => (
                <span key={u}>
                  {j > 0 && " · "}
                  <a href={u} target="_blank" rel="noopener noreferrer" className="underline">
                    {j + 1}
                  </a>
                </span>
              ))}
            </p>
          )}
          {c.rule && <p className={muted}>Rule: {c.rule}.</p>}
        </div>
      ))}
      {block.negated.length > 0 && (
        <p className={`mt-2 ${muted}`}>
          Not counted (the disclosure says the event ended or failed):{" "}
          {block.negated.map((e) => `${e.date ?? ""} ${e.title ?? ""}`.trim()).join("; ")}
        </p>
      )}
      <p className={`mt-2 max-w-2xl ${muted}`}>
        {block.windowDays !== null && <>Disclosures from the last {block.windowDays} days</>}
        {block.documentsConsidered !== null && <> ({block.documentsConsidered} considered)</>}
        {block.insiderSource && (
          <>
            ; {block.insiderSource}, {fmt(block.insiderFilingsRead)} filings read
            {block.insiderTruncated && " (bounded: older filings not read)"}
          </>
        )}
        . {block.note}
      </p>
    </section>
  );
}
