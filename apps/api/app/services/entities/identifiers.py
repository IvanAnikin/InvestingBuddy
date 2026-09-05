"""Identifier semantics for the entity master — V3.2 Slice 2.1.

WHY A VALIDATOR AND NOT A STRING COLUMN
=======================================
Today CIK, LEI and ISIN exist only as transient fields on ``ProfileEnrichment``
and ``FreeRealSnapshot``: assembled per run, never persisted, never checked. The
moment they become durable and joinable, an unchecked one is a liability rather
than an improvement — a mistyped LEI stored against the wrong issuer is
indistinguishable from a correct one, and every later join propagates it.

So a structurally invalid identifier is **refused**, not stored with a low
confidence. "Recorded with confidence 0.3" does not stop the next reader from
joining on it; a ``ValueError`` at the boundary does. This is the same fail-closed
rule ``fact_scope`` applies to scope and ``document_period`` applies to periods,
applied to identity.

Two of these schemes carry a real checksum, and both are implemented here rather
than deferred to a provider:

* **LEI** (ISO 17442) — ISO 7064 MOD 97-10 over the letter-expanded 20 characters.
  A single mistyped character fails with probability ~96/97.
* **ISIN** (ISO 6166) — a Luhn check digit over the letter-expanded first eleven
  characters.

WHAT EACH IDENTIFIER MAY DESCRIBE
=================================
A scheme declares its subject, and attaching an identifier to the wrong kind of
subject raises. An ISIN identifies an *instrument*; a LEI identifies a *legal
person*. Storing an ISIN on a legal entity would make "the entity's ISIN"
meaningful for a single-security issuer and silently wrong for every cross-listed
or multi-class one — which is the entire class of issuer the entity master exists
for.

FIGI, AND WHY ITS CHECK DIGIT IS NOT VALIDATED
==============================================
Whether OpenFIGI is used at all is OPEN DECISION #10 and it is the user's. This
module makes a FIGI *representable* without making it *obtainable*: the structural
rules are checked, no source populates one, and no OpenFIGI client exists in the
tree.

The check digit is deliberately **not** validated, and a caller must not read a
stored FIGI as checksum-verified. The FIGI check-digit variant could not be
confirmed here against a published test vector, and a checksum implementation
that is subtly wrong rejects *valid* identifiers — a validator that refuses good
data is worse than an honest structural check that admits what it did not verify.

CUSIP IS ABSENT ON PURPOSE
==========================
CUSIP is licensed data from CUSIP Global Services. Storing one is a licensing
decision rather than a technical one, and ISIN covers the same need for every
issuer in the regression set. Adding the scheme is a governance change, not a
one-line vocabulary edit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Subjects ─────────────────────────────────────────────────────────────── #

#: The identifier describes a legal person — the issuer.
SUBJECT_LEGAL_ENTITY = "legal_entity"
#: The identifier describes an instrument the issuer issued.
SUBJECT_SECURITY = "security"

SUBJECTS: frozenset[str] = frozenset({SUBJECT_LEGAL_ENTITY, SUBJECT_SECURITY})

# ── Schemes ──────────────────────────────────────────────────────────────── #

SCHEME_LEI = "lei"
SCHEME_CIK = "cik"
SCHEME_COMPANY_REGISTER = "company_register"
SCHEME_ISIN = "isin"
SCHEME_FIGI = "figi"

#: The scope key used for schemes whose values are unique across the whole world.
#:
#: It is a literal ``*`` rather than NULL because the uniqueness guarantee is
#: expressed as a partial unique index over ``(scheme, value, scope_key)``, and
#: PostgreSQL treats NULLs as DISTINCT in a unique index. A nullable scope column
#: would therefore have allowed two rows with the same LEI to coexist — silently
#: permitting the exact collision the index exists to prevent.
GLOBAL_SCOPE = "*"

_LEI_RE = re.compile(r"^[0-9A-Z]{18}[0-9]{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")
_CIK_RE = re.compile(r"^[0-9]{1,10}$")
_REGISTER_RE = re.compile(r"^[0-9A-Z][0-9A-Z\-./]{0,38}$")
_JURISDICTION_RE = re.compile(r"^[A-Z]{2}$")
#: FIGI: two consonants, a literal ``G``, eight consonant/digit characters, then a
#: check digit. The consonant restriction is what keeps a FIGI from ever colliding
#: with an ISIN's country prefix.
_FIGI_RE = re.compile(
    r"^[BCDFGHJKLMNPQRSTVWXYZ]{2}G[BCDFGHJKLMNPQRSTVWXYZ0-9]{8}[0-9]$"
)
#: FIGI prefixes reserved because they would read as ISO 3166 country codes.
_FIGI_FORBIDDEN_PREFIXES: frozenset[str] = frozenset(
    {"BS", "BM", "GG", "GB", "GH", "KY", "VG"}
)

_VALUE_MAX = 40


@dataclass(frozen=True)
class IdentifierScheme:
    """One recognised identifier scheme.

    ``checksum_verified`` is part of the contract rather than a comment: a reader
    deciding how much to trust a stored value needs to know whether anything
    beyond a shape was checked.
    """

    key: str
    label: str
    subject: str
    #: True when the scheme's own check digits were verified on write.
    checksum_verified: bool
    #: True when a value is unique across the whole world, so ``scope_key`` is
    #: ``*``. False when it is unique only within a jurisdiction.
    globally_unique: bool
    #: True when a two-letter jurisdiction must accompany the value.
    requires_jurisdiction: bool


SCHEMES: dict[str, IdentifierScheme] = {
    SCHEME_LEI: IdentifierScheme(
        key=SCHEME_LEI,
        label="Legal Entity Identifier (ISO 17442)",
        subject=SUBJECT_LEGAL_ENTITY,
        checksum_verified=True,
        globally_unique=True,
        requires_jurisdiction=False,
    ),
    SCHEME_CIK: IdentifierScheme(
        key=SCHEME_CIK,
        label="SEC EDGAR Central Index Key",
        subject=SUBJECT_LEGAL_ENTITY,
        # A CIK has no check digit. Normalisation to ten digits is all that can
        # be done, and this field says so rather than implying more.
        checksum_verified=False,
        globally_unique=True,
        requires_jurisdiction=False,
    ),
    SCHEME_COMPANY_REGISTER: IdentifierScheme(
        key=SCHEME_COMPANY_REGISTER,
        label="National company-register number",
        subject=SUBJECT_LEGAL_ENTITY,
        checksum_verified=False,
        # A Danish CVR and a UK company number may be the same digits. Unique
        # within a register, never across them.
        globally_unique=False,
        requires_jurisdiction=True,
    ),
    SCHEME_ISIN: IdentifierScheme(
        key=SCHEME_ISIN,
        label="International Securities Identification Number (ISO 6166)",
        subject=SUBJECT_SECURITY,
        checksum_verified=True,
        globally_unique=True,
        requires_jurisdiction=False,
    ),
    SCHEME_FIGI: IdentifierScheme(
        key=SCHEME_FIGI,
        label="Financial Instrument Global Identifier",
        subject=SUBJECT_SECURITY,
        # Structural only — see the module docstring. Nothing sources a FIGI
        # while OPEN DECISION #10 is open.
        checksum_verified=False,
        globally_unique=True,
        requires_jurisdiction=False,
    ),
}

LEGAL_ENTITY_SCHEMES: frozenset[str] = frozenset(
    k for k, s in SCHEMES.items() if s.subject == SUBJECT_LEGAL_ENTITY
)
SECURITY_SCHEMES: frozenset[str] = frozenset(
    k for k, s in SCHEMES.items() if s.subject == SUBJECT_SECURITY
)


# ── Checksums ────────────────────────────────────────────────────────────── #


def _expand_letters(value: str) -> str:
    """Map ``A``-``Z`` to ``10``-``35`` and leave digits alone.

    Both ISO 7064 MOD 97-10 (LEI) and the ISIN Luhn variant are defined over this
    expansion, so it exists once.
    """
    out: list[str] = []
    for ch in value:
        if ch.isdigit():
            out.append(ch)
        else:
            out.append(str(ord(ch) - 55))
    return "".join(out)


def lei_checksum_valid(value: str) -> bool:
    """ISO 7064 MOD 97-10: the letter-expanded 20 characters mod 97 must be 1."""
    if not _LEI_RE.match(value):
        return False
    return int(_expand_letters(value)) % 97 == 1


def lei_check_digits(base18: str) -> str:
    """Return the two MOD 97-10 check digits for an 18-character LEI base.

    Exists so tests can construct structurally valid LEIs from the generation
    rule instead of transcribing a half-remembered real one. An invented "real"
    identifier is exactly the sort of unverified factual claim this platform
    refuses everywhere else, and a wrong one would make the test assert the bug.
    """
    base = base18.strip().upper()
    if len(base) != 18 or not base.isalnum():
        raise ValueError("An LEI base must be exactly 18 alphanumeric characters.")
    remainder = int(_expand_letters(base + "00")) % 97
    return f"{98 - remainder:02d}"


def isin_check_digit(body11: str) -> int:
    """Return the ISO 6166 check digit for the first eleven ISIN characters.

    Luhn over the letter-expanded body, doubling from the rightmost digit.
    """
    body = body11.strip().upper()
    if len(body) != 11:
        raise ValueError("An ISIN body must be exactly 11 characters.")
    digits = _expand_letters(body)
    total = 0
    for index, ch in enumerate(reversed(digits)):
        digit = int(ch)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def isin_checksum_valid(value: str) -> bool:
    if not _ISIN_RE.match(value):
        return False
    return isin_check_digit(value[:11]) == int(value[11])


# ── Normalisation ────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class NormalizedIdentifier:
    """A validated identifier, ready to be persisted.

    ``scope_key`` is what the uniqueness index is built on and is never NULL.
    """

    scheme: str
    value: str
    value_normalized: str
    scope_key: str
    subject: str
    jurisdiction: str | None
    checksum_verified: bool


def normalize_identifier(
    scheme: str,
    value: str,
    *,
    jurisdiction: str | None = None,
) -> NormalizedIdentifier:
    """Validate and normalise one identifier, or raise ``ValueError``.

    Raising is the point. A caller that cannot produce a well-formed identifier
    has learned something real about its source, and recording the malformed value
    would hide it behind a row that later joins as if it were sound.
    """
    key = (scheme or "").strip().lower()
    spec = SCHEMES.get(key)
    if spec is None:
        raise ValueError(
            f"Unknown identifier scheme {scheme!r}. Recognised: "
            f"{', '.join(sorted(SCHEMES))}. Adding one is a deliberate change to "
            "the vocabulary, not a caller-side decision."
        )

    raw = (value or "").strip()
    if not raw:
        raise ValueError(f"An empty value is not a {spec.label}.")
    if len(raw) > _VALUE_MAX:
        raise ValueError(
            f"{spec.label} value is {len(raw)} characters; the maximum is {_VALUE_MAX}."
        )

    juris = (jurisdiction or "").strip().upper() or None
    if spec.requires_jurisdiction:
        if juris is None:
            raise ValueError(
                f"{spec.label} requires a jurisdiction: the same number identifies "
                "different companies in different registers."
            )
        if not _JURISDICTION_RE.match(juris):
            raise ValueError(
                f"Jurisdiction {jurisdiction!r} is not a two-letter "
                "ISO 3166-1 alpha-2 code."
            )
    elif juris is not None and not _JURISDICTION_RE.match(juris):
        raise ValueError(
            f"Jurisdiction {jurisdiction!r} is not a two-letter "
            "ISO 3166-1 alpha-2 code."
        )

    normalized = _normalize_value(key, raw)
    scope = GLOBAL_SCOPE if spec.globally_unique else (juris or GLOBAL_SCOPE)
    return NormalizedIdentifier(
        scheme=key,
        value=raw,
        value_normalized=normalized,
        scope_key=scope,
        subject=spec.subject,
        jurisdiction=juris,
        checksum_verified=spec.checksum_verified,
    )


def _normalize_value(scheme: str, raw: str) -> str:
    upper = raw.upper().replace(" ", "")

    if scheme == SCHEME_LEI:
        if not _LEI_RE.match(upper):
            raise ValueError(
                f"{upper!r} is not an ISO 17442 LEI: 18 alphanumeric characters "
                "followed by two check digits."
            )
        if not lei_checksum_valid(upper):
            raise ValueError(
                f"LEI {upper!r} fails its ISO 7064 MOD 97-10 checksum. It is "
                "refused rather than stored: a mistyped LEI is indistinguishable "
                "from a correct one once it is joinable."
            )
        return upper

    if scheme == SCHEME_ISIN:
        if not _ISIN_RE.match(upper):
            raise ValueError(
                f"{upper!r} is not an ISO 6166 ISIN: a two-letter prefix, nine "
                "alphanumeric characters and a check digit."
            )
        if not isin_checksum_valid(upper):
            raise ValueError(
                f"ISIN {upper!r} fails its check digit (expected "
                f"{isin_check_digit(upper[:11])}). Refused rather than stored."
            )
        return upper

    if scheme == SCHEME_CIK:
        stripped = upper.removeprefix("CIK").lstrip("-").lstrip()
        if not _CIK_RE.match(stripped):
            raise ValueError(
                f"{raw!r} is not a CIK: one to ten digits. A CIK is never derived "
                "from a ticker string — that is the Boeing bug."
            )
        if int(stripped) == 0:
            raise ValueError("CIK 0 does not identify a filer.")
        return stripped.zfill(10)

    if scheme == SCHEME_FIGI:
        if upper[:2] in _FIGI_FORBIDDEN_PREFIXES:
            raise ValueError(
                f"{upper!r} starts with {upper[:2]!r}, which FIGI reserves because "
                "it reads as an ISO 3166 country code."
            )
        if not _FIGI_RE.match(upper):
            raise ValueError(
                f"{upper!r} is not a FIGI: two consonants, 'G', eight "
                "consonant/digit characters and a check digit. Note that the FIGI "
                "check digit itself is NOT validated here — see the module "
                "docstring."
            )
        return upper

    if scheme == SCHEME_COMPANY_REGISTER:
        collapsed = upper.replace(" ", "")
        if not _REGISTER_RE.match(collapsed):
            raise ValueError(
                f"{raw!r} is not a plausible company-register number: "
                "letters, digits and -./ only."
            )
        return collapsed

    # Unreachable while SCHEMES and this function agree; a new scheme that forgets
    # to normalise must not silently fall through to "store whatever arrived".
    raise ValueError(f"Scheme {scheme!r} has no normalisation rule.")


def subject_for_scheme(scheme: str) -> str:
    """Return which kind of row a scheme may attach to, or raise."""
    spec = SCHEMES.get((scheme or "").strip().lower())
    if spec is None:
        raise ValueError(f"Unknown identifier scheme {scheme!r}.")
    return spec.subject
