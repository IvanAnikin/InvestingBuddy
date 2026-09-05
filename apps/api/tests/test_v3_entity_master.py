"""The entity master — V3.2 Slice 2.1.

WHAT THESE TESTS PIN
====================
The failure the whole slice exists for. `BA` + LSE resolved to **Boeing's** CIK
when the issuer was BAE Systems; `MC` + PA returned Moelis for LVMH. Those were
fixed by special-casing a ticker table. Here they are made structurally
impossible, and these tests are what says so:

  * two listings on two venues resolve to ONE legal entity;
  * two issuers sharing a ticker on DIFFERENT venues stay two entities and neither
    can reach the other's CIK;
  * two issuers cannot share a live ticker on the SAME venue — the database
    refuses it;
  * a checksum-invalid LEI or ISIN is REFUSED, never stored at low confidence;
  * two entities cannot both currently hold one LEI — again the database;
  * a ticker change closes a window and opens another, so history still resolves;
  * an ISIN cannot be attached to a legal entity, nor a LEI to a security;
  * with ``V3_ENTITY_MASTER_ENABLED`` off nothing is written at all;
  * ``companies`` is untouched, and nothing in the package reaches the network.

TEST VECTORS ARE GENERATED, NOT REMEMBERED
==========================================
Every LEI here is built with the ISO 7064 MOD 97-10 *generation* rule and every
ISIN with its own check-digit rule, rather than transcribed as a real company's
identifier. A half-remembered "real" LEI is precisely the sort of unverified
factual claim this platform refuses everywhere else — and one of the candidates
tried while writing this module turned out to fail its checksum, which is exactly
how a test comes to assert the bug.

The ISIN and LEI *algorithms* are separately confirmed against published
algorithm vectors: ``US0378331005`` reproduces check digit 5 and GLEIF's own
``5493001KJTIIGC8Y1R12`` satisfies MOD 97-10. Those are algorithm test vectors,
not claims about who owns them.

Everything runs against a real database — real INSERTs, real partial-unique-index
violations, real CHECK constraints. A mocked session would let "the database
enforces it" pass while being false, because the assertion would be about what we
asked the mock. No clock dependence: every dated test passes explicit dates.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.company import Company
from app.models.legal_entity import (
    EntityAlias,
    EntityIdentifier,
    LegalEntity,
    Security,
    SecurityListing,
)
from app.services.entities import identifiers as ident_mod
from app.services.entities import master as master_mod
from app.services.entities.identifiers import (
    GLOBAL_SCOPE,
    SCHEME_CIK,
    SCHEME_COMPANY_REGISTER,
    SCHEME_FIGI,
    SCHEME_ISIN,
    SCHEME_LEI,
    SCHEMES,
    SUBJECT_LEGAL_ENTITY,
    SUBJECT_SECURITY,
    isin_check_digit,
    isin_checksum_valid,
    lei_check_digits,
    lei_checksum_valid,
    normalize_identifier,
    subject_for_scheme,
)
from app.services.entities.master import (
    EntityInput,
    close_listing,
    entity_key_for,
    find_entity_by_identifier,
    listings_for_entity,
    normalize_entity_name,
    record_alias,
    record_identifier,
    resolve_entity_by_listing,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
    venue_key_for,
)
from app.services.entities.vocabulary import (
    ALIAS_FORMER_LEGAL_NAME,
    ALIAS_FORMER_TICKER,
    ENTITY_ACTIVE,
    LISTING_ACTIVE,
    LISTING_DELISTED,
    NON_ACTIONABLE_RESOLUTION_STATES,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CONFLICTING,
    RESOLUTION_RESOLVED,
    RESOLUTION_UNRESOLVED,
    SECURITY_ADR,
    SECURITY_ORDINARY_SHARE,
    is_depositary_receipt,
    require_alias_type,
    require_entity_status,
    require_listing_status,
    require_resolution_state,
    require_security_type,
)
from app.services.exchange_registry import (
    currency_for_exchange,
    price_quote_currency_for_exchange,
)


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {"v3_entity_master_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _lei(base18: str) -> str:
    """A structurally valid LEI built from the MOD 97-10 generation rule."""
    return base18 + lei_check_digits(base18)


def _isin(prefix: str, nsin: str) -> str:
    """A structurally valid ISIN built from its own check-digit rule."""
    body = f"{prefix}{nsin}"
    return body + str(isin_check_digit(body))


LEI_A = _lei("529900AAAAAAAAAA01")
LEI_B = _lei("213800BBBBBBBBBB02")
ISIN_A = _isin("DK", "006025269")
ISIN_B = _isin("GB", "000263494")


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    # ``create_all`` builds every registered table, and some carry JSONB columns
    # that SQLite cannot compile. Same shim the corpus suite needs, same reason.
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _entity(
    session, cfg: Settings, *, key: str, name: str, **kw: object
) -> LegalEntity:
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=key, legal_name=name, **kw), cfg=cfg
    )
    assert entity is not None
    return entity


async def _listed(
    session,
    cfg: Settings,
    *,
    entity_key: str,
    name: str,
    ticker: str,
    exchange: str,
    security_key: str = "ordinary_share:1",
) -> tuple[LegalEntity, Security, SecurityListing]:
    entity = await _entity(session, cfg, key=entity_key, name=name)
    security = await upsert_security(
        session,
        legal_entity_id=entity.id,
        security_key=security_key,
        cfg=cfg,
        is_primary=True,
    )
    assert security is not None
    listing = await upsert_listing(
        session,
        security_id=security.id,
        ticker=ticker,
        exchange=exchange,
        cfg=cfg,
        listing_status=LISTING_ACTIVE,
        is_primary=True,
    )
    assert listing is not None
    return entity, security, listing


# --------------------------------------------------------------------------- #
# Checksums — the algorithms, against published algorithm vectors
# --------------------------------------------------------------------------- #


class TestChecksumAlgorithms:
    def test_lei_mod97_accepts_a_published_algorithm_vector(self) -> None:
        # GLEIF's own LEI, used here purely as a MOD 97-10 vector.
        assert lei_checksum_valid("5493001KJTIIGC8Y1R12") is True

    def test_lei_generation_and_validation_are_inverses(self) -> None:
        for base in (
            "529900AAAAAAAAAA01",
            "213800ZZZZZZZZZZ99",
            "9845001234567890AB",
            "000000000000000000",
        ):
            assert lei_checksum_valid(base + lei_check_digits(base)) is True

    def test_a_single_mistyped_lei_character_fails(self) -> None:
        good = _lei("529900AAAAAAAAAA01")
        # Mutate the last check digit: MOD 97-10 catches a single-character slip
        # with probability ~96/97, which is the entire reason it is checked.
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        assert bad != good
        assert lei_checksum_valid(bad) is False

    def test_isin_check_digit_reproduces_four_country_prefixes(self) -> None:
        # Algorithm vectors: each of these is a well-formed ISIN whose check digit
        # the rule must reproduce. They are used as arithmetic, not as ownership.
        for value in ("US0378331005", "GB0002634946", "DK0060252690", "NL0010273215"):
            assert isin_check_digit(value[:11]) == int(value[11])
            assert isin_checksum_valid(value) is True

    def test_a_wrong_isin_check_digit_fails(self) -> None:
        good = _isin("DK", "006025269")
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        assert isin_checksum_valid(bad) is False

    def test_a_body_of_the_wrong_length_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError):
            isin_check_digit("TOOSHORT")
        with pytest.raises(ValueError):
            lei_check_digits("TOOSHORT")


# --------------------------------------------------------------------------- #
# Identifier normalisation — refusal is the feature
# --------------------------------------------------------------------------- #


class TestIdentifierNormalisation:
    def test_a_valid_lei_normalises_and_is_marked_checksum_verified(self) -> None:
        ident = normalize_identifier(SCHEME_LEI, f"  {LEI_A.lower()}  ")
        assert ident.value_normalized == LEI_A
        assert ident.subject == SUBJECT_LEGAL_ENTITY
        assert ident.checksum_verified is True
        assert ident.scope_key == GLOBAL_SCOPE

    def test_a_checksum_invalid_lei_is_refused_not_stored_weakly(self) -> None:
        bad = LEI_A[:-1] + str((int(LEI_A[-1]) + 1) % 10)
        with pytest.raises(ValueError, match="MOD 97-10"):
            normalize_identifier(SCHEME_LEI, bad)

    def test_a_checksum_invalid_isin_is_refused(self) -> None:
        bad = ISIN_A[:-1] + str((int(ISIN_A[-1]) + 1) % 10)
        with pytest.raises(ValueError, match="check digit"):
            normalize_identifier(SCHEME_ISIN, bad)

    def test_a_cik_is_zero_padded_to_ten_digits(self) -> None:
        assert normalize_identifier(SCHEME_CIK, "320193").value_normalized == "0000320193"
        assert normalize_identifier(SCHEME_CIK, "CIK0000012927").value_normalized == (
            "0000012927"
        )

    def test_a_cik_that_is_not_a_number_is_refused(self) -> None:
        # This is the Boeing bug's shape: a ticker string arriving where a CIK
        # belongs. It must not become an identifier.
        with pytest.raises(ValueError, match="not a CIK"):
            normalize_identifier(SCHEME_CIK, "BA")
        with pytest.raises(ValueError):
            normalize_identifier(SCHEME_CIK, "0")
        with pytest.raises(ValueError):
            normalize_identifier(SCHEME_CIK, "12345678901")

    def test_a_company_register_number_requires_a_jurisdiction(self) -> None:
        with pytest.raises(ValueError, match="requires a jurisdiction"):
            normalize_identifier(SCHEME_COMPANY_REGISTER, "12345678")
        ident = normalize_identifier(
            SCHEME_COMPANY_REGISTER, "12345678", jurisdiction="dk"
        )
        assert ident.scope_key == "DK"
        assert ident.jurisdiction == "DK"

    def test_the_same_register_number_in_two_jurisdictions_does_not_collide(
        self,
    ) -> None:
        dk = normalize_identifier(SCHEME_COMPANY_REGISTER, "12345678", jurisdiction="DK")
        gb = normalize_identifier(SCHEME_COMPANY_REGISTER, "12345678", jurisdiction="GB")
        assert dk.value_normalized == gb.value_normalized
        assert dk.scope_key != gb.scope_key

    def test_an_unknown_scheme_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unknown identifier scheme"):
            normalize_identifier("sedol", "0263494")

    def test_cusip_is_deliberately_not_a_recognised_scheme(self) -> None:
        # CUSIP is licensed data from CUSIP Global Services. Adding it is a
        # governance decision, not a vocabulary edit.
        assert "cusip" not in SCHEMES
        with pytest.raises(ValueError):
            normalize_identifier("cusip", "037833100")

    def test_each_scheme_declares_which_subject_it_describes(self) -> None:
        assert subject_for_scheme(SCHEME_LEI) == SUBJECT_LEGAL_ENTITY
        assert subject_for_scheme(SCHEME_CIK) == SUBJECT_LEGAL_ENTITY
        assert subject_for_scheme(SCHEME_COMPANY_REGISTER) == SUBJECT_LEGAL_ENTITY
        assert subject_for_scheme(SCHEME_ISIN) == SUBJECT_SECURITY
        assert subject_for_scheme(SCHEME_FIGI) == SUBJECT_SECURITY

    def test_only_the_schemes_with_check_digits_claim_verification(self) -> None:
        # A reader deciding how far to trust a stored value needs this to be part
        # of the contract rather than a comment.
        assert SCHEMES[SCHEME_LEI].checksum_verified is True
        assert SCHEMES[SCHEME_ISIN].checksum_verified is True
        assert SCHEMES[SCHEME_CIK].checksum_verified is False
        assert SCHEMES[SCHEME_FIGI].checksum_verified is False

    def test_an_over_long_value_is_refused_before_it_reaches_a_query(self) -> None:
        with pytest.raises(ValueError, match="maximum"):
            normalize_identifier(SCHEME_LEI, "X" * 200)


class TestFigiRepresentableButNotObtainable:
    """OPEN DECISION #10 is the user's: FIGI is representable, not sourced."""

    def test_a_structurally_valid_figi_normalises(self) -> None:
        ident = normalize_identifier(SCHEME_FIGI, "bbg000blnnh6")
        assert ident.value_normalized == "BBG000BLNNH6"
        assert ident.subject == SUBJECT_SECURITY

    def test_a_figi_missing_its_g_marker_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a FIGI"):
            normalize_identifier(SCHEME_FIGI, "BBX000BLNNH6")

    def test_a_figi_prefix_that_reads_as_a_country_code_is_refused(self) -> None:
        with pytest.raises(ValueError, match="ISO 3166"):
            normalize_identifier(SCHEME_FIGI, "GBG000BLNNH6")

    def test_a_figi_is_never_claimed_as_checksum_verified(self) -> None:
        # The FIGI check-digit variant could not be confirmed against a published
        # vector, and a validator that rejects VALID identifiers is worse than an
        # honest structural check. So the flag stays False and the docstring says so.
        ident = normalize_identifier(SCHEME_FIGI, "BBG000BLNNH6")
        assert ident.checksum_verified is False

    def test_no_openfigi_client_exists_yet(self) -> None:
        # Fails the moment somebody adds one, which forces OPEN DECISION #10 to be
        # taken deliberately rather than by a commit.
        #
        # What is searched for is CODE, not prose: a module named for the vendor,
        # an import of one, or its endpoint host. Grepping for the word would fire
        # on the docstrings that explain why the decision is open, which would make
        # the test unmaintainable and eventually deleted.
        root = Path(__file__).resolve().parents[1] / "app"
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if "openfigi" in path.name.lower():
                offenders.append(f"{path.relative_to(root)}: module name")
                continue
            text = path.read_text(encoding="utf-8")
            if "openfigi.com" in text.lower():
                offenders.append(f"{path.relative_to(root)}: endpoint host")
                continue
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any("openfigi" in name.lower() for name in names):
                    offenders.append(f"{path.relative_to(root)}: import")
        assert offenders == [], (
            "OpenFIGI usage is OPEN DECISION #10 and the user's. "
            f"Found in: {offenders}"
        )


# --------------------------------------------------------------------------- #
# Vocabulary — closed sets, fail closed
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_an_unrecognised_value_raises_in_every_vocabulary(self) -> None:
        for guard in (
            require_security_type,
            require_listing_status,
            require_entity_status,
            require_alias_type,
            require_resolution_state,
        ):
            with pytest.raises(ValueError, match="not a recognised"):
                guard("something_new")

    def test_recognised_values_are_case_folded_not_rejected(self) -> None:
        assert require_security_type(" Ordinary_Share ") == SECURITY_ORDINARY_SHARE
        assert require_listing_status("ACTIVE") == LISTING_ACTIVE

    def test_a_depositary_receipt_is_named_so_per_share_maths_can_refuse_it(
        self,
    ) -> None:
        assert is_depositary_receipt(SECURITY_ADR) is True
        assert is_depositary_receipt(SECURITY_ORDINARY_SHARE) is False

    def test_the_four_resolution_states_exist_and_only_one_is_actionable(self) -> None:
        assert require_resolution_state(RESOLUTION_RESOLVED) == RESOLUTION_RESOLVED
        assert RESOLUTION_RESOLVED not in NON_ACTIONABLE_RESOLUTION_STATES
        for state in (
            RESOLUTION_AMBIGUOUS,
            RESOLUTION_CONFLICTING,
            RESOLUTION_UNRESOLVED,
        ):
            assert state in NON_ACTIONABLE_RESOLUTION_STATES


# --------------------------------------------------------------------------- #
# Keys — identity comes from identifiers, never from a name
# --------------------------------------------------------------------------- #


class TestEntityKeys:
    def test_the_strongest_identifier_wins(self) -> None:
        assert entity_key_for(lei=LEI_A, cik="320193", ticker="AAPL") == f"lei:{LEI_A}"
        assert entity_key_for(cik="320193", ticker="AAPL") == "cik:0000320193"
        assert (
            entity_key_for(register="12345678", jurisdiction="DK")
            == "register:DK:12345678"
        )

    def test_a_ticker_only_key_is_venue_scoped_and_deliberately_over_splits(
        self,
    ) -> None:
        # This is the fallback, and it is the ONLY key that can be reused by a
        # different issuer. Scoping it to a venue is what keeps BA@XLON and
        # BA@XNYS apart instead of collapsing them into one entity.
        assert entity_key_for(venue_key="XLON", ticker="BA") == "listing:XLON:BA"
        assert entity_key_for(venue_key="XNYS", ticker="BA") == "listing:XNYS:BA"
        assert entity_key_for(venue_key="XLON", ticker="BA") != entity_key_for(
            venue_key="XNYS", ticker="BA"
        )

    def test_an_entity_with_no_identity_at_all_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one identifier"):
            entity_key_for()

    def test_an_invalid_identifier_cannot_become_a_key(self) -> None:
        bad = LEI_A[:-1] + str((int(LEI_A[-1]) + 1) % 10)
        with pytest.raises(ValueError):
            entity_key_for(lei=bad)

    def test_the_venue_key_prefers_the_mic_over_the_local_code(self) -> None:
        # Two sources naming the same venue differently must still produce one key.
        assert venue_key_for("LSE") == "XLON"
        assert venue_key_for("NASDAQ") == venue_key_for("nasdaq")

    def test_an_unknown_venue_still_gets_a_key_rather_than_a_shared_bucket(
        self,
    ) -> None:
        key = venue_key_for("SOME-NEW-VENUE")
        assert key
        assert key != venue_key_for("ANOTHER-NEW-VENUE")

    def test_name_normalisation_is_for_lookup_and_folds_only_punctuation(self) -> None:
        assert normalize_entity_name("Pandora A/S.") == "pandora a/s"
        assert normalize_entity_name("  BAE   Systems  plc ") == "bae systems plc"
        # Two genuinely different names must not fold together.
        assert normalize_entity_name("Pandora A/S") != normalize_entity_name(
            "Pandora Media Inc"
        )


# --------------------------------------------------------------------------- #
# The Boeing case — the reason this phase exists
# --------------------------------------------------------------------------- #


class TestTickerCollisionsAcrossVenues:
    async def test_the_same_ticker_on_two_venues_is_two_entities(self, session) -> None:
        cfg = _cfg()
        bae, _, _ = await _listed(
            session,
            cfg,
            entity_key=entity_key_for(venue_key="XLON", ticker="BA"),
            name="BAE Systems plc",
            ticker="BA",
            exchange="LSE",
        )
        boeing, boeing_sec, _ = await _listed(
            session,
            cfg,
            entity_key=entity_key_for(venue_key="XNYS", ticker="BA"),
            name="The Boeing Company",
            ticker="BA",
            exchange="NYSE",
        )
        assert bae.id != boeing.id

        # Only Boeing has a CIK, and it hangs off the LEGAL ENTITY, so there is no
        # path from BAE's listing to it.
        await record_identifier(
            session,
            scheme=SCHEME_CIK,
            value="12927",
            cfg=cfg,
            legal_entity_id=boeing.id,
            source="sec_edgar",
        )
        await session.commit()

        from_lse = await resolve_entity_by_listing(
            session, ticker="BA", exchange="LSE", cfg=cfg
        )
        from_nyse = await resolve_entity_by_listing(
            session, ticker="BA", exchange="NYSE", cfg=cfg
        )
        assert from_lse is not None and from_lse.id == bae.id
        assert from_nyse is not None and from_nyse.id == boeing.id

        bae_ciks = (
            (
                await session.execute(
                    select(EntityIdentifier).where(
                        EntityIdentifier.legal_entity_id == bae.id,
                        EntityIdentifier.scheme == SCHEME_CIK,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert bae_ciks == [], (
            "BAE Systems must have no CIK. Deriving one from the ticker string is "
            "the live failure this schema exists to make impossible."
        )
        assert boeing_sec is not None

    async def test_two_issuers_cannot_share_a_live_ticker_on_one_venue(
        self, session
    ) -> None:
        cfg = _cfg()
        _, first, _ = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="First Issuer A/S",
            ticker="XYZ",
            exchange="LSE",
        )
        other = await _entity(session, cfg, key="lei:" + LEI_B, name="Second Issuer plc")
        second = await upsert_security(
            session, legal_entity_id=other.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert second is not None and first is not None

        with pytest.raises(ValueError, match="BA\\+LSE"):
            await upsert_listing(
                session,
                security_id=second.id,
                ticker="XYZ",
                exchange="LSE",
                cfg=cfg,
            )

    async def test_the_database_refuses_it_too_not_only_the_service(
        self, session
    ) -> None:
        # The service check is a friendly error. The INDEX is the guarantee, and it
        # is what holds when two writers race past a read-then-write check.
        cfg = _cfg()
        _, first, _ = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="First Issuer A/S",
            ticker="XYZ",
            exchange="LSE",
        )
        other = await _entity(session, cfg, key="lei:" + LEI_B, name="Second Issuer plc")
        second = await upsert_security(
            session, legal_entity_id=other.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert second is not None and first is not None
        await session.commit()

        session.add(
            SecurityListing(
                id=uuid.uuid4(),
                security_id=second.id,
                venue_key="XLON",
                mic="XLON",
                exchange_code="LSE",
                ticker="XYZ",
                listing_status=LISTING_ACTIVE,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


# --------------------------------------------------------------------------- #
# Cross-listings — two listings, one entity
# --------------------------------------------------------------------------- #


class TestCrossListings:
    async def test_two_listings_of_one_security_resolve_to_one_entity(
        self, session
    ) -> None:
        cfg = _cfg()
        entity, security, _ = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="ASML Holding N.V.",
            ticker="ASML",
            exchange="NASDAQ",
        )
        assert security is not None
        second = await upsert_listing(
            session,
            security_id=security.id,
            ticker="ASML",
            exchange="AS",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
        )
        assert second is not None
        await session.commit()

        from_us = await resolve_entity_by_listing(
            session, ticker="ASML", exchange="NASDAQ", cfg=cfg
        )
        from_nl = await resolve_entity_by_listing(
            session, ticker="ASML", exchange="AS", cfg=cfg
        )
        assert from_us is not None and from_nl is not None
        assert from_us.id == from_nl.id == entity.id

        venues = await listings_for_entity(session, legal_entity_id=entity.id, cfg=cfg)
        assert len(venues) == 2
        assert {row.venue_key for row in venues} == {
            venue_key_for("NASDAQ"),
            venue_key_for("AS"),
        }

    async def test_an_adr_is_a_separate_security_of_the_same_entity(
        self, session
    ) -> None:
        cfg = _cfg()
        entity, ordinary, _ = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="An Issuer S.A.",
            ticker="ISS",
            exchange="PA",
        )
        adr = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
            description="Sponsored ADR",
        )
        assert adr is not None and ordinary is not None
        assert adr.id != ordinary.id
        await upsert_listing(
            session,
            security_id=adr.id,
            ticker="ISSAY",
            exchange="OTC",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
        )
        await session.commit()

        via_adr = await resolve_entity_by_listing(
            session, ticker="ISSAY", exchange="OTC", cfg=cfg
        )
        assert via_adr is not None and via_adr.id == entity.id
        # And the type survives, so per-share arithmetic can refuse to apply.
        assert is_depositary_receipt(adr.security_type) is True

    async def test_at_most_one_primary_security_and_listing_per_parent(
        self, session
    ) -> None:
        cfg = _cfg()
        entity, first, listing = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="An Issuer A/S",
            ticker="AAA",
            exchange="CO",
        )
        assert first is not None and listing is not None and first.is_primary is True
        second = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
            is_primary=True,
        )
        assert second is not None and second.is_primary is True
        await session.commit()
        await session.refresh(first)
        assert first.is_primary is False, "promotion must demote the outgoing primary"

        primaries = (
            await session.execute(
                select(func.count())
                .select_from(Security)
                .where(Security.legal_entity_id == entity.id, Security.is_primary)
            )
        ).scalar_one()
        assert primaries == 1


# --------------------------------------------------------------------------- #
# Identifier persistence — the database is the guarantee
# --------------------------------------------------------------------------- #


class TestIdentifierPersistence:
    async def test_recording_the_same_lei_twice_on_one_entity_is_idempotent(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        first = await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=entity.id,
            source="gleif",
            confidence=0.7,
        )
        second = await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A.lower(),
            cfg=cfg,
            legal_entity_id=entity.id,
            source="gleif",
            confidence=1.0,
        )
        assert first is not None and second is not None
        assert first.id == second.id
        assert second.confidence == 1.0, "a better source may raise confidence"
        count = (
            await session.execute(select(func.count()).select_from(EntityIdentifier))
        ).scalar_one()
        assert count == 1

    async def test_two_entities_cannot_both_currently_hold_one_lei(
        self, session
    ) -> None:
        cfg = _cfg()
        first = await _entity(session, cfg, key="cik:0000000001", name="First A/S")
        second = await _entity(session, cfg, key="cik:0000000002", name="Second A/S")
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=first.id,
            source="gleif",
        )
        await session.commit()

        # The service does NOT resolve this. Which entity is right is an evidence
        # question, and answering it by overwriting is the merge the schema exists
        # to prevent — so the write reaches the index and the index refuses it.
        # The error surfaces on the service's own flush rather than at commit,
        # which is the right moment: the caller learns before it builds anything
        # else on top of a claim the database was never going to accept.
        with pytest.raises(IntegrityError):
            await record_identifier(
                session,
                scheme=SCHEME_LEI,
                value=LEI_A,
                cfg=cfg,
                legal_entity_id=second.id,
                source="some_aggregator",
            )
        await session.rollback()

    async def test_a_superseded_identifier_frees_the_value(self, session) -> None:
        cfg = _cfg()
        first = await _entity(session, cfg, key="cik:0000000001", name="First A/S")
        second = await _entity(session, cfg, key="cik:0000000002", name="Second A/S")
        old = await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=first.id,
            source="gleif",
        )
        assert old is not None
        old.effective_to = date(2026, 1, 31)
        await session.flush()
        new = await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=second.id,
            source="gleif",
            effective_from=date(2026, 2, 1),
        )
        await session.commit()
        assert new is not None and new.id != old.id
        # Both rows survive: the history of who held it is not overwritten.
        rows = (
            (await session.execute(select(EntityIdentifier))).scalars().all()
        )
        assert len(rows) == 2

    async def test_an_isin_cannot_be_attached_to_a_legal_entity(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        with pytest.raises(ValueError, match="needs a security_id"):
            await record_identifier(
                session,
                scheme=SCHEME_ISIN,
                value=ISIN_A,
                cfg=cfg,
                legal_entity_id=entity.id,
                source="issuer_report",
            )

    async def test_a_lei_cannot_be_attached_to_a_security(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        security = await upsert_security(
            session, legal_entity_id=entity.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert security is not None
        with pytest.raises(ValueError, match="needs a legal_entity_id"):
            await record_identifier(
                session,
                scheme=SCHEME_LEI,
                value=LEI_A,
                cfg=cfg,
                security_id=security.id,
                source="gleif",
            )

    async def test_an_identifier_with_no_source_is_refused(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        with pytest.raises(ValueError, match="needs a source"):
            await record_identifier(
                session,
                scheme=SCHEME_LEI,
                value=LEI_A,
                cfg=cfg,
                legal_entity_id=entity.id,
                source="   ",
            )

    async def test_confidence_outside_zero_to_one_is_refused(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        with pytest.raises(ValueError, match="confidence"):
            await record_identifier(
                session,
                scheme=SCHEME_LEI,
                value=LEI_A,
                cfg=cfg,
                legal_entity_id=entity.id,
                source="gleif",
                confidence=1.4,
            )

    async def test_the_schema_refuses_an_identifier_with_no_subject(
        self, session
    ) -> None:
        session.add(
            EntityIdentifier(
                id=uuid.uuid4(),
                scheme=SCHEME_LEI,
                value=LEI_A,
                value_normalized=LEI_A,
                scope_key=GLOBAL_SCOPE,
                source="gleif",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_an_entity_is_findable_by_its_lei_and_by_its_securitys_isin(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        security = await upsert_security(
            session, legal_entity_id=entity.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert security is not None
        await record_identifier(
            session,
            scheme=SCHEME_LEI,
            value=LEI_A,
            cfg=cfg,
            legal_entity_id=entity.id,
            source="gleif",
        )
        await record_identifier(
            session,
            scheme=SCHEME_ISIN,
            value=ISIN_A,
            cfg=cfg,
            security_id=security.id,
            source="issuer_report",
        )
        await session.commit()

        by_lei = await find_entity_by_identifier(
            session, scheme=SCHEME_LEI, value=LEI_A, cfg=cfg
        )
        by_isin = await find_entity_by_identifier(
            session, scheme=SCHEME_ISIN, value=ISIN_A, cfg=cfg
        )
        assert by_lei is not None and by_isin is not None
        assert by_lei.id == by_isin.id == entity.id

    async def test_an_unheld_identifier_returns_none_rather_than_a_near_match(
        self, session
    ) -> None:
        cfg = _cfg()
        assert (
            await find_entity_by_identifier(
                session, scheme=SCHEME_LEI, value=LEI_B, cfg=cfg
            )
            is None
        )


# --------------------------------------------------------------------------- #
# Ticker changes and history
# --------------------------------------------------------------------------- #


class TestTickerChange:
    async def test_a_ticker_change_closes_a_window_and_opens_another(
        self, session
    ) -> None:
        cfg = _cfg()
        entity, security, old = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="An Issuer A/S",
            ticker="OLD",
            exchange="CO",
        )
        assert security is not None and old is not None
        await close_listing(
            session,
            listing=old,
            effective_to=date(2026, 6, 30),
            listing_status=LISTING_DELISTED,
            cfg=cfg,
        )
        new = await upsert_listing(
            session,
            security_id=security.id,
            ticker="NEW",
            exchange="CO",
            cfg=cfg,
            listing_status=LISTING_ACTIVE,
            effective_from=date(2026, 7, 1),
            is_primary=True,
        )
        assert new is not None
        await record_alias(
            session,
            legal_entity_id=entity.id,
            alias="OLD",
            alias_type=ALIAS_FORMER_TICKER,
            cfg=cfg,
            effective_to=date(2026, 6, 30),
            source="issuer_announcement",
        )
        await session.commit()

        # Today resolves to the new symbol only.
        assert (
            await resolve_entity_by_listing(session, ticker="OLD", exchange="CO", cfg=cfg)
        ) is None
        current = await resolve_entity_by_listing(
            session, ticker="NEW", exchange="CO", cfg=cfg
        )
        assert current is not None and current.id == entity.id

        # And history still resolves — the row was closed, not rewritten.
        historical = await resolve_entity_by_listing(
            session,
            ticker="OLD",
            exchange="CO",
            cfg=cfg,
            on_date=date(2026, 3, 1),
        )
        assert historical is not None and historical.id == entity.id

    async def test_a_symbol_may_be_reused_after_the_previous_window_closed(
        self, session
    ) -> None:
        cfg = _cfg()
        _, first, old = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="First A/S",
            ticker="REU",
            exchange="CO",
        )
        assert old is not None and first is not None
        await close_listing(
            session, listing=old, effective_to=date(2026, 6, 30), cfg=cfg
        )
        other = await _entity(session, cfg, key="lei:" + LEI_B, name="Second A/S")
        second = await upsert_security(
            session, legal_entity_id=other.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert second is not None
        reused = await upsert_listing(
            session,
            security_id=second.id,
            ticker="REU",
            exchange="CO",
            cfg=cfg,
            effective_from=date(2026, 7, 1),
        )
        await session.commit()
        assert reused is not None
        now = await resolve_entity_by_listing(
            session, ticker="REU", exchange="CO", cfg=cfg
        )
        assert now is not None and now.id == other.id

    async def test_an_ambiguous_historical_window_is_not_an_answer(
        self, session
    ) -> None:
        # Two closed windows overlapping the queried date is the only way one
        # (venue, ticker) can match twice. Returning either would be a coin flip
        # presented as an answer; slice 2.3 turns this into an `ambiguous` gap.
        cfg = _cfg()
        _, first, old = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="First A/S",
            ticker="AMB",
            exchange="CO",
        )
        assert first is not None and old is not None
        old.effective_from = date(2026, 1, 1)
        old.effective_to = date(2026, 12, 31)
        await session.flush()
        other = await _entity(session, cfg, key="lei:" + LEI_B, name="Second A/S")
        second = await upsert_security(
            session, legal_entity_id=other.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert second is not None
        session.add(
            SecurityListing(
                id=uuid.uuid4(),
                security_id=second.id,
                venue_key=venue_key_for("CO"),
                exchange_code="CO",
                ticker="AMB",
                listing_status=LISTING_ACTIVE,
                effective_from=date(2026, 6, 1),
                effective_to=date(2026, 12, 31),
            )
        )
        await session.commit()

        assert (
            await resolve_entity_by_listing(
                session,
                ticker="AMB",
                exchange="CO",
                cfg=cfg,
                on_date=date(2026, 7, 1),
            )
        ) is None


# --------------------------------------------------------------------------- #
# Aliases — evidence, not identity
# --------------------------------------------------------------------------- #


class TestAliases:
    async def test_a_former_legal_name_is_recorded_and_deduplicated(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Pandora A/S")
        first = await record_alias(
            session,
            legal_entity_id=entity.id,
            alias="Pandora Jewelry A/S",
            alias_type=ALIAS_FORMER_LEGAL_NAME,
            cfg=cfg,
            source="issuer_report",
        )
        again = await record_alias(
            session,
            legal_entity_id=entity.id,
            alias="  pandora   jewelry a/s  ",
            alias_type=ALIAS_FORMER_LEGAL_NAME,
            cfg=cfg,
            source="gleif",
        )
        assert first is not None and again is not None and first.id == again.id
        count = (
            await session.execute(select(func.count()).select_from(EntityAlias))
        ).scalar_one()
        assert count == 1

    async def test_an_alias_alone_never_resolves_an_entity(self, session) -> None:
        # Name matching is how two companies get merged wrongly. There is
        # deliberately no resolve-by-name function in this slice, and this test
        # fails if one appears.
        assert not [
            name
            for name in dir(master_mod)
            if "name" in name and name.startswith(("resolve", "find"))
        ], (
            "Resolving an entity from a name is slice 2.3's job, with evidence "
            "beyond the name itself."
        )

    async def test_a_rename_keeps_the_key_and_updates_the_current_name(
        self, session
    ) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Old Name A/S")
        again = await upsert_legal_entity(
            session,
            EntityInput(
                entity_key="lei:" + LEI_A,
                legal_name="New Name A/S",
                jurisdiction="DK",
            ),
            cfg=cfg,
        )
        await session.commit()
        assert again is not None and again.id == entity.id
        assert again.legal_name == "New Name A/S"
        assert again.normalized_name == "new name a/s"
        assert again.jurisdiction == "DK"


# --------------------------------------------------------------------------- #
# Entity upsert semantics
# --------------------------------------------------------------------------- #


class TestEntityUpsert:
    async def test_a_second_source_fills_gaps_and_never_erases(self, session) -> None:
        cfg = _cfg()
        await upsert_legal_entity(
            session,
            EntityInput(
                entity_key="lei:" + LEI_A,
                legal_name="An Issuer A/S",
                jurisdiction="DK",
                legal_form="A/S",
                entity_status=ENTITY_ACTIVE,
                status_source="gleif",
            ),
            cfg=cfg,
        )
        merged = await upsert_legal_entity(
            session,
            EntityInput(entity_key="lei:" + LEI_A, legal_name="An Issuer A/S"),
            cfg=cfg,
        )
        await session.commit()
        assert merged is not None
        assert merged.jurisdiction == "DK", "a source that knows less must not erase"
        assert merged.legal_form == "A/S"
        assert merged.entity_status == ENTITY_ACTIVE
        assert merged.status_source == "gleif"

    async def test_status_defaults_to_unknown_rather_than_active(self, session) -> None:
        # A dissolved entity's last annual report is still fetchable, so finding a
        # document must never be read as evidence that the entity is active.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        assert entity.entity_status == "unknown"
        assert entity.status_source is None

    async def test_two_entities_may_share_a_name(self, session) -> None:
        # Deliberate: two companies may legally share a name in two jurisdictions,
        # and a unique name would force exactly the merge this schema prevents.
        cfg = _cfg()
        a = await _entity(
            session, cfg, key="lei:" + LEI_A, name="Acme Holdings", jurisdiction="DK"
        )
        b = await _entity(
            session, cfg, key="lei:" + LEI_B, name="Acme Holdings", jurisdiction="GB"
        )
        await session.commit()
        assert a.id != b.id
        assert a.normalized_name == b.normalized_name

    async def test_an_entity_with_no_key_or_no_name_is_refused(self, session) -> None:
        cfg = _cfg()
        with pytest.raises(ValueError, match="entity_key"):
            await upsert_legal_entity(
                session, EntityInput(entity_key="", legal_name="X"), cfg=cfg
            )
        with pytest.raises(ValueError, match="legal_name"):
            await upsert_legal_entity(
                session, EntityInput(entity_key="k", legal_name="  "), cfg=cfg
            )


# --------------------------------------------------------------------------- #
# The flag, and V2 compatibility
# --------------------------------------------------------------------------- #


class TestReviewFindings:
    """Four defects the build pass missed and the review pass found."""

    async def test_a_london_listing_records_pence_not_pounds(self, session) -> None:
        # ExchangeInfo.currency for LSE is GBP; LSE main-market equities are QUOTED
        # in pence. Defaulting the listing from the venue's general currency would
        # mislabel every London price as 100x its real pound value, which is the
        # exact mistake ``_PRICE_QUOTE_CURRENCY_OVERRIDES`` already exists to stop.
        cfg = _cfg()
        _, _, listing = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="A London Issuer plc",
            ticker="LON",
            exchange="LSE",
        )
        await session.commit()
        assert listing is not None
        assert listing.quote_currency == "GBX"
        assert listing.quote_currency == price_quote_currency_for_exchange("LSE")

    async def test_a_venue_with_no_override_keeps_its_own_currency(
        self, session
    ) -> None:
        cfg = _cfg()
        _, _, listing = await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="A Copenhagen Issuer A/S",
            ticker="CPH",
            exchange="CO",
        )
        await session.commit()
        assert listing is not None
        assert listing.quote_currency == currency_for_exchange("CO")

    async def test_an_adr_is_never_silently_downgraded_to_an_ordinary_share(
        self, session
    ) -> None:
        # A caller relying on a default would otherwise overwrite the type, and the
        # whole reason the type is named is that per-share arithmetic must refuse to
        # run on a depositary receipt without a ratio.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer S.A.")
        adr = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key="adr:1",
            cfg=cfg,
            security_type=SECURITY_ADR,
        )
        assert adr is not None
        # No assertion of a type: leave what is there.
        again = await upsert_security(
            session, legal_entity_id=entity.id, security_key="adr:1", cfg=cfg
        )
        assert again is not None and again.security_type == SECURITY_ADR
        # An explicit CONTRADICTION raises rather than overwriting.
        with pytest.raises(ValueError, match="cannot be re-asserted"):
            await upsert_security(
                session,
                legal_entity_id=entity.id,
                security_key="adr:1",
                cfg=cfg,
                security_type=SECURITY_ORDINARY_SHARE,
            )

    async def test_a_delisting_cannot_be_recorded_on_an_open_window(
        self, session
    ) -> None:
        # Such a row would resolve as the CURRENT listing of its ticker while
        # claiming to be delisted.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="An Issuer A/S")
        security = await upsert_security(
            session, legal_entity_id=entity.id, security_key="ordinary_share:1", cfg=cfg
        )
        assert security is not None
        with pytest.raises(ValueError, match="close_listing"):
            await upsert_listing(
                session,
                security_id=security.id,
                ticker="GONE",
                exchange="CO",
                cfg=cfg,
                listing_status=LISTING_DELISTED,
            )

    async def test_a_rename_leaves_the_old_name_findable_via_an_alias(
        self, session
    ) -> None:
        # The rename write adopts the new name; it is the ALIAS that keeps the old
        # one searchable. The comment above that code once said the opposite.
        cfg = _cfg()
        entity = await _entity(session, cfg, key="lei:" + LEI_A, name="Old Name A/S")
        await record_alias(
            session,
            legal_entity_id=entity.id,
            alias="Old Name A/S",
            alias_type=ALIAS_FORMER_LEGAL_NAME,
            cfg=cfg,
            effective_to=date(2026, 6, 30),
            source="issuer_announcement",
        )
        await upsert_legal_entity(
            session,
            EntityInput(entity_key="lei:" + LEI_A, legal_name="New Name A/S"),
            cfg=cfg,
        )
        await session.commit()
        aliases = (
            (
                await session.execute(
                    select(EntityAlias).where(
                        EntityAlias.legal_entity_id == entity.id,
                        EntityAlias.alias_type == ALIAS_FORMER_LEGAL_NAME,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [row.alias for row in aliases] == ["Old Name A/S"]
        assert entity.legal_name == "New Name A/S"


class TestDisabledByDefault:
    def test_the_flag_defaults_to_off(self) -> None:
        assert Settings().v3_entity_master_enabled is False

    async def test_with_the_flag_off_nothing_is_written(self, session) -> None:
        off = _cfg(v3_entity_master_enabled=False)
        assert (
            await upsert_legal_entity(
                session, EntityInput(entity_key="k", legal_name="X"), cfg=off
            )
            is None
        )
        assert (
            await record_identifier(
                session,
                scheme=SCHEME_LEI,
                value=LEI_A,
                cfg=off,
                legal_entity_id=uuid.uuid4(),
                source="gleif",
            )
            is None
        )
        assert (
            await upsert_security(
                session, legal_entity_id=uuid.uuid4(), security_key="s", cfg=off
            )
            is None
        )
        assert (
            await upsert_listing(
                session,
                security_id=uuid.uuid4(),
                ticker="X",
                exchange="CO",
                cfg=off,
            )
            is None
        )
        assert (
            await record_alias(
                session,
                legal_entity_id=uuid.uuid4(),
                alias="X",
                alias_type=ALIAS_FORMER_LEGAL_NAME,
                cfg=off,
            )
            is None
        )
        assert (
            await resolve_entity_by_listing(
                session, ticker="X", exchange="CO", cfg=off
            )
            is None
        )
        assert (
            await find_entity_by_identifier(
                session, scheme=SCHEME_LEI, value=LEI_A, cfg=off
            )
            is None
        )
        await session.commit()

        for model in (LegalEntity, Security, SecurityListing, EntityIdentifier,
                      EntityAlias):
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            assert count == 0, f"{model.__tablename__} was written with the flag off"

    async def test_the_entity_master_does_not_touch_companies(self, session) -> None:
        cfg = _cfg()
        session.add(
            Company(
                id=uuid.uuid4(),
                ticker="PNDORA",
                exchange="CO",
                name="Pandora A/S",
                status="new",
            )
        )
        await session.commit()
        before = (
            await session.execute(select(func.count()).select_from(Company))
        ).scalar_one()

        await _listed(
            session,
            cfg,
            entity_key="lei:" + LEI_A,
            name="Pandora A/S",
            ticker="PNDORA",
            exchange="CO",
        )
        await session.commit()

        after = (
            await session.execute(select(func.count()).select_from(Company))
        ).scalar_one()
        assert before == after == 1
        # And no FK from the entity master points at companies in this slice —
        # companies.legal_entity_id is slice 2.2, in one place, with the backfill.
        for model in (LegalEntity, Security, SecurityListing, EntityIdentifier,
                      EntityAlias):
            targets = {
                fk.column.table.name
                for column in model.__table__.columns
                for fk in column.foreign_keys
            }
            assert "companies" not in targets


class TestNoNetworkInThePackage:
    def test_no_module_in_the_package_imports_a_network_client(self) -> None:
        # Identifier SOURCES (GLEIF, SEC, OpenFIGI) are a network path and belong
        # with the resolution slice, which can record what a lookup returned and
        # what it did not. This test is what keeps them out of here.
        forbidden = {"httpx", "requests", "aiohttp", "urllib", "urllib3", "socket"}
        package = Path(ident_mod.__file__).parent
        offenders: list[str] = []
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    if name in forbidden:
                        offenders.append(f"{path.name}: {name}")
        assert offenders == [], f"network imports in the entity package: {offenders}"
