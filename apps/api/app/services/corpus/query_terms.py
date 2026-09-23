"""A corpus query is a query, not a question — V3.18.10.

WHAT WAS WRONG
==============
Every question's corpus search sent the analyst question **verbatim** as the query::

    "Which commodities and materials does the company produce and sell, and what
     share of revenue and of production does each represent in the latest reported
     period? Give the figures and the period."

The chunk vector is built with PostgreSQL's ``simple`` text-search configuration, which
has **no stopword list**: ``which``, ``does``, ``the``, ``and``, ``what`` are indexed
lexemes exactly like ``molybdenum``. The backend ORs the query's lexemes (deliberately —
an AND over a twenty-word question returns nothing), so a question matched nearly every
chunk in the filing on its function words alone, and ``ts_rank_cd`` then ranked by
whichever passage happened to pack the most ``the``/``and``/``company`` near each other.

MEASURED IN PRODUCTION on SCCO's FY2025 10-K (939 indexed chunks, 401 of them tables):

* the question above returned six paragraphs of general narrative, and the run recorded
  "No revenue or profit figures by product, segment or geography appear anywhere in the
  evidence" — while the corpus held ``Net sales in 2025 reached a record high of
  $13,420.0 million`` and a by-product revenue table;
* the same corpus, asked ``net sales by product copper molybdenum revenue share``,
  returned those chunks in the top ranks.

The evidence was there. The query was a paragraph of English.

WHAT THIS DOES
==============
``keyword_query`` reduces text to its distinctive terms: interrogatives, auxiliaries,
determiners, prepositions, the research vocabulary that appears in *every* question
("company", "issuer", "period", "figures", "disclose"), and legal-form suffixes
("Inc", "Corp", "PLC") are dropped. Everything else — nouns, numbers, identifiers,
fiscal labels — is kept in order, de-duplicated, capped.

TWO THINGS THIS IS NOT
======================
**It is not stemming or synonym expansion.** No term is invented, so a query can only
ever ask for words the question itself used. Expansion is how a search starts answering
a question nobody asked.

**It does not narrow the corpus.** Terms are dropped from the *query*, never from the
index, and the backend ORs what remains — so no document becomes unreachable. Dropping
``the`` cannot hide a chunk; it stops ``the`` from deciding which chunk wins.
"""

from __future__ import annotations

import re

#: Tokens worth searching for: words, numbers, and the identifiers a financial corpus is
#: made of (``FY2025``, ``mRNA-1273``, ``S-K``). Mirrors the backend's own tokeniser so a
#: term that survives here is a term the index can match.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*")

#: Function words. The index keeps them (the ``simple`` configuration has no stopword
#: list), which is exactly why the query must not spend its ranking on them.
#:
#: Curated against the corpus it searches, not copied from a generic list: ``mine`` is a
#: pronoun and the central noun of every mining filing, ``value``, ``level``, ``state``,
#: ``list`` and ``reportable`` all carry meaning in a disclosure ("sales value",
#: "inventory levels", "state support", "critical-minerals list", "reportable segment").
#: A stopword list that swallows the subject is worse than none, and
#: ``test_the_stopword_list_never_swallows_subject_matter`` pins every one of them.
_FUNCTION_WORDS: frozenset[str] = frozenset(
    """
    a an the this that these those it its it's their they them his her he she we our us
    and or but nor so yet if then than as because while when where which who whom whose
    what why how whether both either neither each every any some all none other another
    of in on at to from by for with within without into onto over under above below
    between among across during before after since until up down out off about against
    is are was were be been being am do does did done doing have has had having
    can could may might must shall should will would ought need
    not no nor only just also too very much many more most less least such same
    there here now still yet already ever never always often sometimes
    i you your my me ours yours theirs
    """.split()
)

#: The research vocabulary. Every question in every playbook contains most of these, so
#: they discriminate nothing and cost ranking: they are what made "the company" the
#: highest-scoring term in a query about by-product revenue.
_RESEARCH_WORDS: frozenset[str] = frozenset(
    """
    company companies company's issuer issuers group business businesses
    period periods periodic latest recent recently current currently reported report
    reports reporting disclose disclosed disclosure disclosures filing filings
    filed stated statement give given gives provide provided
    figure figures number numbers amount amounts
    evidence evidenced cite cited citation source sources
    question questions answer answers analysis analyse analyze analyst
    represent represents representing each own using use used
    say says said show shows shown name names named exist exists
    does do
    """.split()
)

#: Legal-form suffixes. ``{company}`` expands to the registered name, and in a corpus
#: already filtered to one company ``Corp`` matches its every page.
_ENTITY_SUFFIXES: frozenset[str] = frozenset(
    """
    inc inc's incorporated corp corp's corporation co company plc ltd limited llc lp llp
    sa s.a sab ab as asa nv bv gmbh ag spa oyj kgaa pte pty holdings holding group
    """.split()
)

STOPWORDS: frozenset[str] = _FUNCTION_WORDS | _RESEARCH_WORDS | _ENTITY_SUFFIXES

#: Enough terms to describe what is wanted; few enough that the OR does not turn back
#: into "every chunk matches something".
DEFAULT_MAX_TERMS = 14

#: A single character is never the distinctive term of a search, and the tokeniser
#: produces plenty of them from possessives and units.
_MIN_TERM_CHARS = 2


def keyword_query(text: str | None, *, max_terms: int = DEFAULT_MAX_TERMS) -> str:
    """The distinctive terms of ``text``, in order, de-duplicated, capped.

    Pure and total: no I/O, no model, never raises. Returns ``""`` when the text carries
    no distinctive term at all — the caller decides what to do with that, because
    searching for nothing and searching for everything are different mistakes.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        token = raw.strip("-/")
        low = token.lower()
        if not token or low in seen:
            continue
        if low in STOPWORDS:
            continue
        # A bare digit run is kept (a year, a tonnage), a one-letter fragment is not.
        if len(token) < _MIN_TERM_CHARS and not token.isdigit():
            continue
        seen.add(low)
        terms.append(token)
        if len(terms) >= max_terms:
            break
    return " ".join(terms)


__all__ = ["DEFAULT_MAX_TERMS", "STOPWORDS", "keyword_query"]
