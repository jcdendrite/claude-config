# Adopt `context: fork` for two analysis skills

## Context

Decide which of this repo's 34 skills, if any, should adopt the Claude Code
skill frontmatter field `context: fork`, and implement it where it is a clear
win with no behavior loss.

Claude Code keeps each invoked skill's rendered `SKILL.md` body in the
conversation as a message that persists across turns; after auto-compaction all
re-attached skills share a combined 25,000-token budget, with older ones
dropped entirely when a session has invoked many. This repo's workflow invokes
six or more skills per branch, with bodies at 470 (`code-review`), 289
(`plan-review`), 200 (several), and 199 lines — the profile that budget
punishes.

Why now: a tracker ticket asks whether to adopt `context: fork`, which makes
per-skill context isolation declarative. Nothing in this repo has ever
evaluated it — no design-decision entry, no plan, and no doc mentions the
field.

Intended outcome: a two-skill roster, a stated selection criterion future
contributors can apply, and mechanical enforcement of both.

## Approach

Adopt `context: fork` for exactly two skills — `transcript-narrative` and
`error-mode-analysis` — and reject it for every other skill in the repo,
including the two the ticket's framing points at hardest (`code-review` at 470
lines and `plan-review` at 289). The ticket's implicit selection criterion is
body size; the evidence says the right criterion is **working-set size** — how
large a working set the skill pulls into the parent that the parent never
needed.
`code-review` and `plan-review` already keep their working sets out of the
parent by dispatching subagents, so forking them buys only the body and costs
real capability. `transcript-narrative` and `error-mode-analysis` are the only
two skills whose bodies instruct the model to read full transcripts and full
PR-comment dumps directly into the invoking conversation, and whose deliverable
is a written artifact rather than a continuation of the parent's work.

Two consequences shape the rest of the change. First, a fork's saving is only
realized if its *return* is small, so each skill gains an explicit "write the
artifact to a file, return the path plus a short digest" step (M4). Second,
that return-shape change is a one-way behavior change for every stow consumer
and it breaks the one skill-to-skill seam in the pair — `error-mode-analysis`
consumes `transcript-narrative`'s output — so the plan must update the consumer
side, not only the producer (M8).

### Assumption ledger

**Root problem.** Two analysis skills instruct the model to read raw transcript
JSONL and full PR-comment GraphQL payloads into the invoking conversation,
where they are re-read every subsequent turn, even though the parent needs only
the analysis. Their combined body cost (228 lines) is a rounding error next to
their working sets, and `error-mode-analysis` Step 7 multiplies that working
set once per sub-window.

**Threat line.** These two skills are the repo's only forked skills that ingest
content the session owner did not write — raw transcripts (which may carry
credentials and PII) and GitHub PR comments (writable by any GitHub user on a
public repo). A fork runs unsupervised, in the parent's process identity, with
the full tool set. Both properties are load-bearing for M9 and M10.

**Givens** (fixed beyond this plan's reach):

- **G1 — Claude Code owns `context:` / `background:` / `agent:` semantics and
  their version floors.** Vendor-imposed; the repo can only choose values, not
  behavior. `[verified: code.claude.com/docs/en/skills]`
- **G2 — `claude/` installs to every stow consumer, whose Claude Code version
  this repo does not control.** Consumers own their own upgrade cadence;
  `install.sh` cannot upgrade the `claude` CLI for them.
  `[verified: README.md:95, root CLAUDE.md "Plans in this repo affect all stow users"]`

`marker.sh`'s session-resolution model and `require-plugin-version-bump.sh`'s
contract were previously listed here as givens G3 and G4. Both are this repo's
own artifacts and therefore in reach; they are conditions this plan *could*
change and deliberately will not, so they belong in **Out of scope** and appear
only there.

**Rows:**

1. A `background: false` forked skill runs inside the invoking session's own OS
   process — no separate `claude` process appears — so `marker.sh`'s ancestor
   walk resolves its writes to the parent session id. All three gate classes
   (content-addressed completion markers, session-keyed active-bypass markers,
   session-scoped review ledger) therefore survive forking.
   `[verified: probe run this session — see row 14 for the probe's own scope caveat,
   which applies to rows 1–4 alike]`
2. A `background: false` fork holds the full tool set, including `Bash`,
   `Read`, `Write`, `Skill`, and `Agent`.
   `[verified: probe run this session — row 14 caveat applies]`
3. A fork receives no conversation history, but does receive harness-injected
   `gitStatus`, `CLAUDE.md`, and `MEMORY.md`.
   `[verified: probe run this session — row 14 caveat applies; consistent with
   the docs' "Also loads: CLAUDE.md" table row]`
4. `AskUserQuestion` is **absent** from a fork's tool list — a forked skill
   cannot conduct user dialogue.
   `[verified: probe run this session — row 14 caveat applies]`
5. `background` defaults to `true`, so a bare `context: fork` yields a
   background fork with a narrowed tool set and edits outside `/rewind`
   checkpoints. `[verified: docs — "Default: true"; "applies its edits outside
   your session's checkpoints"]`
6. `background: false` requires Claude Code v2.1.218 or later; this machine
   runs 2.1.259. `[verified: the `background` row of the frontmatter-reference
   table at code.claude.com/docs/en/skills, which states "Requires Claude Code
   v2.1.218 or later"; installed version from `claude --version`]`
7. The commit-time structural validator applies exactly two checks —
   strict-YAML parse and `description`+`when_to_use` length — with **no closed
   frontmatter-key allowlist**, so `context:`, `background:`, and `agent:` pass
   it unchanged today.
   `[verified: plugins/skill-management/scripts/validate_skill_structure.py:71-107]`
8. `transcript-narrative` Step 2 instructs "Read each returned path directly
   with the Read tool" over the full `sessions --paths --include-subagents`
   file set — the largest single un-delegated parent-context load in the skill
   corpus. `[verified: claude/.claude/skills/transcript-narrative/SKILL.md:18-26]`
9. `error-mode-analysis` pulls up to 100 PR comments, 100 reviews, and 100
   review threads (each with up to 100 nested comments) in one GraphQL
   response, and Step 7 re-runs Steps 2–4 once per sub-window with the same
   shape. `[verified: claude/.claude/skills/error-mode-analysis/SKILL.md:44-55, :129]`
10. Neither candidate has a step requiring user dialogue to proceed:
    `error-mode-analysis` Step 1 states an explicit default for the
    unanswered-scoping case ("widen rather than narrow if a scoping question
    goes unanswered"), and `transcript-narrative` has no ask step at all.
    `[verified: error-mode-analysis/SKILL.md:20; transcript-narrative/SKILL.md
    read in full this session]`
11. `plan-review` Step 0 branches on "a plan-mode system reminder is present in
    this session" and *silently skips* the `.planmode-path` sibling write when
    absent — a fork never sees the parent's plan-mode reminder, so forking
    `plan-review` would silently degrade `require-plan-review.sh`'s
    `ExitPlanMode` gate rather than fail loudly. This is the decisive
    disqualifier for the ticket's largest candidate.
    `[verified: claude/.claude/skills/plan-review/SKILL.md:26,36; require-plan-review.sh:188-308]`
12. `read-docx-comments` was evaluated and rejected: its body already routes the
    raw XML through Bash rather than into context, so the only saving is its
    78-line body, while Step 1 ("If not provided as an argument, ask for it")
    and Step 5 ("ask the user if they want you to act on the feedback") both
    collide with row 4.
    `[verified: claude/.claude/skills/read-docx-comments/SKILL.md:11,78]`
13. Nested forking — a forked `error-mode-analysis` invoking a forked
    `transcript-narrative` per its Step 2 — behaves like ordinary subagent
    nesting and resolves session identity the same way. `[unverified]` —
    Verification step 1 exercises this directly before the frontmatter lands.
14. Fork behavior in a normal interactive session matches what the `claude -p`
    probe observed. `[unverified]` — the probe ran inside a nested
    non-interactive session; the observed mechanism matches what the repo
    already documents for Agent-tool subagents, but the interactive path was
    not directly tested. **This caveat governs rows 1–4, which rest on the same
    single probe run.**
15. The Claude Code version that introduced `context:` itself is unknown. Below
    it the key is an unrecognized frontmatter field and the skill runs
    in-context as it does today — graceful degradation to the status quo.
    `[unverified]`
16. A version window exists in which a consumer honors `context: fork` but
    ignores `background: false`, yielding a background fork whose narrowed tool
    set may lack the `Bash` both skills require. **Its width is unknown**, not
    narrow: the lower bound is row 15's unverified `context:` introduction
    version, so the window could span many minor releases across G2's
    uncontrolled consumer fleet. `[unverified]` — and note this row layers a
    *second* inference that row 15 does not support: row 15 establishes how a
    wholly-unrecognized key degrades, whereas this row assumes a recognized key
    with an unsupported sub-value degrades the same way. No source establishes
    that. M10 exists because this row cannot be relied on.
17. The `enforce-marker-script-shape.sh` `agent_type` allowlist gap is filed as
    a separate GitHub issue and excluded here. `[engineer-verified]`
18. Empirical resolution of the process-topology question preceded planning
    ("probe first, then plan"). `[engineer-verified]`
19. Whether Claude Code's own frontmatter parser is case-sensitive on the
    `context:` value, and how it treats a null or non-boolean `background:`
    value, is unestablished. `[unverified]` — M5's validator must therefore not
    assume its own exact-match check mirrors the vendor parser's tolerance; its
    unit tests cover the near-miss shapes regardless.
20. `claude/.claude/scripts/tests/test_transcript_analysis.py`
    (`TestSkillFilesReportObservedScopeNotUnionGuarantee`) pins two verbatim
    sentences from `transcript-narrative/SKILL.md:24,26` — inside the exact
    `:18-26` range M4 edits. `select-tests.py`'s `TRANSCRIPT_ANALYSIS_TEST_GLOB`
    cross-domain rule fires on any `SKILL.md` change, so a wording regression
    fails loudly rather than silently.
    `[verified: test_transcript_analysis.py, select-tests.py's cross-domain rule]`
21. `error-mode-analysis` Step 3 ingests GitHub PR comments — content any
    GitHub user can write on a public repo — and a fork processes them
    unsupervised with the full tool set (row 2) under the parent's session
    identity (row 1). Forking converts `general-purpose`'s escape-hatch trust
    basis from "a human's per-call decision to delegate" into a standing,
    automatic arrangement. `[verified: error-mode-analysis/SKILL.md Step 3;
    _lib.sh:2066-2069; docs/hooks.md:93,101]`
22. `mktemp -d` creates its directory with mode `0700`, so the no-argument
    default output path is not world-readable. A *caller-supplied* path carries
    no such guarantee and may resolve inside a git-tracked tree.
    `[verified: mktemp(1); error-mode-analysis/SKILL.md Step 6 already states
    the git-containment requirement for its scrubbing copy only]`
23. The residual prompt-injection path to gate release described in M9 — no
    runtime enforcement until the row-17 allowlist issue closes — is knowingly
    accepted for this change rather than blocking it, and the roster keeps both
    skills rather than dropping `error-mode-analysis` or pinning a
    non-gate-release `agent:` type. `[engineer-verified]`

### Mechanism justifications

**M1 — `context: fork` on `transcript-narrative` and `error-mode-analysis`.**
`anchors: root`. These are the only two skills whose bodies direct raw bulk
data into the parent conversation (rows 8, 9) with no residual parent work
after the artifact is written (row 10).

This is the plan's heavy mechanism — `context: fork` is unconditional
per-skill, whereas runtime dispatch is per-call — so the over-powered-primitive
check applies. Five lighter primitives, each anchored:

- **Do nothing; rely on the existing post-compaction caps.** Fails `root`: the
  25,000-token combined budget and 5,000-token per-skill cap bound only the
  *re-attached body* after compaction. Neither touches the pre-compaction
  working set, which rows 8 and 9 establish as the actual bulk.
- **Shorten the two bodies, or move content to a `REFERENCES.md`.** Fails
  `row8`/`row9`: at 85 and 143 lines both are already well under the 200-line
  cap, and `docs/skills.md:123` states outright that an auxiliary "adds
  Read-tool indirection without reducing context cost." Body size is not the
  cost being attacked.
- **In-body subagent dispatch per `subagent-delegation`.** This is the
  strongest competitor and it is *why* `code-review` and `plan-review` are
  excluded — for them it already works. It fails for these two specifically
  because their entire body *is* the task: an in-body dispatch would have to
  restate the whole body into a prompt and add a hop, leaving the body in the
  parent anyway. `context: fork` is the lighter expression of the identical
  intent, declared rather than narrated. `anchors: row10`
- **Convert both to lazy-loaded agent files under `claude/.claude/agents/`.**
  Fails `root`: agent bodies are lazy-loaded
  (`.claude/rules/review-pipeline-dispatch.md`), which would solve the body
  cost, but agents have no slash-command surface and both skills are
  user-invoked by name. This primitive is right for a dispatcher-only skill and
  wrong for a user-facing one.
- **`disable-model-invocation: true`.** Fails `root`: it suppresses the
  description from the listing budget (`validate_skill_structure.py:133`) and
  does nothing to the body or working set once invoked.

**M2 — `background: false` explicitly on both, and no `agent:` key.**
`anchors: row5, row2`. Without it, row 5's default gives a background fork
whose narrowed tool set may lack `Bash` and whose edits escape `/rewind`.
Omitting `agent:` keeps both forks on the default `general-purpose`, which is
deliberately absent from `_LIB_NO_GATE_RELEASE_AGENTS` (`_lib.sh:2066-2073`) —
pinning a review-only agent type would categorically forbid marker writes for
any future forked skill. Row 21 establishes that this choice is only safe when
paired with M9; the two ship together or neither ships.

**M3 — No `model:` or `effort:` frontmatter on either skill.** `anchors: root`.
A forked skill without `model:` inherits the invoking session's model, which is
precisely today's behavior when the skill runs in-context. Adding a pin in the
same change would conflate "where the work runs" with "which model does it" and
make any quality regression unattributable. The global CLAUDE.md's "pass an
explicit `model: sonnet` on every dispatch" rule targets Agent-tool call sites,
of which a forked skill has none; whether to extend that rule to fork
frontmatter is a separate decision (see Out of scope).

**M4 — Each forked skill writes its artifact to a file and returns the path
plus a short digest.** `anchors: root`. A fork that returns its full report
inline saves only the body, defeating M1. `transcript-narrative` gains an
optional output-path argument defaulting to a `mktemp -d` directory;
`error-mode-analysis` Steps 5–6 name explicit file targets for Artifact A and
Artifact B. Each output step also states that the default location is
temporary, so a user who wants to keep the artifact copies it out. Lighter
alternative considered and rejected: return the artifact inline and let the
user save it — that is the null change and forfeits the entire saving.

This trades a real workflow for the saving, and the trade is accepted rather
than hidden: today the full report sits in the parent, so a follow-up question
("why is lesson 3 ranked above lesson 5") is free. After the change the model
must re-open the artifact. That cost lands on the follow-up rather than on
every turn, which is the point, but it is a genuine loss and M11 records it for
users.

**M5 — Extend `validate_skill_structure.py` to deny `context: fork` with no
explicit `background` key.** `anchors: row5, row16`. The failure it catches is
silent: a bare `context: fork` gets background semantics, a narrowed tool set,
and no checkpoint coverage, with no error message anywhere. That is exactly the
frontmatter-footgun class this validator already owns (row 7), and the check
only requires the author to *state* a value, never to pick one — an explicit
`background: true` passes. Per row 19 the check must also reject a null or
non-boolean `background`, since `"background" not in frontmatter` alone would
accept `background:` (YAML null) or `background: "flase"` as "explicit."

Two lighter primitives: a `docs/skills.md` bullet alone is advisory, which
README's own "How enforcement complements instructions" section rejects for
this class; a `test_skills.py`-only check protects this repo's 34 skills but
not the downstream consumers who install `skill-management@claude-config`
precisely for commit-time frontmatter validation.

**Semver.** `plugin-semver`'s own table
(`plugins/plugin-semver/skills/plugin-semver/SKILL.md:40`) classifies a change
that "changes the outcome for anyone who relied on the prior version" as
**major**. A downstream consumer whose `SKILL.md` already carries
`context: fork` with no `background` key would find their next edit to *that
file* newly rejected. The bump is nonetheless **minor**, on the explicit ground
that no such prior reliance can exist: `context: fork` appears in no skill
anywhere in this repo, and the field is new enough that the plan treats a
downstream adopter as implausible rather than merely unlikely. If that ground
is wrong the bump is wrong — implementation must re-check
`git grep -rn "context:\s*fork"` across the repo immediately before bumping and
escalate to major if any hit exists.

**M6 — Roster test in `test_skills.py` asserting the exact set of skills
carrying `context: fork`.** `anchors: row11, row12`. The repo's own rule
requires test enforcement for a new convention in the same PR, and the specific
regression to prevent is a future contributor forking `plan-review` or
`code-review` on body-size reasoning — the exact mistake row 11 shows is
silently destructive. This mirrors `EXPECTED_EFFORT` in `test_agent_roster.py`.

Set-equality alone would fail identically whether an addition is sound or is
exactly the row-11 mistake, and in both cases the contributor's fix is "add the
name to the set." The test's failure message therefore must name the
working-set criterion and the two disqualifiers, and point at the
`docs/skills.md` bullets — so a contributor who hits it engages the criterion
rather than editing the set to make red go green.

**M7 — Document the v2.1.218 floor in README's Requirements section, without
enforcing it in `install.sh`.** `anchors: row16, G2`. `install.sh` already
hard-fails on the Python 3.11 floor, so the enforcing pattern exists — but a
`claude` version check would block installation for every consumer, including
the majority who never invoke either skill. Documented floor, unenforced, is
proportionate to G2. Row 16's window is of unknown width, so the README bullet
carries the diagnosis, and M10 carries the runtime guard that makes the failure
legible.

**M8 — Update `error-mode-analysis`'s consumer side, not only its producer
side.** `anchors: root`. `error-mode-analysis` Step 2 states that
`transcript-narrative` "produces the annotated per-phase timeline and a first
pass of ranked lessons," written for today's inline semantics; Step 5's
Artifact A sources its per-session evidence from that timeline. M4 changes the
inner skill's return to a path plus ranked lessons, and the failure is silent —
Artifact A loses evidence fidelity with no error. Step 2 must state that the
timeline is now file-only, and Step 5 must instruct reading the returned path
before assembling Artifact A. Step 7's per-sub-window re-run needs the same
path bookkeeping across windows.

**M9 — Negative marker instruction in both bodies, plus a test asserting it.**
`anchors: row21, row1`. Row 21 establishes the standing arrangement M2 creates;
this is the control that bounds it. Neither body today says anything about
`marker.sh`, and M6's roster test checks frontmatter shape only. Both bodies
gain an explicit "this skill never invokes `marker.sh` and never invokes a
review skill" instruction, and `test_skills.py` gains an assertion that neither
body references either — author-time preventive, adversarial-input
best-effort, without touching the
allowlist itself (Out of scope). The `docs/skills.md` precedent bullet states
the converse of the existing note too: a forked skill that ingests
externally-writable content must carry this instruction before defaulting to
`general-purpose`.

State the limit plainly rather than implying coverage: M9's test is a static
lint over this repo's own authored text, so it verifies what the skill *says*,
not what a model does when adversarial PR-comment content tells it otherwise.
No runtime enforcement stands between an induced `marker.sh` call and gate
release until the allowlist issue (row 17) closes. The residual risk is
bounded by the declared surface — a stow consumer's own review gate, not
cross-tenant data — and is accepted knowingly, not overlooked.

**M10 — Step-0 tool-availability guard in both bodies.** `anchors: row16`.
Row 16's window cannot be closed by documentation, and its failure mode is
undiagnosable: a background fork missing `Bash` either stalls at the first
command or trips a generic harness denial with nothing linking it to the
version floor. Both bodies gain a first step that stops and names the
v2.1.218 requirement when `Bash` is unavailable. The marginal cost is one
step in bodies this diff already rewrites. Lighter alternative rejected:
`install.sh` enforcement, per M7.

**M11 — CHANGELOG entry recording the return-shape change as one-way.**
`anchors: root`. `CHANGELOG.md`'s `[Unreleased]` section already documents
comparable user-invoked skill behavior changes, and every stow consumer gets
this one on `git pull` with no reinstall. The entry states plainly that the
old inline-report behavior is gone with no opt-out flag — a deliberate one-way
change rather than an oversight a user should report. The entry states the
user-facing fact only — the full report is no longer returned inline; read the
written file, or pass an output path, to get the full artifact — with no
reference to this plan or its internal deliberation.

## Critical files

Single `code-writer` dispatch, single phase. The files do not partition into
independently specifiable sets: the roster test asserts the frontmatter, the
validator check and the docs both encode the same selection criterion, and
every dispatch prompt would have to restate that criterion in full.

**Labels below name mechanisms inside this plan only.** `M<N>` and `row <N>`
resolve for no reader of a shipped file. Every entry below that prescribes
content for `docs/skills.md`, `README.md`, `CHANGELOG.md`, a `SKILL.md` body,
a test name, a docstring, a failure message, or a code comment requires that
content to state the fact in its own plain terms. Do not carry a plan label
into any file this change ships.

**Term discipline.** "Working set" is the criterion's name — hold it
throughout, in the plan and in the `docs/skills.md` bullet, rather than
alternating with "raw material" or "bulk data." For the second return value,
each skill returns its own specific summary (`transcript-narrative`: the
ranked lessons; `error-mode-analysis`: the Step 4 bucket table), so
`docs/skills.md` states the rule as "a path plus a short, skill-defined
summary" rather than introducing "digest" as a generic term that appears in
neither body.

**Modify:**

- `claude/.claude/skills/transcript-narrative/SKILL.md` — add `context: fork`,
  `background: false`, and `argument-hint: "[optional output path]"`. Add the
  M10 Step-0 `Bash`-availability guard and the M9 negative marker instruction.
  Add an output step: write the annotated timeline, quantitative appendix, and
  ranked lessons to the caller-supplied path, or to a `mktemp -d`-rooted file
  when none is given; state that the default is temporary; return that path
  plus the ranked lessons only. Apply row 22's git-containment check to the
  caller-supplied path, and run Step 5's PII/credential scan against the file's
  content *before* persisting it, not only before the user shares it. Step 5's
  scan itself stays otherwise unchanged — it is the sole defense for quoted
  prompts. **Do not reword `SKILL.md:24` or `:26`** — `test_transcript_analysis.py`'s
  `TestSkillFilesReportObservedScopeNotUnionGuarantee` pins both sentences
  verbatim (row 20).
- `claude/.claude/skills/error-mode-analysis/SKILL.md` — same three frontmatter
  fields (`argument-hint` naming the optional output directory), same M10 guard
  and M9 instruction. Amend Steps 5–6 to name explicit file targets for
  Artifact A and Artifact B, apply row 22's git-containment check to both, and
  run Step 5's PII/credential scan against each file's content before
  persisting it — Artifact B's existing Step 6 scrub-before-promotion gate
  already satisfies this for B, so the new requirement binds Artifact A; state
  that explicitly rather than leaving the asymmetry to inference. Then
  return the two paths plus the Step 4 bucket table rather than the reports
  inline. **Amend Step 2** per M8 to state that `transcript-narrative`'s
  timeline is now file-only, **and Step 5** to read that path before assembling
  Artifact A; **Step 7** to carry the returned paths across sub-windows. **Do
  not touch the `HOOK_TEST_FIXTURE: fetch-pr-comments` fenced block at lines
  41–53** — `test_require_respond_pr.py:772` reads it by path.
- `plugins/skill-management/scripts/validate_skill_structure.py` — add a third
  check to `validate()`: when `frontmatter.get("context") == "fork"`, require a
  `background` key whose value is a literal boolean, rejecting absent, null,
  and non-boolean values (row 19). Reuse the existing `parse_frontmatter()`
  helper and the established violation-string shape; do not add a new
  frontmatter parser.
- `plugins/skill-management/.claude-plugin/plugin.json` — raise `version`
  (minor, on M5's stated no-prior-reliance ground; re-check
  `git grep -rn "context:\s*fork"` immediately before bumping and escalate to
  major on any hit). `require-plugin-version-bump.sh` blocks the commit
  otherwise, and `plugin-semver@claude-config` must be invoked per
  `.claude/rules/review-pipeline-dispatch.md`.
- `claude/.claude/skills/tests/test_skills.py` — add (a) the M6 roster test
  asserting the `context: fork` set equals `{transcript-narrative,
  error-mode-analysis}`, each with a literal-boolean `background: false` and no
  `agent:` key, whose failure message names the working-set criterion and links
  the `docs/skills.md` bullets; (b) the M9 assertion that neither body
  references `marker.sh` or invokes a review skill; (c) a static assertion that
  each body still carries M4's write-to-file-and-return-path instruction and
  M10's guard, so a partial revert fails loudly; (d) validator unit tests
  covering absent, null, non-boolean, and near-miss-cased `context`/`background`
  values. Reuse `_all_skill_md_files()` (`test_skills.py:2067`) rather than
  re-globbing, and import `validate()` directly as the file already does at
  `:1322` and `:1343`.
- `scripts/dev/fork-topology-probe.sh` (new) — the committed, re-runnable form
  of Verification step 1's throwaway-skill probe, so the nested-fork and
  interactive-topology checks survive as a procedure rather than as tribal
  memory in a plan file. Re-run at each Claude Code version bump. Its header
  comment states what it verifies in its own terms — that a nested fork
  resolves to the same session and process ancestry as the outer fork.
- `docs/skills.md` — add bullets to **Skill architecture notes** (line 120),
  each written as its own statement rather than fused into a compound
  sentence:
  1. The selection criterion: fork on working set, not body size.
  2. The subagent-dispatch disqualifier, in two sentences — a skill that
     already keeps its working set out of the parent by dispatching subagents
     gains only its body from forking and loses capability; `code-review` and
     `plan-review` are the skills that rule currently excludes.
  3. A skill that must ask the user cannot fork, because `AskUserQuestion` is
     unavailable there.
  4. A skill that branches on session-only state, such as a plan-mode
     reminder, cannot fork.
  5. A forked skill that writes a gate marker must stay on the default
     `general-purpose` agent type.
  6. A forked skill that ingests externally-writable content must state
     plainly in its own body that it never invokes `marker.sh` and never
     invokes a review skill.
  7. A forked skill returns a path plus a short, skill-defined summary, never
     its full artifact.
- `README.md` — add one Requirements bullet (line 91 section): Claude Code
  v2.1.218 or later for the two forked skills, naming the pre-2.1.218
  degradation in one clause and describing the guard by what it does — the
  first step of each skill body stops and names the version requirement when
  `Bash` is unavailable — as the symptom a consumer will actually see.
- `CHANGELOG.md` — an `[Unreleased]` entry recording the return-shape change
  as one-way, matching the format of the existing user-invoked-skill-behavior
  entries.

**Create:** `scripts/dev/fork-topology-probe.sh` (listed above).

## Verification

Run before staging, in the order given. Steps 1–2 gate the frontmatter change,
because row 13 is the `[unverified]` row the plan's correctness rests on.

1. **Nested-fork check (resolves row 13).** Run `scripts/dev/fork-topology-probe.sh`,
   which creates two throwaway skills under `.claude/skills/`, each with
   `context: fork` + `background: false` + `allowed-tools: Bash, Skill`; the
   outer invokes the inner by name; both print their process-ancestor chain and
   which ancestor owns a `sessions/<pid>` file. Confirm the inner fork returns
   to the outer and both resolve to the same Claude main-process PID. The
   script cleans up its own skills on exit including on failure, so an aborted
   run leaves no stale skill directories.
2. **Single-fork end-to-end (partially resolves row 14).** After the frontmatter
   lands, invoke `/transcript-narrative` from a normal interactive session and
   confirm the fork runs, `transcript-analysis.py sessions --paths` succeeds
   inside it (Bash reachable), and the return is a path plus ranked lessons
   rather than the full case study.
3. **`error-mode-analysis` fork end-to-end, covering the second dependency.**
   Invoke `/error-mode-analysis` and confirm its `gh api graphql` call succeeds
   *inside* the fork — auth-bearing, one nesting level deeper than step 2, and
   the dependency step 2 does not exercise.
4. **Follow-up-question flow.** After step 2, ask a question the digest alone
   cannot answer (e.g. the evidence behind a specific ranked lesson) and
   confirm the model re-opens the artifact rather than answering from the
   digest or refusing. This is the workflow M4 knowingly trades away; the check
   is that the fallback works, not that the cost is zero.
5. **Structural validator, direct.**
   `.venv/bin/python3 plugins/skill-management/scripts/validate_skill_structure.py claude/.claude/skills/transcript-narrative/SKILL.md claude/.claude/skills/error-mode-analysis/SKILL.md`
   — exits 0. Then run it against scratch fixtures carrying, in turn: a bare
   `context: fork`; `background:` (null); `background: "flase"`; and
   `context: Fork` — confirming the first three exit 1 with the M5 message and
   that the fourth's handling matches whatever row 19 resolves to.
6. **Test suite.** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
   Note that this diff touches `plugins/skill-management/.claude-plugin/plugin.json`,
   which matches no `DOMAIN_RULES` predicate, so `select-tests.py` fails open to
   the full suite via its `unmatched-path` reason code. That is the tool working
   as designed, not a manual widening — expect a full-suite run and do not
   re-invoke it by hand.
7. **Python lint:** `.venv/bin/ruff check claude/.claude/` and
   `.venv/bin/ruff check plugins/skill-management/`.
8. **Shell lint** for the new probe script:
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
9. **Gate skills, in pipeline order:** `/skill-review` (hook-enforced on the two
   `SKILL.md` diffs), `plugin-semver` (hook-enforced on the `plugins/` diff),
   then `/code-review`.

## Out of scope

- **`marker.sh`, `_lib.sh`'s session resolution, and the seven gate hooks.**
  This repo owns them and could change them; it deliberately will not here.
  Row 1 establishes they already work unmodified under forking, and replacing
  the ancestor-walk resolution model is a separate architectural decision with
  blast radius across all seven hooks.
- **`require-plugin-version-bump.sh`'s contract.** Likewise this repo's own,
  likewise unchanged: the `plugin-semver` skill owns that contract and M5
  complies with it rather than amending it.
- **The `enforce-marker-script-shape.sh` `agent_type` allowlist gap** — filed
  as a separate GitHub issue (row 17). M9 bounds this plan's exposure to it
  without touching it. A future change to `_LIB_NO_GATE_RELEASE_AGENTS` must
  re-check the forked-skill roster.
- **Forking `code-review`, `plan-review`, `ready-for-review`, `pr-description`,
  `plan-it`, `handoff`, `brief`, `branch-management`, or
  `ai-instruction-and-memory-files`.** All evaluated and rejected — the first
  four already delegate their working sets, `plan-review` additionally breaks
  its own plan-mode gate arm (row 11), and the rest consume conversation state
  a fork cannot see (rows 3, 4).
- **Forking `review-permissions`, `skill-review`, `agent-review`,
  `claude-hook-review`, `issue-triage`, `tighten-prose`,
  `lovable-cloud-migration-sync`, or `read-docx-comments`.** Each is task-shaped
  and could technically fork, but none has a working set large enough to pay for
  the change; `read-docx-comments` additionally has two user-dialogue steps
  (row 12). Each further addition should re-run the `docs/skills.md` criterion
  on its own merits.
- **Adding `model:` or `effort:` to any forked skill's frontmatter, and
  extending the global CLAUDE.md "Model & Effort Routing" section to cover fork
  frontmatter.** M3 explains the omission. The CLAUDE.md extension is a
  global-instruction edit gated by `/ai-instruction-and-memory-files`, and a
  two-skill roster does not yet justify a general rule.
- **Enforcing the Claude Code version floor in `install.sh`.** M7's reasoning;
  M10 covers the diagnosability gap at runtime instead.
