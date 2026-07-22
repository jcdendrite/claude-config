# Plan — GH #466: assumption ledger + content-addressed plan-review gate

## Context

**Goal:** stop plan revisions from silently contradicting facts the same session
already established, by (a) recording those facts in a structured ledger inside
the plan file and (b) forcing a fresh-context re-review on *every* plan revision.

Issue [#466](https://github.com/jcdendrite/claude-config/issues/466) documents a
failure mode: across successive revisions of one plan — sometimes across two
conversational turns — the agent loses track of a fact it had already verified,
because attention is captured by whatever finding is currently active. The catch
has always come from *fresh context* (a human's outside-view question, or a
reviewer subagent), never from the agent re-checking itself in the same turn.

The issue proposes four mechanisms (ledger, structural-completeness hook,
Stop-hook, cross-check subagent). Exploration found the "force re-review on
revision" half needs **no new hook**: `require-plan-review.sh`'s completion
marker is currently session-keyed and existence-only (`printf 'reviewed'`), so a
revision *after* a clean review is not re-gated. `require-code-review.sh` already
solves the identical problem by content-addressing its marker (sha256 of the
staged diff), auto-invalidating on any change. Adopting that idiom for
plan-review is the whole forcing function. Per the user's scope decision
("Lean foundation first"), the structural-completeness hook and the Stop-hook +
dedicated cross-check subagent are **deferred** until the issue's own
"validate before committing engineering effort" step confirms the hypothesis on
live plans.

**Intended outcome:** revising a reviewed plan re-arms the plan-review gate; the
re-run's fresh-context reviewers cross-check the revision against a written
ledger of established facts and flag any contradiction of a previously-settled
row before the plan can be presented.

## Approach

Three coordinated changes; the ledger is the load-bearing part (both the
existing review flow and any future check can only catch facts that were
written down), the marker change is the minimal forcing function, the
plan-review step is what makes the fresh-context reviewers do the cross-check.

### 1. Assumption ledger — `plan-it/SKILL.md` Step 5 (prose)

Formalize the existing **"Lighter alternatives considered"** subsection into a
structured ledger the plan author writes into the plan file:

- **One root problem/threat line.**
- **Per mechanism:** a one-line justification that references `anchors: root`
  or `anchors: row<N>` so completeness is a real parse, not another judgment
  call (mirrors the issue's proposal).
- **Every material assumption gets its own row, tagged:**
  - `[verified: <source>]` — checked against code/docs this session, source citable.
  - `[unverified]` — asserted, load-bearing, not checked; anything downstream is flagged.
  - `[engineer-verified]` — came from the human directly. The agent may **not**
    silently revise or override this tag from its own investigation; a
    contradiction must pause and ask.

Keep the SKILL.md addition tight (budget: 69/200 lines today). The worked
example + grammar rationale go in the **co-located `plan-it/REFERENCES.md`**
(already exists; not loaded at runtime), not in the skill body — matching the
repo's edit-time-reference convention.

### 2. Content-addressed plan-review marker — the forcing function

Replace the existence-only marker with a content hash of the active plan
file(s), so a revision re-arms the gate. Single source of truth for the hash
lives in `_lib.sh` (same pattern as `_marker_lib_repo_hash`), so read side and
write side compute byte-identical values.

- **New `_lib.sh` helper `_lib_active_plan_hash <repo_root>`:** enumerate the
  active (untracked-or-modified-vs-HEAD) plan files in `.claude/plans/`
  (`*.md`/`*.txt`, maxdepth 1), and hash their **paths + contents**. Returns
  empty when no plan is active. This *unifies* the current `NEEDS_REVIEW`
  detection loop with the hashing — one function answers both "is the gate
  armed?" and "what state was reviewed?" **Determinism contract (write side and
  read side must produce byte-identical output or the gate wedges):**
  - **Ordering:** `LC_ALL=C sort` the file list — a bare `sort` honors
    `LC_COLLATE`, and marker.sh (user Bash-tool locale) and the hook (harness
    hook environment) can differ, flipping order on ≥2 plans → false-deny.
  - **Active-set gating — ask git for the set, don't enumerate and probe.**
    "Active" is exactly `untracked` ∪ `tracked and modified vs HEAD`, so
    derive it from `git ls-files --others` plus `git diff --name-only HEAD`
    (`:(glob)` pathspecs to hold the maxdepth-1 scope; `--others` *without*
    `--exclude-standard`, since a gitignored plan is still an unreviewed
    plan). Probing each file's status individually costs two `git` spawns
    per plan and measured **~1.2s on a 61-plan directory** — this hook fires
    on every Write/Edit/MultiEdit/ExitPlanMode against a <100ms budget. The
    set-query form measures ~0.05s and does not grow with plan count.
    - **HEAD-less repo:** `git diff HEAD` errors when nothing has shipped
      (the fresh scratch repo of Verification step 3). Branch on
      `git rev-parse --verify HEAD` and treat every tracked plan as active
      there, rather than letting the error misclassify.
    - **`--diff-filter=d` is required.** A tracked plan deleted from the
      worktree is reported as modified but has no bytes left to hash;
      counting it active makes the hash permanently unobtainable, which
      under the failure contract below denies *forever* — with no file left
      for the user to repair. Deleting a plan must disarm, not wedge.
  - **Delimiting:** separate path from content and entry from entry with a
    newline. Note the collision this *doesn't* defend against: with
    fixed-width 64-hex digests the concatenation is already injective, so
    `{a, "b…"}` vs `{ab, "…"}` cannot actually collide today. Keep the
    delimiter for readability and for a future variable-width digest, but do
    not document it as a live collision defense — an unfalsifiable claim in a
    comment is worse than no claim.
  - **Hash repo-relative paths, not absolute ones.** The write side and read
    side each resolve the repo root independently; hashing the absolute path
    folds any difference between those resolutions into the digest and turns
    a cosmetic path difference into a false-deny.
  - **Digest tail:** `sha256sum | awk '{print $1}'`, matching
    `_marker_lib_repo_hash`/code-review exactly.
  - **Failure contract — three outcomes, exit status disambiguates stdout.**
    "Nothing to gate" and "could not compute" must never collapse onto the
    same caller-visible signal; a single empty-string return for both is
    fail-*open*, because every caller reads empty as "disarmed → allow."
    - `exit 0` + non-empty stdout → that is the active plan set's hash.
    - `exit 0` + empty stdout → no plan is active; gate disarmed.
    - `exit 1` + stdout = **the path of the plan file that could not be
      hashed** → a plan IS active but unhashable (unreadable, vanished
      mid-enumeration, `sha256sum` failed). Callers MUST fail closed.
      Reusing stdout for the offending path (rather than stderr or a global)
      keeps it to one call site with no subshell-visibility problem — the
      exit status already tells the caller which meaning applies.
    Never hash partial state, and never emit a divergent non-empty hash.
  - **Cap every subprocess through the `command -v timeout` guard, not a bare
    `timeout 5 …`.** Stock macOS ships no `timeout(1)`, so a bare call is not
    "uncapped" — it is exit 127 with stderr suppressed, which yields empty
    output. Under a set-query enumeration that reads as "no active plans" and
    **silently disarms the gate on every fire, on every repo, on that entire
    platform**. `_lib.sh` already open-codes the guard twice (`_lib_jq`,
    `_lib_stray_marker_hint`); extract it into one `_lib_capped` helper and
    route the git calls *and* the per-file `sha256sum` through it. (Capping
    `sha256sum` is new: the old code never read plan contents, so a plan on a
    dead NFS mount is a hang path this change introduces.)
  - **Status-check every enumeration call; a partial set must fail closed.**
    This is the subtle one. An unhashable *file* already fails closed, but a
    failed *enumeration* silently yields fewer files — and fewer files still
    hashes cleanly. Both sides would then agree on a hash computed over an
    active plan neither of them saw, opening the gate with nothing logged.
    Treat any non-zero git exit exactly like an unhashable file.
  - **Capture-then-check every digest, so the contract does not depend on the
    caller's shell options.** The per-file loop already captures into a
    variable and checks it; the **final combined digest must do the same**
    (`digest=$(printf … | sha256sum | awk …); [ -n "$digest" ] || return 1`).
    This is load-bearing, not stylistic: `marker.sh` sources `_lib.sh` under
    `set -u` with **no `pipefail`**, where a pipeline reports only the last
    command's status — a failed/missing `sha256sum` still leaves `awk`
    exiting 0 having printed nothing, silently misclassifying a hash failure
    as "no active plan" on the write side. Capturing and testing the value
    sidesteps inherited shell options entirely.
  - **Shell hygiene:** `local` on every var, `IFS= read -r`, quote all
    expansions, emit with `printf '%s'` not `echo`; do **not** add `set -e`
    inside the helper (`git diff --quiet` legitimately returns 1) and no bare
    `(( ))` (repo convention: aborts at zero).
- **`marker.sh` `write plan-review` arm:** replace `printf 'reviewed\n'` with
  the value of `_lib_active_plan_hash`. Computed at write time, so it reflects
  the plan *after* any review-time edits. **Capture the hash into a variable
  first, then redirect** — never `_lib_active_plan_hash … > "$MARKER"`
  directly. Shell `>` truncates the destination *before* the command runs, so
  the direct form destroys an existing valid marker as a side effect of a
  failed attempt. On `exit 1`, print the offending path to stderr and `exit 2`
  **without touching the marker path at all**. Capturing eliminates the
  failure window entirely; `mktemp` + atomic `mv` is deliberately *not* used
  here, because all three sibling `write` arms (code-review, skill-review,
  ready-for-review) use the same plain-redirect shape and diverging this one
  arm buys nothing once the value is already computed and validated.
- **`require-plan-review.sh`:** replace the break-on-first-active-plan loop
  with `if ! CURRENT_HASH=$(_lib_active_plan_hash …)`. Keep this a *top-level*
  assignment — if it ever moves inside a function, `local CURRENT_HASH=$(…)`
  would report `local`'s status (always 0) and mask the failure; declare and
  assign on separate lines in that case.
  - **`exit 1` → deny, with a message distinct from the ordinary
    missing-marker deny.** It must name the specific unhashable plan file and
    tell the user to fix or remove it directly (`chmod`/`rm`/`mv`). It must
    **not** say "run `/plan-review`": that remedy is circular here, because
    `marker.sh` hits the identical condition and aborts. This hook gates only
    `Write|Edit|MultiEdit|ExitPlanMode`, so `Bash` remains available as the
    escape hatch — the wedge is soft, but only if the message points at it.
  - Empty → `exit 0` (disarmed). Then read the marker's stored content, strip it
  with `tr -d '[:space:]'` (mirror `require-code-review.sh`'s read side, which
  already defends against a trailing newline), and compare to `CURRENT_HASH`:
  equal → allow; differ (or marker missing) → deny. This makes "the plan changed
  since review" the deny condition, not "no marker at all." Keep the
  active-marker bypass and repo-scope filter exactly where they are — the bypass
  short-circuits before the hash logic (so review-time edits are unaffected),
  and the empty-hash sentinel now *expresses* the historical (committed==HEAD)
  pass-through the old loop did.

**Atomic rollout (required).** `_lib.sh`, `marker.sh`, and
`require-plan-review.sh` are stowed and go live on `git pull` with no reinstall.
Ship all three in **one commit**: a new hash-writing `marker.sh` paired with an
old existence-checking `require-plan-review.sh` would existence-check the hash
file and **false-allow**. The transition is otherwise self-healing — markers are
keyed `<repo-hash>.<session_id>`, so a stale literal-`reviewed` marker from a
prior session is never consulted by a new session; the worst case is one
in-flight session that pulled mid-review getting a fail-closed re-review prompt.
No migration shim needed. The `helpers.py` marker-writer change must land in the
same commit as the read-side change (its literal-marker write would otherwise
break the suite).

Behavior delta: after a clean review, editing the plan (including any ledger
row) re-arms plan-review; editing *other* files does not, as long as the plan is
untouched. Same accepted cost `require-code-review.sh` already carries (any
change re-runs the review). Committed-and-unmodified plans remain "historical"
and disarm the gate entirely — unchanged. Note: when multiple active plans sit
in `.claude/plans/`, the hash covers all of them, so editing *any* one re-arms —
consistent with today's "arms on any active plan," but the hash now also churns
on an unrelated untracked plan's content. Accepted; named here so it's not a
surprise.

### 3. Ledger cross-check — `plan-review/SKILL.md` (prose)

The fresh-context property is already provided by plan-review's spawned reviewer
subagents (it runs in the authoring session, so the *orchestrator* shares the
captured attention — the subagents do not). Give those reviewers the new job:

- Feed the plan's ledger to spawned reviewers; instruct them to diff the current
  revision against every `[verified]` and `[engineer-verified]` row for
  continued consistency.
- A reviewer must **not** resolve a contradiction against an `[engineer-verified]`
  row on its own — flag it back to the human.
- Emit a **"previously-settled, now reopened"** output section whenever the
  revision touches a row already confirmed — surfacing the human's own version
  of the failure (re-litigating something already decided), not just the agent's.

Primary home is the reviewer-dispatch instructions (Step 5 / `ROUTING.md`) plus a
one-line Step 4 note and an output-format section; exact placement is an
implementation detail governed by "the cross-check must run in the spawned
reviewers' fresh context." plan-review is 251/500 lines — ample room.

### Assumption ledger

Dogfooding the format this plan introduces. Row 5 is the reason this plan
carries a ledger at all — see **Previously-settled, now reopened** below.

```
Root: a plan revision can silently contradict a fact the same session already
verified, because attention is captured by whatever finding is currently active.

Row 1 [mechanism]: assumption ledger in plan-it Step 5 — anchors: root — a fact
that was never written down cannot be diffed against by any later check.
Row 2 [mechanism]: content-addressed plan-review marker — anchors: root — forces
a fresh-context re-review on every revision; an existence-only marker does not.
Row 3 [mechanism]: ledger cross-check in plan-review's spawned reviewers —
anchors: row1 — the ledger needs a reader whose attention was never captured;
the orchestrator shares the authoring session's, the subagents do not.
Row 4 [assumption]: require-code-review.sh already content-addresses its marker
via `git diff --cached | sha256sum`, so row2 ports a proven in-repo idiom rather
than inventing one [verified: claude/.claude/hooks/require-code-review.sh] —
anchors: row2
Row 5 [assumption]: an unhashable active plan must fail CLOSED, and a single
empty-string return cannot express that — "no plan active" and "hash failed"
must be distinguishable by exit status [verified: empirically, by two
independent reviewers who reproduced the fail-open with chmod 000 on the sole
active plan and observed the hook allow] — anchors: row2
Row 6 [assumption]: denying on the row5 failure path is recoverable rather than a
hard wedge, because this hook gates only Write/Edit/MultiEdit/ExitPlanMode and
leaves Bash available to chmod/rm the offending file [verified:
require-plan-review.sh's tool-name filter] — anchors: row5
Row 7 [assumption]: no other repo mechanism depends on plan-review-markers/
existence-only semantics [unverified] — anchors: row2
Row 8 [assumption]: the structural-completeness hook and the Stop-hook +
dedicated cross-check subagent stay deferred until the ledger proves itself on
live plans [engineer-verified] — anchors: root
```

**Previously-settled, now reopened.** Row 5 replaces a prior-round row that
asserted the opposite and was carried into the implementation unchallenged:
*"Collapse a partial/failed read to empty (disarm → forces re-review,
fail-closed)."* That row was self-contradictory on its face — disarming the
gate **allows**; it does not force re-review — and the implementation
faithfully reproduced the error, producing a silent gate bypass. The
contradiction survived a full prior-round plan review and was caught only by
fresh-context reviewers reading the built code. Recorded here rather than
silently edited, because this is precisely the failure class #466 exists to
close, and it is the first live evidence that the ledger's value is in making
such a row *visible enough to be re-read*, not merely written down.

### Why not the heavier mechanisms (deferred)

- **Structural-completeness hook** guards ledger *well-formedness*, but the
  failure mode is *losing a fact* — a well-formed ledger with a silently
  overwritten row passes it. It exists only because the ledger format exists:
  the exact "layer closing a gap the prior layer created" tell from
  `plan-review` Step 4. Defer until the ledger proves it needs mechanical
  shape enforcement.
- **Stop-hook + dedicated cross-check subagent** introduces a Stop event the
  repo has none of, to do what the content-addressed marker + existing reviewer
  spawn already do. Defer per the issue's own validation gate.

## Critical files

**Modify:**
- `claude/.claude/skills/plan-it/SKILL.md` — Step 5 ledger directive (concise).
- `claude/.claude/skills/plan-it/REFERENCES.md` — ledger worked example + grammar.
- `claude/.claude/skills/plan-review/SKILL.md` — Step 4/5 + output-format cross-check.
- `claude/.claude/skills/plan-review/ROUTING.md` — reviewer-dispatch cross-check instruction (if that is where reviewer prompts are specified).
- `claude/.claude/hooks/_lib.sh` — add `_lib_active_plan_hash`.
- `claude/.claude/hooks/require-plan-review.sh` — consume the hash; compare content not existence.
- `claude/.claude/scripts/marker.sh` — `write plan-review` stores the hash.

**Reuse (call, don't reimplement):**
- `_marker_lib_repo_hash` (`_lib.sh`) — the exact single-source-of-truth
  hashing pattern to mirror for `_lib_active_plan_hash`.
- `require-code-review.sh` + `marker.sh` `write code-review` — the
  content-addressed-marker idiom being ported (sha256 → marker file, read side
  recomputes and compares).
- Existing NEEDS_REVIEW plan-enumeration loop in `require-plan-review.sh` — its
  active-plan definition (untracked-or-`git diff HEAD`) is what
  `_lib_active_plan_hash` folds in; do not invent a second definition.

**Tests (add/update).** Anti-tautology rule for all of these: **derive the
expected hash from the real writer** — write the marker via `marker.sh write
plan-review` (or source and call `_lib_active_plan_hash`), then observe the
gate's allow/deny. Do **not** add a Python `active_plan_hash()` reimplementation
to `helpers.py` (it would pass by matching the test author's mental model, not
production, and diverge silently on any newline/delimiter/normalization detail).

**Name the tradeoff this makes, don't overclaim it.** Shelling out to the real
`_lib_active_plan_hash` buys immunity from a Python-side reimplementation
drifting — but it does *not* make the marker-seeded allow-tests independent
oracles: they check the function agrees with itself across two invocations, so
a bug baked into the helper (say, a constant return) still passes them. The
independent-correctness burden therefore sits entirely on the relational
`_lib` unit tests above, which do not route through the helper. This is the
right split, but the helper's docstring must say so rather than claiming
parity with `write_marker`/`write_skill_review_marker` — those genuinely *do*
recompute in Python from a real `git diff`, which is the opposite technique.

- `claude/.claude/tests/helpers.py` — `write_plan_review_marker` must shell out
  to the real hash (source `_lib.sh` → `_lib_active_plan_hash`), not recompute
  in Python. Blanket-updating this fixes the tests that key off marker content.
- `claude/.claude/hooks/tests/test_require_plan_review.py`:
  - **Retarget the scope-masked allow-tests.** `test_plan_exists_with_marker_allows_write`
    (~L77), `..._allows_edit` (~L89) target `/tmp/foo.py`; the hook's completion-marker
    check runs *before* the repo-scope filter, and the scope filter allows any
    out-of-repo path unconditionally — so these assert `allow` and stay green
    even if the hash compare is inverted or always-true. Point them at an
    **in-repo path or ExitPlanMode** so the comparison is load-bearing.
  - **New:** matching-hash allows; stale-hash (plan edited post-review) denies —
    both on an in-repo/ExitPlanMode target. `test_completion_marker_allows_exitplanmode`
    (~L705) is the one true breaker (no `file_path` → scope filter skipped) and
    must move to the real hash.
  - **New — arm-set == hash-set agreement:** one committed-clean plan + one
    active plan; assert (a) gate armed, (b) committing/reverting the active plan
    disarms — proving the committed-clean file contributes nothing to the hash
    (guards the "unification" claim; a break-on-first-`find`-result bug would
    pass the old empty→disarm test but leave the gate armed forever).
  - **New — multi-plan second-file re-arm:** marker over a two-active-plan set
    allows; editing *either* plan then denies (catches the ported
    break-on-first-plan bug from the old loop).
  - **New — legacy-marker transition:** a literal `reviewed\n` marker + an
    active plan must `deny` cleanly (fail-closed, not error under `pipefail`).
- `claude/.claude/hooks/tests/test_marker_script.py` — `write plan-review` stores
  `_lib_active_plan_hash`, not the literal `reviewed`.
- New `_lib` coverage for `_lib_active_plan_hash`, asserting **relational**
  properties only (never a golden sha256 literal): empty when no active plan;
  `hash(A)==hash(A)` under reordered `find` output; `hash(A)!=hash(A')` after a
  content edit; `hash(A)!=hash(A+B)`; and a **spaces/non-ASCII plan filename**
  round-trip (write-hash == read-hash, non-empty) to catch unquoted
  word-splitting.
- **Every defense this plan names in prose needs a test that fails when the
  defense is removed.** A determinism-contract bullet with no regression test
  is a comment, not a contract — a later "simplification" deletes it with zero
  CI signal. Each of these must be verified by *mutation*: remove the defense,
  confirm the new test goes red, restore.
  - **Locale invariance:** same two-file active set, hashed twice with
    differing ambient `LC_ALL` (e.g. `C` vs a non-C collation), asserting
    equal output. Skip the test if no second locale is installed rather than
    failing — the defense is real but the fixture is environment-dependent.
    Filenames must be chosen so the two collations genuinely disagree (e.g.
    `B.md` vs `a.md`); an all-lowercase pair sorts identically everywhere and
    would make the test vacuous.
  - **Delimiter collision:** two active sets constructed to collide under
    naive `path+hash` concatenation, asserting unequal hashes.
  - **Failure contract (`exit 1`):** an unreadable active plan (`chmod 000`)
    → helper exits 1 and prints the offending path; `require-plan-review.sh`
    **denies** (not allows); `marker.sh write plan-review` exits 2 **and
    leaves a pre-existing marker byte-identical** (pins the truncation fix).
- **End-to-end write→read agreement.** No test currently runs the real
  `marker.sh write plan-review` and feeds its output to a real
  `require-plan-review.sh` for the same repo state — each side is tested only
  against a stand-in for the other (a regex format check on one, a Python
  shell-out helper on the other), so a divergence in how either resolves
  `REPO_ROOT` or writes/strips the value is invisible. Add one integration
  test: seed a repo with an active plan, run `marker.sh write plan-review`,
  assert the hook allows; edit the plan, assert it denies.
- `claude/.claude/skills/plan-*/SKILL.md` are re-read as `HOOK_TEST_FIXTURE`
  sources; the fenced `marker.sh write plan-review` command string is unchanged,
  so `test_hook_alignment.py` stays green — verify, don't assume.

## Verification

1. `.venv/bin/pytest claude/.claude/` — full hook + skill suite (run from a worktree via `../../../.venv/bin/pytest`).
2. `.venv/bin/ruff check claude/.claude/` and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — Python + shell lint.
3. **Manual gate smoke test** (the behavior that's the whole point): in a scratch repo with a `.claude/plans/p.md`, run `/plan-review` to write the marker; confirm `ExitPlanMode` is allowed; edit `p.md`; confirm `ExitPlanMode` is now **denied** (hash mismatch) until re-review — the current code would wrongly allow it.
4. Confirm editing a non-plan file after review is still allowed while the plan is untouched (marker hash still matches).
5. Review dispatch at implementation time: `claude-hook-review` on the
   `require-plan-review.sh` + `_lib.sh` + `marker.sh` changes (the drafted hook
   text does not exist yet at plan time, so this runs against real code, not the
   plan); `/skill-review` on the two edited SKILL.md files; `/code-review`
   before presenting.

## Out of scope / incidental

- **`plan-review/REFERENCES.md` tripwire-count drift** (surfaced during
  exploration; initially deferred as a dated *incident record* under
  preserved-content Axis 3, then **brought into scope at the engineer's
  explicit request** — which is the authorization Axis 3 requires). Review
  found the drift was wider than first reported: the tripwire→principle
  *table* carried 2 of 5 rows, and the surfacing-incident section 4 of 5.
  Both now carry one entry per tripwire. Two entries deliberately record an
  *absence* rather than inventing content — misordered observe-then-mutate
  maps to no CLAUDE.md principle, and overcorrection has no surfacing
  incident (it came from a judgment-activation pass) — because a fabricated
  provenance in a record is worse than the drift it papers over.
- **`plan-review` over-powered-primitive threshold** (same request): the
  tripwire required naming "the lighter primitive," singular, while `plan-it`
  Step 5 requires the author to enumerate at least two, so a one-alternative
  plan passed review while violating the authoring rule. Both sides now state
  the same threshold and the reviewer cites `plan-it` as its source.
- **Structural-completeness hook** and **Stop-hook + cross-check subagent** —
  deferred pending live validation, per the scope decision. Track as a #466
  follow-up.
- No change to the code-review, ready-for-review, or respond-pr marker
  mechanics — plan-review only.
- **Sibling `write` arms shared the truncate-before-run shape** (surfaced
  while fixing it here, and **folded in** rather than deferred): `write
  code-review`, `skill-review`, and `ready-for-review` all used `<pipeline> >
  "$MARKER"`, so a mid-pipeline failure emptied an existing marker before the
  command could fail. Individually lower severity than the plan-review case —
  their pipelines are `git diff`/`git rev-parse`, which fail far more rarely
  than reading an arbitrary user file — but the bug shape and the fix are
  identical across all four arms of one `case` statement, which is the
  sibling-audit rule's central case: scope is set by the bug, not by the arm
  the symptom surfaced in. Each arm now computes into a variable, aborts with
  exit 2 on an empty value, and only then redirects.
