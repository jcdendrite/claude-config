# resume-context.sh: add a --model flag

## Context

Let `resume-context.sh` forward a `--model` value to the `claude` launcher it
execs, so a user resuming a `/handoff` or `/brief` continuity file can pick
the model for the new session the same way they'd run `claude --model
opus` directly, instead of being stuck with the launcher's default model
resolution. The script currently execs `"$LAUNCHER" --append-system-prompt-file
"$DEST" "<fixed prompt>"` with no room for any other launcher flag; the user
asked for this specifically because they're used to passing `--model` on
every interactive `claude` invocation and lose that ability whenever they
resume through this script instead.

## Approach

Add a `--model <value>` flag to `resume-context.sh`'s existing arg-parse loop, and build the launcher's argv as a never-empty bash indexed array so the optional `--model` pair can be appended without duplicating the `exec` line. `claude-auto.sh:17` already solves this exact problem (conditional `--model` pair forwarded to `claude`) with an indexed array in the same directory, so this reuses the repo's established shape rather than inventing one.

Concretely, four edits to `resume-context.sh`:

1. A `--model)` case in the loop at lines 200-227, storing `LAUNCH_MODEL=$2` with the same missing-argument guard `--cwd` carries at lines 207-210 (`resume-context.sh: --model requires a value`). No `_lib_sanitize_for_terminal` display copy — see Row 8.
2. A mutual-exclusion check beside the existing one at lines 237-240, same message style: `resume-context.sh: --model is not valid with --consume-only (that mode never launches)`.
3. Replace the fixed `exec` at line 342 with an array built immediately above it (after the `--cwd` `cd` block at lines 335-340, since `DEST` is only known post-`mktemp`):

   ```bash
   LAUNCH_ARGS=(--append-system-prompt-file "$DEST")
   [[ -n "$LAUNCH_MODEL" ]] && LAUNCH_ARGS+=(--model "$LAUNCH_MODEL")
   exec "$LAUNCHER" "${LAUNCH_ARGS[@]}" "<existing prompt text, unchanged>"
   ```

   The `--model` pair is **appended after** the `--append-system-prompt-file` pair, never prepended: with the flag absent the emitted argv stays byte-identical to today's, which is what keeps the `--consume-only` hook caller and the two index-based assertions at `test_resume_context.py:171-173` and `:193-194` valid.
4. `usage()` (lines 129-134) and the header `Usage:` block (lines 9-12) both gain `[--model <value>]` on the launch-mode line — two copies of the same string inside one file, a pre-existing duplication that must be updated together.

Alternatives weighed for edit 3. Unquoted `${LAUNCH_MODEL:+--model "$LAUNCH_MODEL"}` inline in the `exec` relies on word-splitting to become two argv elements, which ShellCheck flags (SC2086) and which breaks on any value containing a space. Two `exec` lines under `if`/`else` duplicates the ~300-character prompt string, forcing it into a variable anyway — strictly more code than the array for the same result.

**Root problem.** A user resuming a continuity file through this script cannot choose the new session's model, because line 342's `exec` is a fixed argv with no seam for any launcher flag beyond `--append-system-prompt-file`.

**Givens** (treated as fixed, each beyond this design's reach):

- **G1** — `claude` owns model-name validation and alias resolution; this script cannot know the valid set. Reason: upstream vendor CLI owns it.
- **G2** — the launcher may be an arbitrary user-supplied wrapper, not `claude` itself (`RESUME_CONTEXT_LAUNCHER`, documented at lines 30-35 as a production seam). Reason: the wrapper's own flag handling is that script's contract, not this one's.

(A third candidate given — what `/handoff`/`/brief` embed in a continuity file's resume command — was dropped from this list on plan-review: `claude-skills/skills/handoff/SKILL.md` and `brief/SKILL.md` are in-repo, editable artifacts, not a condition beyond this design's reach. It's recorded once, under **Out of scope**, with its reason.)

**Rows.**

Row 1 [engineer-verified]: the flag is space-separated `--model <value>`, with no `--model=value` form — anchors: root.

Row 2 [verified: `claude/.claude/scripts/claude-auto.sh:21`]: the Step 4 evidence statement ("grepped the repo's scripts for that syntax and found no precedent anywhere") is inaccurate as written — `--model | --model=*)` does appear there. It is a *detection* pattern for deferring to a caller-supplied flag, never parsed into a variable, so no script under `claude/.claude/scripts/` parses `--model=value`; Row 1's conclusion stands on the corrected evidence — anchors: row1.

Row 3 [verified: `resume-context.sh:219-222`]: `--model=opus` therefore falls to the `-*` catch-all and exits 1 with usage, pre-move. Identical to `--cwd=/x` today, so the rejection is consistent rather than a new asymmetry — anchors: row1.

Row 4 [engineer-verified]: the value gets no validation on this script's side — anchors: root, G1.

Row 5 [verified: `resume-context.sh:229-232`]: the realistic missing-value typo (`--model <path>`, value omitted) is already caught pre-move — `--model` swallows the path, leaving zero positionals, and `$# -ne 1` exits 1. The residual is narrower than it first looks: only a value that is itself dash-prefixed (`--model -x <path>`) survives parsing, gets forwarded verbatim, and is rejected by `claude` *after* the move — leaving the continuity file consumed but recoverable via the destination and reload hint already printed at lines 329-330 — anchors: row4.

Row 6 [engineer-verified, challenged and re-affirmed]: no dash-prefix guard is added for the Row 5 residual. Considered and set aside: it would cost four parser lines plus a test to close a near-zero-frequency invocation, contradict Row 4, and introduce a check `--cwd` has no counterpart for. Recording the residual is the proportionate treatment — anchors: row5.

Row 7 [engineer-verified]: `--model` is rejected together with `--consume-only` — anchors: root.

Row 8 [verified: `test_resume_context.py:293-326`]: both flag orderings need a test, matching the existing `--cwd` pair. Both work under the design without special handling — `--consume-only` is a standalone token, so it is never swallowed as `--model`'s value in either order — anchors: row7.

Row 9 [verified: `resume-context.sh:71-78`, `test_resume_context.py:361-400`]: every stderr print of a user-controlled value in this script uses a sanitized display copy, backed by a paired raw-escape-byte and bidi-override test per channel. The design keeps `LAUNCH_MODEL` out of stderr entirely, so it needs neither. **Implementation constraint:** do not add a "launching with model X" announcement — that would open a new terminal-injection channel requiring `_lib_sanitize_for_terminal` plus its two channel tests — anchors: root.

Row 10 [verified: `claude/.claude/scripts/tests/test_no_bash4_constructs.py:4-5`]: this repo gates against bash-4-only tokens (`declare -A`, `mapfile`, `readarray`, `sort -V`). A plain indexed array with `+=` is bash 3.1+ and not among them. The array is additionally never empty by construction, so it sidesteps the pre-4.4 `set -u` unbound-variable behavior for `"${arr[@]}"` on a zero-element array — the reason `--append-system-prompt-file "$DEST"` stays *inside* the array rather than inline on the `exec` — anchors: root.

Row 11 [verified: `claude/.local/bin/resume-context`]: the PATH wrapper users actually type is a two-line `exec "$HOME/.claude/scripts/resume-context.sh" "$@"`, so `--model` reaches the script with no change to that file — anchors: root.

Row 12 [verified: `claude-auto.sh:17-27`]: composing this with the documented `RESUME_CONTEXT_LAUNCHER=claude-auto` seam does not produce a duplicate `--model`. That wrapper scans `"$@"` for `--model`, finds the forwarded one, and drops its own `(--model sonnet)` default, so the user's value wins. Its scan `break`s only at a literal `--`, which this script never emits into the launcher argv — anchors: G2.

Row 13 [verified: `resume-context.sh:13-16`]: the only in-repo non-human caller is `consume-durable-continuity-file-on-read.sh` via `--consume-only`, which never passes `--model`. The absent-flag argv pin (Verification item 2) is what keeps that caller safe against a future reordering — anchors: root.

Row 14 [engineer-verified, affirmed]: `_lib_print_recovery_hint` (`_lib.sh:2714-2726`) gains no `--model` forwarding. Independent reason to affirm: it is shared with `find-consumed-continuity-file.sh`, which has no invocation-model context, so threading a value through would add a parameter to a two-caller helper to serve one of them — and the hint describes a *future* manual reload, not the current invocation — anchors: root.

## Critical files

Three files, one `code-writer` dispatch, no split. The script's argv, the test's argv assertions, and the doc's flag description are mutually dependent — a second dispatch would need the whole shared design restated, which plan-it's dispatch-split rule names as the case not to split.

- **`claude/.claude/scripts/resume-context.sh`** — the four edits in Approach. **Reuse:** the `--cwd` case at lines 206-214 as the shape for the new case's missing-argument guard; the check at lines 237-240 as the shape for the mutual exclusion; `claude-auto.sh:17-27` as the array idiom. Header gains a `--model` block after the `--cwd` block (lines 19-28), ~5 lines, one durable fact per sentence:

  ```
  # --model <value> forwards to the launcher as `--model <value>`, so a resume
  # can pick the new session's model the same way `claude --model <value>` would.
  # Forwarded verbatim with no validation here, since `claude` owns model alias
  # resolution — a bad value therefore fails after the move, not before it.
  # Rejected together with --consume-only, since that mode never launches.
  ```

  Plus one comment above the array: `# Array so the optional --model pair appends without duplicating the exec line; never empty, so "${LAUNCH_ARGS[@]}" is safe under set -u.`

- **`claude/.claude/scripts/tests/test_resume_context.py`** — new `class TestModelFlag:` placed after `TestCwdFlag` (which spans lines 247-445, immediately before `TestConsumeOnlyMode` at line 446), mirroring its structure. **Reuse:** `_install_recorder` (lines 29-35), `_install_cwd_recorder` (lines 38-48), and `_run` (lines 51-64) unchanged — no new harness. Five tests:
  1. `--model opus <src>` → recorded argv is exactly `[--append-system-prompt-file, <DEST>, --model, opus, <prompt>]`.
  2. No `--model` → recorded argv is exactly three elements with no `--model` token anywhere (the Row 13 pin against a future reordering).
  3. `--model` + `--consume-only`, both orderings → non-zero exit, source not moved.
  4. `--model` as the last token → non-zero exit, `--model requires a value` in stderr, no side effects.
  5. `--cwd <dir> --model opus <src>` via `_install_cwd_recorder` → launcher runs in `<dir>` *and* receives the `--model` pair; this is the real-world worktree-resume invocation.

  No sanitization-channel tests, deliberately — per Row 9 the value never reaches stderr.

- **`docs/scripts.md`** — the `resume-context.sh` entry at line 163 gains the `--model` description after its `--cwd` sentence, and the fence at lines 165-169 gains one example line. This is the canonical per-flag reference for human readers; the wrapper-composition fact (Row 12) belongs here rather than in the script header, because this entry already documents the `RESUME_CONTEXT_LAUNCHER=claude-auto` seam three sentences later and this is the reader who hits the interaction.

Review surface: one domain (scripts), no new dependency, no new stderr or stdout channel, no change to the `--consume-only` contract or to any hook.

## Verification

`.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped command, run from the worktree root.

Given this diff it selects the scripts, hooks, and skills test directories: `resume-context.sh` matches `_is_scripts_dir_shell_script_change`, which adds `HOOKS_TESTS_DIR` and `SKILLS_TESTS_DIR` (`select-tests.py:458`) on top of its own scripts domain; `docs/scripts.md` adds the same two via the `DOCS_DIR` blanket (`:468`); the test-file edit adds `test_select_tests.py`. Two consequences worth naming:

- No separate ShellCheck invocation is needed — `claude/.claude/hooks/tests/test_shellcheck.py` is inside the selected `HOOKS_TESTS_DIR`, which is precisely why that cross-domain row exists (`select-tests.py:395-397`).
- `claude/.claude/scripts/tests/test_no_bash4_constructs.py` is in the selected scripts domain, so Row 10's bash-3.2 claim is checked mechanically rather than by inspection.

No `/skill-review`, `/agent-review`, or `plugin-semver` dispatch applies — the diff touches no `SKILL.md`, no agent file, and nothing under a plugin directory.

One check the test suite structurally cannot make, since every test stubs the launcher through `RESUME_CONTEXT_LAUNCHER`: no automated test proves the real `claude` binary accepts `--model` in the emitted position. Confirm once by hand after landing — resume a throwaway continuity file with `--model sonnet` and check the launched session's model — and treat it as a manual smoke check, not a suite gate.

## Out of scope

- **`_lib_print_recovery_hint` (`claude/.claude/hooks/_lib.sh:2714-2726`) and the not-found branch's hint (`resume-context.sh:274-277`)** — advisory strings for a future manual reload, and the helper is shared with a caller that has no model context (Row 14).
- **`claude-skills/skills/handoff/SKILL.md:133` and `claude-skills/skills/brief/SKILL.md:97`** — these fix the command construction the *writing skill* embeds, decided before a human picks a model. In reach (in-repo, editable) but deliberately left unchanged: `/handoff`/`/brief` write the resume command before any human has chosen a model for the *next* session, so there's nothing for the writing skill to embed yet — changing that embedding point is a decision outside this plan, not a condition this design depends on.
- **`README.md:483`** — a third site naming the two resume forms and explaining `--cwd`, in the Context management section. Left unchanged for the same reason as the two SKILL.md sites: it documents the skill-authored command construction, and `docs/scripts.md` is the canonical per-flag reference. Named explicitly here so a sibling-site audit reads as a decision rather than a miss.
- **`docs/skills.md:22-23` and `claude/.claude/agents/skill-fidelity-reviewer.md:85`** — verified non-sites: both use the bare `resume-context <path>` form and document no flags, so neither needs an edit.
- **A generic launcher-flag passthrough** (e.g. accepting arbitrary post-`--` flags to forward). Only `--model` was asked for, and each forwarded flag needs its own `--consume-only` interaction decision — a generic seam would defer exactly the decisions Rows 7 and 9 settle.
- **A dash-prefix guard on the `--model` value** (Row 6), and any stderr announcement of the chosen model (Row 9).
