# Stop Claude Code from double-loading claude-config's global CLAUDE.md

## Context

Every Claude Code session working in the claude-config repo, and every subagent it dispatches, currently loads the repo's 176-line global-instructions document (`claude/.claude/CLAUDE.md`) twice: once via the `~/.claude/CLAUDE.md` symlink at session start, and a second time — full byte-identical content re-injected as a new system-reminder — the first time the session `Read`s any file under `claude/.claude/**`, because Claude Code's nested-CLAUDE.md discovery does not resolve the symlink back to the already-loaded file. Reproduced live in a prior session (Read on `claude/.claude/skills/code-review/SKILL.md` triggered the duplicate; a second Read under a different `claude/.claude/**` subdirectory did not re-trigger it — one-time cost per session, and separately one-time per subagent dispatch since each subagent builds its own fresh context). Motivating cost data (this account, `transcript-analysis.py --this-repo --since 30d`): 554 sessions in this repo totaled $10,067.95, with 94.9% of sessions peaking past 80,000 tokens in a single turn — a floor nearly every session hits, consistent with a fixed per-session/per-dispatch context cost. The duplicate load is one concrete, verified contributor to that floor, not claimed to be the only one. Goal: eliminate the duplicate load for every contributor session and every subagent it dispatches, while leaving the repo's stow-based `claude/.claude/` → `~/.claude/` 1:1 install mapping, and the correct/intended per-path `.claude/rules/*.md` injection, both completely intact.

`verify-sources` was run this session against primary Claude Code documentation, resolving the brief's open §5 question. Confirmed via direct fetch of `https://code.claude.com/docs/en/memory` (section "Exclude specific CLAUDE.md files", under "Manage CLAUDE.md for large teams"), quoted verbatim:

> In large monorepos, ancestor CLAUDE.md files may contain instructions that aren't relevant to your work. The `claudeMdExcludes` setting lets you skip specific files by path or glob pattern.
>
> This example excludes a top-level CLAUDE.md and a rules directory from a parent folder. Add it to `.claude/settings.local.json` so the exclusion stays local to your machine:
>
> ```json
> {
>   "claudeMdExcludes": [
>     "**/monorepo/CLAUDE.md",
>     "/home/<user>/monorepo/other-team/.claude/rules/**"
>   ]
> }
> ```
>
> Patterns are matched against absolute file paths using glob syntax. You can configure `claudeMdExcludes` at any settings layer: user, project, local, or managed policy. Arrays merge across layers.
>
> To exclude a rules file you reach through a symlink, whether the file or its directory is the link, write the pattern against either path: the file's path under `.claude/rules/` or its link target. A pattern that matches either path excludes the file.
>
> Managed policy CLAUDE.md files cannot be excluded.

A supported, documented exclusion mechanism exists, so this plan takes the primary path from the brief (§6 step 3: use `claudeMdExcludes`) and does not plan the fallback rename/invariant-break path (§6 step 4) — that path is moot and out of scope. No engineer confirmation is needed for the fallback since it isn't being taken.

## Approach

Add one key to this repo's **project-scope** settings file so Claude Code's nested-CLAUDE.md discovery skips the stow-source global-instructions file it has already loaded at user scope. The whole fix is a three-line JSON addition to `.claude/settings.json` at the repo root; the rest of the change is a durable record of why the key exists and a guard against its silent deletion.

Two settings files in this repo are easy to conflate, and only one is correct here:

1. **`.claude/settings.json` (repo root)** — this repo's own *project*-scope settings. Applies to sessions whose working directory is this repo or one of its worktrees. Currently holds `enabledPlugins` and `permissions.allow`. **This is the file the fix edits.**
2. **`claude/.claude/settings.json`** — the stow source that installs to every consumer's `~/.claude/settings.json` (*user* scope, every project on the machine). **Not touched.**

The duplicate load can only occur when a session's working directory makes `claude/.claude/**` reachable by nested discovery — that is, when the session is inside this repo. It cannot happen in a consumer's unrelated project. Project scope therefore matches the bug's actual reach, and `claude/.claude/CLAUDE.md`'s Safety section settles it directly ("would another engineer on this project need this? If yes → `settings.json`"). Siting it at project scope also contains the blast radius of the one unverified premise below (row5): if the pattern turns out to over-match through the stow symlink, the damage is confined to sessions in this repo rather than every project on every consumer's machine.

**Root problem.** Every session and every subagent working in this repo loads `claude/.claude/CLAUDE.md` twice — once at user scope through the `~/.claude/CLAUDE.md` stow symlink, and again as a fresh system-reminder block the first time anything under `claude/.claude/**` is read.

**Givens**

- **G1.** Nested-CLAUDE.md discovery walks the physical filesystem tree and does not dedup a file it already loaded through a symlink. Vendor behavior in Claude Code; nothing in this repo can change it.
- **G2.** The global instructions must stay at `claude/.claude/CLAUDE.md`. Stow links each immediate child of `claude/.claude/` into `~/.claude/`, so relocating or renaming the file breaks the 1:1 install mapping every consumer runs (`docs/design-decisions.md:45`).
- **G3.** `claudeMdExcludes` patterns match against absolute file paths using glob syntax, and arrays merge across settings layers. Vendor-defined semantics, quoted from primary docs.
- **G4.** The docs state the either-path symlink rule only for `.claude/rules/` files, and say nothing about whether it extends to CLAUDE.md files. A vendor documentation gap — settleable only by observation, not by any decision in this plan.

**Assumptions**

- **row1** `[verified: https://code.claude.com/docs/en/memory, "Exclude specific CLAUDE.md files"]` — `claudeMdExcludes` is a supported setting, configurable at project scope, and arrays merge across layers. Only managed-policy CLAUDE.md files are unexcludable.
- **row2** `[verified: this plan-architect dispatch's own context]` — the duplicate reproduces live. This agent's context carried the global-instructions text twice: once headed `/Users/<user>/.claude/CLAUDE.md`, and once headed with the worktree's `claude/.claude/CLAUDE.md` after reading `claude/.claude/skills/plan-it/SKILL.md`. In a worktree-anchored session the nested-discovery hit is the *worktree's* copy, a different absolute path from the symlink target in the main checkout.
- **row3** `[verified: Glob **/CLAUDE.md over the repo]` — exactly two files named `CLAUDE.md` are tracked: `CLAUDE.md` at the repo root and `claude/.claude/CLAUDE.md`. The pattern `**/claude/.claude/CLAUDE.md` therefore names exactly one file, and cannot reach the repo-root project CLAUDE.md (no `claude/.claude/` prefix) or any `.claude/rules/*.md` file (filename does not match).
- **row4** `[verified: .claude/settings.json, read this session]` — the file currently contains only `enabledPlugins` and `permissions.allow`. `claudeMdExcludes` is added as a third top-level sibling key; neither existing key is touched.
- **row5** `[unverified]` — the pattern `**/claude/.claude/CLAUDE.md` does **not** suppress the user-scope load reached through `~/.claude/CLAUDE.md`. The literal user-scope path (`/Users/<user>/.claude/CLAUDE.md`) does not match the pattern, but its symlink *target* (`<checkout>/claude/.claude/CLAUDE.md`) does. Whether Claude Code matches a CLAUDE.md file against its link target — documented for rules files, silent for CLAUDE.md (G4) — decides this. Verification step V2 settles it; the fallback pattern is named there. Everything downstream of the pattern choice inherits this flag.
- **row6** `[verified: claude/.claude/scripts/select-tests.py:244-332]` — no predicate in `DOMAIN_RULES` or `CROSS_DOMAIN_EXCEPTIONS` matches the literal path `.claude/settings.json`. `MAPPED_ROOT_CLAUDE_DIRS` covers only *directories* under root `.claude/` (`plans`, `rules`, `skills`), not files. A change to this file currently falls open to the full suite with reason `unmatched-path`.
- **row7** `[verified: grep for real-file settings reads across claude/.claude/]` — no test reads the repo-root `.claude/settings.json` today. The three real-file settings reads all target the stow-source copy: `test_hook_alignment.py:151`, `test_check_claude_md_length.py:21`, `test_skills.py:153`. `test_install_sh_project_scope_plugins.py` writes fixture settings into temp repos rather than reading the real one.
- **row8** `[verified: claude/.claude/hooks/guard-settings-session-keys.sh:67,70]` — the staged-settings guard pins `SETTINGS_REPO_PATH="claude/.claude/settings.json"` and matches staged names with `grep -qF`. `.claude/settings.json` is not a substring of that path, so the hook does not fire on this change and will not block the commit.
- **row9** `[verified: docs/design-decisions.md heading scan]` — the highest existing section is §38; the next number is 39. §28 ("Startup context bloat") is the direct sibling decision and establishes the section shape: dated heading, prose, `### Sources` citing primary docs plus the plan file.
- **row10** `[verified: https://code.claude.com/docs/en/memory, "How CLAUDE.md files load"]` — taking `claudeMdExcludes` rather than renaming or relocating any stow-managed file. The documented mechanism exists and is purpose-built for this case, so the invariant-breaking alternative is moot.

**Mechanisms**

- **M1 — add `claudeMdExcludes` to `.claude/settings.json` (repo root).** `anchors: root`. The only documented lever that suppresses a nested-discovery CLAUDE.md load without touching the file itself, so it fixes the duplicate while leaving G2's 1:1 stow mapping untouched. Exact addition, merged as a third top-level key alongside the existing two (row4):

  ```json
  "claudeMdExcludes": [
    "**/claude/.claude/CLAUDE.md"
  ]
  ```

  `**` absorbs the checkout root, which differs per contributor's clone location, and also absorbs the `.claude/worktrees/<slug>/` prefix so worktree-anchored sessions are covered by the same entry (row2).

  *Lighter primitives considered.* (a) Relying on Claude Code to dedup the symlinked file on its own — fails: reproduced live as not happening (row2), and no documentation claims it for this path. (b) `permissions.deny` on `Read` of the CLAUDE.md path — fails on two counts: the nested block is injected by discovery rather than by a tool call, so nothing about it passes through a permission gate, and `docs/design-decisions.md:339` already records that `permissions.deny` gates *calling* a tool rather than removing content from the prompt. (c) `.claude/settings.local.json` (Local scope, gitignored) — genuinely lighter in blast radius and is the scope the vendor's own example uses, but it fixes the duplicate for one machine only; the duplicate is a property of this repo's layout that every contributor hits, which `claude/.claude/CLAUDE.md`'s settings-scoping rule routes to committed `settings.json`.

- **M2 — add `docs/design-decisions.md` §39.** `anchors: root, row5`. JSON carries no comments, so a bare fourth key in a 15-line file has no explanation attached to it and reads as deletable; the decision log is this repo's documented home for exactly that ("the non-obvious choices in this repo… and the reasoning behind each", `README.md:61`). It is also the only durable place to record row5's verified/unverified boundary.

  Required content, subject to `claude/.claude/CLAUDE.md`'s durable-doc rules (must stand alone after the PR body is lost; no PR-defined labels; no "used to be X" framing; one fact per sentence):
  1. Nested-CLAUDE.md discovery walks the physical filesystem tree, so a session inside this repo loads `claude/.claude/CLAUDE.md` a second time on the first read under `claude/.claude/**`, in addition to the user-scope load through the stow symlink.
  2. The exclusion is sited at project scope because the duplicate only occurs for a session whose working directory is inside this repo.
  3. The stow-source `claude/.claude/settings.json` deliberately carries no such entry: a user-scope entry would apply to every project on every consumer's machine, which is wider than the condition it addresses.
  4. Renaming or relocating the file was rejected: stow links each immediate child of `claude/.claude/` individually, so a rename breaks the install mapping (cross-reference §5 rather than restating it).
  5. Whether a `claudeMdExcludes` pattern is matched against a symlink's target as well as its own path is documented for `.claude/rules/` files and unstated for CLAUDE.md files; name which behavior was observed when V2 ran, and state that the current pattern depends on it.
  6. `### Sources` — the Claude Code memory docs URL, and `.claude/plans/exclude-nested-claude-md-duplicate.md`.

  Renumber if another branch lands a §39 first (row9).

- **M3 — add a guard test for the settings entry.** `anchors: root, row3`. The exclusion fails silently: nothing in a running session reports that a glob stopped matching, and the observable symptom is a larger context, which nobody notices. The test is also where the key's rationale becomes greppable from the test suite. Keep it to one small module with a docstring naming why the key exists and pointing at §39, and three assertions: the `claudeMdExcludes` key is present and non-empty; it contains the exact pattern string; and the path the pattern's non-glob tail names (`claude/.claude/CLAUDE.md`) exists on disk. This mirrors the existing pattern of `test_check_claude_md_length.py` and `test_hook_alignment.py`, both of which pin real settings content by path.

- **M4 — map `.claude/settings.json` in `select-tests.py`.** `anchors: row6, row7`. M3 makes a HOOKS_TESTS_DIR test read this path, and the rule table's own documented discipline is that a cross-domain path read gets an audited entry ("When a test starts reading a file outside its own domain-rule tree by path or subprocess, audit this table by hand and add the matching entry", `select-tests.py:255-258`). Add a `ROOT_SETTINGS_JSON = ".claude/settings.json"` constant carrying the file's conventional per-constant comment naming its reader, plus `(lambda p: p == ROOT_SETTINGS_JSON, (HOOKS_TESTS_DIR,))` in `CROSS_DOMAIN_EXCEPTIONS`. This also removes the current `unmatched-path` fall-open (row6), the same cleanup `CHANGELOG.md:12` records for `.claude/plans/` and `CHANGELOG.md`.

- **M5 — add a `CHANGELOG.md` entry under `## [Unreleased]` → `### Changed`.** `anchors: root`. Contributor-facing behavior change to this repo's own session context; the section already carries entries of this size and genre (a marker-directory rename, a `permissions.allow` addition). Prepend, matching the newest-first ordering.

**Dispatch split.** One `code-writer` dispatch covers all five files — they share the same context, and splitting would force the same background to be restated per prompt. V2 and V3 below are observational and cannot be delegated to `code-writer`; the parent session runs them after the dispatch returns.

## Critical files

- **`.claude/settings.json`** (repo root, modify) — add the `claudeMdExcludes` key exactly as given in M1. Preserve `enabledPlugins` and `permissions.allow` byte-for-byte; this is a merge, not a rewrite (row4).
- **`docs/design-decisions.md`** (modify) — append §39 with the six content points in M2. Reuse §28's shape verbatim as the template: `## 39. <title> (YYYY-MM-DD)`, prose paragraphs, `### Sources` bullet list. Cross-reference §5 for the stow mechanism rather than restating it.
- **`claude/.claude/hooks/tests/test_claude_md_excludes.py`** (create) — M3's three assertions, against the **repo-root** `.claude/settings.json` (not the stow-source `claude/.claude/settings.json`). Resolve the repo root the way sibling modules already do (`Path(__file__).resolve().parents[4]`, as at `test_check_claude_md_length.py:21` and `test_install_sh_project_scope_plugins.py:15`), then join `".claude" / "settings.json"` — do not copy `test_check_claude_md_length.py`'s own join target verbatim, since that sibling's `parents[4] / "claude/.claude/settings.json"` resolves to the *other*, stow-source file.
- **`claude/.claude/scripts/select-tests.py`** (modify) — M4's constant and `CROSS_DOMAIN_EXCEPTIONS` entry. Place the constant near `ROOT_CLAUDE_MD`/`ROOT_RULES_DIR` (lines 121-132) and follow the file's existing convention of a comment above each constant naming which test reads the path.
- **`claude/.claude/scripts/tests/test_select_tests.py`** (modify) — add the coverage case for the new rule. Check whether the existing rule-table fidelity tests near line 660 need an update; `MAPPED_ROOT_CLAUDE_DIRS` is a directory-name set and should **not** gain a file entry.
- **`CHANGELOG.md`** (modify) — M5's entry, prepended under `### Changed`.

Not modified, and confirmed so rather than assumed: no doc site asserting the stow 1:1 invariant needs an edit, because `claudeMdExcludes` changes nothing about install or stow. The sites checked are `CLAUDE.md:41`, `CLAUDE.md:54`, `README.md:64`, `README.md:113`, `docs/design-decisions.md:45`, and `docs/design-decisions.md:536`. `install.sh` needs no new step — `.claude/settings.json` is a tracked repo file that requires no install-time action, unlike the `enabledPlugins` block at `install.sh:618-668`.

## Verification

**V1 — automated checks (run from the worktree root).**

```
../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py
../../../.venv/bin/ruff check claude/.claude/
```

Both are exact-match entries in this repo's `permissions.allow`. With M4 in place, `select-tests.py` maps the diff to `claude/.claude/hooks/tests/` and `claude/.claude/scripts/tests/`; without M4 it would report `unmatched-path` and widen to the full suite on its own (row6), which is CLAUDE.md's case 1 and needs no by-hand widening either way.

**V2 — the duplicate is gone and the user-scope load survives (settles row5).** Start a **fresh** session anchored in this worktree after the settings edit is on disk; do not reuse the editing session, since project settings are read at launch and a mid-session subagent may inherit the parent's launch-time settings. In that session, dispatch one subagent, have it `Read` a single file under `claude/.claude/**` (e.g. `claude/.claude/skills/code-review/SKILL.md`), and have it report the source path heading of every CLAUDE.md-content system-reminder block in its context.

- **Pass:** exactly one block carrying the global-instructions text, headed with the user-scope path. The block headed with the worktree's `claude/.claude/CLAUDE.md` is absent.
- **Fail mode A — two blocks still present:** the pattern is not matching. Do not widen it blindly; re-derive the exact absolute path from the block heading the subagent reported and fix the pattern against that literal path.
- **Fail mode B — zero global-instruction blocks:** the pattern matched through the symlink target and suppressed the user-scope load too (row5 falsified). Replace the entry with `"**/.claude/worktrees/*/claude/.claude/CLAUDE.md"`, which cannot match the main-checkout symlink target, and record in §39 that the narrower pattern leaves the duplicate in place for a session anchored in the main checkout rather than a worktree. Note the narrow pattern's limit: a branch slug containing `/` produces a nested worktree directory that a single `*` will not match.

**V3 — path-scoped rule injection is unaffected.** In the same fresh session, `Read` a `SKILL.md` file and `Read` `.claude/settings.json`, then confirm all three `.claude/rules/*.md` blocks still inject: `skill-and-agent-self-review.md`, `review-pipeline-dispatch.md`, and `settings-json-conventions.md`. That exact trio injected on those exact reads during this plan's own design pass, so it is a known-good baseline rather than a guess. The pattern cannot reach them by construction (row3), but this confirms it empirically rather than by argument.

**V4 — the repo-root project CLAUDE.md still loads.** Falls out of V2 for free: the fresh session's context must still carry the block headed with the repo-root `CLAUDE.md` path. It does not match the pattern (row3); confirm rather than assume.

## Out of scope

- **No change to `claude/.claude/settings.json`** (the stow-source, user-scope file). A `claudeMdExcludes` entry there would apply to every project on every consumer's machine — wider than the condition it addresses, and it would mix two independently-versioned settings files' concerns.
- **No rename, relocation, or split of any stow-managed file**, including `claude/.claude/CLAUDE.md`. G2 forbids it, and row10 makes it moot.
- **No `install.sh` change.** `.claude/settings.json` needs no install-time step.
- **No change to the content split** between the repo-root `CLAUDE.md` and `claude/.claude/CLAUDE.md`.
- **No re-evaluation of stow** as this repo's distribution mechanism (`docs/design-decisions.md` §5).
- **No attempt to shrink `claude/.claude/CLAUDE.md` itself.** Reducing the file's size is a separate lever with its own tradeoffs against the 200-line cap discipline; this plan removes the second copy, not the first.
- **No broader audit of the per-session context floor.** The duplicate load is one verified contributor to the measured 80,000-token floor, not the only one. Other contributors are neither identified nor addressed here, and no claim is made about how much of that floor this change removes — V2 confirms the duplicate is gone, and does not quantify the saving.
- **No widening of `MAPPED_ROOT_CLAUDE_DIRS`** to cover files. It is a directory-name set with a fidelity test behind it (`test_select_tests.py:662-678`); M4 adds a file-path entry to `CROSS_DOMAIN_EXCEPTIONS` instead.
- Do not use this work as an occasion to restructure `install.sh` beyond what the brief's fallback step required (moot, not taken), or to re-evaluate the stow mechanism generally.
- Do not attempt to fix the review-pipeline fan-out / Opus-routing investigation — that is separate work, briefed independently.
- Do not change `.claude/worktree-required` or any other hook-enforcement opt-in as part of this change.
