"""Company research step: GLEIF Level-2 parents — V3.20 slice 2.2 (spec §3.2).

Joins the network client (``app.integrations.gleif_level2``) to the writer
(``app.services.entities.gleif_parents``), which may not import one. Never raises: a
registry timing out costs this step and is said in the outcome, not the run.

``_live_lookup`` and ``_live_promote`` are the two network paths; the test conftest
replaces both so no unit test reaches GLEIF.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.integrations.gleif_level2 import ParentReport, fetch_parent_report
from app.services.entities.gleif_parents import refresh_company_parents


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


__all__ = ["gleif_parents_step"]
