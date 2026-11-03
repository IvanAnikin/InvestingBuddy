"""ADR-079 — a web copy of an EDGAR filing is the subject's only when its CIK is.

Live Cellectis rerun df36d6f6 (2026-11-02): web search admitted a Cboe EDGA Exchange
financial-statement exhibit from ``sec.gov/Archives/edgar/data/<Cboe CIK>/…`` into
Cellectis's corpus. The host made it ``regulatory_filing``, the T1 tier made it
``issuer_filing``, and three findings ("The company operates a single reportable
segment: an electronic market…") led the executive synthesis and replaced the 20-F's
Therapeutics-segment finding. The same path brought Calyxt's FY2022 20-F into CLLS2.
"""

from __future__ import annotations

import json
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
    foreign_publisher,
    other_company_labels,
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
    EDGAR_COUNTERPARTY,
    EDGAR_OTHER,
    EDGAR_OWN,
    EDGAR_UNIDENTIFIED,
    edgar_cik,
    filing_names_subject,
    is_ownership_filing_url,
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
        assert registrant_verdict(CBOE_URL, {CELLECTIS_CIK}, other_ciks=None) == EDGAR_OTHER

    def test_own_cik_with_leading_zeros_is_own(self) -> None:
        assert registrant_verdict(OWN_20F_URL, {"0001627281"}, other_ciks=None) == EDGAR_OWN

    def test_no_basis_only_a_directory_named_other_registrant_is_other(self) -> None:
        assert registrant_verdict(OWN_20F_URL, set(), other_ciks={"1627281"}) == EDGAR_OTHER
        assert registrant_verdict(OWN_20F_URL, set(), other_ciks={"999"}) == (
            EDGAR_UNIDENTIFIED
        )

    def test_no_basis_and_an_unreadable_directory_is_unidentified(self) -> None:
        assert registrant_verdict(OWN_20F_URL, set(), other_ciks=frozenset()) == (
            EDGAR_UNIDENTIFIED
        )

    def test_no_cik_is_unidentified_not_issuer(self) -> None:
        assert registrant_verdict(NO_CIK_URL, {CELLECTIS_CIK}, other_ciks=None) == (
            EDGAR_UNIDENTIFIED
        )

    def test_sec_subject_with_unresolved_cik_is_unidentified(self) -> None:
        assert registrant_verdict(CBOE_URL, set(), other_ciks=None) == EDGAR_UNIDENTIFIED

    def test_a_regulator_page_on_sec_gov_is_out_of_scope(self) -> None:
        # Already a regulator publication (T2) by publisher_tiers; not a filing.
        assert registrant_verdict(
            "https://www.sec.gov/newsroom/press-releases/2026-101", {CELLECTIS_CIK},
            other_ciks=None,
        ) is None

    def test_a_non_sec_url_is_out_of_scope(self) -> None:
        assert registrant_verdict(
            "https://www.cellectis.com/en/investors/", {CELLECTIS_CIK}, other_ciks=None
        ) is None

    def test_a_dot_dot_path_reads_as_the_cik_it_resolves_to(self) -> None:
        # D4: normalised after unquoting, never the first CIK-looking segment.
        url = "https://www.sec.gov/Archives/edgar/data/1627281/../1234/0001/x.htm"
        assert edgar_cik(url) == "1234"
        assert registrant_verdict(url, {CELLECTIS_CIK}, other_ciks=None) == EDGAR_OTHER
        quoted = "https://www.sec.gov/Archives/edgar/data/1627281/%2E%2E/1234/x.htm"
        assert edgar_cik(quoted) == "1234"


class TestOwnershipUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.sec.gov/Archives/edgar/data/1086364/000108636426000012/"
            "xslSCHEDULE_13G_X01/primary_doc.xml",
            "https://www.sec.gov/Archives/edgar/data/1599999/000159999926000003/"
            "xslF345X05/wk-form4_1767000000.xml",
            "https://www.sec.gov/Archives/edgar/data/1086364/000108636426000012/"
            "xslSCHEDULE_13D_X01/primary_doc.xml",
        ],
    )
    def test_ownership_forms_are_recognised(self, url: str) -> None:
        assert is_ownership_filing_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            CBOE_URL, OWN_20F_URL, NO_CIK_URL,
            # N3: a document NAME is the filer's choice, never read as a form type.
            "https://www.sec.gov/Archives/edgar/data/1086364/000119312526000001/"
            "exhibit-form-3-of-agreement.htm",
            "https://www.sec.gov/Archives/edgar/data/1086364/000119312526000001/d1sc13ga.htm",
        ],
    )
    def test_ordinary_filings_are_not(self, url: str) -> None:
        assert not is_ownership_filing_url(url)


# ---------------------------------------------------------------------------
# Relabelling evidence
# ---------------------------------------------------------------------------


def _hit(
    cid: str, url: str, *, web: bool = True,
    text: str = "The Company operates a single reportable segment.",
) -> _Evidence:
    """A real corpus-hit payload through the real harvesting path."""
    item: dict[str, Any] = {
        "evidence_id": cid,
        "text": text,
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


CLLS_SUBJECT = EdgarSubject(
    ciks=frozenset({CELLECTIS_CIK}), other_ciks=None,
    match_names=("Cellectis S.A.",), tickers=("ALCLS", "CLLS"),
)
#: No SEC basis, and a directory that names CIK 1627281 as a different company.
NON_SEC_SUBJECT = EdgarSubject(ciks=frozenset(), other_ciks=frozenset({CELLECTIS_CIK}))


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
        (item,) = apply_edgar_registrant([_hit("ev:c:own", OWN_20F_URL)], NON_SEC_SUBJECT)
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_OTHER

    def test_knowing_nothing_is_unidentified_never_issuer(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:own", OWN_20F_URL)], EdgarSubject())
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_UNIDENTIFIED

    def test_a_holders_13g_naming_the_subject_is_a_counterparty_filing(self) -> None:
        url = (
            "https://www.sec.gov/Archives/edgar/data/1086364/000108636426000012/"
            "xslSCHEDULE_13G_X01/primary_doc.xml"
        )
        item = _hit("ev:c:13g", url, text="Name of issuer: CELLECTIS S.A. Ordinary shares.")
        (checked,) = apply_edgar_registrant([item], CLLS_SUBJECT)
        assert checked.edgar_registrant == EDGAR_COUNTERPARTY
        assert checked.ref is not None and checked.ref.source_kind == "third_party_filing"
        assert statement_about_other_registrant([checked]) == ()

    def test_an_ownership_form_not_naming_the_subject_is_another_registrants(self) -> None:
        # N3: the form type alone does not make a filing about the subject.
        url = (
            "https://www.sec.gov/Archives/edgar/data/1086364/000108636426000012/"
            "xslSCHEDULE_13G_X01/primary_doc.xml"
        )
        item = _hit("ev:c:13g", url, text="Name of issuer: Vexmoor Holdings Inc.")
        (checked,) = apply_edgar_registrant([item], CLLS_SUBJECT)
        assert checked.edgar_registrant == EDGAR_OTHER

    def test_a_form4_issuer_trading_symbol_names_the_subject(self) -> None:
        url = (
            "https://www.sec.gov/Archives/edgar/data/1599999/000159999926000003/"
            "xslF345X05/wk-form4_1767000000.xml"
        )
        item = _hit("ev:c:f4", url, text="<issuerTradingSymbol>CLLS</issuerTradingSymbol>")
        (checked,) = apply_edgar_registrant([item], CLLS_SUBJECT)
        assert checked.edgar_registrant == EDGAR_COUNTERPARTY

    def test_an_acquirers_filing_naming_the_subject_is_a_counterparty_filing(self) -> None:
        item = _hit(
            "ev:c:8k", CBOE_URL,
            text="Acquirer Corp entered into an agreement to acquire Cellectis S.A. for cash.",
        )
        (checked,) = apply_edgar_registrant([item], CLLS_SUBJECT)
        assert checked.edgar_registrant == EDGAR_COUNTERPARTY
        assert statement_about_other_registrant([checked]) == ()

    def test_the_company_in_another_filing_does_not_name_the_subject(self) -> None:
        (item,) = apply_edgar_registrant([_hit("ev:c:cboe", CBOE_URL)], CLLS_SUBJECT)
        assert item.edgar_registrant == EDGAR_OTHER

    def test_a_url_without_cik_is_never_an_issuer_filing(self) -> None:
        named = "Cellectis S.A. filed its annual report."
        (item,) = apply_edgar_registrant([_hit("ev:c:x", NO_CIK_URL, text=named)], CLLS_SUBJECT)
        assert item.ref is not None and item.ref.source_kind == "third_party_filing"
        assert item.edgar_registrant == EDGAR_UNIDENTIFIED
        # One that never names the subject is not the subject's at all (CLLS4 review).
        (bare,) = apply_edgar_registrant([_hit("ev:c:y", NO_CIK_URL)], CLLS_SUBJECT)
        assert bare.ref is not None and bare.ref.source_kind == "third_party_filing"
        assert bare.edgar_registrant == EDGAR_OTHER

    def test_bridge_documents_are_never_rejudged(self) -> None:
        # The SEC bridge's chunks are not web (no source_class): fetched for the
        # subject's own CIK by construction. Even a resolver that found nothing, or a
        # different CIK in the path, leaves them exactly as they were.
        bridge = _hit("ev:c:bridge", OWN_20F_URL, web=False)
        other_path = _hit("ev:c:bridge2", CBOE_URL, web=False)
        for subject in (CLLS_SUBJECT, EdgarSubject(), NON_SEC_SUBJECT):
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
    name: str = "Cellectis S.A.",
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    inv = LLMInvestigator(
        session=object(),
        company_id=uuid.uuid4(),
        ticker=ticker,
        exchange=exchange,
        client=_Client(findings),
        company_name=name,
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

    async def test_a_non_sec_subject_drops_statements_on_a_named_other_registrant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, None,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A."),
             SimpleNamespace(ticker="CFR", cik="39263", name="Cullen/Frost Bankers, Inc.")],
        )
        inv, findings, gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:own"])], [_hit("ev:c:own", OWN_20F_URL)],
            ticker="CFR", exchange="SIX", name="Compagnie Financiere Richemont SA",
        )
        assert findings == []
        assert inv.diagnostics.statements_about_other_registrant == 1
        assert [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_no_basis_and_a_name_agreeing_cik_is_kept_but_not_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D1: sec_listing_for returns None on an unreadable record or an unknown venue
        # spelling, NOT only for a non-SEC issuer, so the filer's own page is not
        # dropped. N1: nor is it the issuer's filing — a name match is not identity.
        _patch_subject(
            monkeypatch, None,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="CELLECTIS S.A.")],
        )
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:own"])], [_hit("ev:c:own", OWN_20F_URL)],
            exchange="Euronext Paris (unrecognised spelling)",
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_a_namesake_is_never_the_issuer_with_no_basis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # N1: "The Sage Group plc" (LSE) agrees with Sage Therapeutics under the prefix
        # rule of _names_agree. Its 10-K must never become Sage Group's issuer filing.
        _patch_subject(
            monkeypatch, None,
            [SimpleNamespace(ticker="SAGE", cik="1597553", name="Sage Therapeutics, Inc.")],
        )
        url = "https://www.sec.gov/Archives/edgar/data/1597553/000159755326000010/sage-10k.htm"
        inv, findings, _gaps = await _run(
            [("Revenue rose on subscription growth.", ["ev:c:sage"])],
            [_hit("ev:c:sage", url)],
            ticker="SGE", exchange="LSE", name="The Sage Group plc",
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert "issuer_filing" not in findings[0].source_kinds
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_no_basis_and_an_unreadable_directory_keeps_it_unidentified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # D1, the verified failure: directory unreadable AND no basis. Before the fix
        # Cellectis's own /data/1627281/ page was "other" and dropped.
        _patch_subject(monkeypatch, None)

        async def _unreadable() -> list[Any]:
            raise OSError("sec.gov unreachable")

        monkeypatch.setattr(inv_mod, "_load_sec_directory", _unreadable)
        inv, findings, gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:own"])], [_hit("ev:c:own", OWN_20F_URL)]
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_no_basis_and_a_cik_the_directory_does_not_hold_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An exchange registrant has no ticker, so the directory does not hold it.
        _patch_subject(
            monkeypatch, None,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A.")],
        )
        inv, findings, _gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:cboe"])], [_hit("ev:c:cboe", CBOE_URL)]
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    async def test_an_ownership_filing_about_the_subject_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(monkeypatch, SIBLING)
        url = (
            "https://www.sec.gov/Archives/edgar/data/1599999/000159999926000003/"
            "xslF345X05/wk-form4_1767000000.xml"
        )
        inv, findings, _gaps = await _run(
            [("A director bought 10,000 ADSs in October 2026.", ["ev:c:f4"])],
            [_hit("ev:c:f4", url, text="Form 4. Issuer: Cellectis S.A. (CLLS).")],
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

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
        named = "Cellectis S.A. filed its annual report."
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:x"])], [_hit("ev:c:x", NO_CIK_URL, text=named)]
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


# ---------------------------------------------------------------------------
# D3 — a publisher class is never "the other company's own site"
# ---------------------------------------------------------------------------


class TestPublisherFirewallKeepsPublishers:
    def _item(self, source_class: str) -> _Evidence:
        return _Evidence(
            citation_id="ev:c:enx", kind="search_company_corpus", text="{}", web=True,
            origin_key="euronext.com", source_class=source_class,
        )

    def test_a_registered_exchange_does_not_own_its_announcements(self) -> None:
        others = other_company_labels(["Euronext N.V."], "Cellectis S.A.")
        assert others.get("euronext") == "Euronext N.V."
        item = self._item("exchange_announcement")
        assert foreign_publisher(["ev:c:enx"], [item], others) is None

    @pytest.mark.parametrize(
        "source_class",
        ["regulatory_filing", "regulator_publication", "government_publication",
         "statistical_agency", "major_financial_press", "aggregator"],
    )
    def test_every_publisher_class_is_kept(self, source_class: str) -> None:
        others = other_company_labels(["Euronext N.V."], "Cellectis S.A.")
        assert foreign_publisher(["ev:c:enx"], [self._item(source_class)], others) is None

    def test_the_companys_own_web_page_still_drops(self) -> None:
        others = other_company_labels(["Euronext N.V."], "Cellectis S.A.")
        item = self._item("company_web_page")
        assert foreign_publisher(["ev:c:enx"], [item], others) == "Euronext N.V."


# ---------------------------------------------------------------------------
# N2 — "names the subject" needs its full name or an exchange-framed ticker
# ---------------------------------------------------------------------------


class TestFilingNamesSubject:
    @pytest.mark.parametrize(
        ("text", "names", "tickers"),
        [
            ("NET INCOME attributable to common stockholders rose.", ["Cloudflare, Inc."],
             ["NET"]),
            ("A summary of significant accounting policies follows.",
             ["Agilent Technologies, Inc."], ["A"]),
            ("CASH and cash equivalents at end of period.", ["Pathward Financial"], ["CASH"]),
            ("The exchange operates an electronic trading platform.",
             ["Electronic Arts Inc."], ["EA"]),
            ("Total stockholders' equity increased.", ["Equity Commonwealth"], ["EQC"]),
            ("Next year the company expects growth.", ["Next plc"], ["NXT"]),
            ("The Company operates a single reportable segment.", ["Cellectis S.A."],
             ["CLLS", "ALCLS"]),
        ],
    )
    def test_words_and_bare_tickers_never_name_the_subject(
        self, text: str, names: list[str], tickers: list[str]
    ) -> None:
        assert not filing_names_subject(text, names, tickers)

    @pytest.mark.parametrize(
        ("text", "names", "tickers"),
        [
            ("Cloudflare, Inc. (NYSE: NET) agreed to a partnership.", ["Zzz"], ["NET"]),
            ("Agilent (NYSE: A) will supply instruments.", ["Zzz"], ["A"]),
            ("We agreed to acquire Electronic Arts Inc. for cash.", ["Electronic Arts Inc."],
             ["EA"]),
            ("A licence with Cellectis covers TALEN editing.", ["Cellectis S.A."], ["CLLS"]),
            ("Holders of ADSs (Nasdaq: CLLS) may tender.", ["Zzz"], ["CLLS"]),
        ],
    )
    def test_a_full_name_or_an_exchange_framed_ticker_does(
        self, text: str, names: list[str], tickers: list[str]
    ) -> None:
        assert filing_names_subject(text, names, tickers)

    def test_a_json_record_is_read_through_its_strings(self) -> None:
        text = json.dumps({"text": "Accord avec Compagnie Financière Richemont SA."})
        assert filing_names_subject(text, ["Compagnie Financière Richemont SA"], ["CFR"])

    def test_issuer_trading_symbol_counts_only_on_an_ownership_form(self) -> None:
        text = "<issuerTradingSymbol>NET</issuerTradingSymbol>"
        assert not filing_names_subject(text, ["Cloudflare, Inc."], ["NET"])
        assert filing_names_subject(text, ["Cloudflare, Inc."], ["NET"], ownership_form=True)

    def test_a_net_income_filing_stays_another_registrants(self) -> None:
        subject = EdgarSubject(
            ciks=frozenset({"1477333"}), other_ciks=None,
            match_names=("Cloudflare, Inc.",), tickers=("NET",),
        )
        item = _hit("ev:c:x", CBOE_URL, text="NET INCOME was $12.1 million.")
        (checked,) = apply_edgar_registrant([item], subject)
        assert checked.edgar_registrant == EDGAR_OTHER


@pytest.mark.parametrize(
    "text",
    ["Nasdaq Global Market: CLLS", "Nasdaq Global Select Market: CLLS", "Nasdaq Capital Market: CLLS"],
)
def test_every_nasdaq_tier_frames_a_ticker(text: str) -> None:
    import re

    from app.services.web_research.edgar_registrant import _EXCHANGE_CONTEXT

    assert re.search(_EXCHANGE_CONTEXT + "CLLS", text)


#: The live CLLS4 page: a Cboe EDGA Exchange X-17A-5 PDF, filed on paper, so its EDGAR
#: address carries no registrant.
VPRR_URL = "https://www.sec.gov/Archives/edgar/vprr/2600/26000283.pdf"


class TestAnEdgarPageWithNoRegistrantInItsAddress:
    """ADR-079 review (CLLS4): a CIK-less EDGAR page supports a statement about the
    subject only when its own text names the subject."""

    async def test_the_live_vprr_exhibit_statement_is_dropped_with_a_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, SIBLING,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A.")],
        )
        inv, findings, gaps = await _run(
            [(CBOE_STATEMENT, ["ev:c:vprr"]), (OWN_STATEMENT, ["ev:c:own"])],
            [_hit("ev:c:vprr", VPRR_URL), _hit("ev:c:own", OWN_20F_URL)],
        )
        assert [f.statement for f in findings] == [OWN_STATEMENT]
        assert inv.diagnostics.statements_about_other_registrant == 1
        (entity,) = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert "no registrant in its address" in entity.description

    async def test_a_cik_less_page_that_names_the_subject_is_kept_as_third_party(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_subject(
            monkeypatch, SIBLING,
            [SimpleNamespace(ticker="CLLS", cik="1627281", name="Cellectis S.A.")],
        )
        text = "Cellectis S.A. reported research and development expenses for the year."
        inv, findings, _gaps = await _run(
            [(OWN_STATEMENT, ["ev:c:vprr"])], [_hit("ev:c:vprr", VPRR_URL, text=text)],
        )
        assert [f.source_kinds for f in findings] == [("third_party_filing",)]
        assert inv.diagnostics.statements_about_other_registrant == 0

    def test_a_cik_bearing_page_the_directory_cannot_place_stays_unidentified(self) -> None:
        subject = EdgarSubject(ciks=frozenset(), other_ciks=frozenset(),
                               match_names=("Cellectis S.A.",), tickers=("CLLS",))
        (item,) = inv_mod.apply_edgar_registrant([_hit("ev:c:own", OWN_20F_URL)], subject)
        assert item.edgar_registrant == "unidentified"

    def test_nothing_known_about_the_subject_never_drops(self) -> None:
        (item,) = inv_mod.apply_edgar_registrant([_hit("ev:c:vprr", VPRR_URL)], EdgarSubject())
        assert item.edgar_registrant == "unidentified"
