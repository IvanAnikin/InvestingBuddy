"""Whose text is it, when nothing says so? The implicit-subject firewall — ADR-096.

WHAT WAS WRONG (QA campaign 2026-11, round 3)
=============================================
Eleven items about other companies stood under four subjects:

* "Current developments" listed web documents whose title never names the subject — a
  carmaker's factory, another miner's drilling update, an investor conference, a quote
  page for a pharma major, a market report naming a different gene-therapy company, a
  prediction-market review — and, for an industrial, three headlines about a separately
  registered company that carries its name ("<Name> Aerospace selected for …"). The
  catalyst list never met the subject test the findings get (ADR-066/074/083).
* findings whose subject is another company or nobody the platform can name, cited only
  from documents that are not the subject's ("FDA placed two <Other> gene therapies on
  clinical hold …"; "<Other>, a separate company referenced in <Subject> filings, …").

THE RULES
=========
1. A CURRENT-DEVELOPMENT document stands under the subject only when the platform
   verified it as the subject's own (its origin is the run's issuer), or its own title
   names the subject — its ticker (exact case, whole token), a distinctive word of its
   name, or a brand of it (``trust.BRAND_ALIASES``, multi-word slugs included) — other
   than inside the name of a separately registered company that carries the subject's
   name (``RelatedRegistrant``, ADR-066). Fail closed: a title that names no one is not
   the subject's.
2. A FINDING about the subject's own business (not an industry, peer or market
   question) that rests only on documents that are not the subject's — no cited passage
   owned by the subject (its filing, its verified site, a typed platform record) — is
   the subject's only when the statement or a cited passage NAMES the subject. A
   statement with an implicit subject ("The company …", "It …", a table with no name)
   inherits the subject of its DOCUMENT; when that document neither is the subject's nor
   names it, the subject is unknown and the finding is not attributed (fail closed).
3. A statement whose writer declares its opening entity another company ("<Name>, a
   separate company …", "<Name>, an unrelated company …") is that company's, wherever
   the subject is named later in the sentence.

Explicit other-company subjects remain the ADR-066 / ADR-074 / ADR-079 / ADR-083
firewalls' job; these rules only add the implicit and the self-declared cases.

Every pattern is bounded and linear; every input is cut before it is read. No issuer is
named.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Inputs longer than this are cut before they are read.
MAX_TEXT_CHARS = 4000
#: A name word read across joined tokens ("J.P. Morgan", "JPMorganChase") only when it
#: is at least this long (a short word would match inside unrelated ones), and over at
#: most this many tokens.
_MIN_JOINED_ALIAS = 6
_MAX_JOINED_TOKENS = 4

REASON_TITLE_NAMES_NOT_SUBJECT = "title_does_not_name_the_subject"
REASON_TITLE_ABOUT_RELATED_REGISTRANT = "title_about_a_separately_registered_namesake"

#: "<Name>, a separate company …" — the writer says the opening entity is not the subject.
_DECLARED_OTHER_RE = re.compile(
    r"^\s{0,3}(?:the\s{1,3})?(?P<name>[A-Z][\w&.'’-]{1,40}(?:[ \t]{1,3}[A-Z][\w&.'’-]{1,40})"
    r"{0,3})\s{0,3},\s{0,3}(?:a|an)\s{1,3}(?:separate(?:ly\s{1,3}listed)?|unrelated|different"
    r"|third[- ]party|other|independent)\s{1,3}(?:company|entity|business|issuer|registrant"
    r"|firm|group)\b"
)


@dataclass(frozen=True)
class SubjectNames:
    """How the subject is named: its distinctive name words (folded), its ticker(s),
    and the folded two-word phrases of separately registered namesakes."""

    aliases: frozenset[str]
    tickers: tuple[str, ...] = ()
    related_phrases: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        subject_name: str | None,
        tickers: Iterable[str | None] = (),
        related_phrases: Iterable[str] = (),
    ) -> SubjectNames:
        from app.services.entities.passage_subject import subject_aliases

        return cls(
            aliases=subject_aliases(subject_name),
            tickers=tuple(sorted({t.strip() for t in tickers if t and len(t.strip()) >= 2})),
            related_phrases=tuple(sorted({p.strip().lower() for p in related_phrases if p})),
        )


def _words(text: str) -> list[str]:
    from app.services.discovery.identity import _fold  # noqa: PLC2701

    return re.findall(r"[a-z0-9&]+", _fold(text[:MAX_TEXT_CHARS]))


def _names_by_ticker(text: str, names: SubjectNames) -> bool:
    cut = text[:MAX_TEXT_CHARS]
    return any(
        re.search(rf"(?<![A-Za-z0-9.-]){re.escape(t)}(?![A-Za-z0-9-])", cut)
        for t in names.tickers
    )


#: A brand slug in ``trust.BRAND_ALIASES`` spans at most this many words ("whole-foods").
_MAX_BRAND_WORDS = 3


def _brand_names_subject(words: Sequence[str], i: int, names: SubjectNames) -> bool:
    """A brand of the subject starts at ``words[i]``: one of ``trust.BRAND_ALIASES``'
    slugs (one to three words, hyphen-joined) whose parent is the subject — the parent
    slug itself, or one of its distinctive words, is a subject alias ("rio-tinto")."""
    from app.services.web_research.trust import BRAND_ALIASES

    for n in range(1, _MAX_BRAND_WORDS + 1):
        if i + n > len(words):
            break
        parent = BRAND_ALIASES.get("-".join(words[i: i + n]))
        if not parent:
            continue
        if parent in names.aliases or any(
            len(part) >= 4 and part in names.aliases for part in parent.split("-")
        ):
            return True
    return False


def names_subject(text: str | None, names: SubjectNames) -> bool:
    """The text NAMES the subject: its ticker, a distinctive name word or a brand of it
    — never "the company" (that is an implicit subject) — other than only inside a
    separately registered namesake's name ("<Name> Aerospace")."""
    if not text or not names.aliases:
        return False
    if _names_by_ticker(text, names):
        return True
    words = _words(text)
    long_aliases = [a for a in names.aliases if len(a) >= _MIN_JOINED_ALIAS]
    for i, word in enumerate(words):
        if word in names.aliases or _brand_names_subject(words, i, names):
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if f"{word} {nxt}" in names.related_phrases:
                continue  # the namesake's name, not the subject's
            return True
        if long_aliases:
            # A name written as initials or run together: "J.P. Morgan" (j p morgan),
            # "JPMorganChase" (one token). Only a long alias, from a token boundary.
            joined = "".join(words[i: i + _MAX_JOINED_TOKENS])
            if any(joined.startswith(alias) for alias in long_aliases):
                return True
    return False


def names_only_related_registrant(text: str | None, names: SubjectNames) -> bool:
    """The subject's name word appears, but only inside a separately registered
    namesake's name ("Honeywell Aerospace selected for …" under Honeywell International)."""
    if not text or not names.related_phrases or names_subject(text, names):
        return False
    words = _words(text)
    return any(
        f"{word} {words[i + 1]}" in names.related_phrases
        for i, word in enumerate(words[:-1])
    )


def current_development_verdict(
    title: str | None, names: SubjectNames, *, issuer_origin: bool = False
) -> str | None:
    """None when a current-development document stands under the subject; else the
    reason it does not (rule 1). A document the platform VERIFIED as the subject's own
    (its origin is the run's issuer: an issuer domain, an issuer press release) is the
    subject's whatever its title says. With no name to tell the subject apart, nothing
    is judged (None)."""
    if issuer_origin:
        return None
    if not names.aliases and not names.tickers:
        return None
    if names_subject(title, names):
        return None
    if names_only_related_registrant(title, names):
        return REASON_TITLE_ABOUT_RELATED_REGISTRANT
    return REASON_TITLE_NAMES_NOT_SUBJECT


def filter_current_developments(
    items: Sequence[dict], names: SubjectNames, *, issuer_key: object = None
) -> tuple[list[dict], list[dict]]:
    """``(kept, withheld)`` — withheld items carry their title, domain and reason only.
    ``issuer_key`` is the run's issuer key (its company id): an item whose stored
    ``origin_key`` is that verified issuer (``trust.is_verified_issuer``) is kept. A
    source class alone ("company_press_release") never is: it may be another company's."""
    from app.services.web_research.trust import is_verified_issuer

    kept: list[dict] = []
    withheld: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        own = is_verified_issuer(item.get("origin_key"), issuer_key)
        reason = current_development_verdict(
            str(item.get("title") or ""), names, issuer_origin=own
        )
        if reason is None:
            kept.append(item)
        else:
            withheld.append({
                "title": item.get("title"),
                "domain": item.get("domain"),
                "reason": reason,
            })
    return kept, withheld


def declared_other_company(statement: str | None, names: SubjectNames) -> str | None:
    """Rule 3 — the opening entity the writer itself calls another company, or None."""
    match = _DECLARED_OTHER_RE.match((statement or "")[:600])
    if not match:
        return None
    name = match.group("name")
    if names_subject(name, names):
        return None  # "Acme, a separate company from its parent …": still Acme
    return name[:60]


def implicit_subject_unresolved(
    statement: str,
    passages: Sequence[tuple[str, bool]],
    names: SubjectNames,
) -> bool:
    """Rule 2. ``passages`` are ``(text, owned_by_subject)`` per cited item. True when no
    cited document is the subject's and neither the statement nor any cited passage
    names the subject: the statement's subject cannot be the research subject's."""
    if not names.aliases and not names.tickers:
        return False  # the subject cannot be told apart: no verdict
    if not passages or any(owned for _text, owned in passages):
        return False
    if names_subject(statement, names):
        return False
    return not any(names_subject(text, names) for text, _owned in passages)


__all__ = [
    "MAX_TEXT_CHARS",
    "REASON_TITLE_ABOUT_RELATED_REGISTRANT",
    "REASON_TITLE_NAMES_NOT_SUBJECT",
    "SubjectNames",
    "current_development_verdict",
    "declared_other_company",
    "filter_current_developments",
    "implicit_subject_unresolved",
    "names_only_related_registrant",
    "names_subject",
]
