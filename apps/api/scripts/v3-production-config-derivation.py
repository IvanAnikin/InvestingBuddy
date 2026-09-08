#!/usr/bin/env python3
"""What a given production configuration actually turns on. Prints no secret.

WHY THIS EXISTS
===============
Every V3 activation question so far has been answered by reading a flag name and
assuming what it does. That is how this campaign produced its worst finding — a
credential in ``.env`` silently re-routed the Investigator off Azure OpenAI, because a
key was treated as consent. So before any setting is written to Azure App Service, the
configuration is DERIVED here: which tools register, which model legs resolve, which
questions the Director can staff, and whether the external research chain is complete.

WHAT IT NEVER DOES
==================
It never prints, logs or returns a credential. Credentials are reduced to the single bit
that matters — present or absent — before anything is rendered, and the ``Settings``
fields themselves are ``repr=False``. Run it with ``--check-secret-safety`` to assert
that property rather than trust it.

USAGE
    python scripts/v3-production-config-derivation.py                # the proposed config
    python scripts/v3-production-config-derivation.py --current      # production today
    python scripts/v3-production-config-derivation.py --all          # every scenario
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# Isolate from the developer's own environment BEFORE the settings module loads. A
# derivation that inherits `.env` describes this laptop, not the deployment.
for _name in list(os.environ):
    if _name.startswith(("V3_", "DEEPSEEK_", "AZURE_OPENAI_", "LLM_")):
        del os.environ[_name]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings  # noqa: E402

#: What production has today, read from the App Service setting NAMES (values never
#: leave Azure). Azure OpenAI is configured; no V3 setting exists, so every V3 field
#: takes its code default.
_AZURE_PRESENT = {
    "azure_openai_api_key": "present-not-a-real-key",
    "azure_openai_endpoint": "https://ib-stg-openai.openai.azure.com",
    "azure_openai_deployment_name": "gpt-4o",
}


def _settings(**over: Any) -> Settings:
    """A Settings with `.env` excluded. States both ends of every fallback."""
    base: dict[str, Any] = {
        "_env_file": None,
        **_AZURE_PRESENT,
        "deepseek_api_key": "",
    }
    base.update(over)
    return Settings(**base)


SCENARIOS: dict[str, dict[str, Any]] = {
    # Production as deployed right now: V3 code present, no V3 setting configured.
    "current": {},
    # V3 on, but no external research: the pipeline runs on internal evidence only.
    "pipeline-only": {
        "v3_pipeline_enabled": True,
        "v3_corpus_enabled": True,
        "v3_search_backend": "postgres",
        "v3_entity_master_enabled": True,
        "v3_agent_tools_enabled": True,
    },
    # The credential present but neither DeepSeek flag on. THE two-key rule: this must
    # look exactly like `pipeline-only`, or a key is acting as consent.
    "credentialed-not-consented": {
        "v3_pipeline_enabled": True,
        "v3_corpus_enabled": True,
        "v3_search_backend": "postgres",
        "v3_entity_master_enabled": True,
        "v3_agent_tools_enabled": True,
        "deepseek_api_key": "present-not-a-real-key",
    },
    # The proposed activation configuration.
    "proposed": {
        "v3_pipeline_enabled": True,
        "v3_corpus_enabled": True,
        "v3_search_backend": "postgres",
        "v3_entity_master_enabled": True,
        "v3_agent_tools_enabled": True,
        "deepseek_api_key": "present-not-a-real-key",
        "v3_deepseek_search_enabled": True,
        "v3_deepseek_model_enabled": True,
    },
    # Search consented but the credential removed. Must fail CLOSED: the tool must not
    # register, so the Director cannot plan a question that needs it.
    "consented-no-credential": {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "v3_deepseek_search_enabled": True,
        "v3_deepseek_model_enabled": True,
    },
}


def derive(cfg: Settings) -> dict[str, Any]:
    """Everything the configuration decides, computed by the real code paths."""
    from app.services.agent_tools.builtin import register_builtins
    from app.services.agent_tools.external import (
        external_tools_enabled,
        register_external_tools,
    )
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agents.routing import research_provider_for, resolve_routing
    from app.services.director.planner import implemented_tools

    registry = register_external_tools(register_builtins(ToolRegistry()), cfg=cfg)
    routing = resolve_routing(cfg)
    provider = research_provider_for(cfg)

    return {
        "pipeline_runs": bool(cfg.v3_pipeline_enabled),
        "corpus": bool(cfg.v3_corpus_enabled),
        "search_backend": cfg.v3_search_backend,
        "entity_master": bool(cfg.v3_entity_master_enabled),
        "agent_tools": bool(cfg.v3_agent_tools_enabled),
        "durable_jobs": bool(cfg.v3_durable_jobs_enabled),
        "artifact_store": cfg.v3_artifact_store_backend,
        # A credential is reduced to one bit before it can reach any output.
        "deepseek_credential_present": bool(cfg.deepseek_api_key),
        "deepseek_search_consented": bool(cfg.v3_deepseek_search_enabled),
        "deepseek_model_consented": bool(cfg.v3_deepseek_model_enabled),
        "external_tool_surface": external_tools_enabled(cfg),
        "registered_tools": sorted(registry.names()),
        "implemented_tools": sorted(implemented_tools(cfg)),
        "routing": {
            slot: (resolved.vendor or "none") for slot, resolved in sorted(routing.slots.items())
        },
        "research_provider": type(provider).__name__ if provider is not None else None,
        "research_provider_search_enabled": bool(
            getattr(provider, "search_enabled", False)
        ),
    }


def _render(name: str, d: dict[str, Any]) -> None:
    print(f"\n=== {name} ===")
    for key in (
        "pipeline_runs",
        "corpus",
        "search_backend",
        "entity_master",
        "agent_tools",
        "durable_jobs",
        "artifact_store",
        "deepseek_credential_present",
        "deepseek_search_consented",
        "deepseek_model_consented",
        "external_tool_surface",
        "research_provider",
        "research_provider_search_enabled",
    ):
        print(f"  {key:34} {d[key]}")
    print(f"  {'routing':34} {d['routing']}")
    ext = [t for t in d["registered_tools"] if t in {"search_web", "fetch_public_source"}]
    print(f"  {'external tools registered':34} {ext or 'none'}")
    print(f"  {'tools the planner may staff':34} {len(d['implemented_tools'])}")


def _check_secret_safety() -> int:
    """Assert no credential can reach this script's output.

    Every comparison is reduced to a BOOL before it reaches an ``assert``. A failing
    assert renders its operands, and that is precisely how the DeepSeek key reached
    pytest output twice in this campaign — the second time from the guard written to
    prevent the first.

    ``model_dump()`` is checked and EXPECTED to expose: ``Field(repr=False)`` suppresses
    ``repr``, not serialisation. That is a real property of ``Settings`` and stating it
    here is more useful than a guard that quietly avoids the one surface that leaks. The
    protection is that no application code calls it — asserted in the test suite, not
    here, because this script cannot see the whole app.
    """
    # Deliberately carries no vendor prefix. A canary that looks like a real key
    # trains a secret scanner — and its reader — to ignore the real thing.
    secret = "canary-value-for-the-leak-check-not-a-key"
    cfg = _settings(deepseek_api_key=secret, azure_openai_api_key=secret)

    printed_surfaces = {"repr(Settings)": repr(cfg), "derivation": str(derive(cfg))}
    leaking = sorted(k for k, v in printed_surfaces.items() if secret in v)
    assert not leaking, f"a credential reached: {leaking}"

    still_printable = sorted(
        f for f in CREDENTIAL_SETTING_FIELDS if type(cfg).model_fields[f].repr
    )
    assert not still_printable, f"credential fields still printable: {still_printable}"

    dump_exposes = secret in repr(cfg.model_dump())
    print("secret-safety: OK")
    print("  repr(Settings) and this derivation expose no credential")
    print(f"  {len(CREDENTIAL_SETTING_FIELDS)} credential fields are repr=False")
    print(f"  model_dump() exposes values: {dump_exposes} — never call it on Settings")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check-secret-safety", action="store_true")
    args = ap.parse_args()

    if args.check_secret_safety:
        return _check_secret_safety()

    names = (
        list(SCENARIOS)
        if args.all
        else ["current"]
        if args.current
        else ["proposed"]
    )
    for name in names:
        _render(name, derive(_settings(**SCENARIOS[name])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
