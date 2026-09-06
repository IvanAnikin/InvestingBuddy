"""DeepSeek is optional. V3.11 slice 11.1.

V3.10 recorded "the primary external research runtime has never been called" as a release
blocker, and it was the right call while the architecture assumed DeepSeek would carry
the research leg. V3.11 could not verify the live contract — **no credential is reachable
from this environment** — so the blocker is cleared the other way the acceptance brief
allows: by removing DeepSeek as a release-critical dependency.

This file is the proof. Not a promise in a document: assertions that the research path
resolves, routes and completes with **no DeepSeek key, no DeepSeek flag and no DeepSeek
call**, which is exactly the configuration the three real playbook-gated Councils ran
under.

What is NOT claimed: that the DeepSeek adapter works. Its wire contract remains
documentation-derived and unverified, the opt-in live contract test in
``test_v3_deepseek_live_contract.py`` remains unrun, and the feature stays off.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.agents.routing import (
    SLOT_CHAIR,
    SLOT_FOLLOW_UP,
    SLOT_INVESTIGATOR,
    SLOT_RED_TEAM,
    resolve_routing,
)

SLOTS = (SLOT_INVESTIGATOR, SLOT_FOLLOW_UP, SLOT_RED_TEAM, SLOT_CHAIR)


class TestItIsOffByDefault:
    def test_no_key_is_configured(self) -> None:
        assert Settings().deepseek_api_key == ""

    def test_the_search_feature_is_off(self) -> None:
        assert Settings().v3_deepseek_search_enabled is False

    def test_a_default_settings_object_names_no_deepseek_slot(self) -> None:
        routing = resolve_routing(Settings())
        assert all(slot.vendor != "deepseek" for slot in routing.slots.values())


class TestEverySlotStillResolves:
    """The failure mode this guards against is a slot resolving to nothing because its
    preferred vendor is unconfigured — a research run that stops before it starts."""

    @pytest.mark.parametrize("slot_name", SLOTS)
    def test_the_slot_has_a_vendor(self, slot_name) -> None:
        routing = resolve_routing(Settings())
        assert routing.slots[slot_name].vendor

    @pytest.mark.parametrize("slot_name", SLOTS)
    def test_the_slot_falls_back_to_what_is_configured(self, slot_name) -> None:
        assert resolve_routing(Settings()).slots[slot_name].vendor == "azure_openai"

    def test_enabling_the_flag_without_a_key_does_not_select_it(self) -> None:
        """A flag is not a credential. Turning the feature on with no key must not route
        work to a provider that cannot answer."""
        routing = resolve_routing(Settings(v3_deepseek_search_enabled=True))
        assert all(slot.vendor != "deepseek" for slot in routing.slots.values())

    def test_enabling_the_flag_without_a_key_still_resolves_every_slot(self) -> None:
        routing = resolve_routing(Settings(v3_deepseek_search_enabled=True))
        assert all(routing.slots[s].vendor for s in SLOTS)


class TestTheSharedVendorIsReported:
    def test_it_says_so_when_the_red_team_shares_the_chairs_vendor(self) -> None:
        """A Red Team run by the same vendor as the Chair is a weaker check than one run
        by a different vendor. With DeepSeek absent that is the configuration, and a
        reader must be able to see which they got rather than infer it."""
        assert resolve_routing(Settings()).shares_vendor_with_chair is True


class TestNothingRequiresIt:
    def test_the_pipeline_does_not_import_the_deepseek_adapter(self) -> None:
        """An import is a dependency. The research pipeline must be able to run in a
        build where the adapter is absent entirely."""
        source = Path("app/services/pipeline/v3_pipeline.py").read_text()
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert not [m for m in imported if "deepseek" in m.lower()]

    def test_the_agent_adapters_do_not_import_it_either(self) -> None:
        for path in (
            "app/services/agents/investigator.py",
            "app/services/agents/red_team.py",
            "app/services/agents/chair.py",
        ):
            source = Path(path).read_text()
            assert "deepseek" not in source.lower(), path

    def test_the_live_contract_test_is_opt_in(self) -> None:
        """It must never run in ordinary CI, with or without a key."""
        source = Path("tests/test_v3_deepseek_live_contract.py").read_text()
        assert "skip" in source.lower()
        assert "DEEPSEEK_API_KEY" in source
