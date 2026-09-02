"""Primary-document parsing must never run ON the event loop.

Regression cover for the ib-stg-api outage class observed 2026-08-24 → 09-02:
``extract_document_text`` / ``extract_primary_document`` / ``validate_extracted_facts``
are pure-Python CPU-bound parses that were called directly from ``async def``
fetchers. A slow document therefore blocked the loop, uvicorn missed its
heartbeat, and gunicorn killed the ONLY worker with ``[CRITICAL] WORKER TIMEOUT``
→ ``SIGKILL`` — destroying every in-flight research run and 502-ing every
concurrent request. Six occurrences in ten days; one document measured 186s
against a 120s worker timeout.

Two independent assertions per path, because each catches a different mistake:
  * DETERMINISTIC — the parse executes on a thread that is NOT the loop thread.
    Fails the moment someone reverts to a direct call. No timing involved.
  * BEHAVIOURAL — a concurrent ticker keeps getting scheduled WHILE a genuinely
    blocking parse runs. This is the property the worker's heartbeat depends on,
    so it is worth asserting directly rather than trusting the proxy above.
"""

from __future__ import annotations

import asyncio
import threading
import time

from app.services.sources import live_fetchers
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExtraction,
)

# A blocking parse long enough that a starved loop is unmistakable, yet short
# enough to keep the suite fast.
_BLOCK_SECONDS = 0.5
_TICK_SECONDS = 0.01
# Theoretical max ticks is ~50; a loop that is genuinely free clears this easily,
# a blocked one scores 0–1. The wide margin is deliberate anti-flake headroom.
_MIN_TICKS = 10


def _pdf_fetch() -> DocumentFetchResult:
    return DocumentFetchResult(
        requested_url="https://issuer.example/ar2024.pdf",
        final_url="https://issuer.example/ar2024.pdf",
        status_code=200,
        content_type="application/pdf",
        document_type="pdf",
        content=b"%PDF-1.7\n" + b"0" * 512,
    )


async def _ticker(stop: threading.Event) -> int:
    """Count how many times the loop scheduled us. A blocked loop starves this."""
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(_TICK_SECONDS)
        ticks += 1
    return ticks


class _Recorder:
    """A parse stub that genuinely blocks and records the thread it ran on."""

    def __init__(self, result):
        self.result = result
        self.thread_name: str | None = None
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.thread_name = threading.current_thread().name
        self.calls += 1
        time.sleep(_BLOCK_SECONDS)  # a real, uninterruptible CPU-bound-style stall
        return self.result


async def _run_with_ticker(coro_factory):
    """Run ``coro_factory()`` while a ticker measures loop liveness."""
    stop = threading.Event()
    tick_task = asyncio.create_task(_ticker(stop))
    loop_thread = threading.current_thread().name
    result = await coro_factory()
    stop.set()
    ticks = await tick_task
    return result, ticks, loop_thread


# --------------------------------------------------------------------------- #
# Deep path: extract_primary_document + validate_extracted_facts
# --------------------------------------------------------------------------- #


def test_deep_extraction_runs_off_the_event_loop(monkeypatch):
    extraction = PrimaryDocumentExtraction(
        content_hash="a" * 64,
        mime_type="application/pdf",
        extraction_method="pdfplumber",
        status=STATUS_EXTRACTED,
    )
    parse = _Recorder(extraction)
    monkeypatch.setattr(live_fetchers, "extract_primary_document", parse)
    # Keep the assertion on the parse alone: validation gets its own test below.
    monkeypatch.setattr(live_fetchers, "validate_extracted_facts", lambda *a, **k: [])

    async def scenario():
        return await live_fetchers._artifact_from_fetch(
            _pdf_fetch(),
            title="Annual Report 2024",
            original_language=None,
            issuer_context=None,
            cfg=live_fetchers.default_settings,
            fetch_ms=1,
        )

    artifact, ticks, loop_thread = asyncio.run(_run_with_ticker(scenario))

    assert parse.calls == 1
    # DETERMINISTIC: the parse did not run on the loop thread.
    assert parse.thread_name is not None
    assert parse.thread_name != loop_thread, (
        "extract_primary_document ran ON the event loop thread — this is exactly "
        "the defect that caused the gunicorn WORKER TIMEOUT/SIGKILL outages."
    )
    # BEHAVIOURAL: the loop stayed schedulable throughout the blocking parse.
    assert ticks >= _MIN_TICKS, (
        f"event loop was starved during extraction (only {ticks} ticks in "
        f"~{_BLOCK_SECONDS}s); the worker heartbeat would have been missed"
    )
    assert artifact.status == STATUS_EXTRACTED


def test_fact_validation_runs_off_the_event_loop(monkeypatch):
    extraction = PrimaryDocumentExtraction(
        content_hash="b" * 64,
        mime_type="application/pdf",
        extraction_method="pdfplumber",
        status=STATUS_EXTRACTED,
    )
    monkeypatch.setattr(live_fetchers, "extract_primary_document", lambda *a, **k: extraction)
    validate = _Recorder([])
    monkeypatch.setattr(live_fetchers, "validate_extracted_facts", validate)

    async def scenario():
        return await live_fetchers._artifact_from_fetch(
            _pdf_fetch(),
            title="Annual Report 2024",
            original_language=None,
            issuer_context=None,
            cfg=live_fetchers.default_settings,
            fetch_ms=1,
        )

    _, ticks, loop_thread = asyncio.run(_run_with_ticker(scenario))

    assert validate.calls == 1
    assert validate.thread_name is not None
    assert validate.thread_name != loop_thread, (
        "validate_extracted_facts ran ON the event loop thread"
    )
    assert ticks >= _MIN_TICKS


# --------------------------------------------------------------------------- #
# Shallow path: extract_document_text
# --------------------------------------------------------------------------- #


def test_document_text_extraction_runs_off_the_event_loop(monkeypatch):
    from app.services.sources.document_text_extractor import DocumentTextExtraction

    parse = _Recorder(
        DocumentTextExtraction(source_url="https://issuer.example/ar2024.pdf", document_type="pdf")
    )
    monkeypatch.setattr(live_fetchers, "extract_document_text", parse)

    async def fake_fetch(url, **kwargs):
        return _pdf_fetch()

    monkeypatch.setattr(live_fetchers, "safe_fetch_document", fake_fetch)

    async def scenario():
        return await live_fetchers.live_document_extractor(
            "https://issuer.example/ar2024.pdf",
            allowed_domains=("issuer.example",),
            title_hint="Annual Report 2024",
        )

    _, ticks, loop_thread = asyncio.run(_run_with_ticker(scenario))

    assert parse.calls == 1
    assert parse.thread_name is not None
    assert parse.thread_name != loop_thread, "extract_document_text ran ON the event loop thread"
    assert ticks >= _MIN_TICKS


# --------------------------------------------------------------------------- #
# Concurrency: two documents must not serialize behind one another on the loop
# --------------------------------------------------------------------------- #


def test_concurrent_documents_do_not_block_each_other(monkeypatch):
    """Two parses overlap in wall time instead of summing.

    On the loop they would strictly serialize (~2x). Off it they overlap, which
    is what keeps a multi-document ingestion inside its budget.
    """
    extraction = PrimaryDocumentExtraction(
        content_hash="c" * 64,
        mime_type="application/pdf",
        extraction_method="pdfplumber",
        status=STATUS_EXTRACTED,
    )

    def blocking(*args, **kwargs):
        time.sleep(_BLOCK_SECONDS)
        return extraction

    monkeypatch.setattr(live_fetchers, "extract_primary_document", blocking)
    monkeypatch.setattr(live_fetchers, "validate_extracted_facts", lambda *a, **k: [])

    async def scenario():
        started = time.perf_counter()
        await asyncio.gather(
            *(
                live_fetchers._artifact_from_fetch(
                    _pdf_fetch(),
                    title=f"Doc {i}",
                    original_language=None,
                    issuer_context=None,
                    cfg=live_fetchers.default_settings,
                    fetch_ms=1,
                )
                for i in range(2)
            )
        )
        return time.perf_counter() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < _BLOCK_SECONDS * 1.8, (
        f"two documents took {elapsed:.2f}s — they serialized on the loop "
        f"instead of overlapping in worker threads"
    )
