"""Corpus search tools — V3.3 Slice 3.4.

WHAT THESE TESTS PIN
====================
  * a semantic-only request is refused, and a query that neither names its companies
    nor declares itself cross-entity is refused — as RECORDED refusals with reasons,
    not raised exceptions, so "agents keep asking for unscoped search" is countable;
  * no argument accepts a backend query expression, a filter dict or an option bag;
  * period and scope filters actually constrain — a wrong-period hit is excluded even
    when the text matches perfectly;
  * every hit is citation-complete and carries a stable evidence id;
  * `contains_untrusted_content` is TRUE for a corpus hit and cannot be lowered by a
    payload, because a corpus hit is text from a fetched document;
  * `search_private_research` refuses every call with a POLICY reason, and says so
    distinctly from "found nothing";
  * "no backend configured" is distinguishable from "the corpus holds nothing";
  * the backend is injected — this slice takes no position on OPEN DECISION #1.

Real database, real chunking, real in-memory backend. No network.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import research_artifact as _research_artifact  # noqa: F401
from app.models import research_chunk as _research_chunk  # noqa: F401
from app.models import research_derivation as _research_derivation  # noqa: F401
from app.models import research_document as _research_document  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import research_tool_call as _research_tool_call  # noqa: F401
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.models.research_tool_call import ResearchToolCall
from app.services.agent_tools.contracts import (
    REFUSED_ACCESS_CLASS_NOT_PERMITTED,
    REFUSED_INVALID_ARGUMENTS,
    TOOL_SEARCH_COMPANY_CORPUS,
    TOOL_SEARCH_PRIVATE_RESEARCH,
)
from app.services.agent_tools.corpus_search import (
    MAX_TOP_K,
    PRIVATE_RESEARCH_DENIED,
    REQUESTABLE_MODES,
    SEARCH_COMPANY_CORPUS_SPEC,
    SEARCH_PRIVATE_RESEARCH_SPEC,
    validate_search_company_corpus,
)
from app.services.agent_tools.policy import (
    ROLE_BUSINESS_INDUSTRY_ANALYST,
    policy_for,
)
from app.services.agent_tools.registry import default_registry
from app.services.agent_tools.session import ToolSession
from app.services.corpus.indexing import index_version, persist_chunks
from app.services.corpus.parsed import build_parsed_document, persist_parsed_document
from app.services.corpus.policy import ACCESS_LICENSED_PRIVATE, ACCESS_USER_PRIVATE
from app.services.corpus.search import SearchMode
from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.sources.primary_document_extractor import (
    ExtractedBlock,
    ExtractedTable,
    PrimaryDocumentExtraction,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "v3_agent_tools_enabled": True,
        "v3_corpus_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


CFG = _cfg()


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _corpus(session, *, company_id=None, period_key="2025"):  # noqa: ANN001, ANN202
    """Seed one indexed document and return (company_id, backend)."""
    company_id = company_id or uuid.uuid4()
    document = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        document_key=f"annual_report:{period_key}",
        document_type="annual_report",
        first_seen_at=T0,
        last_seen_at=T0,
    )
    session.add(document)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=document.id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        canonical_url="https://pandoragroup.com/annual-report-2025.pdf",
        title=f"Annual Report {period_key}",
        transport="company_ir",
        source_tier=T1_PRIMARY_FILING,
        access_class="public_issuer",
        retrieved_at=T0,
        extraction_status="extracted",
        is_current=True,
        period_key=period_key,
        period_type="annual",
        language="en",
        published_at=date(2026, 2, 4),
    )
    session.add(version)
    await session.flush()

    parsed = build_parsed_document(
        PrimaryDocumentExtraction(
            content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status="extracted",
            page_count=169,
            language="en",
            blocks=[
                ExtractedBlock(
                    page_number=12,
                    section="Group results",
                    text=(
                        "Group revenue was DKK 32,549 million, up six per cent, and "
                        "cash flow from operations was DKK 7,400 million."
                    ),
                ),
                ExtractedBlock(
                    page_number=48,
                    section="Segment information",
                    text="Specialist Watchmakers revenue was EUR 107 million.",
                ),
            ],
            tables=[
                ExtractedTable(
                    table_location="p14:m0",
                    table_index=0,
                    page_number=14,
                    rows=[["DKK million", "2025"], ["Revenue", "32,549"]],
                    row_count=2,
                    col_count=2,
                    reconstructed=True,
                    column_periods=["2025"],
                    scope="Group",
                )
            ],
        )
    )
    derivation = await persist_parsed_document(
        session, version_id=version.id, parsed=parsed, cfg=CFG
    )
    assert derivation is not None and parsed is not None
    await persist_chunks(
        session, version=version, derivation=derivation, parsed=parsed, cfg=CFG
    )
    backend = InMemorySearchBackend()
    await index_version(
        session, research_document_version_id=version.id, backend=backend, cfg=CFG
    )
    await session.commit()
    return company_id, backend


def _session(session, cfg: Settings, *, backend=None, tools=None) -> ToolSession:
    return ToolSession(
        registry=default_registry(),
        policy=policy_for(
            ROLE_BUSINESS_INDUSTRY_ANALYST,
            tools=tools if tools is not None else {TOOL_SEARCH_COMPANY_CORPUS},
        ),
        cfg=cfg,
        db=session,
        search_backend=backend,
    )


# --------------------------------------------------------------------------- #
# The contract, as arguments
# --------------------------------------------------------------------------- #


class TestNoBackendSyntaxIsReachable:
    def test_no_argument_accepts_a_query_expression_or_a_filter_bag(self) -> None:
        # The rule that matters once an AGENT is the caller: an agent that can pass a
        # raw filter can pass a filter nobody reviewed.
        normalised = validate_search_company_corpus(
            {"query": "cash flow", "company_ids": [str(uuid.uuid4())]}
        )
        for key in normalised:
            assert key not in ("raw", "options", "filters", "query_dsl", "kwargs")
        signature = inspect.signature(validate_search_company_corpus)
        assert list(signature.parameters) == ["arguments"]

    def test_an_unknown_argument_is_dropped_rather_than_forwarded(self) -> None:
        normalised = validate_search_company_corpus(
            {
                "query": "cash flow",
                "allow_cross_entity": True,
                "backend_filter": {"$where": "1=1"},
            }
        )
        assert "backend_filter" not in normalised

    def test_only_lexical_and_hybrid_are_requestable(self) -> None:
        assert set(REQUESTABLE_MODES) == {"lexical", "hybrid"}
        assert SearchMode.SEMANTIC not in REQUESTABLE_MODES.values()


class TestTheTwoRefusalsAreRecordedNotRaised:
    async def test_a_semantic_only_request_is_refused_with_a_reason(
        self, session
    ) -> None:
        # Recorded rather than raised, so "agents keep asking for semantic-only search"
        # is a countable fact rather than a stack trace.
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue grew strongly", "company_ids": [str(company_id)],
             "mode": "semantic"},
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "semantically close to every revenue sentence" in (result.summary or "")
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert row.refusal_reason == REFUSED_INVALID_ARGUMENTS

    async def test_an_unscoped_query_is_refused(self, session) -> None:
        result = await _session(session, _cfg()).call(
            TOOL_SEARCH_COMPANY_CORPUS, {"query": "cash flow from operations"}
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "declare allow_cross_entity=True out loud" in (result.summary or "")

    async def test_a_cross_entity_query_is_allowed_when_declared(self, session) -> None:
        _, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "cash flow from operations", "allow_cross_entity": True},
        )
        await session.commit()
        assert result.ok is True

    async def test_a_missing_query_is_refused(self, session) -> None:
        result = await _session(session, _cfg()).call(
            TOOL_SEARCH_COMPANY_CORPUS, {"company_ids": [str(uuid.uuid4())]}
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS

    async def test_a_non_uuid_company_id_is_refused(self, session) -> None:
        result = await _session(session, _cfg()).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "cash flow", "company_ids": ["PNDORA"]},
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "not a UUID" in (result.summary or "")

    async def test_a_malformed_date_is_refused(self, session) -> None:
        result = await _session(session, _cfg()).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {
                "query": "cash flow",
                "allow_cross_entity": True,
                "published_from": "Feb 2026",
            },
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS


# --------------------------------------------------------------------------- #
# Real retrieval
# --------------------------------------------------------------------------- #


class TestRetrieval:
    async def test_a_hit_is_citation_complete_and_carries_a_stable_evidence_id(
        self, session
    ) -> None:
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "cash flow from operations", "company_ids": [str(company_id)]},
        )
        await session.commit()
        assert result.ok is True
        items = (result.payload or {})["items"]
        assert items, "the seeded document mentions cash flow from operations"
        hit = items[0]
        assert hit["evidence_id"].startswith("ev:")
        assert hit["citation_label"]
        assert hit["canonical_url"].endswith("annual-report-2025.pdf")
        assert hit["period_key"] == "2025" and hit["period_type"] == "annual"
        assert hit["source_tier"] == T1_PRIMARY_FILING
        assert hit["access_class"] == "public_issuer"
        assert hit["page_start"] is not None
        assert hit["company_id"] == str(company_id)

    async def test_a_period_filter_actually_constrains(self, session) -> None:
        # A semantically perfect match in the wrong period must be excluded — the
        # property the whole corpus query contract exists for.
        company_id, backend = await _corpus(session, period_key="2025")
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {
                "query": "cash flow from operations",
                "company_ids": [str(company_id)],
                "period_keys": ["2019"],
            },
        )
        await session.commit()
        assert (result.payload or {})["items"] == []

    async def test_a_scope_filter_actually_constrains(self, session) -> None:
        company_id, backend = await _corpus(session)
        group_only = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {
                "query": "revenue",
                "company_ids": [str(company_id)],
                "scope_types": ["group"],
            },
        )
        await session.commit()
        for hit in (group_only.payload or {})["items"]:
            assert hit["scope_type"] == "group", (
                "a Specialist Watchmakers span must not come back from a Group query"
            )

    async def test_another_companys_corpus_is_not_searched(self, session) -> None:
        company_id, backend = await _corpus(session)
        other = uuid.uuid4()
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "cash flow from operations", "company_ids": [str(other)]},
        )
        await session.commit()
        assert (result.payload or {})["items"] == []
        assert company_id != other

    async def test_top_k_is_bounded_by_the_tool_not_the_caller(self, session) -> None:
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {
                "query": "revenue",
                "company_ids": [str(company_id)],
                "top_k": 10_000,
            },
        )
        await session.commit()
        assert result.ok is True
        assert len((result.payload or {})["items"]) <= MAX_TOP_K
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert row.arguments_json["top_k"] == 10_000, "the request is recorded as asked"

    async def test_a_lexical_only_request_works(self, session) -> None:
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {
                "query": "cash flow from operations",
                "company_ids": [str(company_id)],
                "mode": "lexical",
            },
        )
        await session.commit()
        assert result.ok is True


class TestUntrustedContent:
    async def test_a_corpus_hit_is_labelled_untrusted(self, session) -> None:
        # Every hit is text from a fetched document, so a prompt builder must fence it.
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue", "company_ids": [str(company_id)]},
        )
        await session.commit()
        assert result.contains_untrusted_content is True
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert row.contains_untrusted_content is True

    def test_the_spec_declares_it_so_a_payload_cannot_lower_it(self) -> None:
        # Slice 3.1 makes the declaration a floor. The tool does not SANITISE the text:
        # a sanitiser is a filter an attacker iterates against.
        assert SEARCH_COMPANY_CORPUS_SPEC.may_contain_untrusted_content is True
        assert SEARCH_PRIVATE_RESEARCH_SPEC.may_contain_untrusted_content is True

    def test_the_search_tool_charges_a_search_and_says_which_unit_it_measures(
        self,
    ) -> None:
        assert SEARCH_COMPANY_CORPUS_SPEC.cost.searches == 1
        assert SEARCH_COMPANY_CORPUS_SPEC.instrumented_units == (
            "search_index_queries",
        )

    async def test_a_declared_unit_is_actually_reported_not_stored_as_zero(
        self, session
    ) -> None:
        # Found by the review pass, in this slice's own code: the spec declared
        # `search_index_queries` instrumented and the handler reported nothing, so the
        # audit row stored `search_index_queries: 0` for a search that DID issue a
        # query — the exact fabricated measurement `units_for` exists to prevent.
        company_id, backend = await _corpus(session)
        await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue", "company_ids": [str(company_id)]},
        )
        await session.commit()
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert row.instrumented_units_json == ["search_index_queries"]
        assert row.consumption_json["search_index_queries"] == 1

    async def test_a_missing_backend_reports_zero_as_a_measurement(
        self, session
    ) -> None:
        # Zero here IS a measurement: no query was issued because no backend exists.
        company_id, _ = await _corpus(session)
        await _session(session, _cfg(), backend=None).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue", "company_ids": [str(company_id)]},
        )
        await session.commit()
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert row.consumption_json["search_index_queries"] == 0


class TestNoBackendIsNotAnEmptyCorpus:
    async def test_it_says_so_rather_than_returning_nothing(self, session) -> None:
        # "No backend is configured" and "the corpus holds nothing" are different
        # answers, and an agent that cannot tell them apart will conclude the second.
        company_id, _ = await _corpus(session)
        result = await _session(session, _cfg(), backend=None).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "cash flow from operations", "company_ids": [str(company_id)]},
        )
        await session.commit()
        payload = result.payload or {}
        assert result.ok is True
        assert payload["items"] == []
        assert payload["backend_configured"] is False
        assert "not a statement about what the corpus contains" in payload["summary"]

    async def test_a_configured_backend_says_so_too(self, session) -> None:
        company_id, backend = await _corpus(session)
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue", "company_ids": [str(company_id)]},
        )
        assert (result.payload or {})["backend_configured"] is True

    def test_the_backend_is_injected_and_this_slice_chooses_nothing(self) -> None:
        # OPEN DECISION #1 is the user's. This module names no backend at all.
        import pathlib

        from tests.helpers.source_scan import identifiers_in

        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app/services/agent_tools/corpus_search.py"
        ).read_text(encoding="utf-8")
        used = identifiers_in(source)
        for banned in (
            "InMemorySearchBackend",
            "PostgresSearchBackend",
            "AzureSearchBackend",
            "pgvector",
        ):
            assert banned not in used


# --------------------------------------------------------------------------- #
# Private research fails closed
# --------------------------------------------------------------------------- #


class TestPrivateResearchFailsClosed:
    async def test_it_refuses_every_call_with_a_policy_reason(self, session) -> None:
        # The tool EXISTS rather than being omitted, because a missing tool is
        # indistinguishable from a tool that found nothing — and an agent is entitled
        # to know the difference between "there is no private research" and "you may
        # not read it".
        result = await _session(
            session,
            _cfg(),
            tools={TOOL_SEARCH_PRIVATE_RESEARCH},
        ).call(TOOL_SEARCH_PRIVATE_RESEARCH, {"query": "consultant study"})
        await session.commit()
        # It reaches the handler only if the role's access classes permit it; with none
        # declared, 3.1's governance check refuses it first.
        assert result.refusal_reason == REFUSED_ACCESS_CLASS_NOT_PERMITTED

    async def test_even_a_role_with_the_access_classes_gets_a_policy_refusal(
        self, session
    ) -> None:
        ts = ToolSession(
            registry=default_registry(),
            policy=policy_for(
                ROLE_BUSINESS_INDUSTRY_ANALYST,
                tools={TOOL_SEARCH_PRIVATE_RESEARCH},
                access_classes={ACCESS_LICENSED_PRIVATE, ACCESS_USER_PRIVATE},
            ),
            cfg=_cfg(),
            db=session,
        )
        result = await ts.call(
            TOOL_SEARCH_PRIVATE_RESEARCH, {"query": "consultant study"}
        )
        await session.commit()
        payload = result.payload or {}
        assert result.ok is True, "it answers; the answer is a refusal"
        assert payload["items"] == []
        assert payload["policy_established"] is False
        assert payload["refusal"] == PRIVATE_RESEARCH_DENIED
        assert "POLICY answer" in payload["summary"]
        assert "OPEN DECISION #11" in payload["summary"]

    def test_the_spec_declares_its_access_classes_as_the_registry_requires(
        self,
    ) -> None:
        # 3.1 makes a spec for this tool with no access classes a REGISTRATION error,
        # because declaring none reads as platform-internal and skips the check.
        assert set(SEARCH_PRIVATE_RESEARCH_SPEC.access_classes) == {
            ACCESS_LICENSED_PRIVATE,
            ACCESS_USER_PRIVATE,
        }

    def test_it_is_registered_rather_than_absent(self) -> None:
        assert TOOL_SEARCH_PRIVATE_RESEARCH in default_registry()
