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


#: A placeholder Azure OpenAI configuration. Not a credential — `_azure_client` only
#: checks that a key and an endpoint are non-empty before constructing its client, and
#: nothing in these tests makes a call.
_AZURE = {
    "azure_openai_api_key": "test-key-not-a-credential",
    "azure_openai_endpoint": "https://example-not-a-real-endpoint.invalid",
    "azure_openai_deployment_name": "test-deployment",
}


@pytest.fixture
def buildable_vendors(monkeypatch):  # noqa: ANN001, ANN201
    """Make both vendors CONSTRUCTIBLE, so a routing test tests routing.

    `resolve_routing` asks each vendor's builder for a client and takes the first that
    returns one. `_azure_client` needs `langchain-openai`, which lives in the `llm`
    extra — and CI installs only `[dev]`. So on CI every builder returned None, every
    slot resolved to `vendor=None`, and eleven tests that passed locally failed.

    They were asserting a property of the INSTALLED ENVIRONMENT, which is the same
    mistake as asserting a property of the developer's `.env` — the third time this
    campaign has met it. Patching the builders keeps the assertions on what these tests
    are actually about: which vendor is PREFERRED for a slot, and what it falls back to.
    Whether an SDK is importable is a different question, and it has its own test.
    """
    import app.services.agents.routing as routing

    def _fake_azure(cfg):  # noqa: ANN001, ANN202
        return object() if (cfg.azure_openai_api_key and cfg.azure_openai_endpoint) else None

    def _fake_deepseek(cfg):  # noqa: ANN001, ANN202
        if not getattr(cfg, "v3_deepseek_model_enabled", False):
            return None
        return object() if cfg.deepseek_api_key else None

    monkeypatch.setitem(routing._BUILDERS, routing.VENDOR_AZURE_OPENAI, _fake_azure)
    monkeypatch.setitem(routing._BUILDERS, routing.VENDOR_DEEPSEEK, _fake_deepseek)


def _no_deepseek() -> Settings:
    """Settings with DeepSeek explicitly absent **and Azure OpenAI explicitly present**.

    A bare ``Settings()`` reads the developer's ``.env``, so tests written against it
    assert a property of the MACHINE, not of the code. These originally passed only
    because no key existed anywhere; the moment a real one was configured, five of them
    failed.

    The fix then stated DeepSeek's ABSENCE explicitly and left Azure OpenAI's PRESENCE
    implicit — which is the same bug wearing the other hat, and CI found it: on a runner
    with no Azure credential every slot resolved to ``vendor=None`` and eleven tests that
    passed locally failed. A test about fallback has to state **both** ends of the
    fallback, or it is still asking the machine.
    """
    return Settings(
        deepseek_api_key="",
        v3_deepseek_model_enabled=False,
        v3_deepseek_search_enabled=False,
        **_AZURE,
    )


class TestItIsOffByDefault:
    """Defaults are read from the field definitions, never from the ambient env."""

    def test_no_key_ships_in_the_defaults(self) -> None:
        assert Settings.model_fields["deepseek_api_key"].default == ""

    def test_the_search_feature_defaults_off(self) -> None:
        assert Settings.model_fields["v3_deepseek_search_enabled"].default is False

    def test_the_model_leg_defaults_off(self) -> None:
        assert Settings.model_fields["v3_deepseek_model_enabled"].default is False

    def test_an_absent_deepseek_names_no_deepseek_slot(self, buildable_vendors) -> None:
        routing = resolve_routing(_no_deepseek())
        assert all(slot.vendor != "deepseek" for slot in routing.slots.values())


class TestEverySlotStillResolves:
    """The failure mode this guards against is a slot resolving to nothing because its
    preferred vendor is unconfigured — a research run that stops before it starts."""

    @pytest.mark.parametrize("slot_name", SLOTS)
    def test_the_slot_has_a_vendor(self, slot_name, buildable_vendors) -> None:
        assert resolve_routing(_no_deepseek()).slots[slot_name].vendor

    @pytest.mark.parametrize("slot_name", SLOTS)
    def test_the_slot_falls_back_to_what_is_configured(self, slot_name, buildable_vendors) -> None:
        assert resolve_routing(_no_deepseek()).slots[slot_name].vendor == "azure_openai"

    def test_a_flag_is_not_a_credential(self, buildable_vendors) -> None:
        """Enabling the feature with no key must not route work to a provider that
        cannot answer."""
        enabled_but_keyless = Settings(
            deepseek_api_key="", v3_deepseek_model_enabled=True, **_AZURE
        )
        routing = resolve_routing(enabled_but_keyless)
        assert all(slot.vendor != "deepseek" for slot in routing.slots.values())
        assert all(routing.slots[s].vendor for s in SLOTS)

    def test_a_credential_is_not_consent(self, buildable_vendors) -> None:
        """The converse, and the defect a real key exposed: a key appearing in the
        environment silently moved Investigator and follow-up research off Azure
        OpenAI — the vendor every V3.11 acceptance measurement was taken on."""
        credentialed_but_off = Settings(
            deepseek_api_key="sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            v3_deepseek_model_enabled=False,
            **_AZURE,
        )
        routing = resolve_routing(credentialed_but_off)
        assert all(slot.vendor != "deepseek" for slot in routing.slots.values())

    def test_both_together_do_route_to_it(self, buildable_vendors) -> None:
        """The flag must actually work, or it is a permanent off switch pretending to
        be a choice."""
        both = Settings(
            deepseek_api_key="sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            v3_deepseek_model_enabled=True,
            **_AZURE,
        )
        routing = resolve_routing(both)
        assert routing.slots[SLOT_INVESTIGATOR].vendor == "deepseek"
        assert routing.slots[SLOT_CHAIR].vendor == "azure_openai"


class TestAMissingOptionalSdkDegradesHonestly:
    """The contract CI exposed, and the regression coverage for it.

    `langchain-openai` lives in the `llm` extra; CI installs only `[dev]`, and the
    pyproject comment says so in as many words. So on a CI runner `_azure_client`
    cannot construct a client even with an endpoint and a key configured.

    That behaviour is CORRECT — a missing optional dependency is "vendor unavailable",
    and `_azure_client` returns None rather than raising, which keeps the caller's
    deterministic path. What was wrong was eleven tests that asserted a vendor *does*
    resolve, which is only true where the SDK happens to be installed. They were
    asserting a property of the INSTALLED ENVIRONMENT — the same mistake as asserting a
    property of the developer's `.env`, which this campaign has now met three times.

    These tests pin the contract itself, so it is checked rather than assumed, and they
    pass with or without the package.
    """

    def test_an_unimportable_sdk_is_unavailable_not_an_exception(
        self, monkeypatch
    ) -> None:
        import app.services.agents.routing as routing

        def _boom(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            raise ModuleNotFoundError("No module named 'langchain_openai'")

        monkeypatch.setattr(
            "app.services.llm.azure_openai_client.AzureOpenAILLMClient", _boom
        )
        cfg = Settings(**_AZURE, deepseek_api_key="")
        assert routing._azure_client(cfg) is None, (
            "a missing optional dependency must read as 'vendor unavailable', never as "
            "a crash — the caller keeps its deterministic path"
        )

    def test_a_slot_with_no_constructible_vendor_says_why(self, monkeypatch) -> None:
        """And the run is not lost: the slot carries a reason a reader can act on."""
        import app.services.agents.routing as routing

        monkeypatch.setitem(
            routing._BUILDERS, routing.VENDOR_AZURE_OPENAI, lambda _cfg: None
        )
        monkeypatch.setitem(
            routing._BUILDERS, routing.VENDOR_DEEPSEEK, lambda _cfg: None
        )
        resolved = resolve_routing(Settings(**_AZURE)).slots[SLOT_CHAIR]
        assert resolved.vendor is None
        assert resolved.client is None
        assert "no configured vendor" in resolved.reason

    def test_the_routing_tests_do_not_depend_on_the_optional_sdk(self) -> None:
        """Structural, so the next person cannot reintroduce the assumption quietly.

        Every test in this file that asserts a vendor RESOLVES must take the
        `buildable_vendors` fixture. Without it the assertion is about whether
        `langchain-openai` is installed, which is not what any of them are named for.
        """
        import ast
        from pathlib import Path as _Path

        tree = ast.parse(_Path(__file__).read_text())
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # A real CALL to `resolve_routing`, found in the syntax tree — not a text
            # match, which this very function would satisfy from its own docstring.
            # The campaign has already lost time to a grep matching its own prose.
            calls_routing = any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", getattr(c.func, "attr", None))
                == "resolve_routing"
                for c in ast.walk(node)
            )
            reads_vendor = any(
                isinstance(a, ast.Attribute)
                and a.attr in ("vendor", "vendor_for", "shares_vendor_with_chair")
                for a in ast.walk(node)
            )
            if not (calls_routing and reads_vendor):
                continue
            args = {a.arg for a in node.args.args}
            if "buildable_vendors" not in args and "monkeypatch" not in args:
                offenders.append(node.name)
        assert not offenders, (
            "these assert on a resolved vendor without making the vendors "
            f"constructible, so they test the installed environment: {offenders}"
        )


class TestTheSharedVendorIsReported:
    def test_it_says_so_when_the_red_team_shares_the_chairs_vendor(self, buildable_vendors) -> None:
        """A Red Team run by the same vendor as the Chair is a weaker check than one run
        by a different vendor. With DeepSeek absent that is the configuration, and a
        reader must be able to see which they got rather than infer it."""
        assert resolve_routing(_no_deepseek()).shares_vendor_with_chair is True


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


class TestNoTestMayPrintTheKey:
    """Two real leaks happened during V3.11.1.1, both through pytest output.

    The first was the transport's dataclass ``repr``. The second was a test asserting
    ``settings.deepseek_api_key == ""``, which makes pytest print the live value into
    the failure diff. Both are the same mistake: letting a credential reach a comparison
    or a formatter that a failure will render.
    """

    def test_no_test_asserts_on_the_key_value(self) -> None:
        """Assert on the FIELD DEFAULT, never on a constructed object's key.

        Checked by AST, not by text search: a grep matches this very docstring, which is
        exactly how the V3.9 "no scheduler" test once failed on the word "cron" inside
        its own prose.
        """
        from pathlib import Path

        hits: list[str] = []
        for path in Path("tests").glob("test_v3_deepseek*.py"):
            tree = ast.parse(path.read_text())

            # Locals bound to a key attribute. The security review of V3.11.1.2 found the
            # original guard blind to exactly this: `key = Settings().deepseek_api_key`
            # followed by `assert key not in repr(...)` put an `ast.Name` in the compare,
            # not an `ast.Attribute`, so the guard passed while the leak was open — in
            # the one test that only ever fails when the redaction has regressed.
            tainted: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "deepseek_api_key"
                    and not isinstance(value.value, ast.Subscript)
                ):
                    tainted.update(
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    )

            # Scoped to `assert` statements, because that is exactly where the danger
            # is: pytest's assertion rewriting renders the OPERANDS of a failing assert
            # (and adds an "is contained here" expansion for `in`). The same comparison
            # in a plain assignment — `leaked = key in repr(t)` — prints nothing, which
            # is why reducing to a bool first is the fix rather than a dodge.
            for assertion in [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]:
                for node in ast.walk(assertion):
                    if not isinstance(node, ast.Compare):
                        continue
                    for operand in [node.left, *node.comparators]:
                        # `Settings.model_fields[...].default` is the safe form: its
                        # value is a Subscript, never an attribute on a live object.
                        if (
                            isinstance(operand, ast.Attribute)
                            and operand.attr == "deepseek_api_key"
                            and not isinstance(operand.value, ast.Subscript)
                        ) or (isinstance(operand, ast.Name) and operand.id in tainted):
                            hits.append(f"{path}:{operand.lineno}")
        assert not hits, (
            "these ASSERT on a live key value — directly or through a local bound to "
            "one — so pytest renders it on failure: " + ", ".join(hits)
        )

    def test_every_credential_in_settings_is_non_printing(self) -> None:
        """The leak the AST guard above could not have caught.

        That guard forbids comparing a *key attribute*. But V3.11.1.2's own live run
        failed an assertion on ``Settings().deepseek_model`` — a perfectly innocent
        field — and pytest rendered the whole ``Settings`` object into the diff, key
        included. Any assertion, log line or exception that formats a settings object
        was a leak, and no rule about how to write assertions can cover all of them.

        So the guarantee moved to the type, as it did for the transport in V3.11.1.1.
        The predicate is the **named list** in ``config``, not a name suffix: review
        found the first version matching only ``*_api_key``/``*_secret``, which passed
        while ``database_url`` (the DB password) and ``staging_basic_auth`` (literally
        ``user:pass``) still printed. A credential is a credential whatever it is called.
        """
        from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

        printing = sorted(
            name
            for name in CREDENTIAL_SETTING_FIELDS
            if Settings.model_fields[name].repr is not False
        )
        assert not printing, (
            "these credential fields would be printed by any repr of Settings: "
            + ", ".join(printing)
        )

    def test_the_credential_list_has_not_drifted_from_the_settings(self) -> None:
        """A list nothing checks is a list that goes stale.

        Catches both directions: a credential added to ``Settings`` and forgotten here,
        and a name left here after the field was renamed away.
        """
        from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

        missing = sorted(CREDENTIAL_SETTING_FIELDS - set(Settings.model_fields))
        assert not missing, f"named but no longer a setting: {missing}"

        suffix_matched = {
            n for n in Settings.model_fields if n.endswith(("_api_key", "_secret"))
        }
        unlisted = sorted(suffix_matched - CREDENTIAL_SETTING_FIELDS)
        assert not unlisted, (
            "these look like credentials but are not in CREDENTIAL_SETTING_FIELDS, so "
            f"nothing checks that they are non-printing: {unlisted}"
        )

    def test_a_configured_settings_repr_contains_no_credential(self) -> None:
        """Behavioural companion, written so its OWN failure cannot leak.

        The first version asserted ``planted not in repr(...)`` — and pytest's assertion
        rewriting prints the operands, so a regression would have published the whole
        rendered ``Settings`` (built from the developer's ``.env``) into the failure
        output. The comparison is reduced to a boolean before it reaches ``assert``.
        """
        from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

        planted = "planted-value-that-is-not-a-real-credential"
        rendered = repr(Settings(**{n: planted for n in CREDENTIAL_SETTING_FIELDS}))
        leaked = planted in rendered
        assert leaked is False, (
            "a credential value reached repr(Settings); the rendered text is "
            "deliberately NOT included in this message"
        )

    def test_no_application_module_bypasses_the_search_gate(self) -> None:
        """`enabled=True` is a TEST affordance, and must stay one.

        The model leg's gate lives inside `_deepseek_client` and cannot be overridden.
        The search leg's is a constructor kwarg, so it *can* be — which is fine for a
        test that says so explicitly, and not fine anywhere under `app/`. Checked
        structurally, at the same rigour as the credential guards in this file, because
        "nothing constructs it today" is a fact with a short shelf life.
        """
        offenders: list[str] = []
        for path in Path("app").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "DeepSeekSearchProvider":
                    continue
                for kw in node.keywords:
                    if kw.arg == "enabled":
                        offenders.append(f"{path}:{node.lineno}")
        assert not offenders, (
            "application code must not pass `enabled=` to DeepSeekSearchProvider — that "
            "bypasses V3_DEEPSEEK_SEARCH_ENABLED, which is the only thing standing "
            f"between a research run and unapproved external spend: {offenders}"
        )

    def test_the_transport_field_is_marked_non_repr(self) -> None:
        """Structural, not behavioural: the guarantee must survive someone adding a
        field or switching the decorator."""
        import dataclasses

        from app.integrations.deepseek.transport import HttpDeepSeekTransport

        field = next(f for f in dataclasses.fields(HttpDeepSeekTransport) if f.name == "api_key")
        assert field.repr is False
