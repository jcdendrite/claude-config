# GH-494 — skill-fidelity-reviewer: dismiss what its evidence cannot decide

## Context

**Goal:** stop `skill-fidelity-reviewer` from reporting "artifact absent" for
skills whose execution its evidence could never have decided, and make what it
declined to check visible instead of silent.

The reviewer's entire evidence base is the branch diff text plus an optional
plan path — it has `Read, Grep, Glob, Write` and no `Bash`. But its comparison
step tells it to "check the diff and plan for evidence those artifacts were
produced" for *every* in-scope skill, and its step-1 enumeration of what counts
as a specified output explicitly names "a marker, a PR edit" — two things that
never enter a repo diff. For `handoff` and `brief`, whose artifacts live at
`~/.claude/handoffs/<slug>-handoff.md` and `~/.claude/briefs/<slug>-task.md`,
the check is structurally unsatisfiable.

GH-494 reports the cost in two shapes. In one session the parent burned a turn
rebutting the finding. In another it escalated to the engineer as suspected
environment data loss — `~/.claude/briefs/` was empty because `resume-context`
had moved the file, not because anything was lost.

**Intended outcome:** the reviewer recognises an undecidable case, records it
where the parent will actually see it, and never flags it — so neither the false
finding nor the recovery turn happens, and the coverage boundary stays visible.

## Approach

Two coordinated edits to `skill-fidelity-reviewer.md`: a dismissal rule keyed on
**decidability**, and an output slot for what it dismisses. The second is not
polish — without it the rule emits a verdict the agent's own output contract has
nowhere to put (row 12), converting a false positive into silent non-coverage.

### The rule is decidability, not artifact location

Keying on artifact location is wrong twice over.

It is wrong for `respond-pr`, which commits code changes (row 10) — a
diff-visible artifact. Deciding whether that commit addressed the review
comments requires knowing what the comments said, which is not in evidence. A
location test would evaluate it and reach a confident wrong answer.

It is wrong for `plan-it`, in the opposite direction. In plan mode `plan-it`
writes to the harness path under `~/.claude/plans/` and only moves the file onto
the branch after approval (row 14). Any category keyed on the `~/.claude/`
prefix would sweep up the one skill whose artifact is most reliably decidable —
the exact over-dismissal this plan's negative control exists to catch.

So the test is **un-relocatability**: does the artifact ever enter a branch
diff, and can you judge from what you were given that the skill was carried out.

| Skill | Why undecidable |
|---|---|
| `handoff`, `brief` | user-scope continuity file; never enters a branch diff |
| `pr-description` | pull-request body, written via `gh pr edit --body-file` |
| `respond-pr` | commit is diff-visible, but the comment set defining its correctness is not (row 10) |
| `branch-management`, `subagent-delegation` | specify no artifact — today's rule, absorbed by the general one |

`respond-pr` and `pr-description` are not on the reviewer's existing "Out of
scope" list — that list covers pipeline recursion (don't audit the gate running
you), a different reason. So they are live false-positive sources today.

**Is dismissing these safe?** `handoff`/`brief` were never detectable (row 3 is
decisive). `pr-description`'s empty-body failure is backstopped on the create
path by `/ready-for-review` step 6's `[ -f ] && [ -n ]` halt and on the sync
path by step 7's `gh pr view` re-fetch (row 11). `respond-pr` keeps its own hook
gate. Decidable skills — `plan-it` above all — are untouched.

**Alternatives set aside.** Two heavier designs would give the reviewer more
evidence rather than teach it the limits of what it has:

- *Plumb a listing of `~/.claude/handoffs/` and `~/.claude/briefs/` through the
  dispatch prompt.* Unsound three ways: a consumed continuity file has been
  moved out of those directories (row 3), an mtime cannot be bound to this
  branch, and `/handoff` commonly runs after `/ready-for-review` (row 9).
- *Grant the reviewer `Bash` to date-bound artifacts itself.* More privilege for
  a check that stays unsound for the same reasons — and a handoff file is
  exactly the deviating session's own rationale, which the agent's premise
  depends on never seeing.

### Assumption ledger

**Root problem:** the reviewer evaluates skills its evidence cannot decide,
producing false absent-artifact findings that cost the parent a recovery turn —
and would, once dismissed, produce silent non-coverage instead.

**Mechanisms**

- *Decidability-keyed dismissal in "The comparison"* — anchors: root. Fixes the
  false finding at the one site that decides whether a skill is evaluated.
- *Dismissal surfaced in "Output format", including the pointer line* — anchors:
  root. Without it the rule emits a verdict with no home (row 12), and the
  parent's only inline surface reports "Found 0 issues" with no signal that
  coverage was declined (row 15).
- *Disk-hunt prohibition, folded into the dismissal sentence* — anchors: root,
  as reviewer-side hygiene only. Symptom (b) is closed by **non-emission** — no
  finding means nothing for the parent to investigate — not by this clause,
  which addresses the reviewer rather than the parent that escalated.
- *Agent-body assertion tests* — anchors: root. Pin both edit sites.

**Assumptions**

| # | Assumption | Tag |
|---|---|---|
| 1 | The reviewer's only evidence is the diff text and an optional plan path; no `Bash`. | `[verified: skill-fidelity-reviewer.md:5, :18-23]` |
| 2 | `handoff`/`brief` write to `~/.claude/handoffs/` and `~/.claude/briefs/`. | `[verified: handoff/SKILL.md:6, brief/SKILL.md:6]` |
| 3 | `resume-context` moves a consumed continuity file out of those directories. | `[verified: resume-context.sh:144 — mv -- "$SRC" "$DEST"]` |
| 4 | `respond-pr`/`pr-description` are absent from the current out-of-scope list. | `[verified: skill-fidelity-reviewer.md:31]` |
| 5 | Step 1's enumeration names "a marker, a PR edit" as outputs to look for. | `[verified: skill-fidelity-reviewer.md:50]` |
| 6 | Agent-body string assertions are established repo convention. | `[verified: test_skills.py:402-450, _agent_body at :413]` |
| 7 | Generalize by evidence base rather than enumerate; keep illustrative examples but key each category on un-relocatability, not on write location. | `[engineer-verified]` |
| 8a | Dismissing `respond-pr` loses no *sound* coverage — it emits a diff-visible commit, but that commit cannot be judged without the comment set. | `[verified: respond-pr/SKILL.md:77]` |
| 8b | Dismissing `pr-description` loses no sound coverage — sync mode writes the PR body via `gh pr edit --body-file`; author mode returns only a temp `BODY_FILE:` path. | `[verified: pr-description/SKILL.md:28,143; ready-for-review/SKILL.md:129-130]` |
| 9 | `/handoff` commonly runs after `/ready-for-review`. | `[unverified]` — supports the rejected alternative only; nothing else depends on it |
| 10 | `respond-pr` has a diff-visible commit arm. | `[verified: respond-pr/SKILL.md:77]` |
| 11 | `pr-description`'s failure is caught downstream on **both** arms. | `[verified: ready-for-review/SKILL.md:136 create path; :148 sync path]` |
| 12 | The Output format has no slot for a non-finding dismissal — inline is "for each in-scope skill **with a finding**", file-based is "one H2 per finding" plus `## Recommendations`, and `:78` ends "Findings or nothing." | `[verified: skill-fidelity-reviewer.md:70-78, :92-97]` |
| 13 | In *this repo* the findings file cannot be staged, because `agent-reviews/` is committed to `.gitignore`. The `info/exclude` append in `code-review/SKILL.md:261` is **not** what protects it: under worktree enforcement `git rev-parse --git-dir` returns the linked-worktree gitdir, whose `info/` git ignores when `$GIT_COMMON_DIR` is set — six dead 15-byte `info/exclude` files exist in this tree. It is also conditioned on `/code-review` spawning ≥1 specialist, which a prose-only diff may not. | `[verified: .gitignore:19; dead excludes observed under .git/worktrees/*/info/exclude]` |
| 14 | In plan mode `plan-it` writes to the harness path under `~/.claude/plans/` and moves the file onto the branch only after approval — so a `~/.claude/`-prefix rule would capture it. | `[verified: plan-it/SKILL.md:17]` |
| 15 | When `findings_path` is present the agent returns **only** the pointer line, and `/ready-for-review` step 4 always passes one — so the inline count line is unreachable in the pipeline. | `[verified: skill-fidelity-reviewer.md:98-102; ready-for-review/SKILL.md:108-109]` |
| 16 | `skill-invocation` filters per record on `gitBranch`, so skills invoked before the branch existed do not appear under `--branches "$BRANCH"`. | `[verified: transcript-analysis.py skill-invocation branch filter]` |

## Critical files

### 1. `claude/.claude/agents/skill-fidelity-reviewer.md` — "The comparison"

Split step 1 so identifying the output and judging decidability are separate,
and drop "a marker, a PR edit" from the enumeration (row 5 makes that phrase the
invitation to the bug):

```
1. Read its body and identify what it **specifies as output** — a plan file,
   a written review, a named artifact, a required step sequence.
2. Decide whether execution is **decidable from your evidence** — the diff text
   and the plan path, nothing else. The test is not whether an artifact exists
   but whether you can judge that the skill was carried out. Undecidable when
   the skill specifies no artifact (`branch-management`, `subagent-delegation`);
   when the artifact never enters a branch diff (`handoff`, `brief` — user-scope
   continuity files); when it is a pull-request body or review comment
   (`pr-description`); or when it is diff-visible but its correctness turns on
   input you were not given (`respond-pr`, whose commit can only be judged
   against review comments you do not have). An artifact written outside the
   repo and later staged onto the branch IS decidable — judge it. A skill with
   both decidable and undecidable outputs is not dismissed: evaluate the
   decidable ones and record the remainder. Record every undecidable case —
   under `## Dismissed as undecidable` when your prompt gives `findings_path`,
   otherwise in the inline count — do not flag it, and do not go looking for it
   on disk: `resume-context` moves a continuity file aside once consumed, so
   absence there is not evidence either way.
3. For the rest, check the diff and plan for evidence those artifacts were
   produced.
4. Apply the standard below.
```

### 2. Same file — "Output format"

Three coordinated changes, because the pointer line is the only surface the
pipeline actually reads (row 15):

- **Pointer line** (`:99`) — `Wrote findings to <path>. Found <N> issues, <M>
  dismissed as undecidable. <One-sentence summary>.` A dismissal is never
  counted in `<N>`.
- **File-based output** — add `## Dismissed as undecidable` between the
  per-finding H2s and `## Recommendations`: one line per skill, naming the skill
  and the reason.
- **Inline output** — the opening count line reports dismissed separately from
  in-scope; and reconcile `:78`'s closing "Findings or nothing." so it does not
  read as forbidding the dismissal list. A dismissal is not padding — it is the
  coverage boundary, and hiding it behind a green gate is the ticket's own
  complaint in mirror image.

### 3. `claude/.claude/skills/tests/test_skills.py`

New class beside `TestConventionSkillWiring`, reusing its `_agent_body` helper
(`:413` — no new helper needed). Assert the load-bearing instructions:

- The decidability clause — pin "decidable from your evidence", the body's own
  vocabulary. Do **not** pin "the diff text and the plan file": the Input
  contract calls it "the plan path" (`:22`), so a later editor normalising the
  two breaks the test for a cosmetic reason.
- The disk-hunt prohibition — a stable substring of "do not go looking for it
  on disk".
- **Both** edit sites for the dismissal section — a single `in body` check is
  satisfied by either occurrence, so deleting the Output-format section leaves
  it green. Pin the pointer line's fuller `"issues, <M> dismissed as
  undecidable"` fragment — unique to the Output-format section and more robust
  than the bare "dismissed as undecidable" substring, which step 2's prose also
  contains — *separately* from a step-2-only assertion.
- The inline count-line change — the only feasible verification for that branch,
  since the smoke test cannot reach inline mode (row 15).

**Do not** assert `"resume-context" in body` (matches a subordinate clause, so a
rewrite reinstating the disk hunt stays green), and **do not** add a negative
assertion on `"a marker, a PR edit"` — it is a positional fragment of a comma
list, and the `TestPrDescriptionTwoModeDispatch` precedent does not transfer:
that docstring (`test_skills.py:465-467`) rests on *self-contradiction*, whereas
identify-then-dismiss is this design's intent.

## Verification

Worktree is three levels deep, so the venv is at `../../../.venv`.

1. **Test-first** (`test-conventions` §2): write the class and run it **red
   against the unmodified agent body**, then edit. `_agent_body` resolves from
   `__file__` and always reads the in-worktree file, so there is no
   after-the-fact way to check the assertions against the pre-change text.
2. `../../../.venv/bin/pytest claude/.claude/` — full suite green.
3. `/agent-review`, per `.claude/rules/skill-and-agent-self-review.md`.
4. `/code-review`, then `/ready-for-review`.

**Behavioural smoke test** — the only check that observes what string assertions
cannot, and the sole detector for the risk this change introduces: over-dismissal
(the reviewer sweeping in a skill whose execution *is* decidable). That failure
is silent by construction and ships to every stow consumer on `git pull`, so the
negative control is also the rollback trigger.

Run on the **second** `/ready-for-review` of this branch, when `pr-description`
appears in the invocation list (step 4 at `:87` precedes step 5 at `:121`, so
the first run cannot list it):

1. **Establish the control subject first** — run the step-4 command and read its
   real output rather than predicting it:
   `python3 ~/.claude/scripts/transcript-analysis.py skill-invocation --branches "$BRANCH" --include-subagents`
   Pick a listed skill whose artifact lands in the diff or plan. Picking from
   actual output matters because the list is branch-filtered (row 16) — anything
   invoked before the branch existed is absent.
2. **Positive:** `pr-description` appears under `## Dismissed as undecidable`
   with a one-line reason, and produces no `[SILENT-SKIP]` verdict.
3. **Negative control:** the subject from step 1 is *absent* from
   `## Dismissed as undecidable` in the findings file — which step 2 has already
   shown to exist and be populated, so absence is discriminating rather than
   vacuous. That is the whole observable. Not observables: "can still reach
   `[SILENT-SKIP]`" (a skill that did execute emits no finding line at all), and
   the in-scope total (it lives on the inline count line, which row 15 puts out
   of reach in the pipeline).
4. **Pointer line** carries a non-zero `<M> dismissed as undecidable`.

Record the chosen subject and all four outcomes verbatim in the PR description;
a manual check with no recorded artifact is unverifiable at review time. Per the
repo's standing position against an automated `claude -p` harness in CI, this
stays manual.

## Out of scope

- **Mirroring the rule into `/ready-for-review` step 4's prompt.** The agent
  body is the single home; step 4's prompt-side list covers pipeline recursion.
- **Editing `handoff/SKILL.md` or `brief/SKILL.md`.** Their write targets are
  correct; the defect is in how the reviewer reads them.
- **Moving `pr-description`/`respond-pr` into "Out of scope".** That section's
  reason is pipeline recursion; conflating the rationales obscures both.
- **Widening the reviewer's tool grant or evidence inputs.** Set aside above.
- **Follow-up ticket — real gap, not already mitigated.** The reviewer `Write`s
  into the working tree with no in-agent guard. This repo is covered by
  `.gitignore:19`, but stow consumers are not: `code-review/SKILL.md:261`'s
  `info/exclude` append is inert under worktree enforcement (git ignores a
  linked worktree's `info/` when `$GIT_COMMON_DIR` is set) and is conditioned on
  a specialist spawn that a prose-only diff may not trigger. Carry that
  diagnosis into the ticket body so it is not re-derived. The ticket also
  corrects `.gitignore:16-18` and `code-review/SKILL.md:261`, which both still
  describe the `info/exclude` append as an effective fallback — leaving that
  prose uncorrected would let a future reader re-derive the same wrong
  conclusion this plan just disproved.

## Branch and plan-file placement

Plan mode deferred branch creation. On approval: create
`GH-494/fidelity-reviewer-undecidable-artifacts` via `branch-management`, and
move this plan to `.claude/plans/fidelity-reviewer-undecidable-artifacts.md` on
that branch.
