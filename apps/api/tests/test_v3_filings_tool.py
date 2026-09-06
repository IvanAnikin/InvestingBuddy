"""V3.10 corrective — the `get_recent_filings` tool.

WHY THIS IS A CORRECTIVE
========================
The first real MRNA acceptance run could not convene its Council. Biotech's **blocking**
`pipeline_state` question requires `get_recent_filings`, which V3.3 reserved and never
implemented — so the Director refused to assign it, raised a `tool_unavailable` gap, and
the run reported insufficient evidence.

Every one of those behaviours was correct. The outcome was still that a biotech run could
never complete, and only running a real issuer end to end surfaced it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.config import Settings
from app.services.agent_tools.filings import (
    GET_RECENT_FILINGS_SPEC,
    MAX_LIMIT,
    validate_get_recent_filings,
)


@dataclass
class _Context:
    cfg: Settings
    session: Any = None
    company_id: uuid.UUID | None = None
    role: str = "risk_analyst"


@dataclass
class _Event:
    id: str = "evt-1"
    form_type: str = "10-K"
    filing_date: str = "2026-02-20"
    report_date: str = "2025-12-31"
    accession_number: str = "0001682852-26-000033"
    headline: str = "Annual report"
    source_url: str = "https://www.sec.gov/Archives/edgar/data/1682852/x.htm"
    related_filing_url: str | None = None
    item_numbers: list[str] = field(default_factory=list)
    source_tier: str = "T2_regulator_or_gov"


@dataclass
class _Result:
    ticker: str = "MRNA"
    cik: str | None = "0001682852"
    events: list[_Event] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TestValidation:
    def test_a_ticker_is_required(self) -> None:
        """It asks a regulator about an issuer, and an issuer is not a row id."""
        with pytest.raises(ValueError, match="not a row id"):
            validate_get_recent_filings({"exchange": "NASDAQ"})

    def test_the_limit_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            validate_get_recent_filings({"ticker": "MRNA", "limit": MAX_LIMIT + 1})

    def test_the_lookback_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            validate_get_recent_filings({"ticker": "MRNA", "lookback_days": 99_999})

    def test_a_ticker_is_normalised(self) -> None:
        assert validate_get_recent_filings({"ticker": " mrna "})["ticker"] == "MRNA"


class TestGating:
    async def test_the_flag_off_returns_an_honest_empty_result(self) -> None:
        """Never a silent zero: the summary names the flag."""
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=False)),
            validate_get_recent_filings({"ticker": "MRNA", "exchange": "NASDAQ"}),
        )
        assert result["items"] == []
        assert "V3_FILINGS_TOOL_ENABLED is off" in result["summary"]
        assert "not about the issuer's filings" in result["summary"]

    async def test_a_non_sec_venue_is_refused_without_a_lookup(self) -> None:
        """The Boeing/BAE rule. SEC's ticker index covers US registrants, and looking a
        non-US local ticker up in it does not fail — it returns an unrelated US issuer."""
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings({"ticker": "BA", "exchange": "LSE"}),
        )
        assert result["items"] == []
        assert "unrelated US issuer" in result["summary"]

    def test_the_tool_declares_that_it_fetches_and_that_it_is_untrusted(self) -> None:
        assert GET_RECENT_FILINGS_SPEC.may_contain_untrusted_content is True
        assert GET_RECENT_FILINGS_SPEC.cost.fetches == 1


class TestResults:
    async def test_metadata_is_returned_and_contents_are_not(self, monkeypatch) -> None:
        """Turning a filing into citable text is the corpus's job; a tool that returned
        prose would let an agent cite a document nobody fetched."""

        class _Provider:
            async def get_recent_events(self, ticker, **kwargs):  # noqa: ANN001, ANN003, ANN201
                return _Result(events=[_Event()])

        monkeypatch.setattr(
            "app.integrations.providers.sec_recent_filings_provider."
            "SecRecentFilingsProvider",
            _Provider,
        )
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings({"ticker": "MRNA", "exchange": "NASDAQ"}),
        )
        item = result["items"][0]
        assert item["form_type"] == "10-K"
        assert item["accession_number"]
        assert item["source_url"].startswith("https://")
        # No contents, under any key.
        assert not {"text", "content", "body", "excerpt"} & set(item)

    async def test_the_citable_id_is_the_providers_stable_one(self, monkeypatch) -> None:
        """An id minted per call would make a citation unresolvable the moment it was
        stored."""

        class _Provider:
            async def get_recent_events(self, ticker, **kwargs):  # noqa: ANN001, ANN003, ANN201
                return _Result(events=[_Event(id="stable-event-id")])

        monkeypatch.setattr(
            "app.integrations.providers.sec_recent_filings_provider."
            "SecRecentFilingsProvider",
            _Provider,
        )
        first = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings({"ticker": "MRNA", "exchange": "NASDAQ"}),
        )
        second = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings({"ticker": "MRNA", "exchange": "NASDAQ"}),
        )
        assert first["items"][0]["id"] == second["items"][0]["id"] == "stable-event-id"

    async def test_a_form_type_filter_is_applied(self, monkeypatch) -> None:
        class _Provider:
            async def get_recent_events(self, ticker, **kwargs):  # noqa: ANN001, ANN003, ANN201
                return _Result(
                    events=[_Event(form_type="10-K"), _Event(id="e2", form_type="8-K")]
                )

        monkeypatch.setattr(
            "app.integrations.providers.sec_recent_filings_provider."
            "SecRecentFilingsProvider",
            _Provider,
        )
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings(
                {"ticker": "MRNA", "exchange": "NASDAQ", "form_types": ["8-K"]}
            ),
        )
        assert [i["form_type"] for i in result["items"]] == ["8-K"]

    async def test_provider_warnings_travel(self, monkeypatch) -> None:
        class _Provider:
            async def get_recent_events(self, ticker, **kwargs):  # noqa: ANN001, ANN003, ANN201
                return _Result(events=[], warnings=["SEC CIK not available"])

        monkeypatch.setattr(
            "app.integrations.providers.sec_recent_filings_provider."
            "SecRecentFilingsProvider",
            _Provider,
        )
        result = await GET_RECENT_FILINGS_SPEC.handler(
            _Context(cfg=Settings(v3_filings_tool_enabled=True)),
            validate_get_recent_filings({"ticker": "ZZZZ", "exchange": "NASDAQ"}),
        )
        assert result["warnings"] == ["SEC CIK not available"]
        assert result["items"] == []


class TestItClosesTheGapThatFoundIt:
    def test_the_tool_is_now_implemented(self) -> None:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry

        assert "get_recent_filings" in register_builtins(ToolRegistry()).names()

    async def test_biotechs_blocking_question_is_now_assignable(self) -> None:
        """The exact failure the first real MRNA run produced: `pipeline_state` was
        unassignable because nothing implemented `get_recent_filings`."""
        from app.services.director.planner import plan_research
        from app.services.playbooks.industries import BIOTECH

        class _Adapter:
            playbook_id = BIOTECH.playbook_id
            version = BIOTECH.version

            def mandatory_questions(self):  # noqa: ANN201
                return BIOTECH.mandatory_questions()

            def specialist_roles(self):  # noqa: ANN201
                return BIOTECH.specialist_role_ids()

            def completion_rules(self):  # noqa: ANN201
                return BIOTECH.completion_rule_ids()

        plan = await plan_research(subject="MRNA", playbooks=[_Adapter()])
        unassignable = {key for key, _ in plan.unassignable}
        assert "pipeline_state" not in unassignable
        assert "regulatory_posture" not in unassignable
