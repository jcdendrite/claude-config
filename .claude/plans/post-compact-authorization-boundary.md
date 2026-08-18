# Restore the irreversible-action boundary after compaction

## Context

**Goal:** after Claude Code compacts a session, re-state the boundary that
irreversible actions need in-session engineer confirmation — because compaction
discards that boundary while simultaneously handing the agent a section titled
"Optional Next Step."

Compaction replaces conversation history with a harness-generated summary. That
summary follows a fixed nine-section template, empirically identical across
every observed event, whose final section is "9. Optional Next Step." Nothing in
the template distinguishes a reversible next step from one that merges a PR,
force-pushes, runs a migration, creates a release, or deletes in bulk. The
`handoff` skill maintains exactly that distinction in its §3.5 and in its
artifact preamble — but only inside a handoff file, on a path a human chooses.
Compaction is the path nobody chooses: it fires automatically, in every stow
consumer's sessions, with no opt-in.

Of the seven action shapes `handoff/SKILL.md` §3.5 names, only one is
mechanically gated. Four have no mechanical control whatsoever, and a fifth is
gated only under a precondition that commonly does not hold, so their only
protection is context-resident prose — precisely what compaction summarizes
away.

The intended outcome: one hook, firing only on compaction, that re-states the
principle and illustrates it with the shapes no gate reliably covers.

## Approach

A new `SessionStart` hook registered with matcher `compact` emits a fixed block
of `hookSpecificOutput.additionalContext` re-stating the irreversible-action
boundary. The text is a constant: the hook makes no runtime decision about what
to inject, reads no file whose path varies, and takes no untrusted input beyond
the event's own `source` field.

That constancy is the design's main virtue, not an incidental detail. A hook
that reads a file to decide what to inject needs path confinement, symlink
rejection, and a story for what happens when the read half-succeeds. A hook that
emits a constant needs none of them. It does still spawn one subprocess — `jq`,
to encode the payload — so it routes that through `_lib_jq`
(`claude/.claude/hooks/_lib.sh:17`), which wraps `jq` in `_lib_capped_for 5` and
supplies a timeout backstop without the hook re-deriving one.

### Assumption ledger

**Root problem:** compaction discards the context-resident authorization
boundary while asserting a next step, and five of the seven irreversible-action
shapes have no reliable mechanical gate to fall back on.

**Givens** (fixed beyond this design's reach):

- **Compaction cannot be prevented, deferred, or have its summary replaced.**
  `PreCompact`'s decision control is `permissionDecision` `allow`/`deny`/`ask`
  only; `PostCompact` "has no decision control" and its `systemMessage` and
  `continue` fields are discarded
  (https://code.claude.com/docs/en/hooks, "PostCompact" section). Vendor-imposed: the
  summary's content is produced by the harness, and no hook contract exposes a
  substitution point.
- **The summary template is fixed and harness-authored.** Its nine sections,
  ending in "Optional Next Step," are generated server-side. Vendor-imposed for
  the same reason.

**Mechanisms:**

- **New `SessionStart` hook, matcher `compact`** — `anchors: root`. The only
  event whose output the platform injects into the post-compaction context.
  Three lighter primitives were considered and rejected:
  1. *A rule in `claude/.claude/CLAUDE.md`'s Safety section.* Cheapest by far —
     no script, no test, no registration — and the compaction table does list
     "Project-root CLAUDE.md and unscoped rules | Re-injected from disk"
     (https://code.claude.com/docs/en/context-window, "what survives compaction" table).
     Rejected because that row does not demonstrably cover the **user-level**
     file this repo stows to `~/.claude/CLAUDE.md`: the memory docs' scope table
     (https://code.claude.com/docs/en/memory) names `~/.claude/CLAUDE.md` "User
     instructions" and `./CLAUDE.md` "Project instructions" as distinct scopes,
     and both the compaction table and memory.md's troubleshooting section say
     only "Project-root CLAUDE.md survives compaction." A safety control may not
     rest on an undocumented reading (see row 4).
  2. *A new unscoped rule file under `claude/.claude/rules/`.* "Unscoped rules"
     is named directly in the re-injected row, which makes this the strongest
     documented candidate. Rejected for the same evidentiary gap one level down —
     https://code.claude.com/docs/en/memory defines "unscoped" as lacking
     `paths:` frontmatter but never states that user-level rules under
     `~/.claude/rules/` fall in that row — compounded by it being
     precedent-setting: all six rule files in this repo today carry `paths:`
     (verified: `.claude/rules/` ×2, `claude/.claude/rules/` ×4), so this would
     introduce the first always-loaded rule for every stow consumer in every repo.
  3. *Extending `session-marker-dashboard.sh`*, which already fires on
     `startup|clear|compact`. Rejected: that hook is deliberately quiet when no
     marker is active (`claude/.claude/hooks/session-marker-dashboard.sh:68-72`),
     and this text must fire on every compaction regardless of marker state.
     Fusing them would either destroy its silence or make the boundary
     conditional on unrelated state.
- **Static text rather than a file read** — `anchors: root`. The boundary is a
  constant; nothing about it varies per session.
- **An illustrative subset rather than a copy of §3.5's seven** —
  `anchors: row2`. The hook names shapes with no reliable mechanical gate, framed
  explicitly as examples under a general principle rather than as an exhaustive
  list. This is deliberately not a duplicate of `handoff/SKILL.md` §3.5: that
  list changes when the *skill's* policy changes, while this one changes when
  *hooks* change. Different maintenance triggers, so they are two lists that
  overlap, not one list stored twice — with a subset test (Verification) to catch
  drift in the direction that matters.

**Assumptions.** Vendor-doc citations below are Anthropic's hosted
documentation at `code.claude.com`, not files in this repo; each carries its
full URL so a reviewer can resolve it without guessing.

| # | Assumption | Tag |
|---|---|---|
| 1 | `SessionStart` fires with `source: "compact"` after compaction and its `additionalContext` is injected into the post-compaction context | `[verified: https://code.claude.com/docs/en/hooks — SessionStart matcher table row "compact \| Auto or manual compaction"; corroborated in-repo by claude/.claude/settings.json registering session-marker-dashboard.sh on matcher startup\|clear\|compact and README.md:69 documenting it restoring marker state after auto-compact]` |
| 2 | The compact summary's final section is "Optional Next Step" and no section carries an authorization boundary | `[verified: 12/12 compaction events in this machine's transcript corpus follow an identical nine-section template ending in "9. Optional Next Step"; corroborated by https://code.claude.com/docs/en/context-window — the summary keeps intent, concepts, files, errors, pending tasks, and current work, none of which is an authorization boundary]` |
| 3 | Of the 7 §3.5 shapes, 1 is unconditionally gated, 2 are gated only under preconditions that commonly fail, and 4 have no control at all | `[verified: block-gh-pr-merge.sh gates every gh pr merge shape unconditionally and fail-closed; require-ready-for-review.sh gates git push generically without force-flag detection and is documented at line 41 as not checking a branch with no open PR; require-respond-pr.sh gates GitHub comments under an explicitly cooperative threat model; no hook in claude/.claude/hooks/ or plugins/*/hooks/ covers gh pr close, git branch -d, migration execution, gh release create, or rm -rf]` |
| 4 | User-level `~/.claude/CLAUDE.md` is **not** demonstrably re-injected after compaction | `[verified: no user-level CLAUDE.md content found after the boundary in 0/12 corpus events; https://code.claude.com/docs/en/memory distinguishes "User instructions" from "Project instructions" and the compaction row at https://code.claude.com/docs/en/context-window, "what survives compaction" table names only project-root]` — the corpus cannot prove absence, since the transcript format does not expose system-prompt-level content. Treated as unconfirmed, which for a safety control is equivalent to unavailable. |
| 5 | Hooks themselves survive compaction | `[verified: https://code.claude.com/docs/en/context-window, "what survives compaction" table — compaction table row "Hooks \| Not applicable; hooks run as code, not context"]` |
| 6 | Injecting on `compact` only, not `clear`, is correct | `[verified by reasoning from row 2]` — after `/clear` the context is empty and no summary asserts a next step, so there is no false authorization to correct. The risk is specific to a summary that names one. |
| 7 | The hook text will be heeded | `[unverified]` — this is advisory context, not a gate, exactly like every other `hook-class: informational` hook here. It lowers the chance the agent reads "Optional Next Step" as sanction; it cannot prevent it. Stated plainly so no reader mistakes this for enforcement. |

### What this does not claim

This is context restoration, not a gate. The ungated shapes remain ungated; the
hook only ensures the agent is told they need confirmation. Building real
`PreToolUse` gates for them would be strictly stronger and is scoped out below —
with a named follow-up plan path, so the cheap layer does not quietly become the
terminal answer.

**On repetition.** Every other informational hook here is conditional —
`session-marker-dashboard.sh` is silent with no active marker,
`nudge-handoff-near-context-cap.sh` is one-shot with a re-arm. This one fires on
every compaction with identical text, which is normally the shape that trains an
agent to skim past it. It is correct here anyway: each compaction *replaces* the
context, so the agent never sees this text twice within one context window. The
repetition is across contexts, not within one, which is the condition
habituation needs.

## Critical files

**Create**

- `claude/.claude/hooks/restore-authorization-boundary-on-compact.sh` —
  `#!/bin/bash`, `# hook-class: informational` on line 2, then
  `set -uo pipefail`. The plan's mirror for output construction
  (`session-marker-dashboard.sh`) omits `set -uo pipefail` and is the outlier;
  three of the five comparable informational `SessionStart` hooks carry it and
  `claude/.claude/rules/shell-script-conventions.md` mandates it, so follow the
  rule rather than the mirror.

  **Header must state**, one sentence per fact per the repo's comment-length
  convention: that this is advisory and gates nothing; that the shapes it names
  are illustrative rather than exhaustive; and that `clear` is deliberately
  excluded because an emptied context asserts no next step. A future reader of
  the `.sh` alone must get these without opening a plan file.

  **Reuse:** source `_lib.sh` **before** reading or parsing stdin — both the
  `.source` extraction and the output-emission call must route through
  `_lib_jq`, not bare `jq`. Neither cited precedent does this cleanly:
  `session-marker-dashboard.sh:76-78` emits via bare `jq` (no `_lib_jq`
  anywhere in that file, so its output call has no timeout backstop), and
  `set-session-title-from-branch.sh:73` parses `.source` with bare `jq`
  *before* `_lib.sh` is sourced (`_lib_jq` doesn't exist yet at that point).
  This hook deliberately improves on both: sourcing `_lib.sh` first and using
  `_lib_jq` for every jq call it makes is a correction, not a match — a hung
  or PATH-hijacked `jq` on the `.source` check would otherwise block the hook
  indefinitely on every single compaction, which directly undercuts the
  fail-open design this hook exists to provide. Call `_lib_config_dir` for
  kill-switch path resolution (pure env/string logic, `_lib.sh:106-120`, no
  I/O) after sourcing; emit via `_lib_jq -n --arg ctx ... || true` followed by
  an unconditional `exit 0`. Self-filter on `.source == "compact"` in the
  script, not the settings matcher alone, per the repo's defense-in-depth
  rule; document the matcher/internal-filter pairing in the header per
  `claude-hook-review` §5 (`deny-private-project-refs.sh:9-14` is the model),
  since both surfaces cover the identical `compact` case and must stay in
  sync. Kill switch: `~/.claude/.authorization-boundary-disabled`, matching
  the `.handoff-nudge-disabled` / `.consume-durable-continuity-disabled`
  convention.

  **Injected text, in this order:** (1) that the summary is harness-generated and
  its next-step section is a reconstruction, not engineer authorization; (2) the
  general principle — an action that mutates shared state in a way no other
  command undoes, or has effects observable outside this repository, needs
  in-session confirmation even when the summary names it as the next step; (3)
  examples, explicitly labelled non-exhaustive: closing a PR or deleting an
  unmerged branch, database migrations, `gh release create`, `git push --force`
  on a branch with no open PR, `rm -rf` and bulk deletes, and Slack/email/GitHub
  comments on the engineer's behalf. Principle before examples is load-bearing —
  an agent that pattern-matches the list instead of the principle would treat
  `terraform apply` or `npm publish` as implicitly safe. Force-push-before-PR is
  named despite `require-ready-for-review.sh` nominally covering `git push`,
  because row 3 establishes that gate does not fire without an open PR. The text
  does not enumerate what *is* gated; naming safe paths invites reliance.

- `claude/.claude/hooks/tests/test_restore_authorization_boundary_on_compact.py`
  — see Verification.

**Modify**

- `claude/.claude/settings.json` — add a `SessionStart` entry with
  `"matcher": "compact"` and `"command": "~/.claude/hooks/restore-authorization-boundary-on-compact.sh"`,
  matching the `~/.claude/hooks/...` form every existing entry uses. This is a
  `hooks` entry, not a `permissions.allow` rule, so `/review-permissions` does
  not apply.
- `docs/hooks.md` — one bullet under `## Utility hooks` (line 30), where
  `session-marker-dashboard.sh`'s entry already lives. Include the known-gaps
  list per that section's format: advisory only, names an illustrative subset,
  does not fire on `clear`.
- `README.md` — three edits. Add a "Notable patterns" bullet (README.md:64-77),
  not a hooks-table row (README.md:152+): `session-marker-dashboard.sh` — the
  closest analogous hook, also compaction-focused, also emitting
  `additionalContext` — is itself deliberately excluded from that table and
  documented only via a Notable-patterns bullet plus the Context management
  section, unlike the purely-advisory `check-branch-divergence.sh` /
  `set-session-title-from-branch.sh` rows that do live in the table; this hook
  follows the former precedent, not the latter. Add a third numbered item to
  "Context management → How it works", naming the kill switch: that list is
  where `session-marker-dashboard.sh`'s auto-injection is documented and where
  a user surprised by new post-compaction text will look. Add a
  `/compact [instructions]` row to "When to use which", scoped explicitly to
  **pre-threshold, same-session** continuation — unscoped, it reads as
  competing with the handoff-nudge rationale documented two sections below,
  which deliberately moves a user past the threshold into a fresh session.
- `CHANGELOG.md` — one entry under the next unreleased heading: "Added:
  `restore-authorization-boundary-on-compact.sh`, a `SessionStart` hook that
  re-states the irreversible-action confirmation boundary after compaction."

**Not a test-coupling change.** `test_hook_alignment.py` discovers hooks via
`sorted(_MAIN_HOOKS_DIR.glob("*.sh"))` (line 47) and parametrizes off that, so
the new hook is picked up with no edit provided it carries a `hook-class` header
and a `docs/hooks.md` bullet — both already required above. `test_doc_counts.py`
registers no hook-count fact. Neither file needs modification; both must simply
still pass.

## Verification

From this worktree: `../../../.venv/bin/pytest claude/.claude/`,
`../../../.venv/bin/ruff check claude/.claude/`, and
`scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.

**Hook unit tests.** Follow `test_session_marker_dashboard.py`'s
`_run_dashboard`/`_additional_context` local-helper pair — it tests the same
`SessionStart` event and the same `additionalContext` output shape, making it a
closer precedent than `test_consume_durable_continuity_file_on_read.py` (right
runner pattern, wrong event). Do **not** use `helpers.py`'s
`run_hook_session_start`: it asserts an exact `{"hookEventName", "sessionTitle"}`
key set (`helpers.py:353-356`) and will fail on an `additionalContext`-only
payload.

- `source: "compact"` → valid JSON; the principle clause is present; each named
  shape is present. Anchor each assertion on the literal command or verb token
  (`gh pr close`, `rm -rf`, `git push --force`) rather than a full descriptive
  clause, so a prose reword does not false-fail.
- **Negative assertion:** the emitted text does *not* name `gh pr merge` or any
  other reliably-gated shape. This tests the plan's own stated invariant, which
  is otherwise unenforced — a future edit pasting §3.5's full list in would
  violate it silently.
- **Subset drift test:** every command token the hook names appears somewhere in
  `handoff/SKILL.md` §3.5's categorization list. Subset, deliberately not
  equality: if §3.5 later gains a shape that *is* gated, the hook correctly
  should not list it, so equality would assert the wrong thing.
- `source` is `startup` / `clear` / `resume` / `fork` → no output, exit 0.
  Covers the in-script filter independently of the settings matcher.
- `source` key absent entirely, and `source` present but non-string (number,
  bool, object) → no output, exit 0. Both are distinct code paths from malformed
  stdin, which covers non-JSON rather than valid-JSON-wrong-type.
- Kill-switch sentinel present → no output even on `compact`.
- Malformed stdin, empty stdin, missing `jq`, unresolvable config dir → exit 0,
  no output. This is fail-*open*, deliberately: a `SessionStart` hook has no
  deny path to fail closed into, so erroring hard here would only block
  session startup — strictly worse than silently skipping the advisory text.

**Manual end-to-end**, since no unit test can confirm the harness actually
injects. In a scratch session, force a compaction with `/compact`, then **ask the
agent to quote the boundary text verbatim**. Do not grep the transcript JSONL:
row 4 establishes that format does not expose system-prompt-level content, so a
grep returns a false negative. Confirm `session-marker-dashboard.sh`'s output
also arrives — two hooks emitting `additionalContext` on one event is already
precedented by `check-branch-divergence.sh` + `session-marker-dashboard.sh` on
`startup`, so this confirms rather than explores.

## Out of scope

- **Real `PreToolUse` gates for the ungated shapes.** Within reach and
  deliberately declined here: strictly stronger than advisory text, but one hook
  per shape, each needing its own false-positive analysis. Reserved as
  `.claude/plans/gate-ungated-irreversible-actions.md`. This repo has no separate
  issue tracker, so this named path in a committed plan is the tracking record —
  its purpose is to keep the advisory layer legible as a stopgap rather than the
  terminal answer, and a reviewer who disagrees that the gates are worth building
  should say so here rather than let the question lapse.
- **Anything on the `/clear` path**, including auto-injecting a handoff after a
  clear. Investigated and rejected: `/clear` appears twice in 634 corpus
  sessions, and `/compact` preserves task-list state
  (https://code.claude.com/docs/en/interactive-mode — "Tasks persist across
  context compactions"), `cwd`, `gitBranch`, and session identity that `/clear`
  discards.
- **Auto-writing handoffs at a context threshold.** Rejected: the nudge re-arms
  every 80 000 tokens, so a non-idempotent write behind an idempotent trigger
  orphans files, and `handoff/SKILL.md:17` already argues the writes would
  mostly be waste.
- **Path-scoped rule loss after compaction** ("Rules with `paths:` frontmatter |
  Lost until a matching file is read again",
  https://code.claude.com/docs/en/context-window, "what survives compaction" table). Real
  but not worth engineering around: the documented remedy — dropping `paths:` —
  would load every rule in every repo, and the failure is backstopped by the rule
  reloading on the next matching file read, by `/code-review`, and by CI
  shellcheck.
- **Trimming `handoff/SKILL.md` §2.6.** The task-list serialization is genuine
  overhead when a handoff is written near a compaction, since task state survives
  compaction natively — but it stays load-bearing for the `resume-context` path,
  where a new process does lose it. Revisiting it means reasoning about both
  paths at once.

## Raise to the reviewer

`resume-context.sh:76-79` attributes the durable directories' `chmod 700` to
"the handoff/brief SKILL.md write recipes." Those recipes do no such thing —
`handoff/SKILL.md:11-13` and `brief/SKILL.md` are bare `mkdir -p`. The
protection is real but comes from elsewhere: `install.sh:299` chmods `~/.claude`
itself, described at `install.sh:277` as "a single choke point." So this is a
comment crediting the wrong mechanism, not a missing safeguard — lower severity
than a bare reading suggests, but it will mislead anyone who edits either file
expecting the recipe to be load-bearing. Pre-existing and outside this change's
file boundary, so not fixed here.
