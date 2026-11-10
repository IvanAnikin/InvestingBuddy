"""The Chair over verified research state — V3.10 Slice 10.2.

WHAT THE CHAIR MAY SAY, AND WHAT IT MAY NOT
===========================================
The five allowed labels (``ALLOWED_COMMITTEE_LABELS``) are unchanged and are **filtered
after the model replies**, not merely requested in a prompt. A label outside the set
becomes ``insufficient_data``, because the vocabulary being closed is a safety property
and a prompt is a request.

BUY / SELL / HOLD / WATCH are absent from the type rather than stripped from the text.
That took several live corrections to get right in V2 and V3.10 does not reopen it.

THE ONE THING THE CHAIR MUST DO
===============================
> **Surface unresolved conflict; never silently pick a number or a source.**

So unresolved disagreements and unresolved challenges are given to it **first**, and its
output carries them through: ``unresolved_disagreement_ids`` is populated by the platform
from the ledger, not by the model from its reading. A Chair that could drop a conflict by
not mentioning it would be exactly the silent selection the rule forbids.

OPEN QUESTIONS COME FROM THE LEDGER
===================================
A live trap carried forward: the chair's own ``primary_open_questions`` degraded into
machine-record noise on live data. So open questions are taken from the ledger's gaps and
unanswered questions, and the model is not asked to invent them.

DEGRADING IS PART OF THE CONTRACT
=================================
No model, or a model failure, produces a **deterministic** chair verdict computed from the
ledger — the property V3.0 established. A run never loses its conclusion to a provider.

MATERIAL EVENTS NEED A VETTED SOURCE (ADR-078)
==============================================
A takeover offer, insolvency, delisting, a CEO or CFO leaving, fraud, a restatement or
an enforcement action is not stated by the chair — in a key point or in its synthesis —
unless a finding on a vetted source (issuer filing or release, regulator, official
filing system, curated press) states it. Enforced after the reply, like the labels.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.services import commercial_claims, material_events
from app.services.council_v2.inputs import CouncilInput
from app.services.llm.schemas import (
    ALLOWED_COMMITTEE_LABELS,
    ALLOWED_FUNDAMENTAL_SETUPS,
    DEFAULT_COMMITTEE_LABEL,
    DEFAULT_FUNDAMENTAL_SETUP,
)

MAX_TOKENS = 1400
TIMEOUT = 90

#: How many findings reach the Chair's prompt. Verified first (7.1 orders them), so
#: truncation drops the least supported material.
MAX_FINDINGS_IN_PROMPT = 40


@dataclass
class ChairVerdict:
    """The Chair's output, with the conflict it was required to surface."""

    label: str
    fundamental_setup: str
    synthesis: str
    key_points: list[dict[str, Any]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    #: Populated by the PLATFORM from the ledger, never by the model. A Chair that could
    #: drop a conflict by not mentioning it would be the silent selection the rule
    #: forbids.
    unresolved_disagreement_ids: list[str] = field(default_factory=list)
    deterministic_fallback: bool = False
    #: WHY the deterministic verdict was used. These are different states and a reader
    #: must not be told the wrong one: "the council produced nothing to synthesise" is a
    #: research outcome, "no chair model was reachable" is an infrastructure failure.
    #: The live MRNA report degraded with the second message while a chair model was in
    #: fact routed and available — the council simply had no findings.
    fallback_reason: str | None = None
    #: Citations the model made that name nothing in this run.
    discarded_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "fundamental_setup": self.fundamental_setup,
            "synthesis": self.synthesis,
            "key_points": list(self.key_points),
            "open_questions": list(self.open_questions),
            "unresolved_disagreement_ids": list(self.unresolved_disagreement_ids),
            "deterministic_fallback": self.deterministic_fallback,
            "fallback_reason": self.fallback_reason,
            "discarded_citations": list(self.discarded_citations),
        }


#: Why a model verdict's synthesis was replaced by the deterministic one (ADR-078).
FALLBACK_MATERIAL_EVENT = "synthesis_removed_by_material_event_gate"

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _gate_material_events(
    verdict: ChairVerdict, council_input: CouncilInput, subject: Mapping[str, Any] | None
) -> ChairVerdict:
    """ADR-078 — drop key points and synthesis sentences that state a material event no
    finding on a vetted source states. Dropped, not repaired."""
    by_id = {str(f.finding_id): f for f in council_input.findings}
    verdict.key_points = [
        point for point in verdict.key_points
        if not material_events.sentence_blocked(
            str(point.get("text") or ""),
            [by_id[str(point.get("finding_id"))]] if str(point.get("finding_id")) in by_id
            else [],
            subject,
        )
    ]
    # The synthesis cites nothing, so a sentence stating event type T is kept only when
    # some finding of this run on a vetted source states T (the CEO and CFO are
    # different types).
    # A sentence naming the counterparty of an unvetted event in words about that event
    # ("Stripe's interest in the company remains") goes too.
    vetted = material_events.vetted_event_types(council_input.findings, subject)
    parties = material_events.counterparties(council_input.findings, subject)
    sentences = [p for p in _SENTENCE_RE.split(verdict.synthesis or "") if p.strip()]
    kept = [
        sentence for sentence in sentences
        if not material_events.uncited_sentence_blocked(sentence, vetted, parties, subject)
    ]
    # ADR-088 (PYPL-4): no net debt where the company's balance sheet gives it no reading.
    from app.services.playbooks.applicability import net_debt_sentence_blocked

    verdict.key_points = [
        point for point in verdict.key_points
        if not net_debt_sentence_blocked(str(point.get("text") or ""), subject)
    ]
    kept = [sentence for sentence in kept if not net_debt_sentence_blocked(sentence, subject)]
    # ADR-088 (LIN-1): a pre-commercial company is not described as producing, mining or
    # selling unless some finding of the run reports actual production or sales.
    if commercial_claims.pre_commercial_stage(subject) is not None and not (
        commercial_claims.evidenced(council_input.findings)
    ):
        verdict.key_points = [
            point for point in verdict.key_points
            if not commercial_claims.claims_commercial_operation(
                str(point.get("text") or ""), subject)
        ]
        kept = [
            sentence for sentence in kept
            if not commercial_claims.claims_commercial_operation(sentence, subject)
        ]
    if len(kept) != len(sentences):
        verdict.synthesis = " ".join(kept)
        if not kept and not verdict.deterministic_fallback:
            # Nothing of the chair's prose survived: the ledger's own state stands.
            verdict.synthesis = (
                f"{_ledger_state(council_input)} The chair's own synthesis was withheld: "
                "it stated only material corporate events, a commercial operation or "
                "a metric with no reading for this company, that no source in this run "
                "supports."
            )
            verdict.deterministic_fallback = True
            verdict.fallback_reason = FALLBACK_MATERIAL_EVENT
    return verdict


#: Why the deterministic verdict stands (ADR-082). Three different states; a reader must
#: be told the right one. The Cellectis runs said "No model was available" while a
#: chair model was routed and the council had refused on a blocking question.
FALLBACK_NO_MODEL = "no_chair_model_available"
FALLBACK_NOT_CONVENED = "council_did_not_convene"
FALLBACK_CHAIR_FAILED = "chair_model_failed"

#: Plain words for each refusal reason (``council_v2.inputs.REFUSAL_REASONS``).
REFUSAL_WORDS: dict[str, str] = {
    "blocking_question_open": "a blocking research question is still open",
    "gap_blocks_council": "a recorded gap blocks the council",
    "no_findings": "the run produced no evidence-linked findings",
}


def _ledger_state(council_input: CouncilInput) -> str:
    """The LEDGER's counts (ADR-082) — never the size of the pack the Council was shown,
    which is empty on a refusal."""
    return (
        f"Deterministic synthesis: {council_input.finding_count} finding(s), "
        f"{council_input.verified_count} verified, {council_input.open_gap_count} open "
        f"gap(s), {council_input.unresolved_count} unresolved disagreement(s)."
    )


def _fallback_tail(council_input: CouncilInput, reason: str) -> str:
    """Why this is the ledger's state and not a synthesis — the TRUE reason."""
    if reason == FALLBACK_NOT_CONVENED:
        why = REFUSAL_WORDS.get(
            str(council_input.refusal_reason), "the council did not convene"
        )
        return (
            f"The council did not convene because {why}, so no synthesis was "
            "attempted; the findings stand in the research record as recorded."
        )
    if reason == FALLBACK_CHAIR_FAILED:
        return (
            "The chair model did not return a usable reply, so this is the ledger's "
            "own state rather than an interpretation of it."
        )
    return (
        "No model was available, so this is the ledger's own state rather than an "
        "interpretation of it."
    )


def deterministic_verdict(
    council_input: CouncilInput,
    subject: Mapping[str, Any] | None = None,
    *,
    reason: str = FALLBACK_NO_MODEL,
) -> ChairVerdict:
    """The Chair when no model reasoned over the ledger. Computed, never guessed.

    The label follows from the ledger's own state: nothing verified is
    ``insufficient_data``; unresolved conflict or open gaps is ``requires_more_evidence``;
    otherwise ``internal_research_candidate``. All five remain reachable, and none of
    them is a recommendation. A refused Council keeps its refusal semantics (no findings
    in the pack, so ``insufficient_data``); only its TEXT now states the ledger's true
    counts and the true reason.
    """
    verified = len(council_input.verified_findings)
    unresolved = len(council_input.unresolved_disagreements)
    open_gaps = len([g for g in council_input.gaps if g.status == "open"])
    if not council_input.findings:
        label, setup = "insufficient_data", "insufficient_evidence"
    elif verified == 0:
        label, setup = "requires_more_evidence", "insufficient_evidence"
    elif unresolved or open_gaps:
        label, setup = "requires_more_evidence", "mixed"
    else:
        label, setup = "internal_research_candidate", "constructive"
    verdict = ChairVerdict(
        label=label,
        fundamental_setup=setup,
        synthesis=f"{_ledger_state(council_input)} {_fallback_tail(council_input, reason)}",
        key_points=[
            {
                "text": f.statement,
                "finding_id": str(f.finding_id),
                "evidence_ids": list(f.evidence_ids),
            }
            for f in council_input.findings[:8]
        ],
        open_questions=list(council_input.open_question_keys)[:10],
        unresolved_disagreement_ids=[
            str(d.disagreement_id) for d in council_input.unresolved_disagreements
        ],
        deterministic_fallback=True,
        fallback_reason=reason,
    )
    return _gate_material_events(verdict, council_input, subject)


@dataclass
class LLMChair:
    """The Chair, over the ledger, with the safety vocabulary enforced after the fact."""

    client: Any = None
    #: ``{"name", "ticker"}`` of the run's company, so a material event can be told to
    #: be ABOUT it (ADR-078). Optional: without it, "the company" / "the board" still is.
    subject: Mapping[str, Any] | None = None
    #: ADR-088 — platform-set context lines (metrics with no reading for this company,
    #: a pre-commercial stage). Empty (the default) leaves the prompt exactly as it was.
    context_notes: tuple[str, ...] = ()

    async def deliberate(self, council_input: CouncilInput) -> ChairVerdict:
        # The conflict the Chair is required to surface, taken from the ledger BEFORE
        # the model is asked anything. It cannot be dropped by omission.
        unresolved_ids = [
            str(d.disagreement_id) for d in council_input.unresolved_disagreements
        ]
        if self.client is None or not council_input.convened:
            # Which of the two it was. Checked in this order because a council that did
            # not convene has nothing to synthesise even where a model is available, and
            # that is the more informative statement about the RUN.
            verdict = deterministic_verdict(
                council_input,
                self.subject,
                reason=(
                    FALLBACK_NOT_CONVENED
                    if not council_input.convened
                    else FALLBACK_NO_MODEL
                ),
            )
            verdict.unresolved_disagreement_ids = unresolved_ids
            return verdict

        system = (
            "You are the committee chair of an investment research council. You "
            "synthesise; you do not rediscover facts the pipeline already owns.\n"
            "\n"
            "RULES:\n"
            f"1. `label` MUST be one of: {', '.join(sorted(ALLOWED_COMMITTEE_LABELS))}. "
            "These are internal research states, NOT recommendations.\n"
            f"2. `fundamental_setup` MUST be one of: "
            f"{', '.join(sorted(ALLOWED_FUNDAMENTAL_SETUPS))}.\n"
            "3. NEVER output BUY, SELL, HOLD, WATCH, a price target or a fair value.\n"
            "4. Every key_point MUST carry the finding_id it rests on.\n"
            "5. Where two findings disagree, SAY THAT THEY DISAGREE and which source is "
            "more authoritative and why. Do NOT quietly pick one.\n"
            "6. Group and segment figures are different; annual and interim are "
            "different. Never merge them.\n"
            "7. Never state a takeover offer, merger, going-private, insolvency, "
            "delisting, CEO or CFO departure, fraud, restatement or enforcement action "
            "as fact unless a finding whose source_kinds include issuer_filing, "
            "issuer_ir, government_or_regulator, third_party_filing or quality_media "
            "states it.\n"
            "8. A finding whose statement_kind is guidance, target, forecast or estimate "
            "is an expectation, not a reported result: say whose, never that it "
            "happened.\n"
            "\n"
            'Return ONLY JSON: {"label": str, "fundamental_setup": str, "synthesis": '
            'str, "key_points": [{"text": str, "finding_id": str}]}'
        )
        user = self._prompt(council_input)
        try:
            payload = await self._complete_json(system, user)
        except Exception:  # noqa: BLE001 - a chair failure falls back, never fails a run
            verdict = deterministic_verdict(
                council_input, self.subject, reason=FALLBACK_CHAIR_FAILED
            )
            verdict.unresolved_disagreement_ids = unresolved_ids
            return verdict

        citable = council_input.citable_finding_ids
        points: list[dict[str, Any]] = []
        discarded: list[str] = []
        for raw in (payload.get("key_points") or [])[:12]:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            finding_id = str(raw.get("finding_id") or "").strip()
            if not text:
                continue
            if finding_id not in citable:
                # A key point citing nothing in this run is the positional-handle
                # problem all over again; it does not enter the record.
                discarded.append(finding_id)
                continue
            points.append({"text": text[:1000], "finding_id": finding_id})

        # The vocabulary is enforced AFTER the reply. A prompt is a request; this is
        # the guarantee.
        label = str(payload.get("label") or "").strip()
        if label not in ALLOWED_COMMITTEE_LABELS:
            label = DEFAULT_COMMITTEE_LABEL
        setup = str(payload.get("fundamental_setup") or "").strip()
        if setup not in ALLOWED_FUNDAMENTAL_SETUPS:
            setup = DEFAULT_FUNDAMENTAL_SETUP

        verdict = ChairVerdict(
            label=label,
            fundamental_setup=setup,
            synthesis=str(payload.get("synthesis") or "").strip()[:4000],
            key_points=points,
            # From the LEDGER, not from the chair. The live trap this avoids: the
            # chair's own open questions degraded into machine-record noise.
            open_questions=list(council_input.open_question_keys)[:10],
            unresolved_disagreement_ids=unresolved_ids,
            discarded_citations=discarded,
        )
        return _gate_material_events(verdict, council_input, self.subject)

    def _prompt(self, council_input: CouncilInput) -> str:
        lines = (
            [
                "COMPANY CONTEXT (set by the platform from the company's own documents, "
                "not evidence to cite):",
                *(f"  - {note}" for note in self.context_notes),
                "",
            ]
            if self.context_notes
            else []
        )
        lines.append("FINDINGS (cite by finding_id):")
        for finding in council_input.findings[:MAX_FINDINGS_IN_PROMPT]:
            lines.append(
                json.dumps(
                    {
                        "finding_id": str(finding.finding_id),
                        "statement": finding.statement[:600],
                        "direction": finding.direction,
                        "confidence": finding.confidence,
                        "period_key": finding.period_key,
                        "scope_key": finding.scope_key,
                        "verification_status": finding.verification_status,
                        "source_kinds": list(finding.source_kinds),
                        # Migration 043 — a superseded finding is PRIOR guidance; the
                        # newer one it names is current.
                        "source_published_at": getattr(finding, "source_published_at", None),
                        "superseded_by_finding_id": getattr(
                            finding, "superseded_by_finding_id", None
                        ),
                        # ADR-088 — guidance, a forecast, a target or an estimate is
                        # never a reported result.
                        **(
                            {"statement_kind": finding.statement_kind}
                            if getattr(finding, "statement_kind", None)
                            else {}
                        ),
                    },
                    default=str,
                )
            )
        if council_input.unresolved_disagreements:
            lines.append("\nUNRESOLVED DISAGREEMENTS — you must surface these:")
            for d in council_input.unresolved_disagreements[:10]:
                lines.append(
                    f"  {d.nature}: {d.finding_a_id} vs {d.finding_b_id} — "
                    f"{(d.description or '')[:300]}"
                )
        if council_input.gaps:
            # Closed and superseded gaps never reach here (council_v2.inputs). A gap a
            # finding PARTLY addresses says which finding, so the analysis does not call
            # a field missing that a cited finding states.
            lines.append("\nGAPS — the analysis must be explicit about these:")
            for gap in council_input.gaps[:15]:
                line = f"  [{gap.gap_type}] {gap.description[:300]}"
                result = getattr(gap, "resolution_result", None)
                if result and result != "NOT_FOUND_AFTER_SEARCH":
                    # ADR-094: only a gap searched for and not found may be called
                    # absent; every other result is said as what it is.
                    line += (
                        f" (RESEARCH STATE {result}: {getattr(gap, 'resolution_words', '')}"
                        " Do not call this undisclosed or absent.)"
                    )
                if getattr(gap, "reconciliation_status", None) == "partially_closed" and (
                    getattr(gap, "gap_status", None) in (None, "PARTIALLY_CLOSED")
                ):
                    addressed = ", ".join(getattr(gap, "addressed_by_finding_ids", ())[:3])
                    reasons = ", ".join(getattr(gap, "reconciliation_reasons", ())[:3])
                    line += (
                        f" (PARTIALLY ADDRESSED by finding {addressed or 'n/a'}"
                        + (f"; {reasons}" if reasons else "")
                        + ")"
                    )
                lines.append(line)
        return "\n".join(lines)

    async def _complete_json(self, system: str, user: str) -> dict[str, Any]:
        if hasattr(self.client, "complete_json"):
            return await self.client.complete_json(
                system, user, max_tokens=MAX_TOKENS, timeout=TIMEOUT
            )
        response = await self.client.complete(
            system=system, user=user, max_tokens=MAX_TOKENS, timeout=TIMEOUT
        )
        payload = getattr(response, "payload", None)
        return payload if isinstance(payload, dict) else {}


__all__ = [
    "FALLBACK_CHAIR_FAILED",
    "FALLBACK_MATERIAL_EVENT",
    "FALLBACK_NOT_CONVENED",
    "FALLBACK_NO_MODEL",
    "REFUSAL_WORDS",
    "MAX_FINDINGS_IN_PROMPT",
    "ChairVerdict",
    "LLMChair",
    "deterministic_verdict",
]
