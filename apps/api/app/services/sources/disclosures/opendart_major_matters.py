"""OpenDART major-matters decisions as typed corporate actions — V3.20 slice 11.3c.

A KOSPI / KOSDAQ issuer must file a major-matters report (주요사항보고서) when its board
decides a capital increase, a bond issue, a buyback, an asset or stake deal, a merger, or
when a default, suspension or dissolution cause occurs. OpenDART serves the typed fields
of each decision type from its own endpoint (``piicDecsn.json``, ``cvbdIsDecsn.json``, …).
The change-events rules read English titles, so a Korean filing produced no event at all;
this module reads those typed fields instead.

THE RULES (measured on real filings, 2026-11-17)
================================================
* **Only what the issuer filed.** One ``list.json`` request lists the issuer's
  major-matters filings (``pblntf_ty=B``) in the window. Only the decision types named
  there are read, each from its own endpoint, for the issuer's ``corp_code`` and the
  same window — so an issuer with no such filing costs one request, and a type it never
  filed is never asked for.
* **Identity from HELD corp codes only.** The issuer's ``corp_code`` comes from the held
  list (migration 060) with the KRX code and an agreeing name. This step never
  downloads the 499-second list; with nothing held it reports ``not_resolved``.
* **Each action cites its own receipt.** A structured row can carry a later receipt than
  the listed filing (a decision re-filed: a capital reduction listed 10-23 came back as
  its 11-09 re-filing). The action's date and address are its OWN receipt's. A row filed
  under another ``corp_code`` is dropped.
* **Typed fields, as stated.** Amounts are whole won; percentages are the ratios the
  issuer STATES (shares on conversion vs. shares outstanding, deal vs. total assets).
  Nothing is derived. ``-`` is absent. Korean dates become ISO dates.
* **Only company counterparties are named.** A counterparty or target is kept when its
  name carries a company marker (주식회사, (주), Co., Ltd. …). A private individual
  (``강지완``, ``김상곤 외 2인``) is reported as ``individual_or_unnamed``. A lawsuit's
  plaintiff is never carried.
* **One mapping to the trigger vocabulary.** A bond issue (convertible, with warrants,
  exchangeable) whose stated use of funds includes debt repayment is a
  ``maturity_refinanced`` change event. No other decision is forced onto the
  ``event_trigger`` vocabulary; it stays a corporate action.

THE KEY travels only as the ``crtfc_key`` parameter (``opendart.guarded_get``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

API_BASE = "https://opendart.fss.or.kr/api/"
MAX_LIST_PAGES = 3
MAX_ENDPOINTS_PER_RUN = 10
MAX_ACTIONS = 40
MAX_RESPONSE_BYTES = 2_000_000
SOURCE_LABEL = "OpenDART major-matters reports (DART, Financial Supervisory Service of Korea)"

_KR_DATE_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_INT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*$|^-?\d+$")
_PCT_RE = re.compile(r"^-?\d{1,6}(?:\.\d{1,8})?$")
_RCEPT_NO_RE = re.compile(r"^(\d{8})\d{6}$")
_COMPANY_MARKERS = re.compile(
    r"주식회사|\(주\)|㈜|유한회사|합자회사|법인|조합|펀드|공사|은행|증권|"
    r"\b(?:Co|Corp|Corporation|Inc|Ltd|LLC|PLC|GmbH|AG|SA|BV|NV|Fund|Bank|Holdings?)\b\.?",
    re.I)

#: The stated use-of-funds fields shared by capital increases and bond issues.
_FUNDS = {
    "fdpp_fclt": "facilities", "fdpp_bsninh": "business_acquisition",
    "fdpp_op": "operating_funds", "fdpp_dtrp": "debt_repayment",
    "fdpp_ocsa": "acquisition_of_other_securities", "fdpp_etc": "other",
}
_OFFERING = {"사모": "private", "공모": "public"}
_INCREASE_METHOD = {
    "주주배정증자": "rights_issue_to_shareholders",
    "제3자배정증자": "third_party_allotment",
    "일반공모증자": "general_public_offering",
    "주주우선공모증자": "shareholder_priority_public_offering",
}

# Field kinds: int (whole number, won or shares), pct (a stated ratio), date, text,
# company (a counterparty name, kept only when it names a company), enum:<map name>.
_ENUMS: dict[str, dict[str, str]] = {"offering": _OFFERING, "increase": _INCREASE_METHOD}


@dataclass(frozen=True)
class Decision:
    """One major-matters decision type and how its endpoint is read."""

    name: str  # the normalised Korean decision name in the listing
    endpoint: str
    kind: str
    label: str
    fields: dict[str, tuple[str, str]] = field(default_factory=dict)
    funds: bool = False
    #: A bond issue: debt-repayment funds make it a ``maturity_refinanced`` event.
    refinancing_bond: bool = False


_BOND = {"face_amount_krw": ("bd_fta", "int"), "maturity": ("bd_mtd", "date"),
         "offering": ("bdis_mthn", "enum:offering"), "coupon_pct": ("bd_intr_ex", "pct"),
         "yield_to_maturity_pct": ("bd_intr_sf", "pct"), "board_date": ("bddd", "date")}

DECISIONS: tuple[Decision, ...] = (
    Decision("유상증자결정", "piicDecsn", "capital_increase", "Capital increase (paid-in)", {
        "new_common_shares": ("nstk_ostk_cnt", "int"),
        "new_other_shares": ("nstk_estk_cnt", "int"),
        "common_shares_before": ("bfic_tisstk_ostk", "int"),
        "method": ("ic_mthn", "enum:increase")}, funds=True),
    Decision("무상증자결정", "fricDecsn", "bonus_issue", "Bonus issue", {
        "new_common_shares": ("nstk_ostk_cnt", "int"),
        "common_shares_before": ("bfic_tisstk_ostk", "int"),
        "board_date": ("bddd", "date")}),
    Decision("감자결정", "crDecsn", "capital_reduction", "Capital reduction", {
        "reduction_ratio_common_pct": ("cr_rt_ostk", "pct"),
        "common_shares_before": ("bfcr_tisstk_ostk", "int"),
        "common_shares_after": ("atcr_tisstk_ostk", "int"),
        "board_date": ("bddd", "date")}),
    Decision("전환사채권발행결정", "cvbdIsDecsn", "convertible_bond_issue",
             "Convertible bond issue", {
                 **_BOND, "conversion_price_krw": ("cv_prc", "int"),
                 "shares_on_conversion": ("cvisstk_cnt", "int"),
                 "shares_on_conversion_pct_of_total": ("cvisstk_tisstk_vs", "pct")},
             funds=True, refinancing_bond=True),
    Decision("신주인수권부사채권발행결정", "bdwtIsDecsn", "bond_with_warrants_issue",
             "Bond with warrants issue", {
                 **_BOND, "exercise_price_krw": ("ex_prc", "int"),
                 "shares_on_exercise": ("nstk_isstk_cnt", "int"),
                 "shares_on_exercise_pct_of_total": ("nstk_isstk_tisstk_vs", "pct")},
             funds=True, refinancing_bond=True),
    Decision("교환사채권발행결정", "exbdIsDecsn", "exchangeable_bond_issue",
             "Exchangeable bond issue", {
                 **_BOND, "exchange_price_krw": ("ex_prc", "int"),
                 "exchangeable_shares": ("extg_stkcnt", "int"),
                 "exchangeable_shares_pct_of_total": ("extg_tisstk_vs", "pct")},
             funds=True, refinancing_bond=True),
    Decision("자기주식취득결정", "tsstkAqDecsn", "treasury_share_acquisition",
             "Treasury share acquisition", {
                 "planned_common_shares": ("aqpln_stk_ostk", "int"),
                 "planned_amount_krw": ("aqpln_prc_ostk", "int"),
                 "period_start": ("aqexpd_bgd", "date"),
                 "period_end": ("aqexpd_edd", "date")}),
    Decision("자기주식처분결정", "tsstkDpDecsn", "treasury_share_disposal",
             "Treasury share disposal", {
                 "planned_common_shares": ("dppln_stk_ostk", "int"),
                 "planned_amount_krw": ("dppln_prc_ostk", "int"),
                 "price_per_share_krw": ("dpstk_prc_ostk", "int"),
                 "period_start": ("dpprpd_bgd", "date"),
                 "period_end": ("dpprpd_edd", "date")}),
    Decision("자기주식취득신탁계약체결결정", "tsstkAqTrctrCnsDecsn",
             "treasury_share_trust_contract", "Treasury share trust contract", {
                 "contract_amount_krw": ("ctr_prc", "int"),
                 "period_start": ("ctr_pd_bgd", "date"),
                 "period_end": ("ctr_pd_edd", "date"),
                 "board_date": ("bddd", "date")}),
    Decision("유형자산양수결정", "tgastInhDecsn", "asset_acquisition",
             "Tangible asset acquisition", {
                 "amount_krw": ("inhdtl_inhprc", "int"),
                 "total_assets_krw": ("inhdtl_tast", "int"),
                 "pct_of_total_assets": ("inhdtl_tast_vs", "pct"),
                 "counterparty": ("dlptn_cmpnm", "company"),
                 "board_date": ("bddd", "date")}),
    Decision("유형자산양도결정", "tgastTrfDecsn", "asset_transfer", "Tangible asset transfer", {
        "amount_krw": ("trfdtl_trfprc", "int"),
        "total_assets_krw": ("trfdtl_tast", "int"),
        "pct_of_total_assets": ("trfdtl_tast_vs", "pct"),
        "counterparty": ("dlptn_cmpnm", "company"),
        "board_date": ("bddd", "date")}),
    Decision("타법인주식및출자증권양수결정", "otcprStkInvscrInhDecsn", "stake_acquisition",
             "Acquisition of shares in another company", {
                 "amount_krw": ("inhdtl_inhprc", "int"),
                 "pct_of_total_assets": ("inhdtl_tast_vs", "pct"),
                 "target": ("iscmp_cmpnm", "company"),
                 "stake_after_pct": ("atinh_eqrt", "pct"),
                 "counterparty": ("dlptn_cmpnm", "company"),
                 "board_date": ("bddd", "date")}),
    Decision("타법인주식및출자증권양도결정", "otcprStkInvscrTrfDecsn", "stake_disposal",
             "Transfer of shares in another company", {
                 "amount_krw": ("trfdtl_trfprc", "int"),
                 "pct_of_total_assets": ("trfdtl_tast_vs", "pct"),
                 "target": ("iscmp_cmpnm", "company"),
                 "stake_after_pct": ("attrf_eqrt", "pct"),
                 "counterparty": ("dlptn_cmpnm", "company"),
                 "board_date": ("bddd", "date")}),
    Decision("영업양수결정", "bsnInhDecsn", "business_acquisition", "Business acquisition", {
        "amount_krw": ("inh_prc", "int"),
        "acquired_assets_pct_of_company": ("ast_rt", "pct"),
        "counterparty": ("dlptn_cmpnm", "company"),
        "board_date": ("bddd", "date")}),
    Decision("영업양도결정", "bsnTrfDecsn", "business_transfer", "Business transfer"),
    Decision("회사합병결정", "cmpMgDecsn", "merger", "Merger", {
        "partner": ("mgptncmp_cmpnm", "company"),
        "new_common_shares": ("mgnstk_ostk_cnt", "int"),
        "merger_date": ("mgsc_mgdt", "date"),
        "board_date": ("bddd", "date")}),
    Decision("회사분할결정", "cmpDvDecsn", "split", "Company split"),
    Decision("회사분할합병결정", "cmpDvmgDecsn", "split_merger", "Split-merger"),
    Decision("주식교환ㆍ이전결정", "stkExtrDecsn", "share_exchange", "Share exchange or transfer"),
    Decision("소송등의제기", "lwstLg", "litigation", "Litigation filed", {
        "court": ("cpct", "text"), "case": ("icnm", "text"), "filed": ("lgd", "date")}),
    Decision("부도발생", "dfOcr", "default", "Default"),
    Decision("영업정지", "bsnSp", "business_suspension", "Business suspension", {
        "suspended_revenue_krw": ("bsnsp_amt", "int"),
        "company_revenue_krw": ("rsl", "int"),
        "pct_of_revenue": ("sl_vs", "pct"),
        "effective": ("bsnspd", "date"),
        "board_date": ("bddd", "date")}),
    Decision("회생절차개시신청", "ctrcvsBgrq", "rehabilitation_filing",
             "Rehabilitation proceedings filed"),
    Decision("해산사유발생", "dsRsOcr", "dissolution_cause", "Cause of dissolution", {
        "cause": ("ds_rs", "text"), "occurred": ("ds_rsd", "date")}),
    Decision("채권은행등의관리절차개시", "bnkMngtPcbg", "creditor_management",
             "Creditor-bank management procedure"),
)
DECISION_BY_NAME: dict[str, Decision] = {d.name: d for d in DECISIONS}
#: A filing name that differs from the endpoint's own (an exchange-form title).
_ALIASES = {"타법인주식및출자증권취득결정": "타법인주식및출자증권양수결정",
            "타법인주식및출자증권처분결정": "타법인주식및출자증권양도결정"}


def decision_name(report_nm: str | None) -> str:
    """The decision a listed filing names, normalised: no spaces, no amendment prefix,
    no ``주요사항보고서( … )`` wrapper. Pure."""
    name = re.sub(r"\s+", "", report_nm or "")
    name = re.sub(r"^(?:\[[^\]]{1,20}\])+", "", name)
    if name.startswith("주요사항보고서(") and name.endswith(")"):
        name = name[len("주요사항보고서("):-1]
    return _ALIASES.get(name, name)


def decisions_listed(report_names: list[str]) -> list[Decision]:
    """The distinct decision types named in a listing, in the module's order."""
    names = {decision_name(n) for n in report_names}
    return [d for d in DECISIONS if d.name in names]


def _int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    if not _INT_RE.match(text):
        return None
    return int(text.replace(",", ""))


def _pct(value: Any) -> float | None:
    text = str(value if value is not None else "").strip()
    return float(text) if _PCT_RE.match(text) else None


def korean_date(value: Any) -> str | None:
    """``2026년 11월 17일`` → ``2026-11-17``; anything else → ``None``. Pure."""
    found = _KR_DATE_RE.search(str(value or ""))
    if not found:
        return None
    try:
        return date(int(found.group(1)), int(found.group(2)), int(found.group(3))).isoformat()
    except ValueError:
        return None


def _text(value: Any, limit: int = 120) -> str | None:
    from app.schemas.catalyst import neutralize_forbidden_terms

    text = " ".join(str(value or "").split())
    if not text or text == "-":
        return None
    return (neutralize_forbidden_terms(text) or "")[:limit] or None


def is_company_name(name: str | None) -> bool:
    return bool(name and _COMPANY_MARKERS.search(name))


def _value(raw: Any, kind: str) -> Any:
    if kind == "int":
        return _int(raw)
    if kind == "pct":
        return _pct(raw)
    if kind == "date":
        return korean_date(raw)
    if kind == "text":
        return _text(raw)
    if kind == "company":
        text = _text(raw, 80)
        if text is None:
            return None
        return text if is_company_name(text) else "individual_or_unnamed"
    if kind.startswith("enum:"):
        text = re.sub(r"\s+", "", str(raw or ""))
        if not text or text == "-":
            return None
        return _ENUMS[kind[5:]].get(text, "other")
    return None  # pragma: no cover - every kind is listed


def _receipt_date(rcept_no: str) -> str | None:
    found = _RCEPT_NO_RE.match(rcept_no or "")
    if not found:
        return None
    try:
        return datetime.strptime(found.group(1), "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def action_from_row(decision: Decision, row: dict[str, Any], *, corp_code: str
                    ) -> dict[str, Any] | None:
    """One typed corporate action, or ``None`` for a row of another filer. Pure."""
    from app.services.sources.disclosures.opendart import VIEWER_URL

    rcept_no = str(row.get("rcept_no") or "")
    filed = _receipt_date(rcept_no)
    if row.get("corp_code") != corp_code or filed is None:
        return None
    details: dict[str, Any] = {}
    for out, (src, kind) in decision.fields.items():
        value = _value(row.get(src), kind)
        if value is not None:
            details[out] = value
    if decision.funds:
        funds = {label: amount for key, label in _FUNDS.items()
                 if (amount := _int(row.get(key))) is not None and amount > 0}
        if funds:
            details["use_of_funds_krw"] = funds
    return {
        "kind": decision.kind,
        "label": decision.label,
        "date": filed,
        "receipt_no": rcept_no,
        "source_url": VIEWER_URL.format(rcept_no=rcept_no),
        "source_kind": "structured_disclosure",
        "currency": "KRW",
        "details": details,
    }


def refinancing_event(action: dict[str, Any], decision: Decision) -> dict[str, Any] | None:
    """The ``maturity_refinanced`` change event of a bond issue that repays debt."""
    from app.services.discovery.change_events import LABELS

    funds = action.get("details", {}).get("use_of_funds_krw") or {}
    if not decision.refinancing_bond or not funds.get("debt_repayment"):
        return None
    return {
        "trigger_type": "maturity_refinanced",
        "label": LABELS["maturity_refinanced"],
        "date": action["date"],
        "title": f"{decision.label}: KRW {funds['debt_repayment']:,} stated for debt repayment",
        "document_type": "opendart_major_matters",
        "source_url": action["source_url"],
        "source_tier": "T1",
        "source_kind": "structured_disclosure",
        "rule": f"{decision.endpoint}: use of funds includes debt repayment",
    }


def _rows(payload: bytes | None) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        data = json.loads((payload or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, []
    if not isinstance(data, dict):
        return None, []
    rows = data.get("list")
    status = str(data.get("status") or "") or None
    return status, [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def _held_corp_code(session: Any, company: Any) -> str | None:
    from app.services.sources.disclosures import opendart

    code = opendart.krx_code(getattr(company, "ticker", None))
    if not code:
        return None
    rows, _refreshed = await opendart.held_corp_codes(session, code)
    row, _why = opendart.match_issuer(rows, ticker=getattr(company, "ticker", None),
                                      name=getattr(company, "name", None))
    return row.corp_code if row else None


def is_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_opendart_major_matters_enabled", True))


async def corporate_actions(
    session: Any, company: Any, *, cfg: Any, today: date, window_days: int,
    getter: Any = None,
) -> dict[str, Any] | None:
    """``{actions, events, lookup}`` for a Korean issuer, or ``None`` when this source
    does not apply (off, no key, not a held KRX issuer). Never raises."""
    from app.services.sources.disclosures import opendart

    if not is_enabled(cfg) or not opendart.api_key(cfg):
        return None
    try:
        async with session.begin_nested():
            corp_code = await _held_corp_code(session, company)
    except Exception:  # noqa: BLE001
        logger.warning("OpenDART held corp-code lookup failed")
        return None
    if not corp_code:
        return None
    get = getter or opendart.guarded_get
    begin = (today - timedelta(days=window_days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    common = {opendart.KEY_PARAM: opendart.api_key(cfg), "corp_code": corp_code,
              "bgn_de": begin, "end_de": end}
    lookup: dict[str, Any] = {
        "source": SOURCE_LABEL, "corp_code": corp_code, "filings_listed": 0,
        "decision_types": [], "endpoints_read": 0, "endpoints_failed": 0,
        "truncated": False, "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "notes": []}
    names: list[str] = []
    try:
        page = pages = 1
        while page <= min(pages, MAX_LIST_PAGES):
            _code, body, _error = await get(
                opendart.LIST_URL, params={**common, "pblntf_ty": "B", "page_no": str(page),
                                           "page_count": "100"},
                cfg=cfg, max_bytes=MAX_RESPONSE_BYTES, allowed_host=opendart.OPENDART_HOST,
                timeout_seconds=opendart.fetch_timeout(cfg))
            listed, _bad, pages, status = opendart.parse_list_page(body, corp_code=corp_code)
            if listed is None:
                if status != "013":
                    lookup["notes"].append(f"listing unavailable (status {status})")
                    return {"actions": [], "events": [], "lookup": lookup}
                break
            names += [r["report_nm"] for r in listed]
            page += 1
        lookup["truncated"] = pages > MAX_LIST_PAGES
    except Exception as exc:  # noqa: BLE001
        lookup["notes"].append(f"listing failed ({type(exc).__name__})")
        return {"actions": [], "events": [], "lookup": lookup}
    lookup["filings_listed"] = len(names)
    wanted = decisions_listed(names)
    unknown = sorted({decision_name(n) for n in names} - {d.name for d in DECISIONS})
    if unknown:
        lookup["notes"].append(f"{len(unknown)} decision type(s) without a structured reader")
    if len(wanted) > MAX_ENDPOINTS_PER_RUN:
        lookup["truncated"] = True
        wanted = wanted[:MAX_ENDPOINTS_PER_RUN]
    lookup["decision_types"] = [d.kind for d in wanted]

    actions: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for decision in wanted:
        try:
            _code, body, _error = await get(
                API_BASE + decision.endpoint + ".json", params=dict(common), cfg=cfg,
                max_bytes=MAX_RESPONSE_BYTES, allowed_host=opendart.OPENDART_HOST,
                timeout_seconds=opendart.fetch_timeout(cfg))
        except Exception:  # noqa: BLE001
            body = None
        status, rows = _rows(body)
        if body is None or status not in ("000", "013"):
            lookup["endpoints_failed"] += 1
            continue
        lookup["endpoints_read"] += 1
        for row in rows:
            action = action_from_row(decision, row, corp_code=corp_code)
            if action is None or action["receipt_no"] in seen:
                continue
            seen.add(action["receipt_no"])
            actions.append(action)
            event = refinancing_event(action, decision)
            if event is not None:
                events.append(event)
    actions.sort(key=lambda a: (a["date"], a["receipt_no"]), reverse=True)
    if len(actions) > MAX_ACTIONS:
        lookup["truncated"] = True
        actions = actions[:MAX_ACTIONS]
    return {"actions": actions, "events": events, "lookup": lookup}


__all__ = [
    "DECISIONS",
    "DECISION_BY_NAME",
    "Decision",
    "SOURCE_LABEL",
    "action_from_row",
    "corporate_actions",
    "decision_name",
    "decisions_listed",
    "is_company_name",
    "is_enabled",
    "korean_date",
    "refinancing_event",
]
