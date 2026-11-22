/**
 * V3.21 slice 21.7 (ADR-114) — "Independent research": the sponsored / independent
 * research providers with a coverage page for the company, with URL and date, the note
 * that sponsored research is paid for by the issuer, and absence stated as what was looked
 * at. Nothing here produces markup from data.
 */

import type { IndependentResearchBlock } from "@/components/research/independentResearch";
import { EXTERNAL_LINK_REL } from "@/components/research/webEvidence";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";

const day = (iso: string | null): string | null => (iso ? iso.slice(0, 10) : null);

const SEARCH_LABELS: Record<string, string> = {
  disabled: "not searched (web search is off)",
  throttled: "searched within the last 30 days",
  searched: "searched",
  unavailable: "not searched (search unavailable)",
  not_searchable: "not searched (name too generic to match)",
};

export function IndependentResearchSection({ block }: { block: IndependentResearchBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="independent-research" data-state={block.state}>
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Independent research coverage</h4>
      {block.providers.length > 0 ? (
        <ul className="mt-2 space-y-2 text-sm text-[color:var(--ib-ink-2)]">
          {block.providers.map((p) => (
            <li key={p.provider} data-testid="independent-research-provider">
              <span className="font-medium text-[color:var(--ib-ink)]">{p.name}</span>
              {p.latestDate && <span className={muted}> · latest {day(p.latestDate)}</span>}
              {p.note && <span className={muted}> · {p.note}</span>}
              <ul className="mt-1 space-y-0.5">
                {p.pages.map((page) => (
                  <li key={page.url} className="ib-breakable">
                    <a className="underline" href={page.url} rel={EXTERNAL_LINK_REL} target="_blank">
                      {page.title ?? page.url}
                    </a>
                    <span className={muted}>
                      {page.date ? ` · ${day(page.date)}` : ""}
                      {page.dateBasis ? ` (${page.dateBasis})` : ""}
                      {page.discoveredVia === "search" ? " · found by search" : ""}
                      {page.inLatestIndex === false ? " · no longer in the latest sitemap" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ) : null}
      {block.absence && (
        <p className="mt-2 text-sm text-[color:var(--ib-ink-2)]" data-testid="independent-research-absence">
          {block.absence}
        </p>
      )}
      {block.sponsoredNote && <p className={`mt-2 ${muted}`}>{block.sponsoredNote}</p>}
      <p className={`mt-1 ${muted}`}>
        Edison sitemap index
        {block.indexRefreshedAt ? ` refreshed ${day(block.indexRefreshedAt)}` : " never refreshed"}
        {block.searchProviders.length > 0 &&
          ` · ${block.searchProviders.join(", ")}: ${
            SEARCH_LABELS[block.searchState ?? ""] ?? "not searched"
          }${block.searchedAt ? ` (${day(block.searchedAt)})` : ""}`}
        {block.matchBasis ? ` · matched by ${block.matchBasis}` : ""}
      </p>
    </section>
  );
}
