"""Deterministic search-result selection — open-web W5 (spec §11.3, §8.4).

``score = class_prior(domain) + relevance(title/snippet vs family terms) + freshness_fit
+ novelty(not seen) + identity_hint − duplicate_penalty``; selection is the **top K per
family** under the fetch budget, with tie-breaks on ``(provider rank, url)``, so the same
results always select the same URLs.

SNIPPETS AND TITLES RANK, NOTHING MORE
======================================
A result's title and snippet are untrusted third-party text (spec §8.4, PI-09). They feed
the relevance and identity-hint terms below as **tokens compared to platform-owned
terms** and then they are gone: nothing here returns them, stores them, or builds a
query, a prompt or a citation from them. The class prior comes from the URL's host (the
same ``classify_source`` host rules the corpus uses), never from what a page says about
itself.

WHAT IS SKIPPED, AND WHY (recorded, never silent)
=================================================
``already_held`` (the canonical URL is already a document in the company's corpus),
``duplicate_url`` (the same URL under several queries: the best occurrence counts),
``not_https``, ``denylisted_domain`` (archive mirrors, circumvention services, social
networks, shorteners — ``domain_policy``), ``over_family_quota`` and ``over_budget``.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from app.services.providers.contracts import QueryFamily, SearchResultItem
from app.services.sources.public_suffix import registrable_domain
from app.services.web_research.classify import (
    SC_COMPANY_PRESS_RELEASE,
    SC_COMPANY_WEB_PAGE,
    SC_INVESTOR_PRESENTATION,
    SC_ISSUER_FILING,
    classify_source,
)
from app.services.web_research.domain_policy import denylisted
from app.services.web_research.packs import source_class_prior
from app.services.web_research.planner import FAMILY_TERMS, WAVE_FAMILIES, window_days

SELECTION_VERSION = "w5.1"

#: Family shares of the selection budget (largest remainder, ties to the earlier family).
FAMILY_WEIGHTS: dict[QueryFamily, int] = {
    QueryFamily.COMPANY_DOCS: 3,
    QueryFamily.CATALYST: 3,
    QueryFamily.COMPETITIVE: 2,
    QueryFamily.INDUSTRY: 2,
    QueryFamily.RISK: 3,
}

W_CLASS = 1.0
W_RELEVANCE = 1.0
W_FRESHNESS = 0.5
W_NOVELTY = 0.5
W_IDENTITY_ISSUER_DOMAIN = 0.5
W_IDENTITY_NAME = 0.3
DUPLICATE_DOMAIN_PENALTY = 0.5
UNDATED_FRESHNESS = 0.3

#: The issuer's OWN voice. Disconfirming (RISK) research must not rank a self-published
#: page above an independent source, so for RISK these classes get no identity bonus and a
#: capped class prior, and the family always picks one non-issuer result when one exists.
ISSUER_VOICE_CLASSES: frozenset[str] = frozenset(
    {SC_ISSUER_FILING, SC_COMPANY_PRESS_RELEASE, SC_INVESTOR_PRESENTATION, SC_COMPANY_WEB_PAGE}
)
RISK_ISSUER_PRIOR_CAP = 0.4

SKIP_ALREADY_HELD = "already_held"
SKIP_DUPLICATE_URL = "duplicate_url"
SKIP_NOT_HTTPS = "not_https"
SKIP_DENYLISTED = "denylisted_domain"
SKIP_OVER_QUOTA = "over_family_quota"
SKIP_OVER_BUDGET = "over_budget"
SKIP_NO_URL = "no_url"

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def fold_tokens(text: str | None) -> frozenset[str]:
    """Case- and diacritic-folded word tokens (relevance compares these only)."""
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return frozenset(_WORD_RE.findall(folded))


def host_of(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower().strip(".").removeprefix("www.")
    except ValueError:
        return ""


def domain_of(url: str | None) -> str:
    host = host_of(url)
    return registrable_domain(host) or host


@dataclass(frozen=True)
class SearchCandidate:
    """One search result, in the shape selection needs."""

    family: QueryFamily
    item: SearchResultItem
    query_key: str = ""
    query_id: uuid.UUID | None = None
    #: The ``web_search_results`` row (set once the batch is persisted), so a fetch
    #: attempt and a disposition can be tied back to the result.
    result_id: uuid.UUID | None = None

    @property
    def url(self) -> str:
        return self.item.canonical_url or self.item.url


@dataclass(frozen=True)
class SelectionContext:
    today: date
    #: VERIFIED issuer domains only (``verified_issuer_sources``).
    issuer_domains: tuple[str, ...] = ()
    #: Folded name/ticker words that point at the subject company.
    identity_terms: tuple[str, ...] = ()
    #: Canonical URLs of documents the company's corpus already holds.
    held_urls: frozenset[str] = frozenset()
    family_terms: Mapping[QueryFamily, tuple[str, ...]] = field(default_factory=dict)
    mode: str = "standard"
    #: Selected per family; ``None`` derives it from ``total``.
    quotas: Mapping[QueryFamily, int] | None = None
    total: int = 20


@dataclass(frozen=True)
class Scored:
    candidate: SearchCandidate
    score: float
    components: Mapping[str, float]
    source_class: str


@dataclass
class SelectionResult:
    selected: list[Scored] = field(default_factory=list)
    skipped: list[tuple[SearchCandidate, str]] = field(default_factory=list)
    version: str = SELECTION_VERSION

    def skipped_by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _candidate, reason in self.skipped:
            out[reason] = out.get(reason, 0) + 1
        return out

    def selected_by_family(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for scored in self.selected:
            key = scored.candidate.family.value
            out[key] = out.get(key, 0) + 1
        return out


def family_quotas(
    total: int, families: Sequence[QueryFamily] | None = None
) -> dict[QueryFamily, int]:
    """Split ``total`` over the families by weight; largest remainder, earlier family wins."""
    fams = list(families or WAVE_FAMILIES)
    weights = {f: FAMILY_WEIGHTS.get(f, 1) for f in fams}
    weight_sum = sum(weights.values()) or 1
    total = max(0, int(total))
    base = {f: (total * w) // weight_sum for f, w in weights.items()}
    remainders = sorted(
        fams, key=lambda f: (-((total * weights[f]) % weight_sum), fams.index(f))
    )
    for f in remainders[: total - sum(base.values())]:
        base[f] += 1
    return base


def _published(item: SearchResultItem) -> date | None:
    hint = item.published_hint
    if hint is None:
        return None
    if isinstance(hint, datetime):
        return (hint if hint.tzinfo else hint.replace(tzinfo=timezone.utc)).date()
    return hint  # type: ignore[unreachable]


def freshness_fit(item: SearchResultItem, *, family: QueryFamily, mode: str, today: date) -> float:
    """1.0 inside the family's window, 0.0 outside it, a middling value when undated."""
    published = _published(item)
    if published is None:
        return UNDATED_FRESHNESS
    age = (today - published).days
    if age < 0:
        return UNDATED_FRESHNESS  # a future date is a bad hint, not a fresh document
    return 1.0 if age <= window_days(family, mode) else 0.0


def relevance(item: SearchResultItem, terms: Iterable[str]) -> float:
    """Share of the family's terms found in the title, snippet or URL path (0..1)."""
    wanted = {t for term in terms for t in fold_tokens(term)}
    if not wanted:
        return 0.0
    try:
        path = urlsplit(item.url or "").path
    except ValueError:
        path = ""
    seen = fold_tokens(f"{item.title or ''} {item.snippet or ''} {path}")
    return len(wanted & seen) / len(wanted)


def identity_hint(item: SearchResultItem, ctx: SelectionContext, *, issuer: bool) -> float:
    if issuer:
        return W_IDENTITY_ISSUER_DOMAIN
    if not ctx.identity_terms:
        return 0.0
    seen = fold_tokens(f"{item.title or ''} {item.snippet or ''} {host_of(item.url)}")
    return W_IDENTITY_NAME if any(fold_tokens(t) <= seen for t in ctx.identity_terms) else 0.0


def score_candidate(candidate: SearchCandidate, ctx: SelectionContext) -> Scored:
    item = candidate.item
    url = candidate.url
    host = host_of(url)
    issuer_hosts = [x.lower().removeprefix("www.") for x in ctx.issuer_domains]
    issuer = any(host == d or host.endswith("." + d) for d in issuer_hosts)
    classification = classify_source(url, issuer_domains=ctx.issuer_domains)
    terms = ctx.family_terms.get(candidate.family) or FAMILY_TERMS.get(candidate.family, ())
    risk = candidate.family is QueryFamily.RISK
    self_published = classification.source_class in ISSUER_VOICE_CLASSES
    prior = source_class_prior(classification.source_class)
    if risk and self_published:
        prior = min(prior, RISK_ISSUER_PRIOR_CAP)
        issuer = False  # no identity bonus for the issuer's own page when seeking risk
    components = {
        "class_prior": W_CLASS * prior,
        "relevance": W_RELEVANCE * relevance(item, terms),
        "freshness": W_FRESHNESS * freshness_fit(
            item, family=candidate.family, mode=ctx.mode, today=ctx.today
        ),
        "novelty": W_NOVELTY if url not in ctx.held_urls else 0.0,
        "identity": identity_hint(item, ctx, issuer=issuer),
    }
    if risk and self_published:
        components["identity"] = 0.0  # not even a name match: it is the issuer's own page
    return Scored(candidate, round(sum(components.values()), 6), components,
                  classification.source_class)


def _occurrence_key(
    scored: Scored, family_order: Mapping[QueryFamily, int]
) -> tuple[float, int, int]:
    return (
        -scored.score,
        family_order.get(scored.candidate.family, 99),
        scored.candidate.item.rank,
    )


def select_results(
    candidates: Sequence[SearchCandidate], ctx: SelectionContext
) -> SelectionResult:
    """Pick the URLs worth fetching. Pure and deterministic."""
    result = SelectionResult()
    family_order = {f: i for i, f in enumerate(WAVE_FAMILIES)}

    # 1. Hygiene, in a fixed order so a skip is attributed the same way every run.
    ordered = sorted(
        candidates,
        key=lambda c: (family_order.get(c.family, 99), c.item.rank, c.url),
    )
    scored_all: list[Scored] = []
    for candidate in ordered:
        url = candidate.url
        if not url:
            result.skipped.append((candidate, SKIP_NO_URL))
        elif not url.lower().startswith("https://"):
            result.skipped.append((candidate, SKIP_NOT_HTTPS))
        elif denylisted(host_of(url)):
            result.skipped.append((candidate, SKIP_DENYLISTED))
        elif url in ctx.held_urls:
            result.skipped.append((candidate, SKIP_ALREADY_HELD))
        else:
            scored_all.append(score_candidate(candidate, ctx))
    # The same URL under several queries is ONE document, kept under the family in which
    # it scores best (a risk article found by a catalyst query belongs to RISK).
    best_by_url: dict[str, Scored] = {}
    for scored in scored_all:
        url = scored.candidate.url
        incumbent = best_by_url.get(url)
        if incumbent is None or _occurrence_key(scored, family_order) < _occurrence_key(
            incumbent, family_order
        ):
            best_by_url[url] = scored
    viable: list[Scored] = []
    for scored in scored_all:
        if best_by_url[scored.candidate.url] is scored:
            viable.append(scored)
        else:
            result.skipped.append((scored.candidate, SKIP_DUPLICATE_URL))

    # 2. Top K per family, greedy, so the duplicate-domain penalty is order-stable.
    quotas = dict(ctx.quotas) if ctx.quotas is not None else family_quotas(ctx.total)
    by_family: dict[QueryFamily, list[Scored]] = {}
    for scored in viable:
        by_family.setdefault(scored.candidate.family, []).append(scored)
    picked_per_domain: dict[str, int] = {}
    chosen_ids: set[int] = set()

    def best(pool: list[Scored]) -> Scored | None:
        remaining = [s for s in pool if id(s) not in chosen_ids]
        if not remaining:
            return None
        return min(
            remaining,
            key=lambda s: (
                -(s.score - DUPLICATE_DOMAIN_PENALTY
                  * picked_per_domain.get(domain_of(s.candidate.url), 0)),
                s.candidate.item.rank,
                s.candidate.url,
            ),
        )

    def take(scored: Scored) -> None:
        chosen_ids.add(id(scored))
        result.selected.append(scored)
        d = domain_of(scored.candidate.url)
        picked_per_domain[d] = picked_per_domain.get(d, 0) + 1

    total_cap = max(0, int(ctx.total))
    for family in WAVE_FAMILIES:
        pool = by_family.get(family, [])
        for n in range(max(0, quotas.get(family, 0))):
            if len(result.selected) >= total_cap:
                break
            choice = None
            if family is QueryFamily.RISK and n == 0:
                # Disconfirming evidence: the first RISK pick is independent of the issuer
                # whenever any independent result exists.
                choice = best([s for s in pool if s.source_class not in ISSUER_VOICE_CLASSES])
            if choice is None:
                choice = best(pool)
            if choice is None:
                break
            take(choice)
    # 3. Quota a family could not use goes to the best of the rest (still under the cap).
    leftovers = [s for s in viable if id(s) not in chosen_ids]
    while len(result.selected) < total_cap:
        choice = best(leftovers)
        if choice is None:
            break
        take(choice)
        leftovers = [s for s in leftovers if id(s) not in chosen_ids]
    taken_per_family = result.selected_by_family()
    for scored in viable:
        if id(scored) in chosen_ids:
            continue
        family = scored.candidate.family
        within_quota = taken_per_family.get(family.value, 0) >= quotas.get(family, 0)
        result.skipped.append(
            (scored.candidate, SKIP_OVER_QUOTA if within_quota else SKIP_OVER_BUDGET)
        )
    return result


__all__ = [
    "FAMILY_WEIGHTS",
    "SELECTION_VERSION",
    "ISSUER_VOICE_CLASSES",
    "SKIP_ALREADY_HELD",
    "SKIP_DENYLISTED",
    "SKIP_DUPLICATE_URL",
    "SKIP_NOT_HTTPS",
    "SKIP_NO_URL",
    "SKIP_OVER_BUDGET",
    "SKIP_OVER_QUOTA",
    "Scored",
    "SearchCandidate",
    "SelectionContext",
    "SelectionResult",
    "domain_of",
    "family_quotas",
    "fold_tokens",
    "freshness_fit",
    "host_of",
    "relevance",
    "score_candidate",
    "select_results",
]
