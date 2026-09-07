# InvestingBuddy V3 — Data and Evidence Architecture

**Status:** V3 TARGET unless marked `CURRENT`. Baseline `4b60e07`.

This document defines what the platform *knows*, how it knows it, and what it is
allowed to say. Everything here is InvestingBuddy-owned: no vendor may define an
entity, a source tier, a period, a scope or a fact.

---

## 1. The InvestingBuddy moat

External models are replaceable. These are not, and none of them may be delegated
to a provider:

legal entity and security identity · source registry and provenance · source
tiers · the raw artifact corpus · document/version/page/chunk lineage · canonical
financial facts · financial period semantics · Group/segment scope semantics ·
deterministic calculations · evidence verification · citation lineage ·
contradiction handling · the Research Ledger · Research Memory · industry
playbooks · research-completeness rules · peer relationships · Investment Council
methodology · the Red Team workflow · report generation · human-review
requirements.

---

## 2. Research Corpus

### 2.1 Why V2's storage is not a corpus (`CURRENT`)

`ExtractedDocument` (`apps/api/app/models/extracted_document.py:17-113`) is a good
*lineage* record: content hash as dedup identity, canonical URL, provider, source
type and tier, mime type, doc date, period, retrieval time, extraction method,
page count, honest status (`extracted` / `metadata_only` / `extraction_failed`),
and a `pipeline_version` so stale rows are never assumed compatible with current
parser semantics.

What it is not is a corpus:

- `excerpts_json` holds at most `primary_document_max_excerpts_per_document`
  (currently **20**, `config.py`) bounded excerpts of ≤1,200 chars each.
- Pages beyond `primary_document_max_pdf_pages` (**40**) plus a targeted
  `primary_document_max_supplemental_pdf_pages` (**12**) outline-driven look-beyond
  pass are never read at all.
- `blob_path` exists and is nullable, but nothing retrieves from it.
  **(`IMPLEMENTED IN V3` — [Slice 1.1](slices/V3.1-1-raw-artifact-store.md): raw
  bytes are now retained content-addressed behind an `ArtifactStore` interface and
  `blob_path` carries the retrieval key. Off by default behind `V3_CORPUS_ENABLED`.)**
- There is no page, section, chunk or table entity, and no search index.

So a 169-page annual report is fetched, ~20 excerpts survive, and the document is
functionally gone. Every later question about it triggers a re-fetch and re-parse
under a fresh timeout — or, more often, is simply answered "not available".

`IMPLEMENTED IN V3` (Slice 1.3), and measured on that exact document: the real
Pandora Annual Report 2025 now persists **40 of its 169 pages and 108,000
characters**, against the 19,232 characters twenty bounded excerpts would have
kept. It is `status = partial` and says so — the live extraction path reads 40
pages plus a targeted 12-page supplemental pass, and a corpus that could not
distinguish "we never read that page" from "that page does not say it" would be
worse than the excerpt model it replaces. Raising that bound is a reprocessing
run from the retained bytes, off the live path (Slice 1.7).

### 2.2 Target model

```
LegalEntity ──< ResearchDocument ──< ResearchDocumentVersion ──< DocumentPage
                                                              ├─< DocumentSection
                                                              ├─< DocumentChunk
                                                              └─< DocumentTable
```

| Entity | Purpose |
|---|---|
| `ResearchDocument` | The *logical* document: "Pandora Annual Report 2025". Stable across re-fetches and format changes. **(`IMPLEMENTED IN V3` — [Slice 1.2](slices/V3.1-2-research-corpus-schema.md).)** |
| `ResearchDocumentVersion` | One retrieved artifact: content hash, canonical URL, retrieval timestamp, mime type, byte size, blob pointer, extraction version, language, rights/access policy. Immutable. **(`IMPLEMENTED IN V3` — Slice 1.2. "Exactly one current version" is a partial unique index, not a convention.)** |
| `DocumentPage` | Page number, extracted text, char offsets, extraction method, confidence. **(`IMPLEMENTED IN V3` — [Slice 1.3](slices/V3.1-3-full-text-persistence.md), under a versioned `ResearchDocumentDerivation` so improving a parser never rewrites the record of what was retrieved.)** |
| `DocumentSection` | Heading path (e.g. `Financial statements > Segment information`), page span. Reuses the font-size heading-stack logic that already fixed CFR's segment scoping. **(`IMPLEMENTED IN V3` — Slice 1.3, with the typed scope triple.)** |
| `DocumentChunk` | The retrieval unit: text, offsets into page/section, token count, embedding reference, and denormalized filter keys (entity, period, scope, source tier, doc type, date). **(`IMPLEMENTED IN V3` — [Slice 1.5](slices/V3.1-5-document-aware-chunking.md). Structural chunking: never crosses a section boundary, prefers a page boundary, never a fixed window, no overlap. Identity is derived from the document's own coordinates, not assigned by an index.)** |
| `DocumentTable` | Structured grid + the geometric reconstruction metadata `financial_table_reconstructor.py` already produces. Tables are not chunks — a borderless five-year summary must survive as a grid. **(`IMPLEMENTED IN V3` — Slice 1.3. Verified on the real Pandora Annual Report 2025: the five-year summary came back as a grid with `['2025','2024','2023','2022','2021']`.)** |

**Version, not overwrite.** A restated annual report is a new
`ResearchDocumentVersion` of the same `ResearchDocument`. Old citations keep
resolving; the restatement is visible as a delta rather than as silent mutation.

`IMPLEMENTED IN V3` (Slice 1.2). Two retrievals are the same document only when
the documents themselves say so — same company, same declared kind, same declared
period; everything else falls back to a hash of the canonical URL and stays
separate. The two failure directions are not symmetric: splitting produces a
duplicate citation, merging attributes one report's pages to another report's
identity, so the fallback deliberately over-splits.

One consequence is worth naming. `document_period` refuses a bare year on purpose,
so an annual report — the document class where versioning matters most — would
otherwise never get a declared identity. `corpus.identity.annual_period_from`
reads a year **only** where it sits beside the document's own name for itself
("Annual Report 2025"), refuses when two candidate years disagree, and is
corpus-only: it never reaches a fact's period, and a test enforces that.

**Retention is a policy field, not a code branch.** Whether raw bytes may be
stored is `rights_policy` on the version (see
[SECURITY_DATA_GOVERNANCE_AND_LICENSING.md](SECURITY_DATA_GOVERNANCE_AND_LICENSING.md)).
A document that may not be retained keeps its lineage and loses its bytes — the
citation still resolves to the canonical URL.

`IMPLEMENTED IN V3` (Slice 1.1) as `app.services.corpus.policy.ArtifactPolicy` and
the `research_artifacts` policy columns: the six permissions of governance §6 are
columns rather than a JSON blob because they are *queried* as filters, `derived`
inherits the most restrictive input, and an unrecognised access class denies. A
`storage_key` of NULL beside a `storage_backend` of `'none'` is the durable shape
of "lineage kept, bytes not retained".

### 2.3 Search

Retrieval must support lexical **and** semantic **and** the filters, together:

`entity` · `source_tier` · `period` · `scope` · `document_type` · `date_range` ·
`language` · `access_class`.

> **Vector-only retrieval is forbidden in this domain.** "Revenue grew strongly"
> is semantically close to every revenue sentence in every period and every
> segment. A retrieval result that cannot be constrained to *Group, FY2025* is
> not evidence — it is a plausible-looking scope error waiting to be cited. Every
> chunk therefore carries denormalized period and scope keys, and the search
> interface takes them as first-class arguments, not as post-filters.

The **raw-artifact** backend already is (`IMPLEMENTED IN V3`, Slice 1.1):
`ArtifactStore` with in-memory, filesystem and Azure Blob implementations, an
injected client factory so the SDK import is lazy, and a test that parses every
corpus module to assert none of them imports a vendor SDK.

The **search** backend is behind the same kind of InvestingBuddy-owned interface:

```python
class SearchBackend(Protocol):
    async def index(self, chunks: Sequence[CorpusChunk]) -> IndexResult: ...
    async def search(self, query: CorpusQuery) -> list[CorpusHit]: ...
    async def delete(self, *, document_version_id: UUID) -> None: ...
```

`IMPLEMENTED IN V3` (Slice 1.4), as `CorpusQuery` / `CorpusFilters` /
`CorpusHit` / `SearchBackend` plus backend-neutral reciprocal rank fusion and a
complete in-memory reference backend. Two of this section's rules are now
executable rather than aspirational: a caller may request `LEXICAL` or `HYBRID`
and asking for semantic-only raises, and a query must either name the companies it
may return or say `allow_cross_entity=True` out loud.

Azure AI Search vs PostgreSQL + `pgvector` was `OPEN DECISION`
[#1](OPEN_DECISIONS.md#1-azure-ai-search-vs-postgresql--pgvector) and Slice 1.4
deliberately **stopped at it**. The user decided on 2026-09-05
([ADR-047](../DECISIONS.md)): **PostgreSQL**, because V3 must require no new paid SaaS
subscription. `IMPLEMENTED IN V3` (Slice 4.9) as `PostgresSearchBackend`.

Two things about the implementation are worth stating here rather than leaving in the
slice, because both are limitations a later reader would otherwise discover by
measuring:

**The lexical leg is the production path and needs no extension** — a GIN index over
`to_tsvector('simple', text)`, with every metadata filter in the same statement as the
`ORDER BY` and the `LIMIT`. That is the property §2.3 actually demands: a filter applied
after ranking returns the top matches of the wrong set.

**`pgvector` is not installed** on the PostgreSQL this project runs — verified, not
assumed. So the semantic leg is a *bounded rerank of the lexical candidates*, not a
nearest-neighbour search; it is off by default; `semantic_is_exhaustive` is `False`; and
the only embedding provider is deterministic and fake.
[ADR-053](../DECISIONS.md) records the amendment and the path back: enabling the
extension adds a typed column populated from the JSONB already held.

**De-indexing is an UPDATE, never a DELETE.** For this backend the chunk rows *are* the
corpus, so removing a superseded derivation from the index clears `indexed_at` and leaves
every span citable — old evidence ids keep resolving, and a stale reading of a document
stops reaching a researcher.

---

## 3. Entity master

### 3.1 The problem (`CURRENT`)

`companies` is keyed `UNIQUE (ticker, exchange)`
(`apps/api/app/models/company.py:46`). Consequences already observed live:

- Ticker/exchange collisions resolved to the wrong issuer (`BA` + LSE → BAE
  Systems vs Boeing) — fixed by special-casing, not by identity.
- Cross-listings and ADRs cannot be linked.
- Ticker changes rewrite history.
- Parent/subsidiary structure is unrepresentable, so consolidated vs subsidiary
  reporting cannot be reasoned about.

### 3.2 Target model

```
LegalEntity ──< Security ──< SecurityListing
     │              │
     │              └──< Identifier (ISIN, FIGI, CUSIP…)
     ├──< Identifier (LEI, CIK, national register id…)
     ├──< EntityRelationship  (parent_of, subsidiary_of, adr_of, predecessor_of…)
     ├──< ReportingScope      (Group, region, division)
     └──< BusinessSegment     (reported segment, effective-dated)
```

| Concept | Notes |
|---|---|
| `LegalEntity` | The issuer as a legal person. Jurisdiction, legal form, status, former names with effective dates. Identifiers hang off it rather than living on it. **(`IMPLEMENTED IN V3` — [Slice 2.1](slices/V3.2-1-entity-master.md).)** |
| `Security` | An instrument issued by the entity (ordinary shares, ADR). **(`IMPLEMENTED IN V3` — Slice 2.1. `adr`/`gdr` are named types rather than folded into `ordinary_share`, because a depositary receipt represents underlying shares at a ratio and per-share arithmetic must be able to refuse.)** |
| `SecurityListing` | A venue listing: MIC, ticker, quote currency, effective dates. **This is where `(ticker, exchange)` finally belongs.** **(`IMPLEMENTED IN V3` — Slice 2.1.)** |
| `Identifier` | `(scheme, value, entity_or_security_id, confidence, source, effective_from/to)`. Never a bare string on the parent row. **(`IMPLEMENTED IN V3` — Slice 2.1, as `entity_identifiers`, with checksums verified on write rather than trusted.)** |
| `EntityRelationship` | Typed, sourced, effective-dated, confidence-scored. Slice 2.4. |
| `ReportingScope` / `BusinessSegment` | The scope vocabulary that `fact_scope.py` already enforces in memory, given persistent identity so a segment can be tracked across periods and renamings. Slice 2.4. |

`IMPLEMENTED IN V3` (Slice 2.1) for the first four. Three things about it are
worth stating here because they are what make the model safe rather than merely
present:

**Identity is a key, and the key over-splits on purpose.** `entity_key` is derived
from the strongest identifier available at creation — `lei:` then `cik:` then
`register:` then, last, `listing:<venue>:<ticker>` — and it is never rewritten
when a stronger one arrives, because rewriting it would be an implicit merge. Only
the last of those can be reused by a different issuer, which is exactly why it is
the last resort.

**Identifiers are validated, not trusted.** A LEI failing ISO 7064 MOD 97-10 or an
ISIN failing its Luhn check digit is *refused*, never stored at a low confidence:
"recorded with confidence 0.3" does not stop the next reader from joining on it.
`checksum_verified` is a column, so a reader can tell a checked LEI from an
unchecked CIK. CUSIP is deliberately absent — it is licensed data, and adding the
scheme is a governance decision rather than a vocabulary edit.

**The three guarantees are indexes, not conventions.** Two live issuers cannot
share a ticker on one venue; two subjects cannot currently hold one LEI, CIK or
ISIN; an identifier attached to neither subject or to both is a CHECK violation.
All of them were exercised against real PostgreSQL 16, not only through the ORM.
And both discriminator columns are `NOT NULL` on purpose: PostgreSQL treats NULLs
as distinct in a unique index, so a nullable jurisdiction or venue would have
silently permitted the collision the index exists to prevent.

**Never silently merge.** Two candidate entities that cannot be resolved with
evidence stay separate and raise an `entity_ambiguous` gap. Merging on a name
match is how a research platform quietly attributes one company's numbers to
another.

`IMPLEMENTED IN V3` (Slice 2.1) as a structural property rather than a rule to
remember: there is **no unique index on `legal_name`** and **no resolve-by-name
function**, and a test fails if one appears. Two entities may legally share a name
in different jurisdictions, so a unique name would force the merge this model
exists to prevent. `entity_aliases` stores former and trade names as *evidence*
for the resolver in slice 2.3 to weigh beside identifiers — a name match is an
input to a decision, never the decision.

### 3.3 Migration from `companies`

`companies` is not dropped in V3. Each existing row gets a `LegalEntity` + a
`Security` + a `SecurityListing` derived from its `(ticker, exchange, name,
country, currency)`, and `companies.legal_entity_id` becomes a nullable FK. Every
existing report, citation and `company_id` reference keeps resolving. Details in
[MIGRATION_AND_COMPATIBILITY_PLAN.md](MIGRATION_AND_COMPATIBILITY_PLAN.md).

**Slice 2.1 touches `companies` not at all** — migration 026 adds five tables and
alters nothing, and no FK in it points at `companies`. The link and the backfill
land together in slice 2.2, so there is never a period in which two tables
disagree about which entity a company is.

---

## 4. Canonical facts and the promotion path

### 4.1 The promotion path

```
external agent claim / source candidate
        │                                    ← untrusted, may be hallucinated
        ▼
   ResearchLead                              ← persisted, attributed, NOT evidence
        │
        │  InvestingBuddy independently fetches the cited source,
        │  or verifies against a trusted structured API
        ▼
Canonical Source / ResearchDocumentVersion   ← real bytes, real hash, real lineage
        │
        ▼
   EvidenceFragment                          ← a located span in a real document
        │
        │  parse + validate (period, scope, unit, currency, scale, confidence)
        ▼
Canonical Fact  or  ResearchFinding
```

Each arrow is a gate with a recorded outcome. A lead whose cited URL 404s, or
whose claimed figure does not appear in the fetched document, is **retained as a
rejected lead** with the reason — this is exactly the "store rejected companies
and failed analyses, they are valuable learning data" rule applied to provider
output, and it is what makes `verification_survival_rate` measurable per provider.

### 4.2 `ResearchLead`

```
ResearchLead
  id, research_run_id, task_id
  provider, model, provider_task_id
  claim_text
  claimed_source_url, claimed_source_title, claimed_publisher, claimed_date
  claimed_value, claimed_unit, claimed_currency, claimed_period, claimed_scope
  status: pending | verifying | verified | rejected | unverifiable
  rejection_reason: url_unreachable | claim_not_in_source | period_mismatch |
                    scope_mismatch | value_mismatch | source_not_permitted |
                    duplicate | superseded
  promoted_evidence_id, promoted_fact_id
  created_at, verified_at
```

A lead is never rendered to a user as a finding. It may be rendered as *"a
research provider suggested X; we could not verify it"* — which is honest and
occasionally the most useful thing on the page.

### 4.3 Canonical facts

`ExtractedFact` already carries the right shape (`extracted_document.py:116-190`):
label, numeric/text value, unit, currency, scale, period, page, table location,
extraction method, confidence, `validation_status`, `needs_human_review`,
`is_active`, and typed `scope_type` / `scope_name` / `scope_key`.

V3 changes three things:

1. **Stable identity.** Facts get a durable id referenced by findings,
   calculations and citations across runs — replacing run-local `E1`/`E2`
   positional handles. **(`IMPLEMENTED IN V3` for *evidence* —
   [Slice 1.6](slices/V3.1-6-corpus-retrieval-service.md): `ev:<chunk_id>`,
   derived from the document's own coordinates, resolves back to the exact span,
   its page and — for a table — the grid. Existing reports keep their `E1`/`E2`
   handles, which continue to resolve. Facts themselves get their durable id in
   V3.2/V3.3.)**
2. **Entity, not company.** The owning key becomes `LegalEntity` + `ReportingScope`.
3. **Provenance to the chunk.** A fact points at a `DocumentChunk`/`DocumentTable`,
   so "show me where this number came from" renders the surrounding page.

---

## 5. Source / evidence model V3

V2 collapses several distinct ideas into one `sources` row
(`apps/api/app/models/source.py:14-42`): `source_type`, `publisher`, `url`,
`credibility_score`. That works while every fetch is from a known issuer or the
SEC. It breaks the moment a search provider is in the path.

V3 represents these **separately**:

| Dimension | Question it answers | Example |
|---|---|---|
| `transport` | Who delivered the bytes to us? | `exa`, `perplexity`, `sec_edgar`, `issuer_direct`, `user_upload` |
| `content_origin` | Who authored/published the content? | Reuters, the issuer, the SEC, an analyst house |
| `access_class` | What are we allowed to do with it? | `public_official`, `public_issuer`, `public_web`, `licensed_private`, `user_private`, `derived` |
| `source_tier` | How much trust does the *content* earn? | T1 primary issuer … T5 market commentary |
| `document` | Which artifact? | `ResearchDocumentVersion` |
| `evidence_fragment` | Which span of it? | page 14, chars 1200-1780 |
| `fact` | Which typed value? | Group revenue, FY2025, DKK m |
| `calculation` | Which derived value? | FY24→FY25 growth from two facts |
| `model_interpretation` | Which *reading* of the above? | "margin pressure is mix-driven" |

> **The transport must never set the tier.** Exa returning a Reuters article
> yields Reuters-tier content delivered by Exa. SEC EDGAR returning a 10-K yields
> primary-filing-tier content delivered by SEC. Collapsing the two would let a
> vendor upgrade or downgrade the trustworthiness of everything it touches, which
> is precisely the dependency V3 exists to avoid.

`model_interpretation` being a *distinct row type* is what lets the report say
"this is the council's reading" versus "this is the filing's number" without the
reader having to guess — the distinction ADR-041/ADR-042 established for council
output, given a durable representation.

---

## 6. Deterministic calculation engine

Arithmetic moves out of LLM prose. A `Calculation` record:

```
Calculation
  id, definition_id, definition_version
  formula                      # declarative, e.g. (revenue[t] / revenue[t-1]) - 1
  input_fact_ids[]             # typed references, not numbers pasted into a prompt
  periods[], scopes[], units[], currencies[]
  result_value, result_unit, result_currency
  validation_status: valid | rejected_incompatible_period |
                     rejected_incompatible_scope | rejected_unit_mismatch |
                     rejected_missing_input | rejected_currency_mismatch
  provenance                   # who requested it, in which run/task
  computed_at
```

Candidate metrics: growth, CAGR, margins, FCF conversion, OCF conversion, capex
intensity, net debt, leverage, ROE, ROIC, dilution, segment mix, incremental
margin, plus playbook-specific KPIs (CET1 and cost of risk for banks; R&D per
pipeline asset for biotech).

**The engine rejects semantically incompatible inputs rather than computing
something plausible.** Half-year revenue over prior full-year revenue is not a
growth rate; segment revenue over Group revenue is a mix ratio, not growth. These
refusals are the whole point: they turn the period/scope invariants from a review
rule into an executable one, and they make an LLM's arithmetic error structurally
impossible rather than merely discouraged.

---

## 7. Research Ledger

Analysts today coordinate through free-text summaries compacted into the next
agent's prompt (`council.py:664-704` `_prior_summaries` / `_compact_agent_line`).
That is lossy by construction and cannot be queried, challenged or remembered.

The ledger is the shared structured state of one research run:

| Record | Key content |
|---|---|
| `ResearchRun` | mode, budget, entity, playbook versions, status, consumption. |
| `ResearchTask` | assigned role, question ids, tool budget, iteration cap, status. |
| `ResearchQuestion` | text, origin (playbook / director / red team / prior gap), priority, required evidence classes, resolution status. |
| `ResearchFinding` | statement, mechanism, direction, confidence, `evidence_ids[]`, `calculation_ids[]`, scope, period, originating agent/provider, verification status. |
| `ResearchHypothesis` | a claim under test, with supporting and contradicting finding ids. |
| `ResearchGap` | what is missing, why it matters, which sources were tried, whether it is closable. |
| `ResearchDisagreement` | two finding ids, the nature of the conflict, the resolution or its absence. |
| `ResearchToolCall` | tool, arguments, result summary, cost units, latency, outcome. |

**A finding without `evidence_ids` or `calculation_ids` is not a finding.** The
schema enforces it; there is no "trust me" state.

`ResearchGap` deserves emphasis: V2 already produces excellent honest gaps
(`gaps.py`, `gap_attribution.py`). V3's change is that a gap becomes an
*actionable work item* consumed by the bounded loop, not only a caveat printed
in the report.

---

## 8. Research Memory and `ResearchDelta`

Every V2 run starts from zero. V3 persists per entity:

prior thesis · prior findings · unresolved gaps · risks · catalysts · prior Chair
synthesis · key financial state · evidence references (by stable id).

A refresh computes a `ResearchDelta`:

```
ResearchDelta
  entity_id, from_run_id, to_run_id
  new_evidence[]                 # documents/facts that did not exist before
  changed_facts[]                # same slot, different value → restatement or new period
  resolved_questions[]           # previously open, now answered — with the evidence
  new_gaps[] / closed_gaps[]
  invalidated_findings[]         # prior conclusions the new evidence undermines
  unchanged_core_thesis: bool
```

so the run can answer the question a returning investor actually has:

> **What changed since the previous analysis, and which prior conclusions should
> be revisited?**

**New primary evidence always outranks stale memory.** Memory proposes; evidence
disposes. A remembered conclusion that contradicts a fresh filing is an
`invalidated_finding`, never a tiebreaker. Memory is also never a citation
source: it points at evidence ids, and if the underlying evidence no longer
verifies, the memory goes with it.

---

## 9. Peer / competitive relationships

Peers are typed, sourced and effective-dated, and they are not one list:

| Relationship | Meaning |
|---|---|
| `operating_competitor` | Competes for the same customer/revenue. |
| `valuation_comparable` | Used for multiple comparison; may not compete at all. |
| `thematic_peer` | Shares a driver (China luxury demand, semicap cycle). |
| `supplier` / `customer` | Value-chain position. |
| `substitute` | Demand-side alternative. |

Persisted with `relationship_type`, `rationale`, `source`, `confidence`,
`effective_from/to`.

**An LLM-generated peer list is a `ResearchLead`, not a canonical relationship.**
It becomes canonical only with a source — a filing's own competitor discussion, a
segment/industry classification, or a documented value-chain link. Peer sets
silently invented by a model are one of the easiest ways to produce a confident,
wrong comparison.

---

## 10. Macro, government and industry observations

Today 15 macro and 8 event sources are **reference-only by design**: they emit a
bounded pointer plus an honest gap, and make no network call at report time
(`docs/DATA_SOURCE_INVENTORY.md`). That was the right call when nothing could
store a time series. V3 gives them somewhere to land:

```
DatasetDefinition  (provider, dataset id, licence, update cadence)
   └─< SeriesDefinition (series id, unit, geography, frequency, seasonal adj.)
          └─< Observation (period, value, vintage/revision, retrieved_at, source_ref)
```

Candidate official sources: FRED, Eurostat, World Bank, OECD, BEA, BLS, central
banks, national statistics offices, trade/customs, EIA, and sector-specific
government APIs.

**Vintages matter.** A macro series is revised. An observation therefore records
the vintage it was retrieved at, so a past report's macro context remains
reproducible instead of silently changing under it.

---

## 11. Private research

First-class, and firewalled. Supported conceptually: PDF, DOCX, XLSX, PPTX,
Markdown/text — analyst reports, consultant studies, notes, investment memos.

Every private artifact carries: `owner`, `access_policy`, `source_class`,
`retention_policy`, `indexing_rights`, `citation_rights`, and entity/industry
linkage.

> **Private and licensed material is never transmitted to an external provider
> unless the policy on that specific document explicitly permits that specific
> provider.** The default is deny. This is enforced at the tool boundary — a
> research tool that would ship private text to a model checks
> `may_send_to(provider)` per document, not per run — because a per-run flag is
> exactly the kind of coarse control that leaks one document.

---

## 12. What V2 keeps doing unchanged

`safe_web_fetcher` · `pinned_transport` · `document_discovery` ·
`primary_document_extractor` · `financial_table_reconstructor` ·
`extracted_fact_validator` · `financial_history` · `financial_period` ·
`fact_scope` · `citation_checker` · `report_consistency` · `report_lineage` ·
`research_job` · `final_report_generator`.

These encode years of live-issuer failure modes — encrypted PDFs, JS-gated IR
pages, two-column layouts, borderless five-year tables, str-Enum `isinstance`
traps, scope-blind excerpt ranking. V3 wraps and extends them. It does not
rewrite them because they are new-looking.
