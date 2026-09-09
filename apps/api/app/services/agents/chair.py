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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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


def deterministic_verdict(council_input: CouncilInput) -> ChairVerdict:
    """The Chair when no model is available. Computed, never guessed.

    The label follows from the ledger's own state: nothing verified is
    ``insufficient_data``; unresolved conflict or open gaps is ``requires_more_evidence``;
    otherwise ``internal_research_candidate``. All five remain reachable, and none of
    them is a recommendation.
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
    return ChairVerdict(
        label=label,
        fundamental_setup=setup,
        synthesis=(
            f"Deterministic synthesis: {len(council_input.findings)} finding(s), "
            f"{verified} verified, {open_gaps} open gap(s), {unresolved} unresolved "
            "disagreement(s). No model was available, so this is the ledger's own state "
            "rather than an interpretation of it."
        ),
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
    )


@dataclass
class LLMChair:
    """The Chair, over the ledger, with the safety vocabulary enforced after the fact."""

    client: Any = None

    async def deliberate(self, council_input: CouncilInput) -> ChairVerdict:
        # The conflict the Chair is required to surface, taken from the ledger BEFORE
        # the model is asked anything. It cannot be dropped by omission.
        unresolved_ids = [
            str(d.disagreement_id) for d in council_input.unresolved_disagreements
        ]
        if self.client is None or not council_input.convened:
            verdict = deterministic_verdict(council_input)
            verdict.unresolved_disagreement_ids = unresolved_ids
            # Which of the two it was. Checked in this order because a council that did
            # not convene has nothing to synthesise even where a model is available, and
            # that is the more informative statement about the RUN.
            verdict.fallback_reason = (
                "council_did_not_convene"
                if not council_input.convened
                else "no_chair_model_available"
            )
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
            "\n"
            'Return ONLY JSON: {"label": str, "fundamental_setup": str, "synthesis": '
            'str, "key_points": [{"text": str, "finding_id": str}]}'
        )
        user = self._prompt(council_input)
        try:
            payload = await self._complete_json(system, user)
        except Exception:  # noqa: BLE001 - a chair failure falls back, never fails a run
            verdict = deterministic_verdict(council_input)
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

        return ChairVerdict(
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

    def _prompt(self, council_input: CouncilInput) -> str:
        lines = ["FINDINGS (cite by finding_id):"]
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
            lines.append("\nGAPS — the analysis must be explicit about these:")
            for gap in council_input.gaps[:15]:
                lines.append(f"  [{gap.gap_type}] {gap.description[:300]}")
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


__all__ = ["MAX_FINDINGS_IN_PROMPT", "ChairVerdict", "LLMChair", "deterministic_verdict"]
