#!/usr/bin/env python3
"""Run the real V3 pipeline and write the payload the web actually receives.

WHY THIS EXISTS
===============
The V3 result crosses a Python -> JSONB -> TypeScript seam, and nothing type-checks
across it. A renamed key does not fail a build; it renders as a silently blank field.

That is not hypothetical. The first version of the web reader looked for
``rejection_detail``; the producer writes ``detail``. The e2e fixture had been written
from the READER rather than from the producer, so the fixture agreed with the mistake and
the whole suite passed green.

This script generates the fixture from the producer. Regenerate it whenever
``V3ResearchOutcome`` changes:

    python scripts/v3-dump-real-payload.py

It runs the real pipeline against an in-memory SQLite database with no model configured,
so it needs no credentials, no network and no live database. The run degrades — that is
the point of degrading honestly — and the payload still carries every key.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
import uuid
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Importing the FastAPI app is what registers EVERY model on the metadata. Importing
# `app.models` alone is not enough — `scorecards` has a foreign key to
# `screening_candidates`, whose module that package does not reach, and `create_all`
# then fails resolving it.
import app.main  # noqa: E402, F401 - registers every table via the app's imports
from app.core.config import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.services.classification import service as classification_service  # noqa: E402
from app.services.pipeline.v3_pipeline import run_v3_research  # noqa: E402

#: Where the web's parser test reads it from.
FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "web/tests/fixtures/v3-research-payload.json"
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(element: Any, compiler: Any, **kw: Any) -> str:  # noqa: ANN401
    return "JSON"


async def _generate() -> dict[str, Any]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    # The one external boundary this script pins, for the same reason it pins the model
    # credentials to "": the fixture must be a property of the code, not of whether
    # data.sec.gov answered today. The values are exactly what the submissions endpoint
    # returns for Moderna's CIK 1682852 — everything downstream of here is the real
    # resolver, the real supersede rule and the real persistence.
    async def _sec_submissions_for_cik_1682852(ticker: str, exchange: str | None):
        return "2836", "Biological Products, (No Diagnostic Substances)", None

    classification_service._fetch_sec_classification = _sec_submissions_for_cik_1682852

    async with maker() as session:
        # UNCLASSIFIED, which is what every company row in production actually looks
        # like: 70 of 71 have no sector and none has an industry. Pre-filling them here
        # would have made the fixture prove the one thing that was never in doubt.
        company = Company(
            id=uuid.UUID("00000000-0000-4000-8000-0000000000c1"),
            ticker="MRNA",
            exchange="NASDAQ",
            name="Moderna, Inc.",
            status="new",
        )
        session.add(company)
        await session.flush()
        cfg = Settings(
            v3_pipeline_enabled=True,
            v3_agent_tools_enabled=True,
            # Stated explicitly, both ends. A bare Settings() reads the developer's
            # `.env`, which would make this fixture a property of one machine.
            azure_openai_api_key="",
            azure_openai_endpoint="",
            deepseek_api_key="",
        )
        outcome = await run_v3_research(session, company, cfg=cfg)
        payload = outcome.to_dict()
    await engine.dispose()
    return payload


_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)


def _pin(value: Any, ids: dict[str, str]) -> Any:
    """Replace every generated id and elapsed time with a stable stand-in.

    Without this, regenerating rewrites forty random UUIDs and the diff says nothing.
    The fixture exists to pin the SHAPE, so the diff has to show shape changes and
    nothing else — otherwise the next person skims past a renamed key.

    Ids are numbered in first-seen order, so a stable payload gives a stable file and
    an id that MOVES still shows up.
    """
    if isinstance(value, dict):
        return {k: _pin(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_pin(v, ids) for v in value]
    if isinstance(value, float):
        # Every float in this payload is a duration.
        return 0.0
    if isinstance(value, str) and _UUID_RE.fullmatch(value):
        if value not in ids:
            ids[value] = f"00000000-0000-4000-8000-{len(ids) + 1:012d}"
        return ids[value]
    return value


def main() -> int:
    payload = _pin(asyncio.run(_generate()), {})

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
    print(f"top-level keys: {len(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
