# GHE host support for `pr-cost`

> **Retroactive plan.** This document was authored after the implementation
> was already written, reviewed (7+ `/code-review` rounds), tested, and
> pushed to PR #717. `skill-fidelity-reviewer` flagged that no plan had ever
> been written for this branch, in violation of this repo's mandatory
> plan-review policy. This plan closes that gap by documenting the design
> that was actually built, not by proposing new work. Where `/plan-review`
> raises a genuine concern below, it is treated as a live finding requiring
> an actual code change on this branch — not merely noted and left alone.

## Context

`pr-cost` (a `transcript-analysis.py` subcommand) joins local Claude Code
session costs against GitHub PR data into a TSV cost ledger, but it
hardcoded `github.com` in two places and therefore could not run at all
against a GitHub Enterprise (GHE) remote: `_gh_auth_preflight_ok()` ran a
bare `gh auth status` with no `--hostname` filter, and the git-remote-parsing
regex (now `_git_remote_origin_host_and_owner_repo`'s
`_GIT_REMOTE_HOST_OWNER_REPO_RE`) matched only the literal string
`github.com`, so a GHE remote URL never parsed. Fixing those two surfaced
two deeper gaps: `gh pr list`/`gh pr view --repo` calls with a bare
`OWNER/REPO` always resolve against `api.github.com` (or `GH_HOST`)
regardless of the invoking directory's actual remote, defeating host
identity even once host resolution worked; and the ledger's row-identity
key `(repo, pr_number, machine)` had no host component, so a same-named
`owner/repo` on two different hosts could collide. The intended outcome —
achieved and shipped — is that `pr-cost` works correctly and safely against
both github.com and GHE remotes, with existing github.com-only ledger data
upgraded in place rather than requiring a migration step.

## Approach

Host is threaded as an explicit parameter through every `gh`-facing call
site (auth preflight, repo-identity resolution, PR discovery, PR
enrichment) and stored as the first column of the ledger's composite
identity key, with unconditional host-qualification of every `--repo`
argument rather than branching on `host == "github.com"`.

### Assumption ledger

**Root problem:** `pr-cost` assumed a single implicit host (`github.com`)
throughout host resolution, auth preflight, `gh --repo` calls, and the
ledger's row-identity key, making it unusable against GHE and unsafe against
same-named repos on different hosts once host-awareness was partially added.

**Givens** (conditions this design treats as fixed, outside its own reach;
numbered so each mechanism below can point at the one it depends on —
`anchors: row<N>` names a given by this number, `anchors: root` means the
root-problem line itself):

1. `gh auth status` with no `--hostname` flag evaluates every host `gh` has
  ever held credentials for and fails the check if any one of them is bad —
  vendor (`gh` CLI) behavior, not something this design can change.
  `[verified: transcript-analysis.py:7082 docstring, code comment]`
2. `gh`'s bare `OWNER/REPO` form for `--repo` always resolves against
  `api.github.com` (or `GH_HOST` if set), never the invoking directory's git
  remote — vendor-imposed. `[verified: commit 525497e0 message states this
  was explicitly confirmed by testing `gh --repo HOST/OWNER/REPO` against
  github.com before relying on it]`
3. Existing ledger rows written before this change carry no `host` value at
  all (the column didn't exist) — a fact about already-persisted data this
  design must accept, not something it can retroactively correct.
  `[verified: _PR_COST_LEDGER_LEGACY_COLUMNS at transcript-analysis.py:6557]`

**Per mechanism:**

- **Host as the first ledger key column**, not a secondary lookup/index —
  `anchors: root`. Two lighter alternatives considered and rejected: (1) a
  secondary host-lookup map consulted only when a collision is suspected —
  rejected because the gh-call layer already independently derives and
  enforces host per call (`_resolve_pinned_gh_repo`), so a secondary index
  would duplicate that source of truth rather than being it; (2) a separate
  ledger file per host — rejected because every read path
  (`_latest_pr_cost_row`, `_print_pr_cost_uncaptured`) would need to fan out
  across files, multiplying the surface a single composite-key column
  avoids. `[verified: transcript-analysis.py:6529-6559, commit b3e63ad7
  message]`
- **Unconditional host-qualification of every `--repo` argument**
  (`_gh_host_qualified_repo`), not conditional branching on
  `host == "github.com"` — `anchors: root`. The lighter/narrower option (skip
  qualification when host is github.com, since that was the only
  previously-supported case) was rejected in favor of one code path with no
  host-special-casing, after confirming host-qualifying github.com itself is
  behavior-preserving. `[verified: commit 525497e0 message, exact-equality
  test `TestPrCostGhCallsPinnedAfterRepoIdentityResolution`]`
- **`gh auth status --hostname <host>`** scoping the preflight check to the
  resolved host — `anchors: row1` (the vendor-imposed aggregate-check
  given). No lighter alternative exists within `gh`'s own auth-status
  surface; `--hostname` is the CLI's documented mechanism for this.
  `[verified: transcript-analysis.py:7082]`
- **Host-mismatch as a distinct gh-call failure kind
  (`_GH_ERROR_KIND_HOST_MISMATCH`), folded into the existing
  `degraded_network` ledger status rather than a new persisted status
  value** — `anchors: root`. Rationale recorded in-code: a mid-run
  auth/host-misconfiguration failure and a generic transient one both mean
  "this row's enrichment is incomplete," not distinguishable persisted data
  states — so the extra granularity is kept at the gh-call layer for
  retry/messaging purposes but not surfaced in the schema.
  `[verified: transcript-analysis.py:6576+ code comment,
  _GH_CALL_DEGRADED_HOST_MISMATCH at 6601]`
- **Legacy-header dual-parsing with an explicit `github.com` default**, not
  a null/blank sentinel or a standalone migration script — `anchors: row3`
  (the given that pre-existing rows carry no host value). Every row under
  the legacy header predates GHE support entirely, so `github.com` is not a
  guess but the only host that could have produced that row; upgrade happens
  implicitly on the next `--record` write, since the writer always renders
  the current schema from parser-normalized in-memory rows.
  `[verified: transcript-analysis.py:6550-6559,
  test_record_against_legacy_file_upgrades_it_to_current_schema]`
- **`RuntimeError` guard (not `assert`) on `_PR_COST_LEDGER_COLUMNS[0] ==
  "host"`** — `anchors: row1` (protecting the legacy-column derivation from
  silently mis-slicing). `assert` was rejected because it is stripped under
  `python -O`, which would let the legacy/current column split silently
  drift out of sync with the real schema.
  `[verified: transcript-analysis.py:6555-6556]`

## Critical files

- `claude/.claude/scripts/transcript-analysis.py` — all logic:
  - `_git_remote_origin_host_and_owner_repo()` (host/owner/repo parsing,
    generalized regex `_GIT_REMOTE_HOST_OWNER_REPO_RE`)
  - `_gh_auth_preflight_ok(hostname)` (hostname-scoped auth check)
  - `_resolve_pinned_gh_repo(corpus_host, corpus_repo, ordinal)` (identity
    resolution, dual-axis host+repo mismatch detection)
  - `_gh_host_qualified_repo(corpus_host, pinned_repo)` (new helper, reused
    by `_gh_discover_merged_prs` and `_gh_pr_view_enrichment`)
  - `_classify_gh_error` / `_gh_call_with_backoff` (new
    `_GH_ERROR_KIND_HOST_MISMATCH` / `_GH_CALL_DEGRADED_HOST_MISMATCH`
    handling)
  - `_PR_COST_LEDGER_COLUMNS`, `_PR_COST_LEDGER_LEGACY_COLUMNS`,
    `_PR_COST_LEDGER_LEGACY_HOST_DEFAULT`, `_parse_pr_cost_ledger_file_text`
    (schema + backward-compat parsing)
  - `_new_pr_cost_row`, `_latest_pr_cost_row`, `_print_pr_cost_uncaptured`
    (host threaded through row construction and lookup)
  - **Reuse opportunity already taken**: `_resolve_pinned_gh_repo` parses
    `gh repo view`'s `url` field by reusing
    `_GIT_REMOTE_HOST_OWNER_REPO_RE` instead of a second host-parsing
    implementation.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — coverage
  listed in Verification below.
- `docs/pr-cost.md` — schema table, redaction section, re-record contract,
  and identity-mismatch section, all updated for the `host` column.

## Verification

All coverage below already exists and passes on this branch
(`../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py`
from within the worktree, per this repo's three-levels-deep `.venv` path):

- `TestGhAuthPreflightOkHostnameScoping` — exact `--hostname` argv, and the
  three failure modes (nonzero exit, timeout, `OSError`) preserved under the
  new signature.
- `TestGitRemoteOriginHostAndOwnerRepoRegex` — GHE URL shapes (HTTPS and
  SSH), mixed-case host lowering, attacker-substring spoof shapes on both
  github.com and a GHE host, disallowed-character hosts, and the deferred
  port-syntax gap (see Out of scope).
- `TestClassifyGhError` / `TestGhCallWithBackoffFailureClassBehavior` —
  host-mismatch stderr classification pinned against real `gh` 2.97.0
  output; no-retry behavior; actionable (non-raw-stderr) message.
- `TestResolvePinnedGhRepoIdentity` — empty-host/repo invariant guard,
  host-mismatch-with-matching-repo exit 2, malformed/spoofed `gh repo view`
  URL refusal, missing-key payload handling, case-insensitive host match.
- `TestPrCostGheHostQualifiesGhRepoCalls` /
  `TestPrCostGhCallsPinnedAfterRepoIdentityResolution` — full-orchestration
  exact-equality assertions that a GHE origin scopes auth preflight and
  host-qualifies both `gh pr list` and `gh pr view`, and that the
  github.com path is host-qualified too (not left bare).
- `TestPrCostLedgerLegacyHostColumnMigration` — legacy header parses with
  `github.com` default; a `--record` write against a legacy file upgrades it
  to the current schema.
- `TestGitRemoteOriginHostAndOwnerRepoRegex.test_ipv6_literal_host_does_not_resolve`
  — the same `[A-Za-z0-9.-]+` character-class exclusion that fails a
  port-bearing remote closed also fails a bracketed IPv6-literal remote
  closed, for the same reason (added in `/plan-review`, previously untested
  and undocumented — see Out of scope).
- `TestParsePrCostLedgerFileTextMalformed.test_malformed_host_raises_without_leaking_raw_value`
  — mirrors the existing malformed-`repo` test on the `host` axis.
- `TestPrCostCrossHostLedgerIsolation` — a second host's PR is recorded (not
  skipped as already-captured), `--force` doesn't supersede the other host's
  row, and the uncaptured-gap listing still surfaces the second host's PR.

End-to-end manual verification: run `pr-cost` against a GHE-remote checkout
with a GHE-only `gh` auth token and confirm the run completes and records a
row with the correct `host` value, then run it again against a github.com
checkout using the same ledger file and confirm both rows coexist.

## Out of scope

- **Host regex has no port syntax** (`_GIT_REMOTE_HOST_OWNER_REPO_RE`'s host
  character class excludes `:`). A GHE remote on a non-standard port fails
  closed (parse error, run aborts) rather than misrouting. Deferred as
  below current scale; `test_host_with_port_does_not_resolve` pins this as
  current correct behavior, not a TODO.
- **`cmd_pr_link`** (a separate, untouched subcommand) has a similar
  `GH_HOST`-sensitivity gap. Named as deferred in commit `b3e63ad7`; not
  touched by this change.
- **No terminal-output redaction path for `host`**: `docs/pr-cost.md`
  documents `repo` redaction on terminal output; no equivalent exists for
  `host` because no print path currently displays it.
- **`host` stored raw/unscrubbed at rest in the ledger file**, matching the
  pre-existing treatment of `repo` mechanically, though carrying more
  identification risk for a GHE host specifically — see `docs/pr-cost.md`'s
  redaction section (updated in `/plan-review` per a `ciso-reviewer`
  finding). Not something this change set out to fix.
- **`git`'s `url.<base>.insteadOf` remote rewriting** is not accounted for:
  `git remote get-url origin` returns the configured URL, but a rewritten
  target could differ from what actual git network operations resolve
  through. This requires a compromised local gitconfig — a larger problem
  than this tool — and is a pre-existing git-config attack surface this
  change doesn't introduce.
- **Host-mismatch and auth-failure classification depends on matching
  `gh`'s own stderr wording** (`_GH_HOST_MISMATCH_ERROR_RE` et al., pinned
  against `gh` 2.97.0's actual text). This is a best-effort match, not a
  guarantee — a future `gh` release that rewords this stderr line would
  cause a genuine host-mismatch or auth failure to fall through to the
  generic network-error path, which retries a non-retryable local
  misconfiguration for up to 15 minutes before giving up (a stall, not a
  fast actionable exit) rather than breaking outright — and per-PR
  enrichment (`_gh_pr_view_enrichment`) hits this per PR in the run, so a
  `--record` sweep over many PRs multiplies one stall into many. No version
  guard currently pins or checks `gh`'s stderr wording; accepted as a
  monitored risk rather than fixed in this change.
