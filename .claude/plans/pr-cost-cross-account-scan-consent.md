# Let pr-cost scan every declared account without every account pre-opting in

## Context

`pr-cost` refuses outright (exit 2) whenever more than one scan root resolves
— identically for plain read mode and for `--record` — forcing an operator
on a multi-account machine to manually loop single-account invocations (set
`CLAUDE_CONFIG_DIR` per account, and neutralize `TRANSCRIPT_CONFIG_DIRS_FILE`
so the declared-roots union doesn't re-trigger the refusal) just to get a
cross-account view. The fix: let one invocation scan every declared account,
while keeping each account's own `.pr-cost-enabled` sentinel as the gate on
whether *that account's* row is durably written — separating "can this run
see multiple accounts at all" from "may this run write this particular
account's data," which today are the same unconditional refusal.

`docs/pr-cost.md:80` also documents a fix for the current refusal ("point
`CLAUDE_CONFIG_DIR` at a single account") that does not work —
`declared_transcript_roots()` (`_config_dir.py:136-144`) has no
`CLAUDE_CONFIG_DIR` dependency at all — and needs correcting regardless of
which design direction was chosen.

## Approach

Add an `--all-accounts` flag to `pr-cost` that lifts the multi-root refusal
for both read mode and `--record`, and refactor `_pr_cost_report` into an
in-process loop over each resolved root — one full account's worth of
scan/print/write per iteration — mirroring the `_redaction_ordinals`/root-loop
pattern eight other multi-root subcommands in this file already use (e.g.
`cmd_subagents`, `transcript-analysis.py:812-909`). Each account's own
`.pr-cost-enabled` sentinel still individually gates whether that account's
row gets written; an account without one is skipped with a per-account
stderr notice, and the run ends with a summary of how many of the declared
accounts were actually recorded.

**Root problem:** see Context above — `_pr_cost_report`'s `len(roots) > 1`
refusal (`transcript-analysis.py:7431-7444`) conflates read-visibility with
per-account write-consent, forcing manual per-account looping to get any
cross-account view at all.

**Givens:**
- G1: `_resolve_cost_roots()` already correctly assembles the full
  multi-account root list (union of `config_dir()` +
  `declared_transcript_roots()` + `--config-dir` extras, deduped by resolved
  path) `[verified: transcript_analysis/scope.py:489-562]` — it backs 13
  other subcommands beyond pr-cost (`_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`), so
  changing its root-assembly behavior is a decision affecting every one of
  those call sites, outside this single-flag plan's scope.
- G2: the `.pr-cost-enabled` sentinel is a local-disk-write consent gate
  only — it never posts anything to GitHub or any external system
  `[verified: transcript-analysis.py:7508-7523, docs/pr-cost.md:58]` — this
  is established, pre-existing design shared with cost-ledger's parallel
  `.cost-ledger-enabled` sentinel; redefining what it gates would change
  that shared consent-gating convention, a decision outside this single-flag
  plan's scope.

**Per-mechanism ledger:**

| # | Decision | Tag | Anchor |
|---|---|---|---|
| R1 | Read mode requires the same `--all-accounts` opt-in as `--record` (not opened unconditionally) | `[engineer-verified]` | root |
| R2 | An account with no sentinel is skipped (not aborted) during `--record --all-accounts`, with a per-account stderr notice and an end-of-run summary line | `[engineer-verified]` | root |
| R3 | Implementation is an in-process loop, not subprocess self-reinvocation | `[engineer-verified]` | root |
| R4 | Each account's own sentinel still individually gates that account's write, even under `--all-accounts` | `[engineer-verified]` | G2 |
| R5 | `_resolve_cost_roots` needs no changes; the refusal to remove lives entirely in `_pr_cost_report` | `[verified: transcript_analysis/scope.py:481-562, transcript-analysis.py:7414]` | G1 |
| R6 | The account ordinal is currently hardcoded to `1` in `_resolve_pinned_gh_repo()` specifically because multi-root was always refused (`transcript-analysis.py:7027-7061`); it must become a real per-account `_redaction_ordinals(roots)` lookup | `[verified: transcript-analysis.py:7027-7061, scope.py:180-200]` | R3 |
| R7 | gh auth/identity resolution and merged-PR discovery are account-independent (not scoped by `CLAUDE_CONFIG_DIR`), so they're resolved once per run rather than once per account | `[verified: gh help environment documents GH_CONFIG_DIR/XDG_CONFIG_HOME as the only config-location env vars gh reads; the existing _gh_auth_preflight_ok/_resolve_pinned_gh_repo already key off the invoking repo's own git remote and gh's ambient session, never CLAUDE_CONFIG_DIR — confirmed by plan-review's backend reviewer]` | R3 |
| R8 | `PR_COST_LEDGER_PATH` forces one shared absolute ledger path; combined with `--all-accounts` and more than one resolved root, this would silently commingle every account's rows into one file, defeating the per-account separation the sentinel gate (R4) depends on — refused outright (exit 2) rather than allowed | `[verified: transcript-analysis.py:6664-6673, docs/pr-cost.md:56]` | R4 |
| R9 | `docs/pr-cost.md:80`'s "point `CLAUDE_CONFIG_DIR` at a single account" guidance is corrected regardless of design direction — it has no basis in `declared_transcript_roots()`'s actual behavior | `[verified: _config_dir.py:22-39]` | root |
| R10 | `_resolve_pinned_gh_repo()`'s gh-identity-mismatch refusal path uses `ordinal` internally (not just as a return value) to redact the repo label in its own error message; since this call is now shared across the whole run and happens before any single account is "the" account, it takes `ordinal` as a parameter instead of hardcoding it, and the caller passes the first resolved root's ordinal (`_redaction_ordinals(roots)[roots[0].resolve()]`) — preserving today's exact output shape, since today's hardcoded `ordinal = 1` was already just "the only (and therefore first) account"'s ordinal | `[verified: transcript-analysis.py:7049-7060 — flagged by plan-review's backend reviewer]` | R6 |
| R11 | Under `--all-accounts --pr N` specifically — **not** plain single-account `--pr N`, which deliberately keeps writing a zero-valued-agg row (`test_target_pr_with_zero_branch_records_uses_zero_valued_agg_default`, `test_transcript_analysis.py:16011-16036`) — `target_branches` is derived from the shared, repo-wide `merged_prs` list, so a matched branch can appear in every account's per-branch loop regardless of whether that account's own local corpus ever touched it; the existing `agg = branch_totals.get(branch) or _new_pr_cost_agg()` fallback (`transcript-analysis.py:7615`) would otherwise write a zero-cost row for every non-participating account — the loop instead skips (not records) an account whose `branch_totals` lacks the target branch, gated on `args.all_accounts` exactly like R2's sibling skip conversions | `[verified: transcript-analysis.py:7615, 16011-16036 — previously unreachable since multi-root always refused; the args.all_accounts gate is required so this fix doesn't regress the single-account test above]` | R2 |
| R12 | `pr_number`, `machine`, and both timestamp columns print raw/unredacted in the existing single-account read-mode listing (only `repo`/`head_branch` are redacted); under `--all-accounts` this becomes a real cross-account correlation surface once two accounts' rows print within one continuous invocation — documented explicitly in the `docs/pr-cost.md` rewrite (mirroring that doc's own "Residual replication paths" precedent for an unclosed gap) rather than adding new redaction, since these fields are genuine operator-facing data (not previously hidden) and the correlation risk is specific to genuinely multi-tenant declared accounts, not this engineer's own single-operator setup | `[flagged by plan-review's ciso-reviewer; documentation-over-redaction is this plan's recommendation — confirm before implementation]` | root |
| R13 | The end-of-run summary's `recorded` counter counts **accounts** that wrote at least one row, not total rows written — matching the summary line's own "of {len(roots)} declared accounts" denominator | `[verified: this plan's own control-flow shape — a per-row counter would produce a denominator mismatch against "declared accounts"]` | R2 |

**Mechanism: in-process loop over roots, anchors: R3, G1.** Lighter
alternatives considered and rejected:
- *Subprocess self-reinvocation* (spawn `transcript-analysis.py pr-cost`
  once per account with `CLAUDE_CONFIG_DIR` set, mirroring the manual
  per-account workaround described above) — rejected: no precedent anywhere in
  this file, and it introduces a process-spawn/stdout-relay/exit-code
  orchestration model where the established lighter mechanism
  (`_redaction_ordinals` + per-root looping, already used by 8 subcommands)
  fully covers the need.
- *A new standalone orchestration subcommand* separate from `pr-cost` —
  rejected: unnecessary CLI surface duplication; a flag on the existing
  subcommand, following `_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`'s existing
  per-subcommand-flag convention, fully expresses the feature.

**Mechanism: whole-report-per-account loop instead of one merged session
iterator with per-session root attribution, anchors: R6.** The lighter
alternative — reusing `cmd_subagents`'s exact shape (one merged
`session_iter` across all roots, `_root_index_for_path` resolving each
session's owning root) — was considered and rejected: `_pr_cost_report`'s
body is already structured as one full report (branch totals → merged-PR
join → ledger read/print/write) keyed to a single root. Looping that entire
body per account is a smaller diff than threading root-attribution through
`_compute_pr_cost_branch_totals` and the ledger read/write path, and it
keeps each account's ledger read/write fully independent — no risk of one
account's row accidentally keying off another's root index.

### Control-flow shape

The `(R#)` references below are cross-references into this plan's own
ledger table, for a reader tracing rationale — they are not meant to be
copied into the real source's code comments, which should state each
constraint in plain prose instead.

```
roots = _resolve_cost_roots(args, "pr-cost")          # unchanged
if len(roots) > 1 and not args.all_accounts:
    refuse (exit 2, same message as today)
if args.all_accounts and len(roots) > 1 and os.environ.get("PR_COST_LEDGER_PATH"):
    refuse (exit 2, R8 — one forced path would commingle every account's rows)

# --- shared, resolved once for the whole run (R7) ---
validate args (force/record/machine_label/window_days/plan_glob/risk_globs)
gh auth preflight
redact_ordinals = _redaction_ordinals(roots)            # NEW — computed before
                                                           # _resolve_pinned_gh_repo
                                                           # so its own mismatch-
                                                           # refusal label has an
                                                           # ordinal to use (R10)
pinned_repo, repo_map = _resolve_pinned_gh_repo(
    ordinal=redact_ordinals[roots[0].resolve()])          # R10 — no more internal
                                                           # hardcoded ordinal
merged_prs = _gh_discover_merged_prs(pinned_repo)
branch_map: dict = {}                                    # shared across accounts;
                                                           # key already includes ordinal

recorded, skipped_no_sentinel, skipped_other = 0, 0, 0
for root in roots:
    account_config_dir = root.parent
    ordinal = redact_ordinals[root.resolve()]

    session_iter, scope_label = _resolve_project_scope(args, "pr-cost",
        include_subagents=True, roots=[root])
    branch_totals, unbranched_agg = _compute_pr_cost_branch_totals(session_iter)
    ledger_path = _pr_cost_ledger_path(config_dir_override=account_config_dir)
    existing_rows = parse ledger if it exists

    if not record:
        _print_pr_cost_ledger_rows(existing_rows, ordinal, branch_map, repo_map)
        _print_pr_cost_uncaptured(branch_totals, merged_prs, existing_rows,
            pinned_repo, machine_label, ordinal, branch_map)
        continue

    sentinel_path = account_config_dir / ".pr-cost-enabled"
    if not sentinel_path.exists():
        print per-account stderr notice; skipped_no_sentinel += 1; continue
    if _ledger_path_is_git_tracked(ledger_path, "pr-cost"):
        print per-account stderr notice; skipped_other += 1; continue

    account_recorded_a_row = False
    # existing per-branch loop body, unchanged, operating on this account's
    # branch_totals/ledger_path/existing_rows, EXCEPT:
    #  - when args.pr is set, args.all_accounts is set, and the resolved
    #    target branch is not a key in THIS account's branch_totals, skip
    #    this account (skipped_other += 1, continue to the next root)
    #    instead of falling through to
    #    `agg = branch_totals.get(branch) or _new_pr_cost_agg()`, which
    #    would otherwise write a zero-cost row for an account that never
    #    touched the PR (R11). Single-account --pr N (no --all-accounts)
    #    keeps today's behavior unchanged: a zero-valued-agg row is still
    #    written, per
    #    test_target_pr_with_zero_branch_records_uses_zero_valued_agg_default.
    #  - skip conditions that were previously hard sys.exit(1) ONLY when
    #    args.pr is set now become per-account skips (skipped_other += 1)
    #    when args.all_accounts is set too — a --pr target legitimately
    #    exists in at most one account's local corpus, so failing the whole
    #    run on the first account without it would be wrong.
    ...
    if <this account wrote at least one row>:
        account_recorded_a_row = True
    if account_recorded_a_row:
        recorded += 1   # counts ACCOUNTS, not rows (R13)

if record and args.all_accounts:
    print(f"pr-cost: recorded {recorded} of {len(roots)} declared accounts"
          f" ({skipped_no_sentinel} not opted in, {skipped_other} skipped)")
```

### Alternatives considered for the design questions themselves

Recorded here for completeness — each alternative below was considered and
rejected for the stated reason:
- Read-mode gate: opening read mode unconditionally (no flag) was
  considered and rejected — `pr_number`, `machine`, and both timestamp
  columns print raw in the listing (only `repo`/`head_branch` are
  redacted), so an explicit opt-in keeps the "can this run see multiple
  accounts" decision deliberate for read mode too, not silently default-on.
- Non-consented account handling: silent skip (no notice, no summary) was
  considered and rejected — it would leave the operator unable to tell
  "nothing to record" apart from "recorded nothing because nobody opted in."
- Sentinel scope: "`--all-accounts` itself is the consent, bypass each
  account's own sentinel" was considered and rejected after clarifying that
  the sentinel gates a local-disk write, never anything GitHub-facing — the
  per-account gate exists to protect other stow users of this public repo
  whose declared accounts may represent genuinely separate tenants, not
  just this engineer's own single-operator setup.

## Critical files

- `claude/.claude/scripts/transcript-analysis.py`
  - `cmd_pr_cost()` (`:7407-7415`) — no change expected beyond whatever
    `--all-accounts` parsing requires.
  - `_pr_cost_report()` (`:7418-7636`) — the refactor described above;
    re-read in full before editing — this plan was drafted from excerpts of
    this function, not a full read.
  - `_resolve_pinned_gh_repo()` (`:7027-7061`) — re-read in full before
    editing; change its signature to accept `ordinal: int` as a parameter
    (computed by the caller via `_redaction_ordinals(roots)[roots[0].resolve()]`)
    instead of hardcoding `ordinal = 1` internally; drop `ordinal` from its
    return, return `(pinned_repo, repo_map)` only (R10).
  - `_pr_cost_ledger_path()` (`:6664-6673`) — add an optional
    `config_dir_override: Path | None` parameter; `PR_COST_LEDGER_PATH`
    still takes precedence when set (this is what R8's refusal guards
    against under `--all-accounts`).
  - `_pr_cost_args()` test factory (`:15458-15482`) — add
    `all_accounts: bool = False` (or thread the new flag via `getattr` in
    the production code, matching this function's existing convention for
    `record`/`force`/`pr`/`machine_label`) so every existing pr-cost test
    keeps passing unchanged with no `AttributeError`.
  - pr-cost argparser (`:10649-10706` region) — add `--all-accounts`
    (store-true, no default-on).
  - **Reuse, do not reimplement:** `_redaction_ordinals()` (`scope.py:180-200`),
    `_assign_root_scoped_redact_label()` (`transcript_analysis/redaction.py:197-229`),
    `_print_pr_cost_ledger_rows()` / `_print_pr_cost_uncaptured()`
    (`:7362-7405`) — call once per account inside the loop, unchanged.
- `docs/pr-cost.md` — rewrite the "Multi-root (exit 2)" section (`:78-82`):
  remove the incorrect `CLAUDE_CONFIG_DIR` guidance (R9), document
  `--all-accounts` and its interaction with the per-account sentinel (R4)
  and the `PR_COST_LEDGER_PATH` refusal (R8). Add the residual cross-account
  correlation-risk note (R12) and a one-line caveat against symlinking a
  `.pr-cost-enabled` sentinel between two accounts (flagged by
  plan-review's ciso-reviewer — a symlinked sentinel silently opts both
  accounts in together, since `sentinel_path.exists()` follows the link).
- `claude/.claude/scripts/tests/test_transcript_analysis.py`
  - Existing refusal tests (`test_more_than_one_resolved_root_refuses_with_exit_2`
    at `:16138-16148`, `TestPrCostMultiRootRefusalRedaction` at `:16925-16945`)
    keep passing unchanged for the no-flag path — assert they still do, no
    behavior change there.
  - Update: `TestResolvePinnedGhRepoIdentity` (`:16758-16776`) — calls
    `_resolve_pinned_gh_repo()` directly and hardcodes `ordinal=1`'s effect
    via literal `"account-1/repo-1"`-style assertions; update its call site
    for the new `ordinal` parameter (R10). `TestResolvePinnedGhRepoRetryExhaustion`
    (`:16810-16846`) calls `_pr_cost_report`, not `_resolve_pinned_gh_repo`
    directly — re-run to confirm it still passes; no code change expected.
  - New: direct-call test asserting `_resolve_pinned_gh_repo(ordinal=2, ...)`
    against a mismatched gh-repo/corpus-repo fixture produces
    `"account-2/repo-..."`-labeled output, not `"account-1/..."` — closes
    the gap that no test in this plan otherwise exercises a non-default
    `ordinal`, since every caller in the new design passes
    `redact_ordinals[roots[0].resolve()]`, which is always `1` by
    construction for a single/first root.
  - Existing test `test_target_pr_with_zero_branch_records_uses_zero_valued_agg_default`
    (`:16011-16036`, single-account `--pr N` with the target branch absent
    from local corpus activity still writes a zero-valued-agg row) keeps
    passing unchanged — R11's skip conversion is gated on `args.all_accounts`
    specifically so this single-account behavior is untouched.
  - **The `--record --all-accounts` tests below (through the "as-of-window
    conversion" bullet) all require per-account `config_dir_override`-based
    ledger isolation, not the file's usual `PR_COST_LEDGER_PATH` monkeypatch
    idiom** — the latter trips the new R8 refusal outright whenever more
    than one root is in scope, so a test built on it would exit(2) before
    ever reaching the record path it claims to exercise. (The
    `PR_COST_LEDGER_PATH` refusal test itself, further below, is the one
    exception that deliberately sets that env var to prove R8 fires.)
  - New: `--all-accounts` read mode across two declared accounts (reuse the
    `_two_declared_roots(tmp_path, monkeypatch)` helper at `:13500-13519`),
    including a same-named branch/repo present in both accounts — asserts
    the shared `branch_map`/`repo_map` keep the two accounts' labels
    distinct (`account-1/...` vs `account-2/...`, never colliding).
  - New: `--all-accounts --record` with a mix of opted-in/not-opted-in
    accounts — asserts the opted-in account's row is written, the other is
    skipped with a stderr notice, and the summary line reflects both.
  - New: `--all-accounts --record` with zero sentinels present — asserts
    zero rows written, summary reflects it, run still exits 0.
  - New: `--all-accounts --record` where one account writes two rows in one
    run (two branches) — disambiguates `recorded`'s per-account-not-per-row
    counting (R13).
  - New: `--pr N --all-accounts` where the target PR's branch exists in
    only one of two accounts' local corpus — asserts the participating
    account records the row and the non-participating account is skipped
    rather than getting a zero-cost row (R11).
  - New: `--pr N --all-accounts` "already captured" conversion — a
    two-phase test: a first `--record --pr N --all-accounts` call captures
    the row for the participating account, then a second identical call
    (no `--force`) asserts that account is now per-account-skipped rather
    than the whole run hard-aborting with `sys.exit(1)` (the single-account
    behavior at `:7604-7613` this replaces).
  - New: `--pr N --all-accounts` as-of-window conversion — a PR merged
    inside the `--asof-window-days` boundary asserts the same
    per-account-skip conversion applies at `:7580-7587`, distinct from the
    "already captured" and "branch not in corpus" conditions above.
  - New: `--all-accounts` on a single-declared-account machine is a no-op
    (identical output to today, no refusal).
  - New: per-account ordinal is resolved-path-sorted, not scan-order —
    mirrors `test_account_ordinal_is_resolved_path_sorted_not_scan_order`
    (subagent-mix copy at `:1481-1521`).
  - New: `--all-accounts` combined with `PR_COST_LEDGER_PATH` set and >1
    resolved root refuses (exit 2) before any git/gh call and without
    leaking either root's raw path in stderr — mirroring the existing
    multi-root refusal's own two dedicated assertions
    (`test_more_than_one_resolved_root_refuses_with_exit_2`,
    `TestPrCostMultiRootRefusalRedaction`).
  - New: `cmd_pr_cost()` itself is invoked end-to-end at least once (via
    real `argparse.parse_args([...])`, not `_pr_cost_args()`'s
    direct-construction idiom) with `--all-accounts` set — closes the gap
    that no existing pr-cost test drives through the real CLI parser today.

## Verification

`../../../.venv/bin/pytest claude/.claude/scripts/tests/test_transcript_analysis.py`
and `../../../.venv/bin/ruff check claude/.claude/` from the worktree. No
manual run against the engineer's real multi-account ledgers is part of
this plan's verification — the test suite's synthetic fixtures are the
closed loop; a manual smoke test against real accounts is the engineer's
own call, separately from this plan.

## Out of scope

- The nudge-cap recalibration work that surfaced this gap
  (`.claude/plans/handoff-nudge-cap-recalibration.md`, branch
  `handoff-nudge-cap-recalibration`) — unrelated, already has its own plan.
- Re-running or auditing the one-off cross-account `pr-cost --record`
  backfill already performed this machine.
- Deciding whether to keep or remove the `.pr-cost-enabled` sentinel files
  already created in each declared account — a machine-local decision.
- Changing `_acquire_pr_cost_ledger_lock` or the per-ledger `.lock`-sibling
  locking mechanism — already correctly scoped per ledger file, no known
  defect.
- The `cost`/`cost-ledger` subcommand's own separate `--summary`
  single-account bypass (`_resolve_cost_roots`'s `subcommand == "cost"`
  branch) — a different, independently-gated mechanism; not generalized
  into this change.
- Adding `pr-cost` to the shared `_UNCONDITIONAL_HEADER_CASES` test table
  (`:13289-13337`) that other multi-root subcommands run through — pr-cost
  was deliberately excluded while it refused multi-root outright, and
  joining that table needs `gh`-call-faking infra (mirroring `pr-link`'s
  `_fake_gh_pr_list_run`) that's a reasonable follow-up, not required here.
- Redacting or bucketing `pr_number`/`machine`/timestamp columns in
  `--all-accounts` output (R12) — documented as a residual gap instead;
  revisit if the engineer prefers redaction over documentation.
