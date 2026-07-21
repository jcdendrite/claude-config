# Harden durable continuity file modes with a PostToolUse hook

## Context

**Goal:** make owner-only (`0600`) permissions on files under `~/.claude/handoffs/`
and `~/.claude/briefs/` a mechanical guarantee rather than an agent-executed prose
recipe.

`handoff/SKILL.md` and `brief/SKILL.md` each instruct the agent to `mkdir -p` the
directory, `chmod 700` it, then `touch` and `chmod 600` the target file — all
before writing that file with the Write tool. Sessions have repeatedly reported
the file ending up at `664` anyway, most recently with the diagnosis "the Write
tool reset the file to 664 after the touch/chmod 600; the skill's recipe puts the
chmod before the write, which doesn't hold."

**That diagnosis is wrong, and the plan must not encode it.** Measured directly at
`umask 0002`:

| Sequence | Resulting mode |
| --- | --- |
| `touch` + `chmod 600`, then Write | `600` (inode changes; Write re-applies the prior mode) |
| Write to a path with no pre-existing file | `664` |

So the recipe's ordering is correct, and Write does preserve an existing mode. The
real failure is that the `touch`/`chmod` never ran **against the exact path later
written**. The recipe is a fenced block containing the literal placeholder
`descriptive-slug`, which the agent must substitute identically in a Bash call and
again in a Write call. A skipped recipe, or any divergence between the two
substitutions, silently yields a `664` file (plus a stray empty `600` one).

**Observed frequency** on this machine: 7 of 16 files in `~/.claude/handoffs/` and
2 of 10 in `~/.claude/briefs/` are group- and world-readable. Roughly half.

**Severity, stated honestly:** both directories are `0700`, so the `664` files are
*not* readable by another local account today — directory traversal is blocked.
The file mode is defense-in-depth, and matters when a file leaves the directory
(rsync, tarball, backup, `cp`). This is a correctness-of-a-stated-control problem,
not an active exposure. The plan should not be justified as an incident.

**Intended outcome:** every file *written by the Write, Edit, or MultiEdit tool*
into either directory ends up `0600` without depending on agent compliance, and the
two skill bodies stop claiming a control they do not reliably deliver.

## Approach

Add a `PostToolUse` hook on `Edit|Write|MultiEdit` that `chmod 600`s any file the
tool wrote under `~/.claude/handoffs/` or `~/.claude/briefs/`.

**Rationale.** This repo's contributor guide already answers this class of problem:
a recurring "whenever X happens, do Y" behavior is a hook, because the harness
executes hooks and nothing in a SKILL.md body can guarantee execution. A *post*-write
hook is also immune to the verified root cause specifically — it takes the path from
the tool call's own `tool_input.file_path` rather than requiring the agent to retype
a substituted slug a second time.

**Coverage boundary — do not overstate this.** The hook covers tool-authored writes
only. A file created by the Bash tool (`cat > …`, `cp`, `sed -i`, a script), or by
any non-Claude writer (an editor, a git GUI, another terminal), never produces a
`Write`/`Edit`/`MultiEdit` tool call and is a structural no-op for this hook. Both
directories currently hold such ad-hoc files (PR-body drafts, a `.sql` extract,
commit-message drafts); the one-off backfill sweep in Verification cleans up the
existing ones, but new Bash-authored files will still land at the umask default.
This limitation must be stated in the hook header and in `docs/hooks.md`, mirroring
the Limitations pattern `docs/security-hardening.md` already uses for the PII hooks.

**Alternatives weighed and set aside:**

- *Reorder the `chmod 600` after the write in both skill bodies.* Cheapest possible
  change and it matches the incoming report, but the report is a misdiagnosis: a
  post-write `chmod` against a mistyped path fails exactly as the pre-write one
  does. Fixes the stated symptom, not the cause.
- *Delete the file-mode layer; rely on the `0700` directory alone.* Defensible —
  the directory mode is what actually blocks other accounts — but surrenders
  protection for files copied out of the directory.
- *PreToolUse hook.* Cannot set the mode of a file that does not exist yet, and
  PreToolUse's only lever is deny, which would block legitimate writes.
- *Change the machine `umask`.* Blast radius far beyond these two directories.

**Not an over-powered primitive.** The chosen mechanism is a fail-open,
`hook-class: informational` hook that only ever calls `chmod` and always exits `0`
— strictly less powerful than the 19 `gate`-class hooks in this repo, which can deny
tool calls. The two lighter primitives available are enumerated above with the
reason each fails.

### Design decisions inside the hook

1. **Matcher is `Edit|Write|MultiEdit`**, matching the four existing write-side
   hooks in `settings.json` (`ask-review-permissions.sh`, `require-plan-review.sh`,
   `require-worktree-for-file-writes.sh`, `require-memory-skill.sh`). An earlier
   draft used `Write|Edit`; omitting `MultiEdit` would leave an amended continuity
   file unhardened, and diverging from the sibling matcher shape is exactly what
   this repo's audit-structural-siblings rule warns against.

2. **Match any file in the two directories**, not just `*-handoff.md` / `*-task.md`.
   The sibling hook `consume-durable-continuity-file-on-read.sh` uses the narrow
   suffix glob because *moving* a non-continuity file would be wrong. Chmodding one
   is idempotent and non-destructive, so that risk does not transfer.

3. **Canonicalize the path before matching.** A textual
   `case "$FILE_PATH" in "$HOME"/.claude/handoffs/*)` glob treats `..` as ordinary
   characters, so a path merely *prefixed* by the directory could resolve elsewhere
   and get chmodded. Resolve with `realpath` (fall back to skipping the file if
   resolution fails) and compare the resolved prefix. The glob must also carry a
   trailing slash — `handoffs/*` not `handoffs*` — or it collides with a sibling
   directory such as `~/.claude/handoffs-archive/`.

4. **Skip symlinks.** `chmod` dereferences symlinks, so chmodding a symlink planted
   at a matching path would narrow permissions on an arbitrary target.
   `resume-context.sh` already refuses symlink sources for this exact reason
   (documented in `docs/scripts.md`). Guard with `[ -f ]` plus `[ ! -L ]`.

5. **No kill-switch.** The sibling hook ships one because it *moves the user's file*,
   which is surprising and can break a workflow. Tightening the mode of a file in the
   user's own `0700` directory has no legitimate failure mode to escape, and a
   kill-switch on a hardening control is an anti-feature.

6. **Fail-open with a concrete mechanism.** `set -uo pipefail` (matching every
   existing hook — never `-e`, per `require-memory-skill.sh`'s stated rationale),
   every `jq` extraction using the sibling's `|| exit 0` idiom, the `chmod` itself
   guarded (`chmod 600 "$RESOLVED" 2>/dev/null || true`) so a file that vanished in a
   race cannot abort the script, and an unconditional trailing `exit 0`.

7. **Filter `tool_name` and `file_path` inside the script**, not only via the
   settings.json matcher — the repo's stated hook defense-in-depth rule.

8. **Header documents known gaps**, following the sibling hook's convention: the
   Bash/non-Claude writer gap (decision above); case-sensitive path matching vs.
   case-insensitive macOS APFS; a symlinked *ancestor* directory that the literal
   match misses; and the post-write window in which the file exists at the umask
   default before the hook narrows it — sub-second and irrelevant while the
   directory is `0700`, but real and previously undocumented.

## Critical files

**Create**

- `claude/.claude/hooks/harden-durable-continuity-file-mode.sh` — `# hook-class:
  informational` on line 2 (`test_hook_alignment.py` enforces presence and allowed
  values). Does **not** source `_lib.sh`: `_lib_parse_tool_input_or_deny` is a
  fail-closed gate helper that emits a deny envelope, which an informational
  PostToolUse hook must never do. Parse stdin with bare `jq`, as the sibling does.
- `claude/.claude/hooks/tests/test_harden_durable_continuity_file_mode.py`

**Modify**

- `claude/.claude/settings.json` — extend the existing `PostToolUse` array with an
  `{"matcher": "Edit|Write|MultiEdit"}` entry. This is the stowed global settings
  file, not the project-scoped `.claude/settings.json`; the hook applies to every
  session on the machine.
- `docs/hooks.md` — a per-hook entry is mandatory
  (`test_hook_alignment.py::test_hook_documented_in_hooks_md`). Include the coverage
  boundary from the Approach section.
- `claude/.claude/skills/handoff/SKILL.md`, `claude/.claude/skills/brief/SKILL.md` —
  remove the `touch` and `chmod 600` lines from the `HOOK_TEST_FIXTURE` recipe,
  leaving `mkdir -p` + `chmod 700`, which the hook cannot replace (a hook cannot
  create the directory, and the directory mode remains the control that actually
  blocks other accounts). Replace the paragraph justifying the file-level layer with
  one sentence noting the file mode is hook-enforced. Keeping both layers would be
  the compounding-defensive-layers pattern the repo warns against.
- `claude/.claude/skills/tests/test_skills.py` —
  `test_handoff_and_brief_write_recipe_executes_to_durable_path` must drop **both**
  the `expected_file.is_file()` existence assertion and the `file_mode == 0o600`
  assertion (the shortened recipe creates no file at all), keeping only the
  `expected_dir` existence and `0700` checks. Its docstring currently describes the
  four-command recipe and claims the test proves the file-mode control; rewrite it
  to state only the invariant it still guards, so a later reader does not delete the
  new hook test as redundant.
- `README.md` — the "Self-consuming continuity files" bullet (line 67). Check
  `test_doc_counts.py` before adding or removing any README row.

**Reuse — do not reinvent**

- `consume-durable-continuity-file-on-read.sh` is the direct structural precedent:
  same two directories, same stdin filtering, same fail-open discipline, same
  known-gaps header convention.
- `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py` as
  the test template (note its `test_read_same_dir_wrong_suffix_is_noop`
  boundary-case precedent), and `tests/conftest.py` for isolated-`$HOME` fixtures.
- `test_require_plan_review.py::test_settings_exitplanmode_matcher_exists_and_isolated`
  as the precedent for the settings-registration test below.

## Verification

**Pre-merge — the only steps performable from the linked worktree.** `~/.claude/hooks`
and `~/.claude/settings.json` are stow symlinks resolving into the **main** worktree,
so the new hook and the settings entry are not live until this branch merges and the
main worktree runs `git pull`.

1. `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_harden_durable_continuity_file_mode.py`
   — synthetic-stdin unit tests covering: a write into each directory lands `600`; a
   non-suffix filename (`x.sql`) lands `600`; a path outside both directories is
   untouched; a directory-prefix collision (`~/.claude/handoffs-archive/x.md`) is
   untouched; a `..`-traversal path textually prefixed by the directory is untouched;
   `Edit` and `MultiEdit` are matched as well as `Write`; a non-matching `tool_name`
   is a no-op; malformed stdin, empty stdin, and an absent `jq` binary each exit `0`
   silently; a symlink at a matching path is **not** chmodded and its target's mode is
   unchanged; a missing file does not crash; no deny envelope is emitted under any
   input.
2. A settings-registration unit test asserting the `PostToolUse` array contains an
   `Edit|Write|MultiEdit`-matcher block whose `hooks[].command` references this
   script. **Highest-priority test in the plan:** it is the only automated backstop
   against a matcher typo, a wrong-tool matcher, or the entry landing in `PreToolUse`
   — none of which any other test would catch, and whose only alternative backstop is
   a manual step that is easy to skip on a routine merge.
3. `../../../.venv/bin/pytest claude/.claude/` — full suite, to catch the
   `test_skills.py`, `test_hook_alignment.py`, and `test_doc_counts.py` couplings.
4. `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

**Post-merge — after the branch lands and the main worktree is pulled.**

5. Live smoke test, run by the user in a fresh session. **This is the one gate this
   repo cannot automate** — the unit tests prove the script's logic against synthetic
   stdin, but nothing automated proves the harness actually invokes `PostToolUse` for
   these tools. Treat it as a required checklist item, not an optional aside. Write a
   file into `~/.claude/handoffs/` and `stat -c '%a'` it. Repeat under `claude -p`
   (headless) and from a subagent's own write: `docs/hooks.md` already documents that
   `SessionEnd` does not fire under `claude -p`, which is not evidence about
   `PostToolUse` but is evidence that per-event firing cannot be assumed in this
   codebase. Both cases are currently unverified assumptions.
6. One-off backfill sweep of the pre-existing files (approved):
   `find "$HOME/.claude/handoffs" "$HOME/.claude/briefs" -maxdepth 1 -type f -exec chmod 600 {} +`
   — `-type f` does not dereference, so a top-level symlink reports as `-type l` and
   is correctly excluded, matching the hook's own guard; `-maxdepth 1` mirrors the
   hook's single-level match. Paths are quoted against a `$HOME` containing spaces.
   `find` errors to stderr if a directory does not exist yet — harmless for a manual
   one-off. Local-only, touches nothing tracked, reversible.

**Known rollout window:** between merging this change and a given machine running
`git pull`, that machine has neither the removed per-file recipe lines nor the new
hook. Self-resolving on the next pull; noted so it is not mistaken for a regression.

## Out of scope

- Changing the machine `umask`.
- Hardening Bash-authored or non-Claude-authored files in these directories. The
  coverage boundary is documented rather than closed; closing it would need a
  different mechanism (a Bash-tool PostToolUse hook or a filesystem watcher) with a
  different risk profile.
- Other `~/.claude/` subdirectories (`*-markers/`, `plans/`, `pii-patterns.md`).
  `docs/security-hardening.md` covers those separately.
- Any modification to `resume-context.sh` or the consume-on-read hook.
- Rewriting the `HOOK_TEST_FIXTURE` anchor comment convention.
