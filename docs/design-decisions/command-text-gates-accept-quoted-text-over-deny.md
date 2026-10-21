# Command-text gates accept over-denying quoted text

*2026-10-18.*

Gates on the Bash tool match command text. Gates that run `_lib_strip_shell_quotes` delete quote characters before matching, so a trigger word, or a `>` that starts a word, inside a quoted argument or a heredoc body can deny a read-only command. For example, `git log --grep 'a > b'` in `deny-reviewer-tree-mutation.sh` reads as a redirect to `b`.

This false deny is accepted. Gates keep reading command text, and no bash-grade tokenizer is shared across gates.

## Why the gates keep reading command text

- A hook sees the command before it runs, not what the command writes while it runs. A script a command launches can write somewhere the command text never names ([the sandbox case study](../case-studies/claude-code-sandbox-write-boundary.md) states this in its opening paragraph).
- A gate denies a write shape it recognizes, such as a redirect, `cp`, or `tee`, even when it cannot see what the write contains. Preventing a write of unseen content is worth a false deny.
- A write shape a gate does not recognize is an accepted gap, listed in that gate's header. For this gate that is the Known gaps section of `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`.
- The acceptance rests on the tokenizer argument below.

## Why not a quote-aware tokenizer

- Deleting quotes is what exposes a nested git command to the reviewer gate's any-word git scan, so `bash -c "git checkout -- x"` denies.
- A tokenizer that keeps the quoted text whole sees no `git` word. Ending the false deny would open a false allow for nested git commands.
- That claim is scoped to nested git commands. The command-word checks already allow `bash -c "sed -i s/a/b/ x"`.
- `claude/.claude/hooks/split-command-words.py` uses stdlib `shlex`, and its module docstring still lists residual over-denies and misses.
- `_lib_mask_shell_quotes` in `claude/.claude/hooks/_lib.sh` is a character-level quote scanner, not a bash tokenizer. Its header states that a gap in this class can drop a command word from a caller's fragment count, and the effect then fails open.
- Every gate sharing one tokenizer would share its gaps at once.

## Why removing Bash from review-only agents does not settle this

- It ends the exposure for this gate only.
- The eight reviewer personas' Scratch execution sections use Bash for read-only inspection and scratch runs.
- The main session and the other agents still run Bash under the other gates.

## The sandbox is worth assessing but is not a drop-in replacement

Every measurement below comes from one Linux spike pinned to `claude` 2.1.283, run against vendor docs fetched 2026-09-25 and 2026-09-26, using only cooperative-agent-shaped trials. macOS is untested. See `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Limits".

- Session-wide scope, cited: subagents use the parent session's sandbox configuration, so enabling the sandbox for reviewers enables it for every agent. See `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Q3 — a subagent's policy".
- Escape hatch open by default, measured in one trial: bare `Bash` was granted, `sandbox.allowUnsandboxedCommands` stayed at its default, and a retry the prompt instructed succeeded silently. See `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Q3 — a subagent's policy" and § "Method".
- Untested: no trial retried with `sandbox.allowUnsandboxedCommands` turned off.
- Hard link not closed, measured: a write through a hard link created before the sandboxed command ran was not blocked. See `docs/case-studies/claude-code-sandbox-write-boundary.md` § "Q1 — symlink and hard-link writes outside the allowed set".

The case study also records what the sandbox blocked and what it broke in the trials: § "Q1 — symlink and hard-link writes outside the allowed set" and § "Q2 — what breaks session-wide". It also records that the sandbox fails open when it cannot start unless `sandbox.failIfUnavailable` is set, a citation that no trial exercised.

Adopting the sandbox is its own decision with its own companions: the escape hatch off, a control for pre-created hard links, and compatibility settings. It is not a swap for these gates, and its assessment stays open.

## What a denied reviewer does

The deny reason tells the reviewer to use Read, Grep, or Glob for a read the hook misjudges (`SANCTIONED_ALTERNATIVE` in the hook).

## Reconsideration trigger

Reopen if any of these holds:

- Claude Code passes PreToolUse hooks a parsed form of the Bash command.
- The sandbox gains a per-subagent policy, or its assessment finds that a filesystem write set can carry the write invariant of this gate's Bash arm for review-only agents.
- This gate's denials show reviewers repeatedly hitting this false deny.

Counting those denials is a manual transcript read with no tooling support. `transcript-analysis.py review-trace --deny-summary` groups true and false denies under the shared `reviewer-tree-mutation` label. Both share one deny template with no field marking the cause. Any figure cited from that output follows `docs/private-project-redaction.md` § "Publishing a tooling measurement".
