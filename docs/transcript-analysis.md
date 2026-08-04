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

All three gaps are silent under-coverage, not an error: a narrower-than-expected corpus reads identically to "no evidence exists" unless you notice the resolved-scope header's project-dir count is lower than expected.

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

**Purpose.** Per-turn Opus token breakdown by routing class (`orchestration`, `judgment`, `code-write`, `code-read`, `pure-thinking`, `other`), aggregated across sessions with a Sonnet-tier estimate.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit to the last N days (e.g. `35d`)
- `--top N` — maximum per-session rows to emit (default: 20)
- `--redact` — replace project dir names with anonymized labels (`private-project-1`, `private-project-2`, …); `claude-config` is preserved. Use this flag when posting output to GitHub issues.

**Sample output.**
```
## Opus turn-class breakdown (last 7d)

Session          Proj                     orch  judgment  code-write  code-read  thinking   other   total_out   cache_rd
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
875cfbeb-f03     private-project-13     24,755       810     145,450    120,194   231,809 231,860     754,878 38,243,901
2a0ff669-a0d     private-project-2     149,000     2,495      54,070      6,071   125,343 147,887     484,866 10,169,069

## Corpus aggregate

Class              Output tokens  Cache read tokens
code-read              2,118,996        253,033,256
code-write             1,656,358         76,694,724
pure-thinking          3,863,382        220,641,203
other                  3,923,547        220,513,484
orchestration            792,294         15,917,519
judgment                  65,038         13,974,791
───────────────────────────────────────────────────
total                 12,419,615        800,774,977

Sonnet-tier estimate: 3,775,354 output tokens
  = 30% of Opus output in this window
```

**When to reach for it.** Answer "is Opus spend doing Sonnet-tier code-read/write in parent sessions?" — the `Sonnet-tier estimate` row summarizes the delegation opportunity. Always use `--redact` before posting output publicly.

---

## cost

**Purpose.** Price-weighted dollar cost by token class (cache read, cache write 5m/1h, output, input), model ID, and context-at-turn bucket, plus a top-N-sessions-by-dollars breakdown. Every other subcommand in this toolkit is denominated in raw token counts; `cost` is the one that answers "which lever actually moves the bill," since cache-read is 0.1× base input while output is 5× — a 50× spread raw token counts erase. **Context-at-turn** is defined as `input_tokens + cache_read_input_tokens + ephemeral_1h + ephemeral_5m` tokens for a turn — the sum of everything read into context for that turn, excluding output.

Pricing is looked up per exact model ID (Sonnet 5 and Sonnet 4.6 price differently), derived from one base input rate per model plus the pricing page's stated multipliers (output 5×, 5m cache write 1.25×, 1h cache write 2×, cache read 0.1×). Each model ID carries a re-verify-by date; when the current date is past it, a `STALE PRICING` banner prints inside the same output block as the dollar tables — never a separate log line a copy-paste of the tables could drop. An unrecognized model ID (e.g. `<synthetic>`) is never silently priced at $0: it gets its own row and its tokens are counted in a separate "Unpriced tokens" total, excluded from every dollar figure. Sidechain (subagent-dispatched) turns are priced — `cost` reads with `include_subagents=True`, unlike `audit-routing`'s main-thread-only scope, since subagent spend is real spend.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`, all projects)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above); mutually exclusive with `--projects`
- `--since Nd` — limit to turns with timestamp in the last N days (e.g. `30d`)
- `--top N` — maximum per-session rows in the top-N-by-dollars section (default: 20)
- `--no-redact` — emit real project names and session IDs instead of anonymized labels. `cost` is **redacted by default** (the opposite default from `audit-routing`) since its documented purpose includes producing text for public issues; never publish `--no-redact` output.

**Sample output.**
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

## Top 20 sessions by dollars

Session          Proj                                  $
session-1        private-project-13               244.24
session-2        private-project-2                189.07
```

**When to reach for it.** Rank workflow-efficiency levers by dollars instead of raw token counts before proposing an optimization — a metric denominated in output tokens alone (like `audit-routing`'s Sonnet-tier estimate) can headline 0.6% of spend while missing an 87%-of-spend context-cost problem entirely. Also the source of the redacted, aggregate-only tables that go into a public cost-audit issue — never publish the top-N-sessions section or any `--no-redact` output, both of which are real-project-identifying by construction. If that issue is filed via `gh issue create`, note that `deny-private-project-refs.sh` does not scan `gh issue create`/`gh issue comment` bodies at all — see `docs/private-project-redaction.md`'s "Known gaps" section — so this redaction is not backstopped by the hook on that publish path. Even a `--this-repo`-scoped, redacted table's `private-project-N` numbering is shaped by the operator's full local project corpus, not just this repo — see `_build_redact_map`'s docstring for the ordinal side-channel this implies. Observed wall-clock for a `--since 30d` run against this toolkit's own transcript corpus: ~10s with `--no-redact`, ~18s with the default redacted run — the redact-map first pass roughly doubles the time, since it fully parses every transcript once just to read a directory name (see `iter_sessions`' docstring; this is a known, deliberately deferred perf gap, not a `cost`-specific one).

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
