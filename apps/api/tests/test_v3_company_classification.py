"""Classification, from the regulator's code to the playbook that gets selected.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
=======================================
The defect this suite exists for was never a wrong answer. Every layer was individually
correct: the SEC returned SIC 2836, the enrichment derived a healthcare sector, the
taxonomy knew "Biotechnology", and the biotech playbook declared it. The company still
got the generic methodology, because nothing carried the answer from the layer that
produced it to the layer that needed it.

So the load-bearing assertions here are the **handoffs**, not the lookups:

* a SIC code reaches a canonical industry (``test_sic_*``);
* a canonical industry reaches a playbook (``test_selection_*``);
* the answer survives being written down and read back (``test_repeat_run_*``);
* a weaker source cannot destroy a stronger one on the way (``test_supersede_*``).

A test that only asserted ``industry_for_sic("2836") == "Biotechnology"`` would have
passed on the broken code too.
"""

from __future__ import annotations

import pytest

from app.integrations.financial_data_provider import SourceTier
from app.services.classification.resolver import resolve_classification
from app.services.classification.sic import industry_for_sic, normalize_sic
from app.services.playbooks import select as select_playbooks
from app.services.sector_taxonomy import INDUSTRY_TO_SECTOR

T2 = SourceTier.T2_regulator_or_gov.value
T5 = SourceTier.T5_api_aggregator.value
T6 = SourceTier.T6_model_estimate.value


# ---------------------------------------------------------------------------
# 1. SIC → canonical industry, by code
# ---------------------------------------------------------------------------

#: (sic, canonical industry, canonical sector, expected playbook ids).
#:
#: Deliberately spans industry families rather than proving biotech five times: a
#: mapping table validated on one row is a table nobody has checked the shape of.
_FAMILIES = [
    ("2836", "Biotechnology", "Healthcare", ["biotech"]),          # Moderna's code
    ("8731", "Biotechnology", "Healthcare", ["biotech"]),          # contract research
    ("2834", "Pharmaceuticals", "Healthcare", ["biotech"]),        # Pfizer's code
    ("6022", "Banks", "Financials", ["banks_financials"]),         # state bank
    ("6021", "Banks", "Financials", ["banks_financials"]),         # national bank
    ("3674", "Semiconductors", "Technology", ["semiconductors"]),  # NVIDIA's code
    ("3721", "Aerospace & Defense", "Industrials", ["industrial_defense"]),
    ("3760", "Aerospace & Defense", "Industrials", ["industrial_defense"]),
    ("3911", "Watches & Jewelry", "Consumer Discretionary", ["luxury"]),
]


@pytest.mark.parametrize("sic,industry,sector,_playbooks", _FAMILIES)
def test_sic_code_maps_to_canonical_industry_and_sector(sic, industry, sector, _playbooks):
    assert industry_for_sic(sic) == industry
    assert INDUSTRY_TO_SECTOR.get(industry) == sector


@pytest.mark.parametrize("sic,industry,sector,playbooks", _FAMILIES)
def test_a_sic_code_reaches_the_playbook_it_should(sic, industry, sector, playbooks):
    """The end-to-end handoff: regulator code in, methodology out.

    This is the assertion that would have failed before the fix, with every individual
    lookup in it already passing.
    """
    resolved = resolve_classification(sic_code=sic)
    selection = select_playbooks(
        sector=resolved.matching_sector, industry=resolved.industry
    )
    assert sorted(p.playbook_id for p in selection.playbooks) == sorted(playbooks)


def test_a_sic_outside_the_playbook_families_selects_nothing():
    """7372 is prepackaged software — a real, common, correctly-classified company that
    no playbook covers.

    Selecting nothing here is the *right* answer, and the one worth pinning: the failure
    mode this whole layer risks is a company acquiring the nearest-looking methodology,
    which produces a confident analysis of the wrong questions.
    """
    resolved = resolve_classification(sic_code="7372")
    assert resolved.industry == "Software & IT Services"
    selection = select_playbooks(
        sector=resolved.matching_sector, industry=resolved.industry
    )
    assert selection.playbooks == ()
    assert "no playbook declares this company" in selection.reason


def test_an_unmapped_sic_stays_unknown_and_is_not_guessed():
    resolved = resolve_classification(sic_code="9995")
    assert resolved.industry is None
    assert resolved.sector is None
    assert not resolved.is_known
    assert any("not guessed" in n for n in resolved.notes)


@pytest.mark.parametrize(
    "raw,expected",
    [("2836", "2836"), (2836, "2836"), (" 836 ", "0836"), ("", None),
     (None, None), ("28-36", None), ("biotech", None)],
)
def test_sic_normalisation_refuses_what_is_not_a_code(raw, expected):
    assert normalize_sic(raw) == expected


def test_health_services_are_not_biotechnology():
    """SIC 8060 is hospitals. It is in healthcare and it is not a biotech.

    A mapping that blurred the two would hand a pre-revenue-pipeline methodology to a
    company with revenue and no pipeline, and nothing downstream could tell.
    """
    assert industry_for_sic("8060") == "Health Care Providers"
    assert industry_for_sic("8060") != "Biotechnology"


# ---------------------------------------------------------------------------
# 2. Normalisation must not widen meaning
# ---------------------------------------------------------------------------


def test_healthcare_alone_does_not_imply_biotechnology():
    """The explicit boundary on normalisation.

    A company known only to be in the healthcare *sector* must not acquire the
    biotechnology *industry*. It may still match the biotech playbook — that playbook
    declares the whole Health Care sector and says so in its own declaration — but the
    match must come from the sector arm, never from an industry list that normalisation
    quietly widened.
    """
    from app.services.playbooks.schema import _industry_key
    from app.services.sector_taxonomy import normalize_industry

    assert normalize_industry("Healthcare") is None
    assert normalize_industry("Health Care") is None
    # And therefore it does not collide with the biotech playbook's industry list.
    assert _industry_key("Healthcare") != _industry_key("Biotechnology")
    assert _industry_key("Healthcare") != _industry_key("Pharmaceuticals")


def test_sector_vocabularies_reconcile_without_widening():
    """"Health Care" and "Healthcare" are the same sector; they are not the same as
    every other sector."""
    from app.services.playbooks.schema import _sector_key

    assert _sector_key("Health Care") == _sector_key("Healthcare")
    assert _sector_key("Information Technology") == _sector_key("Technology")
    assert _sector_key("Healthcare") != _sector_key("Financials")
    # An unknown value still matches only itself.
    assert _sector_key("Frobnication") == _sector_key("frobnication")
    assert _sector_key("Frobnication") != _sector_key("Healthcare")


def test_an_empty_classification_matches_no_playbook():
    """Guards the normalisation fallback: ``None`` and ``""`` must not fold into a key
    that matches a declaration."""
    assert select_playbooks(sector=None, industry=None).playbooks == ()
    assert select_playbooks(sector="", industry="").playbooks == ()
    assert select_playbooks(sector="   ", industry="   ").playbooks == ()


# ---------------------------------------------------------------------------
# 3. Selection does not depend on business-model signals
# ---------------------------------------------------------------------------


def test_selection_works_with_no_signals_because_production_passes_none():
    """Production passes **no** signals.

    ``v3_pipeline`` calls ``select_playbooks`` with sector and industry only; nothing in
    the platform sets ``pre_revenue``, ``pipeline_driven``, ``regulated_capital``,
    ``brand_led``, ``cyclical_capex`` or ``contract_backlog`` on a real run. The signal
    arm of ``AppliesTo.matches`` is therefore dead code in production today, and the
    classification fix must not be quietly relying on it.

    If someone later makes a model emit these, this test still passes — but the fix does
    not *need* that to have happened.
    """
    for sic, industry, sector, playbooks in _FAMILIES:
        resolved = resolve_classification(sic_code=sic)
        with_none = select_playbooks(
            sector=resolved.matching_sector, industry=resolved.industry, signals=()
        )
        assert sorted(p.playbook_id for p in with_none.playbooks) == sorted(playbooks), (
            f"SIC {sic} must select {playbooks} from classification alone"
        )


def test_pipeline_does_not_pass_signals_to_selection():
    """Pins the production call itself, so the claim above is checked against the code
    rather than restated."""
    import inspect

    from app.services.pipeline import v3_pipeline

    source = inspect.getsource(v3_pipeline)
    call_start = source.index("selection = select_playbooks(")
    call = source[call_start : source.index(")", call_start)]
    assert "signals" not in call, (
        "production selection passes no signals; if that changes, update the test that "
        "documents it rather than the documentation"
    )


# ---------------------------------------------------------------------------
# 4. Raw and canonical are both kept
# ---------------------------------------------------------------------------


def test_the_regulators_own_words_survive_normalisation():
    resolved = resolve_classification(
        sic_code="2836",
        sic_description="Biological Products, (No Diagnostic Substances)",
    )
    assert resolved.industry == "Biotechnology"
    assert resolved.industry_raw == "Biological Products, (No Diagnostic Substances)"
    assert resolved.sector == "Healthcare"
    assert resolved.tier == T2
    assert resolved.source == "sec_sic"


def test_raw_words_survive_a_second_run_that_reads_back_the_canonical_value():
    """The subtle one.

    On run two the stored industry *is* "Biotechnology" — the translation. Without
    carrying the previously recorded raw value forward, the regulator's own words would
    be overwritten by the platform's label the first time the classification was reused,
    and the reader would lose the ability to check it.
    """
    resolved = resolve_classification(
        stored_industry="Biotechnology",
        stored_industry_raw="Biological Products, (No Diagnostic Substances)",
        stored_tier=T2,
    )
    assert resolved.industry == "Biotechnology"
    assert resolved.industry_raw == "Biological Products, (No Diagnostic Substances)"


# ---------------------------------------------------------------------------
# 5. Supersede rules
# ---------------------------------------------------------------------------


def test_supersede_a_regulator_classification_outranks_a_stored_aggregator_one():
    resolved = resolve_classification(
        sic_code="2836", stored_industry="Pharmaceuticals", stored_tier=T5
    )
    assert resolved.industry == "Biotechnology"
    assert resolved.tier == T2


def test_supersede_an_inference_never_outranks_a_stored_regulator_value():
    """The failure the tier column exists to prevent: the SEC is briefly unreachable,
    the run falls back to reading a description, and a guess replaces a fact."""
    resolved = resolve_classification(
        sic_code=None,
        stored_industry="Biotechnology",
        stored_tier=T2,
        sic_description="Services-Commercial Physical & Biological Research",
    )
    assert resolved.industry == "Biotechnology"
    assert resolved.tier == T2
    assert not resolved.is_inferred


def test_an_inference_is_used_when_nothing_stronger_exists_and_is_labelled():
    resolved = resolve_classification(
        sic_description="Pharmaceutical Preparations And Related Products"
    )
    assert resolved.industry == "Pharmaceuticals"
    assert resolved.tier == T6
    assert resolved.is_inferred
    assert any("INFERRED" in n for n in resolved.notes)


def test_a_stored_sector_alone_is_kept_and_asserts_nothing_narrower():
    resolved = resolve_classification(stored_sector="Health Care")
    assert resolved.sector == "Healthcare"
    assert resolved.industry is None
    assert resolved.is_known


# ---------------------------------------------------------------------------
# 6. A sector is not always grounds for a methodology
# ---------------------------------------------------------------------------


def test_a_known_industry_suppresses_the_broad_sector_fallback():
    """Alphabet is a software company in the technology sector.

    The semiconductor playbook declares the whole ``Information Technology`` sector, so
    a bare sector fallback hands Alphabet fab utilisation, node transitions and wafer
    pricing. Every one of those comes back unanswerable — which reads as a coverage
    problem rather than as the wrong playbook, and that is the expensive kind of wrong.

    ``AppliesTo.matches`` already states the intended rule: the sector fallback is for a
    company "whose sector is known and whose industry is **not**".
    """
    alphabet = resolve_classification(
        sic_code="7370", sic_description="Services-Computer Programming, Data Processing"
    )
    assert alphabet.sector == "Technology"
    assert alphabet.industry == "Software & IT Services"
    assert alphabet.matching_sector is None
    assert (
        select_playbooks(
            sector=alphabet.matching_sector, industry=alphabet.industry
        ).playbooks
        == ()
    )
    # And the sector itself is still reported — it is just not load-bearing.
    assert alphabet.sector == "Technology"


def test_an_estimated_sector_is_reported_but_never_selects_a_methodology():
    """A sector-only match is already the broadest selection the platform makes.
    Making it from an estimate too would apply a specialist methodology to a company no
    source actually classified."""
    amazon = resolve_classification(sic_description="Retail-Catalog & Mail-Order Houses")
    assert amazon.sector == "Consumer Discretionary"
    assert amazon.is_inferred
    assert amazon.matching_sector is None
    assert (
        select_playbooks(sector=amazon.matching_sector, industry=amazon.industry).playbooks
        == ()
    )


def test_a_sourced_sector_with_no_industry_still_selects_its_sectors_playbook():
    """The case the fallback exists for, and it must keep working."""
    resolved = resolve_classification(stored_sector="Health Care", stored_tier=T5)
    assert resolved.industry is None
    assert resolved.matching_sector == "Healthcare"
    assert [
        p.playbook_id
        for p in select_playbooks(
            sector=resolved.matching_sector, industry=resolved.industry
        ).playbooks
    ] == ["biotech"]


def test_a_reit_is_real_estate_and_not_a_bank():
    """"Real Estate Investment Trusts" contains the word "Investment", so a keyword scan
    reaches Financials before it ever reaches Real Estate.

    That produced a self-contradictory row — sector Financials beside industry Real
    Estate — once already, and it would have handed two data-centre REITs the bank
    methodology. The taxonomy's deterministic industry rule runs first for this reason.
    """
    resolved = resolve_classification(sic_description="Real Estate Investment Trusts")
    assert resolved.sector == "Real Estate"
    assert (
        select_playbooks(
            sector=resolved.matching_sector, industry=resolved.industry
        ).playbooks
        == ()
    )
