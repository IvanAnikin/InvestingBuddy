import Surface from "@/components/product/Surface";
import type {
  ExternalLead,
  V3Research,
} from "@/components/research/v3Research";

/**
 * What the V3 research pipeline did, shown to the person reading the report.
 *
 * WHY THIS SECTION IS SEPARATE FROM THE REPORT ABOVE IT
 * ====================================================
 * The V3 council's verdict is recorded BESIDE the report, not driving its prose — the
 * V2 generator still assembles every narrative section above. Presenting V3's findings
 * inside those sections would claim an integration that does not exist. So this is one
 * clearly labelled section that says what the V3 run established, what it could not,
 * what it challenged, and what it spent.
 *
 * WHAT IT REFUSES TO DO
 * =====================
 * It never shows a rejected provider lead as a source. A lead is a vendor's claim about
 * a page; it becomes evidence only when InvestingBuddy fetched that page itself and the
 * claim survived checking against those bytes. Both are shown — the rejections are the
 * honest denominator — but the difference is stated on every row, not implied by
 * ordering or colour alone.
 *
 * It also never reports an unpriced run as free. `estimated_cost_usd` is null when no
 * price list is configured, and null renders as "not priced", never as $0.00.
 */

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
        {label}
      </dt>
      <dd className="mt-1 text-base font-semibold text-[color:var(--ib-ink)]">
        {value}
      </dd>
      {hint && (
        <p className="mt-0.5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {hint}
        </p>
      )}
    </div>
  );
}

const n = (v: number | null): string => (v === null ? "—" : String(v));

function labelWords(raw: string | null): string {
  if (!raw) return "—";
  return raw.replace(/_/g, " ");
}

function LeadRow({ lead }: { lead: ExternalLead }) {
  return (
    <li
      className="border-t border-[color:var(--ib-line)] py-3 first:border-t-0 first:pt-0"
      data-testid={lead.isEvidence ? "v3-lead-evidence" : "v3-lead-claim"}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={
            lead.isEvidence
              ? "rounded-sm bg-emerald-400/10 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-[0.1em] text-emerald-300"
              : "rounded-sm bg-[color:var(--ib-line)] px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-[0.1em] text-[color:var(--ib-ink-3)]"
          }
        >
          {lead.isEvidence
            ? lead.corroboratingOnly
              ? "Verified — corroboration"
              : "Verified — evidence"
            : "Provider claim only"}
        </span>
        {lead.host && (
          <span className="ib-breakable text-xs text-[color:var(--ib-ink-3)]">
            {lead.host}
          </span>
        )}
        {lead.claimedPeriod && (
          <span className="text-xs text-[color:var(--ib-ink-3)]">
            {lead.claimedPeriod}
            {lead.periodVerified === false && " (period unconfirmed)"}
          </span>
        )}
      </div>
      <p className="ib-breakable mt-1.5 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
        {lead.claim}
      </p>
      {lead.isEvidence ? (
        <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
          {lead.evidenceId}
          {lead.contentHash && ` · content ${lead.contentHash}`}
          {lead.corroboratingOnly &&
            " · a primary source established this fact; kept as corroboration"}
        </p>
      ) : (
        <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
          Not used as evidence
          {lead.rejectionReason && ` — ${labelWords(lead.rejectionReason)}`}
          {lead.rejectionDetail && `: ${lead.rejectionDetail}`}
        </p>
      )}
    </li>
  );
}

export default function V3ResearchPanel({ v3 }: { v3: V3Research | null }) {
  // The property every existing report depends on: no V3 payload, no section.
  if (!v3) return null;

  const { external, consumption, chair, council, challenges, delta, classification } = v3;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="v3-research"
      id="v3-research"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          V3 research run
        </h2>
        {v3.elapsedSeconds !== null && (
          <p className="text-xs text-[color:var(--ib-ink-3)]">
            {v3.elapsedSeconds.toFixed(1)}s
            {v3.mode && ` · ${labelWords(v3.mode)} mode`}
          </p>
        )}
      </div>

      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
        The multi-agent research layer, recorded beside the report. The narrative
        sections above are assembled by the existing generator — this section is the
        research ledger itself: what was established, with what evidence, what was
        challenged, and what could not be closed.
      </p>

      {/* A failed run is the most important thing on the page. Never hidden. */}
      {v3.error && (
        <p className="mt-4 rounded-sm border border-rose-400/30 bg-rose-400/5 p-3 text-sm leading-relaxed text-rose-200">
          The V3 run did not complete ({v3.error}). The report above was produced by the
          existing pipeline and is unaffected, but nothing in this section should be read
          as a full research result.
        </p>
      )}

      {v3.degraded.length > 0 && (
        <div className="mt-4" data-testid="v3-degraded">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Why this run produced less than a full one
          </p>
          <ul className="mt-2 space-y-1.5">
            {v3.degraded.map((d, i) => (
              <li
                key={i}
                className="ib-breakable flex gap-3 text-sm leading-relaxed text-amber-200/90"
              >
                <span
                  aria-hidden="true"
                  className="mt-2.5 h-px w-3 shrink-0 bg-amber-400/50"
                />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* WHICH METHODOLOGY THIS RUN USED, AND WHY.
          The playbook a run applies is decided by this classification, so a reader
          judging whether the right questions were asked has to be able to see it —
          and to check the translation. `Biotechnology` is the platform's label;
          `Biological Products, (No Diagnostic Substances)` is the regulator's, and
          both are shown so the second can be verified against the source. */}
      {classification && (
        <div className="mt-4" data-testid="v3-classification">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Classification
          </p>
          <p className="ib-breakable mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {classification.industry ?? classification.sector ?? "Not classified"}
            {classification.sector && classification.industry && (
              <span className="text-[color:var(--ib-ink-3)]">
                {" "}
                · {classification.sector}
              </span>
            )}
            {classification.industryRaw &&
              classification.industryRaw !== classification.industry && (
                <span className="text-[color:var(--ib-ink-3)]">
                  {" "}
                  — source says &ldquo;{classification.industryRaw}&rdquo;
                  {classification.sicCode && ` (SIC ${classification.sicCode})`}
                </span>
              )}
          </p>
          {classification.isInferred && (
            <p className="mt-1.5 text-sm leading-relaxed text-amber-200/90">
              This classification is the platform&rsquo;s own estimate
              (T6_model_estimate), not a sourced fact. The methodology chosen from it
              should be confirmed against a primary classification.
            </p>
          )}
        </div>
      )}

      <dl className="mt-6 grid grid-cols-2 gap-5 sm:grid-cols-4">
        <Stat
          label="Findings"
          value={n(council?.findingCount ?? v3.findings.length)}
          hint={
            council?.verifiedFindingCount !== null &&
            council?.verifiedFindingCount !== undefined
              ? `${council.verifiedFindingCount} verified`
              : undefined
          }
        />
        <Stat label="Open gaps" value={n(consumption?.gapsOpen ?? v3.gaps.length)} />
        <Stat label="Rounds" value={n(v3.rounds)} hint={`${n(v3.tasksRun)} tasks`} />
        <Stat
          label="Tool calls"
          value={n(v3.toolCalls)}
          hint={v3.stoppedBy ? `stopped by ${labelWords(v3.stoppedBy)}` : undefined}
        />
      </dl>

      {/* THE COUNCIL'S OWN REFUSAL. A council that declined to convene is a result,
          and stating the reason is what makes it a coverage finding. */}
      {council && !council.convened && (
        <p
          className="mt-5 border-l-2 border-[color:var(--ib-line-strong)] pl-4 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
          data-testid="v3-council-refused"
        >
          The council did not convene — {labelWords(council.refusalReason)}.
          {council.refusalDetail && ` ${council.refusalDetail}`}
        </p>
      )}

      {v3.findings.length > 0 && (
        <div className="mt-6" data-testid="v3-findings">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            What the run established
          </p>
          <ul className="mt-3 space-y-3">
            {v3.findings.slice(0, 12).map((f, i) => (
              <li key={f.findingId ?? i} className="text-sm leading-relaxed">
                <p className="ib-breakable text-[color:var(--ib-ink-2)]">
                  {f.statement}
                </p>
                <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
                  {[
                    f.verificationStatus,
                    f.confidence && `${f.confidence} confidence`,
                    f.periodKey,
                    f.scopeKey,
                    f.originatingRole && labelWords(f.originatingRole),
                    f.evidenceIds.length
                      ? `${f.evidenceIds.length} evidence id${f.evidenceIds.length === 1 ? "" : "s"}`
                      : "no evidence id",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </ul>
          {v3.findings.length > 12 && (
            <p className="mt-3 text-xs text-[color:var(--ib-ink-3)]">
              {v3.findings.length - 12} further finding
              {v3.findings.length - 12 === 1 ? "" : "s"} in the technical record.
            </p>
          )}
        </div>
      )}

      {v3.gaps.length > 0 && (
        <div className="mt-6" data-testid="v3-gaps">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            What it could not establish
          </p>
          <ul className="mt-3 space-y-2">
            {v3.gaps.slice(0, 8).map((g, i) => (
              <li
                key={g.gapId ?? i}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                {g.description}
                {g.whyItMatters && (
                  <span className="text-[color:var(--ib-ink-3)]">
                    {" "}
                    — {g.whyItMatters}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* EXTERNAL RESEARCH. The section the provenance rule exists for. */}
      {external && (
        <div className="mt-7" data-testid="v3-external-research">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            External research
          </p>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            A research provider proposed {external.leadsDiscovered} lead
            {external.leadsDiscovered === 1 ? "" : "s"}. InvestingBuddy retrieved{" "}
            {external.sourcesRetrieved} source
            {external.sourcesRetrieved === 1 ? "" : "s"} itself and promoted{" "}
            {external.evidencePromoted} to evidence. A lead the platform did not verify
            against the source&apos;s own bytes is a claim, not a source, and is labelled
            as one below.
          </p>

          {Object.keys(external.rejectedByReason).length > 0 && (
            <p className="ib-breakable mt-2 text-xs text-[color:var(--ib-ink-3)]">
              Rejected:{" "}
              {Object.entries(external.rejectedByReason)
                .sort((a, b) => b[1] - a[1])
                .map(([reason, count]) => `${labelWords(reason)} (${count})`)
                .join(", ")}
              .
            </p>
          )}

          {external.leads.length > 0 && (
            <ul className="mt-4">
              {external.leads.slice(0, 12).map((lead, i) => (
                <LeadRow key={i} lead={lead} />
              ))}
            </ul>
          )}
        </div>
      )}

      {challenges && challenges.challenges > 0 && (
        <div className="mt-7" data-testid="v3-challenges">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Red team
          </p>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {challenges.challenges} finding
            {challenges.challenges === 1 ? " was" : "s were"} challenged:{" "}
            {challenges.resolved} resolved, {challenges.partiallyResolved} partially,{" "}
            {challenges.unresolved} unresolved. {challenges.withdrawnFindings} finding
            {challenges.withdrawnFindings === 1 ? " was" : "s were"} withdrawn and{" "}
            {challenges.confidenceLowered} had confidence lowered.
          </p>
        </div>
      )}

      {chair && (
        <div className="mt-7" data-testid="v3-chair">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Chair verdict
          </p>
          <p className="mt-2 text-sm font-medium text-[color:var(--ib-ink)]">
            {labelWords(chair.label)}
            {chair.fundamentalSetup && (
              <span className="font-normal text-[color:var(--ib-ink-3)]">
                {" "}
                · {labelWords(chair.fundamentalSetup)}
              </span>
            )}
          </p>
          {chair.synthesis && (
            <p className="ib-breakable mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
              {chair.synthesis}
            </p>
          )}
          {chair.deterministicFallback && (
            <p className="mt-2 max-w-3xl text-xs leading-relaxed text-amber-200/90">
              {chair.fallbackReason === "council_did_not_convene"
                ? "The council did not convene, so there was nothing for the chair to synthesise. This verdict is the ledger's own arithmetic — not a model's, and not a sign that one was missing."
                : chair.fallbackReason === "no_chair_model_available"
                  ? "No chair model was available, so this verdict is the ledger's own arithmetic rather than an interpretation of it."
                  : "This verdict is the ledger's own arithmetic rather than a model's."}
            </p>
          )}
          {chair.openQuestions.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {chair.openQuestions.slice(0, 8).map((q, i) => (
                <li
                  key={i}
                  className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
                >
                  {labelWords(q)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {delta && !delta.isFirstRun && (
        <div className="mt-7" data-testid="v3-delta">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Since the previous run
          </p>
          <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {delta.newEvidence} new evidence item
            {delta.newEvidence === 1 ? "" : "s"}, {delta.changedFacts} changed fact
            {delta.changedFacts === 1 ? "" : "s"}, {delta.resolvedQuestions} question
            {delta.resolvedQuestions === 1 ? "" : "s"} resolved, {delta.closedGaps} gap
            {delta.closedGaps === 1 ? "" : "s"} closed, {delta.newGaps} new.
            {delta.invalidatedFindings > 0 &&
              ` ${delta.invalidatedFindings} earlier finding${delta.invalidatedFindings === 1 ? " was" : "s were"} invalidated.`}
          </p>
        </div>
      )}

      {consumption && (
        <details className="mt-7 group" data-testid="v3-consumption">
          <summary className="cursor-pointer list-none text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]">
            What this run consumed
            <span aria-hidden="true" className="ml-2 group-open:hidden">
              ▸
            </span>
            <span aria-hidden="true" className="ml-2 hidden group-open:inline">
              ▾
            </span>
          </summary>
          <dl className="mt-4 grid grid-cols-2 gap-5 sm:grid-cols-4">
            <Stat label="Web searches" value={n(consumption.webSearchCalls)} />
            <Stat
              label="URL retrievals"
              value={n(consumption.urlFetchCalls)}
              hint="pages this platform fetched itself"
            />
            <Stat
              label="Corpus documents"
              value={n(consumption.documentsFetched)}
              hint="persisted to the corpus"
            />
            <Stat
              label="Useful findings"
              value={n(consumption.verifiedUsefulFindings)}
              hint={
                consumption.findingsWithdrawn
                  ? `${consumption.findingsWithdrawn} withdrawn`
                  : undefined
              }
            />
          </dl>
          <dl className="mt-5 grid grid-cols-2 gap-5 sm:grid-cols-4">
            {/* Two DIFFERENT spends, and the labels now say which. The routed models are
                the investigator, chair and red team; the research provider is the
                search leg, whose calls are recorded on the tool rows. They are separate
                clients with separate transports, so nothing is counted twice. */}
            <Stat
              label="Routed model tokens in"
              value={n(consumption.modelInputTokens)}
              hint="investigator, chair, red team"
            />
            <Stat
              label="Routed model tokens out"
              value={n(consumption.modelOutputTokens)}
            />
            <Stat
              label="Research provider tokens in"
              value={n(consumption.providerInputTokens)}
              hint={
                consumption.providerCachedTokens
                  ? `${consumption.providerCachedTokens} cached · the search leg`
                  : "the search leg"
              }
            />
            <Stat
              label="Research provider tokens out"
              value={n(consumption.providerOutputTokens)}
            />
          </dl>

          {consumption.byVendor.length > 0 && (
            <div className="mt-5" data-testid="v3-vendor-attribution">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                By vendor
              </p>
              <ul className="mt-2 space-y-1">
                {consumption.byVendor.map((v) => (
                  <li
                    key={v.vendor}
                    className="text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    {labelWords(v.vendor)} — {v.calls} call
                    {v.calls === 1 ? "" : "s"}, {v.input} in, {v.output} out
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                A vendor absent from this list made no routed call in this run. That is
                not the same as spending nothing, and it is not reported as zero.
              </p>
            </div>
          )}
          <p className="mt-4 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            {consumption.estimatedCostUsd === null
              ? (consumption.costUnknownBecause ??
                "This run is not priced: no price list is configured. Unpriced is not free.")
              : `Estimated cost $${consumption.estimatedCostUsd.toFixed(4)}` +
                (consumption.costPerVerifiedUsefulFinding !== null
                  ? ` · $${consumption.costPerVerifiedUsefulFinding.toFixed(4)} per verified useful finding.`
                  : ".")}
          </p>
        </details>
      )}

      {v3.runId && (
        <p className="ib-breakable mt-6 border-t border-[color:var(--ib-line)] pt-3 text-xs text-[color:var(--ib-ink-3)]">
          Research run {v3.runId}
        </p>
      )}
    </Surface>
  );
}
