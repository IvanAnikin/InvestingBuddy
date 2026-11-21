/**
 * V3.20 slice 2.6 — Customers / Suppliers / Parent & group, and a small relationship
 * map: parents above, suppliers left, the company in the centre, customers right,
 * subsidiaries below. Every node links to its citation when the citation is an https
 * document; the excerpt is the node's title. Nothing here produces markup from data.
 */

import type {
  RelationshipBlock,
  RelationshipGroup,
  RelationshipRow,
  RelationshipState,
} from "@/components/research/relationships";
import { shareText } from "@/components/research/relationships";

const STATE_WORD: Record<RelationshipState, string> = {
  disclosed: "Disclosed",
  not_disclosed: "Not disclosed in the sources read",
  not_researched: "Not researched",
};

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const MAP_SIDE = 6;

function Cited({ row }: { row: RelationshipRow }) {
  const share = shareText(row);
  const body = (
    <>
      <span className="font-medium text-[color:var(--ib-ink)]">{row.name}</span>
      {share && <span className="ml-1 text-[color:var(--ib-ink-2)]">— {share}</span>}
    </>
  );
  const title = row.excerpt ?? undefined;
  return (
    <li className="text-sm leading-relaxed" data-testid="relationship-row">
      {row.sourceUrl ? (
        <a href={row.sourceUrl} target="_blank" rel="noopener noreferrer" title={title}
           className="underline decoration-[color:var(--ib-line)] underline-offset-2">
          {body}
        </a>
      ) : (
        <span title={title}>{body}</span>
      )}
      <span className={`ml-2 ${muted}`}>
        {[row.sourceName, row.asOf ? `as of ${row.asOf}` : null].filter(Boolean).join(", ")}
      </span>
      {row.excerpt && (
        <p className={`mt-0.5 max-w-2xl ${muted}`}>&ldquo;{row.excerpt}&rdquo;</p>
      )}
      {row.excerptWithheld && (
        <p className={`mt-0.5 ${muted}`}>The quoted excerpt is withheld; the source link stands.</p>
      )}
    </li>
  );
}

function Group({ title, group, testId }: { title: string; group: RelationshipGroup;
                                            testId: string }) {
  return (
    <div className="mt-4" data-testid={testId} data-state={group.state}>
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        {title}
        <span className={`ml-2 font-normal ${muted}`}>{STATE_WORD[group.state]}</span>
      </h4>
      {group.rows.length > 0 && <ul className="mt-1 space-y-1.5">
        {group.rows.map((row, i) => <Cited key={`${row.name}-${i}`} row={row} />)}
      </ul>}
      {group.note && <p className={`mt-1 max-w-2xl ${muted}`}>{group.note}</p>}
    </div>
  );
}

function Node({ row }: { row: RelationshipRow }) {
  const label = shareText(row) ? `${row.name} (${row.sharePct}%)` : row.name;
  const cls = "block truncate rounded border border-[color:var(--ib-line)] px-2 py-1 text-xs";
  return row.sourceUrl ? (
    <a href={row.sourceUrl} target="_blank" rel="noopener noreferrer"
       title={row.excerpt ?? row.name} className={cls}>{label}</a>
  ) : (
    <span title={row.excerpt ?? row.name} className={cls}>{label}</span>
  );
}

function Column({ rows, label }: { rows: RelationshipRow[]; label: string }) {
  return (
    <div className="min-w-0 space-y-1" aria-label={label}>
      <p className={muted}>{label}</p>
      {rows.slice(0, MAP_SIDE).map((row, i) => <Node key={`${row.name}-${i}`} row={row} />)}
      {rows.length > MAP_SIDE && <p className={muted}>+{rows.length - MAP_SIDE} more</p>}
    </div>
  );
}

function RelationshipMap({ block, company }: { block: RelationshipBlock; company: string }) {
  const p = block.parentAndGroup;
  const above = [...p.ultimateParents, ...p.directParents];
  const below = p.subsidiaries;
  if (!above.length && !below.length && !block.customers.rows.length
      && !block.suppliers.rows.length) return null;
  return (
    <figure className="mt-5" data-testid="relationship-map">
      <figcaption className={muted}>Relationship map — each box links to its source.</figcaption>
      {above.length > 0 && <div className="mx-auto mt-2 max-w-xs"><Column rows={above} label="Parents" /></div>}
      <div className="mt-2 grid grid-cols-3 items-center gap-3">
        <Column rows={block.suppliers.rows} label="Suppliers" />
        <div className="rounded border-2 border-[color:var(--ib-accent)] px-2 py-2 text-center text-sm font-semibold text-[color:var(--ib-ink)]">
          {company}
        </div>
        <Column rows={block.customers.rows} label="Customers" />
      </div>
      {below.length > 0 && <div className="mx-auto mt-2 max-w-xs"><Column rows={below} label="Subsidiaries" /></div>}
    </figure>
  );
}

export default function RelationshipSection({ block, company }: {
  block: RelationshipBlock | null; company: string;
}) {
  if (!block) return null;
  const p = block.parentAndGroup;
  const cc = block.customerConcentration;
  return (
    <section className="mt-6" data-testid="relationship-section">
      <h3 className="text-base font-semibold text-[color:var(--ib-ink)]">
        Customers, suppliers and group
      </h3>
      {cc && (
        <p className="mt-2 text-sm" data-testid="customer-concentration">
          Largest disclosed customer: <strong>{cc.value}%</strong> of revenue
          {cc.period ? ` (${cc.period})` : ""}.{" "}
          {cc.sourceUrl ? (
            <a href={cc.sourceUrl} target="_blank" rel="noopener noreferrer"
               className="underline underline-offset-2">{cc.sourceName ?? "source"}</a>
          ) : cc.sourceName}
          {cc.note && <span className={`block ${muted}`}>{cc.note}</span>}
        </p>
      )}
      <Group title="Customers" group={block.customers} testId="relationship-customers" />
      <Group title="Suppliers" group={block.suppliers} testId="relationship-suppliers" />
      <div className="mt-4" data-testid="relationship-parent-group" data-state={p.state}>
        <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
          Parent and group
          <span className={`ml-2 font-normal ${muted}`}>{STATE_WORD[p.state]}</span>
        </h4>
        {[["Ultimate parent", p.ultimateParents], ["Direct parent", p.directParents],
          ["Significant holders", p.significantHolders], ["Subsidiaries", p.subsidiaries],
          ["Minority holdings", p.holdings]].map(([label, list]) =>
          (list as RelationshipRow[]).length > 0 && (
            <div key={label as string} className="mt-2">
              <p className={muted}>{label as string}</p>
              <ul className="space-y-1">
                {(list as RelationshipRow[]).map((row, i) => <Cited key={`${row.name}-${i}`} row={row} />)}
              </ul>
            </div>
          ))}
        {p.reportingExceptions.map((e, i) => (
          <p key={i} className="mt-2 text-sm" data-testid="reporting-exception">
            No {e.relationshipType === "ultimate_parent_of" ? "ultimate" : "direct"} parent
            reported to GLEIF: {e.reasonLabel ?? e.reason}
            {e.sourceUrl && (<> (<a href={e.sourceUrl} target="_blank" rel="noopener noreferrer"
              className="underline underline-offset-2">GLEIF</a>)</>)}
            . This is not the same as having no parent.
          </p>
        ))}
        {p.note && <p className={`mt-1 max-w-2xl ${muted}`}>{p.note}</p>}
      </div>
      <RelationshipMap block={block} company={company} />
      {block.withheldForSafety > 0 && (
        <p className={`mt-2 ${muted}`}>
          {block.withheldForSafety} relationship row(s) are not shown (no citation, or wording
          the safety screen withholds).
        </p>
      )}
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
