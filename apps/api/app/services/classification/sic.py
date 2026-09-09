"""SEC SIC codes to the platform's canonical industry vocabulary.

WHY THIS EXISTS
===============
The SEC publishes a Standard Industrial Classification code for every filer, and
``data.sec.gov/submissions/CIK##########.json`` returns it on every request the platform
already makes. For Moderna it is ``2836`` / "Biological Products, (No Diagnostic
Substances)" — a regulator's own statement of what the company does, free, structured,
and already in the response body.

The code was parsed into a dict key and read by nothing. Only its DESCRIPTION survived,
as a free-text ``industry`` on the profile, and no description string in the SEC's
vocabulary matches any industry the playbooks declare. So the most authoritative
classification available was present at every run and could never select a methodology.

THE CODE, NOT THE DESCRIPTION
=============================
Mapping is keyed on the numeric code. SEC descriptions are prose — "Biological Products,
(No Diagnostic Substances)", "Pharmaceutical Preparations", "Services-Commercial Physical
& Biological Research" — and matching them by keyword is how "Services-Prepackaged
Software" becomes a pharmaceutical the day someone adds "services" to a keyword list. The
code is stable, enumerated, and published.

WHAT IT REFUSES TO DO
=====================
An unmapped code returns ``None``. There are roughly 450 SIC codes and this table covers
the ones that reach a playbook plus common neighbours; everything else is honestly
unknown, because a company the platform cannot classify must get the generic methodology
rather than the nearest-looking specialist one.

It also never widens meaning. SIC 8000-8099 is health SERVICES — hospitals, clinics,
labs. Those map to "Health Care Providers", which is NOT ``Biotechnology``: a hospital
group is in healthcare and is not a biotech, and a mapping that blurred the two would
hand a pre-revenue-pipeline methodology to a company with revenue and no pipeline.
"""

from __future__ import annotations

#: Exact SIC code -> canonical industry (a key of ``INDUSTRY_TO_SECTOR``).
#:
#: Exact codes win over ranges below, because a range is a generalisation and a code is
#: the regulator's own answer.
_EXACT: dict[str, str] = {
    # ── Life sciences ────────────────────────────────────────────────────
    "2836": "Biotechnology",   # Biological Products (No Diagnostic Substances)
    "8731": "Biotechnology",   # Commercial Physical & Biological Research
    "2834": "Pharmaceuticals",  # Pharmaceutical Preparations
    "2835": "Pharmaceuticals",  # In Vitro & In Vivo Diagnostic Substances
    "2833": "Pharmaceuticals",  # Medicinal Chemicals & Botanical Products
    # ── Semiconductors ───────────────────────────────────────────────────
    "3674": "Semiconductors",          # Semiconductors & Related Devices
    "3559": "Semiconductor Equipment",  # Special Industry Machinery (semi equipment)
    "3827": "Semiconductor Equipment",  # Lab Analytical / optical measuring
    # ── Aerospace & defence ──────────────────────────────────────────────
    "3721": "Aerospace & Defense",  # Aircraft
    "3724": "Aerospace & Defense",  # Aircraft Engines & Engine Parts
    "3728": "Aerospace & Defense",  # Aircraft Parts & Auxiliary Equipment
    "3760": "Aerospace & Defense",  # Guided Missiles & Space Vehicles
    "3761": "Aerospace & Defense",  # Guided Missiles & Space Vehicles & Parts
    "3480": "Aerospace & Defense",  # Ordnance & Accessories
    "3812": "Aerospace & Defense",  # Search, Detection, Navigation, Guidance
    # ── Luxury / premium consumer ────────────────────────────────────────
    "3911": "Watches & Jewelry",  # Jewelry, Precious Metal
    "3873": "Watches & Jewelry",  # Watches, Clocks, Watchcases & Parts
    "5944": "Watches & Jewelry",  # Retail-Jewelry Stores
    "3100": "Leather Goods",      # Leather & Leather Products
    "3140": "Luxury Goods",       # Footwear (except rubber)
    # ── Electrical / industrial ──────────────────────────────────────────
    "3600": "Electrical Equipment",
    "3612": "Electrical Equipment",
    "1600": "Construction & Engineering",
    "1731": "Construction & Engineering",
    # ── Materials / energy ───────────────────────────────────────────────
    "1040": "Mining",
    "1090": "Metals & Mining",
    "1000": "Metals & Mining",
    "1311": "Oil & Gas",
    "1381": "Oil & Gas",
}

#: Inclusive ``(low, high, canonical_industry)`` ranges, consulted only when no exact
#: code matches. Deliberately few: a wide range is a guess wearing a number's clothes.
_RANGES: tuple[tuple[int, int, str], ...] = (
    (2830, 2836, "Pharmaceuticals"),   # drugs, minus the exacts above
    (3670, 3679, "Semiconductors"),    # electronic components
    (6020, 6036, "Banks"),             # commercial / savings banks
    (6199, 6199, "Financial Technology"),
    (6311, 6411, "Insurance"),
    (3720, 3729, "Aerospace & Defense"),
    (8000, 8099, "Health Care Providers"),  # NOT biotech — see the module docstring
    (7370, 7379, "Software & IT Services"),
    (4900, 4991, "Utilities"),
)

#: Industries this module can emit that the canonical taxonomy does not (yet) know.
#: They are still returned — an honest label the playbooks do not match is better than
#: a canonical label that selects the wrong methodology.
UNMAPPED_TO_TAXONOMY: frozenset[str] = frozenset(
    {"Health Care Providers", "Software & IT Services", "Insurance", "Oil & Gas"}
)


def normalize_sic(raw: str | int | None) -> str | None:
    """A four-digit SIC string, or ``None``.

    SEC returns ``"2836"``; a hand-entered value may be ``2836`` or ``" 836"``. Anything
    that is not a plain number is refused rather than coerced.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():
        return None
    return text.zfill(4)


def industry_for_sic(raw: str | int | None) -> str | None:
    """The canonical industry for a SIC code, or ``None`` when unmapped.

    ``None`` is a real answer and the common one: roughly 450 SIC codes exist and this
    table covers those that reach a methodology plus their neighbours.
    """
    code = normalize_sic(raw)
    if code is None:
        return None
    exact = _EXACT.get(code)
    if exact is not None:
        return exact
    value = int(code)
    for low, high, industry in _RANGES:
        if low <= value <= high:
            return industry
    return None


__all__ = ["UNMAPPED_TO_TAXONOMY", "industry_for_sic", "normalize_sic"]
