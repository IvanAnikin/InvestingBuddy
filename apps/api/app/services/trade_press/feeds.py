"""The trade-press feed registry, by electricity layer — V3.21.6 (spec §7, ADR-113).

Versioned data. Each feed names its outlet, its EXACT feed URL and host (the poller is
pinned to that host and nothing else), the electricity layers it covers
(``services/electricity/taxonomy.py`` keys), its language and a licence note.

Every feed below answered 200 with ``application/rss+xml`` on 2026-11-22, and its
robots.txt allowed the feed path for our product token (energy-storage.news answered 403
for robots.txt, which RFC 9309 reads as "no restrictions"; the poller re-checks on every
run). offshoreWIND.biz serves its feed at ``/feed``; ``/feed/`` redirects there.

Three outlets have no reachable feed — T&D World, Recharge and energate — and are reached
only through layer-routed web search (:data:`SEARCH_ONLY_OUTLETS`), when web search is on.

WHAT IS KEPT (licence)
======================
All of these outlets are copyright publishers with no open licence. The platform keeps
what the publisher itself syndicates in its feed — the title, a summary cut to 600
characters and the link — never the article body: excerpt, not republish. Items are T4
``trade_publication`` documents (``web_research/classify.py``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.services.electricity.taxonomy import LAYER_KEYS

REGISTRY_VERSION = "2026-11-22.1"

LICENCE_NOTE = (
    "Publisher copyright; no open licence. Only the feed's own title, a summary cut to "
    "600 characters and the link are kept — never the article body (excerpt, not "
    "republish)."
)


@dataclass(frozen=True)
class TradePressFeed:
    key: str
    outlet: str
    url: str
    host: str
    #: Electricity layers the outlet covers; the FIRST is the default theme for an item
    #: whose own words name none of them.
    layers: tuple[str, ...]
    language: str
    region: str
    licence_note: str = LICENCE_NOTE


@dataclass(frozen=True)
class SearchOnlyOutlet:
    """An outlet with no reachable feed: used only as a search ``include_domains`` host."""

    outlet: str
    host: str
    layers: tuple[str, ...]
    language: str
    note: str


FEEDS: tuple[TradePressFeed, ...] = (
    TradePressFeed(
        "utility_dive", "Utility Dive", "https://www.utilitydive.com/feeds/news/",
        "www.utilitydive.com",
        ("utilities_grid_services", "transmission", "distribution_grid_edge",
         "demand_side_decentralised"),
        "en", "US"),
    TradePressFeed(
        "energy_storage_news", "Energy-Storage.news", "https://www.energy-storage.news/feed/",
        "www.energy-storage.news", ("storage",), "en", "global"),
    TradePressFeed(
        "pv_magazine", "pv magazine", "https://www.pv-magazine.com/feed/",
        "www.pv-magazine.com", ("generation_fuel", "storage", "power_electronics"),
        "en", "global"),
    TradePressFeed(
        "world_nuclear_news", "World Nuclear News", "https://www.world-nuclear-news.org/rss",
        "www.world-nuclear-news.org", ("generation_fuel",), "en", "global"),
    TradePressFeed(
        "datacenter_dynamics", "Data Center Dynamics",
        "https://www.datacenterdynamics.com/en/rss/", "www.datacenterdynamics.com",
        ("demand_side_decentralised",), "en", "global"),
    TradePressFeed(
        "power_electronics_news", "Power Electronics News",
        "https://www.powerelectronicsnews.com/feed/", "www.powerelectronicsnews.com",
        ("power_electronics", "sensing_protection_digital"), "en", "global"),
    TradePressFeed(
        "offshore_wind_biz", "offshoreWIND.biz", "https://www.offshorewind.biz/feed",
        "www.offshorewind.biz", ("generation_fuel", "transmission"), "en", "Europe"),
    TradePressFeed(
        "power_engineering", "Power Engineering", "https://www.power-eng.com/feed/",
        "www.power-eng.com", ("generation_fuel", "utilities_grid_services"), "en", "US"),
    TradePressFeed(
        "wysokie_napiecie", "WysokieNapiecie.pl", "https://wysokienapiecie.pl/feed/",
        "wysokienapiecie.pl",
        ("utilities_grid_services", "transmission", "distribution_grid_edge",
         "generation_fuel"),
        "pl", "CEE"),
)

SEARCH_ONLY_OUTLETS: tuple[SearchOnlyOutlet, ...] = (
    SearchOnlyOutlet("T&D World", "tdworld.com",
                     ("transmission", "distribution_grid_edge", "sensing_protection_digital",
                      "utilities_grid_services"),
                     "en", "no reachable feed on 2026-11-22"),
    SearchOnlyOutlet("Recharge", "rechargenews.com", ("generation_fuel", "transmission"),
                     "en", "no reachable feed on 2026-11-22"),
    SearchOnlyOutlet("energate messenger", "energate-messenger.de",
                     ("utilities_grid_services", "distribution_grid_edge", "generation_fuel"),
                     "de", "no reachable feed on 2026-11-22"),
)

_BY_KEY = {f.key: f for f in FEEDS}


def feed(key: str) -> TradePressFeed | None:
    return _BY_KEY.get(key)


def theme_key(layer: str) -> str:
    """``electricity:<layer>`` — the theme a trade-press item is filed under."""
    return f"electricity:{layer}"


def layer_of_theme_key(key: str | None) -> str | None:
    if not key or not key.startswith("electricity:"):
        return None
    layer = key.split(":", 1)[1]
    return layer if layer in LAYER_KEYS else None


def _bare(host: str) -> str:
    return host.lower().removeprefix("www.")


def outlet_hosts() -> tuple[str, ...]:
    """Every outlet host (feeds and search-only), bare (no ``www.``), sorted."""
    hosts = {_bare(f.host) for f in FEEDS} | {_bare(o.host) for o in SEARCH_ONLY_OUTLETS}
    return tuple(sorted(hosts))


def hosts_for_layers(layers: Iterable[str]) -> tuple[str, ...]:
    """The outlet hosts (bare, sorted) that cover any of ``layers``; empty for none."""
    wanted = {x for x in layers if x}
    if not wanted:
        return ()
    hosts = {_bare(f.host) for f in FEEDS if wanted & set(f.layers)}
    hosts |= {_bare(o.host) for o in SEARCH_ONLY_OUTLETS if wanted & set(o.layers)}
    return tuple(sorted(hosts))


def outlet_for_host(host: str | None) -> str | None:
    bare = _bare(host or "")
    for f in FEEDS:
        if _bare(f.host) == bare:
            return f.outlet
    for o in SEARCH_ONLY_OUTLETS:
        if _bare(o.host) == bare:
            return o.outlet
    return None


def _check() -> None:
    for f in FEEDS:
        if not f.layers or any(x not in LAYER_KEYS for x in f.layers):
            raise ValueError(f"trade-press feed {f.key}: unknown layer")
        if not f.url.startswith(f"https://{f.host}/"):
            raise ValueError(f"trade-press feed {f.key}: url is not on its host")
    for o in SEARCH_ONLY_OUTLETS:
        if any(x not in LAYER_KEYS for x in o.layers):
            raise ValueError(f"search-only outlet {o.host}: unknown layer")


_check()


__all__ = [
    "FEEDS",
    "LICENCE_NOTE",
    "REGISTRY_VERSION",
    "SEARCH_ONLY_OUTLETS",
    "SearchOnlyOutlet",
    "TradePressFeed",
    "feed",
    "hosts_for_layers",
    "layer_of_theme_key",
    "outlet_for_host",
    "outlet_hosts",
    "theme_key",
]
