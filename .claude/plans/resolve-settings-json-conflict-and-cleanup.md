# Resolve settings.json merge conflict and clean up personal config

## Context

An interrupted `git stash pop` in the main tree of the claude-config repo left `.claude/settings.json` (repo-root, this project's own contributor settings file) with literal git conflict markers — invalid JSON that broke the terminal status line. The same stash pop separately left `claude/.claude/settings.json` (the stow-source file that `./install.sh` symlinks into every contributor's `~/.claude/settings.json`, and into every local Claude Code account's config dir on this machine) with an uncommitted, staged diff mixing in personal/account-scoped config that shouldn't ship as-is in this public, shared file: a one-account-only plugin toggle, a personal absolute-path marketplace registration, and personal UI prefs (`tui`, `theme`, `agentPushNotifEnabled`).

Why now: the broken JSON is actively breaking the status line, and the stray staged diff is sitting uncommitted in the main tree, blocking a clean working state.

Intended outcome: (1) the repo-root `.claude/settings.json` is repaired back to its last-known-good (HEAD) state and the now-exhausted stash entry is dropped; (2) the shared `claude/.claude/settings.json` ends up, via a normal branch + PR, with only `tui`/`theme`/`agentPushNotifEnabled` added on top of its current HEAD content — the account-only plugin entries and the personal-marketplace-path entry are dropped entirely, per the engineer's explicit decision; (3) `docs/design-decisions.md` gains a new numbered entry recording that decision and a settings-scope discrepancy discovered against the official docs.

## Approach

Two independent threads. Thread A repairs main-tree git state by hand (no branch diff, no Claude tool call possible). Thread B lands a three-key addition to the stow-source settings file plus a `docs/design-decisions.md` entry through a normal PR — with the landing commit run by the engineer, because this repo's own `guard-settings-session-keys.sh` guards `theme` and `tui` and denies an agent-run `git commit` that changes them.

### Prerequisite — engineer-run main-tree repair (Thread A)

Not on this branch, not in Critical files: it nets to zero diff from HEAD and is git-state repair, not a change. `require-worktree-for-git-writes.sh` denies every git write whose effective target is the main tree with no carve-out, so no Claude Code session — in this worktree or any other — can run it. The engineer runs these in a terminal, in the main working tree (referred to below as `<repo-root>`):

```bash
cd <repo-root>
git status --short                  # expect exactly: UU .claude/settings.json / M  claude/.claude/settings.json
git stash list                      # the interrupted pop retains its entry; confirm it is stash@{0}

# Safety check before anything destructive: capture the stash in full, then read it.
git stash show -p --include-untracked stash@{0} > /tmp/claude-config-stash-backup.patch
less /tmp/claude-config-stash-backup.patch

# Keep the stray settings content — the dropped plugin/marketplace entries may be wanted
# for the separate per-account work.
cp claude/.claude/settings.json /tmp/claude-config-settings-stray.json

git checkout HEAD -- .claude/settings.json                    # clears the unmerged stages and the markers
git restore --staged --worktree -- claude/.claude/settings.json

git diff HEAD -- .claude/settings.json claude/.claude/settings.json   # expect empty
jq empty .claude/settings.json && jq empty claude/.claude/settings.json
git status --short                                            # expect both paths gone

git stash drop stash@{0}            # only after the patch above showed nothing unaccounted for
```

Notes:

- `git stash show --include-untracked` requires Git 2.32 or newer. If it errors, use `git stash show -p stash@{0}` (tracked changes only) and inspect `git show stash@{0}^3` separately for an untracked commit, if one exists.
- **Do not use `git reset --hard`.** It would reach every other uncommitted file in the main tree, not just these two. `git checkout HEAD -- <path>` and `git restore -- <path>` are path-scoped.
- A stash-pop conflict leaves no sequencer state — there is nothing to `--continue` or `--abort`. Resolving the paths and dropping the entry is the whole recovery, and it matches git's own on-conflict guidance ("The stash entry is kept in case you need it again").

The stray `claude/.claude/settings.json` discard must complete before the main tree pulls Thread B's merge, or the pull hits a dirty-tree refusal on that exact path.

### Thread B — the branched change

Edit `claude/.claude/settings.json` additively: this worktree's copy is pristine `origin/main` content, so this adds three keys and removes nothing. Add the `docs/design-decisions.md` entry. Fix the one stale line in `docs/hooks.md` that the new entry points at.

**Why the landing commit is engineer-run rather than amending the guard.** `guard-settings-session-keys.sh` lists `theme` and `tui` in `GUARDED_KEYS_JSON` (lines 88–97) and has two dedicated tests asserting each denies (`test_theme_change_denies_commit`, `test_tui_change_denies_commit`). It compares the staged file against `main` per key, so it denies every agent-run commit staging these keys on this branch until the values are in `main`. Two heavier alternatives were weighed and set aside:

- *Remove `theme`/`tui` from `GUARDED_KEYS_JSON`, flip the two tests, update the doc.* This permanently loses the catch on the exact failure the keys were added for. Claude Code writes both keys itself (`/config`, `/tui fullscreen`). This machine's `~/.claude/settings.json` is a stow symlink into the tracked file, so those writes land as staged public-repo diffs. Paying a permanent protection loss to unblock one commit is the wrong trade.
- *Add a deliberate-change escape valve (a marker, an env flag).* A defensive layer whose only job is to reopen a gate for one commit — the compounding-layers shape `docs/design-decisions.md` §41 and §42 both already declined.

The guard is a **drift** gate, not a presence ban: once `theme`/`tui` hold committed values, a consumer's later deviation still denies. `claude/.claude/settings.json:69` already ships `"model": "sonnet"` under exactly this arrangement, with `model` first in the guarded list. So the guard keeps working unchanged; only the one commit that introduces the values needs a human, which is the gate's intended override channel (it gates agent tool calls; no git-level `pre-commit` hook exists in this repo).

Operationally: `code-writer` writes all three files, `/code-review` runs, then **the engineer runs `git commit` themselves in the worktree**. `git push`, `/ready-for-review`, and `gh pr create` are unaffected and stay with the session. Any post-review fix touching `claude/.claude/settings.json` needs the same human commit or amend, for as long as `main` lacks the two keys. **Confirmed with the engineer:** this manual-commit consequence is accepted for this change.

**Redaction, non-negotiable in both this plan file and the new entry.** This plan and `docs/design-decisions.md` both ship in this public PR. The dropped plugin toggle's account and plugin name, and the absolute checkout path in the dropped `extraKnownMarketplaces` entry, appear only as placeholders ("one Claude Code account", "an absolute checkout path specific to one machine") — never written out.

### Assumption ledger

**Root:** the stow-source `settings.json` is simultaneously one engineer's user-scope config and every consumer's shipped default, and Claude Code's documented scope model offers no user-scope local file to separate the two — so three preference keys have nowhere to live except the tracked file, and the guard protecting that file from accidental personal state cannot distinguish this deliberate write from an accidental one.

**Givens** (fixed, outside this plan's reach):

- **G1 — Claude Code documents no user-scope *local* settings file.** Vendor-imposed: the precedence model has five levels and `settings.local.json` is defined as project-scoped, so `~/.claude/settings.local.json` corresponds to no scope. This is the only true given here — the stow symlink architecture and the worktree-write hook's lack of a carve-out are both this engineer's own repos to edit, so per plan-review's unjustified-given tripwire they're recorded in **Out of scope** below instead (a condition in reach the plan deliberately declines to change), not listed as givens.

**Rows:**

1. Repo-root `.claude/settings.json` at HEAD is valid, complete, and already contains the stash's only unique content for that file; restoring HEAD is a full fix. `[verified: git show HEAD:.claude/settings.json plus direct comparison, dispatching session]` — anchors: root
2. This worktree's `claude/.claude/settings.json` is clean HEAD content: no `tui`, `theme`, `agentPushNotifEnabled`, account-plugin, or marketplace-path entries. `[verified: read this session — file ends at enabledPlugins/extraKnownMarketplaces exactly as HEAD]` — anchors: root
3. `guard-settings-session-keys.sh` guards `theme` and `tui` and denies an agent-run `git commit` staging either. `[verified: claude/.claude/hooks/guard-settings-session-keys.sh:88-97,143; claude/.claude/hooks/tests/test_guard_settings_session_keys.py:138-170]` — anchors: root
4. That guard compares staged content against `main` per key, so a committed value stays protected against later drift. `[verified: same hook, lines 78-83 and 112-131]` — anchors: row3
5. `"model": "sonnet"` already ships as a guarded key's committed value in this same file. `[verified: claude/.claude/settings.json:69 against GUARDED_KEYS_JSON:89]` — anchors: row4
6. No git-level `pre-commit` hook exists, so an engineer's terminal commit passes through no gate. `[verified: no .pre-commit-config.yaml in the repo; every gate in claude/.claude/settings.json is a PreToolUse Bash hook]` — anchors: row3
7. `agentPushNotifEnabled` is not guarded and needs no human commit on its own. `[verified: GUARDED_KEYS_JSON, hook lines 88-97]` — anchors: row3
8. `docs/hooks.md:39` names only `model`, `effortLevel`, `skipAutoPermissionPrompt`, and the two `env.*` keys — it omits `theme`, `tui`, and `skipWorkflowUsageWarning`, which the hook guards. `[verified: docs/hooks.md:39 against hook lines 88-97]` — anchors: row3
9. Main-tree git writes are denied for Claude, including read-shaped `git stash list`. `[verified: live denial this session, dispatching session; claude/.claude/hooks/require-worktree-for-git-writes.sh]` — anchors: root
10. `tui`, `theme`, and `agentPushNotifEnabled` are not honored in `~/.claude/settings.local.json`. `[engineer-verified]` — corroborated by G2: that path matches no documented scope, so the negative result is what the precedence model predicts. — anchors: root
11. `enabledPlugins` has no per-account expression under this layout — every account's `settings.json` resolves through the same symlink. `[engineer-verified]`, plus `[verified: the account config dir's settings.json is a symlink into this repo, dispatching session]` — anchors: root
12. The stash entry may hold paths beyond the two settings files. `[unverified]` — `git status` showing only two paths is suggestive but not conclusive: a stashed file whose content already matched the working tree applies with no status change. The mandatory `git stash show -p` + backup-patch step exists for this row and nothing else. — anchors: root
13. `select-tests.py` maps both edited paths to test domains, so a scoped run is a real check rather than a no-op. `[verified: claude/.claude/scripts/select-tests.py:77 (CLAUDE_SETTINGS_JSON), :88-94 (docs/ blanket)]` — anchors: root
14. Appending entry 43 moves no doc-count fact. `[verified: test_doc_counts.py's only design-decisions occurrences are the reviewer-persona counts at :346-359; there is no entry-count fact]` — anchors: root
15. `ask-review-permissions.sh` will raise an `ask` on the `settings.json` edit; approving is correct because the edit touches no `permissions` key. `[verified: claude/.claude/hooks/ask-review-permissions.sh:23-27]` — anchors: root
16. The account name, plugin name, and machine-absolute checkout path are redaction-tier content in this public repo. `[verified: CLAUDE.md "Redact private-project-identifying content", tiers 2 and 3]` — anchors: root

**Mechanisms:**

- `git checkout HEAD -- <path>` over hand-editing the conflict markers out — the target content is exactly HEAD, and this clears the unmerged index stages in the same call. — anchors: row1
- Every Thread A step handed to the engineer as terminal commands, none attempted by a Claude tool. — anchors: row9, G3
- Additive edit in the clean worktree rather than porting the main tree's contaminated copy — nothing needs removing here, so a copy-and-clean step would only import risk. — anchors: row2
- Engineer-run landing commit instead of amending the guard or adding an escape valve — the two heavier alternatives are enumerated with their costs in the Approach prose above. — anchors: row3, row4, row5, row6
- One-line `docs/hooks.md` correction, in scope because entry 43 cites that line as the canonical guarded-key list rather than restating it. — anchors: row8
- Backup patch and file copy before `git stash drop` — the drop is the only irreversible step in either thread. — anchors: row12

## Critical files

One `code-writer` dispatch covers all three files: they are one coherent change and splitting would force the same shared background into every prompt.

- **`claude/.claude/settings.json`** — insert three keys immediately after `"model": "sonnet",` (line 69), keeping the scalar keys grouped and leaving every block below untouched:
  ```json
    "model": "sonnet",
    "theme": "dark",
    "tui": "fullscreen",
    "agentPushNotifEnabled": true,
    "disableArtifact": true,
  ```
  Nothing else in the file changes. `enabledPlugins` and `extraKnownMarketplaces` stay exactly as they are.

- **`docs/hooks.md:39`** — extend the guarded-key list in the `guard-settings-session-keys.sh` bullet to match the hook: add `skipWorkflowUsageWarning`, `theme`, and `tui` alongside the keys already named. One line, no restructuring.

- **`docs/design-decisions.md`** — append after the current EOF (end of entry 42). Reuse the file's existing grammar: `## N. Title (YYYY-MM-DD)` heading, terse prose, inline links, a `### Sources` list. Draft:

  ```markdown
  ## 43. UI and notification preference keys ship as shared defaults in the stow-source settings file (2026-09-02)

  Stow installs `claude/.claude/settings.json` as every consumer's user-scope
  `~/.claude/settings.json`. Claude Code's settings precedence has five
  levels — managed policy, command line, project-local
  (`.claude/settings.local.json`), shared project (`.claude/settings.json`),
  and user (`~/.claude/settings.json`) — with no user-scope *local* file.
  `~/.claude/settings.local.json`, where a stow user's own
  `claude/.claude/settings.local.json` lands, therefore corresponds to no
  documented scope, and a direct test confirmed `tui`, `theme`, and
  `agentPushNotifEnabled` are not honored there. While the user settings file
  is a symlink into this repo, a personal preference and a shipped default are
  the same bytes; overriding one means editing the tracked file, the tradeoff
  every key in that file already carries.

  All three are committed here. `tui: "fullscreen"` has the functional case:
  the alternate-screen renderer removes redraw flicker, holds memory flat
  across long conversations, and adds mouse support — click-to-expand tool
  output, click-and-drag selection, and copy-to-clipboard on mouse release —
  none of which the classic renderer offers. `theme: "dark"` and
  `agentPushNotifEnabled: true` have no equivalent justification; they are
  defaults, not capabilities.

  **The session-keys guard is a drift gate, not a presence ban.**
  `guard-settings-session-keys.sh` compares the staged file against `main` key
  by key, so a guarded key holding a committed value keeps its protection: a
  later `/config` theme change or `/tui classic` still denies the commit that
  would ship it. `model: "sonnet"` already sits in this file under that
  arrangement. The gate has no agent-side proceed path by design, so the
  commit that first introduces a guarded key's value is run by the engineer
  directly rather than by a Claude Code session, and the guarded key list stays
  untouched. Dropping `theme` and `tui` from that list to unblock one commit
  was rejected: Claude Code writes both keys itself, and this file is reachable
  through the user-scope symlink, which is the accidental-commit path the keys
  were added to catch.

  **Two entries this file cannot host.** `enabledPlugins` has no per-account
  expression under this layout — every Claude Code account on a machine
  resolves `settings.json` through the same symlink, so a plugin enabled for
  one account is enabled for all. A toggle wanted for a single account, and an
  `extraKnownMarketplaces` entry naming one machine's absolute checkout path,
  were both dropped rather than committed; the second would be wrong for any
  other consumer, whose checkout is elsewhere. A real per-account settings file
  belongs in the machine's own provisioning, outside this repo.

  **Open discrepancy, not resolved here.** The settings reference lists
  `enabledPlugins` and `agentPushNotifEnabled` with scope "Any file", defined
  there as effective in all four settings locations including Local.
  `.claude/rules/settings-json-conventions.md` states the opposite for
  `enabledPlugins`. Both cannot be right, and nothing in this repo tests the
  question.

  ### Sources

  - [Claude Code settings](https://code.claude.com/docs/en/settings) — the five-level precedence model and each level's file path.
  - [Claude Code settings reference](https://code.claude.com/docs/en/settings-reference) — per-key scope column, including the "Any file" scope cited above.
  - [Fullscreen mode](https://code.claude.com/docs/en/fullscreen) — alternate-screen renderer behavior, memory, and mouse support.
  - `claude/.claude/hooks/guard-settings-session-keys.sh` and [`docs/hooks.md`](hooks.md) — the guarded key set and the staged-vs-`main` comparison.
  ```

  Constraints on the writer: no account name, plugin name, or absolute checkout path anywhere in the entry; and no "used to be"/PR-relative framing — the entry must read correctly to someone who never sees this PR.

**Not in this branch's diff, engineer-run in the main tree:** repo-root `.claude/settings.json`, the main tree's working copy of `claude/.claude/settings.json`, and `stash@{0}`.

**Reuse:** no new code and no new helper. The only reuse is conventional — the existing entry grammar and `### Sources` style in `docs/design-decisions.md`, and the existing bullet shape in `docs/hooks.md`.

## Verification

Run from the worktree root, in order:

1. `jq empty claude/.claude/settings.json` — the edit is inside a JSON file whose corruption is what started this; a parse check is the cheapest possible guard. (`python3 -m json.tool claude/.claude/settings.json > /dev/null` if `jq` is unavailable.)
2. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` (worktree-relative venv path) — this repo's documented scoped test command. It maps both edited paths (`select-tests.py:77`, `:88-94`), so the selected set genuinely covers this diff. Do not widen to the full suite — nothing here needs a whole-repo claim.
3. Read the rendered `docs/design-decisions.md` tail: heading numbering runs 42 → 43, the date matches the heading convention, links resolve, and no account name, plugin name, or absolute path survived. No test harness covers prose docs, so this is a visual check.
4. `/code-review` before the commit, per the global rule.
5. The commit itself: **the engineer runs `git commit` in the worktree.** An agent-run commit denies on `theme`/`tui` — that denial is the guard working, not a failure to route around.

Thread A verification, engineer-run in the main tree: `git diff HEAD -- .claude/settings.json` is empty, `jq empty .claude/settings.json` passes, `git status --short` shows neither settings path, `git stash list` no longer shows the entry, and the terminal status line renders again.

## Out of scope

- **A per-account `enabledPlugins` mechanism.** No supported Claude Code expression exists under a layout where every account's `settings.json` is the same symlink. The stow architecture that causes this is provisioned by the engineer's own separate, private machine-provisioning repo, so it's in reach — the durable fix (a real, non-symlinked settings file provisioned per account, mirroring how `claudeMdExcludes` already handles `CLAUDE.md`) is deliberately deferred there rather than attempted here, not blocked by anything outside this plan's reach.
- **A nets-to-zero carve-out in `require-worktree-for-git-writes.sh` for git-state repairs that produce no diff from HEAD.** This repo owns that hook, so the carve-out is in reach, not an external constraint — it's deliberately not designed here, since it needs its own blast-radius analysis (distinguishing "produces zero diff" from "looks safe" is exactly the kind of judgment call a hook can't make reliably) rather than being a byproduct of this settings fix. Thread A stays engineer-run under the hook's current, unconditional behavior.
- **A personal home for the dropped `extraKnownMarketplaces` entry**, and more generally a user-customization layer over the stowed copy. The engineer is taking this up separately; this plan only records that the entry cannot stay in the shared file.
- **Amending `GUARDED_KEYS_JSON` or its tests.** Rejected with reasons in Approach; the only guard-adjacent edit here is the one-line `docs/hooks.md` accuracy fix that entry 43's citation depends on.
- **Resolving the settings-reference "Any file" vs. `.claude/rules/settings-json-conventions.md` contradiction.** Recorded as open; settling it needs an experiment nobody has run, and neither this change nor the rules file depends on the answer.
- **Editing `.claude/rules/settings-json-conventions.md`.** It may be the wrong half of the contradiction, but changing it on an unresolved question would ship a guess as a rule.
- **The Thread A repair as a committable change.** It nets to zero diff from HEAD and cannot be executed by any Claude Code session; it appears in this plan as a prerequisite, not as a file.
