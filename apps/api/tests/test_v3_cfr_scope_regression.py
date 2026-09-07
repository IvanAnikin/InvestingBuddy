"""V3.10 — the CFR Group/segment regression, on the real sentence.

THE REQUIREMENT
===============
> **A Specialist Watchmakers figure must never be promoted to Group scope.**

The real Richemont FY26 annual report was ingested during V3.10 acceptance and its corpus
contains, verbatim:

    "The Group's Specialist Watchmakers reported sales of € 3.1 billion, down by 4% at
     actual exchange rates…"

That sentence is the trap in one line. It contains the word **Group**, it is about a
**segment**, and the €3.1bn figure belongs to the segment. A pipeline that read "Group"
and stamped the finding Group scope would produce exactly the defect this platform spent
a phase fixing.

WHAT THE ACCEPTANCE RUN ACTUALLY FOUND
======================================
The chunk carrying that sentence has **`scope_key = NULL`** — 170 of the 173 chunks from
that document do. So scope integrity here holds by **absence** rather than by correct
labelling: the finding inherits *no* scope, and unknown is not Group.

That is the safe direction and it is weaker than it could be. It is recorded here rather
than glossed, because a reader of the RC report needs to know the difference between "the
platform labelled it segment" and "the platform declined to label it at all".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.agents.investigator import LLMInvestigator

COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")

#: Verbatim from the real Richemont FY26 annual report, as ingested by the V3.10
#: acceptance run.
REAL_SENTENCE = (
    "The Group’s Specialist Watchmakers reported sales of € 3.1 billion, down "
    "by 4% at actual exchange rates, but up modestly at constant exchange rates."
)


@dataclass
class _Client:
    payload: dict[str, Any] = field(default_factory=dict)

    async def complete_json(self, system, user, **kwargs):  # noqa: ANN001, ANN003, ANN201
        return dict(self.payload)


@dataclass
class _ToolResult:
    ok: bool = True
    payload: dict[str, Any] | None = None
    contains_untrusted_content: bool = True


@dataclass
class _Session:
    results: dict[str, _ToolResult] = field(default_factory=dict)
    calls: list = field(default_factory=list)

    async def call(self, tool_name, arguments=None, *, task_ref=None):  # noqa: ANN001, ANN201
        self.calls.append((tool_name, dict(arguments or {})))
        return self.results.get(tool_name, _ToolResult(ok=False))


def _question():  # noqa: ANN202
    from app.services.director.planner import PlannedQuestion
    from app.services.ledger import store as ledger

    return PlannedQuestion(
        key="segment_discipline",
        text="What did each reported segment contribute?",
        origin=ledger.ORIGIN_PLAYBOOK,
        required_tools=frozenset({"search_company_corpus"}),
    )


def _corpus(scope_key: str | None) -> _ToolResult:
    return _ToolResult(
        payload={
            "items": [
                {
                    "evidence_id": "ev:c:watchmakers",
                    "text": REAL_SENTENCE,
                    "period_key": "2026",
                    "scope_key": scope_key,
                    "page_start": 9,
                }
            ]
        }
    )


async def _run(session, client):  # noqa: ANN001, ANN202
    return await LLMInvestigator(
        session=session, company_id=COMPANY, ticker="CFR", exchange="SW", client=client
    ).investigate(
        role_id="financial_analyst",
        questions=[_question()],
        round_index=0,
        remaining_tool_calls=10,
    )


class TestTheWatchmakersFigureIsNeverGroup:
    async def test_an_unscoped_chunk_yields_an_unscoped_finding_not_a_group_one(
        self,
    ) -> None:
        """What the real acceptance run produced. Unknown is not Group."""
        outcome = await _run(
            _Session(results={"search_company_corpus": _corpus(None)}),
            _Client(
                payload={
                    "findings": [
                        {
                            "statement": (
                                "Specialist Watchmakers sales were EUR 3.1 billion."
                            ),
                            "evidence_ids": ["ev:c:watchmakers"],
                        }
                    ]
                }
            ),
        )
        assert len(outcome.findings) == 1
        assert outcome.findings[0].scope_key is None
        assert outcome.findings[0].scope_key != "group"

    async def test_a_segment_scoped_chunk_yields_a_segment_scoped_finding(self) -> None:
        """When the corpus DOES resolve the scope, the finding carries it."""
        outcome = await _run(
            _Session(
                results={
                    "search_company_corpus": _corpus("segment:specialist watchmakers")
                }
            ),
            _Client(
                payload={
                    "findings": [
                        {
                            "statement": "Specialist Watchmakers sales were EUR 3.1bn.",
                            "evidence_ids": ["ev:c:watchmakers"],
                        }
                    ]
                }
            ),
        )
        assert outcome.findings[0].scope_key == "segment:specialist watchmakers"

    async def test_the_model_cannot_promote_it_to_group_by_saying_so(self) -> None:
        """The sentence contains the word "Group". A model that writes "the Group's
        sales were EUR 3.1 billion" still gets the SEGMENT scope from the evidence,
        because scope is inherited and never read out of prose."""
        outcome = await _run(
            _Session(
                results={
                    "search_company_corpus": _corpus("segment:specialist watchmakers")
                }
            ),
            _Client(
                payload={
                    "findings": [
                        {
                            "statement": "The Group's sales were EUR 3.1 billion.",
                            "evidence_ids": ["ev:c:watchmakers"],
                        }
                    ]
                }
            ),
        )
        assert outcome.findings[0].scope_key == "segment:specialist watchmakers"

    async def test_mixing_the_segment_figure_with_a_group_figure_is_refused(
        self,
    ) -> None:
        """The failure in full: one statement resting on a Group figure and the
        Watchmakers figure at once."""
        from app.services.ledger import store as ledger

        session = _Session(
            results={
                "search_company_corpus": _ToolResult(
                    payload={
                        "items": [
                            {
                                "evidence_id": "ev:c:watchmakers",
                                "text": REAL_SENTENCE,
                                "period_key": "2026",
                                "scope_key": "segment:specialist watchmakers",
                            },
                            {
                                "evidence_id": "ev:c:group",
                                "text": "Group sales were EUR 21.4 billion.",
                                "period_key": "2026",
                                "scope_key": "group",
                            },
                        ]
                    }
                )
            }
        )
        outcome = await _run(
            session,
            _Client(
                payload={
                    "findings": [
                        {
                            "statement": "Sales were EUR 3.1 billion.",
                            "evidence_ids": ["ev:c:watchmakers", "ev:c:group"],
                        }
                    ]
                }
            ),
        )
        assert outcome.findings == []
        assert any(
            g.gap_type == ledger.GAP_CONFLICTING_SOURCES and "scopes" in g.description
            for g in outcome.gaps
        )

    def test_the_real_sentence_is_the_trap_it_is_claimed_to_be(self) -> None:
        """A guard on the fixture itself: if somebody edits it into something harmless,
        the tests above stop testing anything."""
        assert "Group" in REAL_SENTENCE
        assert "Specialist Watchmakers" in REAL_SENTENCE
        assert "3.1 billion" in REAL_SENTENCE
