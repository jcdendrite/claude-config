# Let plan authoring write plans in chunks without weakening the plan-review gate

## Context

Let a plan file under `.claude/plans/` be authored across multiple
`Write`/`Edit` calls — including across a lost connection that forces a
brand-new Claude Code process/session to resume the draft — without ever
letting an unreviewed plan reach `ExitPlanMode` or get presented, and
without routing the write through a surface `require-plan-review.sh`
doesn't cover.

`require-plan-review.sh` computes one content-addressed hash over every
active (uncommitted-or-modified) file under `.claude/plans/` and allows a
`Write`/`Edit`/`MultiEdit`/`ExitPlanMode` call only when a `/plan-review`
completion marker's stored hash matches that combined hash exactly. The
very first `Write` to a brand-new plan path is allowed (nothing was active
before it), but that write immediately arms the gate: every subsequent
`Write`/`Edit` to any plan file in the repo is blocked until a
`/plan-review` marker covers the new exact hash. `/plan-it`'s own
documented flow requires a second `Write`/`Edit` to the same file (Step 1
scaffolds placeholder sections; Step 5 replaces them with `plan-architect`'s
returned design via `Edit`), so the gate blocks the skill's own
documented second step today. This is more than a workflow wrinkle: the
session that surfaced it lost its connection mid-draft, and the only way
to resume authoring an unfinished plan is another `Write`/`Edit` to that
same file — which the gate correctly blocks, since nothing has reviewed
that content, but which also has no legitimate "still drafting, not ready
for review" path back in. The fix must add that path without changing
what the gate guarantees once a plan is presented or finalized: `/plan-review` must still certify the exact
content an engineer or `ExitPlanMode` will see.

The engineer confirmed the mechanism must survive a full process restart
— a new session_id and PID resuming the same partially-drafted file — not
just a same-process reconnect. That rules out reusing the existing
PID-liveness active-bypass marker shape verbatim (`/plan-review`'s
`.plan-review-active.d`, `memory-skill`'s `.memory-skill-active.d`, both
checked via `_lib_active_bypass_marker_live`'s `kill -0` on a stored PID):
a resumed process has a new PID, the old marker's stored PID is dead, and
`_lib_active_bypass_marker_live` auto-evicts it, re-arming the gate at the
exact moment drafting needs to resume.

## Approach

Give `require-plan-review.sh` a write exemption for the repo's own `.claude/plans/*.md` and `.claude/plans/*.txt` files — the exact file set the gate already hashes — so authoring a plan is never blocked by the gate that exists to force that plan's review, while every other in-repo `Write`/`Edit`/`MultiEdit` and every `ExitPlanMode` stays gated on a completion marker matching the plan set's current hash. The exemption is a path-shape test with no session, PID, sentinel, or marker behind it, so a brand-new process resuming a half-written plan is indistinguishable from the one that started it.

This is not a new idea in this repo — `docs/design-decisions.md` §30 already names the missing exemption by name while explaining why `plan-architect` holds no `Write`: the hook "has no write exemption for `.claude/plans/` the way it does for `agent-reviews/` — an agent holding `Write` that authored the plan directly would trip the gate on its own first write, then be denied every subsequent edit to the section it was still drafting." That paragraph describes the defect this plan closes, from the same failure shape, written before the incident that motivated the plan.

**Assumption ledger**

- **Root problem:** `require-plan-review.sh` gates `Write`/`Edit`/`MultiEdit` on a completion marker matching the combined hash of every active file under `.claude/plans/`, with no exemption for a write whose own target is one of those files. The first write to a new plan path is allowed only because nothing was active before it; that write arms the gate against every subsequent edit to the plan being drafted. Authoring a plan is therefore capped at one tool call, which makes `/plan-it`'s own Step 1 → Step 5 flow impossible and leaves a partially-drafted plan unrecoverable after a lost connection — the only way forward is another write to the file, and the only way to clear the gate is a review of content the author knows is unfinished.

- **Givens:**
  - Chat-text presentation of a plan is not a tool call, so no `PreToolUse` hook can gate it; `ExitPlanMode` is the only presentation surface this hook can see. *Reason:* platform boundary — Claude Code's hook surface, which this repo cannot extend.
  - `ExitPlanMode`'s payload carries `tool_input.planFilePath` and no `tool_input.file_path`. *Reason:* harness-defined tool schema; the hook reads it, it does not shape it.
  - Whether a session runs `/plan-review` before presenting a plan conversationally rests on `CLAUDE.md`'s Plan Review rule and `/plan-it` Step 6, not on this hook. *Reason:* runtime instruction-following, not an artifact an edit in this repo can change.
  - `_lib_realpath_m`'s normalization semantics — including the symlink caveat already documented at `require-plan-review.sh:268` and the fact that a relative `file_path` resolves against the hook process's own cwd rather than the payload's `cwd` — are fixed here. *Reason:* it is the shared primitive both existing carve-outs in this same block already call; changing it re-scopes every gate hook that uses it, which is a decision outside this plan.

- **Mechanisms:**
  - **M1 — `_lib_is_repo_plan_file REPO_ROOT ABS_PATH` in `_lib.sh`, placed immediately after `_lib_active_plan_hash`** — `anchors: root`. A pure `case`-based path test with no fork and no git call, because it runs on every gated tool call; its contract comment binds its suffix set to `plan_pathspecs` (`_lib.sh:402`) so the exemption and the hash describe one file set rather than two that drift.
  - **M2 — a third condition in `require-plan-review.sh`'s target-scope block: exempt a target satisfying M1** — `anchors: root`. This is the whole fix; three lighter or narrower primitives were checked against the engineer-verified restart requirement (row 1) and set aside:
    1. *Reuse the existing active-bypass marker shape* (`.plan-review-active.d`, written by `marker.sh activate`, checked by `_lib_active_bypass_marker_live`) under a new `plan-it` key — fails outright: `_lib_active_bypass_marker_live` (`_lib.sh:770-803`) allows only when `kill -0` on the stored PID succeeds and `rm`s the marker otherwise, so a resumed process's new PID finds nothing and the gate re-arms at the exact moment drafting needs to resume.
    2. *An in-file draft sentinel the hook recognizes and excludes from the hash* — survives restart, but is strictly heavier than M2 for the same result: it makes review state part of the plan file's committed-or-not content, requires the hash function to parse file bodies rather than list them, needs `ExitPlanMode` to grow a separate unconditional deny for sentinel-carrying files (otherwise excluding the file empties `CURRENT_HASH` and `require-plan-review.sh:162-164` *allows* presentation), and creates a chicken-and-egg on recovery — adding the sentinel to an already-armed plan file is itself a blocked `Edit`.
    3. *A draft filename convention (`<slug>.draft.md`) excluded from the pathspecs* — also survives restart and also needs the same `ExitPlanMode` deny as (2), but additionally collides with `branch-management`'s "plan path is `.claude/plans/<topic-slug>.md`" rule and with the harness-chosen plan-mode path, which cannot be renamed.
    4. *A new non-PID marker type* (`marker.sh` subcommand + `enforce-marker-script-shape.sh` whitelist entry + cleanup story) — the heaviest candidate and the one with the worst failure mode: dropping PID-liveness is precisely what removes the only signal that would ever evict it, so an abandoned draft marker wedges the gate open for that repo with nothing to reclaim it.
  - **M3 — move the target-scope block (`require-plan-review.sh:256-275`) above the hash computation at line 134, keeping its internal order** — `anchors: row15`. The block decides purely on path shape and needs no hash; leaving it below means a plan-file write pays `_lib_active_plan_hash`'s git forks, the tier-1 marker grep, and tier-2's `git worktree list` plus a `sha256sum` per worktree before being allowed — and the hook's own comment (lines 225-230) identifies drafting as exactly the window where tier 1 always misses. This fix makes drafting the common path, so it converts a deny-path cost into a per-edit cost. **This mechanism is separable:** dropping it leaves M1/M2 fully correct at a higher per-edit cost, and nothing else in the design depends on it.
  - **M4 — one sentence added to the `Write`/`Edit` deny message (`require-plan-review.sh:284`) and to the header comment (lines 3-5, 13-39)** — `anchors: root`. After M2 the deny is only ever seen for a non-plan target; without saying so, an agent reading it concludes it cannot fix the plan either, which is the reasoning failure that produced the incident.
  - **M5 — a relational drift test in `test_marker_lib.py` asserting `_lib_is_repo_plan_file` returns true for exactly those paths whose removal changes `_lib_active_plan_hash`'s output** — `anchors: row5`, `anchors: row7`. M1's correctness is an agreement property between two independently-written matchers, and an agreement property that is only asserted in a comment is one edit from being false.
  - **M6 — correct the four prose sites that assert the pre-change behavior** — `anchors: row3`. `docs/design-decisions.md:378`, `docs/hooks.md:96`, `README.md:158`, `README.md:129`.

- **Assumption rows:**
  1. The mechanism must survive a full process restart — a new `session_id` and PID resuming the same partially-drafted file, not just a same-process reconnect — `[engineer-verified]`. The chosen design holds no per-session, per-process, or per-file state whatsoever, so it satisfies this by construction rather than by a liveness check.
  2. No existing test asserts that a `Write`/`Edit` targeting an in-repo `.claude/plans/*.md` denies; every deny-side case targets `src/foo.py` or `src/main.py`, and every plan-path case targets an out-of-repo path (`~/.claude/plans/session-plan.md`, `/tmp/foo.py`) — `[verified: grep + read of claude/.claude/hooks/tests/test_require_plan_review.py this session, including lines 62-149, 380-437, 660-743, 861-919, 962-1019]`. M2 therefore adds behavior without contradicting an existing assertion.
  3. `docs/design-decisions.md` §30 states the hook "has no write exemption for `.claude/plans/` the way it does for `agent-reviews/`" and describes this exact failure — `[verified: read of docs/design-decisions.md:378 this session]`. That clause becomes false on merge; it describes current behavior rather than recording an event, so under this repo's preserved-record decision test it is editable, but the *decision* it justifies (`plan-architect` holds no `Write`) stands on its own grounds and must not be disturbed.
  4. `ExitPlanMode` carries no `tool_input.file_path`, so `TARGET_PATH` is always empty for it, the whole target-scope block is skipped, and no carve-out placed inside it can ever release plan presentation — `[verified: require-plan-review.sh:259-261 comment plus test_no_file_path_exitplanmode_denies_without_crash, test_require_plan_review.py:1142-1152, which asserts the payload has no file_path key]`.
  5. `_lib_active_plan_hash` hashes exactly depth-1 `*.md` and `*.txt` under `.claude/plans/`, via `:(glob)` pathspecs that confine `*` to one path segment — `[verified: _lib.sh:392-402]`.
  6. `Bash` is never gated by this hook, so every existing escape hatch (`rm`, `git commit`, `mv` the plan out) stays available and `docs/hooks.md`'s recovery recipes stay valid — `[verified: require-plan-review.sh:73-76 tool-name filter, plus test_bash_tool_allows_always at test_require_plan_review.py:184-193]`.
  7. Bash `case` pattern `*` matches `/`, unlike git's `:(glob)`, so M1 needs an explicit depth-1 check on top of its suffix match or it would exempt `.claude/plans/sub/x.md` — a path `_lib_active_plan_hash` does not hash — `[unverified]`. Not run this session; M5's nested-path case is what settles it, and it is written to fail loudly rather than silently if the assumption is wrong.
  8. `/plan-review`'s own writes during a review land in `<config-dir>/.plan-review-active.d/` (out of repo, already exempt), in `agent-reviews/` (already exempt at `require-plan-review.sh:269-271`), and in the plan file itself (exempt after M2) — `[unverified]` as an exhaustive enumeration; established by grep of `claude/.claude/skills/plan-review/SKILL.md` this session, not a full read. The `.plan-review-active.d` bypass is therefore retained unchanged: it can only ever allow, so keeping a possibly-redundant bypass is safe, while removing it on an unverified enumeration is not.
  9. `select-tests.py` maps any `claude/.claude/hooks/**` change to `claude/.claude/hooks/tests`, and a `*.sh` change there additionally to `claude/.claude/scripts/tests` and the transcript-analysis glob; a `docs/**` or `README.md` change matches no rule and falls open to the full suite — `[verified: read of claude/.claude/scripts/select-tests.py:154-227 and 236-265 this session]`.
  10. The latency class times a non-plan target (`src/foo.py`), so neither M2 nor M3 short-circuits the path it measures and both scaling assertions keep testing what they claim — `[verified: _time_hook at test_require_plan_review.py:1482-1493]`.
  11. `timing`-marked tests are collected by default (`addopts` sets only `-n auto --strict-markers`), and the marker's own registration note asks for `-m timing -n0` to avoid parallel-load flakiness — `[verified: pyproject.toml:24-27]`.
  12. No `SKILL.md` change is required: `/plan-it` Step 1 and Step 5 already describe the two-write flow this fix unblocks, and `plan-review/SKILL.md`'s claims (line 5's "gates Write/Edit/MultiEdit/ExitPlanMode", line 15's "so this skill's own Write/Edit operations are not blocked") stay true for non-plan targets — `[verified: read of claude/.claude/skills/plan-it/SKILL.md in full and grep of claude/.claude/skills/plan-review/SKILL.md this session]`. Consequence worth stating: `require-skill-review.sh`'s hook-enforced commit gate never arms for this change, and no eval fixture run is owed.
  13. The harness plan-mode file is authored outside the repo under the convention this suite pins (`~/.claude/plans/<slug>.md`) and is already exempt via the out-of-repo branch — `[verified: test_require_plan_review.py:684-704, including the comment stating plan-mode authors there "without needing a bypass marker"]`. If a harness ever names an in-repo `.claude/plans/*.md` path instead, M2 covers that case too rather than conflicting with it.
  14. Moving the target-scope block above the hash (M3) flips exactly one untested path: an **out-of-repo** write while an in-repo active plan is unhashable currently denies (`require-plan-review.sh:143-160` fires first) and would then allow — `[verified: read of require-plan-review.sh:143-160 against 256-275 this session]`. This is a narrowing of over-reach consistent with the block's own stated intent ("the gate guards this repo's code, not all files on disk"), and `ExitPlanMode` is unaffected because it never reaches the block. It gets its own pinning test either way, so the behavior is asserted rather than incidental.
  15. Tier 2 (`git worktree list` plus one `sha256sum` per worktree) is paid on every edit for the whole window between authoring a plan and its first clean review — the hook says so itself — and this fix makes that window the normal drafting state rather than an exceptional one — `[verified: require-plan-review.sh:225-230 and the same claim restated in test_worktree_count_does_not_drive_tier_two_cost's docstring, test_require_plan_review.py:1527-1530]`. This cost reduction is not free on every path: moving the target-scope block earlier (M3) also means the two `_lib_realpath_m` calls it already made (each a capped `realpath`/`grealpath` fork) now run *before* the tier-1/tier-2 marker check for every ordinary, non-plan `Write`/`Edit`/`MultiEdit` too — a call that allows via a tier-1 marker match today never reaches those calls at all, since tier 1 exits first. The added cost is 1-2 lightweight forks ahead of the git/sha256sum work that already dominates that path, so the net direction still favors the change, but M3's tradeoff is two-sided, not purely a win — `[verified: staff-platform-engineer operational-footprint review, this session, walking the post-M3 line order against the current tier-1-exits-first order]`.

**Approach-level alternatives set aside** (mechanism-level ones are inline at M2):

- **Changing `_lib_active_plan_hash` to per-file hashes so one draft stops invalidating another plan's marker.** This would additionally fix the residual sharp edge in Out of scope below, but it changes the meaning of every stored completion marker and requires a coordinated write-side change in `marker.sh` plus a migration story for existing markers — a much larger blast radius than the authoring problem needs, and not required by the motivating incident.
- **Weakening the gate to arm only on `ExitPlanMode`.** Would fix authoring by deleting the property the gate exists for: preventing implementation work from proceeding under an unreviewed plan.
- **Adding a `PostToolUse` or `Stop` hook that nudges when a plan file is edited without a following review.** A nudge is not a gate; it neither restores the blocked write nor preserves any guarantee, so it is a second layer rather than a fix — the compounding-defensive-layers tell.

**Dispatch split:** one `code-writer` dispatch, not several. The hook, the library predicate, both test files, and the four prose sites are a single non-partitionable set — M5's drift test is written against M1's contract, and M4's message text is asserted by the new hook tests — so splitting would force the same shared background into every prompt.

## Critical files

- **`claude/.claude/hooks/_lib.sh`** — add `_lib_is_repo_plan_file REPO_ROOT ABS_PATH` immediately after `_lib_active_plan_hash` (which ends at line 453), before the `git`-invocation-detection helper that begins at line 455.

  Contract: both arguments are already `_lib_realpath_m`-normalized by the caller (same precondition the `agent-reviews/` check at `require-plan-review.sh:269` relies on); returns 0 iff `ABS_PATH` is a direct child of `REPO_ROOT/.claude/plans` whose name ends in `.md` or `.txt`. Two properties are load-bearing and belong in the comment, one sentence each: the suffix set must stay identical to `plan_pathspecs` at line 402, and the depth-1 check is required separately because a bash `case` glob matches `/` where git's `:(glob)` does not — verified this session (`case ".claude/plans/sub/x.md" in .claude/plans/*.md) ...` matches in bash), so implement the depth-1 check as an explicit `[ "$(dirname -- "$ABS_PATH")" = "$REPO_ROOT/.claude/plans" ]` comparison ahead of the suffix `case`, not as a single combined glob pattern.

  **Reuse:** none — deliberately fork-free and git-free, unlike `_lib_active_plan_hash`, because it runs on every gated tool call. Match the arity guard shape of `_lib_active_bypass_marker_live` (`_lib.sh:778`) — `[ "$#" -eq 2 ] || return 1`, with `[ ]` rather than `(( ))` for the reason documented at lines 775-777. Bash 3.2 only (`test_no_bash4_constructs.py` scans `claude/.claude/**/*.sh`): no associative arrays, no `${var,,}`.

- **`claude/.claude/hooks/require-plan-review.sh`** — four edits.

  1. **Move lines 256-275** (the `if [ -n "$TARGET_PATH" ]` target-scope block, from its `# Scope the deny to writes inside this repo.` comment through its closing `fi`) to sit immediately after line 132 — after the `ExitPlanMode` plan-mode branch closes, before the `_lib_active_plan_hash` call at line 134. Keep its internal order and its existing comments, including the `agent-reviews/` rationale at 265-268 and the symlink caveat at 268. *(M3 — droppable without touching anything else.)*
  2. **Inside that block**, after the `agent-reviews/` exemption (269-271) and before the repo-boundary check (272-274), add the plans exemption calling `_lib_is_repo_plan_file "$REAL_REPO" "$REAL_TARGET"`. One-sentence comment stating the fact, not the history: a write whose target is a plan file this gate hashes is authoring the plan the gate demands a review of, and `ExitPlanMode` never reaches this block because it carries no `file_path`.
  3. **Header comment**, lines 3-5 and the two-marker block at 13-39: one sentence naming the exemption alongside the existing `agent-reviews/` mention, so the file's own summary stays true.
  4. **Line 284's deny message**: one sentence stating that editing the plan file itself is exempt, so the reader is not left concluding the plan is unfixable. Keep the existing `/plan-review` and `/plan-it` routing sentences intact — `test_deny_reason_mentions_plan_review` (line 380) asserts both `/plan-review` and `plan-review-markers` appear.

  **Reuse:** `_lib_realpath_m` and the `REAL_REPO`/`REAL_TARGET` locals already computed at 262-263 — the new check must not re-resolve them; `_lib_config_dir`, `_marker_lib_repo_hash`, `_lib_marker_value_present`, `_lib_active_plan_hash`, `_lib_active_bypass_marker_live` are all untouched.

- **`claude/.claude/hooks/tests/test_require_plan_review.py`** — new test class, plus one addition to the scope-filter group at lines 684-743.

  - `Write`, `Edit`, and `MultiEdit` to `<repo>/.claude/plans/impl-plan.md` all allow with the gate armed and no marker present (the Step 1 → Step 5 case). Use `write_input` / `edit_input` / `multiedit_input`, all already imported at lines 13-30.
  - The same `Edit` allows under a **different** `session_id` from the one that created the file, with no active-bypass marker anywhere — the restart case, and the assertion that directly encodes ledger row 1.
  - `Write` to a second, not-yet-existing `<repo>/.claude/plans/new-plan.md` allows while `impl-plan.md` is active and unreviewed.
  - Negative controls: `<repo>/.claude/plans/notes.rst` denies, `<repo>/.claude/plans/sub/nested.md` denies (row 7), `<repo>/src/foo.py` still denies, and `ExitPlanMode` with `plan_file_path=""` still denies while an unreviewed plan is active — the carve-out must not have leaked into the presentation path.
  - The deny message for a non-plan target names the plan-file exemption (M4).
  - Scope-filter group addition: an out-of-repo `Write` allows while an in-repo plan is unhashable (`chmod 0o000`), pinning ledger row 14; the in-repo `src/foo.py` case at line 861 must keep denying under the same condition.

  **Reuse:** the `plan_review_repo` / `plan_review_home` fixtures (lines 37-58), `run_hook`, `run_hook_reason`, `write_plan_review_marker`, and the `@pytest.mark.skipif(os.geteuid() == 0, ...)` guard already used at 860/876/894 for any permission-bit case.

- **`claude/.claude/hooks/tests/test_marker_lib.py`** — the M5 drift test, which belongs here rather than in the hook test file because this is where `_lib_active_plan_hash`'s relational unit tests already live (`helpers.py:703-705` names it as the site for independent correctness checks that do not route through `write_plan_review_marker`).

  Seed a repo with `.claude/plans/a.md`, `.claude/plans/b.txt`, `.claude/plans/c.rst`, and `.claude/plans/sub/d.md`, all untracked. For each, assert `_lib_is_repo_plan_file` returns 0 exactly when removing that file changes `_lib_active_plan_hash`'s output. That makes the "same file set" invariant a real parse rather than a comment, and it fails on a future pathspec change that touches only one side.

  **Reuse:** the existing `bash -c '. "$lib_sh"; <fn> "$1"'` shell-out shape used by `write_plan_review_marker` (`helpers.py:708-713`) for calling a `_lib.sh` function directly from Python.

- **`docs/design-decisions.md`** — §30, line 378. Minimal correction only: the clause asserting the hook has no `.claude/plans/` write exemption becomes false. Reword that clause to state the exemption exists and that `plan-architect` still holds no `Write` on its own grounds (a read-only design agent returning prose the dispatching session inserts). Do not rewrite the surrounding decision record; the entry is dated and the decision itself is unchanged.

- **`docs/hooks.md`** — line 96. Qualify "blocks Write/Edit when an uncommitted or modified plan file exists" with the plan-file exemption. The two Bash recovery recipes at 98-107 stay valid and unchanged (ledger row 6).

- **`README.md`** — line 158's gate-table condition cell and line 129's mermaid edge label, both of which currently read as gating all `Write`/`Edit`. Same one-clause qualification, no restructuring.

## Verification

Run from the worktree root (`.claude/worktrees/plan-review-chunked-drafting`); `.venv` lives at the repo root, three levels up.

1. **Domain-scoped suite** — `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`. With `docs/**` and `README.md` in the diff this falls open to the full suite (`unmatched-path`), which is the correct and expected outcome here, not a misconfiguration — the hook-only subset is selected by `claude/.claude/hooks/tests`, `claude/.claude/scripts/tests`, and the transcript-analysis glob.
2. **Targeted, while iterating** — `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_plan_review.py claude/.claude/hooks/tests/test_marker_lib.py claude/.claude/hooks/tests/test_lib.py claude/.claude/hooks/tests/test_hook_alignment.py claude/.claude/hooks/tests/test_marker_script.py`. `test_hook_alignment.py` and `test_marker_script.py` are in the list because both read `_lib_active_plan_hash` and the `HOOK_TEST_FIXTURE` blocks in `plan-review/SKILL.md`; neither should change, and a failure there means the edit reached the active-marker layout, which it must not.
3. **Latency class serially** — `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_require_plan_review.py -m timing -n0`, per the marker's own registration note in `pyproject.toml`. Both scaling assertions must still pass and must still be *reached*: confirm `_time_hook`'s `src/foo.py` target is unchanged, or the tests silently stop measuring the tier-1/tier-2 path.
4. **Lint** — `../../../.venv/bin/ruff check claude/.claude/` and `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` for the two shell edits.
5. **End-to-end, in a scratch repo, exercising the actual incident** (the tests cover the hook in isolation; this covers the sequence):
   1. `git init` a scratch repo with one commit, no `.claude/plans/`.
   2. Feed the hook a `Write` to `.claude/plans/draft.md` → allow (unchanged from today).
   3. Create the file on disk. Feed an `Edit` to the same path with a **different** `session_id` and no marker of any kind → **allow**. This is the Step 5 case and the restart case in one call; it denies before the change.
   4. Feed a `Write` to `src/foo.py` → still deny, with a message naming both `/plan-review` and the plan-file exemption.
   5. Feed `ExitPlanMode` with `planFilePath` empty → still deny.
   6. Write a completion marker via `~/.claude/scripts/marker.sh write plan-review`, then re-feed steps 4 and 5 → both allow.
   7. Edit the plan file again, then re-feed steps 4 and 5 → both deny again. This is the property the whole change must not break: review still certifies exact content, and the gate re-arms the instant that content moves.
6. **Multi-plan interference check** — with a reviewed plan A active and marker-covered, scaffold an unreviewed plan B: confirm `Edit` to *both* A and B allows, and that a `Write` to `src/foo.py` denies. The deny is the documented residual (Out of scope below), and confirming it here keeps it a known state rather than a surprise.
7. **Prose truth pass** — `git grep -n "agent-reviews" -- claude/.claude/hooks/require-plan-review.sh docs/ README.md` and re-read each hit plus `docs/design-decisions.md:378`. Every site that describes the hook's exemption set must now name both exemptions or neither; a site naming only `agent-reviews/` is the drift this step exists to catch.
8. **Review pipeline** — `/code-review` dispatches per file type automatically; this change touches `claude/.claude/hooks/**` and no `SKILL.md`, so `claude-hook-review` applies and `require-skill-review.sh`'s commit gate does not arm (ledger row 12). Confirm that holds rather than assuming it — if any `SKILL.md` ends up edited, `/skill-review` becomes mandatory before commit.

## Out of scope

The plan's Context section already excludes the worktree-lock reacquire task, the worktree-isolation Bash-guard heuristic, and generalizing to `require-code-review.sh` / `require-ready-for-review.sh`. Four further scope-creep risks surfaced while designing:

- **Changing `_lib_active_plan_hash` from one combined digest to per-file hashes.** In reach, and it would dissolve the residual sharp edge that scaffolding an unreviewed plan B invalidates a reviewed plan A's marker for implementation writes. Deliberately not done: it redefines what every already-written completion marker means, requires a coordinated `marker.sh` write-side change plus a migration story, and the motivating incident is an authoring failure, not a two-plan failure. The residual behavior is unchanged by this plan and verification step 6 pins it as known.
- **Removing the `.plan-review-active.d` bypass as newly redundant.** Its remaining scope narrows once plan-file writes are exempt — but ledger row 8's enumeration of what `/plan-review` writes is `[unverified]`, and removal would touch `marker.sh`'s subcommand whitelist, `enforce-marker-script-shape.sh`'s `MARKER_SHAPE` regex, `plan-review/SKILL.md`'s two `HOOK_TEST_FIXTURE` blocks, `test_hook_alignment.py`, and `session-marker-dashboard.sh`. Retaining a possibly-redundant bypass can only allow; removing one on an unverified enumeration is not.
- **Editing `/plan-it` or `/plan-review` SKILL.md.** Both stay accurate after this change (ledger row 12), and touching either arms `require-skill-review.sh`'s commit gate and pulls in `/skill-review` plus an eval-fixture obligation for no behavioral gain.
- **Broadening the exemption to all of `.claude/plans/`** rather than the depth-1 `*.md`/`*.txt` set. Simpler to write, but it exempts writes the gate never hashes — a file the gate can neither see nor re-arm on. Scoping the exemption to exactly the hashed set keeps "the exempt set is the hashed set" a statable invariant that M5 mechanically enforces; the cost is that a `.claude/plans/notes.rst` write is denied while the gate is armed, which is the same treatment any other unrecognized in-repo file gets.
