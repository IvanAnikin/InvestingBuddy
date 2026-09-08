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
    async with maker() as session:
        company = Company(
            id=uuid.UUID("00000000-0000-4000-8000-0000000000c1"),
            ticker="MRNA",
            exchange="NASDAQ",
            name="Moderna, Inc.",
            status="new",
            sector="Health Care",
            industry="Biotechnology",
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


def main() -> int:
    payload = asyncio.run(_generate())
    # The run id and elapsed time are the only fields that differ between runs.
    # Pinned so regenerating produces a clean diff of the SHAPE, which is the point.
    payload["research_run_id"] = "00000000-0000-4000-8000-0000000000a1"
    payload["elapsed_seconds"] = 0.0
    for key in ("loop", "consumption"):
        if isinstance(payload.get(key), dict) and "elapsed_seconds" in payload[key]:
            payload[key]["elapsed_seconds"] = 0.0
    if isinstance(payload.get("council"), dict):
        payload["council"]["research_run_id"] = "00000000-0000-4000-8000-0000000000a1"

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
    print(f"top-level keys: {len(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
