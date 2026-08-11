# transcript-analysis.py reference

`transcript-analysis.py` is an analysis toolkit for Claude Code transcripts. By default it scans the union of the active profile's `~/.claude/projects/*/*.jsonl` and every config dir declared in `~/.claude/transcript-config-dirs`, not just the active profile alone — see "Corpus scope: the declared-roots file" below. Run it directly from the shell — there is no `~/.local/bin/` wrapper.

All subcommands are local-only reads except `pr-link` (calls `gh`), `judgment-pair --out` (writes to a specified file), and `cost-ledger --record` (writes to `docs/cost-ledger.md`, gated on an opt-in sentinel — see the `cost-ledger` section below). No other subcommand writes to disk.

For question-driven routing ("which subcommand answers X?"), use the `/transcript-analysis` skill. This page is the per-subcommand reference: flags, output shape, and when to reach for each one.

---

## Scoping to this repo: `--this-repo`

Every subcommand below accepts `--this-repo` as a mutually exclusive alternative to `--projects GLOB`. It resolves *this checkout's* worktrees by identity — `git worktree list`, matched as exact project-directory names — the same minimization control `skill-invocation` has used by default since it shipped, now available everywhere. Every subcommand that accepts it prints a one-line resolved-scope header (`<NAME> SOURCES (...)`) before its output, so a run is never ambiguous about whether it read one repo or the whole machine.

The default differs by subcommand: `skill-invocation` defaults to repo-scoped (safe-by-default) and treats `--this-repo` as a no-op; every other subcommand defaults to machine-wide (unsafe-by-default) and requires `--this-repo` to opt into repo scoping.

**`review-trace` output is not publish-safe under the default machine-wide scope** — each event line's branch string can carry a ticket ID or project name. `--this-repo` does not guarantee single-account output either: once `~/.claude/transcript-config-dirs` declares more than one root, or an explicit `--config-dir` extra is combined with `--this-repo`, it unions across every resolved root the same way the default scope does (see "Corpus scope: the declared-roots file" below). No flag on `review-trace` narrows output to one account short of an explicit single top-level `--config-dir PATH`; use that before quoting output anywhere public.

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

`cost --config-dir` does not refuse `--this-repo`: since `--this-repo`'s identity match is derived from `git worktree list` alone, it is root-independent, so `cost --config-dir DIR --this-repo` unions across the default corpus and every `--config-dir` extra the same way `--this-repo` does everywhere else. See the `cost` section below for the full `--config-dir` contract.

---

## Corpus scope: the declared-roots file

Every subcommand's default scan corpus is a **union**, not a single account. List additional Claude Code config directories, one absolute path per line, in `~/.claude/transcript-config-dirs` (blank lines and `#`-comments ignored, a leading `~`/`~/` expands to `$HOME`) — a machine running several accounts to keep engagements apart (`~/.config/claude-accounts/<account>/`, for example) declares each one here. The active profile's own config dir is always scanned first regardless of this file; every declared entry is added to it, deduped by resolved path. The resolved-scope header (`<NAME> SOURCES (...)`) always states the root count, even at one root with nothing declared — that unconditional line is what makes a scan self-disclosing about whether it covered one account or several. Populating this file also changes `cost --no-redact`'s behavior even with no `--config-dir` flag involved: it now exits 2 once more than one root is in scope, where it previously always worked with zero declared roots (see the `cost` section's `--no-redact` entry below).

This union amplifies two costs, both linearly in the number of declared roots:

- **Scan time.** Measured on one workstation: ~9s per root at top-level scope, ~16s per root with `--include-subagents`. A four-root union runs roughly 35–65s against ~9s at one root — expect a per-root progress line on stderr above one root so a long-running scan doesn't read as hung. The one narrowing control, if a given invocation needs to run faster or scope to fewer accounts: pass an explicit single top-level `--config-dir PATH`, which overrides the union back to exactly one root (see "Scoping to this repo" above and the `cost`/`context-distribution` sections below for their own separate, repeatable `--config-dir`).
- **Redaction's ordinal fingerprint.** `cost` and `audit-routing --redact` already read every project's transcript bytes to build their redact map, even under `--this-repo` (see the `cost` section's `--config-dir` contract below) — a structural fingerprint of the operator's other local projects. A declared-roots union multiplies both the bytes read and that fingerprint's information content: the ordinals now encode which projects exist across every declared account, not just one. Two redacted reports built from the **same** declared-roots file assign the same `account-N` to the same physical root regardless of which profile produced either report, which makes them correlatable by ordinal across time — a property that did not exist before this file was populated. This is a privacy tradeoff to weigh before posting a second public report from an unchanged roots file: an operator who posts two redacted reports months apart gives a reader no way to learn `account-2`'s name, but does hand them a way to tell that both posts came from the same underlying account. Two reports built from **different** declared-roots files are not comparable; a changed root set can renumber every ordinal.

Redaction — the `DO NOT PUBLISH` banner and `account-N`/`private-project-N` labels — is not uniform across the three subcommands that mention it. `cost` and `audit-routing` build the redact map and print per-project or per-account labels. `context-distribution` prints the same banner and refuses `--no-redact` above one root, but never builds the redact map and emits no project label at all, so there is nothing in its output to actually redact. Every other subcommand — `audit-routing-samples`, `buckets`, `review-trace`, `fail-seq`, `struggle`, `duration`, `subagents`, and `pr-link` — has no redaction of any kind and prints raw branch names, paths, or prior-user text under the default union; none of them narrows the union to one account short of the `--config-dir` escape hatch above.

---

## buckets

**Purpose.** Show assistant-turn counts bucketed by git branch × model family, with session count and date range.

**Flags.**
- `--projects GLOB` — project directory glob (default: `*`, all projects)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--branches B1,B2,...` — filter to specific branches (default: all)

**Sample output.**
```
BUCKETS SOURCES (this repo (6 project dirs); 1 root (no ~/.claude/transcript-config-dirs declared))
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

**Purpose.** Show `isSidechain` (subagent) versus main-thread turn counts per branch, split by model family, plus total tool-result bytes per thread — and a second table breaking those same bytes down by the tool name that produced them.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--since Nd` — limit both tables to records with timestamp in the last N days (e.g. `35d`). Never narrows the corpus-wide spawn/sidechain-turn counters that feed the format-drift warning — a narrow window can't manufacture a false warning.
- `--config-dir DIR` — additional Claude Code config directory to scan (repeatable), same contract as `cost`'s own `--config-dir` (see the `cost` section above): the default resolved config dir is always scanned first, each extra directory must contain its own `projects/` subdirectory, and it's refused together with `--this-repo`. Unlike `cost`, there is no `--no-redact` escape hatch here — under more than one root, branch names are always redacted (`account-<K>/branch-<N>`, stable only within one run) since nothing else in this subcommand's output identifies a root, and `DO NOT PUBLISH` prints on stdout and stderr. Every `mcp__<server>__<tool>` tool name in the byte-by-tool table collapses into one `mcp__*` row regardless of root count — an MCP server name is a per-account integration identifier, not just a multi-root concern.

The top-level `--config-dir PATH` flag (placed before the subcommand name) is refused for `subagents`, the same way it already was for `cost` and `context-distribution` — use the subcommand's own repeatable `--config-dir DIR` instead. This is a breaking change for any prior invocation of the form `transcript-analysis.py --config-dir PATH subagents ...`.

**Sample output.**
```
Branch                                   Thread       Opus  Sonnet  Haiku  Other              Bytes
---------------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand  main           78     159      0      0            412,880
GH-333/audit-routing-samples-subcommand  sidechain       0      78      0      0            896,120

Branch                                   Side       Tool                             Bytes
--------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand  main       Read                            301,220
                                          sidechain  Bash                            640,004
                                          sidechain  mcp__*                          120,050
```

Byte totals are aggregate-only: no tool-result content, file paths, session IDs, or cwd are ever printed. There is no per-byte dollar model — this is an un-dollar-weighted signal for where verbose tool output accumulates, not a cost figure.

**When to reach for it.** Understand how much work was delegated versus inline. Compare against `subagent-mix` for a breakdown of what *kind* of subagents were spawned.

---

## subagent-mix

**Purpose.** Show per-branch subagent spawn type counts and code/plan/ready-for-review review-skill spawn counts, plus a second table breaking each `agentType`'s dispatches down by declared, requested, and observed model.

**Flags.**
- `--branches B1,B2,...` — filter to specific branches (default: all)
- `--projects GLOB` — project directory glob (default: `*`)
- `--this-repo` — scope to this repo's own worktrees by identity, instead of a machine-wide glob (see "Scoping to this repo" above)
- `--per-session` — break out by individual session instead of aggregating per branch. Refused when `--config-dir` puts more than one root in scope — a per-session row would join a foreign account's own session-id prefix to its branch name.
- `--since Nd` — limit both tables to records with timestamp in the last N days (e.g. `35d`)
- `--since-date YYYY-MM-DD` / `--until-date YYYY-MM-DD` — closed date range bounding the model-mix table's `Actual$`/`Counterfactual$` columns only, inclusive start / exclusive end (`[since, until)`, UTC day boundaries). Unlike `--since Nd`, this filters at the *sidechain assistant record* level, not the dispatch level: a dispatch whose sidechain straddles the window edge has only its in-window records priced into `Actual$`, never the whole dispatch's dollars just because it started inside the window. Every other column in this table (`Runs`, `Dangling`, `Declared`, `Requested`, `Observed`) keeps `--since Nd`'s existing dispatch-level scope, unaffected by `--since-date`/`--until-date`.
- `--reprice-as MODEL_ID` — re-price that same in-window usage at an alternate model ID via `_price_turn`, adding `Counterfactual$` and `Delta` (`Actual$ − Counterfactual$`, negative when the counterfactual model is pricier) columns. `MODEL_ID` must be one of `_MODEL_BASE_INPUT_RATES`'s keys; an unrecognized value is rejected, listing the valid IDs.

`Actual$`/`Counterfactual$` follow `cost`'s own conventions for the two failure modes a dollar figure can hide: a turn on a model absent from `_MODEL_BASE_INPUT_RATES` is excluded and surfaced via a `(N unpriced turns / M tokens excluded from priced spend)` footer rather than silently reading as zero-cost, and a priced model past its `_MODEL_RATE_EXPIRES` re-verify-by date prints a `STALE PRICING` banner before the table.
- `--config-dir DIR` — additional Claude Code config directory to scan (repeatable), same contract as `subagents`' own `--config-dir` above: refused together with `--this-repo` and with `--per-session`; branch names **and** `subagent_type` values are always redacted under more than one root (a custom `subagent_type` can name a project-scoped agent definition, the same disclosure risk a branch name carries), and `DO NOT PUBLISH` prints on stdout and stderr. The model-mix table's `Declared` column is also resolved from each dispatch's own root — not always this process's own config dir — and the table is keyed on the redacted `(root, subagent_type)` pair, so two accounts' same-named `agentType` never merge into one row.

The top-level `--config-dir PATH` flag (placed before the subcommand name) is refused for `subagent-mix`, the same way it already was for `cost` and `context-distribution` — use the subcommand's own repeatable `--config-dir DIR` instead. This is a breaking change for any prior invocation of the form `transcript-analysis.py --config-dir PATH subagent-mix ...`.

**Sample output.**
```
Branch                                         Sess  Spawns  CR  PR  RR  Top subagent types
------------------------------------------------------------------------------------------------------------------------
GH-333/audit-routing-samples-subcommand           2      11   2   1   2  staff-sdet(4), code-writer(3), check-runner(2), staff-backend-engineer(2)

AgentType                    Runs  Dangling  Declared        Actual$ Requested                      Observed
-----------------------------------------------------------------------------------------------------------
staff-sdet                      4         0  sonnet          $12.40 (none)(4)                      opus(1), sonnet(3)
code-writer                     3         0  sonnet           $2.85 sonnet(3)                      sonnet(3)
```

With `--reprice-as claude-haiku-4-5-20251001` added, each row also carries `Counterfactual$` and `Delta`:
```
AgentType                    Runs  Dangling  Declared        Actual$    Counterfactual$        Delta Requested                      Observed
------------------------------------------------------------------------------------------------------------------------------------------
staff-sdet                      4         0  sonnet          $12.40              $4.13        $8.27 (none)(4)                      opus(1), sonnet(3)
```

Columns: `CR` = `/code-review` spawns, `PR` = `/plan-review` spawns, `RR` = `/ready-for-review` spawns. In the model-mix table, `Runs` counts dispatches with a readable `subagents/*.meta.json` **and** a readable sibling `.jsonl` — a dangling pair (meta.json present, `.jsonl` missing or unreadable) is excluded from `Runs` and counted under `Dangling` instead. `Declared` is the frontmatter `model:` pin from `config_dir()/agents/<agentType>.md`, or `built-in` when no on-disk agent file exists (e.g. `general-purpose`, `claude-code-guide`, `Plan`). `Requested` is `meta.json`'s own `model` key, bucketed under `(none)` when absent. `Observed` is the modal real model ID across the dispatch's own sidechain — two distinct real model IDs report the literal `mixed` bucket rather than collapsing to one family, and a sidechain whose only recorded model is `<synthetic>` resolves to `other`, never counted as a pin violation. `Actual$` sums `_price_turn`'s own per-class dollars over each matched dispatch's sidechain, scoped to `--since-date`/`--until-date` when given (unbounded otherwise) — a dispatch with no priced usage in scope (a synthetic-only sidechain, or a fully out-of-window one) renders `$0.00`, never a crash or a blank cell.

**When to reach for it.** Audit which reviewer agents fired on a branch, or compare how delegation patterns differ across branches. The tool recognizes `check-runner` in historical session data — the agent is retired (2026-06-23) but historical transcript entries remain valid corpus inputs. Use the model-mix table to spot a pinned agent (e.g. `Explore`) whose `Observed` column still shows Opus despite a `sonnet` `Declared` pin — then reach for `--since-date`/`--until-date --reprice-as` to put an actual dollar figure on what that Opus-vs-declared-pin gap cost over a specific window, and what it would have cost at the declared pin instead.

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
SKILL INVOCATION SOURCES (this repo; main thread; 1 root (no ~/.claude/transcript-config-dirs declared))
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
REVIEW TRACE SOURCES (this repo (6 project dirs); 1 root (no ~/.claude/transcript-config-dirs declared))

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
- `--config-dir DIR` — additional Claude Code config directory to scan (repeatable), on top of the default corpus already described in "Corpus scope: the declared-roots file" above (the active profile plus every root declared in `~/.claude/transcript-config-dirs`). Each `--config-dir` extra must also contain its own `projects/` subdirectory or the run is rejected. Composes with `--this-repo`: its worktree-identity match is root-independent, so `--this-repo` unions across the default corpus and every `--config-dir` extra the same way it does under the declared-roots file alone. Refused together with `--no-redact` (more than one root in scope would put one profile's real project names into a report meant to also cover another).
- `--by-project` — add a per-project cost breakdown, keyed on (account root, project family); composes with both `--projects` and `--this-repo`. One repo's own worktrees collapse into a single family row instead of fragmenting per branch. When `--config-dir` puts more than one root in scope, each row also carries an `Account` column using the same `account-N` labeling the per-root scan-summary lines use. Under `--no-redact` (single-root only — see below), the Project column shows the raw, suffix-collapsed directory name instead of a `private-project-N` placeholder, same as every other section of this report. Refused together with `--summary`.
- `--since Nd` — limit to turns with timestamp in the last N days (e.g. `30d`)
- `--top N` — maximum per-session rows in the top-N-by-dollars section (default: 20)
- `--no-redact` — emit real project names and session IDs instead of anonymized labels. `cost` is **redacted by default** (the opposite default from `audit-routing`) since its documented purpose includes producing text for public issues; never publish `--no-redact` output. Refused when `--config-dir` puts more than one root in scope, and refused together with `--summary`.
- `--summary` — a distinct, aggregate-only rendering mode meant to be embedded directly in a PR body (see the `pr-description` skill's `## Cost` section). Requires `--this-repo` and refuses any other scope flag, including a non-default `--projects` glob — every project-directory slug is absolute-path-derived and therefore starts with `-`, so a glob like `-*` would otherwise be machine-wide despite not being the literal default `*`. Also refuses `--by-project`, `--no-redact`, and `--config-dir` in combination — each drives an identity-bearing code path (`## Cost by project`, raw labels, multi-root scan-summary lines) `--summary` structurally never reaches. It never builds or reads the redact map, never prints the `DO NOT PUBLISH` banner, and never emits a per-root raw-path label — nothing it prints is keyed by project or session identity. It does print the scan-count diagnostic ("scanned N transcripts, M skipped") and the zero-scope `WARNING`, since those are identity-free under single-root `--this-repo` scope and are what makes an empty or under-scanned corpus visible instead of a silent `$0.00`. Always prints "Unpriced tokens: N tokens across M model IDs", even at zero. Carries the `STALE PRICING` banner in the same block as the dollar tables, same as the full report.

**Per-account breakdown.** When `--config-dir` puts more than one root in scope, the full report (not `--summary`, which refuses `--config-dir` outright) also prints a `## Cost by account` section: one `### account-N` block per scanned root, each carrying its own token-class and model-ID tables scoped to that account's own spend. No separate flag — it auto-appears whenever more than one root is in scope, the same way `edit-format`'s own per-account breakdown does. This is a resolution increase over the prior combined-only output: a reader of shared multi-root `cost` output can now see, e.g., "account-2 is 100% Sonnet, never Opus" where previously only a combined total was visible. It is designed to be shareable under the same multi-root contract as `--by-project`'s own redacted `account-N` labeling — no raw project name or config-dir path is ever printed in this section.

Redacted project labels (`private-project-N`, `account-N`) and the printed corpus fingerprint are **not comparable across two report runs built from different declared-roots files** — a changed corpus (a new session, a removed project dir, a different declared-roots or `--config-dir` set) can renumber every ordinal. Two runs built from the *same* declared-roots file, even from different active profiles, assign the same ordinal to the same physical root and stay comparable — see "Corpus scope: the declared-roots file" above. Treat a run's redacted output as self-contained unless you know the roots file behind it is unchanged; never diff `private-project-3` between two runs without that guarantee and assume it names the same project.

**Worktree-isolated subagent attribution.** A subagent dispatched with `isolation: "worktree"` runs on a harness-generated `worktree-agent-<hash>` branch, not the branch that dispatched it. `--branches` filters on each record's *attributed* branch, not that literal value: for every `worktree-agent-*` record, `cost` resolves the dispatching session's own branch active at that record's timestamp (falling forward to the session's earliest branch if the record predates every main-thread record), and folds the subagent's dollars and tokens into that branch's total — the same real spend a plain literal-`gitBranch` filter would otherwise silently drop. The one genuinely unattributable case — a session with no main-thread branch-bearing record at all — renders `?` (reusing the `?` sentinel `review-trace`/`judgment-pair` already use for "no signal to carry forward") and is excluded from every `--branches`-filtered total. Attribution is scoped to the dispatching session only; a `worktree-agent-*` record is never resolved against a *different* session's main-thread history.

**The disclosed fields are not neutral.** `--summary`'s output is aggregate-only, but "aggregate" does not mean "safe to publish by default": session count and priced-turn count signal how much engagement went into a branch, per-class token volume signals how long that engagement ran, and per-model-ID dollars discloses which models are in use. That is the intended read for an account that opts into publishing it (see `pr-description`'s `## Cost` section and `docs/hooks.md`'s `pr-cost-disclosure` entry) — it is not a property of the output format itself, and an account enabling the sentinel for an unrelated reason should not assume these fields are harmless to expose.

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
- `--config-dir DIR` — additional Claude Code config directory to scan (repeatable), on top of the default corpus already described in "Corpus scope: the declared-roots file" above. Each extra must contain its own `projects/` subdirectory or the run is rejected. Composes with `--this-repo` the same way `cost`'s own `--config-dir` does. Roots resolve via the same `_resolve_cost_roots` funnel `cost` uses (not the generic single-root resolver every other subcommand uses), so the same per-root scan-summary and zero-scope `WARNING` lines `cost` prints also print here. This closes the gap as a **combined-across-accounts** trend — the existing single weekly table, now summed over every `--config-dir` root — not a per-account-per-week matrix.

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

## cost-ledger

**Purpose.** Read or append `docs/cost-ledger.md`, a durable, committed, append-only record of this repo's own weekly cost/efficiency figures — the retention `cost-trend` can't provide on its own, since Claude Code deletes the transcripts `cost-trend` re-derives every week from. See `docs/cost-ledger.md` for the row schema and `.claude/plans/cost-trend-ledger.md` for the full design rationale.

**Flags.**
- `--projects GLOB` / `--this-repo` — project directory scope (see "Scoping to this repo" above)
- `--record` — append the current ISO week's row instead of reading. Requires the opt-in sentinel `~/.claude/.cost-ledger-enabled` and `--machine-label`.
- `--machine-label LABEL` — required with `--record`: an opaque per-machine token matching `^[a-z0-9]{1,8}$`, rejected case-insensitively against this machine's hostname.
- `--force` — with `--record`, overwrite an existing row for the same (week, machine) pair instead of refusing.
- `--note TEXT` — free-text note for `--record`'s row (what changed in the workflow this week). Must not contain `|` or a newline.

**Default (read) output.** Every row currently in `docs/cost-ledger.md`, followed by any ISO week present in the live corpus that no row (for any machine) has captured yet — the gap between "recorded" and "still recoverable."

**`--record`'s row.** `usd`/`context_pct`/`opus_pct`/`ge200k_pct` reuse `_compute_cost_trend_data`, the per-week accumulation behind `cost-trend`'s own report. `context_pct` and `ge200k_pct` are two distinct metrics, not one under two names: `context_pct` is the context-class (cache read plus both cache-write tiers) dollar share of the week's spend, while `ge200k_pct` is the dollar share of turns whose context crossed the >=200k bucket — the same figure `cost-trend`'s own printed "Context%" column has always shown. `denials` and `reviewer_gap_pp` are windowed to the current ISO week's Monday-through-next-Monday UTC boundary via `review-trace --deny-summary`'s and `reviewer-yield`'s own accumulation, scoped to that one week rather than corpus lifetime. `reviewer_gap_pp` prints empty when either side of the findings-found/zero-finding comparison has zero measured dispatches that week.

**Error paths.** `--record` refuses (non-zero exit, writes nothing) on: an empty corpus or a current week with zero priced turns; a malformed ledger file (wrong column count, non-ISO week label, non-numeric cell, an embedded `|`, or an unresolved git merge-conflict marker); a `--machine-label` that doesn't match `^[a-z0-9]{1,8}$` or that equals this machine's hostname (the rejection never echoes the compared hostname value); an existing row for the same (week, machine) without `--force`; and a clock-skew mismatch between the corpus's most recent activity and the week the machine's clock resolves as current. The final read-check-write step (re-read the ledger, check for an existing (week, machine) row, write) holds an exclusive lock on a sibling `.lock` file, so two racing `--record` invocations can't both pass the duplicate-row check; the corpus scan that computes the row's values runs unlocked beforehand. Every write goes through a temp-file-then-atomic-replace step with a parse-back verification.

**When to reach for it.** Check the ledger before a workflow change ships, to confirm the baseline week is actually recorded before its transcripts age out — and after, to score the change once enough weeks have accumulated.

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
