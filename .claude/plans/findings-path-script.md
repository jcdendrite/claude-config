# Extract the `findings_path` recipe into a shared script

## Context

Give the mechanical half of the `findings_path` recipe one authoritative home — a script under `claude/.claude/scripts/` — with every dispatcher calling it instead of restating shell expressions.

`claude/.claude/skills/code-review/SKILL.md:293` and `claude/.claude/skills/plan-review/ROUTING.md:47-64` both carry the `findings_path` recipe — the `agent-reviews/<agent-name>-<epoch>-<slug>.md` path template, the idempotent `info/exclude` append, and the synchronous-spawn/read-back rules — as prose, guarded only by a token-presence sync test (`test_findings_path_recipe_tokens_present_in_code_review_and_plan_review`, `claude/.claude/skills/tests/test_skills.py:3900`). The `/plan-review` wiring (PR #848, commit `f27eb2e`) created the second copy, and filing this follow-up was a named deliverable of that PR so a third dispatcher would not later face three copies. Exploration found the third dispatcher already exists. Closes https://github.com/jcdendrite/claude-config/issues/846.

## Approach

Move the mechanical half of the `findings_path` recipe into one no-argument shell script, `claude/.claude/scripts/findings-path-suffix.sh`, which idempotently adds `agent-reviews/` to the repo's ignore list and prints a single `<epoch>-<slug>` line. All three dispatchers that currently carry the recipe — `code-review/SKILL.md:293`, `plan-review/ROUTING.md:47-64`, and `ready-for-review/SKILL.md:94` — call it once per round and reuse the printed suffix across every spawn in that round. What stays in each skill body is the contract statement only: `findings_path: agent-reviews/<agent-name>-<suffix>.md`, the synchronous-spawn rule, the read-back protocol, and the inline-fallback rule. The executed fixture block in `ROUTING.md` is deleted and its test moves to `scripts/tests/`, where it executes the real script instead of a documentation copy of it.

**Three sites, not two.** `ready-for-review/SKILL.md:94` already carries the `info/exclude` append verbatim and a `findings_path: agent-reviews/skill-fidelity-reviewer-<epoch>-<slug>.md` line that defers its derivation with "(same convention as `/code-review`)". The ticket's stated motivation — so a third `findings_path` dispatcher does not later face three copies — is already past tense; the third dispatcher exists. Migrating it in the same change is not scope creep, it is the ticket's own premise applied to the facts on disk. Leaving it out would also strand its cross-reference, which after this change points at a `/code-review` paragraph that no longer states the convention.

### Interface verdict

**No arguments.** Three independent reasons:

1. **Call count per round.** `/code-review` and `/plan-review` spawn several reviewers in one round. An agent-name argument makes this one Bash call *per reviewer*; a no-arg script is one call per round whose output is reused N times. That asymmetry is the real cost, and it is invisible in the allow-rule framing.
2. **Allow-rule surface.** Exact-match rules cannot cover an open-ended argument list, and `CLAUDE.md` §Safety bars the glob that would. Arg-taking means either ~10 enumerated rules that go stale as the roster changes, or a permission prompt on every review round for every stow consumer.
3. **The `marker.sh` disanalogy holds.** `marker.sh` takes arguments because `enforce-marker-script-shape.sh` anchors on the invocation shape to stop agents skirting a gate — a gate-release-authority concern. `findings-path-suffix.sh` releases no gate and grants no authority, so there is nothing for a shape hook to anchor on.

**Does this remove the duplication or relocate part of it?** It relocates part of it, deliberately, and the part it relocates is the right part. After the change, each body retains exactly two tokens — the script path and one template string. That is down from five verbatim shell expressions across two files plus a seven-line executable fence. The residual is a *contract statement* (what the dispatcher tells the reviewer to do) rather than a *mechanical recipe* (how a value is computed) — the same line drawn when deciding the spawn/read-back prose stays duplicated.

**The script prints a bare suffix (`1756900000-GH-846-findings-pat`), not a path.** A script that printed `agent-reviews/<epoch>-<slug>` would put the agent name *inside* the printed string, forcing the caller to splice mid-string and the skill body to document that splice — restating the template the change exists to remove. With a bare suffix the body states `agent-reviews/<agent-name>-<suffix>.md`, a normal template with one appended value.

**Rejected: printing the full path with a `{agent}` placeholder.** This would put the entire template in the script and remove it from all three bodies — genuinely more consolidation. It is rejected because a forgotten substitution produces a literal `agent-reviews/{agent}-<suffix>.md` that passes `deny-reviewer-tree-mutation.sh` (it is under `agent-reviews/`) and that *every* reviewer in the round writes to, silently clobbering each other's findings. Losing a security reviewer's findings is the exact failure class the file-based-output contract exists to prevent. Cheaper duplication beats a silent-loss failure mode.

### Script contract

```
#!/usr/bin/env bash
# Prepare this round's reviewer findings destination and print its <epoch>-<slug> suffix.
# Exit 0: suffix printed on stdout as one line. A failed ignore-list update warns on
#         stderr and still exits 0 -- the update is best-effort, not the enforcement point.
# Exit 1: not a git repository, or HEAD does not resolve -- no stdout.
```

**Step order is load-bearing: guard, guard, append, derive, print.** Both guards precede the append so a failure cannot leave the ignore entry written and then abort — the ordering defect the current recipe has, since it appends before deriving the slug.

1. **Not-a-git-repo guard.** `if ! git rev-parse --git-dir >/dev/null 2>&1; then` → stderr message, `exit 1`. An explicit guard, not inherited propagation: under bare `set -e` a failing `git rev-parse` aborts with git's *own* status (128 for a `rev-parse` fatal), not the documented 1. `branch-divergence-status.sh:17-20` is the in-repo idiom.
2. **HEAD-resolves guard.** Same shape, `git rev-parse --verify HEAD`. Unborn HEAD is not an edge case: a consumer runs `git init`, writes the first change, and invokes `/code-review` before the first commit — an ordinary bootstrap. Today's recipe hits git's raw `fatal: ambiguous argument 'HEAD'` there.
3. **Ignore-list update, best-effort.** `grep -qxF "agent-reviews/" "$EXCLUDE_FILE" 2>/dev/null || echo "agent-reviews/" >> "$EXCLUDE_FILE" 2>/dev/null || echo "<script>: warning: could not update $EXCLUDE_FILE" >&2` — a **three-way** chain ending in a command that cannot realistically fail. A two-way `A || B` chain aborts under `set -e` when `B` fails, which would skip the derivation and print nothing: the exact opposite of the documented contract. `pr-cost-section.sh:18` (`|| mode=""`) and `branch-divergence-status.sh:64` (`|| true`) are the two in-repo instances of this idiom, the latter commented for precisely this reason.
4. **Slug.** `git rev-parse --abbrev-ref HEAD` piped through `tr '/' '-'`, then `tr -cd 'A-Za-z0-9-'` to filter to a safe character class, then `cut -c1-20`, in that order. Detached HEAD yields the slug `HEAD` and is not special-cased, matching today's recipe. The slug is a human-readable hint, not an identifier -- a branch name with zero characters in `[A-Za-z0-9-]` (e.g. an all-Cyrillic name) filters to an empty slug, which is valid output.
5. **Print** `<epoch>-<slug>` as one line on stdout, nothing else.

`set -euo pipefail`, every expansion quoted, no functions (so no `local`), per `.claude/rules/shell-script-conventions.md`. `pipefail` is required, not incidental: without it a failing `git rev-parse --abbrev-ref HEAD` inside the slug pipeline is masked by `tr`/`cut` succeeding, silently producing an empty slug instead of failing.

### One script or two

**One script.** The justification is not that the two operations are related — they are not — but that they share a single call site and a strict happens-before ordering: the ignore entry must exist before the first reviewer write, and the suffix is needed at that same moment. Collapsing them is what removes multi-step choreography from the skill body; splitting them puts it straight back as "run A, then run B," plus a second allow-rule and a second test module. The script's contract reads as one job: *prepare a findings destination for this round and print its address*.

Lighter primitives considered for the append specifically, and why each fails:

- **Drop the append, rely on `.gitignore`.** Fails for every stow consumer: `.gitignore:42` covers this repo only, and consumer repos have no committed entry, so the first round in each would degrade to inline fallback — the mechanism's whole value.
- **Move it to `install.sh`.** Fails on scope: `install.sh` runs once in the stow repo, but `info/exclude` is per-consumer-repo and there is no per-repo install step.
- **Move it into the reviewer agents.** Fails: `skill-fidelity-reviewer` and `comment-discipline-reviewer` hold no `Bash` at all, and the dispatcher needs the suffix before dispatch regardless, since it is an input to the prompt.
- **Make `deny-reviewer-tree-mutation.sh` append instead of deny.** Fails as an over-powered primitive and a compounding defensive layer — a deny-hook silently mutating the repo it guards, to fix the condition it exists to detect.

### The two existing tests

**`test_findings_path_recipe_tokens_present_in_code_review_and_plan_review` (`test_skills.py:3900`) — retarget, keep, and split in two.** Three of its five pinned tokens leave both bodies. Replace `_FINDINGS_PATH_RECIPE_TOKENS` with the two that survive as shared contract: `agent-reviews/<agent-name>-<suffix>.md` and ``not `run_in_background` ``. Then add a second, parameterized test whose contract has two halves — the script path is **present** in each of the three dispatcher bodies, and all three retired expressions are **absent**: `$(date +%s)`, the `rev-parse --abbrev-ref` pipeline, and `git rev-parse --git-path info/exclude`. `test_ready_for_review_step3_never_produces_a_staged_diff` (`test_skills.py:3426-3439`) is the in-repo precedent for the present-AND-absent shape; `TestPrDescriptionCostSectionWiring.test_declares_account_scoped_mode_gate` (`test_skills.py:831-833`) is the precedent for asserting a body invokes a named script.

**Scope the absence half over every skill body, not the three known dispatchers.** The precedent's negative half works because `git diff --cached` is a singular idiom; the three retired expressions are not — `date '+%s'`, `git branch --show-current`, `sed` for `tr`, `${var:0:20}` for `cut -c1-20`, and `--path-format=absolute` are all live near-synonyms, so a literal-substring test proves those three *spellings* are gone, not the mechanism. Paraphrase is out of reach for a string assertion and is accepted (see Out of scope). The reachable half is the fourth-file risk: run the absence assertion across `_all_skill_md_paths()` plus `ROUTING.md`, so the recipe reappearing in a *new* dispatcher fails rather than passing unseen. That is the likelier regression shape — copy-paste into a new file, not a deliberate rewording.

Two positive tests rather than a three-file × N-token matrix because `ready-for-review` legitimately differs: it spawns one named reviewer, so its template is `agent-reviews/skill-fidelity-reviewer-<suffix>.md`, and it says "synchronously" rather than carrying the ``not `run_in_background` `` phrase. Each test then has a statable contract instead of a table of exceptions.

**`test_findings_path_fixture_recipe_is_idempotent_and_matches_documented_path_shape` (`test_skills.py:3994`) — move to `claude/.claude/scripts/tests/test_findings_path_suffix.py`.** Only the subject changes — `subprocess.run(["bash", "-c", recipe])` becomes a direct invocation of the real script with `cwd=<seeded tmp repo>`. Unlike `test_pr_cost_section.py` the script needs no fixture-tree copy, since it sources no `_lib.sh`. The seeded-repo setup and its rationale (unborn HEAD makes `rev-parse --abbrev-ref` exit 128) move with the docstring; `scripts/tests/conftest.py` already supplies git-repo scaffolding, so check it before hand-rolling the seed.

**Two assertions do not transfer unchanged, and the difference is the point.** The old fenced recipe printed the full `agent-reviews/${AGENT_NAME}-${EPOCH}-${SLUG}.md`; the script prints a bare suffix. So the shape regex necessarily narrows to `^[0-9]+-[A-Za-z0-9-]{1,20}$`, and `env = dict(os.environ, AGENT_NAME="staff-backend-engineer")` becomes dead setup the migration must **delete**, not carry forward — a no-arg script never reads it, and a copy-pasted `AGENT_NAME` would silently appear to test something it does not. What transfers unchanged is the substance: two-run idempotency, the `feature/a-very-long-branch-name-here` checkout, third-run idempotency across branches, and the exact-truncation equality forcing `tr` and `cut` to both run in order.

**Add a recombination test — this is coverage the split otherwise loses.** The old test executed the literal skill-body text, so it implicitly proved the body's instructions and the mechanism were one object. After the split each half is validated alone and nothing proves they compose. Build the final path from the script's real stdout plus the template string the retargeted wiring test pins as a surviving contract token, and assert the result against the *original* full-path regex. It reuses the same seeded repo and invocation, so it costs no new fixture. Without it, a template edit (wrong separator, dropped `.md`, agent name after the suffix) or a stdout regression (trailing whitespace, an extra diagnostic line) breaks only when an agent splices the two, which no test would see.

**Add three failure/edge-path tests.** None of the script's documented contracts is currently exercised, and shellcheck cannot catch any of them — they are `set -e` interaction bugs, not syntactic anti-patterns.

- **Not a git repo** — invoke against a bare `tmp_path`, assert exit 1 (not merely nonzero) and empty stdout. Needs no scaffolding at all.
- **Unborn HEAD** — seed a repo with no commit, assert exit 1 and, critically, that `info/exclude` was **not** written: the guards precede the append precisely so no side effect lands before a failure.
- **Read-only ignore file** — make `info/exclude` or its parent unwritable, assert exit 0, non-empty stderr, and a suffix still on stdout. This is the one whose mishandling is total rather than degraded: no suffix means no dispatch, and no skill body documents a fallback for the suffix step itself failing — they only document a reviewer's *write* failing.

**Add a linked-worktree test.** The migrated test only ever runs against a plain `git init` repo, but every real invocation in this repo happens from inside a linked worktree, where `.git` is a file. Ledger row 7 records that worktree gitdir resolution was asserted wrongly once already and re-verified only by a one-off manual probe; an automated test is what stops the next regression needing another probe. `scripts/tests/conftest.py` already exposes `_make_worktree(repo, branch_name, wt_path)`, used by `test_pr_cost_section.py`, `test_pr_diff_against_base.py`, and `test_skill_fidelity_report.py` — assert the append lands in the **common** repo's `.git/info/exclude`, not a worktree-local path.

Consequences to carry out explicitly, since each is a deletion an implementer can miss:

- `ROUTING.md:55`'s `HOOK_TEST_FIXTURE: findings-path-recipe` marker comment and the fenced block at `:56-62` are deleted together.
- `extract_skill_command(_ROUTING_MD_PATH, "findings-path-recipe")` is the *only* call site for that fixture id (`test_skills.py:4031`); the helper itself has ~20 other callers and stays.
- Keeping the fixture block alongside the script is not an option — that is three copies of the mechanics, with the fixture free to drift from the artifact it documents.
- `TestTriggerAFenceScan` scans only files named `SKILL.md`, so it never saw this block; removing it changes nothing there.

### settings.json

**One rule, in the stow-package `claude/.claude/settings.json`.** `/code-review` and `/plan-review` run in every repo a consumer works in, so the rule must live in the file that stows to `~/.claude/settings.json` — alongside the 17 `marker.sh` rules and the `cleanup-*.sh` rules, which sit there for exactly this reason. The worktree-root `.claude/settings.json` holds four rules, all claude-config-specific commands (`ruff check claude/.claude/`, `select-tests.py`, one pytest path); putting it there would leave every other repo prompting on every review round.

Exact-match, tilde-form, matching every existing script rule: `Bash(~/.claude/scripts/findings-path-suffix.sh)`. Because the script takes no arguments, this grant authorizes exactly one immutable command string with zero caller-controlled surface — strictly narrower than the existing `marker.sh` rules, which each carry two argument words.

The lighter alternative — no rule, accept the prompt — is rejected on friction: this fires once per review round, in every repo, for every stow consumer.

`/review-permissions` runs on this change. It is hook-prompted rather than merely plan-scheduled: `ask-review-permissions.sh` is wired at `Edit|Write|MultiEdit` (`claude/.claude/settings.json:316`) and fires on the settings write itself.

### docs/scripts.md

**Add an entry.** The other scripts with no `docs/scripts.md` bullet are each invoked from one skill body that explains the call in place. This one is invoked from three, and its effects are described in `docs/design-decisions.md` §12, which currently names a shell expression rather than an artifact — a reader arriving from §12 has no path to the thing that does the work. `marker.sh`'s entry (`docs/scripts.md:54`) is the shape: name the invoking skills, state what it does, point at the enforcing/explaining doc. Keep it to that and let §12 own the mechanism — do not restate the template, the idempotency rationale, or the fallback behavior, or the entry becomes a fifth prose site.

**Two prose sites go stale and are fixed in the same change**, both descriptions of current behavior rather than preserved records (`CLAUDE.md` §Scope discipline Axis 3):

- `docs/design-decisions.md:186` — "The dispatching skill idempotently appends `agent-reviews/` to `$(git rev-parse --git-path info/exclude)`" becomes an expression that appears in no skill. Swap the expression for the script name. Alongside it, `:188`'s "`/plan-review` … is a second dispatcher" reads as a census that omits `ready-for-review`; add it in the same clause, since the change makes all three call one script and the omission becomes conspicuous.
- `.gitignore:36-38` — "The general-repo mechanism is `code-review/SKILL.md`'s idempotent append to `$(git rev-parse --git-path info/exclude)`" points at a body that no longer performs it. One-line repoint to the script.

### Assumption ledger

**Root:** Three skill bodies each restate the same `findings_path` shell recipe as prose, so a fourth dispatcher — or an edit to any one of the three — forks a mechanism whose only guard is a token-presence test.

**Givens:**

- **G1.** The `findings_path` mechanism's shape (path template, per-round ignore append, synchronous spawn, read-back, inline fallback) is fixed. Reason: eight reviewer agent bodies implement it via their `### File-based output` sections, so re-deciding it is a decision outside this plan. `[verified: docs/design-decisions.md:184-188]`
- **G2.** Permission allow-rules here are exact-match only; `Bash(script *)` globs are barred. Reason: a repo-owner-level policy (`CLAUDE.md` §Safety) this plan does not get to relax. `[verified: claude/.claude/settings.json:3-34 — 21 rules, zero globs]`
- **G3.** The worktree Bash guard is harness-native; no repo change can intercept it. Reason: vendor-owned. `[verified: docs/worktree-bash-guard.md:6-7]` — **but its documented "does not reproduce on demand" status is stale.** On 2026-09-03, from a linked worktree, the guard refused a compound `grep … "$(git rev-parse --git-path info/exclude)"` call with "names git in a form too complex to verify," against a doc recording zero refusals across seven shapes. `[unverified as a repeatable check — a single dated observation, and the doc's own status section explains why the guard cannot be triggered deterministically; the Incidental edit below is what makes it durable]`

**Rows:**

| # | Assumption | Tag | Anchors |
|---|---|---|---|
| 1 | `ready-for-review/SKILL.md:94` is a third `findings_path` dispatcher carrying the `info/exclude` append verbatim and deferring its derivation to `/code-review` by cross-reference. | `[verified: claude/.claude/skills/ready-for-review/SKILL.md:94]` | root |
| 2 | One script holds both the append and the derivation, rather than two. They share one call site and a happens-before ordering, and splitting reinstates the multi-step choreography in the body. Lighter primitives rejected above. | `[verified: .gitignore:35-41 documents the append as best-effort; docs/design-decisions.md:186 names deny-reviewer-tree-mutation.sh as the enforcement point]` | root |
| 3 | No-argument interface, printing the bare `<epoch>-<slug>` suffix. See "Interface verdict" reasons 1–3 for the argument-vs-no-argument case; the bare-suffix print form is anchored separately by the clobber failure mode in the "Rejected: printing the full path" bullet. | `[engineer-verified]` for the no-arg direction | root, G2 |
| 4 | A new executable in every consumer's `~/.claude/scripts/` plus a permission grant in every consumer's `~/.claude/settings.json` is heavier than the task strictly requires, so three lighter primitives were checked: (a) byte-compare the paragraphs across files, as `TestReconciliationBlockConsistency` does — fails, the surrounding prose legitimately differs per dispatcher, which the existing test's own docstring records, and it addresses neither the guard exposure nor the third copy; (b) a shared cross-skill reference file — fails, `docs/design-decisions.md` §4 "No shared skill partials" and `.claude/rules/skill-and-agent-self-review.md` permit co-located auxiliaries only within one skill, and it was rejected in Step 4; (c) a hook appending at spawn time — fails as a compounding defensive layer on top of `deny-reviewer-tree-mutation.sh`, and still leaves the derivation in prose. | `[verified: test_skills.py:3906-3912; docs/design-decisions.md:39-44; .claude/rules/skill-and-agent-self-review.md]` | root |
| 5 | The migration preserves the *ignore-check* semantics — `grep -qxF` + `>>`, not a switch to `git check-ignore`. Changing that inside a consolidation would make the tests unable to distinguish a move from a regression. **This is not a blanket behavior-preservation claim.** Adding `set -euo pipefail`, which `.claude/rules/shell-script-conventions.md` requires of every script here, is itself a behavior change: today's fenced recipe carries no `set -e`, so a failed append continues to the derivation, while a naive two-way OR chain under `set -e` would abort. The three-way chain in step 3 restores the current graceful-continue behavior deliberately, rather than inheriting an abort. | `[verified: ROUTING.md:56-62 carries no set -e; `set -euo pipefail; false \|\| false; echo unreached` exits 1 without printing]` | row2 |
| 11 | The documented `exit 1` contract needs the explicit guards in Script contract steps 1–2; bare `set -e` propagates git's own status (128 on a `rev-parse` fatal), not 1. Unborn HEAD is a realistic trigger — a consumer's first `/code-review` in a freshly `git init`'d repo — not an edge case. | `[verified: test_skills.py:3994's own docstring records the exit-128 behavior; `V=$(false)` aborts inheriting the failing status]` | row2 |
| 12 | Concurrency across the worktrees sharing one common-gitdir `info/exclude` (`git worktree list` for the current count) is a real check-then-append race but an inconsequential one: `O_APPEND` makes each write atomic, so the worst case is a duplicate identical ignore line, which git pattern-matching ignores. Not sourcing `_lib.sh`'s `append_line_locked` is therefore correct — the dependency buys crash-safe locking for a cosmetic outcome. | `[verified: claude/.claude/hooks/_lib.sh's append_line_locked doc comment reaches the same conclusion for its own dedup case]` | row2 |
| 13 | Shipping the allow-rule with no `PreToolUse:Bash` hook and no arg validation is consistent with the repo's own script policy, but not because that policy prescribes it — its carve-out names a different layer. `review-permissions/REFERENCES.md` § "Allowing privileged scripts" states three layers (exact-string allow entries; a dedicated hook; the script's own `case` whitelist) and carves out "for trivial scripts that call only absolute paths and do not exec untrusted input, **layer 3** alone may be sufficient." Layers 2 and 3 both exist to constrain an argument surface, and this script has none: zero arguments, a fixed `git`/`grep`/`tr`/`cut` command set, and one externally-influenced value (the branch name) that is piped to stdout, never `eval`'d or re-shelled. So neither layer has anything to validate, and layer 1 is the entire configuration. | `[verified: claude/.claude/skills/review-permissions/REFERENCES.md:84-101]` | root, G2 |
| 14 | The allow-rule is maximally restrictive at the global scope it ships to. `/review-permissions` checklist item 19 holds stowed (`~/.claude/settings.json`) rules to a higher bar than project rules; an exact-string, zero-argument entry cannot be narrowed further. An `LD_PRELOAD=` prefix (item 4) or the tilde-free absolute form (item 5) both produce a different command string, so they fail to match and prompt — the safe direction. | `[verified: claude/.claude/skills/review-permissions/SKILL.md items 1-23, walked against the drafted rule; re-runnable as `/review-permissions`]` | root, G2 |
| 6 | "Move the append into the reviewer agents" fails, but not because reviewers lack `Bash` — the seven `staff-*` agents and `ciso-reviewer` all hold it. It fails because `skill-fidelity-reviewer` and `comment-discipline-reviewer` hold only `Read, Grep, Glob, Write`, and because the dispatcher must derive the suffix before dispatch regardless, since it is an input to the prompt. | `[verified: claude/.claude/agents/*.md frontmatter `tools:` lines, all 13 agents read this session]` | row2 |
| 7 | `info/exclude` resolves to the **common** gitdir under worktree enforcement, so the append is functional in a linked worktree and worth preserving. Contradicts `.claude/plans/fidelity-reviewer-undecidable-artifacts.md:118`, which is stale and must not be inherited. | `[verified: re-runnable from any linked worktree — `git rev-parse --git-path info/exclude` resolves under `--git-common-dir`, not `--git-dir`; `git check-ignore -v agent-reviews/probe.md` names `.gitignore:42`]` | row2 |
| 8 | `code-review/SKILL.md`'s length cap is **500**, not 400, and the file is at 470. `limit_for()` gives 500 to `code-review/SKILL.md`, `plan-review/SKILL.md`, and `plan-review/ROUTING.md`; the default is 200. This change only shrinks all three bodies. | `[verified: claude/.claude/hooks/check-skill-length.sh:65-74]` | root |
| 9 | The allow-rule belongs in the stowed `claude/.claude/settings.json` because the invoking skills run in every repo, not only this one. | `[verified: claude/.claude/settings.json:3-34 vs .claude/settings.json:9-14]` | root, G2 |
| 10 | `docs/design-decisions.md:186` and `.gitignore:36-38` are descriptions of current behavior, not preserved records, so Axis 3 permits editing them and they become stale if not edited. | `[verified: both cite the shell expression as the live mechanism]` | root |

## Critical files

**Single `code-writer` dispatch.** The work does not partition: the skill-body edits and the test retargeting both depend on the script's exact name and stdout contract, which would have to be restated in a second dispatch prompt — `plan-it` Step 5's explicit do-not-split condition.

**Create**

- `claude/.claude/scripts/findings-path-suffix.sh` — the no-arg script above. Must be committed executable; `test_scripts_are_executable` (skills/tests) is what catches a missed `chmod +x`. Reuse: none needed — unlike `pr-cost-section.sh` it requires no `hooks/_lib.sh` source, since it resolves no config dir.
- `claude/.claude/scripts/tests/test_findings_path_suffix.py` — the migrated idempotency/shape test. Reuse: `scripts/tests/conftest.py`'s git-repo scaffolding and `_base_test_env()` before hand-rolling a seeded repo; `test_pr_cost_section.py` for the `subprocess.run(..., capture_output=True, text=True, check=False)` + `class Test<Scenario>` structure. Per `.claude/rules/test-tree-packaging.md`, confirm `claude/.claude/scripts/__init__.py` and `claude/.claude/scripts/tests/__init__.py` already exist rather than adding either.

**Modify**

- `claude/.claude/skills/code-review/SKILL.md:293` — replace the derivation clause with the script call and `agent-reviews/<agent-name>-<suffix>.md`; state that the script also adds `agent-reviews/` to the repo's ignore list, without naming a path or expression. The spawn-synchronously rule, read-back protocol, inline-fallback sentence, and contract-enforcement note are unchanged.
- `claude/.claude/skills/plan-review/ROUTING.md:47-64` — same substitution; collapse the `<agent-name>`/`epoch`/`slug` bullet list to a one-line gloss; delete the `HOOK_TEST_FIXTURE` marker at `:55` and the fenced block at `:56-62`. The contract-gate paragraph and the ≤2K-token budget paragraph at `:64` stay.
- `claude/.claude/skills/ready-for-review/SKILL.md:94` — replace the inline append with the script call, and `findings_path: agent-reviews/skill-fidelity-reviewer-<epoch>-<slug>.md (same convention as /code-review)` with `agent-reviews/skill-fidelity-reviewer-<suffix>.md` plus the suffix's source. The dangling cross-reference goes with it.
- `claude/.claude/skills/tests/test_skills.py:3884-3925` — retarget `_FINDINGS_PATH_RECIPE_TOKENS` to the two surviving contract tokens; add the parameterized three-file present-AND-absent test. Delete `:3994-4083` and the now-unused fixture extraction; keep `_ROUTING_MD_PATH` itself (`:3880`), still used by `_routing_reviewer_agent_names()`. Rewrite the block comment at `:3875-3878` ("Three tests…") to match the new count and subjects.
- `claude/.claude/settings.json` — add `"Bash(~/.claude/scripts/findings-path-suffix.sh)"` to `permissions.allow`.
- `docs/scripts.md` — one entry in the `- **<basename>** — <prose>` shape of the `marker.sh` entry at `:54`: what it does, the three invoking skills, a pointer to §12.
- `docs/design-decisions.md:186` (and `:188`) — swap the shell expression for the script name; add `ready-for-review` to the dispatcher census.
- `.gitignore:36-38` — repoint "code-review/SKILL.md's idempotent append to `$(git rev-parse --git-path info/exclude)`" at the script.

**Incidental edits** (`CLAUDE.md` §Scope discipline Axis 1, bucket 2 — small, non-cosmetic, kept with rationale in the PR body)

- `docs/worktree-bash-guard.md`, "Current status" section — append one dated line recording the 2026-09-03 refusal of a compound `grep … "$(git rev-parse --git-path info/exclude)"` call. The section currently states zero refusals across seven shapes and that readers "should not expect to trigger any row of the taxonomy table deterministically." Append rather than rewrite: the section is a dated investigation record, which Axis 3 makes read-only. In scope because G3 cites that status, and the refusal landed on the exact shape this change removes.

## Verification

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then run exactly what it prints. Expect it to widen: a `.sh` under `claude/.claude/scripts/` pulls in `hooks/tests` (for `test_shellcheck.py`) and `skills/tests` (for `test_scripts_are_executable`) on top of `scripts/tests`, and `test_skills.py` is itself a non-`SKILL.md` file under `skills/**`. Any widening it does on its own is case 1 in this repo's `CLAUDE.md` — not a licence to widen by hand.
2. `.venv/bin/ruff check claude/.claude/` — the new and edited test modules.
3. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — auto-discovery means no registration step, and `.shellcheckrc` sets no `severity=`, so the floor is `style`.
4. Behavioral spot-check in this worktree: run the script twice and confirm one line matching `^[0-9]+-[A-Za-z0-9-]{1,20}$`, and that the common gitdir's `info/exclude` (`git rev-parse --git-common-dir`) still holds exactly one `agent-reviews/` line afterward — it already carries one at line 7, so a second entry means the grep-check regressed. This is the happy path only; the three failure paths and the linked-worktree shape are covered by tests (above), not by this step.
5. Grep the three dispatcher bodies for the three retired expressions; expect zero hits. This duplicates the new test deliberately — it is the one assertion whose failure means the ticket was not actually closed.
6. `/code-review`, which dispatches `/skill-review` (hook-enforced by `require-skill-review.sh` — two `SKILL.md` files are edited, so it fires regardless of `ROUTING.md`'s own status under the hook). `/review-permissions` on the `settings.json` edit; `ask-review-permissions.sh` will prompt for it at write time.

`docs/scripts.md`, `docs/design-decisions.md`, and `.gitignore` map to no test — no doc-coverage test exists for `docs/scripts.md`. Their accuracy is a review-time check, and `/pr-description`'s claim-verification step is where the dispatcher-census edit gets re-derived.

## Out of scope

- **Widening `TestTriggerAFenceScan` to `ROUTING.md`.** After this change `ROUTING.md` carries no fenced blocks at all, so the widening would assert nothing today. Whether the scan's file set should match its docstring's "corpus" claim is a separate coverage question.
- **Making the absence assertion paraphrase-proof.** A string test cannot distinguish `$(date +%s)` from `date '+%s'` or `cut -c1-20` from `${var:0:20}`. Widening the scan to every skill body (in scope, above) closes the likelier fourth-file shape; catching a deliberate rewording needs semantic scanning, which is a different tool.
- **Rewriting `docs/worktree-bash-guard.md`'s investigation record.** Its "Current status" section is a dated re-test record (Axis 3 preserved content), so the change appends one dated observation rather than editing the finding — see Incidental edits.
- **Switching the append's condition from `grep -qxF` on `info/exclude` to `git check-ignore`.** More accurate (it would skip the redundant append in repos like this one whose `.gitignore:42` already covers it) but a behavior change inside a consolidation, which would leave the tests unable to distinguish the move from a regression.
- **A shared reference file owning the full `findings_path` contract prose.** Rejected in Step 4, and independently barred by `docs/design-decisions.md` §4.
- **A `docs/scripts.md` coverage test** for the other scripts lacking an entry. `claude/.claude/scripts/tests/test_transcript_analysis_architecture_doc.py` is the precedent shape if it is ever wanted.
- **Reducing `code-review/SKILL.md` below its 500-line cap** beyond the incidental shrink this produces.
- **Making the `findings_path`-contract check a dispatch-time gate.** `docs/design-decisions.md:188` names this as an open gap across both dispatchers; this change neither closes nor widens it.
