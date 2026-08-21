# Worktree-isolation Bash-tool guard: re-bisect the trigger taxonomy and fix stale/incomplete skill guidance

## Context

The Claude Code harness's worktree-isolation Bash-tool guard — a harness-native
static check, not anything this repo's own hooks implement — refuses far more
command shapes, at far more sites, than this repo currently documents or
guards against. Three live skill recipes carry a "fixed in 2.1.224" claim
that a fresh re-bisection on the currently-installed CLI (2.1.238) shows is
false; a follow-up sweep for the same bug shape found three more skill files
carrying the identical, previously-undocumented defect; and this session's
bisection surfaced two entirely new trigger shapes the prior investigation
never tested. This plan restores and extends the prior investigation's fix,
corrects its stale claims, closes the newly-found sites, lands the
regression test the prior investigation designed but never shipped, and adds
a general Bash-usage convention for the ad-hoc orchestrator Bash calls no
skill recipe covers.

## Approach

Split every affected skill recipe into single-statement Bash calls with
literal (not variable-carried) substitution between them, and — per the
engineer's own review of this plan — strip the version-specific "fixed in
2.1.224" framing out of every site entirely rather than correcting it in
place, replacing it with one version-free structural pointer per site to a
new `docs/worktree-bash-guard.md` reference, which is the sole place a
version number or current-status claim is allowed to live. Land the
`test_skills.py` sweep the prior investigation specified but reverted
alongside its premise, add one CLAUDE.md-level convention bullet for Bash
calls this fix's per-recipe scope doesn't reach, and record — but do not
implement — the engineer's preferred long-term mitigation (`install.sh`
version-gating the minimum Claude Code version) as blocked until a fix
version exists to gate on.

### Investigation, re-derived this session

**Version check (Decision 1).** `claude --version` reports `2.1.238` —
above the `2.1.224` the prior investigation's Postscript cited as the fix
version. Ruled out: this is not an outdated-install false alarm.

**Re-bisection (Decision 2).** Re-ran the prior plan's bisection table
verbatim as literal, separate Bash calls from this worktree, plus every new
row the brief flagged, plus follow-up isolation calls to pin down exactly
which token was responsible when a result surprised. Full table:
`docs/worktree-bash-guard.md`, written as part of this plan. Headline
results:

- **Trigger A** (`$(...)` assigned to a variable, used in a later statement
  in the same call) — still refuses, unconditionally. Confirmed with a
  git-free reproduction (`TESTVAR=$(echo hello); [ -n "$TESTVAR" ] &&
  printf ...`) to rule out git specifically being the cause.
- **Trigger B** (any reference to `$CLAUDE_CONFIG_DIR`, bare or
  `${VAR:-default}`, in an otherwise-trivial single statement) — still
  refuses, unconditionally.
- **Trigger C (new)** — an unquoted variable-built path argument refuses;
  the identical path double-quoted succeeds. Isolated via a 2×2: quoted
  `$HOME`+`$PPID` succeeds (twice, non-flaky); unquoted `$HOME`+`$PPID`
  refuses; unquoted `~`+`$PPID` refuses; unquoted `~`+literal-number
  (no `$PPID`) succeeds — quoting, not `~` vs `$HOME`, is the operative
  variable.
- **Trigger D (new)** — `$PPID` used as a whole/standalone argument refuses
  regardless of quoting: `echo $PPID`, `head -n1 "$PPID"`, and
  `head -n1 $PPID` all refuse, even though `$PPID` embedded as a suffix
  inside a longer double-quoted string (Trigger C's non-refusing case)
  succeeds. `$HOME` alone does not trigger this (`echo $HOME` succeeds) —
  the shape is specific to `$PPID`.
- **Trigger E (new)** — a bare `$(git ...)` substitution refuses even with
  no assignment and no later statement (`echo "$(git rev-parse --git-path
  info/exclude)"`, and the same substitution inside `grep`). This is the
  one trigger whose refusal message's git framing actually matches its
  content; Triggers B–D refuse commands with no git in them at all, using
  the same templated message — the message text is not a reliable signal
  of *why* a given command refused.

The brief's own new-finding row (bare `head -n1 ~/.claude/sessions/$PPID`
contradicting the prior table's "succeeded" verdict for "the exact same
shape") is resolved, not just reproduced: it isn't the same shape. The
prior table's succeeding row quoted the whole path
(`"$HOME/.claude/sessions/$PPID"`); the brief's new row left it unquoted.
Both are true and both are explained by Trigger C — no contradiction in the
guard's behavior, only in the brief's shape comparison.

**Changelog cross-check (Decision 2, continued).** The Postscript's
Anthropic-changelog citation for "the fix" doesn't hold up under a verbatim
re-fetch of the primary source:

- **2.1.224** (the cited version) has no worktree/isolation/Bash-guard
  entry at all in its actual changelog section — its entries cover
  self-hosted runners, cross-session messaging, and unrelated fixes.
- The quoted text the Postscript attributed to 2.1.224 — "Fixed
  worktree-isolated sessions and their subagents being able to run
  destructive git commands against the main checkout; isolation now
  applies to file edits and Bash in every session type" — is real, but
  belongs to **2.1.222**, two versions earlier. Read plainly, it describes
  an isolation-*bypass* fix (subagents evading isolation) addressed by
  *widening* the guard's scope to more session types — not a
  false-positive fix that would narrow which commands it refuses. That
  reading is consistent with what this session found: Triggers A and B,
  which the Postscript's own bisection table already demonstrated
  pre-fix, still refuse unconditionally on 2.1.238, fourteen point
  releases past the cited fix.
- The guard has a long history of narrow, incremental patches rather than
  one resolving fix — worktree/git-isolation changelog entries appear at
  2.1.210, 2.1.216, 2.1.222, 2.1.229, and 2.1.232, each closing one
  specific bypass or false-positive shape. **2.1.238 itself** — the
  version this session is running — ships "Fixed worktree-isolation Bash
  refusals telling you to remove a redirect when the command had none," a
  narrow false-positive fix for one specific spurious-message case,
  confirming the guard is still under active, incremental correction as
  of today's install, not settled.

**Conclusion (Decision 2/3).** This is not a regression of a fixed bug —
Triggers A and B were never actually fixed; the Postscript's "no longer
reproduces" observation at the time was real but its causal attribution
was wrong (wrong version cited, and the actual cited version's changelog
text doesn't describe the claimed fix). Triggers C, D, and E are shapes
the prior investigation's bisection never tested, not new breakage. Given
the guard's multi-version history of one-narrow-shape-at-a-time patches,
waiting for a comprehensive upstream fix is not a viable near-term
mitigation — a repo-side convention is the durable lever available now
(per the prior plan's own G2: the guard isn't scoped to this repo, so the
general convention belongs in the *global* `claude/.claude/CLAUDE.md`, not
the repo-root one; per-recipe splits belong in each affected skill).

**Sweep for other affected sites (closes the prior investigation's A1
gap).** The prior plan's A1 assumption swept only for Trigger A's shape
plus literal `$CLAUDE_CONFIG_DIR`/`sessions/$PPID` references. Re-running
a grep for `$(...)`-assignment lines across every skill this session found
three more affected files the prior sweep missed, none carrying any
historical note — these are undocumented, not previously investigated and
accepted:

| File | Shape found |
|---|---|
| `ready-for-review/SKILL.md:72-73` | Trigger A (already has a stale note) |
| `ready-for-review/SKILL.md:92-96` | Trigger A + Trigger B — **new, undocumented** |
| `pr-description/SKILL.md:78-86` | Trigger A (×2 assignments) + Trigger B — **new, undocumented**, distinct from the one site (line 88) that does carry a stale note |
| `pr-description/SKILL.md:87-90` | Trigger A (already has a stale note) |
| `handoff/SKILL.md:161-165` | Trigger A + Trigger B (already has a stale note) |
| `git-feature-branch-sync/SKILL.md:29-31` | Trigger A — **new, undocumented**. Shared by three callers (`check-branch-divergence.sh` hook, `/ready-for-review`, `/respond-pr`), but only the two skill-driven callers run it through the Bash tool — the hook executes its own script directly, outside this guard's reach entirely, so only the SKILL.md recipe text needs the split. |
| `respond-pr/SKILL.md:111-118` | Trigger A — **new, undocumented** |
| `plan-review/SKILL.md:31` (this skill's own conditional plan-mode recipe) | Trigger A + Trigger B — **new, undocumented**, but low-traffic (only runs when a plan-mode reminder is present) |

Per this repo's CLAUDE.md "Audit structural siblings before scoping a fix
narrowly": the bug shape is identical across all eight sites, so the fix
applies to all eight, not just the three the prior investigation already
knew about.

A ninth site turned up while drafting the CLAUDE.md convention bullet
itself (mechanism 4): `claude/.claude/CLAUDE.md`'s own "Shipping" section
(the autonomous-shipping-sentinel check) already contains the example
command `test -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/autonomous-shipping-required"
|| test -f ~/.claude/autonomous-shipping-required` — a live Trigger-B
site inside CLAUDE.md's own prose, not a SKILL.md fence. In scope for the
same reason as the other eight.

**Alternatives considered and rejected:**
- *Per-skill-recipe fixes only, no general convention* — this session's
  own refusals (the brief's own reproduction table) all happened in ad-hoc
  orchestrator Bash calls, not inside any skill's fenced recipe; a
  recipe-scoped fix, even applied to all eight sites above, would not have
  prevented any of them.
- *A repo hook that pre-validates or rewrites Bash commands before this
  guard sees them* — rejected as a heavier, more invasive mechanism than
  the task needs: hooks fire on tool calls this session issues, they
  cannot intercept or alter the harness's own internal Bash-tool
  evaluation, and a hook that pattern-matches arbitrary shell to guess
  whether the *harness* will refuse it would need to track an
  undocumented, actively-changing heuristic (five point-release patches in
  this taxonomy alone) — a documentation-level convention degrades
  gracefully as the guard's exact shape drifts; a hook trying to predict
  it would silently go stale in whichever direction is wrong.
- *File the harness bug and do nothing repo-side* — rejected as the sole
  response given the guard's multi-version patch history; a report is
  still worth filing (see Out of scope) but isn't a near-term mitigation
  on its own.
- *`install.sh` version-gates the minimum Claude Code version instead of
  splitting recipes* — the engineer's preferred long-term shape once a
  fix version exists (replacing nine per-recipe workarounds with one
  mechanical check is exactly the "prefer mechanical enforcement over
  prose" principle this repo already follows elsewhere), but not
  buildable today: this session found no version, including the
  currently-installed 2.1.238, where Triggers A/B are fixed, so there is
  no version number to assert. Recorded as mechanism 6 and Out of scope,
  not implemented in this plan.
- *Skip the `test_skills.py` regression test, prose-only fix* — this was
  this session's first draft, reasoning that the guard's exact shapes
  shift release to release and a test would go stale. Reversed after the
  sweep above: Trigger A's specific shape (`$(...)`-assignment followed by
  a later statement in the same fenced block) is syntactically stable and
  was never actually implemented after the prior investigation designed
  it — and a grep for exactly that shape just found five previously-unknown
  live violations in one pass. A test that would have caught five real
  bugs is not a test guarding against a hypothetical; it's a gap that let
  five bugs ship silently. Triggers C/D/E stay prose-only (see Out of
  scope) — those really are shapes with no clean syntactic signature that
  wouldn't also flag correct code.

### Assumption ledger

**Root problem:** skill recipes across this repo, and this repo's shared
CLAUDE.md, understate how often and how broadly the harness's
worktree-isolation Bash-tool guard refuses ordinary commands in a
worktree-anchored session (which, under this repo's own worktree
enforcement, is every session working in it) — both in claimed severity
(three sites say "fixed" when the shape still refuses) and in coverage
(five more sites carry the identical unfixed shape with no note at all).

**Givens:**
- G1. The refusal originates in the Claude Code harness's own Bash-tool
  guard, not in any script this repo owns. Given because: this repo
  cannot patch harness source; the only lever available is recipe shape
  and documentation. [verified: fresh `grep -rn "too complex to verify
  \|isolated in the worktree" claude/.claude/hooks/ claude/ docs/` this
  session returns zero matches for the harness's refusal text — the two
  hits that do appear (`transcript-analysis.py` and its test fixture) are
  a transcript-classification label for a *different*, Edit-tool-family
  worktree-write message, not this Bash-tool guard]
- G2. The guard is not scoped to this repo — carried over from the prior
  investigation's engineer-verified finding
  (`.claude/plans/handoff-nudge-log-worktree-path.md`, G2), unchanged by
  this session. [engineer-verified, prior session]

**Per mechanism:**
1. Split all eight sites in the sweep table above into single-statement
   Bash calls with literal substitution between steps (never a
   variable-carried or re-interpolated value), and for every site
   referencing `$CLAUDE_CONFIG_DIR`, retry with the literal `$HOME/.claude`
   fallback on refusal — reusing the two-tier pattern the prior
   investigation already designed and had reviewed for `handoff/SKILL.md`.
   anchors: root, sweep table. [verified: this session's own re-bisection
   confirms each of the eight sites' exact fenced shape matches a
   confirmed-refusing trigger]. No lighter primitive applies — this is a
   same-weight correction to existing recipe text, not an escalation to a
   heavier mechanism.
2. Remove every version-specific claim from all nine sites — including
   the three existing "Claude Code ≤2.1.223 could refuse this exact shape
   ... fixed in 2.1.224" notes, which are both wrong (per the changelog
   cross-check above) and, per the engineer's review of this plan, the
   wrong *kind* of content to carry at each site regardless: a
   version-gated fact restated nine times is read-cost paid on every
   session that loads any of these skills, for a fact that goes stale the
   next time the guard changes. Replace each with a single, version-free
   sentence stating only the structural constraint ("run each step
   separately — a worktree-isolated session's Bash tool refuses
   multi-statement or `$CLAUDE_CONFIG_DIR`-referencing calls") and a
   pointer to `docs/worktree-bash-guard.md`, which is the one place a
   version number or current-status claim is allowed to live (see A2
   below). anchors: row1.
3. Add `docs/worktree-bash-guard.md` recording the full current trigger
   taxonomy (A–E), the changelog cross-check, the eight-site sweep table,
   and a "how to re-verify" section, following this repo's existing
   docs/-as-supporting-reference pattern (`docs/design-decisions.md`,
   `docs/auto-mode.md`, `docs/handoff-nudge.md`, etc., cited from terse
   CLAUDE.md bullets) — this is the one place the full evidence lives;
   every other mechanism below points at it rather than restating it.
   anchors: root, sweep table.
4. Add one CLAUDE.md bullet (`claude/.claude/CLAUDE.md`, Agent Briefing —
   the section that already documents other worktree-anchored-session
   behavioral quirks) giving the general convention for ad-hoc
   orchestrator Bash calls, and fix the Shipping section's own affected
   example command (the ninth site above) to use the same two-tier
   pattern. anchors: root. This is the mechanism reaching the ad-hoc Bash
   calls no skill-recipe fix touches. Drafted bullet text:

   > **A worktree-anchored session's Bash tool refuses more than complex
   > commands — and the refusal message's git framing isn't a reliable
   > signal of why.** The harness-native worktree-isolation guard (not a
   > hook this repo owns) confirmed-refuses, as of Claude Code 2.1.238: a
   > `$(...)` substitution assigned to a variable and used in a later
   > statement in the same call; any reference to `$CLAUDE_CONFIG_DIR` in
   > any form, however simple — even a bare `echo`; an unquoted
   > variable-built path argument (the identical path succeeds
   > double-quoted); `$PPID` passed as a whole/standalone argument, quoted
   > or not; and a bare `$(git ...)` substitution with no assignment at
   > all. Full bisection and update cadence:
   > `docs/worktree-bash-guard.md`. Default to one fully double-quoted,
   > single-statement Bash call with no nested `$(...)` and no
   > `$CLAUDE_CONFIG_DIR` reference; read a value a later call needs from
   > a prior call's printed output and paste it as a literal rather than
   > carrying it in a variable. Since `$CLAUDE_CONFIG_DIR` can never be
   > read from inside a worktree-isolated Bash call, a command that needs
   > it tries the `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` form first and
   > retries with the literal `$HOME/.claude` fallback on refusal; a
   > non-Bash tool call that needs the same value (e.g. a Write target
   > path) has no such retry and must accept the `$HOME/.claude` default
   > outright.

   And the Shipping-section fix: replace the single combined
   `test -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/autonomous-shipping-required"
   || test -f ~/.claude/autonomous-shipping-required` example with two
   sequential single-statement attempts (try the `$CLAUDE_CONFIG_DIR`-aware
   form; on refusal, the plain `~/.claude` form), matching the bullet's
   own stated convention.
5. Land the `test_skills.py` sweep the prior investigation specified
   (mechanism 2 of `.claude/plans/handoff-nudge-log-worktree-path.md`)
   but never implemented — a module-level test flagging any fenced
   bash/sh block, across every SKILL.md (stowed + plugin, via the
   existing `_all_skill_md_paths()`), that assigns a variable via `$(...)`
   and is followed by another non-comment statement in the same block.
   anchors: sweep table. [verified: `grep -n "assigns a variable\|command
   substitution" claude/.claude/skills/tests/test_skills.py` returns zero
   matches — the test was never shipped, and the sweep above shows five
   sites it would have caught].
6. **Not implemented in this plan — blocked, not deferred by choice.**
   The engineer's preferred long-term mitigation is an `install.sh`
   version check hard-blocking install below the Claude Code version that
   fixes this guard, replacing per-recipe workarounds entirely once that
   version exists. It cannot be built yet: this session found no version,
   including the currently-installed 2.1.238, where Triggers A/B are
   fixed — there is no version number to gate on. This mechanism becomes
   live only after Anthropic confirms a fix version (see Out of scope);
   recorded here so a future plan revising this repo's mitigation starts
   from this ledger instead of re-deriving the same conclusion.

**Other assumptions:**
- A1 (closed this session). The prior investigation's A1 ("no other skill
  currently contains Trigger A's bug shape") is now known false — see the
  sweep table. This session's own sweep used the same
  `_all_skill_md_paths()`-scoped search surface
  (`claude/.claude/skills/`, `claude/.claude/CLAUDE.md`,
  `claude/.claude/rules/`, `docs/`) plus all five confirmed trigger
  shapes, not just A/B. [verified: `grep -rn '^\s*[A-Za-z_][A-Za-z0-9_]*=\$('
  claude/.claude/skills/*/SKILL.md claude/.claude/CLAUDE.md
  claude/.claude/rules/*.md docs/*.md` — 9 matches, resolved to the 8
  distinct sites in the sweep table above (one file, `ready-for-review`,
  has two separate sites)]
- A2. A version-free structural note at each site ("run each step
  separately — see docs/worktree-bash-guard.md") stays accurate even
  after the guard's behavior changes again, unlike the version-specific
  notes it replaces. [accepted — this is the engineer's stated preference
  from this plan's review: no per-site version claims, one place
  (`docs/worktree-bash-guard.md`) where a version number or current-status
  claim is allowed to live and get corrected as the guard evolves.]
- A3. The `$CLAUDE_CONFIG_DIR`-plus-worktree-isolation residual gap (a
  session with a customized `CLAUDE_CONFIG_DIR`, worktree-isolated, falls
  back to the default `$HOME/.claude` location for every one of the eight
  sites, not just `handoff`) is acceptable to ship. [inherited from the
  prior investigation's A3, which the engineer had already been made
  aware of for `handoff` specifically; this plan applies the same
  accepted trade-off uniformly rather than re-litigating it per site —
  flagged here for the engineer to confirm it still holds now that it
  applies repo-wide, not to one recipe.]

## Critical files

- `claude/.claude/skills/handoff/SKILL.md` (~line 160-171, "After writing:
  record the conversion signal") — replace the single compound fenced
  block with the prior investigation's already-drafted split (see
  `.claude/plans/handoff-nudge-log-worktree-path.md`, Critical files,
  "Drafted replacement" — reuse that text verbatim for the split
  mechanics), updating only the closing parenthetical per mechanism 2
  above.

- `claude/.claude/skills/ready-for-review/SKILL.md:72-73` (the
  `BASE_REF=$(...)` / `git diff $(...)` block) — split into three
  single-statement calls, each substituting the prior step's printed
  output as a literal:
  1. `gh pr view --json baseRefName --jq .baseRefName 2>/dev/null || echo main`
     — read the base branch name.
  2. `git merge-base origin/<literal-base-branch> HEAD` — read the
     merge-base SHA (this step still needs its own call because it's a
     second, independent value the next step needs as a literal; it has
     no assignment and no later statement, so Trigger A doesn't apply to
     it standalone, but the *original* combined form put it inside the
     same call as `git diff`, which is what triggered the refusal).
  3. `git diff <literal-merge-base-sha>...HEAD` — the actual diff, no
     `$(...)` left at all.
  Update the closing parenthetical per mechanism 2.

- `claude/.claude/skills/ready-for-review/SKILL.md:92-96` (the
  `BRANCH=$(...)` / two `python3 ...` calls, "Skill-procedural-fidelity
  review" section) — split into three single-statement calls: (1)
  `git rev-parse --abbrev-ref HEAD` to read the branch name; (2) and (3)
  each `python3` invocation with the branch substituted as a literal,
  each tried first with `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/..."`
  and retried with the literal `"$HOME/.claude/scripts/..."` fallback on
  refusal (mechanism 1's two-tier pattern). New site — currently carries
  no historical note; add one per mechanism 2's wording.

- `claude/.claude/skills/pr-description/SKILL.md:78-86` (the cost-gate
  `case`/`sentinel_path`/`mode` block) — replace the multi-statement
  trim/lowercase/compare chain with a single read plus model-side
  judgment, since the chain's only consumer is a yes/no branch: read
  `head -n2 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pr-cost-disclosure" 2>/dev/null`
  (retry with the literal `$HOME/.claude` fallback on refusal per
  mechanism 1), then evaluate directly against the printed text —
  exactly one line whose content, trimmed of leading/trailing whitespace
  only and lowercased, equals `dollars` → regenerate the Cost block;
  anything else (no output, a second line, any other value) → delete the
  block if one exists. This preserves the documented gate semantics
  exactly (same trim rule, same case-fold, same single-line requirement,
  same fail-closed-to-"off" default) while eliminating the assignment
  chain a worktree-isolated Bash call can't run in one piece. New site —
  add a historical note per mechanism 2's wording. **One deliberate
  narrowing, named rather than hidden:** the original's whitespace trim
  operates on the *whole captured multi-line string*, so a file
  consisting of a blank line followed by `dollars` would, after bash's
  `[:space:]` trim (which strips leading/trailing newlines too), collapse
  to a single line and read as "on." The replacement judges line count
  from the raw `head -n2` output directly, so that same input reads as
  two lines and judges "off." This narrows an unlikely, almost certainly
  unintended edge case (a hand-edited sentinel file with a leading blank
  line) in the direction the section's own stated design philosophy
  already prefers — "the gate prefers under-disclosing to guessing" —
  named here rather than left as a silent behavior change.

- `claude/.claude/skills/pr-description/SKILL.md:87-90` (the
  `branch="$(...)"` / `python3 ...` block) — split into two calls: (1)
  `git rev-parse --abbrev-ref HEAD` to read the branch; (2) the `python3`
  call with the branch substituted as a literal. Update the existing
  historical note per mechanism 2.

- `claude/.claude/skills/git-feature-branch-sync/SKILL.md:29-31`
  ("Detecting divergence") — split the four-statement block into four
  single-statement calls with literal substitution: (1)
  `git symbolic-ref refs/remotes/origin/HEAD | sed 's#^refs/remotes/origin/##'`
  to read the default branch; (2) `git fetch --no-tags --quiet origin
  <literal-default>`; (3) `git rev-list --count "HEAD..origin/<literal-default>"`
  to read the behind-count; (4) `git merge-tree --write-tree
  "origin/<literal-default>" HEAD`, unchanged since it has no assignment
  of its own — its printed output *is* the thing being inspected. Add a
  one-sentence note that the `check-branch-divergence.sh` hook using the
  same detection logic is unaffected — it runs as a script the harness
  invokes directly, not through the Bash tool, so this guard never
  evaluates it. New site — add a historical note per mechanism 2's
  wording.

- `claude/.claude/skills/respond-pr/SKILL.md:111-118` (the
  `TARGET_BODY=$(...)` / `case` block) — split into two calls: (1)
  `gh api repos/{owner}/{repo}/pulls/comments/{id} --jq '.body'` to read
  the target comment body; (2) inspect the printed text directly — if it
  starts with `**[Claude Code]**`, issue the `PATCH` call as its own
  single statement with the literal comment id and corrected body
  substituted; otherwise abort to the `/replies` form per the existing
  instruction, no shell `case`/`exit 1` needed. New site — add a
  historical note per mechanism 2's wording.

- `claude/.claude/skills/plan-review/SKILL.md:29-33` (Step 0's conditional
  plan-mode-path recipe) — split into two calls, only reached when a
  plan-mode reminder is present: (1)
  `~/.claude/scripts/marker.sh resolve-session-id` to read the session id
  (abort per the existing instruction if empty); (2) the `Write` tool call
  to `.plan-review-active.d/<literal-session-id>.planmode-path`. **Not** a
  config-dir-aware/fallback pair like the other sites: the `Write` tool's
  `file_path` argument is a literal string, never shell-expanded, so
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` pasted directly into it would try
  to write inside a directory literally named that expression rather than
  expanding it (a defect in this plan's first draft, caught in review, not
  a defect to carry into implementation) — and because *any*
  `$CLAUDE_CONFIG_DIR` reference refuses even a bare read (Trigger B),
  there is no Bash call that can resolve its value first either. Drop the
  `$CLAUDE_CONFIG_DIR`-aware attempt for this one site and construct the
  Write target directly against the literal `$HOME/.claude` path, removing
  the `CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"` assignment and
  its downstream reference from the Bash recipe entirely. New site — add a
  historical note per mechanism 2's wording, plus a one-sentence note that
  this site accepts the `$HOME/.claude`-only gap unconditionally (no
  retry), unlike the Bash-callable sites. This site is low-traffic (only
  exercised when this skill runs under harness plan mode) but identical
  in shape, so it's in scope per the structural-siblings rule.

- `docs/worktree-bash-guard.md` (new) — the full bisection table (all
  triggers A-E, refused/succeeded pairs, isolation follow-ups), the
  changelog cross-check with exact version numbers and quoted entries,
  the eight-site sweep table, and a short "how to re-verify" section
  (re-run the table's commands from a worktree-anchored session; note the
  installed `claude --version` and date next to any updated result) so
  this doesn't calcify into a second stale "fixed in X" claim. One
  sentence cross-referencing `docs/design-decisions.md`'s existing
  worktree-enforcement-rationale entry (why this repo opts into worktree
  isolation at all, a different question from what the harness's guard
  does once isolated) for a reader who lands on one doc looking for the
  other.

- `claude/.claude/CLAUDE.md` (Agent Briefing section) — one new bullet per
  Assumption ledger mechanism 4, above.

- `claude/.claude/skills/tests/test_skills.py` (near `_all_skill_md_paths()`
  at line ~1292, following the style of the parametrized tests
  immediately around it) — add the module-level test specified in
  mechanism 5: extract every fenced bash/sh block from every SKILL.md
  (via `_all_skill_md_paths()`) and flag any block assigning a variable
  via `$(...)` that is followed by another non-comment statement in the
  same block. **Reuse:** `_all_skill_md_paths()` already enumerates every
  SKILL.md (stowed and plugin) — call it directly. Must pass against all
  eight corrected recipes above and fail if any of their pre-fix compound
  forms is pasted back in (one-time local sanity check before committing,
  not a committed step, mirroring the prior plan's own Verification
  note).

## Verification

- Re-run the final fenced-block wording for all eight corrected sites as
  literal, separate Bash tool calls from a worktree-anchored session,
  confirming each split step succeeds — same discipline the prior plan's
  Verification section used, extended to every site in the sweep table,
  not just `handoff`.
- `../../../.venv/bin/pytest claude/.claude/` and
  `../../../.venv/bin/ruff check claude/.claude/` from the worktree — the
  new `test_skills.py` test must pass against all eight fixed recipes.
- `skill-management:skill-review` on each of the eight SKILL.md diffs
  (this repo's skill-review discipline requires it before staging any
  skill edit) and `ai-instruction-and-memory-files` on the CLAUDE.md
  bullet, both of which `/code-review` dispatches automatically for these
  file types — then `/code-review` itself before commit.

## Out of scope

- Reporting this as a harness bug to Anthropic. Worth doing given
  Triggers C/D/E are shapes the prior investigation never had a chance to
  report, but this repo's own CLAUDE.md (Safety) and the brief's §6.5
  both require confirming external-communication content with the
  engineer before sending — a separate follow-up, not part of this plan's
  implementation.
- `install.sh` asserting a minimum Claude Code version and hard-blocking
  install below it, with the corresponding CHANGELOG breaking-change
  entry — the engineer's preferred long-term replacement for per-recipe
  workarounds (mechanism 6, above). Blocked, not deferred by choice: no
  version currently fixes Triggers A/B, so there is nothing to gate on
  yet. Becomes actionable once the bug report above gets a fix version
  from Anthropic; track it against this plan and
  `docs/worktree-bash-guard.md` rather than re-deriving the mitigation
  strategy from scratch when that happens.
- A `test_skills.py` sweep hardcoding Triggers C/D/E as regression
  assertions — per Approach's alternatives, these are prose-covered by
  "quote everything, never pass `$PPID` as a whole argument, never
  reference `$CLAUDE_CONFIG_DIR` inline" without needing shape-specific
  tests that would need re-verification on every harness upgrade; Trigger
  A's test is landed because its shape is stable and syntactically
  well-defined, not because all five triggers are.
- Fixing or documenting the harness guard's implementation itself —
  confirmed harness-owned, not this repo's code (unchanged from the prior
  investigation's Out of scope).
- `docs/permission-prompt-tracking.md`'s human-run log-trimming recipe —
  a human-operator recipe, not something any agent session executes (per
  the prior investigation and the brief's own Out of scope).
- The `pr-cost --all-accounts` implementation work on branch
  `pr-cost-cross-account-scan-consent` — unrelated, noticed incidentally
  while working on it.
- Editing `.claude/plans/handoff-nudge-log-worktree-path.md` — preserved
  historical record per this repo's CLAUDE.md Axis 3; this plan's
  findings supersede its Postscript's conclusion in practice, but the
  file itself stays untouched, with this plan and
  `docs/worktree-bash-guard.md` as the citable correction.
