"""
Offline validation of real-asset equity research reports against the
packages/research-contracts/real_asset_equity/v1/report_schema.json contract.

No network calls, no database access, no LLM calls.
Requires: jsonschema>=4.23 (Draft 2020-12 support).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_DATA_QUALITY_WARN = "D_weak_or_stale"
_DECISION_CRITICAL_FIELDS = {
    "snapshot_financials",
    "real_asset_block",
    "financials_deep",
    "valuation",
    "scoring",
}

_SCHEMA_SUFFIX = Path("real_asset_equity") / "v1" / "report_schema.json"
_THIS_FILE = Path(__file__)


def _find_schema_path() -> Path:
    """Resolve report_schema.json from multiple candidate locations.

    Priority:
    1. RESEARCH_CONTRACTS_DIR env var (explicit override)
    2. Sibling packages/ dir — Azure App Service (ZIP extracts api/* to wwwroot/)
    3. Repo-root packages/ — local dev (file is 4 levels below repo root)
    4. apps/ sibling packages/ — intermediate layout
    """
    env_dir = os.environ.get("RESEARCH_CONTRACTS_DIR")
    if env_dir:
        candidate = Path(env_dir) / _SCHEMA_SUFFIX
        if candidate.exists():
            return candidate
        raise FileNotFoundError(
            f"RESEARCH_CONTRACTS_DIR={env_dir!r} is set but schema not found at {candidate}. "
            "Check the RESEARCH_CONTRACTS_DIR value."
        )

    candidates = [
        # Azure App Service: ZIP root is wwwroot; parents[2] == wwwroot
        _THIS_FILE.parents[2] / "packages" / "research-contracts" / _SCHEMA_SUFFIX,
        # Local dev: repo root is 4 levels up from apps/api/app/services/
        _THIS_FILE.parents[4] / "packages" / "research-contracts" / _SCHEMA_SUFFIX,
        # apps/ sibling layout: parents[3] == apps/, parents[3].parent == repo root
        _THIS_FILE.parents[3] / "packages" / "research-contracts" / _SCHEMA_SUFFIX,
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    paths_checked = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"Report schema not found. Searched:\n  {paths_checked}\n"
        "To fix: set RESEARCH_CONTRACTS_DIR to the research-contracts directory, "
        "or ensure packages/research-contracts/real_asset_equity/v1/ is present "
        "relative to the deployment root."
    )


def _load_schema() -> dict[str, Any]:
    schema_path = _find_schema_path()
    with schema_path.open(encoding="utf-8") as fh:
        return json.load(fh)


# Cache schema after first load (module-level, lives for process lifetime).
_schema_cache: dict[str, Any] | None = None


def _get_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = _load_schema()
    return _schema_cache


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Public validation function
# ---------------------------------------------------------------------------


def validate_real_asset_report(report_data: dict[str, Any]) -> ValidationResult:
    """Validate a real-asset equity report dict against the v1 JSON Schema.

    Performs two passes:
    1. Structural validation via jsonschema Draft 2020-12 — yields errors.
    2. Data-quality scan — any datapoint with data_quality == D_weak_or_stale
       inside a decision-critical section yields a warning (not a hard error).

    Args:
        report_data: The report JSON as a Python dict.

    Returns:
        ValidationResult with is_valid, errors, and warnings.
    """
    schema = _get_schema()
    errors: list[str] = []
    warnings: list[str] = []

    # --- Pass 1: structural / schema validation ---
    try:
        validator = Draft202012Validator(schema)
        for error in sorted(validator.iter_errors(report_data), key=lambda e: list(e.path)):
            path = " → ".join(str(p) for p in error.absolute_path) or "(root)"
            errors.append(f"[{path}] {error.message}")
    except SchemaError as exc:
        errors.append(f"[schema_error] {exc.message}")

    is_valid = len(errors) == 0

    # --- Pass 2: data-quality warnings (only if structure is valid) ---
    if is_valid:
        warnings.extend(_scan_data_quality_warnings(report_data))

    return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _scan_data_quality_warnings(report_data: dict[str, Any]) -> list[str]:
    """Walk decision-critical sections and collect D_weak_or_stale datapoints."""
    found: list[str] = []
    for section_key in _DECISION_CRITICAL_FIELDS:
        section = report_data.get(section_key)
        if isinstance(section, dict):
            _walk_for_weak(section, path=section_key, out=found)
    return found


def _walk_for_weak(node: Any, path: str, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("data_quality") == _DATA_QUALITY_WARN:
            out.append(
                f"[data_quality_warning] {path}: "
                f"value={node.get('value')!r} is marked D_weak_or_stale "
                f"(source: {node.get('source_name', 'unknown')})"
            )
        else:
            for k, v in node.items():
                _walk_for_weak(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_for_weak(item, f"{path}[{i}]", out)
