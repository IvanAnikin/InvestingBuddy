"""Phase 32A Slice 5B.1 hotfix — SEC preflight identity resolution + visibility.

Staging validation of Slice 5B.1 found the SEC filing-body path is a SILENT
no-op for every issuer, including AAPL: ``CompanyContext.cik`` is populated from
``company_snapshot`` -> ``company_identity``, which carries no ``cik`` field at
all, so ``resolve_filing_documents`` early-returned at ``normalize_cik(cik) is
None`` with NO log, NO SourceGap, and NO attempt row. A prior hotfix
(``d5351fa``) taught the resolver to derive a CIK from the filing metadata
itself, but did not yet: (a) cross-check that derived value against a caller-
supplied one, (b) fail closed on disagreement, or (c) make ANY preflight
failure — malformed accession, unsafe filename, no selectable document, or the
preflight budget running out — visible as a durable attempt record. This file
covers that remaining gap.

Fully OFFLINE and deterministic: the offline fake-httpx idiom from
``test_phase32a_slice5b1_sec_filing_body.py`` is reused so the resolution layer
is exercised for real (not mocked away), while the persistence-layer tests use a
REAL in-memory SQLite async database (``aiosqlite``) so INSERT / UPDATE / SELECT
are genuinely executed and read back, mirroring
``test_phase32a_slice5b1_ingestion_attempts.py``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import agent_run as _agent_run_model  # noqa: F401
from app.models import company as _company_model  # noqa: F401
from app.models import document_ingestion_attempt as _attempt_model  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.document_ingestion_attempt import DocumentIngestionAttempt
from app.services.document_ingestion_attempt_service import (
    IngestionAttemptRecord,
    record_ingestion_attempts,
)
from app.services.sources import live_fetchers
from app.services.sources.ingestion_attempts import (
    SOURCE_TIER_PRIMARY_FILING,
    SOURCE_TYPE_SEC_FILING,
    artifact_to_attempt,
    attempts_for_primary_documents,
)
from app.services.sources.ingestion_status import (
    ATTEMPT_METADATA_ONLY,
    ATTEMPT_REJECTED_SECURITY,
    ATTEMPT_TIMEOUT,
    ATTEMPT_UNSUPPORTED,
    FAILURE_CONFLICTING_CIK,
    FAILURE_INVALID_SEC_URL,
    FAILURE_MALFORMED_ACCESSION,
    FAILURE_MISSING_CIK,
    FAILURE_NO_PRIMARY_FILING_DOCUMENT,
    FAILURE_PREFLIGHT_BUDGET_EXHAUSTED,
)
from app.services.sources.live_fetchers import live_sec_primary_document_extractor
from app.services.sources.sec_filing_documents import (
    SecPreflightFailure,
    SecRateLimiter,
    resolve_filing_documents,
    resolve_sec_filer_cik,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.

AAPL_CIK = "0000320193"
OTHER_CIK = "0000789019"  # a different filer entirely (Microsoft's real CIK)
ACC_10K = "0000320193-24-000123"
ACC_8K = "0000320193-24-000456"
ACC_OTHER = "0000789019-24-000001"  # accession prefix belongs to OTHER_CIK
AAPL_ARCHIVES = (
    "https://www.sec.gov/Archives/edgar/data/320193/"
    "000032019324000123/aapl-20241028.htm"
)


# --------------------------------------------------------------------------- #
# Offline fake httpx client (same idiom as the SEC filing-body test module).
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(
        self, *, status_code: int = 200, body: bytes = b"", headers=None
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, handler, **kw: Any) -> None:
        self._handler = handler
        self.requests: list[str] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def stream(self, method: str, url: str):
        self.requests.append(url)
        return self._handler(url)


def _patch_httpx(monkeypatch, handler) -> list[_FakeClient]:
    created: list[_FakeClient] = []

    def _factory(**kw: Any) -> _FakeClient:
        client = _FakeClient(handler, **kw)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return created


def _index_body(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"directory": {"name": "/Archives", "item": items}}).encode()


def _ok(items: list[dict[str, Any]]) -> _FakeStream:
    return _FakeStream(
        status_code=200, headers={"content-type": "application/json"}, body=_index_body(items)
    )


def _entry(name: str, *, type_: str = "", size: Any = "0") -> dict[str, Any]:
    return {"name": name, "type": type_, "size": size}


def _quiet_limiter() -> SecRateLimiter:
    async def _sleep(delay: float) -> None:
        return None

    return SecRateLimiter(min_interval_seconds=0.0, sleep=_sleep, clock=lambda: 0.0)


def _resolver(ip: str = "93.184.216.34"):
    def resolve(host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return resolve


def _cfg(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "source_connector_allowlist_only": True,
        "primary_document_pin_dns_enabled": True,
        "primary_document_fetch_timeout_seconds": 15,
        "sec_request_min_interval_ms": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _filing(
    form: str, accession: str | None, filed: str, *, url: str | None = None
) -> dict[str, Any]:
    return {
        "form_type": form,
        "title": f"SEC {form} filing",
        "url": url,
        "filed_date": filed,
        "summary": None,
        "accession_number": accession,
    }


def _no_network_handler(url: str) -> _FakeStream:  # pragma: no cover - guard
    raise AssertionError(f"no network call should have been made: {url}")


# =========================================================================== #
# 1. resolve_sec_filer_cik — cross-checked, fail-closed identity resolution
# =========================================================================== #


def test_caller_cik_alone_resolves():
    cik, failure = resolve_sec_filer_cik(AAPL_CIK, [_filing("10-K", ACC_10K, "2024-11-01")])
    assert cik == AAPL_CIK
    assert failure is None


def test_filings_only_agree_resolves_without_a_caller_cik():
    cik, failure = resolve_sec_filer_cik(
        None,
        [
            _filing("10-K", ACC_10K, "2024-11-01", url=AAPL_ARCHIVES),
            _filing("8-K", ACC_8K, "2024-05-01"),  # same filer via accession prefix
        ],
    )
    assert cik == AAPL_CIK
    assert failure is None


def test_caller_and_filings_agree_resolves():
    cik, failure = resolve_sec_filer_cik(
        AAPL_CIK, [_filing("10-K", ACC_10K, "2024-11-01", url=AAPL_ARCHIVES)]
    )
    assert cik == AAPL_CIK
    assert failure is None


def test_caller_conflicts_with_filings_fails_closed():
    """A caller value that disagrees with the filings' own identity must refuse."""
    cik, failure = resolve_sec_filer_cik(
        OTHER_CIK, [_filing("10-K", ACC_10K, "2024-11-01", url=AAPL_ARCHIVES)]
    )
    assert cik is None
    assert failure == FAILURE_CONFLICTING_CIK


def test_filings_disagree_among_themselves_fails_closed_even_without_a_caller_value():
    cik, failure = resolve_sec_filer_cik(
        None,
        [
            _filing("10-K", ACC_10K, "2024-11-01"),  # -> AAPL_CIK via accession
            _filing("8-K", ACC_OTHER, "2024-05-01"),  # -> OTHER_CIK via accession
        ],
    )
    assert cik is None
    assert failure == FAILURE_CONFLICTING_CIK


def test_nothing_derivable_anywhere_is_missing_not_conflicting():
    cik, failure = resolve_sec_filer_cik(None, [_filing("10-K", None, "2024-11-01")])
    assert cik is None
    assert failure == FAILURE_MISSING_CIK


def test_company_name_and_ticker_are_never_consulted():
    """The resolver takes NO company/ticker argument at all — nothing to guess from."""
    import inspect

    params = set(inspect.signature(resolve_sec_filer_cik).parameters)
    assert "company_name" not in params
    assert "ticker" not in params
    assert "name" not in params


# =========================================================================== #
# 2. resolve_filing_documents(preflight_sink=...) — every preflight failure
#    becomes a bounded, safe record; a real network call never happens for a
#    candidate that cannot even be identified.
# =========================================================================== #


def test_missing_cik_produces_no_fetch_and_a_sanitized_preflight_record(monkeypatch):
    created = _patch_httpx(monkeypatch, _no_network_handler)
    # A real, safe sec.gov URL that is NOT shaped like an Archives filing path (no
    # /edgar/data/<cik>/...) and no accession — nothing derives a CIK from this,
    # but the filing's own URL is still a safe, non-fabricated identity to report.
    non_archives_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            None,
            [_filing("10-K", None, "2024-11-01", url=non_archives_url)],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert created == []  # NO network call was made
    assert len(sink) == 1
    assert sink[0].failure_code == FAILURE_MISSING_CIK
    # The identity is the filing's own safe sec.gov URL — never fabricated.
    assert sink[0].canonical_url == non_archives_url


def test_missing_cik_with_nothing_identifiable_anywhere_is_silently_skipped(
    monkeypatch,
):
    """No URL, no accession -> genuinely nothing safe/stable to key an attempt
    record on. Matches the pre-existing 'no accession, no record' convention —
    silence here is honest, not a regression."""
    created = _patch_httpx(monkeypatch, _no_network_handler)
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            None,
            [_filing("10-K", None, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert created == []
    assert sink == []


def test_conflicting_cik_produces_no_fetch_and_no_cross_company_attribution(monkeypatch):
    """The core safety property: a wrong CIK must never silently borrow another
    issuer's filing body."""
    created = _patch_httpx(monkeypatch, _no_network_handler)
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            OTHER_CIK,
            [_filing("10-K", ACC_10K, "2024-11-01", url=AAPL_ARCHIVES)],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert created == []
    assert len(sink) == 1
    assert sink[0].failure_code == FAILURE_CONFLICTING_CIK
    # The identity used is AAPL's real archives location — never OTHER_CIK's.
    assert "320193" in sink[0].canonical_url
    assert "789019" not in sink[0].canonical_url


def test_malformed_accession_is_recorded_when_a_raw_value_exists(monkeypatch):
    created = _patch_httpx(monkeypatch, _no_network_handler)
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", "not-an-accession", "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert created == []  # a malformed accession never even reaches the index fetch
    assert len(sink) == 1
    assert sink[0].failure_code == FAILURE_MALFORMED_ACCESSION
    assert sink[0].accession_number is None  # nothing normalizable to report


def test_absent_accession_is_not_recorded_as_malformed(monkeypatch):
    """A filing with NO accession field at all was never a real candidate — this
    is pre-existing ``skipped_no_accession`` behaviour, unchanged."""
    created = _patch_httpx(monkeypatch, _no_network_handler)
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", None, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert created == []
    assert sink == []  # nothing safe/real to report — correctly silent


def test_no_selectable_primary_document_is_recorded(monkeypatch):
    """The index fetches fine but contains nothing usable (e.g. only exhibits)."""
    _patch_httpx(
        monkeypatch, lambda url: _ok([_entry("ex99-1.htm", type_="EX-99.1")])
    )
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", ACC_10K, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert len(sink) == 1
    assert sink[0].failure_code == FAILURE_NO_PRIMARY_FILING_DOCUMENT
    # Identity is the real, safe index location — never a fabricated document URL.
    assert sink[0].canonical_url.endswith("/index.json")
    assert "320193" in sink[0].canonical_url


def test_invalid_document_filename_is_recorded_not_leaked(monkeypatch):
    """A path-traversal filename from the index must never become a fetch URL —
    and the bad filename itself must never appear in the persisted identity.

    The name must still END in .htm/.html to pass the HTML-entry filter and be
    selected as the form-typed match; the traversal segment is what makes
    ``build_document_url`` refuse it."""
    _patch_httpx(
        monkeypatch, lambda url: _ok([_entry("../evil.htm", type_="10-K")])
    )
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", ACC_10K, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert len(sink) == 1
    assert sink[0].failure_code == FAILURE_INVALID_SEC_URL
    assert "passwd" not in sink[0].canonical_url
    assert ".." not in sink[0].canonical_url
    assert sink[0].canonical_url.endswith("/index.json")


def test_preflight_budget_exhausted_records_every_skipped_known_candidate(monkeypatch):
    """The attempt-cap bound stops resolution; every KNOWN, not-yet-visited
    candidate is recorded as skipped-for-budget rather than silently dropped."""
    _patch_httpx(monkeypatch, _no_network_handler)  # index fetch never reached
    filings = [
        _filing("10-K", ACC_10K, "2024-11-01"),
        _filing("10-Q", ACC_8K, "2024-08-01"),
        _filing("8-K", ACC_OTHER.replace("789019", "320193"), "2024-05-01"),
    ]
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            filings,
            max_documents=1,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            # deadline already in the past: EVERY candidate is skipped for budget.
            deadline=-1.0,
            clock=lambda: 0.0,
            preflight_sink=sink,
        )
    )
    assert docs == []
    assert len(sink) == len(filings)
    assert all(f.failure_code == FAILURE_PREFLIGHT_BUDGET_EXHAUSTED for f in sink)


def test_successful_resolution_leaves_the_sink_empty(monkeypatch):
    """No stale failure record survives a resolution that actually succeeds."""
    _patch_httpx(
        monkeypatch, lambda url: _ok([_entry("aapl-10k.htm", type_="10-K", size="900000")])
    )
    sink: list[SecPreflightFailure] = []
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", ACC_10K, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
            preflight_sink=sink,
        )
    )
    assert len(docs) == 1
    assert sink == []


def test_preflight_sink_none_is_a_pure_no_op(monkeypatch):
    """Every existing caller (preflight_sink=None, the default) is unaffected."""
    created = _patch_httpx(monkeypatch, _no_network_handler)
    docs = asyncio.run(
        resolve_filing_documents(
            None,
            [_filing("10-K", None, "2024-11-01")],
            max_documents=2,
            cfg=_cfg(),
            limiter=_quiet_limiter(),
            resolver=_resolver(),
        )
    )
    assert docs == []
    assert created == []


def test_repeated_resolution_of_the_same_candidate_is_deterministic(monkeypatch):
    """Same input -> same identity, every time (required for idempotent persistence)."""
    _patch_httpx(monkeypatch, _no_network_handler)
    filings = [_filing("10-K", "not-an-accession", "2024-11-01")]
    first: list[SecPreflightFailure] = []
    second: list[SecPreflightFailure] = []
    asyncio.run(
        resolve_filing_documents(
            AAPL_CIK, filings, max_documents=2, cfg=_cfg(), limiter=_quiet_limiter(),
            resolver=_resolver(), preflight_sink=first,
        )
    )
    asyncio.run(
        resolve_filing_documents(
            AAPL_CIK, filings, max_documents=2, cfg=_cfg(), limiter=_quiet_limiter(),
            resolver=_resolver(), preflight_sink=second,
        )
    )
    assert len(first) == len(second) == 1
    assert first[0].canonical_url == second[0].canonical_url
    assert first[0].failure_code == second[0].failure_code


# =========================================================================== #
# 3. live_sec_primary_document_extractor — end-to-end (real resolve_filing_
#    documents, NOT mocked) proves the artifact + SourceGap actually appear.
# =========================================================================== #


def _live_cfg(**overrides: Any) -> Settings:
    cfg = Settings()
    cfg.primary_document_ingestion_enabled = True
    cfg.primary_document_sec_body_enabled = True
    cfg.primary_document_sec_max_bodies = 2
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


async def test_missing_cik_end_to_end_yields_one_honest_artifact_no_fetch(monkeypatch):
    _patch_httpx(monkeypatch, _no_network_handler)
    non_archives_url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
    artifacts = await live_sec_primary_document_extractor(
        None,
        [_filing("10-K", None, "2024-11-01", url=non_archives_url)],
        cfg=_live_cfg(),
        resolver=_resolver(),
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.failure_code == FAILURE_MISSING_CIK
    assert art.extraction is None  # never mistaken for a successful extraction
    assert len(art.source_gaps) == 1
    gap_text = art.source_gaps[0].message.lower()
    assert "cik" in gap_text
    assert "not fetched" in gap_text


async def test_conflicting_cik_end_to_end_fails_closed_with_no_fetch(monkeypatch):
    _patch_httpx(monkeypatch, _no_network_handler)
    artifacts = await live_sec_primary_document_extractor(
        OTHER_CIK,
        [_filing("10-K", ACC_10K, "2024-11-01", url=AAPL_ARCHIVES)],
        cfg=_live_cfg(),
        resolver=_resolver(),
    )
    assert len(artifacts) == 1
    assert artifacts[0].failure_code == FAILURE_CONFLICTING_CIK


async def test_flag_off_produces_no_artifacts_and_no_network(monkeypatch):
    _patch_httpx(monkeypatch, _no_network_handler)
    artifacts = await live_sec_primary_document_extractor(
        None,
        [_filing("10-K", None, "2024-11-01")],
        cfg=_live_cfg(primary_document_ingestion_enabled=False),
        resolver=_resolver(),
    )
    assert artifacts == []


# =========================================================================== #
# 4. artifact_to_attempt mapping — every new failure code resolves onto an
#    EXISTING attempt status (no new status was introduced).
# =========================================================================== #


@pytest.mark.parametrize(
    "failure_code,expected_status",
    [
        (FAILURE_MISSING_CIK, ATTEMPT_METADATA_ONLY),
        (FAILURE_NO_PRIMARY_FILING_DOCUMENT, ATTEMPT_METADATA_ONLY),
        (FAILURE_CONFLICTING_CIK, ATTEMPT_REJECTED_SECURITY),
        (FAILURE_INVALID_SEC_URL, ATTEMPT_REJECTED_SECURITY),
        (FAILURE_MALFORMED_ACCESSION, ATTEMPT_UNSUPPORTED),
        (FAILURE_PREFLIGHT_BUDGET_EXHAUSTED, ATTEMPT_TIMEOUT),
    ],
)
async def test_preflight_failure_maps_onto_an_existing_attempt_status(
    monkeypatch, failure_code, expected_status
):
    _patch_httpx(monkeypatch, _no_network_handler)
    art = live_fetchers._preflight_artifact(
        SecPreflightFailure(
            canonical_url="urn:investingbuddy:sec-filing:test",
            accession_number=None,
            form_type=None,
            filing_date=None,
            failure_code=failure_code,
        )
    )
    record = artifact_to_attempt(
        art, source_type=SOURCE_TYPE_SEC_FILING, source_tier=SOURCE_TIER_PRIMARY_FILING
    )
    assert record.status == expected_status
    assert record.failure_code == failure_code


async def test_attempts_for_primary_documents_labels_preflight_as_sec_filing():
    art = live_fetchers._preflight_artifact(
        SecPreflightFailure(
            canonical_url="urn:investingbuddy:sec-filing:test",
            accession_number=ACC_10K,
            form_type="10-K",
            filing_date="2024-11-01",
            failure_code=FAILURE_MISSING_CIK,
        )
    )
    records = attempts_for_primary_documents([art])
    assert len(records) == 1
    assert records[0].source_type == SOURCE_TYPE_SEC_FILING
    assert records[0].status == ATTEMPT_METADATA_ONLY


# =========================================================================== #
# 5. Persistence — real SQLite DB: idempotency + company isolation.
#
# "A preflight failure record must not create extracted_document success /
# extracted_fact / evidence / citation" is proved structurally elsewhere
# (company_evidence.sec_artifacts_to_evidence only builds evidence for
# ``STATUS_EXTRACTED`` artifacts with a non-None extraction — a preflight
# artifact is neither). Here we prove the ATTEMPT record itself persists
# idempotently and never crosses company boundaries.
# =========================================================================== #


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def flags_on(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        app_settings, "primary_document_ingestion_enabled", True, raising=False
    )
    monkeypatch.setattr(
        app_settings, "report_citation_persistence_enabled", True, raising=False
    )
    return app_settings


async def _add_company(session, *, ticker: str) -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="NASDAQ",
        name=f"{ticker} Inc.",
        country="US",
        sector="Technology",
        industry="Consumer Electronics",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_run(session) -> AgentRun:
    run = AgentRun(id=uuid.uuid4(), workflow_name="company_analysis", status="completed")
    session.add(run)
    await session.flush()
    return run


def _preflight_record(failure_code: str = FAILURE_MISSING_CIK) -> IngestionAttemptRecord:
    art = live_fetchers._preflight_artifact(
        SecPreflightFailure(
            canonical_url="urn:investingbuddy:sec-filing:0000320193-24-000123",
            accession_number=ACC_10K,
            form_type="10-K",
            filing_date="2024-11-01",
            failure_code=failure_code,
        )
    )
    return artifact_to_attempt(
        art, source_type=SOURCE_TYPE_SEC_FILING, source_tier=SOURCE_TIER_PRIMARY_FILING
    )


async def test_repeated_execution_upserts_a_single_preflight_row(session, flags_on):
    company = await _add_company(session, ticker="AAPL")
    run = await _add_run(session)

    written_1 = await record_ingestion_attempts(
        session, company_id=company.id, agent_run_id=run.id,
        attempts=[_preflight_record()],
    )
    await session.commit()
    written_2 = await record_ingestion_attempts(
        session, company_id=company.id, agent_run_id=run.id,
        attempts=[_preflight_record()],
    )
    await session.commit()

    assert written_1 == 1
    assert written_2 == 1  # an update, not a second insert
    count = (
        await session.execute(select(func.count()).select_from(DocumentIngestionAttempt))
    ).scalar_one()
    assert count == 1

    row = (
        await session.execute(select(DocumentIngestionAttempt))
    ).scalar_one()
    assert row.status == ATTEMPT_METADATA_ONLY
    assert row.failure_code == FAILURE_MISSING_CIK
    assert row.source_type == SOURCE_TYPE_SEC_FILING
    # Never a raw provider string, never a secret, never the exact HTTP status.
    assert row.canonical_url.startswith("urn:investingbuddy:sec-filing:")


async def test_preflight_attempt_never_crosses_company_boundaries(session, flags_on):
    aapl = await _add_company(session, ticker="AAPL")
    other = await _add_company(session, ticker="MSFT")
    run = await _add_run(session)

    await record_ingestion_attempts(
        session, company_id=aapl.id, agent_run_id=run.id,
        attempts=[_preflight_record()],
    )
    await session.commit()

    from app.services.document_ingestion_attempt_service import load_attempt_summary

    aapl_summary = await load_attempt_summary(session, company_id=aapl.id)
    other_summary = await load_attempt_summary(session, company_id=other.id)

    assert aapl_summary.get("total") == 1
    assert aapl_summary.get(ATTEMPT_METADATA_ONLY) == 1
    # A company with zero attempts (gated ON, nothing recorded for it) reports
    # {"total": 0} — {} is reserved for the gated-OFF / no-company-id dark path.
    assert other_summary == {"total": 0}  # AAPL's preflight attempt is invisible to MSFT


async def test_preflight_record_never_creates_a_fact_or_document_row(session, flags_on):
    """The attempt row exists; extracted_documents / extracted_facts do not."""
    company = await _add_company(session, ticker="AAPL")
    run = await _add_run(session)
    await record_ingestion_attempts(
        session, company_id=company.id, agent_run_id=run.id,
        attempts=[_preflight_record()],
    )
    await session.commit()

    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    doc_count = (
        await session.execute(select(func.count()).select_from(ExtractedDocument))
    ).scalar_one()
    fact_count = (
        await session.execute(select(func.count()).select_from(ExtractedFact))
    ).scalar_one()
    assert doc_count == 0
    assert fact_count == 0


async def test_flag_off_writes_no_row_for_a_preflight_failure(session):
    """Master/persistence flags OFF -> the preflight record is dropped, not
    queried — the dark path holds even for the new failure codes."""
    company = await _add_company(session, ticker="AAPL")
    run = await _add_run(session)
    written = await record_ingestion_attempts(
        session, company_id=company.id, agent_run_id=run.id,
        attempts=[_preflight_record()],
    )
    assert written == 0
    count = (
        await session.execute(select(func.count()).select_from(DocumentIngestionAttempt))
    ).scalar_one()
    assert count == 0
