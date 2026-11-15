"""Company research steps: GLEIF Level-2 parents (V3.20 slice 2.2) and SEC Exhibit 21 /
13D / 13G (slice 2.3), spec §3.2.

Joins the network client (``app.integrations.gleif_level2``) to the writer
(``app.services.entities.gleif_parents``), which may not import one. Never raises: a
registry timing out costs this step and is said in the outcome, not the run.

``_live_lookup``, ``_live_promote`` (GLEIF) and ``_live_sec_lookup`` / ``_live_cik``
(SEC EDGAR) are the network paths; the test conftest replaces them so no unit test
reaches either registry.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.integrations.gleif_level2 import ParentReport, fetch_parent_report
from app.integrations.sec_relationships import SecRelationshipReport, fetch_sec_relationships
from app.services.entities.gleif_parents import refresh_company_parents
from app.services.entities.sec_relationships import record_sec_relationships


async def _live_lookup(lei: str) -> ParentReport:
    return await fetch_parent_report(lei)


async def _live_promote(session: Any, entity_id: uuid.UUID, *, cfg: Any) -> Any:
    from app.integrations.entity_identifier_sources import GleifIdentifierSource
    from app.integrations.providers.gleif_provider import GleifProvider
    from app.services.entities.promotion import promote_entity_identity

    return await promote_entity_identity(
        session,
        legal_entity_id=entity_id,
        sources=[GleifIdentifierSource(provider=GleifProvider())],
        cfg=cfg,
    )


async def gleif_parents_step(session: Any, company: Any, *, cfg: Any) -> dict[str, Any]:
    """Refresh the company's GLEIF parents; the result as a dict for the outcome."""
    # Plain values first: a SAVEPOINT rollback below expires ORM objects, and touching
    # an expired one raises MissingGreenlet instead of the real error.
    subject = SimpleNamespace(
        id=getattr(company, "id", None),
        legal_entity_id=getattr(company, "legal_entity_id", None),
    )
    promote = None
    if getattr(cfg, "v3_gleif_lei_promotion_enabled", False):
        async def promote(s: Any, entity_id: uuid.UUID) -> Any:
            return await _live_promote(s, entity_id, cfg=cfg)

    try:
        async with session.begin_nested():
            result = await refresh_company_parents(
                session, company=subject, cfg=cfg, lookup=_live_lookup, promote=promote,
            )
    except Exception as exc:  # noqa: BLE001 - the step degrades, the run goes on
        return {"state": "error", "errors": [f"GLEIF parent step failed ({type(exc).__name__})"]}
    return result.to_dict()


async def _live_sec_lookup(cik: str, registrant_name: str | None) -> SecRelationshipReport:
    return await fetch_sec_relationships(cik, registrant_name=registrant_name)


async def _live_annual_ixbrl(cik: str, annual: dict[str, Any]) -> Any:
    from app.integrations.sec_relationships import fetch_annual_inline_xbrl

    return await fetch_annual_inline_xbrl(cik, annual)


CONCENTRATION_STRATEGY = "customer_concentration_v1"


async def _attempted(session: Any, company_id: Any, url: str) -> bool:
    from sqlalchemy import select

    from app.models.document_ingestion_attempt import DocumentIngestionAttempt
    from app.services.document_ingestion_attempt_service import url_hash_of

    if company_id is None:
        return False
    return (await session.execute(
        select(DocumentIngestionAttempt.id).where(
            DocumentIngestionAttempt.company_id == company_id,
            DocumentIngestionAttempt.url_hash == url_hash_of(url),
            DocumentIngestionAttempt.discovery_strategy == CONCENTRATION_STRATEGY,
        ).limit(1)
    )).first() is not None


async def _record_attempt(session: Any, company_id: Any, url: str, cfg: Any) -> None:
    from app.models.document_ingestion_attempt import STATUS_EXTRACTED
    from app.services.document_ingestion_attempt_service import (
        IngestionAttemptRecord,
        record_ingestion_attempts,
    )

    await record_ingestion_attempts(
        session, company_id=company_id, agent_run_id=None, cfg=cfg,
        attempts=[IngestionAttemptRecord(
            canonical_url=url, source_type="sec_filing", source_tier="T2_regulator_or_gov",
            status=STATUS_EXTRACTED, discovery_strategy=CONCENTRATION_STRATEGY,
        )],
    )


async def customer_concentration_part(
    session: Any, *, company_id: Any, entity_id: Any, cik: str, annual: dict[str, Any] | None,
    cfg: Any,
) -> dict[str, Any]:
    """V3.20 slice 2.4 — the newest 10-K's tagged customer concentration, once per filing."""
    from app.services.entities.sec_relationships import (
        ixbrl_already_read,
        record_concentrations,
    )
    from app.services.sources.customer_concentration import concentrations_from_inline_xbrl
    from app.services.sources.sec_filing_documents import build_document_url

    if not getattr(cfg, "v3_xbrl_concentration_enabled", False):
        return {}
    if not annual or annual.get("form") != "10-K":
        return {"state": "not_applicable",
                "notes": ["customer concentration is read from a 10-K's inline XBRL"]}
    url = build_document_url(cik, annual.get("accession"), annual.get("primary_document"))
    accession = str(annual.get("accession") or "")
    if url is None:
        return {"state": "error", "errors": ["annual report URL could not be built"]}
    if await ixbrl_already_read(session, entity_id=entity_id, accession=accession) or (
        await _attempted(session, company_id, url)
    ):
        return {"state": "already_read", "accession": accession}
    read = await _live_annual_ixbrl(cik, annual)
    if read.error or read.document is None:
        return {"state": "error", "accession": accession,
                "errors": [read.error or "annual report unread"]}
    items = concentrations_from_inline_xbrl(read.document)
    async with session.begin_nested():
        written = await record_concentrations(
            session, entity_id=entity_id, concentrations=items, annual=annual,
            source_url=url, cfg=cfg)
        await _record_attempt(session, company_id, url, cfg)
    return {
        "state": "written" if written else "none_tagged", "accession": accession,
        "source_url": url, "concentrations": written,
        **({} if written else {"notes": [
            "the 10-K tags no customer concentration (ConcentrationRiskPercentage1 on "
            "MajorCustomersAxis); not disclosed in XBRL is not the same as none"]}),
    }


async def _live_cik(ticker: str, exchange: str | None) -> str | None:
    from app.integrations.providers.sec_recent_filings_provider import (
        SecRecentFilingsProvider,
    )

    return await SecRecentFilingsProvider()._resolve_cik(ticker, exchange)


async def sec_relationships_step(session: Any, company: Any, *, cfg: Any) -> dict[str, Any]:
    """Subsidiaries (Exhibit 21 / 8) and 13D/13G holders for an SEC registrant.

    ``{}`` when the step is off. Reads first, writes inside a SAVEPOINT; never raises.
    """
    if not (getattr(cfg, "v3_sec_relationships_enabled", False)
            and getattr(cfg, "v3_entity_master_enabled", False)):
        return {}
    entity_id = getattr(company, "legal_entity_id", None)
    if entity_id is None:
        return {"state": "entity_unresolved",
                "notes": ["the company is not linked to a legal entity"]}
    try:
        from sqlalchemy import select

        from app.models.legal_entity import LegalEntity
        from app.services.entities.sec_listing import sec_listing_for

        basis = await sec_listing_for(session, company, cfg=cfg)
        if basis is None:
            return {"state": "not_sec_registrant"}
        cik = basis.cik or await _live_cik(basis.ticker, basis.exchange)
        if not cik:
            return {"state": "not_sec_registrant",
                    "notes": [f"no CIK resolved for {basis.ticker}"]}
        name = (await session.execute(
            select(LegalEntity.legal_name).where(LegalEntity.id == entity_id)
        )).scalar_one_or_none()
        report = await _live_sec_lookup(str(cik), name)
        async with session.begin_nested():
            result = await record_sec_relationships(
                session, entity_id=entity_id, report=report, cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - the step degrades, the run goes on
        return {"state": "error",
                "errors": [f"SEC relationship step failed ({type(exc).__name__})"]}
    out = result.to_dict()
    out["cik"] = str(cik)
    try:
        concentration = await customer_concentration_part(
            session, company_id=getattr(company, "id", None), entity_id=entity_id,
            cik=str(cik), annual=getattr(report, "annual", None), cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - this part degrades alone
        concentration = {"state": "error",
                         "errors": [f"customer concentration failed ({type(exc).__name__})"]}
    if concentration:
        out["customer_concentration"] = concentration
    return out


__all__ = ["gleif_parents_step", "sec_relationships_step"]
