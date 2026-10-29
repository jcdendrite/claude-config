# Automate handoff continuity across compaction

## Context

Give a session that compacts mid-task an automatic continuity artifact plus a
re-injection path, so unplanned context loss stops silently discarding work
state. Today `/handoff` is entirely model-authored and only fires on the
advisory context-cap nudge, an explicit engineer ask, or a session ending
(`claude-skills/skills/handoff/SKILL.md:30-37`); auto-compaction meets none of
those conditions, so a session that compacts keeps only the harness's own
summary — which this repo already judged lossy for review narrative, the
finding that motivated the review-narrative ledger. Why now: an external
consult proposed splitting the job across `PreCompact` (write the artifact),
`PostCompact` (archive `compact_summary`), and `SessionStart` with a `compact`
matcher (re-inject), and the prior Phase 0 spike
(`docs/precompact-hook-behavior.md`, against `claude` 2.1.233) never tested
either the `PreCompact` disk-write path or the `PostCompact` archival it
explicitly deferred. Intended outcome: a Phase 0 spike on the installed build
(2.1.267) that empirically settles the unverified mechanics, gating any
implementation behind its findings, per the engineer's spike-first decision.

## Approach

**Concluded design:** run a spike gated on one question — does
`SessionStart(compact)` fire on an *auto* compaction — and, if it does, add one
bounded block to the `SessionStart` hook that already composes multiple
payloads in-process. That block emits exactly when a `plan-it` plan file
exists at the path derived from the current branch. It states two facts and
directs nothing: the branch name, and that a plan file for that branch exists
at a named absolute path. `PreCompact` is retired unconditionally, not
conditionally.
`PostCompact` archival ships only if `compact_summary` turns out to be
unrecoverable from the transcript.

The spike's honest expected value is **bug detection in already-shipped code**,
not clearance for new mechanism. Its questions split three ways: one gates
Phase 1 (Q6), two audit code that ships to every stow consumer today (Q1, Q2),
and two feed Phase 2's archival design (Q3, Q4). Only Q6 can stop Phase 1.

**Two PRs, in sequence.** Phase 0 ships as a docs-only PR carrying the findings
section; Phase 1 ships as a second PR on its own branch. A single PR would
carry a findings doc, a possible security-relevant fix to a hook silently
losing its authorization-boundary payload if Q2 comes back negative, and a new
feature — three review surfaces in one diff, with the security item the most
likely to get the least attention.

### Assumption ledger

**Root problem:** a session that auto-compacts mid-task keeps only the harness's
own summary of its work state; the parts of that state that live on disk
(markers, review ledger, plan file) are recoverable but nothing surfaces them
unprompted, and the parts that are pure judgment (next concrete step, open
decisions) are not recoverable at all by any mechanism a hook can drive.

**Givens** (fixed beyond this plan's reach):

- `PreCompact` and `PostCompact` cannot inject context in the tested build — the
  harness's own JSON schema rejects `hookSpecificOutput` from either event.
  Vendor-imposed; no output shape from those events can reach the model.
  `[verified: docs/precompact-hook-behavior.md:37-51]`
- A `PreCompact` block reason is not model-visible; it reaches only the
  terminal-echo `local-command-stdout` surface. Vendor-imposed, so a "block
  until the model writes a handoff" forcing function cannot tell the model what
  to do. `[verified: docs/precompact-hook-behavior.md:25-27]`
- Blocking a compaction that was triggered to recover from a context-limit error
  already returned by the API surfaces that error and fails the request.
  Vendor-documented. `[verified: code.claude.com/docs/en/hooks.md, PreCompact
  section]`
- Producing a `handoff`-quality artifact is model judgment, not derivable state:
  §3 (next concrete step) and §6 (open questions / decisions deferred) have no
  disk source. `[verified: claude-skills/skills/handoff/SKILL.md section list;
  restated from .claude/plans/precompact-review-snapshot.md:46]`
- The live docs are not version-pinned to the installed build, and the events in
  question demonstrably moved between builds — `PostCompact` and
  `compact_summary` are documented now and were not at 2.1.233.
  Vendor-controlled; no archive exists to pin against.
  `[verified: code.claude.com/docs/en/hooks.md § PostCompact vs
  docs/precompact-hook-behavior.md:21]`
- The 10,000-character `additionalContext` threshold (over which the harness
  spills the value to a session-directory file and hands the model a path plus a
  short preview) is harness behavior. Vendor-imposed.
  `[verified: code.claude.com/docs/en/hooks.md § "Add context for Claude"]`

**Assumptions** (numbered; mechanisms anchor to these).

*Row numbering.* Rows 1–14, 18, 20, and 23 keep their original numbers and
content, so existing anchors resolve. Rows 16, 17, 19, 21, and 22 are unused:
each supported the changed-file *count* clause, which the payload does not
carry, and an unused row number is not reassigned. Specifically, 16
(`awk 'END{print NR}'` printing `0` on empty input), 17 (`git status
--porcelain`'s unmeasured cost), and 19 (`--no-optional-locks` convention)
describe a `git status` call the design does not make. Rows 21
(`git_timeout_shim` argv offsets) and 22 (`git_repo` ships dirty) supported
the required cases that depended on that call. Row 15 states only the half of
its original claim that the design still needs. Rows 6 and 7 each state a
single fact. Rows 24–28 have no counterpart among rows 1–23.

1. Spike first, then implement — Phase 0 empirically settles the mechanics on
   the installed build and later phases are gated behind its findings.
   `[engineer-verified]`
2. This work complements `/handoff` rather than replacing it: `/handoff` stays
   the deliberate session-boundary tool, and neither
   `nudge-handoff-near-context-cap.sh` nor its thresholds are touched.
   Subsumption is left open, not designed for. `[engineer-verified]`
3. Payload scope is delegated to this plan. `[engineer-verified]` — this row is
   the authority under which the changed-file count is dropped from the payload.
   The engineer delegated *what the block says*, not merely how it is formatted,
   so narrowing the payload to a single pointer is an exercise of that
   delegation rather than a departure from it.
4. The re-injection leg is already built: `session-marker-dashboard.sh` (matcher
   `startup|clear|compact|resume`) and `restore-authorization-boundary-on-compact.sh`
   (matcher `compact`) both emit `hookSpecificOutput.additionalContext` on the
   compact path, and `session-marker-dashboard.sh` already composes two
   independent blocks into one `additionalContext` string in-process.
   `[verified: claude/.claude/hooks/session-marker-dashboard.sh:113-128,
   claude/.claude/hooks/restore-authorization-boundary-on-compact.sh:36-48,
   claude/.claude/settings.json:111-126]`
5. `SessionStart` with a `compact` matcher is the vendor-recommended mechanism
   for this exact problem, and its `compact` matcher is documented to cover auto
   as well as manual compaction.
   `[verified: code.claude.com/docs/en/hooks-guide.md § "Re-inject context after
   compaction"; hooks.md SessionStart matcher table]`
6. Compaction does not delete anything from disk — it shrinks only what the
   model carries — so there is no artifact that must be written *before*
   compaction to avoid losing it. `[unverified]` — **not resolved by Phase 0.**
   This stays `[unverified]` and non-load-bearing — `PreCompact` retirement
   rests on row 24, which is verified and does not depend on this row.
7. `session_id` is preserved across a compaction, so a session-keyed read on the
   compact path resolves to the same file it was written under. `[unverified]` —
   Q1 resolves. Q1 is a sequencing trigger, not a feasibility gate: no
   work-state clause keys on `session_id`, and a rotated id is still a valid
   non-empty string, so the hook's early exit (row 25) never fires on it and
   the new block behaves identically either way. What a rotation breaks is the
   **already-shipped** marker lookup and ledger summary in the same hook, which
   key on `<repo-hash>.<session-id>` and silently return empty when the id
   changes — a bad answer redirects effort to fixing shipped code first, not a
   condition Phase 1's correctness depends on.
8. Two `SessionStart` hooks both emitting `additionalContext` on one `compact`
   event both reach context. `[unverified]` — Q2 resolves. Undocumented either
   way; the docs describe the field per-hook and never describe cross-hook
   composition.
9. Whether plain stdout on `SessionStart` is equivalent to `additionalContext`
   (system-reminder wrapping, size handling) is undocumented in both directions.
   `[verified: code.claude.com/docs/en/hooks.md — the docs state both paths
   reach context and state nothing about equivalence]` — resolved as a
   documented silence, which is why the design uses `additionalContext` only.
10. `PostCompact` completing before `SessionStart(compact)` fires is not
    guaranteed anywhere in the docs. `[verified: code.claude.com/docs/en/hooks.md
    — each event's timing is documented individually, their relative order is
    not]` — the design must therefore never have a `SessionStart` hook read a
    file a `PostCompact` hook writes in the same compaction.
11. `consume-durable-continuity-file-on-read.sh` moves a
    `<config-dir>/handoffs/*-handoff.md` file out of place whenever it is read
    directly, and is on by default. `[verified: docs/hooks.md:78]` — so an
    injected pointer to a handoff artifact would destroy that artifact for its
    intended resume the moment a compacted session peeked at it.
12. `_lib_emit_allow_with_context` hardcodes `"hookEventName":"PreToolUse"` and a
    `permissionDecision`, so it is not reusable for a `SessionStart` payload.
    `[verified: claude/.claude/hooks/_lib.sh:233-240]`
13. A new file inside `claude/.claude/hooks/` is not a new *immediate child* of
    `claude/.claude/`, so `require-stow-reminder.sh` does not fire and no
    re-stow is needed for a hook-body edit. `[verified: docs/hooks.md:41]`
14. This repo already has a settled convention for embedding a git-derived
    branch name in a harness-consumed `SessionStart` payload:
    `set-session-title-from-branch.sh` gates its branch component on a
    `^[A-Za-z0-9._/@+-]+$` allowlist matched under `LC_ALL=C` specifically,
    because outside the C locale bracket-range matching is collation-ordered and
    admits non-ASCII characters and C1 control bytes into `[A-Za-z]`.
    `[verified: docs/hooks.md:73]` — Phase 1 reuses this rather than inventing a
    second encoding discipline for the same data.
15. `_lib_capped_for` returns the wrapped command's own exit status when it runs
    to completion, and returns 124 with empty **or partial** output when a cap
    fires. `[verified: claude/.claude/hooks/_lib.sh:38-48]` — so a capped call's
    exit status is the only signal that distinguishes a complete result from a
    truncated one; output emptiness alone does not. This is what requires the
    branch call below to test exit status rather than emptiness, because a
    partial branch name would pass an emptiness test and then derive a wrong
    plan path.
18. `deny-private-project-refs.sh` cannot catch a truncated session-id reference
    in prose: `_LIB_LONG_HEX_IDENTIFIER_REGEX` floors at 32 contiguous hex
    characters or a full four-hyphen UUID, so an 8-character standalone hex
    token matches neither branch. `[verified: claude/.claude/hooks/_lib.sh:2287;
    at least two existing instances, e.g. docs/precompact-hook-behavior.md:43]`
    — the `plan-it` Step 7 rule has no mechanical backstop in this exact file.
20. `git-check-ref-format(1)` forbids two consecutive dots (`..`) anywhere in a
    ref name and forbids any path component beginning with `.`, so neither a
    `..` component nor a dot-leading component can appear in a real branch name.
    `[verified empirically: `git check-ref-format 'refs/heads/foo..bar'` and
    `'refs/heads/.hidden'` both exit 1]` — combined with the post-last-`/` slug
    construction, this is what closes the traversal question for the derived
    plan path. The branch allowlist (row 14) is *not* what closes it.
23. `jq` does not reject invalid UTF-8 input: it substitutes U+FFFD per
    offending byte and exits 0. `[verified empirically:
    `printf '\xff\xfe' > f && jq -Rs . f` prints `"??"` and exits 0]` — so the
    branch allowlist's UTF-8 ground is "keep mojibake out of injected context,"
    not "prevent a `_lib_jq` failure."
24. `PreCompact`'s only capability that `SessionStart(compact)` cannot replicate
    is reading the transcript *as it stood before* compaction, via the
    `transcript_path` field every hook event receives.
    `[verified: code.claude.com/docs/en/hooks.md § hook input common fields; and
    by inspection of this design, which reads `transcript_path` from no event]`
    — every other input `PreCompact` carries is disk state a
    `SessionStart(compact)` hook reads identically from the same disk. This
    design never reads a transcript, so `PreCompact` holds no lever it could
    use, and its retirement is **unconditional**: no Phase 0 result reopens it.
25. `session-marker-dashboard.sh` exits 0 before any block is built unless
    `.session_id` is both present and passes `_lib_valid_session_id_component`.
    `[verified: claude/.claude/hooks/session-marker-dashboard.sh:41-51]` — the
    new work-state block therefore sits behind a precondition none of its
    clauses needs, since it keys on `REPO_ROOT` and the branch rather than on
    the session id. Harmless, and recorded so a future reader does not hunt for
    a coupling that isn't there.
26. `require-plan-review.sh` arms on an **uncommitted or modified** plan file
    under `.claude/plans/`; a committed, unmodified plan file is treated as
    historical and does not arm the gate, and a Write/Edit whose own target is a
    plan file is exempt. `[verified: docs/hooks.md:151]` — so committing the plan
    once and never editing it again keeps the gate disarmed across both PRs,
    while editing it after commit would re-arm the gate and require a fresh
    `/plan-review` before any other file could be written.
27. `select-tests.py` maps `.claude/plans/**` to a domain rule with an **empty**
    target tuple, so a plan-file change is matched-and-selects-nothing rather
    than falling through to the full-suite unmatched-path fallback.
    `[verified: claude/.claude/scripts/select-tests.py:358 and the `DOMAIN_RULES`
    table at :351-360]` — a docs-plus-plan diff (Phase 0's PR) therefore still
    resolves to a domain-selected run. Separately, a `test_*.py` under
    `claude/.claude/hooks/tests/` matches
    `_is_py_source_under_claude_or_plugins` in addition to
    `_is_test_source_change`, because `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS`
    excludes only `claude/.claude/tests/`, not a nested `tests/` directory.
    `[verified: select-tests.py:308-312, :325-347, :477-478]` — PR 2's diff
    resolves through **six** `select-tests.py` rules (see Verification §3).
28. `check-branch-divergence.sh`'s header excludes `compact` from its matcher
    with a stated reason — "divergence is on-disk state, not session-scoped;
    re-emitting on /compact or /clear would be noise" — and its payload closes
    by demanding "Acknowledge this advisory in your first response to the user."
    `[verified: claude/.claude/hooks/check-branch-divergence.sh:11-14 and :123]`
    — the first fact is the apparent contradiction this plan must resolve, since
    it cites that hook as `SessionStart` git precedent. The second fact is a
    precedent this plan's payload correctly declines rather than lacks: that
    acknowledgment is paired to a real user decision — rebase now, or defer —
    and this payload asks for no decision at all, so adopting the form without
    the decision behind it would be cargo-culting it.

**Mechanisms:**

- **Phase 0 — live spike on the installed build; its one committed artifact is
  an appended, version-stamped section in `docs/precompact-hook-behavior.md`.**
  `anchors: row1`. Lighter alternatives, both rejected: (a) implement straight
  from the live docs — rejected because the prior spike refuted three
  doc-plausible claims outright, and the doc-verification dispatch caught the
  `WebFetch` tool fabricating a `PreCompact` input-field table that does not
  exist in the source, so doc-derived confidence about these two events
  specifically has a demonstrated failure rate; (b) treat
  `docs/precompact-hook-behavior.md` as still authoritative — rejected because
  it is stamped to 2.1.233 and at least one of its findings (that
  `compact_summary` is undocumented) is already falsified by the current docs,
  which is direct evidence the schemas move between builds.

- **Phase 0 ships as its own docs-only PR, merged before Phase 1 opens. The
  whole plan file, unsplit, is committed on Phase 0's branch and never edited
  again.** `anchors: row1, row26`. Two decisions here; the first is the PR
  split, the second is where the plan file lives across it.

  *Why split.* If Q2 comes back negative, a single PR carries three unlike things: a
  findings document reviewed for claim accuracy against live runs, a
  security-relevant fix to a hook that is silently losing its
  authorization-boundary payload, and a new user-facing feature. Those need
  different reviewers' attention and different scrutiny, and the security item
  is the one most likely to be skimmed when bundled behind a feature.
  Sequencing also matters on its own: Phase 1's gate is a Phase 0 finding, so a
  reviewer of Phase 1 who cannot see the merged findings has to take the gate on
  trust.

  *Where the plan file lives — settled here, not left open.* Commit the entire
  plan file to **Phase 0's** branch, before the spike runs; Phase 1's PR body
  references it at its merged path and neither re-commits nor edits it. Five
  reasons. (1) `plan-it` Step 7 requires the reviewed plan committed before
  implementation begins, and Phase 0 is the first implementation step — no other
  placement satisfies that literally. (2) `branch-management`'s prohibition is
  on a *plan-only* branch that can merge independently of the work it plans;
  Phase 0's PR carries a committed deliverable of its own, so it is not that
  shape. (3) Splitting the plan file across two PRs is the worst option
  available: the ledger is one cross-referenced artifact — Phase 1's mechanisms
  anchor rows 4, 8, 14, 15, 20, 23, 25 while Phase 0's anchor rows 1, 18, 26 —
  so a split either duplicates rows into two drifting copies or leaves anchors
  pointing at a file the reader does not have. (4) If Q6 comes back badly,
  Phase 1 never ships; the plan on main beside the findings that killed it then
  records a design correctly rejected on evidence, which is exactly the
  provenance Step 7 exists to make durable. A plan withheld for Phase 1 would
  simply be discarded in that world. (5) Row 26: editing the committed plan
  afterwards re-arms `require-plan-review.sh` and forces a fresh `/plan-review`
  before any other file can be written, so the gate outcome is recorded
  **once**, in the Phase 0 findings section, and the plan's Phase 1 gate
  paragraph points there rather than restating an answer. Accepted consequence,
  named: between the two merges, main carries a plan describing unshipped work.
  The findings section merged alongside it states each question's answer and the
  resulting go/no-go, so the reader is never left to guess.

- **Phase 1 — one additional block inside `session-marker-dashboard.sh`'s
  existing composed `additionalContext`, gated on `.source == "compact"`.**
  `anchors: root, row4, row8`. This is the over-powered-primitive check, worked
  against `docs/hooks.md` and `claude/.claude/settings.json` with the question
  "what mechanisms exist that do NOT require a new hook event?" — five
  candidates, in ascending weight:

  | Candidate | Verdict |
  | --- | --- |
  | Nothing new; rely on the existing ledger block | Fails: the ledger records review-narrative only (finding / disposition / rationale). It says nothing about which task or which plan — the orientation slice this plan exists for. |
  | Extend the review ledger's schema to carry work state | Fails: the ledger is append-only history written by `code-review` at disposition time, and `docs/design-decisions/round3-plan-architect-consult-gate.md:33-35` already records that its write compliance is not trusted by this repo's own later work. Reading disk state directly at injection time has no compliance dependency. |
  | **Extend `session-marker-dashboard.sh` (chosen)** | Cheapest sufficient primitive: no new `hook-class` header, no new `docs/hooks.md` bullet (Layer 0 of `test_hook_alignment.py` untriggered), no new `settings.json` registration, no new test file, and — decisively — it reuses the hook's own proven in-process composition of `MARKER_BLOCK` + `LEDGER_SUMMARY` rather than betting on the undocumented cross-hook composition of row 8. `REPO_ROOT` and the payload's `.cwd` resolution are already computed there. |
  | A third `SessionStart(compact)` hook | Fails: adds a third `additionalContext` emitter on one event, making the design depend on row 8's undocumented premise for no benefit the chosen row does not already deliver. |
  | A `PostCompact` hook injecting the payload | Fails twice: injection from that event is refuted for the tested build (given 1), and even a positive Q5 result would not justify moving off a vendor-recommended, in-repo-proven event onto a newly-registered one. |
  | A `PreCompact` hook writing the payload | Fails: row 24 — its only non-replicable lever is reading the pre-compaction transcript, which this design never does. |

- **`PreCompact` is retired unconditionally, on row 24 rather than on row 6.**
  `anchors: root, row24`. This reaffirms
  `.claude/plans/precompact-review-snapshot.md:79` with a stronger reason:
  retirement rests on row 24, which does not depend on row 6. Row 24
  removes the contingency entirely: the only thing `PreCompact` can do that
  `SessionStart(compact)` cannot is read the transcript before compaction, and
  no clause in this design reads a transcript from any event. Nothing Phase 0
  can return reopens it. Its one remaining candidate use — *blocking* — is
  rejected separately below.

- **`PreCompact` `decision: "block"` on write failure — rejected outright.**
  `anchors: root`. Three independent reasons, any one sufficient. (1) A block on
  a context-limit-recovery compaction surfaces the API error and fails the live
  request (given 3) — a session-breaking failure mode shipped to every stow
  consumer, triggered by a *write failure*, i.e. exactly the moment the
  mechanism is least healthy. (2) The block reason is not model-visible
  (given 2), so the model cannot learn why its request died or what to do
  instead. (3) The prior spike observed the harness re-attempting a blocked
  auto-compaction on essentially every subsequent prompt — 7 refusals over ~46
  minutes — so the failure is a per-prompt loop, not a single event
  (`docs/precompact-hook-behavior.md:29-33`). A defensive block guarding a write
  that has no data to rescue is the compounding-layers tell in its purest form.

- **Payload: one pointer, two facts, one shape.** `anchors: row3, row11`,
  given 6. The engineer delegated payload scope (row 3); the block emits exactly
  this, with two substitutions:

  > `` Work state at compaction: branch `<name>`. A plan-it plan file for this ``
  > `` branch exists at `<absolute path>`. ``

  **The block states a fact and directs nothing.** It carries no verb aimed at
  the model — no "read it," no "before acting." Two grounds, independent of each
  other. A hook emitting a bare fact leaves the next action to the model's own
  judgment, which is what makes the dogfood check in Verification §5.2
  informative in both directions rather than only in the failing one. And a
  precedence clause is a claim this hook cannot support: nothing available to a
  `SessionStart` hook establishes that the plan file outranks the harness's own
  compaction summary for the next step, so ranking one above the other would be
  an assertion the emitter has no basis for.

  The branch name and path are backtick-delimited, matching
  `check-branch-divergence.sh`'s existing convention for the same data class in
  the same delivery channel (that hook's `` "Branch \`$CURRENT_BRANCH\` is
  $BEHIND commits behind..." ``) — a visual data/instruction delimiter, not a
  security boundary on its own, but free and already this repo's convention for
  a git-derived value reaching model context. Row 14's allowlist reuse is
  precedent for the *encoding* discipline only; it is not a precedent for
  *delimiting*, since `set-session-title-from-branch.sh`'s consumer
  (`hookSpecificOutput.sessionTitle`, a terminal tab title) never reaches model
  context at all, so nothing about that reuse addresses this template's own
  delivery channel.

  The path is **absolute**, not repo-relative: the consumer is an agent whose
  own instructions require absolute paths for `Read`, and a linked-worktree
  session's cwd is not guaranteed to match the repo root (the same reason the
  hook already resolves `REPO_ROOT` from the payload's `.cwd` rather than from
  process cwd).

  **Named residual: the allowlist does not close semantic prompt injection.**
  `^[A-Za-z0-9._/@+-]+$` constrains encoding, not content. The permitted
  character set can spell a readable imperative using `-`/`.` as word
  separators (e.g. a branch literally named to read as an instruction to
  force-push without confirmation), and `git-check-ref-format(1)` accepts such
  names. A branch name is attacker-influenceable in any multi-committer repo: a
  crafted branch pushed by another committer and later checked out by the
  victim (or their agent) delivers that block into model context,
  unattributed and indistinguishable in form from genuine hook-authored
  guidance, immediately after a compaction. This is not new exposure:
  `check-branch-divergence.sh` already embeds the same attacker-controlled
  value into `additionalContext` with no character filter at all, so this
  block is strictly narrower than that already-shipped sibling. The
  allowlist's three encoding-safety grounds do not close this class. The
  design accepts the residual because the payload is advisory text with no
  tool-invocation capability of its own, and closing prompt injection at the
  data layer is out of reach for a bash hook.

  **Named residual: plan-file provenance is not checked before the pointer
  fires.** The trigger (below) is bare file existence at the derived path;
  nothing checks whether that file is tracked, clean, or has ever passed
  `/plan-review`. No cheaper mechanical check exists: `_lib.sh`'s active-plan-
  file definition is `untracked` UNION `tracked-and-modified-vs-HEAD`, so a
  tracked-and-clean file is covered by no plan-review-marker hash. The two
  candidate signals are mutually exclusive for a single file, so their union
  never fires in the case this check exists to guard. "Tracked-and-clean"
  alone does not stand in for "reviewed": nothing in this repo's tooling stops
  a plain `git commit` from landing a plan file in that state with zero
  `/plan-review` invocation ever having occurred. The residual is accepted on
  the same grounds as the branch-name residual above: the payload is advisory
  text with no tool-invocation capability of its own. Its surface is
  materially larger: an unbounded free-text plan file versus a branch name
  capped at ~255 bytes per component and constrained to
  `^[A-Za-z0-9._/@+-]+$`.

  *Why a pointer rather than the artifact.* (1) A `handoff` artifact spanning
  §1–§7 routinely exceeds 10,000 characters, at which point the harness spills
  it to a file and hands the model a path plus a preview anyway (given 6) — so
  "inject verbatim" degrades into "inject a pointer" *non-deterministically, at
  the harness's discretion*. Choosing a pointer makes that deterministic and
  reviewable. (2) Injected context is re-read every turn for the rest of the
  session, and immediately post-compaction is when that budget is scarcest.
  (3) A pointer self-refreshes; an injected copy is a snapshot that goes stale.
  (4) The payload deliberately does **not** point at
  `<config-dir>/handoffs/*-handoff.md` — row 11: reading one moves it out via
  `resume-context.sh --consume-only`, so a compacted session glancing at a
  handoff written for a different purpose would consume it and destroy it for
  its intended resume. The committed plan file has no such consume hook behind
  it.

  *Why the changed-file count is not in the payload.* A bare changed-file count
  isn't in the payload: the model would re-derive which files via `git status`
  anyway, and a count with no filenames can't inform a decision about any
  specific file (that's `restore-authorization-boundary-on-compact.sh`'s job).
  The pointer is categorically different: post-compaction the model does not
  know a plan file exists, does not know the `.claude/plans/<slug>.md`
  convention, and may not know it is mid-plan at all, so the pointer supplies
  knowledge that is genuinely unavailable rather than merely unfetched.

  *Accepted coverage limit, named because it is the sharpest cost of that
  deletion.* On a branch with no `plan-it` plan file — an exploratory session, a
  hotfix, a branch off this repo's naming convention — the block never fires, so
  a compaction there produces no orientation signal at all. That is accepted on
  the pointer's own logic: the block's value is telling the model about an
  artifact it would not know to look for, and where no such artifact exists
  there is nothing to tell. A future second trigger is possible and deliberately
  not designed here (Out of scope).

- **Trigger condition: one binary condition — a file exists at
  `$REPO_ROOT/.claude/plans/<slug>.md`. The branch name is a qualifier, never a
  trigger.** `anchors: root, row3, row20`. This is the plan's single statement of
  the rule.

  The branch is not a trigger — a resolvable branch alone emits nothing — but an
  *unresolvable* branch suppresses the block entirely, because the slug is the
  only input to the derived path. So the block has exactly two states: absent,
  or the full two-sentence payload above with both substitutions filled. There is no
  clause-omission case, no empty-clause case, and no
  clause-presence-versus-block-presence distinction to hold; those rules existed
  only because the count could resolve independently of the branch. A detached
  HEAD, a branch failing the allowlist, and an unresolvable `REPO_ROOT` all
  produce the same outcome: no block.

  `<slug>` is the branch name after its **last** `/`, per `plan-it` Step 1's
  documented convention. The full branch name is what the block displays;
  only the slug builds the path. **Traversal is closed by construction, twice
  over:** the slug is by definition the substring after the last `/`, so it can
  never itself contain a `/`; and row 20 independently forbids `..` anywhere in
  a ref name and forbids any dot-leading component, so neither can arise from a
  real branch. Accepted residual: two branches sharing a suffix after their
  respective last `/` derive the same slug — that is `plan-it` Step 1's own
  convention, not something this plan introduces, and the block names the
  path it resolved so a reader can tell what was read.

- **The 2,000-character cap bounds the work-state block alone, as a whole-block
  drop rather than a partial cut.** `anchors: root, row3, row14, row23`. Two
  consequences that delete a class of failure rather than testing around it.
  First, the pre-existing marker and ledger blocks are uncapped today and stay
  uncapped — an additive change is the wrong vehicle for introducing this repo's
  first length bound on two already-shipped behaviors. Second, because an
  over-cap block is dropped whole and never trimmed, **no truncation logic
  exists anywhere in this diff**: there is no cut to land inside a plan-file
  path, and no cut to split a multi-byte codepoint into invalid UTF-8 that `jq`
  would silently rewrite to U+FFFD (row 23) — a corruption that reaches the
  model looking like legitimate content rather than surfacing as an error. The
  character-versus-byte question is moot by construction: row 14's allowlist
  admits only ASCII into the block's variable content, so `${#VAR}` counts the
  same either way. Why 2,000: the block is roughly 120–200 characters in normal
  use, so the cap is a backstop against a pathological-but-legal nested branch
  name (ref components are filesystem-bounded at roughly 255 bytes each and may
  nest arbitrarily deep, and the full name is what the block displays), sized
  to stay far under the harness's 10,000-character spill threshold (given 6)
  rather than to constrain ordinary output.

- **`REPO_ROOT`/`PAYLOAD_CWD` resolution is gated on `.source == "compact"` OR
  the ledger sentinel being absent — not hoisted unconditionally.** `anchors:
  root, row4`. Today these are computed only inside the
  `.review-narrative-ledger-disabled` guard, so a consumer with that sentinel
  set runs **zero** git subprocesses on every `SessionStart` fire. An
  unconditional hoist would spend a new `_lib_capped git rev-parse` on every
  `startup`, `clear`, and `resume` fire for that population, on paths where the
  `compact`-gated block cannot use the result. The gate is exactly the union of
  what the two consumers need, so the only new cost is on `compact` fires, which
  is the only path the block exists for. Verification §6 states the resulting
  per-fire accounting.

- **No new opt-out sentinel.** `anchors: root, row28`. The block reports the
  same class of repo state the hook's marker block already reports ungated,
  costs roughly 150 bytes on the compact path only, and
  `check-branch-divergence.sh` establishes ungated bounded-git-at-`SessionStart`
  as precedent. A third independently-named switch inside one hook — after the
  always-on marker block and the sentinel-gated ledger block — is the
  compounding-layers tell, and every sentinel carries real documentation cost
  (`docs/hooks.md`'s inventory plus `install.sh`'s `report_sentinel_inventory`).
  Accepted consequence, named: the rollback path for a bad Phase 1 release is a
  code revert plus `git pull`, with no runtime switch a stow consumer can flip
  while waiting — asymmetric with the ledger block beside it, which has one.
  That asymmetry is the price of not adding the third switch.

  **The accepted consequence's actual scope is the shared composition code, not
  only the new block's advisory text.** Delivering the new block requires
  rewriting `:113-123`'s two-way early-exit and hardcoded cascade into the
  three-way join loop specified above, which the two pre-existing blocks
  (active review-skill markers, review-narrative ledger summary) now flow
  through as well. A defect in that shared rewrite — an off-by-one, a
  separator regression — does not fail confined to the new block; it can
  degrade marker or ledger emission on **every** `SessionStart` fire, not only
  `compact`, for every stow consumer, with no in-field signal until someone
  notices and `git pull`s a fix. Required cases 3 ("byte-for-byte" regression
  on non-compact fires), 4 (all three blocks composed, exact order and
  separator), and 6 (exit 0 on every path) are the pre-merge backstop for
  exactly this risk; there is no runtime backstop after merge, which is what
  "no new sentinel" actually costs. This is also the first of the hook's three
  blocks whose *content* is attacker-influenceable (a pushed branch name, see
  the Approach's payload mechanism) rather than purely local, non-adversarial
  state — a differently-risked sibling than the two it is compared against
  above, though the block's advisory-only, no-tool-invocation shape keeps that
  differential from changing the sentinel calculus.

- **Resolving the `check-branch-divergence.sh` precedent contradiction.**
  `anchors: root, row28`. This plan cites that hook twice — for its
  branch-resolution command and as precedent for ungated bounded git at
  `SessionStart` — while row 28 records that its own header excludes `compact`
  for its data class, reasoning that divergence is on-disk state whose
  re-emission on `/compact` would be noise. Both positions are correct, because
  the two hooks fire at different points relative to a loss event. At `startup`
  nothing was lost, so re-emitting on-disk state the session never held is
  repetition; at `compact` the earlier injection *was* lost, so re-emitting it is
  restoration rather than noise. The distinction is not the data's provenance
  (both hooks read disk) but whether the model previously held the fact and no
  longer does. That resolves in this plan's favor and leaves that hook's own
  reasoning intact for its own matcher. It also implies — and this plan does not
  fix it — that the same reasoning makes that hook's `compact` exclusion a
  defect: a mid-PR session that auto-compacts silently loses its divergence
  advisory, which is a decision-bearing fact about a branch that may be about to
  be pushed, and by this plan's own logic it is a better candidate for the
  compact path than the changed-file count ever was. Named as a follow-up in Out
  of scope rather than bundled here.

- **Q2's answer changes the *security disposition* of
  `restore-authorization-boundary-on-compact.sh`, even though it changes nothing
  about Phase 1's implementation.** `anchors: root, row8`. Keeping the two hooks
  decoupled is right for implementation — Phase 1 composes internally, so its
  correctness does not depend on Q2 either way. That hook is the only
  control stopping the model from reading a compaction summary's "Optional Next
  Step" as engineer sanction for an irreversible action; it exits 0 whether or
  not its payload lands, so a failure has no signal; and the risk is sharpest
  under this repo's own `autonomous-shipping-required` posture. Pre-committed
  here: **if Q2 shows only one payload reaches context**, the follow-up is filed
  as a security-relevant defect and referenced from Phase 0's PR description
  rather than dropped into routine backlog, and a minimal same-PR fix is
  explicitly evaluated at write-up time once the actual failure mode is known (a
  registration-order effect and a schema conflict warrant different responses) —
  rather than foreclosed in advance. Sharpening the priority: a negative Q2
  plausibly leaves the work-state pointer this plan adds as the *only* advisory
  message the model receives post-compaction — a "go read the plan" nudge with
  no "don't act destructively without asking" alongside it.

  **Named premise, not independently verified here:** calling that hook "the
  only control" assumes `CLAUDE.md`'s own standing confirm-before-destructive-
  action instruction (Engineering Judgment section) does not itself survive
  compaction as system-level content the model still holds. Whether project
  instructions loaded at session start persist unchanged through a compaction
  (which summarizes conversational turn history) or degrade with it is not
  established anywhere in this plan or in `docs/precompact-hook-behavior.md`'s
  existing findings. If it survives intact, "only control" overstates the
  severity above; if it does not, the framing is exactly right and the
  escalation matters more, not less. Phase 0's deliverable includes checking
  this alongside Q6's forced-compaction run — no new numbered question, since
  it rides the same live run at no extra cost: after the compaction, grep the
  transcript for whether the confirm-before-destructive-action instruction
  text, or an equivalent effect, is still present in what the model was given.
  Record the result in the findings section as a premise check, not a gate —
  it recalibrates Q2's severity language but does not itself gate Phase 1.

- **`docs/precompact-hook-behavior.md`: append, never rewrite.** `anchors: row1`,
  given 5. Axis 3 governs: that file is a dated empirical record stamped to a
  specific build, so its 2.1.233 findings are read-only — line 21's "one the
  docs never mention" was *true of the docs at that time* and is a record, not a
  description of current behavior. Phase 0 appends a new `## Findings —
  <version>` section and adds exactly one navigational line near the top stating
  that findings are stamped per build and pointing at the newest section. The
  file keeps its name despite the widened scope: renaming would break the
  inbound link at `docs/hooks.md:135` for no reader benefit.

- **A drafted-section redaction self-check, scoped to the new section only.**
  `anchors: row1, row18`. The `plan-it` Step 7 rule against quoting a raw
  `session_id` into committable prose has already failed twice in this exact
  file, in the truncated 8-character form the redaction hook's 32-character
  floor cannot reach (row 18) — so "the author remembers the rule" is a
  single-layer control with a demonstrated failure rate in the identical
  context. The verbatim scratch-hook embedding this deliverable requires
  (Phase 0's deliverable item 2, below) is content authored for local
  debugging rather than for public-repo scrutiny, so the same self-check must
  also catch identifying content that isn't hex-shaped. Phase 0's deliverable
  therefore carries an explicit pre-commit pass over the drafted section
  against this repo's full redaction category list
  (`docs/private-project-redaction.md`'s three scans and six structural
  detectors) — not only standalone 6–10-character hex tokens and narrative
  session/PID references — in addition to re-reading the rule. Two
  boundaries: the existing instances already in
  `docs/precompact-hook-behavior.md` (e.g. `:43`, and several more in the
  subagent-context table) are dated preserved records under Axis 3 and are
  **not** edited, and widening `_LIB_LONG_HEX_IDENTIFIER_REGEX`'s floor is not
  proposed here.

### Phase 0 — the spike

Runs in a throwaway directory outside this repo with its own minimal
`.claude/settings.json`, per the prior spike's own method
(`.claude/plans/precompact-review-snapshot.md:125`). Four hard constraints on
method:

- **Verify the scratch directory is genuinely outside this repo before writing
  anything into it.** `mkdir -p <scratch-dir>` first, then confirm `git -C
  <scratch-dir> rev-parse --show-toplevel` does not resolve to this repo's
  toplevel (or does not resolve at all), and only then create the spike's
  `.claude/settings.json` there — a mechanical check run once per spike
  session, not reliance on the operator getting the `cd` right from memory.
  Run the check strictly after `mkdir -p`: against a not-yet-created path, the
  check's `fatal: ...`/exit-128/empty-stdout output is indistinguishable from
  a genuinely-external match, so an early-ordered check always vacuously
  passes regardless of where the path actually points.
- **Never edit a shipped hook to add a spike marker.** `claude/` is stowed, so
  an edit goes live for the engineer immediately and stages into a public repo.
  Q2 uses two stand-in hooks in the scratch config that emit distinct markers —
  behaviorally equivalent for a composition question.
- **Verify against the session's own transcript by grep, never by asking the
  model whether it saw the marker.** The prior spike's assumption 3 was only
  correctly refuted because it grepped the transcript; a self-report would have
  returned a false confirm, since the marker *was* visible via the
  terminal-echo channel (`docs/precompact-hook-behavior.md:43-51`).
- **Q2 runs in both hook-registration orders, not repeated runs of one order.**
  Whether cross-hook composition depends on `settings.json`'s hook-array order
  is undocumented (row 8), and three runs of A-then-B agree with each other
  while masking an order-dependent effect only B-then-A surfaces. At least one
  run each way. The other questions concern fixed harness behavior the prior
  spike observed as deterministic across repeats, so 2–3 independent runs
  remains the standard there.

Findings record run labels (run A/B/C), never a `session_id` — `plan-it` Step 7
bars quoting a raw session id into prose that reaches a plan file or commit, and
this doc ships in the same public PR.

**The spike is three efforts plus a footnote, not one undifferentiated set of
questions.** Grouping them by what their answers actually do is what makes the
Phase 1 gate a single question instead of three:

*Effort 1 — feasibility. Q6 alone gates Phase 1.*

| # | Question | What it measures | What the result kills or reshapes |
| --- | --- | --- | --- |
| **Q6** | Does `SessionStart(compact)` fire on an **auto** compaction, not only `/compact`? | `claude --autocompact 100000`, force an auto compaction, confirm a `SessionStart` payload with `.source == "compact"` arrives and its `additionalContext` reaches the transcript. | Row 5, and the plan's entire premise. Documented as covering both, but auto-compaction is the *only* case this plan exists for, and this is the highest-consequence question in the set. Fires only on manual ⇒ the re-injection leg is unavailable for the case that motivates the work, Phase 1 is dead, and `restore-authorization-boundary-on-compact.sh`'s advertised auto-compaction coverage is false today. |

*Effort 2 — audit of already-shipped code. High value on its own; sequencing,
not feasibility.* Neither answer changes Phase 1's design, and neither can stop
it. A bad answer redirects effort: the resulting work is a fix to shipped code,
which deserves its own scope and probably precedes Phase 1.

| # | Question | What it measures | What the result kills or reshapes |
| --- | --- | --- | --- |
| **Q1** | Does `session_id` survive a compaction? | Log `.session_id` from `PreCompact`, `PostCompact`, and `SessionStart(compact)` in one session; compare all three against the pre-compaction id. | Row 7. Rotation ⇒ `session-marker-dashboard.sh`'s marker lookups and ledger summary are **already inert on the compact path today** — a shipped bug that reaches every stow consumer. Phase 1's own block is unaffected either way: nothing in it keys on the session id, and a rotated id is still valid and non-empty so the hook's early exit (row 25) does not fire. |
| **Q2** | Do two `SessionStart` hooks' `additionalContext` payloads both reach context on one `compact` event? | Two stand-in scratch hooks emitting distinct markers on `SessionStart` matcher `compact`; grep the transcript for both. Run once in each registration order. | Row 8. Both present ⇒ the premise holds and a third emitter would have been safe (we still don't add one). Only one present ⇒ never add a third emitter, **and** one of the two shipped `compact` emitters is losing its payload today — which triggers the security escalation pre-committed in the Approach, not a routine backlog filing. |

*Effort 3 — inputs to Phase 2's archival design.* Neither gates Phase 1; both
shape whether and how Phase 2 is built.

| # | Question | What it measures | What the result kills or reshapes |
| --- | --- | --- | --- |
| **Q3** | Does the transcript retain pre-compaction records after compaction? | Record transcript byte count at `PreCompact` and again at `PostCompact` and the next tool call; grep afterwards for a distinctive pre-compaction user string. | Phase 2's evidence base, alongside Q4 — **not** row 6, and **not** a `PreCompact` reopener. It measures transcript retention; row 6 claims disk state is not deleted; those are different artifacts, and Phase 1 injects nothing transcript-derived. Records survive ⇒ an archival design can read the transcript after the fact and needs no capture hook. Truncated or rotated ⇒ only a hook holding `transcript_path` at the right moment can capture pre-compaction content, which is what a Phase 2 design would have to reckon with. `PreCompact` stays retired regardless, on row 24. |
| **Q4** | Is `compact_summary` recoverable anywhere other than `PostCompact`'s stdin? | Capture `compact_summary` to a file from a `PostCompact` hook, then grep the transcript for its distinctive opening text. | Recoverable ⇒ `PostCompact` archival is pure convenience and stays deferred, now with a reason rather than a hunch (`.claude/plans/precompact-review-snapshot.md:152`). Not recoverable ⇒ archival is the only capture path and earns a Phase 2. Note row 10: any archival design must not have a `SessionStart` hook read what `PostCompact` writes in the same compaction. |

*Footnote — currency check, cheap to run alongside the above.*

| # | Question | What it measures | What the result kills or reshapes |
| --- | --- | --- | --- |
| **Q5** | Is the `additionalContext` refutation for `PreCompact`/`PostCompact` still true on the installed build? | Re-run the exact probe from `docs/precompact-hook-behavior.md` §"Assumption 3"; grep the transcript. | Given 1's currency. Still refuted ⇒ record the re-confirmation with the new version stamp. Now working ⇒ record it, but it does **not** move Phase 1: staying on a vendor-recommended, in-repo-proven event beats migrating to a newly-registered one. It never revives `PreCompact` blocking, which dies on given 3 regardless. |

**Q7 is dropped.** Q7 (whether `additionalContext` over 10,000 characters
degrades) does not apply: the block is deterministically capped at 2,000
characters as a whole-block drop, an order of magnitude below the spill
threshold and with no truncation path, so no answer to Q7 could validate or
invalidate anything the design does. Question numbers are not
renumbered — Q1–Q6 keep their identifiers so the findings doc and the
review-round discussion still line up.

**Phase 0's deliverable:** the appended, version-stamped section in
`docs/precompact-hook-behavior.md`, carrying four things.

1. Each of Q1–Q6 answered as confirmed, refuted, or
   ambiguous-with-what-would-resolve-it, grouped by the three efforts above so a
   reader can tell the gate from the audit, and stating Q6's answer and the
   resulting Phase 1 go/no-go explicitly. This is the single home for the gate
   outcome; the plan file is not edited to restate it (row 26).
2. **A reproducible artifact, not only the outer CLI invocation.** "The exact
   commands run" is insufficient on its own: the existing 2.1.233 section names
   its `SPIKE_MODE` env vars and describes its scratch hooks in prose without
   reproducing either script body or the `settings.json` matcher lines that
   wired them up, and this plan's own constraint makes the scratch directory
   disposable. A future re-runner reconstructing Q2's two stand-in hooks or Q5's
   probe from prose alone risks differing in exactly the way that changes the
   harness's answer. The section therefore embeds each scratch hook's script
   body verbatim — or, where the body is trivial, the exact `hookSpecificOutput`
   JSON it emitted — alongside its `settings.json` matcher line, inline.
3. For Q2 specifically, both registration orders' results recorded separately
   rather than collapsed into one verdict.
4. The pre-commit redaction self-check pass over the drafted section
   (Approach, final mechanism) completed before the section is staged.

### Phase 1 — the work-state block

**Gate (the plan's single statement of it): Phase 1 runs only when Q6 confirms**
that `SessionStart(compact)` fires on an auto compaction. Nothing else gates it.
Q1 and Q2 audit already-shipped code and may reorder the work — neither changes
this block's design or correctness. Q3 and Q4 feed Phase 2. Q5 is a currency
footnote. A bad Q6 means **stop and re-plan**: the mechanism this plan depends
on does not exist for the case it exists for.

**Q1 and Q2 do not carry equal sequencing weight, despite both being
"fix shipped code first, in its own scope."** A bad Q1 is a functional bug
(stale marker/ledger lookups on session-id rotation) — fixing it first is a
scheduling preference, and Phase 1 opening before that fix lands changes
nothing about Phase 1's own correctness. A bad Q2 is different in kind: per
the Approach's escalation mechanism, it means one of the two shipped `compact`
emitters is silently losing its payload, and the specific emitter at risk
(`restore-authorization-boundary-on-compact.sh`) is named there as the only
control stopping the model from reading a compaction summary's "Optional Next
Step" as engineer sanction for an irreversible action. Opening Phase 1's
branch while that finding is filed-but-unfixed adds this plan's own
work-state pointer as the *only* advisory message the model receives
post-compaction — a "go read the plan" nudge with no "don't act destructively
without asking" alongside it, which is the exact compounding risk the
Approach names. **Stated precondition:** if Q2 comes back negative, Phase 1's
branch does not open until either the fix lands or the engineer explicitly
accepts the interim exposure — this is a human call, not a mechanical one,
precisely because nothing in this repo's hook or gate pipeline checks a
prior PR's findings section before permitting a new branch's edits. That
absence is general to this repo, not introduced here: Phase 1's PR reviewer
is the actual enforcement point, and must confirm the merged findings section
states a confirmed Q6 (and, if Q2 was negative, a resolved or accepted
disposition) before approving — no hook in this pipeline verifies either on
its own.

Phase 1 opens a **new branch** off the updated default tip after Phase 0's PR
merges — suggested slug `compaction-work-state-block`, picked per
`branch-management`. Its PR body references the merged plan at
`.claude/plans/automate-handoff-via-compaction-hooks.md` and the merged findings
section; it commits no plan file of its own and edits none (row 26, so the
plan-review gate stays disarmed).

One `code-writer` dispatch; the file set below does not partition into
independently specifiable halves, so it is not split.

Implementation notes that are not obvious from the file:

- **Read `.source` and gate the new block on `"compact"`.** The hook does not
  read `.source` at all today, relying on its matcher. Adding the check
  satisfies the repo's hook defense-in-depth rule and keeps the block off
  `startup`/`clear`/`resume`, where no context was lost.
- **Resolve `REPO_ROOT`/`PAYLOAD_CWD` under a two-way gate, not unconditionally:**
  compute them when `.source == "compact"` **or** when
  `$CONFIG_DIR/.review-narrative-ledger-disabled` is absent. Then read the
  ledger only when the sentinel is absent **and** `REPO_ROOT` is non-empty
  (today's `:98` guard, unchanged), and build the work-state block only when
  `.source == "compact"` **and** `REPO_ROOT` is non-empty. This preserves
  today's zero-git-subprocesses invariant for a ledger-disabled consumer on
  every non-compact fire; a literal hoist out of the sentinel guard would not.
- **Branch: `if BRANCH=$(_lib_capped git -C "$REPO_ROOT" symbolic-ref -q --short HEAD); then`,
  exit status checked, not output emptiness.** This is
  `check-branch-divergence.sh:61-62`'s command, chosen specifically because it
  fails empty and non-zero on detached HEAD — the correct "no block" outcome. Do
  **not** use `git rev-parse --abbrev-ref HEAD`, which returns the literal
  string `HEAD` there and would render as `branch HEAD`, actively misleading
  rather than absent. Test exit status rather than emptiness because a fired cap
  returns 124 with empty *or partial* output (row 15), and a partial branch name
  would pass an emptiness test and then derive a wrong plan path. Keep the
  `_lib_capped` wrapper for consistency with the adjacent `rev-parse` at `:96` —
  but add **no** `_lib_capped`-is-not-a-bound caveat comment at this call site:
  `symbolic-ref` reads one ref file, so its cost does not scale with anything.
- **Validate the branch name against `^[A-Za-z0-9._/@+-]+$` under `LC_ALL=C`
  before it enters either the block text or the plan path** (row 14, POSIX ERE).
  A branch failing the allowlist produces no block. Match under `LC_ALL=C`
  explicitly: outside the C locale, bracket-range matching is collation-ordered
  and admits non-ASCII characters and C1 control bytes into `[A-Za-z]`.

  **What the allowlist is and is not for.** Three grounds: it keeps C1 control
  bytes and other control characters out of a string the harness injects into
  model context; it keeps a non-UTF-8 branch name from reaching the model as
  mojibake, since `jq` does not reject invalid UTF-8 but silently substitutes
  U+FFFD per byte and exits 0 (row 23), so without the allowlist the failure
  would be a quietly corrupted branch name rather than a loud error; and it
  makes the cap's character-versus-byte question moot by admitting only ASCII
  into the block's variable content. It is **not** what closes path traversal on
  the derived plan path — row 20 and the post-last-`/` slug construction do that
  independently, so the two concerns are separable and neither props up the
  other. It is also **not** a defense against semantic prompt injection via a
  crafted branch name — see the Approach's payload mechanism for that residual
  and why it is accepted rather than closed.

  **Named tradeoff, scoped to the population that actually runs this.**
  `claude/` stows to every consumer, so the hook runs against each consumer's
  own repositories and their own branch-naming habits — not against
  `claude-config`'s history. A consumer whose branch names are legitimately
  non-ASCII (a ticket title in a non-Latin script, for instance) gets **no block
  at all** on any compaction, even when their plan file exists. That
  frequency is unbounded by anything this repo controls, and it is accepted
  anyway: the degradation is silent and safe rather than wrong, and the
  alternative — a second, UTF-8-validity-only discipline that permits non-ASCII
  letters while excluding control characters — would need UTF-8 validation in
  bash that no helper in `_lib.sh` provides, would fork this data class away
  from `set-session-title-from-branch.sh`'s settled convention (row 14), and
  would reopen the byte-versus-character question the cap argument closes by
  construction. Reusing one discipline for one data class beats three new moving
  parts.
- **Plan-file resolution is deterministic, not a glob:**
  `$REPO_ROOT/.claude/plans/<slug>.md`, where `<slug>` is the branch name after
  its **last** `/`. Existence of that file is the block's only trigger; absence
  means no block. Emit the absolute path, per the payload mechanism above.
- **Widen both the early-exit and the join from two-way to three-way, with a
  join idiom that cannot trail a separator.** This is a real code-structure
  change, not one added line: today `:113` early-exits on
  `[ -z "$MARKER_BLOCK" ] && [ -z "$LEDGER_SUMMARY" ]`, and `:117-123` is a
  hardcoded both/marker-only/ledger-only cascade. Replace both with a
  fixed-element loop over the three block variables, appending a separator only
  when something is already accumulated, and exit 0 when nothing accumulated:

  ```bash
  NL=$'\n'
  ADDITIONAL_CONTEXT=""
  for BLOCK in "$MARKER_BLOCK" "$LEDGER_SUMMARY" "$WORK_STATE_BLOCK"; do
    [ -n "$BLOCK" ] || continue
    ADDITIONAL_CONTEXT="${ADDITIONAL_CONTEXT:+$ADDITIONAL_CONTEXT$NL}$BLOCK"
  done
  [ -n "$ADDITIONAL_CONTEXT" ] || exit 0
  ```

  Kept independent of the payload reshape because it is correct hygiene on its
  own terms. Three reasons for this exact form. A fixed-element loop needs no
  bash array, which is the lighter primitive for three known variables and keeps
  the documented order (marker, ledger, work-state) literally visible in the
  source rather than encoded in append order. The separator is held in `NL` so
  it enters the `${VAR:+…}` alternate word as a plain parameter expansion, the
  form bash unambiguously supports there. And the `:+` guard means a single-block
  payload is byte-identical to today's `ADDITIONAL_CONTEXT="$MARKER_BLOCK"`
  assignment, where the naive `printf '%s\n' "${BLOCKS[@]}"` would append a
  trailing newline and regress the byte-for-byte case below. Keep the file's
  existing `[ … ]` test style rather than switching to `[[ … ]]` for these
  lines, so the diff stays additive in style as well as scope. **Failure mode if
  the early-exit is not widened:** a compaction on a branch with a plan file but
  no active markers and no ledger content — the common case, since most
  compactions do not coincide with an active review-skill gate — silently emits
  nothing, defeating the block's purpose on the most common path.
- **Apply the 2,000-character cap to the assembled work-state block only, as a
  whole-block drop.** Never trim it, and never cap the composed
  `additionalContext`. The pre-existing marker and ledger blocks remain uncapped
  exactly as today.
- **Do not reach for `_lib_emit_allow_with_context`** (row 12): it hardcodes
  `PreToolUse` and a `permissionDecision`. Extend the hook's existing
  `_lib_jq -n --arg ctx …` envelope instead.
- **Update the hook's own header comment**, one sentence per fact, per the
  repo's comment-length convention. Five facts: the work-state block's purpose;
  that a plan file at the branch-derived path is its only trigger, with a
  resolved branch name a qualifier rather than a trigger; that the
  `startup|clear|compact|resume` matcher is narrowed to `compact` internally for
  this block (the matcher-versus-internal-filter pairing the hook-review
  standard requires be documented in the header); that the block carries no
  sentinel of its own; and that the block sits behind the hook's existing
  `session_id` presence-and-validity early exit even though none of its clauses
  keys on the session id (row 25). The existing "Exit 0 always" fail-posture
  line at `:33` already covers the new block and does not need restating.

### Phase 2 — `PostCompact` archival (contingent on Q4, informed by Q3)

Ships only if Q4 shows `compact_summary` exists nowhere but `PostCompact`'s
stdin. Q3's answer shapes the design rather than gating it: transcript retention
determines whether an archival hook can read after the fact or must capture at
the event. Otherwise Phase 2 stays deferred, matching
`.claude/plans/precompact-review-snapshot.md:152`'s existing deferral with an
evidence-backed reason substituted for the hunch. If it does ship it is a
genuinely new hook, needing all of:

- a `hook-class: informational` header stating purpose, scope, and fail posture
  — the posture is fail-open by construction, since `PostCompact` has no
  decision control, and the header states that rather than leaving a reader to
  re-derive it from the event's schema;
- a `docs/hooks.md` bullet (Layer 0 of `test_hook_alignment.py` enforces this);
- a `settings.json` registration whose `command` is the stable stowed form
  `~/.claude/hooks/<name>.sh`, matching every existing entry — a user-scope
  `settings.json` cannot carry a repo-relative or worktree path;
- its own test file;
- write-only semantics with no `SessionStart` reader in the same compaction
  (row 10).

## Critical files

**PR 1 — Phase 0, docs only.** Branch: the current
`automate-handoff-via-compaction-hooks`.

| Path | Change |
| --- | --- |
| `.claude/plans/automate-handoff-via-compaction-hooks.md` | Committed here in full, before the spike runs, and **not edited again** in either PR (row 26). Provenance for both PRs; Phase 1's PR references it at this path. |
| Throwaway scratch directory outside this repo | Minimal `.claude/settings.json` plus debug `PreCompact`/`PostCompact`/`SessionStart(compact)` hooks and two stand-in composition probes for Q2, registered in both array orders. Disposable, never committed. Logs to `/tmp`, never into this tree. |
| `docs/precompact-hook-behavior.md` | The only committed code-tree artifact of Phase 0. Append a `## Findings — <claude --version>` section answering Q1–Q6 grouped by the three efforts, stating Q6's answer and the Phase 1 go/no-go, embedding each scratch hook's script body and `settings.json` matcher line inline, recording Q2's two registration orders separately, plus one navigational line near the top stating findings are stamped per build. Do **not** edit the existing 2.1.233 findings — Axis 3 preserved record, including its two session-id references. Keep the filename (inbound link at `docs/hooks.md:135`). Run the redaction self-check over the drafted section before staging. |

**PR 2 — Phase 1, the work-state block.** New branch off the updated default
tip; suggested slug `compaction-work-state-block`. Opens only if Q6 confirmed.

| Path | Change |
| --- | --- |
| `claude/.claude/hooks/session-marker-dashboard.sh` | Add a third composed block: `.source == "compact"`-gated work-state pointer (branch name plus absolute plan-file path), emitted only when a file exists at `$REPO_ROOT/.claude/plans/<slug>.md`. Gate `REPO_ROOT`/`PAYLOAD_CWD` on `compact`-or-ledger-enabled rather than hoisting unconditionally, and keep every new clause inside the `[ -n "$REPO_ROOT" ]` guard. Replace the two-way early-exit and cascade with the fixed-element join loop specified in the Approach. Cap the new block alone at 2,000 chars as a whole-block drop. Update the header comment with the five facts named above. `hook-class: informational` unchanged; no `settings.json` change (matcher already includes `compact`); no new sentinel. |
| `claude/.claude/hooks/tests/test_session_marker_dashboard.py` | Layer the cases below onto the existing file as a regression guard, not a replacement. |
| `docs/hooks.md` | Update the existing `session-marker-dashboard.sh` bullet (line 70) to describe the work-state block, its single plan-file trigger, its `compact`-only internal gating, the branch allowlist, and that it carries no sentinel of its own. No new bullet — no new hook file. |
| `claude/.claude/hooks/restore-authorization-boundary-on-compact.sh` | **Reference only, not edited.** The other `compact` emitter, and the precedent for the `.source` self-filter. A negative Q2 routes to the escalation path pre-committed in the Approach, not to an edit here. |
| `claude/.claude/hooks/check-branch-divergence.sh` | **Reference only, not edited.** Source of the branch-resolution command and its detached-HEAD posture (`:61-62`), and of the acknowledgment-demand precedent (`:123`) Verification §5 measures against. Its own `compact` exclusion is an Out-of-scope follow-up. |
| `claude/.claude/hooks/set-session-title-from-branch.sh` | **Reference only, not edited.** Source of the branch-component allowlist and the `LC_ALL=C` matching requirement Phase 1 reuses (row 14). |
| `claude/.claude/hooks/_lib.sh`, `claude/.claude/scripts/review-ledger.sh`, `claude/.claude/scripts/marker.sh` | **Reference only, not edited.** `_lib_capped`/`_lib_capped_for` (row 15), `_lib_valid_session_id_component`, `_lib_config_dir`, `_marker_lib_repo_hash`, and the keying convention the existing ledger block uses. |

**Required cases for `test_session_marker_dashboard.py`.** Every one reuses an
existing fixture or mirrors an existing case in this suite or a sibling — none
is new infrastructure. Working-tree cleanliness is irrelevant to every case:
`git_repo`'s dirty starting state needs no handling in any case, since the
block does not read `git status`.

*Gating and composition*

1. `.source != "compact"` (each of `startup`, `clear`, `resume`) emits no
   work-state block, with the marker and ledger blocks unaffected.
2. **Work-state-block-alone:** compact source, plan file present at the derived
   path, no active markers, no ledger content → the work-state block is present
   and the other two absent. This is the case that catches an unwidened
   early-exit; it mirrors the existing
   `test_ledger_summary_alone_triggers_output_with_no_active_markers`.
3. Marker-only and ledger-only outputs unchanged — byte-for-byte regression
   against today's output on non-compact fires. This is the case a naive
   `printf '%s\n' "${BLOCKS[@]}"` join would fail on a trailing newline, which
   is why the Approach specifies the `${VAR:+…}` form.
4. **All three blocks at once** — an active review-skill marker, non-empty
   ledger content, and a compact-source payload with a plan file present. Assert
   all three are present in the documented order (marker, then ledger, then
   work-state), each separated by exactly one `\n`, with no block run together
   with its neighbour and none dropped. This is the case the three-way join
   rewrite actually needs: cases 2, 3, and 5 are each pairwise or single-block
   by construction, so a wrong order, a doubled or missing separator, or an
   off-by-one in the join would pass all of them. An active marker plus a
   non-empty ledger plus a plan file is an ordinary state for a mid-review
   session that auto-compacts.
5. Ledger sentinel present still suppresses only the ledger block after the
   `REPO_ROOT` gating change — the existing
   `test_kill_switch_suppresses_only_ledger_portion` pattern, extended to assert
   the work-state block still emits on a `compact` fire with the sentinel set.
6. Exit 0 on every path, including missing `git` and a non-repo `.cwd` (the
   latter degrades silently: no `REPO_ROOT`, so no work-state block, and no
   partially-resolved clause).

*Trigger condition*

7. **No plan file at the derived path, resolvable branch → no work-state
   block.** The trigger decision encoded directly, and distinct from the
   non-repo `.cwd` case above: here `REPO_ROOT` and the branch both resolve, and
   the block is still absent because a branch alone is not a trigger.
8. **Plan file present at the derived path → block present**, reusing case 2's
   block-alone fixture (no active markers, no ledger content), asserted as an
   exact match of the full `additionalContext` payload against the template
   with both substitutions filled — not a hand-sliced substring of it. The
   exact match is what pins the design decision that the block states two
   facts and issues no instruction (Approach: "Payload: one pointer, two
   facts, one shape") — a substring assertion would still pass if a later
   edit appended an imperative or a precedence clause. The existence check
   uses `[ -f ... ]`, matching `session-marker-dashboard.sh:55`'s existing
   convention for a marker path. A plan directory at the derived path is not
   a trigger.

*Branch and path resolution*

9. **Branch name containing `/`** — `GH-42/add-auth`, this repo's documented
   common shape per `plan-it` Step 1. The `git_repo` fixture
   (`conftest.py:272-285`) never checks out a slashed branch, so every current
   plan-slug case exercises only the trivial path and a `cut -d/ -f1` bug would
   pass the entire list. Assert the block displays the full branch name and
   resolves the plan pointer to `<repo>/.claude/plans/add-auth.md`.
10. **Detached HEAD** — `git checkout <sha>` on the `git_repo` fixture, with a
    plan file present at a path some branch would derive. Assert **no work-state
    block at all** and that no literal `branch HEAD` appears anywhere in
    `additionalContext`. Precedent: `test_check_branch_divergence.py:361`'s
    `test_detached_head`.
11. **Non-ASCII branch name** at ordinary length, **with an active marker
    present** so an envelope exists to inspect. Assert the allowlist rejects the
    branch, no work-state block appears, the envelope is still valid JSON, and
    `additionalContext` round-trips through `json.loads`. The marker is
    load-bearing for this case: with no other block present the hook exits 0
    with no output, leaving no JSON to validate. This pins the encoding decision
    independently of the cap — `jq` would not fail on a non-UTF-8 branch name,
    it would rewrite it to U+FFFD and exit 0 (row 23), so absent the allowlist
    the defect would ship as plausible-looking mojibake in model-visible context
    rather than as an error anyone notices. **Do not** assert byte-equality
    against the original branch literal anywhere in this case: checking out a
    non-ASCII ref name normalizes differently on macOS (APFS, NFD) than on Linux
    CI, so an equality check against the input string would be
    platform-dependent even though the rejection outcome is not.
12. **ASCII branch name containing punctuation the allowlist excludes but git
    permits** — e.g. `hotfix!123` or `wip,notes`. Same no-block assertion as
    case 11, same active-marker setup. This is the more likely real-world
    trigger for the allowlist than a non-ASCII name, and it is the only case
    that would fail on a regex typo admitting `!` or `,`. It is not covered
    elsewhere: `set-session-title-from-branch.sh`'s own suite tests C1 control
    bytes, UTF-8 high code points, and accented names for the branch component,
    and tests a space only against the *directory* component — so the same blind
    spot exists at both the source convention and this reuse site.

*Cap*

13. **Cap priority** — marker plus ledger content alone already near or over
    2,000 characters (all three markers active plus a long ledger summary).
    Assert both pre-existing blocks survive intact and the work-state block is
    still appended, since the cap does not bound them.
14. **Whole-block drop** — construct the over-cap condition through the **full
    branch name**, not the path: the slug is bounded by one ref component
    (~255 bytes) while the displayed branch name may nest arbitrarily many
    components, so a deeply nested branch with a short final slug and a plan
    file at `.claude/plans/<slug>.md` is the reachable construction. Assert the
    work-state block is absent *in its entirety*, the other two blocks are
    intact, and no partial plan-file path appears anywhere in
    `additionalContext`.

*Branch-resolution cap*

15. **`_lib_capped` fires on the branch-resolution call itself** — reuse
    `git_timeout_shim` (generalized match condition, retargeted from `status`
    to `symbolic-ref`) wrapped in `assert_cap_engaged`; no new fixture
    infrastructure needed. Assert the outcome is "no work-state block at
    all" — the same outcome class as case 10 — and that no partial or
    truncated branch string, and no path derived from one, appears anywhere
    in `additionalContext`. This is the one design-stated correctness
    property in the Phase 1 implementation notes (exit-status check, not
    emptiness check, because a fired cap can return 124 with *partial*
    output per row 15) with no other required case exercising it: cases
    9–12 all exercise branch *content* variations via a branch that resolves
    promptly, none forces the cap itself to fire.

*Provenance*

16. **Untracked plan file, and separately a tracked-but-never-reviewed plan
    file, at the derived path → block fires either way, byte-identical to
    case 8.** Two axes, both producing the same outcome because the trigger
    has no provenance signal to differentiate on, by design (Approach's
    plan-file-provenance Named residual): (a) *untracked* — the file exists
    on disk but has never been `git add`ed, confirmed via `git status
    --porcelain` showing it as `??`; (b) *review-marker-less* — the file is
    tracked and clean but no `plan-review-markers/` entry covers its content
    hash. Reuse case 8's block-alone fixture and template for both, varying
    only the plan file's tracked/review state; assert the emitted work-state
    block is byte-identical to case 8's under each fixture.
17. **Content-interpolation regression.** With a plan file present whose body
    contains a distinctive, attacker-shaped string (e.g. an embedded
    imperative sentence), assert the emitted work-state block's shape is
    fixed — branch name and plan-file *path* only — and that the plan file's
    *content* never appears anywhere in `additionalContext`. Guards the "a
    pointer, not the artifact" decision (Approach: "Why a pointer rather
    than the artifact") against a future change that starts interpolating
    file content into the block.

**Reuse:** the chosen mechanism is almost entirely reuse —
`session-marker-dashboard.sh` already resolves the payload's `.cwd`, already
computes `REPO_ROOT` via a `_lib_capped`-wrapped `git rev-parse`, already
validates the session-id component, already composes multiple independent blocks
into one `additionalContext` string, and is already registered on a matcher that
includes `compact`. Beyond the hook itself: `check-branch-divergence.sh:61-62`
supplies the branch-resolution command and its detached-HEAD posture,
`set-session-title-from-branch.sh` the branch allowlist and `LC_ALL=C` matching,
and `conftest.py`'s `git_repo` the test-fixture surface — used unmodified, with
no extra commit needed anywhere now that no case depends on tree cleanliness.
`restore-authorization-boundary-on-compact.sh` is the precedent for the
`.source` self-filter.

## Verification

1. **Phase 0 is dogfooding, not pytest, and is not delegable to a subagent.**
   Its questions are about harness routing behavior no synthetic-stdin test can
   observe — they need live sessions with real compactions
   (`claude --autocompact 100000` for the auto cases). Every result is confirmed
   by grepping the session's own transcript for a distinctive marker, never by
   asking the model what it saw. Q2 runs at least once in each
   hook-registration order; the others get 2–3 independent runs before being
   written up. Findings land in `docs/precompact-hook-behavior.md` with each
   scratch hook's script body and matcher line embedded inline, so they are
   reproducible against a later build rather than merely re-describable.
2. **PR 1's automated check:**
   `.venv/bin/python3 claude/.claude/scripts/select-tests.py` from the repo
   root, with no arguments. That diff is `docs/precompact-hook-behavior.md` plus
   the plan file, resolving through two rules: `DOCS_DIR`→`HOOKS_TESTS_DIR` +
   `SKILLS_TESTS_DIR`, and `PLANS_DIR`→an empty target tuple (row 27) —
   matched, so the plan file does not force an unmatched-path full-suite
   fallback. Domain-selected, not widened.
3. **PR 2's automated check:** same invocation. That diff — the hook `.sh`, its
   test `.py`, and `docs/hooks.md` — resolves through **six** rules:
   `HOOKS_DIR`→`HOOKS_TESTS_DIR`;
   `_is_hooks_dir_shell_script_change`→`SCRIPTS_TESTS_DIR`;
   `_is_hooks_or_skills_change`→the transcript-analysis glob;
   `DOCS_DIR`→`HOOKS_TESTS_DIR` + `SKILLS_TESTS_DIR`;
   `_is_py_source_under_claude_or_plugins`→`TICKET_REFERENCE_DISCIPLINE_TEST_PATH`;
   and `_is_test_source_change`→`SELECT_TESTS_TEST_PATH`. The fifth applies
   because `DELIBERATELY_UNMAPPED_TOP_LEVEL_DIRS` excludes only
   `claude/.claude/tests/`, not a nested `hooks/tests/`, so the test file
   matches that predicate too (row 27). No changed path hits
   `GLOBAL_TRIGGER_PATHS` or falls through unmatched, so the run stays
   domain-selected. Use the worktree-relative `.venv` path (README's Tests
   section). Do not widen to the full suite by hand.
4. **Lint (PR 2):** `.venv/bin/ruff check claude/.claude/ claude-skills/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.
5. **End-to-end, Phase 1 — test the behavior, not the arrival.** The entire
   value of this block is a behavioral nudge, so confirming the text reached the
   transcript verifies the plumbing and none of the premise. Three steps, in a
   real session on a feature branch with a committed plan file:
   1. Force a compaction and grep the transcript for the injected block
      naming the branch and the absolute plan path. This is the arrival check,
      and it is necessary but not sufficient.
   2. **In the same transcript, in the turn immediately after the compaction,
      grep for a `Read` tool call whose path is the plan path the block
      emitted.** This is the step that tests the premise: that telling the model
      a plan file exists changes what it does next. The block states a fact and
      issues no instruction, so both outcomes carry information. A read is
      evidence the pointer was useful, not evidence the model complied with an
      imperative. A non-read falsifies the block's premise, rather than merely
      showing the model ignored an instruction. Grep the transcript for the tool
      call; do not ask the model whether it read the file, for the same reason
      Phase 0 forbids self-report.
   3. Repeat on a branch with **no** plan file at the derived path and confirm
      no work-state block appears, exercising the trigger decision against a
      live resolvable branch rather than a fixture.

   **Named residual.** `check-branch-divergence.sh:123` earns a compliance
   signal by demanding "Acknowledge this advisory in your first response to the
   user," which makes non-compliance visible in every session it fires in. This
   block has no comparable check, so step 5.2 measures its effect **once, in one
   dogfood run**, and nothing measures it thereafter. That asymmetry is a
   deliberate decline, not a gap: the divergence advisory's acknowledgment is
   paired to a real user decision — rebase now, or defer — and this payload asks
   for no decision at all, so copying that form would be cargo-culting a
   mechanism whose reason for existing is absent here. The cost side is concrete
   as well: an acknowledgment demand would force user-visible chatter on every
   compaction, for an act the user has nothing to decide about. Declined with
   its reason in Out of scope. If step 5.2 shows the model does *not* read the
   file, that is a design finding, not a test bug: the block's premise is false
   and the payload needs rethinking before Phase 1 ships.
6. **Blast radius, Phase 1.** No new hook, no new `settings.json` registration,
   no new sentinel, no matcher change. One `hook-class: informational` hook
   gains one block on one matcher value, on a hook that exits 0 on every path,
   and nothing ships off-by-default, so there is no graduation gate to reach.
   The cost a stow consumer actually sees, as per-fire git-subprocess counts:

   | Fire | Ledger enabled (default) | Ledger sentinel set |
   | --- | --- | --- |
   | `startup` / `clear` / `resume` | 1 (`rev-parse`) — unchanged | 0 — unchanged |
   | `compact` | 1 → 2 (`rev-parse`, `symbolic-ref`) | 0 → 2 |

   Both calls are O(1) reads — `rev-parse --show-toplevel` walks up to the repo
   root and `symbolic-ref` reads a single ref file — so neither scales with
   working-tree size, and there is no compact-path latency exposure to bound,
   caveat, or accept a worst case for. Beyond that, the model-visible change is
   roughly 150 characters of context after a compaction, and only on a
   compaction where a plan file exists at the derived path.
7. **Redaction, both PRs:** the plan file and the Phase 0 findings ship in public
   PRs. No `session_id`, no scratch-directory paths carrying identifying names,
   no figure with a per-project or per-engagement dimension. Placeholder paths
   in every example. Because `deny-private-project-refs.sh` cannot catch a
   truncated session id (row 18) and `docs/precompact-hook-behavior.md` already
   carries two such instances, the Phase 0 section additionally gets the
   pre-commit redaction self-check named in the Approach — a review step, not a
   hook change, and scoped to the new section only.

**Review surface.** Two PRs, reviewed separately. PR 1 is one appended docs
section plus the plan file, reviewed for claim accuracy against the runs rather
than for code — with the caveat that a negative Q2 turns it into a security
finding needing its own attention, which is the reason for the split. PR 2 is
one shell hook, one pytest file, and one docs bullet — three files, one domain.
Risk is concentrated in two places: that the hook still exits 0 and still emits
its two existing blocks byte-identically on every non-compact path (cases 3 and
6), and that the three-way composition rewrite of `:113-123` did not change the
first two blocks' order or joining (case 4, the only case putting all three
blocks in one composed string). The trigger is a single file-existence test
pinned by cases 7 and 8.

## Out of scope

- **`check-branch-divergence.sh`'s own exclusion of `compact` — a named
  follow-up, not fixed here.** By this plan's own resolution of the precedent
  contradiction (Approach), that exclusion is a defect: at `startup` re-emitting
  on-disk state is repetition, but at `compact` the earlier injection *was*
  lost, so a mid-PR session that auto-compacts silently loses a divergence
  advisory about a branch it may be about to push. That is a decision-bearing
  fact, and a better candidate for the compact path than a changed-file count
  would be (see Approach: "Why the changed-file count is not in the
  payload"). Not bundled: it is a second hook, a matcher
  change, and its own test surface, and folding it in would put a behavior
  change to a *different* hook inside a PR whose review surface is this one.
- **Any `PreCompact` hook, in any form.** Retired unconditionally on row 24 —
  its only non-replicable lever is reading the pre-compaction transcript, which
  this design never does. No Phase 0 result reopens it. This strengthens
  `.claude/plans/precompact-review-snapshot.md:79` rather than merely
  reaffirming it: treating Q3 as a reopener would be a category error (Q3
  measures transcript retention; row 6 claims disk-state retention).
- **`PreCompact` `decision: "block"`, including on write failure.** Rejected on
  three independent grounds in the Approach. Not a scoping deferral — a design
  rejection.
- **A changed-file count, or any other working-tree measurement, in the
  payload.** Rejected: a bare number is data the model re-derives by running
  `git status` to learn which files, and a count with no filenames cannot
  inform a decision about any specific file. Reintroducing it would also
  reintroduce the entire `git status` latency surface — the cap caveat, the
  unmeasured cost, the three-state resolution logic, and four required cases —
  for a clause the model replaces with one tool call.
- **A second trigger for the block** — an open PR, a `handoff` file, an active
  marker set, or anything else that would fire it on a branch with no `plan-it`
  plan file. The accepted coverage limit is stated in the Approach. Designing a
  second trigger means re-deriving what knowledge is genuinely unavailable
  post-compaction for that population, which is a fresh question rather than an
  extension of this one.
- **Adding an acknowledgment demand to the payload**, in the shape of
  `check-branch-divergence.sh:123`. Declined because the precedent does not
  transfer: that acknowledgment is paired to a real user decision — rebase now,
  or defer — and this payload asks for no decision at all, so adopting the form
  without the decision behind it would be cargo-culting it. The cost is concrete
  as well — user-visible chatter on every compaction, for an act the user has
  nothing to decide about. Verification §5.2 carries the one-time measurement
  instead.
- **Retiring or retuning `/handoff`, `nudge-handoff-near-context-cap.sh`, or its
  thresholds.** Row 2. Nothing here touches the 40%/`ABS_CAP` thresholds, the
  re-arm spacing, the hard block, or `docs/handoff-nudge.md` — which
  `test_doc_counts.py` ground-truths against the hook's literals, so a threshold
  change would carry its own test surface.
- **Subsuming `/handoff` into an automated compaction path.** The engineer left
  the door open; nothing here forecloses it, and nothing here plans it. The
  chosen mechanism is a disk-state reader, so a future subsuming design would
  replace `/handoff`'s trigger rather than unwind this hook.
- **A hook or script that authors handoff *content*.** No precedent exists in
  this repo, and `docs/design-decisions/full-suite-pytest-drift-traced-to.md`
  records a directly analogous handoff-validation proposal rejected at
  `/plan-review` for checking a condition on intent rather than a
  machine-readable predicate. §3 and §6 are intent; a scripted heuristic would
  reproduce the low-fidelity artifact that made compaction unacceptable in the
  first place, relabeled.
- **Consolidating `restore-authorization-boundary-on-compact.sh` into
  `session-marker-dashboard.sh`.** Phase 1's design is unaffected by Q2's
  answer, so no consolidation code lands here. This deferral is **conditional on
  Q2, not unconditional**: if Q2 shows only one payload reaches context, the
  Approach's escalation mechanism governs — filed as a security-relevant defect
  referenced from PR 1's description, with a minimal same-PR fix explicitly
  evaluated once the failure mode is known. What stays out of scope either way
  is the *full* consolidation refactor.
- **Widening `deny-private-project-refs.sh`'s `_LIB_LONG_HEX_IDENTIFIER_REGEX`
  floor** so it catches truncated session ids (row 18). The gap is real and now
  recorded, but the detector's threshold is owned by whoever owns that hook's
  tuning, and lowering a hex floor has a false-positive surface across every
  file the hook scans. Phase 0 closes its own exposure with a review step
  instead.
- **Editing the existing session-id references already in
  `docs/precompact-hook-behavior.md`** (e.g. `:43`, and several more in the
  subagent-context table). Axis 3 preserved records in a dated empirical
  findings section. The new control applies to the new section only.
- **A per-block opt-out sentinel for the work-state block.** Declined in the
  Approach with its rollback consequence named there. A third
  independently-named switch inside one hook is the compounding-layers tell.
- **Capping, trimming, or otherwise bounding the composed `additionalContext`,
  or the marker and ledger blocks individually.** Both are uncapped today; an
  additive change is the wrong vehicle for introducing this repo's first length
  bound on two already-shipped behaviors, and doing so would risk silently
  losing marker or ledger content as a side effect. The 2,000-character cap
  applies to the new block alone.
- **Adding a `test_set_session_title_from_branch.py` case for
  ASCII-but-disallowed punctuation.** Required case 12 records that the cited
  precedent's own suite has the same blind spot, but that hook's coverage is its
  own file's scope and the fix belongs with whoever touches it next. This plan
  closes the gap only at its own call site.
- **A CLAUDE.md line describing the new block.** The ledger has a manual
  fallback documented in CLAUDE.md; this block does not need one because it is
  self-describing on arrival and has no manual invocation to document.
  `claude/.claude/CLAUDE.md` is also always-loaded and length-capped at
  200 lines by `check-claude-md-length.sh`. Deliberately declined.
- **Renaming `docs/precompact-hook-behavior.md`** to match its widened scope — it
  would break the inbound link at `docs/hooks.md:135` for no reader benefit.
- **Extending the work-state block to `resume` or `startup`.** A `--resume`
  reloads the transcript and a fresh `startup` has lost nothing, so neither is
  the problem this plan addresses.
- **Verifying whether plain stdout on `SessionStart` is equivalent to
  `additionalContext`.** Row 9 records the docs' silence; the design sidesteps
  it by using `additionalContext` exclusively, so resolving it would buy
  nothing.
- **Measuring `git status --porcelain` latency across working-tree sizes.** Out
  of scope: the design does not read `git status`, so there is nothing to
  measure. Retired row 17.
- **What the model receives when `additionalContext` exceeds 10,000
  characters** (the dropped Q7). The new block is capped at 2,000 characters as
  a whole-block drop with no truncation path, so no answer could validate or
  invalidate anything the design does.
- **Chasing the prior spike's unresolved items** — the
  two-`PreCompact`-fires-then-nothing anomaly
  (`docs/precompact-hook-behavior.md:35`), the subagent context-window question,
  and the never-run unintentional-hook-failure case. None is load-bearing for a
  design that adds no compaction-event hook.
