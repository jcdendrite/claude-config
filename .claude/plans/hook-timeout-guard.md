# Close the fail-open `timeout` gap in gate (and informational) hooks

## Context

Four `hook-class: gate` hooks call `timeout` bare instead of through the
repo's `command -v`-guarded wrapper, so on any machine without GNU
`timeout(1)` — stock macOS, which `claude/` is stowed onto for every
contributor — the call exits 127, the surrounding command substitution
captures nothing, and each hook silently reaches its "nothing to enforce"
path instead of its fail-closed one. This work closes that gap so the four
gates (and, per the engineer's decision below, two informational hooks with
the identical bug shape) enforce correctly on stock macOS. It unblocks
publication of `PR #614` (the audit report describing this exact gap in the
present tense), which is deliberately held behind this fix landing first.

This closes the *accidental* trigger (a contributor machine that happens to
lack GNU `timeout`); it does not and cannot close the *deliberate* one — an
actor who controls `PATH` and strips both `timeout` and `gtimeout` gets the
same "runs uncapped, unbounded" outcome today and after this fix. That's
unchanged and out of scope: the four hooks' own headers already declare a
cooperative-agent threat model ("the agent is cooperative, not attacking the
gate," `require-worktree-for-git-writes.sh:14-19`), not an adversarial one.

## Approach

**Root problem:** the four gate hooks (`guard-settings-session-keys.sh`,
`require-worktree-for-git-writes.sh`, `check-claude-md-length.sh`,
`check-skill-length.sh`) invoke `timeout N cmd` directly at 13 call sites
instead of through `_lib_capped`, the repo's established
`command -v timeout`-guarded wrapper — so on a machine lacking `timeout(1)`,
the bare call fails with "command not found" (127) and each hook's
downstream logic treats the resulting empty output as "nothing staged" /
"repo root not found" rather than "tooling failed," silently declining to
enforce.

**Givens** (conditions this design treats as fixed, each with a reason it's
outside this design's reach):

| # | Given | Reason |
|---|---|---|
| G1 | Stock macOS ships no `timeout(1)` (arrives only with GNU coreutils, e.g. via Homebrew) | Apple's own OS distribution choice — outside this repo's reach |
| G2 | Homebrew installs GNU coreutils `g`-prefixed by default (`gtimeout`, not `timeout`) | Homebrew's own naming convention, cited from `check-branch-divergence.sh:61-71`'s existing `TIMEOUT_CMD` probe, which already codifies this — outside this repo's reach |

**Ledger row** (not a given — this is a condition the plan could technically
change by editing this repo's own call sites, but declines to, per the
engineer's own standing scope-discipline rule):

| Row | Assumption | Tag |
|---|---|---|
| R1 | `_lib_capped`'s existing name and `_lib_capped cmd [args...]` signature stays unchanged. [verified: grep audit found ~25 existing call sites across 9 hook files (`ask-new-dependency-disclosure.sh`, `deny-reviewer-tree-mutation.sh`, `advance-past-commit-stall.sh`, `deny-pii-in-commits.sh`, `require-plan-review.sh`, `require-respond-pr.sh`, `nudge-worktree-anchor.sh`, `nudge-handoff-near-context-cap.sh`, `_lib.sh` itself) that would all need updating for a signature change unrelated to the fail-open bug this plan fixes — CLAUDE.md's Scope Discipline Axis 4 ("prefer minimal, targeted changes... do not expand scope beyond what was asked") and the brief's own explicit "keep the function's current name and signature" directive both back declining that churn here. | `[engineer-verified]` |

**Mechanism 1 — teach `_lib_capped` (and a new duration-parameterized
sibling) the `gtimeout` probe.** `anchors: root`.

`_lib_capped` and `_lib_jq` (`_lib.sh:14-20,29-35`) each independently probe
only `command -v timeout`, matching G2's gap: on a Homebrew-coreutils
machine they find nothing and run uncapped, while
`check-branch-divergence.sh`'s local `TIMEOUT_CMD` wrapper (`:61-71`)
already probes `timeout` then `gtimeout`. Six of the newly-included sites
(`nudge-handoff-near-context-cap.sh` x4 at `timeout 2`,
`nudge-error-mode-analysis.sh` at `timeout 10`) use a duration other than
`_lib_capped`'s fixed 5s, and R1 forbids changing `_lib_capped`'s signature
to accept one. The fix extracts the probe-then-run logic into a new
`_lib_capped_for <seconds> <command> [args...]` helper; `_lib_capped`
becomes `_lib_capped_for 5 "$@"` (unchanged behavior for all ~25 existing
callers beyond gaining the `gtimeout` probe) and `_lib_jq` becomes
`_lib_capped_for 5 jq "$@"` (removes its own duplicate probe — the
identical bug shape flagged in the brief as "itself weaker than a sibling
wrapper," per CLAUDE.md's audit-structural-siblings rule). Call sites that
need a different duration (2s, 10s) call `_lib_capped_for` directly.
`_lib_capped_for` reads its duration as `local seconds="${1:?_lib_capped_for
requires a seconds argument}"` per `shell-script-conventions.md`'s
required-inputs rule — all 19 call sites pass a literal, but a bare `"$1"`
would fail with an opaque `timeout` argument-count error instead of a clear
one if a future caller omits it.

Two lighter primitives considered and rejected:
- **Change `_lib_capped`'s own signature to take an optional leading
  duration.** Rejected: R1 forbids it — a duration-shaped first token would
  silently break any of the ~25 existing `_lib_capped git ...`-style calls
  where `git` (or another literal command name) is the first argument.
- **Duplicate the 3-line probe-then-run block locally at each of the 6
  duration-mismatched call sites instead of a shared helper.** Rejected:
  the brief's own framing of the underlying defect — "callers bypass
  [`_lib_capped`]... it is the established pattern in the hook layer" — is
  exactly the single-source-of-truth argument against re-duplicating that
  same pattern 6 more times inline; a shared parameterized helper is the
  established idiom this file already uses everywhere else.

**Mechanism 2 — route the 13 brief-identified bare-`timeout` sites through
`_lib_capped`.** `anchors: row R1`.

`guard-settings-session-keys.sh`'s local `git_capped()` is deleted in favor
of `_lib_capped git`; `require-worktree-for-git-writes.sh`,
`check-claude-md-length.sh`, and `check-skill-length.sh` route their bare
`timeout 5 ...` sites through `_lib_capped` directly (all four hooks already
source `_lib.sh` before these call sites run). No lighter primitive applies
here — this is the direct fix for the defect described in Context, using
the mechanism Mechanism 1 just repaired.

**Mechanism 3 — route the six informational-hook sites through
`_lib_capped_for` at their existing durations.** `anchors: root`.
Per the engineer's decision (see below), `nudge-error-mode-analysis.sh:152`
(`timeout 10`) and `nudge-handoff-near-context-cap.sh`'s four sites
(`timeout 2`, synchronized per that file's own `:396` comment) get the
identical mechanical fix, preserving each site's original duration via
`_lib_capped_for <n> ...` rather than silently shifting it to 5s.

**Mechanism 4 (incidental, in-file) — fold `_lib_stray_marker_hint`'s
inline probe into `_lib_capped`.** `anchors: root`.
`_lib.sh:809-818` (`_lib_stray_marker_hint`) already correctly branches on
`command -v timeout` before falling back to bare `git` — not the 127-crash
bug this plan otherwise fixes — but it duplicates the exact probe logic
`_lib_capped` now centralizes, and its own comment says "5s timeout backstop
mirrors `_lib_jq` (line 14)," acknowledging the duplication. Replacing its
body with `_lib_capped git -C "$repo_root" ls-files ...` removes the
duplicate and gains the `gtimeout` probe for free, with no behavior change
when `timeout` is present. In-file scope (Axis 2), same function being
edited for Mechanism 1 — not a new file touched for this alone.

**Decisions the engineer made this session** (brief's §5, all four
resolved before writing code, plus a fifth raised and resolved mid-review):
1. Behavior-change carve-out from the wider repo-quality-audit's
   "structural only, zero observable behaviour change" scoping: **granted**
   — a gate that starts firing on a platform where it was silently bypassed
   is this fix's entire point. `[engineer-verified]`
2. Six informational-hook sites: **included in this change** — same
   mechanical fix, and `nudge-handoff-near-context-cap.sh` already mixes
   `_lib_capped` (its `ps` calls) and bare `timeout` inconsistently within
   one file. `[engineer-verified]`
3. `check-branch-divergence.sh`'s local `TIMEOUT_CMD` wrapper: **left
   alone**, only the `gtimeout`-probe idea is ported into `_lib_capped`. Its
   fail-absent contract (skip the network `git fetch` outright) is
   deliberately safer than `_lib_capped`'s "run uncapped" contract for a
   bounded local read — folding would silently remove that protection from
   an unbounded network call. `[engineer-verified]`
4. `check-claude-md-length.sh` / `check-skill-length.sh` fail-open path:
   **stays silent**, no new stderr warning — scope stays on the guard fix;
   a warning is a distinct, separable improvement.
   `[engineer-verified]`
5. **Considered and rejected: deny outright (rather than run uncapped) at
   the four gate hooks' own call sites when neither `timeout` nor `gtimeout`
   is present.** Tracing it site-by-site showed it doesn't close a remaining
   gap — 3 of `require-worktree-for-git-writes.sh`'s 4 sites (`:135,177,283`)
   already fail closed today on empty/failed output (only `:115` is a true
   silent-allow), and Mechanism 1+2 alone already makes every one of the 13
   sites evaluate correctly in the ordinary (non-hanging) case. Denying
   outright would instead trade a rare git-hang risk for a guaranteed block
   of every git commit/write on any machine lacking both binaries — and
   would regress `require-worktree-for-git-writes.sh`'s *existing* bug
   (today, on such a machine, `:135` already cascades into denying every git
   command, reads included, before the read-only fast path ever runs; gtimeout
   probing alone fixes this for any Homebrew-coreutils machine) back to
   broken for the narrower population still lacking both binaries. Documented
   as a residual, accepted hang risk instead (see Critical Files). `[engineer-verified]`

## Critical files

- `claude/.claude/hooks/_lib.sh`
  - `_lib_capped` (`:29-35`) and `_lib_jq` (`:14-20`): extract shared
    probe-then-run logic into new `_lib_capped_for <seconds> <cmd...>`;
    both become 1-line delegations at their historical 5s duration. Update
    both functions' header comments (currently "Fallback to bare jq when
    timeout(1) is absent" / similar, `:9,22-27`) to state the `gtimeout`
    probe — the existing text describes only the old timeout-only fallback
    and goes stale the moment the probe gains a second binary. Carry
    forward `_lib_capped`'s existing "Callers MUST check the exit status"
    contract comment onto `_lib_capped_for` itself (not just `_lib_capped`),
    since the six newly-routed informational-hook call sites are less
    exit-status-disciplined than the four gate hooks that established the
    contract.
  - `_lib_stray_marker_hint` (`:809-818`): replace its inline
    `command -v timeout` branch with a single `_lib_capped git ...` call.
  - **Reuse:** no new external dependency — `command -v`, the existing
    `timeout`/`gtimeout` binaries, nothing else.
- `claude/.claude/hooks/tests/test_lib.py` — add `_lib_capped_for` /
  `_lib_capped` coverage for all four PATH states: `timeout` present,
  `gtimeout`-only, neither, **and both present** (the 4th case — asserts
  `timeout` wins precedence over `gtimeout` via a distinguishing side
  effect, not just "didn't error"; without it a swapped `if`/`elif` probe
  order would pass the other three cases unnoticed). Follow the existing
  `test_hung_jq_denied_within_timeout` / `test_timeout_absent_fallback_...`
  symlinked-PATH pattern in this same file (`:115-179`).
- `claude/.claude/hooks/guard-settings-session-keys.sh` — delete local
  `git_capped()` (`:29-31`); replace its 5 call sites (`:66,74,80,81,84`)
  with `_lib_capped git ...`; trim the now-stale 5-line rationale comment
  above it to the one-line pointer `_lib_capped` itself already documents.
  Add a one-line header note (this hook has no existing "Known gaps"
  section), stated as a plain fact with no reference to this plan or its
  decision numbering: on a machine lacking both `timeout` and `gtimeout`,
  `_lib_capped` runs these git calls uncapped, so a stalled git (locked
  index, network mount) hangs this gate rather than degrading gracefully.
- `claude/.claude/hooks/require-worktree-for-git-writes.sh` — route
  `:115,135,177,283` through `_lib_capped` (`:177` wraps `python3`, not
  `git` — `_lib_capped` takes an arbitrary command). Add one bullet to the
  existing "Known gaps" section (`:50-62`) noting these four sites now share
  the same uncapped-when-both-absent hang risk, rather than rewriting the
  existing bullets there.
- `claude/.claude/hooks/check-claude-md-length.sh` — route `:72,73`
  through `_lib_capped`. Add the same one-line uncapped-hang-risk note as
  `guard-settings-session-keys.sh` above (no existing "Known gaps" section).
- `claude/.claude/hooks/check-skill-length.sh` — route `:73,74` through
  `_lib_capped`. Same one-line note.
- `claude/.claude/hooks/nudge-error-mode-analysis.sh` — route `:152`
  through `_lib_capped_for 10 python3 ...` (preserves the existing 10s
  budget; **not** `_lib_capped`, which is fixed at 5s).
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — route
  `:146,178,253,275,398` through `_lib_capped_for 2 ...` (preserves the
  existing 2s budget, cross-referenced by this file's own `:396` comment).
- New regression test **pair** per affected gate hook (`test_lib.py`'s
  existing test files for `guard-settings-session-keys.sh` / length hooks,
  and `test_require_worktree_for_git_writes.py`), stubbing a PATH with
  neither `timeout` nor `gtimeout`: (a) a violating scenario asserting the
  hook still denies, following `test_require_worktree_for_git_writes.py:
  665-692`'s `test_python3_absent_denies` shape (stub-dir-of-symlinks,
  `pytest.skip` when a needed real binary is itself absent from the test
  machine); **and (b) a companion non-violating scenario under the same
  stubbed PATH asserting the hook still allows** — without (b), a bug that
  makes the fallback branch return nonzero unconditionally (turning the
  fixed fail-open bypass into a fail-closed lockout that denies every
  invocation once both binaries are absent) would pass (a) and go
  undetected. Each new stub-dir tool enumeration carries a one-line comment
  naming the code path traced to build it, mirroring
  `test_python3_absent_denies`'s docstring, so a later maintainer can tell
  a stub-list-drift failure from a real regression.
- Same neither-binary PATH-stub regression test for the two informational
  hooks (`test_nudge_error_mode_analysis.py`,
  `test_nudge_handoff_near_context_cap.py` or their existing equivalents) —
  weaker than "enforces a deny" since these never deny, but asserting each
  still produces its intended nudge/output under the neither-binary state
  (not a silent no-op), symmetric with the four gate hooks rather than
  leaving their higher-frequency (every `UserPromptSubmit`/`Stop`, not just
  `git commit`) call sites untested.

**Out of scope for this diff** (left bare, matching the brief's exact
line references, not this bug's shape): `check-claude-md-length.sh:58` and
`check-skill-length.sh:57` (`REPO_ROOT=$(git rev-parse ...)`) call `git`
with no `timeout` wrapper at all — a pre-existing "no backstop" gap, not
the "backstop silently no-ops" bug this plan fixes.

## Verification

1. `../../../.venv/bin/pytest claude/.claude/ -q` — full suite, including
   the new `_lib_capped_for` unit tests and the four gate-hook regression
   tests.
2. `../../../.venv/bin/ruff check claude/.claude/`
3. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
4. Manual: on this machine, confirm `command -v gtimeout` reflects whatever
   Homebrew coreutils state is actually installed, and that
   `check-branch-divergence.sh`'s advisory (untouched by this diff) and the
   four gate hooks agree on which of `timeout`/`gtimeout` they each pick.
5. `/code-review`, then `/ready-for-review` before opening the PR (both
   hook-enforced in this repo).

**Commit structure.** Commit Mechanisms 1+2 (the shared helper plus the
four gate hooks — the piece blocking `PR #614`) separately from Mechanism 3
(the two informational hooks — an engineer-approved but lower-urgency
inclusion riding the same helper). Both commits land in the same PR, but
separating them means a maintainer can `git revert` the informational-hook
commit alone, without touching the gate-hook fix or the shared
`_lib_capped_for` helper it depends on, if a defect surfaces specifically
in the 2s/10s duration-preservation path.

## Out of scope

- **Phase 2b** — extracting a `_lib_repo_root` helper for the twelve
  `git rev-parse --show-toplevel` call sites. Separate PR per the
  repo-quality-audit plan; keeping this PR small is what lets it merge
  ahead of PR #614.
- **Phase 3** — reorganizing `_lib.sh` into delimited sections. Same file,
  unrelated cohesion change.
- `require-ready-for-review.sh:191` and
  `plugins/skill-management/hooks/require-skill-review.sh:202` — both are
  documented deliberate fail-open designs (network-hang and
  non-blocking-by-design respectively), not instances of this bug.
- Adding a macOS leg to CI, and wiring `plugins/` into CI's pytest/ruff
  steps — real findings from the same audit, separate phases.
- A stderr warning on `check-claude-md-length.sh` / `check-skill-length.sh`'s
  fail-open path (engineer decision 4, above).
- Updating `docs/reports/2026-08-10-repo-quality-audit/findings.md` on
  branch `repo-quality-audit` — happens after this PR merges, on that
  branch, per the brief's step 13; not part of this PR's diff.
