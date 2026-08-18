# Redaction hook follow-ups from PR #685

## Context

Close the two real, actionable follow-ups surfaced after PR #685 merged,
without touching the two the engineer already marked as pre-existing or
speculative. Follow-up 1 is a two-line doc correction: `skill-management`'s
`skill-review` SKILL.md says project-layer skills are "invoked by a parent
via Glob+Skill-tool," but all three actual parents (`code-review` Step 0.5,
`plan-review` Step 2.5, `plan-it` Step 2.5) load them via Glob + the **Read**
tool — confirmed by grep against all three. Follow-up 2 is that
`deny-private-project-refs.sh`'s tier-2 blocklist silently no-ops for any
contributor who never created `~/.claude/private-projects.md` — confirmed at
line 680, no `else`, no notice. The engineer redirected the fix for #2 away
from the hook itself: gate a contributor's dev-environment setup
(`install-dev.sh`) on that file's existence, and have `install.sh` ask
whether the user plans to contribute and point them at `install-dev.sh` when
they do. Follow-ups 3 (`gh issue create`/`gh issue comment` unscanned) and 4
(the whole control is detective, not preventive) stay named only — both are
already covered (3 in `docs/private-project-redaction.md` Known gaps; 4 is
speculative design work) and the engineer confirmed leaving them as-is.

## Approach

Two independent fixes land in one PR: a doc correction inside
`skill-management` (with its required plugin version bump), and a
setup-time gate that moves tier-2 opt-in enforcement from "silent at commit
time" to "explicit before a contributor's first commit," implemented
entirely in `install.sh`/`install-dev.sh` rather than in the hook.

**Alternatives considered for follow-up 2 and why they were set aside:** the
hook could instead fail closed at commit time (block every git/gh operation
until the file exists) or emit a non-blocking runtime warning — both were
raised as options, but the engineer explicitly chose the setup-time gate
instead: it confronts every contributor once, before their first commit,
without changing the hook's per-commit behavior for anyone (including
end-users who install the repo but never contribute).

### Assumption ledger

**Root problem:** four follow-ups from PR #685; this plan closes the two
actionable ones (doc drift, silent tier-2 opt-in) and leaves the other two
named only, per the engineer's own priority read.

**Givens:** none beyond this plan's own reach — leaving
`deny-private-project-refs.sh` unchanged and follow-ups 3/4 unaddressed are
in-repo scope decisions the plan declines to make, not externally-fixed
conditions; both are recorded under **Out of scope** with their reasons
instead of here.

**Per-mechanism:**

1. Fix `plugins/skill-management/skills/skill-review/SKILL.md:30,144`:
   replace "invoked/loaded by a parent via Glob+Skill-tool" with language
   matching what the three parents actually do (Glob, then read with the
   Read tool). anchors: root. `[verified: claude/.claude/skills/code-review/SKILL.md:25, claude/.claude/skills/plan-review/SKILL.md:57, claude/.claude/skills/plan-it/SKILL.md Step 2.5]` —
   grep confirms these are the only two sites making this claim anywhere
   under `plugins/` or `claude/.claude/skills/`.
2. Bump `plugins/skill-management/.claude-plugin/plugin.json` `version`
   `3.2.1` → `3.2.2` (patch). anchors: row1. Required because any edit
   under a plugin directory is hook-enforced by `plugin-semver`; classified
   patch because it's "correcting a skill to match its already-documented
   behavior." `[verified: plugins/plugin-semver/skills/plugin-semver/SKILL.md:38]`
3. `install-dev.sh`: add a gate, before venv creation, requiring a
   private-projects file to exist as a **regular file** (`-f`, not bare
   `-e`) — exit 1 with guidance pointing at
   `docs/private-project-redaction.md` "Opt-in: enable the blocklist" when
   it's absent. anchors: root.
   `[engineer-verified: user directed this exact mechanism over a hook-side fix]`
   — lighter-primitive check: a non-blocking print (matching
   `install.sh`'s existing `check_private_projects_file` TIP) was
   considered and rejected, because that's the status quo the engineer
   said doesn't close the gap; a runtime hook-side warning was also
   considered and rejected for the same reason the engineer gave when
   redirecting away from option 1/2 in the original question. The engineer
   named the mechanism and its location explicitly, so this row records
   the alternatives rather than re-arguing a settled choice.
   `-f` (not `-e`) is deliberate: it excludes a directory accidentally
   created at that path (a realistic contributor slip — `-e` would
   wrongly treat it as "opted in") and treats a dangling symlink the same
   as absent, matching the doc's own file-format contract.
   Path resolution mirrors the hook's own union — `$CLAUDE_CONFIG_DIR`
   when set and absolute, else `$HOME/.claude` — rather than `$HOME/.claude`
   alone: a contributor on a diverged `CLAUDE_CONFIG_DIR` profile who
   already populated `$CLAUDE_CONFIG_DIR/private-projects.md` is fully
   protected by the hook today, and a `$HOME`-only gate would hard-block
   them with no diagnostic despite that. This repo already duplicates this
   exact resolution rule inline once (`install.sh`'s `_report_account_sentinel`,
   ~line 484-487) rather than sourcing `claude/.claude/hooks/_lib.sh`'s
   `_lib_config_dir()` into a setup script — a deliberate small-duplication
   exception per repo CLAUDE.md's single-source-of-truth rule ("a small
   duplicated value can beat a bad abstraction built only to remove it"),
   not an oversight; the new gate follows that same established precedent
   as a third site rather than introducing a fresh resolution mechanism.
   `[verified: install.sh:481-514 _report_account_sentinel; claude/.claude/hooks/_lib.sh:106-120 _lib_config_dir]`
4. The gate in row 3 keys on **existence**, not populated content — a
   contributor with nothing to blocklist can create the file comment-only
   (the same minimal form the doc's own opt-in snippet produces before any
   project name is appended). anchors: row3.
   `[verified: docs/private-project-redaction.md "File format" section — comment/blank lines are ignored, not required to be absent]`
   Hard-blocking a whole script on this file's existence is safe to scope to
   `install-dev.sh` alone because CI never invokes it — it installs
   `requirements-dev.txt` directly instead.
   `[verified: .github/workflows/tests.yml:142]`
5. `install.sh`: add contributor-intent prompting as an **inner ungated
   prompt-logic function plus an outer TTY-gated wrapper** — the same
   two-function split `_prompt_sentinel_opt_in`/`configure_machine_level_opt_ins`
   already uses, and for the same reason: `_prompt_sentinel_opt_in` has
   **no** `[ -t 0 ]` guard of its own ("Caller must check `[ -t 0 ]` before
   invoking this — it has no TTY guard of its own," `install.sh:310-312`);
   the guard lives only in the wrapper, `configure_machine_level_opt_ins`
   (`install.sh:355`). A single self-contained function with the TTY check
   inline — this row's own earlier draft — cannot be exercised by a piped-stdin
   test at all: a subprocess fed via pipe is never a TTY regardless of what's
   piped, so every such test would silently fall into the non-interactive
   branch and a "yes" test and a "no" test would pass identically without
   distinguishing anything. Wrap both functions and the call site in a new
   `# INSTALL_TEST_FIXTURE: contributor-intent-prompt — start/end` marker
   pair, mirroring the `machine-level-opt-ins` marker `install.sh:309-374`
   — every other testable `install.sh` function is marker-delimited (9
   existing pairs, each backing a dedicated test file) precisely because the
   script is top-to-bottom executing with side effects at nearly every
   top-level statement, so nothing outside a marker pair is isolable for a
   subprocess test. On yes, points at `install-dev.sh` and names what it
   will require; on no or non-interactive, keeps today's static "Optional
   (contributors): run the hook test suite" hint. Replaces the closing
   block at `install.sh:810-813`. anchors: root.
   `[verified: install.sh:310-312,355 _prompt_sentinel_opt_in/configure_machine_level_opt_ins; install.sh:309,374 INSTALL_TEST_FIXTURE markers]`
6. `claude/.claude/scripts/tests/test_install_dev.py`: `_run_script`
   currently defaults `HOME` to the real invoking environment's `$HOME`
   (`env.get("HOME", "/tmp")`, no override at **any** call site — all 13
   confirmed) — row 3's gate would make every existing test in this file
   read the real developer/CI machine's `~/.claude/private-projects.md`.
   Isolate `HOME` per test run (extend the existing `repo_root_stub`
   fixture to also create a fake-home subdirectory pre-seeded with a
   comment-only `private-projects.md`, and thread that path into
   `_run_script`'s `env["HOME"]` — creating the fixture directory alone is
   not sufficient without also changing what `_run_script` passes as
   `HOME`) before row 3 lands, not after: land rows 3 and 6 atomically in
   the same commit, or row 6 strictly first — landing row 3 first breaks
   every test in this file on any machine, including CI's own runner
   `$HOME`, before the isolation fix reaches it. anchors: row3.
   `[verified: claude/.claude/scripts/tests/test_install_dev.py:56, all 13 _run_script call sites]`
7. New test coverage for row 3's gate: missing file → exit 1 with
   `private-projects.md` and the docs pointer in stderr; file present
   (even comment-only) → falls through to existing ensurepip/venv logic
   unchanged; a directory accidentally created at that path → exit 1 (same
   as missing, per row 3's `-f` semantics); a dangling symlink at that path
   → exit 1 (same as missing); a permission-denied (`chmod 000`) regular
   file → still passes (`-f` doesn't require read access, matching the
   existence-only bar row 4 sets). anchors: row3,row4.
8. New test coverage for row 5's two-function split, following the
   existing `test_install_sh_machine_level_opt_ins.py` INSTALL_TEST_FIXTURE
   extraction pattern: call the inner (ungated) prompt-logic function
   directly with piped stdin to exercise the yes/no branches — the same
   approach that file's `_run_prompt_sentinel_opt_in` already uses to reach
   `_prompt_sentinel_opt_in` around its own `[ -t 0 ]` guard — plus a
   non-interactive-fallback test for the outer wrapper. anchors: row5.
   `[verified: claude/.claude/hooks/tests/test_install_sh_machine_level_opt_ins.py:74-79]`
9. `docs/private-project-redaction.md`: one line each in "Opt-in: enable
   the blocklist" and "For fork contributors" noting `install-dev.sh` now
   enforces this file's existence before contributor setup completes.
   anchors: row3. Single source of truth stays the doc; `install.sh`/
   `install-dev.sh`'s own comments should cite it, not restate the
   mechanics.
10. `README.md` "Private-project redaction": one line, matching the
    section's existing terse tier-summary style, noting the
    `install-dev.sh` gate. anchors: root.
    `[verified: README.md:407-415 current three-tier summary]`

## Critical files

- `plugins/skill-management/skills/skill-review/SKILL.md` — lines 30, 144
  (the two-line doc fix)
- `plugins/skill-management/.claude-plugin/plugin.json` — patch version bump
- `install-dev.sh` — new `-f` existence gate, path resolved via the same
  `$CLAUDE_CONFIG_DIR`-else-`$HOME/.claude` rule `install.sh`'s
  `_report_account_sentinel` already duplicates inline (reuse that
  resolution snippet, don't source `claude/.claude/hooks/_lib.sh`);
  inserted after the existing CWD-anchor step (renumber the `Step N`
  comments that follow)
- `install.sh` — new inner ungated prompt-logic function + outer
  TTY-gated wrapper (mirrors `_prompt_sentinel_opt_in`/
  `configure_machine_level_opt_ins`), wrapped in a new
  `INSTALL_TEST_FIXTURE: contributor-intent-prompt` marker pair; replaces
  the static closing hint at lines 810-813
- `claude/.claude/scripts/tests/test_install_dev.py` — isolate `HOME`
  across existing call sites (reuse opportunity: extend the existing
  `repo_root_stub` fixture rather than adding a parallel one, and thread
  the fixture's home path into `_run_script`'s `env["HOME"]`), add the new
  gate's test class (including the directory/symlink/permission-denied
  cases); land atomically with, or strictly before, the `install-dev.sh`
  gate itself
- new test file (or extension of an existing `test_install_sh_*.py`) for
  the new contributor-intent-prompt marker pair — reuse
  `test_install_sh_machine_level_opt_ins.py`'s extraction and
  direct-inner-function stdin-piping pattern rather than reinventing them
- `docs/private-project-redaction.md` — one line each in two existing
  sections
- `README.md` — one line in the existing "Private-project redaction" section

## Verification

- `../../../.venv/bin/pytest claude/.claude/` (full suite, from the
  worktree) — must stay green, including the updated `test_install_dev.py`
  and the new `install.sh` test coverage.
- `../../../.venv/bin/ruff check claude/.claude/`
- `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` —
  covers both modified shell scripts.
- Manual: run `./install-dev.sh` from a clean checkout with no
  `~/.claude/private-projects.md` → confirm non-zero exit and guidance
  naming the file and the docs section; create the file (comment-only) and
  re-run → confirm it proceeds to the existing venv logic unchanged; repeat
  with a directory at that path and a dangling symlink → confirm both exit
  1 the same as missing; repeat with `CLAUDE_CONFIG_DIR` set to a diverged
  directory holding its own populated `private-projects.md` and nothing at
  `$HOME/.claude` → confirm the gate passes.
- Manual: run `./install.sh` interactively, answer "y" to the new prompt →
  confirm it points at `install-dev.sh` and names the requirement; answer
  "n" or run non-interactively (piped) → confirm the original static hint
  is unchanged.
- `/skill-review` on the `skill-review` SKILL.md diff itself (self-review,
  per `.claude/rules/skill-and-agent-self-review.md`).

## Out of scope

- `deny-private-project-refs.sh` itself — untouched; the fix for tier-2
  silence lives entirely at setup time per the engineer's redirect, not in
  the hook's runtime behavior.
- Follow-up 3 (`gh issue create`/`gh issue comment` unscanned) — pre-existing,
  already documented in `docs/private-project-redaction.md` Known gaps;
  engineer confirmed leaving it named only.
- Follow-up 4 (detective-only control, no preventive layer) — speculative
  design work, not a defect; engineer confirmed leaving it named only.
- `.claude/plans/engagement-lessons-fixes.md` — also contains the
  "Glob+Skill-tool" phrase this plan corrects elsewhere, but it's an
  already-merged historical plan record (preserved-content per repo
  CLAUDE.md Axis 3); it is not edited by this plan.
