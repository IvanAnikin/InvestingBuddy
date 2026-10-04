"""Venue -> locale table and the versioned theme glossary — open-web W6b (spec §5.2).

WHAT THIS IS
============
Data, reviewed like code, that lets the Discovery planner ask for a theme in the
language the companies of a venue publish in:

* :data:`VENUE_LOCALES` — a registry venue code -> ``(language, ISO country)``. It
  extends the company planner's table (W5) with the Discovery set (de, fr, it, es, da,
  sv, no, fi, pl, cs, ja, zh); **English is always planned as well**, never replaced.
* :data:`REGION_LOCALES` / :data:`COUNTRY_LOCALES` — which locales a thesis's requested
  geography implies (an intent names a region or a country, not a venue).
* :data:`VENUE_SEGMENTS` — exchange segments (AIM, First North, Euronext Growth, ASX, TSX
  Venture ...) where the long tail lists, for the ``VENUE`` family.
* :data:`THEME_GLOSSARY` / :data:`MATERIAL_GLOSSARY` / :data:`PHRASE_GLOSSARY` — the
  translations. A concept with no entry for a language yields NO variant in that
  language (the English text is never relabelled as local), so a gap in this table
  costs a query, never a wrong one.

Everything here is a closed vocabulary. Nothing a user typed and nothing a fetched page
said can enter this module's output: lookups are keyed by the intent's closed values
(theme keys, commodity slugs), and a key with no entry returns ``None``.

The bounded LLM translation the spec allows for gaps (``origin=llm_translation``) is NOT
implemented: a model-written query would be an unreviewed query, and the glossary can be
extended in a reviewed diff instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.services.web_research.planner import VENUE_LOCALES as _PLANNER_VENUE_LOCALES

GLOSSARY_VERSION = "2026-10-04.d1"

#: The Discovery language set (spec §5.2). English is implicit and always planned.
DISCOVERY_LANGUAGES: tuple[str, ...] = (
    "de", "fr", "it", "es", "da", "sv", "no", "fi", "pl", "cs", "ja", "zh",
)

#: Registry venue code -> (language, ISO country). Extends the W5 company table.
VENUE_LOCALES: dict[str, tuple[str, str]] = {
    **_PLANNER_VENUE_LOCALES,
    "PR": ("cs", "CZ"),
    "TW": ("zh", "TW"),
}

#: Region name (as ``intent.regions`` carries it) -> the locales it implies, in order.
REGION_LOCALES: dict[str, tuple[tuple[str, str], ...]] = {
    "Europe": (
        ("de", "DE"), ("fr", "FR"), ("it", "IT"), ("es", "ES"), ("sv", "SE"),
        ("da", "DK"), ("no", "NO"), ("fi", "FI"), ("pl", "PL"), ("cs", "CZ"),
    ),
    "Asia": (("ja", "JP"), ("zh", "CN"), ("zh", "HK")),
    "South America": (("pt", "BR"), ("es", "MX")),
}

#: Country name -> its locale.
COUNTRY_LOCALES: dict[str, tuple[str, str]] = {
    "Germany": ("de", "DE"), "Austria": ("de", "AT"), "Switzerland": ("de", "CH"),
    "France": ("fr", "FR"), "Belgium": ("fr", "BE"), "Italy": ("it", "IT"),
    "Spain": ("es", "ES"), "Mexico": ("es", "MX"), "Denmark": ("da", "DK"),
    "Sweden": ("sv", "SE"), "Norway": ("no", "NO"), "Finland": ("fi", "FI"),
    "Poland": ("pl", "PL"), "Czech Republic": ("cs", "CZ"), "Czechia": ("cs", "CZ"),
    "Japan": ("ja", "JP"), "China": ("zh", "CN"), "Hong Kong": ("zh", "HK"),
    "Taiwan": ("zh", "TW"), "South Korea": ("ko", "KR"), "Netherlands": ("nl", "NL"),
    "Portugal": ("pt", "PT"), "Brazil": ("pt", "BR"),
}

#: With no geography requested, the languages a global industrial theme is most often
#: published in beyond English. Bounded by the plan's locale cap.
DEFAULT_LOCALES: tuple[tuple[str, str], ...] = (
    ("de", "DE"), ("zh", "CN"), ("ja", "JP"), ("fr", "FR"),
)

#: Exchange segments where the long tail lists (``VENUE`` family), by region/country.
VENUE_SEGMENTS: dict[str, tuple[str, ...]] = {
    "United Kingdom": ("AIM", "LSE small cap"),
    "Sweden": ("Nasdaq First North", "Spotlight Stock Market", "NGM"),
    "Denmark": ("Nasdaq First North Denmark",),
    "Finland": ("Nasdaq First North Finland",),
    "Norway": ("Euronext Growth Oslo",),
    "France": ("Euronext Growth Paris",),
    "Italy": ("Euronext Growth Milan",),
    "Germany": ("Scale Frankfurt", "Xetra small cap"),
    "Poland": ("NewConnect",),
    "Australia": ("ASX small cap",),
    "New Zealand": ("NZX",),
    "Canada": ("TSX Venture", "CSE"),
    "United States": ("NYSE American", "OTCQX"),
    "Japan": ("TSE Growth",),
    "Hong Kong": ("HKEX GEM",),
    "Europe": ("AIM", "Euronext Growth", "Nasdaq First North", "Scale Frankfurt"),
    "Oceania": ("ASX small cap", "NZX"),
    "North America": ("TSX Venture", "CSE", "NYSE American"),
    "Asia": ("HKEX GEM", "TSE Growth", "ASX small cap"),
}
DEFAULT_SEGMENTS: tuple[str, ...] = (
    "ASX small cap", "AIM", "TSX Venture", "Nasdaq First North", "Euronext Growth",
)

# --------------------------------------------------------------------------- #
# Glossary. concept -> language -> phrase. Company names never appear here.
# --------------------------------------------------------------------------- #

#: ``theme:<key>`` — the noun phrase of a Discovery theme (``intent.themes``).
THEME_GLOSSARY: dict[str, dict[str, str]] = {
    "luxury_goods": {
        "de": "Luxusgüter", "fr": "produits de luxe", "it": "beni di lusso",
        "es": "artículos de lujo", "da": "luksusvarer", "sv": "lyxvaror",
        "no": "luksusvarer", "fi": "ylellisyystuotteet", "pl": "towary luksusowe",
        "cs": "luxusní zboží", "ja": "高級ブランド", "zh": "奢侈品",
    },
    "critical_materials": {
        "de": "kritische Rohstoffe", "fr": "matières premières critiques",
        "it": "materie prime critiche", "es": "materias primas críticas",
        "da": "kritiske råstoffer", "sv": "kritiska råmaterial",
        "no": "kritiske råvarer", "fi": "kriittiset raaka-aineet",
        "pl": "surowce krytyczne", "cs": "kritické suroviny", "ja": "重要鉱物",
        "zh": "关键矿产",
    },
    "mining_materials": {
        "de": "Bergbau", "fr": "exploitation minière", "it": "attività mineraria",
        "es": "minería", "da": "minedrift", "sv": "gruvdrift", "no": "gruvedrift",
        "fi": "kaivostoiminta", "pl": "górnictwo", "cs": "těžba", "ja": "鉱業",
        "zh": "矿业",
    },
    "defense": {
        "de": "Rüstung", "fr": "défense", "it": "difesa", "es": "defensa", "da": "forsvar",
        "sv": "försvar", "no": "forsvar", "fi": "puolustus", "pl": "obronność",
        "cs": "obrana", "ja": "防衛", "zh": "国防",
    },
    "semiconductors": {
        "de": "Halbleiter", "fr": "semi-conducteurs", "it": "semiconduttori",
        "es": "semiconductores", "da": "halvledere", "sv": "halvledare",
        "no": "halvledere", "fi": "puolijohteet", "pl": "półprzewodniki",
        "cs": "polovodiče", "ja": "半導体", "zh": "半导体",
    },
    "nuclear_energy": {
        "de": "Kernenergie", "fr": "énergie nucléaire", "it": "energia nucleare",
        "es": "energía nuclear", "da": "atomkraft", "sv": "kärnkraft", "no": "kjernekraft",
        "fi": "ydinvoima", "pl": "energetyka jądrowa", "cs": "jaderná energie",
        "ja": "原子力", "zh": "核能",
    },
    "grid_electrification": {
        "de": "Stromnetz Elektrifizierung", "fr": "réseau électrique électrification",
        "it": "rete elettrica elettrificazione", "es": "red eléctrica electrificación",
        "da": "elnet elektrificering", "sv": "elnät elektrifiering",
        "no": "strømnett elektrifisering", "fi": "sähköverkko sähköistäminen",
        "pl": "sieć elektroenergetyczna elektryfikacja", "cs": "elektrická síť elektrifikace",
        "ja": "送配電 電化", "zh": "电网 电气化",
    },
    "robotics_automation": {
        "de": "Robotik Automatisierung", "fr": "robotique automatisation",
        "it": "robotica automazione", "es": "robótica automatización",
        "da": "robotteknologi automatisering", "sv": "robotik automation",
        "no": "robotikk automatisering", "fi": "robotiikka automaatio",
        "pl": "robotyka automatyka", "cs": "robotika automatizace",
        "ja": "ロボット 自動化", "zh": "机器人 自动化",
    },
    "biotech_pharma": {
        "de": "Biotechnologie Pharma", "fr": "biotechnologie pharmaceutique",
        "it": "biotecnologie farmaceutica", "es": "biotecnología farmacéutica",
        "da": "bioteknologi medicinal", "sv": "bioteknik läkemedel",
        "no": "bioteknologi legemidler", "fi": "bioteknologia lääkkeet",
        "pl": "biotechnologia farmaceutyki", "cs": "biotechnologie farmacie",
        "ja": "バイオ 医薬品", "zh": "生物技术 制药",
    },
    "banks_fintech": {
        "de": "Banken Fintech", "fr": "banques fintech", "it": "banche fintech",
        "es": "bancos fintech", "da": "banker fintech", "sv": "banker fintech",
        "no": "banker fintech", "fi": "pankit fintech", "pl": "banki fintech",
        "cs": "banky fintech", "ja": "銀行 フィンテック", "zh": "银行 金融科技",
    },
    "ai_infrastructure": {
        "de": "KI-Infrastruktur Rechenzentren", "fr": "infrastructure IA centres de données",
        "it": "infrastruttura IA data center", "es": "infraestructura IA centros de datos",
        "da": "AI-infrastruktur datacentre", "sv": "AI-infrastruktur datacenter",
        "no": "AI-infrastruktur datasentre", "fi": "tekoälyinfrastruktuuri datakeskukset",
        "pl": "infrastruktura AI centra danych", "cs": "infrastruktura AI datová centra",
        "ja": "AIインフラ データセンター", "zh": "人工智能基础设施 数据中心",
    },
}

#: Commodity slug (``intent.materials``) -> language -> the material's name. A language
#: missing here has no local-language variant for that material.
MATERIAL_GLOSSARY: dict[str, dict[str, str]] = {
    "copper": {"de": "Kupfer", "fr": "cuivre", "it": "rame", "es": "cobre", "da": "kobber",
               "sv": "koppar", "no": "kobber", "fi": "kupari", "pl": "miedź", "cs": "měď",
               "ja": "銅", "zh": "铜"},
    "lithium": {"de": "Lithium", "fr": "lithium", "it": "litio", "es": "litio", "da": "lithium",
                "sv": "litium", "no": "litium", "fi": "litium", "pl": "lit", "cs": "lithium",
                "ja": "リチウム", "zh": "锂"},
    "nickel": {"de": "Nickel", "fr": "nickel", "it": "nichel", "es": "níquel", "da": "nikkel",
               "sv": "nickel", "no": "nikkel", "fi": "nikkeli", "pl": "nikiel", "cs": "nikl",
               "ja": "ニッケル", "zh": "镍"},
    "cobalt": {"de": "Kobalt", "fr": "cobalt", "it": "cobalto", "es": "cobalto", "da": "kobolt",
               "sv": "kobolt", "no": "kobolt", "fi": "koboltti", "pl": "kobalt", "cs": "kobalt",
               "ja": "コバルト", "zh": "钴"},
    "rare_earths": {"de": "Seltene Erden", "fr": "terres rares", "it": "terre rare",
                    "es": "tierras raras", "da": "sjældne jordarter",
                    "sv": "sällsynta jordartsmetaller", "no": "sjeldne jordarter",
                    "fi": "harvinaiset maametallit", "pl": "metale ziem rzadkich",
                    "cs": "vzácné zeminy", "ja": "レアアース", "zh": "稀土"},
    "uranium": {"de": "Uran", "fr": "uranium", "it": "uranio", "es": "uranio", "da": "uran",
                "sv": "uran", "no": "uran", "fi": "uraani", "pl": "uran", "cs": "uran",
                "ja": "ウラン", "zh": "铀"},
    "graphite": {"de": "Graphit", "fr": "graphite", "it": "grafite", "es": "grafito",
                 "da": "grafit", "sv": "grafit", "no": "grafitt", "fi": "grafiitti",
                 "pl": "grafit", "cs": "grafit", "ja": "黒鉛", "zh": "石墨"},
    "gallium": {"de": "Gallium", "fr": "gallium", "it": "gallio", "es": "galio",
                "da": "gallium", "sv": "gallium", "no": "gallium", "fi": "gallium",
                "pl": "gal", "cs": "gallium", "ja": "ガリウム", "zh": "镓"},
    "germanium": {"de": "Germanium", "fr": "germanium", "it": "germanio", "es": "germanio",
                  "da": "germanium", "sv": "germanium", "no": "germanium", "fi": "germanium",
                  "pl": "german", "cs": "germanium", "ja": "ゲルマニウム", "zh": "锗"},
    "tungsten": {"de": "Wolfram", "fr": "tungstène", "it": "tungsteno", "es": "wolframio",
                 "da": "wolfram", "sv": "volfram", "no": "wolfram", "fi": "volframi",
                 "pl": "wolfram", "cs": "wolfram", "ja": "タングステン", "zh": "钨"},
    "antimony": {"de": "Antimon", "fr": "antimoine", "it": "antimonio", "es": "antimonio",
                 "da": "antimon", "sv": "antimon", "no": "antimon", "fi": "antimoni",
                 "pl": "antymon", "cs": "antimon", "ja": "アンチモン", "zh": "锑"},
    "vanadium": {"de": "Vanadium", "fr": "vanadium", "it": "vanadio", "es": "vanadio",
                 "da": "vanadium", "sv": "vanadin", "no": "vanadium", "fi": "vanadiini",
                 "pl": "wanad", "cs": "vanad", "ja": "バナジウム", "zh": "钒"},
    "silver": {"de": "Silber", "fr": "argent", "it": "argento", "es": "plata", "da": "sølv",
               "sv": "silver", "no": "sølv", "fi": "hopea", "pl": "srebro", "cs": "stříbro",
               "ja": "銀", "zh": "银"},
    "gold": {"de": "Gold", "fr": "or", "it": "oro", "es": "oro", "da": "guld", "sv": "guld",
             "no": "gull", "fi": "kulta", "pl": "złoto", "cs": "zlato", "ja": "金", "zh": "金"},
    "zinc": {"de": "Zink", "fr": "zinc", "it": "zinco", "es": "zinc", "da": "zink", "sv": "zink",
             "no": "sink", "fi": "sinkki", "pl": "cynk", "cs": "zinek", "ja": "亜鉛",
             "zh": "锌"},
    "manganese": {"de": "Mangan", "fr": "manganèse", "it": "manganese", "es": "manganeso",
                  "da": "mangan", "sv": "mangan", "no": "mangan", "fi": "mangaani",
                  "pl": "mangan", "cs": "mangan", "ja": "マンガン", "zh": "锰"},
    "tin": {"de": "Zinn", "fr": "étain", "it": "stagno", "es": "estaño", "da": "tin",
            "sv": "tenn", "no": "tinn", "fi": "tina", "pl": "cyna", "cs": "cín",
            "ja": "スズ", "zh": "锡"},
    "aluminum": {"de": "Aluminium", "fr": "aluminium", "it": "alluminio", "es": "aluminio",
                 "da": "aluminium", "sv": "aluminium", "no": "aluminium", "fi": "alumiini",
                 "pl": "aluminium", "cs": "hliník", "ja": "アルミニウム", "zh": "铝"},
    "molybdenum": {"de": "Molybdän", "fr": "molybdène", "it": "molibdeno", "es": "molibdeno",
                   "da": "molybdæn", "sv": "molybden", "no": "molybden", "fi": "molybdeeni",
                   "pl": "molibden", "cs": "molybden", "ja": "モリブデン", "zh": "钼"},
    "platinum_group_metals": {
        "de": "Platingruppenmetalle", "fr": "métaux du groupe du platine",
        "it": "metalli del gruppo del platino", "es": "metales del grupo del platino",
        "da": "platingruppemetaller", "sv": "platinametaller",
        "no": "platinagruppemetaller", "fi": "platinaryhmän metallit",
        "pl": "metale z grupy platyny", "cs": "kovy platinové skupiny",
        "ja": "白金族金属", "zh": "铂族金属",
    },
    "iron_ore": {"de": "Eisenerz", "fr": "minerai de fer", "it": "minerale di ferro",
                 "es": "mineral de hierro", "da": "jernmalm", "sv": "järnmalm",
                 "no": "jernmalm", "fi": "rautamalmi", "pl": "ruda żelaza",
                 "cs": "železná ruda", "ja": "鉄鉱石", "zh": "铁矿石"},
}

#: Generic phrases a Discovery query is built from.
PHRASE_GLOSSARY: dict[str, dict[str, str]] = {
    "listed_company": {
        "de": "börsennotiert", "fr": "société cotée", "it": "società quotata",
        "es": "empresa cotizada", "da": "børsnoteret", "sv": "börsnoterat bolag",
        "no": "børsnotert selskap", "fi": "pörssiyhtiö", "pl": "spółka giełdowa",
        "cs": "burzovní společnost", "ja": "上場企業", "zh": "上市公司",
    },
    "producer": {
        "de": "Hersteller", "fr": "producteur", "it": "produttore", "es": "productor",
        "da": "producent", "sv": "producent", "no": "produsent", "fi": "valmistaja",
        "pl": "producent", "cs": "výrobce", "ja": "メーカー", "zh": "生产商",
    },
    "supplier": {
        "de": "Zulieferer", "fr": "fournisseur", "it": "fornitore", "es": "proveedor",
        "da": "leverandør", "sv": "leverantör", "no": "leverandør", "fi": "toimittaja",
        "pl": "dostawca", "cs": "dodavatel", "ja": "サプライヤー", "zh": "供应商",
    },
    "shortage": {
        "de": "Knappheit", "fr": "pénurie", "it": "carenza", "es": "escasez", "da": "mangel",
        "sv": "brist", "no": "mangel", "fi": "pula", "pl": "niedobór", "cs": "nedostatek",
        "ja": "供給不足", "zh": "短缺",
    },
    "government_support": {
        "de": "staatliche Förderung", "fr": "subvention publique", "it": "sostegno pubblico",
        "es": "apoyo gubernamental", "da": "statsstøtte", "sv": "statligt stöd",
        "no": "statlig støtte", "fi": "valtion tuki", "pl": "wsparcie rządowe",
        "cs": "státní podpora", "ja": "政府支援", "zh": "政府支持",
    },
    "offtake": {
        "de": "Abnahmevertrag", "fr": "contrat d'achat à long terme",
        "it": "contratto di offtake", "es": "acuerdo de compra offtake",
        "da": "offtake-aftale", "sv": "offtake-avtal", "no": "offtake-avtale",
        "fi": "offtake-sopimus", "pl": "umowa offtake", "cs": "offtake smlouva",
        "ja": "オフテイク契約", "zh": "包销协议",
    },
    "permit": {
        "de": "Genehmigung", "fr": "permis", "it": "autorizzazione", "es": "permiso",
        "da": "tilladelse", "sv": "tillstånd", "no": "tillatelse", "fi": "lupa",
        "pl": "pozwolenie", "cs": "povolení", "ja": "許認可", "zh": "许可",
    },
}


def locale_for_venue(venue: str | None) -> tuple[str, str] | None:
    """``(language, country)`` for a registry venue code, or None (English only)."""
    return VENUE_LOCALES.get((venue or "").strip().upper()) if venue else None


def locales_for_geography(
    regions: Iterable[str] = (), countries: Iterable[str] = ()
) -> list[tuple[str, str]]:
    """The non-English locales a requested geography implies, deduplicated, in order.

    Countries come first (a named country is the more specific ask), then regions. An
    empty geography gets :data:`DEFAULT_LOCALES`. A language outside the Discovery set
    and without a glossary entry simply produces no variant later.
    """
    out: list[tuple[str, str]] = []
    for country in countries:
        locale = COUNTRY_LOCALES.get(country)
        if locale and locale not in out:
            out.append(locale)
    for region in regions:
        for locale in REGION_LOCALES.get(region, ()):
            if locale not in out:
                out.append(locale)
    if not out and not tuple(regions) and not tuple(countries):
        out = list(DEFAULT_LOCALES)
    return out


def segments_for_geography(
    regions: Iterable[str] = (), countries: Iterable[str] = ()
) -> list[str]:
    """Exchange segments to ask about, most specific (country) first, deduplicated."""
    out: list[str] = []
    for key in (*countries, *regions):
        for segment in VENUE_SEGMENTS.get(key, ()):
            if segment not in out:
                out.append(segment)
    return out or list(DEFAULT_SEGMENTS)


def theme_phrase(theme: str, language: str) -> str | None:
    """The theme's noun phrase in ``language``, or None when the glossary has none."""
    return THEME_GLOSSARY.get(theme, {}).get(language)


def material_phrase(slug: str, language: str) -> str | None:
    return MATERIAL_GLOSSARY.get(slug, {}).get(language)


def phrase(concept: str, language: str) -> str | None:
    return PHRASE_GLOSSARY.get(concept, {}).get(language)


def glossary_coverage(languages: Sequence[str] = DISCOVERY_LANGUAGES) -> dict[str, list[str]]:
    """Which Discovery languages each glossary entry lacks (a reviewer's checklist)."""
    gaps: dict[str, list[str]] = {}
    for table_name, table in (
        ("theme", THEME_GLOSSARY), ("material", MATERIAL_GLOSSARY), ("phrase", PHRASE_GLOSSARY)
    ):
        for key, row in table.items():
            missing = [lang for lang in languages if lang not in row]
            if missing:
                gaps[f"{table_name}:{key}"] = missing
    return gaps


__all__ = [
    "COUNTRY_LOCALES",
    "DEFAULT_LOCALES",
    "DEFAULT_SEGMENTS",
    "DISCOVERY_LANGUAGES",
    "GLOSSARY_VERSION",
    "MATERIAL_GLOSSARY",
    "PHRASE_GLOSSARY",
    "REGION_LOCALES",
    "THEME_GLOSSARY",
    "VENUE_LOCALES",
    "VENUE_SEGMENTS",
    "glossary_coverage",
    "locale_for_venue",
    "locales_for_geography",
    "material_phrase",
    "phrase",
    "segments_for_geography",
    "theme_phrase",
]
