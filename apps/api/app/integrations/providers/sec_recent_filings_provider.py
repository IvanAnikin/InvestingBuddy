"""
Phase 24 — SEC recent filing event provider.

Fetches recent filing metadata for a company from the SEC EDGAR submissions
endpoint and converts each filing into a source-backed ``CatalystEvent``.

Source tier: T2_regulator_or_gov (SEC filings are regulated disclosures).
API key: none required. U.S. SEC-registered issuers only.
Rate limit: SEC Fair Access ~10 req/s (respected via short timeouts + caps).

Design:
  - ``parse_recent_filings`` is a pure function (no network) so all CI tests run
    offline against mocked submissions JSON.
  - CIK resolution is delegated to ``SecEdgarFundamentalsProvider.resolve_cik``
    to avoid duplicating the ticker→CIK index logic. A CIK may also be supplied
    directly (from the company record / free_real snapshot).
  - No provider failure is fatal: missing CIK, 404, timeout, or parse errors all
    return warnings and an empty/partial event list.

The 8-K item → catalyst mapping and direction defaults live in
``app.services.catalyst_classifier``; this module only extracts filing metadata
and delegates classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import CatalystEvent, make_catalyst_event_id
from app.services.catalyst_classifier import apply_classification

_EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"

# Filing forms treated as candidate catalyst events.
_TARGET_FORMS: frozenset[str] = frozenset(
    {
        "8-K", "8-K/A",
        "10-Q", "10-Q/A",
        "10-K", "10-K/A",
        "6-K", "6-K/A",
        "20-F", "20-F/A",
        "40-F",
        "DEF 14A", "DEFA14A",
        "S-1", "S-3", "S-8",
    }
)


@dataclass
class RecentFilingsResult:
    """Output of the SEC recent filings provider."""

    ticker: str
    cik: str | None = None
    events: list[CatalystEvent] = field(default_factory=list)
    provider: str = "sec_recent_filings"
    retrieved_at: str = ""
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat()


def _pad_cik(cik: str) -> str:
    return str(int(cik)).zfill(10)


def _parse_items(items_value: Any) -> list[str]:
    """
    Parse the SEC ``items`` field into a clean list of item codes.

    SEC provides items as a comma/space-separated string, e.g. "2.02,9.01" or
    "Item 5.02, Item 7.01". We strip the "Item" prefix and whitespace and keep
    dotted numeric codes (e.g. "2.02").
    """
    if not items_value:
        return []
    raw = str(items_value)
    parts = raw.replace("Item", "").replace("item", "").split(",")
    out: list[str] = []
    for p in parts:
        code = p.strip()
        if code:
            out.append(code)
    return out


def _build_document_url(padded_cik: str, accession: str, primary_doc: str) -> str:
    accession_clean = accession.replace("-", "")
    if accession_clean and primary_doc:
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(padded_cik)}/{accession_clean}/{primary_doc}"
        )
    return (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={padded_cik}&type=&dateb=&owner=include&count=40"
    )


def _build_index_url(padded_cik: str, accession: str) -> str:
    accession_clean = accession.replace("-", "")
    if accession_clean:
        return (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={padded_cik}&type=&dateb=&owner=include&count=40"
        )
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}"


def parse_recent_filings(
    submissions_data: dict[str, Any],
    ticker: str,
    cik: str,
    company_name: str | None = None,
    lookback_days: int = 90,
    max_events: int = 20,
) -> list[CatalystEvent]:
    """
    Parse SEC EDGAR submissions JSON into classified CatalystEvent objects.

    Pure function — no network. Filings older than ``lookback_days`` are skipped.
    """
    padded_cik = _pad_cik(cik)
    recent: dict[str, Any] = submissions_data.get("filings", {}).get("recent", {})

    forms: list[str] = recent.get("form", [])
    filing_dates: list[str] = recent.get("filingDate", [])
    report_dates: list[str] = recent.get("reportDate", [])
    accessions: list[str] = recent.get("accessionNumber", [])
    primary_docs: list[str] = recent.get("primaryDocument", [])
    items: list[str] = recent.get("items", [])
    primary_descs: list[str] = recent.get("primaryDocDescription", [])

    cutoff = date.today() - timedelta(days=lookback_days)
    events: list[CatalystEvent] = []

    for i, form in enumerate(forms):
        if form not in _TARGET_FORMS:
            continue

        filing_date = filing_dates[i] if i < len(filing_dates) else ""
        # Lookback filter on the filing date.
        if filing_date:
            try:
                if date.fromisoformat(filing_date) < cutoff:
                    continue
            except ValueError:
                pass

        accession = accessions[i] if i < len(accessions) else ""
        primary_doc = primary_docs[i] if i < len(primary_docs) else ""
        report_date = report_dates[i] if i < len(report_dates) else filing_date
        item_numbers = _parse_items(items[i]) if i < len(items) else []
        doc_desc = primary_descs[i] if i < len(primary_descs) else ""

        document_url = _build_document_url(padded_cik, accession, primary_doc)
        index_url = _build_index_url(padded_cik, accession)

        item_str = (", items " + ", ".join(item_numbers)) if item_numbers else ""
        headline = f"SEC {form} filing — {ticker.upper()} — {filing_date}"
        summary = (
            f"{form} filed {filing_date} (report date {report_date}){item_str}. "
            f"Primary document: {primary_doc or doc_desc or 'see filing index'}."
        )

        event = CatalystEvent(
            id=make_catalyst_event_id(ticker, form, filing_date, accession),
            ticker=ticker.upper(),
            company_name=company_name,
            event_date=report_date or filing_date,
            source_name="SEC EDGAR",
            source_url=document_url,
            source_tier=SourceTier.T2_regulator_or_gov.value,
            provider_name="sec_recent_filings",
            headline=headline,
            summary=summary,
            raw_event_type=form,
            normalized_event_type="sec_filing",
            form_type=form,
            accession_number=accession,
            filing_date=filing_date,
            report_date=report_date,
            item_numbers=item_numbers,
            related_filing_url=index_url,
            related_document_url=document_url,
        )
        events.append(apply_classification(event))

        if len(events) >= max_events:
            break

    return events


class SecRecentFilingsProvider:
    """SEC EDGAR recent-filings catalyst provider (T2_regulator_or_gov)."""

    provider_name = "sec_recent_filings"

    def __init__(self) -> None:
        # Keyed by (TICKER, EXCHANGE): a ticker-only cache would let a US
        # lookup answer for the same ticker on a foreign venue.
        self._cik_cache: dict[tuple[str, str], str] = {}

    async def _resolve_cik(self, ticker: str, exchange: str | None = None) -> str | None:
        """
        Resolve ticker→CIK, delegating to the SEC fundamentals index.

        The exchange must be forwarded: without it a non-US ticker resolves
        against the US-registrant index and this provider would attach another
        company's filings to this company as catalysts (BA.LSE -> Boeing 8-Ks).
        SecExchangeNotSupportedError is caught here and surfaces as "no CIK",
        which the caller already reports as an honest warning.
        """
        upper = ticker.upper()
        key = (upper, (exchange or "").strip().upper())
        if key in self._cik_cache:
            return self._cik_cache[key]
        try:
            from app.integrations.providers.sec_edgar_fundamentals import (
                SecEdgarFundamentalsProvider,
            )

            cik = await SecEdgarFundamentalsProvider().resolve_cik(ticker, exchange)
            self._cik_cache[key] = cik
            return cik
        except Exception:
            return None

    async def _fetch_submissions(self, padded_cik: str) -> dict[str, Any]:
        url = _EDGAR_SUBMISSIONS_URL.format(cik=padded_cik)
        async with httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT}, timeout=15.0
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def get_recent_events(
        self,
        ticker: str,
        cik: str | None = None,
        company_name: str | None = None,
        lookback_days: int = 90,
        max_events: int = 20,
        exchange: str | None = None,
    ) -> RecentFilingsResult:
        """
        Fetch and classify recent SEC filing events for a company.

        Never raises: missing CIK, 404, network, or parse errors are captured as
        warnings so the surrounding workflow always continues.
        """
        resolved_cik = cik or await self._resolve_cik(ticker, exchange)
        if not resolved_cik:
            return RecentFilingsResult(
                ticker=ticker.upper(),
                warnings=[
                    f"SEC CIK not available for {ticker}. Company may not be "
                    "SEC-registered (U.S. only). Recent SEC filing events "
                    "unavailable."
                ],
            )

        padded = _pad_cik(resolved_cik)
        try:
            data = await self._fetch_submissions(padded)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            return RecentFilingsResult(
                ticker=ticker.upper(),
                cik=resolved_cik,
                warnings=[
                    f"SEC EDGAR submissions returned HTTP {status} for CIK {padded} "
                    f"({ticker}). Continuing without SEC filing events."
                ],
            )
        except httpx.HTTPError as exc:
            return RecentFilingsResult(
                ticker=ticker.upper(),
                cik=resolved_cik,
                warnings=[
                    f"SEC EDGAR submissions fetch failed for {ticker} "
                    f"(CIK {padded}): {exc}. Continuing without SEC filing events."
                ],
            )

        try:
            events = parse_recent_filings(
                data,
                ticker=ticker,
                cik=resolved_cik,
                company_name=company_name,
                lookback_days=lookback_days,
                max_events=max_events,
            )
        except Exception as exc:  # defensive — never crash the workflow
            return RecentFilingsResult(
                ticker=ticker.upper(),
                cik=resolved_cik,
                warnings=[f"SEC filing parse failed for {ticker}: {exc}."],
            )

        warnings: list[str] = []
        if not events:
            warnings.append(
                f"No SEC filings for {ticker} in the last {lookback_days} days "
                f"(CIK {padded}). The company may not have filed recently."
            )

        return RecentFilingsResult(
            ticker=ticker.upper(),
            cik=resolved_cik,
            events=events,
            warnings=warnings,
        )
