# Reviewer-agent worktree-isolation prose fix

## Context

**Goal:** stop repo prose from licensing `isolation: "worktree"` for agents
whose input is the dispatching session's working tree or whose output must land
there, and install the correct invariant in one canonical, always-loaded place.

A session dispatched five reviewer subagents with `isolation: "worktree"` plus a
`findings_path: agent-reviews/...` line; all five had their findings-file `Write`
denied and fell back to inline output. Investigation found the session was not
misbehaving — repo prose affirmatively told it to do that, in four places, one of
which recommends the flag inside the very deny message a blocked session reads.
Why now: that prose is live for every stow consumer, and the failure mode has a
silent variant — a worktree-isolated reviewer dispatched *without* a
`findings_path` writes nothing, is denied nothing, and reviews a committed-ref
checkout lacking the changes under review, returning a false clean with no error
to notice. Intended outcome: a prose-only change plus one structural test, with
the separate lock-parse defect and the deferred hook gate each filed as their own
issue.

## Approach

Replace the affirmative license in `claude/.claude/CLAUDE.md`'s Agent Briefing
with a rule scoped to **input provenance**: `isolation: "worktree"` is passed
only when an agent's input is already committed and its output is disposable, and
is never passed when the agent's input is the dispatching session's working tree
or its output has to land there. Correct the four other prose sites that induce or
recommend the opposite, and add one blanket structural test asserting no agent
file in this repo declares `isolation:` in frontmatter. No hook gate, no
per-dispatch-site restatement, no persona carve-out.

The rule is scoped to provenance rather than to "reviewers" because the narrower
phrasing ("never combine `isolation` with `findings_path`") licenses the silent
half of the failure: a worktree-isolated reviewer dispatched without a
`findings_path` writes nothing, is denied nothing, and reviews a committed-ref
checkout that lacks the changes under review — a false clean with no error to
notice. The rule carries no exception clause because the exception is exactly the
judgment call that failed on 5 of 5 dispatches. The two uses the corrected rule
still permits — parallel exploration of committed code, throwaway spikes — are
illustrative categories of what an ad hoc dispatch may still do, not workflows any
skill in this repo currently prescribes; no skill-prescribed dispatch passes
`isolation: "worktree"` today, so the rule forbids nothing in current use.

### Assumption ledger

**root:** Repo prose affirmatively licenses `isolation: "worktree"` for agents
whose input is the dispatching session's uncommitted working tree and whose output
must land back in it — a combination that cannot work, and whose failure is loud in
one variant (denied `findings_path` write, inline fallback) and silent in the other
(false-clean review of a stale checkout).

**Givens** — conditions this design treats as fixed and outside its own reach:

- **row1** — The harness checks an isolated agent out at a committed ref on a
  harness-generated branch (`worktree-agent-<hash>`), with no path back into the
  dispatching session's tree. Vendor-owned harness behavior; nothing in this repo
  can change it. `[verified: docs/design-decisions.md:99; claude/.claude/skills/agent-review/REFERENCES.md:21]`
**Numbered rows:**

- **row2** — `findings_path` is repo-relative
  (`agent-reviews/<agent-name>-<epoch>-<slug>.md`). It is the shared contract of
  `/code-review`, `/plan-review`, and `/ready-for-review`.
  `[verified: claude/.claude/skills/plan-review/ROUTING.md:47-53]`
- **row3** — `require-worktree-for-file-writes.sh` precedes
  `deny-reviewer-tree-mutation.sh` in the `Edit|Write|MultiEdit` matcher chain, so
  the latter's `agent-reviews/*` exemption never gets a turn.
  `[verified: claude/.claude/settings.json:324-338]`

Both row2 and row3 are conditions this repo's own artifacts could change, so
neither is a given — each is a fact the design relies on, and the deliberate
decision not to change it is recorded in **Out of scope** with its reason.

- **row4** — The observed 5/5 failure was instruction-following, not
  rule-defiance: four prose sites affirmatively licensed or actively recommended
  the combination. This is why prose correction is the fix and a gate is not.
  `[verified: claude/.claude/CLAUDE.md:87; README.md:302; claude/.claude/skills/branch-management/SKILL.md:92-93; claude/.claude/hooks/require-worktree-for-file-writes.sh:171]`
- **row5** — The `or spawn an agent with isolation: worktree` remedy clause
  appears in five deny messages across two hook scripts, not one.
  `[verified: require-worktree-for-file-writes.sh:148,171; require-worktree-for-git-writes.sh:335,380,436]`
- **row6** — The silent variant is real: an isolated agent with no
  `findings_path` reviews the committed ref, not the parent's changes, and returns
  a verdict about a tree nobody asked about. This repo already reached that
  conclusion once, for `check-runner`. `[verified: row1 + docs/design-decisions.md:99]`
- **row7** — The combination fails for a second, independent reason beyond the
  lock defect: a relative `findings_path` resolves inside the ephemeral worktree,
  so the parent's mandatory read-back finds nothing — or reads a stale same-named
  file from a prior round. Fixing the lock parse would not make the combination
  work. `[verified: row1 + row2]`
- **row8** — No file under `claude/.claude/agents/` declares `isolation:` today
  (13 files; keys in use are `name`, `description`, `tools`, `model`, `effort`),
  and no `plugins/*/agents/*.md` exists, so the new test lands green and needs no
  sibling-tree coverage. `[verified: frontmatter audit; Glob for plugins/*/agents/*.md returned no files]`
- **row9** — Scope is prose plus one structural test. The `_lib.sh` lock-reason
  parse defect is filed as its own issue and not fixed here. `[engineer-verified]`
- **row10** — Whether `tool_input.isolation` appears in a PreToolUse hook's raw
  stdin JSON is undocumented. `tool_input.subagent_type` is proven populated
  (`log-reviewer-round.sh:52,120`), and `hooks.md` documents `if` as
  permission-rule syntax, but the isolation argument's presence in stdin is the
  load-bearing unknown for any gate. `[unverified]`
- **row11** — `permissions.md` constrains each permission rule to one parameter,
  so a `permissions.deny` on `Agent(isolation:worktree)` cannot also be narrowed by
  subagent type and would over-block the two legitimate uses.
  `[verified: Anthropic permissions.md]`
- **row12** — `test_skill_citations_resolve_to_real_headings` resolves a
  `` `file` § "Heading" `` citation whose target lies outside the skill tree.
  `_resolve_citation_target` tries a repo-root-relative path first, before either
  skill-tree-scoped branch, so `` `claude/.claude/CLAUDE.md` § "Agent Briefing" ``
  resolves and is CI-checked against file and exact heading. The citation must use
  the full repo-relative path — a bare `` `CLAUDE.md` `` resolves to the root
  `CLAUDE.md`, which carries no such heading.
  `[verified: claude/.claude/skills/tests/test_skills.py:2576-2616, 2666-2687]`
- **row13** — `ROUTING.md:32`'s "a fresh worktree" carries a legitimate second
  reading: `agent-reviews/` is appended to `info/exclude` and stays untracked, so a
  prior round's findings files do not exist in a parent worktree created after that
  round ran. `[verified: claude/.claude/skills/plan-review/ROUTING.md:32,53]`

**Mechanisms:**

- **M1 — Replace the `CLAUDE.md:87` bullet (not add a second one).**
  `anchors: row4` — it is the site stating the affirmative license, and it is
  always loaded, so correcting it reaches every dispatch decision with no new
  mechanism and no line-count growth.
- **M2 — Correct `README.md:302`, `branch-management/SKILL.md:92-93`, and five
  hook deny messages.** `anchors: row4, row5` — each currently tells a reader to do
  the wrong thing at the moment they are deciding; the hook messages are the worst
  case, since a blocked session is reading that text actively.
- **M3 — Disambiguate `ROUTING.md:32` rather than delete the clause.**
  `anchors: row13` — deletion would remove a real data condition; rewording removes
  only the reading that treats an isolated reviewer's unreachable write as
  tolerable.
- **M4 — Record the decision in `docs/design-decisions.md` §48.**
  `anchors: root, row9` — the no-carve-out reasoning and the two deferred issues
  need a durable home the test docstring and future readers can cite, in the genre
  this repo already uses for it.
- **M5 — Add no sentence to `code-review/SKILL.md:293`,
  `plan-review/ROUTING.md:47`, or `ready-for-review/SKILL.md:94`.**
  `anchors: root` — those sites own the `findings_path` convention, not dispatch
  isolation; the corrected always-loaded rule is already in context at those
  dispatch points, so a per-site sentence buys zero reach and is the restatement
  CLAUDE.md §Engineering Judgment forbids.
- **M6 — One blanket structural test: no `claude/.claude/agents/*.md` declares
  `isolation:`.** `anchors: row8` — blanket rather than persona-filtered, because a
  filter reinstates the judgment call that failed 5/5; the exception condition lives
  in the docstring so a future contributor removing the assertion states what they
  are claiming.

**Over-powered-primitive check.** The heaviest primitive available here is a
PreToolUse hook gate on `Agent(isolation:worktree)`. Five lighter primitives, in
order of weight:

1. **Corrected always-loaded prose in the canonical home (chosen — M1).**
   Sufficient for this change: the failure was a session doing what the prose told
   it (`row4`), not defying a rule. Zero runtime cost, zero new hook surface.
2. **A blanket structural test over agent frontmatter (chosen as a supplement —
   M6).** Covers the durable half — an `isolation:` key persisted into a repo agent
   file — at CI time. **It provides zero regression coverage for the reported
   incident**, which was caused by a per-dispatch `isolation` argument at
   Agent-tool-call time; no agent file involved carried the key then or carries it
   now. M6 guards a strictly worse variant that has not yet occurred (a standing
   frontmatter default, which would remove the per-dispatch choice entirely), not
   the variant that did. State this plainly rather than letting "adds one
   structural test" read as closing the loop on the incident: after this change,
   the only defence against the observed failure mode is the corrected prose being
   read and followed — and the hook-gate alternative that would cover it
   mechanically is blocked on unresolved evidence (`row10`, `row11`).
3. **A `permissions.deny` rule on `Agent(isolation:worktree)`.** Declarative and
   script-free, so lighter than a hook. Fails: one parameter per rule (`row11`)
   means it cannot be narrowed by subagent type, and a blanket deny over-blocks the
   two legitimate uses the corrected rule preserves.
4. **Per-dispatch-site sentences in the three review skills.** Lightest of all —
   no mechanism. Fails on reach, not on cost (M5).
5. **An `isolation:`-suppressing value in agent frontmatter.** No documented "off"
   value exists; `agent-review/REFERENCES.md:21` quotes only `worktree`, and
   frontmatter would not override a dispatch-time argument anyway. Not available.

The hook gate is deferred, not merely deprioritized. Its load-bearing premise is
unproven (`row10`), a gate matching only on `Agent(isolation:worktree)` would still
need a persona allowlist — reinstating the judgment call — and adding a runtime
layer on top of prose that currently *commands* the mistake is the
compounding-defensive-layers tell. Fix the foundation, then measure whether the
corrected prose holds.

### Exact replacement wording

**`claude/.claude/CLAUDE.md:87`** — replace the bullet in full, one physical line:

> - `isolation: "worktree"` is an **ephemeral-isolation** primitive, not a feature-branch primitive. The harness checks the agent out at a committed ref on a harness-generated branch (`worktree-agent-<hash>`): the agent sees none of the parent's uncommitted changes, has no path back into the parent's tree, and never runs the `branch-management` skill. Pass it only when the agent's input is already committed **and** its output is disposable — parallel exploration of committed code, throwaway spikes. Dispatch without it whenever the agent's input is the parent's working tree or its output has to land there; every reviewer dispatch is that shape, since it writes its `findings_path` file back into `agent-reviews/`, and most also read uncommitted work. For PR-bound implementation work, create the worktree yourself first: pick a slug per the `branch-management` skill, run `git worktree add .claude/worktrees/<slug> -b <slug>` (allowed on the main tree even under worktree enforcement), then dispatch the agent **without** `isolation: "worktree"`. Anchor the parent session in that worktree before dispatching — a `Working directory:` line in the prompt does not override where a child's commands actually run. `branch-management` covers why and how.

Every fact in the current bullet is preserved. Added: the committed-ref semantics,
the two-part provenance test, and the reviewer instance. Removed: `reviewer
agents` from the license list.

The false-clean consequence is deliberately **not** stated here. The preceding
sentence already prohibits the dispatch outright, so the consequence adds
motivation rather than a behavior-changing instruction — rationale belongs in
§48, and CLAUDE.md is loaded into every session, so its length has a recurring
cost. §48 carries it.

**`README.md:302`** — replace the paragraph:

> Agents spawned with `isolation: worktree` create their own worktrees under `.claude/worktrees/` automatically — on a harness-generated branch name (`worktree-agent-<hash>`), checked out at a committed ref. That fits ephemeral, non-PR work whose input is already committed: parallel exploration, throwaway spikes. It does not fit reviewer agents or PR-bound implementation work. For PR-bound work that needs a meaningful branch name, create the worktree yourself with `git worktree add .claude/worktrees/<slug> -b <slug>` first, then dispatch the agent into that path. `claude/.claude/CLAUDE.md`'s "Agent Briefing" section states the rule; `docs/design-decisions.md` §48 records why it carries no exception.

Kept to roughly the current paragraph's length — a corrected summary plus a
pointer, not a second full statement of the rule. The two-part provenance test
and the reviewer-shape argument stay in CLAUDE.md alone. The "instructional
prose that must stand alone" exception does **not** apply here: a paragraph
that closes by deferring to CLAUDE.md for authority is by definition not
standing alone, so restating the full rule would widen the duplicated surface
rather than preserve it.

**`claude/.claude/skills/branch-management/SKILL.md:89-94`** — replace the
paragraph (reflowed so the two denial facts sit together and the remedy closes
it):

> Hold the anchor still for the life of any dispatch, read-only ones
> included — the isolation check re-evaluates it for the whole dispatch,
> not just at launch. Re-anchoring mid-dispatch denies the subagent's
> Write/Edit calls outright, not redirected. Its Bash calls are denied
> too, a bare `pwd` included, for the rest of the run. Finish anchoring
> before the dispatch rather than during it; `isolation: worktree` is
> not a substitute, since it changes what the agent reads — a checkout
> at a committed ref, without the parent's uncommitted work
> (`claude/.claude/CLAUDE.md` § "Agent Briefing").

Use the machine-checked `` `file` § "Heading" `` citation form, with the **full
repo-relative path**. `test_skill_citations_resolve_to_real_headings` resolves
repo-root-relative targets before any skill-tree branch, so this citation is
CI-verified against both the file and the exact heading (`row12`). A bare
`` `CLAUDE.md` `` would resolve to the *root* `CLAUDE.md`, which has no "Agent
Briefing" heading — that fails loudly rather than silently, but use the full path.

**Five hook deny messages** — delete the remedy clause, repairing each list's
conjunction:

| File:line | Change |
|---|---|
| `require-worktree-for-file-writes.sh:148` | `…cd into an existing worktree under .claude/worktrees/, or spawn an agent with isolation: worktree."` → `…cd into an existing worktree under .claude/worktrees/."` |
| `require-worktree-for-file-writes.sh:171` | `…e.g. .claude/worktrees/<branch>/$REL_PATH — or spawn an agent with isolation: worktree.$(_lib_stray_marker_hint …)` → `…e.g. .claude/worktrees/<branch>/$REL_PATH.$(_lib_stray_marker_hint …)` |
| `require-worktree-for-git-writes.sh:335` | `…either change the session cwd into an existing worktree under .claude/worktrees/, use the EnterWorktree tool, or spawn an agent with isolation: worktree."` → `…either change the session cwd into an existing worktree under .claude/worktrees/, or use the EnterWorktree tool."` |
| `require-worktree-for-git-writes.sh:380` | `…not a variable, glob, subshell, or backgrounded cd — or spawn an agent with isolation: worktree."` → `…not a variable, glob, subshell, or backgrounded cd."` |
| `require-worktree-for-git-writes.sh:436` | `…cd into an existing worktree under .claude/worktrees/, create one with '…' (that specific command is allowed on the main tree), or spawn an agent with isolation: worktree. See claude-config README…` → `…cd into an existing worktree under .claude/worktrees/, or create one with '…' (that specific command is allowed on the main tree). See claude-config README…` — **insert `or` before `create one with`**; deleting the third item alone would leave a comma splice between two independent remedies, readable as "do both in sequence". This is the same three-to-two-item repair already applied at `:335`. |

Deletion, not replacement with a prohibition — a deny message names what to do,
not what to avoid. Each of these fires at a session that has state in the main
tree, so an isolated agent is wrong advice by construction.
`require-worktree-for-git-writes.sh:18` uses "isolation" in an unrelated generic
sense; leave it.

**`claude/.claude/skills/plan-review/ROUTING.md:32`** — one clause:

> `…falling back to those findings inline when the prior round wrote none — an older round, a denied write, or a parent worktree created after that round ran.`

## Critical files

| Path | Change | Reuse |
|---|---|---|
| `claude/.claude/CLAUDE.md` | Replace the line-87 bullet with the wording above. One physical line in, one out — file stays at 178/200. | Existing bullet slot; no new bullet, no budget growth. |
| `README.md` | Replace the line-302 paragraph. No length cap on this file. | Existing "Working inside a worktree" subsection. |
| `claude/.claude/skills/branch-management/SKILL.md` | Replace the lines-89-94 paragraph. File is 126 lines against a 200 cap (`check-skill-length.sh:63-73` default) — roughly +4 lines, ample headroom. | — |
| `claude/.claude/hooks/require-worktree-for-file-writes.sh` | Delete the remedy clause in the `:148` and `:171` deny strings; repair conjunctions. | Existing `emit_deny` strings; no helper, no control-flow change. |
| `claude/.claude/hooks/require-worktree-for-git-writes.sh` | Delete the remedy clause in the `:335`, `:380`, `:436` deny strings; repair conjunctions. | Existing `emit_deny_folding_fresh_lock_context` strings. |
| `claude/.claude/skills/plan-review/ROUTING.md` | One-clause reword at line 32. File is 122/500. | — |
| `claude/.claude/hooks/tests/test_agent_roster.py` | Add `test_no_agent_declares_worktree_isolation` to the existing `TestAgentFrontmatter` class (line 294). Its file set unions `_AGENT_FILES` with `(REPO_ROOT / "plugins").glob("*/agents/*.md")`, matching `test_agent_names_are_unique_across_the_tree` (line ~385) rather than scoping to `claude/.claude/agents/` alone. | **Three reuses:** the class's `_AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))` parametrize list (lines 304-306) gives coverage with no roster list to keep in sync; the sibling test at line ~385 already establishes the plugin-glob union for a repo-wide invariant; and `parse_frontmatter` (already imported from `validate_skill_structure`) is the file's established alternative to a `^key:` regex — see the note at line 578, which is in the sibling `TestNoGateReleaseRosterSync` class, cited as a file-wide convention. `parse_frontmatter` is required, not merely preferred: four agent bodies use the word "isolation" in unrelated senses, so a whole-file regex would false-positive. Assert `"isolation" not in parse_frontmatter(agent_path)`. |
| `claude/.claude/hooks/tests/test_require_worktree_for_file_writes.py` | Add `assert "spawn an agent" not in reason` / `assert "isolation" not in reason` to the deny-path tests covering `:148` and `:171`, using the file's established `assert "X" not in reason` pattern (lines 66, 74, 82, 92). | Existing deny-path fixtures and assertion idiom. |
| `claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py` | Same assertions for the `:380` path, **plus minimal new fixtures for `:335` (SENTINEL record deny) and `:436` (main-tree-not-on-allowlist deny)** — neither path is exercised by any existing test. | Existing assertion idiom (lines 83-84, 90-91, 99-100); the two new fixtures are the only genuinely new test setup in this change. |
| `docs/design-decisions.md` | New `## 48.` section (next free number; §47 is last). | Existing numbered-section format. |

**Test shape.** Put the exception condition in the test's **docstring**, not a `#`
block — this suite's convention is explanatory docstrings, and a paragraph-length
`#` comment would violate CLAUDE.md §Code Comments' one-line rule. The docstring
states: an `isolation:` key applies the harness's ephemeral worktree to every
dispatch of that agent; no agent in this roster can take it, because each either
reads the dispatching session's uncommitted work or writes back into its tree,
while an isolated agent is checked out at a committed ref with no path back; the
exception that would justify adding one is an agent whose input is fully committed
*and* whose output is disposable; cite `CLAUDE.md`'s Agent Briefing and
`docs/design-decisions.md` §48.

**§48 content.** The rule, why it is scoped to input provenance rather than to
reviewer personas, **the false-clean failure mode** (an isolated reviewer reviews
the committed ref rather than the changes under review and can return a clean
verdict on work it never read — the reason the rule is scoped to provenance and
not to the `findings_path` collision alone, and the content deliberately kept out
of the always-loaded CLAUDE.md bullet), why it carries no carve-out (the judgment
call it would restore failed on every dispatch that faced it), why prose plus a
structural test rather than a hook gate (`row10`, `row11`, and the
compounding-layers argument), and pointers to the two deferred issues. Abstract the trigger per
`.claude/rules/skill-and-agent-self-review.md` — keep the failure mode, drop its
identity.

**Dispatch split: one `code-writer` dispatch, not several.** The file sets are
disjoint on paper, but every edit is derived from the same artifact — the
replacement invariant wording. Splitting would mean restating that wording in each
prompt, and two agents resolving "how much of the rule belongs at this site"
differently is exactly the divergence `plan-it`'s no-split condition names. The
whole change is eight files of small, fully-specified edits; one dispatch carrying
this section verbatim is correct. It runs in the parent's feature worktree with no
`isolation` — as this rule now requires of any PR-bound implementation agent.

## Verification

1. **Scoped suite — the project's documented command:**
   `.venv/bin/python3 claude/.claude/scripts/select-tests.py`
   This diff's paths select `HOOKS_TESTS_DIR`, `SKILLS_TESTS_DIR`, **and
   `SCRIPTS_TESTS_DIR`** — the last pulled in by `_is_hooks_dir_shell_script_change`
   because the two edited `.sh` files fall under `test_no_bash4_constructs.py`'s
   repo-wide `*.sh` glob — plus three single-file cross-domain-exception targets.
   `is_full_suite` stays `False`, so it never widens to the full suite, and CI runs
   the full suite on push. Do not substitute `.venv/bin/pytest claude/.claude/` —
   neither documented full-run case applies here.
2. **The new test, run alone during authoring:**
   `.venv/bin/pytest claude/.claude/hooks/tests/test_agent_roster.py -k isolation`
   It asserts, once per file in `claude/.claude/agents/*.md` (13 today,
   parametrized by `_AGENT_FILES` so a new agent is covered without editing any
   roster list), that the parsed frontmatter has no `isolation` key. It lands green
   on the current roster (`row8`). Confirm it fails for the right reason by adding
   `isolation: worktree` to one agent file temporarily and checking that exactly
   that file's parametrized case fails — then revert.
3. **Shell lint for the two hook scripts:**
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`
4. **Residual-mention sweep:** `git grep -n 'isolation: worktree'` and
   `git grep -n 'spawn an agent'` should return only (a) the corrected `CLAUDE.md`
   bullet and `README.md` paragraph, (b) the negative-form statements in
   `branch-management/SKILL.md`, `plan-it/SKILL.md:49`, and
   `issue-triage/SKILL.md:95`, (c) `agent-review`'s frontmatter-field reference and
   its `REFERENCES.md` vendor quote, (d) `_lib.sh:1697` and
   `test_lib_worktree_collision_guard.py:452`, and (e) files under `.claude/plans/`
   and `docs/case-studies/`, which are preserved records. Zero hits in any hook deny
   message.
5. **Review-pipeline dispatch** (per `.claude/rules/review-pipeline-dispatch.md`
   and the repo CLAUDE.md): the `branch-management/SKILL.md` edit makes
   `/skill-review` hook-enforced for this commit; the two hook-script edits route to
   `claude-hook-review`; `/code-review` invokes both. `ROUTING.md` is not a
   `SKILL.md`, so it adds no separate gate.

## Out of scope

- **The `_lib.sh` lock-reason parse defect — filed as its own GitHub issue, not
  fixed here** (`row9`). What the issue should say: `_lib_worktree_lock_pid`
  (`claude/.claude/hooks/_lib.sh:1446`) parses lock reasons with
  `^locked\ claude-code\ pid\ ([0-9]+)(\ session\ ([A-Za-z0-9_-]+))?$`, which does
  not match the harness's own ephemeral-worktree lock reason, shaped
  `claude agent agent-<hash> (pid <n> start <date>)`. The parse returns 2,
  `_lib_worktree_collision_guard` (`_lib.sh:1713-1716`) takes the `state==2`
  branch, and the guard denies with "this worktree is locked, but the lock reason
  does not name a process" — liveness is never consulted. Three lock formats
  coexist in the wild; the regex knows two. Reproduced live in this repo. Note
  explicitly that fixing it does **not** make the isolated-reviewer combination
  work — the relative `findings_path` still lands in the ephemeral worktree
  (`row7`) — so this is a genuine independent defect affecting any legitimately-
  isolated agent that writes, not a partial fix for the same problem.
- **A PreToolUse gate on `Agent(isolation:worktree)`** — a separate issue, blocked
  on evidence, not a design this plan rejects outright. It must first establish
  whether `tool_input.isolation` appears in raw hook stdin JSON (`row10`); a gate
  matching only on the permission-rule `if` and reading `subagent_type` would need
  a persona allowlist, which is the judgment call this change is removing.
  `permissions.deny` is not an alternative (`row11`). Revisit only if the corrected
  prose is observed to fail.
- **Hook matcher ordering in `claude/.claude/settings.json`** (`row3`) — a
  condition within this repo's reach that this change deliberately declines to
  alter. `deny-reviewer-tree-mutation.sh`'s `agent-reviews/*` exemption never runs
  because `require-worktree-for-file-writes.sh` precedes it. Reordering affects
  every Write in every repo a stow consumer opens, and it would not make an
  isolated reviewer's write reach the parent (`row7`).
- **Making `findings_path` absolute** (`row2`) — likewise within reach and
  deliberately declined. It would remove one of the two independent failure
  reasons, but it changes a contract shared by three review skills and is not
  needed once the dispatch itself is correct.
- **`claude/.claude/skills/agent-review/SKILL.md:36`** — unchanged. It is an
  accurate reference to a harness frontmatter field, in a skill that installs
  globally and reviews agent files in any repo; the repo-local ban belongs in the
  repo-local test, not in a global skill's field list.
- **`claude/.claude/skills/agent-review/REFERENCES.md:21`** — unchanged. It is a
  verbatim vendor quote in an edit-time citation store; altering a quotation is not
  on the table even though "an isolated copy of the repository" reads more
  permissively than the committed-ref reality.
- **`claude/.claude/hooks/tests/test_lib_worktree_collision_guard.py:452-453`** —
  unchanged, and this was checked rather than assumed. Its docstring says
  CLAUDE.md's Agent Briefing describes two parallel subagents writing into one
  worktree with no isolation as normal. That stays true after the change, and more
  strongly so: the corrected rule requires that shape for reviewers, and
  `plan-it/SKILL.md:83-84` already notes parallel dispatches share the parent's
  worktree.
- **`.claude/plans/*.md` and `docs/case-studies/*.md` mentions of `isolation`** —
  preserved records under CLAUDE.md §Scope discipline Axis 3; they document what was
  decided, not how the system currently behaves.
- **A test asserting the corrected prose wording** — rejected. Pinning CLAUDE.md
  phrasing makes every future reword a test edit, and the legitimate string
  `isolation: "worktree"` still appears in the corrected bullet, so a
  literal-absence assertion cannot express the invariant either. The structural test
  covers the durable, mechanically-checkable half; the rest rests on the prose being
  correct.

**No open questions remain.** `row12`, the plan's one prior unknown, was settled
during review: the `§` citation form resolves out-of-tree targets and is used.
