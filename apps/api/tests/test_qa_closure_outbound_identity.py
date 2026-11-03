"""ADR-081 — outbound identity: one User-Agent builder, no personal contact, ever.

The 2026-11 QA campaign found an operator script that sent requests to sec.gov with
a developer's personal email as its User-Agent. These tests pin the closure:

1. a static scan: no module in ``app/`` (or ``apps/api/scripts``) spells a UA header,
   a UA constant, a product-token string or a consumer-mail address — only
   ``app/core/outbound_identity.py`` builds a UA;
2. the default UA carries the product token and the service contact;
3. with the local identity forced to sentinel values (env, ``getpass``, hostname) and
   the setting forced to a consumer-mail address or to a sentinel, the UA carries
   none of them and falls back to the service contact — and the warning logged on
   fallback does not carry the value;
4. every SEC client, captured at the transport (``httpx.MockTransport``), sends
   exactly the central UA;
5. the operator helper ``scripts/sec_get.py`` refuses non-SEC hosts and any UA
   override, and sends the central UA.

No test makes a network request and none prints a real address: every value is an
obvious sentinel, and the real git identity (read here, never in app code) is only
ever an operand of a boolean.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import pathlib
import re
import socket
import subprocess
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.core import outbound_identity as oi
from app.core.config import settings as default_settings
from app.core.outbound_identity import (
    DEFAULT_OUTBOUND_CONTACT,
    RESEARCH_PRODUCT_TOKEN,
    USER_AGENT_PRODUCT_TOKEN,
    contact_refusal_reason,
    crawler_user_agent,
    is_consumer_mail_domain,
    outbound_contact,
    research_user_agent,
)

API_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"
SCRIPTS_ROOT = API_ROOT / "scripts"
CENTRAL_MODULE = APP_ROOT / "core" / "outbound_identity.py"
CONFIG_MODULE = APP_ROOT / "core" / "config.py"

# Sentinels: obviously fake, never a real person.
SENTINEL_LOGIN = "sentinelperson"
SENTINEL_PERSONAL = "sentinel.person@gmail.com"
SENTINEL_AUTHOR = "sentinel.author@corp-sentinel.example"
SENTINEL_HOST = "sentinel-host.local"
SENTINELS = (
    SENTINEL_LOGIN,
    SENTINEL_PERSONAL,
    "sentinel.person",
    SENTINEL_AUTHOR,
    "sentinel.author",
    SENTINEL_HOST,
    "sentinel-host",
    "gmail",
)


@pytest.fixture(autouse=True)
def _fresh_warning_state(monkeypatch):
    monkeypatch.setattr(oi, "_WARNED", set())


@pytest.fixture
def sentinel_identity(monkeypatch):
    """The process looks like it runs as a person with a personal mailbox."""
    for name in ("USER", "LOGNAME", "USERNAME"):
        monkeypatch.setenv(name, SENTINEL_LOGIN)
    monkeypatch.setenv("EMAIL", SENTINEL_PERSONAL)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", SENTINEL_AUTHOR)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", SENTINEL_AUTHOR)
    monkeypatch.setattr(oi.getpass, "getuser", lambda: SENTINEL_LOGIN)
    monkeypatch.setattr(oi.socket, "gethostname", lambda: SENTINEL_HOST)


def _carries_a_sentinel(value: str) -> bool:
    low = value.lower()
    return any(s in low for s in SENTINELS)


# =========================================================================== #
# 1. Static scan: only the central module builds a User-Agent
# =========================================================================== #

_PRODUCT_TOKEN_RE = re.compile(r"InvestingBuddy-Research-(Bot|Platform)/|\(contact:", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def _string_constants(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _is_ua_key(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.strip().lower() in ("user-agent", "user_agent", "useragent")
    )


def scan_source(source: str, *, allow_service_contact: bool = False) -> list[str]:
    """Return the outbound-identity violations in one module's source."""
    tree = ast.parse(source)
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(_is_ua_key(k) for k in node.keys):
            problems.append(f"line {node.lineno}: a User-Agent header literal")
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            if _is_ua_key(node.slice):
                problems.append(f"line {node.lineno}: a User-Agent header assignment")
        if (
            isinstance(node, ast.Tuple)
            and len(node.elts) == 2
            and _is_ua_key(node.elts[0])
            and not _is_ua_key(node.elts[1])  # ("user-agent", "useragent"): robots keys
        ):
            problems.append(f"line {node.lineno}: a User-Agent header pair")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = getattr(target, "id", None) or getattr(target, "attr", None) or ""
                if name.upper().endswith("USER_AGENT") and node.value is not None:
                    if _string_constants(node.value):
                        problems.append(f"line {node.lineno}: a {name} string constant")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if _PRODUCT_TOKEN_RE.search(text):
                problems.append(f"line {node.lineno}: a hand-built UA string")
            for match in _EMAIL_RE.finditer(text):
                domain = match.group(1).lower()
                if is_consumer_mail_domain(domain):
                    problems.append(f"line {node.lineno}: a consumer-mail address literal")
                elif domain.startswith("investingbuddy.") and not allow_service_contact:
                    problems.append(f"line {node.lineno}: a contact address literal")
    return problems


def _scanned_files() -> list[pathlib.Path]:
    files = sorted(APP_ROOT.rglob("*.py")) + sorted(SCRIPTS_ROOT.glob("*.py"))
    return [p for p in files if p != CENTRAL_MODULE]


def test_scanner_detects_every_shape_it_is_meant_to_catch():
    # Built from parts so this test file itself never holds a full UA literal.
    ua = "InvestingBuddy-Research-" + "Platform/1.0"
    bad = (
        f'_USER_AGENT = "{ua}"\n'
        'h = {"User-Agent": x}\n'
        'h["user-agent"] = x\n'
        'pairs = [("User-Agent", x)]\n'
        f'SEC_USER_AGENT = ("{ua} " "(contact: a@b.example)")\n'
        'who = "sentinel.person@' + 'gmail.com"\n'
    )
    problems = scan_source(bad)
    assert len(problems) >= 6, problems
    assert scan_source("h = {USER_AGENT_HEADER: research_user_agent()}\n") == []


def test_no_module_outside_the_central_one_builds_a_user_agent():
    offenders: list[str] = []
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        for problem in scan_source(source, allow_service_contact=path == CONFIG_MODULE):
            offenders.append(f"{path.relative_to(API_ROOT)} {problem}")
    assert offenders == [], "\n".join(offenders)


def test_every_outbound_client_imports_the_central_builder():
    # The modules the brief names; each must reach the central module.
    clients = [
        "app/integrations/providers/sec_edgar_provider.py",
        "app/integrations/providers/sec_recent_filings_provider.py",
        "app/integrations/providers/sec_edgar_fundamentals.py",
        "app/integrations/providers/gleif_provider.py",
        "app/integrations/providers/stooq_provider.py",
        "app/integrations/news_catalyst_provider.py",
        "app/integrations/providers/free_news_provider.py",
        "app/services/agent_tools/peers.py",
        "app/services/sources/safe_web_fetcher.py",
        "app/services/sources/document_fetcher.py",
        "app/services/sources/sec_filing_documents.py",
        "app/services/web_research/fetch.py",
    ]
    for rel in clients:
        source = (API_ROOT / rel).read_text(encoding="utf-8")
        assert "from app.core.outbound_identity import" in source, rel


# =========================================================================== #
# 2. The default UA
# =========================================================================== #


def test_default_contact_is_the_service_mailbox():
    assert default_settings.outbound_contact == DEFAULT_OUTBOUND_CONTACT
    assert contact_refusal_reason(DEFAULT_OUTBOUND_CONTACT, identity_tokens=()) is None


def test_research_ua_from_default_settings(monkeypatch):
    monkeypatch.setattr(default_settings, "outbound_contact", DEFAULT_OUTBOUND_CONTACT)
    ua = research_user_agent()
    assert ua.startswith(RESEARCH_PRODUCT_TOKEN + "/")
    assert f"(contact: {DEFAULT_OUTBOUND_CONTACT})" in ua
    assert not re.search(r"@(gmail|googlemail|outlook|hotmail|live|yahoo|icloud)\.", ua)


def test_crawler_ua_keeps_the_robots_token(monkeypatch):
    """robots.txt matching relies on the UA starting with the product token."""
    from app.services.sources import safe_web_fetcher
    from app.services.traversal.issuer_site import ROBOTS_AGENT
    from app.services.web_research import robots

    monkeypatch.setattr(default_settings, "outbound_contact", DEFAULT_OUTBOUND_CONTACT)
    ua = crawler_user_agent()
    assert USER_AGENT_PRODUCT_TOKEN == "InvestingBuddy-Research-Bot"
    assert safe_web_fetcher.USER_AGENT_PRODUCT_TOKEN == USER_AGENT_PRODUCT_TOKEN
    assert robots.USER_AGENT_PRODUCT_TOKEN == USER_AGENT_PRODUCT_TOKEN
    assert ROBOTS_AGENT == USER_AGENT_PRODUCT_TOKEN
    assert ua.startswith(USER_AGENT_PRODUCT_TOKEN + "/")
    assert f"contact: {DEFAULT_OUTBOUND_CONTACT})" in ua


def test_a_valid_service_contact_setting_is_honoured(monkeypatch, sentinel_identity):
    # On the same organisation domain as the sentinel author, but a service mailbox.
    monkeypatch.setattr(default_settings, "outbound_contact", "desk@corp-research.example")
    assert outbound_contact() == "desk@corp-research.example"
    assert research_user_agent().endswith("(contact: desk@corp-research.example)")
    cfg = SimpleNamespace(outbound_contact="ops@corp-research.example")
    assert crawler_user_agent(cfg).endswith("contact: ops@corp-research.example)")


# =========================================================================== #
# 3. Fail closed on a personal / local identity
# =========================================================================== #


@pytest.mark.parametrize(
    "configured",
    [
        SENTINEL_PERSONAL,  # consumer mail + equals EMAIL
        "someone.else@yahoo.co.uk",  # consumer mail (yahoo.*)
        "x@googlemail.com",
        "x@gmx.de",
        "x@proton.me",
        SENTINEL_AUTHOR,  # equals GIT_AUTHOR_EMAIL
        "SENTINEL.AUTHOR@corp-sentinel.example",  # case does not hide it
        f"{SENTINEL_LOGIN}@corp-research.example",  # local part is the login
        f"bot+{SENTINEL_LOGIN}@corp-research.example",  # derived from the login
        f"ops@{SENTINEL_HOST}",  # derived from the hostname
        "research@investingbuddy.example)\r\nX-Injected: 1",  # header injection
        "not an address",
    ],
)
def test_a_personal_or_local_contact_falls_back_to_the_service_contact(
    monkeypatch, sentinel_identity, configured
):
    monkeypatch.setattr(default_settings, "outbound_contact", configured)
    for ua in (research_user_agent(), crawler_user_agent()):
        assert _carries_a_sentinel(ua) is False
        assert f"contact: {DEFAULT_OUTBOUND_CONTACT})" in ua
        assert "\r" not in ua and "\n" not in ua
    # The same through an explicit cfg (how the guarded fetchers pass it).
    cfg = SimpleNamespace(outbound_contact=configured)
    assert outbound_contact(cfg) == DEFAULT_OUTBOUND_CONTACT


def test_the_local_identity_never_reaches_the_default_ua(monkeypatch, sentinel_identity):
    monkeypatch.setattr(default_settings, "outbound_contact", DEFAULT_OUTBOUND_CONTACT)
    for ua in (research_user_agent(), crawler_user_agent()):
        assert _carries_a_sentinel(ua) is False


def _real_git_email() -> str | None:
    """The developer's configured git address — read ONLY here, never printed."""
    try:
        done = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=API_ROOT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (done.stdout or "").strip()
    return value or None


def test_the_real_git_identity_is_never_sent(monkeypatch):
    real = _real_git_email()
    if real is None:
        pytest.skip("no git identity configured on this machine")
    monkeypatch.setattr(default_settings, "outbound_contact", DEFAULT_OUTBOUND_CONTACT)
    leaked = real.lower() in research_user_agent().lower()
    assert leaked is False, "the default UA carries the local git identity"
    leaked = real.lower() in crawler_user_agent().lower()
    assert leaked is False, "the crawler UA carries the local git identity"

    # An operator who exports the git identity and also configures it is refused.
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", real)
    monkeypatch.setattr(default_settings, "outbound_contact", real)
    leaked = real.lower() in research_user_agent().lower()
    assert leaked is False, "a contact equal to the git identity was sent"


def test_the_fallback_warning_never_carries_the_value(monkeypatch, sentinel_identity, caplog):
    monkeypatch.setattr(default_settings, "outbound_contact", SENTINEL_PERSONAL)
    caplog.set_level(logging.DEBUG)
    research_user_agent()
    research_user_agent()  # one warning per reason, not per request
    refusals = [r for r in caplog.records if "outbound_contact_refused" in r.getMessage()]
    assert len(refusals) == 1
    assert refusals[0].levelno == logging.WARNING
    assert "consumer_mail_domain" in refusals[0].getMessage()
    for record in caplog.records:
        assert _carries_a_sentinel(record.getMessage()) is False


def test_the_contact_is_never_logged_when_accepted(monkeypatch, caplog):
    monkeypatch.setattr(default_settings, "outbound_contact", "desk@corp-research.example")
    caplog.set_level(logging.DEBUG)
    research_user_agent()
    crawler_user_agent()
    assert all("corp-research" not in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "domain,consumer",
    [
        ("gmail.com", True),
        ("GMAIL.COM", True),
        ("mail.yahoo.com", True),
        ("yandex.ru", True),
        ("mail.ru", True),
        ("me.com", True),
        ("investingbuddy.example", False),
        ("notgmail.com", False),
        ("yahoo-research.example", False),
    ],
)
def test_consumer_domain_detection(domain, consumer):
    assert is_consumer_mail_domain(domain) is consumer


# =========================================================================== #
# 4. Every SEC client sends the central UA (captured at the transport)
# =========================================================================== #

PUBLIC_IP = "93.184.216.34"


def _resolver(host: str, port: Any = None, *a: Any, **kw: Any) -> list[Any]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]


def _capture(monkeypatch, body: bytes, content_type: str = "application/json"):
    """Route every httpx.AsyncClient through a MockTransport; return the requests."""
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def _assert_central_research_ua(seen: list[httpx.Request]) -> None:
    assert seen, "no request reached the transport"
    for request in seen:
        sent = request.headers.get("user-agent", "")
        assert sent == research_user_agent()
        assert _carries_a_sentinel(sent) is False
        assert DEFAULT_OUTBOUND_CONTACT in sent


@pytest.fixture
def personal_contact_configured(monkeypatch, sentinel_identity):
    """The worst case: a personal address configured on a personal machine."""
    monkeypatch.setattr(default_settings, "outbound_contact", SENTINEL_PERSONAL)


_SUBMISSIONS = json.dumps(
    {
        "cik": "320193",
        "name": "Sentinel Holdings",
        "tickers": ["ZZZQ"],
        "exchanges": ["Nasdaq"],
        "filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": []}},
    }
).encode()


def test_sec_edgar_provider_sends_the_central_ua(monkeypatch, personal_contact_configured):
    from app.integrations.providers.sec_edgar_provider import SecEdgarProvider

    seen = _capture(monkeypatch, _SUBMISSIONS)
    with contextlib.suppress(Exception):  # only the outbound header matters here
        asyncio.run(SecEdgarProvider().get_company_by_cik("320193"))
    _assert_central_research_ua(seen)
    assert seen[0].url.host == "data.sec.gov"


def test_sec_recent_filings_provider_sends_the_central_ua(
    monkeypatch, personal_contact_configured
):
    from app.integrations.providers.sec_recent_filings_provider import SecRecentFilingsProvider

    seen = _capture(monkeypatch, _SUBMISSIONS)
    asyncio.run(SecRecentFilingsProvider()._fetch_submissions("0000320193"))
    _assert_central_research_ua(seen)


def test_sec_fundamentals_sends_the_central_ua(monkeypatch, personal_contact_configured):
    from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider

    tickers = json.dumps({"0": {"cik_str": 320193, "ticker": "ZZZQ", "title": "Sentinel"}})
    seen = _capture(monkeypatch, tickers.encode())
    with contextlib.suppress(Exception):
        asyncio.run(SecEdgarFundamentalsProvider().resolve_cik("ZZZQ", "NASDAQ"))
    facts = json.dumps({"cik": 320193, "entityName": "Sentinel", "facts": {}}).encode()
    seen_facts = _capture(monkeypatch, facts)
    with contextlib.suppress(Exception):
        asyncio.run(SecEdgarFundamentalsProvider().get_fundamentals("320193"))
    _assert_central_research_ua(seen + seen_facts)
    assert len(seen + seen_facts) >= 2


def test_sec_8k_news_provider_sends_the_central_ua(monkeypatch, personal_contact_configured):
    from app.integrations.news_catalyst_provider import SecEdgar8KProvider

    seen = _capture(monkeypatch, _SUBMISSIONS)
    asyncio.run(SecEdgar8KProvider().get_recent_events("ZZZQ", cik="320193"))
    _assert_central_research_ua(seen)


def _archive_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        source_connector_allowlist_only=True,
        primary_document_pin_dns_enabled=True,
        primary_document_fetch_timeout_seconds=15,
        sec_request_min_interval_ms=0,
    )


def test_sec_filing_index_sends_the_central_ua(monkeypatch, personal_contact_configured):
    from app.services.sources.sec_filing_documents import SecRateLimiter, fetch_filing_index

    body = json.dumps({"directory": {"item": [{"name": "a.htm", "size": "1"}]}}).encode()
    seen = _capture(monkeypatch, body)
    entries = asyncio.run(
        fetch_filing_index(
            "320193",
            "0000320193-26-000001",
            cfg=_archive_cfg(),  # type: ignore[arg-type]
            limiter=SecRateLimiter(min_interval_seconds=0),
            resolver=_resolver,
        )
    )
    assert entries
    _assert_central_research_ua(seen)
    assert seen[0].url.host == "www.sec.gov"


def test_sec_6k_path_sends_the_central_ua(monkeypatch, personal_contact_configured):
    """The ADR-077 results-6-K legs: the ``-index.htm`` page and a document's head.

    ``sec_6k_results.fetch_document_head`` / ``fetch_filing_index_page`` are stubbed
    out by an autouse conftest fixture (no test may reach the SEC through them), so
    this drives the shared network leg they both use, with fetch_document_head's
    exact arguments.
    """
    from app.services.sources import sec_filing_documents as sfd

    async def _no_dns(url: str, allowed: Any, **kw: Any) -> tuple[None, None]:
        return None, None  # allowed, unpinned: no real DNS lookup in a test

    monkeypatch.setattr(sfd, "async_check_fetch_url", _no_dns)
    page = (
        b"<table><tr><td>1</td><td>Results</td>"
        b'<td><a href="/Archives/edgar/data/320193/x/ex99-1.htm">ex99-1.htm</a></td>'
        b"<td>EX-99.1</td><td>1000</td></tr></table>"
    )
    seen = _capture(monkeypatch, page, "text/html")
    asyncio.run(
        sfd.fetch_filing_index_page(
            "320193",
            "0000320193-26-000002",
            cfg=_archive_cfg(),  # type: ignore[arg-type]
            limiter=sfd.SecRateLimiter(min_interval_seconds=0),
        )
    )
    head = asyncio.run(
        sfd._fetch_sec_archive_bytes(
            sfd.build_document_url("320193", "0000320193-26-000002", "ex99-1.htm") or "",
            "0000320193-26-000002",
            accept="text/html",
            cfg=_archive_cfg(),  # type: ignore[arg-type]
            client_factory=None,
            limiter=sfd.SecRateLimiter(min_interval_seconds=0),
            resolver=None,
            max_bytes=sfd.INDEX_PAGE_MAX_BYTES,
            event_prefix="sec_6k_headline",
            allow_truncated=True,
        )
    )
    assert head == page
    assert len(seen) == 2
    _assert_central_research_ua(seen)


def test_sec_filing_body_sends_the_central_crawler_ua(monkeypatch, personal_contact_configured):
    """The body leg goes through the guarded document fetcher (the crawler identity)."""
    from app.services.sources.sec_filing_documents import (
        SecFilingDocument,
        SecRateLimiter,
        fetch_filing_body,
    )

    seen = _capture(monkeypatch, b"<html><body>annual report</body></html>", "text/html")
    doc = SecFilingDocument(
        accession_number="0000320193-26-000001",
        form_type="10-K",
        filing_date="2026-10-30",
        canonical_url="https://www.sec.gov/Archives/edgar/data/320193/x/a.htm",
        document_name="a.htm",
        cik="0000320193",
        title="Annual report",
    )
    asyncio.run(
        fetch_filing_body(doc, resolver=_resolver, limiter=SecRateLimiter(min_interval_seconds=0))
    )
    assert seen, "no request reached the transport"
    for request in seen:
        sent = request.headers.get("user-agent", "")
        assert sent == crawler_user_agent()
        assert sent.startswith(USER_AGENT_PRODUCT_TOKEN + "/")
        assert _carries_a_sentinel(sent) is False


# =========================================================================== #
# 5. The operator helper: scripts/sec_get.py
# =========================================================================== #


def _load_sec_get():
    spec = importlib.util.spec_from_file_location("sec_get_helper", SCRIPTS_ROOT / "sec_get.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/x",
        "https://sec.gov.evil.example/x",
        "https://evil.example/?u=https://www.sec.gov/",
        "https://user:pw@www.sec.gov/x",
        "https://www.sec.gov:8443/x",
        "https://example.com/",
        "ftp://data.sec.gov/x",
    ],
)
def test_sec_get_refuses_anything_but_the_sec_hosts(url):
    helper = _load_sec_get()
    assert helper.url_refusal_reason(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://efts.sec.gov/LATEST/search-index?q=x",
    ],
)
def test_sec_get_accepts_the_sec_hosts(url):
    helper = _load_sec_get()
    assert helper.url_refusal_reason(url) is None


@pytest.mark.parametrize(
    "argv,environ",
    [
        (["--user-agent", "x", "https://data.sec.gov/a.json"], {}),
        (["-A", "x", "https://data.sec.gov/a.json"], {}),
        (["-H", "User-Agent: x", "https://data.sec.gov/a.json"], {}),
        (["https://data.sec.gov/a.json"], {"SEC_USER_AGENT": SENTINEL_PERSONAL}),
        (["https://data.sec.gov/a.json"], {"HTTP_USER_AGENT": SENTINEL_PERSONAL}),
        (["https://data.sec.gov/a.json"], {"user_agent": SENTINEL_PERSONAL}),
    ],
)
def test_sec_get_refuses_any_user_agent_override(tmp_path, argv, environ):
    helper = _load_sec_get()
    err = io.StringIO()
    calls: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    code = helper.main(
        argv,
        environ=environ,
        transport=httpx.MockTransport(handler),
        state_path=tmp_path / "last",
        stderr=err,
    )
    assert code == helper.EXIT_REFUSED
    assert calls == []
    assert _carries_a_sentinel(err.getvalue()) is False


def test_sec_get_sends_the_central_ua_and_writes_the_body(
    tmp_path, monkeypatch, personal_contact_configured
):
    helper = _load_sec_get()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"ok": true}')

    out = tmp_path / "out.json"
    err = io.StringIO()
    code = helper.main(
        ["https://data.sec.gov/submissions/CIK0000320193.json", str(out)],
        environ={},
        transport=httpx.MockTransport(handler),
        state_path=tmp_path / "last",
        stderr=err,
    )
    assert code == helper.EXIT_OK
    assert out.read_bytes() == b'{"ok": true}'
    _assert_central_research_ua(seen)
    # Nothing it prints carries the identity.
    assert DEFAULT_OUTBOUND_CONTACT not in err.getvalue()
    assert _carries_a_sentinel(err.getvalue()) is False


def test_sec_get_does_not_follow_redirects(tmp_path):
    helper = _load_sec_get()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://evil.example/"})

    code = helper.main(
        ["https://www.sec.gov/x"],
        environ={},
        transport=httpx.MockTransport(handler),
        state_path=tmp_path / "last",
        stderr=io.StringIO(),
        stdout=io.BytesIO(),
    )
    assert code == helper.EXIT_FAILED


def test_sec_get_keeps_the_sec_spacing_across_invocations(tmp_path, monkeypatch):
    helper = _load_sec_get()
    monkeypatch.setattr(default_settings, "sec_request_min_interval_ms", 500)
    state = tmp_path / "last"
    state.write_text("100.0")
    slept: list[float] = []
    code = helper.main(
        ["https://www.sec.gov/x"],
        environ={},
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok")),
        sleep=slept.append,
        clock=lambda: 100.2,
        state_path=state,
        stderr=io.StringIO(),
        stdout=io.BytesIO(),
    )
    assert code == helper.EXIT_OK
    assert slept and abs(slept[0] - 0.3) < 1e-6
