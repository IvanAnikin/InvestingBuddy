import type { ReactNode } from "react";
import Surface from "@/components/product/Surface";
import { sourceTierWord } from "@/components/research/reportSections";
import type {
  ChangeSection,
  CommodityRow,
  DomainSection,
  EvidenceSection,
  LabelledSentence,
  PeerRow,
  ProfessionalEditor,
  ProfessionalFinding,
  ProfessionalResearch,
  ProfessionalSection,
  ProfessionalSectionStatus,
  SynthesisSection,
} from "@/components/research/v3Research";
import { formatNumber } from "@/lib/format";

/**
 * The professional research report — the V3 ledger, read as a report (V3.18.8).
 *
 * WHAT IT REPLACES
 * ================
 * On a report that carries it, this is the research: the V1 council's narrative
 * sections are not rendered beside it. Thirteen sections, in the order the producer
 * declares, each built from the findings its research domain owns.
 *
 * THE RULES IT RENDERS, NOT INVENTS
 * =================================
 * A finding appears ONCE, in the section that owns it, under a label ("F3") with an
 * anchor. Everything else — the synthesis, the section leads, "what would change the
 * thesis", the business risks — refers to it by that label and links to it, and never
 * restates it. A null figure prints as "—", never as 0. And the evidence section keeps
 * two lists apart that a reader must never confuse: business risks are findings about
 * the COMPANY; platform evidence gaps are what this RESEARCH could not acquire.
 */

const STATUS: Record<ProfessionalSectionStatus, { word: string; tone: string }> = {
  evidenced: {
    word: "Evidenced",
    tone: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  },
  partially_evidenced: {
    word: "Partially evidenced",
    tone: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  },
  not_established: {
    word: "Not established",
    tone: "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink-3)]",
  },
  no_thesis: {
    word: "No thesis",
    tone: "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink-3)]",
  },
};

const CONTRACT_STATUS_WORDS: Record<string, string> = {
  satisfied: "settled",
  partial: "partly settled",
  unmet: "not settled",
};
const CONTRACT_STATUS_ORDER = Object.keys(CONTRACT_STATUS_WORDS);

/** A commodity series is data, not a filing: the site-wide tier words say "filing". */
const SERIES_TIER_WORDS: Record<string, string> = {
  T2_regulator_or_gov: "Government or regulator data",
};

const UNRESOLVED_REASON_WORDS: Record<string, string> = {
  not_acquired: "evidence not acquired by the platform",
};

const KNOWLEDGE_STATE_WORDS: Record<string, string> = {
  not_acquired_by_platform: "Not acquired by the platform",
  not_disclosed_by_issuer: "Not disclosed by the issuer, per cited evidence",
  not_applicable: "Not applicable",
};

const EDITOR_REJECTION_WORDS: Record<string, string> = {
  cites_no_finding: "cited no finding",
  cites_unknown_finding: "cited a finding that does not exist",
  figure_not_in_cited_findings: "used a figure not in the cited findings",
  safety_terms: "failed the safety check",
  absence_asserted_as_issuer_fact: "stated an absence as a fact about the company",
  lead_cites_another_section: "cited another section's findings",
};

function words(raw: string | null | undefined): string {
  return raw ? raw.replace(/_+/g, " ").trim() : "";
}

function findingAnchor(label: string): string {
  return `finding-${label.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

const DASH = "—";

function fmt(value: number | null, options?: Intl.NumberFormatOptions): string {
  return value === null ? DASH : formatNumber(value, options);
}

function pct(value: number | null, signed = false): string {
  if (value === null) return DASH;
  return `${formatNumber(value, {
    maximumFractionDigits: 1,
    ...(signed ? { signDisplay: "exceptZero" } : {}),
  })}%`;
}

const kicker =
  "text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]";

function StatusBadge({
  status,
  testId,
}: {
  status: ProfessionalSectionStatus | null;
  testId?: string;
}) {
  if (!status) return null;
  const { word, tone } = STATUS[status];
  return (
    <span
      data-testid={testId}
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {word}
    </span>
  );
}

/**
 * A reference to a finding. A link when the finding is on this page; plain text when
 * it is not (a section keeps at most fourteen), so no label ever points at nothing.
 */
function FindingRef({ label, anchored }: { label: string; anchored: Set<string> }) {
  const base =
    "inline-flex items-center rounded-sm border px-1.5 py-px font-mono text-[11px] leading-4";
  if (!anchored.has(label)) {
    return (
      <span
        className={`${base} border-[color:var(--ib-line)] text-[color:var(--ib-ink-3)]`}
        title="This finding is in the technical record"
        data-testid="finding-ref-unlinked"
      >
        {label}
      </span>
    );
  }
  return (
    <a
      href={`#${findingAnchor(label)}`}
      aria-label={`Finding ${label}`}
      className={`${base} border-[color:var(--ib-line-strong)] text-[color:var(--ib-accent)] hover:bg-[color:var(--ib-accent-quiet)]`}
      data-testid="finding-ref"
    >
      {label}
    </a>
  );
}

function FindingRefs({
  labels,
  anchored,
}: {
  labels: string[];
  anchored: Set<string>;
}) {
  if (labels.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-baseline">
      {labels.map((label) => (
        <FindingRef key={label} label={label} anchored={anchored} />
      ))}
    </span>
  );
}

function Sentence({
  sentence,
  anchored,
}: {
  sentence: LabelledSentence;
  anchored: Set<string>;
}) {
  return (
    <>
      <span className="ib-breakable">{sentence.text}</span>{" "}
      <FindingRefs labels={sentence.labels} anchored={anchored} />
    </>
  );
}

function Lead({
  lead,
  anchored,
}: {
  lead: LabelledSentence | null;
  anchored: Set<string>;
}) {
  if (!lead) return null;
  return (
    <p
      className="mt-3 max-w-3xl text-[15px] leading-relaxed text-[color:var(--ib-ink)]"
      data-testid="professional-lead"
    >
      <Sentence sentence={lead} anchored={anchored} />
    </p>
  );
}

function Bullets({ items }: { items: ReactNode[] }) {
  return (
    <ul className="mt-2 space-y-1.5">
      {items.map((item, i) => (
        <li
          key={i}
          className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
        >
          <span
            aria-hidden="true"
            className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
          />
          <span className="ib-breakable">{item}</span>
        </li>
      ))}
    </ul>
  );
}

/* ── Sections ───────────────────────────────────────────────────────────────────── */

function authorLine(author: string | null, editor: ProfessionalEditor | null): string {
  if (author === "editor_model_verified") {
    return "Written by the editor model; every sentence verified against the cited findings.";
  }
  if (author === "deterministic") {
    return editor?.used && editor.fallback
      ? "Assembled deterministically from the findings. The editor model's draft did not pass verification, so it was not used."
      : "Assembled deterministically from the findings.";
  }
  return "How this synthesis was written is not recorded.";
}

function SynthesisBody({
  section,
  editor,
  anchored,
}: {
  section: SynthesisSection;
  editor: ProfessionalEditor | null;
  anchored: Set<string>;
}) {
  const rejected = editor?.used
    ? Object.entries(editor.rejectedByReason).filter(([, n]) => n > 0)
    : [];
  const rejectedTotal = rejected.reduce((sum, [, n]) => sum + n, 0);
  return (
    <>
      <p
        className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
        data-testid="professional-synthesis-author"
      >
        {authorLine(section.author, editor)}
      </p>
      <Lead lead={section.lead} anchored={anchored} />
      {section.sentences.length === 0 ? (
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          No finding was established, so there is nothing to synthesise yet.
        </p>
      ) : (
        <ul className="mt-4 space-y-2.5" data-testid="professional-synthesis">
          {section.sentences.map((sentence, i) => (
            <li
              key={i}
              className="flex gap-3 text-[15px] leading-relaxed text-[color:var(--ib-ink)]"
            >
              <span
                aria-hidden="true"
                className="mt-3 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
              />
              <span className="min-w-0">
                <Sentence sentence={sentence} anchored={anchored} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {rejectedTotal > 0 && (
        <p className="ib-breakable mt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {rejectedTotal} editor-written sentence{rejectedTotal === 1 ? "" : "s"}{" "}
          failed verification and {rejectedTotal === 1 ? "was" : "were"} dropped (
          {rejected
            .map(([reason, n]) => `${EDITOR_REJECTION_WORDS[reason] ?? words(reason)}: ${n}`)
            .join("; ")}
          ).
        </p>
      )}
    </>
  );
}

function FindingItem({
  finding,
  sectionTitle,
  anchored,
  labelsById,
}: {
  finding: ProfessionalFinding;
  sectionTitle: string;
  anchored: Set<string>;
  labelsById: Record<string, string>;
}) {
  const sources = finding.sourceKinds.map(words).join(", ");
  const meta = [
    // The owning domain, when it is not simply this section's own name.
    finding.domainLabel &&
    finding.domainLabel.toLowerCase() !== sectionTitle.toLowerCase()
      ? finding.domainLabel
      : null,
    finding.periodKey,
    finding.confidence && `${words(finding.confidence)} confidence`,
    finding.direction && words(finding.direction),
    sources && `source: ${sources}`,
  ].filter(Boolean);
  const references = finding.references
    .map((id) => labelsById[id])
    .filter((label): label is string => Boolean(label));

  return (
    <li
      id={finding.label ? findingAnchor(finding.label) : undefined}
      className="-mx-2 scroll-mt-24 rounded-md px-2 py-1.5 transition-colors target:bg-[color:var(--ib-accent-quiet)]"
      data-testid="professional-finding"
    >
      <div className="flex gap-3">
        <span
          className="mt-0.5 inline-flex h-5 shrink-0 items-center rounded-sm bg-[color:var(--ib-accent-quiet)] px-1.5 font-mono text-[11px] font-medium text-[color:var(--ib-accent)]"
          data-testid="finding-label"
        >
          {finding.label ?? DASH}
        </span>
        <div className="min-w-0">
          <p className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {finding.statement}
          </p>
          {meta.length > 0 && (
            <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
              {meta.join(" · ")}
            </p>
          )}
          <p className="ib-breakable mt-0.5 font-mono text-[11px] leading-relaxed text-[color:var(--ib-ink-3)]">
            {finding.evidenceIds.length > 0
              ? `Evidence ${finding.evidenceIds.join(", ")}`
              : "No evidence id recorded"}
            {finding.calculationIds.length > 0 &&
              ` · calculation ${finding.calculationIds.join(", ")}`}
          </p>
          {references.length > 0 && (
            <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]">
              Refers to <FindingRefs labels={references} anchored={anchored} />
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

/** A table that scrolls inside its own container, so the page never scrolls sideways. */
function TableFrame({
  caption,
  description,
  testId,
  children,
}: {
  caption: string;
  description: string;
  testId: string;
  children: ReactNode;
}) {
  return (
    <div
      className="mt-6 max-w-full overflow-x-auto rounded-lg border border-[color:var(--ib-line)]"
      role="region"
      aria-label={caption}
      // Focusable so a keyboard user can scroll a table wider than the screen.
      tabIndex={0}
      data-testid={testId}
    >
      <table className="w-full min-w-[36rem] text-left text-sm">
        <caption className="px-4 pt-3 text-left">
          <span className="block text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            {caption}
          </span>
          <span className="mt-1 block text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            {description}
          </span>
        </caption>
        {children}
      </table>
    </div>
  );
}

const th =
  "px-4 py-2.5 text-xs font-medium whitespace-nowrap text-[color:var(--ib-ink-3)]";
const td = "px-4 py-2.5 whitespace-nowrap font-mono text-[color:var(--ib-ink)]";

function CommodityTable({ rows }: { rows: CommodityRow[] }) {
  return (
    <TableFrame
      caption="Commodity prices"
      description="Latest reported value and its change, from the named series. Historical only — nothing here is projected."
      testId="commodity-table"
    >
      <thead>
        <tr className="border-b border-[color:var(--ib-line)]">
          <th scope="col" className={th}>Commodity</th>
          <th scope="col" className={th}>Period</th>
          <th scope="col" className={`${th} text-right`}>Latest value</th>
          <th scope="col" className={th}>Unit</th>
          <th scope="col" className={`${th} text-right`}>12-month change</th>
          <th scope="col" className={`${th} text-right`}>36-month change</th>
          <th scope="col" className={th}>Source</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-[color:var(--ib-line)]">
        {rows.map((row, i) => (
          <tr key={`${row.commodity ?? row.displayName}-${i}`}>
            <th scope="row" className="px-4 py-2.5 font-normal text-[color:var(--ib-ink-2)]">
              <span className="block whitespace-nowrap">
                {words(row.commodity) || row.displayName}
              </span>
              {row.displayName && row.commodity && (
                <span className="block text-xs text-[color:var(--ib-ink-3)]">
                  {row.displayName}
                </span>
              )}
            </th>
            <td className={td}>{row.latestPeriod ?? DASH}</td>
            <td className={`${td} text-right`}>{fmt(row.latestValue)}</td>
            <td className="px-4 py-2.5 whitespace-nowrap text-[color:var(--ib-ink-2)]">
              {row.unit ?? DASH}
            </td>
            <td className={`${td} text-right`}>{pct(row.change12mPct, true)}</td>
            <td className={`${td} text-right`}>{pct(row.change36mPct, true)}</td>
            <td className="px-4 py-2.5 text-xs text-[color:var(--ib-ink-3)]">
              <span className="block whitespace-nowrap" title={row.sourceTier ?? undefined}>
                {(row.sourceTier && SERIES_TIER_WORDS[row.sourceTier]) ??
                  sourceTierWord(row.sourceTier) ??
                  DASH}
              </span>
              {row.evidenceId && (
                <span className="block whitespace-nowrap font-mono">{row.evidenceId}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </TableFrame>
  );
}

function PeerTable({ rows }: { rows: PeerRow[] }) {
  return (
    <TableFrame
      caption="Peer comparison"
      description="Reported figures per company for the period shown. A dash is a figure that was not sourced — not a zero."
      testId="peer-table"
    >
      <thead>
        <tr className="border-b border-[color:var(--ib-line)]">
          <th scope="col" className={th}>Company</th>
          <th scope="col" className={th}>Period</th>
          <th scope="col" className={`${th} text-right`}>Revenue (USD m)</th>
          <th scope="col" className={`${th} text-right`}>Operating margin</th>
          <th scope="col" className={`${th} text-right`}>Net margin</th>
          <th scope="col" className={`${th} text-right`}>Cash conversion</th>
          <th scope="col" className={`${th} text-right`}>Capex / operating cash flow</th>
          <th scope="col" className={`${th} text-right`}>Net debt (USD m)</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-[color:var(--ib-line)]">
        {rows.map((row, i) => (
          <tr
            key={`${row.ticker}-${i}`}
            className={row.isSubject ? "bg-[color:var(--ib-accent-quiet)]" : undefined}
            data-subject={row.isSubject ? "true" : undefined}
            data-testid="peer-row"
          >
            <th
              scope="row"
              className="px-4 py-2.5 font-mono font-medium whitespace-nowrap text-[color:var(--ib-ink)]"
            >
              {row.ticker}
              {row.isSubject && (
                <span className="ml-2 font-sans text-[11px] font-normal text-[color:var(--ib-accent)]">
                  (subject)
                </span>
              )}
            </th>
            <td className={td}>{row.period ?? DASH}</td>
            <td className={`${td} text-right`}>
              {fmt(row.revenueUsdM, { maximumFractionDigits: 0 })}
            </td>
            <td className={`${td} text-right`}>{pct(row.operatingMarginPct)}</td>
            <td className={`${td} text-right`}>{pct(row.netMarginPct)}</td>
            <td className={`${td} text-right`}>
              {row.cashConversion === null
                ? DASH
                : `${formatNumber(row.cashConversion, { maximumFractionDigits: 2 })}×`}
            </td>
            <td className={`${td} text-right`}>{pct(row.capexToOcfPct)}</td>
            <td className={`${td} text-right`}>
              {fmt(row.netDebtUsdM, { maximumFractionDigits: 0 })}
            </td>
          </tr>
        ))}
      </tbody>
    </TableFrame>
  );
}

function ThesisFit({
  section,
  anchored,
}: {
  section: DomainSection;
  anchored: Set<string>;
}) {
  const { thesis, dimensions, sizeFit } = section;
  if (!thesis && dimensions.length === 0 && !sizeFit && !section.note) return null;
  return (
    <div className="mt-4 space-y-4" data-testid="thesis-fit">
      {section.note && (
        <p className="max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {section.note}
        </p>
      )}
      {thesis && (
        <div>
          <p className={kicker}>Originating thesis</p>
          {thesis.text && (
            <p className="ib-breakable mt-2 max-w-3xl border-l-2 border-[color:var(--ib-line-strong)] pl-4 text-sm leading-relaxed text-[color:var(--ib-ink)]">
              {thesis.text}
            </p>
          )}
          {(thesis.matchedTheme || thesis.relevanceReason || thesis.councilRationale) && (
            <div className="mt-2 max-w-3xl space-y-1 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
              {thesis.matchedTheme && (
                <p className="ib-breakable">Matched theme: {words(thesis.matchedTheme)}</p>
              )}
              {thesis.relevanceReason && (
                <p className="ib-breakable">Why it was matched: {thesis.relevanceReason}</p>
              )}
              {thesis.councilRationale && (
                <p className="ib-breakable">
                  Discovery council: {thesis.councilRationale}
                </p>
              )}
            </div>
          )}
        </div>
      )}
      {dimensions.length > 0 && (
        <div>
          <p className={kicker}>By thesis dimension</p>
          <ul className="mt-2 divide-y divide-[color:var(--ib-line)]" data-testid="thesis-dimensions">
            {dimensions.map((d) => (
              <li
                key={d.questionKey ?? d.dimension}
                className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2 text-sm text-[color:var(--ib-ink-2)]"
              >
                <span className="ib-breakable">{words(d.dimension)}</span>
                <StatusBadge status={d.status} />
                <FindingRefs labels={d.findingLabels} anchored={anchored} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {sizeFit && (
        <p
          className="ib-breakable max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
          data-testid="size-fit"
        >
          <span className="text-[color:var(--ib-ink-3)]">Size: </span>
          {[
            sizeFit.requested.length > 0 &&
              `requested ${sizeFit.requested.map(words).join(" or ")}`,
            sizeFit.marketCapUsd !== null &&
              `market capitalisation $${formatNumber(sizeFit.marketCapUsd, {
                notation: "compact",
                maximumFractionDigits: 1,
              })}`,
            sizeFit.fits === true
              ? "fits the requested size"
              : sizeFit.fits === false
                ? "does not fit the requested size"
                : "fit could not be determined",
          ]
            .filter(Boolean)
            .join(" · ")}
          .{sizeFit.note && ` ${sizeFit.note}`}
        </p>
      )}
    </div>
  );
}

function OpenQuestionsBlock({ section }: { section: DomainSection }) {
  const open = section.openQuestions;
  if (open.length === 0) return null;
  const asked = section.questionsAsked;
  return (
    <details className="group mt-5" data-testid="professional-open-questions">
      <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
        {asked !== null && asked >= open.length
          ? `${open.length} of ${asked} question${asked === 1 ? "" : "s"} not settled`
          : `${open.length} open question${open.length === 1 ? "" : "s"}`}
        <span aria-hidden="true" className="ml-2 no-underline group-open:hidden">
          ▸
        </span>
        <span aria-hidden="true" className="ml-2 hidden no-underline group-open:inline">
          ▾
        </span>
      </summary>
      <ul className="mt-3 space-y-3">
        {open.map((q, i) => (
          <li
            key={q.questionKey ?? i}
            className="border-l-2 border-[color:var(--ib-line)] pl-4"
            data-testid="professional-open-question"
          >
            <p className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
              {q.text ?? words(q.questionKey)}
            </p>
            {(q.contractStatus || q.unresolvedReason) && (
              <p className="ib-breakable mt-0.5 text-xs text-[color:var(--ib-ink-3)]">
                {[
                  q.contractStatus &&
                    (CONTRACT_STATUS_WORDS[q.contractStatus] ?? words(q.contractStatus)),
                  q.unresolvedReason &&
                    (UNRESOLVED_REASON_WORDS[q.unresolvedReason] ??
                      words(q.unresolvedReason)),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
            {q.whyItMatters && (
              <p className="ib-breakable mt-1 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                Why it matters: {q.whyItMatters}
              </p>
            )}
            {q.missing.length > 0 && (
              <div className="mt-1.5">
                <p className="text-xs text-[color:var(--ib-ink-3)]">
                  Evidence that would settle it:
                </p>
                <Bullets items={q.missing} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function DomainBody({
  section,
  anchored,
  labelsById,
}: {
  section: DomainSection;
  anchored: Set<string>;
  labelsById: Record<string, string>;
}) {
  return (
    <>
      <Lead lead={section.lead} anchored={anchored} />
      <ThesisFit section={section} anchored={anchored} />

      {section.findings.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {section.findings.map((finding, i) => (
            <FindingItem
              key={finding.label ?? finding.findingId ?? i}
              finding={finding}
              sectionTitle={section.title}
              anchored={anchored}
              labelsById={labelsById}
            />
          ))}
        </ul>
      ) : section.status !== "no_thesis" ? (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {section.questionsAsked === 0
            ? "No research question was assigned to this section in this run, so nothing here was established. That is a limit of this research, not a statement about the company."
            : "No finding was established for this section. That is a limit of this research, not a statement about the company."}
        </p>
      ) : null}

      {section.findingsOmitted > 0 && (
        <p className="mt-3 text-xs text-[color:var(--ib-ink-3)]">
          {section.findingsOmitted} further finding
          {section.findingsOmitted === 1 ? " belongs" : "s belong"} to this section and{" "}
          {section.findingsOmitted === 1 ? "is" : "are"} in the technical record.
        </p>
      )}

      {section.commodityTable.length > 0 && <CommodityTable rows={section.commodityTable} />}
      {section.peerTable.length > 0 && <PeerTable rows={section.peerTable} />}

      <OpenQuestionsBlock section={section} />
    </>
  );
}

function ChangeBody({
  section,
  anchored,
  questionText,
}: {
  section: ChangeSection;
  anchored: Set<string>;
  questionText: Map<string, string>;
}) {
  const refs = (labels: string[], none: string) =>
    labels.length > 0 ? (
      <p className="mt-2">
        <FindingRefs labels={labels} anchored={anchored} />
      </p>
    ) : (
      <p className="mt-2 text-sm text-[color:var(--ib-ink-3)]">{none}</p>
    );
  return (
    <>
      <Lead lead={section.lead} anchored={anchored} />
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
        This section points to findings rather than restating them — follow a label to
        read the finding in the section that owns it.
      </p>
      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <div data-testid="change-counter-thesis">
          <p className={kicker}>Counter-thesis findings</p>
          {refs(section.counterThesisLabels, "No counter-thesis finding was established.")}
        </div>
        <div data-testid="change-catalysts">
          <p className={kicker}>Catalyst findings</p>
          {refs(section.catalystLabels, "No catalyst finding was established.")}
        </div>
      </div>
      {section.unestablishedThesisDimensions.length > 0 && (
        <div className="mt-5">
          <p className={kicker}>Thesis dimensions not established</p>
          <Bullets items={section.unestablishedThesisDimensions.map(words)} />
        </div>
      )}
      {section.evidenceToSettle.length > 0 && (
        <div className="mt-5" data-testid="change-evidence-to-settle">
          <p className={kicker}>Evidence that would settle open questions</p>
          <ul className="mt-2 space-y-3">
            {section.evidenceToSettle.map((q) => (
              <li key={q.questionKey}>
                <p className="ib-breakable text-sm text-[color:var(--ib-ink-2)]">
                  {questionText.get(q.questionKey) ?? words(q.questionKey)}
                </p>
                <Bullets items={q.missing} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function EvidenceBody({
  section,
  anchored,
}: {
  section: EvidenceSection;
  anchored: Set<string>;
}) {
  const diversity = section.sourceDiversity;
  const kinds = diversity
    ? Array.from(
        new Set([
          ...Object.keys(diversity.acquiredBySourceKind),
          ...Object.keys(diversity.findingsCitingKind),
        ]),
      ).sort()
    : [];
  // Settled first, then partly, then not; a status this page does not know goes last.
  const rank = (status: string) => {
    const i = CONTRACT_STATUS_ORDER.indexOf(status);
    return i === -1 ? CONTRACT_STATUS_ORDER.length : i;
  };
  const outcomes = Object.entries(section.questionsByContractStatus).sort(
    ([a], [b]) => rank(a) - rank(b),
  );
  const reasons = Object.entries(section.unresolvedByReason);

  return (
    <>
      <Lead lead={section.lead} anchored={anchored} />

      {diversity && (
        <div className="mt-4" data-testid="professional-source-diversity">
          <p className={kicker}>Source diversity</p>
          <p className="mt-2 text-sm text-[color:var(--ib-ink-2)]">
            {diversity.acquiredDistinctSources === null
              ? "The number of distinct sources acquired is not recorded."
              : `${diversity.acquiredDistinctSources} distinct source${diversity.acquiredDistinctSources === 1 ? "" : "s"} acquired.`}
          </p>
          {kinds.length > 0 && (
            <Bullets
              items={kinds.map((kind) => {
                const acquired = diversity.acquiredBySourceKind[kind] ?? 0;
                const cited = diversity.findingsCitingKind[kind] ?? 0;
                return `${words(kind)} — ${acquired} acquired, cited by ${cited} finding${cited === 1 ? "" : "s"}`;
              })}
            />
          )}
          {diversity.explanation && (
            <p className="ib-breakable mt-2 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
              {diversity.explanation}
            </p>
          )}
        </div>
      )}

      {(outcomes.length > 0 || reasons.length > 0) && (
        <div className="mt-5" data-testid="professional-question-outcomes">
          <p className={kicker}>Research questions</p>
          {outcomes.length > 0 && (
            <p className="mt-2 text-sm text-[color:var(--ib-ink-2)]">
              {outcomes
                .map(([status, n]) => `${n} ${CONTRACT_STATUS_WORDS[status] ?? words(status)}`)
                .join(" · ")}
            </p>
          )}
          {reasons.length > 0 && (
            <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
              Unresolved because:{" "}
              {reasons
                .map(([reason, n]) => `${UNRESOLVED_REASON_WORDS[reason] ?? words(reason)} (${n})`)
                .join(", ")}
            </p>
          )}
        </div>
      )}

      {/* The distinction this section exists for. Two blocks, two headings, two
          visual treatments: one is about the company, the other about this research. */}
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <section
          className="rounded-lg border border-[color:var(--ib-line-strong)] p-4"
          data-testid="professional-business-risks"
          aria-labelledby="professional-business-risks-heading"
        >
          <h3
            id="professional-business-risks-heading"
            className="text-sm font-semibold text-[color:var(--ib-ink)]"
          >
            Business risks
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Findings about the company, each resting on cited evidence.
          </p>
          {section.businessRiskLabels.length > 0 ? (
            <p className="mt-3">
              <FindingRefs labels={section.businessRiskLabels} anchored={anchored} />
            </p>
          ) : (
            <p className="mt-3 text-sm text-[color:var(--ib-ink-3)]">
              No business-risk finding was established in this run.
            </p>
          )}
        </section>

        <section
          className="rounded-lg border border-dashed border-[color:var(--ib-line-strong)] bg-[color:var(--ib-surface)] p-4"
          data-testid="professional-platform-gaps"
          aria-labelledby="professional-platform-gaps-heading"
        >
          <h3
            id="professional-platform-gaps-heading"
            className="text-sm font-semibold text-[color:var(--ib-ink-2)]"
          >
            Limits of this research — platform evidence gaps
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            What the platform&apos;s research could not acquire. These are limits of the
            research, not facts about the company.
          </p>
          {section.platformEvidenceGaps.length > 0 ? (
            <ul className="mt-3 space-y-2.5">
              {section.platformEvidenceGaps.map((gap, i) => (
                <li key={i} data-testid="platform-evidence-gap">
                  <p className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                    {gap.description}
                  </p>
                  {gap.knowledgeState && (
                    <p className="text-xs text-[color:var(--ib-ink-3)]">
                      {KNOWLEDGE_STATE_WORDS[gap.knowledgeState] ?? words(gap.knowledgeState)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-sm text-[color:var(--ib-ink-3)]">
              No platform evidence gap was recorded.
            </p>
          )}
        </section>
      </div>

      {section.explanation && (
        <p className="ib-breakable mt-4 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {section.explanation}
        </p>
      )}

      {section.domainCost.length > 0 && (
        <details className="group mt-5" data-testid="professional-domain-cost">
          <summary className="cursor-pointer list-none text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]">
            Research effort by domain
            <span aria-hidden="true" className="ml-2 group-open:hidden">
              ▸
            </span>
            <span aria-hidden="true" className="ml-2 hidden group-open:inline">
              ▾
            </span>
          </summary>
          <Bullets
            items={section.domainCost.map(
              ({ domain, counts }) =>
                `${words(domain)} — ${Object.entries(counts)
                  .map(([what, n]) => `${n} ${words(what)}`)
                  .join(", ")}`,
            )}
          />
        </details>
      )}
    </>
  );
}

/* ── The report ─────────────────────────────────────────────────────────────────── */

function SectionCard({
  section,
  index,
  children,
}: {
  section: ProfessionalSection;
  index: number;
  children: ReactNode;
}) {
  return (
    <Surface
      as="section"
      className="scroll-mt-24 p-6 sm:p-7"
      testId={`professional-section-${section.key}`}
      id={`section-${section.key}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <h2 className="min-w-0 text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          <span className="font-mono text-sm font-normal text-[color:var(--ib-ink-3)]">
            {index + 1}.
          </span>{" "}
          {section.title}
        </h2>
        {section.kind === "domain" && (
          <StatusBadge status={section.status} testId="section-status" />
        )}
      </div>
      {children}
    </Surface>
  );
}

export default function ProfessionalResearchReport({
  report,
}: {
  report: ProfessionalResearch;
}) {
  // Every label a finding on this page carries. A reference to any other label is
  // rendered as text, so no link on the page points at nothing.
  const anchored = new Set<string>();
  const questionText = new Map<string, string>();
  for (const section of report.sections) {
    if (section.kind !== "domain") continue;
    for (const f of section.findings) if (f.label) anchored.add(f.label);
    for (const q of section.openQuestions) {
      if (q.questionKey && q.text) questionText.set(q.questionKey, q.text);
    }
  }
  const subject = report.subject
    ? [report.subject.name, report.subject.ticker && `(${report.subject.ticker})`]
        .filter(Boolean)
        .join(" ")
    : null;

  return (
    <div className="space-y-5" data-testid="professional-research" id="professional-research">
      <Surface className="p-5 sm:p-6" testId="professional-research-intro">
        <p className={kicker}>Research report</p>
        <p className="ib-breakable mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {subject ? `${subject} — ` : ""}
          {report.sections.length} sections assembled from the research ledger. Each
          finding is stated once, in the section that owns it, under a label such as F1;
          everything else refers to it by that label.
          {report.councilConvened === false &&
            " The research council did not convene for this run."}
          {report.councilConvened === true && " The research council convened for this run."}
        </p>
        <nav aria-label="Research report sections" className="mt-4">
          <ol className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {report.sections.map((section, i) => (
              <li key={section.key} className="flex min-w-0 gap-2">
                <span className="w-6 shrink-0 text-right font-mono text-xs leading-6 text-[color:var(--ib-ink-3)]">
                  {i + 1}.
                </span>
                <a
                  href={`#section-${section.key}`}
                  className="ib-breakable leading-6 text-[color:var(--ib-ink-2)] underline decoration-[color:var(--ib-line-strong)] underline-offset-4 hover:text-[color:var(--ib-ink)]"
                >
                  {section.title}
                </a>
              </li>
            ))}
          </ol>
        </nav>
      </Surface>

      {report.sections.map((section, i) => (
        <SectionCard key={section.key} section={section} index={i}>
          {section.kind === "synthesis" ? (
            <SynthesisBody section={section} editor={report.editor} anchored={anchored} />
          ) : section.kind === "change" ? (
            <ChangeBody section={section} anchored={anchored} questionText={questionText} />
          ) : section.kind === "evidence" ? (
            <EvidenceBody section={section} anchored={anchored} />
          ) : (
            <DomainBody
              section={section}
              anchored={anchored}
              labelsById={report.findingLabels}
            />
          )}
        </SectionCard>
      ))}

      {report.disclaimer && (
        <p
          className="ib-breakable max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
          data-testid="professional-disclaimer"
        >
          {report.disclaimer}
        </p>
      )}
    </div>
  );
}
