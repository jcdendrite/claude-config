# transcript-analysis.py reference

`transcript-analysis.py` is an analysis toolkit for Claude Code transcripts at `~/.claude/projects/*/*.jsonl`. Run it directly from the shell — there is no `~/.local/bin/` wrapper.

All subcommands are local-only reads except `pr-link` (calls `gh`) and `judgment-pair --out` (writes to a specified file). No other subcommand writes to disk.

For question-driven routing ("which subcommand answers X?"), use the `/transcript-analysis` skill. This page is the per-subcommand reference: flags, output shape, and when to reach for each one.

---

## Scoping to this repo: `--this-repo`

Every subcommand below accepts `--this-repo` as a mutually exclusive alternative to `--projects GLOB`. It resolves *this checkout's* worktrees by identity — `git worktree list`, matched as exact project-directory names — the same minimization control `skill-invocation` has used by default since it shipped, now available everywhere. Every subcommand that accepts it prints a one-line resolved-scope header (`<NAME> SOURCES (...)`) before its output, so a run is never ambiguous about whether it read one repo or the whole machine.

The default differs by subcommand: `skill-invocation` defaults to repo-scoped (safe-by-default) and treats `--this-repo` as a no-op; every other subcommand defaults to machine-wide (unsafe-by-default) and requires `--this-repo` to opt into repo scoping.

**`review-trace` output is not publish-safe under the default machine-wide scope** — each event line's branch string can carry a ticket ID or project name. Run it with `--this-repo` before quoting output anywhere public.

**What `--this-repo` does not cover, and the documented fallback:**

- **Other clones of this repo.** `git worktree list` enumerates the linked worktrees of the checkout you ran it from — a second, independent clone of the same repo elsewhere on the machine is correctly outside that set. There is no fallback for this; it is not this checkout's data.
- **A session started in a repo subdirectory.** Claude Code slugs a project directory from the session's *startup cwd*, not the repo root — replacing `/` and `.` with `-` — so a session started inside a subdirectory of a worktree has a slug that is string-unequal to that worktree's own slug — `--this-repo`'s exact-identity match excludes it. The fallback is a prefix glob derived from `--git-common-dir`, not `pwd` (`--git-common-dir` resolves to the main repo's `.git` from inside any worktree, so the prefix is stable regardless of which worktree the session started in):

  ```bash
  --projects="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)" | tr '/.' '-')*"
  ```

  The trailing `*` picks up both per-worktree project dirs and sessions started in a subdirectory. A prefix-with-separator match (`<slug>-*`) was considered and rejected as the default instead: it still collides with a sibling clone at `<repo>-fork`, which is exactly the collision `--this-repo`'s exact-identity match exists to prevent — this glob is a deliberately looser fallback for the one case exact identity can't reach, not a general replacement for it. Because it is a prefix, it also over-includes rows for any other sibling sharing that prefix; for `buckets` that shows up as extra rows in the output, visibly, rather than silently returning nothing.
- **An orphaned project directory.** If a worktree is removed, its project directory under `~/.claude/projects/` is not cleaned up automatically, and its slug no longer matches any live `git worktree list` entry — it is silently excluded from `--this-repo`. This is the same behavior `skill-invocation`'s default scope has always had.
- **A subagent dispatched from another repo's session.** With `--include-subagents`, `--this-repo` does read every subagent file whose *parent* session ran in this repo — that path works. The gap is the inverse: a parent session anchored in a different repo that dispatches a subagent whose own cwd is inside this repo. That subagent's transcript still lives under the *parent's* project directory, so no scope resolved by directory identity — `--this-repo` or otherwise — ever reaches it. More generally, most subagent cwds have no project directory of their own at all, so a name-based match has nothing to find regardless of scope. The fallback is content-based, not directory-based: content-grep across `*/subagents/*.jsonl`, or traverse those files and read each one's own `cwd` field directly. Note that `--include-subagents` is off by default on every subcommand that offers it, so even a correctly-scoped run under-reports subagent work unless it's passed explicitly. For enumerating sessions after a crash rather than analyzing scoped history, see `post-crash-sessions` ([`docs/scripts.md`](scripts.md)).

All four gaps are silent under-coverage, not an error: a narrower-than-expected corpus reads identically to "no evidence exists" unless you notice the resolved-scope header's project-dir count is lower than expected.

`cost --config-dir` sidesteps all four gaps a different way: it refuses `--this-repo` outright rather than silently mis-scoping, since `--this-repo`'s identity match can only resolve one config dir's worktrees. See the `cost` section below for the full `--config-dir` contract.

---

## buckets

**Purpose.** Show assistant-turn counts bucketed by git branch × model family, with session count and date range.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`, all projects)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--branches B1,B2,...` — filter to specific branches (default: all)

**Sample output.**
```
BUCKETS SOURCES (this repo (6 project dirs))
Branch                                   Proj  Sess   Total   Opus  Sonnet  Haiku  Other  Date range
-----------------------------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand     1     2     237     78     159      0      0  2026-05-26..2026-05-28
GH-315/audit-routing-shape                  1     5     375     45     330      0      0  2026-05-26..2026-05-26
add-actionlint-pr-gate                      2     5     444      0     444      0      0  2026-05-25..2026-05-26
HEAD                                        6   401    3664   1261    2278    120      5  2026-04-23..2026-05-28
```

`Proj` is the count of distinct project directories contributing to that row — a pooled row (several repos sharing a branch name, `main` being the usual case) shows `Proj > 1`; scope with `--this-repo` or a narrower `--projects` glob to collapse it back to `1`.

**When to reach for it.** Survey all branches and spot which ones used which models. Usually the first command to run on any transcript analysis session.

---

## fail-seq

**Purpose.** Emit the ordered sequence of test-failure counts per branch/model to distinguish convergent debugging (spike then zeros) from thrashing (sustained oscillation).

**Flags.**
- `--branches B1,B2,...` *(required)* — branches to analyze
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)

**Sample output.**
```
### my-feature-branch
Total runs: 12  Failing: 3 (25.0%)  Longest consecutive-failing streak: 2
  opus    : 8 runs, 2 failing (25.0%)
  sonnet  : 4 runs, 1 failing (25.0%)
Sequence: 0 0 5 0 0 0 3 0 0 0 0 0
```

- **Convergent** (expected): a spike followed by zeros — the root-cause fix lands and holds.
- **Thrashing** (flag for review): oscillation like `8 6 9 7 8` with no sustained run of zeros.
- The `longest consecutive-failing streak` is the load-bearing metric. A streak of 5+ warrants a closer look.

**When to reach for it.** After a branch completes: verify the debugging loop converged. Compare two branches side by side with a comma-separated `--branches` list.

---

## struggle

**Purpose.** Count correction and frustration signal phrases in user turns, split by model, to surface whether one model generated more rework prompts.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)

**Sample output.**
```
Branch                          Opus  Sonnet  Haiku  Other  Unknown
---------------------------------------------------------------------
some-feature-branch                1       3      0      0        0
another-branch                     0       2      0      0        0
```

Each cell is the count of signal phrases ("no, that's wrong", "stop doing", "you misunderstood", etc.) in user turns that follow an assistant turn from that model family.

**When to reach for it.** A/B model comparison: run on two branches worked with different models to see if one generated more correction prompts. One or two branches per model is directional, not controlled.

---

## duration

**Purpose.** Decompose branch time into active work spans versus idle gaps.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--gap-minutes N` — threshold (in minutes) for classifying an inter-turn gap as idle (default: 30)

**Sample output.**
```
Branch                                    Span(min) Active(min)  Idle(min)  Sessions  GapMin
-----------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand        1553         112       1442         5      30
```

**When to reach for it.** Estimate how many hours were actually spent on a branch, stripping calendar time. Use `Active(min)`, not `Span(min)` — the span is wall-clock dominated by idle gaps.

---

## subagents

**Purpose.** Show `isSidechain` (subagent) versus main-thread turn counts per branch, split by model family.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)

**Sample output.**
```
Branch                                   Thread       Opus  Sonnet  Haiku  Other
-----------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand  main           78     159      0      0
GH-333/audit-routing-samples-subcommand  sidechain       0      78      0      0
```

**When to reach for it.** Understand how much work was delegated versus inline. Compare against `subagent-mix` for a breakdown of what *kind* of subagents were spawned.

---

## subagent-mix

**Purpose.** Show per-branch subagent spawn type counts and code/plan/ready-for-review review-skill spawn counts.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--per-session` — break out by individual session instead of aggregating per branch

**Sample output.**
```
Branch                                         Sess  Spawns  CR  PR  RR  Top subagent types
------------------------------------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand           2      11   2   1   2  staff-sdet(4), code-writer(3), check-runner(2), staff-backend-engineer(2)
```

Columns: `CR` = `/code-review` spawns, `PR` = `/plan-review` spawns, `RR` = `/ready-for-review` spawns.

**When to reach for it.** Audit which reviewer agents fired on a branch, or compare how delegation patterns differ across branches. The tool recognizes `check-runner` in historical session data — the agent is retired (2026-06-23) but historical transcript entries remain valid corpus inputs.

---

## reviewer-yield

**Purpose.** Per-reviewer-agent-type dispatch-to-verdict yield: for each main-thread reviewer-agent dispatch (`staff-*`, `ciso-reviewer`, `skill-fidelity-reviewer`), joins it to its own subagent transcript via `subagents/<id>.meta.json`'s `toolUseId` field, then classifies the transcript's last assistant text block as findings-found, zero-finding, or unclassified — answering "are reviewer-agent dispatches producing real findings, or mostly zero-finding passes" (the efficiency-audit tracking issue's F4). A second table reports, per (agent type, verdict bucket), whether the paths a reviewer cited were later edited — answering "did the session subsequently act on what was cited," not only whether the reviewer spoke (GH-558).

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit to dispatches with timestamp in the last N days (e.g. `30d`)
- `--redact` — accepted for CLI parity with `cost`/`audit-routing`; a no-op in practice, since cited-path candidates are held only as sha256 digests and never surface as raw paths, so both tables stay aggregate-only by construction. This does not cover the pre-existing `--projects` scope-header line (shared by every subcommand — see the `cost` section's redaction notes above).

**Sample output.**
```
## Reviewer-agent yield (last 30d)

AgentType                    Dispatches   Found   Zero  Unclass  Findings
-------------------------------------------------------------------------
ciso-reviewer                       230      87     32      111       265
staff-sdet                          283     130     23      130       449

## Reviewer-agent cited-path edit overlap (last 30d)

AgentType                    Bucket          Dispatches  Cited Active Edited         Rate
-----------------------------------------------------------------------------------------
staff-sdet                   findings-found         297    297    296    124        41.9%
staff-sdet                   zero-finding            30     30     30      7        23.3%

  (Active/Edited count parent-main-thread edits only; see docs for the cost-gate fallback.)
```

**When to reach for it.** Judge whether a reviewer agent's dispatch volume is worth its cost. Verdict classification is best-effort: it recognizes the `**No X concerns**`, `Wrote findings to <path>. Found <N> issues.`, `**Approve with concerns**`, and `**Request changes**` contract shapes (case-insensitive, bold-optional, singular/plural-tolerant) documented in `claude/.claude/agents/*.md`. The bulleted `**Approve with concerns**`/`**Request changes**` verdicts land in `Found` alongside the numeric-count verdicts, but carry no derivable count of their own — `Findings` is therefore a lower bound on actual findings, not an exact total. A dispatch whose `subagents/*.meta.json` sidecar can't be resolved at all is excluded entirely, not counted as `Unclass`. A `subagents/*.meta.json` sidecar that exists but is unreadable (invalid JSON) or is missing `toolUseId` is a second, distinct exclusion path — also excluded entirely, and corpus-wide counted in a `(N meta.json files failed to parse, excluded)` line printed under the table.

The second table's columns: `Cited` = dispatches yielding at least one extracted, path-normalized citation (excluding the dispatch's own findings-file target and any cited plan file, which would otherwise self-match a `/plan-review` dispatch against the plan the parent then edits). `Active` = of those, dispatches after which the session recorded any code edit at all — a null control for "was the session still working," not yet path-specific. `Edited` = of the `Active` ones, a *cited* path itself was among the edited paths — the real cited-path-overlap signal. `Rate` = `Edited ÷ Active`, so it cannot exceed 100%. `insufficient` in `Rate` means `Active` fell below 10 for that cell — too few qualifying dispatches to report a rate. `excluded` marks the `unclassified` bucket, which this table doesn't score at all. **`Active`/`Edited` currently count parent-main-thread edits only** — a measured cost gate (subagent-transcript edit reads added roughly 16s over a 13.5s all-time baseline on a `--since 30d` run) triggered the plan's own pre-committed fallback of excluding subagent-sourced edits from the index. This repo's own `CLAUDE.md` mandates routing implementation work to a `code-writer` subagent, so `Active`/`Edited` undercount real fix work whenever it happened there rather than in the reviewing session's own main thread — a known, named limitation, not a silent gap.

---

## pr-link

**Purpose.** Map branches to GitHub PRs and pull per-PR comment counts. Requires `gh` and network access.

**Flags.**
- `--repo OWNER/REPO` *(required)* — GitHub repository
- `--branches B1,B2,...` *(required)* — branches to look up
- `--author LOGIN` — filter comment counts to one GitHub login
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)

**Sample output.**
```
Branch                    PR#   Title                              Author comments  Total comments
---------------------------------------------------------------------------------------------------
feat-TICKET-101           #42   Add new widget component                         3              8
feat-TICKET-202           #47   Refactor auth middleware                          1              5
```

**When to reach for it.** After a set of branches lands: measure review engagement per branch or filter to one author's comments to count their review activity.

---

## skill-pair

**Purpose.** Measure the pairing rate between two skills — how often the follower fires in the same session as the leader — bucketed by ISO week.

**Positional args.**
- `LEADER` — leading skill name (exact match on `input.skill`)
- `FOLLOWER` — following skill name (exact match on `input.skill`)

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--exclude-projects GLOB` — skip project dirs matching this glob
- `--branches B1,B2,...` — filter to specific branches

**Sample output.**
```
Bin         Lead  Main  Side   Pair%
-------     ----  ----  ----   -----
2026-W18      39    18     0   46.2%
2026-W19     108    56     0   51.9%
2026-W20     149    95     0   63.8%
2026-W21     161   113     0   70.2%
```

`Lead` = sessions where the leader fired. `Main` = of those, sessions where the follower also fired on the main thread. `Pair%` = `Main / Lead`.

**When to reach for it.** Track whether a skill pairing (e.g. `code-review` → `ready-for-review`) is gaining compliance over time, or measure the effect of a new convention.

---

## commit-gate

**Purpose.** Per-week gate-compliance: did a given skill precede each `git commit` in the same session? Detects `--no-verify` usage.

**Positional args.**
- `skill` — skill name to check (byte-equal match against Skill tool_use `input.skill`)

**Flags.**
- `--by-permission-mode` — split rows by `permissionMode`
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--exclude-projects GLOB` — exclude project dirs matching this glob
- `--branches B1,B2,...` — branch name filter (default: all)

**Sample output.**
```
bin          sessions   turns  skill-inv  skill/1k commits  w-skill  wo-skill  no-verify
----------------------------------------------------------------------------------------
2026-W18           72   13920         97               7.0      166       86        80          0
2026-W19          202   24828        175               7.0      215      134        81          0
2026-W20          484   29588        216               7.3      230      134        96          0
```

`w-skill` = commits preceded by the skill in the same session. `wo-skill` = commits without it. `no-verify` = `git commit --no-verify` calls detected.

**When to reach for it.** Measure whether a review skill is consistently running before commits, and whether hook bypasses are occurring.

---

## skill-invocation

**Purpose.** Per-skill invocation-source tally for a scoped set of projects (this repo by default), split into three buckets: `top-level` (description-triggered Skill tool_use on a main-thread turn with no parent skill active), `routed` (Skill tool_use fired while another skill's body was active — `attributionSkill` is non-empty), and `user-slash` (user record containing `<command-name>/skillname</command-name>`, the `/slash` invocation path). The classification summary at the bottom identifies routed-only candidates (name-only eligible) and slash-only candidates (disable-model-invocation eligible) for skill-description budget analysis.

**Scope defaults to this repo.** With `--projects` unset, the read is scoped to *this repository's own worktrees*, derived from `git worktree list` and matched by exact directory identity. This is a minimization control: the output is routinely quoted into public PR descriptions, and skill names on the machine can themselves be private-project identifiers. It fails closed (error, no output) rather than falling back to a machine-wide read if the repo scope cannot be derived. Passing an explicit `--projects` glob is an escape hatch for corpus analysis — the output is then no longer scoped to this repo.

**Flags.**
- `--projects GLOB` — project directory glob. Default: this repo's own worktrees only (publish-safe); an explicit glob is the escape hatch (not repo-scoped).
- `--this-repo` — explicit no-op: `skill-invocation` already defaults to this repo's own worktrees. Kept so the flag is uniform across every `--projects` subcommand; mutually exclusive with `--projects` like everywhere else.
- `--branches B1,B2,...` — restrict to named `gitBranch` values (default: all branches in scope).
- `--include-subagents` — also count invocations inside spawned subagents, adding a `thread` column (`main` vs `sidechain`). Off by default so budget analysis stays main-thread-only; the fidelity consumer opts in.

**Sample output** (default scope — this repo, main thread):
```
SKILL INVOCATION SOURCES (this repo; main thread)
skill                                    top-level  routed  user-slash    total
--------------------------------------------------------------------------------
code-review                                    207       8           2      217
plan-review                                     91       4           5      100
subagent-delegation                             44       0           0       44
skill-review                                     0      38           1       39
ready-for-review                                 0       3          31       34

ROUTED PAIRS (parent -> child : count)
  code-review -> skill-review : 38

CLASSIFICATION SUMMARY
  Load-bearing (any top-level or slash invocations):
    code-review (207 top, 2 slash)
    plan-review (91 top, 5 slash)
    subagent-delegation (44 top, 0 slash)
    ready-for-review (0 top, 31 slash)
    skill-review (0 top, 1 slash)
  Routed-only candidates (zero top-level and zero slash — name-only eligible):
    (none)
  Slash-only candidates (zero top, zero routed — disable-model-invocation eligible):
    (none)
```

**When to reach for it.** Two consumers. (1) Auditing which skills reach users via description-matching versus only via explicit `/slash` invocation or routing from a parent skill — the classification summary surfaces routed-only candidates (their description never independently fires — name-only conversion saves description-budget tokens) and slash-only candidates (only reach users via explicit command — disable-model-invocation conversion is safe). (2) The `skill-fidelity-reviewer` at `/ready-for-review`, which runs `--branches <branch> --include-subagents` to list every procedure a branch's work committed to, then checks each was executed rather than silently abbreviated.

---

## review-trace

**Purpose.** Emit an ordered event timeline per session — skill invocations, hook denials, and reviewer-agent spawns.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above); required before quoting output publicly (see below)
- `--branches B1,B2,...` — filter to specific branches
- `--since DATE` — inclusive start date (`YYYY-MM-DD`)
- `--until DATE` — inclusive end date (`YYYY-MM-DD`)
- `--deny-only` — restrict to sessions containing at least one hook denial
- `--deny-summary` — replace the per-session event listing with corpus-wide denial-count tables and a friction-kind breakout (see "`--deny-summary`" below)
- `--skill NAME` — restrict skill-invocation matching to one skill name

Branch and model are resolved *per event*, from the record that produced it — not from the session's first record — so a session that moves from one branch or model to another attributes each event correctly, and `--branches` filters by that per-event value. An event whose branch or model cannot be resolved renders `?`.

**Sample output.**
```
REVIEW TRACE SOURCES (this repo (6 project dirs))

### ~/.claude/projects/my-project/abc123.jsonl
branches=main,my-feature  models=opus,sonnet  skills=3  denials=1  reviewer-spawns=4
  [2026-05-20T10:15:00.000Z] line   45  skill        plan-review  (branch=main model=opus)
  [2026-05-20T10:17:30.000Z] line   62  reviewer     staff-backend-engineer  (branch=my-feature model=sonnet)
  [2026-05-20T10:17:31.000Z] line   63  reviewer     staff-sdet  (branch=my-feature model=sonnet)
  [2026-05-20T10:45:00.000Z] line  120  denial       hook=  id=toolu_abc  msg='marker.sh invocation denied...'  (branch=my-feature model=sonnet)
  [2026-05-20T11:02:00.000Z] line  145  skill        code-review  (branch=my-feature model=sonnet)
```

The session above opened on `main`, then moved to `my-feature` partway through — the header's `branches=`/`models=` lists both, and each event line carries its own attribution rather than inheriting the session's first-record branch.

**When to reach for it.** Audit which sessions hit hook denials (`--deny-only`), or compare review-skill activity before vs. after a convention landed using `--since`/`--until`. The timeline locates sessions; judging whether a review caught a material issue is a qualitative read.

### `--deny-summary`

Replaces the per-session event listing with a corpus-wide census: three grouped tables plus a friction-kind breakout, aggregated across every session in scope rather than printed per session. Combine with `--deny-only`, `--branches`, `--since`/`--until` to bound the window the same way as any other `review-trace` run.

**Sample output** (synthetic, illustrative counts only).
```
Corpus window: 2026-06-24 to 2026-08-05

## Denials by hook/gate (14 total)

Hook/gate                                Count
-----------------------------------------------
worktree-enforcement                          6
marker-script-shape                           5
unmatched                                     3

## Denials by attempted command shape (14 total)

Shape             Count
-----------------------
git commit             5
marker.sh write        4
other                  3
git checkout           2

## Denials by hook/gate x command shape (14 total)

  Hook                                  git checkout  git commit  marker.sh write  other
  -----------------------------------------------------------------------------------------
  marker-script-shape                              0           0                4      1
  unmatched                                         1           0                0      2
  worktree-enforcement                              1           5                0      0

## Friction events by kind (9 total)

Kind                     Count
-------------------------------
user-rejected                  5
automode-blocked               2
automode-unavailable           1
interrupted                    1

3 errored, non-gate tool result(s) predate the per-record denial-kind field's introduction (2026-07-20) and are excluded from the breakdown above — kind is structurally unmeasurable before that date, not zero.
```

- **The two marginal tables and the cross-tab.** "Denials by hook/gate" and "Denials by attempted command shape" are independent marginals over the same denial population `--deny-only` selects; "Denials by hook/gate x command shape" cross-tabs the two, since the marginals alone can't say which hook denied which command shape — a hook's row, read across the cross-tab's columns, is the only place that join is visible.
- **`other` and `unmatched` are open buckets, not errors.** A command shape falls to `other` when the denied command isn't a recognized `git`/`gh`/`marker.sh` invocation (or its subcommand-position token looks like a flag rather than a subcommand); a hook/gate label falls to `unmatched` when the denial message matches the general hook-denial signature but names no hook the classifier recognizes. Both buckets stay printed — a shape or label that needs adding to the classifier shows up as a nonzero count here rather than being silently absorbed.
- **Friction events are a different axis from denials.** A `denial` event is a hook or `permissions.allow` block, matched by message text. A `friction` event is one of four other reasons a tool call didn't go through, read from the record's own `toolDenialKind` field: `user-rejected` (a permission prompt the user declined), `automode-blocked`, `automode-unavailable`, or `interrupted` (`[Request interrupted by user for tool use]`). A `toolDenialKind` value outside that four-value set prints as `other-kind` rather than being echoed raw. Friction events never change `has_denial`, the per-session `denials=N` header, or `--deny-only`'s session-selection — those three stay denial-kind-only. `--deny-summary` is the only surface that tallies friction into a table; without it, the default per-session timeline still renders a `friction` line for each one, but nothing counts them.
- **Combined with `--deny-only`.** Friction counts are tallied from a session's full event list before the `--deny-only` session filter is applied, so a session whose only events are friction (no denials at all) still contributes to the friction breakout even though `--deny-only` alone wouldn't select that session for the default timeline view.
- **Corpus window and the pre-regime caveat.** The printed window is the earliest/latest timestamp among in-scope events, after `--branches`/`--since`/`--until` are applied. `toolDenialKind` was not recorded on any transcript before 2026-07-20, so an errored, non-gate tool result timestamped earlier than that can't be classified into the friction breakdown at all. Those records are counted separately, on the line under the friction table, rather than folded into the breakdown as zero friction.

---

## judgment-pair

**Purpose.** Extract `(review-skill output, user response)` pairs from sessions where a review skill was invoked. For each matching skill invocation, locates the last main-thread assistant text turn before the next user prompt or next invocation (whichever comes first), then captures the first genuine user reply after that window. Tool-result turns, `isMeta` injections, and `isCompactSummary` records are automatically skipped when searching for the user response.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`, all projects)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--branches B1,B2,...` — filter to specific branches
- `--since DATE` — inclusive start date (`YYYY-MM-DD`)
- `--until DATE` — inclusive end date (`YYYY-MM-DD`)
- `--skills SKILL1,SKILL2,...` — comma-separated skill names to match (default: `code-review,plan-review,ready-for-review`)
- `--truncate-chars N` — maximum characters for the review output block (default: 1000)
- `--out PATH` — write output to this file instead of stdout

`--branches` filters on the invocation record's own `gitBranch`, not a session-wide branch — a session whose branch changes between the review invocation and the user's reply is selected by where the invocation itself happened.

**Sample output.**
```
### my-project · abc12345 · 2026-05-20
Skill: code-review  branch=main  (line 42)

--- REVIEW OUTPUT (truncated to 1000 chars) ---
The auth middleware bypasses rate limiting when the `X-Internal` header is
present. Any caller who can set that header gains unrestricted access. The
header check should be restricted to requests originating from the internal
load balancer, validated by IP, not by header value alone.

--- USER RESPONSE ---
Good catch. I'll add IP-range validation before trusting that header.
---
```

**When to reach for it.** Surface sessions where the human pushed back on, accepted, or acted on a review skill's output. Use as a dataset for evaluating review quality — look at what the user said immediately after the AI review to judge whether the finding was acted on, disputed, or ignored. Use `--out` to save output for offline analysis.

---

## audit-routing

**Purpose.** Per-turn Opus token breakdown by routing class (`orchestration`, `judgment`, `code-write`, `code-read`, `pure-thinking`, `other`), aggregated across sessions with a price-weighted Sonnet-tier estimate — the dollar-weighted headline, priced via the same `_price_turn` primitive `cost` uses, prints first; the original output-token estimate prints below it as a secondary diagnostic.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit to the last N days (e.g. `35d`)
- `--top N` — maximum per-session rows to emit (default: 20)
- `--redact` — replace project dir names with anonymized labels (`private-project-1`, `private-project-2`, …); `claude-config` is preserved. Use this flag when posting output to GitHub issues.

**Sample output.**
```
## Opus turn-class breakdown (last 30d)

Session          Proj                     orch  judgment  code-write  code-read  thinking   other   total_out   cache_rd
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
session-119      private-project-15     23,054       107      31,598     39,045    65,658  60,805     220,267 15,789,467
session-223      private-project-20     23,059         0      36,663     34,271    60,155  62,058     216,206 11,555,870

## Corpus aggregate

Class              Output tokens  Cache read tokens
orchestration          1,935,893         48,688,324
judgment                  10,499          6,247,896
code-write             1,214,689         59,676,399
code-read              1,531,175        165,397,638
pure-thinking          3,226,641        196,674,409
other                  3,505,919        206,850,948
───────────────────────────────────────────────────
total                 11,424,816        683,535,614

Sonnet-tier estimate: $323.63
  = 30% of priced Opus spend in this window

Sonnet-tier estimate: 2,745,864 output tokens (secondary diagnostic)
  = 24% of Opus output in this window
```

An Opus turn whose model ID has no pricing-table entry is excluded from the dollar figure and counted separately — a `(N unpriced turns / M tokens excluded from priced spend)` line appears under the dollar headline whenever the window contains one, so a corpus with unpriced Opus turns doesn't silently under-report.

**When to reach for it.** Answer "is Opus spend doing Sonnet-tier code-read/write in parent sessions?" — the dollar-weighted `Sonnet-tier estimate` headline is the number to cite; the token-based line below it is a secondary diagnostic only, since output tokens alone can understate a routing class's true spend share by tens of percentage points relative to its dollar share (cache-read and cache-write dominate real billing). Always use `--redact` before posting output publicly.

---

## cost

**Purpose.** Price-weighted dollar cost by token class (cache read, cache write 5m/1h, output, input), model ID, context-at-turn bucket, and main-thread-vs-subagent thread, plus a top-N-sessions-by-dollars breakdown and an optional `--by-project` per-project breakdown. Every other subcommand in this toolkit is denominated in raw token counts; `cost` is the one that answers "which lever actually moves the bill," since cache-read is 0.1× base input while output is 5× — a 50× spread raw token counts erase. **Context-at-turn** is defined as `input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m` tokens for a turn — the sum of everything read into context for that turn, excluding output.

Pricing is looked up per exact model ID (Sonnet 5 and Sonnet 4.6 price differently), derived from one base input rate per model plus the pricing page's stated multipliers (output 5×, 5m cache write 1.25×, 1h cache write 2×, cache read 0.1×). Each model ID carries a re-verify-by date; when the current date is past it, a `STALE PRICING` banner prints inside the same output block as the dollar tables — never a separate log line a copy-paste of the tables could drop. An unrecognized model ID (e.g. `<synthetic>`) is never silently priced at $0: it gets its own row and its tokens are counted in a separate "Unpriced tokens" total, excluded from every dollar figure. Sidechain (subagent-dispatched) turns are priced — `cost` reads with `include_subagents=True`, unlike `audit-routing`'s main-thread-only scope, since subagent spend is real spend.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`, all projects)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above); mutually exclusive with `--projects`
- `--branches B1,B2,...` — filter to specific branches (default: all). Per-record, not per-session: a single session routinely spans branches, so a session-level filter would misprice it in both directions. A `worktree-agent-*` subagent record's own literal `gitBranch` is not what `--branches` filters on — see "Worktree-isolated subagent attribution" below.
- `--config-dir DIR` — additional Claude Code config directory to scan (repeatable). The default resolved config dir (`$CLAUDE_CONFIG_DIR`, or `~/.claude`) is always scanned first; each extra directory must contain its own `projects/` subdirectory or the run is rejected. Use this to union spend across account profiles (e.g. a machine running several `~/.config/claude-accounts/<account>/` profiles) into one report. Refused together with `--this-repo` (it cannot filter a foreign config dir's worktrees) and with `--no-redact` (more than one root in scope would put one profile's real project names into a report meant to also cover another).
- `--by-project` — add a per-project cost breakdown, keyed on (account root, project family); composes with both `--projects` and `--this-repo`. One repo's own worktrees collapse into a single family row instead of fragmenting per branch. When `--config-dir` puts more than one root in scope, each row also carries an `Account` column using the same `account-N` labeling the per-root scan-summary lines use. Under `--no-redact` (single-root only — see below), the Project column shows the raw, suffix-collapsed directory name instead of a `private-project-N` placeholder, same as every other section of this report. Refused together with `--summary`.
- `--since Nd` — limit to turns with timestamp in the last N days (e.g. `30d`)
- `--top N` — maximum per-session rows in the top-N-by-dollars section (default: 20)
- `--no-redact` — emit real project names and session IDs instead of anonymized labels. `cost` is **redacted by default** (the opposite default from `audit-routing`) since its documented purpose includes producing text for public issues; never publish `--no-redact` output. Refused when `--config-dir` puts more than one root in scope, and refused together with `--summary`.
- `--summary` — a distinct, aggregate-only rendering mode meant to be embedded directly in a PR body (see the `pr-description` skill's `## Cost` section). Requires `--this-repo` and refuses any other scope flag, including a non-default `--projects` glob — every project-directory slug is absolute-path-derived and therefore starts with `-`, so a glob like `-*` would otherwise be machine-wide despite not being the literal default `*`. Also refuses `--by-project`, `--no-redact`, and `--config-dir` in combination — each drives an identity-bearing code path (`## Cost by project`, raw labels, multi-root scan-summary lines) `--summary` structurally never reaches. It never builds or reads the redact map, never prints the `DO NOT PUBLISH` banner, and never emits a per-root raw-path label — nothing it prints is keyed by project or session identity. It does print the scan-count diagnostic ("scanned N transcripts, M skipped") and the zero-scope `WARNING`, since those are identity-free under single-root `--this-repo` scope and are what makes an empty or under-scanned corpus visible instead of a silent `$0.00`. Always prints "Unpriced tokens: N tokens across M model IDs", even at zero. Carries the `STALE PRICING` banner in the same block as the dollar tables, same as the full report.

Redacted project labels (`private-project-N`) and the printed corpus fingerprint are **not comparable across two separate report runs** — a changed corpus (a new session, a removed project dir, a different `--config-dir` set) can renumber every ordinal, single- or multi-root. Treat each run's redacted output as self-contained; never diff `private-project-3` between two runs and assume it names the same project.

**Worktree-isolated subagent attribution.** A subagent dispatched with `isolation: "worktree"` runs on a harness-generated `worktree-agent-<hash>` branch, not the branch that dispatched it. `--branches` filters on each record's *attributed* branch, not that literal value: for every `worktree-agent-*` record, `cost` resolves the dispatching session's own branch active at that record's timestamp (falling forward to the session's earliest branch if the record predates every main-thread record), and folds the subagent's dollars and tokens into that branch's total — the same real spend a plain literal-`gitBranch` filter would otherwise silently drop. The one genuinely unattributable case — a session with no main-thread branch-bearing record at all — renders `?` (reusing the `?` sentinel `review-trace`/`judgment-pair` already use for "no signal to carry forward") and is excluded from every `--branches`-filtered total. Attribution is scoped to the dispatching session only; a `worktree-agent-*` record is never resolved against a *different* session's main-thread history.

**The disclosed fields are not neutral.** `--summary`'s output is aggregate-only, but "aggregate" does not mean "safe to publish by default": session count and priced-turn count signal how much engagement went into a branch, per-class token volume signals how long that engagement ran, and per-model-ID dollars discloses which models are in use. That is the intended read for a repo that opts into publishing it (see `pr-description`'s `## Cost` section and `docs/hooks.md`'s `pr-cost-disclosure` entry) — it is not a property of the output format itself, and a repo enabling the gating sentinel for an unrelated reason should not assume these fields are harmless to expose.

The branch itself is never echoed in `--summary`'s text — it only narrows which records the tables below are computed from — so a reviewer confirms scope by re-running the printed command, not by reading a label in the output.

**Sample output (`--summary`, synthetic, illustrative counts only).**
```
## Cost summary (all time)
Scope: 6 transcripts scanned, 4 priced sessions, 812 priced turns

## Cost by token class

Class                       $   Share         Tokens
cache_read              612.19   48.9%      6,121,900
cache_write_5m          310.44   24.8%      2,483,520
output                  270.02   21.6%        540,040
input                    58.87    4.7%      1,177,400
total                 1,251.52

## Cost by model ID

Model                                     $   Share
claude-sonnet-5                    1,251.52  100.0%

Unpriced tokens: 0 tokens across 0 model IDs

## Cost by thread

Thread          $   Share
main            975.32   77.9%
subagent        276.20   22.1%
```

**Sample output (full report).**
```
## Cost report (last 30d)

## Cost by token class

Class                         $   Share
cache_read             3,037.00   51.4%
cache_write_5m         1,533.00   26.0%
cache_write_1h           572.00    9.7%
output                   755.00   12.8%
input                      8.00    0.1%
total                  5,905.00

## Cost by model ID

Model                                     $   Share
claude-sonnet-5                    4,850.00   82.1%
claude-opus-5                        674.00   11.4%
claude-opus-4-8                      289.00    4.9%
claude-sonnet-4-6                     93.00    1.6%
<synthetic>                        unpriced      1,240 tokens

Unpriced tokens (unknown model IDs): 1,240

## Cost by context-at-turn bucket (input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m tokens, 200,000 boundary)

Bucket                $   Share
<200k          1,866.00   31.6%
>=200k         4,039.00   68.4%

## Cost by thread

Thread          $   Share
main       4,612.00   78.1%
subagent   1,293.00   21.9%

## Cost by project  (only with --by-project)

Project                       $   Share
private-project-13        612.00   10.4%
private-project-2         458.00    7.8%

## Top 20 sessions by dollars

Session          Proj                                  $
session-1        private-project-13               244.24
session-2        private-project-2                189.07
```

**When to reach for it.** Rank workflow-efficiency levers by dollars instead of raw token counts before proposing an optimization — a metric denominated in output tokens alone (like `audit-routing`'s Sonnet-tier estimate) can headline 0.6% of spend while missing an 87%-of-spend context-cost problem entirely. Also the source of the redacted, aggregate-only tables that go into a public cost-audit issue — never publish the top-N-sessions section or any `--no-redact` output, both of which are real-project-identifying by construction. If that issue is filed via `gh issue create`, note that `deny-private-project-refs.sh` does not scan `gh issue create`/`gh issue comment` bodies at all — see `docs/private-project-redaction.md`'s "Known gaps" section — so this redaction is not backstopped by the hook on that publish path. Even a `--this-repo`-scoped, redacted table's `private-project-N` numbering is shaped by the operator's full local project corpus, not just this repo — see `_build_redact_map`'s docstring for the ordinal side-channel this implies. Observed wall-clock for a `--since 30d` run against this toolkit's own transcript corpus: ~10s with `--no-redact`, ~18s with the default redacted run — the redact-map first pass roughly doubles the time, since it fully parses every transcript once just to read a directory name (see `iter_sessions`' docstring; this is a known, deliberately deferred perf gap, not a `cost`-specific one).

---

## cost-trend

**Purpose.** Per-ISO-week dollar spend, Opus-family share, and `>=200k` context-bucket share — the standing week-over-week view neither `cost` (a single-window snapshot) nor `audit-routing` provides on its own. Reuses `cost`'s `_price_turn` pricing and `handoff-ratio`'s ISO-week bucketing rather than introducing a second date-bucketing convention.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)

No `--redact` flag: like `handoff-ratio`, this subcommand's output (week / $ / context-share % / Opus-share %) is aggregate-only and names no per-session or per-project field.

**Sample output.**
```
Week                              $  Context%   Opus%
-----------------------------------------------------
2026-W30                   1,695.42     49.0%   23.0%
2026-W31                   5,419.44     62.0%   14.7%
2026-W32 (partial)         2,385.73     61.0%   13.6%
```

The most recent bucket is very likely a partial week — it is labeled `(partial)` explicitly rather than presented as a complete week's total, so it doesn't misread as a real week-over-week drop once history is only a few weeks deep.

A turn whose model ID has no pricing-table entry is excluded from every week's totals and counted separately — a `(N unpriced turns / M tokens excluded from priced spend)` line appears under the table whenever the window contains one, mirroring `audit-routing`'s unpriced-turns convention.

**When to reach for it.** Answer "is spend climbing week over week, and is the composition (Opus share, long-context share) shifting" as a standing instrument, rather than re-running `cost --since 30d` by hand and eyeballing the delta against a stale snapshot.

---

## handoff-ratio

**Purpose.** Per-week ratio of explicit `/handoff` invocations versus auto-compaction events.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since DATE` — inclusive start date (`YYYY-MM-DD`)
- `--debug-detector` — print candidate compaction records for schema-drift inspection

**Sample output.**
```
Week        Handoffs  Compactions   Ratio
-------------------------------------------
2026-W19           5           39   11.4%
2026-W20          10           50   16.7%
2026-W21           5           16   23.8%
-------------------------------------------
Total             22          141   13.5%
```

**When to reach for it.** Check whether context-cap management is proactive (handoffs) or reactive (compaction). A low ratio means most context resets are happening automatically rather than at deliberate checkpoints.

---

## audit-routing-shape

**Purpose.** Turn-shape distributions for Opus code-read turns across three dimensions: files-Read per turn (D1), code-read streak lengths (D2), and read-then-edit ratio (D3).

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit to the last N days (e.g. `35d`)

**Sample output.**
```
## Opus code-read turn-shape distributions (last 7d)

### D1 — Files Read per turn (code-read turns, outside judgment spans)

Bucket      Turns   Output tokens
─────────────────────────────────
0           1,958       1,750,876
1             549         366,426
2-3             0               0
4-7             1           1,694

### D2 — Code-read streak length

Bucket    Streaks   Output tokens
─────────────────────────────────
1           2,407       2,007,741
2              49         106,950
3-5             1           4,305

### D3 — Read-then-edit ratio (lookahead up to 3 Opus turns)

Case              Turns   Output tokens
───────────────────────────────────────
inline-edit         280         293,110
dispatched           61          62,899
neither           2,167       1,762,987
```

**When to reach for it.** Calibrate delegation-rule strength: D1 shows how many files are read per turn (higher = stronger delegation candidate), D2 shows streak length (longer streaks = more exploration than necessary inline), D3 shows what fraction of code-read turns are immediately followed by an edit (inline-edit) versus dispatched.

---

## audit-routing-samples

**Purpose.** Emit a random sample of Opus code-read turns with prior-user context, recent agent narration, a tool trail, and a next-turn lookahead classification for manual delegation curation.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit to the last N days (e.g. `35d`)
- `--sample N` — maximum turns to emit (default: 30)
- `--seed N` — random seed for reproducible sampling
- `--format json|md` — output format: `json` (default, machine-readable array) or `md` (human-readable curation cards with verdict checkboxes)

**Sample output (`--format md`).**
```markdown
## 1/2 — session `abc123` turn 45

**User:**
> Implement the new auth middleware

**Recent agent narration:**
> Reading the existing middleware to understand the shape before writing...

**Recent tool trail:**
- Read: `src/middleware/auth.ts`
- Read: `src/middleware/rate-limit.ts`

**Next:** `code-write`
> (writes new middleware file)

**Verdict** (check one):
- [ ] true (delegate)
- [ ] false (inline correct)
- [ ] skip
```

Verdict meanings: `true (delegate)` — the reads sat in context with no immediate consumer, delegation would have saved tokens; `false (inline correct)` — the reads fed an immediate edit or comprehension-driven response; `skip` — ambiguous or noise.

**When to reach for it.** Build a labeled training set for delegation-rule calibration. Use `--seed` for reproducible samples across sessions; use `--format md` for human curation review; use `--format json` to feed a downstream scoring script.
