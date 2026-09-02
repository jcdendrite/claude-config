# Cache ready-for-review's cumulative code-review pass by diff content

## Context

`ready-for-review` step 3 runs a mandatory, fully-unnarrowed `/code-review`
pass over the entire PR-vs-base diff on every push to a branch with an open
PR — including a push that only rebases or merge-syncs onto a moved default
branch with zero conflicts and zero content change. The completion marker
that gates this (`~/.claude/scripts/marker.sh`'s `ready-for-review` kind) is
keyed on the exact `git rev-parse HEAD` SHA, not on diff content, so a
conflict-free rebase — which always produces a new HEAD SHA even when the
resulting diff is byte-identical — always re-arms the gate and forces a full
specialist-reviewer re-run from scratch.

This surfaced from a real session (2026-09-02) that rebased 42 commits onto
a moved default branch with zero conflicts, then had to re-ingest and
re-review the entire ~1,687-line cumulative diff, prompting direct user
pushback on the expense. A `transcript-analysis` pass across a 30-day,
~1,700-transcript, 6-account corpus (658 `ready-for-review` invocations)
found this exact pattern in 1 confirmed and 1 probable instance, plus ≥4
sessions where the agent already self-detected "byte-identical diff, skip
re-spawn" informally and skipped re-review by judgment call alone — proof
the optimization is already happening, just inconsistently, with no
persisted record backing the skip decision. Estimated impact is
$50–300/month against $21,762.57 total 30-day corpus spend (<1.5%) — real
but modest, per the toolkit's phase-level cost limitations (order-of-
magnitude, not a computed sum).

The intended outcome: when step 3's cumulative diff content is byte-identical
to a diff that already passed a full, unnarrowed cumulative review, skip
re-running `/code-review` and reuse that prior clean result — formalizing,
with a hard content-addressed check, the informal skip some sessions already
attempt by eyeballing the diff. `docs/design-decisions.md` §34's guarantee —
that when the cumulative pass *does* run, it is never narrowed — is
unaffected; this only changes whether the pass needs to run at all for
provably unchanged content.

## Approach

Add a fifth content-addressed completion-marker kind, `cumulative-review`, whose value is the sha256 of `pr-diff-against-base.sh`'s output, and give `ready-for-review` step 3 a fast path that skips the `/code-review` invocation when that hash already has a live marker for this repo. The marker is written by `marker.sh` at the end of a clean step-3 pass and read back through `marker.sh status`'s existing completion-marker report, so a rebase that leaves the cumulative diff byte-identical reuses the prior clean review instead of re-running it.

**Why `marker.sh` and not a standalone cache script.** `enforce-marker-script-shape.sh`'s Bash arm denies gate-releasing writes by matching the literal `marker\.sh[[:space:]]+(write|activate)` in the command text (line 541). A new script name does not match that pattern, so a no-gate-release agent could write a "this diff was reviewed" record that step 3 then honors — the exact forgery the hook's own denial guidance names ("Matching a hash you computed yourself is not authorization"). Living inside `marker.sh` inherits the whole authority control at no cost.

**Why the value is recomputed inside `marker.sh` and never passed as an argument.** A caller-supplied hash would let any caller assert that arbitrary content was reviewed. It is also barred structurally: `VALID_PATTERN` (line 582) forbids extra args after the skill name, so accepting one means loosening the shape gate itself. Recomputing matches the `code-review` arm exactly (line 275).

**Why the preimage is the diff bytes alone.** Folding the merge-base SHA or `BASE_REF` into the hash would make the cache miss in precisely its motivating case — a rebase onto a moved default branch always changes the merge-base. The residual is named in row 8 rather than engineered away: step 2's verification and CI both still run against the rebased tree on every pass, so the cache skips reviewer judgment over bytes nobody changed, not verification of the tree.

**Why the read path is `status` and not a new subcommand.** `status`'s own usage text already promises "every completion marker … for this repo," so omitting the new kind would make it stale regardless. Reusing it adds zero new invocation shapes on the read side.

**Alternatives set aside.** (1) SKILL.md prose only, no persisted record — this is the status quo failure mode, where sessions skip by eyeballing with nothing durable behind the decision; also engineer-declined this session. (2) A standalone cache script — see the authority gap above. (3) A new `marker.sh check <kind>` subcommand — adds an invocation shape to four files plus two test rosters to produce information `status` now already carries. (4) Storing a second value line in the existing `ready-for-review` marker — `_lib_marker_value_present` matches whole lines, so it would work mechanically, and that is the danger: `require-ready-for-review.sh:315` would then release the *push* gate on a diff-hash match, converting a review-reuse optimization into a HEAD-gate bypass.

### Assumption ledger

**Root:** `ready-for-review` step 3 re-runs a full unnarrowed cumulative `/code-review` after any HEAD change, including a conflict-free rebase that leaves the reviewed diff byte-identical, because the only gating marker is keyed on the HEAD SHA.

**Givens** (fixed conditions outside this design's reach):

- **G1.** `check-skill-length.sh` caps `claude/.claude/skills/ready-for-review/SKILL.md` at 200 lines (`limit_for()`, lines 71–81) and denies a commit when a staged SKILL.md is over its limit *and* grew vs HEAD. Raising the cap is a decision about a shared enforcement hook whose override list has a stated content class (structural-dispatcher routing tables) that `ready-for-review` does not fit — not this plan's to make.
- **G2.** Completion markers are never pruned; `clear-stale` touches only active-bypass markers (`marker.sh:430-467`). Changing that is a lifecycle change to the whole marker subsystem, shared by four existing kinds.
- **G3.** `marker.sh` resolves the session id through the SessionStart-hook ancestor walk and refuses a main-tree write under worktree enforcement (`_resolve_session_id`, `_refuse_main_tree_under_enforcement`). The new arm inherits both unchanged; they are `_lib.sh`/harness-owned.
- **G4.** `pr-diff-against-base.sh` shells out to `gh pr view` and caps nothing. The vendor CLI's latency is not this plan's to bound inside that script.

**Rows:**

1. `marker.sh`'s `write` argument is a hardcoded `case` enum, not a free-form string, so a new kind needs a script edit — `[verified: claude/.claude/scripts/marker.sh:266-349]`.
2. Adding a `write` kind requires updating `enforce-marker-script-shape.sh`'s `MARKER_SHAPE` regex (line 576), its deny-message shape list (lines 636–653), its "18 single-command shapes" header count (line 71), and a matching `permissions.allow` entry — `[verified: claude/.claude/hooks/enforce-marker-script-shape.sh:576,636-653; claude/.claude/settings.json:4-19]`.
3. `marker.sh` runs under `set -u` only — no `-e`, no `pipefail` (line 10). A failed `pr-diff-against-base.sh` piped into `sha256sum` yields the empty-input digest at exit 0, so the existing `[ -n "$MARKER_VALUE" ]` guard shape cannot catch it — `[verified: claude/.claude/scripts/marker.sh:10,275-276]`.
4. Command substitution strips trailing newlines, so a write side that pipes and a read side that captures produce different digests for identical content; both sides must call one shared helper, as `plan-review` already does via `_lib_active_plan_hash` — `[verified: claude/.claude/scripts/marker.sh:326,506]`.
5. The write-roster convention tests parametrize over `WRITE_SKILLS` using the remote-less `git_repo` fixture, and `test_write_roster_is_closed_and_self_reported` asserts the advertised roster equals `WRITE_SKILLS` exactly. Adding the kind therefore breaks all three unless the fixture gains an `origin` default-branch ref producing a non-empty cumulative diff — `[verified: claude/.claude/hooks/tests/conftest.py:245-257; claude/.claude/hooks/tests/test_marker_script.py:601-689]`.
6. `TILDE_MARKER_SHAPES` drives both hook-acceptance and `permissions.allow` alignment, so hook and settings cannot be updated independently — `[verified: claude/.claude/hooks/tests/test_enforce_marker_script_shape.py:28-47,1703-1719]`.
7. `require-ready-for-review.sh` reads only `ready-for-review-markers` against `git rev-parse HEAD`; nothing in it reads the cumulative diff. Step 8 is therefore unaffected and still writes for the current HEAD whether step 3 ran fresh or hit the cache — `[verified: claude/.claude/hooks/require-ready-for-review.sh:307-317]`.
8. A byte-identical diff rebased onto a moved default branch is not strictly the same review object: a reviewer's ripple and causal-reach judgment can depend on code outside the diff, and a clean rebase surfaces textual conflicts only, not semantic ones. Frequency and severity of a review finding that would flip on the moved base alone is `[unverified]`; the compensating controls are `[verified: claude/.claude/skills/ready-for-review/SKILL.md:43-63]` — step 2's verification runs against the rebased tree on every pass and is not cached, and CI runs on every push.
9. `claude/.claude/skills/ready-for-review/SKILL.md` is exactly 200 lines against a 200-line cap, so the step-3 edit has zero headroom and must be net-zero or net-negative — `[verified: Read of the file; claude/.claude/hooks/check-skill-length.sh:71-81]`.
10. `_EXPECTED_SCOPE_ANCHORS` is an exact set covering only the `SCOPE_RULE:`/`SCOPE_EXEMPT_ROW` namespaces, and `_extract_scope_anchor_region` is already parameterized over the marker name, so a new anchor namespace reuses the parser but needs its own registry entry to be enforced at all — `[verified: claude/.claude/skills/tests/test_skills.py:2263-2299]`.
11. `docs/scripts.md` pins the shape count as "The 16 valid invocation shapes" and must move to 17 — `[verified: docs/scripts.md:54]`.
12. The engineer chose the formal content-addressed marker over prose-only formalization and over declining, having weighed the measured impact (<1.5% of 30-day corpus spend; 1 confirmed and 1 probable instance) against build cost — `[engineer-verified]`.
13. Scope is `ready-for-review` step 3 only; §34's narrowing mechanism and `/code-review`'s ad-hoc and presentation paths are untouched — `[engineer-verified]`.

**Mechanisms:**

- **M1 — `cumulative-review` write arm in `marker.sh`, guarded against a failed, empty, or hung diff.** Capture `pr-diff-against-base.sh`'s output and exit status separately under `_lib_capped`, mirroring M3's read-side treatment — `pr-diff-against-base.sh` shells out to `gh pr view` with no timeout of its own (`claude/.claude/scripts/pr-diff-against-base.sh:24`), so an uncapped write arm can block step 3 indefinitely on a slow or unresponsive call. Abort with exit 2 on nonzero exit, empty output, or timeout, then hash. *anchors: row3* — the empty-input digest is a silently-valid marker for a diff that never resolved, and an uncapped hang is a liveness gap the same guard must also close.
- **M2 — one `_lib_cumulative_diff_hash` helper in `_lib.sh`, called by both the write arm and the `status` arm.** *anchors: row4* — a duplicated recipe would drift on the trailing-newline boundary and never match.
- **M3 — `status` gains a `cumulative-review` line, computed under `_lib_capped` and degrading to empty (reported "absent") on failure or timeout.** *anchors: G4* — `status` is a report, not a write, matching how the `plan-review` arm already degrades at `marker.sh:506`.
- **M4 — shape-hook regex, deny-message list, header count, and `permissions.allow` updated together for `write cumulative-review` only.** *anchors: row2, row6* — the new kind is deliberately left out of `VALID_CHAINED_COMMIT_PATTERN`, since it is never written before a commit.
- **M5 — step 3 gains a cache pre-check plus an anchored fast-path rule, paid for by compressing existing step-3 prose.** *anchors: row9* — the length hook denies the commit outright on any net growth. The existing line 86 ("Unskippable — markdown, skill, and config diffs benefit from the same pass.") must be reworded or folded into the `CACHE_RULE` block itself — as written it flatly contradicts the new cache-hit skip path, and an implementer following both literally produces a self-contradictory SKILL.md.
- **M6 — the skip is reported in `ready-for-review`'s own step-3 output and Completion summary, not in `/code-review`'s `Spawn decisions:` line.** *anchors: root* — on a cache hit `/code-review` is never invoked, so there is no `Spawn decisions:` line to carry a third state, and `code-review/SKILL.md` needs no edit.
- **M7 — new `docs/design-decisions.md` §42 referencing §34's named residual, plus a clause added to §2's live enumeration of marker kinds.** *anchors: root* — §2 line 15 describes current behavior rather than recording an event, so leaving it out would make it stale; §34 itself is a historical record and is not edited.
- **M8 — write the marker at the end of step 3, not at step 8.** *anchors: row7* — the claim is "this diff content passed a full unnarrowed cumulative review," true the moment step 3 is clean; a later step-4 fix commit changes the cumulative diff and self-invalidates the marker with no bookkeeping.
- **M9 — no TTL and no pruning for the new kind.** *anchors: G2* — a stale content match is already reachable for `code-review` today (review criteria can change while a diff hash stays valid) and is accepted; this adds no new class.

## Critical files

Two sequenced `code-writer` dispatches. Phase 2 must follow Phase 1 because the SKILL.md recipe names the exact command Phase 1 makes valid, and both would otherwise edit overlapping test expectations.

**Phase 1 — marker plumbing and its gates** (verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`)

- `claude/.claude/hooks/_lib.sh` — add `_lib_cumulative_diff_hash`. **Reuse:** model it on `_lib_active_plan_hash`'s shared-recipe role and its abort contract; `_marker_lib_repo_hash` and `_lib_marker_value_present` are used unchanged.
- `claude/.claude/scripts/marker.sh` — new `write cumulative-review` arm (after line 344), new `status` reporting line (after line 515), `usage()` roster and valid-combination lines (lines 26–37). **Reuse:** `_resolve_session_id`, `_resolve_repo_root`, `_marker_lib_repo_hash`, `_status_report_completion_marker`, `_lib_capped`. Do **not** call `_guard_staged_vs_unstaged` — the cumulative diff is not the staged diff. Do **not** call `_status_reconciliation_flag` — it is documented as applying only to pathspec-hash markers (`marker.sh:176-180`).
- `claude/.claude/hooks/enforce-marker-script-shape.sh` — `MARKER_SHAPE` write alternation (line 576), deny-message shape list (lines 636–653), header shape count (line 71). Leave `VALID_CHAINED_COMMIT_PATTERN` (line 607) alone.
- `claude/.claude/settings.json` — one `permissions.allow` entry, `Bash(~/.claude/scripts/marker.sh write cumulative-review)`.
- `claude/.claude/hooks/tests/conftest.py` — arm `git_repo` (or add a sibling fixture) with `refs/remotes/origin/<default>` pointing at the initial commit plus `refs/remotes/origin/HEAD`, and a committed second change so the cumulative diff is non-empty. **Reuse:** `claude/.claude/scripts/tests/test_pr_diff_against_base.py`'s `_gh_shim_source`/`_env_with_gh_shim` PATH-shim pattern — the new arm must not make a real network call in tests.
- `claude/.claude/hooks/tests/test_marker_script.py` — add `cumulative-review` to `WRITE_SKILLS` (line 601); new cases for the abort-on-failed-diff and abort-on-empty-diff guards, for `status` live/historical/absent, and for a write/read round trip proving the two hash recipes agree.
- `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py` — add the shape to `TILDE_MARKER_SHAPES` (line 28) and update its count comment.

**Phase 2 — skill, anchor pin, and docs** (verification: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`)

- `claude/.claude/skills/ready-for-review/SKILL.md` — step 3 (lines 65–86) gains the `marker.sh status` pre-check, the skip rule inside a `<!-- CACHE_RULE:ready-for-review-cumulative-diff-cache start/end -->` anchor pair, and the marker write on a clean pass; the Completion section's Code review bullet (line 169) gains a cache-hit phrasing. **Hard constraint: the file is exactly 200 lines against a 200-line cap, so the edit must be net-zero or net-negative** — compress the lines 79–84 paragraph to pay for the addition. Do not reuse the `SCOPE_RULE:` prefix (its exact set is pinned) or `DISPOSITION_RULE:` (hardcoded in `evals/run_skill_evals.py`). Leave every `HOOK_TEST_FIXTURE` block byte-identical; the hook-alignment suite re-reads them.
- `claude/.claude/skills/tests/test_skills.py` — a `CACHE_RULE:`-namespace exact-set registry and exact-text pin mirroring `_EXPECTED_SCOPE_ANCHORS` (line 2269). **Reuse:** `_extract_scope_anchor_region` (line 2279) unchanged; it is already parameterized over the marker name.
- `docs/design-decisions.md` — new §42 citing §34's "Named residual" as the precedent shape, plus one clause added to §2's marker-kind enumeration (line 15). Do not edit §34.
- `docs/scripts.md` — line 54, "16 valid invocation shapes" → 17.
- `docs/hooks.md` — the "Marker keying and gate-release authority" section (line 75) and, if it enumerates gated kinds, the `enforce-marker-script-shape.sh` bullet.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

The diff spans `claude/.claude/scripts/`, `claude/.claude/hooks/`, `claude/.claude/skills/`, `claude/.claude/settings.json`, and `docs/`, so `select-tests.py` will widen substantially on its own — that is the rule table working, not a reason to invoke the full suite by hand. Watch specifically for `test_doc_counts.py`, which pins doc-side counts and may fail on the `docs/scripts.md` shape count if it is not updated in the same commit.

Three review dispatches are required rather than optional:

- `/skill-review` on the `ready-for-review/SKILL.md` diff — hook-enforced by `require-skill-review.sh`, and the compression in M5 needs an explicit behavioral-equivalence audit.
- `/review-permissions` on the `permissions.allow` addition — `code-review` checklist item 35 requires it whenever that key changes.
- `claude-hook-review:claude-hook-review` on the `enforce-marker-script-shape.sh` diff.

One manual end-to-end check no unit test covers: in a scratch worktree with an open PR, run step 3 to completion, note the marker, rebase onto a moved default branch with no conflicts, and confirm `marker.sh status` still reports `cumulative-review: live` while `ready-for-review: historical`.

## Out of scope

- §34's responsibility-narrowing mechanism and `code-review/SKILL.md` in any form — this is a pre-check in front of step 3's invocation, not a change to what that invocation does when it runs.
- `/code-review`'s ad-hoc and presentation paths, and its own staged-diff marker.
- `require-ready-for-review.sh` and the push gate's HEAD-SHA keying, verified independent at row 7.
- Marker pruning, TTL, or staleness handling for any kind (G2). The unbounded-growth property is pre-existing and shared.
- Folding the merge-base or `BASE_REF` into the hash preimage. Deliberately declined; the base-change residual is recorded at row 8 with its compensating controls, not closed here.
- Raising `ready-for-review/SKILL.md`'s length cap or adding it to `check-skill-length.sh`'s override list. If compression cannot pay for the step-3 addition without losing meaning, stop and raise it rather than editing the shared cap — the override list's existing entries each carry a stated content-class rationale that `ready-for-review` does not currently fit.
- `clear-stale`'s two `ALLOWLIST_EXCEPTIONS` and the unfiled scoping follow-up they reference.
- §34's own named residual (two `/code-review` runs against the same staged state within one round). Different case, still open.
