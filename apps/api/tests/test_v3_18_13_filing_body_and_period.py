"""V3.18.13 — the filing body is the filing, and an annual report is not a quarter.

Three defects, all found by reading MP Materials' first live V3.18 report and then
checking the production database. MP is the generalisation case: rare earths rather than
copper, and a company whose filings the corpus had never ingested.

1. **The corpus held an EXHIBIT instead of the 10-K.** MP's Exhibit 96.1 — the S-K 1300
   technical report summary, published as ``mpmcexhibit961123125.htm`` — is not named
   ``ex-96.htm``, so the exhibit filter missed it whenever the filing index carried no
   ``type``. Step 3 of the selector then prefers the LARGEST body document, and a
   500-page technical report beats a 10-K. 1,938 of MP's 1,944 corpus chunks came from
   that exhibit; the 10-K was never ingested.
2. **Readiness accepted the exhibit as the filing.** A 10-K's exhibits live in the same
   accession folder and match the same URL fragment, so "is this filing searchable?"
   answered yes on a document that is not the filing — permanently, because a ready
   filing is never reacquired.
3. **A 500-page technical report was stamped 2027-Q1.** ``document_period`` reads a
   bounded slice of body text, and its quarter rules were written for a quarterly
   results release, whose leading text IS about its own period. In a technical report
   summary, "the first quarter of 2027" is a FORECAST. The period reached 1,938 chunks
   and then the report, where findings displayed a period in the future — visible only
   because V3.18.10 gave findings their periods back.
"""

from __future__ import annotations

from app.services.corpus.documents import _contradicts_document_type
from app.services.corpus.filing_evidence import is_exhibit_url
from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, PERIOD_TYPE_QUARTER
from app.services.sources.sec_filing_documents import (
    is_exhibit_name,
    select_primary_document,
)

MP_EXHIBIT = "mpmcexhibit961123125.htm"
MP_BODY = "mp-20251231.htm"
MP_FOLDER = "https://www.sec.gov/Archives/edgar/data/1801368/000180136826000008/"


class TestAnExhibitIsNotTheFiling:
    def test_mps_exhibit_is_recognised_by_its_name(self) -> None:
        # The live name. It says "exhibit" and it does not start with "ex-".
        assert is_exhibit_name(MP_EXHIBIT) is True

    def test_a_body_document_is_not_an_exhibit(self) -> None:
        for name in (MP_BODY, "scco-20251231x10k.htm", "a-20260630.htm"):
            assert is_exhibit_name(name) is False

    def test_a_company_whose_name_starts_with_ex_is_not_an_exhibit(self) -> None:
        """`exelon-20251231.htm` is a filing body. The rule needs a digit after the
        marker, which is what keeps this from being a name-prefix guess."""
        assert is_exhibit_name("exelon-20251231.htm") is False
        assert is_exhibit_name("express-20251231.htm") is False

    def test_the_selector_prefers_the_10k_over_a_far_larger_exhibit(self) -> None:
        """The live shape: no `type` on either entry, and the exhibit is 10x bigger."""
        entries = [
            {"name": MP_EXHIBIT, "type": "", "size": "12000000"},
            {"name": MP_BODY, "type": "", "size": "1200000"},
        ]
        chosen = select_primary_document(entries, form_type="10-K")
        assert chosen is not None and chosen["name"] == MP_BODY

    def test_an_exhibit_is_still_selectable_when_it_is_all_there_is(self) -> None:
        entries = [{"name": MP_EXHIBIT, "type": "", "size": "12000000"}]
        assert select_primary_document(entries, form_type="10-K") is None
        allowed = select_primary_document(
            entries, form_type="10-K", allow_exhibits=True
        )
        assert allowed is not None and allowed["name"] == MP_EXHIBIT

    def test_the_url_rule_and_the_name_rule_agree(self) -> None:
        assert is_exhibit_url(MP_FOLDER + MP_EXHIBIT) is True
        assert is_exhibit_url(MP_FOLDER + MP_BODY) is False
        assert is_exhibit_url(None) is False


class TestAnAnnualDocumentCannotCoverAQuarter:
    def test_the_contradiction_is_refused(self) -> None:
        assert _contradicts_document_type("annual_report", PERIOD_TYPE_QUARTER) is True
        assert _contradicts_document_type("annual_report", "half") is True

    def test_every_other_combination_is_left_alone(self) -> None:
        # An interim report legitimately carries a quarter; `other` claims nothing.
        assert _contradicts_document_type("interim_report", PERIOD_TYPE_QUARTER) is False
        assert _contradicts_document_type("annual_report", PERIOD_TYPE_ANNUAL) is False
        assert _contradicts_document_type("other", PERIOD_TYPE_QUARTER) is False
        assert _contradicts_document_type("annual_report", None) is False
