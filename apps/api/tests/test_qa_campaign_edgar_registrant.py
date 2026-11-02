"""ADR-079 — a web copy of an EDGAR filing is the subject's only when its CIK is.

Live Cellectis rerun df36d6f6 (2026-11-02): web search admitted a Cboe EDGA Exchange
financial-statement exhibit from ``sec.gov/Archives/edgar/data/<Cboe CIK>/…`` into
Cellectis's corpus. The host made it ``regulatory_filing``, the T1 tier made it
``issuer_filing``, and three findings ("The company operates a single reportable
segment: an electronic market…") led the executive synthesis and replaced the 20-F's
Therapeutics-segment finding. The same path brought Calyxt's FY2022 20-F into CLLS2.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agents import investigator as inv_mod
from app.services.agents.investigator import (
    EdgarSubject,
    LLMInvestigator,
    _Evidence,
    _evidence_of,
    apply_edgar_registrant,
    statement_about_other_registrant,
)
from app.services.director.planner import PlannedQuestion
from app.services.entities import sec_listing
from app.services.entities.sec_listing import (
    BASIS_OWN,
    BASIS_SEC_DIRECTORY,
    SecListing,
)
from app.services.web_research.edgar_registrant import (
    EDGAR_OTHER,
    EDGAR_OWN,
    EDGAR_UNIDENTIFIED,
    edgar_cik,
    normalise_cik,
    registrant_verdict,
)

CELLECTIS_CIK = "1627281"
#: A made-up CIK standing for the exchange registrant (not asserted to be Cboe's real one).
EXCHANGE_CIK = "1473845"

CBOE_URL = (
    f"https://www.sec.gov/Archives/edgar/data/{EXCHANGE_CIK}/000147384526000021/"
    "edga-ex99_financialstatements.htm"
)
OWN_20F_URL = (
    "https://www.sec.gov/Archives/edgar/data/1627281/000162728126000012/clls-20251231.htm"
)
NO_CIK_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=CLLS"


# ---------------------------------------------------------------------------
# Reading the registrant out of an EDGAR URL
# ---------------------------------------------------------------------------


class TestEdgarCik:
    @pytest.mark.parametrize(
        ("url", "cik"),
        [
            (CBOE_URL, EXCHANGE_CIK),
            ("https://www.sec.gov/Archives/edgar/data/0001627281/x/y.htm", "1627281"),
            (
                "https://www.sec.gov/ix?doc=/Archives/edgar/data/773840/000077384026000143/"
                "hon-20260930.htm",
                "773840",
            ),
            (
                "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000019617"
                "&type=10-K",
                "19617",
            ),
            ("https://data.sec.gov/submissions/CIK0000773840.json", "773840"),
            ("https://data.sec.gov/api/xbrl/companyfacts/CIK0001633917.json", "1633917"),
            ("https://efts.sec.gov/LATEST/search-index?q=x&ciks=0001627281", "1627281"),
        ],
    )
    def test_the_registrant_is_read_and_zeros_stripped(self, url: str, cik: str) -> None:
        assert edgar_cik(url) == cik

    @pytest.mark.parametrize(
        "url",
        [
            NO_CIK_URL,  # a ticker in CIK= is not a CIK
            "https://efts.sec.gov/LATEST/search-index?q=x&ciks=0001627281,0000019617",
            "https://www.sec.gov/Archives/edgar/Feed/2026/QTR3/",
            "https://www.cellectis.com/Archives/edgar/data/1627281/x.htm",  # not sec.gov
            None,
        ],
    )
    def test_no_single_cik_is_none(self, url: str | None) -> None:
        assert edgar_cik(url) is None

    def test_the_accession_prefix_is_never_the_registrant(self) -> None:
        # 0001193125 is a filing agent that files for hundreds of issuers.
        url = "https://www.sec.gov/Archives/edgar/data/1627281/000119312526285565/a.htm"
        assert edgar_cik(url) == "1627281"

    def test_normalise(self) -> None:
        assert normalise_cik("0001627281") == "1627281"
        assert normalise_cik("CLLS") is None
        assert normalise_cik("0000") is None


class TestVerdict:
    def test_foreign_cik_for_cellectis_is_another_registrant(self) -> None:
        assert registrant_verdict(
            CBOE_URL, {CELLECTIS_CIK}, subject_is_sec_registrant=True
        ) == EDGAR_OTHER

    def test_own_cik_with_leading_zeros_is_own(self) -> None:
        assert registrant_verdict(
            OWN_20F_URL, {"0001627281"}, subject_is_sec_registrant=True
        ) == EDGAR_OWN

    def test_non_sec_subject_never_owns_an_edgar_filing(self) -> None:
        assert registrant_verdict(OWN_20F_URL, set(), subject_is_sec_registrant=False) == (
            EDGAR_OTHER
        )

    def test_no_cik_is_unidentified_not_issuer(self) -> None:
        assert registrant_verdict(
            NO_CIK_URL, {CELLECTIS_CIK}, subject_is_sec_registrant=True
        ) == EDGAR_UNIDENTIFIED

    def test_sec_subject_with_unresolved_cik_is_unidentified(self) -> None:
        assert registrant_verdict(CBOE_URL, set(), subject_is_sec_registrant=True) == (
            EDGAR_UNIDENTIFIED
        )

    def test_a_regulator_page_on_sec_gov_is_out_of_scope(self) -> None:
        # Already a regulator publication (T2) by publisher_tiers; not a filing.
        assert registrant_verdict(
            "https://www.sec.gov/newsroom/press-releases/2026-101", {CELLECTIS_CIK},
            subject_is_sec_registrant=True,
        ) is None

    def test_a_non_sec_url_is_out_of_scope(self) -> None:
        assert registrant_verdict(
            "https://www.cellectis.com/en/investors/", {CELLECTIS_CIK},
            subject_is_sec_registrant=True,
        ) is None


# ---------------------------------------------------------------------------
# Relabelling evidence
# ---------------------------------------------------------------------------


def _hit(cid: str, url: str, *, web: bool = True) -> _Evidence:
    """A real corpus-hit payload through the real harvesting path."""
    item: dict[str, Any] = {
        "evidence_id": cid,
        "text": "The Company operates a single reportable segment.",
        "canonical_url": url,
        "source_tier": "T1_primary_filing",
        "published_at": "2026-06-26",
        "company_id": str(uuid.uuid4()),
    }
    if web:
        item.update({"source_class": "regulatory_filing", "origin_key": "sec.gov"})
    found = _evidence_of("search_company_corpus", item, untrusted=web)
    assert found is not None
    return found


CLLS_SUBJECT = EdgarSubject(ciks=frozenset({CELLECTIS_CIK}), is_sec_registrant=True)


class TestRelabel:
    def test_before_the_fix_the_host_alone_made_it_an_issuer_filing(self) -> None:
        item = _hit("ev:c:cboe", CBOE_URL)
        assert item.ref is not None and item.ref.source_kind == "issuer_filing"
        assert item.url == CBOE_URL

    def test_a_foreign_cik_is_a_third_party_filing(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:cboe", CBOE_URL)], CLLS_SUBJECT)
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_OTHER
        assert item.edgar_cik == EXCHANGE_CIK
        # The stored class is the document's, shared across companies: not rewritten.
        assert item.source_class == "regulatory_filing"

    def test_the_subjects_own_cik_stays_an_issuer_filing(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:own", OWN_20F_URL)], CLLS_SUBJECT)
        assert item.ref is not None and item.ref.source_kind == "issuer_filing"
        assert item.edgar_registrant == EDGAR_OWN

    def test_a_non_sec_subject_never_gets_an_issuer_filing(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:own", OWN_20F_URL)], EdgarSubject())
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_OTHER

    def test_a_url_without_cik_is_never_an_issuer_filing(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:x", NO_CIK_URL)], CLLS_SUBJECT)
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_UNIDENTIFIED

    def test_bridge_documents_are_never_rejudged(self) -> None:
        # The SEC bridge's chunks are not web (no source_class): fetched for the
        # subject's own CIK by construction. Even a resolver that found nothing, or a
        # different CIK in the path, leaves them exactly as they were.
        bridge = _hit("ev:c:bridge", OWN_20F_URL, web=False)
        other_path = _hit("ev:c:bridge2", CBOE_URL, web=False)
        for subject in (CLLS_SUBJECT, EdgarSubject()):
            out = apply_edgar_registrant([bridge, other_path], subject)
            assert out == [bridge, other_path]
            assert all(o.ref is not None and o.ref.source_kind == "issuer_filing" for o in out)
            assert all(o.edgar_registrant is None for o in out)

    def test_an_external_lead_with_the_subjects_cik_becomes_an_issuer_filing(self) -> None:
        item = _evidence_of(
            "fetch_public_source",
            {"evidence_id": "ev:x:1", "fetched_url": OWN_20F_URL,
             "source_tier": "T1_primary_filing"},
            untrusted=True,
        )
        assert item is not None and item.ref is not None
        assert item.ref.source_kind == "third_party_filing"  # host alone: fails safe
        (checked,) = apply_edgar_registrant([item], CLLS_SUBJECT)
        assert checked.ref is not None and checked.ref.source_kind == "issuer_filing"

    def test_idempotent(self) -> None:
        once = apply_edgar_registrant([_hit("ev:c:cboe", CBOE_URL)], CLLS_SUBJECT)
        assert apply_edgar_registrant(once, CLLS_SUBJECT) == once

    def test_the_statement_rule_needs_every_cited_item(self) -> None:
        cboe, own = apply_edgar_registrant(
            [_hit("ev:c:cboe", CBOE_URL), _hit("ev:c:own", OWN_20F_URL)], CLLS_SUBJECT
        )
        assert statement_about_other_registrant([cboe]) == (EXCHANGE_CIK,)
        assert statement_about_other_registrant([cboe, own]) == ()
        assert statement_about_other_registrant([]) == ()


# ---------------------------------------------------------------------------
# The investigator: subject resolution and the write-up guard
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, findings: list[tuple[str, list[str]]]) -> None:
        self._payload = {
            "findings": [
                {"statement": s, "evidence_ids": ids, "direction": "neutral"}
                for s, ids in findings
            ]
        }
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return self._payload


def _patch_subject(
    monkeypatch: pytest.MonkeyPatch, basis: SecListing | None, rows: list[Any] | None = None
) -> list[int]:
    calls: list[int] = []

    async def _listing(session: Any, company: Any, *, cfg: Any = None) -> SecListing | None:  # noqa: ANN401
        calls.append(1)
        return basis

    async def _directory() -> list[Any]:
        return list(rows or [])

    monkeypatch.setattr(sec_listing, "sec_listing_for", _listing)
    monkeypatch.setattr(inv_mod, "_load_sec_directory", _directory)
    return calls


CBOE_STATEMENT = (
    "The company operates a single reportable segment: an electronic market for trading "
    "listed cash equity securities and listed equity options in the U.S."
)
OWN_STATEMENT = "Cellectis reports a Therapeutics segment containing its UCART candidates."


async def _run(
    findings: list[tuple[str, list[str]]],
    evidence: list[_Evidence],
    *,
    ticker: str = "ALCLS",
    exchange: str = "EPA",
    domain: str = "business_model",
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    inv = LLMInvestigator(
        session=object(),
        company_id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        client=_Client(findings),
        company_name="Cellectis S.A.",
    )
    question = PlannedQuestion(
        key="business_model", text="How does the company make money?", origin="playbook",
        required_tools=frozenset({"search_company_corpus"}), domain=domain,
    )
    found, gaps, _answered = await inv._write_up("business_analyst", question, evidence)
    return inv, found, gaps


SIBLING = SecListing(
    ticker="CLLS", exchange="NASDAQ", cik="0001627281", basis=BASIS_SEC_DIRECTORY
)


class TestInvestigatorGuard:
    async def test_the_cboe_exhibit_statement_is_dropped_with_a_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, SIBLING,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A.")],
        )
        inv, findings, gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"]), (OWN_STATEMENT, ["ev:c:own"])],
            [_hit("ev:c:cboe", CBOE_URL), _hit("ev:c:own", OWN_20F_URL)],
        )
        assert [f.statement for f in findings] == [OWN_STATEMENT]
        # ADR-075's sibling line (CLLS for ALCLS) counts as the subject.
        assert findings[0].source_kinds == ("issuer_filing",)
        assert inv.diagnostics.statements_about_other_registrant == 1
        assert inv.diagnostics.to_dict()["statements_about_other_registrant"] == 1
        entity = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert len(entity) == 1 and f"CIK {EXCHANGE_CIK}" in entity[0].description

    async def test_the_gap_names_a_registrant_the_directory_knows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, SIBLING,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A."),
             SimpleNamespace(ticker="XCAL", cik="1473845", name="Calyx Example Inc.")],
        )
        _inv, _findings, gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"])], [_hit("ev:c:cboe", CBOE_URL)]
        )
        (entity,) = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert "Calyx Example Inc. (CIK 1473845)" in entity.description

    async def test_a_peer_domain_keeps_it_as_a_third_party_filing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(monkeypatch, SIBLING)
        inv, findings, gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"])], [_hit("ev:c:cboe", CBOE_URL)],
            domain="competitive_position",
        )
        assert len(findings) == 1
        assert findings[0].source_kinds == ("third_party_filing",)
        assert inv.diagnostics.statements_about_other_registrant == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_mixed_support_is_kept_and_never_issuer_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(monkeypatch, SIBLING)
        _inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:cboe", "ev:c:own"])],
            [_hit("ev:c:cboe", CBOE_URL), _hit("ev:c:own", OWN_20F_URL)],
        )
        assert len(findings) == 1
        assert findings[0].source_kinds == ("issuer_filing", "third_party_filing")

    async def test_an_own_line_sec_subject_resolves_its_cik_from_the_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, SecListing(ticker="CLLS", exchange="NASDAQ", cik=None, basis=BASIS_OWN),
            [SimpleNamespace(ticker="CLLS", cik="0001627281", name="Cellectis S.A.")],
        )
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:own"]), (CBOE_STATEMENT, ["ev:c:cboe"])],
            [_hit("ev:c:own", OWN_20F_URL), _hit("ev:c:cboe", CBOE_URL)],
            ticker="CLLS", exchange="NASDAQ",
        )
        assert [f.source_kinds for f in findings] == [("issuer_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 1

    async def test_a_non_sec_subject_drops_statements_on_edgar_filings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(monkeypatch, None)
        inv, findings, gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:own"])], [_hit("ev:c:own", OWN_20F_URL)],
            ticker="CFR", exchange="SIX",
        )
        assert findings == []
        assert inv.diagnostics.statements_about_other_registrant == 1
        assert [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_an_unresolved_sec_subject_is_unidentified_and_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The directory is unreadable: the CIK is unknown, so nothing is the issuer's
        # filing — and nothing is dropped on a guess.
        _patch_subject(
            monkeypatch, SecListing(ticker="CLLS", exchange="NASDAQ", cik=None, basis=BASIS_OWN)
        )
        inv, findings, _gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"])], [_hit("ev:c:cboe", CBOE_URL)],
            ticker="CLLS", exchange="NASDAQ",
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_a_url_with_no_cik_is_kept_but_not_the_issuers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(monkeypatch, SIBLING)
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:x"])], [_hit("ev:c:x", NO_CIK_URL)]
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_bridge_evidence_is_unchanged_and_never_resolves_the_subject(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_subject(monkeypatch, None)
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:bridge"])],
            [_hit("ev:c:bridge", OWN_20F_URL, web=False)],
        )
        assert [f.source_kinds for f in findings] == [("issuer_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0
        assert calls == []  # no web EDGAR page → no resolution at all

    async def test_a_resolver_failure_fails_to_unidentified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(session: Any, company: Any, *, cfg: Any = None) -> None:  # noqa: ANN401
            raise RuntimeError("db gone")

        monkeypatch.setattr(sec_listing, "sec_listing_for", _boom)
        inv, findings, _gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"])], [_hit("ev:c:cboe", CBOE_URL)]
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_the_subject_is_resolved_once_per_investigator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_subject(monkeypatch, SIBLING)
        inv = LLMInvestigator(session=object(), company_id=uuid.uuid4(), ticker="ALCLS",
                              exchange="EPA", company_name="Cellectis S.A.")
        evidence = [_hit("ev:c:cboe", CBOE_URL)]
        await inv._registrant_checked(evidence)
        await inv._registrant_checked(evidence)
        assert calls == [1]


class TestOtherCompaniesReadsTheToolSessionDatabase:
    """The ADR-066 publisher firewall reads the other registered companies through
    the ToolSession's ``db``. It used to call ``execute`` on the ToolSession itself,
    which has no such method; the swallowed AttributeError left the firewall with no
    other companies in production."""

    async def test_other_company_names_come_from_the_session_db(self) -> None:
        executed: list[Any] = []

        class _Result:
            def scalars(self) -> Any:
                return SimpleNamespace(all=lambda: ["Iluka Resources Limited", "Lindian Resources"])

        class _Db:
            async def execute(self, stmt: Any) -> _Result:
                executed.append(stmt)
                return _Result()

        tool_session = SimpleNamespace(db=_Db())  # a ToolSession exposes db, not execute
        inv = LLMInvestigator(session=tool_session, company_id=uuid.uuid4(), ticker="LIN",
                              exchange="ASX", company_name="Lindian Resources")
        names = await inv._other_companies()
        assert len(executed) == 1
        assert names, "the firewall must learn about other registered companies"
        assert all("lindian" not in label for label in names)

    async def test_a_session_without_db_yields_no_names_and_never_raises(self) -> None:
        inv = LLMInvestigator(session=object(), company_id=uuid.uuid4(), ticker="LIN",
                              exchange="ASX", company_name="Lindian Resources")
        assert await inv._other_companies() == {}
