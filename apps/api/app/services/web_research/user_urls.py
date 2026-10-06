"""User-supplied URLs — open-web W2 (spec §21, threat model QI-02).

A URL in a thesis ("also use this report: https://…") goes through EXACTLY the same
pipeline as any other URL: :func:`~app.services.web_research.fetch.open_web_fetch` with
``origin="user"``. There is no separate or unsafe path, and the user's URL gains no
trust from the user — every guard, the denylist, robots.txt, TDM, the budget and the
limiter apply unchanged.

The only differences ``origin="user"`` makes are the ones spec §21 and threat model §2.4
check 1 name: an ``http://`` URL is upgraded to ``https://`` (recorded on the row), and a
bare ``www.`` link gets ``https://``. ``127.0.0.1``, ``169.254.169.254``, private ranges,
other schemes and ports are refused by the W0 guard exactly as for any other origin.

URLs are found by the W1 query sanitiser (``sanitise_query(...).extracted_urls``), which
already separates them from the text that may be sent to a search provider; they are
never sent to one. At most :data:`MAX_USER_URLS` distinct URLs are taken from one text.
"""

from __future__ import annotations

from typing import Any

from app.services.web_research.budget import WebResearchBudget
from app.services.web_research.canonical import canonical_url
from app.services.web_research.fetch import (
    ORIGIN_USER,
    OpenWebFetchResult,
    OpenWebFetchRuntime,
    WebFetchContext,
    open_web_fetch,
)
from app.services.web_research.queries import sanitise_query

MAX_USER_URLS = 5


def extract_user_urls(text: str | None, *, limit: int = MAX_USER_URLS) -> list[str]:
    """Distinct URLs in ``text``, in order of appearance, at most ``limit``.

    Distinct by canonical form where one can be computed (so ``?utm_source=`` variants
    of one link are one URL), else by the text itself.
    """
    urls = sanitise_query(text or "").extracted_urls
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = url
        lowered = url.lower()
        if lowered.startswith(("http://", "https://")):
            key = canonical_url("https://" + url.split("://", 1)[1]) or url
        elif lowered.startswith("www."):
            key = canonical_url("https://" + url) or url
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


async def fetch_user_urls(
    session: Any,
    text: str | None,
    *,
    context: WebFetchContext,
    budget: WebResearchBudget,
    cfg: Any | None = None,
    resolver: Any | None = None,
    runtime: OpenWebFetchRuntime | None = None,
) -> list[OpenWebFetchResult]:
    """Fetch every URL found in ``text`` through the open-web policy, one at a time."""
    results: list[OpenWebFetchResult] = []
    for url in extract_user_urls(text):
        results.append(
            await open_web_fetch(
                session,
                url,
                context=context,
                budget=budget,
                origin=ORIGIN_USER,
                cfg=cfg,
                resolver=resolver,
                runtime=runtime,
            )
        )
    return results


__all__ = ["MAX_USER_URLS", "extract_user_urls", "fetch_user_urls"]
