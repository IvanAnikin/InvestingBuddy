# InvestingBuddy V3 — Security, Data Governance and Licensing

**Status:** V3 TARGET. Extends `docs/SECURITY.md`, which stays authoritative for
everything already deployed. Baseline `4b60e07`.

---

## 1. What changes in V3's threat model

V2's attack surface is small by construction: the API never accepts a URL, and
every fetch target comes from a code-defined issuer allowlist
(`safe_web_fetcher.py:11-31`).

V3 deliberately widens this. Three new capabilities each bring a new class of
risk:

| New capability | New risk |
|---|---|
| Search providers return arbitrary URLs. | The allowlist no longer bounds what may be fetched. |
| Agents call tools in a loop. | Fetched content can attempt to steer tool use — prompt injection with a live tool behind it. |
| Private documents are ingested and indexed. | A retrieval or a provider payload can exfiltrate confidential material. |

Everything below exists to keep those three from becoming incidents.

---

## 2. Preserved protections

Non-negotiable, carried forward unchanged:

| Protection | Where |
|---|---|
| HTTPS-only, host-allowlisted fetching | `safe_web_fetcher.py` |
| No IP-literal / localhost / `.internal` / `.local` targets | `safe_web_fetcher.py` |
| Guarded redirects (no off-allowlist hop, no HTTPS→HTTP downgrade) | `safe_web_fetcher.py` |
| Resolve-then-connect IP pinning (DNS-rebinding TOCTOU) | `pinned_transport.py`, ADR-014 |
| Byte, timeout, page and link caps on every fetch | `config.py` `primary_document_*` |
| Decompression-bomb guard (`primary_document_max_image_pixels`) | `config.py` |
| Content-type allowlist for documents | `config.py` |
| Secret redaction in logs (`RedactingFilter`) | `app/core/log_redaction.py` |
| Registry payload secret scan (`assert_registry_safe`) | `services/sources/registry.py` |
| URL secret stripping | `services/sources/redaction.py` |
| Admin routes never public; `APP_ENV=staging` gates API Basic Auth | `core/staging_auth.py` |

**`APP_ENV=staging` is load-bearing.** It is the only gate on API Basic Auth and
`ib-stg-api` has no network access restrictions. It must never be "cleaned up" to
`production` as a cosmetic change.

---

## 3. Fetching beyond the allowlist

The escalation ladder, cheapest and safest first:

```
1. official API              (SEC, GLEIF, macro providers)
2. known official URL        (current verified-issuer registry + safe fetcher)
3. search provider           (returns candidate URLs — does not fetch them)
4. full-page retrieval       (guarded fetch of a search result)
5. structured crawler        (bounded, per-site, opt-in)
6. browser automation        (JS-gated pages ONLY, last resort)
```

Rules for steps 3-6:

- **A search result is a candidate, not a fetch authority.** A returned URL goes
  through the same scheme, IP, redirect and byte guards as everything else. The
  allowlist is replaced by a *policy* (public internet, minus private ranges,
  minus known-bad, plus per-run caps) — not by nothing.
- **Browser automation is never the default crawler.** It is expensive, it
  executes untrusted JavaScript, and it is the largest isolation surface here. It
  runs in an isolated context with no credentials and no access to the corpus.
- **Fetched content is data, never instructions.** This is the single most
  important sentence in this document.

---

## 4. Prompt and tool injection

An issuer's PDF, a news article and a search snippet are all attacker-influenced
in the general case. Defences, layered:

1. **Closed tool list.** Agents get an enumerated set of typed, read-only tools.
   No SQL, no shell, no filesystem, no arbitrary HTTP, no writes. Injection that
   succeeds still cannot do anything but read entity-scoped data.
2. **Structural separation.** Retrieved content is delivered in a clearly demarcated
   evidence region, never concatenated into the instruction region.
3. **Tool arguments are validated, not interpolated.** An entity id is a UUID that
   must exist and must be the run's entity — not a string a model composed.
4. **Governance at the tool boundary.** `search_private_research` checks
   `may_send_to(provider)` per document, so an injected instruction to "summarise
   the private memo using the external model" fails on policy, not on the model
   declining.
5. **Every tool call is audited.** `ResearchToolCall` records arguments and
   outcome, so an anomalous pattern is visible after the fact.
6. **Injection fixtures in the test suite.** Documents containing explicit
   instruction-injection attempts, asserting no tool call outside the declared set.

---

## 5. Data classification

Every source, document and artifact carries an `access_class`:

| Class | Examples |
|---|---|
| `public_official` | SEC filings, regulator disclosures, government statistics. |
| `public_issuer` | Annual reports, IR pages, press releases, transcripts published by the issuer. |
| `public_web` | News, commentary, third-party pages. |
| `licensed_private` | Vendor data or research under licence. |
| `user_private` | Documents the user uploaded. |
| `derived` | InvestingBuddy-produced facts, calculations, findings. |

## 6. Permission matrix

Per artifact, six independent permissions — not one "is it public" boolean:

```
stored              may raw bytes be persisted?
indexed             may it enter the search index?
sent_to_external_model   may its text go to ANY external model?
sent_to_provider_X       may it go to THIS provider?
quoted              may it be quoted verbatim in a report?
retained_long_term  may it outlive the run?
```

Defaults:

| Class | stored | indexed | ext. model | quoted | long-term |
|---|---|---|---|---|---|
| `public_official` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `public_issuer` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `public_web` | ✅ | ✅ | ✅ | bounded | ✅ |
| `licensed_private` | licence-dependent | licence-dependent | **deny** | licence-dependent | licence-dependent |
| `user_private` | ✅ | ✅ | **deny** | ✅ (to the owner) | user-controlled |
| `derived` | ✅ | ✅ | inherits the most restrictive input | ✅ | ✅ |

`sent_to_provider_X` is deliberately **separate** from
`sent_to_external_model`. "We allow external models" and "we allow *this*
provider in *this* jurisdiction under *these* retention terms" are different
questions, and collapsing them is how a document reaches a vendor nobody
evaluated.

> **`derived` inherits the most restrictive input class.** A finding computed from
> a private memo is private. Without this rule, derivation launders classification
> — which is the most likely way private material would actually leak: not as a
> document, but as a sentence about one.

## 7. Provider-specific governance

| Provider | Status |
|---|---|
| Azure OpenAI (incumbent) | Enterprise terms already in place; the current council backend. |
| OpenAI direct | Terms to be recorded before non-public content. `OPEN DECISION` #5. |
| DeepSeek | **Restricted to `public_official` / `public_issuer` / `public_web`** until data-handling, jurisdiction and retention terms are read and recorded. `OPEN DECISION` #4. |
| Gemini | Deep Research treated as a contractor; public content only pending review. `OPEN DECISION` #6. |
| Claude | Red Team role; public content only pending review. `OPEN DECISION` #7. |
| Exa / Perplexity | Search transports. Queries themselves are data — a query naming a private holding is a disclosure. Query construction must not embed private content. |

That last point is easy to miss: **the query is a payload.** Sending
`"<private memo phrase>" ASML capacity` to a search vendor discloses the phrase
even though no document was uploaded.

---

## 8. Multi-tenancy readiness

V3 is single-user private-use. The design does not assume it stays that way:

- every private artifact carries an owner from day one;
- corpus queries are authorization-filtered at the query layer, never in the
  presentation layer;
- public research and personalized research remain separate (CLAUDE.md rule 10);
- no cross-entity leakage: a tool call scoped to entity A cannot return entity B's
  private material.

Retrofitting a tenant boundary onto a store that never had an owner column is a
rewrite. Adding the column now is a nullable field.

---

## 9. Secret handling

- No secrets in code, registry entries, prompts, logs or test fixtures.
- Azure Key Vault in deployed environments; managed identity preferred over API
  keys where RBAC permits (the CLI identity is Contributor-only, so an API-key
  fallback is expected for some resources).
- **Never widen an `az` query to fetch a secret.** A too-broad query has already
  briefly exposed a real API key. Read specific properties, never whole config
  objects, and never print them.
- Provider payload logging is off by default and must never include prompt or
  completion text.

---

## 10. Licensing

- Store raw artifacts **only where legally permissible.** `rights_policy` on the
  document version governs it, and "we already fetched it" is not a licence.
- Respect `robots.txt` and terms of service for crawled content.
- Vendor content (transcripts, licensed data) follows its contract for retention,
  redistribution and quotation.
- Quoting is bounded: the platform cites and excerpts, it does not republish.
- Consumer AI subscriptions are **not** production licences. Automating a
  consumer UI is prohibited as architecture regardless of technical feasibility.
