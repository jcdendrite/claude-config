# Investigate deny-private-project-refs.sh UUID-detection gap (PR #736 deferred finding)

## Context

Investigate a `ciso-reviewer` finding deferred from PR #736: whether
`deny-private-project-refs.sh` failed to block a commit that reproduced the
leaked UUID from `.claude/plans/identify-terminal-by-pid.md` (originally
merged unredacted in `d626ee0c`, PR #732) despite that UUID matching the
hook's own long-hex-identifier structural regex, and add the regression-test
fixture the finding requested. The finding's own rationale flagged it as
unresolved on two branches — "was the commit made outside the harness's Bash
gate, or is there a scan-path gap?" — and asked for investigation before any
fix, since the hook itself was outside PR #736's changed-file scope.

## Approach

The hook has no detection bug: it never scanned the leaked content because no
gated command (`git commit` / `gh pr create|edit` / `gh api`) was ever
invoked with that content staged. The premise in the deferred finding — that
a commit occurred and wasn't blocked — is not supported by the evidence
gathered this session. Separately, a real test-coverage gap was found and
independently confirmed: all six structural detectors are tested against
commit-*message* content but not against staged-*file* content, unlike the
tracker-ID detector, which has both. That gap is fixed with one parametrized
test covering all six detectors; the hook's production code is untouched.

**Investigation, in order:**
1. Read the PR #736 body's own "Deferred review findings" section and prior
   commit trailers — it states a draft of `redact-terminal-plan-path-leak.md`
   "was found to reproduce the leaked path and project name in cleartext"
   during a review round, before the finding names a specific blocked
   commit.
2. Exhaustively searched this repo's local git object database — every
   loose and packed object, reachable or not, via
   `git cat-file --batch-all-objects` — for the leaked UUID string. Found
   exactly one matching blob. Verified via `git hash-object` against
   `git show d626ee0c:.claude/plans/identify-terminal-by-pid.md` that this
   blob is byte-identical to the already-known, already-on-`main` leaked
   file version from `d626ee0c` — not a separate or later commit. No blob
   anywhere in the object database matches a leaked draft of
   `redact-terminal-plan-path-leak.md`.
3. Read `deny-private-project-refs.sh` in full. Its git-commit gate scans
   `git diff --cached` (added lines) plus the command string, and fires only
   when a `git commit`-shaped Bash invocation actually runs — this is a
   `PreToolUse` hook on the `Bash` matcher, not on `Write`/`Edit`. The
   header comment documents this as deliberate scope, not an oversight.
4. Searched every declared account's local transcript store
   (`~/.claude/transcript-config-dirs`) for the authoring session
   (`session_01VBsCe2LB7R9TTkGxwzxdL7`, from the commit trailers) to
   directly confirm whether a gated command was attempted. No transcript was
   found in any account root — likely rotated out.

Given (2) and (3), the most consistent explanation is that a `/plan-review`
or `/code-review` round caught the leaked draft by reading file content
directly — before the file was ever staged (`git add` also writes a blob,
and none exists) or committed. That path is entirely outside this hook's
designed scan surface. Item (4) is a residual gap this investigation cannot
close: a purely local, never-pushed commit made and then reset or amended
away within the original session, before anything reached this clone, would
leave no trace here either. That possibility is disclosed, not ruled out.

**Alternatives considered:**
- *Extend the hook to scan `Write`/`Edit` tool calls too* — rejected. This
  would make every file write in the repo a gated surface, a materially more
  invasive mechanism than the task needs (CLAUDE.md's over-powered-primitive
  check), and no confirmed bug justifies it — draft content getting caught
  by review before commit is the existing, working safety net, not a gap in
  this hook.
- *Rewrite git history to purge the `d626ee0c` blob* — rejected, out of
  proportion to a detection-gap investigation and already ruled out by the
  original `redact-terminal-plan-path-leak.md` plan's own scope decision.
- *Close the finding with no test change* — rejected. The finding explicitly
  asked for a regression-test fixture regardless of root-cause outcome, and
  a real coverage asymmetry across all six structural detectors was
  independently confirmed (below).
- *Add only the long-hex-identifier staged-diff case the finding named* —
  rejected during plan-review. `ciso-reviewer` and `staff-sdet` independently
  confirmed the same asymmetry exists identically for all six structural
  detectors, sharing one code path (`ADDED_LINES` feeding the combined
  `STRUCTURAL_DETECTORS` scan); per CLAUDE.md's "audit structural siblings
  before scoping a fix narrowly," the fix applies to every affected site.

### Assumption ledger

**Root problem:** determine whether `deny-private-project-refs.sh` has a
real detection gap that let a UUID-shaped leak through a gated command, per
the PR #736 deferred finding, and add regression coverage regardless of the
answer.

- **Givens:**
  - The harness's `PreToolUse` hook-matcher contract gates specific tool
    invocations (e.g. `Bash`), not a blanket "any file write" surface —
    catching pre-commit draft content the way this hook catches staged
    diffs would mean gating `Write`/`Edit` itself, a harness-contract-level
    primitive this plan cannot reach from inside a `.sh` hook script. This is
    why "the hook should have caught the draft before it was ever staged" is
    not an available design, only "the hook correctly didn't see it."

| # | Item | Tag |
|---|------|-----|
| 1 | The leaked UUID exists in exactly one object in this repo's local git database: the already-known, already-reachable `d626ee0c` blob. No separate committed or staged draft of `redact-terminal-plan-path-leak.md` contains it. | `[verified: exhaustive git cat-file --batch-all-objects search across all 5,382 blobs this session]` |
| 2 | The hook's git-commit gate (`STAGED_DIFF` via `git diff --cached`, plus `COMMAND`) only scans content when a `git commit`-shaped Bash call actually runs; it does not hook `Write`/`Edit`. This is documented, deliberate scope. | `[verified: claude/.claude/hooks/deny-private-project-refs.sh header comment and code, read this session]` |
| 3 | `_LIB_LONG_HEX_IDENTIFIER_REGEX` matches the leaked UUID's shape and is combined into the same fastpath pattern that scans staged-diff added lines — the detection logic itself is correct for this shape. | `[verified: claude/.claude/hooks/_lib.sh:1339 regex checked against the shape this session]` |
| 4 | All six structural detectors (IPv4, SSH key path, home-rooted path, long hex, internal hostname, Slack channel) share one code path for staged-file content — `ADDED_LINES` from `git diff --cached` feeds the same combined `STRUCTURAL_DETECTORS` scan as `COMMAND` (hook lines 454–461, 623–669) — yet every existing structural test uses commit-*message* content only; none stages a file. This is a shared-loop asymmetry across all six detectors, not one specific to long-hex, matching CLAUDE.md's "audit structural siblings before scoping a fix narrowly." | `[verified: grep across claude/.claude/hooks/tests/test_deny_private_project_refs.py this session, confirmed by independent ciso-reviewer and staff-sdet plan-review passes]` |
| 5 | Whether a `git commit` Bash call was ever actually attempted with the leaked draft staged, and if so what the hook returned, is not directly confirmable — the authoring session's transcript is absent from every declared local account root. | `[unverified]` |

## Critical files

- `claude/.claude/hooks/tests/test_deny_private_project_refs.py` — add one
  `@pytest.mark.parametrize`-driven test method (the file already uses this
  decorator 16 times, e.g. `test_placeholder_prefix_substring_still_denied`
  at line 178) covering all six structural detectors' staged-file-content
  path, not only long-hex — per the assumption-ledger row 4 sibling-audit
  finding. One parametrize case per detector, reusing each detector's
  existing "denied" message string verbatim as the staged file content
  instead (do not retype it — copy the exact string from the cited line, so
  the new case exercises the identical structural match with the content
  moved from `-m` to a staged file):
  - IPv4: the message string in `test_structural_ipv4_literal_denied`, line 1751
  - SSH key path: the message string in `test_structural_ssh_key_dot_ssh_path_denied`, line 1878
  - Home-rooted path: the message string in `test_structural_home_rooted_path_denied`, line 2002
  - Long hex/UUID: a synthetic UUID distinct from both the real leaked one
    and the existing message-based test's placeholder at line 2036 —
    generate a fresh random-looking UUID at implementation time rather than
    reusing either
  - Internal hostname: the message string in `test_structural_internal_hostname_denied`, line 2078
  - Slack channel: the message string in `test_structural_slack_channel_shape_denied`, line 2215

  (This plan deliberately does not reproduce those literal strings here —
  every one of them matches its own detector's structural regex, so
  quoting them inline would itself trip `deny-private-project-refs.sh` on
  this plan's own commit. Read them from the cited lines at implementation
  time instead.)

  For each case: write the payload to a file, `git add` it, then assert
  `git commit -m 'Generic refactor'` denies — mirroring
  `test_tracker_id_in_staged_diff_denied`'s exact pattern (line 211). The
  commit message **must stay `'Generic refactor'` verbatim** (no detector
  payload in `-m`) — `SCAN_TARGET` concatenates `ADDED_LINES` and `COMMAND`
  together, so putting the payload in both would let the test pass via the
  `COMMAND` path alone and silently fail to exercise the staged-diff path
  this test exists to cover. No production code changes — ledger row 3
  already confirms the scan path covers this case for every detector.

## Verification

- Before committing, temporarily comment out the `SCAN_TARGET+=$'\n'"$ADDED_LINES"`
  line (`deny-private-project-refs.sh:461`) in a scratch copy of the hook
  and confirm all six new parametrize cases fail — proves the test suite
  actually exercises the staged-diff path rather than passing vacuously via
  `COMMAND`. Discard the scratch copy; this is a one-time proof, not a
  committed artifact.
- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_deny_private_project_refs.py -k in_staged_diff -n0` — confirms all new cases (plus the existing tracker-ID one) pass against the unmodified hook.
- `../../../.venv/bin/pytest claude/.claude/hooks/` — full hook suite, no collateral breakage.
- `/code-review`, with `ciso-reviewer` dispatched given this touches the
  redaction hook's test surface for the detector the finding named.

## Out of scope

- Any change to `deny-private-project-refs.sh`'s production logic — no bug
  was confirmed; item 3 above shows the existing scan path is already
  correct for this shape.
- Rewriting git history to purge the existing `d626ee0c` leak.
- The hook's own documented "Known gaps" (editor-flow commit messages sans
  `-m`/`-F`, `\git` bypass, `gh issue create/comment`, `eval`/`xargs`/`sh -c`
  wrapper forms) — real, pre-existing, and unrelated to this finding.
- The second PR #736 deferred finding (`comment-discipline-reviewer`'s
  bare-filename citation in `identify-terminal-by-pid.md:47`) — a distinct
  finding, not part of this investigation.
