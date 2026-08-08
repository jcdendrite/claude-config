# Fix bare-`~/.claude` failures for non-personal Claude accounts

## Context

The user's machine runs a per-account Claude Code config-dir scheme
(`workstation-setup`'s `accounts.tsv` + `use_claude_account`): the `personal`
account stays at `~/.claude`, every other account resolves to
`~/.config/claude-accounts/<account>/` via `CLAUDE_CONFIG_DIR`. Sessions
anchored in a non-personal container reported three symptoms: handoffs/briefs
failing to write, plans failing, and `transcript-analysis` finding no
sessions for a container that clearly had them.

The initial hypothesis — that claude-config's hooks/scripts hardcode
`$HOME/.claude` instead of honoring `CLAUDE_CONFIG_DIR` — was wrong for the
reported symptoms. `CLAUDE_CONFIG_DIR` support already shipped in commit
`399ce6c`. Investigation (forensic transcript search, root-cause analysis,
and primary-source verification via `/verify-sources`) found four
independent, narrower causes instead:

1. **Handoff/brief/plan write failures** (the original complaint) trace to
   `~/.claude/plans`, `~/.claude/handoffs`, and `~/.claude/briefs` on *this
   machine* being live symlinks into this git checkout — along with 34 other
   untracked paths (`projects/`, `sessions/`, every `*-markers/` dir,
   `.claude.json`, `history.jsonl`, and more), all sharing one identical
   filesystem timestamp (confirmed via `ls -ld`). This is **not** a bug in
   `install.sh`'s current `stow --adopt` invocation that a fresh clone would
   reproduce — GNU Stow's own manual confirms `--adopt` only acts on a
   target when it's "encountered" while stow walks the *package source*
   tree, and none of these 37 paths exist in this repo's tracked content
   (`git ls-files -- 'claude/.claude/*'` returns only 9 top-level entries).
   A brand-new clone on a brand-new machine would never touch any of them.
   What actually happened: a one-time, undocumented event on this specific
   machine (most likely the user manually migrating a pre-existing personal
   `~/.claude` into this repo while bootstrapping it) physically populated
   all 37 paths inside `claude/.claude/` — after which every subsequent
   `install.sh` re-run has correctly kept maintaining those symlinks, since
   stow now finds real entries there. Of those 37, only 3 —
   `plans/`, `handoffs/`, `briefs/` — are ever targeted by the Write/Edit
   tools from an arbitrary skill invocation (`handoff/SKILL.md` and
   `brief/SKILL.md` instruct a Write; the plan-mode harness itself defaults
   new plan files to `~/.claude/plans/`, confirmed by this very session's
   own plan-mode instruction, and `plan-it`'s Step 1 already documents a
   fallback for exactly this failure). The other 34 — `.claude.json`,
   `history.jsonl`, `projects/`, `sessions/`, every `*-markers/` dir — are
   written exclusively by Claude Code's own harness or by this repo's
   Bash-only scripts (`marker.sh`, `_lib.sh`), never through the Read/Write/
   Edit tools, so they never hit the worktree-enforcement collision this
   bug produces. The fix is scoped to the 3 that actually exhibit it.
2. **`transcript-analysis` finding nothing** is real, but the documented
   workaround (`CLAUDE_CONFIG_DIR=~/.config/... python3 ...`, saved as a
   session memory) is itself broken: it puts a `~/.config` path in argv,
   which Claude Code's Bash permission classifier denies (confirmed: 9 such
   denials in the transcript store, identical mechanism). A `--config-dir`
   flag that resolves the path *inside* the script, rather than at the
   shell command line, keeps the denied path out of argv entirely.
3. **The `plan-review` routing gate** (`ROUTING.md`) has two distinct
   failure modes conflated in the original report: an *unsatisfiable* Read
   denial under non-personal accounts (root cause: `CLAUDE_CONFIG_DIR`
   relocates the skill's base directory into the `~/.config/**` prefix a
   separate, Kandji-managed `managed-settings.json` denies — confirmed via
   `claude-directory.md:1435` and the bundle's `dirname(skill.filePath)`
   base-dir logic; not fixable from this repo, see Out of scope), and an
   *avoidable* ordering race that trips for roughly a fifth of plan-review
   sessions regardless of account (`log-routing-read.sh` only credits a Read
   that happens *after* `marker.sh activate` — reproduced live during this
   plan's own `/plan-review` pass, on the personal account, when a
   deactivate-then-reactivate cycle left an active marker with no
   routing-read credit).
4. Ten hardcoded `~/.claude/skills/...` references across 8 skill/agent
   files could use the documented `${CLAUDE_SKILL_DIR}` substitution
   instead — but only 2 of the 10 are actually eligible (see item 3 below).

**Out of scope, named explicitly:** the `~/.config/**` managed deny (item
3's root cause) is not fixable from this repo. `permissions.deny` has no
allow-carve-out (`permissions.md:39`: "a deny rule can't carry allowlist
exceptions"); the one documented mechanism that *does* support a scoped
re-allow, `sandbox.filesystem.denyRead`/`allowRead`
(`sandboxing.md:180-181`), governs sandboxed Bash subprocesses only, not the
`Read` tool the gate actually needs. Decomposing the blanket rule requires
confirming who owns `managed-settings.json` (Kandji MDM vs. hand-authored —
unresolved as of this plan) and is tracked as a prerequisite for a future
session, not this one.

## Approach

**Root problem:** four independent, narrow defects — a legacy per-machine
stow-adoption artifact, a broken cross-account workaround, a marker-ordering
race, and an incomplete migration to a documented harness substitution — not
a systemic `CLAUDE_CONFIG_DIR` support gap.

**Givens** (conditions this plan treats as fixed):
- G1: `CLAUDE_CONFIG_DIR` is Claude Code's documented relocation mechanism
  for the entire `~/.claude` tree, including `skills/`.
  `[verified: claude-directory.md:1435 — "If you set CLAUDE_CONFIG_DIR,
  every ~/.claude path on this page lives under that directory instead"]`
- G2: the `personal` account must stay at bare `~/.claude` (the harness
  default with zero configuration).
  `[verified: env-vars/claude-directory docs + workstation-setup's own
  documented rationale]`
- G3: the managed `Read/Edit(~/.config/**)` deny is fixed for this plan.
  `[engineer-verified — user's explicit decision after /verify-sources
  confirmed no carve-out mechanism exists at that permission layer]`
- G4: cross-account transcript content must stay unreadable via the `Read`
  tool except through a deliberately sanctioned path.
  `[engineer-verified]`

**Per-mechanism justification:**

- **Un-adopt `plans/`, `handoffs/`, `briefs/` from stow, going forward** —
  `anchors: root`. Mechanism: a static `--ignore` list naming exactly these
  3 directories (verified as the complete set of stow-adopted paths that
  the Write/Edit tools target from an arbitrary skill invocation — see
  Context), migrating each one that is *currently a symlink resolving into
  this checkout* back to a real directory first. Order: migrate → `stow
  --ignore`. Two design iterations were tried and rejected during
  `/plan-review` before settling here: (a) a *dynamic*, git-ls-files-driven
  `--ignore` list covering all 37 currently-adopted paths, migrating only
  the 3 — rejected (`staff-platform-engineer`): permanently strands the
  other 34 once ignored without being migrated; (b) the same dynamic list
  *with* a full 37-path migration — rejected (`staff-platform-engineer`,
  `staff-sdet`): the file-vs-directory detection this needs is
  under-specified for the 8 file-type entries in that set (`.claude.json`,
  `history.jsonl`, `private-projects.md`, …), and `.claude.json` is a
  *live* file Claude Code itself rewrites via temp-file-plus-rename during
  a running session — migrating it mid-session risks clobbering fresh
  state with no quiesce precondition named. The static 3-name list
  sidesteps both problems: the other 34 don't exhibit the bug (per
  Context), so leaving them adopted is not a regression, and all 3 targets
  here are directories, so no file/directory branch is needed.
- **`transcript-analysis --config-dir`** — `anchors: root`. Mechanism: a
  flag that resolves the target `projects/` directory *inside the script*,
  reassigning the single module-level `PROJECTS_DIR` global immediately
  after argument parsing (before any subcommand or `--this-repo` logic
  runs), covering its 5 `PROJECTS_DIR` read sites in one place rather than
  editing each individually. `:3100`'s separate `config_dir()` call (the
  `.handoff-nudge.log` diagnostic) is deliberately left untouched by this
  reassignment: that log is scoped to the *running* account's own
  schema-drift history, not to session enumeration — the original reported
  symptom — so there is no reason for a cross-account `--config-dir` to
  redirect it. Reads at the OS level, so the managed
  `Read/Edit` deny never applies (it governs Claude's built-in file tools
  and Bash file-commands only). `[verified: permissions.md:272]`. Two
  lighter/narrower alternatives considered and rejected in favor of this
  minimal primitive: (a) keep the existing `CLAUDE_CONFIG_DIR=... python3
  ...` inline workaround — rejected, confirmed broken (9 matching Bash
  classifier denials in the transcript store); (b) a friendlier
  `--account NAME` flag backed by a new `account-config-dirs.json` schema —
  rejected on review (`/plan-review` Step 4): `CLAUDE_CONFIG_DIR` is
  already the generic primitive, this repo already has an established
  convention for machine-local lookup files
  (`private-projects.md`-style, read via `_lib_config_lines`), and
  account-name resolution is specific to one machine's scheme and belongs
  in `workstation-setup`, not the public repo.
- **Migrate to `${CLAUDE_SKILL_DIR}`, narrowly** — `anchors: root`.
  Mechanism: the documented harness substitution, which resolves to *the
  referencing skill's own directory*. Scope confirmed at the substitution's
  actual defining sentence, not the narrower usage hint beside it:
  `[verified: skills.md:333 — "Claude Code substitutes ${CLAUDE_SKILL_DIR}
  and ${CLAUDE_PROJECT_DIR} in two places: the skill's markdown content,
  and Bash rules in the allowed-tools frontmatter"]` — this expands
  anywhere in a `SKILL.md`'s body text, prose included, not only inside
  bash-injection command blocks (a re-review round initially misread the
  adjacent "use this in bash injection commands" *usage* guidance as a
  *scope* restriction; `:333` is the sentence that actually defines scope,
  and it says otherwise). Of the actual 10 hardcoded sites (corrected
  count, `/plan-review` `staff-sdet`), only 2 reference the *same* skill's
  own co-located file
  (`plan-review/SKILL.md:224` → its own `ROUTING.md`;
  `error-handling/SKILL.md:154` → its own `REFERENCES.md`) — those migrate.
  The other 8 are one skill/agent referencing a *different* skill's
  directory (e.g. `code-writer.md` → `test-conventions/SKILL.md`), where
  `${CLAUDE_SKILL_DIR}` would resolve to the wrong directory — those stay
  as literal paths, and this is a deliberate scope decision, not an
  oversight. Hook shell scripts (e.g. `require-routing-read.sh:68`'s deny
  message) cannot use the substitution at all — it's a harness-side
  skill-body expansion, not an environment variable available to hook
  subprocesses — so that hardcoded reference stays, with the resulting
  two-string coupling (the skill's own reference vs. the hook's deny
  message) explicitly accepted rather than covered by a drift test.
- **`log-routing-read.sh` ordering fix, redesigned** — `anchors: root`.
  Original mechanism (session-wide or unspecified-lookback credit) was
  found unimplementable as described (`/plan-review`, `staff-sdet`:
  `marker.sh activate` has no access to Read history at all) and, in its
  broad form, would widen the pre-existing abandoned-marker gap (rejected
  earlier in this plan's own reasoning). Redesigned mechanism: on every
  Read of `ROUTING.md` — regardless of whether the plan-review active
  marker exists yet — `log-routing-read.sh` touches a new, separate
  timestamped record, `$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID`
  (overwritten by each new Read, so it always reflects only the latest
  one). This record alone grants no gate credit and does not change
  `require-routing-read.sh` itself. It *does* deliberately widen the
  property `test_log_routing_read.py`'s existing invariant names ("an
  unrelated Read outside an active plan-review authorizes nothing") in one
  bounded way: a Read shortly *before* activation now counts, where it
  previously didn't — that's the fix. This is stated as an intentional,
  tested widening (see Critical files), not claimed as "unchanged" — the
  old test alone cannot observe the new behavior either way, so re-running
  it proves nothing about the change. `marker.sh activate`'s existing case
  additionally checks this record: if present and its mtime is within a
  short, named
  constant window (documented explicitly as a deliberate choice, not a
  magic number) of "now," it backfills the existing routing-read marker
  immediately — after which `require-routing-read.sh`'s current 60-minute
  freshness check applies exactly as it does today, unchanged.
  `marker.sh`'s `deactivate` case now clears three markers (active,
  routing-read, and the new pending-read record), not two. One alternative
  considered and rejected: fix via SKILL.md instruction ordering alone
  (documentation-only, no hook change) — rejected per this repo's own
  hook-not-prompt-discipline philosophy (CLAUDE.md's Safety section: hooks
  enforce mechanically; that's the reason this gate exists at all).

**Delete the stale memory.** `claude-config-dir-per-account.md` (this
project's auto-memory) documents the broken `CLAUDE_CONFIG_DIR=...`
workaround as the fix. Delete it once `--config-dir` ships.

## Critical files

- **`install.sh`** (`:26-30`) — replace `stow -v --adopt -t "$HOME" claude`
  with: (1) a literal three-name list, `plans`, `handoffs`, `briefs` — no
  `git ls-files` computation, no dynamic set; (2) for each name that is
  currently a symlink resolving inside `$REPO_DIR/claude/.claude/` (detect
  via `cd -P -- "$target" && pwd -P` compared against
  `$REPO_DIR/claude/.claude/$name` — portable, no `readlink -f` dependency,
  per `staff-platform-engineer`'s finding that `relocate-claude-config.sh`'s
  own `_readlink_f` helper fails closed on macOS < 12.3 rather than falling
  back; safe here since all 3 targets are directories, unlike the rejected
  37-path design), call the migration function from the new sourced lib
  (below) for that name; (3) run `stow --adopt --ignore='^plans$'
  --ignore='^handoffs$' --ignore='^briefs$' claude` — `--ignore` values are
  anchored Perl regexes, not literal strings; anchoring with `^…$` is
  required so a future name containing `.` or `-` can't silently
  over-match (per `staff-platform-engineer`'s finding).

  The migration function (in the new lib, invoked once per name) is a
  strict 3-step sequence with **each step individually checked, returning
  non-zero on its own failure**, not relying on `set -e` (which the
  surrounding `if`/`||` wrapper needed for continue-past-failure already
  disables for everything inside it): (a) **copy** (not move) real content
  into `$HOME/.claude-config-relocate-backup/<name>.<timestamp>/` —
  skipped if a resumable state is already detected (see below), so a
  second run's step (a) never overwrites a good backup with an empty
  source; retained, never consumed, so it stays intact as the resume
  source for step (c) regardless of how many times (c) is retried; (b)
  unlink the stow symlink — backup from (a) is already safe; (c) **copy**
  (not move) the backed-up content into a plain `$HOME/.claude/<name>` —
  on failure here, `$HOME/.claude/<name>` is missing or a dangling symlink
  with the backup dir present; this is a named **resumable** state, not a
  silent dead end: a re-run detects a missing/dangling target with a
  matching backup directory, selects the **newest non-empty** backup for
  that name if more than one exists (from prior failed runs), skips step
  (a), and resumes from step (c) rather than treating it as "nothing to
  migrate" (closing `staff-platform-engineer`'s dangling-symlink
  recovery-window finding). Both (a) and (c) copy rather than move
  specifically so a partial (c) failure can be retried against an intact
  backup rather than a half-emptied one.
  Idempotency for the ordinary case (no prior failure) holds by
  construction, as before: migration only ever acts on a current symlink,
  and a migrated entry is a plain real directory afterward, so there is no
  "both real" collision to define.

  `install.sh` sources the lib from its own known location,
  `"$REPO_DIR/claude/.claude/scripts/_stow_migration_lib.sh"` — not a
  `~/.claude/scripts/...` path, which doesn't exist yet at this point in a
  fresh install, since migration and `--ignore` run *before* `stow`
  executes (per `staff-platform-engineer`'s finding). Do **not** source
  `relocate-claude-config.sh` directly — it flips on `set -u`/`pipefail`
  for the rest of `install.sh` (concretely fatal on an empty array under
  `set -u` on macOS system bash 3.2). The new lib carries no top-level
  `set` statement and a `# shellcheck shell=bash` directive (it has no
  shebang), sourced by both `install.sh` and (optionally)
  `relocate-claude-config.sh`.
- **`.gitignore`** — **no change.** All existing entries for these paths
  (and the other 34 untouched-by-this-plan ones) stay permanently as
  defense-in-depth against the state ever reoccurring, at zero ongoing
  cost.
- **`claude/.claude/scripts/transcript-analysis.py`** — add
  `--config-dir PATH` near the existing `--projects GLOB` flag (`:3931`);
  reassign the module-level `PROJECTS_DIR` immediately after argument
  parsing, before any subcommand or `--this-repo` logic runs — covering its
  5 read sites (`:1498, :1500, :1537, :1604, :2437`). Make `--this-repo`
  under a set `--config-dir` error loudly on zero matching project slugs
  rather than silently returning empty — this directly closes the original
  reported symptom (declaring no sessions exist for a container that has
  them). `config_dir()` itself (not `PROJECTS_DIR`) is called at one other
  site, `:3100`'s `.handoff-nudge.log` diagnostic — reassigning
  `PROJECTS_DIR` does **not** cover it, and this is intentional, not a gap:
  that log tracks the *running* account's own schema-drift history, which
  `--config-dir` has no reason to redirect (a prior draft of this plan
  incorrectly claimed `:3100` was covered "for free" by the `PROJECTS_DIR`
  reassignment — corrected here per `/plan-review`, `staff-sdet`).
  Non-breaking for every current invoker
  with no code change needed on their side: `hooks/nudge-error-mode-
  analysis.sh`, `scripts/post-crash-sessions.py`, `scripts/token-
  analyzer.py`, `settings.json`, `agents/skill-fidelity-reviewer.md`, and
  the `error-mode-analysis`, `ready-for-review`, `transcript-analysis`,
  `transcript-narrative` skill bodies — none pass `--config-dir`, so all 9
  keep resolving via the untouched default path.
- **`${CLAUDE_SKILL_DIR}` migration** — exactly 2 files:
  `claude/.claude/skills/plan-review/SKILL.md:224`,
  `claude/.claude/skills/error-handling/SKILL.md:154`. No others.
- **`claude/.claude/hooks/log-routing-read.sh`** — write the pending-read
  record unconditionally on every `ROUTING.md` Read (move the existing
  active-marker check so it gates only the *existing* routing-read marker
  write, not the new pending-read record).
- **`claude/.claude/scripts/marker.sh`** — `activate` case (`:275-305`):
  add the bounded-window backfill read; `deactivate` case (`:311-312`):
  clear the third marker directory too.
- **Tests:**
  - `claude/.claude/hooks/tests/test_install_sh_continuity_hardening.py`
    (corrected path — not `claude/.claude/tests/...`) — a new
    `INSTALL_TEST_FIXTURE`-delimited marker pair around the migration block
    (matching this file's existing extraction convention, so a misplaced
    marker fails loudly rather than silently testing nothing), with named
    cases: a pre-existing symlink-into-checkout for each of the 3 names
    migrates to a real directory with byte-identical content; a second run
    is a no-op; a per-entry failure is caught, reported in the summary, and
    does not abort migration of the other two.
  - `claude/.claude/scripts/tests/test_transcript_analysis.py` — the
    positive case: `--config-dir` set to a fixture directory, a subcommand
    actually enumerates sessions from it (this is the reassignment's core
    invariant — the no-flag and error-path cases below can both pass while
    this one is broken); a test that runs the argument parser with no
    `--config-dir` and asserts `PROJECTS_DIR` is unchanged *after parsing*
    (not an import-time-only check — `test_projects_dir_honors_claude_config_dir` already covers
    import time; the new regression risk is in the post-parse reassignment
    itself); an explicit `--config-dir` + `--this-repo` test for the
    loud-error behavior.
  - For the `${CLAUDE_SKILL_DIR}` migration: verified by live invocation
    (see Verification below), not an automated test — the substitution is
    harness-internal behavior this repo's pytest suite has no seam to
    invoke directly, and a file-existence assertion computed by the test's
    own logic would pass whether or not the harness actually expands the
    placeholder (a prior review round's proposed "resolution-correctness
    test" was tautological for exactly this reason).
  - `claude/.claude/hooks/tests/test_log_routing_read.py` and
    `test_marker_script.py` — boundary pair (Read just inside vs. just
    outside the backfill window credits/doesn't); a stale pending-read
    record (older than the window) is not backfilled by a later, unrelated
    `activate`; a **new** test naming the widened boundary as intentional
    (an unrelated ROUTING.md Read within the window, followed by
    `activate`, now credits — pinned explicitly, not inferred from the old
    test staying green); `deactivate` clearing all three markers; the new
    pending-read marker directory gets the same
    `_lib_valid_session_id_component` traversal-safety guard and canary
    test as the existing markers.

## Verification

1. **Un-adopt:** on the user's own machine, apply the fix, re-run
   `install.sh`, confirm all 3 previously-symlinked paths are now real with
   prior content intact and a second run is a no-op. Spawn a session
   anchored in a different repo's worktree and confirm a handoff Write now
   succeeds on the first attempt.
2. **`--config-dir`:** run the new regression test (no flag → unchanged
   default), the positive-path test (flag set → sessions actually
   enumerated from the fixture dir), and the `--this-repo` loud-error test.
   Confirm no `~/.config` path appears in the subprocess's own argv when
   the flag is used.
3. **`${CLAUDE_SKILL_DIR}`:** invoke `plan-review` for real, **under the
   personal account specifically** — under any other account the Read
   would additionally cross the `~/.config/**` managed deny (this plan's
   own item-3 root cause, out of scope), which would fail for a reason
   indistinguishable from a broken substitution and make the check
   uninterpretable. Confirm the routing-read marker gets written (i.e., the
   Read against the expanded path actually succeeded) plus the
   routing-gate deny message still resolves correctly — this migration's
   only verification, per Critical files above.
4. **Ordering fix:** the boundary-pair, stale-record, and new
   widened-boundary tests pass; regression test confirms `deactivate`
   clears all three markers.
5. Run the full suite from a worktree: `../../../.venv/bin/pytest
   claude/.claude/` and `../../../.venv/bin/ruff check claude/.claude/`.

## Out of scope

- **Decomposing the managed `~/.config/**` deny.** Blocked on confirming
  Kandji ownership of `managed-settings.json`; no in-repo fix exists.
- **Relocating non-personal account dirs out of `~/.config`.** Considered
  and rejected early: would work around a misconfigured deny by moving a
  correctly-placed, XDG-conventional directory, and would forfeit the
  cross-account transcript isolation the user explicitly wants to keep
  (G4).
- **Account-name-to-config-dir resolution.** Belongs in `workstation-setup`
  (a thin wrapper can call `transcript-analysis.py --config-dir $(resolved
  path)`); rejected from this plan at `/plan-review` Step 4.
- **TTL on the abandoned-activation gap**
  (`.plan-review-active.d/$SESSION_ID` has no freshness check, so a
  crashed/abandoned plan-review's markers can silently authorize a later,
  unrelated one in the same live session). Real, needs a broader discussion
  of crashed/abandoned-marker handling across the repo. To be filed as a
  GitHub issue after this plan is approved.
