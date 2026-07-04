"""
Tests for report_validation_service schema path resolution.

All tests run offline — no Azure, EODHD, LLM, or database credentials required.
Covers:
  - local dev path resolution finds real schema
  - env-var override resolves correctly
  - env-var set to bad path raises clear FileNotFoundError
  - missing schema in all candidates raises FileNotFoundError with helpful message
  - deployment-like layout (simulate wwwroot structure) resolves via parents[2]
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[3]
_REAL_CONTRACTS_DIR = (
    _REPO_ROOT / "packages" / "research-contracts"
)
_REAL_SCHEMA = (
    _REAL_CONTRACTS_DIR / "real_asset_equity" / "v1" / "report_schema.json"
)


# ---------------------------------------------------------------------------
# 1. Local dev path resolution finds the real schema
# ---------------------------------------------------------------------------


def test_real_schema_exists_in_repo() -> None:
    """Sanity: the schema file must be present in the repo for any test to mean anything."""
    assert _REAL_SCHEMA.exists(), f"Schema not found at {_REAL_SCHEMA}"


def test_find_schema_path_returns_existing_file() -> None:
    """_find_schema_path() must return a path that actually exists (local dev)."""
    from app.services.report_validation_service import _find_schema_path

    schema_path = _find_schema_path()
    assert schema_path.exists(), f"_find_schema_path() returned non-existent path: {schema_path}"
    assert schema_path.name == "report_schema.json"


def test_find_schema_path_returns_valid_json() -> None:
    """The resolved schema must be valid JSON with a $schema key."""
    from app.services.report_validation_service import _find_schema_path

    schema_path = _find_schema_path()
    with schema_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert "$schema" in data, "report_schema.json is missing the $schema key"


# ---------------------------------------------------------------------------
# 2. RESEARCH_CONTRACTS_DIR env var override
# ---------------------------------------------------------------------------


def test_env_var_override_uses_custom_dir(tmp_path: Path) -> None:
    """When RESEARCH_CONTRACTS_DIR is set to a valid dir, the schema is loaded from there."""
    contracts_dir = tmp_path / "research-contracts"
    schema_dir = contracts_dir / "real_asset_equity" / "v1"
    schema_dir.mkdir(parents=True)
    # Copy real schema into temp dir
    shutil.copy(_REAL_SCHEMA, schema_dir / "report_schema.json")

    with patch.dict(os.environ, {"RESEARCH_CONTRACTS_DIR": str(contracts_dir)}):
        import app.services.report_validation_service as svc_mod

        svc_mod._schema_cache = None

        from app.services.report_validation_service import _find_schema_path

        resolved = _find_schema_path()

    assert resolved == schema_dir / "report_schema.json"
    assert resolved.exists()


def test_env_var_bad_path_raises_clear_error(tmp_path: Path) -> None:
    """When RESEARCH_CONTRACTS_DIR is set but schema is absent, raise FileNotFoundError
    that mentions the env var name — not a confusing generic message."""
    nonexistent = tmp_path / "does-not-exist"

    with patch.dict(os.environ, {"RESEARCH_CONTRACTS_DIR": str(nonexistent)}):
        from app.services.report_validation_service import _find_schema_path
        with pytest.raises(FileNotFoundError) as exc_info:
            _find_schema_path()

    msg = str(exc_info.value)
    assert "RESEARCH_CONTRACTS_DIR" in msg, "Error message must name the env var"
    assert "report_schema.json" not in msg.split("RESEARCH_CONTRACTS_DIR")[0], (
        "Error must mention env var before the path"
    )


# ---------------------------------------------------------------------------
# 3. Missing schema raises FileNotFoundError with candidate paths listed
# ---------------------------------------------------------------------------


def test_missing_schema_error_lists_candidate_paths(tmp_path: Path) -> None:
    """When no candidate path exists, error must list all searched paths."""
    # Point RESEARCH_CONTRACTS_DIR at empty dir with no env var,
    # and mock _THIS_FILE to a location where no parents contain the schema.
    fake_file = tmp_path / "app" / "services" / "report_validation_service.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.touch()

    with patch("app.services.report_validation_service._THIS_FILE", fake_file):
        with patch.dict(os.environ, {}, clear=False):
            env_backup = os.environ.pop("RESEARCH_CONTRACTS_DIR", None)
            try:
                from app.services.report_validation_service import _find_schema_path
                with pytest.raises(FileNotFoundError) as exc_info:
                    _find_schema_path()
            finally:
                if env_backup is not None:
                    os.environ["RESEARCH_CONTRACTS_DIR"] = env_backup

    msg = str(exc_info.value)
    assert "report_schema.json" in msg.lower() or "searched" in msg.lower(), (
        "Error should mention what was searched for"
    )
    assert "RESEARCH_CONTRACTS_DIR" in msg, (
        "Error message should tell the user about the env var override"
    )


# ---------------------------------------------------------------------------
# 4. Deployment-like layout: parents[2] resolution (simulates Azure wwwroot)
# ---------------------------------------------------------------------------


def test_deployment_layout_parents2_resolution(tmp_path: Path) -> None:
    """Simulate the Azure App Service ZIP extraction layout.

    In Azure, the ZIP contains:
      app/services/report_validation_service.py  (from apps/api)
      packages/research-contracts/...            (added separately)

    Extracted to /home/site/wwwroot/:
      wwwroot/app/services/report_validation_service.py
      wwwroot/packages/research-contracts/real_asset_equity/v1/report_schema.json

    So parents[2] == wwwroot, and the schema is at parents[2]/packages/research-contracts/...
    """
    # Build fake wwwroot structure
    wwwroot = tmp_path / "wwwroot"
    fake_service = wwwroot / "app" / "services" / "report_validation_service.py"
    fake_service.parent.mkdir(parents=True)
    fake_service.touch()

    schema_dir = wwwroot / "packages" / "research-contracts" / "real_asset_equity" / "v1"
    schema_dir.mkdir(parents=True)
    shutil.copy(_REAL_SCHEMA, schema_dir / "report_schema.json")

    with patch("app.services.report_validation_service._THIS_FILE", fake_service):
        with patch.dict(os.environ, {}, clear=False):
            env_backup = os.environ.pop("RESEARCH_CONTRACTS_DIR", None)
            try:
                from app.services.report_validation_service import _find_schema_path
                resolved = _find_schema_path()
            finally:
                if env_backup is not None:
                    os.environ["RESEARCH_CONTRACTS_DIR"] = env_backup

    expected = schema_dir / "report_schema.json"
    assert resolved == expected, (
        f"Expected Azure wwwroot layout resolution to {expected}, got {resolved}"
    )


# ---------------------------------------------------------------------------
# 5. Schema loads and parses correctly in deployment-like layout
# ---------------------------------------------------------------------------


def test_deployment_layout_schema_is_valid_json(tmp_path: Path) -> None:
    """In deployment-like layout, _load_schema() returns a valid dict with $schema key."""
    wwwroot = tmp_path / "wwwroot"
    fake_service = wwwroot / "app" / "services" / "report_validation_service.py"
    fake_service.parent.mkdir(parents=True)
    fake_service.touch()

    schema_dir = wwwroot / "packages" / "research-contracts" / "real_asset_equity" / "v1"
    schema_dir.mkdir(parents=True)
    shutil.copy(_REAL_SCHEMA, schema_dir / "report_schema.json")

    with patch("app.services.report_validation_service._THIS_FILE", fake_service):
        with patch.dict(os.environ, {}, clear=False):
            env_backup = os.environ.pop("RESEARCH_CONTRACTS_DIR", None)
            import app.services.report_validation_service as svc_mod
            svc_mod._schema_cache = None
            try:
                from app.services.report_validation_service import _load_schema
                schema = _load_schema()
            finally:
                svc_mod._schema_cache = None
                if env_backup is not None:
                    os.environ["RESEARCH_CONTRACTS_DIR"] = env_backup

    assert isinstance(schema, dict)
    assert "$schema" in schema
