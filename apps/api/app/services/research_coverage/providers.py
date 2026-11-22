"""The research providers the coverage index knows — V3.21 slice 21.7 (ADR-114).

Two ways in, decided per provider from what its site allows (checked 2026-11-22):

* **sitemap** — Edison Group. ``robots.txt`` disallows only ``/wp-admin/`` and lists
  ``https://www.edisongroup.com/sitemap.xml``, which carries ~670 ``/equity/<company>/``
  pages and ~3,700 ``/research/<note>/`` notes. Refreshed weekly.
* **search** — Redeye (its ``robots.txt`` disallows AI crawlers and its sitemap answers
  403), Research Tree (no sitemap) and mwb research (no usable sitemap). Never fetched in
  bulk: only a host-restricted web search, at most once per company per 30 days, when web
  search is on.

Every provider here publishes research that is commonly **paid for by the issuer**
(sponsored / commissioned research). The report says so beside every page it lists.
"""

from __future__ import annotations

from dataclasses import dataclass

METHOD_SITEMAP = "sitemap"
METHOD_SEARCH = "search"


@dataclass(frozen=True)
class ResearchProvider:
    key: str
    name: str
    hosts: tuple[str, ...]
    method: str
    #: Display note on what the provider is.
    note: str


EDISON = ResearchProvider(
    key="edison", name="Edison Group",
    hosts=("www.edisongroup.com", "edisongroup.com"), method=METHOD_SITEMAP,
    note="issuer-sponsored equity research; public company pages")
REDEYE = ResearchProvider(
    key="redeye", name="Redeye",
    hosts=("www.redeye.se", "redeye.se"), method=METHOD_SEARCH,
    note="Nordic research, largely commissioned by the issuer")
RESEARCH_TREE = ResearchProvider(
    key="research_tree", name="Research Tree",
    hosts=("www.researchtree.co.uk", "researchtree.co.uk"), method=METHOD_SEARCH,
    note="UK aggregator of sponsored and broker research")
MWB_RESEARCH = ResearchProvider(
    key="mwb_research", name="mwb research",
    hosts=("www.research-hub.de", "research-hub.de", "www.mwb-research.com",
           "mwb-research.com"), method=METHOD_SEARCH,
    note="German research, largely commissioned by the issuer")

PROVIDERS: tuple[ResearchProvider, ...] = (EDISON, REDEYE, RESEARCH_TREE, MWB_RESEARCH)
SEARCH_ONLY: tuple[ResearchProvider, ...] = tuple(
    p for p in PROVIDERS if p.method == METHOD_SEARCH)
BY_KEY: dict[str, ResearchProvider] = {p.key: p for p in PROVIDERS}

#: The bare registrable hosts the search-only providers are searched on (the search
#: provider's ``include_domains``; a subdomain match is the vendor's and is re-checked).
SEARCH_DOMAINS: tuple[str, ...] = ("redeye.se", "researchtree.co.uk", "research-hub.de",
                                   "mwb-research.com")


def provider_for_host(host: str | None) -> ResearchProvider | None:
    """The provider whose site ``host`` is (exact host, or a ``www.`` variant)."""
    h = (host or "").lower().strip(".")
    for provider in PROVIDERS:
        if h in provider.hosts:
            return provider
    return None


#: Shown beside every coverage page.
SPONSORED_NOTE = (
    "Sponsored research is paid for by the issuer. A listed page shows that a provider "
    "covers the company; its estimates and opinions are the provider's, and none of its "
    "claims is used as a company fact without a primary source."
)

__all__ = [
    "BY_KEY",
    "EDISON",
    "METHOD_SEARCH",
    "METHOD_SITEMAP",
    "MWB_RESEARCH",
    "PROVIDERS",
    "REDEYE",
    "RESEARCH_TREE",
    "SEARCH_DOMAINS",
    "SEARCH_ONLY",
    "SPONSORED_NOTE",
    "ResearchProvider",
    "provider_for_host",
]
