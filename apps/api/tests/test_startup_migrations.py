"""startup.sh runs ``alembic upgrade head`` before gunicorn and records the outcome.

2026-11-22: a release with a new column was auto-deployed without its migration and
served 500s until the migration was run by hand. startup.sh is now the App Service
startup command; the outcome is read by /health and checked by the deploy workflow.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "startup.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _fake_bin(tmp: Path, *, upgrade_ok: bool) -> Path:
    """python (alembic), timeout and gunicorn stand-ins that record their arguments."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    calls = tmp / "calls.log"
    (bin_dir / "python").write_text(
        "#!/usr/bin/env bash\n"
        f'echo "python $*" >> "{calls}"\n'
        'if [ "$3" = "upgrade" ]; then exit ' + ("0" if upgrade_ok else "1") + "; fi\n"
        'if [ "$3" = "current" ]; then echo "064 (head)"; fi\n'
    )
    (bin_dir / "timeout").write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
    (bin_dir / "gunicorn").write_text(f'#!/usr/bin/env bash\necho "gunicorn $*" >> "{calls}"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return bin_dir


def _run(tmp: Path, *, upgrade_ok: bool = True, run: str = "true") -> tuple[dict, str]:
    bin_dir = _fake_bin(tmp, upgrade_ok=upgrade_ok)
    status = tmp / "status.json"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "MIGRATION_STATUS_FILE": str(status), "RUN_MIGRATIONS_ON_START": run}
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True, timeout=30)
    return json.loads(status.read_text()), (tmp / "calls.log").read_text()


def test_migrates_then_starts_gunicorn(tmp_path: Path) -> None:
    status, calls = _run(tmp_path)
    assert status["state"] == "ok" and status["revision"] == "064"
    lines = calls.splitlines()
    assert lines[0] == "python -m alembic upgrade head"
    assert lines[-1].startswith("gunicorn ") and "app.main:app" in lines[-1]


def test_a_failed_migration_is_recorded_and_the_api_still_starts(tmp_path: Path) -> None:
    status, calls = _run(tmp_path, upgrade_ok=False)
    assert status == {"state": "failed", "revision": None, "at": status["at"]}
    assert calls.splitlines()[-1].startswith("gunicorn ")


def test_the_step_can_be_switched_off(tmp_path: Path) -> None:
    status, calls = _run(tmp_path, run="false")
    assert status["state"] == "skipped"
    assert "alembic" not in calls
