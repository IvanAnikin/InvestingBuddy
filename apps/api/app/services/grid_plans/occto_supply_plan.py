"""OCCTO supply-plan aggregation: Japan's main transmission projects — V3.20 slice 11.5.

Japan's general transmission and distribution companies file ten-year supply plans
(供給計画) with the government each year. OCCTO (電力広域的運営推進機関) aggregates them:
「2026年度供給計画の取りまとめ」 (published 2026-03-30), chapter 4, 送配電設備の増強計画.
Layout pinned on that PDF (downloaded 2026-11-17):

* 表４－２ 主要送電線路 — 工事中地点 (under construction)
* 表４－３ 主要送電線路 — 着工準備中地点 (preparing to start construction)
* 表４－５ 主要変電所 — 工事中地点
* 表４－６ 主要変電所 — 着工準備中地点

Each row is one project as its company filed it: the filing company (届出事業者, written
once per block), the name, voltage, line length or transformer capacity, circuits or
units, construction start (着工年月), first use (使用開始年月) and the reason (理由). The
decommissioning tables (表４－４, 表４－７) are not projects and are not read. The PDF
states no capex.

A table belongs to the heading above it on its page, or to the last heading of an
earlier page (tables continue across pages). Its header must name the columns this was
written against (looked up BY NAME); anything else is a ``LayoutError``. Company names
are carried with the company's official English name, so a filing that names "TEPCO
Power Grid" as a customer can be matched. Every cell is NFKC-normalised (the PDF writes
ﾈｯﾄﾜｰｸ in half-width katakana and Ｆ支線 in full-width letters) and footnote marks
(※1, ※2,※3) are removed; otherwise names are kept as filed, including 仮称
(provisional) and the anonymised names the plan uses (F支線, H線). A first-use date of
未定 (undecided) has no year. A header row repeated inside a continued table is skipped.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from typing import Any

from app.services.grid_plans.base import commissioning_year, max_voltage_kv
from app.services.sources.issuer_holdings.base import LayoutError

PARSER_VERSION = "2026-11-17.1"

#: Table number → (kind, status class, status text).
TABLES: dict[str, tuple[str, str, str]] = {
    "2": ("line", "under_construction", "工事中 (under construction)"),
    "3": ("line", "planned", "着工準備中 (preparing to start construction)"),
    "5": ("substation", "under_construction", "工事中 (under construction)"),
    "6": ("substation", "planned", "着工準備中 (preparing to start construction)"),
}
SKIPPED_TABLES = frozenset({"4", "7"})  # 廃止計画: decommissioning, not projects
#: The first heading after the project tables: reading stops there.
_END = re.compile(r"表\s*４\s*－\s*８")
_HEADING = re.compile(r"表\s*４\s*－\s*([０-９0-9]+)\s*(工事中地点|着工準備中地点|廃止計画)")

LINE_COLUMNS = ("届出事業者", "名称", "電圧", "こう長", "回線数", "着工年月", "使用開始年月",
                "理由")
SUBSTATION_COLUMNS = ("届出事業者", "名称", "電圧", "容量", "台数", "着工年月", "使用開始年月",
                      "理由")

#: The filing companies' official English names and service areas.
COMPANIES: dict[str, tuple[str, str]] = {
    "北海道電力ネットワーク株式会社": ("Hokkaido Electric Power Network, Inc.", "Hokkaido"),
    "東北電力ネットワーク株式会社": ("Tohoku Electric Power Network Co., Inc.", "Tohoku"),
    "東京電力パワーグリッド株式会社": ("TEPCO Power Grid, Incorporated", "Tokyo"),
    "中部電力パワーグリッド株式会社": ("Chubu Electric Power Grid Co., Inc.", "Chubu"),
    "北陸電力送配電株式会社": ("Hokuriku Electric Power Transmission & Distribution Company",
                       "Hokuriku"),
    "関西電力送配電株式会社": ("Kansai Transmission and Distribution, Inc.", "Kansai"),
    "中国電力ネットワーク株式会社": ("Chugoku Electric Power Transmission & Distribution "
                          "Co., Inc.", "Chugoku"),
    "四国電力送配電株式会社": ("Shikoku Electric Power Transmission & Distribution Company, "
                       "Incorporated", "Shikoku"),
    "九州電力送配電株式会社": ("Kyushu Electric Power Transmission and Distribution Co., Inc.",
                       "Kyushu"),
    "沖縄電力株式会社": ("The Okinawa Electric Power Company, Incorporated", "Okinawa"),
    "電源開発送変電ネットワーク株式会社": ("J-Power Transmission Network Co., Ltd.",
                            "inter-regional"),
}

#: What the text says → the shared technology tags. Japanese only; the English tagger in
#: ``base`` cannot read these names.
_TAGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hvdc", re.compile(r"直流|DC\s*-?\s*\d")),
    ("cable", re.compile(r"地中|海底|ケーブル")),
    ("overhead_line", re.compile(r"架空")),
    ("substation", re.compile(r"変電所|開閉所")),
    ("converter_station", re.compile(r"変換所")),
    ("interconnector", re.compile(r"連系")),
)
_FOOTNOTE = re.compile(r"※\s*\d+(?:\s*,\s*※\s*\d+)*")
_CJK_WRAP = re.compile(r"(?<=[^\x00-\x7f])\s*\n\s*(?=[^\x00-\x7f])")


def _text(cell: Any) -> str:
    """A cell as one line: Japanese wraps joined, other breaks a space, footnotes off."""
    raw = unicodedata.normalize("NFKC", str(cell or ""))
    raw = _CJK_WRAP.sub("", raw)
    raw = _FOOTNOTE.sub("", raw)
    return " ".join(raw.split()).strip()


def _header(cell: Any) -> str:
    """A header cell without its footnote number ("名称32", "こう長33,34")."""
    return re.sub(r"[\d,]+$", "", _text(cell)).strip()


def _table_kind(header: list[str]) -> str | None:
    names = tuple(_header(c) for c in header)
    if names[:len(LINE_COLUMNS)] == LINE_COLUMNS:
        return "line"
    if names[:len(SUBSTATION_COLUMNS)] == SUBSTATION_COLUMNS:
        return "substation"
    if "廃止年月" in names:
        return "decommissioning"
    return None


def company_names(filed: str) -> tuple[list[str], str | None]:
    """``(promoters, area)`` for a filing company as the table writes it."""
    name = _text(filed).replace(" ", "")
    english, area = COMPANIES.get(name, (None, None))
    return ([name, english] if english else [name]), area


def tags(*texts: str) -> list[str]:
    blob = " ".join(texts)
    return [tag for tag, rx in _TAGS if rx.search(blob)]


def _external_id(table: str, company: str, name: str, voltage: str) -> str:
    digest = hashlib.sha256("\x1f".join((table, company, name, voltage)).encode()).hexdigest()
    return f"t4-{table}-{digest[:16]}"


def records_from_tables(tables: list[tuple[str, list[list[Any]]]]) -> list[dict[str, Any]]:
    """Projects from ``(table number, rows)`` pairs, in document order. A continued table
    (no header row) keeps the columns and the last company of the one before it."""
    out: list[dict[str, Any]] = []
    company = ""
    kind_of: dict[str, str] = {}
    for number, rows in tables:
        if number in SKIPPED_TABLES or not rows:
            continue
        if number not in TABLES:
            raise LayoutError("unexpected_table", f"表4-{number}")
        expected, status_class, status_text = TABLES[number]
        body = rows
        found = _table_kind(rows[0])
        if found is not None:
            if found != expected:
                raise LayoutError("table_header_mismatch", f"表4-{number}: {found}")
            kind_of[number] = found
            body = rows[1:]
            company = ""
        elif number not in kind_of:
            raise LayoutError("missing_header", f"表4-{number}")
        for row in body:
            if _table_kind(row) is not None:
                continue  # a header repeated inside a continued table
            cells = [_text(c) for c in row] + [""] * 8
            if cells[0]:
                company = cells[0]
            name = cells[1]
            if not name or not company:
                continue
            promoters, area = company_names(company)
            voltage, size, count = cells[2], cells[3], cells[4]
            start, first_use, reason = cells[5], cells[6], cells[7]
            unit = "こう長" if expected == "line" else "容量"
            many = "回線数" if expected == "line" else "台数"
            out.append({
                "record_kind": "project",
                "external_id": _external_id(number, promoters[0], name, voltage),
                "parent_external_id": None,
                "project_name": name[:500],
                "promoters_text": promoters[0][:1000],
                "promoters": promoters,
                "region": f"Japan: {area}" if area else "Japan",
                "category": ("主要送電線路 (main transmission line)" if expected == "line"
                             else "主要変電所 (main substation)"),
                "technology_tags": tags(name, voltage),
                "voltage_kv": max_voltage_kv(voltage),
                "description": "; ".join(p for p in (
                    f"電圧 {voltage}" if voltage else "", f"{unit} {size}" if size else "",
                    f"{many} {count}" if count else "", f"着工 {start}" if start else "",
                    f"理由 {reason}" if reason else "") if p)[:4000] or None,
                "capacity_text": (f"{unit} {size}" + (f" ({many} {count})" if count else ""))
                                 [:500] if size and size not in ("-", "－", "―") else None,
                "commissioning_text": first_use[:120] or None,
                "commissioning_year": commissioning_year(first_use),
                "status_text": status_text,
                "status_class": status_class,
            })
    return out


def tables_from_pdf(body: bytes) -> list[tuple[str, list[list[Any]]]]:
    """``(table number, rows)`` for every table under a 表４－N heading, in order."""
    import pdfplumber

    if not body.startswith(b"%PDF"):
        raise LayoutError("not_a_pdf", "")
    found: list[tuple[str, list[list[Any]]]] = []
    current: str | None = None
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            if current is not None and page.search(_END.pattern, regex=True):
                break  # past chapter 4's project tables
            headings = sorted(
                (float(m["top"]), str(unicodedata.normalize("NFKC", m["groups"][0])))
                for m in page.search(_HEADING.pattern, regex=True, return_groups=True))
            if not headings and current is None:
                continue
            for table in sorted(page.find_tables(), key=lambda t: t.bbox[1]):
                above = [n for top, n in headings if top < table.bbox[1]]
                number = above[-1] if above else current
                if number is not None:
                    found.append((number, table.extract()))
            if headings:
                current = headings[-1][1]
    return found


def parse(body: bytes) -> list[dict[str, Any]]:
    tables = tables_from_pdf(body)
    if not any(n in TABLES for n, _ in tables):
        raise LayoutError("missing_tables", "表4-2/4-3/4-5/4-6")
    out = records_from_tables(tables)
    if not out:
        raise LayoutError("no_projects", "表4-2/4-3/4-5/4-6")
    return out


__all__ = ["COMPANIES", "PARSER_VERSION", "TABLES", "company_names", "parse",
           "records_from_tables", "tables_from_pdf", "tags"]
