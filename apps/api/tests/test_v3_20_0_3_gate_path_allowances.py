"""V3.20 slice 0.3 — path-scoped safety-gate allowances (ADR-098 §3, ADR-099 §5).

The point of these tests is the negative space: an allowance admits ONE exact
value at ONE exact path, and the gate is byte-for-byte unchanged everywhere else.
"""

from __future__ import annotations

import pytest

from app.services import safety_terms
from app.services.safety_terms import (
    NEVER_ALLOWED_TERMS,
    V320_REPORT_ALLOWANCES,
    FieldAllowance,
    build_allowances,
    neutralize_text,
    scan_text,
    scan_value,
)


def _report(recommendation: str = "SHORTLIST", position: str = "undervalued") -> dict:
    return {
        "verdict": {
            "recommendation": recommendation,
            "rationale": "Backlog coverage of 2.1 years is corroborated by TSO capex.",
        },
        "valuation": {
            "relative_position": position,
            "fair_value_range": {"bear": 11.2, "base": 14.8, "bull": 19.5},
        },
    }


def _scan(report: dict, allowances=V320_REPORT_ALLOWANCES) -> list[str]:
    hits: list[str] = []
    for section, content in report.items():
        hits.extend(
            safety_terms.hits_to_strings(
                scan_value(content, path=section, allowances=allowances)
            )
        )
    return hits


# --- the allowed values pass, only where allowed -----------------------------


@pytest.mark.parametrize("label", ["PASS", "WATCHLIST", "SHORTLIST"])
def test_verdict_labels_pass_in_the_verdict_field(label):
    assert _scan(_report(recommendation=label)) == []


@pytest.mark.parametrize("position", ["undervalued", "in_line", "overvalued"])
def test_valuation_position_passes_in_its_field(position):
    assert _scan(_report(position=position)) == []


def test_without_allowances_the_gate_is_unchanged():
    hits = _scan(_report(), allowances=None)
    assert "'SHORTLIST' in verdict.recommendation (rating_token)" in hits
    assert "'undervalued' in valuation.relative_position (phrase)" in hits


def test_shortlist_outside_its_path_is_still_a_hit():
    report = _report()
    report["verdict"]["rationale"] = "We place this name on the SHORTLIST."
    hits = _scan(report)
    assert hits == ["'SHORTLIST' in verdict.rationale (rating_token)"]


def test_undervalued_outside_its_path_is_still_a_hit():
    report = _report()
    report["memo"] = {"body": "The company looks undervalued against peers."}
    assert _scan(report) == ["'undervalued' in memo.body (phrase)"]


def test_same_leaf_name_under_another_section_is_not_allowed():
    report = {"council": {"verdict": {"recommendation": "SHORTLIST"}}}
    assert _scan(report) == [
        "'SHORTLIST' in council.verdict.recommendation (rating_token)"
    ]


# --- whole-value match: no free text rides along -----------------------------


@pytest.mark.parametrize(
    "value",
    [
        "SHORTLIST — BUY",
        "SHORTLIST: strong buy",
        "shortlist",  # case differs from the enum: not a member, scanned as usual
        "SHORTLIST ",
        "Rating: hold, SHORTLIST",
    ],
)
def test_a_value_that_is_not_exactly_a_member_is_scanned(value):
    hits = _scan(_report(recommendation=value))
    if value == "shortlist":
        # lower-case is not a Tier-1 token; it passes the gate as before,
        # but it was never admitted by the allowance either.
        assert hits == []
    else:
        assert hits, value


def test_banned_ratings_are_hits_even_at_the_allowed_path():
    for token in ("BUY", "SELL", "HOLD", "REJECT", "OUTPERFORM"):
        hits = _scan(_report(recommendation=token))
        assert hits == [f"'{token}' in verdict.recommendation (rating_token)"]


def test_price_target_is_a_hit_in_the_valuation_section():
    report = _report()
    report["valuation"]["relative_position"] = "price target 15"
    assert _scan(report) == ["'price target' in valuation.relative_position (phrase)"]


# --- construction guards -----------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["BUY", "Hold", "price target", "Strong Buy", "rating: buy", "SHORTLIST BUY"],
)
def test_an_allowance_cannot_admit_a_never_allowed_term(bad):
    with pytest.raises(ValueError):
        FieldAllowance(path="verdict.recommendation", allowed_values=frozenset({bad}), reason="t")


@pytest.mark.parametrize("path", ["", "verdict.*", "*.recommendation", "verdict.reco*"])
def test_an_allowance_path_must_be_exact(path):
    with pytest.raises(ValueError):
        FieldAllowance(path=path, allowed_values=frozenset({"PASS"}), reason="t")


def test_an_allowance_must_name_values():
    with pytest.raises(ValueError):
        FieldAllowance(path="verdict.recommendation", allowed_values=frozenset(), reason="t")


def test_duplicate_paths_are_rejected():
    a = FieldAllowance(path="x.y", allowed_values=frozenset({"PASS"}), reason="t")
    with pytest.raises(ValueError):
        build_allowances(a, a)


def test_list_indices_match_a_star_allowance():
    allowances = build_allowances(
        FieldAllowance(path="rows[*].verdict", allowed_values=frozenset({"SHORTLIST"}), reason="t")
    )
    value = {"rows": [{"verdict": "SHORTLIST"}, {"verdict": "SHORTLIST", "note": "SHORTLIST"}]}
    hits = safety_terms.hits_to_strings(scan_value(value, allowances=allowances))
    assert hits == ["'SHORTLIST' in rows[1].note (rating_token)"]


def test_the_shipped_allowances_admit_only_the_adr_values():
    assert V320_REPORT_ALLOWANCES == {
        "verdict.recommendation": frozenset({"PASS", "WATCHLIST", "SHORTLIST"}),
        "valuation.relative_position": frozenset({"undervalued", "in_line", "overvalued"}),
    }
    for values in V320_REPORT_ALLOWANCES.values():
        for v in values:
            assert not any(t.lower() in v.lower().split() for t in NEVER_ALLOWED_TERMS)


# --- unchanged behaviour elsewhere -------------------------------------------


def test_exempt_keys_still_apply_alongside_allowances():
    value = {"disallowed_outputs": ["BUY", "price target"], "verdict": {"recommendation": "PASS"}}
    hits = scan_value(
        value, exempt_keys=frozenset({"disallowed_outputs"}), allowances=V320_REPORT_ALLOWANCES
    )
    assert hits == []


@pytest.mark.parametrize(
    "text",
    [
        "Analysts placed it on the SHORTLIST and said it was undervalued.",
        "fair value of the derivative book",
        "sell-side analyst estimates",
    ],
)
def test_neutraliser_is_untouched(text):
    out = neutralize_text(text)
    assert not scan_text(out or "")
