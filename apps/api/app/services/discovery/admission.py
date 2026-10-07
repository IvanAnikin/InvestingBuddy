"""Admission rules A1–A4 for web-discovered leads — open-web W6b (spec §6.2).

A candidate enters the **web-discovered** shortlist only when all four hold:

========  ========================================================  ============================
Rule      Requirement                                               Persisted failure code
========  ========================================================  ============================
A1        a ``web_search_results`` row from an EXECUTED provider    ``no_search_provenance``
          call whose FETCHED page named the company
A2        ``verify_identity`` passed: exchange directory, regulator  ``identity_unverified``
          or the issuer's own domain (existing reasons are kept as
          the detail: ``not_in_exchange_directory`` …)
A3        a fetched and EXTRACTED passage — never a snippet — where  ``theme_evidence_missing``
          the company mention and a theme term co-occur in ONE
          paragraph or table row, from a source class at least
          trade publication / association / government / issuer
A4        the existing hard constraints (``constraints.py``)         existing: EXCLUDED /
                                                                     ELIGIBLE_UNVERIFIED
========  ========================================================  ============================

THEME EVIDENCE IS NOT CORROBORATION (owner decision 2026-10-07)
================================================================
A3 asks whether fetched evidence shows the company takes part in the thesis. A page on a
domain the platform has INDEPENDENTLY established as the issuer's (``official_domains``) may
carry it: what a company says it does is evidence of what it does. That page is ISSUER-ORIGIN
and never counts as independent corroboration. Every decision therefore records two things:

``theme_evidence.status``  ``verified_issuer`` · ``independent`` · ``multiple`` · ``insufficient``
``corroboration``          ``issuer_only`` · ``independently_corroborated`` · ``unavailable``

A domain is never trusted because it resembles the company's name, a search provider calls it
official, a model says so, or the page claims it — see ``official_domains``.

Outcomes: A1–A4 pass → ``admitted``. A3 missing → ``also_surfaced`` — the platform's
``eligible_unverified(theme)``: shown in the "also surfaced" list and NEVER filling the
quota. A1 or A2 failing → ``rejected`` with the code.

**An LLM naming a company admits nothing:** A1 and A3 both require bytes the platform
fetched. A lead from the curated registry, a held company or model recall is NOT rejected
for lacking A1 — it keeps its true label (``curated_registry`` / ``platform_registry`` /
``model_recall``) and is recorded as ``labelled`` — but a lead labelled ``search`` that
cannot show its executed query is rejected ``no_search_provenance``: the label is a claim
about the network and the network is checked.

Pure functions and plain dicts: nothing here touches a database or a network, so the rules
are unit-testable without either.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.web_research.classify import (
    SC_COMPANY_PRESS_RELEASE,
    SC_COMPANY_WEB_PAGE,
    SC_EXCHANGE_ANNOUNCEMENT,
    SC_GOVERNMENT_PUBLICATION,
    SC_INDUSTRY_ASSOCIATION,
    SC_INVESTOR_PRESENTATION,
    SC_ISSUER_FILING,
    SC_MAJOR_FINANCIAL_PRESS,
    SC_REGULATOR_PUBLICATION,
    SC_REGULATORY_FILING,
    SC_SPECIALIST_AGENCY,
    SC_STANDARDS_BODY,
    SC_STATISTICAL_AGENCY,
    SC_TRADE_PUBLICATION,
)

ADMISSION_VERSION = "w6b.1"

CODE_NO_SEARCH_PROVENANCE = "no_search_provenance"
CODE_IDENTITY_UNVERIFIED = "identity_unverified"
CODE_THEME_EVIDENCE_MISSING = "theme_evidence_missing"
#: A model-recalled lead that, with live search available, no executed search plus fetched
#: page corroborated: shown in "also surfaced", never in the quota.
CODE_RECALL_NOT_CORROBORATED = "recall_not_corroborated"
#: A search lead on a venue with no official directory, whose listing no official page
#: confirmed: ``eligible_unverified(identity)`` — shown, never admitted (a page's own text
#: can never verify the listing of the company it names).
CODE_NO_OFFICIAL_DIRECTORY = "no_official_directory"
#: The venue HAS an official directory but it could not be read (an outage): "cannot check",
#: which is not "not listed" and not "no directory" — the reason is kept in the code.
CODE_DIRECTORY_UNAVAILABLE = "directory_unavailable"

STATE_ADMITTED = "admitted"
#: ``eligible_unverified(theme)``: identity verified, theme evidence missing.
STATE_ALSO_SURFACED = "also_surfaced"
STATE_REJECTED = "rejected"
#: A registry / held / recall lead: not gated by A1, labelled by its true source.
STATE_LABELLED = "labelled"

#: Source classes at least as good as a trade publication (spec §6.2 A3).
A3_SOURCE_CLASSES: frozenset[str] = frozenset(
    {
        SC_TRADE_PUBLICATION,
        SC_INDUSTRY_ASSOCIATION,
        SC_GOVERNMENT_PUBLICATION,
        SC_REGULATOR_PUBLICATION,
        SC_STATISTICAL_AGENCY,
        SC_SPECIALIST_AGENCY,
        SC_STANDARDS_BODY,
        SC_MAJOR_FINANCIAL_PRESS,
        # issuer material
        SC_ISSUER_FILING,
        SC_REGULATORY_FILING,
        SC_EXCHANGE_ANNOUNCEMENT,
        SC_COMPANY_PRESS_RELEASE,
        SC_INVESTOR_PRESENTATION,
        SC_COMPANY_WEB_PAGE,
    }
)

#: Hosts whose content ANYONE can publish: open press-release wires and user-content
#: platforms. A passage there is not evidence about a company (a wire release is the
#: issuer's words, but a stranger can post one), so it never carries A3 — whatever class the
#: classifier gave the page (security review M3).
A3_EXCLUDED_HOSTS: tuple[str, ...] = (
    "globenewswire.com", "businesswire.com", "prnewswire.com", "prnewswire.co.uk",
    "accesswire.com", "newsfilecorp.com", "cision.com", "mynewsdesk.com", "ots.at", "dgap.de",
    "eqs-news.com", "newswire.ca", "einpresswire.com", "prlog.org", "openpr.com",
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "wixsite.com", "github.io",
    "tumblr.com", "quora.com", "reddit.com", "linkedin.com", "facebook.com", "x.com",
    "twitter.com", "sites.google.com", "weebly.com", "notion.site", "pages.dev", "netlify.app",
    "vercel.app", "stocktwits.com", "seekingalpha.com",
    # Shared hosts: whoever rents a subdomain can publish anything on it.
    "myshopify.com", "herokuapp.com", "web.app", "firebaseapp.com", "webflow.io", "gitlab.io",
    "squarespace.com", "wixstudio.io", "carrd.co", "strikingly.com", "godaddysites.com",
)
#: Student / personal pages on academic domains are not scholarship.
A3_EXCLUDED_SUFFIXES: tuple[str, ...] = (".edu", ".edu.au", ".ac.uk", ".ac.jp", ".ac.at")


def a3_host_excluded(domain: str | None) -> bool:
    host = (domain or "").lower().strip(".").removeprefix("www.")
    if not host:
        return False
    if any(host == h or host.endswith("." + h) for h in A3_EXCLUDED_HOSTS):
        return True
    return host.endswith(A3_EXCLUDED_SUFFIXES)


def is_acceptable_source(
    source_class: str | None, domain: str | None, hosts: Iterable[str] = ()
) -> bool:
    """A source that may carry A3 / a THEME item: an acceptable class on a host anyone
    cannot publish to. ``hosts`` are the OTHER hosts the page is known by (the search
    result's, the canonical claim): the class comes from the final URL, so every host the
    page passed through is checked, not only one."""
    return source_class in A3_SOURCE_CLASSES and not any(
        a3_host_excluded(h) for h in (domain, *hosts)
    )


MODE_SEARCH = "search"
MODE_MODEL_RECALL = "model_recall"

DIM_THEME = "theme_relevance"
DIM_CATALYSTS = "catalysts"
DIM_DOWNSIDE = "principal_downside"


def is_a3_passage(entry: Mapping[str, Any]) -> bool:
    """A mention entry that satisfies A3: a theme term in the mention's own passage, from
    an acceptable source class, in a document that is not injection-suspect."""
    return bool(
        entry.get("theme_terms")
        and is_acceptable_source(
            entry.get("source_class"), entry.get("domain"), entry.get("hosts") or ()
        )
        and entry.get("passage_ref")
        and not entry.get("injection_suspect")
    )


def a3_evidence_ids(mentions: Iterable[Mapping[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(m["evidence_id"]) for m in mentions if is_a3_passage(m) and m.get("evidence_id")
        )
    )


#: Classes whose text is the ISSUER's own words. Evidence of what the company says it does;
#: never independent corroboration of it.
ISSUER_ORIGIN_CLASSES: frozenset[str] = frozenset(
    {
        SC_ISSUER_FILING,
        SC_REGULATORY_FILING,
        SC_EXCHANGE_ANNOUNCEMENT,
        SC_COMPANY_PRESS_RELEASE,
        SC_INVESTOR_PRESENTATION,
        SC_COMPANY_WEB_PAGE,
    }
)

THEME_VERIFIED_ISSUER = "verified_issuer"
THEME_INDEPENDENT = "independent"
THEME_MULTIPLE = "multiple"
THEME_INSUFFICIENT = "insufficient"

CORROBORATION_ISSUER_ONLY = "issuer_only"
CORROBORATION_INDEPENDENT = "independently_corroborated"
CORROBORATION_UNAVAILABLE = "unavailable"


def is_issuer_origin(entry: Mapping[str, Any]) -> bool:
    """A passage the ISSUER wrote: a page on a verified official domain, or an issuer class."""
    return bool(entry.get("issuer_origin")) or entry.get("source_class") in ISSUER_ORIGIN_CLASSES


def theme_evidence_summary(mentions: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """What the lead's A3 passages establish, and — separately — whether anything INDEPENDENT
    of the issuer supports it. Counts only passages that satisfy A3 (never a snippet)."""
    passages = [m for m in mentions if is_a3_passage(m)]
    issuer = [m for m in passages if is_issuer_origin(m)]
    independent = [m for m in passages if not is_issuer_origin(m)]
    from app.services.discovery.official_domains import registrable_domain

    # Independent PUBLISHERS: subdomains of one site are one publisher.
    domains = {
        registrable_domain(str(m["domain"])) or str(m["domain"]).lower()
        for m in independent
        if m.get("domain")
    }
    if not passages:
        status, corroboration = THEME_INSUFFICIENT, CORROBORATION_UNAVAILABLE
    elif len(domains) >= 2:
        status, corroboration = THEME_MULTIPLE, CORROBORATION_INDEPENDENT
    elif independent:
        status, corroboration = THEME_INDEPENDENT, CORROBORATION_INDEPENDENT
    else:
        status, corroboration = THEME_VERIFIED_ISSUER, CORROBORATION_ISSUER_ONLY
    return {
        "status": status,
        "corroboration": corroboration,
        "issuer_passages": len(issuer),
        "independent_passages": len(independent),
        "independent_domains": sorted(domains)[:6],
    }


@dataclass
class AdmissionDecision:
    state: str
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)
    codes: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    detail: str | None = None
    source_label: str | None = None
    #: ``web_search_results`` ids that surfaced a corroborated recall lead.
    surfaced_by: list[str] = field(default_factory=list)
    #: ``theme_evidence_summary`` of the lead's passages: what they establish, and whether
    #: anything independent of the issuer supports it. ``None`` only where A3 was not asked.
    theme_evidence: dict[str, Any] | None = None

    @property
    def admitted(self) -> bool:
        return self.state == STATE_ADMITTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": ADMISSION_VERSION,
            "state": self.state,
            "rules": self.rules,
            "codes": list(self.codes),
            "evidence_ids": list(self.evidence_ids),
            "detail": self.detail,
            "source_label": self.source_label,
            **({"surfaced_by": list(self.surfaced_by)} if self.surfaced_by else {}),
            **(
                {
                    "theme_evidence": dict(self.theme_evidence),
                    "corroboration": self.theme_evidence["corroboration"],
                }
                if self.theme_evidence
                else {}
            ),
        }


def search_lead_has_provenance(
    web: Mapping[str, Any] | None, executed_query_ids: Iterable[str]
) -> bool:
    """A1: the lead cites at least one sighting whose query is an EXECUTED search row.

    ``executed_query_ids`` is the set of ``web_search_queries.id`` the stage verified as
    executed (``executed=True``, a network call behind it). A sighting needs a fetched page
    too: a result URL the platform never fetched is a snippet, and a snippet names nobody.
    """
    if not web:
        return False
    executed = {str(i) for i in executed_query_ids}
    return any(
        str(s.get("query_id")) in executed and s.get("fetch_attempt_id")
        for s in web.get("sightings") or []
    )


def decide(
    *,
    discovery_mode: str | None,
    has_provenance: bool,
    identity_verified: bool,
    identity_reason: str | None = None,
    mentions: Sequence[Mapping[str, Any]] = (),
    lead_source: str | None = None,
) -> AdmissionDecision:
    """A1–A3 for one lead (A4 is the constraint evaluation that follows).

    A lead whose ``discovery_mode`` is not ``search`` is not gated by A1: it keeps its true
    label. Its A2 outcome is the pipeline's (identity is verified or the lead is rejected
    exactly as in V3.19), so this reports it as ``labelled`` and carries whatever theme
    evidence a search happened to find for it.
    """
    evidence = a3_evidence_ids(mentions)
    summary = theme_evidence_summary(mentions)
    if discovery_mode != MODE_SEARCH:
        return AdmissionDecision(
            state=STATE_LABELLED,
            rules={"A1": {"applies": False}, "A3": {"passed": bool(evidence)}},
            evidence_ids=evidence,
            source_label=lead_source or discovery_mode,
            theme_evidence=summary,
        )
    rules: dict[str, dict[str, Any]] = {"A1": {"passed": has_provenance}}
    if not has_provenance:
        rules["A1"]["code"] = CODE_NO_SEARCH_PROVENANCE
        return AdmissionDecision(
            STATE_REJECTED,
            rules,
            [CODE_NO_SEARCH_PROVENANCE],
            detail="no executed search with a fetched page names this company",
        )
    rules["A2"] = {"passed": bool(identity_verified)}
    if not identity_verified:
        rules["A2"].update(code=CODE_IDENTITY_UNVERIFIED, reason=identity_reason)
        codes = [CODE_IDENTITY_UNVERIFIED] + ([identity_reason] if identity_reason else [])
        return AdmissionDecision(
            STATE_REJECTED,
            rules,
            codes,
            detail="the listing was not confirmed by an official source",
        )
    rules["A3"] = {"passed": bool(evidence), "passages": len(evidence), "status": summary["status"]}
    if not evidence:
        rules["A3"]["code"] = CODE_THEME_EVIDENCE_MISSING
        return AdmissionDecision(
            STATE_ALSO_SURFACED,
            rules,
            [CODE_THEME_EVIDENCE_MISSING],
            detail="eligible_unverified(theme): no fetched passage ties the company to the theme",
            theme_evidence=summary,
        )
    return AdmissionDecision(STATE_ADMITTED, rules, [], evidence_ids=evidence,
                             theme_evidence=summary)


def decide_unverifiable_venue(
    mentions: Sequence[Mapping[str, Any]] = (), *, reason: str = CODE_NO_OFFICIAL_DIRECTORY
) -> AdmissionDecision:
    """A search lead whose listing no OFFICIAL source (directory, exchange host, regulator,
    registry-verified issuer page) confirmed. Never admitted; shown in "also surfaced"."""
    evidence = a3_evidence_ids(mentions)
    summary = theme_evidence_summary(mentions)
    rules: dict[str, dict[str, Any]] = {
        "A1": {"passed": True},
        "A2": {"passed": False, "code": CODE_IDENTITY_UNVERIFIED,
               "reason": reason},
        "A3": {"passed": bool(evidence), "passages": len(evidence), "status": summary["status"]},
    }
    return AdmissionDecision(
        STATE_ALSO_SURFACED, rules, [CODE_IDENTITY_UNVERIFIED, reason],
        evidence_ids=evidence, theme_evidence=summary,
        detail="eligible_unverified(identity): no official source confirms this listing",
    )


def decide_recall(
    *,
    has_provenance: bool,
    identity_verified: bool,
    identity_reason: str | None = None,
    mentions: Sequence[Mapping[str, Any]] = (),
    surfaced_by: Sequence[str] = (),
) -> AdmissionDecision:
    """A1–A3 for a model-recalled lead WHILE live web search is available.

    The product rule: a final candidate must not exist only because an LLM named it. A recall
    lead is admitted only if an executed search surfaced it (A1), its listing verified
    officially (A2) and a fetched passage ties it to the theme (A3). Otherwise it is demoted
    to ``also_surfaced`` with ``recall_not_corroborated`` plus the rule that failed — never
    silently dropped, never filling the quota. A failed A2 is a rejection, as in V3.19.
    The lead KEEPS ``discovery_mode="model_recall"`` as its origin; ``surfaced_by`` lists the
    search results that corroborate it.
    """
    evidence = a3_evidence_ids(mentions)
    summary = theme_evidence_summary(mentions)
    rules: dict[str, dict[str, Any]] = {"A1": {"passed": has_provenance}}
    if not identity_verified:
        rules["A2"] = {"passed": False, "code": CODE_IDENTITY_UNVERIFIED,
                       "reason": identity_reason}
        codes = [CODE_IDENTITY_UNVERIFIED] + ([identity_reason] if identity_reason else [])
        return AdmissionDecision(STATE_REJECTED, rules, codes, source_label=MODE_MODEL_RECALL,
                                 detail="the listing was not confirmed by an official source")
    rules["A2"] = {"passed": True}
    rules["A3"] = {"passed": bool(evidence), "passages": len(evidence), "status": summary["status"]}
    failed: list[str] = []
    if not has_provenance:
        rules["A1"]["code"] = CODE_NO_SEARCH_PROVENANCE
        failed.append(CODE_NO_SEARCH_PROVENANCE)
    if not evidence:
        rules["A3"]["code"] = CODE_THEME_EVIDENCE_MISSING
        failed.append(CODE_THEME_EVIDENCE_MISSING)
    if failed:
        return AdmissionDecision(
            STATE_ALSO_SURFACED, rules, [CODE_RECALL_NOT_CORROBORATED, *failed],
            source_label=MODE_MODEL_RECALL,
            detail="named by a model; no executed search and fetched page corroborate it",
            theme_evidence=summary,
        )
    out = AdmissionDecision(STATE_ADMITTED, rules, [], evidence_ids=evidence,
                            source_label=MODE_MODEL_RECALL,
                            detail="named by a model and corroborated by an executed search",
                            theme_evidence=summary)
    out.surfaced_by = list(surfaced_by)
    return out


def apply_a4(decision: Mapping[str, Any], *, status: str, reasons: Sequence[str]) -> dict[str, Any]:
    """Add rule A4 (the existing hard constraints) to a persisted decision.

    ``status`` is the eligibility status (``eligible`` / ``excluded`` / …). An EXCLUDED
    admitted lead becomes ``rejected`` with the existing constraint reason as its code;
    anything else keeps its A1–A3 state.
    """
    out = dict(decision)
    rules = dict(out.get("rules") or {})
    excluded = status == "excluded"
    rules["A4"] = {"passed": not excluded, "eligibility": status}
    out["rules"] = rules
    if excluded and out.get("state") == STATE_ADMITTED:
        out["state"] = STATE_REJECTED
        out["codes"] = [*(out.get("codes") or []), "excluded", *list(reasons)[:3]]
    out["eligibility"] = status
    return out


__all__ = [
    "A3_SOURCE_CLASSES",
    "ADMISSION_VERSION",
    "CODE_IDENTITY_UNVERIFIED",
    "CODE_DIRECTORY_UNAVAILABLE",
    "CODE_NO_OFFICIAL_DIRECTORY",
    "CODE_NO_SEARCH_PROVENANCE",
    "CODE_RECALL_NOT_CORROBORATED",
    "CODE_THEME_EVIDENCE_MISSING",
    "DIM_CATALYSTS",
    "DIM_DOWNSIDE",
    "DIM_THEME",
    "MODE_MODEL_RECALL",
    "MODE_SEARCH",
    "STATE_ADMITTED",
    "STATE_ALSO_SURFACED",
    "STATE_LABELLED",
    "STATE_REJECTED",
    "AdmissionDecision",
    "A3_EXCLUDED_HOSTS",
    "a3_evidence_ids",
    "a3_host_excluded",
    "is_acceptable_source",
    "apply_a4",
    "decide",
    "decide_recall",
    "decide_unverifiable_venue",
    "is_a3_passage",
    "search_lead_has_provenance",
]
