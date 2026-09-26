# Remove the claude-plugins-official marketplace registration

## Context

Stop `claude-config` from shipping a pre-registered `claude-plugins-official`
marketplace, because it costs every stow consumer a real per-session startup
tax to preserve an opt-in convenience nothing currently uses.

A sibling session investigating slow plugin loading (a suspected race
condition, "race war") on one Claude Code account found that
`claude-plugins-official` is the only one of four registered marketplaces
that does a fresh github-source SSH clone every session, and that its
clone finishing directly precedes the first "Loaded plugins" milestone in
every session log reviewed. That account currently has zero plugins
enabled from it — the two it lists in `enabledPlugins`
(`claude-md-management`, `claude-code-setup`) are both `false`.

That sibling session initially framed this as an account-level settings
change (a path under that account's own `<config-dir>/settings.json`)
separate from this repo. Verification here found otherwise: that path is a stow
symlink resolving to this repo's own `claude/.claude/settings.json` —
identical content ships to every account on every machine that runs
`./install.sh`. So this is a `claude-config` change with a "every stow
consumer" blast radius, not a single-account tweak.

The registration is not dead cruft; it is deliberate, documented history:

- PR #28 added `extraKnownMarketplaces` specifically "so a fresh machine
  knows where to get `claude-plugins-official`" for the install-time
  bootstrap loop (now `claude/.claude/scripts/register-marketplace.sh`).
- PR #29 pinned `claude-md-management` / `claude-code-setup` into
  `enabledPlugins: false`.
- PR #161 explicitly *kept* both as `false` "quick-flip handles for
  plugins that may occasionally be useful" rather than removing them, and
  updated README.md to describe that stance as intended: "wires in the
  `anthropics/claude-plugins-official` marketplace but ships official
  plugins ... disabled by default, so contributors opt in deliberately."

The user, given this history, chose to proceed with removal anyway: the
session-startup cost paid by every consumer outweighs preserving a
zero-effort opt-in path for two plugins nobody currently enables. This
plan reverses the decision made in PR #161 on that basis, not because the prior
decision was wrong on its own terms — the tradeoff calculus changed once
the per-session clone cost was measured.

## Approach

Delete both `claude-plugins-official` keys from the stowed `claude/.claude/settings.json` — the `extraKnownMarketplaces` entry and the two `enabledPlugins: false` entries — and pair that commit with one live, engineer-run `claude plugin marketplace remove claude-plugins-official` per Claude Code profile that has already been provisioned. **The commit alone does not realize the performance fix on an already-provisioned profile, and no commit can.** `register-marketplace.sh` decides "already registered" by reading Claude Code's own per-profile marketplace registry (`claude plugin marketplace list --json`), never settings.json, and nothing in this repo ever removes a marketplace except a relocated `claude-config`. So removing the declaration changes only what a *future* `./install.sh` or a *fresh* profile adds; a registration that already landed survives the pull untouched. Whether that surviving registration is what Claude Code's session-start refresh actually iterates is the one thing this plan does not know (row 3) — which is precisely why the removal command ships as a required step rather than as optional hygiene. Sizing the change as repo-only would leave the measured cost in place on the very profile that motivated it.

The two `false` entries go with the declaration rather than staying behind. `claude/.claude/rules/settings-json-conventions.md` already states the governing rule (`false` only for a genuine re-enable case), and the stronger argument is specific to this change: once the marketplace is undeclared, `register-marketplace.sh`'s `enabledPlugins` loop skips-and-warns any plugin whose marketplace isn't registered, so flipping either entry to `true` would print a skip line and install nothing. The entries would not be silently inert — they would be a broken handle that reads as a working one, which is worse than absent. The rationale behind PR #161 is answered on its own terms: the handle's whole value was that a flip to `true` sufficed, and removing the marketplace makes that false. Both keys are dropped entirely rather than left as `{}` — `register-marketplace.sh` reads each through `// {}`, so a missing key is already a no-op, and an empty placeholder documents nothing.

Rejected: **auto-pruning undeclared marketplaces inside `register-marketplace.sh`** so the repo half becomes self-realizing. It converts a declarative file into a destructive sync over user state, silently removing any marketplace a consumer added by hand on their own profile — blast radius well past this change — and it would not even help the motivating profile, since `install.sh` does not run on `git pull`. Rejected: **dropping only `extraKnownMarketplaces`** (covered above). Rejected: **keeping the declaration and suppressing the per-session refresh** — no such per-marketplace knob is known to exist in Claude Code and none was verified this session; that stays an open revisit trigger in the decision file rather than an assumed alternative.

Over-powered-primitive check on row 3's mechanism (M7): the heavier candidate is the declarative-sync-with-pruning just rejected. The two lighter primitives considered are (a) a one-time documented command the engineer runs per provisioned profile, and (b) doing nothing on provisioned profiles and letting the declaration removal cover only fresh installs. (b) fails because it leaves the measured cost in place everywhere it is currently paid. (a) is adopted, so no heavier primitive is taken up.

**Assumption ledger**

**Root problem.** Every stow consumer pays a per-session Claude Code startup cost to keep a github-source marketplace pre-registered whose two declared plugins are both disabled and which nothing currently enables a plugin from.

**Givens**

- **G1.** Claude Code owns whether and how it refreshes a registered github-source marketplace at session start; this repo has no knob over that behavior. Vendor-imposed.
- **G2.** The per-profile marketplace registry that `claude plugin marketplace add`/`list`/`remove` reads and writes is Claude Code's own local state, outside this repository and outside any commit's reach. The CLI owns it. `[verified: register-marketplace.sh:62-63 and :117 decide "already registered" from `claude plugin marketplace list --json`, never from settings.json]`
- **G3.** Un-registering a marketplace on an already-provisioned profile is an action only the engineer can take, in a live session against that profile. Dissolving the design's dependence on it would require the rejected auto-prune mechanism, i.e. a decision outside this plan.
- **G4.** Rollout is pull-gated with no push mechanism: `claude/.claude/**` goes live on `git pull` with no reinstall, and a consumer who does not pull keeps the registration. Each consumer owns their own pull cadence.

**Rows**

1. The stowed `claude/.claude/settings.json` is the only place this repo declares `claude-plugins-official`, and both of its `enabledPlugins` entries are `false`. `[verified: settings.json:514-525 read this session; `git grep claude-plugins-official` returns no other declaration — every remaining hit describes the upstream anthropics repo]` — anchors: root, row2
2. `claude/.claude/scripts/register-marketplace.sh` is this repo's only caller of `claude plugin marketplace add`, runs only from `install.sh:703`, is wired as no hook, and removes nothing but a relocated `claude-config`. `[verified: register-marketplace.sh:62-127 and :89; `git grep register-marketplace` shows install.sh:703 as the sole invocation and no entry in settings.json's `hooks` block]` — anchors: root, row3
3. Removing the declaration therefore cannot un-register the marketplace on a profile where the add already ran. **Which state Claude Code's session-start refresh iterates — its own registry or settings.json's `extraKnownMarketplaces` — is `[unverified]`:** no file in this repo states it, and it was not checked against primary documentation this session. Any downstream claim that the repo edit alone realizes the fix inherits this flag, which is why M7 ships unconditionally and why Verification settles it empirically instead of the plan asserting it. — anchors: root
4. Leaving the two `false` entries behind would make them misleading rather than inert: the `enabledPlugins` loop skips-and-warns any plugin whose marketplace is unregistered, so a flip to `true` installs nothing. `[verified: register-marketplace.sh:132-137, :144]` — anchors: row2
5. `claude/.claude/rules/settings-json-conventions.md` already carries the rule that settles the `false`-vs-remove question. `[verified: read this session, its "Disabling a plugin" bullet]` — anchors: row2
6. No test reads `enabledPlugins` or `extraKnownMarketplaces` out of the stowed settings.json. `[verified: select-tests.py:136-143 enumerates that file's test readers — `permissions.allow`, `skillOverrides` counts and map, the docs/skills.md cross-check, the destructive-cleanup permissions check, and claude-enable-tool's settings payload; grepping `enabledPlugins` under `claude/.claude/hooks/tests/` matches only `test_install_sh_project_scope_plugins.py`, which reads the repo-root `.claude/settings.json`]` — anchors: row2
7. Dropping both keys makes `statusLine` the file's last key, so the comma after its closing brace must go or the file is invalid JSON. `[verified: settings.json:510-526]` — anchors: row2
8. The three `claude-plugins-official` mentions in `plugins/skill-management/skills/skill-review/REFERENCES.md`, `claude-skills/skills/agent-review/REFERENCES.md`, and `plugins/linear-formatting/skills/linear-formatting/REFERENCES.md` all describe the upstream anthropics repo's own contents, not this repo's registration of it, so none goes stale. `[verified: read skill-review/REFERENCES.md:1-41 and agent-review/REFERENCES.md:42-44 this session; linear-formatting's is a source path into that upstream repo]` — anchors: row9
9. One doc statement does go stale: `docs/design-decisions/loop-simplify-name-only.md:9` names both plugins as remaining in `enabledPlugins: false`. Its own decision (the `off`→`name-only` flip) is not overturned, so this is a narrow factual repair, not a supersession note. `[verified: read this session]` — anchors: root
10. This plan file ships in the same PR, so its Context section is public-repo content — it must not name a non-personal Claude Code account, publish a per-account session-startup duration, or publish a log count. `[verified: `CLAUDE.md` § "Also redact structural fingerprints and provenance" states the per-account-figure prohibition as absolute, and `docs/private-project-redaction.md` § "This repository, one account" sets the only publishable-measurement bar — a hand-read session log on another account meets neither its corpus nor its refusing-instrument requirement]` — anchors: root, row11
11. The decision does not need the withheld figure. Its publishable grounding is the mechanism claim: the only github-source marketplace in the registered set re-clones at session start, and nothing enables a plugin from it. That claim is a fact about Claude Code, re-derivable on the account used for this repo's own work. `[unverified]` as a re-derivation — nobody has re-run it on that account yet; Verification's last step does. — anchors: root, row10
12. Blast radius is every stow consumer, not the session owner, so the user surface and the migration note are written for that audience. `[verified: `CLAUDE.md` § "Plans in this repo affect all stow users"]` — anchors: root

**Mechanisms**

- **M1 — This plan's own Context section stays redacted** (already applied above): no account name, no per-account timing figure, no log count — only the surviving mechanism claim (that marketplace is the only github-source one that re-clones each session, and the account enables nothing from it). `anchors: root, row10, row11`
- **M2 — `claude/.claude/settings.json`:** delete the whole `enabledPlugins` object and the whole `extraKnownMarketplaces` object, and remove the now-trailing comma after `statusLine`'s closing brace. Nothing else in the file changes. `anchors: root, row1, row2, row4, row5, row6, row7`
- **M3 — `README.md:57`:** drop the marketplace-and-official-plugins clause, keeping the bundled-skills half and its `docs/skills.md` pointer intact — the sentence becomes "It ships a set of bundled Claude Code skills that overlap with its review pipeline disabled by default, so contributors opt in deliberately (see the "Bundled skills disabled by default" section of [docs/skills.md](docs/skills.md))." `anchors: root, row1`
- **M4 — new `docs/design-decisions/official-marketplace-not-preregistered.md`.** Records the reversal, since nothing currently records the original wiring decision. Modelled byte-for-byte in shape on `docs/design-decisions/claude-ai-skill-sync-disabled-by-default.md`: H1, blank line, `*2026-09-17.*` on line 3 with no `Formerly §N` clause (post-split decision), body, `## Sources`. Content: the cost-versus-opt-in tradeoff; the two-command path a consumer now takes to get either plugin back (`claude plugin marketplace add anthropics/claude-plugins-official` then `claude plugin install <name>@claude-plugins-official --scope user`); that the commit does not un-register an already-provisioned profile and why the auto-prune was rejected; the empirically-settled answer to row 3, written after Verification runs, not before; a revisit trigger — if Claude Code gains a per-marketplace refresh or cache control that makes a declaration free at session start, the pre-registration becomes worth reconsidering; and the full-revert path — `git revert`ing this commit restores the repo-wide declaration for future/re-run installs, and any profile (including one that ran M7) regains the registration automatically the next time it runs `./install.sh`, since `register-marketplace.sh`'s existing add-if-absent logic already covers it, with no new one-off command needed in the reverse direction. No per-account figure appears anywhere in it. `anchors: root, row3, row10, row11`
- **M5 — `docs/design-decisions/loop-simplify-name-only.md:9`:** keep the `skillOverrides`-does-not-apply-to-plugin-skills sentence and its source citation; replace the following sentence's now-false example with the general form it was illustrating — a plugin this repo wants excluded is kept out of `enabledPlugins` (or its marketplace left unregistered) rather than handled via `skillOverrides`. No other line in the file changes, and no supersession note is added. `anchors: row9`
- **M6 — `CHANGELOG.md`, `### Removed` under `[Unreleased]`:** the pre-registration is a removed shipped feature, matching that section's existing `check-runner` entry genre. One entry naming the removed keys, the every-stow-consumer scope, the live-on-`git pull` rollout, a link to M4's decision file, and a **Migration** line carrying: the two-command re-add path; the per-profile `claude plugin marketplace remove claude-plugins-official` an already-provisioned consumer must run themselves; an explicit instruction to pull this commit before running that command, since running it against a not-yet-pulled checkout and then re-running `./install.sh` (README.md:110's documented existing-user workflow) lets `register-marketplace.sh`'s add-if-absent loop silently re-add the marketplace and undo the removal; and a note that re-running the remove command on a profile where it already succeeded exits non-zero with a `Marketplace 'claude-plugins-official' not found` message rather than silently no-op-ing, and that this is expected, not a failure. Content shaped on the `syncClaudeAiSkills` entry, the closest precedent in the same file. `anchors: root, row3, G4`
- **M7 — engineer-run, once per already-provisioned profile, outside this repo:** `claude plugin marketplace remove claude-plugins-official`, run only after pulling this commit (see M6's Migration line for why the ordering matters). Not a commit step, not a subagent step — it mutates local Claude Code state on profiles this session's anchor and credentials may not reach, and it is destructive against user state. Not idempotent in the CLI-success sense: `claude plugin marketplace remove` exits non-zero with `Marketplace 'claude-plugins-official' not found` if run again on a profile where it already succeeded — expected, and not a sign anything is wrong. `anchors: root, row3, G2, G3`

## Critical files

- **`claude/.claude/settings.json`** (modify) — M2. A deletion of two top-level keys plus the trailing-comma repair; every other key, including `permissions`, `hooks`, and `skillOverrides`, stays byte-for-byte. Do not confuse this with the repo-root `.claude/settings.json`, which holds this repo's *project*-scope `enabledPlugins` and is not touched.
- **`README.md`** (modify, line 57) — M3. One clause removed from one sentence; the `docs/skills.md` link and the surrounding paragraph are unchanged. No other marketplace sentence in the file mentions the official marketplace — lines 199-209 describe the `claude-config` marketplace only, and line 219 describes the unrelated upstream `linear@claude-plugins-official` plugin.
- **`docs/design-decisions/official-marketplace-not-preregistered.md`** (create) — M4. **Reuse:** `docs/design-decisions/claude-ai-skill-sync-disabled-by-default.md` is the template — same genre (a stow-wide `settings.json` change with a pull-gated rollout, a scope-limited recovery path, and a `## Sources` section citing the settings reference plus this plan file). Filename already satisfies `.claude/rules/design-decisions.md`'s `^[a-z][a-z0-9-]*\.md$` grammar.
- **`docs/design-decisions/loop-simplify-name-only.md`** (modify, line 9 only) — M5.
- **`CHANGELOG.md`** (modify) — M6. **Reuse:** the `syncClaudeAiSkills` entry under `### Changed` as the wording model for scope, rollout, and Migration; the `check-runner` entry under `### Removed` for placement.

**Not modified, deliberately:**
- **`claude/.claude/scripts/register-marketplace.sh`** — no change. Its `// {}` guards already make both missing keys a no-op (line 125 and line 144), so the script needs no defensive edit; its `enabledPlugins` loop becomes a no-op for the default profile while remaining a documented, separately-tested per-profile capability. **Reuse over new code:** rely on that existing tolerance rather than adding a guard.
- **`install.sh`** — no change. Line 703's unconditional `register-marketplace.sh` call and the separate project-scope loop at 736 are both unaffected.
- **`docs/scripts.md:167`** — no change. Its `register-marketplace.sh` description states the script's capability, not the contents of any one profile's `enabledPlugins`.
- **The three `REFERENCES.md`/manifest mentions of `claude-plugins-official`** — no change (row 8).
- **`CLAUDE.md:85`** — no change. Its "three marketplace plugins registered in `enabledPlugins`" sentence refers to the repo-root project settings file.

## Verification

- **`.venv/bin/python3 claude/.claude/scripts/select-tests.py`** — the repo's documented scoped command, per `CLAUDE.md` § "Commands". It computes its own target set; do not widen it by hand. Predicted selection for this diff: `claude/.claude/hooks/tests/`, `claude-skills/skills/tests/`, and `claude/.claude/scripts/tests/` (the `claude/.claude/settings.json` row), plus hooks and skills tests again from `README.md` and `docs/`; `CHANGELOG.md` and `.claude/plans/` select nothing. This diff is neither of `CLAUDE.md`'s two named full-suite cases, so no whole-suite run. `test_design_decision_files.py` sits inside the selected hooks tests, so M4's filename grammar, single H1, and line-3 italic dated provenance line are checked by that same run — no separate command.
- **`jq . claude/.claude/settings.json > /dev/null`** — the one-second check on M2's trailing comma. The selected suite also fails loudly on a malformed file, but not with a message that names the comma.
- **No `ruff` or `shellcheck` run.** This diff changes no `.py` and no `.sh` file, so neither adds signal.
- **`git grep -n claude-plugins-official`** — expect zero hits in `claude/.claude/settings.json`, and hits only in: `CHANGELOG.md` (preserved records plus M6's new entry), `README.md:219`, `.claude-plugin/marketplace.json:56`, `plugins/linear-formatting/.claude-plugin/plugin.json:3`, the three `REFERENCES.md` files, and `.claude/plans/`. Any other hit is a declaration M2 missed.
- **Empirical check, engineer-run on one already-provisioned profile — this is what settles row 3, and M4 is written only after it.** After pulling the change and before running M7: `claude plugin marketplace list` (expect the marketplace still listed — that is row 2's prediction), then launch a session and observe whether the github-source clone still happens at startup. Then run M7's `claude plugin marketplace remove claude-plugins-official` and re-observe. A clone still present before the remove and absent after it confirms the commit alone is insufficient; absent in both confirms the declaration drove it and M7 is hygiene. Record which, as a mechanism outcome, in M4. **Measuring on any profile is fine — only publishing is constrained: M4, M6, the commit message, and the PR body carry the present/absent outcome and no per-account duration, log count, or account name** (row 10, row 11).
- **Not verified by any command:** that every *other* stow consumer's profile gets cleaned up. G4 makes the rollout pull-gated with no completion signal back to this repo; M6's Migration line is the only mechanism reaching them, and M4 should say so plainly rather than imply the fix lands everywhere on merge.

## Out of scope

- **Auto-pruning undeclared marketplaces in `register-marketplace.sh`.** The plan could do this and deliberately won't: it turns a declarative file into a destructive sync over user state, would silently remove a marketplace a consumer added by hand, and would not help the motivating profile anyway since `install.sh` does not run on `git pull`.
- **The other marketplaces registered on any profile, and the broader plugin-load startup investigation** that surfaced this one. This plan removes one registration; it makes no claim about total startup time and proposes no change to the remaining three.
- **Re-enabling either plugin, and any per-account user-customization layer over the stowed `settings.json`** that would let one profile keep the declaration. A separate effort already owns that question — see `.claude/plans/resolve-settings-json-conflict-and-cleanup.md`'s own out-of-scope entry for the dropped `extraKnownMarketplaces` entry.
- **Determining whether Claude Code offers a per-marketplace refresh or cache control** that would make a declaration free at session start. Not investigated, not assumed to exist; recorded as M4's revisit trigger instead.
- **`register-marketplace.sh`'s `enabledPlugins` loop becoming a no-op for the default profile.** It stays as-is: a documented per-profile capability with its own test coverage, not dead code.
- **The three upstream-describing `claude-plugins-official` mentions** (row 8) and **`CLAUDE.md:85`**, which describes the repo-root project settings file.
- **Publishing the originating measurement in any form** — plan file, decision file, CHANGELOG, commit message, or PR body (row 10).
