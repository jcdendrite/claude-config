# cost-ledger --record: fix the multi-root refusal

## Context

`cost-ledger --record` refuses outright (exit 2) whenever more than one
Claude account is in scope — on any machine that declares more than one
account in `~/.claude/transcript-config-dirs`, `--record` can never
succeed, permanently, regardless of where its output goes. The engineer
asked for this fixed after confirming the refusal is permanent-by-design,
not a temporary artifact of the just-landed cost-ledger storage redesign
(PR #637). This machine is exactly that case: a separate personal-tooling
change just landed a generator for `transcript-config-dirs`, and PR #635's
own plan predicted this outcome verbatim — landing that generator "would leave
`--record` refusing outright on any machine with more than one declared
account, rather than recording safely." The intended outcome is that
`--record` works normally on a multi-account machine, while still refusing
in the situation the guard actually exists to prevent.

## Approach

Replace the root-count-based refusal with a refusal keyed on the actual
remaining risk: whether the resolved ledger path sits inside a git working
tree. Multi-root recording proceeds normally (matching the union semantics
`docs/cost-ledger.md` already documents as intended) unless doing so would
write a multi-account figure into a path git could commit. This is
narrower than "shared" in the fully general sense — a cloud-sync folder
(Dropbox/iCloud/OneDrive) or a bare-repo dotfile manager (yadm-style,
tracking `$HOME` via `--git-dir`/`--work-tree` flags rather than an
in-tree `.git`) can make a path just as shareable without tripping this
check. Closing those is disproportionate to a single-operator threat model
(no code check reliably detects either without false positives), so
`docs/cost-ledger.md` gets a documented-limitation note instead (see
Critical files).

**Why not the three shapes the prior investigation sketched:**
- *Drop the guard entirely* — rejected. It would satisfy "instead of
  refusing outright" literally, but the guard's original purpose (never let
  a multi-account figure land somewhere git could commit/push it) is still
  worth keeping now that it can be scoped precisely instead of scoped to
  every multi-root invocation.
- *Per-account rows / schema change* — rejected. The ledger's union content
  was never the defect; two separate merged commits say so directly
  (`c198742`, `0c46f4f` message text — see Row 5). Adding an account column
  changes `_COST_LEDGER_HEADER_LINE`/`_COST_LEDGER_SEPARATOR_LINE`, which
  `_parse_cost_ledger_file_text` matches by exact string equality — every
  existing ledger file would fail to parse until manually migrated, and
  this file has no schema-versioning mechanism to land that safely. That's
  a materially bigger, riskier change than the problem calls for.
- *Keep union, gate behind an explicit acknowledgment flag* — rejected.
  This would satisfy "not outright" literally but not "safely": it adds a
  flag the operator must remember to pass, with no guard behind it, so a
  scripted or muscle-memory `--record --acknowledge-multi-account` reintroduces
  exactly the silent-commit risk the guard exists for. A condition-based
  guard is safer than an opt-out flag with no underlying check.

```
Root: cost-ledger --record refuses outright (exit 2) whenever more than
one Claude account is in scope, even though the specific risk that guard
was built for — silently committing a multi-account-union dollar figure to
a public, git-tracked file — no longer exists by default now that ledger
storage lives at a local, non-git-tracked path.

Givens: ledger storage defaults to $CLAUDE_CONFIG_DIR/cost-ledger.md (or a
COST_LEDGER_PATH override), never git-tracked by default — beyond reach:
PR #637 already redesigned and merged this as its own separately-planned
and separately-reviewed effort; this plan's Critical Files don't touch
`_cost_ledger_path()`'s resolution logic at all, so re-deciding storage
location would mean reopening a decision outside this plan.

Row 1 [mechanism]: replace `if len(roots) > 1: sys.exit(2)` with a guard
that refuses only when len(roots) > 1 AND the resolved ledger path sits
inside a git working tree — anchors: root — targets the actual remaining
risk (a union figure landing somewhere committable/pushable/shareable)
instead of root count.
Row 2 [assumption]: git-tracked-ness of ledger_path is determinable at
write time via `git -C <nearest existing ancestor of ledger_path> rev-parse
--is-inside-work-tree`, which refines PR #637's plan's stated reason for
leaving this guard unconditional ("cannot know at write time whether a
given configured destination is actually private") — "inside a git working
tree" is a narrower, checkable question than "private," and this file
already uses the identical `git rev-parse` idiom for an analogous
repo-identity check. [verified:
claude/.claude/scripts/transcript-analysis.py:2408-2409 uses `git
rev-parse --show-toplevel` for the same purpose] — anchors: row1
Row 3 [assumption]: no override/escape-hatch flag is added for the new
git-tracked refusal — anchors: row1 — every sibling multi-root guard in
this file (cost --summary's mechanism 1, cost --no-redact, subagent-mix
--per-session) refuses outright with no bypass flag once its risky
condition is met; adding one here would be a new pattern, not a
consistent one. [verified:
claude/.claude/scripts/transcript-analysis.py:5583-5594 (cost --summary),
5617-5622 (cost --no-redact), 2980-2992 (subagent-mix --per-session), this
session]
Row 4 [assumption]: an ambiguous git-check result (subprocess timeout,
missing git binary, or a non-zero exit that isn't the expected "not a git
repository" signal) fails closed — refuses — mirroring this file's
existing subprocess-based guard's own fail-closed posture on failure.
[verified: claude/.claude/scripts/transcript-analysis.py:2352-2368,
2404-2420] — anchors: row1
Row 5 [assumption]: the ledger row schema (columns, week+machine dedupe
key in _upsert_cost_ledger_row) needs no change — anchors: root —
[verified: the module-level `_COST_LEDGER_HEADER_LINE`/
`_COST_LEDGER_SEPARATOR_LINE` constants and `_parse_cost_ledger_file_text`'s
exact-string header match on them (transcript-analysis.py) show a fixed
10-column schema with no versioning/migration mechanism; changing it would
break every existing ledger file's header match, a cost this plan doesn't
need to pay since the union content itself isn't the defect (see
Approach's "why not per-account rows")]
Row 6 [assumption]: --machine-label stays an opaque per-machine label, not
repurposed to carry account identity — anchors: row5 — [verified:
`_MACHINE_LABEL_RE` and its hostname-collision check
(`machine_label.lower() == socket.gethostname().lower()`) in
`_cost_ledger_report`, plus docs/cost-ledger.md's `machine` column
description — none of which this plan's Critical Files touch]
```

## Critical files

- **`claude/.claude/scripts/transcript-analysis.py`**
  - Add a helper (near the other ledger helpers, ~line 7298) —
    `_ledger_path_is_git_tracked(ledger_path: Path) -> bool` — that walks up
    from `ledger_path.parent` to the nearest existing ancestor (the ledger
    file/dir may not exist yet on a first `--record`; the walk always
    terminates at `/`, which always exists), then runs `git -C <ancestor>
    rev-parse --is-inside-work-tree` with `capture_output=True, text=True,
    timeout=10, check=False` — reuse the subprocess/timeout/exception idiom
    already established at transcript-analysis.py:2352-2368/2404-2420, not
    a fresh implementation. Two additions to that idiom, both required by
    plan-review findings:
    - Pass an explicit `env`: a copy of `os.environ` with `GIT_DIR`,
      `GIT_WORK_TREE`, and `GIT_INDEX_FILE` **removed** (not merely
      emptied — an empty value is not the same as unset to git) so a
      `GIT_DIR`/`GIT_WORK_TREE` exported earlier in the caller's shell
      session can't redirect the check to an unrelated repo, plus
      `LC_ALL=C` forced so git's fatal-error text is stable English
      regardless of the operator's locale (git does not guarantee
      machine-parseable error text otherwise).
    - Branch on the **four** outcomes verified empirically this session,
      not three — a bare repository exits 0 with stdout `"false"`, which
      is neither the "true" branch nor a non-zero exit, and the original
      three-way spec left it falling through an ambiguous catch-all:
      1. exit 0, stdout strips to `"true"` → `True` (tracked, refuse)
      2. exit 0, stdout strips to anything else (e.g. `"false"` — a bare
         repository or similar non-worktree context) → `False` (not a
         normal work tree, proceed)
      3. non-zero exit, stderr contains git's `LC_ALL=C` "not a git
         repository" text → `False` (cleanly not tracked, proceed)
      4. anything else (a non-zero exit not matching #3, `TimeoutExpired`,
         `FileNotFoundError`, `OSError`) → `True` (fail closed, refuse)
  - In `_cost_ledger_report`, replace the `if len(roots) > 1:` block's
    unconditional `sys.exit(2)` with a check that also calls
    `_ledger_path_is_git_tracked(ledger_path)`, refusing (exit 2, same
    convention as the sibling multi-root guards) only when both are true.
    Use two distinguishable stderr messages, not one — an operator hitting
    branch 4 above (e.g. git missing from `PATH`) needs to know the check
    itself failed, not that their path is confirmed git-tracked, or they'll
    troubleshoot the wrong thing. Neither message echoes the resolved path
    itself, matching this function's existing redaction discipline for
    home-rooted paths (the "no ledger recorded here yet" message earlier in
    the same function).
  - Reuse opportunities: the subprocess+timeout idiom above; the existing
    exit-code convention (2 for a policy refusal, matching every sibling
    multi-root guard, vs. 1 for validation errors elsewhere in this
    function).

- **`claude/.claude/scripts/tests/test_transcript_analysis.py`**
  - `test_record_refuses_when_more_than_one_root_in_scope` (in
    `TestCostLedgerSentinelGate`; shipped as
    `test_record_refuses_when_multi_root_and_ledger_path_git_tracked`)
    currently uses a plain `tmp_path` ledger fixture, which is not
    git-tracked — under the new guard this scenario must now succeed, not
    refuse. Repurpose/rename it to cover the new condition: `git init`
    `tmp_path` directly — `cost_ledger_file`'s ledger
    path is `tmp_path / "cost-ledger.md"`, so `tmp_path` itself is the
    directory to initialize, matching the existing real-git fixture
    precedent already used elsewhere in this file (e.g.
    `test_agents_md_over_limit_produces_enumerated_label`,
    `test_skill_md_over_limit_produces_enumerated_label`) rather than
    scaffolding a new nested repo dir. Keep the exit-2 / no-row-appended
    assertions.
  - Add `test_record_succeeds_when_multi_root_and_ledger_path_not_git_tracked`
    covering the newly-unblocked case — the original fixture shape
    (plain `tmp_path`, two declared roots) — asserting a row IS appended.
  - Add `test_record_succeeds_when_multi_root_and_ledger_path_in_bare_repo`
    (or fold into the above as a second case) — `git init --bare` on
    `tmp_path` and confirm `--record` still succeeds, pinning decision-table
    branch 2 (exit 0, stdout `"false"`) against silently regressing into
    the fail-closed branch.
  - Add a test for decision-table branch 4's `TimeoutExpired` path
    (monkeypatch `subprocess.run` to raise it) confirming refusal, and a
    second branch-4 test for a non-zero exit whose stderr does **not**
    match "not a git repository" (e.g. a mocked permission-denied message)
    — both must fail closed, and the second case is the one most likely to
    silently flip if the stderr-matching string ever changes.
  - Add a case exercising `_ledger_path_is_git_tracked` from inside a
    linked worktree (`git worktree add`, not just `git init`) — this
    repo's own convention (`.claude/worktrees/<branch>/`) makes that the
    dominant real-world layout, and none of the above cases exercise a
    `.git` file (worktree pointer) instead of a `.git` directory.
  - Add a regression test pinning that `--force` cannot bypass the new
    guard: multi-root + git-tracked ledger path + `--force` still exits 2
    with no row appended — the guard sits before the `_upsert_cost_ledger_row`
    call in `_cost_ledger_report`, but nothing currently pins that ordering
    against a future change that special-cases `--force` to skip it.
  - Confirm existing single-root tests are unaffected (they should be,
    since the new guard is still gated on `len(roots) > 1`).

- **`docs/cost-ledger.md`** — add a short note (near the "Storage location"
  section) documenting that `--record` refuses under multi-root only when
  the resolved ledger path is inside a git working tree, and that the
  default path isn't one — this condition was previously undocumented
  outside plan files and an inline code comment. Also document the
  detection's known blind spots named in review: a cloud-sync folder
  (Dropbox/iCloud/OneDrive) and a bare-repo dotfile manager (yadm-style,
  tracking a path via `--git-dir`/`--work-tree` rather than an in-tree
  `.git`) are both git-invisible to this check and won't trigger the
  refusal even though they're similarly shareable.

## Verification

From the worktree: `../../../.venv/bin/pytest claude/.claude/ -k cost_ledger`
for the targeted suite, then the full `../../../.venv/bin/pytest claude/.claude/`
before handoff (per README.md's documented invocation for a linked
worktree). Manually confirm on this machine: with
`~/.claude/transcript-config-dirs` populated (already true here) and
`COST_LEDGER_PATH` unset (default, non-git-tracked path),
`cost-ledger --record --machine-label <label>` succeeds instead of exiting 2.

## Out of scope

- **Extending the same git-tracked check to the single-root case.** PR
  #637's plan flagged (CISO, low severity) that an operator could still
  misconfigure `COST_LEDGER_PATH` into another git-tracked repo even with
  one account in scope, and left it deliberately unaddressed. This plan's
  new check would also close that gap if applied unconditionally, but
  doing so changes single-root behavior that wasn't asked for and that no
  prior plan scoped as broken. This is the second plan in a row to defer
  it inline rather than close it, and the marginal cost of applying the
  same helper unconditionally is low (the helper already exists and needs
  no new logic) — not low enough to fold in unasked, but low enough that a
  third silent deferral isn't warranted; if declined again, it should
  become a tracked follow-up (issue or plan stub) rather than prose in a
  third plan's Out of Scope section.
