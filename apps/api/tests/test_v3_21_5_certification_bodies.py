"""V3.21 slice 21.5 — certification bodies, verification states, qualification evidence."""

from __future__ import annotations

import hashlib
import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.certification import bodies, verification
from app.services.certification.bodies import (
    BUNDLED_STL_MEMBERS,
    InMemoryMemberListStore,
    StlLayoutChanged,
    StlMember,
    StlMemberList,
    build_registry,
    decode_page,
    ensure_stl_members_fresh,
    match_body,
    named_body_text,
    parse_stl_members,
    refresh_stl_members,
)
from app.services.certification.verification import (
    MAX_WEB_SEARCHES_PER_RUN,
    STATE_BODY_UNRECOGNISED,
    STATE_CORROBORATED,
    STATE_ISSUER_CLAIM,
    STATE_UNVERIFIED,
    HeldDocument,
    Subject,
    WebCheck,
    certification_step,
    classify_document,
    qualification_evidence_block,
    subject_names,
)
from app.services.electricity.real_asset import real_asset_block
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base  # noqa: F401  (JSONB on SQLite)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "stl_members.html"
NOW = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
COMPANY_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER_ID = uuid.UUID("99999999-2222-3333-4444-555555555555")
CLASSIFIED = {"status": "classified", "primary_layer": "transmission"}


def _cfg(**over: object) -> SimpleNamespace:
    base = {"v3_certification_bodies_enabled": True, "v3_web_search_enabled": False}
    return SimpleNamespace(**{**base, **over})


def _company() -> SimpleNamespace:
    return SimpleNamespace(id=COMPANY_ID, name="Powell Industries, Inc.", ticker="POWL",
                           website="https://www.powellind.com")


def _finding(statement: str, evidence: list[str]) -> dict[str, Any]:
    return {"finding_id": str(uuid.uuid4()), "statement": statement,
            "evidence_ids": evidence, "verification_status": "verified",
            "superseded_by": None, "source_published_at": "2026-03-01"}


def _real_asset(*statements: tuple[str, list[str]]) -> dict[str, Any]:
    block = real_asset_block([_finding(s, e) for s, e in statements], CLASSIFIED)
    assert block is not None
    return block


def _doc(text: str, *, tier: str = "T1_primary_filing", cls: str | None = None,
         company: uuid.UUID | None = COMPANY_ID, url: str = "https://www.sec.gov/x",
         eid: str = "ev:c:1") -> HeldDocument:
    return HeldDocument(eid, text, str(company) if company else None, tier, cls, url,
                        "2026-02-01", "2026-03-01T00:00:00+00:00")


class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    def begin_nested(self) -> _Nested:
        return _Nested()


def _patch_docs(monkeypatch: pytest.MonkeyPatch, evidence: dict[str, HeldDocument],
                candidates: list[HeldDocument] | None = None) -> None:
    async def _load(session: Any, ids: list[str]) -> dict[str, HeldDocument]:
        return {i: evidence[i] for i in ids if i in evidence}

    async def _find(session: Any, body: Any, item: Any, subject: Any) -> list[HeldDocument]:
        return list(candidates or [])

    monkeypatch.setattr(verification, "load_evidence_documents", _load)
    monkeypatch.setattr(verification, "find_candidate_documents", _find)


# ── STL members parser (real fixture) ────────────────────────────────────── #


def test_parser_reads_every_member_and_country_from_the_live_page() -> None:
    parsed = parse_stl_members(FIXTURE.read_text(), retrieved_at=NOW.isoformat())
    assert [(m.name, m.country) for m in parsed.members] == [
        ("CESI", "Italy"), ("CPRI", "India"), ("ESEF ASEFA", "France"), ("JSTC", "Japan"),
        ("KEMA", "The Netherlands"), ("KERI", "South Korea"), ("PEHLA", "Germany"),
        ("SATS", "Norway"), ("STLNA", "USA"), ("VEIKI", "Hungary"),
        ("ZKU", "Czech Republic"),
    ]
    assert parsed.members[2].code == "ESEF"
    assert (parsed.directory_issue, parsed.directory_date) == ("59", "2026-06-01")
    assert parsed.origin == "live"
    # The bundled snapshot is exactly what the live page said.
    assert parsed.members == BUNDLED_STL_MEMBERS.members
    assert parsed.summary()["source"] == ("Short-Circuit Testing Liaison (STL), "
                                          "stl-liaison.org")


def test_windows_1252_bytes_are_decoded() -> None:
    assert decode_page("Prüffeld".encode("cp1252")) == "Prüffeld"
    assert decode_page("Prüffeld".encode("utf-8")) == "Prüffeld"


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("Members of STL are", "Our partners"),          # marker gone
    lambda h: h.replace("CESI, Italy", "CESI Italy"),                   # cell shape
    lambda h: h.replace('value = "CESI"', 'value = "XXXX"'),            # codes disagree
    lambda h: h.split("Members of STL are")[0] + "Members of STL are: none",  # emptied
])
def test_a_changed_layout_fails_closed(mutate: Any) -> None:
    with pytest.raises(StlLayoutChanged):
        parse_stl_members(mutate(FIXTURE.read_text()), retrieved_at="x")


async def test_refresh_keeps_the_last_good_list_on_layout_change_or_fetch_failure() -> None:
    store = InMemoryMemberListStore()
    good = FIXTURE.read_bytes()

    async def ok() -> bytes:
        return good

    async def changed() -> bytes:
        return b"<html><body>Site under maintenance</body></html>"

    async def failed() -> None:
        return None

    first = await refresh_stl_members(store=store, fetch=ok, now=NOW)
    assert first == {"state": "refreshed", "members_count": 11, "origin": "live"}
    kept = store.load()
    out = await refresh_stl_members(store=store, fetch=changed, now=NOW)
    assert out["state"] == "layout_changed" and store.load() is kept
    out = await refresh_stl_members(store=store, fetch=failed, now=NOW)
    assert out["state"] == "fetch_failed" and store.load() is kept
    # No good list ever: the bundled snapshot stands, never an empty register.
    empty = InMemoryMemberListStore()
    out = await refresh_stl_members(store=empty, fetch=changed, now=NOW)
    assert out == {"state": "layout_changed", "members_count": 11,
                   "origin": "bundled_snapshot"}
    assert bodies.current_stl_members(empty) is BUNDLED_STL_MEMBERS


async def test_refresh_is_monthly_and_retries_a_failure_after_a_day() -> None:
    store = InMemoryMemberListStore()
    calls: list[int] = []

    async def ok() -> bytes:
        calls.append(1)
        return FIXTURE.read_bytes()

    async def failed() -> None:
        calls.append(0)
        return None

    assert (await ensure_stl_members_fresh(store=store, fetch=ok, now=NOW))["state"] == \
        "refreshed"
    later = NOW + timedelta(days=29)
    assert (await ensure_stl_members_fresh(store=store, fetch=ok, now=later))["state"] == \
        "not_due"
    assert (await ensure_stl_members_fresh(store=store, fetch=failed,
                                           now=NOW + timedelta(days=31)))["state"] == \
        "fetch_failed"
    soon = NOW + timedelta(days=31, hours=12)
    assert (await ensure_stl_members_fresh(store=store, fetch=ok, now=soon))["state"] == \
        "not_due"
    assert (await ensure_stl_members_fresh(store=store, fetch=ok,
                                           now=NOW + timedelta(days=32, hours=1)))[
        "state"] == "refreshed"
    assert calls == [1, 0, 1]


# ── Registry and aliases ─────────────────────────────────────────────────── #


def test_registry_joins_curated_labs_and_stl_members() -> None:
    reg = {b.key: b for b in build_registry(BUNDLED_STL_MEMBERS)}
    for key in ("kema_labs", "cesi", "iph_berlin", "keri", "tubitak_mam", "cpri", "dnv",
                "tuv_sud", "tuv_rheinland", "ul_solutions", "intertek", "csa_group",
                "bureau_veritas", "sgs"):
        assert key in reg and reg[key].hosts and reg[key].country and reg[key].scope, key
    assert reg["kema_labs"].stl_member and reg["cesi"].stl_member and reg["cpri"].stl_member
    assert reg["iph_berlin"].stl_member and reg["iph_berlin"].stl_via == "PEHLA"
    assert not reg["dnv"].stl_member and not reg["ul_solutions"].stl_member
    # Members with no curated entry still count, with no host to search.
    assert reg["stl:pehla"].stl_member and reg["stl:pehla"].hosts == ()
    assert {"stl:jstc", "stl:esef", "stl:stlna", "stl:veiki", "stl:zku", "stl:sats"} <= set(reg)
    # Membership follows the list: a list without KEMA drops KEMA's membership.
    without = StlMemberList(tuple(m for m in BUNDLED_STL_MEMBERS.members if m.code != "KEMA"),
                            origin="live", retrieved_at="x")
    assert not {b.key: b for b in build_registry(without)}["kema_labs"].stl_member


@pytest.mark.parametrize(("text", "key"), [
    ("passed the KEMA short-circuit test", "kema_labs"),
    ("a DNV KEMA type-test certificate", "kema_labs"),
    ("tested at KEMA Labs in Arnhem", "kema_labs"),
    ("certified by TÜV SÜD", "tuv_sud"),
    ("certified by TUV Rheinland", "tuv_rheinland"),
    ("a TÜV certificate", "tuv"),
    ("listed by UL Solutions", "ul_solutions"),
    ("certified by DNV GL", "dnv"),
    ("tested at the Central Power Research Institute", "cpri"),
    ("type tested by TÜBİTAK MAM", "tubitak_mam"),
    ("short-circuit tested at IPH Berlin", "iph_berlin"),
    ("tested at PEHLA", "stl:pehla"),
    ("tested by CSA Group", "csa_group"),
])
def test_body_aliases(text: str, key: str) -> None:
    body = match_body(text, build_registry(BUNDLED_STL_MEMBERS))
    assert body is not None and body.key == key


def test_aliases_do_not_match_standards_or_ordinary_words() -> None:
    reg = build_registry(BUNDLED_STL_MEMBERS)
    for text in ("Inverters are UL 1741 compliant.", "UL1741 SB listed",
                 "The ultimate test.", "sgsn module", "Revenue rose 12%."):
        assert match_body(text, reg) is None, text
    assert named_body_text("type-tested by Acme Testing Labs of Ohio") == \
        "Acme Testing Labs of Ohio"
    assert named_body_text("certified by independent laboratories") is None


def test_subject_names_drop_the_legal_suffix() -> None:
    assert subject_names("Powell Industries, Inc.", "POWL") == (
        "Powell Industries, Inc.", "Powell Industries", "POWL")


# ── Document classification ──────────────────────────────────────────────── #


def test_who_is_speaking_in_a_document() -> None:
    subject = Subject(str(COMPANY_ID), ("Powell Industries",), ("powellind.com",))
    kema = {b.key: b for b in build_registry(BUNDLED_STL_MEMBERS)}["kema_labs"]

    def kind(**kw: Any) -> str:
        return classify_document(_doc("x", **kw), subject=subject, body=kema)

    assert kind() == "issuer"                                     # own 10-K
    assert kind(company=OTHER_ID) == "third_party"                # a customer's filing
    assert kind(tier="T2_regulator_or_gov", company=None) == "third_party"
    assert kind(tier="T3_industry_specialist", cls="company_press_release") == "issuer"
    assert kind(tier="T4_quality_media") == "ineligible"
    assert kind(tier="T5_api_aggregator", url="https://www.kema.com/c/1") == "body_host"
    assert kind(tier="T3_industry_specialist",
                url="https://investors.powellind.com/a") == "issuer"


# ── Verification states ──────────────────────────────────────────────────── #

STATEMENT = ("The 420 kV switchgear passed the KEMA short-circuit test (IEC 62271-200) "
             "in March 2025.")


async def test_issuer_claim_when_only_the_company_says_so(monkeypatch) -> None:
    _patch_docs(monkeypatch, {"ev:c:own": _doc(STATEMENT + " Powell Industries",
                                               eid="ev:c:own")})
    ra = _real_asset((STATEMENT, ["ev:c:own"]))
    annotated, block = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    assert annotated is not None and block is not None
    [item] = annotated["certifications"]["items"]
    v = item["verification"]
    assert v["state"] == STATE_ISSUER_CLAIM
    assert v["body"]["key"] == "kema_labs" and v["body"]["stl_member"] is True
    assert block["states"][STATE_ISSUER_CLAIM] == 1 and block["any_stl_member"] is True
    assert block["share_third_party_corroborated"] == 0.0
    assert block["web_search"]["state"] == "disabled"
    assert any("Web search is off" in n for n in block["not_verifiable"])


async def test_corroborated_by_another_partys_document(monkeypatch) -> None:
    third = _doc("Utility X accepted Powell Industries switchgear after KEMA tests to "
                 "IEC 62271-200.", tier="T2_regulator_or_gov", company=None, eid="ev:c:reg")
    _patch_docs(monkeypatch, {"ev:c:own": _doc(STATEMENT, eid="ev:c:own")}, [third])
    ra = _real_asset((STATEMENT, ["ev:c:own"]))
    annotated, block = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    v = annotated["certifications"]["items"][0]["verification"]  # type: ignore[index]
    assert v["state"] == STATE_CORROBORATED
    assert v["corroboration"][0]["kind"] == "held_document"
    assert v["corroboration"][0]["source_tier"] == "T2_regulator_or_gov"
    assert block["share_third_party_corroborated"] == 1.0  # type: ignore[index]


async def test_corroborated_on_the_bodys_own_host(monkeypatch) -> None:
    page = _doc("Certificate: Powell Industries, switchgear, IEC 62271-200, KEMA Labs.",
                tier="T5_api_aggregator", company=None, url="https://www.kema.com/cert/9",
                eid="ev:c:kema")
    _patch_docs(monkeypatch, {"ev:c:kema": page})
    ra = _real_asset((STATEMENT, ["ev:c:kema"]))
    annotated, _ = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    v = annotated["certifications"]["items"][0]["verification"]  # type: ignore[index]
    assert v["state"] == STATE_CORROBORATED
    assert v["corroboration"][0]["kind"] == "finding_evidence"


async def test_a_wrong_standard_or_a_media_article_does_not_corroborate(monkeypatch) -> None:
    other_std = _doc("Powell Industries passed KEMA tests to IEC 60076-5.",
                     tier="T2_regulator_or_gov", company=None, eid="ev:c:a")
    media = _doc("Powell Industries passed KEMA tests to IEC 62271-200.",
                 tier="T4_quality_media", company=None, eid="ev:c:b")
    _patch_docs(monkeypatch, {"ev:c:own": _doc(STATEMENT, eid="ev:c:own")},
                [other_std, media])
    ra = _real_asset((STATEMENT, ["ev:c:own"]))
    annotated, _ = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    assert annotated["certifications"]["items"][0]["verification"]["state"] == \
        STATE_ISSUER_CLAIM  # type: ignore[index]


async def test_body_unrecognised_and_unverified(monkeypatch) -> None:
    _patch_docs(monkeypatch, {})
    ra = _real_asset(
        ("The breaker was type-tested by Acme Testing Labs to IEC 62271-100 in 2024.",
         ["ev:c:1"]),
        ("The company is an approved vendor to several utilities.", ["ev:c:2"]),
        (STATEMENT, ["ev:c:missing"]),
    )
    annotated, block = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    states = [i["verification"]["state"]
              for i in annotated["certifications"]["items"]]  # type: ignore[index]
    assert states == [STATE_BODY_UNRECOGNISED, STATE_UNVERIFIED, STATE_UNVERIFIED]
    items = annotated["certifications"]["items"]  # type: ignore[index]
    assert items[0]["verification"]["body_named"] == "Acme Testing Labs"
    assert "resolved" in items[2]["verification"]["basis"]
    assert block["states"] == {STATE_CORROBORATED: 0, STATE_ISSUER_CLAIM: 0,  # type: ignore[index]
                               STATE_BODY_UNRECOGNISED: 1, STATE_UNVERIFIED: 2}
    assert block["any_stl_member"] is True  # type: ignore[index]  (KEMA, though unverified)


# ── Web search: bounded, and skipped when off ────────────────────────────── #


def _many_certificates(n: int) -> dict[str, Any]:
    return _real_asset(*[
        (f"The unit passed the KEMA short-circuit test (IEC 6227{i}-1) in 2025.",
         [f"ev:c:{i}"]) for i in range(n)])


async def test_web_search_is_bounded_per_run(monkeypatch) -> None:
    _patch_docs(monkeypatch, {f"ev:c:{i}": _doc("own", eid=f"ev:c:{i}") for i in range(8)})
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def checker(body: Any, item: Any, subject: Any) -> WebCheck:
        calls.append((body.key, body.hosts))
        return WebCheck(searched=True, corroborated=len(calls) == 1,
                        url="https://www.kema.com/c/1", retrieved_at=NOW.isoformat())

    annotated, block = await certification_step(
        _FakeSession(), _company(), _many_certificates(8),
        cfg=_cfg(v3_web_search_enabled=True), web_checker=checker)
    assert len(calls) == MAX_WEB_SEARCHES_PER_RUN == 5
    assert all(hosts == ("kema.com",) for _, hosts in calls)
    assert block["web_search"] == {"state": "ran", "searches": 5,  # type: ignore[index]
                                   "max_searches": 5}
    states = [i["verification"]["state"]
              for i in annotated["certifications"]["items"]]  # type: ignore[index]
    assert states.count(STATE_CORROBORATED) == 1
    assert states.count(STATE_ISSUER_CLAIM) == 7


async def test_web_search_is_skipped_when_web_search_is_off(monkeypatch) -> None:
    _patch_docs(monkeypatch, {"ev:c:0": _doc("own", eid="ev:c:0")})
    from app.services.web_research import search as search_module

    async def never(*_a: object, **_k: object) -> None:
        raise AssertionError("no search when web search is off")

    async def checker(*_a: object) -> WebCheck:
        raise AssertionError("no checker when web search is off")

    monkeypatch.setattr(search_module, "run_searches", never)
    _, block = await certification_step(_FakeSession(), _company(), _many_certificates(1),
                                        cfg=_cfg(), web_checker=checker)
    assert block["web_search"]["state"] == "disabled"  # type: ignore[index]
    assert block["web_search"]["searches"] == 0  # type: ignore[index]


async def test_the_live_checker_searches_only_the_bodys_host_and_reads_the_page(
        monkeypatch) -> None:
    from app.services.providers.contracts import SearchResultItem
    from app.services.sources import document_fetcher
    from app.services.web_research import search as search_module

    seen: dict[str, Any] = {}

    async def fake_run(session: Any, requests: Any, context: Any, **kw: Any) -> Any:
        seen["request"] = requests[0]
        seen["stage"] = context.stage
        hit = SearchResultItem(rank=1, url="https://www.kema.com/certificates/abc",
                               canonical_url="https://www.kema.com/certificates/abc",
                               domain="kema.com", snippet="untrusted")
        outcome = SimpleNamespace(execution=SimpleNamespace(executed=True), results=[hit])
        return SimpleNamespace(outcomes=[outcome], state="ok")

    async def fake_fetch(url: str, *, allowed_domains: tuple[str, ...], **kw: Any) -> Any:
        seen["allowed"] = allowed_domains
        html = b"<html><body><p>Powell Industries IEC 62271-200 KEMA Labs</p></body></html>"
        return SimpleNamespace(error=None, blocked=False, content=html,
                               document_type="html")

    monkeypatch.setattr(search_module, "run_searches", fake_run)
    monkeypatch.setattr(document_fetcher, "safe_fetch_document", fake_fetch)
    monkeypatch.setattr("app.services.web_research.stage.persist_factory_for",
                        lambda s: None)
    _patch_docs(monkeypatch, {"ev:c:own": _doc(STATEMENT, eid="ev:c:own")})
    annotated, _ = await certification_step(
        _FakeSession(), _company(), _real_asset((STATEMENT, ["ev:c:own"])),
        cfg=_cfg(v3_web_search_enabled=True))
    v = annotated["certifications"]["items"][0]["verification"]  # type: ignore[index]
    assert v["state"] == STATE_CORROBORATED
    assert v["corroboration"][0] == {
        "kind": "body_host_page", "evidence_id": None,
        "source_url": "https://www.kema.com/certificates/abc", "source_tier": None,
        "source_class": None, "published_at": None,
        "retrieved_at": v["corroboration"][0]["retrieved_at"]}
    assert seen["request"].include_domains == ("kema.com",)
    assert seen["allowed"] == ("kema.com",)
    assert seen["stage"] == "certification"
    assert "Powell Industries" in seen["request"].query


# ── Flag, report seam, safety ────────────────────────────────────────────── #


async def test_flag_off_leaves_the_report_byte_identical(monkeypatch) -> None:
    import json

    from app.services.pipeline import professional_research as pr
    from app.services.pipeline import v3_pipeline

    _patch_docs(monkeypatch, {})
    ra = _real_asset((STATEMENT, ["ev:c:own"]))
    before = json.dumps(ra, sort_keys=True)
    out, block = await v3_pipeline._certification_section(
        _FakeSession(), _company(), cfg=_cfg(v3_certification_bodies_enabled=False),
        real_asset=ra)
    assert block is None and out is ra and json.dumps(out, sort_keys=True) == before
    assert await v3_pipeline._certification_section(
        _FakeSession(), _company(), cfg=_cfg(), real_asset=None) == (None, None)

    subject = {"ticker": "POWL", "exchange": "NASDAQ", "name": "Powell"}
    plain = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=()))
    competitive = next(s for s in plain["sections"] if s["key"] == "competitive_position")
    assert "qualification_evidence" not in competitive
    annotated, qe = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    report = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=(),
                                         real_asset=annotated, qualification_evidence=qe))
    competitive = next(s for s in report["sections"] if s["key"] == "competitive_position")
    assert competitive["qualification_evidence"]["total"] == 1
    operations = next(s for s in report["sections"] if s["key"] == "operations")
    assert operations["real_asset"]["certifications"]["items"][0]["verification"]["state"]


async def test_a_failing_read_never_costs_the_report(monkeypatch) -> None:
    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(verification, "load_evidence_documents", boom)
    ra = _real_asset((STATEMENT, ["ev:c:own"]))
    annotated, block = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    assert annotated["certifications"]["items"][0]["verification"]["state"] == \
        STATE_UNVERIFIED  # type: ignore[index]
    assert block is not None


async def test_no_recommendation_language_in_the_blocks(monkeypatch) -> None:
    _patch_docs(monkeypatch, {"ev:c:own": _doc(STATEMENT, eid="ev:c:own")})
    ra = _real_asset(
        (STATEMENT, ["ev:c:own"]),
        ("The breaker was type-tested by Acme Testing Labs to IEC 62271-100 in 2024.",
         ["ev:c:1"]),
        ("The company is an approved vendor to several utilities.", ["ev:c:2"]),
    )
    annotated, block = await certification_step(_FakeSession(), _company(), ra, cfg=_cfg())
    assert scan_value(annotated, path="ra") == []
    assert scan_value(block, path="qe") == []
    empty = qualification_evidence_block([], stl_register=BUNDLED_STL_MEMBERS.summary(),
                                         web_search={"state": "disabled"})
    assert empty["state"] == "none_found" and empty["share_third_party_corroborated"] is None
    assert scan_value(empty, path="qe") == []
    assert scan_value([b.view() for b in build_registry(BUNDLED_STL_MEMBERS)]) == []


def test_web_readers_pin_the_block_keys() -> None:
    root = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "research"
    reader = (root / "qualificationEvidence.ts").read_text()
    for key in ("state", "total", "third_party_corroborated", "share_third_party_corroborated",
                "certificates", "by_body", "any_stl_member", "stl_members_named",
                "stl_register", "web_search", "not_verifiable", "note", "certification",
                "standard", "body", "body_country", "stl_member", "date", "count",
                "directory_issue", "directory_date", "source", "retrieved_at", "origin",
                "searches"):
        assert f".{key}" in reader, key
    real_asset_reader = (root / "realAsset.ts").read_text()
    assert ".verification" in real_asset_reader and "v.state" in real_asset_reader


# ── Held documents on a real (SQLite) session ────────────────────────────── #


@pytest.fixture
async def db_session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True,
                                 poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _held(session: Any, chunk_id: str, text: str, *, tier: str,
                company: uuid.UUID | None, url: str, cls: str | None = None) -> None:
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_document import ResearchDocumentVersion

    version_id = uuid.uuid4()
    session.add(ResearchDocumentVersion(
        id=version_id, research_document_id=uuid.uuid4(),
        content_hash=hashlib.sha256(text.encode()).hexdigest(), canonical_url=url,
        transport="test", source_tier=tier, access_class="public_web",
        extraction_status="extracted", source_class=cls))
    session.add(ResearchDocumentChunk(
        id=uuid.uuid4(), chunk_id=chunk_id, derivation_id=uuid.uuid4(),
        research_document_version_id=version_id, company_id=company, kind="text",
        ordinal=0, text=text, char_start=0, char_end=len(text), indexable=True,
        source_tier=tier))
    await session.flush()


async def test_held_documents_are_read_from_the_corpus(db_session) -> None:
    await _held(db_session, "c:own", STATEMENT, tier="T1_primary_filing",
                company=COMPANY_ID, url="https://www.sec.gov/powl-10k")
    await _held(db_session, "c:reg",
                "Grid operator notice: Powell Industries switchgear, KEMA test report, "
                "IEC 62271-200.", tier="T2_regulator_or_gov", company=None,
                url="https://www.ferc.gov/notice")
    await _held(db_session, "c:noise", "KEMA tested another vendor to IEC 62271-200.",
                tier="T2_regulator_or_gov", company=None, url="https://www.ferc.gov/b")
    annotated, block = await certification_step(
        db_session, _company(), _real_asset((STATEMENT, ["ev:c:own"])), cfg=_cfg())
    v = annotated["certifications"]["items"][0]["verification"]  # type: ignore[index]
    assert v["state"] == STATE_CORROBORATED
    assert [c["evidence_id"] for c in v["corroboration"]] == ["ev:c:reg"]
    # Only the issuer's own filing: an issuer claim.
    loaded = await verification.load_evidence_documents(db_session, ["ev:c:own"])
    assert loaded["ev:c:own"].source_tier == "T1_primary_filing"
    assert block["third_party_corroborated"] == 1  # type: ignore[index]


def test_stl_member_dataclass_is_hashable_for_the_snapshot() -> None:
    assert StlMember("KEMA", "KEMA", "The Netherlands") in set(BUNDLED_STL_MEMBERS.members)
