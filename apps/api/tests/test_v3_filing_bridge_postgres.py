"""The filing-evidence bridge on real PostgreSQL, with the real search backend.

WHY THIS FILE EXISTS SEPARATELY FROM THE UNIT TESTS
===================================================
The unit tests run on SQLite with foreign keys off, which in this codebase has twice
hidden a defect that would have broken production. Three of the properties this slice
depends on cannot be checked there at all:

* **The search backend is PostgreSQL-only.** ``search_corpus`` runs a full-text query; a
  test that never executes it is asserting that rows exist, not that they can be found —
  which is the exact confusion V3.16 was opened to fix.
* **Foreign keys are enforced**, so company isolation is a constraint rather than a
  convention.
* **A failed statement aborts the transaction.** "Extraction succeeded, indexing failed"
  behaves differently here, and it is a state the bridge must not report as ready.

Skipped without ``V3_TEST_POSTGRES_URL``. A guard that never runs is not a guard.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import Settings

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 039",
)

ACCESSION = "0001682852-25-000006"
ACCESSION_NODASH = "000168285225000006"
CIK = "0001682852"
FILING_URL = (
    f"https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCESSION_NODASH}/mrna-20241231.htm"
)

PIPELINE_TEXT = (
    "Our clinical pipeline includes mRNA-1283, a next-generation COVID-19 vaccine in "
    "Phase 3 development, and mRNA-1010 for seasonal influenza. The Company received "
    "regulatory approval for SPIKEVAX and continues to advance its respiratory portfolio."
)


def _cfg(**over) -> Settings:  # noqa: ANN003
    base = {
        "v3_corpus_enabled": True,
        "v3_search_backend": "postgres",
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


@pytest.fixture
async def pg():  # noqa: ANN201
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(POSTGRES_URL, future=True)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _company(maker, ticker=None):  # noqa: ANN001
    from app.models.company import Company

    ticker = ticker or f"T{uuid.uuid4().hex[:6].upper()}"
    async with maker() as s:
        c = Company(
            id=uuid.uuid4(),
            ticker=ticker,
            exchange="US",
            name=f"{ticker} Inc.",
            status="new",
        )
        s.add(c)
        await s.flush()
        cid = c.id
        await s.commit()
    return cid


async def _index_filing(session, company_id, accession, text=PIPELINE_TEXT):  # noqa: ANN001
    """The full chain a real acquisition produces, written directly."""
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    nodash = accession.replace("-", "")
    doc = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        document_key=f"sec_filing:{accession}",
        document_type="sec_filing",
        title=f"10-K {accession}",
    )
    session.add(doc)
    await session.flush()
    version = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=doc.id,
        content_hash=uuid.uuid4().hex * 2,
        canonical_url=f"https://www.sec.gov/Archives/edgar/data/{CIK}/{nodash}/x.htm",
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
        pages_persisted=1,
        page_count=1,
        char_count=len(text),
    )
    session.add(deriv)
    await session.flush()
    chunk = ResearchDocumentChunk(
        id=uuid.uuid4(),
        chunk_id=f"c:{uuid.uuid4().hex}",
        derivation_id=deriv.id,
        research_document_version_id=version.id,
        company_id=company_id,
        ordinal=1,
        kind="prose",
        text=text,
        char_start=0,
        char_end=len(text),
        indexable=True,
        # The index entry, not just the permission — the lexical query filters on
        # `indexed_at IS NOT NULL`, so without this the fixture describes a document
        # the backend cannot return.
        indexed_at=datetime.now(timezone.utc),
        document_type="sec_filing",
        source_tier="T1_primary_filing",
        access_class="public_official",
    )
    session.add(chunk)
    await session.flush()
    return version, chunk


# ---------------------------------------------------------------------------
# §16.3 / §16.4 / §16.5 — readiness, reuse, idempotence
# ---------------------------------------------------------------------------


@requires_postgres
class TestReadinessOnRealPostgres:
    async def test_a_version_without_chunks_is_not_ready(self, pg) -> None:  # noqa: ANN001
        """§16.3 — the backfill's output state, on the engine that matters."""
        from app.models.extracted_document import ExtractedDocument
        from app.services.corpus.documents import backfill_from_extracted_documents
        from app.services.corpus.filing_evidence import is_corpus_search_ready

        company_id = await _company(pg)
        async with pg() as s:
            s.add(
                ExtractedDocument(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    canonical_url=FILING_URL,
                    content_hash=uuid.uuid4().hex * 2,
                    source_type="sec_filing",
                    provider="sec_edgar",
                    source_tier="T1_primary_filing",
                    title=f"10-K {ACCESSION}",
                    mime_type="text/html",
                    status="extracted",
                    extraction_method="html",
                    retrieved_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
                    excerpts_json=[{"text": "Total revenue was $3.2 billion."}],
                    pipeline_version=15,
                )
            )
            await s.commit()

        async with pg() as s:
            result = await backfill_from_extracted_documents(
                s, cfg=_cfg(), company_id=company_id
            )
            await s.commit()
            assert result.versions_created == 1

        async with pg() as s:
            assert (
                await is_corpus_search_ready(
                    s, company_id=company_id, accession=ACCESSION
                )
                is False
            ), "a backfilled version has no chunks and must not read as searchable"

    async def test_an_indexed_filing_is_ready_and_is_never_refetched(self, pg) -> None:  # noqa: ANN001
        """§16.4 + §16.5 — the repeat-run property, three times over."""
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        company_id = await _company(pg)
        async with pg() as s:
            await _index_filing(s, company_id, ACCESSION)
            await s.commit()

        calls: list = []

        async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
            calls.append(k)
            raise AssertionError("an indexed filing must never be re-acquired")

        for attempt in range(3):
            async with pg() as s:
                out = await ensure_filing_corpus_evidence(
                    s,
                    company_id=company_id,
                    cik=CIK,
                    accession=ACCESSION,
                    form="10-K",
                    cfg=_cfg(),
                    acquire=_never,
                )
                assert out.state == "ready", f"attempt {attempt}"
                assert out.fetched is False
                assert out.reused is True
        assert calls == []

    async def test_company_isolation_is_enforced_with_foreign_keys_on(self, pg) -> None:  # noqa: ANN001
        """§16.6 — one issuer's indexed filing cannot satisfy another's request."""
        from app.services.corpus.filing_evidence import filing_evidence_state

        mine = await _company(pg)
        theirs = await _company(pg)
        async with pg() as s:
            await _index_filing(s, theirs, ACCESSION)
            await s.commit()

        async with pg() as s:
            theirs_state = await filing_evidence_state(
                s, company_id=theirs, accession=ACCESSION
            )
            mine_state = await filing_evidence_state(
                s, company_id=mine, accession=ACCESSION
            )
        assert theirs_state.state == "ready"
        assert mine_state.state == "absent"


# ---------------------------------------------------------------------------
# §16.11 / §16.12 — the retrieval backend can actually find it, and it is citable
# ---------------------------------------------------------------------------


@requires_postgres
class TestTheSpecialistCanActuallyRetrieveIt:
    async def test_search_returns_the_indexed_filing_content(self, pg) -> None:  # noqa: ANN001
        """§16.11 — the assertion the whole slice is for.

        Not "a row exists". The real PostgreSQL search backend, running the query a
        biotech specialist would run for ``pipeline_state``, returns the filing's own
        words with a citable handle attached.
        """
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend
        from app.services.corpus.search.types import (
            CorpusFilters,
            CorpusQuery,
            SearchMode,
        )

        company_id = await _company(pg)
        async with pg() as s:
            _version, chunk = await _index_filing(s, company_id, ACCESSION)
            expected_chunk_id = chunk.chunk_id
            await s.commit()

        async with pg() as s:
            backend = PostgresSearchBackend(session=s)
            hits = await backend.search(
                CorpusQuery(
                    text="clinical pipeline phase 3 development",
                    filters=CorpusFilters(company_ids=(company_id,)),
                    mode=SearchMode.LEXICAL,
                    top_k=5,
                )
            )
        assert hits, "the specialist's query returned nothing from an indexed filing"
        ids = [h.chunk.chunk_id for h in hits]
        assert expected_chunk_id in ids
        top = next(h for h in hits if h.chunk.chunk_id == expected_chunk_id)
        # Every field a citation needs, present on the hit itself.
        assert "mRNA-1283" in top.chunk.text
        assert top.chunk.source_tier == "T1_primary_filing"
        assert top.chunk.research_document_version_id is not None


# ---------------------------------------------------------------------------
# §16.9 / §16.10 — failure must never look like success
# ---------------------------------------------------------------------------


@requires_postgres
class TestFailureNeverLooksLikeSuccess:
    async def test_extraction_that_indexes_nothing_is_reported_unavailable(
        self, pg
    ) -> None:  # noqa: ANN001
        """§16.9 — the acquirer claims success and writes nothing."""
        from app.services.corpus.filing_evidence import (
            AcquireOutcome,
            ensure_filing_corpus_evidence,
        )

        company_id = await _company(pg)

        async def _lies(session_, **k):  # noqa: ANN001, ANN003, ANN202
            return AcquireOutcome(acquired=True, fetched=True)

        async with pg() as s:
            out = await ensure_filing_corpus_evidence(
                s,
                company_id=company_id,
                cik=CIK,
                accession=ACCESSION,
                form="10-K",
                cfg=_cfg(),
                acquire=_lies,
            )
        assert out.state == "unavailable"
        assert out.reason == "no_indexable_content"

    async def test_a_failed_acquisition_leaves_the_transaction_usable(self, pg) -> None:  # noqa: ANN001
        """§16.10 — `except Exception` cannot un-abort a PostgreSQL transaction, so a
        failing acquisition must not poison the run that called it."""
        from sqlalchemy import text

        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        company_id = await _company(pg)

        async def _boom(session_, **k):  # noqa: ANN001, ANN003, ANN202
            raise RuntimeError("SEC unreachable")

        async with pg() as s:
            out = await ensure_filing_corpus_evidence(
                s,
                company_id=company_id,
                cik=CIK,
                accession=ACCESSION,
                form="10-K",
                cfg=_cfg(),
                acquire=_boom,
            )
            assert out.state == "unavailable"
            assert out.reason == "acquisition_error"
            alive = (await s.execute(text("SELECT 1"))).scalar_one()
            assert alive == 1
            await s.commit()

    async def test_a_malformed_accession_never_reaches_the_network(self, pg) -> None:  # noqa: ANN001
        """§16.7 — fail closed, before any URL could be constructed."""
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        company_id = await _company(pg)

        async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("no network for a malformed accession")

        async with pg() as s:
            for bad in ("../../secrets", "0001682852-25-0000", "", "'; DROP TABLE x;--"):
                out = await ensure_filing_corpus_evidence(
                    s,
                    company_id=company_id,
                    cik=CIK,
                    accession=bad,
                    form="10-K",
                    cfg=_cfg(),
                    acquire=_never,
                )
                assert out.state == "unavailable"
                assert out.reason == "malformed_accession"


# ---------------------------------------------------------------------------
# §12 — the biotech specialist queries, against a corpus built by the REAL chain
# ---------------------------------------------------------------------------

#: A realistic 10-K body. Short, but structurally what EDGAR serves: HTML with
#: headings and paragraphs, so the extractor produces real blocks and the chunker
#: produces real chunks. Hand-writing chunk rows would prove the predicate and skip
#: the producer, which is the half that was broken.
_FILING_HTML = b"""<html><body>
<h1>MODERNA, INC. ANNUAL REPORT ON FORM 10-K</h1>
<h2>Item 1. Business</h2>
<p>We are a biotechnology company pioneering messenger RNA therapeutics and vaccines.
Our commercial products include SPIKEVAX, our COVID-19 vaccine, and mRESVIA, our
respiratory syncytial virus vaccine approved in 2024.</p>
<h2>Our Clinical Pipeline</h2>
<p>Our development pipeline includes mRNA-1283, a next-generation COVID-19 vaccine
which has completed Phase 3 clinical development, and mRNA-1010, a seasonal influenza
vaccine candidate in Phase 3 trials. We also advance mRNA-4157, an individualized
neoantigen therapy in Phase 3 development with our collaborator for adjuvant melanoma.</p>
<h2>Regulatory Matters</h2>
<p>In 2024 we received regulatory approval from the U.S. Food and Drug Administration
for mRESVIA. We have ongoing submissions with regulatory authorities and received
Breakthrough Therapy designation for certain programs. No complete response letters
were received during the period.</p>
<h2>Liquidity and Capital Resources</h2>
<p>As of December 31, 2024 we held cash and cash equivalents and investments of
$9.5 billion. Net cash used in operating activities was $3.0 billion for the year
ended December 31, 2024. We expect our existing liquidity to fund operations.</p>
<h2>Research and Development Expenses</h2>
<p>Research and development expenses were $4.5 billion for the year ended
December 31, 2024, reflecting continued investment across our respiratory and
oncology programs.</p>
</body></html>"""


async def _ingest_via_the_real_chain(pg, company_id, acquire, accession=ACCESSION):  # noqa: ANN001
    """Fetch → extract → persist → chunk → index, with the network stubbed at the edge.

    Everything between the stub and the database is the production code path, which is
    the point: the defect this slice fixes lived in that stretch.
    """
    from app.services.sources import live_fetchers
    from app.services.sources.document_fetcher import DocumentFetchResult
    from app.services.sources.sec_filing_documents import SecFilingDocument

    nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{nodash}/mrna-10k.htm"
    # DISTINCT BYTES PER COMPANY, deliberately.
    #
    # `_get_or_create_document` dedups `extracted_documents` on `content_hash` ALONE,
    # with no company in the key — so byte-identical fixture content makes the second
    # company REUSE the first company's row, and the corpus version stays attributed to
    # the first. Sharing one fixture across tests would therefore exercise that
    # collision instead of the path under test. (The collision itself is a real finding;
    # see the V3.16 slice doc. The bridge survives it because it re-reads state instead
    # of believing the acquirer, which is how it was caught.)
    body = _FILING_HTML.replace(
        b"<h1>MODERNA, INC.",
        f"<h1>MODERNA, INC. [{company_id}]".encode(),
    )

    async def _fake_resolve(cik, filings, **kwargs):  # noqa: ANN001, ANN003, ANN202
        sink = kwargs.get("preflight_sink")
        if sink is not None:
            sink.clear()
        return [
            SecFilingDocument(
                accession_number=accession,
                form_type="10-K",
                filing_date="2026-02-20",
                canonical_url=url,
                document_name="mrna-10k.htm",
                cik=CIK,
                title=f"10-K {accession}",
            )
        ]

    async def _fake_body(doc, **kwargs):  # noqa: ANN001, ANN003, ANN202
        return DocumentFetchResult(
            requested_url=doc.canonical_url,
            final_url=doc.canonical_url,
            status_code=200,
            content_type="text/html",
            document_type="html",
            content=body,
        )

    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(live_fetchers, "resolve_filing_documents", _fake_resolve)
    mp.setattr(live_fetchers, "fetch_filing_body", _fake_body)
    try:
        # `acquire` is the REAL acquirer, passed in via the `real_acquire_sec_filing`
        # fixture: the autouse guard that keeps the suite off the network would
        # otherwise answer here, and this helper's whole purpose is to run the
        # production chain.
        async with pg() as s:
            outcome = await acquire(
                s,
                company_id=company_id,
                cik=CIK,
                accession=accession,
                form="10-K",
                cfg=_cfg(),
            )
            await s.commit()
        return outcome
    finally:
        mp.undo()


@requires_postgres
class TestBiotechSpecialistQueries:
    async def test_the_real_chain_produces_searchable_indexed_chunks(
        self, pg, real_acquire_sec_filing
    ) -> None:  # noqa: ANN001
        """The end-to-end producer, on PostgreSQL.

        A filing arrives as bytes and leaves as chunks the retrieval backend can return.
        Before V3.16 this chain stopped twice: a cached document produced no blocks, and
        the chunks that were produced were never indexed.
        """
        from app.services.corpus.filing_evidence import filing_evidence_state

        company_id = await _company(pg)
        outcome = await _ingest_via_the_real_chain(pg, company_id, real_acquire_sec_filing)
        assert outcome.acquired is True, outcome.detail
        assert outcome.fetched is True

        async with pg() as s:
            state = await filing_evidence_state(
                s, company_id=company_id, accession=ACCESSION
            )
        assert state.state == "ready", state.detail
        assert state.indexable_chunk_count > 0, "chunks exist but none is indexed"

    async def test_the_biotech_specialist_questions_retrieve_real_content(
        self, pg, real_acquire_sec_filing
    ) -> None:  # noqa: ANN001
        """§12 — the queries the biotech playbook's own questions drive.

        Each must come back with the FILING'S OWN WORDS and a citable handle. This is
        the assertion that distinguishes "the corpus has rows" from "the specialist can
        answer its question".
        """
        from app.services.corpus.search.backends.postgres import PostgresSearchBackend
        from app.services.corpus.search.types import (
            CorpusFilters,
            CorpusQuery,
            SearchMode,
        )

        company_id = await _company(pg)
        await _ingest_via_the_real_chain(pg, company_id, real_acquire_sec_filing)

        # The real playbook question text, not a query invented to pass.
        questions = {
            "pipeline_state": (
                "Which assets are in the clinical pipeline, at what phase, for what "
                "indication, and with what next milestone?",
                ("mRNA-1283", "Phase 3", "pipeline"),
            ),
            "regulatory_posture": (
                "What regulatory interactions have been disclosed - designations, "
                "holds, complete response letters, approvals?",
                ("regulatory", "approval"),
            ),
            "cash_runway": (
                "What are cash and equivalents, what is the operating cash burn, and "
                "how many quarters of runway does that imply?",
                ("cash",),
            ),
            "rnd_intensity": (
                "What is R&D expense, and how is it distributed across the pipeline?",
                ("Research and development",),
            ),
        }

        async with pg() as s:
            backend = PostgresSearchBackend(session=s)
            for key, (text, expected_any) in questions.items():
                hits = await backend.search(
                    CorpusQuery(
                        text=text,
                        filters=CorpusFilters(company_ids=(company_id,)),
                        mode=SearchMode.LEXICAL,
                        top_k=5,
                    )
                )
                assert hits, f"{key}: the specialist's own question returned nothing"
                joined = " ".join(h.chunk.text for h in hits)
                assert any(token in joined for token in expected_any), (
                    f"{key}: retrieved content does not contain any of {expected_any}"
                )
                # Citable: every hit carries the provenance a finding needs.
                top = hits[0].chunk
                assert top.chunk_id
                assert top.research_document_version_id is not None
                assert top.source_tier == "T1_primary_filing"

    async def test_a_second_run_reuses_the_filing_and_fetches_nothing(
        self, pg, real_acquire_sec_filing
    ) -> None:  # noqa: ANN001
        """§8/§12 — the repeat-run cost, measured through the real chain."""
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        company_id = await _company(pg)
        await _ingest_via_the_real_chain(pg, company_id, real_acquire_sec_filing)

        calls: list = []

        async def _never(*a, **k):  # noqa: ANN002, ANN003, ANN202
            calls.append(k)
            raise AssertionError("the filing is already indexed; nothing to acquire")

        async with pg() as s:
            out = await ensure_filing_corpus_evidence(
                s,
                company_id=company_id,
                cik=CIK,
                accession=ACCESSION,
                form="10-K",
                cfg=_cfg(),
                acquire=_never,
            )
        assert out.state == "ready"
        assert out.fetched is False
        assert out.reused is True
        assert calls == []


@requires_postgres
class TestKnownLimitationV2DocumentDedupIsNotCompanyScoped:
    """A finding this slice records rather than fixes, with the reason.

    ``_get_or_create_document`` dedups ``extracted_documents`` on ``content_hash`` alone
    — no company in the key. So when two companies' documents are byte-identical, the
    second company REUSES the first's row and the corpus version stays attributed to the
    first. The second company then has no searchable filing of its own.

    Why it is not fixed here: the fix is to change V2 document IDENTITY, which changes
    dedup for every company and every document ever written and needs its own acceptance.
    V3.16's scope is the corpus bridge.

    Why shipping is nonetheless safe: the bridge **re-reads the readiness state instead
    of believing the acquirer**, so the collision produces an honest ``unavailable``
    rather than a false ``ready``. This test pins exactly that, so the day the dedup key
    changes, this test tells whoever changed it what else moves.
    """

    async def test_identical_bytes_for_two_companies_does_not_fake_readiness(
        self, pg, real_acquire_sec_filing
    ) -> None:  # noqa: ANN001
        from app.services.corpus.filing_evidence import (
            ensure_filing_corpus_evidence,
            filing_evidence_state,
        )
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult
        from app.services.sources.sec_filing_documents import SecFilingDocument

        first = await _company(pg)
        second = await _company(pg)
        # UNIQUE per invocation. The scratch database persists between tests and between
        # runs, and `extracted_documents` dedups on `content_hash` — so a fixed
        # accession with fixed bytes passes once on a clean database and fails for ever
        # afterwards, because the FIRST company then reuses a row from an earlier run.
        # The test's subject is the collision between these two companies, which means
        # it has to own its bytes.
        unique = uuid.uuid4().hex
        accession = f"0001682852-25-{int(unique[:6], 16) % 1000000:06d}"
        nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{nodash}/same.htm"
        shared = (
            f"<html><body><h1>10-K {unique}</h1><p>Identical bytes.</p></body></html>"
        ).encode()

        async def _res(cik, filings, **kw):  # noqa: ANN001, ANN003, ANN202
            sink = kw.get("preflight_sink")
            if sink is not None:
                sink.clear()
            return [
                SecFilingDocument(
                    accession_number=accession,
                    form_type="10-K",
                    filing_date="2026-02-20",
                    canonical_url=url,
                    document_name="same.htm",
                    cik=CIK,
                    title="10-K",
                )
            ]

        async def _body(doc, **kw):  # noqa: ANN001, ANN003, ANN202
            return DocumentFetchResult(
                requested_url=url,
                final_url=url,
                status_code=200,
                content_type="text/html",
                document_type="html",
                content=shared,
            )

        import pytest as _pytest

        mp = _pytest.MonkeyPatch()
        mp.setattr(live_fetchers, "resolve_filing_documents", _res)
        mp.setattr(live_fetchers, "fetch_filing_body", _body)
        try:
            for company_id in (first, second):
                async with pg() as s:
                    await real_acquire_sec_filing(
                        s,
                        company_id=company_id,
                        cik=CIK,
                        accession=accession,
                        form="10-K",
                        cfg=_cfg(),
                    )
                    await s.commit()

            async with pg() as s:
                first_state = await filing_evidence_state(
                    s, company_id=first, accession=accession
                )
                second_state = await filing_evidence_state(
                    s, company_id=second, accession=accession
                )
                # And the bridge's own answer for the second company is honest.
                bridged = await ensure_filing_corpus_evidence(
                    s,
                    company_id=second,
                    cik=CIK,
                    accession=accession,
                    form="10-K",
                    cfg=_cfg(),
                    acquire=real_acquire_sec_filing,
                )
        finally:
            mp.undo()

        assert first_state.state == "ready", "the first company should hold the filing"
        # THE PROPERTY THAT MATTERS: the second company is told the truth.
        assert second_state.state != "ready"
        assert bridged.is_ready is False
        assert bridged.state == "unavailable"
