# Handoff skill: push mechanical work into deterministic scripts

## Context

The `handoff` skill (`claude/.claude/skills/handoff/SKILL.md`) spends model
tokens re-deriving, by reading and reasoning, two things a script can already
answer exactly: which review-gate markers currently cover the repo's state
(§5 "Gates / markers"), and which items of its 18-line "Pre-write checklist"
already hold (preamble text, placeholder scan, unresolved tokens, §3 vs §3.5
mis-categorization). The goal is to replace both with calls to deterministic
scripts, so the model only spends tokens on the genuinely semantic judgment
calls the checklist can't mechanize (status/next-step consistency, task-list
fidelity, and the like).

## Approach

Extend `marker.sh` with a new read-only `status` subcommand that reports
every marker relevant to §5 (live, historical, or absent) by reusing the exact
hash-recipe functions the gate hooks already use — so the report can never
disagree with what a gate will actually decide. Separately, add a new
`check-handoff.py` linter that mechanically verifies the checkable half of
the pre-write checklist against a draft handoff file, printing PASS/FAIL for
each mechanical item and an explicit residual list of the items that still
need the model's own judgment. Both are additive, non-gating scripts — no new
PreToolUse hook, no change to what's currently allowed or denied.

**Root problem:** the handoff skill asks the model to hand-compute state
(marker liveness, checklist compliance) that a script can compute exactly
from the same inputs the gate hooks already read.

**Givens:**
- G1: `claude/.claude/hooks/_lib.sh`'s hash-recipe functions
  (`_marker_lib_repo_hash`, `_lib_marker_value_present`,
  `_lib_active_plan_hash`, `_lib_active_bypass_marker_live`) are the
  authoritative definition of "what state a marker covers" — any new
  reporting surface must call these, not reimplement them, or it can drift
  from what the gates themselves decide. [reason: these functions are already
  the read side every `require-*.sh` hook depends on; a plan-local
  reimplementation isn't this plan's to invent]
- G2: `enforce-marker-script-shape.sh`'s single `MARKER_SHAPE` regex
  (line 332) is the existing security control gating which `marker.sh`
  invocation shapes may run without a manual permission prompt; adding a
  subcommand requires extending it, not routing around it. [reason:
  pre-existing control that predates this plan]
- G3 [engineer-verified]: scope is both the marker-status subcommand and the
  pre-write-checklist linter (this session's `AskUserQuestion` answer, not
  the narrower marker-only option).

**Per-mechanism justification:**

1. `marker.sh status` subcommand — anchors: root
   - Lighter alternative A: a wholly new standalone script duplicating the
     hash recipes. Rejected — violates G1; a second implementation of "is
     this marker live" is exactly the drift risk the gates can't afford.
   - Lighter alternative B: extend `session-marker-dashboard.sh` instead,
     since it already reports a lighter (mtime-based, session-scoped-only)
     version of this. Rejected — that script is a `SessionStart` hook reading
     hook-payload JSON off stdin, not a script meant to be invoked ad hoc
     mid-session by name; it also only covers the four *active-bypass*
     markers, never the four *completion* markers (`code-review-markers/`
     etc.) §5 also has to report. `marker.sh` is already the CLI surface for
     every marker operation and already contains every hash recipe a
     `status` subcommand needs — no new abstraction required, just a new
     read-only case arm.

2. `check-handoff.py` checklist linter — anchors: root
   - Lighter alternative A: a bash script mirroring `marker.sh`'s style.
     Rejected — markdown-section-aware parsing (finding §-headers, matching
     fenced blocks, scanning for anchor-shape substrings) is materially
     easier to keep correct in Python, and the repo already has precedent
     for exactly this shape (`analyze-context.py`, `token-analyzer.py`)
     rather than forcing multi-stage text parsing into `awk`/`sed`.
   - Lighter alternative B: a new `PreToolUse` gate blocking the `Write` that
     creates the handoff file until checklist items pass. Rejected — this is
     advisory linting over a draft the model is still iterating on inline
     (there is no single tool call marking "the handoff is done" the way
     `git commit` marks a review's endpoint); gating a mid-draft `Write`
     would make normal incremental authoring impossible.

**Material assumptions:**
- [verified: `claude/.claude/hooks/_lib.sh`] `_lib_marker_value_present`,
  `_lib_active_bypass_marker_live`, `_marker_lib_repo_hash`,
  `_lib_active_plan_hash` exist and are the read-side source of truth used by
  every `require-*.sh` hook today.
- [verified: `claude/.claude/scripts/marker.sh:190-267`] the write-side hash
  recipe per skill: code-review = `git diff --cached | sha256sum`;
  skill-review = the same, scoped to `claude/.claude/skills/**/SKILL.md` /
  `plugins/*/skills/**/SKILL.md` / `plan-review/ROUTING.md`; plan-review =
  the plan-mode sibling file's hash, or `_lib_active_plan_hash`;
  ready-for-review = `git rev-parse HEAD`.
- [verified: `claude/.claude/hooks/require-memory-skill.sh:126`,
  `require-ready-for-review.sh:179`, `require-respond-pr.sh:85`,
  `require-plan-review.sh:179`] the four active-bypass marker directories are
  `.plan-review-active.d`, `.ready-for-review-active.d`,
  `.respond-pr-active.d`, `.memory-skill-active.d`, each keyed by session id
  and checked via `_lib_active_bypass_marker_live`.
- [verified: `claude/.claude/hooks/enforce-marker-script-shape.sh:332`] the
  single authoritative `MARKER_SHAPE` regex must gain a `|status`
  alternation to permit the new subcommand at the same no-arg friction level
  as the existing `resolve-session-id` shape.
- [verified: `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`]
  `TILDE_MARKER_SHAPES` (the list backing both `test_valid_shapes_allowed`
  and the `TestPrescriptionAllowlistAlignment` cross-check against
  `settings.json`) must include the new shape or the suite will flag
  misalignment on its own.
- [verified: `claude/.claude/tests/helpers.py:39-63`] `extract_skill_command`
  already extracts a `HOOK_TEST_FIXTURE`-anchored fenced block from a
  SKILL.md at test time, so SKILL.md stays the single source of truth for
  the artifact preamble text; `check-handoff.py` reads the same anchored
  block from the live file at runtime via its own small extraction function
  (duplicating the generic ~10-line extraction mechanism across a test
  helper and a production script, never the preamble prose itself — the
  kind of small, structural duplication CLAUDE.md's DRY exception allows
  over building a shared import path between test and production code for
  it).
- [unverified] no existing script in `claude/.claude/scripts/` already
  implements markdown-section-aware parsing reusable for `check-handoff.py`;
  checked the directory listing but did not exhaustively grep every script
  for a generic "parse markdown sections" helper.
- [verified: `docs/scripts.md:46`] this line already understates the current
  shape count ("12 valid invocation shapes") against the 15 the test file
  enumerates today — pre-existing drift, not something this plan introduces,
  but the plan's own addition pushes the true count to 16 and touches this
  exact fact, so it's an in-scope incidental fix (Axis 1 bucket 2).
- [verified: `claude/.claude/hooks/tests/test_marker_script.py`, confirmed via
  direct `find`] the existing marker.sh test suite lives under
  `claude/.claude/hooks/tests/`, not `claude/.claude/scripts/tests/` — an
  earlier draft of this plan named the wrong path; `scripts/tests/conftest.py`
  is a separate pytest rootdir with unrelated fixtures per its own docstring.
- [verified: `claude/.claude/hooks/_lib.sh:774-779`] `_lib_active_bypass_marker_live`
  evicts (`rm -f`) a stale marker as a side effect of checking liveness —
  inherited by every existing caller, and by `status` via the same function;
  the plan's "read-only" framing needed correcting to scope this precisely.
- [verified: `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py:606-718`]
  the existing `resolve-session-id` test block (all-`NO_GATE_RELEASE_AGENTS`-
  types-allowed + extra-arg-denied pattern) is the template `status`'s own
  tests must mirror.

## Critical files

**`claude/.claude/scripts/marker.sh`**
- Add a `status` subcommand (no `ARG2`, alongside `resolve-session-id` in the
  arg-count case): for the current repo, recompute each completion marker's
  current expected value (reusing the exact per-skill recipes already in the
  `write` case arms) and report live/historical/absent via
  `_lib_marker_value_present`; for the current session, report each
  active-bypass marker via `_lib_active_bypass_marker_live`.
- **Repo isolation is non-negotiable, for both live and historical
  detection.** Every marker path this reads is `$CONFIG_DIR/<kind>-markers/`,
  which is shared across every repo ever opened under this config dir.
  "Historical" (marker exists, value stale) must be detected the same way
  "live" already is — by globbing `$REPO_HASH.*` (the exact prefix
  `_lib_marker_value_present`'s existing callers already scope to), never by
  listing the marker directory unscoped. A `status` run in repo A must never
  read or report a marker filename, hash, or session id that belongs to
  repo B.
- **Active-bypass markers are not purely read-only.** `_lib_active_bypass_marker_live`
  already evicts (`rm -f`) a stale (dead-PID) marker as a side effect of
  checking liveness — every existing caller (`require-plan-review.sh` etc.)
  inherits this, and `status` does too by reusing the same function. State
  the plan's "read-only" framing precisely: completion-marker reads never
  write; active-bypass reads may evict an already-dead marker, matching
  every other consumer of this function today (not a new side effect this
  plan introduces).
- **The reconciliation flag (live marker + uncommitted overlapping
  changes) applies only to code-review and skill-review markers** — the two
  whose value is a hash of a `git diff` over a real pathspec (whole repo for
  code-review, the SKILL.md pathspecs for skill-review), so "uncommitted
  changes overlapping it" is well-defined. It does not apply to
  ready-for-review (HEAD-keyed, post-commit — there is no pathspec to
  overlap; the next commit simply changes HEAD and the marker stops
  matching) or to plan-review (already has its own live/historical
  distinction via `_lib_active_plan_hash`'s tracked-vs-modified-vs-untracked
  logic, which is a different and already-correct mechanism — reuse it, don't
  re-derive it).
- **Output format:** print state labels only (live / historical / absent,
  which skill, repo-hash-scoped — never a literal `$CONFIG_DIR`-rooted
  path) and describe state mechanically ("hash matches the current staged
  diff" / "hash does not match") — never language implying review
  provenance ("reviewed," "authorized," "passed"). A matching hash shows the
  state is unchanged, not that anyone reviewed it (the same distinction
  `enforce-marker-script-shape.sh`'s own `GATE_RELEASE_DENIAL_GUIDANCE`
  comment makes). This output is what §5's SKILL.md rewrite has the model
  paste verbatim into a handoff file, so it must already read safely
  out of context.
- Note (no action needed): the active-bypass report reflects the *parent*
  session's state when `status` runs inside a subagent, via the same
  process-ancestor `SESSION_ID` walk every other `marker.sh` operation
  already uses — existing behavior, not a new property of this subcommand.
- Update the `usage()` help text's subcommand list and valid-combination
  table.
- **Reuse:** `_marker_lib_repo_hash`, `_lib_marker_value_present`,
  `_lib_active_plan_hash`, `_lib_active_bypass_marker_live`,
  `_resolve_repo_root`, `_resolve_session_id`, `_lib_config_dir` — all
  already sourced/defined in this file or `_lib.sh`. No new hash logic.

**`claude/.claude/hooks/enforce-marker-script-shape.sh`**
- Add `|status` to the `MARKER_SHAPE` regex (line 332) as its own top-level
  alternative (a sibling of `resolve-session-id`, not nested inside the
  `write`/`activate|deactivate` skill-name groups) and to the usage/help
  listing of valid shapes (~line 392-404).
- Fix the two in-file shape-count comments this addition invalidates: line 46
  ("...must match one of the 15 single-command shapes...") → 16, and line 373
  ("...the 13 shapes in permissions.allow...") → 14.

**`claude/.claude/settings.json`**
- Add `"Bash(~/.claude/scripts/marker.sh status)"` to `permissions.allow`,
  adjacent to the existing `resolve-session-id` entry. (This is a
  `permissions.allow` change — `/review-permissions` fires automatically at
  `/code-review` time per this repo's own dispatch rule; no separate action
  needed here.)

**`claude/.claude/hooks/tests/test_marker_script.py`** (not `scripts/tests/` —
this is the actual, existing file; `claude/.claude/scripts/tests/` is a
separate pytest rootdir with unrelated conftest fixtures, per its own
docstring)
- Extend in place, reusing its existing `isolated_home`/`git_repo`/
  `_seed_session` fixtures (shared via `claude/.claude/hooks/tests/conftest.py`)
  rather than re-deriving them. New cases for `status`:
  - Each completion-marker type (code-review, skill-review, plan-review,
    ready-for-review): live (hash matches), historical (marker present, hash
    stale), absent (no marker file at all).
  - A cross-repo isolation case: seed a marker for repo A under a different
    `$REPO_HASH.` prefix, run `status` from repo B, assert repo A's filename,
    hash, and session id never appear in repo B's report.
  - A zero-commit repo for ready-for-review (no fixture in this suite builds
    one; `git_repo` always seeds a commit) — assert `status` reports "absent"
    cleanly rather than erroring when `git rev-parse HEAD` has nothing to
    resolve.
  - An unreadable marker file (`chmod 000`) — assert `status` reports it
    consistently with `_lib_marker_value_present`'s own documented
    swallowed-stderr behavior (treated as not-matching), not as a crash.
  - Each active-bypass marker: present (live PID), stale (dead PID — also
    assert the file is evicted, not just that the label reads "stale"),
    absent.
  - The reconciliation flag: one positive/negative pathspec-scoped pair each
    for code-review (whole-repo diff) and skill-review (SKILL.md pathspec
    only — an unstaged change *outside* that pathspec must not fire, mirroring
    `TestMarkerScriptEmptyStagedGuard`'s existing pathspec discipline); assert
    the flag is absent entirely for ready-for-review and plan-review markers.

**`claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`**
- Add `"~/.claude/scripts/marker.sh status"` to `TILDE_MARKER_SHAPES`.
- Add `test_status_allowed_for_main_session` and
  `test_status_allowed_for_restricted_subagent` (parametrized over
  `NO_GATE_RELEASE_AGENTS`), mirroring the existing `resolve-session-id` block
  at lines 696-718 — `status` is read-only and must be allowed for every
  agent type, the same as `resolve-session-id`.
- Add a `status`-with-extra-arg-denied test, mirroring `test_extra_arg_denied`
  / `test_memory_skill_extra_arg_denied`.
- Add a denied-chain-tail test (`status && rm -rf /`-shaped) confirming
  `status` cannot ride a chain to a non-`git commit`, non-marker-shape tail —
  same coverage every other op already has.

**`claude/.claude/scripts/check-handoff.py`** (new)
- Takes one arg: path to a draft handoff file. Mechanically checks: the
  artifact preamble matches (byte-for-byte, whitespace-normalized) the block
  extracted live from `claude/.claude/skills/handoff/SKILL.md` behind a new
  `<!-- HOOK_TEST_FIXTURE: artifact-preamble -->` anchor; no placeholder text
  ("TBD", "TODO", "fill in later"); §1–§7 headers all present with non-empty
  bodies; §7 contains no unresolved `<config-dir>`/`<slug>` literal tokens
  and its filename matches the file being checked. Soft (non-failing)
  warnings: §3 containing any of the §3.5 anchor-shape substrings — matching
  SKILL.md's own categorization-rule list verbatim: `gh pr merge`, `git push
  --force`/`git push -f`, `gh pr close`, `git branch -d`, `migrate`, `db
  push`, `db reset`, `gh release create`, `rm -rf`, `Slack`, `email`, `GitHub
  issue`, `GitHub PR comment` — as a "verify categorization" flag (a hit
  inside a quoted/cited example, not a planned action, is an accepted
  false-positive rate, not a bug); §2/§3/§6 containing zero confidence tags
  (`[engineer-confirmed]`, `[verified:`, `[assumed]`). Both the placeholder
  scan and the anchor-shape scan skip text inside inline code spans
  (`` `...` ``) and fenced code blocks — the natural way a draft would quote
  a literal string it's citing rather than proposing as a real next step —
  which bounds the common false-positive case without needing full
  quote/context parsing.
  Exits non-zero only on a hard-check failure. Prints, at the end, the fixed
  list of checklist items it does not and cannot check (status/next-step
  consistency, no premature "done" claims, §2.5/§2.6 content fidelity, the
  pr-description-skill-run condition, Bash-not-Read draft verification) so
  the model knows exactly what judgment is still on it.
- **Reuse:** the extraction mechanism mirrors (not imports)
  `claude/.claude/tests/helpers.py`'s `extract_skill_command` — same regex
  shape, independently implemented per the DRY-exception note above.

**`claude/.claude/scripts/tests/test_check_handoff.py`** (new)
- Follow this repo's established convention for testing a hyphenated `.py`
  script (`test_analyze_context.py`/`test_token_analyzer.py`'s
  `importlib.util.spec_from_file_location` pattern): import
  `check-handoff.py`'s individual check functions directly and unit-test each
  one's true/false boundary on a small in-memory string, not a full markdown
  fixture file per case. Reserve full-document, subprocess-level tests
  (invoking the CLI end-to-end) for wiring only: argv handling, exit codes,
  and the combined PASS/FAIL/warning printout — one or two cases, not one per
  check.
- Per hard check: a failing case and a passing case (preamble mismatch,
  placeholder text, missing/empty section, unresolved §7 token, §7 filename
  mismatch), plus one clean fixture passing every hard check.
- Per soft-warning check: a firing case and a non-firing case.
- **Near-miss / false-positive cases** for every substring-based check (both
  placeholder-text and §3.5 anchor-shape scans): the trigger string inside an
  inline code span or fenced block (e.g. citing `` `TODO:` `` from another
  file, or a §3 line quoting `` `rm -rf` `` as something explicitly *not* to
  run) must not fire; the same string outside code formatting, in plain
  prose, still fires — proving the code-span skip is scoped, not a blanket
  exemption.
- CLI edge cases: a nonexistent file path, and non-UTF-8-encoded file
  content — both must exit non-zero with an actionable message, not an
  unguarded traceback.

**`claude/.claude/skills/handoff/SKILL.md`**
- Add a `<!-- HOOK_TEST_FIXTURE: artifact-preamble -->` anchor comment
  immediately above the existing preamble fenced block (no prose change, no
  duplication — the block already exists verbatim).
- Replace the current §5 body with:

  > Run `<config-dir>/scripts/marker.sh status` and paste its output verbatim
  > — it reports every completion marker (code-review, skill-review,
  > plan-review, ready-for-review) for this repo and every active-bypass
  > marker (plan-review, ready-for-review, respond-pr, memory-skill) for
  > this session, each labeled live, historical, or absent, and flags a live
  > code-review or skill-review marker whose covered state has uncommitted
  > changes overlapping it.
  >
  > A live marker whose reconciliation flag fired means finished work is one
  > incidental edit away from a full re-review on resume; commit it
  > *before* writing this file. When the work is not commit-ready, say so
  > here and name in §3 the review skill the resuming session must re-run
  > first.

- Replace the "Pre-write checklist" section's body with:

  > Run `<config-dir>/scripts/check-handoff.py <path>` against the draft file.
  > It fails on: preamble mismatch, a missing/empty §1–§7 section,
  > placeholder text ("TBD", "TODO", "fill in later"), an unresolved
  > `<config-dir>`/`<slug>` token in §7, or §7 naming the wrong file. It
  > warns (non-failing) on: a §3 step matching a §3.5 anchor shape, and a
  > §2/§3/§6 section carrying no confidence tag. Fix every failure before
  > writing; treat each warning as a prompt to re-check that step's
  > bucketing or tagging, not as evidence it's already wrong.
  >
  > The script cannot check these — verify them yourself before writing:
  > - §2 Status is consistent with §3 Next concrete step and §6 Open questions
  > - You are not claiming "done" for any step whose verification is still pending
  > - §2.5 is populated; if any prerequisite phases are incomplete or
  >   unverified, they are listed there, not silently omitted
  > - If the handoff reason is context-limit, §2.5 names what was
  >   mid-flight at the time of the handoff
  > - §2.6 is populated — a faithful task-list serialization with per-item
  >   ordinal, status, and blocking edges, or "None." — and carries the
  >   resume directive
  > - §5's script output shows no unresolved reconciliation flag; where one
  >   fired, §3 names the review skill the resuming session must re-run to
  >   commit the covered work first
  > - If this session pushed commits to a branch with an open PR and
  >   `/ready-for-review` did not run this session, run the `pr-description`
  >   skill before writing this file
  > - Every load-bearing claim in §2/§3/§6 carries a confidence tag — the
  >   script only checks that a section isn't entirely untagged, not that
  >   each individual claim is
  > - A §3 step the script did *not* warn on can still belong in §3.5 — it
  >   only pattern-matches the named anchor shapes, not the underlying
  >   principle (mutates shared state irreversibly, or has externally-visible
  >   side effects outside this repo). A cited justification ("per repo
  >   convention", "per memory") never downgrades a step's irreversibility on
  >   its own; a step claiming a convention must name the file that states it
  > - Draft verification used Bash (`cat`/`grep`/`sed -n`/`wc -l`), not
  >   `Read` — a `Read` of the handoff path consumes the file out from
  >   under any remaining `Edit` calls

  This removes the five checklist bullets the script now enforces
  mechanically (preamble, section-population, placeholder scan, §7 token
  resolution, §7 filename match) plus the two markers-related bullets that
  §5's rewrite already made moot (the globs-not-hardcoded-list bullet and the
  every-marker-labelled bullet), and keeps the ten bullets that need the
  model's own judgment: the nine whose wording didn't depend on the removed
  manual-enumeration step, plus this new one restoring the old categorization
  bullet's "or the underlying principle" fallback and its anti-rationalization
  clause — the script's anchor-shape scan is necessarily narrower than the
  full categorization rule, and dropping that residual instruction would have
  been a silent regression on a safety-relevant check, not a compression.

**`docs/scripts.md`**
- Fix the `marker.sh` line's stale "12 valid invocation shapes" to the
  correct count (16, after this change) — incidental fix in a file already
  touched for this exact fact (Axis 1 bucket 2; noted in the PR description).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` (full suite) from the worktree,
  per this repo's three-levels-deep venv path.
- `../../../.venv/bin/ruff check claude/.claude/`.
- Manually run `~/.claude/scripts/marker.sh status` against this repo's own
  current state (a mix of live and absent markers) and confirm the report
  matches what `git status` / the marker directories on disk actually show.
- Manually run `check-handoff.py` against an existing real handoff file
  under `<config-dir>/handoffs/` (read-only — the script never writes) to
  confirm no false hard-failures on a known-good file, and against a
  deliberately broken copy to confirm each hard check fires.
- `/skill-review` on the `handoff/SKILL.md` diff (hook-enforced) and
  `/code-review` overall, which will dispatch `/review-permissions` for the
  `settings.json` change automatically.

## Out of scope

- The pr-description-skill-run condition (§5 checklist item: "run
  `pr-description` if this session pushed with an open PR and
  `/ready-for-review` didn't run") is left as a manual judgment call — it
  needs "did this session push," which isn't reliably derivable from disk
  state alone, and adding `gh api` calls to a linter meant to be cheap and
  offline-friendly is a bigger step than this plan's scope.
- No change to `session-marker-dashboard.sh` — its lighter, session-scoped,
  mtime-based report stays as the passive `SessionStart` surface it already
  is; `marker.sh status` is a separate, on-demand, hash-accurate surface for
  the handoff skill specifically.
