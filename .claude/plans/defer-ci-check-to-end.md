# Move ready-for-review's CI-status check off the synchronous gate path

## Context

`ready-for-review`'s Step 8 checks PR CI status synchronously, immediately
before the gate records completion — and since CI can take anywhere from
seconds to hours, that synchronous check is close to guaranteed to report a
mid-flight "pending" state that carries no information. The user hit this
today verbatim: the gate printed "CI check is running (pending) — that's
warn-only, not a halt" right before "Recording gate completion," on a run
where CI genuinely had not had time to resolve. The intended outcome: the
gate's handoff summary posts without waiting on CI at all; CI status is
determined by an asynchronous background watch that reports once checks have
actually resolved, launched as early as the PR number is known so it overlaps
with the rest of the gate's own runtime; and if that watch reports a failure,
diagnosis and any fix route through isolated subagents — never the gate
session itself reflexively patching inline with its own accumulated,
stale context.

## Approach

Launch `gh pr checks <n> --watch` via `Bash` `run_in_background` the moment
the PR number is known (Step 1 if the PR already exists, Step 6 if just
created), let the rest of the gate and its Completion summary run without
waiting on it, and when the harness re-invokes this session on that
command's exit, branch on the final per-check `bucket` state: report a
one-line pass confirmation, or dispatch `general-purpose` to run
`/root-cause-analysis` against the failure and, only once the user confirms,
dispatch `code-writer` to implement the diagnosed fix through the gate's
existing staged-diff `/code-review` + marker gate.

### Assumption ledger

**Root problem:** `ready-for-review` Step 8 checks CI status synchronously
mid-gate, before the handoff summary, so it almost always reports a
mid-flight "pending" state that carries no information — yet still occupies
a bureaucratic line in the printed gate narrative on every single run.

**Givens:**

- G1: `gh` CLI is installed and authenticated in this environment.
  [verified: `claude/.claude/skills/ready-for-review/SKILL.md` current steps
  1, 6, and 8 already call `gh pr view`, `gh pr create`, and `gh pr checks`]
  — reason: this plan inherits an existing dependency of the skill, it does
  not introduce a new one.
- G2a: the harness's re-invocation model for `Bash` `run_in_background` work
  is fixed infrastructure this plan cannot change. [verified: empirical —
  a `sleep 8 && echo ...` launched with `run_in_background: true` triggered
  a real notification carrying its output with no new user message in
  between] — reason: another party (the harness) owns this mechanism. Caveat:
  this test exercised only an 8-second duration; the design's primary case
  is multi-hour CI, and nothing here confirms the harness's background-task
  tracking survives that duration across intervening user turns or context
  compaction — the multi-hour case remains an unverified inference from this
  short-cycle test plus the tool's own documentation, not a separately
  verified data point (see Verification's live-dogfood bullet, which flags
  the same limit per `staff-backend-engineer`'s plan-review finding).
  Extension, same mechanism: a follow-up empirical test this session (a
  backgrounded `sleep 5 && echo "<marker>" 1>&2`) confirmed the harness
  captures a backgrounded command's stderr-only output in the same combined
  output stream it captures stdout in — resolving `staff-backend-engineer`'s
  plan-review finding that G2a's original test (stdout-only, via `echo`)
  didn't cover stderr, which M2's zero-checks detection depends on. No
  special stream redirection is needed in the launched command itself; the
  harness's combined capture already includes stderr.
- G2b: the harness's re-invocation model for dispatched `Agent` calls is
  likewise fixed infrastructure this plan cannot change, but is grounded
  only in the `Agent` tool's own description text — never empirically fired
  this session, unlike G2a. [verified: documentation only, not empirically
  exercised — `Agent` tool description: "Subagents run in the background;
  you'll be notified when one completes"] `staff-backend-engineer`'s
  plan-review pass flagged that G2a's empirical test does not transfer to
  this mechanism; keeping them as separate rows with distinct evidence tiers
  prevents a reader from inheriting false confidence in the untested path
  from the tested one. — reason: another party (the harness) owns this
  mechanism.
- G3: no stated delivery-reliability tier for the notification channel
  itself (at-least-once, exactly-once, or best-effort) across a multi-hop
  chain (watch → diagnose → confirm → implement, up to three async
  re-invocations per the row below). [unverified] — flagged explicitly per
  `staff-backend-engineer`'s plan-review finding rather than left implicit:
  treat delivery as best-effort per hop, with no retry if a hop's
  notification is missed — an unstated assumption compounds silently across
  three hops, a stated one lets a reader judge the chain's aggregate
  reliability. — reason: another party (the harness) owns the notification
  channel's delivery semantics.

**Mechanisms:**

- M1 (anchors: root) — `Bash` `run_in_background` for the CI watch, not
  `Monitor`, not a subagent, not a synchronous foreground wait. `Monitor`'s
  own tool description explicitly steers away from this shape: "Don't use an
  unbounded command for a single notification... For 'tell me when X is
  ready,' use Bash run_in_background with an until loop instead (one
  notification, ends in seconds)" — a CI watch is exactly that single
  "tell me when it's done" case. A subagent dispatched purely to wait on CI
  doesn't avoid the parent's eventual context-reload cost (the parent still
  needs re-invoking either way) and adds its own LLM-turn overhead for a task
  with no judgment content — confirmed independently by
  `claude-config/review-pipeline-orchestrator-subagent`'s reply this session.
  A synchronous foreground wait is infeasible for the multi-hour case (the
  Bash tool's foreground timeout caps at 600000ms) and would fall back to
  `run_in_background` anyway, so it isn't a genuinely distinct mechanism —
  only a strictly worse one for the short-CI case, since it blocks the
  interactive turn.
- M2 (anchors: row "Pre-implementation blocker" below) — no separate
  synchronous guard command. The original design (superseded — see the
  "Pre-implementation blocker" ledger row) staged a `gh pr checks <n>
  --json name --jq 'length'` read ahead of the background watch, to skip
  launching `--watch` at all when a PR has zero checks configured. Reading
  `gh`'s own source (`cli/cli`, `pkg/cmd/pr/checks/checks.go`) shows that
  guard is both unimplementable and unnecessary: `checksRun()` calls
  `populateStatusChecks()` once *before* either the `--json` exporter branch
  or the watch loop; on zero checks it returns `no checks reported on the
  '<branch>' branch` immediately, so (a) `--json` never reaches its export
  step to print `[]` — the `--jq 'length' == 0` case the guard checked for
  is not a reachable outcome — and (b) `--watch` itself never enters its
  polling loop on a zero-check PR, so there's nothing for a guard to
  protect against. [verified: primary source, `cli/cli` `trunk` branch,
  `pkg/cmd/pr/checks/checks.go` — `checksRun()` calls
  `populateStatusChecks(...)` and returns its error immediately (before the
  `opts.Exporter != nil` branch and before the `for` watch loop);
  `populateStatusChecks()` returns `fmt.Errorf("no checks reported on the
  '%s' branch", pr.HeadRefName)` when `len(statusCheckRollup.Nodes) == 0`]
  The recipe is now two steps, both already in the original design, plus an
  explicit third outcome `staff-sdet` and `staff-backend-engineer`'s
  plan-review pass both independently flagged as missing (same root cause,
  named as two distinct failure modes — a stream-capture gap and an
  unhandled fallthrough — so both are folded in here rather than treated as
  one voice): (1) `gh pr checks <n> --watch` in the background — no special
  stream redirection needed; the harness's `run_in_background` capture
  already includes stderr, not just stdout (see G2a's extension). On exit,
  grep the captured output for `no checks reported` (gh's own literal error
  text, quoted above): a match means zero checks ever registered on this
  PR, at any point during the watch, so report "no CI checks configured for
  this PR" directly and stop — no further call. No match proceeds to (2) a
  separate `gh pr checks <n> --json name,bucket,description,link,workflow`
  call for a clean structured snapshot, whose `bucket` field is the
  pass/fail source of truth — not `--watch`'s own exit code, which carries
  no signal finer than `gh`'s own generic exit-code taxonomy offers (exit
  `8` is reserved for "checks still pending"; every other failure —
  checks genuinely failing, the zero-checks case, and an unrelated
  transient error alike — collapses to the same generic exit `1`;
  [verified: primary source, `cli/cli` `internal/ghcmd/cmd.go`'s exit-code
  constants and its `err == cmdutil.SilentError` branch, cross-checked by
  both plan-review specialists against the installed `gh 2.97.0` binary and
  `gh help exit-codes`] — so text-matching stderr for this one determination
  isn't a shortcut in place of a cleaner signal; no cleaner signal exists).
  A **third outcome**, distinct from "match" and "no match": step (2) itself
  can fail (non-zero exit, no JSON output) for a reason unrelated to zero
  checks — a transient network error, an expired token, the PR being
  deleted mid-watch, the same underlying failure a "no match" from step (1)
  could also mask if `--watch` errored for a non-zero-checks reason. Do not
  treat "no match" as proof step (2) will succeed. If step (2) fails, report
  "couldn't determine CI status — check `gh pr checks <n>` yourself" rather
  than assuming the PR's checks resolved cleanly; do not proceed to bucket
  parsing on a call that produced no JSON to parse. Steps (1)+(2) can't
  merge into one call: `gh pr checks --help` (gh 2.97.0) rejects `--watch`
  and `--json` combined outright ("cannot use `--watch` with `--json` flag"
  — confirmed via a live error this session). [verified: empirical, this
  session — the combined-flag error above; a plain `--watch` run against
  resolved PR #708 returned in 1.4s printing duplicated plain-text lines
  across its internal polls, confirming its own output is unsuitable to
  parse and must be discarded in favor of the separate `--json` call; a
  plain `--json` call against the same PR returned one clean parseable
  array; separately, a probe against a deliberately nonexistent PR number
  confirmed `gh` prints a `checksRun`-returned error to stderr with stdout
  empty, via the same `return err` code path the zero-checks error also
  uses — generalizing the stream destination to the zero-checks case
  specifically, which was never itself triggered against a real PR]
- M3 (anchors: root) — diagnosis via `general-purpose` + `/root-cause-analysis`,
  not `code-writer`, not inline in the gate session. `code-writer`'s own tool
  list (`Read, Edit, Write, Bash, Grep, Glob`) has no `Skill` tool, so it
  cannot invoke `/root-cause-analysis` regardless of instruction, and its
  charter is explicit: "Implement exactly what the dispatch prompt
  specifies," not diagnose an unspecified failure. [verified:
  `claude/.claude/agents/code-writer.md`] Running the diagnosis inline in the
  gate session was considered and rejected: `subagent-delegation/SKILL.md`'s
  own "Debug-investigation probe" section routes exactly this shape
  ("root-causing a check or test failure requires a read-heavy probe") to
  `general-purpose`/`Explore`, specifically so the investigation's read load
  doesn't sit in a parent session's context "for the rest of the session."
  [verified: `subagent-delegation/SKILL.md`]
- M4 (anchors: root) — implementation via `code-writer` once a diagnosis
  exists (which agent implements — independent of the separate
  "diagnosis/implementation autonomy split" row below, which governs only
  when implementation is triggered), deviating from
  `subagent-delegation`'s own generic worked example (where "the parent...
  applies the edit... inline"). CLAUDE.md's Model & Effort Routing routes all
  delegated code-writing — "feature code, fixes, refactors" — to
  `code-writer`, not the parent; by the time this fires, the gate session's
  context is the accumulated output of an entire `ready-for-review` run
  (verification, code review, skill-fidelity review) — exactly the kind of
  stale, bloated context unsuited to authoring a fix, which is what the user
  flagged directly. [engineer-verified: user's explicit statement this
  session — "the main session is going to try to fix it and really instead
  it should leverage an orchestrator agent"; `code-writer` is the concrete
  agent that resolves that concern given M3's finding that the
  not-yet-built `review-orchestrator` doesn't cover this case]
- M5 (anchors: root) — a HEAD-SHA staleness check before dispatching
  diagnosis, not a lock/mutex preventing concurrent background watches.
  `gh pr checks` is read-only, so two watches racing from repeat
  `ready-for-review` invocations (e.g. the user pushes a fix commit while an
  earlier watch is still in flight) cannot corrupt anything by running
  concurrently — cross-process coordination state (a lock file) to prevent
  that would be machinery with no corresponding risk to justify it. The real
  risk `staff-sdet`'s plan-review pass identified is downstream: two
  redundant notifications could each trigger a diagnosis dispatch, and if
  the user has since pushed a new commit, the original failure may already
  be moot — diagnosing and offering to fix a superseded failure wastes a
  dispatch and could confuse the user with a stale offer. A single cheap
  read (`gh pr view --json headRefOid`, compared against the SHA the watch
  was launched against) catches that case directly; a dedup lock would only
  prevent the harmless redundant watch, not the actual problem.

**Per-assumption rows:**

- The PR number becomes known at exactly two points in the existing flow —
  Step 1 (pre-existing PR) or Step 6 (newly created) — never both, never
  neither once a PR exists. [verified:
  `claude/.claude/skills/ready-for-review/SKILL.md` current steps 1 and 6]
- **Pre-implementation blocker — resolved** (escalated from a footnote per
  `staff-backend-engineer`'s plan-review finding on its blast radius, then
  settled against `gh`'s own source rather than a live zero-CI repo — see
  M2): (1) an empty `gh pr checks <n> --json name` array does **not**
  distinguish "no CI configured" from "checks not yet registered" — it
  can't, because that JSON array is never produced for a zero-check PR in
  the first place; `populateStatusChecks()` errors out before the `--json`
  exporter branch runs. Both states collapse to the same `gh` error and
  exit behavior, same as the pre-existing Step 8 already assumed for its
  non-`--json` path — not a regression, just confirmed rather than assumed.
  (2) `gh pr checks <n> --watch` does **not** hang indefinitely on a true
  no-CI PR — `populateStatusChecks()` is called once before the watch loop
  starts and returns its error immediately, so `--watch` exits fast in
  exactly this case, with no possibility of the silent resource leak this
  row originally flagged. M2 above reflects both findings in the recipe
  design: no separate guard command, and the zero-checks case is detected
  by grepping `--watch`'s own captured output for its literal error text
  after it exits, not by a pre-flight `--json` read.
- CI bucket semantics: `bucket` categorizes into `pass`, `fail`, `pending`,
  `skipping`, `cancel`. [verified: `gh pr checks --help` output this
  session]
- A dispatched `Agent()` call is asynchronous — it returns control without
  blocking on the subagent's completion, and a separate notification arrives
  later, potentially after the user has moved on. [verified: `Agent` tool's
  own description — "Subagents run in the background; you'll be notified
  when one completes... if the user asks before it arrives, say it's still
  running"] This means the full CI-failure path (wait → diagnose →
  report/offer → implement → review/push) can span up to three separate
  asynchronous re-invocations of this session, not one atomic turn — an
  accepted property of the design, not a defect to engineer away.
- Diagnosis/implementation autonomy split: diagnosis dispatches automatically
  on any CI failure (read-only, low risk); implementation dispatches only
  after explicit user confirmation. [engineer-verified: user's explicit
  answer to the "Failure handling" clarifying question this session]
- Bucket-to-branch mapping: `fail` on any check routes to the diagnosis path
  (naming only the failing check(s), not all checks); `pass` with no `fail`
  present routes to the one-line success report; `skipping`/`cancel` with no
  `fail` present is neither — report neutrally (no diagnosis, no "passed"
  claim) since nothing actually ran; a `pending` bucket surviving past
  `--watch`'s exit would be a race (a check registering after the watch
  observed terminal state) rather than an expected outcome — treat it as
  "still pending, re-check" rather than folding it into either branch.
  [engineer-verified: aggregation rule adopted directly in response to
  `staff-sdet`'s plan-review finding that only pass/fail were specified
  against a 5-value enum]

## Critical files

- `claude/.claude/skills/ready-for-review/SKILL.md` — the only file this
  plan changes.
  - **Reuse:** existing Step 3's "fix in a new commit → normal staged-diff
    `/code-review` + marker gate → return to step 2" pattern — the
    implementation path's post-fix flow re-triggers this exact existing
    mechanism instead of inventing a new commit/push/review flow.
  - **Reuse:** existing Step 7's "push them now" language for committing and
    pushing any fix commits the async follow-up produces.
  - **Preserve, do not edit the content of:** the two `HOOK_TEST_FIXTURE`
    fenced command blocks (Step 0's `marker.sh activate` recipe, currently
    lines 26–29; Step 9's `marker.sh write` / `marker.sh deactivate`
    recipes, currently lines 153–163) — the hook-alignment test suite reads
    these exact blocks verbatim from this file. Renumbering the step headers
    around them is fine; editing the fenced commands themselves is not.
  - **New/changed structure:**
    - One canonical "launching the background CI watch" recipe, referenced
      (not duplicated) from Step 1 and Step 6 — per CLAUDE.md's
      single-source-of-truth rule. Per M2, the recipe is two steps plus an
      explicit third outcome: (1) `gh pr checks <n> --watch` in the
      background — the harness's own combined-output capture already
      includes stderr, no special redirection needed; on exit, grep that
      output for gh's own literal `no checks reported` text — a match means
      zero checks ever registered, report "no CI checks configured for this
      PR" and stop, no further call; no match means (2) a separate
      `gh pr checks <n> --json ...` call for the clean structured snapshot
      the async follow-up parses. (3) If step (2) itself fails (non-zero
      exit, no JSON output), report "couldn't determine CI status — check
      `gh pr checks <n>` yourself" rather than assuming "no match" meant the
      checks resolved cleanly. The former "Pre-implementation blocker" is
      resolved (see the ledger row above) — no synchronous guard command
      runs before the background watch launches.
    - Old Step 8 ("CI status (warn only)") removed in its entirety.
    - Old Step 9 ("Record gate completion + deactivate session") renumbered
      to Step 8.
    - Completion section's CI bullet rewritten from a static status line to
      a forward-looking one (background watch running / no checks
      configured / already resolved — with a "check `gh pr checks <n>`
      yourself" fallback).
    - New unnumbered "## Async CI follow-up" section, placed after
      Completion, covering:
      - The bucket-to-branch mapping and multi-check aggregation rule from
        the ledger row above (fail-dominates; skip/cancel-only reports
        neutrally; residual `pending` is a race, re-check rather than
        branch on it).
      - Before dispatching diagnosis: the HEAD-SHA staleness check (M5) —
        if the PR's current head no longer matches the SHA the watch was
        launched against, report "superseded by a newer push, no action
        taken" instead of dispatching.
      - The pass path: one-line report.
      - The fail path: dispatch `general-purpose` for diagnosis, instructed
        to (a) apply `/root-cause-analysis`, (b) first check whether Step 2's
        local run of the same suite already passed — a local-pass/CI-fail
        split is `root-cause-analysis` Stage C's own asymmetry signal and
        the fastest route to root cause (environment/dependency drift vs. a
        real bug), and (c) apply Step 2's existing "Test-to-fit is
        forbidden: fix the code, not the test" rule rather than restating
        it. Report the diagnosis and offer to implement; dispatch
        `code-writer` only on confirmation, then reuse the existing Step 3/7
        review-and-push pattern. If the user does not confirm (declines, or
        the conversation moves on), take no further action and do not
        re-offer — the diagnosis stays available in-session if the user
        raises it again later.

## Verification

- `../../../.venv/bin/pytest claude/.claude/` from the worktree — regression
  check that the `HOOK_TEST_FIXTURE` blocks still match
  `require-ready-for-review.sh`'s expectations. This verifies fixture-text
  preservation only — it says nothing about bucket parsing, the
  zero-checks detection, or the dispatch chain; the burden for that logic
  rests entirely on the manual scenarios below. Note: per this skill's own
  Step 2 scope exception, a real future `/ready-for-review` run against this
  diff would skip its own check-suite step (markdown-only diff) — run pytest
  once by hand during implementation regardless, since the fixture-alignment
  risk is real even though the diff is markdown.
- `/skill-review` — hook-enforced for any `SKILL.md` change; required before
  commit.
- **Pre-implementation blocker — resolved via primary source, not a live
  repo test** (see the ledger row and M2): `gh`'s own source
  (`cli/cli/pkg/cmd/pr/checks/checks.go`) confirms both (1) an empty
  `--json name` array is not a reachable outcome for a zero-check PR — the
  command errors before reaching JSON export — and (2) `--watch` exits
  immediately, not indefinitely, on a zero-check PR, since the same error
  fires before the watch loop is entered. This settles the *design*
  question without needing a genuinely zero-CI-configured repo — but per
  `staff-sdet`'s plan-review finding, primary-source reading proves `gh`'s
  Go control flow, not the shell-level composition (backgrounding, output
  capture, grep) this skill's prose actually executes; the live dogfood
  bullet below now separately schedules a run against a genuinely
  zero-CI-configured PR to confirm the *implemented* mechanism behaves as
  designed, distinct from the checks-still-registering race (a timing
  question primary-source reading can't answer either).
- Live dogfood: once implemented, run `/ready-for-review` for real on this
  branch and confirm (a) the summary posts without a synchronous CI block,
  (b) a background watch launches and this session receives a genuine async
  re-invocation carrying a real CI result, (c) the forward-looking summary
  line reads sensibly. This proves short-cycle correctness only — CI
  resolving within the dogfood session's lifetime, likely minutes. It does
  not verify the design's primary case (CI taking hours, across intervening
  turns or context compaction); that gap stays an accepted inference from
  G2a's short-cycle empirical test plus the harness's own documentation, per
  `staff-backend-engineer`'s plan-review finding, not a separately verified
  data point. Also include:
  - One run checked immediately after PR creation before checks have
    registered, to catch the zero-checks detection's race condition
    specifically.
  - One run against a PR in a genuinely zero-CI-configured repo (per
    `staff-sdet`'s finding above) — confirm the grep fires and the skill
    reports "no CI checks configured for this PR" rather than silently
    falling through. Creating a throwaway repo for this is an outward-facing
    action; confirm with the engineer before creating one rather than doing
    it unilaterally.
  - The step-(2)-fails third outcome (M2) is a design fix for a failure mode
    that isn't practically forceable on demand (a transient network error,
    an expired token, a deleted PR) — it's exercised by code review of the
    skill prose, not a manual dogfood run.
  Skill-body prose isn't unit-testable, so this dogfood pass is the primary
  functional check for everything except the pre-implementation blocker
  above.
- Manually exercise the failure path against a **multi-job, mixed-outcome**
  CI config (e.g. lint pass, test fail, build skip) — not a single check —
  since mixed outcomes are the modal real-world shape the aggregation rule
  above exists to handle, and a single-check fixture would never exercise
  it. Walk both the confirm branch (diagnosis dispatch, report-and-offer,
  `code-writer` dispatch through to a pushed fix) and the decline branch
  (confirm nothing further happens and no re-offer occurs). Call this out
  explicitly as manual scenarios walked through once each — the live
  dogfood above only covers the pass path unless CI is deliberately shaped
  for this exercise.

## Out of scope

- Auto-implementing a fix without user confirmation — explicitly decided
  against (see the autonomy-split ledger row).
- A hard timeout on an indefinitely-hung CI check — no vendor- or
  protocol-documented value exists to ground a specific number against, and
  the user explicitly said CI durations range "even to hours"; a check that
  never resolves leaves the background watch running until it resolves or
  the session/process ends.
- Guaranteed delivery of the async follow-up if the interactive session ends
  (terminal closed) before the background watch exits — not specially
  handled; the forward-looking summary line's "or check `gh pr checks <n>`
  yourself" is the mitigation, not a delivery guarantee.
- Any dependency on `claude-config/review-pipeline-orchestrator-subagent` —
  confirmed unbuilt, unproven, and scoped to a different use case even on
  paper (see M3/M4 and that session's reply).
- Retry or recovery machinery for the diagnosis dispatch itself erroring
  (an `Agent()` call failing rather than returning a diagnosis) — not
  specially handled; on that error, report that diagnosis failed and name
  the failing check(s) so the user can investigate directly. Building
  retry/fallback machinery for a subagent-dispatch failure in an
  internal-tooling skill is the kind of compounding-layer machinery this
  plan's own foundation-fitness check weighs against; the plain-report
  fallback is the proportionate response.
- Changes to `code-writer.md`, `general-purpose`, or
  `root-cause-analysis/SKILL.md` themselves — this plan only changes how
  `ready-for-review` dispatches them, not their own bodies.
