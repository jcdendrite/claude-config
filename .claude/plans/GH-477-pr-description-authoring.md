# GH-477 — PR-description authoring standard

## Context

Give the first PR body an authoring standard and bring it under the same
checks every later body already gets. Today `/ready-for-review` syncs the PR
description at step 5 but does not create the PR until step 6, so on a branch
with no pre-existing PR step 5 no-ops by its own precondition ("If the branch
has no open PR, report 'no PR to sync' and stop") and the first body — the one
reviewers read first, written when the branch is least settled — is the only
body the pipeline never checks. Worse, the sole authoring guidance is step 6's
inline template, which models two defects `/sync-pr-description` exists to
flag: `git log --format="- %s"` emits the per-commit narrative its first bullet
reorganizes away, and `- [ ] (fill in manual verification steps)` is exactly the
leftover placeholder prompt the reader-coherence pass added in #475 looks for.
Now, because #475 shipped that coherence pass and its own first body still
carried two defects the pass caught on its first run — hand-writing is not a
reproducible mechanism. Outcome: one skill owns "the PR description is correct
and complete," with an authoring path and a sync path, and the pipeline never
writes a body it has not checked.

## Approach

**Split by concern, not by order.** `pr-description` owns body *content*;
`/ready-for-review` step 6 keeps only PR *lifecycle* (remote-tracking
precondition, TICKET-ID title derivation, `gh pr create`, capture PR number).
Once the skill authors the body *before* the PR exists, the issue's proposed
step-5/step-6 reorder has nothing left to do: step 5 simply stops skipping.

- Step 5 becomes unconditional, in one of two modes. No open PR → draft the
  body from branch state, run every check in the skill against the draft, write
  it to a temp file, and report that path. Open PR → fetch, verify, and apply,
  as today.
- Step 6 deletes its inline body template and creates the PR from that file.
- The skill is renamed `sync-pr-description` → `pr-description`, because a
  skill that authors as well as syncs is misnamed by "sync" — the same
  heading-negates-its-own-body shape GH-473 added a check for.

**Alternatives weighed.** *Reorder steps 5 and 6 only* (issue option 1) closes
the coverage gap with the smallest diff, but leaves the pipeline deliberately
writing a body its very next step flags, leaves the anti-pattern template in
the tree, and leaves the skill itself unable to produce a body when invoked
standalone on a branch with no PR. (That last gain is the skill's, not
`/handoff`'s: `handoff/SKILL.md:118` invokes it only on a branch that already
has an open PR, and widening that trigger is out of scope here.)
*A new standalone `write-pr-description` skill* (issue option 3) duplicates the
entire check list into a second file with no mechanism to keep the copies
aligned — this repo forbids shared partials across skills. *Adopting a
marketplace skill* (issue option 4) was not pursued: the common shape generates
the body from commit history, which is the anti-pattern being removed.

**Two mechanisms are heavier than the task strictly requires**; both were
checked against lighter primitives first.

- *Rename across 8 sites.* Lighter option (i): keep the name and add author
  mode — rejected, the name would then contradict the body. Lighter option
  (ii): a frontmatter alias so both names resolve — no such field exists;
  `SKILL.md` frontmatter carries `name`/`description`/`argument-hint`, and the
  repo already documents that it has no `includes`/`import`/`extends`.
- *Skill reporting a file path in one mode and applying in the other.* Lighter
  option (i): create the PR with an empty body, then have the skill apply in
  both modes — uniform, but fires reviewer notifications on an empty PR and
  leaves a bodyless PR if the run aborts between the two writes. Lighter option
  (ii): give the skill `gh pr create` as well, so it always applies — rejected
  as the heavier choice, since it drags remote-tracking preconditions and title
  derivation into a body-content skill.

### Body handoff: a file, not a shell variable

The body crosses the step-5 → step-6 boundary as a **temp file**, not a shell
variable. The Bash tool does not persist shell state between calls, and step 5
runs via the Skill tool in a separate execution context — a `$BODY` variable
set in step 5 is unset by the time step 6 runs, which under default `bash`
expands to the empty string silently rather than erroring. Step 6 therefore
uses `gh pr create --body-file <path>` (`-F, --body-file file` — verified
present in `gh` 2.93.0, alongside the same flag on `gh pr edit`).

Four details of the carrier are load-bearing and must be stated in the skill
and step bodies, not left to the implementer:

- **The file is populated with the `Write` tool, not a Bash heredoc.** A
  heredoc redirect (`cat > "$f" <<EOF … EOF`) is parsed by the shell exactly
  as `--body "$(cat <<EOF …)"` is — the substitution hazard follows the body
  into the file rather than being dissolved by it. If a heredoc is used at
  all, the single-quoted `<<'EOF'` delimiter is mandatory.
- **`mktemp` is invoked with an explicit template**:
  `mktemp "${TMPDIR:-/tmp}/pr-body.XXXXXX"`. Not for portability — bare
  `mktemp` works on GNU, FreeBSD, and macOS alike — but because the default
  name is `tmp.XXXXXXXXXX`, which tells a human who later finds the file in
  `/tmp`, or who has to identify it in a step-6 halt message, nothing about
  what it is. Either form supplies the random suffix that keeps concurrent
  `/ready-for-review` runs in sibling worktrees of the same repo from
  colliding; a repo- or branch-derived fixed name would not.
- **Step 5 reports the path on a line of its own**, as the last line of its
  report: `BODY_FILE: <path>`. Step 5's report is otherwise long (check
  results, coherence findings), and the only channel to step 6 is the
  orchestrator re-reading that prose; a fixed single-line form makes the
  path extractable rather than transcribed. This mirrors the
  `findings_path:` convention `/code-review` and `/ready-for-review` step 4
  already use for reviewer output.
- **Emptiness means no non-whitespace content**, not zero bytes. A byte-size
  test (`[ -s "$f" ]`) passes on a whitespace-only file — precisely the
  truncated-write shape the halt exists to catch.

The file is **not deleted on success.** A `gh pr create` that fails after the
body is authored (auth expiry, no remote tracking) can then be retried against
the same file instead of re-running the whole authoring pass; the cost is one
small `/tmp` artifact per run, which `mktemp`'s unique naming keeps harmless.

Step 6 must **halt if the body file is missing or empty** rather than creating
a PR with an empty body. An empty-bodied PR is worse than no PR: it is not
recoverable by a re-run, because once the PR exists step 5 takes its *sync*
branch, and sync compares a body against branch state — it is not designed to
author one from nothing.

Given a `Write`-tool-populated file, the skill's current "Backtick hygiene"
section is **collapsed rather than duplicated for the new path.** That section
exists only because bodies are assembled inside `--body "$(cat <<'EOF' … EOF)"`.
Both paths move to `--body-file`, and the section is replaced by the one-line
file-based rule above — one defensive layer removed, not a second copy of it
added.

**Authoring standard, grounded in Google eng-practices "Writing good CL
descriptions."** The verbatim quotes live in a new `pr-description/REFERENCES.md`
per this repo's edit-time-reference convention; the **skill body states each
rule on its own rationale and does not name Google** — a named org's practice
cited as the reason a rule holds is the source-material bias anchor the
platform-genericness rule forbids in a globally-stowed skill body.

- Body answers **What** and **Why** — *"**What** change is being made? This
  should summarize the major changes such that readers have a sense of what is
  being changed without needing to read the entire CL."* and *"**Why** are
  these changes being made? What contexts did you have as an author when
  making this change? Were there decisions you made that aren't reflected in
  the source code?"*
- First line stands alone — *"the first line should stand alone, allowing
  readers to skim through code history much faster."*
- The `git log` template is indicted by the source's own generated-description
  clause: *"Some CLs are generated by tools. Whenever possible, their
  descriptions should also follow the advice here. That is, their first line
  should be short, focused, and stand alone, and the CL description body should
  include informative details."* The named bad descriptions include *"Moving
  code from A to B."* and *"Phase 1."* — a bulleted list of commit subjects is a
  sequence of exactly these.
- Sync mode is grounded by the same source: *"CLs can undergo significant
  change during review. It can be worthwhile to review a CL description before
  submitting the CL, to ensure that the description still reflects what the CL
  does."*

Two candidate sources were checked and set aside. GitHub's own
`creating-a-pull-request` documentation carries no normative guidance on body
content — it says only to "Type a title and description for your pull request."
Conventional Commits governs commit-message *format*, not PR body content, and
this repo's title convention is already `<TICKET-ID>: <slug>` from step 6.

### What the authored body must contain

**Section structure.** If the repo has a `.github/PULL_REQUEST_TEMPLATE.md`,
author mode reads it and uses its headings as the structure. This must be an
explicit read step: `gh pr create --body-file` never consults the repo template
(nor does `--body`), so "the repo template wins" is only true if the skill
reads it itself. Absent a template, use `## Summary` and `## Test plan` — the
headings this repo's existing PRs use; renaming them is a gratuitous change.

**`## Test plan` renders results, not a checklist.** Step 2 of
`/ready-for-review` has already run by the time step 5 fires, so this section
carries what actually ran and its outcome, in past tense — not `- [ ]` items
and never a placeholder prompt. Writing a future-tense checklist for work
already done would reproduce the heading-negates-its-own-body defect the
skill's own coherence pass exists to catch, and the pass would flag the skill's
own output. **When step 2 was skipped** under its documented scope exception
(diffs containing no executable code — the most common shape in this repo), the
section states that the exception applied and no executable-code verification
ran. It does not fabricate results, and it does not go empty.

**`$ARGUMENTS` is incorporated, not merely consulted.** Today step 6 inserts
the caller's `/ready-for-review <context>` text near-verbatim, as a bare block
between the Summary and Test plan headings. Author mode must carry that content
into the body; silently paraphrasing or dropping it is an undocumented behavior
change to a documented input. **Where it lands must be specified**: the content
is folded into the What/Why prose under the body's own headings, not left as an
unlabeled trailing block. A bare block is exactly the span a reader arriving
cold stops on and asks "what is this?" — so leaving it un-integrated would set
up the coherence pass to strip the caller's own input, or to flag the skill's
own output.

**The Claude Code attribution trailer survives.** `🤖 Generated with [Claude
Code](https://claude.com/claude-code)` currently appears exactly once in the
repo — inside the step-6 template being deleted — so nothing else carries it.
Author mode appends it as the last line of the body.

**Deferred-findings block: extract and reinsert, don't self-exclude.**
`/code-review` delimits its `## Deferred review findings` block with
`<!-- code-review:deferred:start -->` / `<!-- code-review:deferred:end -->` and
relies on locating it mechanically on later runs. Instructing the coherence
pass to "treat it as opaque" conflicts with that pass's own trigger — "any span
a reader arriving cold would stop on and ask 'what is this?'" describes an HTML
comment block precisely. So the mechanism is: lift the delimited span out
before the coherence pass runs, and reinsert it verbatim afterward.

### Assumption ledger

```
Root: the first PR body is written with no authoring standard and is the only
body /ready-for-review never checks, because step 5's "no open PR → stop"
precondition fires before step 6 creates the PR.

Row 1 [mechanism]: step 5 becomes unconditional with an author mode —
anchors: root — a body authored before creation is checked by construction;
reordering steps 5/6 only checks a bad body after writing it.
Row 2 [mechanism]: step 6 deletes its inline body template — anchors: root —
the template is the anti-pattern source, not just an unchecked output; leaving
it means every future run regenerates what step 5 must undo.
Row 3 [mechanism]: rename sync-pr-description → pr-description — anchors: row1
— the skill's name must not contradict its body once it authors as well as
syncs.
Row 4 [mechanism]: new pr-description/REFERENCES.md — anchors: row1 — the
authoring standard needs primary-source grounding, and this repo's convention
puts source quotes in a co-located REFERENCES.md, not in the skill body.
Row 5 [mechanism]: temp file + --body-file as the step-5→step-6 carrier —
anchors: row1 — a shell variable cannot cross the boundary, and a file also
removes the shell-quoting hazard that the Backtick hygiene section defends
against.
Row 6 [assumption]: step 5's precondition is the reason the first body is
unchecked [verified: claude/.claude/skills/sync-pr-description/SKILL.md:14-15
and ready-for-review/SKILL.md:118-138] — anchors: root
Row 7 [assumption]: sites containing the literal string "sync-pr-description"
are settings.json:45, handoff/SKILL.md:118, ready-for-review/SKILL.md:120,
test_skills.py:402/444/445/448/449, docs/skills.md:10/11/30/42, README.md:141
[verified: repo-wide git grep] — anchors: row3
Row 8 [assumption]: code-review/SKILL.md:314 describes step 5's behavior
without naming the skill, and additionally carries a pre-existing off-by-one
("step 5 splices it ... at PR-creation time"; creation is step 6)
[verified: read of code-review/SKILL.md:307-316] — anchors: row3
Row 9 [assumption]: no section-anchor reference (§-style) points into
sync-pr-description, so the rename cannot silently break a cross-reference
[verified: grep for "sync-pr-description/SKILL.md" and §-anchor forms across
claude/, docs/, README.md, .claude/rules/ — zero hits] — anchors: row3
Row 10 [assumption]: SKILL.md frontmatter has no alias/second-name field, so
the rename is a hard break [verified: repo CLAUDE.md — frontmatter has no
includes/import/extends; no alias field in any repo SKILL.md] — anchors: row3
Row 11 [assumption]: the skillOverrides key and the skill directory name must
be renamed in the same commit — test_builtin_name_only_allowlist_matches_settings
computes name-only entries lacking a SKILL.md and asserts set equality, so
renaming one without the other fails [verified: test_skills.py:331-372] —
anchors: row3
Row 12 [assumption]: sync-pr-description is not in _NAME_DISPATCHED_NO_TRIGGER
(which contains only "skill-review"), so the rename does not touch that
contract [verified: test_skills.py:224] — anchors: row3
Row 13 [assumption]: gh pr create and gh pr edit both accept
-F/--body-file, including "-" for stdin [verified: gh pr create --help and
gh pr edit --help, gh 2.93.0] — anchors: row5
Row 14 [assumption]: the Bash tool does not persist shell state between calls,
so a $BODY variable set in step 5 is unset in step 6 [verified: Bash tool
contract — "Shell state (env vars, functions) does not persist"] — anchors:
row5
Row 15 [assumption]: the attribution trailer occurs exactly once in the repo,
inside the template being deleted [verified: grep "Generated with \[Claude
Code\]" across claude/ — single hit at ready-for-review/SKILL.md:137] —
anchors: row2
Row 16 [assumption]: step 2 is skipped entirely for diffs with no executable
code, so the authored Test plan section cannot always draw on real results
[verified: ready-for-review/SKILL.md:50-55] — anchors: row1
Row 17 [assumption]: the grown skill stays under the 200-line target — current
body is 82 lines [verified: wc -l; skill-review/SKILL.md:146 states the
200-line target]; the post-implementation total is a projection, recheck
before treating this row as closed [unverified] — anchors: row1
Row 18 [assumption]: ready-for-review is currently 197 lines [verified: wc -l];
whether the net change is line-negative is a projection — step 5 must grow to
describe two-mode branching, the file carrier, and the $ARGUMENTS guarantee,
plausibly offsetting the ~9-13 deleted template lines [unverified] — anchors:
row2
Row 19 [assumption]: whether the reader-coherence pass leaves the deferred
delimiters intact cannot be verified against today's files — the extract-and-
reinsert mechanism is the mitigation. Author mode's copy of it is exercised
only if this branch's own /code-review emits a DEFER; absent that, this row
stays open past this PR rather than closing [unverified] — anchors: row1
Row 20 [assumption]: shape (skill owns body, step 6 creates) and the rename
are both the engineer's call, not the plan's [engineer-verified] — anchors:
root
Row 21 [mechanism]: the body file is populated with the Write tool, and any
heredoc fallback must use the single-quoted <<'EOF' delimiter — anchors: row5
— a heredoc redirect is parsed by the shell exactly as --body "$(cat <<EOF)"
is, so a file only dissolves the substitution hazard when the shell never
parses the body; without this the Backtick hygiene deletion is premature.
Row 22 [assumption]: bare `mktemp` is portable — it needs no template on GNU
coreutils, FreeBSD, or macOS [verified: GNU `man mktemp` "If TEMPLATE is not
specified, use tmp.XXXXXXXXXX"; FreeBSD and macOS mktemp(1) both state "If no
arguments are passed or if only the -d flag is passed mktemp behaves as if -t
tmp was supplied"]. A plan-review round asserted the opposite (that BSD/macOS
errors without a template); that claim did not survive checking and is not the
reason for the pinned form. The skill pins
`mktemp "${TMPDIR:-/tmp}/pr-body.XXXXXX"` for a readable filename in the
step-6 halt message and in /tmp, not for portability — anchors: row5.
Row 23 [mechanism]: step 5 reports the path as a final `BODY_FILE: <path>`
line — anchors: row5 — the orchestrator re-reads step 5's prose to build step
6's argument, and a fixed single-line form makes the path extractable rather
than transcribed; mirrors the existing findings_path: convention.
Row 24 [mechanism]: emptiness is "no non-whitespace content", not zero bytes —
anchors: row5 — `[ -s ]` passes on a whitespace-only file, the truncated-write
shape the halt exists to catch.
Row 25 [assumption]: this repo records breaking config renames in CHANGELOG.md
under [Unreleased] → Changed, with migration instructions [verified: the
`skill-review` → `skill-management` entry carries an uninstall/install line] —
anchors: row3 — so the rename needs a CHANGELOG entry, not only a PR-body note.
```

## Critical files

**Rename + extend (the skill).** The `git mv` and the `settings.json` key
change land in the same commit (Row 11).

- `claude/.claude/skills/sync-pr-description/` → `claude/.claude/skills/pr-description/`
  (`git mv`, so history follows).
- `pr-description/SKILL.md` — update `name:` and `description:` (keep it free
  of `TRIGGER when:` blocks; it stays a name-dispatched workflow utility).
  Replace the "no open PR → stop" precondition with the two-mode entry, and
  **define the standalone no-PR case**: invoked outside `/ready-for-review` on
  a branch with no PR, author the body and report the file path rather than
  silently doing nothing. Add the authoring standard. Replace "Backtick
  hygiene" with the `--body-file` rule and switch the sync path to
  `gh pr edit --body-file`. **Reuse:** the existing check list *is* the
  standard — state that both paths run the same reader-coherence pass and the
  same pattern checks (per-commit narratives, reviewer-action items Claude can
  answer, TBD markers, files-in-diff-absent-from-body). Do not restate them
  per-path.
- `pr-description/REFERENCES.md` — new; Google eng-practices URL and the
  verbatim quotes above, plus the checked-and-set-aside notes on GitHub's PR
  docs and Conventional Commits.

**Pipeline.**

- `claude/.claude/skills/ready-for-review/SKILL.md` — step 5 retitled and made
  unconditional, passing `$ARGUMENTS` and the deferred-findings block, and
  reporting a body-file path on its own `BODY_FILE:` line; step 6 deletes the
  inline template, halts on a missing or whitespace-only body file, and runs
  `gh pr create --body-file <path>`; line 38 updated to name the step-5
  dependency. **Also line 141** ("Steps 3, 4, and 5 may have produced new
  commits or body edits") — on the no-PR path the body is now written by step
  6, so the enumeration becomes wrong; generalize to steps 3–6.
- `claude/.claude/skills/handoff/SKILL.md:118` — pointer text
  `run the `sync-pr-description` skill` → `run the `pr-description` skill`.
- `claude/.claude/skills/code-review/SKILL.md:314` — retarget to step 5 and fix
  the pre-existing off-by-one (Row 8): step 5 includes the block verbatim in
  the body it authors.

**Wiring + docs.**

- `claude/.claude/settings.json` — `skillOverrides` key rename.
- `claude/.claude/skills/tests/test_skills.py` — class docstring (:402) and the
  two wiring assertions (:443-449) updated. **Add two body-content assertions**
  pinning that `pr-description/SKILL.md` carries the two-mode dispatch — the
  existing wiring tests only prove callers name the skill, so a future edit
  collapsing it back to sync-only would leave them green while regressing the
  exact defect this change fixes. One **positive** (author mode is present) and
  one **negative** (the old stop-precondition is gone): a positive-only pin
  stays green if a regression re-adds `no PR to sync` *alongside* the new text,
  which is the heading-negates-its-own-body shape this change exists to
  prevent. Name both literals in the test, not a paraphrase. **Reuse:** follow
  `TestContinuityFileBucketCrosscheck` (:452-471), which asserts a literal
  procedural phrase is present in a skill body; no new test class needed.
- `CHANGELOG.md` — a `### Changed` entry under `[Unreleased]` recording the
  rename as breaking, with the "update local references to `/pr-description`"
  instruction. This repo already documents a config rename this way — the
  `skill-review` → `skill-management` entry carries its own uninstall/install
  migration line — and a PR body is not a surface a stow user hitting "skill
  not found" months later will search.
- `docs/skills.md` — lines 10, 11, 30, 42.
- `README.md` — line 141.

## Verification

Run from the worktree (the contributor `.venv` lives at the main worktree root
only, three levels up):

1. `../../../.venv/bin/pytest claude/.claude/` — wiring assertions, the new
   two-mode assertion, and the settings/SKILL.md set-equality test (Row 11).
2. `../../../.venv/bin/ruff check claude/.claude/` — only `test_skills.py`
   changes on the Python side.
3. `git grep -n "sync-pr-description"` returns hits only under `.claude/plans/`
   and `docs/reports/` (historical records, read-only per the preserved-content
   rule) — zero under `claude/`, `docs/skills.md`, or `README.md`.
4. `/skill-review` on both changed `SKILL.md` files — hook-enforced. Confirm
   the extended body does not violate its own brevity/duplication rules, that
   the Google attribution stayed in `REFERENCES.md`, and re-run `wc -l` on both
   files to close Rows 17 and 18.
5. **End-to-end dogfood:** this branch has no PR, so `/ready-for-review` here
   exercises the author path and the `--body-file` create path. This diff is
   skill/plan/docs only, so it also exercises the step-2-skipped branch of the
   Test plan rule (Row 16). Confirm the body answers What and Why, carries no
   commit-subject bullet list, no placeholder prompt, a past-tense Test plan
   naming the scope exception, the caller's `$ARGUMENTS` content folded into
   the prose rather than left as a bare block, and the attribution trailer.
6. Sync-path regression: after the PR exists, re-run `/ready-for-review` and
   confirm the open-PR branch fetches, verifies, and applies via
   `gh pr edit --body-file` — and that a deferred-findings block, if present,
   survives byte-identical with its delimiters (Row 19).

**Branches this verification set does not reach.** Naming them is the point —
each is a real path through the new skill that ships unexercised, and a silent
gap reads as coverage:

- *The step-6 halt on a missing or whitespace-only body file.* This is the
  change's headline safety invariant, and nothing above constructs the failure.
  Exercise it directly: after the dogfood, point the step-6 recipe at a
  nonexistent path and at a file containing only a newline, and confirm both
  halt before `gh pr create` runs.
- *`## Test plan` rendering real step-2 results.* This diff is docs-only, so
  the dogfood only reaches the scope-exception wording. The common case — real
  command output in past tense — is first exercised by the next code-bearing
  PR authored under this skill.
- *The `.github/PULL_REQUEST_TEMPLATE.md` branch.* This repo has no template
  and adding one is out of scope, so the read-the-template path has no
  exercise here. Accepted as unverified.
- *Author-mode extract-and-reinsert of the deferred-findings block (Row 19).*
  It only fires if this branch's own `/code-review` produces a DEFER, which is
  unlikely on a docs diff. Verification item 6 covers the *sync* path only. If
  no DEFER appears, Row 19 stays open past this PR rather than being treated
  as closed — say so in the PR description.

## Out of scope

- **Adding `.github/PULL_REQUEST_TEMPLATE.md` to this repo.** The skill will
  read one when a repo has it; creating one here is a separate call.
- **Stale count in `docs/skills.md:36`.** It reads "Twelve skills in this repo
  use `skillOverrides: name-only`" while `settings.json` has 14 `name-only`
  entries. Pre-existing drift, unrelated to this change, and a
  `detect-stale-doc-counts` plan already exists — raising to the reviewer
  rather than bundling a fix (Axis 1, bucket 3).

## Handoff note for the PR description

The rename is a hard break with no alias (Row 10), and `git grep` only covers
this repo. A stow user may reference the old name from a personal
`settings.local.json`, a project-layer skill, or muscle memory, and will get
"skill not found" with no soft-fail. The durable notice is the `CHANGELOG.md`
entry (Row 25); call the rename out in the PR description as well so it is
visible at review time.

Also state in the PR description: which of the "Branches this verification set
does not reach" items above actually stayed unreached on this run — in
particular whether Row 19 (author-mode extract-and-reinsert of the deferred
block) was exercised or is being carried forward as an open risk.
