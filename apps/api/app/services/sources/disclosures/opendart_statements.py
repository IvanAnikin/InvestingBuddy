"""OpenDART consolidated statements as statement facts — V3.20 slice 11.3b.

A Korean business, half-year or quarterly report is filed in XBRL, and OpenDART serves
the tagged statements of each filing as rows (``fnlttSinglAcntAll.json``): one row per
account per statement, with the account's taxonomy concept (``ifrs-full_Revenue``,
``dart_OperatingIncomeLoss``) and the amounts of the current and comparative periods.
When ``opendart.document_extractor`` reads such a report, this module turns those rows
into ``ValidatedFact`` rows of THE SAME document, through the same concept map, merge
rule and fact store as an SEC filing's inline XBRL (``inline_xbrl_facts``).

THE RULES (all fail-closed; measured on Samsung Electronics' real 2025 annual and 2026
Q1, H1 and Q3 reports)
=======================
* **The filing, not "the latest".** The request names the issuer's ``corp_code``, a
  ``bsns_year`` and the report code; every returned row must carry the receipt number
  of the document being read and the issuer's ``corp_code``. Rows of another receipt (an
  amendment filed later, a different year) produce nothing. An amended report is not
  read here at all.
* **The frame from the filing's own metadata.** The report kind comes from the report
  name (사업보고서 / 반기보고서 / 분기보고서), its period end from the name's
  ``(YYYY.MM)``, and its position in the fiscal year from the issuer's stated fiscal
  year-end month (OpenDART ``company.json`` ``acc_mt``). A half-year report must sit 6
  months into the fiscal year, a quarterly report 3 or 9, an annual report 12; anything
  else produces nothing. Each row's own period name must agree: ``1분기`` / ``반기`` /
  ``3분기`` for an interim report, neither for an annual one.
* **Which column is which period** (DART's layout, checked on the real files):

  ===========  ==================================  ==================================
  statement    interim report                      annual report
  ===========  ==================================  ==================================
  BS           ``thstrm`` = this period end;       ``thstrm`` / ``frmtrm`` /
               ``frmtrm`` = last fiscal year end   ``bfefrmtrm`` = the three year ends
  IS, CIS      ``thstrm`` = the THREE MONTHS;      ``thstrm`` / ``frmtrm`` /
               ``thstrm_add`` = year to date;      ``bfefrmtrm`` = the three years
               ``frmtrm_q`` / ``frmtrm_add`` =
               the same spans a year earlier
  CF           ``thstrm`` = YEAR TO DATE;          as IS
               ``frmtrm_q`` = a year earlier
  ===========  ==================================  ==================================

  So a Q3 report gives Q3 and its comparative from the income statement, and nothing
  from the cash-flow statement (nine months is not a period the store keeps — the same
  rule as inline XBRL); a half-year report gives Q2 and H1. A Q1 report whose income
  statement's quarter and year-to-date differ is not read.
* **Each line from its own statement.** A balance from BS, a cash flow from CF, any
  other flow from IS or CIS. The cash-flow statement's opening "profit" line and the
  equity statement (SCE) are never read. When IS and CIS both carry a line they must
  agree exactly.
* **Concepts through the reviewed map.** ``ifrs-full_X`` is read as ``ifrs-full:X`` in
  ``xbrl_statement_concepts``; the only DART-taxonomy concept read is
  ``dart_OperatingIncomeLoss`` (영업이익), the operating-profit line K-IFRS filers present
  in place of an ``ifrs-full`` one, under its own label. Rows without a standard code
  (``-표준계정코드 미사용-``) and every other ``dart_`` concept are skipped.
* **The Group only.** Consolidated statements (``fs_div=CFS``) are the Group. Separate
  (parent-only) statements are never read, so a filer without subsidiaries gets no
  statement facts from here.
* **Won as stated.** Amounts are whole units of the row's ISO currency (KRW); no scale,
  no conversion, no derivation. A negative spend line is skipped, not flipped.

THE KEY travels only as the ``crtfc_key`` parameter (``transport.guarded_get``), never
in a URL this module builds, stores or logs.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.integrations.xbrl_statement_concepts import (
    DART_STATEMENT_CONCEPTS,
    KIND_INSTANT,
    STATEMENT_CONCEPT_BY_QNAME,
    StatementConcept,
)
from app.services.sources.disclosures.model import OfficialDocument, VerifiedIssuer

logger = logging.getLogger(__name__)

STATEMENTS_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
EXTRACTION_METHOD_DART_XBRL = "dart_xbrl"
LOCATOR_PREFIX = "opendart:"
#: Filed in XBRL by the issuer with the regulator: the same standing as inline XBRL.
DART_XBRL_CONFIDENCE = 0.97
MAX_FACTS_PER_DOCUMENT = 600
MAX_STATEMENTS_BYTES = 3_000_000
FS_DIV_CONSOLIDATED = "CFS"
SCOPE_GROUP = "Group"

REPORT_CODE_ANNUAL = "11011"
REPORT_CODE_HALF = "11012"
REPORT_CODE_Q1 = "11013"
REPORT_CODE_Q3 = "11014"

_KIND_ANNUAL, _KIND_HALF, _KIND_QUARTER = "annual", "half", "quarter"
_AMOUNT_RE = re.compile(r"^-?\d{1,20}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_RCEPT_NO_RE = re.compile(r"^[0-9]{14}$")

_STATEMENT_BS = "BS"
_STATEMENT_CF = "CF"
_INCOME_STATEMENTS = frozenset({"IS", "CIS"})
#: Fields read from the cash-flow statement and nowhere else.
_CASH_FLOW_FIELDS = frozenset({
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
    "capital_expenditure",
})



@dataclass(frozen=True)
class ReportFrame:
    """Where one periodic report sits on the issuer's fiscal calendar."""

    kind: str
    report_code: str
    #: The fiscal year, named by the calendar year it ENDS in.
    fiscal_year: int
    #: Months from the fiscal year's start to the report's period end (3/6/9/12).
    months: int
    period_end_year: int
    period_end_month: int
    fy_end_month: int

    @property
    def marker(self) -> str | None:
        """The word every row's period name carries (``None`` for an annual report)."""
        return {3: "1분기", 6: "반기", 9: "3분기"}.get(self.months)

    def bsns_years(self) -> tuple[int, ...]:
        """``bsns_year`` values to ask for, most likely first. The receipt-number check
        decides which answer is this filing's, so a wrong guess can only find nothing."""
        return (self.fiscal_year, self.fiscal_year - 1) if self.fy_end_month != 12 \
            else (self.fiscal_year,)


def report_frame(report_nm: str, *, fy_end_month: int | None) -> ReportFrame | None:
    """The frame of a periodic report from its listed name, or ``None``. Pure."""
    from app.services.sources.disclosures.opendart import _AMENDED_RE, _PERIOD_RE

    name = report_nm or ""
    if _AMENDED_RE.match(name):
        return None
    if "사업보고서" in name:
        kind = _KIND_ANNUAL
    elif "반기보고서" in name:
        kind = _KIND_HALF
    elif "분기보고서" in name:
        kind = _KIND_QUARTER
    else:
        return None
    found = _PERIOD_RE.search(name)
    if not found:
        return None
    year, month = int(found.group(1)), int(found.group(2))
    if not 1 <= month <= 12:
        return None
    if kind == _KIND_ANNUAL:
        fy_end_month = fy_end_month or month
        if fy_end_month != month:
            return None
    if fy_end_month is None or not 1 <= fy_end_month <= 12:
        return None
    fiscal_year = year if month <= fy_end_month else year + 1
    months = (month - fy_end_month) % 12 or 12
    expected = {_KIND_ANNUAL: {12: REPORT_CODE_ANNUAL}, _KIND_HALF: {6: REPORT_CODE_HALF},
                _KIND_QUARTER: {3: REPORT_CODE_Q1, 9: REPORT_CODE_Q3}}[kind]
    code = expected.get(months)
    if code is None:
        return None
    return ReportFrame(kind=kind, report_code=code, fiscal_year=fiscal_year, months=months,
                       period_end_year=year, period_end_month=month,
                       fy_end_month=fy_end_month)


def _concept(account_id: str) -> StatementConcept | None:
    prefix, sep, local = (account_id or "").partition("_")
    if not sep or not local or prefix not in ("ifrs-full", "dart"):
        return None
    qname = f"{prefix}:{local}"
    if prefix == "dart":
        return DART_STATEMENT_CONCEPTS.get(qname)
    return STATEMENT_CONCEPT_BY_QNAME.get(qname)


def _statement_allows(sj_div: str, mapped: StatementConcept) -> bool:
    if mapped.kind == KIND_INSTANT:
        return sj_div == _STATEMENT_BS
    if mapped.statement_field in _CASH_FLOW_FIELDS:
        return sj_div == _STATEMENT_CF
    return sj_div in _INCOME_STATEMENTS


def _columns(frame: ReportFrame, sj_div: str) -> list[tuple[str, str]]:
    """``(column, period key)`` pairs to read for one statement. Pure."""
    fy = frame.fiscal_year
    if frame.kind == _KIND_ANNUAL:
        return [("thstrm_amount", str(fy)), ("frmtrm_amount", str(fy - 1)),
                ("bfefrmtrm_amount", str(fy - 2))]
    if sj_div == _STATEMENT_BS:
        here = f"{fy}-H1" if frame.months == 6 else f"{fy}-Q{frame.months // 3}"
        return [("thstrm_amount", here), ("frmtrm_amount", str(fy - 1))]
    quarter = frame.months // 3
    if sj_div in _INCOME_STATEMENTS:
        pairs = [("thstrm_amount", f"{fy}-Q{quarter}"),
                 ("frmtrm_q_amount", f"{fy - 1}-Q{quarter}")]
        if frame.months == 6:
            pairs += [("thstrm_add_amount", f"{fy}-H1"),
                      ("frmtrm_add_amount", f"{fy - 1}-H1")]
        return pairs
    if sj_div == _STATEMENT_CF:
        if frame.months == 3:
            return [("thstrm_amount", f"{fy}-Q1"), ("frmtrm_q_amount", f"{fy - 1}-Q1")]
        if frame.months == 6:
            return [("thstrm_amount", f"{fy}-H1"), ("frmtrm_q_amount", f"{fy - 1}-H1")]
    return []  # nine months year to date is not a stored period


def _row_period_agrees(row: dict[str, Any], frame: ReportFrame) -> bool:
    name = str(row.get("thstrm_nm") or "")
    if frame.marker is None:
        return "분기" not in name and "반기" not in name
    return frame.marker in name


def _amount(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    return int(text) if _AMOUNT_RE.match(text) else None


def _q1_frame_holds(rows: Iterable[dict[str, Any]], frame: ReportFrame) -> bool:
    """In a Q1 report the income statement's quarter IS its year to date."""
    if frame.months != 3:
        return True
    for row in rows:
        if row.get("sj_div") not in _INCOME_STATEMENTS:
            continue
        quarter, ytd = _amount(row.get("thstrm_amount")), _amount(row.get("thstrm_add_amount"))
        if quarter is not None and ytd is not None and quarter != ytd:
            return False
    return True


def statement_rows(payload: bytes | None) -> tuple[str | None, list[dict[str, Any]]]:
    """``(status, rows)`` from one response. Never raises."""
    try:
        data = json.loads((payload or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, []
    if not isinstance(data, dict):
        return None, []
    status = str(data.get("status") or "") or None
    rows = data.get("list")
    if not isinstance(rows, list):
        return status, []
    return status, [r for r in rows if isinstance(r, dict)]


def statement_facts(
    rows: list[dict[str, Any]], frame: ReportFrame, *, rcept_no: str, corp_code: str,
) -> list[Any]:
    """Validated Group statement facts from one filing's rows. Pure; never raises."""
    from app.services.sources.extracted_fact_validator import (
        VALIDATION_VALIDATED,
        ValidatedFact,
    )

    if not _RCEPT_NO_RE.match(rcept_no or ""):
        return []
    own = [r for r in rows if r.get("rcept_no") == rcept_no and r.get("corp_code") == corp_code
           and r.get("reprt_code") == frame.report_code]
    if not own or len(own) != len(rows) or not _q1_frame_holds(own, frame):
        return []  # a mixed answer is not this filing's

    # (field, period) -> [(priority, concept, value, row, column)]
    candidates: dict[tuple[str, str], list[tuple[int, StatementConcept, int, dict, str]]] = {}
    for row in own:
        sj_div = str(row.get("sj_div") or "")
        mapped = _concept(str(row.get("account_id") or ""))
        if mapped is None or mapped.statement_field is None or mapped.ratio:
            continue
        if mapped.bank_only or not _statement_allows(sj_div, mapped):
            continue
        if not _CURRENCY_RE.match(str(row.get("currency") or "")):
            continue
        if not _row_period_agrees(row, frame):
            continue
        for column, period in _columns(frame, sj_div):
            value = _amount(row.get(column))
            if value is None or (mapped.spend and value < 0):
                continue
            key = (mapped.statement_field, period)
            candidates.setdefault(key, []).append((mapped.priority, mapped, value, row, column))

    out: list[Any] = []
    for (field_name, period), group in candidates.items():
        best_priority = min(entry[0] for entry in group)
        best = [entry for entry in group if entry[0] == best_priority]
        if len({entry[1].concept for entry in best}) > 1 or len({entry[2] for entry in best}) > 1:
            continue  # two concepts, or IS and CIS disagreeing: fill nothing
        _priority, mapped, value, row, column = best[0]
        out.append(ValidatedFact(
            label=field_name,
            value_numeric=float(value),
            value_text=str(value),
            unit="currency_amount",
            currency=str(row["currency"]),
            scale=None,
            period=period,
            page_number=None,
            table_location=f"{LOCATOR_PREFIX}{mapped.concept}@{row['sj_div']}/{column}"[:200],
            extraction_method=EXTRACTION_METHOD_DART_XBRL,
            confidence=DART_XBRL_CONFIDENCE,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
            scope=SCOPE_GROUP,
            methods=[EXTRACTION_METHOD_DART_XBRL],
            validation_notes=[
                f"taxonomy label: {mapped.label}",
                f"DART statement {row['sj_div']} ({FS_DIV_CONSOLIDATED}) {column} of receipt "
                f"{rcept_no}",
                f"account caption: {str(row.get('account_nm') or '')[:80]}",
                "scope axis: group",
            ],
        ))
    out.sort(key=lambda f: (f.label, f.period or ""))
    return out[:MAX_FACTS_PER_DOCUMENT]


def is_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_opendart_statements_enabled", True))


async def fetch_statement_facts(
    document: OfficialDocument, issuer: VerifiedIssuer, *, cfg: Any,
    getter: Any = None, fy_end_month: int | None = None,
) -> tuple[list[Any], str]:
    """``(facts, outcome)`` for one listed periodic report. Never raises.

    ``outcome`` is a short code for the extraction notes (``read``, ``not_periodic``,
    ``no_frame``, ``no_data``, ``not_this_filing``, ``unavailable``, ``off``).
    """
    from app.services.sources.disclosures import opendart

    if not is_enabled(cfg) or not opendart.api_key(cfg):
        return [], "off"
    corp_code = issuer.source_ref or ""
    report_nm = document.venue_category or ""
    if not any(w in report_nm for w in ("사업보고서", "반기보고서", "분기보고서")):
        return [], "not_periodic"
    try:
        if fy_end_month is None and "사업보고서" not in report_nm:
            fy_end_month = await opendart._fy_end_month(corp_code, cfg=cfg, getter=getter)
        frame = report_frame(report_nm, fy_end_month=fy_end_month)
        if frame is None:
            return [], "no_frame"
        outcome = "no_data"
        for year in frame.bsns_years():
            _code, body, _error = await (getter or opendart.guarded_get)(
                STATEMENTS_URL,
                params={opendart.KEY_PARAM: opendart.api_key(cfg), "corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": frame.report_code,
                        "fs_div": FS_DIV_CONSOLIDATED},
                cfg=cfg, max_bytes=MAX_STATEMENTS_BYTES, allowed_host=opendart.OPENDART_HOST,
                timeout_seconds=opendart.fetch_timeout(cfg))
            status, rows = statement_rows(body)
            if body is None:
                outcome = "unavailable"
                continue
            if status != "000" or not rows:
                continue
            facts = statement_facts(rows, frame, rcept_no=document.document_ref,
                                    corp_code=corp_code)
            if facts:
                return facts, "read"
            outcome = "not_this_filing"
        return [], outcome
    except Exception as exc:  # noqa: BLE001 - the text reading stands without these
        logger.warning("OpenDART statements for %s failed: %s", document.document_ref,
                       type(exc).__name__)
        return [], "unavailable"


__all__ = [
    "DART_XBRL_CONFIDENCE",
    "EXTRACTION_METHOD_DART_XBRL",
    "FS_DIV_CONSOLIDATED",
    "LOCATOR_PREFIX",
    "ReportFrame",
    "STATEMENTS_URL",
    "fetch_statement_facts",
    "is_enabled",
    "report_frame",
    "statement_facts",
    "statement_rows",
]
