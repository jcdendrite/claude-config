# Warn authoring sessions that Read consumes a continuity file

Closes https://github.com/jcdendrite/claude-config/issues/474

## Context

**Goal:** stop an agent losing tool calls to a continuity file that vanished,
by telling it *in advance* (skill body) that reading a `/handoff` or `/brief`
file relocates the file, and *at the moment of the move* (hook context) where
the file went.

`consume-durable-continuity-file-on-read.sh` fires on any `Read` whose
`file_path` matches `~/.claude/handoffs/*-handoff.md` or
`~/.claude/briefs/*-task.md` (hook line 97) and moves the file out via
`resume-context.sh --consume-only`. That is intended for the resume path, but
the glob cannot distinguish a resume from a peek — the hook's own header
comment names this as a known gap (lines 57–65).

Issue #474 reports the cost: a session wrote a handoff file, edited it twice,
then `Read` it and got "File does not exist," followed by a `stat`/`find /`
detour. Neither `handoff/SKILL.md` nor `brief/SKILL.md` warns about this — §7
documents only that `resume-context` moves the file on resume.

**Mechanics, stated precisely.** PostToolUse fires *"After a tool call
succeeds"*, so the sequence is: an earlier `Read` **succeeds**, returns the
file's contents, and the hook then moves the file. The failing `Read` the issue
reports comes later, against a path that is already empty — and because that
call fails, **no hook fires and no message is emitted on it at all**. Any
explanation therefore has to land on the earlier successful `Read`, not on the
failing one. The plan's prose must say this; getting it backwards would tell
readers to look for a message that structurally cannot be there.

**Why the model never saw it.** The hook already reports the destination — but
only through top-level `systemMessage`, which the hook reference defines as
*"Warning message shown to the user."* The model never receives it. The same
reference documents `hookSpecificOutput.additionalContext` for PostToolUse as
content placed *"next to the tool result"*, i.e. in the model's context. So the
agent's own context never named the destination on the successful read either.

**Outcome:** the authoring session is told not to `Read`-verify (prevention),
and a session that reads a continuity file without having loaded the skill —
the *peek/inspection read* the hook's known-gap list names at lines 60–61 — gets
an in-context explanation naming the new path.

## Approach

Three coordinated edits: make the hook's existing report visible to the model,
and add a prevention warning to both skills the hook globs.

### Assumption ledger

**Root problem:** an agent reading a `/handoff` or `/brief` continuity file
loses the file with no in-context explanation, and no skill body warns it not to.

| # | Assumption | Tag |
|---|---|---|
| 1 | `systemMessage` reaches the user only, not the model; PostToolUse supports `hookSpecificOutput.additionalContext`, landing "next to the tool result" | `[verified: code.claude.com/docs/en/hooks, fetched this session — "systemMessage \| none \| Warning message shown to the user"; "PostToolUse: next to the tool result"]` |
| 2 | PostToolUse fires only after a tool call **succeeds** — so the failing `Read` in #474's repro emits nothing, and the explanation must land on the earlier successful `Read` | `[verified: same source — "PostToolUse \| After a tool call succeeds"]` |
| 3 | The hook globs briefs identically to handoffs, so `brief/SKILL.md` has the same gap | `[verified: consume-durable-continuity-file-on-read.sh:96-99]` |
| 4 | `resume-context.sh --consume-only` prints the absolute destination on stdout and nothing else | `[verified: resume-context.sh:159-161]` |
| 5 | `--consume-only` moves **then** chmods, and prints `$DEST` only after both succeed — so a chmod failure leaves the file moved with empty stdout and no message on either channel | `[verified: resume-context.sh:143-158]` |
| 6 | Bash `case` globs match across `/` **and newlines**, so `$FILE_PATH` can carry arbitrary multi-line text through the glob at lines 96–99 | `[verified: executed this session — a path containing embedded newlines matched `.../handoffs/*-handoff.md`]` |
| 7 | Recovery recipe belongs in the skill bodies, not the hook, so a legitimately-resuming session is not nudged to un-consume a file the hook correctly retired | `[engineer-verified]` |
| 8 | Adding the hook's `additionalContext` is in scope even though #474 asks only for SKILL.md prose | `[engineer-verified]` |
| 9 | Skill length gate caps both files at 200 lines (neither is in the 500-line override) | `[verified: check-skill-length.sh:57-65]` — handoff 126→~140, brief 173→~187. The gate is a local PreToolUse hook on `git commit`, **not** run in CI; the `wc -l` check below is a manual step, not a regression guard |
| 10 | Repo convention forbids extracting shared skill prose into a partial; duplicating into both skills is the named exception | `[verified: repo CLAUDE.md "No shared partials across skills"]` |
| 11 | No existing test pins the current `systemMessage` wording, so rewording breaks nothing | `[verified: repo-wide grep for "to keep ~/.claude/handoffs tidy" — hook + one historical plan file only; the two message tests assert only that `$DEST` appears]` |
| 12 | Injecting the **full** `$DEST` (not just its basename) is the right call despite making a `/var/folders/<hash>/<hash>/T/…` path model-resident on macOS | `[verified: resume-context.sh:142 — `mktemp "$TMPDIR_ROOT/resume-context.XXXXXX"`, fixed prefix, slug never interpolated]` — the SKILL.md recovery recipe needs the full path to `cp` from, the hash embeds no project or org name so `deny-private-project-refs.sh`'s concern class does not apply, and the same string already reaches the user via `systemMessage`. Reviewer discipline only: do not paste it into a PR body or commit message |

### Mechanism justification

- **`hookSpecificOutput.additionalContext` in the existing jq emission**
  (`anchors: root`). Lightest primitive that closes the model-visibility gap:
  reuses the emission block at hook lines 110–114, adds no process, no state,
  no new failure mode — the same `[ -n "$DEST" ] && command -v jq` guard and
  the same `|| true` still apply.

  Lighter alternatives considered and rejected:
  1. *Docs-only, no hook change.* Fails for the **peek/inspection read** — a
     session checking whether an old handoff is still relevant never invoked
     `/handoff`, so never loaded its body. That cohort is exactly the hook's
     own known gap (lines 60–61) and gets no explanation at all.
  2. *Reword the existing `systemMessage`.* Fails on the same mechanism as the
     bug: no wording of a user-only field reaches the model (`row1`).

  Heavier alternatives **not** taken: suppressing the move for the authoring
  session needs session-intent tracking, which the hook header (lines 57–65)
  already rejects. No PreToolUse gate, no session-ID state, no change to
  `resume-context.sh`.

- **Interpolate `$DEST` only — not `$FILE_PATH`** (`anchors: root`,
  `anchors: row6`). The model already holds the source path: it issued the
  `Read`. The only new fact is the destination. Interpolating `$FILE_PATH`
  would additionally open a semantic-injection channel — `jq --arg` prevents
  JSON escape but not injection *inside* the string value, and per `row6` the
  glob permits embedded newlines, so a crafted filename under the durable
  directory could launder attacker-controlled text into a harness-framed
  context slot aimed at the artifact preamble's own re-confirm guardrail.
  Dropping it removes the vector and shrinks the diff.

- **Prose warning in `handoff/SKILL.md` and `brief/SKILL.md`**
  (`anchors: root`, `anchors: row3`). Prevention at authoring time, which
  post-hoc hook context cannot provide. Duplicated per `row10`.

- **Tests** (`anchors: row10`, `anchors: row3`). Parametrized over both
  continuity types so the brief path is asserted, not just consumed.

### Canonical homes (single-source-of-truth)

The "inspect with Bash, not Read" *instruction* lives in exactly three places,
each with a named role — do not add a fourth:

- **`docs/hooks.md`** — canonical description of hook *behavior*. Other sites
  defer here.
- **both SKILL.md bodies** (section + Pre-write checklist bullet, four sites
  across two files) — instructional prose that must stand alone per `row10`'s
  named exception. The in-file section/checklist restatement is the established
  pattern in both files, not duplication.
- **hook header known-gap 1** — implementation rationale for why the gap is not
  worked around. Stays.

`additionalContext` deliberately does **not** carry the instruction — see below.

## Drafted text (verbatim, subject to the Verification Step 1 gate)

Implement these strings exactly, with one named exception: Verification Step 1
may require rewriting the recovery sentence in the skill section. Nothing else
is discretionary.

### `additionalContext` string

```
The /handoff or /brief continuity file you just read has been consumed: it no
longer exists at the path you read, and now lives at <DEST>. Its contents are in
this tool result.
```

Facts only — no generalization, no tool recommendation. Two reasons, both
load-bearing:
- *A generalization would be false.* "Any Read of a `handoffs/`/`briefs/` path
  moves the file" does not hold on four paths the hook's own header names —
  kill-switch set, case-mismatched path on APFS, missing `jq`/`timeout`/script,
  and consume failure. A human discounts an imprecise `systemMessage`; a model
  treats injected context as invariant.
- *"use a Bash command instead" is a standing directive, not a fact.* It would
  fire on every consume, steering the model from the narrow `Read` surface to
  the wide `Bash` surface for a whole path class, and it would duplicate the
  `case` globs at lines 96–99 into prose that drifts. The instruction's homes
  are the SKILL.md bodies and `docs/hooks.md`.

### `systemMessage` string

```
Continuity file moved to <DEST>. Reload with: claude --append-system-prompt-file <DEST>
```

Drops the current hardcoded `"to keep ~/.claude/handoffs tidy"`, which names
the wrong directory whenever the consumed file was a brief.

### New section — insert immediately before `## Artifact preamble` in `handoff/SKILL.md`

```markdown
## Verify the handoff file with Bash, never Read

A `Read` of any `~/.claude/handoffs/*-handoff.md` path consumes the file — verify with a Bash
command (`cat`, `grep`, `sed -n`, `wc -l`) instead. The consume fires from this
authoring session too, mid-draft, long before any resume: the `Read` returns the
content, and the file is gone from the canonical path by the next tool call.

If it already happened, that successful `Read` reports the temp path the file
moved to. `cp` it back to `~/.claude/handoffs/<slug>-handoff.md` before any
further `Edit`, which still targets the canonical path. A later `Read` of the
now-empty canonical path reports only that the file does not exist — it does not
name where the file went.
```

The `brief/SKILL.md` copy is identical with the heading "Verify the brief file
with Bash, never Read" and `~/.claude/briefs/*-task.md` /
`~/.claude/briefs/<slug>-task.md` substituted, inserted at the same position
(immediately before its `## Artifact preamble`). The heading names the artifact,
not "this file" — inside a SKILL.md, "this file" reads as the SKILL.md itself.

"reports the temp path" is deliberately channel-agnostic: on a CLI build that
ignores PostToolUse `additionalContext` the path still arrives via the
user-visible `systemMessage`, so the sentence stays true either way. Do not
sharpen it to "the tool result names."

### New Pre-write checklist bullet

`handoff/SKILL.md`:
```markdown
- Draft verification used Bash (`cat`/`grep`/`sed -n`/`wc -l`), not `Read` — a `Read` of the handoff path consumes the file out from under any remaining `Edit` calls
```
`brief/SKILL.md`: same with "brief path".

**Shared pin fragment** for the tests below: `consumes the file — verify with a Bash`
— subject-first (so a rewrite to "does not consume" breaks the match), stops
before the tool list (so `cat`/`grep`/`sed` can change), excludes slug and
directory (so it is genuinely shared across both files).

The fragment straddles a line break in the drafted text, so the test **must
whitespace-normalize both the body and the fragment before matching**
(`" ".join(text.split())`). A raw substring assertion would fail on any reflow
of that line with no wording change — a trap, not a regression signal.

## Critical files

**`claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`** — rewrite
the emission block at lines 110–114 to keep the single `jq -n`, the single
`[ -n "$DEST" ] && command -v jq` guard, and the `2>/dev/null || true`, and emit
one object carrying both `systemMessage` and
`hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ...}`
using the drafted strings. Keep `--arg dest` only; do **not** add `--arg src`
(`row6`).

Header comment: rewrite lines 18–24 descriptively (the current "No longer fully
silent…" framing trips the CLAUDE.md no-"used to be X" rule), naming both
channels and who reads each. Add one **Known gaps** entry stating the fact plus
a single why-not-fixed clause, per the repo's one-line comment rule: a
`--consume-only` chmod failure moves the file but leaves stdout empty (`row5`),
so neither channel reports it — not closed, because closing it means reordering
`resume-context.sh`'s print.

**`claude/.claude/skills/handoff/SKILL.md`** and
**`claude/.claude/skills/brief/SKILL.md`** — insert the drafted section and
checklist bullet above. Leave the `HOOK_TEST_FIXTURE: write-target` comment and
its `mkdir` block untouched (`handoff` lines 10–13, `brief` lines 11–14);
`test_skills.py` re-reads that recipe.

**`claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py`**:
- **Parametrize** the successful-consume emission test over
  `(handoffs/x-handoff.md, briefs/x-task.md)`; assert `$DEST` appears in both
  `systemMessage` and `additionalContext`. Brief case additionally asserts
  `"handoffs"` appears in **neither** field — this is what pins the incidental
  wrong-directory fix.
- **Negative test pinning `row6`:** create a fixture whose filename embeds a
  newline followed by a sentinel string and still matches the glob (e.g.
  `handoffs/notes\n\nSENTINEL-INJECT\n\nx-handoff.md` — verified this session
  that bash `case` matches across newlines), then assert the sentinel appears
  **nowhere in stdout**. This fails if a future edit reintroduces `--arg src`,
  which asserting "source path not in additionalContext" alone would not
  reliably catch.
- Assert `hookEventName` against the value parsed from `claude/.claude/settings.json`
  rather than a hardcoded `"PostToolUse"` (CLAUDE.md discriminator-literal rule;
  `test_hook_alignment.py:147` already parses that file). A divergence between
  emitted and registered event name silently drops `additionalContext` — the
  exact bug being fixed.
- Extend `test_timeout_absent_fallback_still_consumes` (line 249) to assert the
  new field too; its docstring already explains why that arm needs its own
  emission assertion.
- Reuse `_write_fixture`, `_run_hook_raw`, `install_resume_context_script`, and
  the `RESUME_CONTEXT_TMPDIR` pattern. The existing `assert result.stdout == ""`
  assertions on the kill-switch, missing-script, jq-absent, and double-read
  paths already cover "nothing emitted when nothing moved" — verified, no change
  needed.

**`claude/.claude/skills/tests/test_skills.py`** — **one**
`@pytest.mark.parametrize("skill_name", ["handoff", "brief"])` test asserting a
**single shared module-level constant** (the pin fragment above, whitespace-
normalized on both sides) appears in both bodies, mirroring the
`_DURABLE_WRITE_TARGETS` pattern at line 1267. Two independently-worded per-file
assertions would not detect the skill-to-skill drift `row10` accepts — both
would keep passing while the texts diverge.

**`claude/.claude/tests/helpers.py`** (note: `tests/`, not `hooks/tests/`) — its
contract comment already prescribes the right escape hatch: *"Hooks that
legitimately emit a decision-less advisory payload (e.g. a PostToolUse
`systemMessage`) should use `run_hook_advisory` instead."* That guidance still
holds after this change; `run_hook_advisory` defaults correctly via
`.get(..., "allow")` at line 165. Only nit: the parenthetical example now
understates the shape — after this change the payload carries `hookSpecificOutput`
too, so `run_hook` KeyErrors on the *inner* key at line 129 rather than the outer.
Widen the example by a clause. No code change; skip entirely if it reads as
scope creep at implementation time.

**`docs/hooks.md`** — extend the hook's bullet (line 42) to name both output
channels and mark this file canonical per the Canonical homes section. Preserve
the line-start ``- **`name`**`` bullet shape; `test_hook_documented_in_hooks_md`
requires it.

## Verification

**Step 1 — recovery-recipe check, before the prose is finalized.** The drafted
section tells the session to `cp` the temp file back and continue with `Edit`.
`Edit` enforces read-freshness, and `cp` changes mtime — so `Edit` may demand a
re-`Read` of the canonical path, which re-fires this hook and consumes the file
again. In a throwaway `$HOME`: `Read` a fixture handoff, `cp` the temp file back
to the canonical path, then `Edit` it. If `Edit` completes, the drafted prose
ships as written. **If it demands a re-`Read`,** change the recipe's second
sentence to prescribe re-`Write`ing the file from the draft already in context —
`Write` needs no prior `Read` against an absent path — and re-run
`/skill-review`. Do not ship the recipe unexercised.

**Step 2 — hook output shape**, checked directly, not only through pytest:
```bash
HOME=<throwaway> printf '{"tool_name":"Read","tool_input":{"file_path":"<throwaway>/.claude/handoffs/x-handoff.md"}}' \
  | claude/.claude/hooks/consume-durable-continuity-file-on-read.sh | jq .
```
Expect one JSON object with `systemMessage` and
`hookSpecificOutput.additionalContext`, each naming the temp destination, and
exit 0. Throwaway `$HOME` with a fixture only — never the real one, since a real
consume moves an actual continuity file.

**Step 3 — suites and lint**, from the worktree (`.venv` is at the main tree
root, three levels up):
```bash
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py \
                          claude/.claude/skills/tests/test_skills.py
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
../../../.venv/bin/pytest claude/.claude/            # full suite
wc -l claude/.claude/skills/handoff/SKILL.md claude/.claude/skills/brief/SKILL.md   # manual, per row9
```

**Step 4 — end-to-end** on a stowed machine after `git pull`: write a throwaway
`~/.claude/handoffs/scratch-handoff.md`, `Read` it, confirm the **tool result**
carries the injected context naming the new path — not just the user-visible
status line. Record the `claude --version` this ran against in the PR
description: `additionalContext` on PostToolUse has no in-repo precedent (every
current use is SessionStart or UserPromptSubmit), and on a CLI build that
ignores it the change degrades silently to today's user-only `systemMessage`
with the hook still exiting 0. Stating the tested version and the degraded-path
behavior lets a future reader tell "field ignored" from "hook broken."

**Rollback:** revert-and-pull. The kill-switch
(`~/.claude/.consume-durable-continuity-disabled`) is not a partial lever — it
suppresses the whole consume, reintroducing the orphaned-file accumulation the
hook exists to prevent.

**Review pipeline:** both SKILL.md edits require `/skill-review` (hook-enforced
on commit) plus `/code-review`; the hook edit pulls in `claude-hook-review`.

## Out of scope

Three pre-existing defects found during review. **The first is serious and
should be filed immediately, independent of this plan.**

- **`~/.claude` state directories are not gitignored in this public repo.**
  `git check-ignore` confirms `claude/.claude/handoffs/`, `briefs/`, `projects/`
  (full session transcripts), `sessions/`, `telemetry/`, and `history.jsonl` are
  **not ignored**, while `claude/.claude/plans/` and `*-markers/` are — the
  `.gitignore` already reasons through this exact failure mode twice and stops
  short of these siblings. Under stow these land physically in the repo tree, so
  one `git add -A` stages private continuity content and full transcripts into a
  public repo; `deny-private-project-refs.sh` catches names and tracker IDs, not
  bulk transcript content. All six currently show as untracked in this checkout.
  Separate one-line fix per the existing precedent — **file now, do not bundle.**
- **`resume-context.sh:63-67` asserts a guarantee that does not exist.** It says
  "the handoff/brief SKILL.md write recipes chmod those directories 700"; both
  recipes are `mkdir -p` only. The protection does hold in practice — `install.sh:75`
  chmods `~/.claude` itself to 700 — so this is a wrong-mechanism comment, not a
  missing control. Correct the comment in a separate change; the recipe blocks
  are `HOOK_TEST_FIXTURE`s whose test would need updating alongside.
- **`nudge-handoff-near-context-cap.sh:139` tells the model `/handoff` "captures
  state in a `/tmp` file."** Stale since the durable-location change, and it is
  model-facing `additionalContext` — the same wrong-location defect class as the
  `systemMessage` string this plan fixes, in a different hook. `docs/handoff-nudge.md:5`
  repeats it. Separate issue (Axis 1 bucket 3).

Also out of scope: changing *whether* the hook consumes on a non-resume read
(the header's rejection of intent-tracking stands); any change to
`resume-context.sh` or its `--consume-only` contract, including reordering its
print past the chmod (`row5`); the hook's other known gaps (case-sensitive glob,
symlink-traversal, orphaned-`mv` accumulation); putting the recovery recipe in
the hook (`row7`); extracting the duplicated warning into a shared partial
(`row10`).
