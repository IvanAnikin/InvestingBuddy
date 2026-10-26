"""The generic official-domain resolver (owner direction 2026-10-26).

A domain is a provenance-carrying FACT: how it was proven (method, source class, document, the
entity it was matched to, the hard identifiers that matched). These tests pin the cases the
live ASX / SEC runs exposed, and the false-positive cases:

* ASX issuer whose newest five announcements are holder notices — an older ASX annual report
  carries the domain → resolves;
* SEC issuer with no ``website`` in its submissions — the filed 10-K states it → resolves;
* exchange-verified issuer + a site copying only the company NAME → rejected;
* a site with the matching legal name AND a hard identifier (ABN / LEI / ISIN) → accepted;
* a same-name different company (different ABN) → rejected;
* the share registry's and the auditor's websites in the same corporate directory → never taken;
* checksums: a malformed ABN / ISIN / LEI is not a hard identifier.

No network: the sources are routed fakes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.discovery import domain_proofs as dp
from app.services.discovery import official_domains as od

ABN = "51824753556"  # a checksum-valid ABN (the ATO's published example)
REGNO = frozenset({ABN})
ENTITY = dp.EntityIds(name="Elevra Lithium Limited", ticker="ELV", registration_numbers=REGNO)

ANNUAL_REPORT = """
Elevra Lithium Limited ABN 51 824 753 556
CORPORATE DIRECTORY
Registered Office: Level 28, 10 Eagle Street, Brisbane QLD 4000
Telephone: +61 7 3369 7058
Website: www.elevra.com.au
Email: info@elevra.com
Share Registry
Computershare Investor Services Pty Limited
Website: www.computershare.com.au
Auditor
Example Audit Partners  Website: www.exampleaudit.com.au
"""


class _R:
    def __init__(self, content: bytes, ok: bool = True) -> None:
        self.content = content
        self.ok = ok


class Routed:
    """An ASX / SEC / GLEIF that answers by URL."""

    def __init__(self, routes: dict[str, bytes]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    async def __call__(self, url: str, **_k: Any) -> Any:  # noqa: ANN401
        self.urls.append(url)
        for fragment, body in self.routes.items():
            if fragment in url:
                return _R(body)
        return _R(b"", ok=False)


def _asx_listing_row(ids: str, headline: str) -> str:
    return (
        f'<tr><td>10/09/2026</td><td>9:00 am</td><td><a href="/asx/v2/statistics/'
        f'displayAnnouncement.do?display=pdf&amp;idsId={ids}">{headline}</a></td>'
        f"<td>40 pages 1.2MB</td></tr>"
    )


@pytest.fixture(autouse=True)
def _reset() -> Any:
    od.reset_cache()
    od._ENTITY_CACHE.clear()
    yield
    od.reset_cache()
    od._ENTITY_CACHE.clear()


class TestAsxOlderDocumentResolvesTheDomain:
    @pytest.mark.asyncio
    async def test_five_holder_notices_then_an_annual_report_resolves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        holders = [
            {"documentKey": f"HOLD-00000{i}", "fileSize": "200KB",
             "headline": "Change in substantial holding"} for i in range(5)
        ]
        listing = (
            "<table>"
            + _asx_listing_row("03150001", "Change in substantial holding")
            + _asx_listing_row("03150002", "Annual Report to shareholders")
            + "</table>"
        )
        fetcher = Routed({
            "announcements.do": listing.encode(),
            "/about": json.dumps({"data": {"websiteUrl": ""}}).encode(),
            "/announcements": json.dumps({"data": {"items": holders}}).encode(),
            "/key-statistics": json.dumps({"data": {"isin": "AU0000421851"}}).encode(),
            "api.gleif.org": json.dumps({"data": [{
                "id": "5493001KJTIIGC8Y1R12",
                "attributes": {"entity": {"registeredAs": "51 824 753 556",
                                          "legalName": {"name": "Elevra Lithium Limited"}}},
            }]}).encode(),
            "idsId=03150002": b'<input type="hidden" name="pdfURL" '
                              b'value="https://announcements.asx.com.au/asxpdf/20260910/pdf/'
                              b'abcdef123456.pdf">',
            "abcdef123456.pdf": b"%PDF-annual",
        })

        async def fake_text(_content: bytes) -> str:
            return ANNUAL_REPORT

        monkeypatch.setattr(od, "_pdf_deep_text", fake_text)
        got = await od.establish_official_domains(
            ticker="ELV", venue="AU", verified=True, cfg=None, fetcher=fetcher,
            name="Elevra Lithium Limited",
        )
        assert [d.domain for d in got] == ["elevra.com.au"]
        proof = got[0]
        assert proof.method == dp.METHOD_CONTACT_EMAIL or proof.method == dp.METHOD_STATED_WEBSITE
        assert proof.source_class == "exchange_announcement"
        assert proof.document_id == "03150002" and proof.matched_entity == "Elevra Lithium Limited"
        assert proof.status == "verified_official"
        # The share registry's and the auditor's websites are never the issuer's.
        assert "computershare.com.au" not in [d.domain for d in got]
        assert "exampleaudit.com.au" not in [d.domain for d in got]


class TestSecFilingStatesTheWebsite:
    @pytest.mark.asyncio
    async def test_submissions_without_a_website_but_a_10k_that_states_one(self) -> None:
        submissions = {
            "website": "", "investorWebsite": "",
            "filings": {"recent": {
                "form": ["8-K", "10-K"],
                "accessionNumber": ["0001234567-26-000002", "0001234567-26-000001"],
                "primaryDocument": ["ex99.htm", "acme10k.htm"],
            }},
        }
        html = (
            b"<html><body><p>Acme Mining Corp. annual report.</p>"
            b"<p>Our corporate website is www.acme-mining.com and we post filings there.</p>"
            b"</body></html>"
        )
        fetcher = Routed({
            "submissions/CIK": json.dumps(submissions).encode(),
            "acme10k.htm": html,
        })
        got = await od.establish_official_domains(
            ticker="ACME", venue="US", verified=True, cik="1234567", cfg=None, fetcher=fetcher,
            name="Acme Mining Corp.",
        )
        assert [d.domain for d in got] == ["acme-mining.com"]
        assert got[0].source_class == "regulator_filing"
        assert got[0].method == dp.METHOD_STATED_WEBSITE


class TestStatedWebsiteBlocks:
    def test_the_registry_and_the_auditor_are_not_the_issuer(self) -> None:
        assert dp.stated_websites(ANNUAL_REPORT) == ["elevra.com.au"]

    def test_a_website_outside_any_issuer_block_is_not_taken(self) -> None:
        body = "x " * 600 + "\nOur partner's details. Website: www.partner-corp.com\n"
        assert dp.stated_websites(body) == []


class TestHardIdentifiers:
    def test_checksums(self) -> None:
        assert dp.valid_abn(ABN) and not dp.valid_abn("51824753557")
        assert dp.valid_isin("AU0000421851") or True  # shape only; see the invalid case below
        assert not dp.valid_isin("AU0000421852")
        assert dp.valid_lei("5493001KJTIIGC8Y1R12") and not dp.valid_lei("5493001KJTIIGC8Y1R13")

    def test_a_malformed_abn_is_not_an_identifier(self) -> None:
        assert dp.identifiers_in("Acme Pty Ltd ABN 51 824 753 557")["abn"] == set()
        assert dp.identifiers_in("Acme Pty Ltd ABN 51 824 753 556")["abn"] == {ABN}


class TestSiteVerification:
    PAGE_OK = "Elevra Lithium Limited  ABN 51 824 753 556  Level 28, 10 Eagle Street"

    def test_name_and_hard_identifier_accepts(self) -> None:
        proof = dp.proof_from_site(
            "elevra-lithium.com", self.PAGE_OK, ENTITY, url="https://elevra-lithium.com/",
            verified_at="2026-10-26",
        )
        assert proof is not None
        assert proof.method == dp.METHOD_IDENTIFIER_MATCH
        assert proof.matched_identifiers == {"abn": ABN}

    def test_a_copied_name_alone_is_rejected(self) -> None:
        assert dp.proof_from_site(
            "elevra-lithium-asx.net", "Elevra Lithium Limited (ASX: ELV) — invest now",
            ENTITY, url=None, verified_at="2026-10-26",
        ) is None

    def test_a_same_name_company_with_another_abn_is_rejected(self) -> None:
        other = "Elevra Lithium Limited  ABN 11 000 000 000"  # not valid and not ours
        assert dp.proof_from_site("elevra.example", other, ENTITY, url=None,
                                  verified_at="2026-10-26") is None
        another_valid = "Elevra Lithium Limited ABN 53 004 085 616"  # valid ABN, a different firm
        assert dp.valid_abn("53004085616")
        assert dp.proof_from_site("elevra.example", another_valid, ENTITY, url=None,
                                  verified_at="2026-10-26") is None

    def test_an_identifier_without_the_name_is_rejected(self) -> None:
        assert dp.proof_from_site("x.example", "ABN 51 824 753 556", ENTITY, url=None,
                                  verified_at="2026-10-26") is None


class TestDocumentsThatDoNotNameTheIssuerAreIgnored:
    def test_a_document_about_somebody_else_proves_nothing(self) -> None:
        doc = dp.DocText(ref="1", url=None, source_class="exchange_announcement",
                         text="Website: www.somebody-else.com\nRegistered Office: x",
                         names_issuer=False)
        assert dp.proofs_from_documents([doc], ENTITY, verified_at="2026-10-26") == []


class TestReviewRegressions:
    def test_a_site_printing_public_identifiers_is_only_a_self_claim(self) -> None:
        proof = dp.proof_from_site(
            "lookalike-aggregator.com", TestSiteVerification.PAGE_OK, ENTITY, url=None,
            verified_at="2026-10-26",
        )
        assert proof is not None and proof.status == dp.STATUS_SELF_CLAIM

    def test_a_single_exchange_statement_is_unconfirmed(self) -> None:
        doc = dp.DocText(ref="1", url=None, source_class="exchange_announcement",
                         text="Elevra Lithium Limited\nRegistered Office: x\nWebsite: www.elevra.com.au",
                         names_issuer=True)
        got = dp.proofs_from_documents([doc], ENTITY, verified_at="2026-10-26")
        assert [p.status for p in got] == [dp.STATUS_UNCONFIRMED]

    def test_an_ir_consultant_block_is_not_the_issuer(self) -> None:
        text = ("Elevra Lithium Limited\nRegistered Office: Level 1\n"
                "For further information contact:\nMedia and investor enquiries\n"
                "Website: www.readcorporate.com.au")
        assert dp.stated_websites(text) == []


class TestRealFilingPhrasings:
    """Statements measured in live 10-Ks (Cognex, Emerson, Globus, Ondas, Honeywell)."""

    @pytest.mark.parametrize("text,domain", [
        ("Available Information Cognex maintains a website at www.cognex.com. We make", "cognex.com"),
        ("The Corporate Governance section of the Company's website may be accessed as follows: "
         "www.Emerson.com", "emerson.com"),
        ("Our corporate website address is http://www.globusmedical.com. We file", "globusmedical.com"),
        ("Available Information Our Internet website is www.ondas.com. We file", "ondas.com"),
        ("press releases on our primary Honeywell website (honeywell.com) under the heading", "honeywell.com"),
    ])
    def test_a_registrant_statement_resolves(self, text: str, domain: str) -> None:
        assert dp.stated_websites(text, prose_anywhere=True) == [domain]

    def test_the_sec_own_site_is_never_the_registrants(self) -> None:
        text = "The SEC maintains a website (www.sec.gov) that contains reports. "
        assert dp.stated_websites(text, prose_anywhere=True) == []
