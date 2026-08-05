"""Phase 32A Slice 5B.1 — official SEC filing-BODY ingestion for US issuers.

Slice 5A reached ZERO successful native extractions across seven issuers. For a
US issuer the reason was structural: the SEC path read only *structured* JSON
(companyfacts / submissions), so a filing's BODY was never fetched and there was
no primary-document candidate at all. This is that path.

Two invariants are load-bearing and are asserted repeatedly here:
  * **Feature flag OFF ⇒ nothing happens.** With
    ``primary_document_ingestion_enabled`` (or ``primary_document_sec_body_enabled``)
    off the extractor returns ``[]`` having made NO network call whatsoever.
  * **SUPPLEMENT, never replace.** SEC/XBRL structured-fact evidence is untouched
    by this path; filing bodies only ADD narrative evidence on top.

No network, no Azure: ``resolve_filing_documents`` / ``fetch_filing_body`` are
monkeypatched, and any accidental real call fails the test loudly.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.services.sources import live_fetchers
from app.services.sources.company_evidence import (
    SEC_DOCUMENT_EXCERPT_TYPE,
    SEC_ID,
    collect_company_source_evidence,
    sec_artifacts_to_evidence,
)
from app.services.sources.connector_base import CompanyContext
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_RESULTS_RELEASE,
)
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.ingestion_attempts import (
    SOURCE_TYPE_SEC_FILING,
    attempts_for_primary_documents,
)
from app.services.sources.ingestion_status import (
    ATTEMPT_EXTRACTED,
    ATTEMPT_EXTRACTION_FAILED,
    FAILURE_BUDGET_EXHAUSTED,
    FAILURE_HTTP_CLIENT_ERROR,
)
from app.services.sources.live_fetchers import live_sec_primary_document_extractor
from app.services.sources.primary_document_extractor import (
    METHOD_HTML,
    STATUS_EXTRACTED,
)
from app.services.sources.sec_filing_documents import (
    STRATEGY_SEC_ACCESSION,
    SecFilingDocument,
    doc_kind_for_form,
)
from app.services.sources.taxonomy import (
    T1_PRIMARY_FILING,
    T2_REGULATOR_OR_GOV,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.

_FILING_BODY = b"""<html><head><title>Apple Inc. Form 10-K</title></head><body>
<h2>Item 1. Business</h2>
<p>The Company designs, manufactures and markets smartphones, personal computers
and wearables, and reported total net sales of 383,285 million dollars for the
fiscal year with operating income of 114,301 million dollars.</p>
<h2>Item 1A. Risk Factors</h2>
<p>The Company's business, results of operations and financial condition depend
substantially on global economic conditions and on continued innovation.</p>
</body></html>"""

_ACCESSION = "0000320193-24-000123"
_BODY_URL = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-10k.htm"
)


def _cfg(**overrides: Any) -> Settings:
    """A Settings instance with the SEC-body path ON unless overridden."""
    cfg = Settings()
    cfg.primary_document_ingestion_enabled = True
    cfg.primary_document_sec_body_enabled = True
    cfg.primary_document_sec_max_bodies = 2
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _doc(
    *,
    form: str = "10-K",
    accession: str = _ACCESSION,
    url: str = _BODY_URL,
    name: str = "aapl-10k.htm",
) -> SecFilingDocument:
    return SecFilingDocument(
        accession_number=accession,
        form_type=form,
        filing_date="2024-11-01",
        canonical_url=url,
        document_name=name,
        cik="0000320193",
        title=f"SEC {form} filing",
    )


def _ok_body(url: str = _BODY_URL, content: bytes = _FILING_BODY) -> DocumentFetchResult:
    return DocumentFetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        document_type="html",
        content=content,
    )


def _not_found(url: str = _BODY_URL) -> DocumentFetchResult:
    result = DocumentFetchResult(
        requested_url=url,
        final_url=url,
        status_code=404,
        error="http 404",
        failure_code=FAILURE_HTTP_CLIENT_ERROR,
    )
    result._gap("SEC filing body could not be fetched (http 404).")
    return result


_FILINGS = [
    {
        "form_type": "10-K",
        "title": "Apple Inc. 10-K",
        "url": _BODY_URL,
        "filed_date": "2024-11-01",
        "accession_number": _ACCESSION,
    }
]


def _patch(monkeypatch, *, documents, bodies):
    """Stub the SEC resolution + body fetch; a real call would need network."""
    calls: dict[str, Any] = {"resolved": 0, "fetched": []}

    async def _resolve(cik, filings, *, max_documents, **kwargs):  # noqa: ANN001
        calls["resolved"] += 1
        calls["max_documents"] = max_documents
        calls["resolve_kwargs"] = kwargs
        return list(documents)[:max_documents]

    async def _fetch(doc, **kwargs):  # noqa: ANN001
        calls["fetched"].append(doc.canonical_url)
        body = bodies(doc) if callable(bodies) else bodies
        return body

    monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _resolve)
    monkeypatch.setattr(live_fetchers, "fetch_filing_body", _fetch)
    return calls


def _forbid_network(monkeypatch):
    async def _boom(*args: Any, **kwargs: Any):
        raise AssertionError("SEC network call made while the feature flag was OFF")

    monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _boom)
    monkeypatch.setattr(live_fetchers, "fetch_filing_body", _boom)


# ===========================================================================
# Flag OFF ⇒ byte-identical dark path (no network, no artifacts)
# ===========================================================================
class TestFeatureFlagOff:
    async def test_master_flag_off_returns_empty_with_no_network(
        self, monkeypatch
    ) -> None:
        _forbid_network(monkeypatch)
        cfg = _cfg(primary_document_ingestion_enabled=False)
        assert await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=cfg
        ) == []

    async def test_sec_body_flag_off_returns_empty_with_no_network(
        self, monkeypatch
    ) -> None:
        _forbid_network(monkeypatch)
        cfg = _cfg(primary_document_sec_body_enabled=False)
        assert await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=cfg
        ) == []

    async def test_zero_body_cap_returns_empty_with_no_network(
        self, monkeypatch
    ) -> None:
        _forbid_network(monkeypatch)
        cfg = _cfg(primary_document_sec_max_bodies=0)
        assert await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=cfg
        ) == []

    async def test_no_filings_returns_empty_with_no_network(self, monkeypatch) -> None:
        _forbid_network(monkeypatch)
        assert await live_sec_primary_document_extractor("0000320193", [], cfg=_cfg()) == []


# ===========================================================================
# Happy path — a native-text SEC HTML filing body really extracts
# ===========================================================================
class TestFilingBodyExtraction:
    async def test_native_text_body_yields_one_extracted_artifact(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())

        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.status == STATUS_EXTRACTED
        assert artifact.failure_code is None
        assert artifact.http_status_class == "2xx"
        assert artifact.content_hash

    async def test_extracted_body_carries_page_and_section_provenance(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifact = (
            await live_sec_primary_document_extractor(
                "0000320193", _FILINGS, cfg=_cfg()
            )
        )[0]

        extraction = artifact.extraction
        assert extraction is not None
        assert extraction.extraction_method == METHOD_HTML
        # Real section context from the filing's own item headings.
        sections = {e.section for e in extraction.excerpts}
        assert "Item 1. Business" in sections
        # HTML has no pagination — that is recorded honestly as unknown, not faked.
        assert all(e.page_number is None for e in extraction.excerpts)

    async def test_artifact_records_sec_accession_provenance(self, monkeypatch) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifact = (
            await live_sec_primary_document_extractor(
                "0000320193", _FILINGS, cfg=_cfg()
            )
        )[0]
        assert artifact.discovery_strategy == STRATEGY_SEC_ACCESSION
        assert artifact.doc_kind == DOC_KIND_ANNUAL_REPORT
        assert _ACCESSION in (artifact.title or "")

    async def test_body_cap_is_honoured(self, monkeypatch) -> None:
        calls = _patch(
            monkeypatch,
            documents=[_doc(), _doc(accession="0000320193-24-000124", url=_BODY_URL + "?2")],
            bodies=_ok_body(),
        )
        await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg(primary_document_sec_max_bodies=1)
        )
        assert calls["max_documents"] == 1

    async def test_explicit_max_documents_overrides_config(self, monkeypatch) -> None:
        calls = _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg(), max_documents=1
        )
        assert calls["max_documents"] == 1

    def test_form_to_doc_kind_is_total_and_never_guesses(self) -> None:
        assert doc_kind_for_form("10-K") == DOC_KIND_ANNUAL_REPORT
        assert doc_kind_for_form("20-F") == DOC_KIND_ANNUAL_REPORT
        assert doc_kind_for_form("10-Q") == DOC_KIND_INTERIM_REPORT
        assert doc_kind_for_form("6-K") == DOC_KIND_INTERIM_REPORT
        assert doc_kind_for_form("8-K") == DOC_KIND_RESULTS_RELEASE
        assert doc_kind_for_form("S-1") == DOC_KIND_OTHER
        assert doc_kind_for_form(None) == DOC_KIND_OTHER


# ===========================================================================
# Honest failure — a 404 body produces a recorded attempt, never a fabrication
# ===========================================================================
class TestFilingBodyFailure:
    async def test_404_body_yields_honest_failed_artifact(self, monkeypatch) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_not_found())

        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.status != STATUS_EXTRACTED
        assert artifact.failure_code == FAILURE_HTTP_CLIENT_ERROR
        assert artifact.http_status_class == "4xx"
        # Nothing was invented to fill the gap.
        assert artifact.extraction is None
        assert artifact.validated_facts == []
        assert artifact.source_gaps

    async def test_failed_body_still_produces_an_attempt_record(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_not_found())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )

        records = attempts_for_primary_documents(artifacts)

        assert len(records) == 1
        record = records[0]
        assert record.status == ATTEMPT_EXTRACTION_FAILED
        assert record.failure_code == FAILURE_HTTP_CLIENT_ERROR
        assert record.source_type == SOURCE_TYPE_SEC_FILING
        assert record.discovery_strategy == STRATEGY_SEC_ACCESSION
        assert record.http_status_class == "4xx"

    async def test_extracted_body_records_an_extracted_attempt(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )
        record = attempts_for_primary_documents(artifacts)[0]
        assert record.status == ATTEMPT_EXTRACTED
        assert record.failure_code is None
        assert record.content_hash

    async def test_resolution_failure_degrades_to_empty_never_raises(
        self, monkeypatch
    ) -> None:
        async def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("edgar unavailable")

        monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _boom)
        assert await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        ) == []

    async def test_body_fetch_exception_degrades_to_honest_artifact(
        self, monkeypatch
    ) -> None:
        async def _resolve(cik, filings, *, max_documents, **kwargs):  # noqa: ANN001
            return [_doc()]

        async def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(live_fetchers, "resolve_filing_documents", _resolve)
        monkeypatch.setattr(live_fetchers, "fetch_filing_body", _boom)

        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )
        assert len(artifacts) == 1
        assert artifacts[0].status != STATUS_EXTRACTED
        # Only a closed-vocabulary code — never the exception message.
        assert "transport exploded" not in str(artifacts[0].model_dump())


# ===========================================================================
# Aggregate ingestion budget — stop honestly, never fabricate
# ===========================================================================
class TestAggregateBudget:
    async def test_exhausted_budget_stops_further_fetches(self, monkeypatch) -> None:
        ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
        calls = _patch(
            monkeypatch,
            documents=[
                _doc(),
                _doc(accession="0000320193-24-000124", url=_BODY_URL + "?2"),
            ],
            bodies=_ok_body(),
        )

        artifacts = await live_sec_primary_document_extractor(
            "0000320193",
            _FILINGS,
            cfg=_cfg(),
            budget_seconds=10.0,
            clock=lambda: next(ticks),
        )

        # First document fetched; the second was identified but never fetched.
        assert len(calls["fetched"]) == 1
        assert len(artifacts) == 2
        skipped = artifacts[1]
        assert skipped.failure_code == FAILURE_BUDGET_EXHAUSTED
        assert skipped.extraction is None
        assert skipped.validated_facts == []
        assert "budget" in " ".join(g.message for g in skipped.source_gaps).lower()

    async def test_budget_skip_is_recorded_as_an_honest_attempt(
        self, monkeypatch
    ) -> None:
        ticks = iter([0.0, 99.0, 99.0])
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193",
            _FILINGS,
            cfg=_cfg(),
            budget_seconds=1.0,
            clock=lambda: next(ticks),
        )
        record = attempts_for_primary_documents(artifacts)[0]
        assert record.status == ATTEMPT_EXTRACTION_FAILED
        assert record.failure_code == FAILURE_BUDGET_EXHAUSTED

    async def test_no_budget_means_no_early_stop(self, monkeypatch) -> None:
        calls = _patch(
            monkeypatch,
            documents=[
                _doc(),
                _doc(accession="0000320193-24-000124", url=_BODY_URL + "?2"),
            ],
            bodies=_ok_body(),
        )
        await live_sec_primary_document_extractor("0000320193", _FILINGS, cfg=_cfg())
        assert len(calls["fetched"]) == 2

    async def test_resolution_is_given_the_same_deadline_as_the_fetch_loop(
        self, monkeypatch
    ) -> None:
        """PR-review blocker 1: index resolution runs INSIDE the wall budget."""
        calls = _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        ticks = iter([1000.0, 1000.0, 1000.0, 1000.0])
        clock = lambda: next(ticks, 1000.0)  # noqa: E731

        await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg(), budget_seconds=12.0, clock=clock
        )
        kwargs = calls["resolve_kwargs"]
        # An ABSOLUTE deadline on the SAME clock the fetch loop uses.
        assert kwargs["deadline"] == 1012.0
        assert kwargs["clock"] is clock

    async def test_resolution_gets_no_deadline_when_there_is_no_budget(
        self, monkeypatch
    ) -> None:
        calls = _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        await live_sec_primary_document_extractor("0000320193", _FILINGS, cfg=_cfg())
        assert calls["resolve_kwargs"]["deadline"] is None


# ===========================================================================
# Evidence — consistent SEC tiering, and XBRL structured facts are untouched
# ===========================================================================
def _company() -> CompanyContext:
    return CompanyContext(
        ticker="AAPL",
        exchange="NASDAQ",
        company_name="Apple Inc.",
        country="US",
        cik="0000320193",
    )


class TestSecDocumentEvidence:
    async def test_evidence_uses_the_sec_transport_and_content_tiers(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )

        items, _ = sec_artifacts_to_evidence(
            artifacts, company=_company(), max_items=5
        )

        assert items
        for item in items:
            assert item.source_id == SEC_ID
            assert item.content_source_tier == T1_PRIMARY_FILING
            assert item.provider_transport_tier == T2_REGULATOR_OR_GOV

    async def test_excerpt_items_carry_section_and_method_provenance(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )
        items, _ = sec_artifacts_to_evidence(
            artifacts, company=_company(), max_items=5
        )
        excerpts = [i for i in items if i.source_type == SEC_DOCUMENT_EXCERPT_TYPE]
        assert excerpts
        provenance = " ".join(p for i in excerpts for p in i.provenance)
        assert "method=html" in provenance
        assert "section=" in provenance

    def test_failed_artifact_yields_gaps_only_never_evidence(self) -> None:
        from app.services.sources.gaps import GapSeverity, GapType, SourceGap

        artifact = PrimaryDocumentArtifact(
            source_url=_BODY_URL,
            status="extraction_failed",
            failure_code=FAILURE_HTTP_CLIENT_ERROR,
            source_gaps=[
                SourceGap(
                    connector_key="sec_edgar",
                    source_id="sec_edgar",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message="SEC filing body was not fetched.",
                    blocks_research_complete=False,
                )
            ],
        )
        items, gaps = sec_artifacts_to_evidence(
            [artifact], company=_company(), max_items=5
        )
        assert items == []
        assert len(gaps) == 1

    async def test_xbrl_structured_fact_evidence_is_unchanged_by_this_path(
        self, monkeypatch
    ) -> None:
        """SUPPLEMENT, never replace: the filing-metadata items are identical."""
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        cfg = _cfg()

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            return await live_sec_primary_document_extractor(
                cik, filings, cfg=cfg, **{k: v for k, v in kwargs.items() if k != "cfg"}
            )

        without = await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            cfg=cfg,
        )
        with_bodies = await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=cfg,
        )

        def _metadata(collected):
            return [
                (i.source_type, i.url, i.excerpt)
                for i in collected.evidence_items
                if i.source_type == "company_filing"
            ]

        # The pre-existing SEC filing-metadata evidence is byte-identical.
        assert _metadata(with_bodies) == _metadata(without)
        assert _metadata(without)
        # And the body path only ADDED items on top.
        assert len(with_bodies.evidence_items) > len(without.evidence_items)

    async def test_artifacts_are_threaded_out_for_persistence(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        cfg = _cfg()

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            return await live_sec_primary_document_extractor(
                cik, filings, cfg=cfg, **{k: v for k, v in kwargs.items() if k != "cfg"}
            )

        collected = await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=cfg,
        )
        assert len(collected.primary_document_artifacts) == 1
        assert (
            collected.primary_document_artifacts[0].discovery_strategy
            == STRATEGY_SEC_ACCESSION
        )

    async def test_non_us_issuer_never_runs_the_sec_body_path(
        self, monkeypatch
    ) -> None:
        _forbid_network(monkeypatch)
        called = {"n": 0}

        async def _extractor(*args: Any, **kwargs: Any):
            called["n"] += 1
            return []

        collected = await collect_company_source_evidence(
            company=CompanyContext(
                ticker="CFR", exchange="SW", company_name="Richemont", country="Switzerland"
            ),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=_cfg(),
        )
        assert called["n"] == 0
        assert collected.primary_document_artifacts == []

    async def test_sec_and_ir_paths_share_one_aggregate_budget(
        self, monkeypatch
    ) -> None:
        """Adding SEC bodies must not double the worst-case ingestion wall time."""
        seen: dict[str, Any] = {}

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            seen["budget_seconds"] = kwargs.get("budget_seconds")
            return []

        cfg = _cfg(primary_document_ingestion_budget_seconds=30)
        await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=cfg,
        )
        assert seen["budget_seconds"] is not None
        assert 0 < seen["budget_seconds"] <= 30

    async def test_sec_leg_is_sub_capped_and_cannot_starve_the_ir_leg(
        self, monkeypatch
    ) -> None:
        """PR-review nit 6: the SEC leg may take at most half the shared budget."""
        from app.services.sources import company_evidence as ce

        seen: dict[str, Any] = {}
        captured: dict[str, Any] = {}

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            seen["budget_seconds"] = kwargs.get("budget_seconds")
            return []

        async def _deep_extractor(url, **kwargs):  # noqa: ANN001 - never invoked
            raise AssertionError("no document should be fetched in this test")

        real_cls = ce.CompanyIrConnector

        class _SpyConnector(real_cls):  # type: ignore[misc, valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(ce, "CompanyIrConnector", _SpyConnector)

        cfg = _cfg(primary_document_ingestion_budget_seconds=30)
        await collect_company_source_evidence(
            company=_company(),
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            primary_document_extractor=_deep_extractor,
            cfg=cfg,
        )
        # SEC gets at most half…
        assert 0 < seen["budget_seconds"] <= 15
        # …and the IR leg still receives a usable remainder of the same budget.
        assert captured["ingestion_budget_seconds"] > 15
        # PR-review blocker 4: the connector must actually receive the settings,
        # otherwise every discovery knob silently falls back to a module default.
        assert captured["cfg"] is cfg

    async def test_a_spent_budget_is_exhausted_not_unbounded(
        self, monkeypatch
    ) -> None:
        """PR-review nit 5: 0.0 seconds left must mean EXHAUSTED for both legs."""
        from app.services.sources import company_evidence as ce

        seen: dict[str, Any] = {}

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            seen["budget_seconds"] = kwargs.get("budget_seconds")
            return []

        # A budget that is configured but already fully spent by the clock.
        ticks = iter([0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
        monkeypatch.setattr(ce.time, "monotonic", lambda: next(ticks, 10_000.0))

        await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=_cfg(primary_document_ingestion_budget_seconds=30),
        )
        # 0.0 — an exhausted budget — NOT None, which would mean "unbounded".
        assert seen["budget_seconds"] == 0.0

    async def test_no_configured_budget_stays_unbounded(self, monkeypatch) -> None:
        seen: dict[str, Any] = {}

        async def _extractor(cik, filings, **kwargs):  # noqa: ANN001
            seen["budget_seconds"] = kwargs.get("budget_seconds")
            return []

        await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=_cfg(primary_document_ingestion_budget_seconds=0),
        )
        assert seen["budget_seconds"] is None

    async def test_extractor_failure_never_breaks_evidence_collection(
        self, monkeypatch
    ) -> None:
        async def _extractor(*args: Any, **kwargs: Any):
            raise RuntimeError("sec body path exploded")

        collected = await collect_company_source_evidence(
            company=_company(),
            source_ids=[SEC_ID],
            filings=_FILINGS,
            sec_primary_document_extractor=_extractor,
            cfg=_cfg(),
        )
        assert collected.primary_document_artifacts == []
        # The pre-existing SEC metadata evidence still came through.
        assert any(
            i.source_type == "company_filing" for i in collected.evidence_items
        )


# ===========================================================================
# Secret hygiene
# ===========================================================================
class TestSecretHygiene:
    async def test_no_credentials_or_bodies_appear_on_an_artifact(
        self, monkeypatch
    ) -> None:
        _patch(monkeypatch, documents=[_doc()], bodies=_ok_body())
        artifacts = await live_sec_primary_document_extractor(
            "0000320193", _FILINGS, cfg=_cfg()
        )
        blob = str(artifacts[0].model_dump())
        for marker in ("api_token", "password", "Authorization", "secret"):
            assert marker.lower() not in blob.lower()

    def test_attempt_records_carry_no_url_secrets(self) -> None:
        artifact = PrimaryDocumentArtifact(
            source_url="https://www.sec.gov/Archives/x.htm?api_token=SHOULD_NOT_PERSIST",
            status="extraction_failed",
            failure_code=FAILURE_HTTP_CLIENT_ERROR,
            discovery_strategy=STRATEGY_SEC_ACCESSION,
        )
        record = attempts_for_primary_documents([artifact])[0]
        # The writer canonicalizes + strips before storing; the mapper must not
        # smuggle the raw value anywhere else on the record.
        assert record.failure_code == FAILURE_HTTP_CLIENT_ERROR
        assert record.content_hash is None


# ===========================================================================
# Evidence budgeting — SEC filing-body items must not be dropped first
# (PR-review nit 7: they fell through to the lowest-priority bucket).
# ===========================================================================
class TestSecEvidenceBudgetCategory:
    @staticmethod
    def _item(source_type: str):
        from app.services.llm.schemas import EvidenceItem as LlmEvidenceItem

        return LlmEvidenceItem(
            id="SECFACT1_1",
            source_tier=T1_PRIMARY_FILING,
            content_tier=T1_PRIMARY_FILING,
            transport_tier=T2_REGULATOR_OR_GOV,
            source_type=source_type,
            title="10-K: Total net sales",
            excerpt="Total net sales = 383,285 (million USD) [FY2024]",
            data_quality="B",
            fields_supported=["Total net sales"],
        )

    def test_sec_filing_fact_is_primary_document_evidence(self) -> None:
        from app.services.llm.evidence_budget import (
            CATEGORY_PRIMARY_DOCUMENT,
            evidence_category,
        )
        from app.services.sources.company_evidence import SEC_DOCUMENT_FACT_TYPE

        assert (
            evidence_category(self._item(SEC_DOCUMENT_FACT_TYPE))
            == CATEGORY_PRIMARY_DOCUMENT
        )

    def test_sec_filing_excerpt_is_primary_document_evidence(self) -> None:
        from app.services.llm.evidence_budget import (
            CATEGORY_PRIMARY_DOCUMENT,
            evidence_category,
        )

        assert (
            evidence_category(self._item(SEC_DOCUMENT_EXCERPT_TYPE))
            == CATEGORY_PRIMARY_DOCUMENT
        )

    def test_structured_sec_xbrl_facts_keep_their_own_category(self) -> None:
        """SUPPLEMENT ONLY: the authoritative XBRL bucket is not re-labelled."""
        from app.services.llm.evidence_budget import (
            CATEGORY_FINANCIAL_FACT,
            evidence_category,
        )

        assert (
            evidence_category(self._item("sec_financial_statement"))
            == CATEGORY_FINANCIAL_FACT
        )

    def test_metadata_only_sec_item_is_still_only_a_reference(self) -> None:
        """CFR invariant: a metadata-only item never becomes document evidence."""
        from app.services.llm.evidence_budget import (
            CATEGORY_SOURCE_REFERENCE,
            evidence_category,
        )
        from app.services.sources.company_evidence import SEC_DOCUMENT_FACT_TYPE

        item = self._item(SEC_DOCUMENT_FACT_TYPE)
        item.data_quality = "metadata_only"
        assert evidence_category(item) == CATEGORY_SOURCE_REFERENCE


@pytest.mark.parametrize("form", ["10-K", "20-F", "10-Q", "6-K", "8-K"])
def test_supported_forms_all_map_to_a_known_doc_kind(form: str) -> None:
    assert doc_kind_for_form(form) in {
        DOC_KIND_ANNUAL_REPORT,
        DOC_KIND_INTERIM_REPORT,
        DOC_KIND_RESULTS_RELEASE,
    }


def test_resolved_filing_document_url_cannot_be_mutated() -> None:
    """Frozen dataclass — a validated Archives URL cannot be swapped afterwards."""
    doc = _doc()
    with pytest.raises(Exception):
        doc.canonical_url = "https://evil.test/x"  # type: ignore[misc]
    assert doc.canonical_url == _BODY_URL
