# Skill-procedural-fidelity check via an independent reviewer

## Context

**Goal:** when a session invokes a skill by name and then delivers work that
skips that skill's procedure, make the deviation visible to a reader who does
not share the deviating session's reasoning.

An error-mode analysis over a six-week, two-repo window found that a session
invoked a skill by explicit name, loaded its full procedure into context, and in
the next message reframed it as a "lens" rather than a procedure to execute —
offering a rationale that text in the same loaded skill body already anticipates
and rebuts. None of the artifacts that skill specifies were produced. The
resulting plan passed `plan-review` and the resulting commits passed
`code-review`, neither flagging the deviation.

**The defect is observer contamination, not a missing checklist item.** The
rationalization sits in the deviating agent's context window, still reading as
reasonable, when that same agent reviews the work. A checklist item added to a
contaminated observer is weak by construction — it asks the agent that already
waved off the skill body to notice that it did.

A second, independent instance in the same two-day span is a distinct defect
with a distinct fix: `code-review`'s always-spawn `ciso-reviewer` rule for data
exposure was not applied to a commit that rewrote identifying prose, because the
change presented as documentation-only. No leak resulted (independently
re-verified).

Source for both: a private error-mode analysis held outside this repo, restated
here in non-identifying form — the only form that belongs in a public repo.

## Approach

The problem decomposes into two halves that must be solved differently:

| Half | Problem | Solution |
|---|---|---|
| **Observation** | What did this session actually invoke? | Session transcripts already record it. No new capture machinery. |
| **Evaluation** | Did the deliverable fulfil it? | Irreducibly semantic — needs a reader without the deviating session's context. |

**Observation is already on disk — verified, not assumed.** Transcripts under
`~/.claude/projects/<slug>/*.jsonl` record model-invoked skills as
`{"name":"Skill","input":{"skill":"<name>"}}` and user-typed slash commands as
`<command-name>/<name></command-name>`. Extracting both from a 996K transcript
measured at 33ms with zero model tokens. This record is authoritative in the way
that matters: it is what the session *did*, not the session's account of what it
did, so a rationalization cannot edit it.

**Evaluation goes to a fresh agent.** A new `skill-fidelity-reviewer` receives
the extracted list plus the deliverable, reads each named skill's body from
disk, and compares. It never sees the deviating session's reasoning — which is
the entire point, and is the same principle `code-review/SKILL.md:228` already
states: *"spawn on the CODE, not on this review's output (each subagent reads
the diff fresh)."*

**It fires once per branch, at `/ready-for-review`** — the existing pre-handoff
gate — not per commit in `/code-review`. Cost scales with skills-invoked-per-
branch (typically 2–5 bodies), not with transcript size or commit count.

### Why not the lighter options

- **A checklist item in the existing reviewers (no agent).** Rejected as the
  primary fix: it leaves grading with the contaminated observer, which is the
  defect. Retained only where an independent reader cannot reach — see
  `plan-review` below.
- **A commit-blocking hook.** Rejected. "Did the deliverable fulfil the
  procedure" is semantic; a mechanical gate is either trivially satisfiable or
  wrong. This is where CLAUDE.md's compounding-layers warning genuinely applies.
- **A new hook to capture skill invocations.** Unnecessary — the transcript
  already carries the record, so this would add a capture layer for data that
  exists.
- **Spawning `general-purpose` with an inline prompt instead of a new agent
  file.** Rejected: the instructions this reviewer needs (what counts as a
  specified artifact, the stated-vs-silent standard, how to read a skill body
  without adopting its voice) are too long to inline into `ready-for-review`,
  which is 163 lines and is a checklist, not a prompt library. A dedicated agent
  file is the conventional home here and gets `/agent-review` coverage.

- **A per-commit fidelity check in `code-review`, alongside the branch-gate
  reviewer.** Rejected. Two checks on one axis, where the second exists only to
  cover the first's weakness, is the compounding pattern this repo treats as a
  wrong-foundation tell. The branch gate is a reliable chokepoint — it is
  hook-enforced before handoff — so one check per axis is sufficient.

### The `ciso-reviewer` gap — unrelated defect, separate narrow edit

A different defect with a different mechanism, fixed at its own site rather than
folded into the fidelity check.

**Line 226 is reachable.** Step 0's domain classification gates only *Domain
checklists* — `code-review/SKILL.md:21` states "Apply the **Base checklist**
always. Apply each **Domain checklist** only when at least one changed file
matches that domain." Step 0.6 (line 29) and Ripple effect triage (lines
213–260, containing the always-spawn rule at 226) are not domain-gated. So a
"documentation-only" classification does not short-circuit past line 226; the
failure was the enumeration's wording, not control flow.

**Not single-layer:** `claude/.claude/hooks/deny-private-project-refs.sh` is an
existing orthogonal pre-commit backstop, but fires only on blocklisted literals.
It cannot recognize prose that identifies an entity descriptively, which is the
residue the reviewer-judgment layer covers.

## Critical files

### 1. `claude/.claude/scripts/transcript-analysis.py` — extend `cmd_skill_invocation`

**Do not write a new extractor.** `cmd_skill_invocation` already exists and does
most of this job. A second extractor beside it would be a straight DRY defect.

**Corrected premises.** An earlier revision of this section claimed the function
was unregistered and untested, and that dropping its `isSidechain` guards was a
free additive change. All three were false against current source — the
reconnaissance read only part of `main()` and stopped. Verified this session:

- It **is** registered as the `skill-invocation` subcommand (`--projects` its
  only argument). `main()` wires ~12 subcommands, not six.
- It **is** tested: `TestSkillInvocation` in `test_transcript_analysis.py` holds
  20 cases.
- Its sidechain exclusion is **deliberate**, pinned by three tests
  (`test_sidechain_excluded`, `test_sidechain_user_records_excluded_from_slash`,
  `test_sidechain_user_excluded_main_thread_user_counted`). Subcommand,
  registration, tests, and exclusion all landed together in one commit.

What it already does: extracts `input["skill"]` and nothing else; handles both
the `Skill` tool-use form and the `<command-name>/x</command-name>` slash form;
buckets top-level / routed / user-slash via `attributionSkill`; emits fixed-width
plain text (no `--json` anywhere — do not add one).

**Two consumers ask different questions of one code path.** This is why subagent
inclusion is opt-in, not a changed default:

| Consumer | Question | Wants sidechain turns? |
|---|---|---|
| Skill-description budget analysis (existing) | Does this skill's *description* draw auto-triggers on the main thread? | No — noise |
| Procedural-fidelity review (new, §5) | Which procedures did this branch's work commit to? | Yes — a skill invoked in a spawned agent binds equally |

Flipping the default would silently change what previously-recorded budget
analyses mean. So the flag carries the distinction.

Changes required:

1. **`--branches B1,B2,...` filter.** Follow the `cmd_subagents` branch-filter
   shape. The branch test applies **only** when a filter is passed, so unfiltered
   runs keep counting records with no `gitBranch` — otherwise default output
   changes.
2. **`--include-subagents` (opt-in, default off).** When set, pass
   `include_subagents=True` to `iter_sessions` and count sidechain records,
   adding a `thread` column so main vs sidechain stay distinguishable. When
   unset, output is byte-identical to today — the 20 existing tests are the
   check.
3. **Repo-scoped default for `--projects` (the foundational fix).** See §1b — this
   is the real minimization control, and it reshapes the invariant below.

**Binding output invariant — provenance, not shape.** This subcommand's output is
routinely quoted into public PR descriptions. An earlier draft framed safety as
"extract only `input['skill']`, never `args`, and strip path characters." That is
the wrong invariant. Skill names are user-defined strings drawn from *whatever
projects exist on the machine* — plugin namespaces, directory qualifiers, and
branch names from any repo. Real records in this repo's own transcript dir carry
values like `.claude/worktrees/<branch>/claude:code-review` (path + branch in the
field the earlier draft called safe) and `<private-plugin-namespace>:<skill>`
(a private-project identifier with **no `/` at all**, which passes any
"contains no path separator" test and prints verbatim). No string transform
fixes this, because the leak is *provenance*, not *shape*.

**The control is therefore to scope the read, not to sanitize the output.** With
`--projects` defaulting to this repo's own project directories (§1b), no other
project's names can enter the output, so there is nothing to sanitize. Extracting
only `input["skill"]` (never `args`) still holds — `args` carries absolute paths
even for this repo's sessions — but it is one rule, not the whole guarantee.

**Skill-name normalization stays, demoted to cosmetics.** Within this repo's own
scoped transcripts, worktree sessions still record
`.claude/worktrees/<this-repo-branch>/claude:code-review`. That branch name is
*this public repo's own* — not a leak — but the qualifier splits one skill across
several rows. Strip the segment after the last `/` at extraction to collapse the
spellings. Apply the same collapse to the `attributionSkill` value printed in
ROUTED PAIRS, so both fields render consistently. This is row-hygiene, not a
security boundary — the boundary is §1b.

### 1b. Repo-scoped default for `--projects` — the minimization foundation

`_projects_glob(args)` defaults to `"*"` — **every project on the machine**.
For a general corpus tool that is the right default, but for this subcommand it
is the leak: it reads every private project's transcripts into output documented
as publish-safe. Fix: `skill-invocation` defaults `--projects` to **this repo's
own project directories**, derived at runtime. An explicit `--projects` still
works as an escape hatch for corpus analysis — but then the caller has chosen
non-scoped output knowingly.

Derivation (add a helper — there is no repo-path-to-slug helper today;
`_derive_proj_label` only reverses a slug into a display label). **Match by
identity, not by location** — an earlier draft matched a
`<main-slug>--claude-worktrees-*` glob, which a security pass defeated: that glob
scopes by *where a dir sits in the path string*, so a foreign repo cloned or
worktree-added under this repo's `.claude/worktrees/`, or a sibling repo at
`…/<repo>-fork` (prefix match), or two paths colliding under the lossy
`.`/`/`→`-` transform, all match. Location ≠ membership. Instead:

- Enumerate this repo's **actual** worktrees with
  `git worktree list --porcelain` — it prints absolute paths for the main
  worktree and every linked worktree, from within any worktree, independent of
  which one invoked it. This is the identity list; nothing outside it is in
  scope.
- Forward-transform each listed path to its slug (every `/` and `.` → `-`;
  verified against real dirs: `/home/u/repo` → `-home-u-repo`;
  `/home/u/repo/.claude/worktrees/b` → `-home-u-repo--claude-worktrees-b`) and
  read those **exact** dir names, matched **literally** — enumerate the
  directory names under `PROJECTS_DIR` and compare by string equality (not
  `Path.glob`, whose pattern semantics would let a path containing a `*`/`?`/`[`
  metacharacter widen the match). No prefix, location, or wildcard match is
  possible.
- **Fail closed** on any of: `git worktree list` failing (not a repo, `git`
  absent) → error, never fall back to `"*"`; and — to catch a poisoned
  environment (`GIT_DIR` set, submodule, bare layout) where git *succeeds* but
  returns paths unrelated to the cwd — assert the current working directory
  forward-maps to one of the enumerated slugs. If it does not, the environment
  is inconsistent; error rather than emit a scope that may not be this repo's.
- **Accepted residual, documented not fixed:** the slug transform is lossy
  (`/home/u/a/b` and `/home/u/a.b` collapse to one slug), so a foreign project
  whose path forward-maps to the *exact* slug of one of this repo's real
  worktrees would still match. This is vanishingly unlikely (it requires a second
  repo on the same machine whose *entire* path — every character position —
  forward-maps to the exact slug of one of this repo's worktrees, differing only
  where a `.`↔`/` swap collapses) and
  the transform is Claude Code's own dir-naming scheme, not ours to change. Pin
  it with a test that asserts the collision is known and accepted, so a later
  refactor cannot erase the awareness silently.

### 2. `claude/.claude/scripts/tests/test_transcript_analysis.py` — tests

**The 20 cases in `TestSkillInvocation` already cover the pre-existing behavior**
(top-level / routed / slash / sidechain-excluded / projects-filter / empty).
Do not re-author them. Add only the genuinely-new surface, following the
established harness: `_write_jsonl`, the `fake_projects` fixture (monkeypatches
`_mod.PROJECTS_DIR`), and the record builders `_asst` / `_user_msg` /
`_skill_use`. Call `cmd_skill_invocation` directly with a hand-built args object;
assert via the header-anchored `_table_cols` helper, matching `TestSubagents`.

New cases:
- **`--branches`** — filter excludes off-branch skills; comma-separated list;
  applies to slash invocations too; an unfiltered run still counts a record with
  no `gitBranch` (guards the default-output regression noted in §1).
- **`--include-subagents`** — a subagent-only invocation is absent by default and
  present with the flag on a `sidechain` thread row; the same skill on both
  threads renders as two rows, not one merged count; the thread column is absent
  without the flag; the flag composes with `--branches`.
- **Repo-scoped default** — with `--projects` left unset, the derived scope
  excludes a session in an unrelated project dir. The derivation helper must call
  the **module-level `subprocess.run`** (no local rebind) so tests stub it with
  `monkeypatch.setattr(subprocess, "run", fake_run)` — the exact seam `TestPrLink`
  already uses (`test_transcript_analysis.py:787,803,822`); no real git repo in
  the unit test. Cases:
  - Helper unit tests: `git worktree list --porcelain` output stubbed → correct
    exact-slug set (main + one worktree); the slug forward-transform (`/`,`.`→`-`);
    the cwd-not-in-enumerated-slugs inconsistency path.
  - **Fail-closed** (security-load-bearing; this is the file's first `SystemExit`
    test — none exists today): stub `subprocess.run` to fail → wrap the call in
    `pytest.raises(SystemExit)`, assert the stderr message via
    `capsys.readouterr().err`, **and assert `.out == ""`** on that path. The
    stdout assertion is the actual proof — a stderr-only check would still pass a
    regression that prints the machine-wide table before exiting.
  - **Escape hatch** — stub `subprocess.run` to raise (simulating "not a repo"),
    pass an explicit `--projects`, assert success with **no** `SystemExit` and no
    git call. This pins that an explicit scope never invokes the derivation, the
    load-bearing property §1b's escape hatch rests on.
  - **Slug collision** — pin the known lossy-transform collision (§1b accepted
    residual) with a test asserting it is accepted, not silently "fixed."
  Reuse `_write_subagent_jsonl` (per `TestSubagents`) for the subagent-layout
  cases rather than re-deriving the layout.

**Test the provenance invariant, not a shape proxy.** The earlier draft specified
"assert output contains no `/` path segment." That test would pass while a
colon-prefixed private-plugin name leaked, so it is the wrong assertion. Instead:
- A fixture record with `{"skill":"x","args":"/abs/path/secret-name"}` in scope:
  assert the skill name prints but the `args` value does not (the extract-only-
  `skill` rule).
- A fixture skill `<fake-namespace>:<skill>` living in an **out-of-scope** project
  dir: assert the namespace does **not** appear when running with the repo-scoped
  default. This is the real control — scoping — under test, not a character
  filter.
- A worktree-qualified name and its bare form in the **same** scoped dir collapse
  to one row (the cosmetic normalization), and a `plugin:skill` prefix on an
  in-scope name is preserved.

### 3. `claude/.claude/agents/skill-fidelity-reviewer.md` — new agent

Frontmatter matching the roster convention (`ciso-reviewer.md:1-6`): `model:
sonnet`, `name`, `description` with TRIGGER / DO NOT TRIGGER clauses.

**`tools: Read, Grep, Glob, Write` — without `Bash`, for the honest reason.** An
earlier draft justified withholding `Bash` as a *security boundary* preventing the
agent from reading raw transcripts. That rationale is false and must not ship: the
`ciso-reviewer` confirmed that `Read` accepts any absolute path and `Glob`
enumerates `~/.claude/projects/**/*.jsonl`, so removing `Bash` blocks one of three
routes to raw transcript content while leaving two open. A tool grant is not a
data boundary; the agent runs on the same machine with the same filesystem.

The correct framing: the agent **has no task-reason to read transcripts at all.**
The name-only extractor already reduced them to a skill-name list, which the agent
receives as input. So `Bash` is withheld because the task is closed-form (read
skill bodies, compare to a diff) and needs no shell — not because its absence
confines anything. Minimization lives in the extractor (§1b), upstream of this
agent, where scoping actually enforces it.

Two consequences the implementer must handle:
- The agent body must state plainly: *you are given the skill-invocation list as
  input; do not read session transcripts yourself.* This is an instruction, not a
  boundary — say so, so no future edit mistakes it for enforcement.
- `ready-for-review` must pass the **diff text**, not a diff *range expression* —
  without `Bash` the agent cannot run `git diff` itself.
- Verify `test_agent_roster.py` does not assert a uniform `tools:` line across
  reviewers. It is documented to enforce the file-based-output block
  byte-identically; if it also pins tool grants, widen that test with a named
  exception. Do not add `Bash` back to satisfy a test.

Body must define:
- **Input contract** — receives the `skill-invocation` output, the diff as
  literal text (never a range expression — it has no `Bash`), and the plan path
  if one exists. The instruction from the tools section — *do not read
  transcripts yourself* — belongs here too.
- **Name resolution** — the list carries display labels, not paths:
  `plan-it`, `claude:plan-it`, `skill-management:skill-review`. Resolve by taking
  the segment after the last `:` and reading
  `~/.claude/skills/<name>/SKILL.md` (or the repo's `.claude/skills/<name>/`).
  A label that resolves to no skill body on disk — `exit` and other built-in
  slash commands the `<command-name>` regex catches — is **skipped**, not
  flagged. This is self-maintaining: no built-in denylist for the extractor to
  drift against.
- **The comparison** — for each resolved skill, read its body from disk and
  identify what it *specifies as output*. Skills that specify no artifact
  (`branch-creation`) are dismissed in one line, not analyzed.
- **The standard, borrowed from `code-review/SKILL.md:226`** — a stated,
  reasoned abbreviation is not a finding; a silent one is. A rationale the
  invoked skill's own body already rebuts is not reasoned.
- **Anti-adoption guard** — read a skill body to extract its requirements, not
  to execute it. The reviewer must not perform the skipped procedure itself.
- **File-based output contract** — the block at `staff-backend-engineer.md:80-110`
  is byte-identical across all eight reviewers and is enforced by
  `test_agent_roster.py:201-231`. Copy it verbatim, substituting only the H1
  agent name. Do not paraphrase it.

Target ~110–130 lines, matching the roster (existing files run 110–143).

### 4. Agent registration — hard-gated, build fails if missed

- **`claude/.claude/hooks/tests/test_agent_roster.py:23-30`** — add to
  `REVIEWER_AGENTS`. `test_no_uncategorized_agents` (line 118) fails otherwise.
- **`README.md:197,199-206`** — add a roster bullet **and** bump the count word
  in the sentence matched by `test_doc_counts.py:150-151`
  (`r"\*\*Reviewer subagents\*\* — (\w+) stack-agnostic personas spawned by"`).
  Read the current word and increment it; the test locks the literal against
  `len(REVIEWER_AGENTS)`.
- **`docs/design-decisions.md` §9** — "Reviewer persona roster operations" is the
  documented home for roster additions; record why this persona exists.

**No Item-ownership row** in `code-review` or `plan-review`: this agent is
spawned by `ready-for-review`, not by either dispatcher, so it does not belong
in tables that route checklist items.

**Citation provenance.** The line references in this section, plus
`staff-backend-engineer.md:80-110` and the `ready-for-review` step/marker line
numbers, come from a single reconnaissance pass and were not independently
re-read. The `transcript-analysis.py` citations in section 1 were verified
directly. Confirm the former before relying on an exact line number — the
surfaces themselves are certain, the offsets are not.

### 5. `claude/.claude/skills/ready-for-review/SKILL.md` — new step

Insert as a new step after "3. Code review (halt on findings)" and before "4.
Sync PR description," renumbering the steps that follow. Its shape:

- Resolve the branch, run `skill-invocation --branches <branch>
  --include-subagents`. `--projects` is **omitted deliberately** so the
  subcommand's repo-scoped default (§1b) applies — that default is the
  minimization control; passing an explicit `--projects` here would be a mistake.
- If the list is empty, state that and continue — an affirmative no-op, not
  silence.
- Otherwise spawn `skill-fidelity-reviewer` synchronously with the list, the
  **text** of the cumulative diff computed in step 3
  (`git diff $(git merge-base origin/$BASE_REF HEAD)...HEAD`) — not the range
  expression, since the agent has no `Bash` — and the plan path if one exists.
  Pass `findings_path` per the reviewer contract.
- **Exclude the review pipeline's own skills from evaluation.** The list will
  contain `code-review`, `plan-review`, `ready-for-review`, and this run's own
  invocations, including skills still mid-execution. Name them as out of scope in
  the step text and in the agent body. Without this the reviewer will try to
  audit the gate that is currently running it, and report a procedure as
  incomplete because it has not finished yet.
- **Halt on a silent-abbreviation finding.** The escape hatch is stating the
  deviation and its rationale — a low bar that induces exactly the behavior
  wanted. Reuse the surrounding steps' halt-on-fail phrasing.

The file is 163 lines and has an activate/deactivate gate at steps 0 and 8; this
step sits between them and needs no marker handling of its own.

### 6. `claude/.claude/skills/plan-review/SKILL.md` — B5 paragraph

The independent reviewer runs at branch handoff and needs a diff. Plan-time
abbreviation is therefore outside its reach, and the earliest, cheapest place to
catch it is plan review itself. Extend **B5 — Evidence and verification** (line
102) with a closing paragraph:

> **An invoked-then-abbreviated skill is a missing evidence base.** When the plan
> or the session that produced it names a skill it invoked, check whether the
> artifacts that skill's body specifies were produced. Reframing an invoked skill
> as a "lens," "philosophy," or "principle to keep in mind" rather than a
> procedure to execute is the pattern to catch. A stated, reasoned abbreviation is
> not a finding; a silent one is. A rationale the invoked skill's own body already
> rebuts is not reasoned — re-read that body before accepting one.

**Why B5 rather than a Step 4 tripwire.** Step 4 tripwires halt the review —
*"do not spawn specialists until the foundation question is resolved"* (line 72)
— and mandate a `Foundation concern:` + `Lighter alternative:` output shape. An
abbreviated procedure has no "lighter alternative," so the contract does not
fit. B5 already governs conclusions asserted without supporting evidence, and
routes to "judgment (any reviewer)" in ROUTING.md — **no Item-ownership row and
no new checklist ID.**

### 7. `claude/.claude/skills/code-review/SKILL.md` — enumeration only

One edit. Replace `data exposure` inside the always-spawn sentence at line 226,
leaving the rest of the sentence untouched:

> data exposure (including prose that identifies a real customer, private
> project, internal product, codename, person, hostname, internal URL, email
> address, tracker ID, or filesystem path embedding a project name — a
> documentation-only diff is not exempt)

Categories are aligned to the redaction categories already canonical in repo
CLAUDE.md rather than an ad hoc list.

### 8. Documentation for the new subcommand — not hook-gated, easy to miss

- **`docs/transcript-analysis.md`** — one `##` section per subcommand is the
  established shape; add one.
- **`claude/.claude/skills/transcript-analysis/SKILL.md`** — add a row to the
  "Which subcommand to use" decision table (line 8).

### Wording constraints — hard requirements

- Everything under `claude/` is stowed to every user who clones this repo. No
  repo-, stack-, or engagement-specific tokens (repo CLAUDE.md, *Global skill
  bodies stay platform-agnostic*). Write the category, never an instance.
- This repo is public. No text added by this PR — skill bodies, agent body,
  plan, commit message, or PR description — may name a private project,
  engagement, or a filesystem path embedding one.
- Repo CLAUDE.md fixes the vocabulary: say "project" / "private project", never
  "client."

## Verification

Run from the worktree (the contributor `.venv` lives at the main worktree root
only, three levels up):

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

Three test files gate this change specifically and should be run first when
iterating: `test_transcript_analysis.py` (new subcommand), `test_agent_roster.py`
(roster membership + the byte-identical output-contract canary), and
`test_doc_counts.py` (README count word). A failure in the latter two means a
registration surface was missed, not that the logic is wrong.

The hook-alignment tests read fenced `marker.sh` blocks from
`plan-review/SKILL.md:18,241,248`, `code-review/SKILL.md:370`, and
`ready-for-review/SKILL.md:19-30,128-145` by exact content. The `ready-for-review`
edit inserts a step *between* those fences — confirm they are untouched.

Skill evals are **not** required: `skill-review` mandates them only after a
`TRIGGER`-block change, and no `description` field changes here.

Review gates:
1. `/skill-review` — hook-enforced on the SKILL.md edits.
2. `/agent-review` — required for the new agent file (`.claude/rules/`
   skill-and-agent-self-review). Item 15 is the file-based-output contract; the
   roster test enforces it mechanically, but the review is what catches a
   paraphrase that still passes.
3. `/code-review` — dispatches both of the above.

**End-to-end behavioral checks — required, not optional.** None of this is
unit-testable at the behavioral level:

- Run `skill-invocation --branches <this-branch> --include-subagents` (repo-scoped
  default). Assert **inclusion, not an exact set** — implementing this plan
  invokes further skills on the same branch, so an exhaustive assertion self-
  invalidates. Note the expected names are branch-dependent: an earlier draft
  asserted `plan-it`, `branch-creation`, `plan-review`,
  `skill-management:skill-review`, but the actual recorded set for this branch is
  `handoff`, `plan-review`, `plan-it`, and this review's own invocations — the
  planning session recorded its other skills under different branches. Confirm the
  real set with a dry run before writing the assertion, rather than copying a
  guessed list.
- Give a fresh session a branch where a skill was invoked and its artifacts
  omitted; run `/ready-for-review`; confirm the reviewer flags it. Then state the
  abbreviation with a rationale and confirm it stops flagging. Both directions
  matter — a check that fires unconditionally is as useless as one that never
  fires.
- Run `/code-review` on a documentation-only diff that rewrites identifying prose
  with no code file changed; confirm `ciso-reviewer` is spawned. If it still is
  not, the failure was control flow rather than enumeration wording, and edit 7
  must move to Step 0 instead of line 226.

## Out of scope

- **A per-commit fidelity check in `code-review`** — deliberately dropped; see
  Approach.
- **The `marker.sh` command-shape memory entry.** Split out per the engineer's
  call. The `&&`-chain mechanism the source analysis names as its most-evidenced
  recurring finding was already fixed before the analysis window closed —
  `9b270f3` (2026-05-20) blessed `marker.sh write <skill> && git commit`, and
  `47134d0` (2026-07-03) generalized it to any all-marker-op chain. The residual
  is a different, narrower shape (piping `marker.sh` output, chaining to a
  non-`git commit` command — reproduced live during this planning session). Any
  memory entry needs its own evidence pass first.
- **Filing the candidate tracker issue** — declined by the engineer this round.
- **Worktree-enforcement denials** — the source analysis states this is not
  warranted from its evidence given the short observable window.
- **A project layer in a separate private repo** — planned separately, in that
  repo. Its specifics identify a private project and do not belong here. It
  depends on this PR only for the enumeration in edit 7.
- **Raise to the engineer, not planned here:** that private repo tracks
  reviewer-findings files in a directory the base `code-review` skill directs
  into `.git/info/exclude`.
