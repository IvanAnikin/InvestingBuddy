/**
 * V3.20 slice 9.3 — the one-page memo: a rendering of the same stored report, never a
 * second account. It shows the computed verdict, the valuation range, the synthesis
 * sentences with their finding labels, the case against and the disclaimer. Every
 * figure and sentence is the report's own; nothing here produces markup from data.
 */

import type { ProfessionalResearch } from "@/components/research/v3Research";
import { VERDICT_DISCLAIMER } from "@/components/research/report/ResearchVerdictSection";

const h = "mt-4 text-[11px] font-semibold uppercase tracking-[0.12em] text-[color:var(--ib-ink-3)]";
const p = "mt-1 text-sm leading-relaxed text-[color:var(--ib-ink-2)]";

const money = (v: number | null, ccy: string | null): string =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${ccy ? ` ${ccy}` : ""}`;

export default function ResearchMemo({
  report,
  title,
  createdAt,
}: {
  report: ProfessionalResearch;
  title: string;
  createdAt: string | null;
}) {
  const synthesis = report.sections.find((s) => s.kind === "synthesis");
  const sentences = synthesis && synthesis.kind === "synthesis" ? synthesis.sentences.slice(0, 5) : [];
  const change = report.sections.find((s) => s.kind === "change");
  const v = report.verdict;
  const val = report.valuation;
  const critique = report.selfCritique;
  return (
    <article className="mx-auto max-w-3xl" data-testid="research-memo">
      <p className="text-[11px] uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">Research memo</p>
      <h1 className="mt-1 text-xl font-semibold text-[color:var(--ib-ink)]">
        {[report.subject?.name, report.subject?.ticker && `(${report.subject.ticker})`].filter(Boolean).join(" ") ||
          title}
      </h1>
      {createdAt && <p className="text-xs text-[color:var(--ib-ink-3)]">Report of {createdAt.slice(0, 10)}</p>}

      <h2 className={h}>Computed verdict</h2>
      {v ? (
        <p className={p} data-testid="memo-verdict">
          <span className="font-mono font-semibold text-[color:var(--ib-ink)]">{v.recommendation}</span> — {v.meaning}
          {v.compositeScore !== null && ` Composite ${v.compositeScore} / 5, confidence ${v.confidence.band ?? "—"}.`}
          {v.caps.length > 0 && ` Held back by: ${v.caps.map((c) => c.reason).join("; ")}.`}
        </p>
      ) : (
        <p className={p}>No verdict was computed for this report.</p>
      )}

      <h2 className={h}>Valuation range</h2>
      {val && val.range.state === "computed" ? (
        <p className={p}>
          {money(val.range.low, val.currency)} – {money(val.range.high, val.currency)} per share
          {val.price && <> against a price reference of {money(val.price.perShare, val.price.currency)} (market cap as of {val.price.asOf ?? "—"})</>}.
        </p>
      ) : (
        <p className={p}>{val ? `No range: ${val.range.reason ?? "insufficient inputs"}.` : "Not computed."}</p>
      )}

      <h2 className={h}>Synthesis</h2>
      {sentences.length > 0 ? (
        <ul className="ml-4 list-disc">
          {sentences.map((s, i) => (
            <li key={i} className={p}>
              {s.text}
              {s.labels.length > 0 && <span className="text-xs text-[color:var(--ib-ink-3)]"> [{s.labels.join(", ")}]</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className={p}>No synthesis sentence passed verification.</p>
      )}

      {change && change.kind === "change" && (change.catalystLabels.length > 0 || change.counterThesisLabels.length > 0) && (
        <>
          <h2 className={h}>What would change the thesis</h2>
          <p className={p}>
            {change.catalystLabels.length > 0 && <>Catalysts: {change.catalystLabels.slice(0, 6).join(", ")}. </>}
            {change.counterThesisLabels.length > 0 && <>Counter-thesis: {change.counterThesisLabels.slice(0, 6).join(", ")}.</>}
          </p>
        </>
      )}

      <h2 className={h}>The case against</h2>
      {critique && critique.bearCase.length > 0 ? (
        <ul className="ml-4 list-disc">
          {critique.bearCase.slice(0, 4).map((b, i) => (
            <li key={i} className={p}>
              {b.kind === "unresolved_challenge"
                ? `Unresolved Red Team challenge${b.label ? ` to ${b.label}` : ""}: ${b.statement ?? ""}`
                : `${b.kind === "counter_thesis_finding" ? "Counter-thesis" : "Adverse"} finding ${b.label ?? ""}`}
            </li>
          ))}
        </ul>
      ) : (
        <p className={p}>
          {critique ? `Self-critique incomplete: ${critique.reason ?? "no bear case"}.` : "No self-critique was recorded."}
        </p>
      )}

      <p className="mt-5 text-[11px] leading-relaxed text-[color:var(--ib-ink-3)]">
        {report.disclaimer} {v && VERDICT_DISCLAIMER} Findings are referred to by label; the full report states and cites
        each one.
      </p>
    </article>
  );
}
