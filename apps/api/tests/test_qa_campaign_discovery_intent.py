"""Cross-sector QA campaign 2026-11 — Discovery intent and lead-name regressions.

Three generic defects found by running owner theses against production:

1. A materials thesis written with coordinated adjectives ("critical or specialty materials
   required by semiconductors, electrification, defence") was read as a DEFENCE thesis with
   a hard industry constraint, because only the contiguous "critical materials" matched.
2. Selector auto-fill took a theme's FIRST sector/industry rather than what the text says:
   "payments or fintech" filled "Banks", and "Electrical Equipment" was paired with
   "Utilities". The owner workbench sends the auto-filled sector as an explicit filter.
3. A security identifier printed beside an issuer (a German WKN) was taken as the issuer's
   name, so a real listing was rejected as a name mismatch (live run d15a74c4, SIX:BION).

No issuer is special-cased: every case is a shape of text.
"""

from __future__ import annotations

import pytest

from app.api.v1.market_discovery import _autofill_from_intent
from app.services.discovery.intent import build_intent
from app.services.market_thesis_parser import parse_thesis
from app.services.sector_taxonomy import normalize_sector
from app.services.web_research.candidate_extract import (
    _name_ok,
    extract_mentions,
    is_security_code,
    listing_name,
)

MATERIALS_THESIS = (
    "Listed smaller companies supplying critical or specialty materials required by "
    "semiconductors, electrification, defence or advanced manufacturing, with identifiable "
    "project or capacity catalysts."
)


def _industry_constraint(intent) -> list[str]:
    return [list(c.requested) for c in intent.constraints() if c.key == "industry"][0]


# ── 1. Materials theses written with coordinated adjectives ──────────────── #


@pytest.mark.parametrize(
    "phrase",
    [
        "critical or specialty materials",
        "critical and strategic minerals",
        "critical, strategic or rare metals",
    ],
)
def test_coordinated_material_classes_make_a_materials_thesis(phrase: str) -> None:
    text = f"Listed companies supplying {phrase} required by semiconductors and defence."
    intent = build_intent(text, parsed=parse_thesis(text))
    assert "critical_materials" in intent.themes
    assert intent.materials_role == "product"
    assert "defense" not in intent.themes
    assert "defence" in intent.end_markets


def test_end_markets_of_a_materials_thesis_are_not_its_industry() -> None:
    intent = build_intent(MATERIALS_THESIS, parsed=parse_thesis(MATERIALS_THESIS))
    industry = _industry_constraint(intent)
    assert "defense" not in industry
    assert "critical_materials" in industry
    assert set(intent.end_markets) >= {"defence", "semiconductors", "electrification"}
    assert "Aerospace & Defense" not in intent.industries
    assert "Industrials" not in intent.sectors


def test_a_company_kind_still_wins_over_a_material_input() -> None:
    text = "US semiconductor companies dependent on critical minerals"
    intent = build_intent(text, parsed=parse_thesis(text))
    assert "semiconductors" in intent.themes
    assert intent.materials_role == "input"


def test_a_defence_thesis_is_still_a_defence_thesis() -> None:
    text = "European defence companies benefiting from NATO spending"
    intent = build_intent(text, parsed=parse_thesis(text))
    assert intent.themes[0] == "defense"
    assert "defense" in _industry_constraint(intent)


# ── 2. Auto-fill follows the text, and sector agrees with industry ──────── #


def _autofill(text: str):
    parsed = parse_thesis(text)
    return _autofill_from_intent(parsed, build_intent(text, parsed=parsed))


def test_payments_thesis_autofills_fintech_not_banks() -> None:
    theme, sector, industry = _autofill(
        "Listed small and mid-cap payments or fintech infrastructure companies with "
        "transaction growth."
    )
    assert (sector, industry) == ("Financials", "Financial Technology")


def test_a_bank_thesis_still_autofills_banks() -> None:
    _, sector, industry = _autofill("US regional banks with deposit growth")
    assert (sector, industry) == ("Financials", "Banks")


def test_electrical_equipment_is_never_paired_with_utilities() -> None:
    theme, sector, industry = _autofill(
        "Small and mid-cap listed companies supplying electrical equipment, cooling, power "
        "management or grid infrastructure for AI data-centre expansion."
    )
    assert industry == "Electrical Equipment"
    assert sector == "Industrials"


def test_a_utilities_thesis_still_autofills_utilities() -> None:
    _, sector, industry = _autofill("Utilities with grid exposure in Europe")
    assert (sector, industry) == ("Utilities", "Utilities")


@pytest.mark.parametrize(
    "text",
    [
        "Listed small and mid-cap payments or fintech infrastructure companies",
        "Small and mid-cap suppliers of electrical equipment and grid infrastructure",
        "Japanese industrial robotics companies",
        "European watch producers",
        MATERIALS_THESIS,
    ],
)
def test_autofilled_sector_is_the_industrys_own_sector(text: str) -> None:
    _, sector, industry = _autofill(text)
    if industry:
        assert normalize_sector(industry) == sector


def test_a_demoted_theme_does_not_autofill_its_sector() -> None:
    theme, sector, industry = _autofill(MATERIALS_THESIS)
    assert theme == "critical_materials"
    assert sector == "Materials"
    assert industry is None


# ── 3. Security identifiers are never issuer names ───────────────────────── #


@pytest.mark.parametrize(
    "token", ["A0NFN3", "840400", "CH0038389992", "B0YBKJ7", "037833100", "723610"]
)
def test_security_codes_are_recognised(token: str) -> None:
    assert is_security_code(token)


@pytest.mark.parametrize("token", ["LYNAS", "PH1", "IGO", "3M", "2026", "BHP", "Cellectis"])
def test_words_and_short_tickers_are_not_security_codes(token: str) -> None:
    assert not is_security_code(token)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("BB Biotech AG WKN A0NFN3 (SIX: BION)", "BB Biotech AG"),
        ("BB Biotech AG (WKN: A0NFN3, ISIN CH0038389992) (SIX: BION)", "BB Biotech AG"),
        ("BB Biotech AG | A0NFN3 | (SIX: BION)", "BB Biotech AG"),
        ("Siemens AG, WKN 723610, (XETRA: SIE)", "Siemens AG"),
        ("3M Company (NYSE: MMM)", "3M Company"),
        ("Pharma 1 Holdings (ASX: PH1)", "Pharma 1 Holdings"),
        ("IGO (ASX: IGO)", "IGO"),
    ],
)
def test_the_name_before_a_listing_skips_security_codes(text: str, expected: str) -> None:
    assert listing_name(text, text.rindex("(")) == expected


def test_a_bare_security_code_is_not_a_lead_name() -> None:
    text = "Top holdings: A0NFN3 (SIX: BION)"
    name = listing_name(text, text.rindex("("))
    assert not _name_ok(name, has_identifier=True)
    assert not _name_ok("A0NFN3", has_identifier=True)
    assert not _name_ok("CH0038389992 B0YBKJ7", has_identifier=True)


def test_extracted_mentions_never_carry_a_security_code_as_name() -> None:
    paragraphs = [
        "BB Biotech AG (WKN: A0NFN3) (SIX: BION) invests in biotechnology companies.",
        "A0NFN3 (SIX: BION) closed higher on the day.",
    ]
    mentions, _stats = extract_mentions(paragraphs)
    names = {m.name for m in mentions}
    assert names == {"BB Biotech AG"}


@pytest.mark.parametrize("space", [" ", "\u3000", "\u2003", "\u2009"])
def test_a_long_whitespace_run_cannot_stall_name_extraction(space: str) -> None:
    # Security review: over a whole paragraph the trailing-code pattern backtracked in
    # cubic time on a long whitespace run (400 spaces: 2.7s; 2,000: minutes).
    import time

    text = "the results were published." + space * 2000 + "BB Biotech AG (ISIN CH0038389992) (SIX: BION)"
    started = time.perf_counter()
    name = listing_name(text, text.rindex("("))
    mentions, _stats = extract_mentions([text])
    assert time.perf_counter() - started < 1.0
    assert name == "BB Biotech AG"
    assert {m.name for m in mentions} == {"BB Biotech AG"}


# ── Independent review round 1 ───────────────────────────────────────────── #


@pytest.mark.parametrize(
    "text,theme",
    [
        ("Defence primes exposed to critical minerals export controls.", "defense"),
        ("European defence and aerospace exposure to critical minerals.", "defense"),
        ("Aerospace and defence suppliers of advanced materials and composites", "defense"),
    ],
)
def test_an_exposure_to_critical_minerals_keeps_the_company_kind(text: str, theme: str) -> None:
    intent = build_intent(text, parsed=parse_thesis(text))
    assert theme in intent.themes
    assert theme in _industry_constraint(intent)
    assert intent.materials_role != "product"
    assert "Metals & Mining" not in intent.industries


@pytest.mark.parametrize(
    "text",
    ["Specialty chemicals and advanced materials companies.", "Engineered materials for EV batteries."],
)
def test_a_class_without_critical_strategic_or_rare_is_not_a_miners_thesis(text: str) -> None:
    intent = build_intent(text, parsed=parse_thesis(text))
    assert "critical_materials" not in intent.themes
    assert "Metals & Mining" not in intent.industries


def test_keyword_order_survives_punctuation() -> None:
    _, sector, _ = _autofill("Utilities, with regulated grid exposure, in Europe.")
    assert sector == "Utilities"
    _, _, industry = _autofill("Banks, payments and fintech companies.")
    assert industry == "Banks"


@pytest.mark.parametrize("token", ["B2GOLD", "E2OPEN", "4IMPRINT", "1COMMS"])
def test_names_with_one_digit_are_not_security_codes(token: str) -> None:
    assert not is_security_code(token)
    text = f"{token} (NYSE: XXX)"
    assert listing_name(text, text.rindex("(")) == token


def test_a_labelled_sedol_is_stripped() -> None:
    text = "Fortescue SEDOL 6086253 (ASX: FMG)"
    assert listing_name(text, text.rindex("(")) == "Fortescue"


def test_ambiguous_words_do_not_become_an_industry() -> None:
    assert normalize_sector("transformer") is None
    assert normalize_sector("transmission") is None
