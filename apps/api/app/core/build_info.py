"""
Build / release metadata surfaced by the /health endpoint (Phase 19.2.1).

The deploy workflow writes ``build_info.json`` into the deployment ZIP with the
git commit SHA, a build id and a build timestamp. The API reads it at startup
and exposes ``commit_sha`` / ``build_id`` on /health so the deploy smoke check
can confirm the NEW container is actually serving — avoiding the false-green
case where Azure routes /health to the OLD container during async recycle.

Resolution order (first hit wins):
  1. ``build_info.json`` bundled next to the app package (written at deploy time).
  2. Environment variables (GIT_SHA / BUILD_ID / BUILD_TIME) as a fallback.
  3. "unknown" — normal for local development where no build metadata exists.

No secrets, credentials, or connection strings are ever stored here — only the
public commit SHA and build identifiers.
"""

from __future__ import annotations

import json
import os
import pathlib

_UNKNOWN = "unknown"


def _load_build_info() -> dict[str, str]:
    """Load build metadata from build_info.json or environment, never raising."""
    here = pathlib.Path(__file__).resolve()
    # Search a few parent levels: app/core → app → wwwroot (deployment root).
    for parent in here.parents[:5]:
        candidate = parent / "build_info.json"
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return {
                    "commit_sha": str(data.get("commit_sha") or _UNKNOWN),
                    "build_id": str(data.get("build_id") or _UNKNOWN),
                    "build_time": str(data.get("build_time") or _UNKNOWN),
                }
            except (ValueError, OSError):
                break

    return {
        "commit_sha": os.getenv("GIT_SHA") or os.getenv("BUILD_SHA") or _UNKNOWN,
        "build_id": os.getenv("BUILD_ID") or _UNKNOWN,
        "build_time": os.getenv("BUILD_TIME") or _UNKNOWN,
    }


BUILD_INFO: dict[str, str] = _load_build_info()


#: Where ``startup.sh`` records the outcome of ``alembic upgrade head`` at container start.
MIGRATION_STATUS_FILE = os.getenv("MIGRATION_STATUS_FILE", "/tmp/ib_migration_status.json")


def migration_status() -> dict[str, str | None]:
    """What the container's startup migration did: ``state`` ok | failed | skipped | unknown.

    Read on each call (the file is written before the API starts). ``unknown`` when the
    app was started without ``startup.sh`` (local development, tests). Never raises and
    never contains anything but a state, a revision id and a timestamp.
    """
    try:
        data = json.loads(pathlib.Path(MIGRATION_STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": _UNKNOWN, "revision": None, "at": None}
    state = str(data.get("state") or _UNKNOWN)
    if state not in ("ok", "failed", "skipped"):
        state = _UNKNOWN
    revision = data.get("revision")
    return {"state": state,
            "revision": str(revision)[:64] if revision else None,
            "at": str(data.get("at"))[:32] if data.get("at") else None}
