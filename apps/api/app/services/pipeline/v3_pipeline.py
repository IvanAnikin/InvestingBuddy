"""The V3 research pipeline, end to end — V3.10 Slice 10.3.

THE PATH
========
::

    company research API -> durable job -> entity resolution -> Research Director
      -> playbook -> Research Ledger -> specialist investigations -> tools
      -> bounded gap follow-up -> Council V2 -> Red Team -> Chair
      -> persisted report -> existing report API / frontend -> Research Memory

WHAT THIS SLICE DOES **NOT** DO, AND WHY THAT IS DELIBERATE
===========================================================
It does not replace the report's narrative. The existing final-report generator still
assembles the report, so every section, the safety gate, the schema validation, the
server-side numeric verification and the frontend rendering are **untouched** — and the
brief for this phase says in as many words not to redesign the frontend.

What the V3 pipeline contributes is **persisted, citable research state** attached to that
report under ``source_summary_json["v3_research"]``: the ledger run, the findings with
their evidence ids, the gaps, the disagreements, the Red Team challenges and the Chair's
verdict.

Stating it plainly, because a reader of the RC report needs to know exactly what was
proved: **the V3 Council's verdict is recorded beside the report, not driving its prose.**
Making it drive the narrative is a further slice with its own compatibility surface, and
doing it here would have meant rewriting a 5,500-line generator during an acceptance
phase.

WHY THE V2 ASSEMBLY STILL RUNS
==============================
Three reasons, in order of weight. It is what the frontend renders; it is what the safety
gate and numeric verification are wired to; and it is what makes this reversible — with
the flag off, byte-for-byte nothing changes.

EVERY STEP DEGRADES
===================
No entity, no playbook, no model, no tools: each one narrows the run and none of them
fails it. The V3 contribution is *additive*, so a V3 failure must never cost a report the
V2 path would have produced — and a test asserts exactly that.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from app.services.agent_tools.builtin import register_builtins
from app.services.agent_tools.contracts import ToolBudget
from app.services.agent_tools.policy import policy_for
from app.services.agent_tools.registry import ToolRegistry
from app.services.agent_tools.session import ToolSession
from app.services.agents.chair import ChairVerdict, LLMChair
from app.services.agents.investigator import InvestigatorDiagnostics, LLMInvestigator
from app.services.agents.red_team import LLMRedTeam, LLMResponder
from app.services.agents.routing import (
    SLOT_CHAIR,
    SLOT_INVESTIGATOR,
    SLOT_RED_TEAM,
    ModelRouting,
    resolve_routing,
)
from app.services.classification.service import ensure_company_classification
from app.services.council_v2 import inputs as council_inputs
from app.services.council_v2.red_team import run_challenge_round
from app.services.director.loop import LoopResult, run_investigation
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger
from app.services.memory import store as memory
from app.services.memory.delta import compute_delta, persist_delta
from app.services.playbooks import select as select_playbooks
from app.services.research_mode import budget_for, limits_for, parse_mode
from app.services.sources.taxonomy import tier_rank

#: The key the V3 research state is attached under. Additive on a JSONB column the
#: report API already returns and the frontend already tolerates unknown keys in.
SOURCE_SUMMARY_KEY = "v3_research"


class _PlaybookAdapter:
    """A `Playbook` in the shape the Director's Protocol expects."""

    def __init__(self, playbook: Any) -> None:
        self._playbook = playbook
        self.playbook_id = playbook.playbook_id
        self.version = playbook.version
        #: Item 20 — the planner lets an overlay supersede other playbooks' questions.
        self.overlay = bool(getattr(playbook, "overlay", False))

    def mandatory_questions(self):  # noqa: ANN201
        return self._playbook.mandatory_questions()

    def specialist_roles(self):  # noqa: ANN201
        return self._playbook.specialist_role_ids()

    def completion_rules(self):  # noqa: ANN201
        return self._playbook.completion_rule_ids()


@dataclass
class V3ResearchOutcome:
    """What one V3 run produced. Serialised onto the report."""

    research_run_id: uuid.UUID | None = None
    mode: str = "standard"
    playbook_versions: dict[str, int] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    loop: dict[str, Any] = field(default_factory=dict)
    council: dict[str, Any] = field(default_factory=dict)
    challenges: dict[str, Any] = field(default_factory=dict)
    chair: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    consumption: dict[str, Any] = field(default_factory=dict)
    #: What the EXTERNAL research path did, if it ran. Summarised from the
    #: `research_leads` rows this run wrote — the provenance a reader needs in order to
    #: tell a claim InvestingBuddy verified from one a vendor merely asserted. Empty
    #: when no external tool was used.
    external_research: dict[str, Any] = field(default_factory=dict)
    #: How this company was classified, and by whom. A run that got no playbook must be
    #: separable into "the platform could not classify this company" and "the platform
    #: classified it and no playbook covers that industry" — those need opposite fixes,
    #: and `no playbook applied` alone tells a reader neither.
    classification: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    #: V3.18.4 — what the company's own documents say it produces.
    subject_profile: dict[str, Any] = field(default_factory=dict)
    #: V3.18.8 — the originating thesis, when the research followed from discovery.
    thesis: dict[str, Any] | None = None
    #: V3.18.8 — the reader-facing report, assembled from the ledger.
    professional_research: dict[str, Any] | None = None
    #: What the pre-run indexing of the company's corpus did.
    corpus_index: dict[str, int] = field(default_factory=dict)
    #: Whether the latest annual and quarterly reports were secured into the corpus.
    core_filings: dict[str, Any] = field(default_factory=dict)
    #: Non-US issuers: which official disclosures (UK FCA NSM / ASX) were secured into
    #: the corpus before the questions were asked, and why any was not.
    core_disclosures: dict[str, Any] = field(default_factory=dict)
    #: Item 21 — the issuer's own statements as this run found them: which slots its
    #: acquired documents fill, and — when none — whether the report was acquired but not
    #: extracted, not acquired, or (with the official listing as evidence) never filed.
    financial_statements: dict[str, Any] = field(default_factory=dict)
    #: Item 20 — the development-stage assessment (``classification.stage``) when it
    #: found anything: the signals it gave playbook selection and the evidence for them.
    stage: dict[str, Any] = field(default_factory=dict)
    #: Migration 043 — the final reconciliation: each gap closed / partially closed /
    #: superseded / still open, the temporal supersessions, and (once attached to a
    #: report) the labels on the V2 report's own gap statements.
    gap_reconciliation: dict[str, Any] = field(default_factory=dict)
    #: V3.18.2 — every planned question as a node: domain, owner, contract verdict,
    #: evidence counts, why it is still open, and what acquisition tried.
    question_graph: list[dict[str, Any]] = field(default_factory=list)
    #: Open-web W5: what the company web stage did (state, counts, families, cost units,
    #: dispositions, source classes). EMPTY — and absent from ``to_dict()`` — unless
    #: ``V3_COMPANY_WEB_RESEARCH_ENABLED`` ran the stage, so a run without it serialises
    #: exactly as it did before.
    web_context: dict[str, Any] = field(default_factory=dict)
    #: Why the run produced less than a full one. Never empty on a degraded run: a
    #: pipeline that narrowed silently is one nobody can widen.
    degraded: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ran(self) -> bool:
        return self.research_run_id is not None and self.error is None

    def to_dict(self) -> dict[str, Any]:
        payload = self._base_dict()
        if self.web_context:
            payload["web_context"] = dict(self.web_context)
        return payload

    def _base_dict(self) -> dict[str, Any]:
        from app.services.discovery.freshness import ENGINE_VERSION

        return {
            # V3.19.3 — which research engine produced this block, so a later discovery
            # run can tell current professional research from a legacy report.
            "research_engine_version": ENGINE_VERSION,
            "research_run_id": (str(self.research_run_id) if self.research_run_id else None),
            "mode": self.mode,
            "playbook_versions": dict(self.playbook_versions),
            "routing": dict(self.routing),
            "loop": dict(self.loop),
            "council": dict(self.council),
            "challenges": dict(self.challenges),
            "chair": dict(self.chair),
            "delta": self.delta,
            "findings": list(self.findings),
            "gaps": list(self.gaps),
            "consumption": dict(self.consumption),
            "external_research": dict(self.external_research),
            "classification": dict(self.classification),
            "question_graph": list(self.question_graph),
            "subject_profile": dict(self.subject_profile),
            "thesis": self.thesis,
            "professional_research": self.professional_research,
            "corpus_index": dict(self.corpus_index),
            "core_filings": dict(self.core_filings),
            "core_disclosures": dict(self.core_disclosures),
            "financial_statements_state": dict(self.financial_statements),
            "stage": dict(self.stage),
            "gap_reconciliation": dict(self.gap_reconciliation),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "degraded": list(self.degraded),
            "error": self.error,
            # Stated in the payload, not only in a doc: a reader of the stored report
            # must be able to tell what this block is and is not.
            "note": (
                "V3 research state, recorded beside the report. The report's narrative "
                "is assembled by the existing generator; this is the ledger, the "
                "council's verdict and the Red Team's challenges, with citable ids."
            ),
        }


def _mark_pre_revenue(outcome: V3ResearchOutcome, stage: Any) -> None:
    """Item 20 — for a development-stage company with no revenue slot, revenue is
    "pre-revenue / not applicable yet": a stage, not a missing-evidence gap."""
    statements = outcome.financial_statements
    if not isinstance(statements, dict):
        return
    slots = statements.get("slots") or {}
    if any(key.startswith("revenue_") for key in slots):
        return
    statements["revenue_status"] = {
        "state": "pre_revenue",
        "label": "Revenue: pre-revenue / not applicable yet",
        "basis": [b for b in stage.basis if b.startswith("P1")],
        "provenance": "derived",
        "note": (
            "The company's own annual statements show no or immaterial revenue in the "
            "latest two years, and its documents describe a mining project under the "
            "reporting codes. Revenue and margins are not applicable yet; this is a "
            "stage, not a missing figure."
        ),
    }


def _investigator_degraded_reasons(diagnostics: dict[str, Any]) -> list[str]:
    """Name, on the run, why model replies produced no findings.

    A run whose specialists retrieved evidence and wrote nothing used to look identical
    to one that found nothing to write about. These lines are what make the two
    distinguishable to a reader of the report, not only to someone with database access.

    Counts only — no model text, no prompt, no provider identifier.
    """
    reasons: list[str] = []
    unparseable = int(diagnostics.get("responses_unparseable", 0) or 0)
    empty = int(diagnostics.get("responses_empty_payload", 0) or 0)
    truncated = int(diagnostics.get("responses_truncated", 0) or 0)
    uncited = int(diagnostics.get("statements_dropped_uncited", 0) or 0)
    total = int(diagnostics.get("responses_total", 0) or 0)

    if unparseable:
        reasons.append(
            f"{unparseable} of {total} model repl(ies) could not be read as JSON, so the "
            "evidence retrieved for those questions was never interpreted"
        )
    if empty:
        reasons.append(
            f"{empty} of {total} model repl(ies) were read correctly and contained "
            "nothing; the questions stay open"
        )
    if truncated:
        reasons.append(
            f"{truncated} of {total} model repl(ies) stopped at the output limit "
            "(finish_reason=length)"
        )
    if uncited:
        reasons.append(
            f"{uncited} statement(s) were discarded for citing no evidence at all — the "
            "citation rule held, and the question stays open"
        )
    return reasons


async def run_v3_research(
    session: Any,
    company: Any,
    *,
    cfg: Any = None,
    mode: str | None = None,
    search_backend: Any = None,
    routing: ModelRouting | None = None,
    research_job_id: uuid.UUID | None = None,
    now: Any = None,
    discovery_candidate_id: str | uuid.UUID | None = None,
    report_agent_run_id: uuid.UUID | None = None,
) -> V3ResearchOutcome:
    """Run the V3 pipeline for one company. **Never raises.**

    A V3 failure must never cost a report the V2 path would have produced, so every
    exception is caught, recorded on the outcome, and returned.
    """
    if cfg is None:
        from app.core.config import settings as default_cfg

        cfg = default_cfg
    clock = now or time.monotonic
    started = clock()
    outcome = V3ResearchOutcome()

    # `research_tool_calls.research_job_id` is a FOREIGN KEY to `research_jobs.id`, and
    # the id reaching this function is an **AgentRun** id on BOTH entry points — the V2
    # background task passes it directly, and the durable handler passes
    # `content_run_id`, which `store.link_agent_run(ctx.job.id, agent_run_id=...)` names
    # as the agent run rather than the job. Writing it violated the constraint on the
    # first tool call, aborted the transaction, and took the V2 report down with it.
    #
    # Measured on real PostgreSQL at head 038, before this line existed: 0 tool calls
    # persisted, 0 findings, and **the V2 report was never written**. The unit suite
    # runs on SQLite with foreign keys OFF, which is why 6,100 green tests missed it.
    # The AgentRun that OWNS the report this research attaches to (the caller reads it
    # off the report: ``Report.created_by_agent_run_id``, the id the report's
    # primary-documents view is scoped to). Documents this run acquires or reuses are
    # recorded against it. Only an id that names an agent_runs row is used — a broken
    # link would abort the shared transaction, see above.
    agent_run_id = await _existing_agent_run_id(session, report_agent_run_id)
    research_job_id = await _resolve_research_job_id(session, research_job_id, outcome)

    # V3.18.2 — the ledger this code writes has columns migration 041 adds. On a database
    # without them every ledger query fails, so the run is not attempted: it degrades
    # with the reason named, and the report the V2 path produced is untouched.
    from app.services import schema_readiness

    for migration, check in (
        ("041", schema_readiness.migration_041_readiness),
        ("043", schema_readiness.migration_043_readiness),
    ):
        readiness = await check(session)
        if not readiness.ready:
            outcome.error = "schema_not_ready"
            outcome.degraded.append(
                f"the V3 research graph was not run: migration {migration} is not applied "
                f"to this database (missing {', '.join(readiness.missing[:4])})"
            )
            outcome.elapsed_seconds = clock() - started
            return outcome

    try:
        # A SAVEPOINT, so a V3 error that PROPAGATES releases only V3's writes rather
        # than leaving the shared transaction dirty. It is defence in depth and not the
        # fix above: measured, a swallowed database error still aborts the outer
        # transaction, because `except Exception` cannot un-abort one. The rule that
        # actually protects the report is never writing a broken link in the first
        # place.
        async with session.begin_nested():
            await _run(
                session,
                company,
                cfg=cfg,
                outcome=outcome,
                mode=mode,
                search_backend=search_backend,
                routing=routing,
                research_job_id=research_job_id,
                discovery_candidate_id=discovery_candidate_id,
                agent_run_id=agent_run_id,
            )
    except Exception as exc:  # noqa: BLE001 - additive work must not fail the report
        outcome.error = type(exc).__name__
        outcome.degraded.append(f"the V3 pipeline raised {type(exc).__name__}")
    outcome.elapsed_seconds = clock() - started
    return outcome


async def _existing_agent_run_id(session: Any, run_id: uuid.UUID | None) -> uuid.UUID | None:
    """``run_id`` when it names an ``agent_runs`` row, else ``None`` (never raises)."""
    if run_id is None:
        return None
    try:
        from sqlalchemy import select

        from app.models.agent_run import AgentRun

        found = (await session.execute(
            select(AgentRun.id).where(AgentRun.id == run_id))).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - a missing link is honest; a broken one is not
        return None
    return found


async def _resolve_research_job_id(
    session: Any, research_job_id: uuid.UUID | None, outcome: V3ResearchOutcome
) -> uuid.UUID | None:
    """Return the id only if it really names a ``research_jobs`` row.

    An id that does not is dropped rather than written: the column is a link, and a
    broken link is worth less than no link — it costs the entire transaction, and with
    it the report the V2 path had already produced.

    The check itself moved to ``jobs.lineage`` in V3.17.9 so that the consumption
    recorder — which writes outside this module's SAVEPOINT — is guarded by the same
    rule rather than by a second copy of it. What stays here is the **degraded line**:
    an unlinked run has to say so on the outcome, or the empty column is a mystery
    nobody can date.
    """
    if research_job_id is None:
        return None
    from app.services.jobs.lineage import resolve_durable_job_id

    resolved = await resolve_durable_job_id(session, research_job_id)
    if resolved is None:
        outcome.degraded.append(
            "tool calls are not linked to a durable job row; the id supplied names no "
            "research_jobs row"
        )
    return resolved


async def _run(
    session: Any,
    company: Any,
    *,
    cfg: Any,
    outcome: V3ResearchOutcome,
    mode: str | None,
    search_backend: Any,
    routing: ModelRouting | None,
    research_job_id: uuid.UUID | None = None,
    discovery_candidate_id: str | uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
) -> None:
    resolved_mode = parse_mode(mode or getattr(cfg, "v3_research_mode_default", None))
    limits = limits_for(resolved_mode)
    outcome.mode = resolved_mode.value

    # The corpus search backend, built from configuration when the caller did not
    # supply one — which is every production caller. `V3_SEARCH_BACKEND` had no
    # consumer on this path: the factory existed, the parameter existed, the backend
    # and its tests existed, and nothing joined them, so `search_company_corpus`
    # answered `backend_configured: false` no matter what the setting said. That is
    # the third flag in this campaign found without a consumer, and the reason the
    # rule is now "a flag is not enabled until something reads it".
    #
    # Failure here degrades the SEARCH LEG, not the run: `run_v3_research` catches
    # everything, so letting this raise would cost the findings, the council and the
    # chair over a misconfigured retrieval setting.
    if search_backend is None and getattr(cfg, "v3_corpus_enabled", False):
        from app.services.corpus.search.factory import get_search_backend

        try:
            search_backend = get_search_backend(cfg, session=session)
        except Exception as exc:  # noqa: BLE001 - a bad backend name must not end the run
            outcome.degraded.append(
                f"corpus search unavailable ({type(exc).__name__}); "
                "the internal corpus was not searched"
            )

    # V3.18 live acceptance — the corpus the specialists search must be INDEXED. A
    # company whose chunks were written by the V2 ingestion had none, and every corpus
    # search came back empty. Indexing is index state on rows already held: no cost.
    # V3.18 live acceptance — the corpus must HOLD the documents the questions need.
    # The subject's latest annual and quarterly reports are secured through the V3.16
    # bridge first; a corpus of one 10-Q and an exhibit index cannot describe a business.
    from app.services.corpus.filing_evidence import ensure_core_filings

    try:
        async with session.begin_nested():
            outcome.core_filings = await ensure_core_filings(
                session, company=company, cfg=cfg
            )
    except Exception as exc:  # noqa: BLE001 - a failed acquisition costs the step only
        outcome.degraded.append(
            f"the latest annual and quarterly filings could not be secured "
            f"({type(exc).__name__})"
        )
    for slot in ("annual", "quarterly"):
        state = (outcome.core_filings.get(slot) or {}).get("state")
        if state and state not in ("ready", "acquired", "reused"):
            reason = (outcome.core_filings.get(slot) or {}).get("reason") or state
            outcome.degraded.append(f"the latest {slot} report is not in the corpus ({reason})")

    # The same step for an issuer SEC does not cover: its own official disclosures —
    # the UK FCA National Storage Mechanism or the ASX — secured into the corpus before
    # a single question is asked. Without it a verified LSE / ASX small cap reached
    # research with an empty corpus, and a blocking playbook question refused the
    # Council (Pensana, V3.19).
    from app.services.sources.disclosures.acquisition import ensure_core_disclosures

    try:
        async with session.begin_nested():
            outcome.core_disclosures = await ensure_core_disclosures(
                session, company=company, cfg=cfg, agent_run_id=agent_run_id
            )
    except Exception as exc:  # noqa: BLE001 - a failed acquisition costs the step only
        outcome.degraded.append(
            f"the issuer's official disclosures could not be secured ({type(exc).__name__})"
        )
    skipped = outcome.core_disclosures.get("skipped")
    if skipped == "connector_disabled":
        # An LSE / ASX issuer researched with its disclosure source off: said, not silent.
        outcome.degraded.append(
            "the issuer's official disclosure source is not enabled for its venue, so "
            "its own announcements and reports were not secured into the corpus"
        )
    elif outcome.core_disclosures.get("source_id") and skipped:
        outcome.degraded.append(
            f"no official disclosure was secured from {outcome.core_disclosures['source_id']}"
            f" ({skipped})"
        )
    for item in outcome.core_disclosures.get("documents") or []:
        if item.get("state") != "ready":
            outcome.degraded.append(
                f"an official {item.get('document_kind') or 'document'} "
                f"({item.get('source_id')} {item.get('document_ref')}) is not in the "
                f"corpus ({item.get('reason')})"
            )

    # Item 21 — the statements those documents yielded, read the way the V2 snapshot
    # reads its own facts. The V2 report was assembled before any of this was acquired,
    # so without it an acquired annual report still rendered as "Not reported".
    from app.services.pipeline.issuer_financials import financial_statements_for

    try:
        async with session.begin_nested():
            outcome.financial_statements = await financial_statements_for(
                session,
                company,
                core_disclosures=outcome.core_disclosures,
                core_filings=outcome.core_filings,
            )
    except Exception as exc:  # noqa: BLE001 - a statements view must not end the run
        outcome.degraded.append(
            f"the issuer's statement figures could not be read ({type(exc).__name__})"
        )

    # 2. Classification, then playbook selection.
    #
    # Reading `company.sector` straight off the row was the whole defect: the column is
    # NULL for all but one of the companies in the database, so selection was answering
    # a question nobody had ever given it the input for. `ensure_company_classification`
    # is the single handoff — it reuses what is stored, asks the SEC only when the row
    # has never been classified, and refuses to overwrite a stronger source with a
    # weaker one.
    #
    # It never raises, and a company it cannot classify still gets NONE and a recorded
    # reason, never the nearest-looking playbook.
    classification = await ensure_company_classification(session, company)
    outcome.classification = classification.to_dict()
    # Item 20 — what the company's own documents say it produces, and whether they
    # positively show a pre-revenue resource developer. Built BEFORE selection so the
    # business-model signals the playbooks declare are finally given to selection. A
    # company with no such proof gets no signal, and its selection is unchanged.
    from app.services.classification.stage import detect_stage
    from app.services.director.subject_profile import build_subject_profile

    profile = await build_subject_profile(session, company)
    outcome.subject_profile = profile.to_dict()
    from app.services.playbooks.industries import MINING_MATERIALS

    stage = await detect_stage(
        session, company, subject_profile=profile,
        # Classified in a mining INDUSTRY (never the broad "Materials" sector, which
        # also holds chemicals and packaging).
        mining_sector=bool(classification.industry) and MINING_MATERIALS.applies_to.matches(
            industry=classification.industry),
    )
    if stage.signals or stage.basis:
        outcome.stage = stage.to_dict()
    if stage.is_development_stage:
        _mark_pre_revenue(outcome, stage)
    # Open-web W5 — wave 2 + RISK web research for THIS company (spec §7.1), after the
    # official sources above and BEFORE indexing, so what it stores is indexed with the
    # rest and reachable through `search_company_corpus`. The classification, subject
    # profile and stage signals it plans from were computed above from the company's own
    # official documents ONLY — a third-party page can never feed the stage detector.
    # Gated, isolated (it never raises) and inert with the flag off: nothing is read.
    web_units = None
    web_ctx = None
    if getattr(cfg, "v3_company_web_research_enabled", False):
        from app.services.web_research.stage import WebRunContext, ensure_web_context

        web_ctx = WebRunContext(
            mode=resolved_mode.value,
            research_job_id=research_job_id,
            agent_run_id=agent_run_id,
            industry=classification.industry or classification.sector,
            themes=[m.commodity.name for m in profile.commodities],
            development_stage=bool(stage.is_development_stage),
            search_backend=search_backend,
            # `private_tokens` (rule G1: portfolio holdings, uploads) is NOT populated
            # here: public company research carries none. It MUST be wired from the
            # portfolio/upload context before this stage is used for personalised
            # (V2) research, or the G1 query check is vacuous.
        )
        web = await ensure_web_context(session, company, web_ctx, cfg=cfg)
        if web.ran:
            outcome.web_context = web.summary
            web_units = web.units
            if web.summary.get("label") and web.state != "web_search_disabled":
                outcome.degraded.append(str(web.summary["label"]))

    if search_backend is not None:
        from app.services.corpus.indexing import ensure_company_indexed

        try:
            async with session.begin_nested():
                outcome.corpus_index = await ensure_company_indexed(
                    session, company_id=company.id, backend=search_backend, cfg=cfg
                )
        except Exception as exc:  # noqa: BLE001 - an index failure costs the search leg
            outcome.degraded.append(
                f"the company's corpus could not be indexed ({type(exc).__name__}); "
                "corpus searches may return nothing"
            )

    model_routing = routing or resolve_routing(cfg)
    outcome.routing = model_routing.to_dict()
    if not model_routing.any_resolved:
        outcome.degraded.append(
            "no model provider resolved; investigations run tools and report gaps"
        )

    # 1. Prior research — the "before" a delta compares against, and the gaps that
    #    become this run's questions.
    prior = await memory.recall(session, company_id=company.id)
    carry_forward = prior.carry_forward_questions if prior.exists else ()

    selection = select_playbooks(
        sector=classification.matching_sector,
        industry=classification.industry,
        signals=stage.signals,
    )
    if selection.is_empty:
        outcome.degraded.append(f"no playbook applied: {selection.reason}")
        # The reason above names what selection was GIVEN, and that is deliberately
        # narrower than what the company was classified as. Saying so here is what makes
        # an empty selection actionable, because the three ways to get one need three
        # different fixes.
        if not classification.is_known:
            # Nothing classified this company, so no playbook *could* apply.
            outcome.degraded.append(
                "company is unclassified: "
                + (classification.notes[-1] if classification.notes else "no sources")
            )
        elif classification.matching_sector is None and not classification.industry:
            # Classified, but only as an estimate. A sector-only match is the broadest
            # selection the platform makes; making it from a guess as well would apply a
            # specialist methodology to a company no source actually classified.
            outcome.degraded.append(
                f"company was classified as sector {classification.sector!r}, but only "
                "as a T6_model_estimate — an estimate does not select a specialist "
                "methodology, so the generic one was used."
            )
        else:
            # Classified from a real source, and no playbook covers that industry. This
            # is a playbook-coverage gap, not a classification problem.
            outcome.degraded.append(
                f"company is classified ({classification.industry or classification.sector!r},"
                f" {classification.tier}) and no playbook declares it — a coverage gap, "
                "not a classification failure."
            )
    elif classification.is_inferred:
        # A methodology chosen from the platform's own estimate is still a methodology
        # chosen from a guess, and the reader is told so.
        outcome.degraded.append(
            f"playbook selected from an INFERRED classification "
            f"(industry={classification.industry!r}, T6_model_estimate) — confirm "
            "against a primary classification."
        )
    playbooks = [_PlaybookAdapter(p) for p in selection.playbooks]
    outcome.playbook_versions = dict(selection.versions)

    # 3. The ledger run.
    run = await ledger.open_run(
        session,
        mode=resolved_mode.value,
        company_id=company.id,
        legal_entity_id=getattr(company, "legal_entity_id", None),
        # V3.17.9. `ledger.open_run` has taken this argument since V3.5 and nothing ever
        # passed it, so `research_runs.research_job_id` was NULL on every row in
        # production — a run could not be traced to the job that paid for it even once
        # the id became correct. Already validated by `_resolve_research_job_id` above,
        # so this can only ever be a real job or None.
        research_job_id=research_job_id,
        budget=budget_for(resolved_mode, cfg).__dict__,
        playbook_versions=dict(selection.versions),
    )
    outcome.research_run_id = run.id

    # 4. Plan. V3.18.4 — what the company produces, from its OWN documents (the
    #    profile, built above for selection), so a per-commodity question is asked
    #    about the commodities this company sells.
    # V3.18.8 — WHY this company is being researched. Each dimension the originating
    # thesis names becomes a question the research must answer with evidence; the run
    # records the thesis so the report can test it rather than decorate it.
    from app.services.director.thesis import resolve_thesis, size_fit, thesis_questions

    thesis = await resolve_thesis(session, discovery_candidate_id=discovery_candidate_id)
    thesis_size = None
    if thesis.present:
        market_cap = thesis.market_cap_usd
        if (
            market_cap is None
            and getattr(company, "market_cap", None) is not None
            and str(getattr(company, "currency", "") or "").upper() == "USD"
        ):
            market_cap = float(company.market_cap)
        thesis_size = size_fit(thesis, market_cap)
        outcome.thesis = {**thesis.to_dict(), "size_fit": thesis_size}
        # V3.19.1 — "is it a critical mineral?" is answered by the OFFICIAL list, for the
        # commodities the company's own documents are about. Never by its wording.
        if "critical_materials" in thesis.dimensions:
            from app.services.sources.critical_minerals import designations_for

            outcome.thesis["critical_mineral_designations"] = designations_for(
                m.commodity.slug for m in profile.commodities
            )
        run.thesis_json = outcome.thesis
    elif discovery_candidate_id:
        outcome.degraded.append(
            "a discovery candidate was named but its thesis could not be read; the "
            "research ran without it"
        )

    plan = await plan_research(
        subject=f"{company.ticker}:{company.exchange}",
        mode=resolved_mode,
        playbooks=playbooks,
        prior_open_gaps=carry_forward,
        cfg=cfg,
        commodities=[m.commodity for m in profile.commodities],
        extra_questions=thesis_questions(thesis),
    )
    # A plan that will answer a question with less than it asks for says so on the run,
    # where a reader sees it — not only in the planner's own record.
    for reason in plan.degraded:
        outcome.degraded.append(reason)
    if plan.superseded:
        # Item 20 — the producer questions an overlay superseded, on the record.
        outcome.stage = {**outcome.stage, "superseded_questions": dict(plan.superseded)}
    if plan.blocking_demoted:
        outcome.stage = {**outcome.stage, "blocking_demoted": dict(plan.blocking_demoted)}
    await persist_plan(session, run, plan)

    # 5. Investigate, with real tools and a real model where one resolved.
    # `cfg=` matters: the external tools are the one CONDITIONAL registration, so a
    # registry built without it can never contain them however the run was configured.
    registry = register_builtins(ToolRegistry(), cfg=cfg)
    investigator_client = model_routing.client_for(SLOT_INVESTIGATOR)
    # V3.18.3 — ONE ceiling on external searches for the whole run, shared by every
    # specialist. `max_web_searches` was declared per mode and enforced by nothing; the
    # acquisition ladder makes a search reachable from any question whose contract
    # allows it, so the ceiling has to be real before that is switched on.
    from app.services.agents.investigator import (
        ExternalSearchBudget,
        clean_company_name,
        role_can_search_externally,
    )

    # Open-web W5: the company web stage and the Investigator's `search_web` spend from
    # ONE ceiling — the searches the stage executed are no longer available to the rung.
    # Counted in NETWORK CALLS (what was spent), not queries "executed": a cache serve spent
    # nothing and a failed paid call spent a call (W5 review C-M4). `web_units` survives a
    # stage that died before it wrote its summary.
    stage_searches = int(getattr(web_units, "web_search_calls", 0) or 0)
    external_budget = (
        # The run's BUDGET, not the mode preset: `budget_for` applies the operator's
        # `V3_RUN_MAX_WEB_SEARCHES`, which narrows whatever the mode proposes. Reading
        # the preset let a STANDARD run spend 12 paid searches against a cap of 2, while
        # the budget recorded on the run said 2.
        ExternalSearchBudget(
            limit=max(0, int(budget_for(resolved_mode, cfg).max_web_searches) - stage_searches)
        )
        if "search_web" in registry.names()
        else None
    )
    subject_name = clean_company_name(getattr(company, "name", None))
    subject_industry = classification.industry or classification.sector

    # Open-web W7 — the web rung of the Director loop and the Red Team's search wave.
    # ``None`` unless V3_WEB_FOLLOWUP_ENABLED AND the company web path can search and
    # fetch; with it ``None`` every call below passes nothing new (byte-identical).
    web_followup = None
    if web_ctx is not None and getattr(cfg, "v3_web_followup_enabled", False):
        from app.services.web_research.followup import WebFollowup

        # No shared ceiling: the stage plans up to the mode's whole `max_web_searches`, so
        # sharing it would leave the follow-up nothing. It has its own bounds (the
        # "followup" profile per round, the per-mode round cap, the operator and daily caps).
        web_followup = WebFollowup.create(session, company, web_ctx, cfg=cfg)

    class _RoleRoutedInvestigator:
        """One session per role, because a tool policy is per role.

        Building it here rather than in the loop keeps the loop ignorant of tool
        policy — it owns control flow and nothing about how a specialist works.
        """

        def __init__(self) -> None:
            self.fabricated: list[str] = []
            self.diagnostics = InvestigatorDiagnostics()
            self.tool_calls = 0
            #: Earlier verifications this run cited again. They write no lead row for
            #: this run, so without this list a finding could cite an evidence id the
            #: run's external-research panel never shows.
            self.reused: dict[str, dict[str, Any]] = {}
            #: V3.18.8 — the payloads the report's tables are built from: exactly what
            #: the specialists retrieved, never a second fetch.
            self.table_payloads: dict[str, list[dict[str, Any]]] = {
                "get_industry_series": [],
                "get_peer_financials": [],
            }

        def can_search_externally(self, role_id: str) -> bool:
            # The loop's probe, answered for the RUN's shared budget. Without it the
            # loop saw no probe on this wrapper and re-queued questions for a follow-up
            # that could only climb a rung the run had already spent or switched off.
            return role_can_search_externally(role_id, external_budget)

        async def investigate(  # noqa: ANN201
            self,
            *,
            role_id,  # noqa: ANN001
            questions,  # noqa: ANN001
            round_index,  # noqa: ANN001
            remaining_tool_calls,  # noqa: ANN001
            question_context=None,  # noqa: ANN001
        ):
            from app.services.director.roles import role_for

            role = role_for(role_id)
            # V3.18.2 — the session may call the role's acquisition ladder too. Holding a
            # ladder tool does not ASSIGN questions (that is `tools`), and the ladder is
            # climbed only when a question's contract is unmet and allows it.
            tools = role.session_tools if role is not None else frozenset()
            # A role's declared source classes must reach its policy, or the governance
            # check refuses every tool that reads anything but platform-internal data.
            # V3.12 found this by running it: `search_web` reads `public_web`, the
            # external role declares `public_web`, and the policy was built with an
            # empty set — so the very first external call was refused
            # `access_class_not_permitted`. The check was right; the wiring was not.
            classes = role.session_source_classes if role is not None else ()
            # A role's declared `tool_budget` was persisted to the plan and enforced by
            # nothing: it is keyed by TOOL NAME while `ToolBudget` bounds tool CLASSES,
            # so the two shapes never met. What maps unambiguously is the total — the
            # sum of a role's declared per-tool caps is a real ceiling on its calls, and
            # an enforced approximate bound is worth more than an exact decorative one.
            # Per-tool caps remain unenforced; `MAX_CALLS_PER_QUESTION` and the run's
            # `max_tool_calls` are the bounds that actually bite today.
            declared = sum((role.tool_budget or {}).values()) if role is not None else 0
            if declared:
                # V3.18.3's ladder spends per QUESTION — corpus by intent, one search,
                # its verifications — on top of the platform tools the declaration
                # budgeted. Without this allowance the live SCCO run had 48 corpus calls
                # and 15 searches refused `budget_exceeded` by a ceiling sized before the
                # ladder existed. The run's own max_tool_calls and search budget still
                # bound the total.
                declared += LADDER_CALLS_PER_QUESTION * len(questions)
            tool_session = ToolSession(
                registry=registry,
                policy=policy_for(
                    role_id,
                    tools=tools,
                    access_classes=classes,
                    budget=ToolBudget(max_calls=declared) if declared else None,
                ),
                cfg=cfg,
                db=session,
                # The DURABLE JOB id, or None. Deliberately not `run.id`: that is a
                # `research_runs` id and this column's foreign key points at
                # `research_jobs`. The first real pipeline run found exactly that —
                # every tool call failed to persist and took the transaction with it,
                # because the unit suite runs on sqlite with foreign keys off.
                research_job_id=research_job_id,
                company_id=company.id,
                legal_entity_id=getattr(company, "legal_entity_id", None),
                search_backend=search_backend,
            )
            worker = LLMInvestigator(
                session=tool_session,
                company_id=company.id,
                ticker=getattr(company, "ticker", None),
                exchange=getattr(company, "exchange", None),
                client=investigator_client,
                company_name=subject_name,
                industry=subject_industry,
                external_budget=external_budget,
                available_tools=frozenset(registry.names()),
                primary_commodity=(
                    profile.commodities[0].commodity.name if profile.commodities else None
                ),
                candidate_fetcher=(
                    web_followup.fetch_candidates if web_followup is not None else None
                ),
            )
            result = await worker.investigate(
                role_id=role_id,
                questions=questions,
                round_index=round_index,
                remaining_tool_calls=remaining_tool_calls,
                question_context=question_context,
            )
            self.fabricated.extend(worker.fabricated_citations)
            # V3.16.1a — merged per worker, exactly as `fabricated_citations` already is.
            # A fresh investigator is built per task, so a counter left on the worker
            # would be discarded at the end of every task.
            self.diagnostics.merge(worker.diagnostics)
            self.tool_calls += result.tool_calls
            for call in tool_session.calls:
                payload = getattr(call, "payload", None)
                tool_name = getattr(call, "tool_name", None)
                if tool_name in self.table_payloads and isinstance(payload, dict) and (
                    getattr(call, "ok", True)
                ):
                    self.table_payloads[tool_name].append(payload)
                if tool_name != "fetch_public_source" or not isinstance(payload, dict):
                    continue
                for item in payload.get("items") or []:
                    if isinstance(item, dict) and item.get(
                        "reused_from_earlier_verification"
                    ) and item.get("evidence_id"):
                        self.reused.setdefault(
                            str(item["evidence_id"]),
                            {
                                "evidence_id": str(item["evidence_id"]),
                                "fetched_url": item.get("fetched_url"),
                                "claim": item.get("claim"),
                                "source_excerpt": str(item.get("source_excerpt") or "")[:400],
                            },
                        )
            return result

    investigator = _RoleRoutedInvestigator()
    loop_result: LoopResult = await run_investigation(
        session,
        run,
        plan,
        investigator=investigator,
        limits=limits,
        completion_rules=selection.completion_rules,
        web_followup=web_followup,
    )
    outcome.loop = loop_result.to_dict()
    outcome.loop["fabricated_citations_discarded"] = len(investigator.fabricated)
    if external_budget is not None:
        outcome.loop["external_searches"] = {
            "limit": external_budget.limit,
            "used": external_budget.used,
        }
    # V3.16.1a — why a model reply produced no finding, counted rather than guessed.
    diagnostics = investigator.diagnostics.to_dict()
    outcome.loop["investigator_diagnostics"] = diagnostics
    for reason in _investigator_degraded_reasons(diagnostics):
        outcome.degraded.append(reason)

    # 6. Council V2 input — which may refuse to convene, with a reason.
    council = await council_inputs.assemble(session, run)
    outcome.council = council.to_dict()

    # 7. Red Team, one bounded round.
    citable = {eid for f in council.findings for eid in f.evidence_ids}
    risk_evidence: list[Any] = []
    if web_followup is not None:
        # Open-web W7 (spec §7.4): the RISK family always runs in the challenge wave,
        # whatever the thesis says; what it (and the stage's wave 3) stored reaches the
        # Red Team as labelled evidence, and the responder may cite it.
        await web_followup.challenge_wave()
        risk_evidence = await web_followup.risk_evidence()
        citable |= {item.evidence_id for item in risk_evidence}
    red = LLMRedTeam(client=model_routing.client_for(SLOT_RED_TEAM))
    if web_followup is not None:
        red.risk_evidence = risk_evidence
        red.issuer_key = getattr(company, "id", None)
    responder = LLMResponder(
        client=model_routing.client_for(SLOT_RED_TEAM), citable_ids=frozenset(citable)
    )
    challenge_result = await run_challenge_round(
        session, run, council, red_team=red, responder=responder
    )
    outcome.challenges = challenge_result.to_dict()
    outcome.challenges["discarded_unknown_targets"] = len(red.discarded_unknown_targets)
    if web_followup is not None:
        outcome.challenges["risk_evidence_items"] = len(risk_evidence)
        outcome.challenges["discarded_low_trust_basis"] = len(red.discarded_low_trust_basis)
        followup_record = web_followup.to_dict()
        # The run record: rounds, queries and WHY the follow-up stopped (additive keys).
        outcome.web_context = {
            **(outcome.web_context or {}),
            "followup_rounds": followup_record["followup_rounds"],
            "followup": {
                "queries": followup_record["queries"],
                "stopped_by": followup_record["stopped_by"],
                "rounds": followup_record["rounds"],
                "challenge": followup_record["challenge"],
                "template_version": followup_record["template_version"],
                "gaps_handled": followup_record["gaps_handled"],
            },
        }
        web_units = web_followup.units if web_units is None else web_units + web_followup.units

    # 7b. Reconciliation — BEFORE the Chair and the report are assembled, so neither
    #     calls a field missing that a finding states, nor shows superseded guidance as
    #     current. A failure costs the reconciliation only: the gaps then stand as
    #     recorded, which is the fail-closed direction.
    await reconcile_step(session, run, company=company, outcome=outcome)

    # 8. Chair. Re-assembled first, because the Red Team may have withdrawn a finding
    #    and the Chair must not see one that was retired.
    council = await council_inputs.assemble(session, run)
    verdict: ChairVerdict = await LLMChair(client=model_routing.client_for(SLOT_CHAIR)).deliberate(
        council
    )
    outcome.chair = verdict.to_dict()
    if verdict.deterministic_fallback:
        # Named, because the two states mean opposite things about the deployment. The
        # live MRNA run reported this as a model being unavailable while a chair model
        # was routed and reachable — the council simply had no findings to synthesise.
        outcome.degraded.append(
            "the chair returned the deterministic verdict because the council did not "
            "convene (no findings to synthesise)"
            if verdict.fallback_reason == "council_did_not_convene"
            else "the chair fell back to the deterministic verdict: no chair model was "
            "available"
        )

    # 9. What a reader can cite.
    #
    # V3.18.2 — from the LEDGER, not from the Council's input. A refused Council used
    # to empty this list: one open blocking question and the report showed no findings
    # at all while the ledger held them (MRNA, live: ten findings recorded, zero shown).
    # A refusal withholds the SYNTHESIS — the verdict an unanswered blocking question
    # makes unsafe — not the research. Each finding says whether the Council convened.
    if council.convened:
        outcome.findings = [f.to_dict() for f in council.findings[:60]]
    else:
        outcome.findings = await _ledger_findings(session, run, limit=60)
    outcome.gaps = [g.to_dict() for g in council.gaps[:40]]
    outcome.question_graph = await _question_graph(session, run)

    # 10. Research memory: the delta against the previous run.
    #
    # The previous run is the one captured at step 1, BEFORE this run existed. Asking
    # `previous_run` again here would find THIS run — it is now `reviewing` or `stopped`
    # and is the most recent — and the guard against comparing a run with itself would
    # then produce no delta at all. That is the failure mode where the feature looks
    # implemented and silently returns nothing, and it is what this comment is for.
    if prior.exists and prior.run_id is not None:
        from app.models.ledger import ResearchRun

        previous = await session.get(ResearchRun, prior.run_id)
        if previous is not None and previous.id != run.id:
            delta = await compute_delta(session, to_run=run, from_run=previous)
            await persist_delta(session, delta)
            outcome.delta = delta.to_dict()

    summary = await ledger.summarise(session, run)
    tool_units = await _tool_call_consumption(session, run, company)
    outcome.external_research = await _external_research(session, run, company)
    outcome.external_research["reused_verifications"] = list(investigator.reused.values())[:40]
    # 11. V3.18.8 — the professional report, from the ledger. Never costs the run.
    #     BEFORE consumption is read: the editor's model call is spend on the chair's
    #     client, and `_consumption` takes each client's usage once.
    try:
        report, withheld_reason = await _professional_report(
            session,
            run,
            plan,
            loop_result,
            company=company,
            thesis=outcome.thesis,
            thesis_size=thesis_size,
            table_payloads=investigator.table_payloads,
            council_convened=bool(council.convened),
            editor_client=model_routing.client_for(SLOT_CHAIR),
            supersessions=(outcome.gap_reconciliation or {}).get("supersessions") or [],
            stage=(outcome.stage or {}).get("stage"),
            web_context=outcome.web_context or None,
        )
        outcome.professional_research = report
        if withheld_reason:
            outcome.degraded.append(withheld_reason)
    except Exception as exc:  # noqa: BLE001 - a report failure costs the report block only
        outcome.degraded.append(
            f"the professional report could not be assembled ({type(exc).__name__})"
        )
    outcome.consumption = _consumption(
        model_routing, loop_result, summary, verdict, challenge_result, cfg, tool_units,
        web_units,
    )
    await session.flush()


async def reconcile_step(session: Any, run: Any, *, company: Any, outcome: Any) -> None:
    """Step 7b: reconcile gaps and supersede guidance. Never raises.

    In a SAVEPOINT: a database error inside it releases only its own writes, so the
    Chair, the report and the V2 report after it still write on a clean transaction.
    A failure costs the reconciliation only; the gaps then stand as recorded, which is
    the fail-closed direction.
    """
    from app.services.pipeline import gap_reconciliation

    try:
        async with session.begin_nested():
            outcome.gap_reconciliation = await gap_reconciliation.reconcile_run(
                session,
                run,
                company_id=getattr(company, "id", None),
                core_filings=outcome.core_filings,
                core_disclosures=outcome.core_disclosures,
            )
    except Exception as exc:  # noqa: BLE001 - reconciliation must not end the run
        outcome.degraded.append(
            f"gaps were not reconciled against the findings ({type(exc).__name__}); "
            "they are shown as recorded"
        )


#: Findings read into the report. The ledger may hold more; the report says how many.
MAX_REPORT_FINDINGS = 200
#: Tool calls the acquisition ladder may add per question: two corpus intents, one
#: search, up to four verifications.
LADDER_CALLS_PER_QUESTION = 7


async def _professional_report(
    session: Any,
    run: Any,
    plan: Any,
    loop_result: Any,
    *,
    company: Any,
    thesis: dict[str, Any] | None,
    thesis_size: dict[str, Any] | None,
    table_payloads: dict[str, list[dict[str, Any]]],
    council_convened: bool,
    editor_client: Any,
    supersessions: list[dict[str, Any]] | None = None,
    stage: str | None = None,
    web_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Screen, assemble, edit, rescan. ``(None, reason)`` only when the final scan fails
    — and then the reason is on the run, never a silent absence."""
    from sqlalchemy import func, select

    from app.models.ledger import ResearchFinding, ResearchGap, ResearchQuestion
    from app.schemas.catalyst import neutralize_forbidden_terms
    from app.services import safety_terms
    from app.services.pipeline import professional_research as pr

    async with session.begin_nested():
        findings_total = await session.scalar(
            select(func.count())
            .select_from(ResearchFinding)
            .where(
                ResearchFinding.research_run_id == run.id,
                ResearchFinding.verification_status != "withdrawn",
            )
        )
        question_rows = (
            await session.execute(
                select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
            )
        ).scalars().all()
        finding_rows = (
            await session.execute(
                select(ResearchFinding)
                .where(
                    ResearchFinding.research_run_id == run.id,
                    ResearchFinding.verification_status != "withdrawn",
                )
                .order_by(
                    ResearchFinding.domain.asc().nulls_last(),
                    ResearchFinding.question_key.asc().nulls_last(),
                    ResearchFinding.created_at,
                    ResearchFinding.statement,
                )
                .limit(MAX_REPORT_FINDINGS)
            )
        ).scalars().all()
        gap_rows = (
            await session.execute(
                select(ResearchGap)
                .where(ResearchGap.research_run_id == run.id)
                .order_by(ResearchGap.question_key.asc().nulls_last(), ResearchGap.created_at)
                .limit(80)
            )
        ).scalars().all()

    section_override = {
        q.key: getattr(q, "report_section", None) for q in getattr(plan, "questions", ())
    }
    questions = [
        pr.QuestionView(
            key=row.question_key,
            text=row.text,
            domain=row.domain,
            report_section=section_override.get(row.question_key),
            contract_status=row.contract_status,
            unresolved_reason=row.unresolved_reason,
            missing=tuple((row.contract_detail_json or {}).get("missing") or ()),
            why_it_matters=row.why_it_matters,
            blocking=bool(row.blocking),
            referenced_finding_ids=tuple(
                str(step.get("referenced_finding_id"))
                for step in (row.acquisition_log_json or [])
                if step.get("rung") == "ownership" and step.get("referenced_finding_id")
            ),
        )
        for row in question_rows
    ]
    superseded_fields: dict[str, list[str]] = {}
    for item in supersessions or []:
        label = item.get("field_label") or item.get("field")
        if item.get("finding_id") and label:
            superseded_fields.setdefault(str(item["finding_id"]), []).append(str(label))
    findings = [
        pr.FindingView(
            finding_id=str(row.id),
            statement=row.statement,
            domain=row.domain,
            question_key=row.question_key,
            evidence_ids=tuple(row.evidence_ids_json or ()),
            calculation_ids=tuple(row.calculation_ids_json or ()),
            source_kinds=tuple(row.source_kinds_json or ()),
            confidence=row.confidence,
            direction=row.direction,
            period_key=row.period_key,
            references=tuple(str(r) for r in (row.references_finding_ids_json or ())),
            source_published_at=(
                row.source_published_at.isoformat() if row.source_published_at else None
            ),
            superseded_by=(
                str(row.superseded_by_finding_id) if row.superseded_by_finding_id else None
            ),
            superseded_fields=tuple(superseded_fields.get(str(row.id), ())),
        )
        for row in finding_rows
    ]
    gaps = [
        pr.GapView(
            description=row.description,
            question_key=row.question_key,
            knowledge_state=row.knowledge_state,
            kind="platform_evidence_gap",
            reconciliation_status=row.reconciliation_status,
            addressed_by_finding_ids=tuple(
                str(f) for f in ((row.reconciliation_json or {}).get("finding_ids") or ())
            ),
            reconciliation_reasons=tuple(
                str(r) for r in ((row.reconciliation_json or {}).get("reasons") or ())
            ),
        )
        for row in gap_rows
        if row.status in (ledger.GAP_OPEN, ledger.GAP_ACCEPTED)
        and row.reconciliation_status not in ledger.RECONCILED_HIDDEN
    ]
    acquired = [
        {"kind": ref.source_kind, "source_ref": ref.source_ref, "tier": ref.source_tier}
        for refs in getattr(loop_result, "evidence_by_question", {}).values()
        for ref in refs
        if ref is not None
    ]
    findings, withheld = pr.screen(findings)
    web_support: dict[str, Any] = {}
    if web_context:
        # Open-web W5: which cited ids are WEB documents, and of what class and origin —
        # read from what the platform stored, never from page text. Failure costs the
        # additive web blocks, not the report.
        try:
            from app.services.web_research import trust

            async with session.begin_nested():
                support = await trust.resolve_support(
                    session,
                    sorted({e for f in findings for e in f.evidence_ids if e}),
                    company_id=getattr(company, "id", None),
                )
            web_support = {item.evidence_id: item.to_dict() for item in support if item.web}
        except Exception:  # noqa: BLE001 - the additive blocks are optional
            web_support = {}
    # The user's own thesis words are shown, never allowed to block the report: the
    # same neutralisation V2 applies to text it copies into its memo.
    safe_thesis = (
        {
            key: (neutralize_forbidden_terms(value) if isinstance(value, str) else value)
            for key, value in thesis.items()
        }
        if thesis
        else None
    )
    inputs = pr.ReportInputs(
        subject={
            "ticker": getattr(company, "ticker", None),
            "exchange": getattr(company, "exchange", None),
            "name": getattr(company, "name", None),
            # Item 20 — only when the stage detector proved it, so every other
            # company's subject is unchanged.
            **({"stage": stage} if stage else {}),
        },
        questions=questions,
        findings=findings,
        gaps=gaps,
        acquired=acquired,
        thesis=safe_thesis,
        size_fit=thesis_size,
        findings_total=int(findings_total or 0),
        withheld_for_safety=withheld,
        commodity_rows=pr.commodity_rows(table_payloads.get("get_industry_series", [])),
        peer_rows=pr.peer_rows(
            table_payloads.get("get_peer_financials", []), getattr(company, "ticker", None)
        ),
        council_convened=council_convened,
        domain_cost=pr.domain_cost(
            [(row.domain, row.acquisition_log_json or []) for row in question_rows]
        ),
        web_support=web_support,
        web_context=web_context or None,
    )
    report = pr.assemble(inputs)
    report = await pr.edit(report, editor_client)
    report["withheld_for_safety"] = withheld
    hits = safety_terms.scan_value(report, path="professional_research")
    if hits:
        # Every component was screened; a hit here is in text this module composed or a
        # question text. It is not shown — an unscreened report is worse than none —
        # and the run SAYS so, with where the hit was.
        where = ", ".join(sorted({str(getattr(h, "path", h)) for h in hits})[:3])
        return None, (
            "the professional report was withheld: the final safety scan flagged "
            f"{len(hits)} passage(s) ({where})"
        )
    return report, None


async def _ledger_findings(session: Any, run: Any, *, limit: int) -> list[dict[str, Any]]:
    """Findings straight from the ledger, for a run whose Council did not convene."""
    from sqlalchemy import select

    from app.models.ledger import ResearchFinding
    from app.services.council_v2.inputs import FindingRef

    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    select(ResearchFinding)
                    .where(
                        ResearchFinding.research_run_id == run.id,
                        ResearchFinding.verification_status != "withdrawn",
                    )
                    # Deterministic. Every finding of one run shares the transaction's
                    # `created_at` on PostgreSQL, so ordering by it alone let the LIMIT
                    # pick a different subset each read; grouped by domain and question
                    # it is also the order a reader wants.
                    .order_by(
                        ResearchFinding.domain.asc().nulls_last(),
                        ResearchFinding.question_key.asc().nulls_last(),
                        ResearchFinding.created_at,
                        ResearchFinding.statement,
                    )
                    .limit(limit)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 - a read that fails must not cost the run
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            ref = FindingRef.from_row(row)
            item = ref.to_dict()
        except Exception:  # noqa: BLE001
            continue
        item["council_convened"] = False
        out.append(item)
    return out


async def _question_graph(session: Any, run: Any) -> list[dict[str, Any]]:
    """The question graph as a reader sees it. Bounded, and never raises."""
    from sqlalchemy import func, select

    from app.models.ledger import ResearchFinding, ResearchQuestion

    try:
        questions = (
            await session.execute(
                select(ResearchQuestion)
                .where(ResearchQuestion.research_run_id == run.id)
                .order_by(ResearchQuestion.priority, ResearchQuestion.question_key)
            )
        ).scalars().all()
        counts = dict(
            (
                await session.execute(
                    select(ResearchFinding.question_key, func.count())
                    .where(
                        ResearchFinding.research_run_id == run.id,
                        ResearchFinding.verification_status != "withdrawn",
                    )
                    .group_by(ResearchFinding.question_key)
                )
            ).all()
        )
    except Exception:  # noqa: BLE001
        return []
    graph: list[dict[str, Any]] = []
    for q in questions[:60]:
        detail = q.contract_detail_json or {}
        graph.append(
            {
                "question_key": q.question_key,
                "text": q.text,
                "domain": q.domain,
                "owner_role": q.owner_role,
                "origin": q.origin,
                "priority": q.priority,
                "blocking": q.blocking,
                "why_it_matters": q.why_it_matters,
                "depends_on": list(q.depends_on_json or []),
                "resolution_status": q.resolution_status,
                "contract_status": q.contract_status,
                "contract_missing": list(detail.get("missing") or []),
                "evidence_items": detail.get("items", 0),
                "distinct_sources": detail.get("distinct_sources", 0),
                "source_kinds": list(detail.get("kinds_present") or []),
                "findings": int(counts.get(q.question_key, 0)),
                "unresolved_reason": q.unresolved_reason,
                "acquisition_log": list(q.acquisition_log_json or [])[-8:],
            }
        )
    return graph


async def _rows_for_this_run(session: Any, run: Any, company: Any, model: Any) -> list[Any]:
    """Rows of ``model`` this run wrote, for the company under research.

    Correlated by company plus the run's own start time. Neither `research_tool_calls`
    nor `research_leads` carries a `research_runs` id — their job FK points at
    `research_jobs`, which is a different id space and is null on a non-durable run —
    so the window is what identifies them. Recorded plainly because it is an
    approximation: two runs for one company started within the same instant would
    overlap, which the front door does not permit today.
    """
    from sqlalchemy import select

    started = getattr(run, "started_at", None)
    stmt = select(model).where(model.company_id == company.id)
    if started is not None:
        stmt = stmt.where(model.created_at >= started)
    try:
        return list((await session.execute(stmt)).scalars().all())
    except Exception:  # noqa: BLE001 - telemetry must never end a run
        return []


async def _tool_call_consumption(session: Any, run: Any, company: Any) -> dict[str, Any]:
    """Units the TOOLS spent, summed from the rows the session already wrote.

    `documents_fetched` was hardcoded to 0 and web searches were not reported at all,
    so the single most expensive thing a V3 run can do — reach a vendor and fetch pages
    — was invisible in the run's own consumption record. The numbers existed the whole
    time, one table away, in `research_tool_calls.consumption_json`.
    """
    from app.models.research_tool_call import ResearchToolCall
    from app.services.consumption import VendorUsage, merge_vendor_usage

    totals: dict[str, Any] = {}
    # V3.17.9.2 — the per-vendor, per-cache-class attribution each tool recorded, merged
    # across the run. This is what makes the tool leg priceable: without it its tokens
    # arrive as an anonymous sum and can only be priced by guessing whose they were.
    vendors: tuple[VendorUsage, ...] = ()
    credits = 0.0
    credits_measured = False
    credits_unreported = False
    for row in await _rows_for_this_run(session, run, company, ResearchToolCall):
        payload = row.consumption_json or {}
        for unit, value in payload.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                totals[unit] = int(totals.get(unit, 0)) + int(value)
        parsed = [VendorUsage.from_dict(v) for v in (payload.get("by_vendor") or [])]
        vendors = merge_vendor_usage(vendors, tuple(v for v in parsed if v is not None))
        # Open-web W5: `search_web` bills in provider CREDITS (a float, never truncated),
        # and a call that reached the vendor with no credit figure marks them unreported.
        if "tavily_credits" in (payload.get("instrumented") or ()):
            credits_measured = True
            credits += float(payload.get("tavily_credits") or 0.0)
            credits_unreported = credits_unreported or "tavily_credits" in (
                payload.get("unreported") or ()
            )
    totals["by_vendor"] = vendors
    if credits_measured:
        totals["tavily_credits"] = credits
        totals["tavily_credits_measured"] = True
        totals["tavily_credits_unreported"] = credits_unreported
    return totals


def external_source_tier(url: str | None) -> str:
    """The source tier of a retrieved external URL.

    Three answers only, and the third is the honest default. A regulator host serves
    primary filings; anything else the external path reaches is secondary until
    something establishes otherwise. There is no attempt to recognise issuer domains
    here — `verified_issuer_sources` owns that question for the paths that have an
    issuer to check against, and inventing a second, weaker answer would let a
    lookalike domain be read as the company's own.
    """
    # V3.18.3 — one registry, `publisher_tiers`, shared with the evidence contracts.
    # This function used to know three regulator hosts and call everything else T5, so
    # a page fetched from a geological survey counted as a content farm.
    from app.services.sources.publisher_tiers import publisher_tier

    return publisher_tier(url)


def _claim_identity(lead: Any) -> tuple[float, str, str, str] | None:
    """What makes two leads the SAME fact, or ``None`` when that cannot be decided.

    ``None`` is the important return. The first version keyed on
    ``(claimed_value, claimed_period, claimed_scope)`` as raw strings, so every lead
    with a blank value collapsed onto ``("", "", "")`` and all but one were stamped
    "a higher-tier source established the same fact" about facts never established.
    Scope is routinely blank on this path, which made that common rather than rare.
    Found by review.

    Three changes: the value is NORMALISED (so "$1,343 million" and "1343000000" are
    one identity rather than two), the metric is part of the key (so two unrelated
    figures that happen to be equal are not one fact), and a lead that cannot supply
    both a value and a period is not ranked at all.
    """
    from app.services.providers.leads import parse_number_candidates

    readings = parse_number_candidates(getattr(lead, "claimed_value", None))
    period = str(getattr(lead, "claimed_period", "") or "").strip().lower()
    if not readings or not period:
        return None
    metric = str(getattr(lead, "metric", "") or "").strip().lower()
    if not metric:
        # Derived from the claim's own words rather than invented: a lead with no
        # metric field is identified by the largest reading plus its own text, which
        # cannot collide with a different metric's claim.
        metric = (str(getattr(lead, "claim_text", "") or "")[:60]).strip().lower()
    return (
        max(readings, key=abs),
        period,
        str(getattr(lead, "claimed_scope", "") or "").strip().lower(),
        metric,
    )


async def _external_research(session: Any, run: Any, company: Any) -> dict[str, Any]:
    """The external path's provenance: what was claimed, what we fetched, what survived.

    Every lead is listed with its outcome, because a rejected lead is a research fact —
    the count of them per provider is what `verification_survival_rate` is computed
    from, and hiding them would present a vendor as more reliable than it is.

    A lead appears here whatever its status. **Only a `verified` one carries an
    evidence id**, and the payload says so per row rather than relying on the reader to
    infer it, so nothing here can be mistaken for canonical Evidence.
    """
    from app.models.research_lead import ResearchLeadRecord

    leads = await _rows_for_this_run(session, run, company, ResearchLeadRecord)
    if not leads:
        return {}

    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for lead in leads:
        by_status[lead.status] = by_status.get(lead.status, 0) + 1
        if lead.rejection_reason:
            by_reason[lead.rejection_reason] = by_reason.get(lead.rejection_reason, 0) + 1

    def _host(url: str | None) -> str | None:
        from urllib.parse import urlsplit

        try:
            return (urlsplit(url or "").hostname or "").lower() or None
        except ValueError:
            return None

    # Source hierarchy. Among the leads that VERIFIED, a primary regulator source
    # outranks a secondary one for the same fact — same value, same period, same scope.
    # The lower-tier source is kept and labelled corroboration rather than discarded:
    # a second independent source agreeing is worth recording, and dropping it would
    # lose information without improving the canonical choice.
    #
    # The provider's job here is DISCOVERY. It names URLs; it does not decide which
    # source this platform stands behind. On the live MRNA run it named both an SEC
    # exhibit and a news site, and whichever verified first became the evidence.
    canonical_by_claim: dict[tuple[float, str, str, str], Any] = {}
    for lead in leads:
        if not lead.promoted_evidence_id:
            continue
        key = _claim_identity(lead)
        if key is None:
            # Not rankable, so not ranked. A lead that cannot say what it measured is
            # left as its own citation rather than labelled corroboration for a fact
            # nothing established.
            continue
        best = canonical_by_claim.get(key)
        if best is None or tier_rank(external_source_tier(lead.fetched_url)) < tier_rank(
            external_source_tier(best.fetched_url)
        ):
            canonical_by_claim[key] = lead
    canonical_ids = {id(lead) for lead in canonical_by_claim.values()}

    return {
        "leads_discovered": len(leads),
        "leads_by_status": by_status,
        "leads_rejected_by_reason": by_reason,
        # Retrieved means WE HOLD THE BYTES, and the hash is the only proof of that.
        #
        # An earlier version also counted several rejection reasons, on the theory that
        # a gate which compared a claim against a document must have read one. That is
        # true of `value_mismatch` and its siblings — and they carry the hash anyway, so
        # the clause bought nothing — but it also counted `url_unreachable`, which is
        # set precisely when the fetch came back with nothing usable. So the one reason
        # the clause actually changed was the one where no retrieval happened, and the
        # panel reported it to a reader as "InvestingBuddy retrieved N sources itself".
        "sources_retrieved_by_investingbuddy": sum(
            1 for lead in leads if lead.fetched_content_hash
        ),
        "evidence_promoted": sum(1 for lead in leads if lead.promoted_evidence_id),
        "leads": [
            {
                "provider": lead.provider,
                "model": lead.model,
                "claim": (lead.claim_text or "")[:400],
                "url": lead.claimed_source_url,
                "host": _host(lead.claimed_source_url),
                "status": lead.status,
                "rejection_reason": lead.rejection_reason,
                "detail": (lead.rejection_detail or "")[:300] or None,
                "claimed_value": lead.claimed_value,
                "claimed_period": lead.claimed_period,
                "claimed_scope": lead.claimed_scope,
                "period_verified": bool(lead.period_verified),
                "scope_verified": bool(lead.scope_verified),
                "fetched_url": lead.fetched_url,
                "content_hash": (lead.fetched_content_hash or "")[:16] or None,
                # The load-bearing field. Present only on a verified lead, and only a
                # lead with one contributed anything a finding may cite.
                "evidence_id": lead.promoted_evidence_id,
                "is_canonical_evidence": bool(lead.promoted_evidence_id),
                # The tier of the source this platform actually retrieved.
                "source_tier": external_source_tier(lead.fetched_url),
                # True when a HIGHER-tier source established the same fact in this run,
                # so this one stands as corroboration rather than as the citation.
                "corroborating_only": bool(
                    lead.promoted_evidence_id and id(lead) not in canonical_ids
                ),
            }
            for lead in leads[:40]
        ],
        "note": (
            "Provider claims and what InvestingBuddy did with them. A lead is NOT "
            "evidence: only a row with an evidence_id was retrieved and verified "
            "against bytes this platform fetched itself."
        ),
    }


def _consumption(
    model_routing: ModelRouting,
    loop_result: LoopResult,
    summary: Any,
    verdict: ChairVerdict,
    challenge_result: Any,
    cfg: Any = None,
    tool_units: dict[str, Any] | None = None,
    web_units: Any = None,
) -> dict[str, Any]:
    """What the run actually consumed, and what it produced that was worth consuming it.

    ``cost_per_verified_useful_finding`` is the metric the acceptance phase asks for, and
    it is ``None`` whenever either term is unknown — never zero. Two different unknowns
    are kept apart in the payload rather than collapsed: an **unpriced** run and a run
    that produced **nothing** are different failures, and only one of them is about the
    provider.

    A "useful" finding is one that survived to the Council and was not withdrawn by the
    Red Team. Counting every statement the model emitted would make a run that produced
    forty retracted claims look productive.
    """
    from app.services.consumption import (
        ConsumptionUnits,
        PriceBook,
        VendorUsage,
        derive_cost,
        merge_vendor_usage,
        price_book_from_settings,
    )

    units = ConsumptionUnits(
        instrumented=frozenset({"model_calls", "model_input_tokens", "model_output_tokens"})
    )
    by_vendor: dict[str, dict[str, int]] = {}
    # V3.17.9.2 — the same numbers, split by the vendor that BILLS them and by cache
    # class. `by_vendor` above is the reader-facing summary and has been here since
    # V3.11; this is the priceable form, and it is what reaches the consumption row.
    vendor_usage: tuple[VendorUsage, ...] = ()
    seen: set[int] = set()
    for slot in model_routing.slots.values():
        client = slot.client
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        usage = getattr(client, "consume_usage", None)
        popped = usage() if callable(usage) else None
        if popped is None:
            continue
        calls = int(getattr(popped, "calls", 0) or 0)
        prompt = int(getattr(popped, "prompt_tokens", 0) or 0)
        completion = int(getattr(popped, "completion_tokens", 0) or 0)
        units = units + ConsumptionUnits(
            model_calls=calls,
            model_input_tokens=prompt,
            model_output_tokens=completion,
            instrumented=frozenset({"model_calls", "model_input_tokens", "model_output_tokens"}),
        )
        vendor_usage = merge_vendor_usage(
            vendor_usage,
            (
                VendorUsage(
                    vendor=slot.vendor or "unknown",
                    calls=calls,
                    input_tokens=prompt,
                    cached_input_tokens=int(
                        getattr(popped, "cached_prompt_tokens", 0) or 0
                    ),
                    output_tokens=completion,
                    # Defaults to False on any client that predates the field, which is
                    # the safe direction: unreported means unpriceable, not free.
                    cache_reported=bool(getattr(popped, "cache_reported", False)),
                ),
            ),
        )
        bucket = by_vendor.setdefault(
            slot.vendor or "unknown", {"calls": 0, "input": 0, "output": 0}
        )
        bucket["calls"] += calls
        bucket["input"] += prompt
        bucket["output"] += completion

    # Prices are CONFIGURATION. With none supplied this stays `None` — unpriced, never
    # free, because an unpriced provider reported as costless is how a benchmark picks
    # the wrong one.
    # The search leg's spend counts too.
    #
    # `units` accumulated only from routing slots, so `derive_cost` excluded the
    # research provider's tokens entirely — and they were not listed as unpriced
    # either, because they were not in the instrumented set. The commit that surfaced
    # them quantified the problem as "a search that spent 27k input tokens was recorded
    # as costing nothing", and then costed them at zero one field over. Found by review.
    tool_model = tool_units or {}
    if any(
        tool_model.get(k) for k in ("model_calls", "model_input_tokens", "model_output_tokens")
    ):
        units = units + ConsumptionUnits(
            model_calls=int(tool_model.get("model_calls", 0) or 0),
            model_input_tokens=int(tool_model.get("model_input_tokens", 0) or 0),
            model_output_tokens=int(tool_model.get("model_output_tokens", 0) or 0),
            instrumented=frozenset(
                {"model_calls", "model_input_tokens", "model_output_tokens"}
            ),
        )
        # V3.17.9.2 — the research provider's own tokens, attributed to the vendor the
        # TOOL CALL recorded. A leg whose tool named no vendor contributes no attributed
        # usage, so its tokens stay outside every priced class and the run reports an
        # unknown cost rather than billing them at some other vendor's rate.
        vendor_usage = merge_vendor_usage(
            vendor_usage, tuple(tool_model.get("by_vendor") or ())
        )

    # Open-web W5: the company web stage's units (searches, credits, fetches, bytes,
    # expansion tokens) join the run's cost basis. A Tavily call with no credit figure
    # arrives ``unreported``, which makes the whole cost unknown — never a subtotal.
    if web_units is not None:
        vendor_usage = merge_vendor_usage(vendor_usage, tuple(web_units.by_vendor))
        units = units + replace(web_units, by_vendor=())
    # The Investigator's own `search_web` calls are provider searches too (credits).
    # Only the credits are added here: the calls are added from the same tool rows by
    # `company_research_service._v3_consumption_units`, and counting them twice would
    # double the run's searches.
    if (tool_units or {}).get("tavily_credits_measured"):
        units = units + ConsumptionUnits(
            tavily_credits=float((tool_units or {}).get("tavily_credits", 0.0) or 0.0),
            instrumented=frozenset({"tavily_credits"}),
            unreported=(
                frozenset({"tavily_credits"})
                if (tool_units or {}).get("tavily_credits_unreported")
                else frozenset()
            ),
        )

    units = replace(units, by_vendor=vendor_usage)

    # ONE price book, read through ONE function. V3.17.9.2.
    #
    # This block used to build its own `PriceBook` from `v3_price_usd_per_million_*`
    # while `consumption_recorder` built a different one from `v3_price_per_million_*` —
    # two setting families, one letter apart, for the same number. Configuring either
    # produced a cost in one place and silence in the other. The V3.11 pair is kept as a
    # fallback so an environment that set it is not silently unpriced.
    prices = price_book_from_settings(cfg)
    if prices.is_empty:
        prices = PriceBook(
            usd_per_million_input_tokens=getattr(
                cfg, "v3_price_usd_per_million_input_tokens", None
            ),
            usd_per_million_output_tokens=getattr(
                cfg, "v3_price_usd_per_million_output_tokens", None
            ),
        )
    cost = derive_cost(units, prices)
    useful = max(
        0,
        summary.findings_total - int((challenge_result.withdrawn_findings or 0)),
    )
    return {
        "tool_calls": loop_result.tool_calls,
        "tasks_run": loop_result.tasks_run,
        "rounds": len(loop_result.rounds),
        # Summed from the tool-call rows, not assumed. These were the run's most
        # expensive units and the record reported none of them.
        "web_search_calls": (tool_units or {}).get("web_search_calls", 0),
        "url_fetch_calls": (tool_units or {}).get("url_fetch_calls", 0),
        "documents_fetched": (tool_units or {}).get("documents_downloaded", 0),
        # The provider legs' own token spend. The external research provider is NOT a
        # routing slot, so its tokens never reached `model_by_vendor` — a search that
        # spent 27k input tokens was recorded as costing nothing.
        "provider_model_calls": (tool_units or {}).get("model_calls", 0),
        "provider_input_tokens": (tool_units or {}).get("model_input_tokens", 0),
        "provider_output_tokens": (tool_units or {}).get("model_output_tokens", 0),
        "provider_cached_tokens": (tool_units or {}).get("cached_tokens", 0),
        "elapsed_seconds": round(loop_result.elapsed_seconds, 3),
        "findings_total": summary.findings_total,
        "findings_withdrawn_by_red_team": challenge_result.withdrawn_findings,
        "verified_useful_findings": useful,
        # The same count under its true name: nothing in the pipeline VERIFIES a
        # finding, so "verified useful" overstated it. The old key stays for old readers.
        "useful_findings": useful,
        "gaps_open": summary.gaps_open,
        "model": units.to_dict(),
        "model_by_vendor": by_vendor,
        "estimated_cost_usd": cost.estimated_usd,
        "unpriced_units": list(cost.unpriced_units),
        "cost_per_verified_useful_finding": (
            (cost.estimated_usd / useful) if (cost.estimated_usd is not None and useful) else None
        ),
        "cost_per_useful_finding": (
            (cost.estimated_usd / useful) if (cost.estimated_usd is not None and useful) else None
        ),
        "cost_per_company_research_run": cost.estimated_usd,
        # Reader-facing copy of the web stage's units; they are ALREADY inside "model"
        # above (the cost basis), so nothing downstream sums this key.
        **({"web_stage": web_units.to_dict()} if web_units is not None else {}),
        **(
            {
                "tavily_credits": float(tool_units.get("tavily_credits", 0.0) or 0.0),
                "tavily_credits_unreported": bool(tool_units.get("tavily_credits_unreported")),
            }
            if tool_units and tool_units.get("tavily_credits_measured")
            else {}
        ),
        "price_source": getattr(cfg, "v3_price_source", "") or None,
        "cost_basis": (
            "estimated_from_configured_prices" if cost.estimated_usd is not None else None
        ),
        "cost_is_unknown_because": (
            "no price is recorded for any provider; a cost of unknown is never zero"
            if cost.estimated_usd is None
            else None
        ),
        "chair_used_a_model": not verdict.deterministic_fallback,
    }


def attach_to_report(report: Any, outcome: V3ResearchOutcome) -> Any:
    """Write the V3 state onto an already-assembled report.

    Additive: an existing ``source_summary_json`` is preserved and one key is added, so a
    report written before V3 and one written after differ by exactly that key. The report
    API already returns the column and the frontend already tolerates keys it does not
    read.
    """
    summary = dict(getattr(report, "source_summary_json", None) or {})
    payload = outcome.to_dict()
    reconciliation = payload.get("gap_reconciliation")
    if isinstance(reconciliation, dict) and reconciliation.get("closing_findings") is not None:
        # The V2 report was assembled BEFORE this research ran; its own gap statements
        # are labelled here against the V3 findings, so the page can drop the ones a
        # finding answers. Never raises: an unlabelled V2 item is shown as V2 wrote it.
        try:
            reconciliation = {
                **reconciliation,
                "v2": _label_v2(report, summary, reconciliation["closing_findings"]),
            }
            payload["gap_reconciliation"] = reconciliation
        except Exception:  # noqa: BLE001 - labels are additive
            pass
    summary[SOURCE_SUMMARY_KEY] = payload
    report.source_summary_json = summary
    try:
        _attach_web_catalysts(report, payload.get("web_context"))
    except Exception:  # noqa: BLE001 - the additive catalyst rows must never cost the report
        pass
    return report


def _attach_web_catalysts(report: Any, web_context: Any) -> None:
    """Open-web W5 (spec §17.4): the V2 ``news_catalyst_discovery`` section reads the
    catalyst-family web documents the stage stored, ADDITIVELY.

    The V2 report was assembled before the web stage ran, so its catalyst section holds
    the V2 providers' events only. When the stage stored catalyst documents, one extra
    key — ``web_catalyst_evidence`` — is written into that section's JSON; no existing
    key is touched, a report with no such documents is byte-identical, and every
    third-party string passes the same neutralisation V2 applies to external headlines.
    """
    import json

    from app.schemas.catalyst import neutralize_forbidden_terms

    evidence = (web_context or {}).get("catalyst_evidence") if isinstance(
        web_context, dict) else None
    if not evidence:
        return
    markdown = str(getattr(report, "content_markdown", None) or "")
    start = markdown.find("```json")
    end = markdown.rfind("```")
    if start == -1 or end <= start:
        return
    head = start + len("```json")
    content = json.loads(markdown[head:end].strip())
    section = content.get("news_catalyst_discovery") if isinstance(content, dict) else None
    if not isinstance(section, dict) or "web_catalyst_evidence" in section:
        return
    from app.services.pipeline.professional_research import SOURCE_CLASS_LABELS
    from app.services.web_research.stage import safe_url

    rows = [
        {
            "title": neutralize_forbidden_terms(item.get("title")),
            # Dropped, not neutralised, when it contains a gate term (C-H2): this block
            # is written after the safety scan ran, so nothing else would catch it.
            "url": safe_url(item.get("url")),
            "domain": neutralize_forbidden_terms(item.get("domain")),
            "source_class": item.get("source_class"),
            "source_class_label": SOURCE_CLASS_LABELS.get(
                str(item.get("source_class")), "Web document"
            ),
            "published_at": item.get("published_at"),
            "published_at_source": item.get("published_at_source"),
            "origin": neutralize_forbidden_terms(item.get("origin_key")),
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    section["web_catalyst_evidence"] = {
        "value": rows,
        "total": len(rows),
        "provenance": "web_search",
        "note": (
            "Recent-event documents found by live web search and stored by the platform. "
            "Each is labelled by its source class; none is a filing, and none is a "
            "recommendation. Dates are the document's own where the page states one."
        ),
    }
    report.content_markdown = (
        markdown[:head] + "\n" + json.dumps(content, indent=2, default=str) + "\n"
        + markdown[end:]
    )


def _v2_report_content(report: Any) -> dict[str, Any]:
    """The V2 report content, read the way the page reads it (a fenced JSON block)."""
    import json

    markdown = str(getattr(report, "content_markdown", None) or "")
    start = markdown.find("```json")
    end = markdown.rfind("```")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(markdown[start + len("```json"):end].strip())
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _label_v2(
    report: Any, summary: dict[str, Any], closing_findings: list[dict[str, Any]]
) -> dict[str, Any]:
    from app.services.pipeline import gap_reconciliation

    content = _v2_report_content(report)
    missing_section = content.get("missing_information") or {}
    missing = (missing_section.get("missing_items") or {}) if isinstance(
        missing_section, dict) else {}
    missing_items = missing.get("value") if isinstance(missing, dict) else missing
    concerns: list[dict[str, Any]] = []
    for agent in ((summary.get("llm_council") or {}).get("agents") or []):
        if not isinstance(agent, dict):
            continue
        for index, gap in enumerate(agent.get("risks_or_gaps") or []):
            item = gap.get("item") if isinstance(gap, dict) else None
            if item:
                # agent + index: a stable handle the page can match besides the text.
                concerns.append(
                    {"text": str(item), "agent": agent.get("agent_name"), "index": index}
                )
    return gap_reconciliation.label_v2_items(
        missing_items=[m for m in (missing_items or []) if isinstance(m, (dict, str))],
        concerns=concerns,
        closers=gap_reconciliation.closers_from_payload(closing_findings),
    )


__all__ = [
    "SOURCE_SUMMARY_KEY",
    "V3ResearchOutcome",
    "attach_to_report",
    "run_v3_research",
]
