# Worktree-isolation Bash-tool guard: replace per-recipe prose splitting with dedicated scripts

## Context

The Claude Code harness's worktree-isolation Bash-tool guard — a harness-native
static check, not anything this repo's own hooks implement — refuses far more
command shapes, at far more sites, than this repo currently documents or
guards against. Three live skill recipes carry a "fixed in 2.1.224" claim
that a fresh re-bisection on the currently-installed CLI (2.1.238) shows is
false; a follow-up sweep for the same bug shape found five more skill files
(plus a CLAUDE.md example and, once the sweep was widened to `plugins/`, one
hook-script-skeleton example) carrying the identical, previously-undocumented
defect; and this session's bisection surfaced two entirely new trigger
shapes the prior investigation never tested.

**This plan's approach changed mid-session.** A first draft implemented the
prior investigation's own fix shape — split every affected recipe into
single-statement Bash calls, with a `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`
primary form and a plain-`$HOME/.claude` fallback retried on refusal — across
all nine sites. Two things happened before that draft was committed:

1. Re-verifying the fenced-block wording live, from this worktree, found
   that **none of the five documented triggers reproduce anymore** —
   including the exact original pre-fix compound recipe, run verbatim. Same
   Claude Code version (2.1.238) as the bisection that first found them.
   This doesn't disprove the bisection (the evidence for each trigger was
   independently reproduced 2+ times when the bisection was run), but it
   means the guard's refusals are not a stable, on-demand-reproducible
   static check — treat the trigger table as historical evidence the bug
   shape existed, not a live guarantee it fires on the next attempt.
2. The engineer reviewed the prose-split draft and asked whether a
   deterministic script wouldn't be a materially better fix than
   recipe-level prose choreography the model has to re-derive correctly
   every time a skill runs. It is, for reasons independent of whether the
   guard is currently reproducing (see Approach) — this plan was revised to
   that design before any of the first draft was committed.

## Approach

**Collapse each multi-step recipe into one dedicated script, invoked by one
literal Bash-tool call with no `$(...)`, no assignment, and no
`$CLAUDE_CONFIG_DIR` reference at the call site — instead of documenting a
multi-call, fallback-branching choreography in prose at each site.**

This is a better fix than the prose-split draft on four independent grounds,
none of which depend on the guard actively misbehaving right now:

1. **It matches the refusal message's own framing.** "This command is too
   complex to *verify that it stays inside the worktree*" describes a
   static-verification problem. Inline `$(...)`, unquoted variable-built
   paths, and `$CLAUDE_CONFIG_DIR` references are exactly what makes a
   command hard to verify statically; a single `~/.claude/scripts/foo.sh
   arg1 arg2` — fixed literal path, literal args, nothing to substitute — is
   about as staticly-verifiable as a command gets.
2. **It closes the residual `$CLAUDE_CONFIG_DIR` gap the prose-split draft
   accepted as unclosable.** `$CLAUDE_CONFIG_DIR` can't appear in Bash-tool
   command *text* under Trigger B, but a script reads it from its own
   process environment internally — never as literal text in the invocation
   — so the gap disappears rather than degrading to a `$HOME/.claude`-only
   fallback.
3. **The repo already validates this pattern.** `respond-pr`'s own
   session-id lookup already uses `~/.claude/scripts/marker.sh activate
   respond-pr` instead of inline Bash — the prior investigation's own sweep
   noted this is *why* that specific site was never affected by the bug in
   the first place. `marker.sh` invocations are also already hardcoded to
   the literal `~/.claude/scripts/marker.sh` path repo-wide (not a
   `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`-prefixed form) — `settings.json`'s
   allow-rule and `enforce-marker-script-shape.sh` both anchor to that exact
   literal string. Every new script this plan adds follows the same
   convention: invoked via the hardcoded `~/.claude/scripts/<name>.sh` path,
   never a `$CLAUDE_CONFIG_DIR`-prefixed one, so Trigger B never appears at
   any call site. This inherits `marker.sh`'s own known, already-accepted
   gap for a session running under a non-default `$CLAUDE_CONFIG_DIR` whose
   `~/.claude` isn't a live install (e.g. a secondary account container) —
   not a new gap this plan introduces.
4. **It's more DRY.** The prose-split draft restated the
   "`$CLAUDE_CONFIG_DIR`-aware form first, plain-`$HOME/.claude` fallback on
   refusal" two-tier dance at five separate sites. A script makes that logic
   one tested, shellchecked artifact instead of five copies that can
   silently drift out of sync with each other.

**Not every site gets a new script.** `plan-review/SKILL.md`'s
plan-mode-path site writes its target via the `Write` tool, not Bash — the
`Write` tool's `file_path` argument is a literal string, never
shell-expanded, so no script invocation applies there at all; see that
site's entry in Critical Files for the (script-independent) fix it still
needs. Every other site collapses a multi-statement Bash recipe into a
single-purpose script.

### Investigation (re-derived this session; historical record — see caveat above)

**Version check.** `claude --version` reports `2.1.238` — above the
`2.1.224` the prior investigation's Postscript cited as the fix version.
Ruled out: this is not an outdated-install false alarm.

**Re-bisection.** Re-ran the prior plan's bisection table verbatim as
literal, separate Bash calls from this worktree, plus every new row the
brief flagged, plus follow-up isolation calls to pin down exactly which
token was responsible when a result surprised. Headline results, each
independently reproduced 2+ times at the time:

- **Trigger A** (`$(...)` assigned to a variable, used in a later statement
  in the same call) — refused. Confirmed with a git-free reproduction
  (`TESTVAR=$(echo hello); [ -n "$TESTVAR" ] && printf ...`) to rule out git
  specifically being the cause.
- **Trigger B** (any reference to `$CLAUDE_CONFIG_DIR`, bare or
  `${VAR:-default}`, in an otherwise-trivial single statement) — refused.
- **Trigger C (new)** — an unquoted variable-built path argument refused;
  the identical path double-quoted succeeded. Isolated via a 2×2: quoted
  `$HOME`+`$PPID` succeeded (twice, non-flaky); unquoted `$HOME`+`$PPID`
  refused; unquoted `~`+`$PPID` refused; unquoted `~`+literal-number
  (no `$PPID`) succeeded — quoting, not `~` vs `$HOME`, was the operative
  variable.
- **Trigger D (new)** — `$PPID` used as a whole/standalone argument
  refused regardless of quoting: `echo $PPID`, `head -n1 "$PPID"`, and
  `head -n1 $PPID` all refused, even though `$PPID` embedded as a suffix
  inside a longer double-quoted string (Trigger C's non-refusing case)
  succeeded. `$HOME` alone did not trigger this — the shape was specific to
  `$PPID`.
- **Trigger E (new)** — a bare `$(git ...)` substitution refused even with
  no assignment and no later statement (`echo "$(git rev-parse --git-path
  info/exclude)"`, and the same substitution inside `grep`).

**Mid-implementation re-verification finding.** Re-running all five triggers
plus the exact original pre-fix `handoff` compound recipe, live, from this
same worktree, at the same `claude --version`, found **zero refusals across
seven independent shapes** — a full reversal from the bisection above. No
config, mode, or environment difference between the two test passes was
identified. `docs/worktree-bash-guard.md` records both the original
bisection and this finding, and states plainly that the guard's refusals do
not currently reproduce on demand — readers should not expect to trigger
any of the table's rows deterministically.

**Changelog cross-check.** The Postscript's Anthropic-changelog citation for
"the fix" doesn't hold up under a verbatim re-fetch of the primary source:

- **2.1.224** (the cited version) has no worktree/isolation/Bash-guard entry
  at all in its actual changelog section.
- The quoted text the Postscript attributed to 2.1.224 — "Fixed
  worktree-isolated sessions and their subagents being able to run
  destructive git commands against the main checkout; isolation now applies
  to file edits and Bash in every session type" — is real, but belongs to
  **2.1.222**, two versions earlier, and reads as an isolation-*bypass* fix
  (subagents evading isolation, addressed by widening the guard's scope),
  not a false-positive fix that would narrow which commands it refuses.
- Worktree/git-isolation changelog entries appear at 2.1.210, 2.1.216,
  2.1.222, 2.1.229, and 2.1.232, each closing one specific bypass or
  false-positive shape. 2.1.238 itself ships "Fixed worktree-isolation Bash
  refusals telling you to remove a redirect when the command had none," a
  narrow false-positive fix for one specific spurious-message case.

**Conclusion.** This is not a regression of a fixed bug — Triggers A and B
were never actually fixed by 2.1.224; the Postscript's "no longer
reproduces" observation was real but its causal attribution was wrong.
Given the guard's history of narrow, incremental, apparently
non-deterministic patches, a repo-side mitigation that doesn't depend on
correctly predicting the guard's current behavior — a script the guard
never has to parse for complexity at all — is the durable lever, more so
than it would be even if the bisection still reproduced live today.

**Sweep for other affected sites, widened to include `plugins/`.** The
prior plan's sweep covered `claude/.claude/skills/`, `claude/.claude/CLAUDE.md`,
`claude/.claude/rules/`, and `docs/`. This session's sweep found three more
files the prior sweep missed there, plus a fourth once the search surface
was widened to `plugins/*/skills/*/SKILL.md` (a directory the `_all_skill_md_paths()`
test helper already scans, but the prior manual `grep` sweep never included):

| File | Shape found |
|---|---|
| `ready-for-review/SKILL.md:72-73` | Trigger A (already has a stale note) |
| `ready-for-review/SKILL.md:92-96` | Trigger A + Trigger B — **new, undocumented** |
| `pr-description/SKILL.md:78-86` | Trigger A (×2 assignments) + Trigger B — **new, undocumented**, distinct from the one site (line 88) that does carry a stale note |
| `pr-description/SKILL.md:87-90` | Trigger A (already has a stale note) |
| `handoff/SKILL.md:161-165` | Trigger A + Trigger B (already has a stale note) |
| `git-feature-branch-sync/SKILL.md:29-31` | Trigger A — **new, undocumented**. Shared by three callers (`check-branch-divergence.sh` hook, `/ready-for-review`, `/respond-pr`), but only the two skill-driven callers run it through the Bash tool — the hook executes its own script directly, outside this guard's reach entirely. |
| `respond-pr/SKILL.md:111-118` | Trigger A — **new, undocumented** |
| `plan-review/SKILL.md:31` (this skill's own conditional plan-mode recipe) | Trigger A + Trigger B — **new, undocumented**, but low-traffic (only runs when a plan-mode reminder is present) |

A ninth site: `claude/.claude/CLAUDE.md`'s own "Shipping" section (the
autonomous-shipping-sentinel check) contains `test -f
"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/autonomous-shipping-required" || test -f
~/.claude/autonomous-shipping-required` — a live Trigger-B site inside
CLAUDE.md's own prose, not a SKILL.md fence.

A **tenth site**, found only once `test_skills.py`'s new regression test
(Critical Files, below) was run against the full `_all_skill_md_paths()`
corpus rather than the narrower manual-grep surface: `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md:52,54`
(`reason_json=$(printf ... | jq -Rs . 2>/dev/null)` inside a
`#!/bin/bash`-shebang'd fenced block). This is **not actually an affected
site** — the fenced block is a canonical hook-script skeleton *example*
(documentation of what a hook script's own file content should look like),
never typed into an agent's Bash tool call. This plan adds an explicit
`HOOK_SCRIPT_CONTENT_EXAMPLE` marker comment ahead of that block, and the
regression test excludes any fenced block carrying that marker (or the
existing `HOOK_TEST_FIXTURE` one) for exactly this reason; see that test's
Critical Files entry.

Per this repo's CLAUDE.md "Audit structural siblings before scoping a fix
narrowly": the bug shape is identical across all eight live SKILL.md sites
plus the CLAUDE.md site, so the fix applies to all nine, not just the three
the prior investigation already knew about.

**Alternatives considered and rejected:**
- *Ship the prose-split draft as originally implemented* — rejected per
  Approach's four grounds above; superseded before commit.
- *Per-skill-recipe fixes only, no shared script or general convention* —
  this session's own ad-hoc reproduction attempts (the brief's own table)
  all happened in orchestrator Bash calls with no fixed recipe to script in
  advance; a recipe-scoped fix alone, however implemented, would not have
  prevented those. The CLAUDE.md convention bullet (mechanism 4, below)
  stays in the plan for exactly this reason — it's the mechanism reaching
  ad-hoc calls no script can pre-cover.
- *A repo hook that pre-validates or rewrites Bash commands before this
  guard sees them* — rejected as a heavier, more invasive mechanism than
  the task needs: hooks fire on tool calls this session issues and cannot
  intercept or alter the harness's own internal Bash-tool evaluation.
- *One shared multi-subcommand script (mirroring `marker.sh` /
  `transcript-analysis.py`) instead of seven single-purpose scripts* —
  considered; rejected because the seven sites don't share deep internal
  state the way `marker.sh`'s subcommands do (all keyed off one
  session-marker lifecycle) — they're independent operations for
  independent skills. Single-purpose scripts match the majority convention
  already in `claude/.claude/scripts/` (`resume-context.sh`,
  `cleanup-merged-branches.sh`, `register-marketplace.sh`, etc.).
- *Fully unify `branch-divergence-status.sh` (new) with
  `check-branch-divergence.sh` (existing hook)* — rejected: the hook's
  contract is SessionStart-specific (silent-on-clean, JSON
  `hookSpecificOutput` envelope, always exit 0); the skill-facing script
  needs to always report status in plain text so the calling skill can
  branch on it. Forcing one script to serve both would need an
  output-mode flag threaded through every branch — a worse abstraction than
  the ~15 lines of git-command duplication it would remove. Both already
  point at the same canonical recipe in `git-feature-branch-sync/SKILL.md`
  as their shared source of truth for *what* the detection logic is, even
  though each has its own implementation of *how* to report it.
- *`install.sh` version-gates the minimum Claude Code version* — the
  engineer's preferred long-term shape once a fix version exists, but not
  buildable today: no version, including 2.1.238, has ever been observed to
  fix Triggers A/B. Recorded as Out of scope, unchanged by this revision.

### Assumption ledger

**Root problem:** skill recipes across this repo, and this repo's shared
CLAUDE.md, understate how often and how broadly the harness's
worktree-isolation Bash-tool guard refuses ordinary commands in a
worktree-anchored session (which, under this repo's own worktree
enforcement, is every session working in it) — both in claimed severity and
in coverage — and the fix already drafted for this (prose-documented
per-call splitting) is itself a weaker primitive than a dedicated script
would be, independent of the coverage gap.

**Givens:**
- G1. The refusal originates in the Claude Code harness's own Bash-tool
  guard, not in any script this repo owns. [verified: fresh `grep -rn "too
  complex to verify\|isolated in the worktree" claude/.claude/hooks/
  claude/ docs/` this session returns zero matches for the harness's
  refusal text]
- G2. The guard is not scoped to this repo — carried over from the prior
  investigation's engineer-verified finding
  (`.claude/plans/handoff-nudge-log-worktree-path.md`, G2), unchanged by
  this session. [engineer-verified, prior session]
- G3. `marker.sh`'s hardcoded-`~/.claude/scripts/marker.sh`-path convention
  is deliberate, existing repo policy, not something this plan invents.
  [verified: `claude/.claude/skills/plan-review/SKILL.md`'s own prose states
  this explicitly — "`marker.sh` invocations stay hardcoded to
  `~/.claude/scripts/marker.sh` across this repo, uniformly and by design —
  see `settings.json`'s `Bash(~/.claude/scripts/marker.sh …)` allow-rule and
  `enforce-marker-script-shape.sh`'s anchor"]
- G4. `_lib_resolve_claude_pid` (in `claude/.claude/hooks/_lib.sh`) walks
  the process-ancestor chain to any depth to find the live session's PID,
  not a hardcoded one or two hops — so calling it (via `marker.sh
  resolve-session-id`, or by sourcing `_lib.sh` directly) from inside a new
  script adds one more process hop than calling it straight from the
  Bash-tool's own shell, and that extra hop is safe. [verified:
  `claude/.claude/hooks/_lib.sh:838-843`'s own comment: "Direct hook
  invocation resolves in one step ($PPID = Claude Code PID); a Bash-tool
  script invocation resolves in two steps ($PPID = Bash tool shell,
  grandparent = Claude Code PID). The loop handles any depth."] This
  matters because `handoff/SKILL.md`'s current recipe resolves the session
  id via a bare `sessions/$PPID` lookup rather than this canonical walk —
  moving that resolution into a script (mechanism 1, below) switches it to
  `_lib_resolve_claude_pid` specifically so the added hop doesn't break it.

**Per mechanism:**
1. Add seven dedicated scripts under `claude/.claude/scripts/`, one per
   multi-step site (all sites except `plan-review`'s Write-tool site),
   collapsing each site's multi-statement Bash recipe into a single
   Bash-tool call. Each is invoked via its hardcoded `~/.claude/scripts/<name>.sh`
   path (G3), resolves `$CLAUDE_CONFIG_DIR` internally via `_lib_config_dir`
   (sourced from `_lib.sh`) where it needs a config-dir path at all, and
   follows `claude/.claude/rules/shell-script-conventions.md` (`set -euo
   pipefail`, `[[ ]]`, quoted expansions, `local`, `IFS= read -r`,
   shellcheck-clean). See Critical Files for each script's contract. Land
   each script and its own SKILL.md/CLAUDE.md call-site edit in the same
   commit — a script with no caller (or a caller pointing at a script that
   doesn't exist yet) is a broken intermediate state, not a useful one to
   split across commits. anchors: root, Approach.
2. Update each of the eight SKILL.md sites (Critical Files) to invoke its
   new script as a single Bash-tool call in place of the old multi-statement
   recipe, and update `claude/.claude/CLAUDE.md`'s Shipping-section example
   to invoke the new `autonomous-shipping-active.sh` script the same way.
   Replace each site's stale or absent version-specific note with one
   version-free sentence pointing at `docs/worktree-bash-guard.md` — this
   part of the prior mechanism 2 is unchanged by the script pivot; a
   version-gated fact restated at every site is still read-cost paid on
   every session that loads any of these skills, for a fact that goes stale
   the next time the guard changes. anchors: sweep table.
3. Fix `plan-review/SKILL.md`'s Write-tool site directly (no script
   applies): drop the `CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"`
   assignment and its downstream reference, constructing the Write target
   against the literal `$HOME/.claude` path unconditionally. This is a
   correctness fix independent of the Bash-tool guard question — the
   `Write` tool's `file_path` argument is never shell-expanded, so pasting
   `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` into it tries to write inside a
   directory literally named that expression. Also fix the matching
   `HOOK_TEST_FIXTURE: declare-planmode-path` bash-equivalent block the same
   way, since `helpers.py`'s `extract_skill_command` executes that exact
   fenced block as the test's simulation of the Write tool call. anchors:
   sweep table.
4. Add `docs/worktree-bash-guard.md` recording the full current trigger
   taxonomy (A–E), the changelog cross-check, the ten-site sweep (nine
   live, one false-positive from the widened search surface), the
   mid-implementation non-reproduction finding, and a "how to re-verify"
   section that states plainly the guard did not reproduce on demand as of
   this writing. anchors: root, sweep table.
5. Add one CLAUDE.md bullet (`claude/.claude/CLAUDE.md`, Agent Briefing)
   giving the general convention for ad-hoc orchestrator Bash calls that no
   script can pre-cover, pointing at `docs/worktree-bash-guard.md`. This is
   the mechanism reaching Bash calls no skill-recipe or script fix touches
   — unchanged in kind from the prior draft, reworded to describe the
   script-first convention rather than prose per-call splitting as the
   primary mitigation. anchors: root.
6. Add `test_skills.py`'s Trigger-A regression test as new code in this
   plan's diff — it does not exist on disk. (An earlier draft was written
   and verified against the since-reverted prose-split attempt; it was
   reverted along with everything else in that attempt and nothing carries
   over.) It flags any fenced code block in any SKILL.md, stowed or plugin,
   that assigns a variable via `$(...)` and acts on it in a later statement
   in the same block. Fence detection reuses `_all_skill_md_paths()`'s
   existing `_FENCE_OPEN_RE` open-fence matching (keyed on the fence
   delimiter itself, not a `bash`/`sh` language tag), so an untagged fence —
   `respond-pr/SKILL.md:111-118` and `git-feature-branch-sync/SKILL.md:29-31`
   both use bare ` ``` ` fences — is scanned the same as a tagged one.
   Excludes only a fenced block immediately preceded (blank lines allowed
   between) by a line matching `<!-- (HOOK_TEST_FIXTURE|
   HOOK_SCRIPT_CONTENT_EXAMPLE):` — an explicit, structural opt-in, not a
   stylistic one: a shebang line alone is not treated as an exclusion signal,
   since a defensively-shebang'd real recipe would silently evade a
   shebang-keyed heuristic. `plan-review/SKILL.md`'s `HOOK_TEST_FIXTURE`
   fixture (a pytest-executed simulation of the Write-tool call above it,
   never typed into an agent's Bash tool) already carries this marker;
   `claude-hook-review/SKILL.md`'s hook-script skeleton (documentation of a
   script *file's* content, never typed into the Bash tool either) gets a
   new `HOOK_SCRIPT_CONTENT_EXAMPLE` marker comment added ahead of its
   fenced block. Test fixtures include at least one untagged-fence case
   alongside a tagged-fence case, so the any-fence matching is exercised,
   not just asserted in prose. After this plan's script migration, the test
   should find zero matches across the whole corpus; it stays as a
   regression guard against a future SKILL.md reintroducing the inline
   compound shape instead of calling a script. anchors: sweep table,
   tenth-site finding.
7. **Not implemented in this plan — blocked, not deferred by choice.** The
   engineer's preferred long-term mitigation, an `install.sh` version check
   hard-blocking install below the version that fixes this guard, stays
   unbuildable: no version has ever been observed to fix Triggers A/B.
   Unchanged from the prior draft.

**Other assumptions:**
- A1 (closed this session). The prior investigation's A1 ("no other skill
  currently contains Trigger A's bug shape") is false — see the sweep
  table. [verified: `grep -rn '^\s*[A-Za-z_][A-Za-z0-9_]*=\$('
  claude/.claude/skills/*/SKILL.md claude/.claude/CLAUDE.md
  claude/.claude/rules/*.md docs/*.md` — 9 matches, resolved to 8 distinct
  sites; a further pass via `_all_skill_md_paths()` (which also scans
  `plugins/*/skills/*/SKILL.md`) found the tenth, non-affected site]
- A2. A version-free structural note at each site stays accurate even after
  the guard's behavior changes again. [accepted — engineer's stated
  preference from the prior review: one place
  (`docs/worktree-bash-guard.md`) where a version number or current-status
  claim is allowed to live]
- A3. The `$CLAUDE_CONFIG_DIR`-plus-worktree-isolation gap this plan
  actually leaves — a non-default `~/.claude` account (G3's inherited gap)
  — is narrower than the prose-split draft's gap (which affected every
  `CLAUDE_CONFIG_DIR`-customized user, not just non-default-account setups)
  and is acceptable to ship, since it's the same gap `marker.sh` already
  ships with today. [inherited from `marker.sh`'s existing, accepted
  trade-off — not re-litigated by this plan]
- A4. The guard's non-reproduction during this session's re-verification
  does not mean the bug is gone; it means the guard's behavior is not a
  stable function of command text alone (possibly session-, timing-, or
  otherwise context-dependent in a way this investigation could not
  isolate). Scripts are the correct response either way per Approach's
  four independent grounds. [assumed — flagged in `docs/worktree-bash-guard.md`
  for whoever investigates next, not resolved by this plan]

## Critical files

**New scripts** (`claude/.claude/scripts/`), each following
`shell-script-conventions.md`, sourcing `../hooks/_lib.sh` for
`_lib_config_dir`/`_lib_resolve_claude_pid` where needed (mirroring
`marker.sh`'s own `. "$(dirname "$0")/../hooks/_lib.sh"` sourcing line), and
each paired with a `claude/.claude/scripts/tests/test_<name>.py` following
the existing per-script test-file convention:

- `handoff-record-conversion.sh` — no args. Resolves the session id via
  `_lib_resolve_claude_pid` (not a bare `sessions/$PPID` read — G4). On
  success, appends `handoff session=<id>\n` to
  `<config-dir>/.handoff-nudge.log` and removes
  `<config-dir>/.handoff-nudge-fired.d/<id>-ignored`. On failure to resolve
  a session id, exits 0 silently — best-effort telemetry and a
  defense-in-depth reset, not a gate, matching the current recipe's
  documented semantics.
- `pr-diff-against-base.sh` — no args. Resolves the PR's base branch (`gh
  pr view --json baseRefName --jq .baseRefName 2>/dev/null || echo main`),
  the merge-base SHA, and prints `git diff <merge-base>...HEAD` to stdout —
  the cumulative PR-vs-default-branch diff `/ready-for-review` step 3
  reviews. Under `set -euo pipefail`, a `git merge-base` failure (e.g. the
  base ref has no local tracking ref) aborts the script with a one-line
  stderr message naming the unresolved ref, rather than emitting an empty
  or wrong diff — matching the original recipe's own behavior of erroring
  out on an unresolved merge-base, just with an explicit message instead of
  a raw git error buried inside a failed substitution.
- `skill-fidelity-report.sh` — no args. Resolves the current branch (`git
  rev-parse --abbrev-ref HEAD`), then runs `transcript-analysis.py
  skill-invocation --branches <branch> --include-subagents` followed by
  `transcript-analysis.py review-trace --this-repo --branches <branch>`
  (both invoked via `$(dirname "$0")/transcript-analysis.py` — co-located,
  so no `$CLAUDE_CONFIG_DIR` reference is needed even internally), printing
  both reports to stdout in sequence, clearly delimited. The original
  two-statement recipe ran both commands regardless of the first's exit
  code (plain sequential Bash-tool statements, not a `set -e` shell); this
  script preserves that independence explicitly — each call is wrapped so a
  non-zero exit from the first does not skip the second, with a one-line
  stderr note naming which report failed. Exits 0 if at least one report
  printed, 1 if both failed.
- `pr-cost-section.sh` — no args. Reads and judges
  `<config-dir>/pr-cost-disclosure` (exactly one line, trimmed and
  lowercased, equal to `dollars`). If disabled/unreadable/malformed: no
  stdout, exit 1 (caller: delete the `## Cost` block if one exists). If
  enabled and the current branch is the literal `HEAD` (detached): no
  stdout, exit 2 (caller: omit the section, explain why). Otherwise: runs
  `$(dirname "$0")/transcript-analysis.py cost --this-repo --branches
  <branch> --summary`, prints stdout verbatim, exit 0 (caller: embed
  verbatim under `## Cost`, followed by the literal command
  `~/.claude/scripts/pr-cost-section.sh` as "the exact command that
  produced it" for reproducibility). One exit-code-driven script call
  replaces the gate-check-then-fetch two-step the current recipe documents
  as sequential dependent steps anyway.
- `branch-divergence-status.sh` — no args. Same detection primitive as
  `check-branch-divergence.sh` (default-branch resolution, bounded fetch,
  behind-count, `git merge-tree --write-tree` trial merge) but reports in
  plain text to stdout rather than a SessionStart JSON envelope, and never
  silently exits on a clean/in-sync result — the two skill-driven callers
  need a report every time, not only when there's something to flag. Not
  merged with the existing hook script (see Approach's rejected
  alternatives) — both implement the same canonical recipe
  (`git-feature-branch-sync/SKILL.md` §"Detecting divergence" stays the one
  documented source of truth for *what* the recipe is) but serve
  structurally different callers.
- `respond-pr-safe-patch.sh <owner/repo> <comment-id>` — reads the new
  comment body from stdin (heredoc at the call site, not a shell argument —
  the body is multi-line markdown with an emoji trailer, awkward and
  fragile to pass as a CLI arg). The call site's heredoc terminator must be
  quoted (`<<'EOF'`-shaped, with a collision-resistant terminator) — never a
  bare `<<EOF`, which would let `$(...)`/`$VAR` sequences inside a real PR
  comment's markdown expand in the *calling* shell before the script ever
  sees them, silently corrupting the body being PATCHed in. See the
  `respond-pr/SKILL.md` site entry below for the exact call-site text.
  Fetches the target comment's current body;
  if it does not start with `**[Claude Code]**`, exits 1 with a message on
  stderr telling the caller to use the `/replies` form instead and **does
  not attempt any PATCH**. If it does, issues the PATCH with the stdin body
  and exits 0. Collapses the existing fetch-then-conditional-PATCH recipe
  into one call whose ownership check can never be skipped or fumbled
  independently of the PATCH — this site is the one where an agent
  reproducing the two-step choreography incorrectly has an irreversible
  cost (PATCHing the wrong comment).
- `autonomous-shipping-active.sh` — no args. Sources `_lib.sh` and calls
  `_lib_autonomous_shipping_active "$(git rev-parse --show-toplevel)"`,
  forwarding its exit code — not a re-derived existence check. The
  already-existing, already-tested `_lib_autonomous_shipping_active`
  (`_lib.sh:655-673`, tested at `hooks/tests/test_lib.py:1204-1344`) checks
  two things a plain `<config-dir>/autonomous-shipping-required`-existence
  check would miss: it treats `<repo>/.claude/autonomous-shipping-optout` as
  a hard no, and it unions the resolved config dir's sentinel with the
  literal `$HOME/.claude` one so a sentinel armed before `CLAUDE_CONFIG_DIR`
  adoption still activates. Delegating to the existing function keeps this
  script and CLAUDE.md's own prose check reading the same implementation
  instead of two copies that can silently diverge — a repo with a committed
  opt-out getting a false "active" from a re-derived check would be exactly
  that kind of divergence.

**SKILL.md / CLAUDE.md sites.** Exact final replacement text for each site
below — not a directional description; paste verbatim, adjusting only if the
live file has drifted from the line numbers cited (re-read each file before
editing):

- `claude/.claude/skills/handoff/SKILL.md` (~line 155-171, "After writing:
  record the conversion signal") — replace the section's closing paragraph
  and fenced block with:

  > Once the handoff file is written and verified, append one line recording this session's id to
  > `nudge-handoff-near-context-cap.sh`'s own log — pairing it with that hook's `nudged` lines lets a
  > future report count how often a nudge fire is followed by a handoff in the same session, without
  > joining to transcript content. Also remove that session's escalation-ladder marker, so a
  > successful handoff resets the ignored-re-arm count instead of leaving it primed to hard-block on
  > the next session's first re-arm if the session id were ever reused:
  >
  > ```bash
  > ~/.claude/scripts/handoff-record-conversion.sh
  > ```
  >
  > Best-effort: silently skips the log append and marker removal if this session's id can't be
  > resolved — a conversion metric and a defense-in-depth reset, not a gate. Recipes across this repo
  > route through a dedicated script like this one instead of an inline multi-statement Bash call;
  > see `docs/worktree-bash-guard.md` for why.

- `claude/.claude/skills/ready-for-review/SKILL.md:72-73` — replace the
  Step-3 lead-in sentence and fenced block with:

  > Run `/code-review` against the **cumulative** PR-vs-default-branch
  > diff — not staged changes, not per-commit deltas (see `docs/worktree-bash-guard.md` for why this
  > resolves through a dedicated script rather than an inline multi-statement Bash call):
  >
  > ```bash
  > ~/.claude/scripts/pr-diff-against-base.sh
  > ```

  Leave the following "squash-merge artifact..." paragraph unchanged.

- `claude/.claude/skills/ready-for-review/SKILL.md:92-96` — replace the
  fenced block (the `BRANCH=$(...)` / two `python3 ...` calls) with:

  > ```bash
  > ~/.claude/scripts/skill-fidelity-report.sh
  > ```

  Leave the surrounding "List invocations..." and "`skill-invocation`
  defaults to this repo..." prose unchanged — neither carries a stale
  version note.

- `claude/.claude/skills/pr-description/SKILL.md:70-90` (Cost section) —
  replace the "Gate: resolve the config dir..." paragraph, both fenced
  blocks, and the "Resolve the branch immediately before..." paragraph with:

  > Machine-managed, delimited by `<!-- pr-cost:start -->` / `<!-- pr-cost:end -->` — regenerated fresh every sync, never reinserted verbatim (contrast `## Deferred review findings` below).
  >
  > Resolve the section with a single script call:
  >
  > ```bash
  > ~/.claude/scripts/pr-cost-section.sh
  > ```
  >
  > Exit 0: enabled and the branch resolved cleanly — stdout is the cost report; embed it **verbatim**
  > under `## Cost`, followed by the exact command `~/.claude/scripts/pr-cost-section.sh` as "the exact
  > command that produced it" for reproducibility — never recompose, round, or re-narrate the figures.
  > Exit 1: disabled, unreadable, or malformed
  > `<config-dir>/pr-cost-disclosure` — delete the block if one exists, no stdout. Exit 2: enabled but
  > the branch is the literal `HEAD` (detached) — omit the section and say why, no stdout. The sentinel
  > check (`<config-dir>/pr-cost-disclosure`, trimmed and lowercased, exactly `dollars`) is per Claude
  > account, not per repo: cost is an organizational fact, and each account is its own billing entity.
  > **One deliberate narrowing:** a sentinel consisting of a blank line followed by `dollars` reads as
  > two lines and is judged disabled, where a whitespace-collapsing read would have judged it enabled
  > — in the direction this gate already prefers (under-disclosing over guessing). Session/turn counts
  > and per-model-ID dollars are not neutral — they signal engagement scale and model mix. That is the
  > intended read under an account that opted in; it is not a property of the output format, and an
  > account enabling this for one engagement should not assume the fields are harmless in another. See
  > `docs/worktree-bash-guard.md` for why this collapses to one script call instead of a
  > gate-check-then-fetch two-step.

- `claude/.claude/skills/git-feature-branch-sync/SKILL.md:24-38`
  ("Detecting divergence") — replace the section's lead-in paragraph, fenced
  block, and the `git merge-tree` explanation paragraph with:

  > ## Detecting divergence
  >
  > Canonical recipe — used at the SessionStart advisory hook
  > (`check-branch-divergence.sh`, which runs this logic directly as a hook script, never through the
  > Bash tool) and, via `~/.claude/scripts/branch-divergence-status.sh`, at the `/ready-for-review`
  > pre-push gate and the `/respond-pr` precheck. Same detection primitive everywhere so divergence
  > reporting is uniform; see `docs/worktree-bash-guard.md` for why the two skill-driven callers go
  > through a dedicated script instead of restating the recipe inline.
  >
  > ```bash
  > ~/.claude/scripts/branch-divergence-status.sh
  > ```
  >
  > Reports the default branch, the behind-count, and the result of a `git merge-tree --write-tree`
  > trial merge (conflict files, if any) in plain text to stdout — always, not only when there's
  > something to flag. Requires git ≥ 2.38.

- `claude/.claude/skills/respond-pr/SKILL.md:111-118` — replace the
  `TARGET_BODY=$(...)` / `case` fenced block and its lead-in sentence with:

  > Before any PATCH, fetch the target body and verify it starts with that prefix; abort to the `/replies` form (Step 7) on any mismatch:
  > ```bash
  > ~/.claude/scripts/respond-pr-safe-patch.sh {owner}/{repo} {comment-id} <<'RESPOND_PR_BODY_EOF'
  > **[Claude Code]** ...corrected text...
  >
  > 🤖 Generated with [Claude Code](https://claude.com/claude-code)
  > RESPOND_PR_BODY_EOF
  > ```
  > The heredoc terminator must stay quoted (`<<'RESPOND_PR_BODY_EOF'`, not `<<RESPOND_PR_BODY_EOF`)
  > — an unquoted terminator lets `$(...)`/`$VAR` sequences inside the corrected text expand in the
  > calling shell before the script ever sees them, silently corrupting the body being PATCHed in.
  > The script re-fetches the target comment, checks its current body starts with `**[Claude Code]**`,
  > and only then issues the PATCH — it never attempts one on a mismatch, so the ownership check can't
  > be skipped or fumbled independently of the PATCH the way two separate Bash statements could be.
  > (or `/issues/comments/{id}` for issue-level comments)

- `claude/.claude/skills/plan-review/SKILL.md:22-33` — no script; per
  mechanism 3, replace the "Otherwise write the plan-mode file's path..."
  sentence and the `HOOK_TEST_FIXTURE: declare-planmode-path` fenced block
  with:

  > Otherwise write the plan-mode file's path, with no trailing newline, to
  > `$HOME/.claude/.plan-review-active.d/<the id just resolved>.planmode-path` — using the Write tool,
  > not Bash. This site accepts the `$HOME/.claude`-only gap unconditionally (no `$CLAUDE_CONFIG_DIR`
  > retry, unlike the script-callable sites elsewhere in this repo) — see `docs/worktree-bash-guard.md`.
  > `marker.sh` has no argument for this path, and a Bash-written file at this path is not covered by
  > the same subagent-write restriction a Write tool call is. `marker.sh write plan-review` reads this
  > file to decide which plan set the completion marker covers, falling back to the repo-relative plan
  > set when it is absent.
  >
  > `marker.sh` invocations stay hardcoded to `~/.claude/scripts/marker.sh` across this repo, uniformly and by design — see `settings.json`'s `Bash(~/.claude/scripts/marker.sh …)` allow-rule and `enforce-marker-script-shape.sh`'s anchor, neither of which recognizes a config-dir-aware form.
  >
  > <!-- HOOK_TEST_FIXTURE: declare-planmode-path — the hook-alignment test suite executes this recipe (a bash equivalent of the Write tool call above) to verify the resulting sibling file lands at the path and content require-plan-review.sh and marker.sh expect. Do not duplicate the recipe elsewhere; the test re-reads it from here. This fenced block is a pytest-executed simulation, never typed into an agent's Bash tool — test_skills.py's Trigger-A regression scan excludes it by this same comment. -->
  > ```bash
  > SESSION_ID=$(~/.claude/scripts/marker.sh resolve-session-id) || exit 1
  > printf '%s' "$PLAN_MODE_FILE_PATH" > "$HOME/.claude/.plan-review-active.d/$SESSION_ID.planmode-path"
  > ```

- `claude/.claude/CLAUDE.md` (Shipping section) — in the "Where autonomous
  shipping is active..." bullet, replace `` `test -f
  "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/autonomous-shipping-required" || test
  -f ~/.claude/autonomous-shipping-required` `` with
  `` `~/.claude/scripts/autonomous-shipping-active.sh` (exit 0 = active) ``,
  leaving the surrounding sentence otherwise unchanged.

- `claude/.claude/CLAUDE.md` (Agent Briefing section, new bullet per
  mechanism 5) — add:

  > - **Script-first for multi-step Bash recipes; single-statement, no nested `$(...)`, no
  >   `$CLAUDE_CONFIG_DIR` reference for anything else.** The harness's worktree-isolation Bash-tool
  >   guard refuses more command shapes than it used to be documented as refusing — variable
  >   assignment via `$(...)` used later in the same call, any `$CLAUDE_CONFIG_DIR` reference, and
  >   more (see `docs/worktree-bash-guard.md` for the full trigger taxonomy and current status). Every
  >   skill recipe that used to chain multiple statements now calls a single dedicated script under
  >   `~/.claude/scripts/` instead. For an ad-hoc orchestrator Bash call no script pre-covers, keep it
  >   to one double-quoted statement with no nested `$(...)` and no `$CLAUDE_CONFIG_DIR` reference —
  >   the same discipline, applied by hand where no script exists yet.

- `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` (~line 36,
  "## 3. Script skeleton") — insert one new line immediately before the
  fenced hook-script-skeleton example's opening ` ```bash `:

  > <!-- HOOK_SCRIPT_CONTENT_EXAMPLE: this fenced block documents a hook script's own file content — never typed into an agent's Bash tool. test_skills.py's Trigger-A regression scan excludes it by this comment. -->

  No other change to this file; the example's existing `#!/bin/bash` shebang
  and body are untouched.

**Documentation and tests:**

- `docs/worktree-bash-guard.md` (new) — the full bisection table (all
  triggers A-E), the changelog cross-check, the ten-site sweep table (nine
  live, one false-positive with the marker-comment exclusion explanation), the
  mid-implementation non-reproduction finding stated plainly (not softened
  into "confirmed-refuses unconditionally" language), the script-first
  convention this plan establishes, and a "how to re-verify" section noting
  the guard did not reproduce on demand as of this writing. One sentence
  cross-referencing `docs/design-decisions.md` §7 (why this repo opts into
  worktree isolation at all — a different question from what the harness's
  guard does once isolated).
- `claude/.claude/CLAUDE.md` (Agent Briefing) — one new bullet per
  mechanism 5: script-first convention for skill recipes, prose convention
  (double-quoted, single-statement, no nested `$(...)`, no
  `$CLAUDE_CONFIG_DIR` reference) for ad-hoc orchestrator Bash no script
  pre-covers, pointing at `docs/worktree-bash-guard.md`.
- `claude/.claude/skills/tests/test_skills.py`'s existing
  `test_resume_context_script_exists_and_executable` — generalize from its
  current single-script check (`SCRIPTS_DIR / "resume-context.sh"`) into a
  `@pytest.mark.parametrize` sweep over every `claude/.claude/scripts/*.sh`
  file, asserting `os.access(script, os.X_OK)` for each. Every script in
  that directory (the seven new ones included) is invoked by a hardcoded
  literal path (G3), never `bash <path>`, so a script committed without the
  executable bit fails outright on first use for every stow consumer
  simultaneously — a silent regression this test already exists to catch
  for one script, not seven. This replaces per-script `os.access` checks in
  each new `test_<name>.py` file with the one generalized sweep, avoiding
  seven duplicate copies of the same structural assertion.
- `claude/.claude/skills/tests/test_skills.py` (near `_all_skill_md_paths()`)
  — new code per mechanism 6, above (no prior version exists on disk to
  carry over — see mechanism 6 for why). At minimum two fixture cases:
  a must-flag case using a tagged ` ```bash ` fence (the original shape) and
  a must-flag case using an untagged ` ``` ` fence (matching
  `respond-pr/SKILL.md`'s and `git-feature-branch-sync/SKILL.md`'s actual
  shape), a must-NOT-flag case for a fenced block preceded by a
  `HOOK_TEST_FIXTURE`/`HOOK_SCRIPT_CONTENT_EXAMPLE` marker comment, and a
  must-NOT-flag case for a `$(...)`-assigned variable that is never
  referenced later in the block — the boundary that actually distinguishes
  Trigger A from the far more common "assign and don't use later" shape;
  without this fixture, an over-broad first implementation (flag any
  `=$(` regardless of later use) would pass every other required fixture
  while false-flagging ordinary documentation snippets corpus-wide. After
  this plan's script migration it should find zero matches across every
  SKILL.md, stowed and plugin.
- `claude/.claude/scripts/tests/test_<name>.py` for each of the seven new
  scripts (Critical Files, above) — one per script, following the existing
  one-test-file-per-script convention in `claude/.claude/scripts/tests/`.
  Each covers the script's normal-path behavior against a fixture repo/config
  dir and its best-effort-failure path where one exists
  (`handoff-record-conversion.sh` with no resolvable session). Beyond that
  generic template, each script also needs the coverage specific to its own
  contract, not left to be inferred from the generic bucket list:
  - `pr-cost-section.sh` — all three exit codes (0/1/2) as named cases, plus
    a dedicated fixture for the one deliberate narrowing: a sentinel file
    consisting of a blank line followed by `dollars` must assert exit 1 —
    not just folded into generic "three codes" coverage, so a future edit
    that re-widens the read back to whitespace-collapsing can't pass
    silently.
  - `autonomous-shipping-active.sh` — both exit codes, plus a case
    asserting it defers to `_lib_autonomous_shipping_active` correctly: a
    fixture repo with the machine sentinel present AND a committed
    `.claude/autonomous-shipping-optout` must exit 1 (the case a
    re-derived existence-only check would get wrong — see the script's own
    Critical Files entry above).
  - `respond-pr-safe-patch.sh` — the refusal path (target body doesn't
    start with `**[Claude Code]**`) must assert **zero PATCH calls were
    made**, not just exit code 1, via a call-recording `gh` shim (see the
    conftest.py note below) — an exit-code-only assertion would still pass
    if a real bug issued the PATCH anyway. The allow path is equally
    load-bearing and gets its own named assertion, not the generic
    normal-path bucket: when the target body matches the prefix, assert the
    PATCH was issued against the exact comment id passed on the command
    line, with the exact stdin body — this script's own stated purpose is
    preventing "PATCHing the wrong comment," an allow-path correctness
    property as much as a deny-path one. Include a fixture body containing
    the characters the call site's heredoc-quoting warning exists to
    protect (a backtick, a `$VAR`-shaped sequence, multi-line markdown, an
    emoji trailer) and assert it reaches the shim byte-for-byte unexpanded.
  - `pr-diff-against-base.sh` — a named case for the `git merge-base`
    failure path from this script's own Critical Files entry above (no
    local tracking ref for the resolved base): assert non-zero exit and the
    named stderr message, not just folded into a generic failure bucket
    that doesn't distinguish it from the base-ref-resolution fallback.
  - `handoff-record-conversion.sh` — the success path has two independent
    side effects (log line appended, escalation marker removed); assert
    both explicitly in the same case rather than only one implicitly
    covering "normal-path behavior."
  - `branch-divergence-status.sh` — named cases for in-sync (behind-count
    0), behind-with-clean-merge, and behind-with-conflicts, asserting the
    salient facts (behind-count, conflicting filenames) via targeted
    substring/regex checks — not a full golden-output match, which would be
    brittle to incidental wording changes in a plain-text report two
    different skills consume.
  - `skill-fidelity-report.sh` — named cases for both `transcript-analysis.py`
    calls succeeding, the first failing (second still runs, exit 0, stderr
    note), and both failing (exit 1) — the partial-failure contract from
    this script's own Critical Files entry above, made concrete as tests
    rather than left implicit in the generic template.

  Reuse `claude/.claude/scripts/tests/conftest.py`'s existing scaffolding
  (`_make_repo_with_remote`, `_make_feature_branch`, `_init_repo`, and the
  autouse `CLAUDE_CONFIG_DIR`-pinning fixture) for the three scripts that
  need a git repo/remote/branch fixture
  (`pr-diff-against-base.sh`, `branch-divergence-status.sh`,
  `skill-fidelity-report.sh`) rather than re-deriving it per test file.
  `respond-pr-safe-patch.sh`'s test needs a `gh` shim capable of simulating
  GET-then-PATCH against a PR review comment — a different API shape than
  `test_cleanup_merged_branches.py`'s existing `_gh_shim_source`/`fake_gh`
  (which simulates merge-status queries), but the same underlying
  PATH-shadowing mechanism (`_shimmed_env`). Promote `_shimmed_env` from
  `test_cleanup_merged_branches.py` into `conftest.py` so both files import
  the one shared "write this shim script as a `gh` executable on PATH"
  helper, while each test file keeps its own domain-specific shim *source*
  generator — avoids a third hand-rolled copy of a ~40-line
  credential-scrubbing helper. `_shimmed_env` is not self-contained: it
  calls `_base_test_env()`, `_noop_direnv_shim_source()`, and
  `_curated_path_without_direnv()`/`_TOOLS_NEEDED_WITHOUT_DIRENV`, and needs
  `os`/`uuid`/`shutil` imported alongside it. Promote this whole dependency
  chain together, not `_shimmed_env` alone, and update
  `test_cleanup_merged_branches.py`'s own `from conftest import (...)` list
  (it already imports other promoted helpers this way) plus its one direct
  `_base_test_env()` call site to import from `conftest` instead of using
  its own now-removed local copy.

## Verification

- `chmod +x` on all seven new scripts before first use — every SKILL.md/
  CLAUDE.md call site invokes them as a bare `~/.claude/scripts/<name>.sh`,
  not `bash <path>`, so a non-executable file fails outright on first use.
- Run each new script directly, live, as a literal Bash-tool call from this
  worktree-anchored session, confirming (a) it behaves correctly against
  real or fixture state and (b) the single-line invocation itself is never
  refused — best-effort given this session's own non-reproduction finding,
  but still the same discipline the prior investigation used.
- `../../../.venv/bin/pytest claude/.claude/` from the worktree — new
  script tests, the extended `test_skills.py` Trigger-A test (must find
  zero matches against the fixed corpus), and the full existing suite.
- `../../../.venv/bin/ruff check claude/.claude/` — lint, Python side.
- `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — lint, the
  seven new shell scripts.
- `skill-management:skill-review` on each of the six stowed SKILL.md diffs
  (`handoff`, `ready-for-review`, `pr-description`, `git-feature-branch-sync`,
  `respond-pr`, `plan-review` — the eight sweep-table sites live across only
  these six files, `ready-for-review` and `pr-description` each carrying
  two) plus the seventh, `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md`,
  and `ai-instruction-and-memory-files` on the CLAUDE.md diff (both of which
  `/code-review` dispatches automatically for these file types), then
  `/code-review` itself before commit — which should also dispatch
  `staff-platform-engineer` and `staff-sdet` given the new shell scripts
  and their tests. The `HOOK_SCRIPT_CONTENT_EXAMPLE` marker-comment addition
  to `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` touches
  a plugin directory, so it also triggers `plugin-semver:plugin-semver`
  (version-field discipline) — a one-line comment addition inside an
  existing example is unlikely to need a version bump, but let that skill's
  own review make the call rather than assuming.

## Out of scope

- Reporting this as a harness bug to Anthropic. A separate session
  (`claude-config/worktree-bash-guard-regression`) is already handling
  this; not touched by this plan or this session. Whoever finalizes that
  report should be aware of the mid-implementation non-reproduction
  finding above — it's germane to how the report characterizes
  reliability, but resolving that is that session's call, not this plan's.
- `install.sh` asserting a minimum Claude Code version and hard-blocking
  install below it — blocked, not deferred by choice; no version currently
  fixes Triggers A/B. Unchanged from the prior draft.
- A `test_skills.py` sweep hardcoding Triggers C/D/E as regression
  assertions — these have no syntactic shape that wouldn't also flag
  correct code; prose-covered by the CLAUDE.md convention bullet instead.
- Fixing or documenting the harness guard's implementation itself —
  confirmed harness-owned, not this repo's code.
- `docs/permission-prompt-tracking.md`'s human-run log-trimming recipe — a
  human-operator recipe, not something any agent session executes.
- The `pr-cost --all-accounts` implementation work on branch
  `pr-cost-cross-account-scan-consent` — unrelated, noticed incidentally.
- Editing `.claude/plans/handoff-nudge-log-worktree-path.md` — preserved
  historical record per this repo's CLAUDE.md Axis 3.
- Merging `branch-divergence-status.sh` (new) with `check-branch-divergence.sh`
  (existing hook) into one shared implementation — see Approach's rejected
  alternatives; revisit only if the ~15-line duplication turns out to drift
  in practice.
