"""The open-web domain denylist — versioned data (spec §9.2, §20.3, §13.4).

The open-web policy is "public internet, minus the W0 address and suffix denylists,
minus THIS list". It is data in code, like ``publisher_tiers``: a change is a reviewed
diff with a new :data:`DENYLIST_VERSION`, which is recorded on nothing secret and can be
quoted in an audit.

What is on it, and why:

* **archive and cache mirrors** — spec §20.3 forbids reaching paywalled content through
  them, and they re-host other sites' content under their own name;
* **paywall/unblocker services** — circumvention, forbidden outright;
* **social networks and forums** — deferred as a source class (spec §13.4), terms that
  prohibit automated access, and no reliable dating;
* **URL shorteners** — an opaque hop whose target a reviewer cannot read from the row;
* **proxies and shadow libraries** — translate/AMP/reader proxies re-serve another
  site's page under their own host; shadow libraries are infringing by construction.

Most entries match a host and its subdomains; :data:`DENIED_LABELS` match one label
under any parent (``sci-hub.<anything>``).

A host is denied when it equals an entry or is a subdomain of it.
"""

from __future__ import annotations

DENYLIST_VERSION = "2026-09-30.2"

ARCHIVE_AND_CACHE_MIRRORS: frozenset[str] = frozenset(
    {
        "archive.org",
        "archive.today",
        "archive.ph",
        "archive.is",
        "archive.li",
        "archive.vn",
        "archive.md",
        "archive.fo",
        "webcache.googleusercontent.com",
        "cachedview.nl",
        "ghostarchive.org",
        "perma.cc",
        "cc.bingj.com",
        # Proxies that re-serve another site's page under their own host.
        "translate.goog",
        "cdn.ampproject.org",
        "r.jina.ai",
    }
)
CIRCUMVENTION_SERVICES: frozenset[str] = frozenset(
    {
        "12ft.io",
        "removepaywall.com",
        "removepaywalls.com",
        "smry.ai",
        "freedium.cfd",
        "1ft.io",
        "bypasspaywalls.org",
    }
)
SOCIAL_AND_FORUMS: frozenset[str] = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "linkedin.com",
        "reddit.com",
        "stocktwits.com",
        "threads.net",
        "pinterest.com",
        "quora.com",
        "discord.com",
        "t.me",
        "telegram.org",
        "youtube.com",
        "bsky.app",
        "tumblr.com",
        "news.ycombinator.com",
        "xcancel.com",
        # Investor forums.
        "hotcopper.com.au",
        "lse.co.uk",
        "advfn.com",
    }
)
URL_SHORTENERS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "t.co",
        "tinyurl.com",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "is.gd",
        "rebrand.ly",
        "lnkd.in",
        "trib.al",
        "youtu.be",
        "fb.me",
        "amzn.to",
        "cutt.ly",
        "rb.gy",
        "t.ly",
        "dlvr.it",
        "ift.tt",
    }
)

#: Denied as a LABEL under any TLD or parent: shadow libraries and Twitter mirrors move
#: between domains (``sci-hub.se``, ``libgen.rs``, ``nitter.poast.org`` …).
DENIED_LABELS: frozenset[str] = frozenset({"sci-hub", "libgen", "annas-archive", "nitter"})

DOMAIN_DENYLIST: frozenset[str] = (
    ARCHIVE_AND_CACHE_MIRRORS | CIRCUMVENTION_SERVICES | SOCIAL_AND_FORUMS | URL_SHORTENERS
)


def denylisted(host: str | None) -> bool:
    """True when ``host`` (already IDNA-normalised, lower case) is on the denylist."""
    if not host:
        return False
    h = host.strip().lower().rstrip(".")
    labels = h.split(".")
    if any(label in DENIED_LABELS for label in labels[:-1]):
        return True
    return any(".".join(labels[i:]) in DOMAIN_DENYLIST for i in range(len(labels)))


__all__ = [
    "ARCHIVE_AND_CACHE_MIRRORS",
    "CIRCUMVENTION_SERVICES",
    "DENIED_LABELS",
    "DENYLIST_VERSION",
    "DOMAIN_DENYLIST",
    "SOCIAL_AND_FORUMS",
    "URL_SHORTENERS",
    "denylisted",
]
