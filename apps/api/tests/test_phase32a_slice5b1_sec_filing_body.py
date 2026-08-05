"""
Phase 32A Slice 5B.1 — official SEC filing-body retrieval.

Fully OFFLINE and deterministic: ``httpx.AsyncClient`` is replaced by a hand-built
fake, the throttle's ``sleep``/``clock`` are injected, and ``safe_fetch_document``
is monkeypatched at the module boundary. NO real network, NO DNS, NO LLM, NO DB.

Covers the pure helpers (CIK/accession normalization, URL building including
path-traversal rejection, form support, deterministic document selection,
tolerant index parsing) and the bounded network layer (preference order,
max-documents cap, missing accessions, 404 / malformed JSON degradation, the
declared User-Agent, the real client-side throttle, and the SSRF-safe body fetch
delegation with ``allowed_domains=("sec.gov",)`` + ``resolve_ip=True``).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.services.sources import sec_filing_documents as mod
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.sources.sec_filing_documents import (
    SEC_ALLOWED_DOMAINS,
    SEC_USER_AGENT,
    SecFilingDocument,
    SecRateLimiter,
    build_document_url,
    build_filing_index_url,
    fetch_filing_body,
    fetch_filing_index,
    format_accession,
    is_supported_form,
    normalize_accession,
    normalize_cik,
    parse_filing_index,
    resolve_filing_documents,
    select_primary_document,
)

AAPL_CIK = "0000320193"
ACC_10K = "0000320193-24-000123"
ACC_8K = "0000320193-24-000456"
ACC_10Q = "0000320193-24-000789"


# --------------------------------------------------------------------------- #
# Offline fake httpx client (Phase 29B.2 / Slice 5 idiom).
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        raise_exc: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._raise = raise_exc

    async def __aenter__(self) -> "_FakeStream":
        if self._raise is not None:
            raise self._raise
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, handler, **kw: Any) -> None:
        self._handler = handler
        self.kw = kw
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def stream(self, method: str, url: str):
        self.requests.append((method, url))
        return self._handler(url)


def _patch_httpx(monkeypatch, handler) -> list[_FakeClient]:
    """Replace httpx.AsyncClient with a fake; return the list of created clients."""
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


def _quiet_limiter(min_interval: float = 0.0) -> SecRateLimiter:
    """A throttle that never really sleeps (tests stay instant + deterministic)."""
    calls: list[float] = []

    async def _sleep(delay: float) -> None:
        calls.append(delay)

    limiter = SecRateLimiter(min_interval_seconds=min_interval, sleep=_sleep, clock=lambda: 0.0)
    limiter.sleep_calls = calls  # type: ignore[attr-defined]
    return limiter


# --------------------------------------------------------------------------- #
# Offline DNS + config seams (Slice 5B.1 security review M1).
#
# The index fetch now goes through the SAME hardened chain as the body fetch
# (``async_check_fetch_url`` with ``resolve_ip=True`` + address pinning), so every
# test injects a fake resolver — no test ever performs a real DNS lookup.
# --------------------------------------------------------------------------- #

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "10.0.0.5"


def _info(ip: str) -> tuple[Any, ...]:
    import socket

    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr: tuple[Any, ...] = (ip, 0, 0, 0) if ":" in ip else (ip, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def _resolver(*ips: str):
    """A fake ``socket.getaddrinfo`` resolving every host to ``ips``."""

    def resolve(host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
        return [_info(ip) for ip in ips]

    return resolve


def _cfg(**overrides: Any) -> SimpleNamespace:
    """A minimal settings stand-in carrying everything the fetch chain reads."""
    base: dict[str, Any] = {
        "source_connector_allowlist_only": True,
        "primary_document_pin_dns_enabled": True,
        "sec_filing_index_max_bytes": 2_000_000,
        "primary_document_fetch_timeout_seconds": 15,
        "sec_request_min_interval_ms": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _index_kwargs(**overrides: Any) -> dict[str, Any]:
    """Standard offline kwargs for ``fetch_filing_index``/``resolve_*``."""
    kwargs: dict[str, Any] = {
        "cfg": _cfg(),
        "limiter": _quiet_limiter(),
        "resolver": _resolver(PUBLIC_IP),
    }
    kwargs.update(overrides)
    return kwargs


# =========================================================================== #
# 1. Pure helpers — normalization
# =========================================================================== #


def test_normalize_cik_pads_and_rejects_junk():
    assert normalize_cik("320193") == AAPL_CIK
    assert normalize_cik(320193) == AAPL_CIK
    assert normalize_cik(AAPL_CIK) == AAPL_CIK
    assert normalize_cik("  0000320193 ") == AAPL_CIK
    assert normalize_cik("CIK0000320193") == AAPL_CIK
    # Unusable inputs degrade to None — never a guessed CIK.
    for bad in (None, "", "   ", "abc", "32-0193", "0", "0000000000", "-5", "12.5"):
        assert normalize_cik(bad) is None, bad
    assert normalize_cik("1" * 11) is None  # over-long


def test_normalize_and_format_accession_round_trip_and_rejection():
    assert normalize_accession(ACC_10K) == "000032019324000123"
    assert normalize_accession("000032019324000123") == "000032019324000123"
    assert format_accession("000032019324000123") == ACC_10K
    assert format_accession(ACC_10K) == ACC_10K
    # Wrong length / non-numeric is rejected.
    for bad in (None, "", "0000320193-24-00012", "0000320193240001234", "abcdefghijklmnopqr"):
        assert normalize_accession(bad) is None, bad


# =========================================================================== #
# 2. Pure helpers — URL building (incl. path traversal)
# =========================================================================== #


def test_build_filing_index_url_uses_unpadded_cik_in_archives_path():
    url = build_filing_index_url(AAPL_CIK, ACC_10K)
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json"
    )
    assert build_filing_index_url(None, ACC_10K) is None
    assert build_filing_index_url(AAPL_CIK, "nope") is None


def test_build_document_url_correctness():
    url = build_document_url(AAPL_CIK, ACC_10K, "aapl-20240928.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
        "aapl-20240928.htm"
    )
    # Same document from the unpadded CIK.
    assert build_document_url(320193, ACC_10K, "aapl-20240928.htm") == url


@pytest.mark.parametrize(
    "filename",
    [
        "../../../etc/passwd",
        "..",
        "sub/dir/doc.htm",
        "https://evil.example.com/doc.htm",
        "//evil.example.com/doc.htm",
        "doc%2f..%2fsecret.htm",
        "back\\slash.htm",
        "doc.htm?x=1",
        "",
        None,
        # Security L4 — control characters (request smuggling / log injection).
        "doc\r\nHost: evil.example.com.htm",
        "doc\n.htm",
        "doc\x00.htm",
        "doc\x7f.htm",
        "doc\x01.htm",
    ],
)
def test_build_document_url_rejects_unsafe_filenames(filename):
    assert build_document_url(AAPL_CIK, ACC_10K, filename) is None


# =========================================================================== #
# 3. Pure helpers — supported forms
# =========================================================================== #


@pytest.mark.parametrize("form", ["10-K", "10-k", " 10-K ", "10-K/A", "20-F", "10-Q", "6-K", "8-K"])
def test_is_supported_form_accepts_primary_disclosure_forms(form):
    assert is_supported_form(form) is True


@pytest.mark.parametrize("form", ["S-1", "DEF 14A", "4", "", None, "13D/G"])
def test_is_supported_form_rejects_other_forms(form):
    assert is_supported_form(form) is False


# =========================================================================== #
# 4. Pure helpers — deterministic document selection
# =========================================================================== #


def test_select_primary_document_hint_wins():
    entries = [
        _entry("aapl-20240928.htm", type_="10-K", size="900000"),
        _entry("hinted.htm", size="10"),
    ]
    chosen = select_primary_document(
        entries, form_type="10-K", primary_document_hint="HINTED.HTM"
    )
    assert chosen is not None and chosen["name"] == "hinted.htm"


def test_select_primary_document_prefers_form_typed_html_over_larger_untyped():
    entries = [
        _entry("aapl-20240928.htm", type_="10-K", size="1000"),
        _entry("someother.htm", type_="GRAPHIC", size="9999999"),
    ]
    chosen = select_primary_document(entries, form_type="10-K")
    assert chosen is not None and chosen["name"] == "aapl-20240928.htm"


def test_select_primary_document_largest_non_exhibit_html():
    entries = [
        _entry("small.htm", size="100"),
        _entry("big.htm", size="500000"),
        _entry("ex-101.htm", size="9999999"),
    ]
    chosen = select_primary_document(entries, form_type=None)
    assert chosen is not None and chosen["name"] == "big.htm"


def test_select_primary_document_never_selects_full_submission_txt():
    entries = [
        _entry("0000320193-24-000123.txt", type_="", size="99999999"),
        _entry("aapl-20240928.htm", size="1000"),
    ]
    chosen = select_primary_document(entries, form_type="10-K")
    assert chosen is not None and chosen["name"] == "aapl-20240928.htm"
    # A .txt-only index yields nothing rather than the unbounded dump.
    assert select_primary_document([entries[0]], form_type="10-K") is None


def test_select_primary_document_excludes_index_viewer_and_summary_noise():
    entries = [
        _entry("0000320193-24-000123-index.htm", size="8000000"),
        _entry("R2.htm", size="7000000"),
        _entry("FilingSummary.xml", size="6000000"),
        _entry("aapl-20240928.htm", size="1000"),
    ]
    chosen = select_primary_document(entries, form_type=None)
    assert chosen is not None and chosen["name"] == "aapl-20240928.htm"


def test_select_primary_document_exhibits_only_when_allowed():
    entries = [_entry("ex-991.htm", type_="EX-99.1", size="5000")]
    assert select_primary_document(entries, form_type="8-K") is None
    chosen = select_primary_document(entries, form_type="8-K", allow_exhibits=True)
    assert chosen is not None and chosen["name"] == "ex-991.htm"


def test_select_primary_document_tie_break_is_name_ascending():
    entries = [
        _entry("zeta.htm", size="1000"),
        _entry("alpha.htm", size="1000"),
    ]
    first = select_primary_document(entries, form_type=None)
    second = select_primary_document(list(reversed(entries)), form_type=None)
    assert first is not None and first["name"] == "alpha.htm"
    assert second is not None and second["name"] == "alpha.htm"  # stable


def test_select_primary_document_handles_empty_and_junk_entries():
    assert select_primary_document([], form_type="10-K") is None
    assert select_primary_document([{"name": ""}, {"size": "5"}], form_type="10-K") is None


# =========================================================================== #
# 5. Pure helpers — tolerant index parsing
# =========================================================================== #


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        "not-a-dict",
        {"directory": []},
        {"directory": {"items": [{"name": "a.htm"}]}},  # renamed key
        {"directory": {"item": "a.htm"}},
    ],
)
def test_parse_filing_index_tolerates_shape_mismatch(payload):
    assert parse_filing_index(payload) == []


def test_parse_filing_index_keeps_only_dict_entries():
    payload = {"directory": {"item": [{"name": "a.htm"}, "junk", 7, None]}}
    assert parse_filing_index(payload) == [{"name": "a.htm"}]


# =========================================================================== #
# 6. Network layer — index fetch
# =========================================================================== #


def test_fetch_filing_index_sends_declared_user_agent_and_no_redirects(monkeypatch):
    created = _patch_httpx(monkeypatch, lambda url: _ok([_entry("aapl.htm", size="10")]))
    entries = asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs()))
    assert entries == [{"name": "aapl.htm", "type": "", "size": "10"}]
    assert created, "the fake client was never constructed"
    headers = created[0].kw["headers"]
    assert headers["User-Agent"] == SEC_USER_AGENT
    assert "investingbuddy" in headers["User-Agent"].lower()  # declares identity
    assert created[0].kw["follow_redirects"] is False
    assert created[0].kw["cookies"] is None  # never sends credentials
    method, url = created[0].requests[0]
    assert method == "GET"
    assert url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")


def test_fetch_filing_index_pins_the_connection_to_the_validated_address(monkeypatch):
    """Security M1: the index fetch uses the hardened chain, not a raw client."""
    created = _patch_httpx(monkeypatch, lambda url: _ok([_entry("aapl.htm", size="10")]))
    entries = asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs()))
    assert entries  # the fetch went through
    transport = created[0].kw.get("transport")
    assert transport is not None, "index fetch was not pinned"
    assert transport.pinned_ip("www.sec.gov") == PUBLIC_IP


def test_fetch_filing_index_blocked_when_the_host_resolves_private(monkeypatch):
    """Security M1: a non-public resolved address prevents the request entirely."""
    created = _patch_httpx(monkeypatch, lambda url: _ok([_entry("aapl.htm", size="10")]))
    entries = asyncio.run(
        fetch_filing_index(
            AAPL_CIK, ACC_10K, **_index_kwargs(resolver=_resolver(PRIVATE_IP))
        )
    )
    assert entries == []  # honest empty — nothing fabricated
    assert created == [], "a blocked resolution still opened a client"


def test_fetch_filing_index_blocked_when_resolution_fails(monkeypatch):
    def _boom(host, port=None, *a, **kw):
        raise OSError("no such host")

    created = _patch_httpx(monkeypatch, lambda url: _ok([_entry("a.htm")]))
    assert (
        asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs(resolver=_boom)))
        == []
    )
    assert created == []


def test_fetch_filing_index_pinning_kill_switch_still_fetches(monkeypatch):
    """With pinning off the fetch still happens — unpinned, and never claimed."""
    created = _patch_httpx(monkeypatch, lambda url: _ok([_entry("aapl.htm", size="10")]))
    entries = asyncio.run(
        fetch_filing_index(
            AAPL_CIK,
            ACC_10K,
            **_index_kwargs(cfg=_cfg(primary_document_pin_dns_enabled=False)),
        )
    )
    assert entries  # degraded, not lost
    assert created[0].kw.get("transport") is None


@pytest.mark.parametrize("status", [301, 302, 403, 404, 500])
def test_fetch_filing_index_non_2xx_returns_empty(monkeypatch, status):
    _patch_httpx(monkeypatch, lambda url: _FakeStream(status_code=status, body=b"{}"))
    assert asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs())) == []


def test_fetch_filing_index_logs_only_the_status_class(monkeypatch, caplog):
    """Security L5: the exact provider status code is never logged."""
    _patch_httpx(monkeypatch, lambda url: _FakeStream(status_code=403, body=b"{}"))
    with caplog.at_level("INFO", logger="app.services.sources.sec_filing_documents"):
        asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs()))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "4xx" in text
    assert "403" not in text


def test_fetch_filing_index_malformed_json_returns_empty(monkeypatch):
    _patch_httpx(monkeypatch, lambda url: _FakeStream(status_code=200, body=b"{not json"))
    assert asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs())) == []


def test_fetch_filing_index_transport_error_returns_empty(monkeypatch):
    _patch_httpx(
        monkeypatch, lambda url: _FakeStream(raise_exc=httpx.ConnectTimeout("boom"))
    )
    assert asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs())) == []


def test_fetch_filing_index_bad_identifiers_make_no_request(monkeypatch):
    created = _patch_httpx(monkeypatch, lambda url: _ok([]))
    assert asyncio.run(fetch_filing_index(None, ACC_10K, **_index_kwargs())) == []
    assert asyncio.run(fetch_filing_index(AAPL_CIK, "bad", **_index_kwargs())) == []
    assert created == []  # no network attempt at all


def test_fetch_filing_index_over_byte_cap_is_discarded(monkeypatch):
    big = [_entry(f"doc{i}.htm", size="10") for i in range(50)]
    created = _patch_httpx(monkeypatch, lambda url: _ok(big))
    # The cap is a module CONSTANT, not a Settings field — an env-var-shaped
    # `getattr` for a setting that does not exist would have silently ignored
    # anything an operator set. Patch the constant, which is the real knob.
    monkeypatch.setattr(mod, "DEFAULT_INDEX_MAX_BYTES", 32)
    assert (
        asyncio.run(fetch_filing_index(AAPL_CIK, ACC_10K, **_index_kwargs(cfg=_cfg())))
        == []
    )
    # The request WAS made (this is the byte cap, not a guard block).
    assert created and created[0].requests


# =========================================================================== #
# 7. Network layer — throttle
# =========================================================================== #


def test_rate_limiter_awaits_between_consecutive_requests():
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)

    limiter = SecRateLimiter(min_interval_seconds=0.5, sleep=_sleep, clock=lambda: 0.0)

    async def _run() -> None:
        await limiter.acquire()  # first call: no wait
        await limiter.acquire()  # second call: must wait the full interval
        await limiter.acquire()

    asyncio.run(_run())
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.5)


def test_rate_limiter_is_enforced_on_each_index_fetch(monkeypatch):
    _patch_httpx(monkeypatch, lambda url: _ok([_entry("a.htm", size="1")]))
    limiter = _quiet_limiter(min_interval=0.25)
    kwargs = _index_kwargs(limiter=limiter)

    async def _run() -> None:
        await fetch_filing_index(AAPL_CIK, ACC_10K, **kwargs)
        await fetch_filing_index(AAPL_CIK, ACC_8K, **kwargs)

    asyncio.run(_run())
    assert limiter.sleep_calls == [pytest.approx(0.25)]  # type: ignore[attr-defined]


def test_rate_limiter_zero_interval_never_sleeps():
    limiter = _quiet_limiter(min_interval=0.0)

    async def _run() -> None:
        await limiter.acquire()
        await limiter.acquire()

    asyncio.run(_run())
    assert limiter.sleep_calls == []  # type: ignore[attr-defined]


# =========================================================================== #
# 8. Network layer — resolving filing bodies
# =========================================================================== #


def _routed_handler(routes: dict[str, list[dict[str, Any]]]):
    """Serve a different index per accession, 404 for anything unexpected."""

    def _handler(url: str) -> _FakeStream:
        for accession_nodash, items in routes.items():
            if f"/{accession_nodash}/" in url:
                return _ok(items)
        return _FakeStream(status_code=404, body=b"{}")

    return _handler


def _filing(form: str, accession: str | None, filed: str) -> dict[str, Any]:
    return {
        "form_type": form,
        "title": f"SEC {form} filing — AAPL — {filed}",
        "url": None,
        "filed_date": filed,
        "summary": None,
        "accession_number": accession,
    }


def test_resolve_filing_documents_prefers_10k_over_8k(monkeypatch):
    routes = {
        "000032019324000123": [_entry("aapl-10k.htm", type_="10-K", size="900000")],
        "000032019324000456": [_entry("aapl-8k.htm", type_="8-K", size="5000")],
    }
    _patch_httpx(monkeypatch, _routed_handler(routes))
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            # 8-K is newer, but the 10-K outranks it.
            [_filing("8-K", ACC_8K, "2024-11-01"), _filing("10-K", ACC_10K, "2023-11-02")],
            max_documents=1,
            **_index_kwargs(),
        )
    )
    assert len(docs) == 1
    doc = docs[0]
    assert doc.form_type == "10-K"
    assert doc.document_name == "aapl-10k.htm"
    assert doc.accession_number == ACC_10K  # canonical dashed form retained
    assert doc.filing_date == "2023-11-02"
    assert doc.cik == AAPL_CIK
    assert doc.canonical_url.endswith("/000032019324000123/aapl-10k.htm")
    assert doc.canonical_url.startswith("https://www.sec.gov/Archives/edgar/data/")
    assert doc.is_exhibit is False


def test_resolve_filing_documents_newest_first_within_a_form(monkeypatch):
    older = "0000320193-22-000111"
    routes = {
        "000032019324000123": [_entry("new-10k.htm", type_="10-K", size="900000")],
        "000032019322000111": [_entry("old-10k.htm", type_="10-K", size="900000")],
    }
    _patch_httpx(monkeypatch, _routed_handler(routes))
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", older, "2022-10-28"), _filing("10-K", ACC_10K, "2024-11-01")],
            max_documents=1,
            **_index_kwargs(),
        )
    )
    assert [d.document_name for d in docs] == ["new-10k.htm"]


def test_resolve_filing_documents_respects_max_documents(monkeypatch):
    routes = {
        "000032019324000123": [_entry("a.htm", size="10")],
        "000032019324000789": [_entry("b.htm", size="10")],
        "000032019324000456": [_entry("c.htm", size="10")],
    }
    _patch_httpx(monkeypatch, _routed_handler(routes))
    filings = [
        _filing("10-K", ACC_10K, "2024-11-01"),
        _filing("10-Q", ACC_10Q, "2024-08-01"),
        _filing("8-K", ACC_8K, "2024-05-01"),
    ]
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK, filings, max_documents=2, **_index_kwargs()
        )
    )
    assert [d.form_type for d in docs] == ["10-K", "10-Q"]
    assert asyncio.run(
        resolve_filing_documents(AAPL_CIK, filings, max_documents=0, **_index_kwargs())
    ) == []


def test_resolve_filing_documents_skips_filings_without_accession(monkeypatch):
    created = _patch_httpx(monkeypatch, _routed_handler({}))
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", None, "2024-11-01"), _filing("8-K", "not-an-accession", "2024-05-01")],
            max_documents=3,
            **_index_kwargs(),
        )
    )
    assert docs == []
    assert created == []  # nothing was even requested


def test_resolve_filing_documents_degrades_when_index_is_404(monkeypatch):
    _patch_httpx(monkeypatch, lambda url: _FakeStream(status_code=404, body=b"{}"))
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK, [_filing("10-K", ACC_10K, "2024-11-01")], max_documents=2,
            **_index_kwargs(),
        )
    )
    assert docs == []  # honest empty, no fabricated document


def test_resolve_filing_documents_ignores_unsupported_forms_and_bad_cik(monkeypatch):
    _patch_httpx(monkeypatch, _routed_handler({"000032019324000123": [_entry("a.htm")]}))
    assert asyncio.run(
        resolve_filing_documents(
            AAPL_CIK, [_filing("S-1", ACC_10K, "2024-11-01")], max_documents=2,
            **_index_kwargs(),
        )
    ) == []
    assert asyncio.run(
        resolve_filing_documents(
            "not-a-cik", [_filing("10-K", ACC_10K, "2024-11-01")], max_documents=2,
            **_index_kwargs(),
        )
    ) == []


def test_resolve_filing_documents_survives_a_raising_index_fetcher():
    async def _boom(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        raise RuntimeError("index exploded")

    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            [_filing("10-K", ACC_10K, "2024-11-01")],
            max_documents=2,
            index_fetcher=_boom,
            **_index_kwargs(),
        )
    )
    assert docs == []


# =========================================================================== #
# 8b. Resolution is BUDGETED (PR-review blocker 1)
#
# Resolution runs BEFORE the fetch budget loop, and each filing costs one bounded
# index round-trip, so an unbudgeted resolution over a long filings list could
# spend the whole request before a single body was fetched.
# =========================================================================== #


def _counting_index_fetcher(entries: list[dict[str, Any]] | None = None):
    """An index fetcher that records every accession it was asked to fetch."""
    seen: list[str] = []

    default = [_entry("body.htm", type_="10-K", size="900000")]

    async def _fetch(cik, accession, **kw: Any) -> list[dict[str, Any]]:
        seen.append(str(accession))
        return list(default if entries is None else entries)

    return _fetch, seen


def _many_filings(count: int) -> list[dict[str, Any]]:
    """``count`` distinct, well-formed 10-K filings (each needs an index fetch)."""
    return [
        _filing("10-K", f"00003201932400{i:04d}", f"2024-{(i % 12) + 1:02d}-01")
        for i in range(count)
    ]


def test_resolve_stops_before_any_index_fetch_when_the_deadline_has_passed():
    fetch, seen = _counting_index_fetcher()
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            _many_filings(5),
            max_documents=3,
            index_fetcher=fetch,
            deadline=100.0,
            clock=lambda: 101.0,  # already past the deadline
            **_index_kwargs(),
        )
    )
    assert docs == []
    assert seen == [], "an expired deadline still performed an index fetch"


def test_resolve_stops_midway_when_the_deadline_expires_and_returns_partial():
    fetch, seen = _counting_index_fetcher()
    ticks = iter([0.0, 1.0, 99.0, 99.0, 99.0, 99.0])

    def _clock() -> float:
        try:
            return next(ticks)
        except StopIteration:
            return 99.0

    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            _many_filings(5),
            max_documents=5,
            index_fetcher=fetch,
            deadline=50.0,
            clock=_clock,
            **_index_kwargs(),
        )
    )
    # Two loop passes happened before the clock crossed the deadline.
    assert len(seen) == 2
    assert len(docs) == 2  # partial, honest — never fabricated to fill the cap
    assert all(d.form_type == "10-K" for d in docs)


def test_resolve_caps_total_index_attempts_at_three_per_document():
    # Every index comes back with no usable body, so nothing ever resolves and
    # only the attempt cap can stop the loop.
    fetch, seen = _counting_index_fetcher(entries=[])
    docs = asyncio.run(
        resolve_filing_documents(
            AAPL_CIK,
            _many_filings(20),
            max_documents=2,
            index_fetcher=fetch,
            **_index_kwargs(),  # no deadline at all
        )
    )
    assert docs == []
    assert len(seen) == 2 * 3, f"attempt cap not enforced (made {len(seen)} fetches)"


# =========================================================================== #
# 9. Body fetch — SSRF-safe delegation
# =========================================================================== #


def _doc(url: str = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a.htm"):
    return SecFilingDocument(
        accession_number=ACC_10K,
        form_type="10-K",
        filing_date="2024-11-01",
        canonical_url=url,
        document_name="a.htm",
        cik=AAPL_CIK,
        title="SEC 10-K filing — AAPL",
    )


def _capture_safe_fetch(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake(url: str, **kwargs: Any) -> DocumentFetchResult:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return DocumentFetchResult(
            requested_url=url, status_code=200, content=b"<html>10-K</html>",
            document_type="html",
        )

    monkeypatch.setattr(mod, "safe_fetch_document", _fake)
    return captured


def test_fetch_filing_body_delegates_with_sec_allowlist_and_ip_pinning(monkeypatch):
    captured = _capture_safe_fetch(monkeypatch)
    result = asyncio.run(fetch_filing_body(_doc(), limiter=_quiet_limiter()))
    assert result.ok is True
    assert captured["url"].startswith("https://www.sec.gov/Archives/")
    assert captured["kwargs"]["allowed_domains"] == ("sec.gov",) == SEC_ALLOWED_DOMAINS
    assert captured["kwargs"]["resolve_ip"] is True
    # resolver omitted when None so the callee default applies.
    assert "resolver" not in captured["kwargs"]


def test_fetch_filing_body_forwards_an_injected_resolver(monkeypatch):
    captured = _capture_safe_fetch(monkeypatch)

    def _resolver(host, *a, **k):  # pragma: no cover - never invoked by the fake
        return []

    asyncio.run(fetch_filing_body(_doc(), resolver=_resolver, limiter=_quiet_limiter()))
    assert captured["kwargs"]["resolver"] is _resolver


def test_fetch_filing_body_blocks_non_sec_host_without_fetching(monkeypatch):
    called: list[str] = []

    async def _fake(url: str, **kwargs: Any) -> DocumentFetchResult:
        called.append(url)
        return DocumentFetchResult(requested_url=url)

    monkeypatch.setattr(mod, "safe_fetch_document", _fake)
    result = asyncio.run(
        fetch_filing_body(
            _doc("https://sec.gov.evil.example.com/Archives/a.htm"), limiter=_quiet_limiter()
        )
    )
    assert called == []  # no request was made
    assert result.blocked is True and result.ok is False
    assert result.content is None  # nothing fabricated
    assert result.source_gaps and result.source_gaps[0].connector_key == "sec_edgar"


def test_fetch_filing_body_degrades_when_the_fetcher_raises(monkeypatch):
    async def _boom(url: str, **kwargs: Any) -> DocumentFetchResult:
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(mod, "safe_fetch_document", _boom)
    result = asyncio.run(fetch_filing_body(_doc(), limiter=_quiet_limiter()))
    assert result.ok is False and result.blocked is True
    assert result.error is not None and "RuntimeError" in result.error


def test_fetch_filing_body_is_throttled_before_the_request(monkeypatch):
    _capture_safe_fetch(monkeypatch)
    limiter = _quiet_limiter(min_interval=0.2)

    async def _run() -> None:
        await fetch_filing_body(_doc(), limiter=limiter)
        await fetch_filing_body(_doc(), limiter=limiter)

    asyncio.run(_run())
    assert limiter.sleep_calls == [pytest.approx(0.2)]  # type: ignore[attr-defined]
