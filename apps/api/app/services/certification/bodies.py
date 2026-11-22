"""The certification-body registry — V3.21 slice 21.5 (spec §6.1, ADR-112).

Two lists, and nothing else, decide whether a named body is *recognised*:

1. **STL members**, parsed from the Short-Circuit Testing Liaison's public members page
   (``https://stl-liaison.org/web/03_Members.php``). STL is the international liaison of
   short-circuit testing organisations; membership requires a direct short-circuit test
   facility of at least 500 MVA (3-phase) and independence from manufacturers and users.
   Attribution: "Short-Circuit Testing Liaison (STL), stl-liaison.org".
2. A **curated list** of other recognised laboratories and certifiers (KEMA Labs, IPH
   Berlin, KERI, TÜBİTAK MAM, CPRI, DNV, TÜV, UL Solutions, Intertek, CSA Group, Bureau
   Veritas, SGS), each with its host(s), country and the scope it is known for.

FAILS CLOSED
============
The members page is a small hand-written HTML table. :func:`parse_stl_members` checks
three things before it returns anything: the "Members of STL are" marker, at least
``MIN_STL_MEMBERS`` "Name, Country" cells, and that every cell has the member code of a
form button beside it. Any miss raises :class:`StlLayoutChanged`, and the refresh keeps
the last good list (or the bundled snapshot). A changed page never empties the register.

REFRESH
=======
Monthly. Until the periodic-task registry (slice 21.2, ``source_poll_state``) is on
``main``, the list lives in a :class:`MemberListStore` — in-process by default — and the
report step refreshes it lazily at most once per ``REFRESH_INTERVAL`` (a failed attempt
is retried after ``RETRY_AFTER_FAILURE``). One GET to one allowlisted host; never a
certificate database.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

STL_MEMBERS_URL = "https://stl-liaison.org/web/03_Members.php"
STL_HOSTS: tuple[str, ...] = ("stl-liaison.org", "www.stl-liaison.org")
STL_ATTRIBUTION = "Short-Circuit Testing Liaison (STL), stl-liaison.org"
MIN_STL_MEMBERS = 5
REFRESH_INTERVAL = timedelta(days=30)
RETRY_AFTER_FAILURE = timedelta(days=1)
#: TODO(V3.21 slice 21.2): register ``refresh_stl_members`` as a monthly periodic task
#: under this key, with a ``source_poll_state``-backed :class:`MemberListStore`, and drop
#: the lazy refresh from the report step.
PERIODIC_TASK_KEY = "certification_stl_members"

ORIGIN_LIVE = "live"
ORIGIN_BUNDLED = "bundled_snapshot"

REFRESH_REFRESHED = "refreshed"
REFRESH_LAYOUT_CHANGED = "layout_changed"
REFRESH_FETCH_FAILED = "fetch_failed"
REFRESH_NOT_DUE = "not_due"

KIND_TEST_LABORATORY = "test_laboratory"
KIND_CERTIFIER = "certifier"
KIND_LIAISON = "liaison"


class StlLayoutChanged(ValueError):
    """The members page no longer has the shape the parser was pinned on."""


@dataclass(frozen=True)
class StlMember:
    """One STL member as the page lists it: ``code`` (form value), ``name``, ``country``."""

    code: str
    name: str
    country: str


@dataclass(frozen=True)
class StlMemberList:
    members: tuple[StlMember, ...]
    origin: str
    retrieved_at: str
    directory_issue: str | None = None
    directory_date: str | None = None
    source_url: str = STL_MEMBERS_URL
    attribution: str = STL_ATTRIBUTION

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(m.code.upper() for m in self.members)

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.attribution,
            "source_url": self.source_url,
            "origin": self.origin,
            "retrieved_at": self.retrieved_at,
            "directory_issue": self.directory_issue,
            "directory_date": self.directory_date,
            "members_count": len(self.members),
        }


#: The list as read from the live page on 2026-11-22 (STL Membership Directory Issue 59
#: dated 2026-06-01). Used until a live refresh succeeds; pinned against the fixture.
BUNDLED_STL_MEMBERS = StlMemberList(
    members=(
        StlMember("CESI", "CESI", "Italy"),
        StlMember("CPRI", "CPRI", "India"),
        StlMember("ESEF", "ESEF ASEFA", "France"),
        StlMember("JSTC", "JSTC", "Japan"),
        StlMember("KEMA", "KEMA", "The Netherlands"),
        StlMember("KERI", "KERI", "South Korea"),
        StlMember("PEHLA", "PEHLA", "Germany"),
        StlMember("SATS", "SATS", "Norway"),
        StlMember("STLNA", "STLNA", "USA"),
        StlMember("VEIKI", "VEIKI", "Hungary"),
        StlMember("ZKU", "ZKU", "Czech Republic"),
    ),
    origin=ORIGIN_BUNDLED,
    retrieved_at="2026-11-22T00:00:00+00:00",
    directory_issue="59",
    directory_date="2026-06-01",
)


# ── Parser ─────────────────────────────────────────────────────────────────── #

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKER_RE = re.compile(r"Members\s+of\s+STL\s+are", re.IGNORECASE)
_END_RE = re.compile(r"For\s+more\s+information\s+about\s+the\s+member", re.IGNORECASE)
_CELL_RE = re.compile(r"<font\b[^>]*\bsize\s*=\s*\"?3\"?[^>]*>([^<]*)</font>", re.IGNORECASE)
_CODE_RE = re.compile(
    r"<input\b[^>]*\bname\s*=\s*\"member\"[^>]*\bvalue\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
_ISSUE_RE = re.compile(
    r"Membership\s+Directory\s+Issue\s+(\d+)\s+dated\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_ENTITY = {"&nbsp;": " ", "&amp;": "&", "&#39;": "'", "&quot;": '"'}


def decode_page(content: bytes) -> str:
    """UTF-8, falling back to windows-1252 (the site's older pages)."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def _clean(text: str) -> str:
    for k, v in _ENTITY.items():
        text = text.replace(k, v)
    return " ".join(text.split())


def parse_stl_members(html: str, *, retrieved_at: str) -> StlMemberList:
    """The members on the page. Raises :class:`StlLayoutChanged` on any shape miss."""
    body = _COMMENT_RE.sub(" ", html or "")
    marker = _MARKER_RE.search(body)
    if marker is None:
        raise StlLayoutChanged("members marker not found")
    end = _END_RE.search(body, marker.end())
    region = body[marker.end(): end.start() if end else len(body)]
    cells = [_clean(c) for c in _CELL_RE.findall(region)]
    cells = [c for c in cells if c]
    codes = [_clean(c).upper() for c in _CODE_RE.findall(region)]
    members: list[StlMember] = []
    for cell in cells:
        name, sep, country = cell.partition(",")
        name, country = name.strip(), country.strip()
        if not sep or not name or not country:
            raise StlLayoutChanged("member cell is not 'Name, Country'")
        token = name.split()[0].upper()
        code = next((c for c in codes if c == token or name.upper() == c), None)
        if code is None:
            raise StlLayoutChanged("member cell has no member code beside it")
        members.append(StlMember(code=code, name=name, country=country))
    if len(members) < MIN_STL_MEMBERS:
        raise StlLayoutChanged("fewer members than the minimum")
    if len({m.code for m in members}) != len(members) or len(set(codes)) != len(members):
        raise StlLayoutChanged("member cells and member codes disagree")
    issue = _ISSUE_RE.search(body)
    return StlMemberList(
        members=tuple(members),
        origin=ORIGIN_LIVE,
        retrieved_at=retrieved_at,
        directory_issue=issue.group(1) if issue else None,
        directory_date=issue.group(2) if issue else None,
    )


# ── Store and refresh ─────────────────────────────────────────────────────── #


class MemberListStore(Protocol):
    """Where the last good list lives. Slice 21.2 adds a ``source_poll_state`` one."""

    def load(self) -> StlMemberList | None: ...

    def save(self, members: StlMemberList) -> None: ...

    def last_attempt(self) -> tuple[datetime | None, str | None]: ...

    def record_attempt(self, at: datetime, state: str) -> None: ...


@dataclass
class InMemoryMemberListStore:
    """The default store: process memory. Lost on restart — the snapshot covers that."""

    members: StlMemberList | None = None
    attempted_at: datetime | None = None
    attempt_state: str | None = None

    def load(self) -> StlMemberList | None:
        return self.members

    def save(self, members: StlMemberList) -> None:
        self.members = members

    def last_attempt(self) -> tuple[datetime | None, str | None]:
        return self.attempted_at, self.attempt_state

    def record_attempt(self, at: datetime, state: str) -> None:
        self.attempted_at, self.attempt_state = at, state


DEFAULT_STORE = InMemoryMemberListStore()

#: ``() -> bytes | None``: the page bytes, or ``None`` on any failure. Never raises.
PageFetcher = Callable[[], Awaitable[bytes | None]]


async def fetch_stl_members_page() -> bytes | None:
    """One bounded GET of the members page on its allowlisted host. Never raises."""
    try:
        from app.services.sources.document_fetcher import safe_fetch_document

        result = await safe_fetch_document(
            STL_MEMBERS_URL, allowed_domains=STL_HOSTS, resolve_ip=True,
            max_bytes=512 * 1024, total_deadline_seconds=20.0,
        )
    except Exception:  # noqa: BLE001 - a refresh failure keeps the last good list
        return None
    if result.error or result.blocked or not result.content:
        return None
    return result.content


def current_stl_members(store: MemberListStore | None = None) -> StlMemberList:
    """The last good list, or the bundled snapshot when no refresh has succeeded."""
    return (store or DEFAULT_STORE).load() or BUNDLED_STL_MEMBERS


async def refresh_stl_members(
    *,
    store: MemberListStore | None = None,
    fetch: PageFetcher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch and parse the members page; store it only when it parsed. Never raises."""
    target = store or DEFAULT_STORE
    moment = now or datetime.now(timezone.utc)
    content = await (fetch or fetch_stl_members_page)()
    if content is None:
        state = REFRESH_FETCH_FAILED
    else:
        try:
            parsed = parse_stl_members(decode_page(content), retrieved_at=moment.isoformat())
        except StlLayoutChanged:
            state = REFRESH_LAYOUT_CHANGED
        else:
            target.save(parsed)
            state = REFRESH_REFRESHED
    target.record_attempt(moment, state)
    kept = current_stl_members(target)
    return {"state": state, "members_count": len(kept.members), "origin": kept.origin}


async def ensure_stl_members_fresh(
    *,
    store: MemberListStore | None = None,
    fetch: PageFetcher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh when due (monthly; a day after a failure), else report ``not_due``."""
    target = store or DEFAULT_STORE
    moment = now or datetime.now(timezone.utc)
    attempted_at, state = target.last_attempt()
    if attempted_at is not None:
        wait = REFRESH_INTERVAL if state == REFRESH_REFRESHED else RETRY_AFTER_FAILURE
        if moment - attempted_at < wait:
            kept = current_stl_members(target)
            return {"state": REFRESH_NOT_DUE, "members_count": len(kept.members),
                    "origin": kept.origin}
    return await refresh_stl_members(store=target, fetch=fetch, now=moment)


# ── The registry ──────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class CertificationBody:
    """A recognised testing laboratory or certifier."""

    key: str
    name: str
    country: str
    hosts: tuple[str, ...]
    scope: str
    #: Names a document may use. All-caps acronyms match case-sensitively.
    aliases: tuple[str, ...]
    kind: str = KIND_TEST_LABORATORY
    #: The STL member code this body is listed under, when it is itself a member.
    stl_code: str | None = None
    #: The STL member it belongs to, when it is a member laboratory of one (IPH → PEHLA).
    stl_via: str | None = None
    #: ``curated`` or ``stl_members``.
    listed_in: str = "curated"
    stl_member: bool = False
    note: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "country": self.country,
            "hosts": list(self.hosts), "scope": self.scope, "kind": self.kind,
            "listed_in": self.listed_in, "stl_member": self.stl_member,
            "stl_via": self.stl_via if self.stl_member and self.stl_via else None,
        }


CURATED_BODIES: tuple[CertificationBody, ...] = (
    CertificationBody(
        "kema_labs", "KEMA Labs", "The Netherlands", ("kema.com",),
        "High-power, high-voltage and short-circuit type testing and certification of "
        "transmission and distribution equipment",
        ("KEMA Labs", "KEMA", "DNV KEMA", "DNV GL KEMA"), stl_code="KEMA",
        note="Part of the CESI group."),
    CertificationBody(
        "cesi", "CESI", "Italy", ("cesi.it",),
        "High-power and high-voltage testing of power equipment; grid studies",
        ("CESI",), stl_code="CESI"),
    CertificationBody(
        "iph_berlin", "IPH Berlin", "Germany", ("iph.de",),
        "High-power and short-circuit testing of switchgear, transformers and cables",
        ("IPH Berlin", "IPH"), stl_via="PEHLA",
        note="Member laboratory of PEHLA (Germany); part of the CESI group."),
    CertificationBody(
        "keri", "KERI", "South Korea", ("keri.re.kr",),
        "Korea Electrotechnology Research Institute: high-power and high-voltage testing",
        ("KERI", "Korea Electrotechnology Research Institute"), stl_code="KERI"),
    CertificationBody(
        "tubitak_mam", "TÜBİTAK MAM", "Türkiye", ("mam.tubitak.gov.tr", "tubitak.gov.tr"),
        "Energy Institute high-power and high-voltage test laboratories",
        ("TÜBİTAK MAM", "TÜBITAK MAM", "TUBITAK MAM", "TÜBİTAK", "TUBITAK")),
    CertificationBody(
        "cpri", "CPRI", "India", ("cpri.res.in", "cpri.in"),
        "Central Power Research Institute: short-circuit and type testing of power "
        "equipment", ("CPRI", "Central Power Research Institute"), stl_code="CPRI"),
    CertificationBody(
        "dnv", "DNV", "Norway", ("dnv.com",),
        "Certification of wind turbines and components, grid-code compliance, management "
        "systems", ("DNV", "DNV GL", "DNV-GL"), kind=KIND_CERTIFIER),
    CertificationBody(
        "tuv_sud", "TÜV SÜD", "Germany", ("tuvsud.com",),
        "Product testing and certification, including electrical equipment and PV",
        ("TÜV SÜD", "TUV SUD", "TÜV SUD", "TUEV SUED"), kind=KIND_CERTIFIER),
    CertificationBody(
        "tuv_rheinland", "TÜV Rheinland", "Germany", ("tuv.com",),
        "Product testing and certification, including PV modules and electrical equipment",
        ("TÜV Rheinland", "TUV Rheinland", "TUEV Rheinland"), kind=KIND_CERTIFIER),
    CertificationBody(
        "tuv", "TÜV (organisation not named)", "Germany", ("tuvsud.com", "tuv.com"),
        "Testing and certification (which TÜV organisation is not stated)",
        ("TÜV", "TUV", "TUEV"), kind=KIND_CERTIFIER),
    CertificationBody(
        "ul_solutions", "UL Solutions", "USA", ("ul.com",),
        "Product safety testing and listing to UL standards (e.g. UL 1741, UL 9540)",
        ("UL Solutions", "Underwriters Laboratories", "UL"), kind=KIND_CERTIFIER),
    CertificationBody(
        "intertek", "Intertek", "United Kingdom", ("intertek.com",),
        "Product testing and certification (ETL listing)", ("Intertek",),
        kind=KIND_CERTIFIER),
    CertificationBody(
        "csa_group", "CSA Group", "Canada", ("csagroup.org",),
        "Product testing and certification to CSA and UL/IEC standards",
        ("CSA Group", "CSA"), kind=KIND_CERTIFIER),
    CertificationBody(
        "bureau_veritas", "Bureau Veritas", "France", ("bureauveritas.com",),
        "Testing, inspection and certification", ("Bureau Veritas",), kind=KIND_CERTIFIER),
    CertificationBody(
        "sgs", "SGS", "Switzerland", ("sgs.com",),
        "Testing, inspection and certification", ("SGS",), kind=KIND_CERTIFIER),
    CertificationBody(
        "stl", "Short-Circuit Testing Liaison (STL)", "International", STL_HOSTS,
        "Liaison of short-circuit testing organisations; publishes type-test guides",
        ("Short-Circuit Testing Liaison", "Short Circuit Testing Liaison", "STL"),
        kind=KIND_LIAISON),
)


def build_registry(members: StlMemberList | None = None) -> tuple[CertificationBody, ...]:
    """Curated bodies (with STL membership set from ``members``) plus every other member."""
    listed = members or current_stl_members()
    codes = listed.codes
    out: list[CertificationBody] = []
    covered: set[str] = set()
    for body in CURATED_BODIES:
        code = (body.stl_code or "").upper()
        via = (body.stl_via or "").upper()
        if code and code in codes:
            covered.add(code)
            out.append(replace(body, stl_member=True))
        elif via and via in codes:
            out.append(replace(body, stl_member=True))
        else:
            out.append(replace(body, stl_code=None, stl_member=False))
    for m in listed.members:
        if m.code.upper() in covered:
            continue
        aliases = tuple(dict.fromkeys(a for a in (m.name, m.code) if a))
        out.append(CertificationBody(
            f"stl:{m.code.lower()}", m.name, m.country, (),
            "Short-circuit testing of high-voltage power equipment (STL member)",
            aliases, stl_code=m.code, listed_in="stl_members", stl_member=True))
    return tuple(out)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    flags = 0 if alias.upper() == alias else re.IGNORECASE
    tail = r"(?![\w])" + (r"(?!\s?\d)" if alias == "UL" else "")
    return re.compile(r"(?<![\w-])" + re.escape(alias).replace(r"\ ", r"[\s-]+") + tail,
                      flags)


@dataclass(frozen=True)
class _Alias:
    body: CertificationBody
    alias: str
    pattern: re.Pattern[str] = field(compare=False)


def _aliases(registry: Sequence[CertificationBody]) -> list[_Alias]:
    rows = [_Alias(b, a, _alias_pattern(a)) for b in registry for a in b.aliases]
    # Longest first, so "TÜV SÜD" wins over "TÜV" and "DNV KEMA" over "DNV".
    return sorted(rows, key=lambda r: -len(r.alias))


def match_body(
    text: str | None, registry: Sequence[CertificationBody] | None = None,
) -> CertificationBody | None:
    """The recognised body ``text`` names (longest alias wins), or ``None``."""
    if not text:
        return None
    best: tuple[int, int, CertificationBody] | None = None
    for row in _aliases(registry if registry is not None else build_registry()):
        m = row.pattern.search(text)
        if m is None:
            continue
        rank = (-len(row.alias), m.start())
        if best is None or rank < best[:2]:
            best = (rank[0], rank[1], row.body)
    return best[2] if best else None


def alias_patterns(body: CertificationBody) -> list[re.Pattern[str]]:
    return [_alias_pattern(a) for a in body.aliases]


def names_body(text: str, body: CertificationBody) -> bool:
    return any(p.search(text or "") for p in alias_patterns(body))


_NAMED_BY_RE = re.compile(
    r"\b(?:type[- ]tested|tested|certified|qualified|approved|witnessed|homologated|"
    r"certificates?\s+(?:issued|granted))\s+(?:by|at|from)\s+(?:the\s+)?"
    r"(?P<name>[A-Z][\w&'.-]*(?:\s+(?:[A-Z][\w&'.-]*|of|for|de|und|and)){0,5})"
)
_GENERIC_NAMES = frozenset({
    "the", "we", "our", "its", "an", "a", "independent", "accredited", "third", "several",
    "leading", "major", "international", "national", "company", "group", "customers",
    "utilities", "iec", "ieee", "iso", "en", "ansi",
})


def named_body_text(text: str | None) -> str | None:
    """An organisation a statement names as tester/certifier (``tested by X``), if any."""
    m = _NAMED_BY_RE.search(text or "")
    if m is None:
        return None
    words = m.group("name").split()
    while words and words[-1].lower() in {"of", "for", "de", "und", "and"}:
        words.pop()
    name = " ".join(words).rstrip(".,;")
    if not name or name.split()[0].lower() in _GENERIC_NAMES:
        return None
    return name[:120]


def on_hosts(url: str | None, hosts: Iterable[str]) -> bool:
    """True when ``url``'s host is one of ``hosts`` or a subdomain of one."""
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url or "").hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


__all__ = [
    "BUNDLED_STL_MEMBERS",
    "CURATED_BODIES",
    "DEFAULT_STORE",
    "PERIODIC_TASK_KEY",
    "STL_ATTRIBUTION",
    "STL_MEMBERS_URL",
    "CertificationBody",
    "InMemoryMemberListStore",
    "MemberListStore",
    "StlLayoutChanged",
    "StlMember",
    "StlMemberList",
    "build_registry",
    "current_stl_members",
    "decode_page",
    "ensure_stl_members_fresh",
    "fetch_stl_members_page",
    "match_body",
    "named_body_text",
    "names_body",
    "on_hosts",
    "parse_stl_members",
    "refresh_stl_members",
]
