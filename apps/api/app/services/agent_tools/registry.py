"""The closed tool registry — V3.3 Slice 3.1.

A registry that can only hold read-only tools whose names are in the vocabulary. Both
refusals happen at registration rather than at call time, so a mistake is a failed
import in a test run instead of a permission decision made under load.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.agent_tools.contracts import ToolSpec, require_tool_name


class ToolRegistrationError(ValueError):
    """A tool could not be registered. Always a programming error, never runtime."""


@dataclass
class ToolRegistry:
    """The set of tools that exist. Closed by construction."""

    _specs: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
        """Add a tool, or raise.

        Refuses three things, each because the alternative is unreviewable:

        * a name outside the vocabulary — adding a tool must be a change to
          ``contracts.TOOL_NAMES``, visible in a diff next to the reasoning, rather
          than a ``register()`` call in a module nobody re-reads;
        * a spec that is not side-effect-free — there is no supported way to give an
          agent a write, and the refusal makes that a property rather than a promise;
        * a silent overwrite — two definitions of one tool means whichever imported
          last decides what an agent can do.
        """
        try:
            name = require_tool_name(spec.name)
        except ValueError as exc:
            raise ToolRegistrationError(str(exc)) from exc
        if not spec.side_effect_free:
            raise ToolRegistrationError(
                f"{name}: agent tools are read-only. There is no supported way to "
                "register a tool with side effects — a failed run must never be able "
                "to corrupt the record, which is what makes automatic retry safe."
            )
        if name in self._specs and not replace:
            raise ToolRegistrationError(
                f"{name} is already registered. Two definitions of one tool means "
                "whichever module imported last decides what an agent can do; pass "
                "replace=True only in a test."
            )
        self._specs[name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get((name or "").strip())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs[name] for name in self.names())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.strip() in self._specs

    def __len__(self) -> int:
        return len(self._specs)


def default_registry(cfg: object | None = None) -> ToolRegistry:
    """A registry holding every builtin tool.

    Built fresh on each call rather than shared as module state: a process-wide
    registry that a test mutates is a test that changes what a later test is allowed
    to do, and a permission surface is the worst possible place for that.
    """
    from app.services.agent_tools.builtin import register_builtins

    registry = ToolRegistry()
    # `cfg` is threaded because one registration is CONDITIONAL (V3.12's external tools).
    # Without it this function falls back to the process-global settings — which on a
    # developer machine means the `.env`, and "a test that reads the machine" is a bug
    # this campaign has already paid for twice.
    register_builtins(registry, cfg=cfg)
    return registry


__all__ = ["ToolRegistrationError", "ToolRegistry", "default_registry"]
