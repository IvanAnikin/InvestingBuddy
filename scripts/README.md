# scripts

## `v3-gates.sh`

Runs the gates a V3 slice must pass, locally, in one command.

`api-ci.yml` and `web-ci.yml` trigger on `main` only, so **a PR into
`develop/v3` gets no automatic checks**. Whether to add `develop/v3` to those
workflows is [OPEN DECISION #17](../docs/v3/OPEN_DECISIONS.md#17-ci-coverage-for-the-v3-branch)
and it belongs to the user: it is a preference about CI minutes, and it touches
a file that also lives on `main`.

Until it is decided, every slice runs its gates by hand and records the output in
the PR body. This script runs the **exact** commands the workflows run, so
"it passed locally" means the same thing CI would have meant rather than whatever
the person remembered to type.

```bash
scripts/v3-gates.sh          # api + web
scripts/v3-gates.sh api
scripts/v3-gates.sh web
```

### `mypy-baseline.txt`

`mypy` is not in CI and `mypy app` is **not clean** — it carries a long-standing
baseline of pre-existing errors. Failing on a non-zero count would make the gate
red on every run, which trains a reader to ignore it. So the script fails only
when the count goes **up**, against the number in this file.

The count is **scope-dependent**: `mypy app` and a broader scope including
`tests/` produce very different numbers. Compare the same command between
`develop/v3` and the branch — diffing a narrow baseline against a broad one
manufactures a regression that is not there.

Current baseline: **71** errors in 10 files (`mypy app`, on `develop/v3`).
