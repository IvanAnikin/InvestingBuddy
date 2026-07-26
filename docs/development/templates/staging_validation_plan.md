# Staging Validation Plan — Phase <id>: <title>

> Draft this at the PR gate (part of the implementation report), execute it
> AFTER the human-approved merge/deploy. Runs read-only against staging; never
> changes app settings or deploys.

## Endpoints
- API: `https://ib-stg-api.azurewebsites.net` — health `GET /health`
- Web: `https://ib-stg-web.azurewebsites.net` — version `GET /api/version`
- Admin `/admin` is behind GitHub OAuth → validate via SHA + API data, not a
  live browser walk.

## Checks (map to closure report A–I)
- **A — API SHA:** `curl -s .../health | grep commit_sha` == merge SHA; poll
  until 3 consecutive matches (stale-worker window ~40s).
- **B — Web SHA:** `curl -s .../api/version | grep commit_sha` == merge SHA
  (or expected web SHA if only one app changed).
- **C — Migration:** DB head = `011` unless this phase added one; if it did,
  confirm the new revision is applied (`alembic current`, human-run).
- **D — AUTH_TEST_MODE:** absent — a protected route returns an auth challenge,
  not a bypass; `/health` environment = staging.
- **E–F — Phase-specific HTTP checks:** <list the exact endpoints/behaviors this
  phase added and the expected response, e.g. evidence-preview returns metadata,
  report renders readable sections>.
- **G — Flag state:** confirm intended final state of `LLM_COUNCIL_ENABLED`,
  `LLM_DISCOVERY_COUNCIL_ENABLED`, `SOURCE_CONNECTOR_ENABLED` by observed
  behavior (e.g. `connector_layer_enabled`), not by reading secret values.
- **H — Logs / no-secrets:** tail recent app logs (human-run `az` if needed),
  `grep -a` for token/secret patterns → none leak.
- **I — Safety / publication:** no recommendation/valuation language in output;
  publication stays admin-gated, not public.

## Gotchas (from prior phases)
- App-setting change → async restart; a poll can hit an old worker (~40s). Wait
  for the dip + several OKs before testing flag-dependent behavior.
- run-analysis is synchronous; `latest_report` can transiently show a legacy
  draft before the final report finishes.
- e2e in parallel is auth-flaky → `--workers=1`.
- Azure log tail can be binary → `grep -a`.

## Result
Fill `docs/development/templates/closure_report.md` from the outcomes above.
