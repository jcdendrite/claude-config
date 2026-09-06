# Deterministic `$CLAUDE_CONFIG_DIR` resolution for script/hook call sites

## Context

Repo-wide, skill and CLAUDE.md prose invokes scripts and hooks under
`~/.claude/scripts/` and `~/.claude/hooks/` by shell-templating the config
directory inline — either a literal `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/<name>`
form, or a `<config-dir>` prose placeholder a model transcribes into that same
shape when it actually types the Bash command. The harness's worktree-isolation
Bash-tool guard refuses any single Bash statement containing a bare or
`${VAR:-default}`-form `$CLAUDE_CONFIG_DIR` reference ("Trigger B",
`docs/worktree-bash-guard.md`) — a real, observed failure: a `check-handoff.py`
invocation built this way was refused mid-session while writing a `/handoff`
file. PR #724 (merged six days earlier) fixed eight sites carrying this
exact shape, but its sweep grepped for the old, already-known
compound-recipe pattern rather than every templated or prose-implied
`$CLAUDE_CONFIG_DIR` script/hook reference. That grep missed roughly two
dozen more sites carrying the identical bug shape. Two of those missed
lines are inside `claude/.claude/CLAUDE.md` itself (130-131), violating the
same file's own "no `$CLAUDE_CONFIG_DIR` reference" convention stated two
sections earlier (88-96).

This plan fixes the shape at its root rather than patching each site's prose
again: scripts and hooks under `claude/.claude/scripts/` and
`claude/.claude/hooks/` are stow-shared and machine-invariant (every
git-tracked file under `claude/.claude/` resolves into the same shared
checkout regardless of which `$CLAUDE_CONFIG_DIR` account is active), so their
own path never needs runtime templating — it can be hardcoded, exactly as
`marker.sh` invocations already are everywhere in this repo. Where a script
also needs a config-dir-relative *data* path (a handoff file, a sentinel, a
briefs directory), the resolution moves inside the script itself (already
established via `_lib_config_dir` in `_lib.sh`), so no call site ever needs to
type `$CLAUDE_CONFIG_DIR` in Bash-tool command text again — closing the gap
class rather than the one reported instance of it.

## Step 4 — resolved clarifying questions

Two open questions from discovery were resolved this session:

1. **`plan-review/SKILL.md`'s current state (lines ~22-45) is not drift.**
   `git show 691fde9` (the merged `worktree-bash-guard-regression` plan, PR
   #724) shows this is exactly that plan's deliberate mechanism: resolve
   `<config-dir>` to a concrete value before the Write-tool call, never paste
   the literal `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` expression. A later
   commit (`d6d3930`) only reformatted it into a numbered list for
   comment-discipline reasons. Not in scope for this plan.

2. **`transcript-narrative`/`transcript-analysis`/`error-mode-analysis`'s
   templated interpreter-path form should revert to hardcoded, matching
   every other script/hook call site — this is the one substantive design
   reversal from this session's earlier framing.** `git show b8e9a15` (Aug
   10, twelve days before the worktree Bash-guard was even documented)
   introduced the templated form on the belief that a hardcoded
   `~/.claude/scripts/transcript-analysis.py` "broke under a relocated
   config dir." That belief does not hold on the engineer's own machine.
   `$HOME` is shared across every `claude_account` row, including
   non-`personal` ones — `ls -la ~/.local/state/claude-accounts/*/scripts`
   and `~/.claude/scripts` both resolve to the identical `claude-config`
   checkout symlink target `[verified: ls -la output, this session]`. That
   alone only shows reachability, not correct behavior, so it was tested
   directly rather than left inferred: running the single hardcoded
   `python3 ~/.claude/scripts/transcript-analysis.py sessions --paths`
   invocation with `CLAUDE_CONFIG_DIR` set to three different account
   directories in turn changed which account's own project directory was
   scanned first in each case (e.g. a project dir under the first account's
   own state directory, a different project dir under the second account's) —
   proving the script reads `$CLAUDE_CONFIG_DIR` from its
   process environment at run time regardless of which literal path launched
   it `[verified: executed sessions --paths under 3 CLAUDE_CONFIG_DIR values, this session]`.
   Hardcoding the interpreter path is therefore fully correct here — no
   wrapper script needed, a single guard-safe statement, matching
   `marker.sh` exactly — and the templated form was never buying the
   correctness it was written for; it only cost Trigger-B guard-safety. `docs/worktree-bash-guard.md`'s "already-accepted
   gap ... whose ~/.claude isn't a live install (e.g. a secondary account
   container)" caveat describes a hypothetical this engineer's own multi-account
   setup does not actually exhibit — `$CLAUDE_CONFIG_DIR` isolates
   account-scoped *state* (handoffs, briefs, transcripts, credentials,
   markers), never script/hook *content*, which is uniformly
   shared-checkout content on every account regardless of `$CLAUDE_CONFIG_DIR`.

   Consequence for Step 5: revert all ~20 script/hook call sites, including
   these 3 skills, to the plain hardcoded `~/.claude/scripts/<name>` /
   `~/.claude/hooks/<name>` form. Supersede
   `TestTranscriptToolkitInterpreterPathContract` (currently mandates the
   templated form) to instead assert the hardcoded form, consistent with
   every other script/hook call site. Also correct
   `TestPerAccountStatePathContract`'s error-message text (~line 2939 of
   `test_skills.py`), which currently endorses
   `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..."` as the correct form "for a
   runnable command" — that guidance predates the guard's discovery and is
   now known to be guard-unsafe; a config-dir-relative data-path runnable
   command should push resolution into the invoked script instead, per the
   `_lib_config_dir`/`config_dir()` convention already established for that
   class.

3. **A full repo-wide `$CLAUDE_CONFIG_DIR` sweep (`git grep` across
   `claude/.claude/skills/*/SKILL.md`, `claude/.claude/CLAUDE.md`, and
   `docs/*.md`) finds 61 sites, not ~20 — the extra sites split into two
   shapes, resolved this session with `git ls-files` evidence against every
   referenced target name:**
   - **4 `docs/*.md` sites mirror an already-in-scope script/hook
     invocation** (`docs/handoff-nudge.md:63,126`, `docs/scripts.md:35`,
     `docs/hooks.md:150-151`, referencing `nudge-handoff-near-context-cap.sh`,
     `transcript-analysis.py`, `marker.sh` — all confirmed git-tracked under
     `claude/.claude/`, hence stow-shared and machine-invariant
     `[verified: git ls-files, this session]`). In scope: hardcode, same as
     every other script/hook call site.
   - **~25 more `docs/*.md` sites** (`commit-stall-block.md`,
     `error-mode-nudge.md`, `permission-prompt-tracking.md`,
     `private-project-redaction.md`, `security-hardening.md`, plus the
     remaining `handoff-nudge.md`/`hooks.md` sentinel lines) show
     `touch`/`rm`/`cat >`/`chmod` commands against sentinels, logs, and
     user-populated config files (`autonomous-shipping-required`,
     `private-projects.md`, `credential-file-guard.md`, `pii-patterns.md`,
     and similar) — confirmed **not** git-tracked anywhere in the repo
     `[verified: git ls-files returned no match for any of these target
     names, this session]`, i.e. genuine per-account runtime state that
     differs by account, unlike scripts/hooks. `$CLAUDE_CONFIG_DIR`
     templating is the *correct* form here, not a bug — out of scope, no
     change. (These are also user-typed/`!`-escape documentation rather
     than model-constructed Bash-tool recipes, but the account-differing
     evidence is the load-bearing reason.)
   - **`docs/reports/2026-08-10-repo-quality-audit/findings.md:279`**
     flags `register-marketplace.sh`'s inline config-dir resolution as
     weaker than `_lib_config_dir()` — already fixed in PR #698
     `[verified: git log claude/.claude/scripts/register-marketplace.sh,
     this session]`, and the file is a dated audit record covered by
     CLAUDE.md's preserved-content rule regardless. Not touched.
   - **2 account-scoped `mkdir -p` sites** (`brief/SKILL.md:15`,
     `handoff/SKILL.md:13`, creating the per-account `briefs/`/`handoffs/`
     dirs) are a different bug shape than the script/hook call sites:
     hardcoding `~/.claude/briefs` would be actively wrong, since these
     dirs genuinely differ per account (unlike scripts/hooks)
     `[engineer-verified]`. Consequence for Step 5: add one new dedicated
     script (e.g. `ensure-account-dir.sh <name>`) that resolves
     `config_dir()` internally and `mkdir -p`'s it, matching the
     script-first convention already established for the script/hook call
     sites — the engineer's explicit choice over hand-rolling each site
     differently `[engineer-verified]`.

## Approach

Every script and hook under `claude/.claude/` is stow-shared and
machine-invariant, so its call sites stop templating the config directory
and name it literally: `~/.claude/scripts/<name>` and
`~/.claude/hooks/<name>`, exactly the form `marker.sh` and every
`settings.json` hook command already use. Where a call site needs a
config-dir-relative *data* path — the two per-account `mkdir -p` sites —
resolution moves inside a new one-line script that reads
`$CLAUDE_CONFIG_DIR` from its own process environment via
`_lib_config_dir`, so no call site ever types `$CLAUDE_CONFIG_DIR` into
Bash-tool text again. A superseded contract test then enforces the split
repo-wide: *stowed* paths (`scripts/`, `hooks/`) must be literal; *state*
paths (`handoffs/`, `briefs/`, sentinels, logs) must stay config-dir-relative,
which the existing `TestPerAccountStatePathContract` already guards from
the other direction.

### Assumption ledger

**Root problem.** Skill, CLAUDE.md, and doc prose instructs a model to
type `$CLAUDE_CONFIG_DIR` into Bash-tool command text in order to reach a
stow-shared script or hook — the shape the harness's worktree-isolation
guard refuses as Trigger B — when that path never varies by account and so
never needed templating at all.

**Givens** (fixed conditions this design cannot dissolve):

| # | Given | Tag |
|---|---|---|
| G1 | The worktree-isolation Bash guard is harness-native; no hook or script in this repo implements, intercepts, or disables it. Anthropic owns it. | `[verified: docs/worktree-bash-guard.md:3-9]` |
| G2 | The guard's refusals do not reproduce on demand as of the last re-verification, so this change cannot be validated by triggering a refusal before and after. The cause was never isolated and lies outside this repo. | `[verified: docs/worktree-bash-guard.md:115-129]` |
| G3 | Every git-tracked file under `claude/.claude/` resolves into one shared checkout regardless of the active account, so `~/.claude/scripts/<name>` names the same file under every `CLAUDE_CONFIG_DIR`, and the launched process still reads `$CLAUDE_CONFIG_DIR` from its own environment. Fixed by the stow layout and the operator's account provisioning, not by this plan. | `[engineer-verified]` (plan §Step 4.2: `sessions --paths` executed under three `CLAUDE_CONFIG_DIR` values, each scanning that account's own projects dir) |
| G4 | `handoffs/` and `briefs/` are genuinely per-account state, unlike `scripts/`/`hooks/`. | `[engineer-verified]`, corroborated `[verified: claude/.claude/skills/tests/test_skills.py:2839-2851 — _PER_ACCOUNT_STATE_PATH_RE matches ~/.claude/handoffs/ and ~/.claude/briefs/, and deliberately excludes scripts/hooks/ per its own :2829-2838 comment]` |
| G5 | `marker.sh`'s literal-path form is locked by a triple — `settings.json` allow-rules, `enforce-marker-script-shape.sh`'s anchor, and the SKILL.md literals — that changes in lockstep or not at all. Changing it is a separate decision spanning a gate hook and the permission surface. | `[verified: claude/.claude/skills/plan-review/SKILL.md:38; test_skills.py:2857-2872 and :2969-2977; claude/.claude/settings.json:4-19]` |

**Mechanisms:**

| # | Mechanism and justification | Tag | anchors |
|---|---|---|---|
| 1 | Rewrite all 19 templated `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/{scripts,hooks}/…` call sites to the literal `~/.claude/…` form. G3 makes the literal correct; the repo already treats it as the convention — `test_scripts_are_executable`'s docstring states "Every script under `claude/.claude/scripts/` is invoked by a hardcoded literal path (e.g. `~/.claude/scripts/foo.sh`)", and all 40+ `settings.json` hook commands use it. | `[verified: test_skills.py:1905-1917; settings.json:105-436; enumerated by git grep -nE 'CLAUDE_CONFIG_DIR[^}]*\}/(scripts\|hooks)/']` | root, G3 |
| 2 | Convert the two prose `<config-dir>/scripts/…` sites in `handoff/SKILL.md` (`marker.sh status`, `check-handoff.py`) to the same literal form. These are the shape the plan's Context names as the observed refusal: a model transcribes `<config-dir>` into the templated expression when it actually types the command. `marker.sh status` additionally has an exact-match allow-rule at `settings.json:19` that the transcribed form cannot match. | `[verified: handoff/SKILL.md:122,154; settings.json:19; CLAUDE.md Safety §"Don't add globs" — allow-rules are exact-match]` | root, G5 |
| 3 | Add `claude/.claude/scripts/ensure-account-dir.sh <name>`, accepting only `handoffs` or `briefs` and rejecting anything else, resolving `_lib_config_dir` internally and `mkdir -p`-ing the result. The engineer's explicit choice of one dedicated script over hand-rolling each site. The closed name set is least privilege — a free-form argument would let a caller `mkdir -p` an arbitrary traversal-relative path under the config dir. | `[engineer-verified]` (plan §Step 4.3, fourth bullet) | root, G4 |
| 4 | Over-powered-primitive check on row 3. Three lighter primitives, each rejected: **(a)** keep the inline `mkdir -p "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/handoffs"` — this is the exact Trigger-B shape the plan exists to remove, and violates `CLAUDE.md:88-96`'s own rule; **(b)** hardcode `mkdir -p ~/.claude/handoffs` — wrong under a relocated config dir per G4, and red-fails `_PER_ACCOUNT_STATE_PATH_RE`; **(c)** add an `ensure-dir` subcommand to `marker.sh` — one fewer file, but couples an unrelated concern to the one script the review-gate triple anchors on (G5), so it is the *heavier* choice despite the smaller diff. A fourth, letting the Write tool create missing parents, is not relied on: whether it does is unverified, and it would leave the two write-target execution tests with no subject. | `[verified: CLAUDE.md:88-96; test_skills.py:2839-2851]`, with the Write-tool parent-creation behavior `[unverified]` | row3 |
| 5 | Add one `_symlink_if_absent` line for the new script in `run_skill_command`, mirroring the two already there for `marker.sh` and `_lib.sh`. Without it, `test_handoff_and_brief_write_recipe_executes_to_durable_path` and `…_honors_config_dir_when_set` execute the recipe in an isolated `$HOME` where `~/.claude/scripts/` does not exist, and fail. With it, both tests pass unchanged — `_lib_config_dir` returns `$HOME/.claude` unset and the config dir when set, which is exactly what each test asserts. | `[verified: claude/.claude/tests/helpers.py:858-867; test_skills.py:1842-1897; _lib.sh:110-124]` | row3 |
| 6 | Supersede `TestTranscriptToolkitInterpreterPathContract` — which currently mandates the templated form for the three toolkit skills — with a repo-wide contract banning both the templated and the `<config-dir>/` prose form on any `scripts/`/`hooks/` path, across SKILL.md bodies, agent bodies, `claude/.claude/CLAUDE.md`, and the `_all_doc_paths()` corpus. This is the root-closing move: the earlier sweep missed sites because nothing enforced the shape. A narrow regex anchored on `/scripts/` or `/hooks/` immediately after the expansion leaves all ~25 legitimate per-account-state templating sites untouched — verified by running that exact grep repo-wide, which returns only in-scope sites. The new test reuses `_fence_excluded_by_marker` (mirroring `TestTriggerAFenceScan`, not `TestPerAccountStatePathContract`), because its corpus includes SKILL.md bodies with pytest-executed `HOOK_TEST_FIXTURE` fenced blocks that would otherwise false-positive against the regex. | `[verified: git grep -nE 'CLAUDE_CONFIG_DIR[^}]*\}/(scripts\|hooks)/' and git grep -nE 'config-dir[>}]?/(scripts\|hooks)/' — the complete match set is the in-scope list in Critical files, plus the two .claude/plans/*.md files, which _all_doc_paths() does not cover]` | root |
| 7 | Correct `TestPerAccountStatePathContract`'s failure text at `test_skills.py:2939`, which currently prescribes the templated form "for a runnable command." Point it at in-script `config_dir()`/`_lib_config_dir` resolution instead. Left as-is, the suite's own error message teaches the shape rows 1-6 remove. | `[verified: test_skills.py:2932-2940]` | root |
| 8 | Add `("handoff", "~/.claude/scripts/marker.sh status")` to `_MARKER_TRIPLE_SITES`, so row 2's newly-literal site is pinned like the other 14. | `[verified: test_skills.py:2857-2872]` | row2, G5 |
| 9 | Correct `docs/worktree-bash-guard.md:107-109`, which asserts an inherited "already-accepted gap … whose `~/.claude` isn't a live install (e.g. a secondary account container)." G3 disproves this for the setup it describes; leaving it in place would contradict the convention this plan makes mandatory. This paragraph describes a present property, not a past event, so CLAUDE.md's preserved-content rule (Axis 3) does not cover it — the Site sweep table above it does record a past sweep and is not touched. | `[verified: docs/worktree-bash-guard.md:57-83 vs :107-109; CLAUDE.md Scope-discipline Axis 3 decision test]` | root, G3 |
| 10 | No new rule text in `claude/.claude/CLAUDE.md`. Lines 88-96 already state "no `$CLAUDE_CONFIG_DIR` reference" and already name `~/.claude/scripts/` as where dedicated scripts live; lines 130-131 were a violation of that bullet, not a gap in it. Adding a restatement would be a second prose layer over a rule that is about to gain mechanical enforcement (row 6) — the compounding-layers tell. | `[verified: CLAUDE.md:88-96,130-131]` | root |

**Alternatives set aside at the whole-plan level.** A wrapper script per
call site (the shape PR #724 used for compound recipes) was rejected for
the script/hook call-site class: with G3 established, a literal path is
already a single statically-verifiable statement, so a wrapper would add
~20 indirection points and buy nothing. Fixing only the reported
`check-handoff.py` site was rejected because that is what produced this
plan — the previous sweep grepped for the compound-recipe pattern rather
than the reference itself, and missed two dozen sites carrying the same
shape.

## Critical files

**Single `code-writer` dispatch — do not split.** The two natural phases
(the hardcoding sweep, and the new script plus its two `mkdir -p` callers)
overlap on `handoff/SKILL.md`, `docs/scripts.md`, and `test_skills.py`, and
both need the same stowed-vs-state distinction restated in their prompts.
Parallel dispatches share this worktree and clobber silently rather than
conflict.

**Templated call sites → literal `~/.claude/…`** (rows 1, 2):

- `claude/.claude/skills/transcript-narrative/SKILL.md` — lines 11, 21, 56, 59, 62, 65, 68
- `claude/.claude/skills/transcript-analysis/SKILL.md` — lines 105, 108, 111, 114, 118, 121, plus line 16 (`The toolkit lives at \`scripts/transcript-analysis.py\` under the active Claude Code config dir (\`$CLAUDE_CONFIG_DIR\`, or \`~/.claude\`)` → `The toolkit lives at \`~/.claude/scripts/transcript-analysis.py\``) — this is the script's own install location, the same script/hook call-site class as every other converted site, not state. Line 89 alone stays templated: it names `cost-ledger --record`'s *data* write target (`$CLAUDE_CONFIG_DIR/cost-ledger.md`), a genuine per-account state path.
- `claude/.claude/skills/error-mode-analysis/SKILL.md` — lines 13, 132
- `claude/.claude/skills/handoff/SKILL.md` — line 18 (`nudge-handoff-near-context-cap.sh --check`), line 122 (`<config-dir>/scripts/marker.sh status`), line 154 (`<config-dir>/scripts/check-handoff.py <path>`)
- `claude/.claude/skills/plan-it/SKILL.md` — line 137 (`nudge-handoff-near-context-cap.sh --check`)
- `claude/.claude/CLAUDE.md` — line 130 (`marker.sh clear-stale`), line 131 (`review-ledger.sh show`)
- `docs/hooks.md` — lines 150, 151 (`marker.sh clear-stale`, `--dry-run`)
- `docs/handoff-nudge.md` — line 63 (`nudge-handoff-near-context-cap.sh --check`), line 126 (`transcript-analysis.py spend-over-threshold`)
- `docs/scripts.md` — line 35 (the inline `Invoked directly:` example in the `transcript-analysis.py` entry)

That list is exhaustive for the shape; both narrow greps in row 6 return nothing else outside `.claude/plans/`.

**New file** (row 3):

- `claude/.claude/scripts/ensure-account-dir.sh` — commit with `git add --chmod=+x` (`test_skills.py:1905` parametrizes over `SCRIPTS_DIR.glob("*.sh")`). **Reuse:** copy the shape of `claude/.claude/scripts/handoff-record-conversion.sh` verbatim — `#!/bin/bash`, `set -euo pipefail`, `# shellcheck source=../hooks/_lib.sh`, `. "$(dirname "$0")/../hooks/_lib.sh"`, then `config_dir=$(_lib_config_dir) || <exit>`. Capture-and-check is a load-bearing contract, not style: bare `"$(_lib_config_dir)/handoffs"` collapses to a root-anchored `/handoffs` on resolver failure (`_lib.sh:104-109`). Unlike that script's best-effort telemetry, this one must exit non-zero on a resolution failure or an unknown name — the caller is about to write a file into the directory. **Argument validation must be exact-match, never substring or prefix:** a `case "$1" in handoffs|briefs) ... ;; *) exit 1 ;; esac` (or equivalent `[[ "$1" == "handoffs" ]]`-style equality checks), with each accepted branch hardcoding its own literal `mkdir -p "$config_dir/handoffs"` / `mkdir -p "$config_dir/briefs"` target rather than interpolating `$1` into the path. This rules out a prefix-match or path-building shape that would admit a traversal-shaped or otherwise unexpected argument; reject with a non-zero exit on anything else, including an empty argument. Follow `.claude/rules/shell-script-conventions.md`.

**Callers of the new script** (row 3):

- `claude/.claude/skills/handoff/SKILL.md` — line 13, inside the `HOOK_TEST_FIXTURE: write-target` fenced block; replace the `mkdir -p` line with `~/.claude/scripts/ensure-account-dir.sh handoffs`. Leave the fixture comment on line 11 intact (documented stable anchor; the test re-reads the block from here).
- `claude/.claude/skills/brief/SKILL.md` — line 15, same fixture and same substitution with `briefs`.

**Tests** (rows 5-8):

- `claude/.claude/tests/helpers.py` — `run_skill_command` at lines 858-867: add a third `_symlink_if_absent(isolated_home / ".claude" / "scripts" / "ensure-account-dir.sh", SCRIPTS_DIR / "ensure-account-dir.sh")`. The existing `_lib.sh` symlink on line 861 already satisfies the new script's `../hooks/_lib.sh` source, the same way it does for `marker.sh`.
- `claude/.claude/skills/tests/test_skills.py`:
  - lines 2801-2814 — supersede `TestTranscriptToolkitInterpreterPathContract` with the repo-wide stowed-path contract. **Reuse:** `_all_skill_md_paths()`, `_all_agent_md_paths()` (:2875), `_all_doc_paths()` (:2897), `_strip_yaml_frontmatter()` (:2884), and `_GLOBAL_CLAUDE_MD` are all already in the file and already exclude `docs/reports/**` and `docs/case-studies/**`. Place the new class beside `TestPerAccountStatePathContract` so the two halves of the invariant read together. This new contract test reuses `_fence_excluded_by_marker` (the same fenced-block exclusion `TestTriggerAFenceScan` applies), because unlike `TestPerAccountStatePathContract` — which scans prose only and has no fenced-recipe false positives to guard against — this contract's corpus includes SKILL.md bodies with `HOOK_TEST_FIXTURE` fenced blocks (e.g. `handoff/SKILL.md`'s write-target fixture) that pytest executes directly; without the exclusion, a legitimately-templated fixture line would false-positive against the row-6 regex.
  - line 2782-2786 — `_TRANSCRIPT_TOOLKIT_SKILLS` becomes unused if nothing else references it; delete or repurpose rather than leave dead.
  - line 2939 — replace the `'or "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..." (a runnable command)'` clause with a pointer to in-script `config_dir()`/`_lib_config_dir` resolution.
  - lines 2857-2872 — add the `handoff` / `marker.sh status` row to `_MARKER_TRIPLE_SITES`.
  - lines 1842-1897 — the two write-target execution tests need no edit once helpers.py has the symlink; confirm rather than modify.
  - **line 919** — `test_handoff_section5_directs_running_marker_status_and_committing_flagged_work` pins the literal substring `"Run \`<config-dir>/scripts/marker.sh status\` and paste its output verbatim"` against `handoff/SKILL.md`. Row 2 converts that exact source line (`handoff/SKILL.md:122`) to the `~/.claude/...` literal form; update this assertion's expected substring in lockstep, or the test red-fails against the new plan-review-caught blocking finding.
- New `claude/.claude/scripts/tests/test_ensure_account_dir.sh`-equivalent file, `claude/.claude/scripts/tests/test_ensure_account_dir.py` (mirror `test_handoff_record_conversion.py`'s convention of exercising a helper script via subprocess against an isolated `$HOME`/`$CLAUDE_CONFIG_DIR`): assert `handoffs` and `briefs` each succeed and create the expected directory under a fake config dir; assert an invalid name, an empty argument, and a traversal-shaped argument (e.g. `../etc`) each exit non-zero and create nothing. **Also assert a `_lib_config_dir` resolution failure** (`CLAUDE_CONFIG_DIR` set to a relative path, or unset with `$HOME` also unset/empty — `_lib.sh:104-109`'s failure mode) exits non-zero and creates nothing: the New file bullet above calls this failure mode load-bearing (a bare, uncaptured `"$(_lib_config_dir)/handoffs"` collapses to a root-anchored `/handoffs` on this exact failure), so it needs its own case rather than relying on the three argument-validation cases to exercise it incidentally.

**Docs** (rows 9, plus row 3's documentation home):

- `docs/worktree-bash-guard.md` — lines 107-109 only. Do not edit the Site sweep table (lines 57-83), which records a past sweep.
- `docs/scripts.md` — add an `ensure-account-dir.sh` bullet in the existing alphabetical-ish script list, naming the two accepted arguments and the `_lib_config_dir` resolution. No test enforces script-doc coverage (unlike `test_hook_documented_in_hooks_md` for hooks), so this is a single-source-of-truth obligation, not a gate.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

`select-tests.py` will report `global-trigger: claude/.claude/tests/helpers.py` and run the full suite on its own — that path is in `GLOBAL_TRIGGER_PATHS` (`select-tests.py:182-186`). That is CLAUDE.md's documented case 1 ("`select-tests.py` itself selected the full suite for this diff"), not a licence to widen by hand; run the command above unchanged and let it choose.

Then confirm the specific contracts this diff moves:

- Both narrow sweeps return only `.claude/plans/*.md` matches: `git grep -nE 'CLAUDE_CONFIG_DIR[^}]*\}/(scripts|hooks)/'` and `git grep -nE 'config-dir[>}]?/(scripts|hooks)/'`.
- The new contract test red-fails on a reintroduced site — revert one line of `transcript-narrative/SKILL.md` locally, confirm the failure, restore. A contract test that has never been seen to fail is untested.
- One smoke run of an edited literal, since G2 means the guard itself cannot confirm anything: `python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo`.

## Out of scope

- **`plan-review/SKILL.md:24-45`** — the `<config-dir>`-resolve-before-Write pattern is a deliberate mechanism from PR #724 for a Write-tool *path argument*, not a Bash command, and line 42's `CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"` sits inside a `HOOK_TEST_FIXTURE` block that pytest executes rather than an agent types. Neither matches the row-6 regex. Untouched.
- **The ~25 per-account state sites** in `docs/commit-stall-block.md`, `docs/error-mode-nudge.md`, `docs/permission-prompt-tracking.md`, `docs/private-project-redaction.md`, `docs/security-hardening.md`, and the sentinel/log lines of `docs/handoff-nudge.md` and `docs/hooks.md` — templating is correct there and `TestPerAccountStatePathContract` already requires it.
- **`docs/reports/2026-08-10-repo-quality-audit/findings.md:279`** — stale finding, fixed in PR #698, and a dated audit record under CLAUDE.md's preserved-content rule. `_all_doc_paths()` already excludes `docs/reports/**`, so the row-6 contract will not reach it.
- **`settings.json` permission rules.** This plan could add `Bash(~/.claude/scripts/ensure-account-dir.sh handoffs)` and its `briefs` twin, but deliberately does not: the posture is unchanged either way, since `permissions.allow` has no `Bash(mkdir …)` rule today `[verified: settings.json:3-23]`, so the sites being replaced were never allow-listed. Adding rules is a separate security-surface change that trips `ask-review-permissions.sh` and calls for `/review-permissions`. Raise it as a follow-up if the prompts prove noisy in practice.
- **The `marker.sh` triple itself** (G5) — settings.json allow-rules, `enforce-marker-script-shape.sh`'s anchor, and the SKILL.md literals stay exactly as they are. This plan moves sites *toward* that form, never changes the form.
- **`docs/worktree-bash-guard.md`'s Site sweep table and "Current status" section** — records of past investigations.

