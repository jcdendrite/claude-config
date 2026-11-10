# `diff-size-ack.sh` stays in `permissions.ask`

*2026-11-07.*

The question is whether `~/.claude/scripts/diff-size-ack.sh` should move from `permissions.ask` to `permissions.allow`, or gain machinery that explains its approval prompt. It should not. The script releases the diff-size gate for a branch. An `allow` entry would let an agent that dislikes the gate run the script without the engineer's click, so the entry stays in `ask`. `.claude/plans/diff-size-prompt.md` records the same placement.

The gate is a cost-and-quality interruption, not a security control, per the header of `claude/.claude/hooks/require-diff-size-prompt.sh`. The click is a speed bump for a cooperative agent. It is not a barrier; see Accepted limits.

## Accepted limits

- The `ask` entry is written for one spelling of the command, and whether it matches any other is unobserved. The hook header's Roster bar and Known gaps cover the unmatched spellings, the plain-file latch, and the `dontAsk` and declined-prompt cases where the engineer runs the ack in a terminal.
- The forced prompt of the ack script's `permissions.ask` entry is documented, and the tree holds no observation of it in auto mode or `bypassPermissions`. `docs/hooks.md` § "Gate hooks" states this in its `require-diff-size-prompt.sh` bullet. The live check that would observe it per spelling and mode was deferred out of the PR that shipped the gate, to a follow-up with no plan yet. `.claude/plans/diff-size-gate.md` records the deferral in its row 3 and its Out of scope section.
- An agent that skips the `AskUserQuestion` step and runs the script shows the engineer a bare command prompt. An engineer without the surrounding context may approve it. The count and base that the hook's deny text has the agent state come from that same agent, so they are not evidence the question was asked.

## Declined additions

- **A `PreToolUse` hook that returns `ask` with a `permissionDecisionReason`.** The hooks page documents that reason as shown to the user in the permission prompt. No way to set text on a plain `ask` rule was found on the hooks, permissions or permission-modes pages. Not adopted:
  - In one auto-mode run (a throwaway hook, Claude Code 2.1.282, 2026-09-24/25, one machine) the reason text did not appear in the dialog. The shipped hook's reason text is untested in any configuration.
  - A hook `ask` prompted under `bypassPermissions` in one test (verified 2026-08-08 on Claude Code 2.1.223), with a throwaway hook gating an ordinary file. Nothing was tested for a Bash hook or this ack prompt. See `docs/security-hardening.md` § "WebFetch domain allowlisting — considered and rejected".
  - It adds an `ask` output to the existing gate, with its own tests and fail-direction rules. That cost buys coverage of a risk this record accepts for a cooperative-tier gate.
- **A line in the gate docs telling the engineer to approve the script only right after answering `Proceed anyway`.** Not adopted. The deny text instructs the agent, and a gate-docs line is read only by engineers who already know the rule.

The documentation claims above come from the Claude Code hooks (code.claude.com/docs/en/hooks), permissions (code.claude.com/docs/en/permissions) and permission-modes (code.claude.com/docs/en/permission-modes) pages, read on 2026-11-07 and not reproduced. Re-read them before relying on a claim here.

## Reconsideration trigger

Revisit if any holds:

1. The engineer reports approving a prompt for this script without knowing why, in a session where the agent skipped the `AskUserQuestion` step.
2. A hook `ask`'s reason text is verified to render in the auto-mode dialog with the `permissions.ask` entry also present, and a Bash hook's `ask` is verified to prompt under `bypassPermissions`.
3. The deferred live check finds the bare `~/.claude/scripts/diff-size-ack.sh` spelling does not prompt in auto mode or `bypassPermissions`.
