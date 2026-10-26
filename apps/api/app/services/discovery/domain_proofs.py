"""Official-domain PROOFS — how a website becomes trusted as an issuer's, and the record of it.

Owner direction (2026-10-26): resolve an issuer's official domain GENERICALLY, from bytes the
platform fetched from authoritative sources, never because a page, a search provider or a model
said so. Every accepted domain is a **provenance-carrying fact** (:class:`DomainProof`): the
method, the source class, the document, the entity it was matched to and the hard identifiers
that matched — inspectable in the admin diagnostics.

METHODS (strongest first)
=========================
``directory_record``
    the exchange / regulator / registry record itself links the domain (existing sources).
``regulated_document_stated_website``
    a document fetched from an authoritative regulated venue (ASX, SEC EDGAR, FCA NSM) that
    NAMES the issuer states its website in the issuer's own contact block. The block must not be
    the share registry's, the auditor's, a broker's or a bank's.
``regulated_document_letterhead``
    two or more such documents print the same domain in the letterhead (or one that also names
    the ticker as its own).
``filing_contact_email``
    an issuer contact e-mail in such a document whose domain matches a stated website.
``legal_identifier_site_match``
    a candidate website carries a hard identifier of the independently verified listed entity
    (LEI, ISIN, or a company / business number that GLEIF or the exchange attributes to it) AND
    a compatible legal name. A ticker alone, a name alone, or a hostname that resembles the
    name never qualifies.

PURE functions here (extraction, checksums, decisions) are separate from the network
acquisition so every rule is testable without a network.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

METHOD_DIRECTORY = "directory_record"
METHOD_STATED_WEBSITE = "regulated_document_stated_website"
METHOD_LETTERHEAD = "regulated_document_letterhead"
METHOD_CONTACT_EMAIL = "filing_contact_email"
METHOD_IDENTIFIER_MATCH = "legal_identifier_site_match"

CONF_HIGH = "high"
CONF_MEDIUM = "medium"

STATUS_UNCONFIRMED = "stated_unconfirmed"
STATUS_SELF_CLAIM = "candidate_self_claim"
STATUS_VERIFIED = "verified_official"

#: Cap on the text one document contributes (a 300-page annual report is not scanned whole).
MAX_TEXT_CHARS = 60_000
LETTERHEAD_ZONE = 700

#: Free-mail and generic hosts: an e-mail on them names no company.
_FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "yahoo.com.au", "yahoo.co.uk", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "bigpond.com", "optusnet.com.au", "aol.com", "msn.com",
})

#: The issuer's OWN contact block.
_ISSUER_ANCHOR = re.compile(
    r"registered\s+office|head\s+office|principal\s+(?:place|office|executive)|corporate\s+office"
    r"|company\s+secretar|investor\s+relations|contact\s+(?:us|details)|corporate\s+directory"
    r"|company\s+details|corporate\s+information",
    re.IGNORECASE,
)
#: Blocks that belong to somebody ELSE and sit beside the issuer's in an annual report.
_THIRD_PARTY_ANCHOR = re.compile(
    r"share\s*regist|registry|registrar|transfer\s+agent|auditor|solicitor|lawyer|legal\s+advis"
    r"|bankers?\b|brokers?\b|nominated\s+advis|depositary|custodian|competent\s+person"
    r"|stock\s+exchange|securities\s+exchange|independent\s+(?:expert|geolog)|consultant"
    r"|media\s+(?:and|&|/)\s+investor|investor\s+(?:and|&)\s+media|media\s+enquir|media\s+contact"
    r"|for\s+further\s+information|further\s+information\s+(?:contact|please)|public\s+relations"
    r"|\bPR\b|\bIR\s+(?:adviser|advisor|firm)|corporate\s+advis|financial\s+advis"
    r"|investor\s+relations\s+(?:adviser|advisor|consult)|readcorporate|nominee",
    re.IGNORECASE,
)
_LABELLED = re.compile(
    r"(?:^|[\s|])(?:w|web|website|internet|url)\s*[:.]?\s+(?:https?://)?(?:www\.)?"
    r"([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b",
    re.IGNORECASE,
)
_WWW = re.compile(r"\bwww\.([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b", re.IGNORECASE)
#: "Cognex maintains a website at www.cognex.com" / "The Company operates an Internet site at …"
_PROSE_MAINTAINS = re.compile(
    r"\b(?:maintains?|operates?|hosts?)\s+(?:an?\s+)?(?:internet\s+)?(?:web\s*site|web\s*page|site)"
    r"\s*(?:address\s*)?(?:at|located\s+at|is|:)?\s*(?:https?://)?(?:www\.)?"
    r"([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b",
    re.IGNORECASE,
)
#: The registrant's OWN website, up to a few modifiers and ~120 characters before the address:
#: "Our corporate website address is http://www.x.com", "the Company's website may be accessed as
#: follows: www.x.com", "on our Investor website at www.ir.x.com".
_PROSE_OWN_SITE = re.compile(
    r"\b(?:our|the\s+company[’']?s?|its|the\s+registrant[’']?s?)\s+(?:[a-z-]+\s+){0,3}"
    r"(?:web\s*site|internet\s+site)\b[^.]{0,120}?(?:https?://|www\.|\()"
    r"(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[a-z0-9._%+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.IGNORECASE)
#: "our website is www.x.com" / "Internet address: x.com" / "investor relations website at …"
_PROSE_SITE = re.compile(
    r"(?:our|the\s+company[’']?s?|corporate|investor\s+relations|internet)\s+"
    r"(?:internet\s+)?(?:web\s*site|website|web\s*address|address)\s*"
    r"(?:address\s*)?(?:is|at|can\s+be\s+found\s+at|:|located\s+at)?\s*(?:https?://)?(?:www\.)?"
    r"([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DomainProof:
    """One accepted official domain, with how it was established."""

    domain: str
    method: str
    source_class: str
    source_url: str | None = None
    document_id: str | None = None
    matched_entity: str | None = None
    matched_identifiers: dict[str, str] = field(default_factory=dict)
    confidence: str = CONF_HIGH
    verified_at: str | None = None
    status: str = STATUS_VERIFIED
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocText:
    """The text of one fetched authoritative document, and where it came from."""

    ref: str
    url: str | None
    source_class: str  # exchange_announcement | regulator_filing | company_register
    text: str
    names_issuer: bool


@dataclass(frozen=True)
class EntityIds:
    """The independently verified listed entity — the anchor every proof is matched to."""

    name: str
    ticker: str | None = None
    isin: str | None = None
    lei: str | None = None
    registration_numbers: frozenset[str] = frozenset()
    cik: str | None = None


# --------------------------------------------------------------------------- #
# Identifier checksums — a hard identifier must be a VALID one
# --------------------------------------------------------------------------- #


def valid_abn(digits: str) -> bool:
    d = re.sub(r"\D", "", digits or "")
    if len(d) != 11:
        return False
    nums = [int(c) for c in d]
    nums[0] -= 1
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    return sum(n * w for n, w in zip(nums, weights, strict=True)) % 89 == 0


def valid_acn(digits: str) -> bool:
    d = re.sub(r"\D", "", digits or "")
    if len(d) != 9:
        return False
    nums = [int(c) for c in d]
    total = sum(n * w for n, w in zip(nums[:8], (8, 7, 6, 5, 4, 3, 2, 1), strict=True))
    return (10 - total % 10) % 10 == nums[8]


def _expand_letters(code: str) -> str:
    return "".join(str(int(c, 36)) for c in code)


def valid_isin(code: str) -> bool:
    c = (code or "").upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", c):
        return False
    digits = _expand_letters(c)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


def valid_lei(code: str) -> bool:
    c = (code or "").upper()
    return bool(re.fullmatch(r"[A-Z0-9]{18}\d{2}", c)) and int(_expand_letters(c)) % 97 == 1


_ABN = re.compile(r"\bABN[:\s.]*((?:\d[\s]?){11})(?!\d)", re.IGNORECASE)
_ACN = re.compile(r"\bACN[:\s.]*(\d{3}\s?\d{3}\s?\d{3})(?!\d)", re.IGNORECASE)
_COMPANY_NO = re.compile(
    r"(?:company|registered|registration)\s+(?:number|no\.?)\s*[:.]?\s*([A-Z]{0,2}\d{6,10})\b",
    re.IGNORECASE,
)
_ISIN_TOKEN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
_LEI_TOKEN = re.compile(r"\b([A-Z0-9]{18}\d{2})\b")


def identifiers_in(text: str | None) -> dict[str, set[str]]:
    """Valid hard identifiers a text prints: ``abn``, ``acn``, ``company_no``, ``isin``, ``lei``."""
    body = (text or "")[:MAX_TEXT_CHARS]
    out: dict[str, set[str]] = {"abn": set(), "acn": set(), "company_no": set(), "isin": set(),
                                "lei": set()}
    for m in _ABN.finditer(body):
        digits = re.sub(r"\D", "", m.group(1))
        if valid_abn(digits):
            out["abn"].add(digits)
    for m in _ACN.finditer(body):
        digits = re.sub(r"\D", "", m.group(1))
        if valid_acn(digits):
            out["acn"].add(digits)
    for m in _COMPANY_NO.finditer(body):
        out["company_no"].add(re.sub(r"\s", "", m.group(1)).upper().lstrip("0") or "0")
    for m in _ISIN_TOKEN.finditer(body):
        if valid_isin(m.group(1)):
            out["isin"].add(m.group(1))
    for m in _LEI_TOKEN.finditer(body):
        if valid_lei(m.group(1)):
            out["lei"].add(m.group(1))
    return out


def _norm_regno(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "").upper().lstrip("0") or "0"


# --------------------------------------------------------------------------- #
# Domain extraction
# --------------------------------------------------------------------------- #


def _registrable(host: str) -> str | None:
    from app.services.discovery.official_domains import _is_never_an_issuer, registrable_domain

    domain = registrable_domain((host or "").lower().rstrip("."))
    if not domain or "." not in domain or _is_never_an_issuer(domain) or domain in _FREE_MAIL:
        return None
    return domain


def _in_issuer_block(text: str, pos: int) -> bool:
    """The match at ``pos`` sits in the ISSUER's contact block (or the letterhead)."""
    ctx = text[max(0, pos - 500):pos]
    last_issuer = max((m.end() for m in _ISSUER_ANCHOR.finditer(ctx)), default=-1)
    last_third = max((m.end() for m in _THIRD_PARTY_ANCHOR.finditer(ctx)), default=-1)
    if last_issuer >= 0 and last_issuer > last_third:
        return True
    # The top of page one is the issuer's letterhead — unless a registry / auditor heading
    # already opened a third party's block.
    return pos < LETTERHEAD_ZONE and last_third < 0


def stated_websites(text: str | None, *, prose_anywhere: bool = False) -> list[str]:
    """Registrable domains the document states as ITS issuer's website, in first-seen order.

    Only labelled / ``www.`` / prose-stated sites count, and only inside the issuer's own
    contact block or the letterhead: the share registry's and the auditor's websites printed in
    the same corporate directory are never taken.
    """
    body = (text or "")[:MAX_TEXT_CHARS]
    found: list[str] = []
    for pattern in (_LABELLED, _WWW, _PROSE_SITE, _PROSE_MAINTAINS, _PROSE_OWN_SITE):
        for m in pattern.finditer(body):
            # A registrant's own prose ("our website is …") is its voice wherever it sits.
            is_prose = pattern in (_PROSE_SITE, _PROSE_MAINTAINS, _PROSE_OWN_SITE)
            if not (prose_anywhere and is_prose) and not _in_issuer_block(
                body, m.start()
            ):
                continue
            domain = _registrable(m.group(1))
            if domain and domain not in found:
                found.append(domain)
    return found


def contact_email_domains(text: str | None) -> list[str]:
    """Registrable domains of e-mail addresses printed in the issuer's own contact block."""
    body = (text or "")[:MAX_TEXT_CHARS]
    found: list[str] = []
    for m in _EMAIL.finditer(body):
        if not _in_issuer_block(body, m.start()):
            continue
        domain = _registrable(m.group(1))
        if domain and domain not in found:
            found.append(domain)
    return found


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


def _names(text: str, entity: EntityIds) -> bool:
    from app.services.discovery.identity import normalised_name

    full = normalised_name(entity.name)
    folded = re.sub(r"[^a-z0-9&]+", " ", (text or "").lower())
    pattern = r"(?<![a-z0-9])" + re.escape(full) + r"(?![a-z0-9])"
    return bool(full) and bool(re.search(pattern, folded))


def proofs_from_documents(
    docs: list[DocText], entity: EntityIds, *, verified_at: str
) -> list[DomainProof]:
    """Domains the authoritative DOCUMENTS establish for ``entity``. Pure.

    Documents that do not name the issuer are ignored. A domain is accepted when
    (a) two or more documents print it in the letterhead, or one does and also names the ticker
    as its own; or (b) a document states it as the issuer's website AND the issuer's contact
    e-mail domain matches (or a second document states it, or the document carries one of the
    entity's own hard identifiers); a stated website on its own from a regulator's filing for
    the entity (``regulator_filing``) is accepted as well, since the registrant stated it.
    """
    from app.services.discovery.official_domains import domains_in_letterhead

    ticker_ref = (
        re.compile(rf"\bASX\s*(?:code)?\s*[:\-]?\s*{re.escape(entity.ticker)}\b", re.IGNORECASE)
        if entity.ticker else None
    )
    letterhead: dict[str, list[DocText]] = {}
    self_ref: set[str] = set()
    stated: dict[str, list[DocText]] = {}
    emails: dict[str, list[DocText]] = {}
    ids_by_doc: dict[str, dict[str, set[str]]] = {}
    for doc in docs:
        if not doc.names_issuer:
            continue
        ids_by_doc[doc.ref] = identifiers_in(doc.text)
        heads = domains_in_letterhead(doc.text)
        if len(heads) <= 2:
            for d in heads:
                letterhead.setdefault(d, []).append(doc)
                if ticker_ref and ticker_ref.search(doc.text):
                    self_ref.add(d)
        for d in stated_websites(doc.text, prose_anywhere=doc.source_class == "regulator_filing"):
            stated.setdefault(d, []).append(doc)
        for d in contact_email_domains(doc.text):
            emails.setdefault(d, []).append(doc)

    def own_ids(doc: DocText) -> dict[str, str]:
        ids = ids_by_doc.get(doc.ref, {})
        matched: dict[str, str] = {}
        if entity.isin and entity.isin in ids.get("isin", set()):
            matched["isin"] = entity.isin
        if entity.lei and entity.lei in ids.get("lei", set()):
            matched["lei"] = entity.lei
        for kind in ("abn", "acn", "company_no"):
            for value in ids.get(kind, set()):
                if _norm_regno(value) in entity.registration_numbers:
                    matched[kind] = value
        return matched

    out: dict[str, DomainProof] = {}

    def add(domain: str, method: str, doc: DocText, count: int, matched: dict[str, str],
            confidence: str, detail: str, status: str = STATUS_VERIFIED) -> None:
        if domain in out:
            return
        out[domain] = DomainProof(
            domain=domain, method=method, source_class=doc.source_class, source_url=doc.url,
            document_id=doc.ref, matched_entity=entity.name, matched_identifiers=matched,
            confidence=confidence, verified_at=verified_at, detail=detail, status=status,
        )

    for domain, hits in letterhead.items():
        distinct = {h.ref for h in hits}
        if len(distinct) >= 2 or domain in self_ref:
            matched = {"ticker": entity.ticker} if domain in self_ref and entity.ticker else {}
            add(domain, METHOD_LETTERHEAD, hits[0], len(distinct), matched, CONF_HIGH,
                f"printed in the letterhead of {len(distinct)} document(s) lodged under the issuer")
    for domain, hits in stated.items():
        distinct = {h.ref for h in hits}
        mail = {h.ref for h in emails.get(domain, [])}
        doc = hits[0]
        matched = own_ids(doc)
        regulator = doc.source_class == "regulator_filing"
        if mail:
            add(domain, METHOD_CONTACT_EMAIL, doc, len(distinct),
                {**matched, "contact_email_domain": domain}, CONF_HIGH,
                "stated website and a contact e-mail on the same domain, in a document that "
                "names the issuer")
        elif len(distinct) >= 2:
            add(domain, METHOD_STATED_WEBSITE, doc, len(distinct), matched, CONF_HIGH,
                f"stated as the issuer's website in {len(distinct)} regulated documents")
        elif matched:
            add(domain, METHOD_STATED_WEBSITE, doc, 1, matched, CONF_HIGH,
                "stated website in a regulated document that carries the entity's own identifier")
        else:
            # One regulator-filed document (an SEC filing lodged under the CIK) is the issuer's
            # own statement. A single exchange announcement is not enough on its own: it stays
            # an unconfirmed candidate until a second document or an identifier corroborates it.
            add(domain, METHOD_STATED_WEBSITE, doc, 1,
                {"cik": entity.cik} if regulator and entity.cik else {}, CONF_MEDIUM,
                "stated as the issuer's website in a regulated document that names the issuer",
                STATUS_VERIFIED if regulator else STATUS_UNCONFIRMED)
    return list(out.values())


def proof_from_site(
    domain: str, page_text: str, entity: EntityIds, *, url: str | None, verified_at: str
) -> DomainProof | None:
    """Rule E. A candidate website is the issuer's only if it prints a hard identifier of the
    independently verified entity AND a compatible legal name. Pure.

    Ticker alone, name alone and hostname resemblance never qualify.
    """
    reg = _registrable(domain)
    if reg is None or not _names(page_text, entity):
        return None
    ids = identifiers_in(page_text)
    matched: dict[str, str] = {}
    if entity.isin and entity.isin in ids["isin"]:
        matched["isin"] = entity.isin
    if entity.lei and entity.lei in ids["lei"]:
        matched["lei"] = entity.lei
    for kind in ("abn", "acn", "company_no"):
        for value in ids[kind]:
            if _norm_regno(value) in entity.registration_numbers:
                matched[kind] = value
    if not matched:
        return None
    return DomainProof(
        domain=reg, method=METHOD_IDENTIFIER_MATCH, source_class="candidate_site", source_url=url,
        matched_entity=entity.name, matched_identifiers=matched, confidence=CONF_HIGH,
        verified_at=verified_at, status=STATUS_SELF_CLAIM,
        detail="the site prints the verified entity's legal name and a hard identifier "
               "(public identifiers: a lookalike can print them, so this is a self-claim and "
               "is never treated as the issuer's voice)",
    )


__all__ = [
    "CONF_HIGH",
    "CONF_MEDIUM",
    "METHOD_CONTACT_EMAIL",
    "METHOD_DIRECTORY",
    "METHOD_IDENTIFIER_MATCH",
    "METHOD_LETTERHEAD",
    "METHOD_STATED_WEBSITE",
    "DocText",
    "DomainProof",
    "EntityIds",
    "contact_email_domains",
    "identifiers_in",
    "proof_from_site",
    "proofs_from_documents",
    "stated_websites",
    "valid_abn",
    "valid_acn",
    "valid_isin",
    "valid_lei",
]
