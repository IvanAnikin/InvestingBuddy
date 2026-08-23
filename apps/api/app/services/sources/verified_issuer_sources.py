"""
Verified issuer source registry — Phase 29B.1.

A small, explicit, **code-defined** allowlist of company-owned primary sources
for a handful of well-known non-US (and a few US) issuers. It is a *bootstrap*,
not a crawler: it names an issuer's own investor-relations, annual-reports and
newsroom pages so the company-IR connector can surface real, verified,
company-owned T1 evidence instead of only price-derived T5/T6 data.

Why this exists
---------------
European luxury / watch names (Richemont, Swatch, LVMH, Hermès, Kering,
Burberry, Pandora, Moncler, …) produce LLM reports, but the council correctly
returns *insufficient_data* because the evidence pack has no annual reports,
IR pages, or company press releases — only price/model items. SEC EDGAR does not
cover them, and their home-regulator connectors are still scaffolded. This
registry lets the platform cite the issuer's *own* primary material now.

Hard safety rules (enforced by ``validate_registry`` + tests)
------------------------------------------------------------
  * Every URL is HTTPS.
  * Every URL's host is inside that issuer's ``allowed_domains`` allowlist.
  * No URL carries a credential / token query parameter.
  * Every entry has ticker + exchange + company_name.
  * (ticker, exchange) is unique.
  * ``allowed_domains`` is the minimum set required to reach the listed URLs.

This is maintained reference data — an allowlist — NOT model-fabricated. When a
URL is uncertain it is omitted and the connector emits an honest ``SourceGap``
rather than guessing. The registry never stores secrets and never accepts a
user-supplied URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.services.sources.redaction import url_has_secret

# Confidence in an entry's URLs.
CONFIDENCE_VERIFIED_LIVE = "verified_live"        # a URL was fetched & confirmed
CONFIDENCE_VERIFIED_REFERENCE = "verified_reference"  # known-stable reference URL


@dataclass(frozen=True)
class VerifiedIssuerSource:
    """Curated, verified company-owned sources for one issuer.

    Stores only safe metadata (identity + public page URLs + an allowlist). No
    secrets, no tokenized URLs, no user-supplied URLs.
    """

    ticker: str
    exchange: str
    company_name: str
    country: str
    official_website_domain: str
    allowed_domains: tuple[str, ...]
    investor_relations_url: str | None = None
    annual_reports_url: str | None = None
    press_releases_url: str | None = None
    # Curated content hosts this issuer publishes its OWN documents on.
    #
    # Some issuers serve annual reports from a content CDN rather than their
    # corporate domain (e.g. Pandora publishes to an Amplience host). Those
    # artifacts are genuine issuer publications, but the host sits outside
    # ``allowed_domains``, so the fetcher correctly refuses them.
    #
    # This is a NARROW fetch authority, not a source: it authorises retrieving
    # artifacts LINKED FROM this issuer's verified pages, and it is scoped to
    # THIS issuer — one issuer's CDN never becomes usable by another. Trust
    # comes from curation here, never from a URL discovered at runtime, so no
    # page can talk the fetcher into a new host. Exact hostnames only.
    document_domains: tuple[str, ...] = field(default_factory=tuple)
    source_confidence: str = CONFIDENCE_VERIFIED_REFERENCE
    last_verified_note: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def fetch_allowed_domains(self) -> tuple[str, ...]:
        """Hosts a document fetch for THIS issuer may reach.

        The issuer's own domains plus any curated document hosts. Callers pass
        this to the fetcher, which re-checks it on every redirect hop, so a
        document host cannot be used as a stepping stone off the allowlist.
        """
        return tuple(self.allowed_domains) + tuple(self.document_domains)

    def urls(self) -> list[str]:
        """All non-null page URLs on this entry."""
        return [
            u
            for u in (
                self.investor_relations_url,
                self.annual_reports_url,
                self.press_releases_url,
            )
            if u
        ]


def _key(ticker: str | None, exchange: str | None) -> str:
    return f"{(ticker or '').strip().upper()}:{(exchange or '').strip().upper()}"


def registrable_host_allowed(host: str | None, allowed_domains: tuple[str, ...]) -> bool:
    """True when ``host`` equals or is a sub-domain of an allowed domain.

    ``news.example.com`` is allowed by ``example.com``; ``evil-example.com`` and
    ``example.com.attacker.net`` are not. Empty / missing hosts are rejected.
    """
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("www."):
        h = h[4:]
    for dom in allowed_domains:
        d = dom.strip().lower()
        if not d:
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def host_of(url: str | None) -> str | None:
    """Lower-case host of a URL (no port), or None when unparseable."""
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# The registry. Target non-US luxury / watch issuers first, then a few
# follow-up test issuers. URLs marked verified_live were fetched and confirmed
# on 2026-07-25; the rest are known-stable reference URLs (the connector degrades
# to an honest gap if a path has since moved — it never fabricates a filing).
# --------------------------------------------------------------------------- #
_ISSUERS: tuple[VerifiedIssuerSource, ...] = (
    VerifiedIssuerSource(
        ticker="CFR",
        exchange="SW",
        company_name="Compagnie Financière Richemont SA",
        country="Switzerland",
        official_website_domain="richemont.com",
        allowed_domains=("richemont.com",),
        investor_relations_url="https://www.richemont.com/en/home/investors/",
        annual_reports_url=(
            "https://www.richemont.com/en/home/investors/results-reports-presentations/"
        ),
        press_releases_url="https://www.richemont.com/en/home/media/",
        source_confidence=CONFIDENCE_VERIFIED_LIVE,
        last_verified_note="IR + results/reports index confirmed 2026-07-25.",
        warnings=("Reports are metadata-only; PDF text is not extracted in this phase.",),
    ),
    VerifiedIssuerSource(
        ticker="UHR",
        exchange="SW",
        company_name="The Swatch Group AG",
        country="Switzerland",
        official_website_domain="swatchgroup.com",
        allowed_domains=("swatchgroup.com",),
        investor_relations_url="https://www.swatchgroup.com/en/investors",
        annual_reports_url="https://www.swatchgroup.com/en/investors/annual-report",
        press_releases_url="https://www.swatchgroup.com/en/services/archive",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR reference URLs; live fetch may be JS-gated.",
        warnings=("Live fetch may be blocked or JavaScript-gated; treat as metadata.",),
    ),
    VerifiedIssuerSource(
        ticker="MC",
        exchange="PA",
        company_name="LVMH Moët Hennessy Louis Vuitton SE",
        country="France",
        official_website_domain="lvmh.com",
        allowed_domains=("lvmh.com",),
        investor_relations_url="https://www.lvmh.com/en/investors",
        annual_reports_url="https://www.lvmh.com/en/publications",
        press_releases_url="https://www.lvmh.com/en/news-lvmh",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR / publications (URD) reference URLs.",
        warnings=(
            "Universal Registration Document is French primary disclosure; "
            "local-language extraction pending Phase 30 translation.",
        ),
    ),
    VerifiedIssuerSource(
        ticker="RMS",
        exchange="PA",
        company_name="Hermès International SCA",
        country="France",
        official_website_domain="hermes.com",
        allowed_domains=("hermes.com", "finance.hermes.com", "assets-finance.hermes.com"),
        investor_relations_url="https://www.hermes.com/en/investors/",
        annual_reports_url="https://www.hermes.com/en/financial-publications/",
        press_releases_url="https://www.hermes.com/en/press-releases-and-news/",
        source_confidence=CONFIDENCE_VERIFIED_LIVE,
        last_verified_note="IR, financial-publications + press index confirmed 2026-07-25.",
        warnings=(
            "Universal Registration Document is French primary disclosure; "
            "local-language extraction pending Phase 30 translation.",
        ),
    ),
    VerifiedIssuerSource(
        ticker="KER",
        exchange="PA",
        company_name="Kering SA",
        country="France",
        official_website_domain="kering.com",
        allowed_domains=("kering.com",),
        investor_relations_url="https://www.kering.com/en/finance/",
        annual_reports_url="https://www.kering.com/en/finance/publications/",
        press_releases_url="https://www.kering.com/en/news/",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable finance/publications URLs; site uses bot protection.",
        warnings=(
            "Site uses bot protection; live fetch is often blocked (honest gap "
            "returned). Universal Registration Document is French primary disclosure.",
        ),
    ),
    VerifiedIssuerSource(
        ticker="BRBY",
        exchange="LSE",
        company_name="Burberry Group plc",
        country="United Kingdom",
        official_website_domain="burberryplc.com",
        allowed_domains=("burberryplc.com",),
        investor_relations_url="https://www.burberryplc.com/investors",
        annual_reports_url="https://www.burberryplc.com/investors/results-reports",
        press_releases_url="https://www.burberryplc.com/news",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs; site uses bot protection.",
        warnings=("Site uses bot protection; live fetch may be blocked (honest gap).",),
    ),
    VerifiedIssuerSource(
        ticker="PNDORA",
        exchange="CO",
        company_name="Pandora A/S",
        country="Denmark",
        official_website_domain="pandoragroup.com",
        allowed_domains=("pandoragroup.com",),
        # Verified live 2026-08-23: the reports page links three artifacts on
        # this host; "Annual Report 2025" returns HTTP 206 with
        # Content-Type: application/pdf and %PDF-1.7 magic bytes, no redirect.
        document_domains=("pandora.a.bigcontent.io",),
        investor_relations_url="https://pandoragroup.com/investor",
        annual_reports_url="https://pandoragroup.com/investor/reports-and-presentations",
        press_releases_url=(
            "https://pandoragroup.com/investor/announcements-and-events/"
            "company-announcements"
        ),
        source_confidence=CONFIDENCE_VERIFIED_LIVE,
        last_verified_note=(
            "Re-verified live 2026-08-23: IR landing 200. The previous "
            "news-and-reports/* URLs both 404 — the site reorganised to "
            "reports-and-presentations and announcements-and-events."
        ),
        warnings=(
            "Report documents are hosted off-domain on the issuer's content CDN "
            "(pandora.a.bigcontent.io) and carry no .pdf extension, so neither "
            "the domain allowlist nor extension-based document discovery "
            "reaches them yet (honest gap).",
        ),
    ),
    VerifiedIssuerSource(
        ticker="MONC",
        exchange="MI",
        company_name="Moncler S.p.A.",
        country="Italy",
        official_website_domain="monclergroup.com",
        allowed_domains=("monclergroup.com",),
        investor_relations_url="https://www.monclergroup.com/en/investors",
        annual_reports_url="https://www.monclergroup.com/en/investors/results-and-reports",
        press_releases_url="https://www.monclergroup.com/en/media/press-releases",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs; site returned 403 to research bot.",
        warnings=("Site returned 403 to the research bot; live fetch may be blocked.",),
    ),
    # ── Optional follow-up test issuers ──────────────────────────────────────
    VerifiedIssuerSource(
        ticker="BA",
        exchange="LSE",
        company_name="BAE Systems plc",
        country="United Kingdom",
        official_website_domain="baesystems.com",
        allowed_domains=("baesystems.com", "investors.baesystems.com"),
        investor_relations_url="https://www.baesystems.com/en/investors",
        annual_reports_url="https://www.baesystems.com/en/investors/annual-report",
        press_releases_url="https://www.baesystems.com/en/our-company/newsroom",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs. Distinct from US BA (Boeing).",
        warnings=(
            "BA on LSE is BAE Systems (UK), NOT Boeing (US NYSE) — company IR is "
            "the correct primary source; SEC EDGAR is not eligible.",
        ),
    ),
    VerifiedIssuerSource(
        ticker="ASML",
        exchange="AS",
        company_name="ASML Holding N.V.",
        country="Netherlands",
        official_website_domain="asml.com",
        allowed_domains=("asml.com",),
        investor_relations_url="https://www.asml.com/en/investors",
        annual_reports_url="https://www.asml.com/en/investors/annual-report",
        press_releases_url="https://www.asml.com/en/news/press-releases",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs.",
    ),
    VerifiedIssuerSource(
        ticker="SAP",
        exchange="DE",
        company_name="SAP SE",
        country="Germany",
        official_website_domain="sap.com",
        allowed_domains=("sap.com",),
        investor_relations_url="https://www.sap.com/investors",
        annual_reports_url="https://www.sap.com/investors/en/reports.html",
        press_releases_url="https://news.sap.com/",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs.",
    ),
    VerifiedIssuerSource(
        ticker="NESN",
        exchange="SW",
        company_name="Nestlé S.A.",
        country="Switzerland",
        official_website_domain="nestle.com",
        allowed_domains=("nestle.com",),
        investor_relations_url="https://www.nestle.com/investors",
        annual_reports_url="https://www.nestle.com/investors/annual-report",
        press_releases_url="https://www.nestle.com/media/pressreleases",
        source_confidence=CONFIDENCE_VERIFIED_REFERENCE,
        last_verified_note="Known-stable IR URLs.",
    ),
    VerifiedIssuerSource(
        ticker="GDWN",
        exchange="LSE",
        company_name="Goodwin PLC",
        country="United Kingdom",
        official_website_domain="goodwin.co.uk",
        allowed_domains=("goodwin.co.uk",),
        annual_reports_url="https://www.goodwin.co.uk/company-reports/",
        source_confidence=CONFIDENCE_VERIFIED_LIVE,
        last_verified_note=(
            "Static HTML reports archive confirmed 2026-08-09 (direct <a href> PDF "
            "links, no JS rendering needed, back to 2002)."
        ),
        warnings=(
            "The reports archive spans decades; older entries (e.g. the 2002 "
            "report) are genuinely scanned/image-only PDFs with no text layer — "
            "used as a real-world Phase 32A Slice 5B.2 OCR validation target. "
            "Some archive entries (e.g. the 2003 report) are password-encrypted "
            "and are honestly classified as such, never bypassed.",
        ),
    ),
)


def _build_index() -> dict[str, VerifiedIssuerSource]:
    index: dict[str, VerifiedIssuerSource] = {}
    for issuer in _ISSUERS:
        k = _key(issuer.ticker, issuer.exchange)
        if k in index:
            raise ValueError(f"Duplicate verified issuer (ticker, exchange): {k}")
        index[k] = issuer
    return index


_INDEX: dict[str, VerifiedIssuerSource] = _build_index()


def get_verified_issuer_source(
    ticker: str | None, exchange: str | None = None
) -> VerifiedIssuerSource | None:
    """Look up a verified issuer by (ticker, exchange).

    Tolerates a ``TICKER.EXCHANGE`` combined ticker (e.g. ``"CFR.SW"``) when
    ``exchange`` is not given separately. Returns None for unknown issuers — the
    caller then emits an honest coverage gap, never fabricated evidence.
    """
    if not ticker:
        return None
    t = ticker.strip().upper()
    if exchange is None and "." in t:
        t, _, ex = t.partition(".")
        exchange = ex
    return _INDEX.get(_key(t, exchange))


def all_verified_issuer_sources() -> list[VerifiedIssuerSource]:
    """Every registry entry (stable order)."""
    return list(_ISSUERS)


def validate_registry(issuers: tuple[VerifiedIssuerSource, ...] = _ISSUERS) -> None:
    """Assert the registry's hard safety invariants. Raises on any violation.

    Called by tests; kept importable so a future CI check can run it too.
    """
    seen: set[str] = set()
    for it in issuers:
        assert it.ticker and it.exchange and it.company_name, (
            f"entry missing ticker/exchange/company_name: {it!r}"
        )
        k = _key(it.ticker, it.exchange)
        assert k not in seen, f"duplicate (ticker, exchange): {k}"
        seen.add(k)
        assert it.allowed_domains, f"{k}: allowed_domains must not be empty"
        for url in it.urls():
            assert url.startswith("https://"), f"{k}: non-HTTPS URL: {url}"
            assert not url_has_secret(url), f"{k}: URL carries a secret param: {url}"
            host = host_of(url)
            assert registrable_host_allowed(host, it.allowed_domains), (
                f"{k}: URL host {host!r} not in allowed_domains {it.allowed_domains}"
            )
        # Document hosts are an EXTRA fetch authority, never a substitute for
        # the issuer's own domains, and never a wildcard.
        for dom in it.document_domains:
            d = dom.strip().lower()
            assert d and d == dom, f"{k}: document domain must be lowercase/trimmed: {dom!r}"
            assert "*" not in d and "/" not in d and ":" not in d, (
                f"{k}: document domain must be a bare hostname: {dom!r}"
            )
            assert "." in d, f"{k}: document domain must be fully qualified: {dom!r}"
            assert not registrable_host_allowed(d, it.allowed_domains), (
                f"{k}: document domain {dom!r} is already covered by "
                "allowed_domains; do not duplicate trust"
            )


__all__ = [
    "VerifiedIssuerSource",
    "CONFIDENCE_VERIFIED_LIVE",
    "CONFIDENCE_VERIFIED_REFERENCE",
    "get_verified_issuer_source",
    "all_verified_issuer_sources",
    "registrable_host_allowed",
    "host_of",
    "validate_registry",
]
