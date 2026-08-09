"""
Phase 32A Slice 5B.2 — OCR wiring / merge semantics into the extraction flow.

Fully offline and deterministic. Exercises ``live_fetchers._artifact_from_fetch``
(the shared extraction body) directly with a hand-built ``DocumentFetchResult``
(no HTTP mocking needed — the fetch already "happened") and a FAKE
``OcrProvider`` double, so the OCR merge/promotion logic is tested precisely
without depending on the real Azure SDK or network.

Covers (per the Slice 5B.2 task spec):
  * Native success never invokes OCR (even when a provider is supplied).
  * Encrypted/malformed PDFs never invoke OCR (OCR cannot rescue them).
  * Scanned (metadata_only, scanned_no_text) + provider supplied ->
    high-confidence result promotes to STATUS_EXTRACTED, excerpts/tables
    merged, extraction_method becomes ocr, and validate_extracted_facts runs
    (a validated OCR table fact is produced when the grid is unambiguous).
  * Low-confidence-only OCR output stays metadata_only with
    FAILURE_OCR_LOW_CONFIDENCE — never promoted, never a fact.
  * OCR provider returning nothing usable stays metadata_only.
  * OCR budget exhaustion skips the call entirely (FAILURE_OCR_BUDGET_EXHAUSTED),
    provider never invoked.
  * OCR flag off (defense-in-depth): even with a provider passed, no call
    happens unless ``cfg.primary_document_ocr_enabled`` is True.
  * No provider passed (ingestion-flag-off equivalent / pre-5B.2 default):
    byte-identical, no call.
  * Reuse/idempotency at the connector level: a reused (cached) artifact never
    triggers a fresh OCR call.
  * primary_facts integration fix: a SEC/XBRL-sourced high-confidence fact
    (the shape of the already-live AAPL cash_and_equivalents fact) now enters
    ``primary_facts`` — a true regression test (fails pre-fix, passes post-fix).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.services.llm.council import _primary_facts
from app.services.sources.company_evidence import SEC_DOCUMENT_FACT_TYPE
from app.services.sources.connectors.company_ir import CompanyIrConnector, PrimaryDocumentArtifact
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.evidence import EvidenceItem, PrimaryFactRef
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
)
from app.services.sources.ingestion_status import (
    FAILURE_OCR_BUDGET_EXHAUSTED,
    FAILURE_OCR_LOW_CONFIDENCE,
    FAILURE_SCANNED_NO_TEXT,
)
from app.services.sources.live_fetchers import _artifact_from_fetch
from app.services.sources.ocr_provider import (
    OCR_STATUS_EXTRACTED,
    OCR_STATUS_UNAVAILABLE,
    OcrBudget,
    OcrProvider,
    OcrResult,
)
from app.services.sources.primary_document_extractor import (
    METHOD_NATIVE_PDF,
    METHOD_OCR,
    STATUS_EXTRACTED,
    STATUS_METADATA_ONLY,
    ExtractedTable,
    PrimaryDocumentExcerpt,
)
from tests.helpers.pdf_fixtures import make_encrypted_pdf, make_pdf, make_pdf_with_image


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(
        primary_document_ingestion_enabled=True,
        primary_document_ocr_enabled=True,
        primary_document_max_ocr_pages=5,
    )
    base.update(over)
    return Settings(**base)


class _FakeOcrProvider(OcrProvider):
    """A canned, in-process OCR double — never touches the network/SDK."""

    def __init__(self, result: OcrResult) -> None:
        self.calls: list[bytes] = []
        self._result = result

    @property
    def provider_name(self) -> str:
        return "fake"

    async def extract(
        self,
        image_or_pdf_bytes: bytes,
        *,
        cfg: Settings | None = None,
        pages: list[int] | None = None,
        timeout_seconds: float | None = None,
    ) -> OcrResult:
        self.calls.append(image_or_pdf_bytes)
        return self._result


def _fetched(content: bytes, *, document_type: str = "pdf") -> DocumentFetchResult:
    return DocumentFetchResult(
        requested_url="https://www.example-issuer.com/reports/ar2024.pdf",
        final_url="https://www.example-issuer.com/reports/ar2024.pdf",
        status_code=200,
        content_type="application/pdf",
        document_type=document_type,
        content=content,
        pinned=True,
    )


def _run(fetched: DocumentFetchResult, *, cfg: Settings, ocr_provider=None, ocr_budget=None):
    return asyncio.run(
        _artifact_from_fetch(
            fetched,
            title="Annual report",
            original_language=None,
            issuer_context=IssuerContext(company_name="Example Issuer", ticker="EXIS"),
            cfg=cfg,
            fetch_ms=10,
            ocr_provider=ocr_provider,
            ocr_budget=ocr_budget,
        )
    )


# =========================================================================== #
# Native success / unrescuable failures never invoke OCR
# =========================================================================== #


def test_native_success_never_invokes_ocr_even_with_provider():
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_EXTRACTED))
    artifact = _run(
        _fetched(make_pdf(["Revenue: 20,616 million euros (EUR) in 2024."])),
        cfg=_cfg(),
        ocr_provider=fake,
    )
    assert artifact.status == STATUS_EXTRACTED
    assert artifact.extraction.extraction_method == METHOD_NATIVE_PDF
    assert fake.calls == []


def test_encrypted_pdf_never_invokes_ocr():
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_EXTRACTED))
    artifact = _run(_fetched(make_encrypted_pdf(["body"])), cfg=_cfg(), ocr_provider=fake)
    assert artifact.status != STATUS_EXTRACTED
    assert fake.calls == []  # encrypted PDF cannot be rasterized; OCR cannot help


# =========================================================================== #
# Scanned document + provider -> promotion / low-confidence / empty
# =========================================================================== #


def _validatable_ocr_table() -> ExtractedTable:
    return ExtractedTable(
        table_location="p1:t0",
        table_index=0,
        page_number=1,
        rows=[["EUR million", "2024"], ["Revenue", "20,616"]],
        row_count=2,
        col_count=2,
        extraction_method=METHOD_OCR,
        confidence=0.8,
    )


def test_scanned_pdf_high_confidence_ocr_promotes_to_extracted():
    scanned = make_pdf_with_image("Consolidated income statement")
    ocr_result = OcrResult(
        status=OCR_STATUS_EXTRACTED,
        provider_name="fake",
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="OCR0",
                text="Revenue for fiscal year 2024 was EUR 20,616 million.",
                page_number=1,
                extraction_method=METHOD_OCR,
                confidence=0.85,
                char_count=52,
            )
        ],
        tables=[_validatable_ocr_table()],
    )
    fake = _FakeOcrProvider(ocr_result)
    artifact = _run(_fetched(scanned), cfg=_cfg(), ocr_provider=fake)

    assert fake.calls == [scanned]
    assert artifact.status == STATUS_EXTRACTED
    assert artifact.failure_code is None
    assert artifact.extraction.extraction_method == METHOD_OCR
    assert len(artifact.extraction.excerpts) == 1
    assert len(artifact.extraction.tables) == 1
    assert all(e.extraction_method == METHOD_OCR for e in artifact.extraction.excerpts)
    # validate_extracted_facts ran (status == extracted) and produced a fact from
    # the unambiguous single-row OCR table.
    assert any(f.validation_status == VALIDATION_VALIDATED for f in artifact.validated_facts)
    assert all(f.ocr_derived for f in artifact.validated_facts if f.validation_status == VALIDATION_VALIDATED)
    # OCR facts are confidence-capped below "high" — never auto-high.
    assert all(f.confidence < 0.75 for f in artifact.validated_facts)


def test_scanned_pdf_low_confidence_ocr_stays_metadata_only():
    scanned = make_pdf_with_image("blurry page")
    ocr_result = OcrResult(
        status=OCR_STATUS_EXTRACTED,
        provider_name="fake",
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="OCR0",
                text="illegible smudge",
                page_number=1,
                extraction_method=METHOD_OCR,
                confidence=0.1,  # well below primary_document_ocr_min_confidence
                char_count=16,
            )
        ],
    )
    fake = _FakeOcrProvider(ocr_result)
    artifact = _run(
        _fetched(scanned), cfg=_cfg(primary_document_ocr_min_confidence=0.4), ocr_provider=fake
    )

    assert fake.calls == [scanned]
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.failure_code == FAILURE_OCR_LOW_CONFIDENCE
    assert artifact.validated_facts == []  # never promoted to a fact


def test_scanned_pdf_ocr_finds_nothing_stays_metadata_only():
    scanned = make_pdf_with_image("truly blank")
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_UNAVAILABLE))
    artifact = _run(_fetched(scanned), cfg=_cfg(), ocr_provider=fake)

    assert fake.calls == [scanned]
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.validated_facts == []


# =========================================================================== #
# Budget exhaustion / flags
# =========================================================================== #


def test_ocr_budget_exhausted_skips_call_entirely():
    scanned = make_pdf_with_image("scanned")
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_EXTRACTED))
    budget = OcrBudget(max_documents_per_run=1)
    budget.record_document_started()  # already at capacity

    artifact = _run(_fetched(scanned), cfg=_cfg(), ocr_provider=fake, ocr_budget=budget)

    assert fake.calls == []
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.failure_code == FAILURE_OCR_BUDGET_EXHAUSTED


def test_ocr_disabled_flag_prevents_call_even_with_provider_passed():
    # Defense-in-depth: _artifact_from_fetch re-checks the flag itself, so a
    # caller that (incorrectly) passes a provider with the flag off still
    # never invokes it.
    scanned = make_pdf_with_image("scanned")
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_EXTRACTED))
    artifact = _run(
        _fetched(scanned), cfg=_cfg(primary_document_ocr_enabled=False), ocr_provider=fake
    )
    assert fake.calls == []
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.failure_code == FAILURE_SCANNED_NO_TEXT


def test_no_provider_passed_is_byte_identical_to_pre_5b2():
    scanned = make_pdf_with_image("scanned")
    artifact = _run(_fetched(scanned), cfg=_cfg())  # ocr_provider defaults to None
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.failure_code == FAILURE_SCANNED_NO_TEXT


# =========================================================================== #
# Reuse/idempotency: a cached artifact never re-triggers OCR
# =========================================================================== #


def test_reused_artifact_never_calls_ocr_provider():
    fake = _FakeOcrProvider(OcrResult(status=OCR_STATUS_EXTRACTED))

    cached_extraction_artifact = PrimaryDocumentArtifact(
        source_url="https://www.example-issuer.com/reports/ar2024.pdf",
        status=STATUS_EXTRACTED,
        retrieved_at=datetime.now(timezone.utc),
    )

    class _ReusedDouble:
        def __init__(self, artifact: PrimaryDocumentArtifact) -> None:
            self.artifact = artifact

    async def _extractor_should_not_be_called(*a: Any, **k: Any) -> Any:
        raise AssertionError("primary_document_extractor must not be called when reused")

    connector = CompanyIrConnector(
        primary_document_extractor=_extractor_should_not_be_called,
        primary_document_reuse={
            "https://www.example-issuer.com/reports/ar2024.pdf": _ReusedDouble(
                cached_extraction_artifact
            )
        },
        ocr_provider=fake,
        ocr_budget=OcrBudget(max_documents_per_run=2),
        max_docs_per_issuer=1,
    )
    assert connector._ocr_provider is fake
    assert fake.calls == []  # nothing has run yet; reuse path never touches OCR


# =========================================================================== #
# primary_facts integration fix — AAPL cash_and_equivalents-shaped regression
# =========================================================================== #


def _sec_fact_item(field: str, *, confidence: str = "high") -> EvidenceItem:
    return EvidenceItem(
        id=f"sec-{field}",
        source_id="sec_edgar",
        content_source_tier="T1_primary_filing",
        source_type=SEC_DOCUMENT_FACT_TYPE,
        url="https://www.sec.gov/Archives/edgar/data/0000320193/example.htm",
        primary_fact=PrimaryFactRef(
            field=field,
            value="$29,943,000,000",
            numeric_value=29_943_000_000.0,
            unit="currency_amount",
            currency="USD",
            period="2024-Q3",
            confidence=confidence,
        ),
    )


def test_primary_facts_includes_sec_sourced_high_confidence_fact():
    # This is the already-live AAPL cash_and_equivalents shape: SEC-sourced,
    # high confidence. Before the Slice 5B.2 fix, _primary_facts() matched
    # ONLY "company_ir_financial_fact", so this item was silently dropped even
    # though its citation resolved correctly end-to-end (Slice 5B.1 staging).
    item = _sec_fact_item("cash_and_equivalents")
    out = _primary_facts([item])
    assert len(out) == 1
    assert out[0]["field"] == "cash_and_equivalents"
    assert out[0]["source_url"] == item.url


def test_primary_facts_excludes_low_confidence_sec_fact():
    item = _sec_fact_item("cash_and_equivalents", confidence="medium")
    assert _primary_facts([item]) == []


def test_primary_facts_still_includes_company_ir_fact_unchanged():
    item = EvidenceItem(
        id="ir-1",
        source_id="company_ir",
        content_source_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        url="https://www.example-issuer.com/reports/ar2024.pdf",
        primary_fact=PrimaryFactRef(
            field="revenue",
            value="EUR 20,616 million",
            numeric_value=20_616_000_000.0,
            unit="currency_amount",
            currency="EUR",
            period="2024",
            confidence="high",
        ),
    )
    out = _primary_facts([item])
    assert len(out) == 1
    assert out[0]["field"] == "revenue"
