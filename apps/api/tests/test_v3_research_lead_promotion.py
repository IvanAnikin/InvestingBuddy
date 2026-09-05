"""V3.4 Slice 4.4 — ResearchLead persistence and the verification gate.

WHAT THESE TESTS ARE FOR
========================
The gate's only job is to be the thing that stands between a provider's sentence and
the canonical record. So the tests that matter are the ones asserting it **refuses**,
and the one asserting it cannot be satisfied by the provider's own words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.research_lead import ResearchLeadRecord
from app.services.providers.contracts import (
    LEAD_PENDING,
    LEAD_REJECTED,
    LEAD_REJECTION_REASONS,
    LEAD_STATUSES,
    LEAD_UNVERIFIABLE,
    LEAD_VERIFIED,
    REJECTED_CLAIM_NOT_IN_SOURCE,
    REJECTED_DUPLICATE,
    REJECTED_PERIOD_MISMATCH,
    REJECTED_SCOPE_MISMATCH,
    REJECTED_SOURCE_NOT_PERMITTED,
    REJECTED_SUPERSEDED,
    REJECTED_URL_UNREACHABLE,
    REJECTED_VALUE_MISMATCH,
    ResearchLead,
)
from app.services.providers.leads import (
    KnownLead,
    LeadVerificationOutcome,
    known_leads_for,
    lead_key_for,
    numbers_in,
    parse_number,
    parse_number_candidates,
    persist_lead,
    slot_key_for,
    values_match,
    verification_survival_rate,
    verify_lead,
)
from app.services.sources.document_fetcher import DocumentFetchResult

T0 = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"v3_provider_runtime_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _lead(**overrides: Any) -> ResearchLead:
    base: dict[str, Any] = {
        "claim_text": "Group revenue was 31,338 million DKK in FY2025.",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "claimed_source_url": "https://issuer.example/annual-2025.pdf",
        "claimed_value": "31338",
        "claimed_unit": "currency",
        "claimed_currency": "DKK",
        "claimed_period": "2025",
        "claimed_scope": "group",
    }
    base.update(overrides)
    return ResearchLead(**base)


@dataclass
class _FetchLog:
    calls: list[tuple[str, tuple[str, ...], bool]]


def _fetcher(
    *,
    body: bytes | None = b"<html><body><p>Group revenue was 31,338 million DKK.</p></body></html>",
    document_type: str | None = "html",
    status_code: int | None = 200,
    error: str | None = None,
    blocked: bool = False,
    final_url: str | None = None,
    log: _FetchLog | None = None,
):
    async def _call(
        url: str,
        *,
        allowed_domains: tuple[str, ...],
        cfg: Any = None,
        resolve_ip: bool = False,
    ) -> DocumentFetchResult:
        if log is not None:
            log.calls.append((url, tuple(allowed_domains), resolve_ip))
        return DocumentFetchResult(
            requested_url=url,
            final_url=final_url or url,
            status_code=status_code,
            content_type="text/html",
            document_type=document_type,
            content=body,
            error=error,
            blocked=blocked,
        )

    return _call


# --------------------------------------------------------------------------- #
# Numeric reading — the layer a wrong answer here would silently corrupt
# --------------------------------------------------------------------------- #


class TestNumericReading:
    def test_the_three_separator_conventions_all_read_the_same(self) -> None:
        assert parse_number("1,234.5") == 1234.5
        assert parse_number("1.234,5") == 1234.5
        assert parse_number("1 234,5") == 1234.5

    def test_a_string_that_is_not_a_number_yields_nothing(self) -> None:
        # The regression this exists for: an earlier draft scraped digits and read
        # "Q1 2026" as 12026 — a number no document contains, handed to a comparison
        # that would then have "found" it.
        assert parse_number_candidates("Q1 2026") == []
        assert parse_number_candidates("abc") == []
        assert parse_number_candidates("1.2.3") == []
        assert parse_number_candidates("") == []

    def test_an_ambiguous_token_yields_both_readings_and_no_single_one(self) -> None:
        assert sorted(parse_number_candidates("1,234")) == [1.234, 1234.0]
        assert parse_number("1,234") is None

    def test_a_space_is_never_a_decimal_separator(self) -> None:
        assert parse_number("1 234") == 1234.0

    def test_accounting_parentheses_are_negative(self) -> None:
        assert parse_number_candidates("(1 234)") == [-1234.0]

    def test_edge_currency_is_stripped_and_interior_text_is_not(self) -> None:
        assert parse_number("EUR 1,234.5") == 1234.5
        assert parse_number("1,234.5 DKK") == 1234.5
        assert parse_number_candidates("1,2x34.5") == []

    def test_tolerance_admits_rounding_and_refuses_a_different_number(self) -> None:
        assert values_match(1234.5, 1234.50)
        assert values_match(1000.0, 1004.0)
        assert not values_match(1000.0, 1010.0)
        assert not values_match(1234.0, 1.234)

    def test_numbers_in_reads_a_document_and_skips_what_it_cannot_read(self) -> None:
        found = numbers_in("Revenue 1,234.5 in Q1 2026 versus 1 100,0")
        assert 1234.5 in found
        assert 1100.0 in found
        assert 12026.0 not in found


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


class TestKeys:
    def test_the_same_claim_from_two_providers_shares_a_lead_key(self) -> None:
        a = _lead(provider="deepseek")
        b = _lead(provider="azure_openai")
        assert lead_key_for(a) == lead_key_for(b)

    def test_a_different_value_is_a_different_lead_but_the_same_slot(self) -> None:
        a = _lead(claim_text="Revenue was 31338 m", claimed_value="31338")
        b = _lead(claim_text="Revenue was 30000 m", claimed_value="30000")
        assert lead_key_for(a) != lead_key_for(b)
        assert slot_key_for(a) == slot_key_for(b)

    def test_a_different_period_is_a_different_slot(self) -> None:
        a = _lead(claimed_period="2025")
        b = _lead(claimed_period="2024")
        assert slot_key_for(a) != slot_key_for(b)

    def test_a_different_subject_is_a_different_slot(self) -> None:
        one = _lead()
        assert slot_key_for(one, subject="a") != slot_key_for(one, subject="b")


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


class TestGate:
    async def test_a_claim_that_cites_nothing_is_unverifiable_not_rejected(
        self,
    ) -> None:
        log = _FetchLog(calls=[])
        outcome = await verify_lead(
            _lead(claimed_source_url=None),
            cfg=_cfg(),
            fetcher=_fetcher(log=log),
            allow_public_web=True,
        )
        assert outcome.status == LEAD_UNVERIFIABLE
        assert outcome.rejection_reason is None
        # And nothing was spent finding that out.
        assert log.calls == []

    async def test_a_verified_lead_carries_the_hash_of_bytes_we_fetched(self) -> None:
        outcome = await verify_lead(
            _lead(), cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED
        assert outcome.content_hash and len(outcome.content_hash) == 64
        assert outcome.fetch_attempted
        assert outcome.consumption.url_fetch_calls == 1

    async def test_a_claim_present_only_in_the_snippet_is_rejected(self) -> None:
        """The one rule this slice must not break.

        The lead's own snippet asserts the figure. The document InvestingBuddy fetched
        does not. A gate that read the provider's words would call this verified.
        """
        lead = _lead(claimed_value="99999")
        lead.claim_text = "Group revenue was 99,999 million DKK — per the provider."
        outcome = await verify_lead(
            lead,
            cfg=_cfg(),
            fetcher=_fetcher(body=b"<html><p>Group revenue was 31,338m DKK.</p></html>"),
            allow_public_web=True,
        )
        assert outcome.status == LEAD_REJECTED
        assert outcome.rejection_reason == REJECTED_VALUE_MISMATCH

    async def test_an_unreachable_source_is_url_unreachable(self) -> None:
        outcome = await verify_lead(
            _lead(),
            cfg=_cfg(),
            fetcher=_fetcher(body=None, status_code=404, error="http 404"),
            allow_public_web=True,
        )
        assert outcome.rejection_reason == REJECTED_URL_UNREACHABLE
        assert outcome.content_hash is None

    async def test_a_blocked_url_is_a_policy_refusal_not_an_unreachable_one(
        self,
    ) -> None:
        outcome = await verify_lead(
            _lead(),
            cfg=_cfg(),
            fetcher=_fetcher(body=None, blocked=True, error="unsafe/internal host"),
            allow_public_web=True,
        )
        assert outcome.rejection_reason == REJECTED_SOURCE_NOT_PERMITTED

    async def test_no_fetch_authority_refuses_before_the_network(self) -> None:
        log = _FetchLog(calls=[])
        outcome = await verify_lead(
            _lead(),
            cfg=_cfg(),
            fetcher=_fetcher(log=log),
            allowed_domains=(),
            allow_public_web=False,
        )
        assert outcome.rejection_reason == REJECTED_SOURCE_NOT_PERMITTED
        assert log.calls == []
        assert not outcome.fetch_attempted

    async def test_public_web_mode_pins_the_allowlist_to_the_cited_host(self) -> None:
        """Governance §3: the allowlist is replaced by a policy, not by nothing.

        A redirect off the cited host is still blocked, because the host IS the
        allowlist for this one fetch.
        """
        log = _FetchLog(calls=[])
        await verify_lead(
            _lead(), cfg=_cfg(), fetcher=_fetcher(log=log), allow_public_web=True
        )
        assert log.calls[0][1] == ("issuer.example",)
        # And DNS pinning is not optional once we leave the issuer allowlist.
        assert log.calls[0][2] is True

    async def test_a_period_mismatch_is_refused_against_the_platforms_own_reading(
        self,
    ) -> None:
        outcome = await verify_lead(
            _lead(claimed_period="H1 2025"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
            source_period="2025",
        )
        assert outcome.rejection_reason == REJECTED_PERIOD_MISMATCH

    async def test_an_unknown_period_confirms_nothing_and_refuses_nothing(self) -> None:
        outcome = await verify_lead(
            _lead(claimed_period="whenever"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
            source_period="2025",
        )
        assert outcome.status == LEAD_VERIFIED
        assert outcome.period_verified is False

    async def test_verified_does_not_mean_period_verified(self) -> None:
        outcome = await verify_lead(
            _lead(), cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED
        assert outcome.period_verified is False
        assert outcome.scope_verified is False

    async def test_period_verified_is_true_only_when_it_was_compared(self) -> None:
        outcome = await verify_lead(
            _lead(claimed_period="2025"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
            source_period="2025",
            source_scope="group",
        )
        assert outcome.period_verified is True
        assert outcome.scope_verified is True

    async def test_a_segment_figure_claimed_as_group_is_refused(self) -> None:
        """The CFR failure mode, as a gate rather than as a review rule."""
        outcome = await verify_lead(
            _lead(claimed_scope="group"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
            source_scope="Specialist Watchmakers",
        )
        assert outcome.rejection_reason == REJECTED_SCOPE_MISMATCH

    async def test_a_claim_with_no_value_is_checked_as_text(self) -> None:
        lead = _lead(claimed_value=None)
        lead.claim_text = "Group revenue was 31,338 million DKK."
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        assert outcome.status == LEAD_VERIFIED

    async def test_text_absent_from_the_source_is_claim_not_in_source(self) -> None:
        lead = _lead(claimed_value=None)
        lead.claim_text = "The company announced a share buyback."
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        assert outcome.rejection_reason == REJECTED_CLAIM_NOT_IN_SOURCE

    async def test_an_undecodable_document_leaves_the_lead_undecided(self) -> None:
        """A scanned PDF is a limitation of the reader, never a lie by the provider.

        Rejecting here would make every scanned European annual report look like a
        lying vendor and would move ``verification_survival_rate`` in the direction
        that looks like diligence.
        """
        outcome = await verify_lead(
            _lead(),
            cfg=_cfg(),
            fetcher=_fetcher(body=b"%PDF-1.4 not really a pdf", document_type="pdf"),
            allow_public_web=True,
        )
        assert outcome.status == LEAD_PENDING
        assert outcome.rejection_reason is None
        assert "limitation of the retrieval" in (outcome.detail or "")
        # The bytes still get a hash: we did fetch something.
        assert outcome.content_hash

    async def test_a_claim_absent_from_a_partly_read_document_is_not_a_rejection(
        self,
    ) -> None:
        """Absence proves nothing when only part of the document was read.

        The V2 reader stops at ``primary_document_max_pdf_pages``. A figure on page 100
        of a 169-page annual report is not a fabrication, and the corpus already refuses
        to confuse "we never read that page" with "that page does not say it".
        """
        import app.services.providers.leads as leads_module

        async def _short_read(url, *, allowed_domains, cfg=None, resolve_ip=False):
            return DocumentFetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                document_type="html",
                content=b"<html><body><p>" + b"unrelated prose. " * 30 + b"</p></body></html>",
            )

        real = leads_module._document_text
        try:
            leads_module._document_text = lambda content, dt, cfg: ("unrelated", False)
            outcome = await verify_lead(
                _lead(),
                cfg=_cfg(),
                fetcher=_short_read,
                allow_public_web=True,
            )
        finally:
            leads_module._document_text = real
        assert outcome.status == LEAD_PENDING
        assert outcome.rejection_reason is None
        assert "proves nothing" in (outcome.detail or "")

    async def test_a_claim_absent_from_a_fully_read_document_is_a_rejection(
        self,
    ) -> None:
        outcome = await verify_lead(
            _lead(claimed_value="99999"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
        )
        assert outcome.status == LEAD_REJECTED
        assert outcome.rejection_reason == REJECTED_VALUE_MISMATCH

    async def test_a_value_with_no_numeric_reading_is_refused(self) -> None:
        outcome = await verify_lead(
            _lead(claimed_value="about a third"),
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
        )
        assert outcome.rejection_reason == REJECTED_VALUE_MISMATCH

    async def test_a_duplicate_is_detected_before_a_fetch_is_spent(self) -> None:
        lead = _lead()
        log = _FetchLog(calls=[])
        outcome = await verify_lead(
            lead,
            cfg=_cfg(),
            fetcher=_fetcher(log=log),
            allow_public_web=True,
            known_leads=[
                KnownLead(
                    lead_key=lead_key_for(lead),
                    slot_key=slot_key_for(lead),
                    status=LEAD_VERIFIED,
                )
            ],
        )
        assert outcome.rejection_reason == REJECTED_DUPLICATE
        assert log.calls == []

    async def test_an_older_claim_about_a_slot_already_verified_is_superseded(
        self,
    ) -> None:
        older = _lead(claimed_value="30000", claimed_date=date(2025, 1, 1))
        older.claim_text = "Group revenue was 30,000 million DKK."
        newer = _lead(claimed_value="31338", claimed_date=date(2026, 1, 1))
        newer.claim_text = "Group revenue was 31,338 million DKK."
        outcome = await verify_lead(
            older,
            cfg=_cfg(),
            fetcher=_fetcher(),
            allow_public_web=True,
            known_leads=[
                KnownLead(
                    lead_key=lead_key_for(newer),
                    slot_key=slot_key_for(newer),
                    status=LEAD_VERIFIED,
                    source_date=date(2026, 1, 1),
                )
            ],
        )
        assert outcome.rejection_reason == REJECTED_SUPERSEDED

    async def test_supersession_needs_two_known_dates(self) -> None:
        """A missing date is not an ordering, and half the time it is the wrong one."""
        older = _lead(claimed_value="30000", claimed_date=None)
        older.claim_text = "Group revenue was 30,000 million DKK."
        newer = _lead(claimed_value="31338")
        newer.claim_text = "Group revenue was 31,338 million DKK."
        outcome = await verify_lead(
            older,
            cfg=_cfg(),
            fetcher=_fetcher(
                body=b"<html><p>Group revenue was 30,000 million DKK.</p></html>"
            ),
            allow_public_web=True,
            known_leads=[
                KnownLead(
                    lead_key=lead_key_for(newer),
                    slot_key=slot_key_for(newer),
                    status=LEAD_VERIFIED,
                    source_date=None,
                )
            ],
        )
        assert outcome.status == LEAD_VERIFIED

    async def test_the_fetched_url_is_the_one_that_produced_the_bytes(self) -> None:
        outcome = await verify_lead(
            _lead(),
            cfg=_cfg(),
            fetcher=_fetcher(final_url="https://issuer.example/final.pdf"),
            allow_public_web=True,
        )
        assert outcome.fetched_url == "https://issuer.example/final.pdf"


# --------------------------------------------------------------------------- #
# The outcome type refuses to represent an incoherent decision
# --------------------------------------------------------------------------- #


class TestOutcomeVocabulary:
    def test_a_reason_outside_the_vocabulary_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="not a recognised rejection reason"):
            LeadVerificationOutcome(
                lead=_lead(), status=LEAD_REJECTED, rejection_reason="felt_wrong"
            )

    def test_a_rejection_with_no_reason_cannot_be_built(self) -> None:
        with pytest.raises(ValueError):
            LeadVerificationOutcome(lead=_lead(), status=LEAD_REJECTED)

    def test_a_reason_beside_a_verified_outcome_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="face value"):
            LeadVerificationOutcome(
                lead=_lead(),
                status=LEAD_VERIFIED,
                content_hash="a" * 64,
                rejection_reason=REJECTED_DUPLICATE,
            )

    def test_verified_without_a_platform_fetch_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="fetched itself"):
            LeadVerificationOutcome(lead=_lead(), status=LEAD_VERIFIED)

    def test_every_rejection_reason_the_gate_can_return_is_in_the_vocabulary(
        self,
    ) -> None:
        used = {
            REJECTED_CLAIM_NOT_IN_SOURCE,
            REJECTED_DUPLICATE,
            REJECTED_PERIOD_MISMATCH,
            REJECTED_SCOPE_MISMATCH,
            REJECTED_SOURCE_NOT_PERMITTED,
            REJECTED_SUPERSEDED,
            REJECTED_URL_UNREACHABLE,
            REJECTED_VALUE_MISMATCH,
        }
        assert used == set(LEAD_REJECTION_REASONS)
        assert LEAD_PENDING in LEAD_STATUSES


# --------------------------------------------------------------------------- #
# Persistence, against a real database
# --------------------------------------------------------------------------- #


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(sa_text("PRAGMA foreign_keys=ON"))
        yield s
    await engine.dispose()


class TestPersistence:
    async def test_a_rejection_is_stored_with_its_reason(self, session) -> None:
        lead = _lead()
        outcome = await verify_lead(
            lead,
            cfg=_cfg(),
            fetcher=_fetcher(body=None, status_code=404, error="http 404"),
            allow_public_web=True,
        )
        record = await persist_lead(session, lead, outcome)
        await session.commit()
        assert record.status == LEAD_REJECTED
        assert record.rejection_reason == REJECTED_URL_UNREACHABLE
        assert record.fetched_content_hash is None
        assert record.verified_at is None

    async def test_a_rejection_keeps_the_hash_of_what_we_read(self, session) -> None:
        """"We read this document and it does not say that" is what makes a refusal
        checkable. A hash kept only on success leaves every rejection unauditable."""
        lead = _lead(claimed_value="99999")
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        record = await persist_lead(session, lead, outcome)
        await session.commit()
        assert record.status == LEAD_REJECTED
        assert record.fetched_content_hash == outcome.content_hash

    async def test_a_verified_lead_stores_the_hash_and_the_time(self, session) -> None:
        lead = _lead()
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        record = await persist_lead(session, lead, outcome, now=T0)
        await session.commit()
        assert record.status == LEAD_VERIFIED
        assert record.fetched_content_hash == outcome.content_hash
        assert record.verified_at == T0

    async def test_a_verified_row_without_a_hash_is_unstorable(self, session) -> None:
        """The CHECK, exercised as a statement rather than trusted as a docstring."""
        session.add(
            ResearchLeadRecord(
                id=uuid.uuid4(),
                provider="deepseek",
                lead_key="k" * 64,
                slot_key="s" * 64,
                claim_text="anything",
                claimed_source_url="https://issuer.example/x",
                status=LEAD_VERIFIED,
                fetched_content_hash=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_a_rejected_row_without_a_reason_is_unstorable(self, session) -> None:
        session.add(
            ResearchLeadRecord(
                id=uuid.uuid4(),
                provider="deepseek",
                lead_key="k" * 64,
                slot_key="s" * 64,
                claim_text="anything",
                status=LEAD_REJECTED,
                rejection_reason=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_an_unverifiable_row_that_cites_a_source_is_unstorable(
        self, session
    ) -> None:
        session.add(
            ResearchLeadRecord(
                id=uuid.uuid4(),
                provider="deepseek",
                lead_key="k" * 64,
                slot_key="s" * 64,
                claim_text="anything",
                claimed_source_url="https://issuer.example/x",
                status=LEAD_UNVERIFIABLE,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_a_lead_outlives_the_company_it_referred_to(self, session) -> None:
        company = Company(
            id=uuid.uuid4(), ticker="PNDORA", exchange="CO", name="Pandora", status="new"
        )
        session.add(company)
        await session.commit()
        lead = _lead()
        outcome = await verify_lead(
            lead, cfg=_cfg(), fetcher=_fetcher(), allow_public_web=True
        )
        await persist_lead(session, lead, outcome, company_id=company.id)
        await session.commit()
        await session.delete(company)
        await session.commit()
        rows = (
            await session.execute(sa_text("SELECT company_id, status FROM research_leads"))
        ).all()
        assert len(rows) == 1
        assert rows[0][0] is None
        assert rows[0][1] == LEAD_VERIFIED

    async def test_claim_text_is_clipped_not_refused(self, session) -> None:
        lead = _lead(claimed_value=None)
        lead.claim_text = "x" * 9000
        outcome = LeadVerificationOutcome(
            lead=lead, status=LEAD_UNVERIFIABLE, detail="y" * 4000
        )
        lead.claimed_source_url = None
        record = await persist_lead(session, lead, outcome)
        await session.commit()
        assert len(record.claim_text) == 2000
        assert len(record.rejection_detail or "") == 500

    async def test_survival_rate_is_computed_per_provider_and_unknown_is_none(
        self, session
    ) -> None:
        for provider, status, extra in (
            ("deepseek", LEAD_VERIFIED, {"fetched_content_hash": "a" * 64}),
            ("deepseek", LEAD_VERIFIED, {"fetched_content_hash": "b" * 64}),
            ("deepseek", LEAD_REJECTED, {"rejection_reason": REJECTED_DUPLICATE}),
            ("azure_openai", LEAD_UNVERIFIABLE, {}),
        ):
            session.add(
                ResearchLeadRecord(
                    id=uuid.uuid4(),
                    provider=provider,
                    lead_key=uuid.uuid4().hex,
                    slot_key=uuid.uuid4().hex,
                    claim_text="c",
                    status=status,
                    **extra,
                )
            )
        await session.commit()
        rates = {r.provider: r for r in await verification_survival_rate(session)}
        assert rates["deepseek"].verified == 2
        assert rates["deepseek"].rate == pytest.approx(2 / 3)
        assert rates["azure_openai"].rate == 0.0
        # A provider with nothing decided has an UNKNOWN rate, never 0.0.
        from app.services.providers.leads import SurvivalRate

        assert SurvivalRate("exa", 0, 0, 0, 0).rate is None

    async def test_known_leads_bounds_the_population_the_caller_asked_for(
        self, session
    ) -> None:
        """A filter in Python after a LIMIT in SQL returns the wrong rows.

        Twenty leads for another company and two for this one, asked for with a limit of
        five: the five must come from THIS company's leads.
        """
        mine = Company(
            id=uuid.uuid4(), ticker="CFR", exchange="SW", name="Richemont", status="new"
        )
        theirs = Company(
            id=uuid.uuid4(), ticker="MC", exchange="PA", name="LVMH", status="new"
        )
        session.add_all([mine, theirs])
        await session.commit()
        for i in range(20):
            session.add(
                ResearchLeadRecord(
                    id=uuid.uuid4(),
                    company_id=theirs.id,
                    provider="deepseek",
                    lead_key=f"theirs-{i}",
                    slot_key=f"theirs-{i}",
                    claim_text="c",
                    status=LEAD_PENDING,
                )
            )
        for i in range(2):
            session.add(
                ResearchLeadRecord(
                    id=uuid.uuid4(),
                    company_id=mine.id,
                    provider="deepseek",
                    lead_key=f"mine-{i}",
                    slot_key=f"mine-{i}",
                    claim_text="c",
                    status=LEAD_PENDING,
                )
            )
        await session.commit()
        known = await known_leads_for(session, company_id=mine.id, limit=5)
        assert {k.lead_key for k in known} == {"mine-0", "mine-1"}
