"""Resolve a company's classification, persist it, and never weaken what is stored.

THE ONE WRITER
==============
``companies.sector`` / ``.industry`` are written here and nowhere else. That is the point:
a classification with a provenance tier beside it is only trustworthy while every writer
respects the tier, and the cheapest way to guarantee that is to have one writer.

Both callers use this same function, which is what makes it a single handoff rather than
a second classification system running beside the first:

* ``company_analysis`` already holds a freshly fetched SEC profile and passes it in — no
  extra request.
* ``v3_pipeline`` holds no profile. It reads what is stored, and fetches only when the
  row has never been classified, so the SEC is asked once per company rather than once
  per run.

THE SUPERSEDE RULE
==================
A write happens only when the new answer is at least as well-sourced as the stored one.
The concrete failure this prevents: the SEC is briefly unreachable, the run falls back to
inferring an industry from a description, and a ``T6_model_estimate`` guess overwrites the
``T2_regulator_or_gov`` classification — permanently, because nothing afterwards knows
the row used to be better.

Unknown never overwrites known, for the same reason.

WHAT IT DOES NOT DO
===================
Guess for the issuers it cannot reach. ``resolve_cik`` refuses non-US exchanges before it
makes any request, so a European issuer with no SEC registration produces no SIC, no
canonical industry, and a recorded reason — not the nearest-looking US company's code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.classification.resolver import (
    CompanyClassification,
    resolve_classification,
    tier_rank,
)

logger = logging.getLogger(__name__)


async def _fetch_sec_classification(
    ticker: str, exchange: str | None
) -> tuple[str | None, str | None, str | None]:
    """``(sic_code, sic_description, failure_reason)`` from the SEC, best effort.

    Never raises. A failure is a returned reason, because classification degrading is
    not a reason to lose a research run.
    """
    if not ticker:
        return None, None, "no ticker to resolve"
    try:
        from app.integrations.providers.sec_edgar_fundamentals import (
            SecEdgarFundamentalsProvider,
        )
        from app.integrations.providers.sec_edgar_provider import SecEdgarProvider

        cik = await SecEdgarFundamentalsProvider().resolve_cik(ticker, exchange)
        if not cik:
            return None, None, f"no SEC CIK for {ticker}"
        profile = await SecEdgarProvider().get_company_by_cik(cik)
        return (
            getattr(profile, "sic_code", None),
            getattr(profile, "industry", None),
            None,
        )
    except Exception as exc:  # noqa: BLE001 — classification must not end a run
        # Includes SecExchangeNotSupportedError for every non-US issuer, which is the
        # correct and expected outcome rather than an error worth alarming about.
        return None, None, f"{type(exc).__name__}: {exc}"


def classification_of(company: Any) -> CompanyClassification:
    """The classification currently stored on a company row. No network, no resolve."""
    return CompanyClassification(
        sector=getattr(company, "sector", None),
        industry=getattr(company, "industry", None),
        sector_raw=getattr(company, "sector", None),
        industry_raw=getattr(company, "industry_raw", None),
        sic_code=getattr(company, "sic_code", None),
        tier=getattr(company, "classification_tier", None),
        source="stored",
    )


async def ensure_company_classification(
    session: Any,
    company: Any,
    *,
    sec_profile: Any | None = None,
    allow_fetch: bool = True,
) -> CompanyClassification:
    """Resolve, persist under the supersede rule, and return the effective answer.

    ``sec_profile`` — any object exposing ``sic_code`` / ``industry`` — lets a caller
    that has already paid for the SEC request contribute it instead of a second fetch.

    Never raises. Returns the classification the company now has, which on a repeat run
    is the stored one reached without any network call at all.
    """
    stored = classification_of(company)
    notes: list[str] = []

    sic_code = stored.sic_code
    sic_description: str | None = None

    if sec_profile is not None:
        # A caller-supplied profile is an optimisation, never a blocker. `free_real`
        # falls back to a market-data aggregator whenever SEC ticker lookup is not
        # implemented, so the profile a caller holds frequently carries an industry
        # string and no SIC code at all — and treating "a profile was supplied" as
        # "classification was sourced" would skip the one authoritative source.
        sic_code = getattr(sec_profile, "sic_code", None) or sic_code
        sic_description = getattr(sec_profile, "industry", None)
    if sic_code:
        notes.append(f"Reusing SEC SIC {sic_code} — no classification request needed.")
    elif allow_fetch:
        fetched_code, fetched_desc, reason = await _fetch_sec_classification(
            getattr(company, "ticker", "") or "", getattr(company, "exchange", None)
        )
        if fetched_code:
            sic_code = fetched_code
            sic_description = fetched_desc or sic_description
        elif reason:
            notes.append(f"No SEC classification available ({reason}).")

    resolved = resolve_classification(
        sic_code=sic_code,
        sic_description=sic_description,
        stored_sector=stored.sector,
        stored_industry=stored.industry,
        stored_industry_raw=stored.industry_raw,
        stored_tier=stored.tier,
    )
    resolved.notes = notes + resolved.notes

    # ── The supersede rule ───────────────────────────────────────────────
    if stored.is_known and not resolved.is_known:
        stored.notes = [
            *notes,
            "Kept the stored classification: this run resolved nothing, and unknown "
            "never overwrites known.",
        ]
        return stored
    if stored.is_known and tier_rank(resolved.tier) > tier_rank(stored.tier):
        stored.notes = [
            *notes,
            f"Kept the stored {stored.tier} classification: this run could only reach "
            f"{resolved.tier}, and a weaker source never overwrites a stronger one.",
        ]
        return stored

    changed = (
        resolved.sector != stored.sector
        or resolved.industry != stored.industry
        or resolved.industry_raw != stored.industry_raw
        or resolved.sic_code != stored.sic_code
        or resolved.tier != stored.tier
    )
    if changed and resolved.is_known:
        # SAVEPOINT, not a bare try/except.
        #
        # ``except Exception`` cannot un-abort a PostgreSQL transaction: once a statement
        # fails, every later statement on that connection raises until something rolls
        # back. Earlier in this campaign a single bad INSERT taken that way destroyed the
        # report the run had already produced — the exception was caught, the run
        # continued, and every write after it failed silently.
        #
        # Classification is the least important thing this transaction is carrying. It
        # gets a savepoint so that when it fails it costs only itself.
        # Read anything needed for the failure path BEFORE the savepoint. Rolling one
        # back EXPIRES the instances it touched, so `company.ticker` in the log line
        # below would issue a lazy SELECT — synchronously, outside the greenlet the
        # async engine needs — and raise `MissingGreenlet` from inside the handler for
        # the original error. The real failure would then be replaced by a confusing
        # one, in the one path whose entire job is to fail quietly.
        ticker = getattr(company, "ticker", "?")
        try:
            async with session.begin_nested():
                company.sector = resolved.sector
                company.industry = resolved.industry
                company.industry_raw = resolved.industry_raw
                company.sic_code = resolved.sic_code
                company.classification_tier = resolved.tier
                company.classification_updated_at = datetime.now(timezone.utc)
                await session.flush()
        except Exception:  # noqa: BLE001 — a failed persist must not end the run
            logger.exception("Could not persist classification for %s", ticker)
            resolved.notes.append(
                "Classification resolved but could not be persisted; this run uses it "
                "and the next run will resolve it again."
            )

    return resolved


__all__ = ["classification_of", "ensure_company_classification"]
