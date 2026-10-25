"""The medical imaging / healthcare software niche is a supported theme (it was rejected with 422)."""

from __future__ import annotations

from app.services.market_thesis_parser import parse_thesis
from app.services.market_universe_builder import THEME_COMPANY_REGISTRY


def test_a_medical_imaging_thesis_parses_to_the_theme() -> None:
    parsed = parse_thesis(
        "Listed medical imaging and healthcare software companies, including small and mid-cap "
        "suppliers of diagnostic imaging, AI radiology and clinical software"
    )
    assert parsed.themes == ["healthcare_technology"]
    assert not parsed.needs_narrowing


def test_the_theme_has_a_curated_universe_of_real_listings() -> None:
    names = {e["ticker"]: e["exchange"] for e in THEME_COMPANY_REGISTRY["healthcare_technology"]}
    assert names == {"GEHC": "US", "VEEV": "US", "SHL": "XETRA", "PME": "AU"}
