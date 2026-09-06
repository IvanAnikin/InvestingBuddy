"""The real Red Team and the responding analyst — V3.10 Slice 10.2.

WHAT THE MODEL IS ALLOWED TO DECIDE
===================================
The Red Team decides **which findings look weakest and why**. It does not decide whether
a challenge was answered — 7.2 already established that the platform decides the outcome,
because a responder that could declare itself resolved would make the round decorative.

So this module produces `Challenge` and `Response` objects and nothing else. Every rule
about what counts as resolution stays where it was.

TWO GUARDS ON THE CHALLENGER
============================
* **It may only challenge a finding in this run.** A challenge naming an id it was never
  given is discarded — the same fabrication guard the investigator applies, for the same
  reason: an id nobody minted is an id nobody can check.
* **It may not invent a weakness class.** The vocabulary is closed at seven, and a class
  invented at a call site is one nothing can aggregate on.

THE RESPONDER MUST CITE
=======================
A response with no evidence cannot resolve anything, whatever it claims. This module
enforces the same rule the investigator does: the responder is given a list of citable
ids and any other id is dropped — which usually leaves the response with no evidence at
all, and therefore unresolved. That is the correct outcome.

VENDOR DIVERSITY IS PREFERRED AND OFTEN UNAVAILABLE
===================================================
[ADR-050](../../../docs/DECISIONS.md): a Red Team drawn from the Chair's vendor shares its
blind spots. `ModelRouting.shares_vendor_with_chair` reports whether it currently does,
and in a single-vendor environment it does — which is a limitation to state, not to hide.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.council_v2.inputs import FindingRef
from app.services.council_v2.red_team import (
    WEAKNESS_CLASSES,
    WEAKNESS_UNSUPPORTED_EXTRAPOLATION,
    Challenge,
    Response,
)

MAX_TOKENS = 900
TIMEOUT = 60


def _finding_block(findings: "Sequence[FindingRef]", limit: int = 25) -> str:
    lines = []
    for finding in findings[:limit]:
        lines.append(
            json.dumps(
                {
                    "finding_id": str(finding.finding_id),
                    "statement": finding.statement[:600],
                    "mechanism": (finding.mechanism or "")[:300],
                    "confidence": finding.confidence,
                    "period_key": finding.period_key,
                    "scope_key": finding.scope_key,
                    "evidence_ids": list(finding.evidence_ids)[:8],
                    "verification_status": finding.verification_status,
                },
                default=str,
            )
        )
    return "\n".join(lines)


@dataclass
class LLMRedTeam:
    """Selects the weakest assumptions, by id, with a class and a stated reason."""

    client: Any = None
    #: Set when a reply named a finding that is not in this run. The number that says
    #: whether the guard is doing anything.
    discarded_unknown_targets: list[str] = field(default_factory=list)

    async def select(
        self, *, findings: "Sequence[FindingRef]", max_challenges: int
    ) -> "Sequence[Challenge]":
        if self.client is None or not findings:
            return []
        allowed = {str(f.finding_id) for f in findings}
        system = (
            "You are the Red Team on an evidence-first investment research platform. "
            "Your job is to find the weakest assumptions in the findings below and say "
            "precisely why each is weak.\n"
            "\n"
            "RULES:\n"
            f"1. Target a finding_id from the list. Any other id is discarded.\n"
            f"2. weakness_class must be one of: {', '.join(sorted(WEAKNESS_CLASSES))}.\n"
            f"3. Choose at most {max_challenges}, and choose the WEAKEST — challenging "
            "everything is ranking nothing.\n"
            "4. State the weakness concretely. 'This seems uncertain' is an objection, "
            "not a challenge.\n"
            "5. Never propose a rating, a price target or a fair value.\n"
            "\n"
            'Return ONLY JSON: {"challenges": [{"finding_id": str, "weakness_class": '
            'str, "text": str}]}'
        )
        user = "FINDINGS UNDER REVIEW:\n" + _finding_block(findings)
        try:
            payload = await _complete_json(self.client, system, user)
        except Exception:  # noqa: BLE001 - a Red Team failure must not end the run
            return []

        out: list[Challenge] = []
        for raw in (payload.get("challenges") or [])[:max_challenges]:
            if not isinstance(raw, dict):
                continue
            target = str(raw.get("finding_id") or "").strip()
            if target not in allowed:
                # An id nobody minted is an id nobody can check.
                self.discarded_unknown_targets.append(target)
                continue
            weakness = str(raw.get("weakness_class") or "").strip()
            if weakness not in WEAKNESS_CLASSES:
                weakness = WEAKNESS_UNSUPPORTED_EXTRAPOLATION
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            import uuid as _uuid

            try:
                finding_id = _uuid.UUID(target)
            except ValueError:
                continue
            out.append(
                Challenge(
                    finding_id=finding_id,
                    weakness_class=weakness,
                    text=text[:2000],
                )
            )
        return out


@dataclass
class LLMResponder:
    """The responsible analyst. **Must cite, or it has not answered.**"""

    client: Any = None
    #: Evidence ids the responder may cite — everything the run actually holds.
    citable_ids: frozenset[str] = frozenset()
    discarded_citations: list[str] = field(default_factory=list)

    async def respond(
        self, *, challenge: Challenge, finding: FindingRef
    ) -> Response | None:
        if self.client is None:
            return None
        system = (
            "You are the analyst responsible for a finding that has been challenged. "
            "Answer the challenge WITH EVIDENCE, or concede it.\n"
            "\n"
            "RULES:\n"
            "1. Every evidence id you cite must come from ALLOWED CITATION IDS. A "
            "response citing anything else counts as citing nothing.\n"
            "2. A response with no evidence cannot resolve the challenge. If you have "
            "none, say so and set claims_resolved false.\n"
            "3. You may lower your confidence or withdraw the finding. You may NOT "
            "raise your confidence: surviving scrutiny is recorded by the challenge, "
            "not by a bigger number.\n"
            "4. Never propose a rating, a price target or a fair value.\n"
            "\n"
            'Return ONLY JSON: {"text": str, "evidence_ids": [str], "claims_resolved": '
            'bool, "revised_confidence": number|null, "withdraw": bool}'
        )
        user = (
            f"CHALLENGED FINDING ({finding.finding_id}):\n{finding.statement}\n"
            f"mechanism: {finding.mechanism or '(none stated)'}\n"
            f"period: {finding.period_key}  scope: {finding.scope_key}\n"
            f"its evidence: {list(finding.evidence_ids)[:8]}\n\n"
            f"CHALLENGE ({challenge.weakness_class}):\n{challenge.text}\n\n"
            "ALLOWED CITATION IDS:\n"
            + "\n".join(f"  - {cid}" for cid in sorted(self.citable_ids)[:60])
        )
        try:
            payload = await _complete_json(self.client, system, user)
        except Exception:  # noqa: BLE001 - a failure is an unresolved challenge
            return None

        cited = [str(v).strip() for v in (payload.get("evidence_ids") or []) if v]
        real = [c for c in cited if c in self.citable_ids]
        self.discarded_citations.extend(c for c in cited if c not in self.citable_ids)
        confidence = payload.get("revised_confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        return Response(
            text=str(payload.get("text") or "").strip()[:2000],
            evidence_ids=tuple(real),
            responding_role=finding.originating_role,
            claims_resolved=bool(payload.get("claims_resolved")),
            revised_confidence=confidence,
            withdraw=bool(payload.get("withdraw")),
        )


async def _complete_json(client: Any, system: str, user: str) -> dict[str, Any]:
    if hasattr(client, "complete_json"):
        return await client.complete_json(
            system, user, max_tokens=MAX_TOKENS, timeout=TIMEOUT
        )
    response = await client.complete(
        system=system, user=user, max_tokens=MAX_TOKENS, timeout=TIMEOUT
    )
    payload = getattr(response, "payload", None)
    return payload if isinstance(payload, dict) else {}


__all__ = ["LLMRedTeam", "LLMResponder"]
