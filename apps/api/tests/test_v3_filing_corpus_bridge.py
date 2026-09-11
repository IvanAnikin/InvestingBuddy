"""The filing-to-corpus evidence bridge — V3.16.

THE DEFECT THESE TESTS EXIST FOR
================================
`search_company_corpus` searches `research_document_chunks` and nothing else. Chunks are
created only by `persist_chunks`, which runs only when `build_parsed_document` returns a
parsed document, which happens only when the extraction carried **blocks or tables**.

Two production paths produce a document with no blocks:

1. `backfill_from_extracted_documents` calls only `upsert_document_version`. It creates
   documents and versions and **zero** derivations, pages or chunks.
2. `load_reusable_documents` rebuilds a cached artifact from *bounded excerpts*, not
   blocks — so a cache hit inside the reuse TTL produces a version and no chunks, for
   ever, because the reuse is what prevents the re-extraction that would create them.

Both end in the same place: a corpus row exists, `documents > 0` looks like success, and
the specialist searching for pipeline evidence gets nothing. **A version row is not
searchability**, and the whole point of `is_corpus_search_ready` is that exactly one
place in the codebase is allowed to decide what is.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument


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


def _cfg(**over) -> Settings:  # noqa: ANN003
    base = {
        "v3_corpus_enabled": True,
        "primary_document_ingestion_enabled": True,
        "report_citation_persistence_enabled": True,
        "primary_document_sec_body_enabled": True,
        "v3_filings_tool_enabled": True,
        "v3_filing_body_bridge_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


ACCESSION = "0001682852-25-000006"
ACCESSION_NODASH = "000168285225000006"
CIK = "0001682852"
FILING_URL = (
    f"https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION_NODASH}/mrna-20241231.htm"
)


async def _company(session, ticker="MRNA") -> Company:  # noqa: ANN001
    c = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="US",
        name=f"{ticker} Inc.",
        status="new",
    )
    session.add(c)
    await session.flush()
    return c


async def _historical_extracted_document(session, company, url=FILING_URL):  # noqa: ANN001
    """A V2 row exactly as production holds them: extracted, excerpts only, no bytes."""
    doc = ExtractedDocument(
        id=uuid.uuid4(),
        company_id=company.id,
        canonical_url=url,
        content_hash="a" * 64,
        source_type="sec_filing",
        provider="sec_edgar",
        source_tier="T1_primary_filing",
        title="10-K 0001682852-25-000006",
        mime_type="text/html",
        status="extracted",
        extraction_method="html",
        retrieved_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        excerpts_json=[{"text": "Total revenue was $3.2 billion for the full year 2024."}],
        pipeline_version=15,
    )
    session.add(doc)
    await session.flush()
    return doc


# ---------------------------------------------------------------------------
# 1. The readiness predicate — one definition, and it is not "a row exists"
# ---------------------------------------------------------------------------


async def test_a_version_row_alone_is_not_corpus_search_ready(session):
    """THE false success this whole slice exists to refuse.

    `backfill_from_extracted_documents` produces exactly this state: a document and a
    version and no chunks. Reporting it as ready is how `documents > 0` gets mistaken
    for searchable evidence.
    """
    from app.services.corpus.documents import backfill_from_extracted_documents
    from app.services.corpus.filing_evidence import is_corpus_search_ready

    company = await _company(session)
    await _historical_extracted_document(session, company)
    cfg = _cfg()

    result = await backfill_from_extracted_documents(session, cfg=cfg, company_id=company.id)

    # The backfill genuinely created corpus rows...
    assert result.versions_created == 1
    # ...and the filing is still NOT searchable.
    ready = await is_corpus_search_ready(
        session, company_id=company.id, accession=ACCESSION
    )
    assert ready is False


async def test_ready_requires_a_current_version_an_active_derivation_and_chunks(session):
    from app.services.corpus.filing_evidence import is_corpus_search_ready

    company = await _company(session)
    # Nothing at all.
    assert await is_corpus_search_ready(
        session, company_id=company.id, accession=ACCESSION
    ) is False


# ---------------------------------------------------------------------------
# 2. The cache-hit dead zone (§7) — the regression, written first
# ---------------------------------------------------------------------------


async def test_a_cached_document_without_chunks_is_not_sufficient_for_corpus_evidence(
    session,
):
    """The dead zone, stated as a property.

    `load_reusable_documents` rebuilds an artifact from bounded excerpts. That artifact
    has no blocks, so ingesting it creates a version and no chunks — and because the
    reuse is what skips the re-extraction, nothing will ever create them. The bridge
    must treat "cached but chunkless" as NOT satisfying a corpus-evidence requirement.
    """
    from app.services.corpus.filing_evidence import filing_evidence_state

    company = await _company(session)
    await _historical_extracted_document(session, company)

    state = await filing_evidence_state(
        session, company_id=company.id, accession=ACCESSION
    )
    # STATE B: the historical document exists, and it is not evidence.
    assert state.state == "historical_without_chunks"
    assert state.extracted_document_id is not None
    assert state.is_ready is False


async def test_no_document_at_all_is_state_c(session):
    from app.services.corpus.filing_evidence import filing_evidence_state

    company = await _company(session)
    state = await filing_evidence_state(
        session, company_id=company.id, accession=ACCESSION
    )
    assert state.state == "absent"
    assert state.is_ready is False


# ---------------------------------------------------------------------------
# 3. Company isolation (§3) — another company's filing never satisfies this one
# ---------------------------------------------------------------------------


async def test_another_companys_document_never_satisfies_this_company(session):
    """Content hash and URL can coincide; company identity may not be inferred from
    either. A co-filed or mirrored document must not make this company look ready."""
    from app.services.corpus.filing_evidence import filing_evidence_state

    mine = await _company(session, "MRNA")
    theirs = await _company(session, "BIIB")
    # The document belongs to the OTHER company, at the same URL and hash.
    await _historical_extracted_document(session, theirs)

    state = await filing_evidence_state(
        session, company_id=mine.id, accession=ACCESSION
    )
    assert state.state == "absent"
    assert state.extracted_document_id is None


# ---------------------------------------------------------------------------
# 4. Identity (§3) — accession, not title or bare URL string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0001682852-25-000006", "0001682852-25-000006"),
        ("000168285225000006", "0001682852-25-000006"),
        (" 0001682852-25-000006 ", "0001682852-25-000006"),
        ("", None),
        (None, None),
        ("not-an-accession", None),
        ("0001682852-25-00000", None),  # too short
    ],
)
def test_accession_identity_is_normalised_or_refused(raw, expected):
    from app.services.corpus.filing_evidence import canonical_accession

    assert canonical_accession(raw) == expected


def test_an_amendment_is_a_different_filing(session):  # noqa: ANN001
    """10-K/A has its own accession. Treating it as the 10-K would let an amendment
    silently satisfy a request for the original, or vice versa."""
    from app.services.corpus.filing_evidence import canonical_accession

    original = canonical_accession("0001682852-25-000006")
    amendment = canonical_accession("0001682852-25-000007")
    assert original != amendment


# ---------------------------------------------------------------------------
# 5. Fail closed (§16.7, §16.8)
# ---------------------------------------------------------------------------


async def test_a_malformed_accession_fetches_nothing_and_fails_closed(session):
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    calls: list = []

    async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append(a)
        raise AssertionError("no network may be attempted for a malformed accession")

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession="../../etc/passwd",
        form="10-K",
        cfg=_cfg(),
        acquire=_never,
    )
    assert result.state == "unavailable"
    assert result.reason == "malformed_accession"
    assert calls == []
    assert result.fetched is False


async def test_the_bridge_is_inert_when_its_flag_is_off(session):
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)

    async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("no network with the bridge disabled")

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(v3_filing_body_bridge_enabled=False),
        acquire=_never,
    )
    assert result.state == "unavailable"
    assert result.reason == "bridge_disabled"
    assert result.fetched is False


# ---------------------------------------------------------------------------
# 6. The bridge end to end, with a stubbed acquirer (no network in unit tests)
# ---------------------------------------------------------------------------


async def _make_searchable(session, company, accession=ACCESSION, url=FILING_URL):  # noqa: ANN001
    """Create the full chain a real acquisition produces: document → version →
    active derivation → indexable chunk."""
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    doc = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company.id,
        document_key=f"sec_filing:{accession}",
        document_type="sec_filing",
        title=f"10-K {accession}",
    )
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=doc.id,
        content_hash="b" * 64,
        canonical_url=url,
        transport="sec_edgar",
        source_tier="T1_primary_filing",
        access_class="public_official",
        extraction_status="extracted",
        retrieved_at=datetime.now(timezone.utc),
        is_current=True,
    )
    session.add(version)
    await session.flush()
    deriv = ResearchDocumentDerivation(
        id=uuid.uuid4(),
        research_document_version_id=version.id,
        pipeline_version=15,
        extraction_profile="live",
        extraction_method="html",
        status="complete",
        is_active=True,
        pages_persisted=3,
        page_count=3,
        char_count=900,
    )
    session.add(deriv)
    await session.flush()
    session.add(
        ResearchDocumentChunk(
            id=uuid.uuid4(),
            # `chunk_id` is globally UNIQUE, and real ids are derived from the
            # version's own coordinates — so two companies indexing the same filing get
            # different ids. The company id keeps this fixture faithful to that.
            chunk_id=f"c:{accession}:{company.id}:1",
            derivation_id=deriv.id,
            research_document_version_id=version.id,
            company_id=company.id,
            ordinal=1,
            kind="prose",
            text="Our clinical pipeline includes mRNA-1283 in Phase 3 for respiratory disease.",
            char_start=0,
            char_end=76,
            indexable=True,
            # `indexable` is PERMISSION; `indexed_at` is INDEX STATE, and the lexical
            # query filters on the latter. A fixture that set only the first would be
            # asserting against a document the backend cannot actually return.
            indexed_at=datetime.now(timezone.utc),
            source_tier="T1_primary_filing",
            access_class="public_official",
        )
    )
    await session.flush()
    return version


async def test_an_already_searchable_filing_is_reused_with_no_network(session):
    """§8 idempotence, and the property that makes repeat runs free."""
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    await _make_searchable(session, company)
    calls: list = []

    async def _acquire(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls.append(k)
        raise AssertionError("a searchable filing must never be re-fetched")

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_acquire,
    )
    assert result.state == "ready"
    assert result.reused is True
    assert result.fetched is False
    assert result.chunk_count == 1
    assert calls == []


async def test_a_chunkless_cached_document_IS_reacquired(session):
    """The dead-zone fix, as a behaviour rather than a diagnosis.

    A cached document exists. It cannot be searched. The bridge must reacquire rather
    than accept the cache — that is the difference between this slice and a backfill.
    """
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)
    await _historical_extracted_document(session, company)
    calls: list = []

    async def _acquire(session_, **k):  # noqa: ANN001, ANN003, ANN202
        calls.append(k["accession"])
        await _make_searchable(session_, company)
        from app.services.corpus.filing_evidence import AcquireOutcome

        return AcquireOutcome(acquired=True, fetched=True)

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_acquire,
    )
    assert calls == [ACCESSION], "the chunkless cache must not satisfy the request"
    assert result.state == "ready"
    assert result.fetched is True
    assert result.chunk_count == 1


async def test_extraction_that_produces_no_chunks_is_not_reported_as_success(session):
    """§17 — "extraction succeeds but indexing fails".

    The acquirer says it worked; the corpus has nothing. Believing the acquirer would
    leave a specialist searching an empty document and reporting a coverage gap.
    """
    from app.services.corpus.filing_evidence import AcquireOutcome, ensure_filing_corpus_evidence

    company = await _company(session)

    async def _acquire(session_, **k):  # noqa: ANN001, ANN003, ANN202
        return AcquireOutcome(acquired=True, fetched=True)  # writes nothing

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_acquire,
    )
    assert result.state == "unavailable"
    assert result.reason == "no_indexable_content"
    assert result.is_ready is False


async def test_an_acquirer_that_raises_degrades_honestly(session):
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)

    async def _boom(session_, **k):  # noqa: ANN001, ANN003, ANN202
        raise RuntimeError("SEC unreachable")

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_boom,
    )
    assert result.state == "unavailable"
    assert result.reason == "acquisition_error"


async def test_a_malformed_cik_never_reaches_acquisition(session):
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    company = await _company(session)

    async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("no acquisition for a malformed CIK")

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=company.id,
        cik="not-a-cik",
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_never,
    )
    assert result.state == "unavailable"
    assert result.reason == "malformed_cik"


async def test_one_companys_searchable_filing_does_not_satisfy_another(session):
    """§17 — cross-company reuse. The same filing, indexed for BIIB, must not let MRNA
    skip acquisition."""
    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    mine = await _company(session, "MRNA")
    theirs = await _company(session, "BIIB")
    await _make_searchable(session, theirs)
    calls: list = []

    async def _acquire(session_, **k):  # noqa: ANN001, ANN003, ANN202
        calls.append(k["accession"])
        from app.services.corpus.filing_evidence import AcquireOutcome

        await _make_searchable(session_, mine)
        return AcquireOutcome(acquired=True, fetched=True)

    result = await ensure_filing_corpus_evidence(
        session,
        company_id=mine.id,
        cik=CIK,
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(),
        acquire=_acquire,
    )
    assert calls == [ACCESSION], "another company's index must not satisfy this one"
    assert result.state == "ready"


async def test_the_sec_body_flag_off_means_no_network_and_an_honest_reason(
    session, real_acquire_sec_filing
):
    """The REAL acquirer, with the existing V2 flag off: no fetch, honest reason.

    Asks for `real_acquire_sec_filing` deliberately — the autouse guard that keeps the
    suite off the network would otherwise answer for it, and this test's whole subject
    is what the real implementation does.
    """
    company = await _company(session)
    outcome = await real_acquire_sec_filing(
        session,
        company_id=company.id,
        cik="1682852",
        accession=ACCESSION,
        form="10-K",
        cfg=_cfg(primary_document_sec_body_enabled=False),
    )
    assert outcome.acquired is False
    assert outcome.fetched is False
    assert outcome.reason == "sec_body_fetch_disabled"


# ---------------------------------------------------------------------------
# 7. The tool wiring (§4, §6) — discovery stays discovery
# ---------------------------------------------------------------------------


class _Event:
    def __init__(self, accession, form="10-K"):  # noqa: ANN001
        self.id = f"sec:{accession}"
        self.form_type = form
        self.filing_date = "2026-02-20"
        self.report_date = "2025-12-31"
        self.accession_number = accession
        self.headline = f"{form} filed"
        self.source_url = FILING_URL
        self.related_filing_url = FILING_URL
        self.item_numbers = []
        self.source_tier = "T1_primary_filing"


class _Result:
    def __init__(self, events, cik="1682852"):  # noqa: ANN001
        self.events = events
        self.cik = cik
        self.warnings = []


async def _call_tool(session, company, cfg, monkeypatch, events):  # noqa: ANN001
    from app.services.agent_tools import filings as filings_mod
    from app.services.agent_tools.session import ToolContext

    class _Provider:
        async def get_recent_events(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
            return _Result(events)

    import app.integrations.providers.sec_recent_filings_provider as prov_mod

    monkeypatch.setattr(prov_mod, "SecRecentFilingsProvider", lambda: _Provider())
    ctx = ToolContext(session=session, cfg=cfg, company_id=company.id, role="event_analyst")
    return await filings_mod._get_recent_filings(
        ctx,
        {
            "ticker": "MRNA",
            "exchange": "US",
            "lookback_days": 400,
            "limit": 5,
            "form_types": [],
        },
    )


async def test_discovery_still_returns_metadata_only_and_never_prose(
    session, monkeypatch
):
    """§4 — the contract that must not drift. No filing text may appear in a discovery
    result, however much the bridge did behind it."""
    company = await _company(session)
    await _make_searchable(session, company)
    out = await _call_tool(session, company, _cfg(), monkeypatch, [_Event(ACCESSION)])

    assert out["items"], "discovery returned nothing"
    item = out["items"][0]
    assert set(item) >= {"id", "form_type", "accession_number", "source_url"}
    # Metadata only: no body, no text, no excerpt, no content field of any kind.
    assert not any(
        k in item for k in ("text", "body", "content", "excerpt", "prose", "extract")
    )
    # And the bridge told the specialist whether it can search this filing.
    assert item["corpus_ready"] is True
    assert out["corpus"]["ready"] == 1
    assert out["corpus"]["fetched"] == 0


async def test_with_the_bridge_off_discovery_says_nothing_is_searchable(
    session, monkeypatch
):
    company = await _company(session)
    await _make_searchable(session, company)
    out = await _call_tool(
        session, company, _cfg(v3_filing_body_bridge_enabled=False), monkeypatch,
        [_Event(ACCESSION)],
    )
    assert out["corpus"]["enabled"] is False
    assert "V3_FILING_BODY_BRIDGE_ENABLED is off" in out["summary"]
    # The honest consequence is stated rather than implied by an absent field.
    assert "none of these filings is searchable" in out["summary"]


async def test_an_unsearchable_filing_is_reported_not_omitted(session, monkeypatch):
    """A specialist must be able to tell "nothing in the filing" from "never read"."""
    company = await _company(session)
    out = await _call_tool(
        session, company, _cfg(primary_document_sec_body_enabled=False), monkeypatch,
        [_Event(ACCESSION)],
    )
    assert len(out["items"]) == 1
    assert out["items"][0]["corpus_ready"] is False
    assert out["corpus"]["unavailable"] == 1
    assert out["corpus"]["reasons"]


# ---------------------------------------------------------------------------
# 8. Bounded acquisition (§7) — one question is not fifty fetches
# ---------------------------------------------------------------------------


async def test_one_discovery_call_cannot_acquire_more_than_the_budget(
    session, monkeypatch
):
    """§7 — "do not turn one playbook question into download every filing"."""
    company = await _company(session)
    fetched: list = []

    async def _acquire(session_, **k):  # noqa: ANN001, ANN003, ANN202
        from app.services.corpus.filing_evidence import AcquireOutcome

        fetched.append(k["accession"])
        return AcquireOutcome(acquired=True, fetched=True)

    import app.services.corpus.filing_acquisition as acq

    monkeypatch.setattr(acq, "acquire_sec_filing", _acquire)

    events = [_Event(f"000168285225{i:06d}") for i in range(1, 9)]
    out = await _call_tool(
        session, company, _cfg(v3_filing_body_bridge_max_documents=2), monkeypatch, events
    )

    assert len(out["items"]) == 5, "the tool's own row limit still applies"
    assert len(fetched) == 2, f"budget of 2 exceeded: {fetched}"
    assert out["corpus"]["acquire_budget"] == 2
    # The ones that were not acquired are reported, not hidden.
    assert out["corpus"]["reasons"].get("acquire_budget_exhausted")
    assert all(i["corpus_ready"] is False for i in out["items"][2:])


async def test_an_already_searchable_filing_costs_no_budget(session, monkeypatch):
    """A filing the platform already holds must not consume the acquisition budget —
    otherwise a repeat run would starve itself on documents it does not need."""
    company = await _company(session)
    first = "0001682852-25-000001"
    await _make_searchable(session, company, accession=first,
                           url=f"https://www.sec.gov/Archives/edgar/data/{CIK}/000168285225000001/x.htm")
    fetched: list = []

    async def _acquire(session_, **k):  # noqa: ANN001, ANN003, ANN202
        from app.services.corpus.filing_evidence import AcquireOutcome

        fetched.append(k["accession"])
        return AcquireOutcome(acquired=True, fetched=True)

    import app.services.corpus.filing_acquisition as acq

    monkeypatch.setattr(acq, "acquire_sec_filing", _acquire)

    events = [_Event(first), _Event("0001682852-25-000002"), _Event("0001682852-25-000003")]
    out = await _call_tool(
        session, company, _cfg(v3_filing_body_bridge_max_documents=2), monkeypatch, events
    )
    assert out["corpus"]["ready"] == 1, "the held filing should be reported as reused"
    assert len(fetched) == 2, "both budget slots go to filings not already held"
    assert first not in fetched


async def test_an_unsupported_form_is_never_fetched(session, real_acquire_sec_filing):
    """§3/§17 — a form the SEC resolver does not support must not reach the network."""
    company = await _company(session)
    outcome = await real_acquire_sec_filing(
        session,
        company_id=company.id,
        cik="1682852",
        accession=ACCESSION,
        form="DEF 14A",  # not in SUPPORTED_FORMS
        cfg=_cfg(),
    )
    assert outcome.acquired is False
    assert outcome.reason == "no_document_resolved"
    assert outcome.fetched is False
