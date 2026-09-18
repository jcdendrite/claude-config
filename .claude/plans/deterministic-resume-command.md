## Context

Make the `resume-context` command that `/handoff` and `/brief` produce reach the engineer deterministically and correctly, regardless of what the authoring model says in its chat reply. Root-cause-analysis this session found that displaying the command is currently unenforced model recall: `handoff/SKILL.md` §7 and `brief/SKILL.md` §7.5 only instruct writing the command into the continuity file, with nothing requiring it to also reach chat, and `check-handoff.py` only rejects literal unresolved placeholder tokens, not a resolved-but-wrong config-dir value. Real evidence confirmed the file itself is always correct (mechanically gated), so the gap is specifically the chat-display step. Fix: a `PostToolUse` hook on `Edit|Write|MultiEdit` matching `<config-dir>/handoffs/*-handoff.md` and `<config-dir>/briefs/*-task.md` that independently computes and announces the correct command via `systemMessage`, mirroring the already-shipped read-side pattern in `consume-durable-continuity-file-on-read.sh`.

## Approach

Add one `PostToolUse` hook, `claude/.claude/hooks/announce-resume-command.sh`, wired into `claude/.claude/settings.json`'s existing `"Edit|Write|MultiEdit"` group. When the written path matches the durable continuity-file glob, it composes the resume command itself — from the resolved config dir, the tool payload's own `file_path`, and the session's working directory — and emits it on both hook output channels: `systemMessage` for the engineer and `hookSpecificOutput.additionalContext` for the model. The command's correctness then depends on `_lib_config_dir` and `git rev-parse`, not on the authoring model recalling §7 or §7.5.

Two details carry most of the design weight. First, `--cwd` is derived from the hook payload's top-level `.cwd` field, not from the hook process's `$PWD` — the repo already settled that `.cwd` is the session's working directory against a live harness, and `$PWD` is not a signal any hook here trusts. Second, both interpolated paths pass an `LC_ALL=C` character allowlist before they reach the emitted strings, and the hook stays silent if either fails; that single gate is what makes interpolating a tool-supplied `file_path` into model-visible context safe, and it simultaneously removes the shell-quoting and terminal-escape problems.

### Assumption ledger

**Root problem:** the `resume-context` command that `/handoff` and `/brief` produce reaches the engineer only if the authoring model chooses to restate it in chat and restates it correctly — an unenforced recall step, with no mechanism that independently computes the right command and puts it in front of a human.

**Givens** (conditions this design treats as fixed, each beyond its own reach):

- **G1.** A `PostToolUse` hook cannot deny, retry, or rewrite the tool call it follows, and cannot author the model's chat reply. The harness hook contract imposes this; the hook can only add text on the two documented channels (`row 1`, `row 2`).
- **G2.** `claude/.claude/settings.json` and `claude/.claude/hooks/` ship to every stow consumer, so this hook fires by default for everyone who pulls once merged. The repo's stow model owns that, not this plan. (Whether a per-user opt-out sentinel exists is a decision this plan makes, below — not a further given; see the "No `config-keys.psv` row" mechanism justification.)
- **G3.** Whether the bare `resume-context` wrapper resolves depends on `~/.local/bin` being on PATH, which `install.sh` manages for bash/zsh and README documents as a manual step for fish. This plan cannot make a given machine's PATH true (`row 8`).
- **G4.** The payload's `.cwd` reports where the *session* is, not where the *work's branch* lives. The harness owns `.cwd`, and reconciling the two would require parsing the continuity file's own §4, which `row 16` forecloses.

| # | Assumption | Tag |
|---|---|---|
| 1 | `systemMessage` is shown to the user only; `hookSpecificOutput.additionalContext` on `PostToolUse` lands in the model's context next to the tool result | `[verified: .claude/plans/warn-read-consumes-handoff.md row 1, quoting code.claude.com/docs/en/hooks — "systemMessage \| none \| Warning message shown to the user"; "PostToolUse: next to the tool result"]` |
| 2 | `PostToolUse` fires only after the tool call succeeds, so a failed write emits nothing | `[verified: same plan, row 2]` |
| 3 | The payload's top-level `.cwd` is the session's working directory, and is this repo's established resolution signal in preference to the hook process's ambient cwd | `[verified: set-session-title-from-branch.sh:88-94 and its header lines 37-38; test_marker_worktree_keying.py:438-446, whose scope note records that what the harness puts in `.cwd` "was settled against the live harness"]` |
| 4 | A linked worktree is distinguished from the main tree by `git rev-parse --absolute-git-dir` differing from `git rev-parse --path-format=absolute --git-common-dir`; equal means main tree | `[verified: require-worktree-for-file-writes.sh:136-140, which names require-worktree-for-git-writes.sh as the shared idiom]` |
| 5 | Bash `case` globs match across `/` and across newlines, so the continuity-path glob alone does not constrain `$FILE_PATH` to a sane token | `[verified: .claude/plans/warn-read-consumes-handoff.md row 6 — "executed this session — a path containing embedded newlines matched `.../handoffs/*-handoff.md`"]` |
| 6 | The prior shipped decision in this same hook family deliberately refused to interpolate `$FILE_PATH` into model-visible context, calling it a semantic-injection channel that `jq --arg` does not close | `[verified: same plan, mechanism row "Interpolate $DEST only — not $FILE_PATH"]` |
| 7 | `^[A-Za-z0-9._/@+-]+$` matched under `LC_ALL=C` is this repo's established allowlist for a value flowing into a sensitive sink, and the locale pin — not the character class alone — is what makes it codepoint-wise rather than collation-order | `[verified: set-session-title-from-branch.sh:18-23 and :151-155]` |
| 8 | Bare `resume-context` is the documented user-facing invocation; the wrapper is a tracked stow file and `install.sh` manages the PATH entry | `[verified: claude/.local/bin/resume-context (execs $HOME/.claude/scripts/resume-context.sh); install.sh:859-915; README.md:103-106; docs/scripts.md:3]` |
| 9 | `resume-context`'s grammar is `[--cwd <dir>] [--model <value>] <continuity-file-path>`; `--cwd` is validated as an existing directory before any move and is rejected together with `--consume-only` | `[verified: resume-context.sh:9-34]` |
| 10 | The continuity-path glob has exactly one production copy today; this hook makes it the second | `[verified: repo-wide grep for `handoffs/*-handoff.md`/`briefs/*-task.md` — one `.sh` match, consume-durable-continuity-file-on-read.sh:120; every other hit is docs, skills, or a plan file]` |
| 11 | An informational `PostToolUse` hook in this repo may ship with no `config-keys.psv` kill switch | `[verified: nudge-transcript-toolkit.sh makes no _config_enabled call; docs/hooks.md:65 records nudge-unexpanded-skill-mention.sh keeping "no dedup state and no enable/disable sentinel … both deliberate engineer decisions"]` |
| 12 | Adding a `config-keys.psv` row changes `len(schema())` and therefore forces four prose count-claim edits | `[verified: test_doc_counts.py:509-533 — install.sh, claude/.claude/scripts/migrate-legacy-config.sh, and docs/config-file.md twice]` |
| 13 | A new hook must carry a `docs/hooks.md` `- **`name.sh`**` bullet, a line-2 `# hook-class:` header with one of four values, the exact `if ! . "${0%/*}/_lib.sh" 2>/dev/null; then` source line, `_lib_jq` rather than bare `jq`, and POSIX ERE only | `[verified: test_hook_alignment.py:167-196, :1046-1080, :1140-1189, :793, :988]` |
| 14 | `announce-` matches none of the gate-naming prefixes, so `# hook-class: informational` is consistent with the filename | `[verified: test_hook_alignment.py:127-141 — deny-/require-/enforce-/guard-/block-/check-*-guard, plus a two-name explicit set]` |
| 15 | The hook fires on every `Edit\|Write\|MultiEdit` match, registered in the existing matcher group rather than a new one; repeated firing during mid-draft edits is accepted as harmless | `[engineer-verified]` |
| 16 | The hook performs no divergence check and never parses the continuity file's own content — it announces the computed command unconditionally | `[engineer-verified]` |
| 17 | The continuity file's own §7/§7.5 content is already correct in practice; the gap is specifically the chat-display step | `[engineer-verified]` |
| 18 | `test_hook_alignment.py`'s `_LIB_SOURCE_HOOKS` is derived from `ALL_HOOKS` minus a named non-sourcing set, so a new sourcing hook needs no list edit there | `[verified: test_hook_alignment.py:1143-1173]` |

### Mechanism justification

- **A new `PostToolUse` hook on `Edit|Write|MultiEdit`, registered in the existing matcher group** (`anchors: root`, `anchors: row15`, `anchors: G1`). The write of the continuity file is the only mechanical event that reliably coincides with "a resume command now exists"; a hook on it is the lightest thing that fires without the model choosing to act.

  Lighter primitives considered and rejected:
  1. *Tighten `check-handoff.py` to compare §7's resolved value against `$CLAUDE_CONFIG_DIR`.* No new hook, no settings change — genuinely lighter. Fails on `row 17`: it validates the file, which is already correct, and its output never reaches chat. It also has no `/brief` counterpart (no `check-brief.py` exists), so the sibling gap stays open.
  2. *Add an imperative line to `handoff/SKILL.md` §7 and `brief/SKILL.md` §7.5 telling the model to print the command.* The lightest option of all — pure prose, no code. Fails because it *is* the root problem: the repo's own CLAUDE.md ("Should this be a hook?") states that memory and skill instructions cannot fulfil an automatic-trigger request.
  3. *Have the skills call a script that prints the command.* Still gated on the model choosing to run it, and it adds a Bash-tool call on a path where the worktree Bash guard and permission surface make execution less certain than a hook that fires on a write the model already performs.
  4. *Add a write-side arm to `consume-durable-continuity-file-on-read.sh`.* No new file — but that hook is registered on the `Read` matcher, and its contract is consuming a file. Merging an announce path into it would force a matcher change and put two unrelated contracts behind one kill switch.

  No heavier primitive is taken: no `PreToolUse` gate, no session-ID state file, no marker, no change to `resume-context.sh`, no new script.

- **Dual-channel emission from a single `_lib_jq -n --arg cmd` call** (`anchors: row1`, `anchors: root`). `systemMessage` is the deterministic artifact the engineer sees regardless of what the model says. `additionalContext` puts the same literal string in the model's context, so a chat reply drafted afterwards is quoting rather than recalling — which is also why no second mechanism is needed to make the *file's* §7/§7.5 text correct for `/brief`, the arm with no checker. One jq call and one `--arg` binding for both channels, mirroring `consume-durable-continuity-file-on-read.sh:133-144`.

- **`--cwd` derived from the payload's `.cwd`, gated on the linked-worktree comparison** (`anchors: row3`, `anchors: row4`, `anchors: row9`, `anchors: G4`). Resolve `PAYLOAD_CWD` from `.cwd`; if it is empty or not a git working tree, emit the bare command. Otherwise compare `--absolute-git-dir` against `--path-format=absolute --git-common-dir`: different means a linked worktree, and `git -C "$PAYLOAD_CWD" rev-parse --show-toplevel` supplies the `--cwd` value (the worktree root, correct even when the session sits in a subdirectory). Equal means main tree and no flag. This reproduces exactly what `handoff/SKILL.md` §7 and `brief/SKILL.md` §7.5 prescribe, so the announced command and a correctly-written file agree in both branches rather than diverging systematically.

  Alternatives rejected: (a) sniffing `.cwd` for a `.claude/worktrees/` path segment — a convention sniff where git already answers authoritatively, and wrong for any worktree created elsewhere; (b) emitting `--cwd` unconditionally whenever `.cwd` is in any repo — marginally simpler code, but it would make every main-checkout announcement differ in shape from the skills' own prescribed command, inviting someone to "fix" one side or the other; (c) reading the hook process's `$PWD` — `row 3` settles that the repo does not trust ambient cwd in hooks.

  Accepted residual, per `G4`: a session whose `.cwd` drifted out of the worktree where the work lives gets a command with no `--cwd`. That is the pre-existing behavior of a resume launched from the wrong directory, not a regression this hook introduces, and the hook's known-gaps comment states it.

  The three `git -C "$PAYLOAD_CWD" rev-parse ...` calls this branch makes are deliberately left uncapped, not wrapped in `_lib_capped`. `require-worktree-for-file-writes.sh:139-140` is the precedent: it runs the identical `--absolute-git-dir`/`--git-common-dir` comparison uncapped, on a `PreToolUse` gate that fires on every `Edit|Write|MultiEdit` outside `$HOME/.claude/` with no content gate at all — a wider-exposure hook than this one's glob-gated design, already accepting the same risk. `set-session-title-from-branch.sh:96-103` documents the same choice with the fuller rationale ("a timeout wrapper would out-scale the problem it defends against") but fires once per session at startup, not repeatedly mid-conversation the way this hook's `Edit|Write|MultiEdit` matcher does; `require-worktree-for-file-writes.sh` is the closer match on firing frequency and is the precedent this hook's own header should cite. The accepted residual — a hung or stale-mounted `$PAYLOAD_CWD` blocks the `git -C` call in an uninterruptible syscall wait, which blocks this `PostToolUse` hook's return, which blocks the Edit/Write/MultiEdit tool result reaching the model — is a known gap and must appear as its own sentence in the shipped hook's header, matching the convention both cited precedents already follow (`set-session-title-from-branch.sh:96-103`; `consume-durable-continuity-file-on-read.sh`'s equivalent header section) rather than living only in this plan.

- **An `LC_ALL=C` allowlist gate on both interpolated values, failing to silence** (`anchors: row5`, `anchors: row6`, `anchors: row7`). Before composing the command, match `$FILE_PATH` and (when present) the worktree root against `^[A-Za-z0-9._/@+-]+$` under `LC_ALL=C`; if either fails, `exit 0` with no output. This is the row that answers `row 6` head-on: the earlier decision refused to interpolate `$FILE_PATH` into model-visible context because `row 5`'s glob permits embedded newlines and arbitrary text, and `jq --arg` prevents JSON escape but not injection inside the string value. Here the path *is* the payload — there is no design in which the engineer gets a usable command without it — so the constraint has to be satisfied rather than avoided. A value restricted to `[A-Za-z0-9._/@+-]+` cannot carry a space, a newline, a control byte, a quote, a backtick, or a `$`, so it cannot form a directive sentence, cannot break the emitted command's argv, and cannot deliver a raw OSC/CSI sequence. One gate closes three exposures.

  Rejected alternatives: (a) `_lib_sanitize_for_terminal` on a display copy — strips control bytes but leaves spaces and shell metacharacters, so it fixes the escape problem and neither of the other two; (b) always double- or single-quoting the emitted paths — fixes argv but not injection, and adds a formatting branch to test; (c) omitting the path from `additionalContext` and naming only `--cwd` — the model does already hold the path, but then it must reassemble the command, which is the recall step this plan exists to delete.

  Accepted residual: a config dir or worktree path containing a space (a macOS home directory named after a person's full name, say) produces no announcement at all. Silence is strictly better than a command that silently resolves to the wrong argv, and the file's own §7/§7.5 still carries the command. Documented in the hook's known-gaps comment.

- **No `config-keys.psv` row and no kill switch** (`anchors: row11`, `anchors: row12`, `anchors: G2`). `nudge-transcript-toolkit.sh` — an informational `PostToolUse` `Edit|Write|MultiEdit` hook in the very matcher group this one joins — ships with no key at all, but is not the closest analog: `set-session-title-from-branch.sh` shares this hook's exact shape (the same `LC_ALL=C ^[A-Za-z0-9._/@+-]+$` allowlist, a git-derived value folded into an emitted string, `# hook-class: informational`) and *does* carry one (`session_title_from_branch`, `.session-title-disabled`, presence-disables). The two diverge on firing frequency and intrusiveness, not on shape: `set-session-title-from-branch.sh` fires once on every session's own startup and rewrites a persistent, always-visible UI element (the terminal tab title) for the session's entire lifetime — a standing mutation some users may want off permanently. This hook fires only on the rare, deliberate act of writing a handoff or brief file, and its output is one additional `systemMessage` line on that occasion, not a standing state change. That frequency-and-intrusiveness gap, not `nudge-transcript-toolkit.sh`'s absence of a key, is what carries the "no kill switch" call here — `session_title_from_branch`'s row is the precedent to reconcile against, since it shares this hook's shape; matcher-group similarity to `nudge-transcript-toolkit.sh` alone does not distinguish the two cases. `row 12` prices a key at four prose count-claim edits in three files plus the migration and schema-audit surface, for a capability whose worst case, given the above, is one extra line of chat on an already-infrequent, deliberate trigger.

- **Duplicate the continuity-path `case` glob rather than extracting a `_lib.sh` helper** (`anchors: row10`). `row 10` puts this at the second production copy of a two-pattern glob. Extracting it would edit `_lib.sh`, the highest-blast-radius file in the hooks tree, sourced by every hook, to save one line — CLAUDE.md's named "small duplicated value that beats a bad abstraction" exception. Copy the glob verbatim from `consume-durable-continuity-file-on-read.sh:120` so both sites inherit whatever normalization `_lib_config_dir` provides, and have each hook's comment name the other as its paired site. Revisit the extraction if a third production copy ever appears.

- **Tests as a new sibling file, with a local raw runner** (`anchors: row13`, `anchors: row17`, `anchors: row3`). `helpers.run_hook_payload` returns `hookSpecificOutput` only, and `systemMessage` is top-level, so the new test file needs a runner that returns the parsed stdout whole. `test_consume_durable_continuity_file_on_read.py:71-89` already carries a local `_run_hook_raw` for the same reason; a second local copy is CLAUDE.md's named DAMP-test-code exception to DRY, and is preferred here over promoting a shared helper into `helpers.py` (which would pull a sibling test file into this diff and add a shared-module consumer-enumeration burden for a four-line subprocess wrapper).

### Emitted contract

Compose one shell variable, then bind it once:

- `RESUME_COMMAND` = `resume-context --cwd <worktree-root> <file-path>` in the linked-worktree branch, `resume-context <file-path>` otherwise.
- `systemMessage` = `"Resume this continuity file with: " + $cmd`.
- `additionalContext` = a statement that the resume command for the continuity file just written is `$cmd`, that it was computed from the resolved config dir and this session's working directory, and that it should be used verbatim rather than reconstructed.

The hook's own header comment states the durable facts only, one per sentence: that it announces the resume command for a durable continuity file on both channels; that `--cwd` comes from the payload's `.cwd` and the linked-worktree git-dir comparison; that both interpolated paths must pass the `LC_ALL=C` allowlist or nothing is emitted; the paired-glob-site note; and the known gaps (a drifted session's missing `--cwd`; a space-containing path suppressing the announcement; reliance on `~/.local/bin` being on PATH for the bare wrapper name to resolve; and that the `git -C` calls are deliberately uncapped, so a hung or stale-mounted `$PAYLOAD_CWD` blocks this hook's `PostToolUse` return — see `require-worktree-for-file-writes.sh:139-140` for the precedent accepting the same risk on a wider-exposure hook).

## Critical files

One `code-writer` dispatch. The hook, its registration, its doc entry, and its test are one contract — splitting them would mean restating the emitted-string design in a second prompt, which `plan-it`'s own dispatch-split rule forbids.

**Create**

- `claude/.claude/hooks/announce-resume-command.sh` — `#!/bin/bash`, `# hook-class: informational` on line 2 (`row 13`, `row 14`), `set -uo pipefail`, the exact `if ! . "${0%/*}/_lib.sh" 2>/dev/null; then` source line, `CONFIG_DIR=$(_lib_config_dir) || exit 0`, then `tool_name` and `tool_input.file_path` filtering before the path glob (repo CLAUDE.md's "Hook defense-in-depth" rule: never rely on the settings.json matcher alone). Every failure path is `exit 0`.
- `claude/.claude/hooks/tests/test_announce_resume_command.py` — cases listed under Verification.

**Modify**

- `claude/.claude/settings.json` — add one `{"type": "command", "command": "~/.claude/hooks/announce-resume-command.sh"}` object to the existing `"PostToolUse"` → `"matcher": "Edit|Write|MultiEdit"` group's `hooks` array (that group is at lines 461-469 today and currently holds only `nudge-transcript-toolkit.sh`). No new matcher entry, no `if` condition.
- `docs/hooks.md` — one `- **`announce-resume-command.sh`** (PostToolUse, `Edit|Write|MultiEdit`, advisory) — …` bullet under `## Utility hooks`, matching the surrounding entries' depth. Test-enforced by `test_hook_documented_in_hooks_md`; this is the canonical description of the hook's behavior, so the hook's own header comment stays shorter and defers here for anything beyond the durable facts above.

**Reuse, do not reimplement**

- `_lib_config_dir` (`claude/.claude/hooks/_config.sh`, reached via `_lib.sh`) — capture-and-check, `|| exit 0`, never bare-interpolated; its header states that call-site contract.
- `_lib_jq` (`claude/.claude/hooks/_lib.sh`) — required; `test_no_bare_jq_outside_lib_wrapper` fails a bare `jq`. Carry the same `# shellcheck disable=SC2016` line the two sibling hooks use on the single-quoted jq program.
- The path glob at `consume-durable-continuity-file-on-read.sh:120`, copied verbatim.
- The payload-`.cwd` read at `set-session-title-from-branch.sh:93` and the allowlist at `:151-155`, including the `LC_ALL=C` pin.
- The git-dir comparison at `require-worktree-for-file-writes.sh:139-140`.
- `helpers.edit_input` / `write_input` / `multiedit_input` (`claude/.claude/tests/helpers.py:479-534`) — all three already accept a `cwd=` argument, which is what makes the `--cwd` branches testable without a bespoke payload builder.
- `helpers.symlink_hooks_lib_chain` and the `isolated_home` / `git_repo` fixtures, per the sibling hook test files.

Git calls are deliberately not `_lib_capped`-wrapped, mirroring `set-session-title-from-branch.sh:96-103`: they run only after the path glob has already matched a continuity file, so they are off every hot path.

## Verification

Inner loop, then the repo's documented gate:

1. `.venv/bin/pytest claude/.claude/hooks/tests/test_announce_resume_command.py`
2. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped suite (repo CLAUDE.md: agents run this, not the full suite). If it does not select the new test for a `claude/.claude/hooks/` diff, that is a bug in its rule table to fix there, not a licence to widen the run by hand.
3. `.venv/bin/ruff check claude/.claude/ claude-skills/`
4. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`
5. `claude-hook-review:claude-hook-review` on the new hook and its settings.json entry — repo CLAUDE.md routes hook design and review there. `/code-review` before the commit as usual; no SKILL.md, agent, rule, or plugin file is touched, so no `/skill-review`, `/agent-review`, or `plugin-semver` dispatch applies.
6. One live confirmation after stow: run `/handoff` once and confirm the announcement renders in the terminal. `row 1` already establishes the channel semantics from the vendor docs, so this confirms rendering rather than discovering it — the same class of check `set-session-title-from-branch.sh`'s header records as "Verified against claude 2.1.220".

Cases the new test file must pin:

- A `$CLAUDE_CONFIG_DIR` that is **not** `$HOME/.claude`, with a handoff path under it → `systemMessage` names that config dir. This is the root-cause regression test; a hook that resolved the wrong config dir would still pass every `$HOME/.claude` case.
- The brief arm (`briefs/*-task.md`) → same assertion, so the checker-less sibling is pinned, not merely assumed.
- Parametrized over `Edit`, `Write`, and `MultiEdit` → all three fire (`row 15`).
- A non-matching path under the config dir (e.g. `handoffs/notes.md`) and a path outside it → no stdout, exit 0.
- A matching `file_path` carried on a non-matching `tool_name` → no stdout (defense-in-depth, independent of the settings.json matcher).
- Payload `.cwd` inside a linked worktree → command contains `--cwd <worktree root>`; payload `.cwd` in that repo's main tree → no `--cwd`; `.cwd` absent, or naming a non-repo directory → no `--cwd`, command still emitted.
- A `file_path` that clears the glob but fails the allowlist (embedded space; embedded newline, per `row 5`) → no stdout at all. Assert on the *absence* of output, not on a sanitized string.
- Emitted `hookSpecificOutput.hookEventName` equals the event name the hook is registered under, read out of `settings.json` rather than hardcoded — mirroring `test_consume_durable_continuity_file_on_read.py:31-42`, since a divergence there silently drops `additionalContext`.
- Empty stdin, malformed JSON, and an unreadable `_lib.sh` → exit 0, no stdout.
- A non-matching path, asserted with a `git` PATH stub (a shim directory prepended to `PATH` that fails any invocation) → still exit 0, no stdout. Pins that the path glob gates the `git` calls, not just the emitted output, so a future reorder that moved the `git` calls ahead of the glob would fail this test rather than passing silently.

Review surface: two new files, one four-line settings.json insertion, one docs bullet. Risk concentrates in the allowlist gate and the `--cwd` branch, both of which are directly test-pinned above; the rest is shape copied from two existing hooks.

## Out of scope

- **`claude/.claude/scripts/check-handoff.py`.** Its §7 check stays as-is. It governs the file's own correctness, a separate concern from chat display, and `row 17` says that half already works.
- **A `check-brief.py`.** The hook computes independently of either skill's checker, so the brief arm is covered without one. Adding a second checker would be a new mechanism for a problem this hook already closes.
- **`handoff/SKILL.md` §7 and `brief/SKILL.md` §7.5.** Unchanged — they remain the authority for what goes *in the file*, and the hook's emitted shape is deliberately identical to what they prescribe. Leaving them alone also keeps the hook-enforced `/skill-review` gate out of this PR.
- **A `config-keys.psv` kill-switch row**, with its four downstream count-claim edits. Reasoned above; recorded here rather than as a given because the plan could add one and deliberately does not.
- **A `README.md` feature bullet.** README's "Self-consuming continuity files" bullet stays accurate without amendment, and `docs/hooks.md` is the test-enforced exhaustive surface.
- **Extracting the continuity-path glob into `_lib.sh`.** Revisit at a third production copy.
- **Continuity files written by a Bash heredoc rather than a file tool.** The matcher cannot see them. Out of reach, not overlooked.
- **Parsing the continuity file's §4 to recover the worktree path when the session's `.cwd` has drifted.** `row 16` forecloses content parsing; `G4` records the consequence.
- **`CHANGELOG.md`.** No hook or test requires an entry for this change, and none is prescribed.
