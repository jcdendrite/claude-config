# Plan: Normalize hardcoded `~/.claude` to config-dir-aware paths

## Context

**Goal:** every reference to Claude Code *per-account state* in this repo — in
skill and agent bodies the model acts on, in `claude/.claude/CLAUDE.md`, and in
documentation prose — must name the **active** config directory rather than the
literal `~/.claude`, so a session running under a non-personal
`CLAUDE_CONFIG_DIR` account reads and writes its own state instead of the
personal account's.

**Scope boundary.** This plan is scoped separately from PR #567, which fixed a
different `~/.claude` defect — runtime path resolution in hooks and scripts —
and left no bypasses behind. The remaining gap is narrower than a blanket
substitution: `git grep -lE '(~|\$HOME|\$\{HOME\})/\.claude'` matches 163 tracked
files / 1,095 lines outside `.claude/plans/`, and the large majority are
correct as written. A literal board-wide substitution would break stow
internals, the deliberately `$HOME`-fixed roots registries, and the
legacy-union guard fallbacks. The defect is narrower and sharper: this repo
does not distinguish **stowed content** (identical under every account) from
**per-account state** (different under every account), and a subset of sites
names the wrong one.

**Intended outcome:** per-account-state sites are corrected, in-flight
continuity files keep resolving across the change, documentation stops
asserting a path that is wrong for a multi-account reader, and a regression
test prevents the state-path class from drifting back.

## Approach

**Concluded design.** Split every `~/.claude` reference by *what lives at the
path* and by *which consumer resolves it*, then apply the notation that
actually resolves in that consumer. Only per-account state changes meaning
across accounts, so only those sites are functional bugs; everything else is
accuracy work or is correct as written.

**The load-bearing distinction — stowed content vs. per-account state.**
`install.sh:253` runs `stow -v … -t "$HOME" claude`, and
this machine's external, privately-maintained per-account provisioning tooling
runs a second, independent `stow --restow .claude` invocation — sourced from
this same `claude/.claude/` tree — into each non-personal account's directory. Both draw from the same `claude/.claude/` source, so the
git-tracked top-level entries — `agents`, `CLAUDE.md`, `hooks`, `rules`,
`scripts`, `settings.json`, `skills`, `statusline-command.sh`, `tests` — are
symlinked into every account dir and resolve to byte-identical files whether
reached via `~/.claude/…` or `$CLAUDE_CONFIG_DIR/…`. Everything else under an
account dir (`handoffs/`, `briefs/`, `plans/`, `projects/`, `sessions/`,
`*-markers/`, `.*-active.d/`, `output-preferences.md`, logs, sentinels) is real
per-account state that stow never touches. Naming
`~/.claude/<stowed-entry>` is therefore harmless; naming
`~/.claude/<state-entry>` from a non-personal session touches the wrong
account.

**Notation, chosen per line by its consumer** — not per file. Several files mix
consumers within a few lines, so classification is line-by-line.

| Consumer | Notation | Why |
|---|---|---|
| Shell command anywhere (skill body, doc, `CLAUDE.md`, script) | `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/…"` | A shell evaluates it; already this repo's established form |
| Shell command whose resolver checks **both** locations | `test -f "…/x" \|\| test -f "$HOME/.claude/x"` | A `:-` fallback yields exactly one path; it cannot express a union (ledger row 12) |
| `Read`/`Write`/`Glob` tool path, or descriptive prose, in a skill/agent body | `<config-dir>/…` | No shell runs; the model resolves the placeholder itself (ledger row 4) |
| SKILL.md frontmatter `description:` | **unchanged, literal `~/.claude/…`** | Descriptions load standalone for trigger-matching before any body is read, so a placeholder has no definition in scope (ledger row 13) |
| Descriptive prose in `docs/**`, `README.md` | `<config-dir>/…`, with **one** caveat sentence per file | Readable mid-sentence; one definition per file, not one per site (ledger row 14) |

The `<config-dir>` caveat sentence states that it means `$CLAUDE_CONFIG_DIR`
when set and `~/.claude` otherwise — i.e. that both notations name the same
directory — so a file mixing the runnable and placeholder forms reads
coherently.

**Alternatives considered.** *Runnable form everywhere* — rejected: it does not
resolve in `Read`/`Write` paths (ledger row 4) and reads heavily mid-sentence.
*Keep `~/.claude` plus a caveat, everywhere* — rejected for executed paths,
where a model running a command never consults a disclaimer elsewhere in the
file; **accepted in part** for `docs/**` prose, which only humans read
narratively, hence the one-caveat-per-file rule above rather than a
per-site placeholder. *`${CLAUDE_SKILL_DIR}`* — rejected as already exhausted:
PR #582 deliberately scoped it to the two sites where a skill references its
own co-located file, and it resolves to the referencing file's own directory,
so it cannot express a config-dir path.

**Deliberately excluded** (each a regression if "fixed"): the two per-machine
roots registries (`transcript-config-dirs`,
`cleanup-merged-branches-roots`), which must not follow `CLAUDE_CONFIG_DIR` or
each account would get a private, mutually-invisible copy; the "Union, not
swap" guard-config fallbacks in six `deny-*`/`redact-*` hooks and `_lib.sh`;
stow mechanics in `install.sh`, `_stow_migration_lib.sh`, and
`relocate-claude-config.sh`; and 7 preserved records under `docs/reports/**`
and `docs/case-studies/**` (CLAUDE.md Axis 3).

### Assumption ledger

**Root problem:** this repo conflates stowed content with per-account state, so
sites naming `~/.claude/<state-entry>` read or write the personal account's
state from a non-personal session.

**Givens** (conditions beyond this plan's reach):

- Claude Code's permission matcher compares rules against literal input before
  any normalization, so a permission rule cannot be made env-var-aware.
  *(Platform boundary — the harness provides the matcher; nothing in this repo
  alters it. See ledger row 5.)*

That external per-account provisioning tooling is deliberately **not** a given:
it is a readable peer checkout on this machine, so changing it is within reach.
This plan declines to — see **Out of scope**.

| # | Assumption | Tag | anchors |
|---|---|---|---|
| 1 | Only `agents`, `CLAUDE.md`, `hooks`, `rules`, `scripts`, `settings.json`, `skills`, `statusline-command.sh`, `tests` are stowed into an account dir; every other entry is real per-account state | [verified: `git ls-files -- 'claude/.claude/*' \| awk -F/ '{print $3}' \| sort -u` run this session; that external provisioning tooling derives its own stow member list from the same git listing, read directly this session] | root |
| 2 | Non-personal accounts do receive the stowed tree, so `$CLAUDE_CONFIG_DIR/scripts/…` resolves | [verified: read that external per-account provisioning tooling directly this session] | root |
| 3 | Consequently `~/.claude/<stowed-entry>` and `$CLAUDE_CONFIG_DIR/<stowed-entry>` resolve to the same file, so those sites are accuracy work, not bugs | [verified: rows 1–2 together] | row1 |
| 4 | The `Read` tool expands `~` but does **not** expand `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` | [verified: ran both this session — the `~` form returned file content; the `${…}` form returned "File does not exist"] | root |
| 5 | Permission rules match literally before normalization, so `settings.json`'s `Bash(~/.claude/scripts/marker.sh …)` rules, SKILL.md invocation text, and `enforce-marker-script-shape.sh:295`'s anchor form **the marker triple** — three surfaces that must change together or not at all | [verified: Claude Code permissions documentation; `settings.json:16` and `enforce-marker-script-shape.sh:295` read directly] | root |
| 6 | **No** changes inside the marker triple — all six `marker.sh` invocation sites stay hardcoded, including `plan-review/SKILL.md:29` | [engineer-verified: rewriting `plan-review/SKILL.md:29` would break the literal `settings.json` allow-rule, is unrecognized by `enforce-marker-script-shape.sh:295`'s anchor, and the site sits inside a `HOOK_TEST_FIXTURE` block the hook-alignment suite executes verbatim] | row5 |
| 7 | `handoff`/`brief` writing to `~/.claude/handoffs`/`briefs` while `consume-durable-continuity-file-on-read.sh:111` matches `"$CONFIG_DIR"/handoffs/*` is a genuine read/write mismatch under a non-personal account | [verified: `handoff/SKILL.md:12`, `brief/SKILL.md:14`; hook match arm read in full; `handoffs`/`briefs` absent from row 1's stowed list] | root |
| 8 | Lighter primitive — the repo-wide caveat note, instead of editing sites — rejected for executed paths: a model running a command does not consult prose elsewhere in the file. Adopted for `docs/**` narrative prose, where the reader is human | [verified: the row-7 defect is a path the model writes, not prose a human reads] | root |
| 9 | Lighter primitive — reusing `${CLAUDE_SKILL_DIR}` — rejected: it resolves to the referencing file's own directory and cannot express a config-dir path; PR #582 already scoped it deliberately to two self-reference sites | [verified: `error-handling/SKILL.md:154` and `plan-review/SKILL.md:234` are its only sites] | root |
| 10 | `plugins/lovable-cloud/lib/token-path.sh:12` holds per-account state and should follow the config dir, using an **inline** `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` resolver | [verified: staff-platform-engineer traced all three sourcing sites — `new-migration` and `consume-migration-token.sh` do not source any `_lib.sh`; the plugin's own `hooks/_lib.sh` has no `_lib_config_dir`, so the shared resolver is unreachable from 2 of 3 sites] | row1 |
| 11 | In-flight `handoffs`/`briefs` written before this change become unreachable via the post-change resume path, so `resume-context.sh` needs a legacy-location fallback | [verified: `docs/design-decisions.md:227` states these files can sit unresumed for days; `handoff/SKILL.md:104` generates the resume command this plan changes] | row7 |
| 12 | A `${VAR:-default}` fallback evaluates to exactly one path and cannot express the two-location OR that `_lib_worktree_enforcement_active` (`_lib.sh:612-636`) and `_lib_autonomous_shipping_active` (`_lib.sh:655-674`) actually implement | [verified: both function bodies read this session — the former falls through to `[ -f "$HOME/.claude/worktree-required" ]` after its `config_dir` arm; the latter reads `[ -f "$config_dir/…" ] \|\| [ -f "$HOME/.claude/…" ]` on one line] | root |
| 13 | SKILL.md frontmatter `description:` fields keep their literal `~/.claude/…` paths | [verified: descriptions load standalone for trigger-matching before any body is read, so a `<config-dir>` placeholder would have no definition in scope; independently, `test_skills.py:1543-1558`'s `_DURABLE_WRITE_TARGETS` pins those exact literal substrings] | root |
| 14 | The `<config-dir>` definition is duplicated once per file rather than centralized, invoking CLAUDE.md's named "instructional prose that must let each file stand alone" DRY exception explicitly | [verified: CLAUDE.md §Engineering Judgment names that exception; a doc read in isolation has no access to a central glossary] | root |

## Critical files

### 1. Per-account state paths — the functional bugs

- **`claude/.claude/skills/handoff/SKILL.md`** — lines 6, 12, 21, 27, 94, 104,
  138. **Line 3 (frontmatter `description`) is deliberately unchanged** per
  ledger row 13. Line 12's `mkdir -p` is a shell command inside the
  `HOOK_TEST_FIXTURE: write-target` block → runnable form. Line 94's
  `~/.claude/*-markers/` and `~/.claude/.*-active.d/` globs and the
  `handoffs/` prose paths → `<config-dir>`. Line 104's resume-command template
  gains `<config-dir>` alongside its existing `<slug>` placeholder; add a
  pre-write checklist line requiring **both** placeholders be resolved to real
  values before the handoff file is written, since an unresolved token would
  ship a command that cannot run. Line 157 already uses the correct fallback
  form — leave it.
- **`claude/.claude/skills/brief/SKILL.md`** — lines 6, 14, 27, 33, 39, 96;
  line 3 unchanged. Identical treatment for `~/.claude/briefs/`.
- **`claude/.claude/scripts/resume-context.sh`** — add a legacy-location
  fallback per ledger row 11: when the resolved config dir differs from
  `$HOME/.claude`, also check `$HOME/.claude/{handoffs,briefs}/<name>` before
  reporting not-found. This mirrors the repo's existing "union, not swap"
  convention and is what keeps continuity files written before this change
  resumable after it. A docs-only caveat is insufficient — the affected user
  runs a command they already know rather than re-reading docs.
- **`claude/.claude/CLAUDE.md`** — five sites, classified per line, not as one
  bucket:
  - `:79` (`worktree-required`) and `:139` (`autonomous-shipping-required`,
    twice) — these document resolvers that check **both** locations
    (`_lib.sh:612-636`, `:655-674`). Per ledger row 12 the wording must express
    the union; `:139`'s literal `test -f` command becomes
    `test -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/autonomous-shipping-required" || test -f ~/.claude/autonomous-shipping-required`.
    A single-path substitution here would produce a false negative on a gate
    that authorizes autonomous shipping.
  - `:113` (`marker.sh clear-stale`) — a literal command to run → runnable
    form, **not** the placeholder.
  - `:112` (the `~/.claude/*-markers/*` never-write-by-hand prohibition) and
    `:135` (`output-preferences.md`) — descriptive prose → `<config-dir>`.
    `:112`'s prohibition must keep its unconditional force ("regardless of
    account"); it is prose reinforcing a content-hash gate, not the gate
    itself.
- **`plugins/lovable-cloud/lib/token-path.sh:12`** — `MIGRATION_TOKEN_DIR`
  routes through an **inline** `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` resolver
  per ledger row 10. All three sourcing sites share this one file and inherit
  the same environment, so the existing "byte-identical between generator and
  hooks" comment is preserved by the migration, not threatened by it.

### 2. Skill and agent instruction paths

- **The marker triple is untouched.** All six `marker.sh` invocation sites —
  in `respond-pr`, `plan-review`, `ready-for-review`,
  `ai-instruction-and-memory-files`, and `code-review` — keep their literal
  `~/.claude/scripts/marker.sh` form (ledger row 6). Add a one-line comment at
  the `plan-review/SKILL.md` site noting the set is uniform by design and
  pointing at the matching `settings.json` allow-rule, so a future contributor
  does not "fix" the apparent inconsistency and silently break gate matching.
  Note `plan-review/SKILL.md:28`'s `CONFIG_DIR` is **not** unused — line 30
  writes `"$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"`.
- **`ready-for-review/SKILL.md:93,95`** — the two `transcript-analysis.py`
  invocations take the runnable form, matching `transcript-analysis`,
  `transcript-narrative`, and `error-mode-analysis`. Bash sites, not inside a
  fixture block.
- **`plan-it/SKILL.md:85`** and **`handoff/SKILL.md:17`** — the
  `nudge-handoff-near-context-cap.sh --check` invocation; Bash → runnable form.
- **Auto-memory path references** — `~/.claude/projects/*/memory/` at
  `ai-instruction-and-memory-files/SKILL.md:6,137`, `code-review/SKILL.md:174`,
  `plan-review/SKILL.md:51,216`, and `skill-fidelity-reviewer.md:33`. All
  descriptive prose naming per-account state → `<config-dir>`. Line 6 is a
  frontmatter `description` and is therefore **unchanged** per ledger row 13.
- **Remaining agent bodies** — `code-writer.md:37,38,81`,
  `staff-backend-engineer.md:37,72`, `staff-sdet.md:19`,
  `staff-frontend-engineer.md:67`, `skill-fidelity-reviewer.md:66`. **Left as
  `~/.claude/…` deliberately.** Every one is a `Read` of a *stowed* entry
  (`skills/`, `agents/`), which resolves to the same file under any account
  (ledger row 3), and the shell form would not expand in a `Read` path (row 4).
  Recorded so the omission reads as a decision.
- **`plan-review/REFERENCES.md:30`** — stale quote of a pre-migration literal
  path; correct it to match the live `SKILL.md:234`.

### 3. Documentation prose (~181 sites)

`docs/hooks.md` (39), `docs/scripts.md` (24), `docs/security-hardening.md`
(22), `docs/design-decisions.md` (20), `docs/transcript-analysis.md` (13),
`docs/handoff-nudge.md` (11), `docs/private-project-redaction.md` (8),
`docs/commit-stall-block.md` (7), `docs/permission-prompt-tracking.md` (7),
`docs/skills.md` (5), `docs/error-mode-nudge.md` (5), `docs/auto-mode.md` (3),
`docs/cost-ledger.md` (2), `README.md` (37), `CONTRIBUTING.md` (1),
`SECURITY.md` (1).

**The classification test is the stowed/state split from Approach, stated
concretely so two implementers produce the same diff:** migrate a site only if
the path names `handoffs/`, `briefs/`, `plans/`, `projects/`, `sessions/`,
`*-markers/`, `.*-active.d/`, `output-preferences.md`, a log, or a sentinel —
i.e. per-account state. Leave any site naming one of the nine stowed top-level
entries, any stow-mechanics prose, the two roots registries, the union
fallbacks, and the 7 preserved records. Each file that gains a placeholder gets
exactly one caveat sentence (ledger row 14), not one per site.

### 4. Tests

- **`claude/.claude/skills/tests/test_skills.py` — update
  `test_handoff_and_brief_write_recipe_executes_to_durable_path`
  (:1561-1588).** This test *executes* the `HOOK_TEST_FIXTURE: write-target`
  bash block that section 1 rewrites, and its subprocess env
  (`_build_subprocess_env`, `claude/.claude/tests/helpers.py:66`) does
  `dict(os.environ)` overriding only `HOME` —
  it never clears `CLAUDE_CONFIG_DIR`. Once the recipe honors the fallback, any
  environment exporting `CLAUDE_CONFIG_DIR` makes the recipe create a directory
  outside `isolated_home`, either failing spuriously or writing into a real
  account dir. Add `monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)`,
  plus a second case setting it to a tmp path and asserting the recipe honors
  it — that second case is also the write half of the round-trip below. **Do
  this before landing the recipe edit.**
- **`_DURABLE_WRITE_TARGETS` (:1543-1558) is unchanged**, consistent with
  ledger row 13 leaving the frontmatter descriptions literal. Named here so the
  no-op is explicit rather than an oversight.
- **New regression contract** in the same file: no `SKILL.md`, agent body, or
  file under `docs/**`/`README.md`/`CONTRIBUTING.md`/`SECURITY.md` may contain
  a per-account-state path. Two details the first draft got wrong:
  - Match `(~|\$HOME|\$\{HOME\})/\.claude/` + state prefix, **not** `~/.claude/`
    alone — otherwise `$HOME/.claude/handoffs/…` reintroduces the identical bug
    untouched. Mirror the alternation already in
    `enforce-marker-script-shape.sh:295`.
  - Prefix list must include `projects/` and `sessions/` alongside `handoffs/`,
    `briefs/`, `plans/`, `*-markers/`, `.*-active.d/`,
    `output-preferences.md`.
  Needs a new `_AGENTS_DIR.glob("*.md")` enumeration helper — `_skill_body()`
  and `_agent_body()` are both by-name, and `_all_skill_md_paths()`
  (:1292-1297) covers only skills, so the "no agent body" half is otherwise
  aspirational. Requires an allowlist seam for the deliberately-kept
  frontmatter descriptions, **plus an explicit test case asserting the six
  `marker.sh` invocation sites stay un-migrated** — so the guard itself
  documents and enforces the permission-matcher/hook-anchor coupling (ledger
  rows 5–6) rather than leaving it to a plan-file note a future blanket sweep
  would not read.
- **`claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py`** —
  mirror `test_exact_dotclaude_dir_not_exempt` (:209-214) for the config-dir
  arm: a write path equal to exactly `$CLAUDE_CONFIG_DIR`, no trailing segment,
  asserts `deny`. `TestRequireWorktreeForFileWritesConfigDirExemption`
  (:251-275) has only trailing-segment and sibling-path cases.
- **`claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`** —
  add a case combining a config-dir-resolved marker with
  `.claude/worktree-optout`. `_lib.sh:629`'s config-dir-branch opt-out is a
  genuinely distinct code path from the legacy branch's at `:632-634`, and
  `TestMachineMarkerUnderConfigDir` (:913-972) never combines them.

### Reuse opportunities

`_lib_config_dir()` (`claude/.claude/hooks/_lib.sh:106`) and `config_dir()`
(`claude/.claude/scripts/_config_dir.py:22`) already exist and already cover
every runtime call site — no new resolver, except the inline one in
`token-path.sh` where the shared helper is unreachable (ledger row 10). The
runnable form is already used correctly in `transcript-analysis`,
`transcript-narrative`, `error-mode-analysis`, `pr-description`, and parts of
`plan-review` and `handoff`; copy those. `test_skills.py`'s
`_all_skill_md_paths()` and the existing
`TestTranscriptToolkitInterpreterPathContract` supply the shape for the new
guard.

## Verification

From the worktree (the contributor `.venv` lives at the main worktree root
only, three levels up):

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

Targeted checks:

- **New guard fails before it passes** — temporarily revert one
  `handoff/SKILL.md` line and confirm the contract red-fails. Use only this
  method: `_skill_body()` reads live files off disk, so running the contract
  "against `HEAD~`" would require a throwaway worktree checkout and is not
  directly executable.
- **Handoff round-trip** — the new `CLAUDE_CONFIG_DIR`-set case on
  `test_handoff_and_brief_write_recipe_executes_to_durable_path` proves the
  write lands under the resolved config dir;
  `test_consumes_handoff_under_config_dir_when_set`
  (`test_consume_durable_continuity_file_on_read.py:369`) already proves the
  consume side. Together they close the mismatch in ledger row 7, which no
  single existing test covers today.
- **Legacy-fallback smoke** — with `CLAUDE_CONFIG_DIR` set to a temp dir, place
  a handoff at `$HOME/.claude/handoffs/x-handoff.md` and confirm
  `resume-context.sh` still finds it (ledger row 11).
- **Docs sweep did not over-reach (required manual step, not skippable)** —
  re-run
  `git grep -nE '(~|\$HOME)/\.claude' -- docs README.md CONTRIBUTING.md SECURITY.md`
  and confirm every surviving occurrence falls in a documented
  keep-as-written category. Nothing in pytest/ruff/shellcheck validates prose
  classification; this pass is the only backstop for section 3's initial
  accuracy, with the new contract guarding it forward.

## Out of scope

- **The entire marker triple** — `settings.json` permission rules and hook
  `command` strings, the six SKILL.md `marker.sh` invocations, and
  `enforce-marker-script-shape.sh`'s regex. Engineer-decided (ledger row 6);
  they work under every account today, and any lockstep mismatch silently
  changes gate behavior.
- **`claude/.local/bin/*` shims** — the 9 wrappers `exec` a hardcoded
  `$HOME/.claude/scripts/…`. By ledger row 3 they reach the same file, and the
  script resolves its own state from the inherited `CLAUDE_CONFIG_DIR`, so
  behavior is correct today.
- **Accounts stowed from a diverged checkout** — the external provisioning
  tooling resolves its source checkout per account, so an account stowed from a
  checkout that has not pulled this fix keeps writing state to the personal
  tree until that checkout updates. Pre-existing propagation behavior, not
  introduced here, but it means "goes live on `git pull`" is per-checkout, not
  machine-wide.
- **The external per-account provisioning tooling** — within reach (a readable
  peer checkout on this machine), and its second stow is what makes the
  stowed/state split real. Declined because the defect is entirely expressible
  inside `claude-config`: no path this plan corrects needs a different
  provisioning shape, and editing a second repo would split one fix across two
  review surfaces and two PRs.
- **`~/.claude/transcript-config-dirs` and `cleanup-merged-branches-roots`** —
  deliberately per-machine and account-independent.
- **The "Union, not swap" guard-config fallbacks** — intentional dual-location
  checks in six `deny-*`/`redact-*` hooks and `_lib.sh`.
- **Stow mechanics** — `install.sh`, `_stow_migration_lib.sh`,
  `relocate-claude-config.sh` target the literal `$HOME` by definition.
- **Preserved records** — 7 sites under `docs/reports/**` and
  `docs/case-studies/**`, read-only per CLAUDE.md Axis 3.
- **`_config_dir.py`'s `TRANSCRIPT_CONFIG_DIRS_LABEL`** — a display-only
  literal labelling the genuinely `$HOME`-fixed roots file; correct as written.

**Known rollback note:** if `token-path.sh` is migrated and later reverted, a
migration token written under the new path becomes invisible to the reverted
`$HOME`-fixed lookup for a non-personal account, and
`validate-migration-filename.sh` denies a legitimate Write. Blast radius is one
plugin and one short-lived token; re-running `new-migration` recovers it.
