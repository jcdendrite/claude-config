# Reviewer diff handoff by path

## Context

Make `/ready-for-review` step 4's `skill-fidelity-reviewer` dispatch satisfiable on a
cumulative diff larger than the Bash tool's 30,000-byte truncation threshold, and stop
step 3 from materializing a diff it will not review. Today `ready-for-review/SKILL.md:96`
instructs the parent to paste "the literal diff **output** step 3's `git diff` already
produced," while `subagent-delegation/REFERENCES.md:10-15` establishes that output above
30,000 bytes never reaches the parent — only a head-only 2 KB preview does — and
`subagent-delegation/SKILL.md:95` forbids the whole-file `Read` of the persisted
`tool-results/` overflow file that is the only route to recovering it. The two
instructions are jointly unsatisfiable above that threshold; a session hit the
contradiction, took the forbidden read to recover the diff, and exhausted its context
cap. Separately, step 3 prints the full diff to stdout before the cache check at
`ready-for-review/SKILL.md:74`, so on a cache hit the parent ingests a diff it then
reviews none of. The intended outcome is that step 4 hands the reviewer a path to a diff
file it can `Read` (it holds `Read`, not `Bash`), that a cache hit costs the parent no
diff bytes, and that `docs/cost-levers-considered.md`'s prior rejection of file-path
handoff carries a dated note recording which case it actually priced.

## Approach

Step 3 checks the review cache before it computes a diff it may discard, and **step 4 —
not step 3 — writes the artifact it hands the reviewer.** `pr-diff-against-base.sh`
gains one flag, `--diff-file`, that writes the cumulative diff to a deterministic path
(`<config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id>`, the keying
`--record`'s subject already uses) and announces it on stderr as `DIFF_FILE: <path>`.
Step 4 invokes it as **its own Bash call with stdout redirected to `/dev/null`**, then
hands `skill-fidelity-reviewer` the announced path instead of pasted diff text.
`marker.sh deactivate ready-for-review` deletes the artifact in the same arm that
already deletes the subject.

**The path line gets a call of its own, so it is never at truncation risk.** The
alternative is to write the artifact from step 3's own diff-producing invocation, which
puts the `DIFF_FILE:` stderr line in the same tool result as an unbounded stdout diff.
Row 12 cannot rule out a truncation model that concatenates whole streams, under which
that line is lost. Recovering it then needs a fallback re-run, and a fallback needs a
staleness guard and a terminal case for an empty retry — three conditionals to protect
one line. The line is only ever at risk because it rides alongside the diff. The `--diff-file`
invocation redirects stdout, so its entire tool result is at most two short stderr lines
— the `DIFF_FILE:` line plus the script's pre-existing `gh pr view failed; defaulting
base to …` warning (`pr-diff-against-base.sh:43`). No truncation model, verified or not,
can reach a result that small. Row 12 stays `[unverified]` and becomes **not
load-bearing**, which is a strictly better outcome than mitigating it.

Three consequences follow, and each removes mechanism rather than adding it:

- **No fallback, no retry, no staleness clause.** There is one command, unconditional,
  and its output is un-truncatable. Nothing re-runs, so nothing can re-run against a
  changed tree.
- **One terminal case, not three.** A missing `DIFF_FILE:` line now has exactly one
  meaning — the script on disk did not write the file (an old script under a new
  `SKILL.md`, or a write failure the adjacent stderr warning names). Step 4 halts and
  reports it. This closes the `git pull`-lands-mid-gate window loudly instead of
  silently, and it is one clause on one command rather than a terminal clause bolted
  onto a retry bolted onto a relay.
- **The combined `--record --diff-file` shape disappears from every recipe,** and with
  it the resolution hoist. See "No hoist" below.

**Why step 4 rather than step 3.** Writing in step 3 would reuse a diff step 3 already
computed; under this design nothing is reused, so placement is decided on correctness
instead. Step 4 is a last-gate-before-handoff fidelity check on the branch's
delivered work (`skill-fidelity-reviewer.md:9`; the same file's `:114-115` reasons this
way about spawn evidence), and `ready-for-review/SKILL.md:17-18` routes every step-3 and
step-4 fix back through step 2, so step 4 is only ever reached with a tree that just
cleared steps 2–3. Computing the diff at the moment step 4 needs it is the more correct
reading, and it makes the artifact independent of which cache branch step 3 took. **This
is a real semantic change worth stating plainly: step 4 no longer necessarily audits the
exact bytes `/code-review` audited.** Nothing in the agent's contract requires that
identity — it compares invoked skills against the delivered diff — and on the path that
reaches step 4 the two differ only if a commit lands out-of-band mid-gate, in which case
auditing the newer state is what a pre-handoff gate should do. That claim is about which
bytes step 4 audits and nothing more: recomputing here keeps the announced path fresh, it
does not give `/code-review` coverage of an out-of-band commit it never saw. That gap is
pre-existing and untouched by this plan.

**Cost, re-derived per branch — and the cache-hit branch costs nothing new.** `marker.sh
status`'s `cumulative-review` line runs the script bare through
`_lib_cumulative_diff_hash`, so it already pays a `gh pr view` (`docs/scripts.md:58`;
`pr-diff-against-base.sh:37`). Today both branches run the script twice per gate pass:
`--record` then `status`. After this change a **cache hit** runs it twice as well
(`status`, then step 4's `--diff-file`) — identical count, and the parent now absorbs
zero diff bytes on that branch, because the pre-cache print is gone and the
dispatch-pasted copy is gone. A **cache miss** runs it three times (`status`,
`--record`, `--diff-file`): one added `gh pr view` and one added permission prompt, on
the branch that is already dispatching a full `/code-review`. The hit branch gains
nothing, because it already pays two. The one new cost is a round trip on the miss
branch, and it buys the removal of an unverified assumption from the load path.

**No hoist — `--record`'s block is not touched.** With the two flags never co-occurring
in a recipe, `--diff-file` becomes a sibling block placed after `--record`'s, below the
stdout `printf`, resolving the repo root / config dir / session id independently. The
duplication is the one guarded `if` line plus `REPO_HASH=`; extracting it would need a
function returning three values, which in a `set -euo pipefail` shell means writing
globals from a function body — heavier than the two lines it removes. Three findings
resolve by construction rather than by patch:

- `pr-diff-against-base.sh:56-59`'s comment does not become false. Its substantive claim
  — a resolution or write failure below never costs the caller the diff and never
  changes the exit code — becomes true of **both** flags. Only its first clause
  ("Everything below is the `--record` path") needs widening to name both.
- `--record`'s four stderr messages, its failure branches, and
  `test_record_prints_before_session_resolution_failure` are untouched, so the
  wrong-branch-fires-right-substring risk is not introduced by this plan at all.
- The combined shape's resolution-failure behavior falls out: each block emits its own
  artifact-specific message, so **two** messages, which is the more informative choice.
  It is pinned by test.

**Where the file lives — unchanged and not reopened.**
`<config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id>`, written
atomically via `mktemp` + `mv` in that directory. `agent-reviews/` stays rejected (it
would pull `findings-path-suffix.sh` earlier, put a `.diff` into a directory whose
documented shape is `<agent>-<epoch>-<slug>.md`, and touch step 7's clean-tree check).
`${TMPDIR:-/tmp}` stays rejected: it is the lighter mechanism, but a random suffix cannot
deliver determinism, so every step-4 re-entry through the fix loop would mint a new
readable file, nothing could clean it deterministically, and
`enforce-marker-script-shape.sh`'s `*/.claude/*-markers/*` patterns (`:210-222`) would
not cover it while `deny-reviewer-tree-mutation.sh` explicitly sanctions `/tmp` writes.
The `*-markers` suffix also means no hook file enters the diff.

**Why stderr.** `pr-diff-against-base.sh` already uses stderr as its out-of-band channel
(`:43`, `:77-86`), and stdout is a hashed artifact — `_lib_cumulative_diff_hash`
(`_lib.sh:538-547`) hashes it and `test_pr_diff_against_base.py:303-314` pins it
byte-identical across `--record`. The path line goes on stderr so the flag cannot
perturb that contract. A `DIFF_FILE:` prefix rather than a bare path, because `:43`'s
warning line can precede it; this matches step 5's `BODY_FILE: <path>` announcement
already in this skill (`ready-for-review/SKILL.md:111`, `:117`) rather than
`findings-path-suffix.sh`'s weaker "last line of output" convention.

**Two artifacts, one lifecycle model.** On a cache miss the same diff text lands in two
`<config-dir>` directories. That is not redundancy: `marker.sh write cumulative-review`
deletes the subject on a successful write (`marker.sh:401-404`) before step 4 runs, and
on a cache hit no subject is written at all. Both files use
`<config-dir>/<kind>-markers/<repo-hash>.<session-id>`, both are written via `mktemp` +
`mv`, both are removed by `deactivate ready-for-review`, and the script header names
both. They diverge in exactly one respect — `--record` strips stdout's trailing newline
for hash-recipe agreement, `--diff-file` keeps it — and that divergence is deliberate
and comment-documented.

**Retention under an abandoned gate run is disclosed in `docs/scripts.md`, covering both
artifacts.** A run that never reaches step 8 never calls `deactivate`, so both files
persist until the same session id deactivates again, which may never happen;
`clear-stale` walks only `"$CONFIG_DIR"/.*-active.d` (`marker.sh:502-551`) and cannot
see either directory. The altitude is `docs/scripts.md`'s `pr-diff-against-base.sh`
entry because that is the per-script reference a consumer reads to learn what a script
leaves on disk. Not the script header — that documents what the flags do, for someone
editing the script, and a second copy would drift. Not a dated follow-up in
`docs/cost-levers-considered.md` — a follow-up records a *deferred decision*, and this is
*current behavior*; that file's reader is pricing token cost, not disk retention. The
sentence names both artifacts because the subject's gap is identical and undisclosed
today, and a sentence naming only the new file would be a half-truth in the same breath.

**Read-side completeness is the fix's own second truncation surface.** The reviewer
holds `Read` and no `Bash`, so it cannot size the file first, and `Read`'s 2,000-line
default cap is independent of the 30,000-byte Bash threshold this plan targets. A diff
past 2,000 lines would be silently partially read — a scaled-down copy of the failure
the plan exists to close, inside the fix. M4 therefore requires paging with `offset`
until a read returns no further lines, and a stop-and-say-so when completeness cannot be
confirmed. An armed `deny-data-file-reads.sh` denies any `Read` over 5 MB (`:17-18`),
which surfaces as a clean stop under the same rule.

**The cross-file contract gets anchored-phrase pins, not a full-clause anchor pin.** The
two are different strengths, and the distinction matters here: a bare `DIFF_FILE:`
presence check misses step 4's relay instruction being gutted while the token survives
elsewhere in the section. Two decisions settle the shape:

- **`DIFF_FILE:` is a two-party token, not three.** The script emits it and `SKILL.md`
  step 4 parses it. The reviewer receives a bare path and never sees the token, so
  requiring it in the agent body would be an artificial pin on a string with no job
  there.
- **The pin is instruction-phrase presence, scoped to step 4's section, not a
  full-clause anchor.** This is the lighter primitive: a fourth anchor namespace would
  need its own regex, expected-set constant, pinned-clause dict, and two tests mirroring
  the `CACHE_RULE:` pair. `_PINNED_CACHE_CLAUSES` exists because the skip-vs-run reading
  of the cache clause is not derivable from its text (its own docstring says so); step
  4's relay is not ambiguous in that way, it is only deletable. Four scoped phrase
  asserts catch deletion, rename, command drop, and halt-clause removal.

Row 39 is corrected accordingly: `_MARKER_TRIPLE_SITES` +
`test_marker_triple_site_stays_unmigrated` is a per-file substring-presence check over
parametrized (skill, string) pairs, not a live cross-file comparison. It is cited as the
presence-check precedent it actually is.

**Structural-sibling audit — one instance, confirmed.** Four agents lack `Bash`:
`Explore` (`Explore.md:6`), `plan-architect` (`plan-architect.md:4`),
`comment-discipline-reviewer` (`:6`), `skill-fidelity-reviewer` (`:6`). Only the last is
handed a diff as pasted text. `comment-discipline-reviewer` already receives file paths
with line ranges (`code-review/SKILL.md:32`, the precedent this conforms to);
`plan-architect` already receives a plan path (`plan-it/SKILL.md:58`); `Explore` receives
no diff. `handoff/SKILL.md:125` pastes a bounded fixed-shape report.

**Question 2 — decided: the truncation does degrade step 3's own `/code-review` pass,
and it stays out of scope.** `code-review/SKILL.md:78` makes full-file reads the
checklist's primary evidence and `docs/cost-levers-considered.md:156` records that the
`Bash`-carrying specialists re-fetch, so this is not the jointly-unsatisfiable class
step 4's paste instruction is. But three parent-side checks lose their input above the
threshold: Step 0's changed-file determination (`:11`), Step 0.6's Change-type
enumeration (`:29`), and item 9d's literal suppression-token search (`:108`, which
explicitly forbids relying on read-through). Each is recoverable — the pass carries
`Bash` (`:4`) — but nothing tells the parent to recover it. Closing it means deciding
how `/code-review` obtains and searches its diff, the same decision the collapsed
single-shape design was rejected for. M5's dated follow-up gives it a durable home in
`docs/cost-levers-considered.md` next to the row a future session pricing diff handling
actually reads.

### Assumption ledger

**Root:** `ready-for-review/SKILL.md:96` requires the parent to relay diff *text* it
provably does not hold above 30,000 bytes, and `:69-81` materializes that text before
the cache check that may discard it — one instruction unsatisfiable by construction, one
cost paid for nothing.

**Givens** (fixed beyond this design's reach):

- **G1.** The Bash tool truncates output above 30,000 decimal bytes, returns a
  first-bytes-only 2,000-byte preview, and persists the remainder to a harness-internal
  `tool-results/` file. Harness-imposed; no artifact in this repo can raise, lower, or
  observe it.
- **G2.** `pr-diff-against-base.sh`'s stdout must stay byte-identical across invocation
  shapes. The merged `cumulative-review-marker-proof` design owns that contract — the
  write and read sides agree only because one recipe produces both — so relaxing it is a
  decision outside this plan.
- **G3.** The `<config-dir>/<kind>-markers/<repo-hash>.<session-id>` layout, its
  session-suffix concurrency rule, and `enforce-marker-script-shape.sh`'s `*-markers/`
  name-suffix protection are owned by the marker design and its hook. This plan conforms;
  changing it is that design's decision.

**Rows:**

1. `[engineer-verified]` Scope is exactly four items: the step-4 path handoff (new
   output-file flag + the step-4 recipe + agent contract), the step-3 cache-check
   reorder, the artifact's lifecycle (deterministic write + `deactivate` cleanup), and
   the dated note plus stale-citation fix on `docs/cost-levers-considered.md`.
2. `[engineer-verified]` The agent's stop-on-a-range-expression rule stays intact while
   the input contract widens to accept a path.
3. `[engineer-verified]` The cost-levers row gets a dated follow-up in that file's own
   precedent, not a rewrite.
4. `[verified: subagent-delegation/REFERENCES.md:9-13]` 30,000-byte threshold;
   2,000-byte head-only preview; overflow persisted to `tool-results/` and not auto-read.
5. `[verified: subagent-delegation/SKILL.md:95-96]` "Never re-run the command or `Read`
   the persisted file whole" — the overflow file is not a recovery route.
6. `[verified: ready-for-review/SKILL.md:96]` Step 4 today says to paste "the literal
   diff **output** step 3's `git diff` already produced." With rows 4–5 this is
   unsatisfiable above the threshold.
7. `[verified: skill-fidelity-reviewer.md:5, :6, :20]` `tools: Read, Grep, Glob, Write`
   — `Read` yes, `Bash` no. `:20` says "as literal text" and stops on "a range
   expression"; the frontmatter `description` says "the diff text", so both need
   widening or the always-loaded surface contradicts the body.
8. `[verified: claude/.claude/agents/*.md frontmatter sweep; code-review/SKILL.md:32;
   plan-it/SKILL.md:58; handoff/SKILL.md:125]` The paste-a-large-artifact-into-a-dispatch
   shape has exactly one live instance. Sibling audit closed.
9. `[verified: pr-diff-against-base.sh:51-54; _lib.sh:538-547;
   test_pr_diff_against_base.py:303-314]` Stdout is the hashed artifact and is already
   pinned byte-identical against `--record`. Anchors G2.
10. `[verified: pr-diff-against-base.sh:43, :56-59, :77-86]` The script already reports
    out-of-band on stderr, and `--record` is contracted never to withhold the diff or
    change the exit code. `--diff-file` inherits both properties.
11. `[verified: findings-path-suffix.sh:2-5; ready-for-review/SKILL.md:96, :111, :117]`
    The harness surfaces a script's stderr to the model, and this skill already consumes
    a named-token path line from stderr in step 5 (`BODY_FILE: <path>`). A named prefix
    is the established parse rule here, and it is needed because
    `pr-diff-against-base.sh:43` can emit a warning line ahead of it.
12. `[unverified — and no longer load-bearing]` Whether the harness truncates stdout and
    stderr as one chronological buffer, independently, or by concatenating whole streams.
    **Removed from the load path** rather than mitigated: the only invocation that emits
    `DIFF_FILE:` redirects stdout to `/dev/null`, so its whole tool result is at most two
    short stderr lines, far under G1's threshold under every model. Retained as the
    record that the question was asked and answered structurally. No local test can
    observe the model itself.
13. `[verified: marker.sh:401-404]` `write cumulative-review` `rm -f`s the subject on a
    successful write, so the subject file cannot be step 4's artifact — and on a cache
    hit no subject is written at all.
14. `[verified: ready-for-review/SKILL.md:69-81, :83-98, :95-96]` Step 3 prints before
    checking the cache. Step 4 runs on both cache branches, but `:95`'s
    empty-invocation-list branch returns without dispatching, so the artifact write
    belongs inside `:96`'s "Otherwise" branch — an empty list writes nothing.
15. ``[verified: check-skill-length.sh:71-72; `wc -l` = 198 as of the `reviewer-diff-handoff-by-path` → `main` sync that landed GH-849's skill-package split plus an unrelated context-budget-check bullet in this same file]`` The
    per-skill cap is 200 — two lines of headroom, down from the five available when this
    row was first verified; the sync consumed the other three. Every
    prescribed edit is in-paragraph or a whole-block relocation except one new bullet in
    step 4, so the expected post-edit count is **199**; the ≤199 gate below is the
    remaining safety margin, tighter than before but still short of the hard cap.
16. `[verified: test_skills.py:3594-3612]`
    `test_ready_for_review_step3_never_produces_a_staged_diff` asserts the literal
    `pr-diff-against-base.sh --record` inside step 3's section. The cache-miss command is
    unchanged from today, so this assertion passes untouched.
17. `[verified: test_skills.py:3410, :3444-3453]` `_PINNED_CACHE_CLAUSES` pins the
    `CACHE_RULE:ready-for-review-cumulative-diff-cache` block's exact normalized text;
    the reorder rewrites that block, so this constant is a required update.
    `_EXPECTED_CACHE_ANCHORS`, `_EXPECTED_SCOPE_ANCHORS`, and `_PINNED_SCOPE_CLAUSES` are
    unchanged — same anchor name, one pair, and the `SCOPE_RULE` text does not move.
18. `[verified: claude/.claude/settings.json — no matching entry]`
    `pr-diff-against-base.sh` has no `permissions.allow` rule (only
    `findings-path-suffix.sh` among the step-3/4 scripts does, at `:21`), so every
    invocation already prompts. Step 4's call adds one prompt per gate pass. No rule is
    added: a redirect-bearing command is an uncertain exact-match target, and a
    `settings.json` edit would pull `/review-permissions` into this pipeline for one
    prompt. M6 adds no `marker.sh` invocation shape, so `settings.json` stays out of the
    diff.
19. `[verified: docs/scripts.md:54, :58]` The script's doc entry enumerates its
    invocation shapes and states "Bare invocation is read-only"; it must gain
    `--diff-file` and name step 4 as a third call site. `marker.sh`'s entry states "The
    17 valid invocation shapes" — M6 adds none, so that count is unchanged.
20. `[verified: claude/.claude/rules/shell-script-conventions.md;
    pr-diff-against-base.sh:69-75]` `mktemp` with an explicit template, never a fixed
    path, and a SINGLE `trap … EXIT`. `--record` owns the script's one `EXIT` trap, and
    deliberately leaves it armed on its failure branch. `--diff-file` therefore installs
    none, and `rm -f`s its own temp inline on failure instead. A second `EXIT` trap
    would silently overwrite `--record`'s and leak its temp.
21. `[verified: ready-for-review/SKILL.md:111, :117; ciso-reviewer round-1 Answer 2]`
    Step 5's `BODY_FILE` handoff is the same *announcement* shape but **not** a precedent
    for lifetime or content sensitivity: a PR body is already destined for publication
    and lives one step-to-step handoff.
22. `[verified: deny-reviewer-tree-mutation.sh:9-10, :163; settings.json:199-340;
    deny-data-file-reads.sh:7-18, :97]` `deny-reviewer-tree-mutation.sh` is wired under
    the `Bash` and `Edit|Write|MultiEdit` matchers only, so a review-only agent's `Read`
    of a path outside the repo is ungated by it. The `Read`-matcher hooks match on
    extension, a `Downloads/` component, or >5 MB; a `<repo-hash>.<session-id>` file
    matches no extension rule, and the size arm applies only when the opt-in guard is
    armed.
23. `[verified: docs/worktree-bash-guard.md:21-27, :112-114]` The refused shapes are
    `$(...)`-assignment reuse, `$CLAUDE_CONFIG_DIR`, unquoted variable-built paths,
    standalone `$PPID`, and bare `$(git …)`. The new invocation is a single literal
    script call with none of these; its one `> /dev/null` redirect is not in the
    taxonomy.
24. `[verified: test_pr_diff_against_base.py:316-329; pr-diff-against-base.sh:53-54,
    :74]` `--record`'s subject deliberately omits stdout's trailing newline, for
    hash-recipe agreement. `--diff-file` has no hashing consumer, so its file is
    byte-identical to stdout *including* that newline — a deliberate divergence a
    copy-paste from `--record` would get wrong.
25. `[verified: pr-diff-against-base.sh:14-18]` Today's parser inspects `$1` only, has
    **no** unknown-argument check, and silently ignores anything else. Two flags need a
    small loop, and `exit 2` on an unknown argument is behavior M1 *introduces* — a
    mistyped flag that silently skipped the write would leave step 4 with no path and no
    signal.
26. `[verified: marker.sh:469-483; .claude/plans/cumulative-review-marker-proof.md row
    25]` `deactivate ready-for-review` already resolves `SESSION_ID` and `REPO_ROOT` and
    `rm -f`s the session's subject inside a guarded block that must not abort on a
    repo-root failure. The artifact's cleanup is one more `rm -f` in that block, reusing
    both resolutions. The arm's existing inline comment and its else-branch stderr
    message both name the subject alone, so after M6 a repo-root failure would report a
    one-artifact skip while skipping two.
27. `[verified: code-review/SKILL.md:4, :11, :29, :78, :108;
    docs/cost-levers-considered.md:156]` Question 2's evidence — full-file reads are the
    pass's primary evidence and the specialists re-fetch, but Step 0, Step 0.6, and item
    9d each read the diff and are degraded by a head-only preview, with no instruction
    anywhere telling the parent to recover them.
28. `[verified: docs/cost-levers-considered.md:104-113, :115-139, :166-170]` Three dated
    follow-ups already sit under their tables in bold `**<date> follow-up:**` form. That
    is the shape M5 reuses.
29. `[verified: docs/cost-levers-considered.md:157 vs ready-for-review/SKILL.md:96]` The
    row's `ready-for-review/SKILL.md:102` citation is stale; the instruction it names is
    at `:94` and this change removes it.
30. `[verified: docs/cost-levers-considered.md:149-151, :155, :157]`
    "Amplification-weighted" is defined in the sentence spanning `:149-151`: each
    result's tokens multiplied by the turns remaining in its session, since a tool
    result is re-sent on every subsequent turn. A subagent's context is not re-read by
    the parent, so a path
    handoff relocates the diff from an amplified context to an unamplified one — a
    different quantity from the duplication the row priced.
31. `[verified: ready-for-review/SKILL.md:17-18; pr-diff-against-base.sh:64-67]` The fix
    loop returns to step 2 and re-enters step 4, so the artifact is written more than
    once per gate run. A `<repo-hash>.<session-id>` name replaced by `mv` makes every
    re-entry supersede the previous one at the same path, so no stale path is reachable
    and step 4 needs no freshness qualifier. The session suffix keeps two sessions in the
    same worktree from overwriting each other's artifact — the same reason `:64-66`
    records for the subject.
32. `[verified: resume-context.sh:215-219; pr-diff-against-base.sh:69-74]` A
    same-filesystem `mv` gives the destination the *source's* mode. `--diff-file`'s
    source is itself `mktemp`-created, so 0600 survives; `resume-context.sh`'s documented
    pitfall is the foreign-source case. The property is load-bearing (a direct `>`
    redirect would be umask-dependent), so it gets an explicit test assertion.
33. ``[verified: _lib.sh:307-326; marker.sh:170, :569-611; repo-wide grep for
    `*-markers`]`` `_lib_marker_value_present` globs `<prefix>*` inside a directory its
    caller names explicitly, and every caller names a specific completion-markers
    directory. Nothing globs `$CONFIG_DIR/*-markers` or `$CONFIG_DIR/cumulative-review-*`.
    A sibling directory has no analogous exposure.
34. `[verified: marker.sh:502-551, :469-483; ready-for-review/SKILL.md:132-149, :195]`
    `clear-stale` walks only `"$CONFIG_DIR"/.*-active.d`, so it neither evicts nor reports
    the artifact. `deactivate ready-for-review` runs in step 8, after step 4 has consumed
    the path, and removes only this session's suffix — so it cannot delete another
    session's artifact and cannot fire mid-gate. The CI-watch "Land the fix" path
    re-activates and re-deactivates without running step 4.
35. `[verified: enforce-marker-script-shape.sh:210-222, :238-261; docs/hooks.md:89]`
    The `*-markers` suffix brings the new
    directory under the hook's existing `*/.claude/*-markers/*` patterns, so a
    no-gate-release agent's hand-`Write` there is denied with no hook change. The hook
    fires only on `Write|Edit|MultiEdit` and `Bash` and only for those agents, so neither
    the script's own write nor the main session's invocation is affected.
    `docs/hooks.md:89` currently names one non-marker directory the glob incidentally
    protects — singular, and now inaccurate.
36. `[verified: test_skills.py:4165]` `_PER_ACCOUNT_STATE_PATH_RE` treats a literal
    `~/.claude/<name>-markers/` mention in a skill body as a functional bug under a
    non-personal `CLAUDE_CONFIG_DIR`. The `SKILL.md` edits may not name the artifact
    directory with a `~/.claude/` prefix — and need not, since the script announces the
    resolved absolute path at runtime.
37. `[verified: ready-for-review/SKILL.md:28-31; _lib.sh:110-124, :1456-1462;
    pr-diff-against-base.sh:61]` Step 0 aborts the gate when `marker.sh activate` cannot
    resolve the session id, so a session reaching step 4 has already resolved it —
    `--diff-file`'s dependency on that resolution is pre-existing, not introduced.
    `_lib_config_dir` returns 1 for a relative `CLAUDE_CONFIG_DIR` (`:112-117`), which is
    the deterministic, privilege-independent way to exercise the failure branch, and
    `test_record_prints_before_any_record_path_failure`
    (`test_pr_diff_against_base.py:379-395`) already uses exactly that trigger against
    the same fully-test-controlled `env` dict.
38. `[verified: Read tool contract ("Reads up to 2000 lines by default");
    skill-fidelity-reviewer.md:6]` The reviewer holds no `Bash`, so it cannot size the
    file before reading. `Read`'s line cap is a second truncation surface independent of
    G1's byte threshold, and it is the fix's own failure mode if left unaddressed.
39. `[verified: test_skills.py:70, :73-75, :1951, :4080, :4204]`
    `_MARKER_TRIPLE_SITES` +
    `test_marker_triple_site_stays_unmigrated` is a **per-file substring-presence** check
    over parametrized (skill, literal) pairs (`assert expected_substring in
    _skill_body(skill_name)`); it never compares one literal across sites in a single
    test and says nothing about which sentence the literal sits in. It is the precedent
    for a presence check, not for cross-file literal agreement.
    `_PINNED_CACHE_CLAUSES` (`:3360-3387`) is the full-normalized-clause
    mechanism, and it is scoped to an HTML anchor namespace. `_AGENTS_DIR`/`_agent_body`
    (`:70`, `:73-75`) and the repo-root resolution (`:1951`) exist, so no new test-tree
    packaging is implied either way.
40. `[verified: test_pr_diff_against_base.py:59-65, :68-81, :84-89, :331-350, :379-395,
    :397-419, :465-478; test_marker_script.py:54-61, :64-79, :2480-2536]` The harness
    precedents the new tests reuse — and the two gaps in them. `_run_script`'s keyword
    flag, `_subject_path`'s independent hash recipe, `_seed_session`, the empty-diff
    fixture, the relative-`CLAUDE_CONFIG_DIR` trigger, the `chmod(0o555)` +
    `skipif(geteuid() == 0)` pattern, and the prints-before-failure pattern all transfer.
    In `test_marker_script.py`, `_record_subject` (`:64-79`) hardcodes
    `[str(PR_DIFF_SCRIPT), "--record"]` and `_cumulative_review_subject_path` (`:54-61`)
    is keyed to `cumulative-review-subject-markers`; **neither produces or locates the
    diff artifact**, so a straight mirror of the three cleanup cases seeds nothing and
    passes vacuously.
41. `[verified: skill-fidelity-reviewer.md:5 is 845 characters; test_agent_roster.py:67,
    :409]` The `description` cap is 1000 characters and the current line is 845, so M4's
    widening has 155 characters of headroom.
42. `[verified: pr-diff-against-base.sh:60-88]` All four `--record` failure messages —
    write/`mv` (`:77`), `mktemp` (`:80`), `mkdir` (`:83`), and outer resolution (`:86`) —
    end in the literal "subject not recorded," so
    `test_record_prints_before_session_resolution_failure`'s `assert "subject not
    recorded" in result.stderr` (`:477`) cannot identify which branch fired.
    `--diff-file`'s messages must be branch-distinguishable from the start, and the
    existing assertion is tightened so its mirror is not written blunt.
43. `[verified: pr-diff-against-base.sh:37; docs/scripts.md:58; marker.sh:601-604]`
    `marker.sh status`'s `cumulative-review` line runs the script bare through
    `_lib_cumulative_diff_hash`, paying its own `gh pr view`. Today both branches run the
    script twice per gate pass (`--record`, then `status`). After this change a cache hit
    runs it twice (`status`, `--diff-file`) — no increase — and a cache miss three times
    (`status`, `--record`, `--diff-file`) — one added round trip and one added permission
    prompt (row 18).
44. `[verified: skill-fidelity-reviewer.md:9, :114-115; ready-for-review/SKILL.md:17-18,
    :98]` Step 4's comparison is "did each invoked skill produce what its body specifies,
    in this diff" — it names no dependency on step 3's exact bytes — and the fix loop
    routes every step-3 and step-4 finding back through step 2, so step 4 is entered only
    after a clean step 3 on the current tree. Computing the diff at step 4 is therefore
    well-defined, and the artifact is independent of which cache branch step 3 took.
45. `[verified: pr-diff-against-base.sh:9, :56-59, :60-88]` The script runs under `set
    -euo pipefail`, and the comment at `:56-59` states that everything below the stdout
    `printf` cannot cost the caller the diff or change the exit code. Placing
    `--diff-file`'s block below that line as a sibling of `--record`'s makes that claim
    true of both flags, so the comment widens rather than being falsified — and
    `--record`'s block, its four messages, and its failure branches are edited by zero
    characters.
46. `[verified: marker.sh:469-483, :502-551; docs/scripts.md:58]` A gate run abandoned
    before step 8 never calls `deactivate`, and `clear-stale` structurally cannot reach
    either `cumulative-review-*-markers` directory, so both the diff artifact and the
    pre-existing subject persist until the same session id deactivates again — which may
    never happen. Currently undisclosed for the subject; `docs/scripts.md:58` is the
    entry a consumer reads for what this script leaves on disk, so one sentence there
    covers both.
47. `[verified: test_pr_diff_against_base.py:331-350]`
    `test_record_writes_zero_byte_subject_for_a_genuinely_empty_diff` exists precisely
    because the empty diff is where `--record`'s newline stripping is sharpest (0-byte
    subject against 1-byte stdout). Row 24 inverts that edge for `--diff-file`, so it
    needs its own empty-diff fixture — a bug special-casing "nothing to write" would
    otherwise ship undetected.
48. `[verified: pr-diff-against-base.sh:60-88; the step-3 and step-4 recipes below]` No
    prescribed recipe passes `--record` and `--diff-file` together, so the two blocks
    resolve independently rather than sharing a hoisted block. The combined shape stays
    legal (a `case` loop accepts it for free) and, on a resolution failure, emits **two**
    artifact-specific stderr messages — the more informative behavior, pinned by test
    rather than left to the implementation.
49. `[verified: pr-diff-against-base.sh:69-75]` `--record` stores its `mktemp` path in
    `TMP_FILE` and arms `trap 'rm -f "$TMP_FILE"' EXIT`, deliberately leaving that trap
    armed on its failure branch (row 20). A `--diff-file` block that reused the name would
    overwrite the trapped value, so `--record`'s leaked temp — a full private diff —
    survives exit in the combined shape. The variable name is therefore a correctness
    property, not a style choice, and "reuse the idiom" means the shape and not the name.
50. `[verified: pr-diff-against-base.sh:43, :77-86; row 25]` Every `--diff-file` failure
    the script can detect emits its own stderr line naming the cause. A missing
    `DIFF_FILE:` line with no accompanying stderr line is the one case the script cannot
    report on — an old script that never knew the flag (row 25's no-unknown-argument
    parser). Version skew is therefore the residual diagnosis, not the first one. A halt
    message leading with it would misname the commoner resolution and write failures,
    which the script already diagnosed correctly.
51. `[verified: ready-for-review/SKILL.md:85, :100]` Step 4's section already contains the
    word "halt" twice, in its own heading and in the "Halt on a silent-abbreviation
    finding" bullet, so a bare `halt` substring assertion passes independently of the new
    clause. Every phrase pin must be an exact literal chosen to appear nowhere else in the
    section.

**Mechanisms:**

**M1 — `pr-diff-against-base.sh --diff-file`: a block below the stdout `printf`, sibling
to `--record`'s, that resolves the config dir / repo hash / session id, writes the diff
to `<config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id>` via `mktemp` +
`mv` in that directory, and prints `DIFF_FILE: <path>` to stderr only after the `mv`
succeeds.** `anchors: root, row4, row5, row9, row10, row12, row13, row20, row24, row25,
row31, row32, row37, row45, row48` — the plan's only new capability, so it carries the
full over-powered check.

*Lighter primitives rejected.* (a) *A random-suffix `mktemp` under `${TMPDIR:-/tmp}`* —
strictly less mechanism (no session resolution, no `marker.sh` edit, no doc clause),
rejected because a non-deterministic name cannot deliver the property the design turns
on: every step-4 re-entry mints a new readable file, so a stale path is silently
reachable (row 31), nothing can clean it deterministically (row 26), and any local
process can write the path with no gate (row 35). (b) *Shell redirection at the call
site* (`> <path>`, zero script change) — the redirect target must be a literal path, and
the parent cannot compute `<repo-hash>.<session-id>` inline without the
`$(...)`-assignment and `$CLAUDE_CONFIG_DIR` shapes `docs/worktree-bash-guard.md:21-27`
refuses. (c) *A print-only flag that announces the path without rewriting the file*,
saving the miss branch's added `gh pr view` — rejected because emitting the line only
after a successful `mv` is what makes its presence *proof the file exists*; a
computed-path announcement would hand step 4 a path to a possibly-absent file and convert
a clean halt into a dead-path hunt. (d) *Hand over the `--record` subject path* — no new
capability at all, but the file is consumed by the marker write (row 13) and never exists
on the cache-hit branch. (e) *Hand over the harness's `tool-results/` overflow path* —
zero mechanism, but it exists only above the threshold (row 4), making the handoff
conditional on diff size, and it is harness-internal with no documented path contract.

*Heavier primitives rejected.* A new `marker.sh` subcommand (the marker-proof plan's own
M2(d) cost: `MARKER_SHAPE` alternation, an allow rule, a deny-message entry, header
count, two `docs/scripts.md` counts) — M6 uses an existing arm and pays none of it.
Making `marker.sh status` write the artifact so the miss branch avoids its added round
trip — turns a read-only report into a writer to save one `gh pr view`. A shared hoisted
resolution block above the stdout `printf` — it would edit `--record`'s untouched failure
path and falsify `:56-59`'s comment to deduplicate two lines (row 45). Delegating the
diff read to a subagent, which `docs/cost-levers-considered.md:155` already rejected
because the diff is the artifact under review. The write target stays *discovered* — from
the repo root, the config dir, and the session id — so `CLAUDE.md` §Safety's discovery
requirement holds and `--diff-file` takes no path argument.

**M2 — step 3 runs `marker.sh status` first; a cache hit skips `/code-review` and
computes nothing; a cache miss runs `pr-diff-against-base.sh --record` and `/code-review`
exactly as today.** `anchors: root, row9, row14, row16, row17, row36, row43` — a pure
reordering, satisfying the over-powered check by subtraction: a cache hit now runs
strictly less than today (no `--record`, no diff in context), and the cache-miss path is
byte-for-byte unchanged. No new invocation shape enters step 3. Lighter primitive
rejected: *keep the order and have the parent ignore the diff on a hit* — a Bash result
is in context the moment it returns; ignoring is not something the parent can do. Heavier
primitive rejected: *collapse step 3 to one invocation that hands `/code-review` a path
instead of the diff* — simpler mechanism, but it changes what `/code-review` receives and
forces a full `Read` on every miss, the open decision recorded in Out of scope.

**M3 — step 4 writes the artifact and hands over its path: one unconditional Bash call,
`~/.claude/scripts/pr-diff-against-base.sh --diff-file > /dev/null`, inside `:96`'s
"Otherwise" branch; the dispatch relays the announced path instead of pasted diff text; a
missing `DIFF_FILE:` line halts the step.** `anchors: root, row6, row11, row12, row14,
row15, row18, row23, row31, row36, row43, row44` — the mechanism that makes the path line
un-truncatable by construction. Lighter primitive rejected: *write the artifact from step
3's diff-producing call and re-run on a missing line* — it is one
fewer invocation, and it fails on three counts: it makes row 12's unverified truncation
model load-bearing, its trigger is an LLM judging an *absence* inside a possibly-truncated
transcript (the least reliable detection class this plan elsewhere refuses to rely on),
and its failure mode is a silently skipped review step rather than a loud stop. Lighter
primitive rejected: *no halt clause, let the reviewer's dead-path rule catch it* — M4's
rule fires on an unreadable path, not on a path that was never announced, so nothing
would stop the dispatch. Heavier primitive rejected: *a `HEAD`-unchanged staleness check
mirroring the CI-watch block (`SKILL.md:181-182`)* — that pattern exists because CI state
is asynchronous and observed minutes after launch; here the diff is computed at the
moment it is consumed, so there is no captured value to compare against and the check
would compare `HEAD` to itself.

**M4 — `skill-fidelity-reviewer.md`: the Input contract accepts a path to a diff file as
well as literal diff text, requires the read to reach end-of-file, preserves the
range-expression stop rule with a concrete example so a path is not mistaken for one, and
makes a dead or unreadable path a stop-and-say-so rather than a hunt. The frontmatter
`description`'s "the diff text" becomes "the diff (as a file path or literal text)".**
`anchors: row2, row7, row8, row22, row38, row41` — the minimum widening that makes M3's
handoff well-defined *and* complete. Lighter primitive rejected: *leave the agent
untouched and rely on inference* — its contract says "as literal text" and tells it to
stop on non-text input, so an unwidened agent handed a path either stops or improvises,
both silent; and nothing would make it page past 2,000 lines. Lighter primitive rejected
for the completeness half: *have the script announce a line count alongside the path* — a
second token to keep in sync, where "continue until a read returns no further lines" needs
no protocol at all. Heavier primitive rejected: *grant it `Bash` so it fetches its own
diff*, as the `staff-*` agents do — a strictly more privileged execution context to solve
a read-a-file problem, against the failure class `deny-reviewer-tree-mutation.sh` was
built for.

**M5 — `docs/cost-levers-considered.md`: a dated follow-up appended to the 2026-08-15
section, and the file-path-handoff row's stale line citation replaced with a heading
citation.** `anchors: row3, row6, row27, row28, row29, row30` — prose only. The follow-up
records that the row priced the parent-already-holds-the-text case and does not reach the
truncated case, that its "largest single call measured ~7,500 tokens" bounds the observed
corpus rather than reachable diffs, that the handoff above the threshold is a correctness
fix rather than a cost lever, and — one sentence, the deferred defect's durable home —
that step 4 now knows the untruncated artifact's path, leaving `/code-review`'s
own Step 0 / Step 0.6 / item 9d truncation degradation (row 27) closable by naming that
path rather than building anything. Lighter primitive rejected: *edit the row in place* —
`CLAUDE.md`'s Axis-3 preserved-content rule makes a dated measurement row read-only, and
the file's own three prior follow-ups (row 28) are the established shape. The citation is
repointed to `` `ready-for-review/SKILL.md` § "4. Skill-procedural-fidelity review (halt
on findings)" `` rather than to `:94`, per
`.claude/rules/skill-and-agent-self-review.md`'s citation convention: correcting one line
number to another this same PR invalidates would manufacture a second stale pointer.

**M6 — `marker.sh deactivate ready-for-review` removes this session's diff artifact
alongside the subject it already removes, and its comment and skip message name both.**
`anchors: row26, row33, row34, row35, row46` — one `rm -f` inside an existing guarded
block, reusing the `SESSION_ID` and `REPO_ROOT` that block already resolves. Lighter
primitive rejected: *no cleanup at all* — defensible only for a `/tmp` artifact whose
retention story turned out to be unfounded, and unnecessary here since the removal point
already exists. Lighter primitive rejected: *let the next run's overwrite bound it* —
that bounds the count at one but not the lifetime, leaving a full private diff resident
after the gate ends. Lighter primitive rejected for the message half: *add the second `rm
-f` and leave the arm's existing comment and else-branch text naming the subject alone* —
the operator-visible signal would then understate what a repo-root failure skipped, which
is precisely what that message exists to report. Heavier primitives rejected: a retention
sweep over `<config-dir>` artifact directories (a new mechanism the subject file does not
have either; `_lib.sh:335-339` already asks whoever adds marker retention to own this),
and extending `clear-stale` to non-active directories (it is defined as the
orphaned-active-marker evictor, and widening it would put deletion of review inputs behind
a command documented as safe to run at any time).

## Critical files

**One `code-writer` dispatch (`model: sonnet`), not split.** The script flag, the
`marker.sh` cleanup, the two `SKILL.md` recipes, and the agent contract all carry one
property — the artifact's path scheme, its lifecycle, and the exact `DIFF_FILE:` token —
so any split would have to restate M1's contract in the second prompt, which is `plan-it`
Step 5's named do-not-split signal. The step-4 phrase-pin test asserts agreement between
files a split would put in different dispatches, so it would fail until both landed. The
doc edits are genuinely disjoint but total roughly a dozen lines; they ride in the same
dispatch rather than paying a second agent's context. Verification command for the single
dispatch is the Verification section's `select-tests.py` line.

**Revert coupling — M1, M3, and M4 are one unit; M2 and M6 are one-directionally safe.**
M3 without M1 fails **loudly**: against today's parser
(`pr-diff-against-base.sh:14-18`, no unknown-argument check) `--diff-file` is silently
ignored, the whole diff goes into the `> /dev/null` redirect, and no `DIFF_FILE:` line is
produced — which is exactly M3's halt condition, so step 4 stops and names a skill/script
version mismatch instead of dispatching. That single property also closes the `git
pull`-lands-mid-gate deployment window: a consumer running new `SKILL.md` against an old
script gets a stop, not a silently skipped review. `exit 2` on an unknown argument is
behavior M1 introduces, so it cannot fire in a partial revert; the halt clause, not the
exit code, is what covers this. M3 without M4 hands the reviewer a path against a contract
that says "as literal text," producing improvisation rather than a clean stop. **M1 in
isolation is dormant** — no caller passes `--diff-file`, and the two live call sites
(`_lib_cumulative_diff_hash`'s bare invocation and step 3's `--record`) are unaffected by
a superset parser. **M4 in isolation is dormant** — step 4's prompt text is unchanged
without M3. **M2 stands alone** — the cache reorder is independently correct and touches
no flag. **M6 without M1** is a `rm -f` of a path nothing writes, a no-op. **The M1+M3+M4
unit shipped without M6** accumulates one file per (repo, session) with no removal point —
degraded, not broken. Those last two are different combinations from "M1 in isolation"
above; say so explicitly in the commit message, since a bisector reading "M1 without M6"
as "M1 alone" would take it to contradict the dormancy claim.

**Create:** none in the repo. `<config-dir>/cumulative-review-diff-markers/` is runtime
state, created by `mkdir -p` at write time.

**Modify:**

- `claude/.claude/scripts/pr-diff-against-base.sh` — replace the `$1`-only parse
  (`:14-18`) with a `while [ "$#" -gt 0 ]` case loop accepting `--record` and
  `--diff-file` in any order and together, `exit 2` with a stderr message on anything else
  (row 25), guarding `$#` before every `$1` reference under `set -u`. Widen the comment at
  `:56-59` so its first clause names both write paths instead of `--record` alone — its
  remaining sentences stay true and now cover both (row 45). Leave `--record`'s block
  (`:60-88`) unedited. Add `--diff-file`'s block **after** it and before `exit 0`: resolve
  repo root / config dir / session id in one guarded `if` (so `set -e` cannot abort),
  `mkdir -p` the artifact directory, `mktemp "$DIR/.$NAME.XXXXXX"` into a variable named
  distinctly from `--record`'s `TMP_FILE` (row 49), write `DIFF_TEXT` with
  the same trailing newline stdout carries (row 24 — do **not** copy `--record`'s `printf
  '%s'` stripping), `mv` into place, then print `DIFF_FILE: <path>` to stderr **only
  after** the `mv` succeeds. Give each failure branch a message that names `--diff-file`
  and its own cause, distinguishable from every other branch's (row 42); on failure `rm
  -f` the partial temp, print no path line, leave the exit code alone, and install **no**
  second `trap … EXIT` (row 20). Extend the top-of-file header (`:4-8`) to name both
  flags, each artifact's path, and that both are removed by `marker.sh deactivate
  ready-for-review`. Two one-line comments in the new block only: that the path line is
  emitted only after a successful `mv`, and why this file keeps stdout's trailing newline
  while `--record`'s subject does not. **Reuse:** the existing `DIFF_TEXT` capture,
  `--record`'s `mkdir`/`mktemp`/`mv` idiom and stderr-warning shape — its *shape*, never
  its `TMP_FILE` variable name (row 49) — `_lib_repo_root`,
  `_lib_config_dir`, `_lib_resolve_session_id`, `_marker_lib_repo_hash`.
- `claude/.claude/scripts/marker.sh` — M6. Inside `deactivate ready-for-review`'s existing
  `if REPO_ROOT=…` block (`:478-482`), compute the repo hash once into a variable and `rm
  -f` both the subject and
  `"$CONFIG_DIR/cumulative-review-diff-markers/$HASH.$SESSION_ID"`. Update the arm's
  pre-existing inline comment and the else-branch's "skipping cumulative-review subject
  cleanup" message to name both artifacts (row 26). No new subcommand, no `MARKER_SHAPE`
  change, no `settings.json` rule (row 18), no change to `docs/scripts.md`'s "17 valid
  invocation shapes" (row 19).
- `claude-skills/skills/ready-for-review/SKILL.md` — two independent edits.
  - **Step 3 (`:67-81`), M2:** move the `CACHE_RULE` block above the fenced command block
    so it comes first. Its rewritten text directs `marker.sh status` before anything is
    computed, and on `live` skips `/code-review`, reports the hit, and continues to step 4
    — computing no diff at all. Reword `:67`'s lead-in in place to match. The fenced block
    keeps `~/.claude/scripts/pr-diff-against-base.sh --record` verbatim (row 16) under the
    cache-miss branch. `SCOPE_RULE` and `:81`'s clean-pass/marker/`code-writer` prose are
    unchanged except for relocating the "On a cache miss," lead-in. Step 3 gains no
    reference to `--diff-file`.
  - **Step 4 (`:93-98`), M3:** split `:96` into two bullets. The first: run
    `~/.claude/scripts/pr-diff-against-base.sh --diff-file > /dev/null` **as its own Bash
    call** — it prints `DIFF_FILE: <path>` on stderr and no diff bytes on stdout; if no
    `DIFF_FILE:` line appears, halt and do not dispatch or fall back to pasted diff text.
    The halt report quotes the script's own stderr line when one is present — it names the
    actual cause (an unresolvable config dir, a failed `mkdir`, a failed `mv`) — and names
    a likely skill/script version mismatch only when no such line appeared, which is the
    old-script case (row 50). The second keeps the existing
    `findings-path-suffix.sh` sentence and the dispatch, with the paste clause replaced by:
    hand the reviewer the path that `DIFF_FILE:` line named, written out as literal text —
    never a range expression and never the command that produced it, since the agent has
    `Read` but no `Bash`. No freshness qualifier and no "first occurrence" rule (row 31).
    Do **not** write the artifact directory with a `~/.claude/` prefix anywhere in this
    file (row 36). Net growth is one line: expect 199 (row 15).
- `claude/.claude/agents/skill-fidelity-reviewer.md` — M4. `:20`'s Input-contract bullet
  and the `description:` phrase at `:5`. The bullet gains: a path is read with `Read`;
  continue with `offset` until a read returns no further lines, since a single `Read`
  returns at most 2,000 lines and a longer diff would otherwise be silently truncated; if
  the path is unreadable, or completeness cannot be confirmed, say so and stop rather than
  reviewing a partial diff. No `tools:`, `model:`, or `effort:` change. Row 41 measured 155
  characters of headroom against the 1000-character cap; re-measure after editing.
- `docs/scripts.md` — `:58`'s entry gains four things: `--diff-file`'s artifact path and
  stderr announcement; that stdout is unchanged; that `ready-for-review` step 4 is a third
  call site, so a cache-miss gate pass makes three `gh pr view` calls and a cache-hit pass
  two, as today (row 43); and one sentence naming the retention consequence for **both**
  artifacts — a gate run abandoned before step 8 leaves them until the same session id
  deactivates again, which may never happen, and no automated sweep reaches them (row 46).
  The both-artifacts wording is required for the sentence to be true, not scope creep onto
  the subject file.
- `docs/hooks.md` — `:89`'s clause naming the one non-marker directory the `*-markers/`
  glob incidentally protects must name both (row 35). One clause, inside the existing
  paragraph.
- `docs/cost-levers-considered.md` — M5. Appended after the 2026-08-16 note (`:166-170`),
  before the next `##` heading, plus the `:157` citation repoint.
- `claude/.claude/scripts/tests/test_pr_diff_against_base.py` — a `TestDiffFileFlag` class
  mirroring `TestRecordFlag` (`:281-329`), with `_run_script` gaining a `diff_file`
  parameter and a `_diff_artifact_path` helper mirroring `_subject_path` (`:68-81`) — same
  independent `hashlib.sha256` recipe, different directory constant. Cases:
  1. Stdout byte-identical bare vs. `--diff-file`, and `--record` vs. `--record
     --diff-file`. This conjunct alone passes against the *unmodified* script (row 25), so
     it never stands on its own.
  2. The teeth: an **unconditional** assertion that stderr carries a `DIFF_FILE:` line —
     never a conditional lookup that treats absence as nothing-to-check — that the path it
     names equals the path the test computes itself, that the file exists, and that its
     bytes equal the full stdout exactly, trailing newline included (row 24).
  3. Empty diff (no feature branch, at parity with base), mirroring
     `test_record_writes_zero_byte_subject_for_a_genuinely_empty_diff` (`:331-350`): the
     artifact is exactly `b"\n"` — one byte, not zero (row 47). A "nothing to write"
     special case would otherwise ship undetected.
  4. Re-run supersession: two runs with different diff content leave one file at one path
     holding the second run's bytes, and the directory holds exactly one entry. This is
     row 31's pin.
  5. Reversed order `--diff-file --record` produces identical stdout, artifact, and
     subject to `--record --diff-file`.
  6. Flag isolation: a bare `--diff-file`-only run writes the artifact and leaves the
     subject-markers directory absent or empty, and its stderr contains no subject-related
     text (row 48).
  7. Mode: the artifact is `0o600` after the `mv` (row 32).
  8. Resolution failure: a **relative** `CLAUDE_CONFIG_DIR` (`_lib.sh:112-117`, the
     trigger `:379-395` already uses) yields a `--diff-file`-specific stderr warning, no
     `DIFF_FILE:` line, the diff still on stdout, and an unchanged exit code —
     deterministic and privilege-independent, so no root-bypass skip is needed. Add the
     missing-session variant by mirroring `:465-478`, and use the existing `chmod(0o555)`
     + `skipif(os.geteuid() == 0)` pattern (`:397-419`) only for the `mkdir`/`mktemp`
     failure case.
  9. Combined-shape resolution failure: `--record --diff-file` under the same
     relative-`CLAUDE_CONFIG_DIR` trigger prints **two** messages, one naming each flag
     (row 48) — pinning the choice rather than leaving it to the implementation.
  10. `--record --diff-file` writes both artifacts, each with its own newline convention.
      Plus the trap-collision case (row 49): with `--record`'s write forced to fail while
      `--diff-file` then succeeds, no `.`-prefixed temp survives in either artifact
      directory after exit. A shared `TMP_FILE` name passes every other case here and
      fails only this one.
  11. Unknown argument: run from a non-repo `tmp_path` with no `_init_repo`, assert
      `returncode == 2`, empty stdout, and stderr containing only the parse error with no
      git-originated text — an exit-code-only assertion would not pin "before any git
      work."
  12. The file is complete and readable once the process exits — state it that way, not as
      "complete at announcement time," which `subprocess.run(capture_output=True)` cannot
      observe. **Do not** attempt to assert stream-interleaving order (row 12).

  Separately, tighten the existing `test_record_prints_before_session_resolution_failure`
  (`:465-478`) from `"subject not recorded"` to the resolution-specific fragment (`"could
  not resolve the repo root, config directory, or session id"`), since all four `--record`
  branches share that tail (row 42). This is an in-file precision fix on a file already in
  the diff, and it prevents case 8 from being written with the same blunt assertion.
- `claude/.claude/hooks/tests/test_marker_script.py` — mirror the three existing
  subject-cleanup cases (`:2480-2536`) for the diff artifact, **plus the two fixture
  helpers they need**, which do not exist (row 40): a `_write_diff_artifact(repo, home,
  extra_env)` running `[str(PR_DIFF_SCRIPT), "--diff-file"]` with stdout discarded
  (mirroring `_record_subject` at `:64-79`, which hardcodes `--record`), and a
  `_cumulative_review_diff_artifact_path(home, repo, session_id)` mirroring
  `_cumulative_review_subject_path` (`:54-61`) with the `cumulative-review-diff-markers`
  directory. Each of the three cases must `assert artifact_path.exists()` after seeding
  and before invoking `deactivate` — the same shape `:2487` and `:2510` already use.
  Without both helpers and that pre-assertion, all three pass vacuously against an
  unimplemented M6.
- `claude-skills/skills/tests/test_skills.py` — update
  `_PINNED_CACHE_CLAUSES[("ready-for-review",
  "CACHE_RULE:ready-for-review-cumulative-diff-cache")]` (`:3361-3369`) to the rewritten
  block. Add one test asserting the handoff contract, using the section-slicing
  `test_ready_for_review_step3_never_produces_a_staged_diff` (`:3506-3528`) already
  performs and `_agent_body` (`:73-75`):
  - Step 4's section contains all four of these **exact literals**, not paraphrases of
    them (row 51): `~/.claude/scripts/pr-diff-against-base.sh --diff-file > /dev/null`;
    `DIFF_FILE:`; `as its own Bash call`; and `skill/script version mismatch`. The last
    one is what anchors the halt assertion — a bare `halt` substring already matches this
    section's own heading (`4. Skill-procedural-fidelity review (halt on findings)`) and
    its existing "Halt on a silent-abbreviation finding" bullet, so it would pass whether
    or not the new clause exists.
  - Step 3's section contains **no** `--diff-file` occurrence — pinning that the
    cache-hit branch materializes nothing.
  - `pr-diff-against-base.sh`'s source contains the literal `DIFF_FILE:`.
  - `skill-fidelity-reviewer.md`'s body contains the path-accepting phrase and the paging
    phrase from M4.

  These are anchored instruction-phrase presence checks, not a full-clause anchor pin —
  the mechanism `_MARKER_TRIPLE_SITES` actually implements (row 39), chosen deliberately
  over adding a fourth anchor namespace. `_EXPECTED_CACHE_ANCHORS`,
  `_EXPECTED_SCOPE_ANCHORS`, and `_PINNED_SCOPE_CLAUSES` are unchanged.

**Not modified, each a deliberate result:** `claude/.claude/hooks/_lib.sh` (G2 — the
hashed-stdout path is untouched, so no `claude-hook-review`),
`claude/.claude/hooks/enforce-marker-script-shape.sh` (row 35 — the new directory is
covered by its existing glob, so the hook file stays out of the diff and out of the review
pipeline), `claude/.claude/settings.json` (row 18 — no allow rule is added, so
`/review-permissions` stays out of the pipeline),
`claude/.claude/scripts/findings-path-suffix.sh` (it stays in step 4; the artifact needs
neither its suffix nor its ignore-list append),
`claude-skills/skills/code-review/SKILL.md` (Out of scope item 3),
`claude-skills/skills/subagent-delegation/*`, `docs/design-decisions.md` (§50 is a dated
record and Axis-3 preserved content; the artifact's documentation homes are the script
header, `docs/scripts.md`, and `docs/hooks.md`),
`claude/.claude/hooks/tests/test_agent_roster.py` (no roster field changes).

## Verification

Per repo `CLAUDE.md` §Commands, scoped to the diff:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

The diff touches `claude/.claude/scripts/`, `claude-skills/skills/`, and
`claude/.claude/agents/`, so `select-tests.py` widens to the script, marker, and skill
suites — that is it working, not a reason to hand-widen to the full suite.

**The central property, and it must be written first:** `--diff-file` changes the
invocation's stdout by zero bytes *and* writes a file, at a path the test computes
independently, whose bytes equal that stdout. The first conjunct passes against the
unmodified script, because today's parser silently ignores unknown arguments — so the
`DIFF_FILE:` stderr assertion and the file comparison must be unconditional `assert`s. A
test that looks the line up with a generator expression and only checks content when the
line is present passes vacuously against both the unmodified script and any future silent
no-op regression.

**The empty-diff edge, second:** the artifact for a genuinely empty diff is exactly one
byte (`b"\n"`), not zero. This is where row 24's keep-the-newline divergence from
`--record` is sharpest, and the one fixture a `--record` copy-paste would get wrong.

**The supersession property, third:** two `--diff-file` runs leave one artifact at one
path holding the newer content, and the directory holds exactly one entry. This is what
replaces a prose freshness instruction; without it, a refactor back to a random suffix
reintroduces the stale-path defect with no test failing.

**The cleanup property, fourth:** after `marker.sh deactivate ready-for-review`, this
session's artifact is gone and another session's is not — each case asserting the artifact
**exists** after seeding, before `deactivate` runs. A cleanup test that never seeds proves
nothing.

**The handoff-contract property, fifth:** step 4's section carries the command, the token,
the own-Bash-call requirement, and the halt clause; step 3's section carries none of them.
This is the static backstop for the drift a live gate run papers over by inference.

**The line-budget check, run before staging:** `wc -l
claude-skills/skills/ready-for-review/SKILL.md` ≤ 199. The file is 198 lines as of this
plan's post-sync revision (row 15) and
every prescribed edit is in-paragraph or a whole-block relocation except step 4's one new
bullet, so the expected count is 199; a growth past 199 means an edit was structured
differently than planned. `check-skill-length.sh` enforces 200 at `git commit`, so a miss
there surfaces as a commit-time denial rather than a test failure — but at 198 lines
there is only one line of headroom left before that hard stop, so this check is the only
thing that catches an oversized edit before the commit-time gate does.

Review pipeline, in order: `/plan-review` on this plan; `/skill-review` on the
`ready-for-review/SKILL.md` diff (hook-enforced — `require-skill-review.sh` blocks the
commit without its marker); `/agent-review` on the `skill-fidelity-reviewer.md` diff
(dispatcher-invoked per `.claude/rules/review-pipeline-dispatch.md`, not hook-enforced);
`/code-review` on the staged diff; `/ready-for-review` before push. No
`claude-hook-review` (no hook file changes — row 35), no `/review-permissions` (row 18),
no `plugin-semver` (nothing under a plugin directory).

**This branch's own gate is an acceptance smoke check, not the contract's test.** Step 4
will run the new command and hand a real `DIFF_FILE:` path to a real reviewer; a missing
path line, a dead path, or a reviewer that stops on the input is a finding about this
change. But it exercises one cache branch per run and an LLM papers over a small wording
mismatch by inference, so the phrase pins above — not the live run — are what catch a
`DIFF_FILE:` → `DIFFFILE:` drift or a gutted relay instruction.

## Out of scope

- **An in-band path line plus a step-4 fallback re-run.** That shape writes the artifact
  from step 3's own diff-producing invocation, so the `DIFF_FILE:` line shares a tool
  result with an unbounded stdout diff, and a re-run recovers it when the line goes
  missing. Rejected on three counts: it makes row 12's unverified truncation model
  load-bearing where the chosen design removes it from the load path entirely; its
  trigger requires a model to notice an *absence* inside a possibly-truncated
  transcript, the least reliable detection class available; and its failure mode is a
  silently skipped review step. Guarding those needs a `HEAD`-unchanged staleness check
  and a terminal halt on an empty retry — a third and fourth conditional on a mechanism
  whose existence is itself the defect.
- **A shared hoisted resolution block above the stdout `printf`.** It would deduplicate
  two lines between `--record`'s and `--diff-file`'s blocks, at the cost of editing
  `--record`'s otherwise-untouched failure path and falsifying
  `pr-diff-against-base.sh:56-59`'s comment about what runs after the diff reaches stdout.
  The duplication is cheaper than either.
- **The truncation's effect on step 3's own `/code-review` pass — deferred with a durable
  home.** Decided per the Approach: it degrades (row 27). Above 30,000 bytes the parent's
  Step 0 changed-file determination, Step 0.6 Change-type enumeration, and item 9d
  suppression-token search all run against a 2 KB head, with nothing instructing the
  parent to recover them from `Bash`. Closing it means deciding how `/code-review` obtains
  and searches its diff — a change to a 500-line hook-gated skill. M5's dated follow-up
  puts it in `docs/cost-levers-considered.md` next to the file-path-handoff row. The
  handle is cheap and this plan creates it: step 4 now knows a complete untruncated diff
  file's path.
- **Collapsing step 3 to one invocation that hands `/code-review` a path.** The simpler
  mechanism, and it would close the item above — but it trades a cheap truncated diff for
  an expensive complete `Read`, which is precisely the whole-file read that exhausted a
  session's context in the incident behind this plan. Decide them together or not at all.
- **A `${TMPDIR:-/tmp}` artifact.** The lighter mechanism, rejected because it cannot
  supply a deterministic path: it reintroduces the stale-path defect, an unbounded file
  count, an unfounded retention story (`${TMPDIR:-/tmp}` resolves to the per-user
  `/var/folders/.../T/` on macOS, which the `periodic daily` cleaner does not cover), and
  a write target `enforce-marker-script-shape.sh` does not protect.
- **Colocating the artifact under `agent-reviews/`.** Rejected for the exclude-ordering,
  clean-tree, and file-shape couplings named in the Approach; recorded because it was the
  first consult's proposal.
- **A `permissions.allow` rule for `pr-diff-against-base.sh --diff-file`.** The added
  prompt (row 18, row 43) is accepted rather than allowlisted: a redirect-bearing command
  is an uncertain exact-match target, and a `settings.json` edit would pull
  `/review-permissions` into this pipeline for a single prompt. `pr-diff-against-base.sh`
  carries no allow rule today, so this keeps the script's existing posture rather than
  making an exception for one flag. The consequence is stated rather than discovered: step
  4's call is a second literal command string distinct from `--record`'s, so it prompts on
  **every** gate pass rather than only cache-miss ones, and an unattended run under
  autonomous shipping pauses there until approved. `[engineer-verified]` — raise it if the
  pause proves annoying in practice.
- **A retention sweep or `clear-stale` extension covering `<config-dir>` artifact
  directories.** `clear-stale` is defined as the orphaned-active-marker evictor
  (`marker.sh:502-551`) and widening it would put deletion of review inputs behind a
  command documented as safe to run anytime. Completion markers are unpruned today and
  `_lib.sh:335-339` already assigns retention to whoever adds it; both artifacts should be
  covered then, together. The *consequence* is not deferred with the mechanism —
  `docs/scripts.md` states it for both artifacts (row 46).
- **Granting `skill-fidelity-reviewer` (or `comment-discipline-reviewer`) `Bash` so it
  fetches its own diff.** A roster-level privilege change against the failure class
  `deny-reviewer-tree-mutation.sh` exists to close.
- **Naming the artifact directory in step 4 as a spoof defense.** With one deterministic
  path per session every `DIFF_FILE:` occurrence is the same string, and the reviewed
  spoof analysis could not construct a forged line from unified-diff content in the first
  place. Naming the directory in `SKILL.md` would also invite row 36's per-account-path
  trap for one clause of defense against an unconstructed attack.
- **`comment-discipline-reviewer`'s input contract.** Already path-based via
  `code-review/SKILL.md:32` (row 8). Named so a reviewer does not read its absence from
  the diff as a missed sibling.
- **Any change to the `cumulative-review` marker's value source, the `--record` subject's
  consume-on-write rule, or `marker.sh`'s invocation shapes.** The
  `cumulative-review-marker-proof` design just landed; this plan conforms to its layout
  (G3) and touches exactly one existing `deactivate` arm.
- **A `docs/design-decisions.md` entry.** The mechanism is one script flag plus one
  cleanup line, documented at three altitudes already (script header, `docs/scripts.md`,
  `docs/hooks.md`). Raise it if a second consumer ever reads the diff artifact, which is
  what would make the `DIFF_FILE:` contract a cross-file invariant rather than a
  single-producer/single-consumer handoff.
- **Reversing `docs/cost-levers-considered.md:157`'s verdict.** The dated note scopes what
  the row priced; it does not overturn the below-threshold pricing, and re-measuring that
  is not this plan's work.
