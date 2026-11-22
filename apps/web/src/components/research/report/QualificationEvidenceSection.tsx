/**
 * V3.21 slice 21.5 (ADR-112) — "Qualification evidence": certificates by body and date,
 * the share corroborated by another party, STL membership of the bodies named, and what
 * could not be verified. Nothing here produces markup from data.
 */

import type { VerificationState } from "@/components/research/realAsset";
import {
  type QualificationEvidenceBlock,
  VERIFICATION_LABELS,
} from "@/components/research/qualificationEvidence";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";

const BADGE_TONE: Record<VerificationState, string> = {
  third_party_corroborated: "border-emerald-300 text-emerald-800",
  issuer_claim: "border-slate-300 text-[color:var(--ib-ink-2)]",
  body_unrecognised: "border-amber-300 text-amber-800",
  unverified: "border-slate-300 text-[color:var(--ib-ink-3)]",
};

export function VerificationBadge({ state }: { state: VerificationState }) {
  return (
    <span
      className={`ml-1 inline-block rounded border px-1.5 py-px text-[11px] ${BADGE_TONE[state]}`}
      data-testid="certificate-verification-state"
      data-state={state}
    >
      {VERIFICATION_LABELS[state]}
    </span>
  );
}

export function QualificationEvidenceSection({ block }: { block: QualificationEvidenceBlock | null }) {
  if (!block) return null;
  const register = block.stlRegister;
  return (
    <section className="mt-6" data-testid="qualification-evidence">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Qualification evidence</h4>
      {block.total === 0 ? (
        <p className={muted}>No certificate was found in this run&apos;s cited findings.</p>
      ) : (
        <>
          <p className="text-sm text-[color:var(--ib-ink-2)]">
            {block.corroborated} of {block.total} certificate{block.total === 1 ? "" : "s"} corroborated by
            another party
            {block.share !== null && <> ({Math.round(block.share * 100)}%)</>}.{" "}
            {block.anyStlMember
              ? `STL member named: ${block.stlMembersNamed.join(", ")}.`
              : "No body named is an STL member."}
          </p>
          <ul className="mt-2" data-testid="qualification-evidence-by-body">
            {block.byBody.map((row, i) => (
              <li key={i} className="text-sm text-[color:var(--ib-ink-2)] [overflow-wrap:anywhere]">
                {row.body ?? "No body named"}
                {row.stlMember && <span className={muted}> · STL member</span>}
                <span className={muted}>
                  {" "}
                  · {row.count} certificate{row.count === 1 ? "" : "s"}
                  {row.dates.length > 0 && <> · {row.dates.join(", ")}</>}
                </span>
              </li>
            ))}
          </ul>
          <ul className="mt-2" data-testid="qualification-evidence-certificates">
            {block.certificates.map((c, i) => (
              <li key={i} className="text-sm text-[color:var(--ib-ink-2)] [overflow-wrap:anywhere]">
                {c.certification ?? "certificate"}
                {c.standard && <> · {c.standard}</>}
                {c.body && <> · {c.body}</>}
                {c.bodyCountry && <span className={muted}> ({c.bodyCountry})</span>}
                <span className={muted}> · {c.date ?? "date not disclosed"}</span>
                <VerificationBadge state={c.state} />
                {c.corroboratingUrls.map((u, j) => (
                  <div key={j} className={muted}>
                    {u}
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </>
      )}
      {block.notVerifiable.length > 0 && (
        <ul className="mt-2" data-testid="qualification-evidence-not-verifiable">
          {block.notVerifiable.map((n, i) => (
            <li key={i} className={muted}>
              {n}
            </li>
          ))}
        </ul>
      )}
      {register && (
        <p className={`mt-2 ${muted}`} data-testid="qualification-evidence-stl-register">
          STL members from {register.source ?? "stl-liaison.org"}
          {register.directoryIssue && <>, directory issue {register.directoryIssue}</>}
          {register.directoryDate && <> dated {register.directoryDate}</>}
          {register.retrievedAt && <>, retrieved {register.retrievedAt.slice(0, 10)}</>}
          {register.origin === "bundled_snapshot" && <> (bundled snapshot)</>}.
        </p>
      )}
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
