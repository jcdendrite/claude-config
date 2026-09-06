# Hook family standardization — Phase 5: conformance tests and POSIX-ERE sweep

## Context

Land the final phase of the `hook-family-standardization` master plan
(`.claude/plans/hook-family-standardization.md`), closing GH-485. Phases
1–4 removed the hand-copied `jq`, command-matcher, and deny-message
duplication across `claude/.claude/hooks/*.sh` and are already merged to
`main`; nothing in the test suite yet asserts the resulting shape, so the
next hand-written matcher or uncapped `jq` call would reintroduce that
duplication silently, and one hook still matches commands with GNU's `\s`
regex extension, which a POSIX-strict grep reads as a literal `s`,
silently turning that gate's detection into a fail-open. This is the last
remaining phase in the master plan — nothing blocks it. Intended outcome:
three repo-wide conformance tests in
`claude/.claude/hooks/tests/test_hook_alignment.py` that fail if a future
hook bypasses the shared `_lib_*` jq/subcommand-matching helpers or
reintroduces a GNU-only regex escape, five of the six current bare-`jq`
residuals fixed to close on the intended helper,
`require-ready-for-review.sh`'s six `\s` occurrences converted to POSIX
`[[:space:]]`, and a `CLAUDE.md` bullet making the POSIX-ERE convention
discoverable to future contributors rather than only silently enforced by
the new test.

**Scope note on the master plan's own Phase 5 text:** the master plan's
"### Phase 5" section and its Verification-section Phase 5 bullet were
written before Phase 4 merged and before `test_hook_alignment.py` grew to
its current size. Several of its specifics are stale — file counts, line
citations, and the assumed post-Phase-4 residual shape. This plan
supersedes those specifics with evidence gathered this session against the
actual current tree; see the Approach section's assumption ledger for the
corrected figures and the Critical files section for the one-line
supersession pointer this plan adds back to the master plan document.

## Approach

Phase 5 lands three static conformance tests in
`claude/.claude/hooks/tests/test_hook_alignment.py` (bare `jq`, inline
command-matcher regex, GNU `\s`), fixes five of the six bare-`jq`
residuals in place, and converts `require-ready-for-review.sh`'s six `\s`
occurrences to POSIX `[[:space:]]`. One bare-`jq` site and two
inline-matcher sites are allowlisted as documented structural exceptions
rather than converted, each for a reason that is a property of the file
rather than an unconverted leftover. The two gh-matcher regexes in
`require-ready-for-review.sh` are **not** converged onto
`_lib_command_invokes_tool_subcmd` in this phase — converting them would
make that gate's gh arms blind to `bash -c`/`eval` wrapper forms that its
own git arm still sees, which is a fail-open behavior change and the same
design question the master plan already deferred for
`require-respond-pr.sh`.

Three deliberate departures from the master plan's Phase-5 spec, all from
re-reading the post-Phase-4 tree.

*The bare-`jq` fix is `printf` + `_lib_jq -Rs .`, not `_lib_jq -n`.*
`_lib_emit_deny` (`_lib.sh:151-177`) builds its JSON envelope with `printf`
and routes only the string field through `_lib_jq -Rs .`;
`_lib_emit_allow_with_context` (`:193-200`) mirrors it with a
degrade-to-silence tail. Wrapping `advance-past-commit-stall.sh:217`'s
`jq -n` in `_lib_jq` instead would put a `timeout` between the harness and
a Stop-block payload, so a SIGTERM mid-write truncates the envelope — the
one thing that hook's header (`:10-14`) says it must never emit. The
`printf`-plus-encode shape removes the bare call, caps the only step that
reads input, and makes truncation structurally impossible.

*Two of the six `\s` sites are not subsumed by anything.* The master
plan's sequencing assumed the surviving `\s` regexes would mostly be
deleted by helper conversion. Because lines 259 and 262 stay as regexes
(below), the POSIX sweep covers all six occurrences, not four. Neither
remediation subsumes the other at those two lines: the regex is converted
in place and the site is simultaneously recorded in the inline-matcher
allowlist.

*A third conformance test.* The POSIX-form convention this phase
establishes repo-wide has no enforcement in the master plan's two-test
spec, so it regresses on the next hand-written regex. The `\s` guard is
~10 lines, uses the same subject list and idiom as the other two, and is
the only one of the three whose violation is silent on the CI runner's own
grep.

### Assumption ledger

**Root problem.** Phases 1-4 removed the hand-copied `jq`, command-matcher,
and deny-message duplication from `claude/.claude/hooks/*.sh`, but nothing
in the test suite asserts the resulting shape, so the next hand-written
matcher or uncapped `jq` reintroduces the duplication silently — and one
hook still matches commands with GNU's `\s`, an extension that a
POSIX-strict grep reads as a literal `s`, turning a gate's detection into
a silent fail-open.

**Givens** (fixed beyond this plan's reach):

- **G1.** No literal BSD/macOS grep is reachable from this machine — its
  Docker runs Linux containers only, and (verified this session — see row
  13) busybox's grep is not a usable stand-in either, since it also
  supports `\s`. GH-485's defect class cannot be locally demonstrated
  against a real non-GNU grep at all with tools available on this machine.
- **G2.** The post-Phase-4 tree is the baseline. Every allowlist entry
  below is derived from what Phases 1-4 actually left behind; this phase
  does not reopen a decision those merged PRs made (which sites converted,
  which were deliberately retained).
- **G3.** Two sibling gh-matcher residuals are already deferred by the
  master plan to their own issues: `require-respond-pr.sh`'s hand-rolled
  `PATTERN_*` family (Out of scope, "the durable fix is converging that
  hook onto the shared helper, which is a design question of its own") and
  `block-gh-pr-merge.sh`'s wrapper-form blindness. This plan inherits both
  boundaries rather than moving them.

**Rows:**

1. Bare `jq` outside a `_lib_*` wrapper survives at **6 sites in 3 files**,
   not the master plan's 19 — Phase 4 closed the extraction-pattern class
   it counted. `[verified: advance-past-commit-stall.sh:217,
   check-branch-divergence.sh:120,
   nudge-handoff-near-context-cap.sh:199/208/294/303 — read this session;
   the three excluded non-invocations (`command -v jq`, a printf string
   naming jq, `gh --jq`) re-confirmed]` `anchors: root`
2. `nudge-handoff-near-context-cap.sh`'s four sites are **fixed**, not
   allowlisted: the same file already runs `_lib_capped_for 2 jq` at five
   other sites, and two of the four are the uncapped second stage of a
   pipeline whose first stage is `_lib_capped_for 2 tail`. The 2-second
   budget is the file's own, not `_lib_jq`'s 5 — this hook fires on nearly
   every turn. On a cap, `usage_block` is empty and the caller already
   falls back to the cached estimate, which is the same path a `jq` error
   takes today. `[verified: read_latest_usage and
   read_latest_usage_cached, nudge-handoff-near-context-cap.sh:195-313,
   read in full this session]` `anchors: row1`
3. `advance-past-commit-stall.sh:217` is **fixed** by replacing `jq -n`
   with `printf` of the `{"decision":"block","reason":%s}` envelope and
   `_lib_jq -Rs .` for the reason string, keeping the `|| true` and the
   trailing `exit 0`. The file already calls `_lib_jq` at `:87`, so `:217`
   is the lone unwrapped call in a file that otherwise uses the wrapper,
   and its header's documented degradation (broken or missing jq → silent
   allow) is preserved exactly. Two lighter alternatives were weighed: (a)
   leave it bare and allowlist — rejected because the header documents the
   `|| true`, not the bare-ness, so the exemption would be a leftover
   wearing a rationale; (b) add a `_lib_emit_stop_block` helper to
   `_lib.sh` — rejected as a new shared symbol for one call site, and it
   would put this phase's diff into `_lib.sh`, widening `select-tests.py`
   for no gain. `[verified: advance-past-commit-stall.sh:1-40 and
   :200-219; _lib.sh:151-200 — read this session]` `anchors: row1`
4. `check-branch-divergence.sh:120` is **allowlisted at file level**. The
   hook never sources `_lib.sh`; it carries its own `TIMEOUT_CMD` probe
   (`:61-71`) and applies it to the network fetch alone, leaving `git
   rev-parse`, `git rev-list`, and `git merge-tree` uncapped. Adding the
   stub-then-source-or-degrade bootstrap to a self-contained always-`exit
   0` advisory hook to reach one wrapper is the heavier primitive; capping
   only the `jq` while three local git calls stay uncapped would be
   arbitrary. File-level keying is the honest granularity here: every `jq`
   in this file is bare for the same structural reason, and the entry
   comes out the day the hook starts sourcing `_lib.sh`. `[verified:
   check-branch-divergence.sh:1-40, :58-100, :120-123 — read this
   session]` `anchors: row1`
5. Inline literal command-matcher regexes survive at **3 sites in 2
   files**: `require-ready-for-review.sh:259` and `:262`, plus
   `enforce-marker-script-shape.sh:545`. A broader detector that also
   scans variable-assigned patterns would flag 12 sites in 3 files, 9 of
   which are either whole-command *shape validators* no matcher helper can
   express (`enforce-marker-script-shape.sh:598`, `:629`) or the pattern
   family G3 already defers (`require-respond-pr.sh:204-210`). `[verified:
   repo-wide grep for a tool token adjacent to a whitespace-class atom
   across claude/.claude/hooks/*.sh, run this session — full result set is
   the 12 sites named]` `anchors: root`
6. `require-ready-for-review.sh:259/262` are **allowlisted, not
   converged**, and the hook gains a one-line comment recording why.
   Converting them to `_lib_command_invokes_tool_subcmd` would gate
   detection behind `_lib_fragment_invokes_tool`'s command-word resolution,
   whose runner list excludes every shell — so `bash -c "gh pr create"`,
   which the current any-position fragment scan catches, would stop firing
   the gate. The same hook's git-push arm goes through
   `_lib_fragment_invokes_git`, which is any-word, so conversion would
   leave one gate with two arms of different wrapper reachability. Both
   forms have a fail-open hole and the allowlist entry must say so: the
   retained regex misses `gh --repo o/r pr create` (adjacency-only), a
   live bypass of this gate today. The durable fix is OR-combining both
   detections, the pattern `enforce-marker-script-shape.sh:502-556` already
   establishes — deferred to one issue covering this hook and
   `require-respond-pr.sh` together, since it is one design question in
   two files. `[verified: require-ready-for-review.sh:186-268 read in
   full; _lib.sh:1030-1068's own comment stating the tool helper is
   command-word-gated "unlike the git helper above"]` `anchors: row5`
7. `enforce-marker-script-shape.sh:545` is **allowlisted**, unchanged: it
   is already OR-combined with `_lib_command_invokes_tool_subcmd` under
   that file's documented dual-detection design. The allowlist entry cites
   the hook's own comment block rather than restating its rationale.
   `[verified: master-plan row 21 and the hook's :502-556 design, as
   reported by this session's exploration]` `anchors: row5`
8. Live `\s` survives at **6 sites in 1 file** —
   `require-ready-for-review.sh:196, 199, 202, 218, 259, 262`. The master
   plan's other two named survivors are already clean. Two further repo
   hits are comment text, not regexes (`require-respond-pr.sh:167`, the
   POSIX reference; `_lib.sh:1881`, inside an excluded file), which is why
   the `\s` guard must exclude full-line comments or it fails on the very
   comment that documents the convention. `[verified: repo-wide `\s` grep
   across claude/.claude/hooks/*.sh, run this session — 8 hits total, 6
   live]` `anchors: root`
9. All six conversions are semantic no-ops under GNU grep, where `\s` and
   `[[:space:]]` match the same class, so **`test_require_ready_for_review.py`
   passing unedited is the equivalence proof** — the same idiom Phase 2
   used for the length-gate driver. The behavior change exists only on a
   POSIX-strict grep, which CI does not run, so no in-suite test can
   observe it, and (per G1) no local tool available on this machine can
   observe it either — see row 13 for how the plan handles that gap.
   `[verified: GNU grep's `\s` is documented as equivalent to
   `[[:space:]]`; the six patterns' structures read this session]`
   `anchors: row8`
10. The three tests go **below the Layer-1 banner at `:420-422`**, not
    `:346` (mid-docstring in an unrelated settings test), and parametrize
    over `_MAIN_HOOKS` with `ids=[h.name for h in _MAIN_HOOKS]`, matching
    `test_hook_documented_in_hooks_md`'s existing subject choice and its
    stated reason for excluding `plugins/*/hooks/`. Allowlists are
    module-level `dict[str, str]` constants (filename → structural reason)
    rather than the file's `frozenset`/`tuple` shape, because every entry
    here needs its reason attached, and each test asserts a listed hook
    **still violates** — a stale entry fails loudly instead of outliving
    its reason. `[verified: test_hook_alignment.py:36-115, :381-422 read
    this session]` `anchors: root`
11. Each detector scans non-comment lines and is a regression guard for
    the established call shapes, not a shell parser; each test docstring
    names its own blind spot. Bare `jq`: a `jq` token in command position
    — after start-of-line, `|`, `;`, `&`, `(`, or `$(` — which by
    construction skips `--jq`, `command -v jq`, `_lib_jq`,
    `_lib_capped_for 2 jq`, and prose inside a printf string, and misses
    `xargs jq` and `command jq` (bypassing a same-named function without
    `command -v`) — an extension of the same accepted blind spot, not a
    new one (`claude-hook-review`, this round). Inline matcher: a
    `grep`-family invocation (match the family broadly — `grep -q`, `-c`,
    `-l`, piped into `[ -n ... ]`, etc., not only the literal `-q` flag,
    since a hook author using any of those forms implements the same
    command-detection idiom and could otherwise evade the check by
    accident — `claude-hook-review`, this round) carrying a **literal**
    pattern in which a tool token (`git`, `gh`, `marker.sh`)
    is immediately followed by a whitespace-class atom, which by
    construction skips flag-presence checks (`--dry-run`, `--tags`,
    `--fill`) and misses a pattern hoisted into a variable — the shape row
    5 declined to chase. `\s`: a literal backslash-`s` anywhere on a
    non-comment line. `[verified: each detector traced by hand against
    the 6 jq sites, the 12 matcher sites, and the 8 `\s` hits enumerated
    in rows 1, 5, and 8]` `anchors: row10`
12. `plugins/*/hooks/` is excluded from the bare-`jq` test because five
    sites there would need adjudicating first, and they are not one
    class: `plugin-semver/hooks/require-plugin-version-bump.sh:54` and
    `npm-semver/hooks/require-npm-version-bump.sh:90` are the documented
    `declare -F _lib_jq` pre-source fallback, while
    `require-plugin-version-bump.sh:245`/`:251` and
    `require-npm-version-bump.sh:395` are inconsistencies against their
    own sibling (npm-semver uses `_lib_jq` at `:355`/`:367`/`:373` for the
    same shape). For the inline-matcher and `\s` tests, run the same two
    greps over `plugins/*/hooks/*.sh` at implementation time and
    parametrize over `ALL_HOOKS` instead if either returns zero — free
    coverage with no allowlist. `[verified: repo-wide jq grep across
    plugins/, run this session]` `anchors: row10`
13. **GH-485's defect did not reproduce on any of four independent
    probes this session, including the real target platform.** (1) A
    first Docker/busybox attempt used a shell heredoc (`<<<`) as stdin,
    which Docker does not forward without `-i` — invalid, produced a
    false negative initially misreported as a successful repro. (2) A
    corrected busybox attempt, mounting a file instead of piping:
    `a\sb` against `a b` → exit 0 match; against literal `asb` → exit 1
    no match; `a[[:space:]]b` against `a b` → exit 0 match. Proves
    busybox's grep treats `\s` as a whitespace-class escape, same as GNU
    grep — no defect. (3) A remote session on the engineer's own Mac
    ran the same three probes via bare `grep`, which resolved through
    `PATH` to a Homebrew-installed `ugrep` shim (`ugrep 7.8.4`,
    self-reported): same result, no defect, `\s` GNU-compatible. (4) The
    same session re-ran the probes against the explicit `/usr/bin/grep`
    path, bypassing the `PATH` override and reaching the actual
    Apple-shipped binary: `grep (BSD grep, GNU compatible) 2.6.0-FreeBSD`
    — self-identifies as GNU-compatible, and behaves identically to GNU
    grep on all three probes (matches `a\sb` against `a b`; does not
    read `\s` as literal `s`). **This is the real target platform GH-485
    was concerned about, and it does not have the defect.** No
    non-GNU-compatible grep was reachable to test (a true POSIX-only
    BSD/FreeBSD-base build without Apple's GNU-compat layer, or a
    Solaris/embedded-system grep, remain theoretical and untested).
    **Engineer's call this session, given four negative results
    including the real target platform:** convert the six `\s` sites
    anyway, framed explicitly as precautionary POSIX-portability
    consistency with the rest of the already-standardized hook family —
    not as a confirmed fix for a reproduced defect. State this plainly
    in the PR body. `[engineer-verified]` `anchors: G1`
14. The pre-Phase-1 failing-run verification needs the **new test module
    copied into the scratch worktree**, because `_REPO_ROOT` resolves from
    `Path(__file__).resolve().parents[4]` — running pytest from this
    worktree against a checked-out old tree would silently re-test the
    current tree and pass, proving nothing. Pin
    `933e3a3d2bcc`, run node-id- or
    `-k`-scoped so the module's unrelated settings assertions don't drown
    the signal, and **record the measured failure counts** rather than
    asserting the master plan's 8/19/5, which predate Phase 4 and count a
    different jq class than row 11's detector does. `[verified:
    test_hook_alignment.py:40 `_REPO_ROOT` derivation; the SHA's ancestry
    as reported by this session's exploration]` `anchors: root`
15. The master plan's stale citations get **one appended supersession
    pointer**, not a rewrite of the stale numbers. That file already
    carries three in-place supersession notes (rows 4, 20, 24) rather than
    edited history, so a pointer follows its own convention; the numbers
    themselves record what was believed when the phase was scoped, which
    CLAUDE.md's preserved-content axis treats as read-only. Phase 5 is
    terminal, so nothing downstream reads that section again — the
    pointer exists for a future reader doing archaeology, and it lands as
    an Incidental edit in the PR body. `[verified:
    hook-family-standardization.md read in full this session]` `anchors:
    root`

## Critical files

Paths are relative to the repository root. **One `code-writer` dispatch**
covers the whole phase: the file set is five files and the test
allowlists are downstream of the hook edits, so splitting would force the
second dispatch's prompt to restate every disposition the first one made —
the shared-state case `plan-it` says not to split. Verification command
for the dispatch: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
The two out-of-tree probes (busybox, scratch worktree) stay with the
dispatching session, not the dispatch — both need Bash outside this
worktree's anchor, and a mid-run anchor mismatch denies every remaining
call in the agent.

**Reuse, do not reimplement:** `_lib_emit_deny`'s `printf`-envelope-plus-
`_lib_jq -Rs .` shape (`_lib.sh:151-177`) and
`_lib_emit_allow_with_context`'s degrade-to-silence tail (`:193-200`);
`_lib_jq` (`:16`); `_lib_capped_for` (`:38`); `_all_hook_files()`,
`_MAIN_HOOKS`, and the `_EXPLICIT_GATES` / `_SELF_FILTERING_BASH_GATES`
named-constant-with-rationale idiom (`test_hook_alignment.py:45-107`,
`:381-394`); `helpers.build_path_without` for the jq-absent degrade test.

- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh` — four sites
  (`:199`, `:208`, `:294`, `:303`) become `_lib_capped_for 2 jq …`,
  matching the file's five existing wrapped calls. No comment needed; the
  change makes the file self-consistent rather than introducing a fact.
- `claude/.claude/hooks/advance-past-commit-stall.sh` — `:217` becomes a
  `printf` of the block envelope with the reason encoded through
  `_lib_jq -Rs .`. **The `printf` must be gated on the encode succeeding**
  (`_lib_emit_allow_with_context`'s `[ -z "$context_json" ] && return 0`
  shape at `_lib.sh:193-200` is the exact pattern to mirror, substituting
  the appropriate empty/failure check for `reason_json`) — without this
  guard, a timed-out or missing jq produces `{"decision":"block","reason":}`,
  malformed JSON, not the silent-allow the hook's header requires
  (`claude-hook-review`, this round). `|| true` and the trailing `exit 0`
  stay. Update the header sentence at `:12-14` to state the durable fact
  in one line: the reason is encoded through the capped wrapper and a
  failed encode emits nothing, so a broken, missing, or hung jq degrades
  to silent-allow. `/code-review` must confirm the guard is present in the
  actual diff, not only described here.
- `claude/.claude/hooks/require-ready-for-review.sh` — six `\s`
  occurrences (`:196`, `:199`, `:202`, `:218`, `:259`, `:262`) to
  `[[:space:]]`, no other change to those patterns. Add one comment line
  above `:259` stating the durable fact only: these two matchers scan the
  whole fragment rather than resolving its command word, so a `bash -c`
  or `eval` wrapper stays covered, matching the git arm above; the cost
  is that a flag interposed before the subcommand is missed.
  `require-respond-pr.sh:167`'s comment is the textual reference for the
  POSIX form — `block-gh-pr-merge.sh:66` is not, that hook now carries no
  POSIX regex at all.
- `claude/.claude/hooks/tests/test_hook_alignment.py` — three
  parametrized tests plus two allowlist constants, inserted below the
  Layer-1 banner at `:422`. Update the module docstring's Layer-1
  paragraph (`:6-15`) to name the three static shape checks alongside the
  existing hook-class assertions.
  - `_BARE_JQ_EXEMPT_HOOKS: dict[str, str]` — one entry,
    `check-branch-divergence.sh`, reason: does not source `_lib.sh`,
    carries its own `TIMEOUT_CMD` probe applied to the network call only.
  - `_INLINE_COMMAND_MATCHER_EXEMPT_HOOKS: dict[str, str]` — two entries.
    `enforce-marker-script-shape.sh`: raw-text arm OR-combined with
    `_lib_command_invokes_tool_subcmd` per that hook's dual-detection
    design. `require-ready-for-review.sh`: whole-fragment scan retained
    so shell-wrapper forms stay covered, at the cost of missing a flag
    interposed before the subcommand; cite the follow-up issue number.
  - Each test skips-with-assertion for a listed hook: assert the hook
    still violates, so a stale entry fails rather than persisting.
- `.claude/plans/hook-family-standardization.md` — one line appended to
  the `### Phase 5` section pointing at this plan file as the live spec,
  naming that the counts and the `:346` / `block-gh-pr-merge.sh:66`
  citations in that section predate Phase 4. No edit to the stale text
  itself. Incidental edit; call it out in the PR body.
- `CLAUDE.md` — one new bullet under "Working in this repo," immediately
  after the existing "Hook defense-in-depth" bullet: "**Hook regexes:
  POSIX ERE only.** Use `[[:space:]]`, not GNU grep's `\s` extension —
  `\s` isn't POSIX ERE and isn't guaranteed portable.
  `claude/.claude/hooks/tests/test_hook_alignment.py` enforces this
  across `claude/.claude/hooks/*.sh`." Wording calibrated to what the
  GH-485 investigation (row 13) actually found — not "some grep
  implementations don't support it," which would overclaim a confirmed
  defect after four independent negative results, per
  `ai-instruction-and-memory-files`'s review this round. Engineer-requested this session,
  after the GH-485 reproduction investigation (row 13) — makes the
  convention the new `\s` conformance test enforces discoverable to a
  future contributor, not just silently checked. Scoped to `\s`
  specifically (not a broader "no GNU regex extensions" claim) because
  that is exactly what the new test checks; a wider rule than the
  enforcement would be an unenforced claim. Route through
  `ai-instruction-and-memory-files` at `/plan-review` and `/code-review`
  time, per this repo's own Domain: Claude Code config checklist.

No `docs/hooks.md` change: none of the three hook edits changes documented
behavior, and a wrapper choice is an implementation caveat that belongs in
the source comment.

## Verification

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/ claude-skills/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

`select-tests.py` can under-collect when one diff touches both a domain
directory and a file inside it (GH-882) — this diff is exactly that shape
(`claude/.claude/hooks/*.sh` plus
`claude/.claude/hooks/tests/test_hook_alignment.py`). Confirm the selected
set actually contains `claude/.claude/hooks/tests/` before trusting a
green run; if it collapsed, run that directory explicitly rather than
widening to the full suite by hand.

Beyond the scoped suite:

1. **Equivalence proofs, by unedited pass.** `test_require_ready_for_review.py`
   and `test_nudge_handoff_near_context_cap.py` must pass with no edit —
   under GNU grep the `\s` conversion is a no-op, and the `jq` wrapping
   changes only the cap. Any required edit means one of the two changed
   behavior.
2. **Cap-engagement characterization test for the four newly-capped
   `nudge-handoff-near-context-cap.sh` sites** (`staff-sdet`, this
   round): an unedited pass of `test_nudge_handoff_near_context_cap.py`
   proves the fast path is unchanged, but not that the 2-second budget
   actually engages at these four sites rather than silently inheriting
   `_lib_jq`'s 5s default or running uncapped. The master plan's own
   Phase 2 verification bullet set this precedent for exactly this
   situation ("one characterization test for the newly-capped call
   confirming the cap actually engages, reusing the `assert_cap_engaged`
   fixture"), and this same test file already has the idiom for two
   sibling sites in this file
   (`test_read_latest_usage_tail_killed_by_2s_cap_not_5s_default`,
   `test_fire_path_jq_killed_by_2s_cap_not_5s_default`, both
   `@pytest.mark.timing`). Add one parametrized test covering all four
   newly-capped sites using that same idiom, asserting a slow `jq` is
   killed at ~2s and not permitted to run to ~3.5s+. Without this, a
   future edit that widens the cap (e.g. swapping in bare `_lib_jq`,
   silently changing 2s to 5s) or drops the cap argument entirely passes
   every other test in this plan's verification set.
3. **New degrade-path test** for `advance-past-commit-stall.sh`: with
   `jq` removed from `PATH` via `helpers.build_path_without`, the hook
   emits nothing on stdout and exits 0 — pinning the header's documented
   silent-allow contract as executable, since the emit path is the one
   line this phase rewrites. Also assert the happy path still emits a
   single well-formed `{"decision":"block","reason":…}` object.
4. **New regression-lock test in `test_require_ready_for_review.py`**
   for the `gh --repo o/r pr create` bypass (`ciso-reviewer` and
   `staff-sdet`, this round, independently converging), mirroring the
   file's existing test for the sibling full-path-invocation bypass
   (`/usr/bin/gh pr create`, `test_require_ready_for_review.py:1344-1353`)
   and the master plan's own precedent for this identical shape (GH-498's
   deferral, `hook-family-standardization.md:150,242` — "add a
   regression-lock characterization test for the current ... partition
   first, so an incidental edit elsewhere can't silently drift the list
   while unowned"): assert the gate currently **allows**
   `gh --repo o/r pr create` through, with a comment citing this plan's
   row 6 and the follow-up issue. This is an inverted assertion — it
   documents the gap, doesn't fix it — and flips to a required fix the
   day the convergence issue lands, the same role `test_block_gh_pr_merge.py`'s
   wrapper-form tests already play for sibling gaps. Without it, an
   unrelated future edit to this matcher (touched while fixing something
   else) could silently widen the bypass further with nothing failing.
5. **Self-test the bare-jq detector's command-position anchor set**
   (`staff-sdet`, this round): the described anchor set (start-of-line,
   `|`, `;`, `&`, `(`, `$(`) omits two shell-syntactic command-start
   positions common elsewhere in this repo's hooks — after a keyword
   (`then`, `else`, `elif`, `do`) and after brace-grouping (`{`) — plus
   a hand-rolled `timeout N jq` call, which duplicates rather than
   reuses `_lib_jq` and is exactly the class this test exists to catch.
   Add fixture lines for `then jq`, `{ jq`, and `timeout 5 jq` as a
   meta-test asserting the detector actually flags them, before relying
   on it as a regression guard across 40+ hook files; extend the anchor
   set if any pass undetected. Also strip same-line trailing comments
   (not just full-line comments) before the `\s` sweep scans for a
   literal backslash-`s` — the stated exclusion only strips whole
   comment lines, so a future inline comment mentioning `\s` in prose
   near the six converted sites this phase creates would spuriously fail
   the check (no current hit has this shape: both existing `\s`-in-comment
   occurrences, `require-respond-pr.sh:167` and `_lib.sh:1881`, are
   full-line).
6. **Each conformance test shown failing at the pre-Phase-1 tree.**
   `git worktree add <scratch> 933e3a3d2bcc`,
   copy the new `test_hook_alignment.py` into that worktree's
   `claude/.claude/hooks/tests/`, and run it there `-k`-scoped to the
   three new tests. The module resolves its subject from its own path, so
   running it from this worktree would test the current tree and pass
   while proving nothing. Record the **measured** per-test failure count
   in the PR body; do not carry forward the master plan's 8/19/5, which
   predate Phase 4 and count a different jq class. If a count looks
   implausibly large, the detector is overbroad — fix the detector, do
   not grow the allowlist.
7. **GH-485 repro: four attempts, all negative, including on the real
   target platform.** See assumption-ledger row 13 — busybox (twice, the
   first invalid), a Mac's `PATH`-resolved `ugrep`, and finally that same
   Mac's actual `/usr/bin/grep` (BSD grep 2.6.0-FreeBSD, self-identified
   as "GNU compatible") all behave identically to GNU grep on `\s`. The
   `\s`→`[[:space:]]` conversion proceeds anyway, per the master plan's
   own named fallback, but the PR body must frame it accurately as
   precautionary POSIX-portability consistency with the rest of the
   hook family, not as a confirmed fix — state plainly that the specific
   fail-open GH-485 describes was tested four ways, including on the
   actual macOS grep binary, and not reproduced.
8. **One-way-door consequence for Phases 1–2 (`staff-platform-engineer`,
   this round).** State plainly in the PR body: merging this PR forecloses
   standalone revert of Phases 1 or 2 — per the master plan's own "rollback
   is forward-only past Phase 5" line (Verification section), reverting
   either after this point requires reverting Phase 5 first, since their
   conformance-test allowlists are keyed to the post-Phase-4 file shapes
   this phase's tests assert against. A future reader of this PR alone,
   without the master plan open, must be able to learn this fact from this
   PR's own body.
9. `/code-review` before the commit and `/ready-for-review` before the
   push, per CLAUDE.md.

## Out of scope

- **Converging `require-ready-for-review.sh:259/262` and
  `require-respond-pr.sh:204-210` onto
  `_lib_command_invokes_tool_subcmd`.** One issue covering both, as a
  tracking-convenience choice, not a technical dependency between the two
  files — `enforce-marker-script-shape.sh:502-556` already proves the
  OR-combination is a per-file, incremental fix with no cross-file
  coupling (`claude-hook-review`, this round: the fix is not "blocked on"
  `require-respond-pr.sh`, bundling them is purely for issue-tracking
  economy). The likely shape is the unconditional OR-combination
  `enforce-marker-script-shape.sh` already uses. The issue must name the
  live bypass it closes — `gh --repo o/r pr create` and
  `gh pr --body x comment 5` currently pass both gates — and the master
  plan's existing `require-respond-pr.sh` deferral folds into it rather
  than staying separate. **File this issue before or alongside this PR,
  not as a dangling promise** (`ciso-reviewer`, this round): the
  allowlist comment and the new behavioral pinning test (Verification
  item 2a) both cite an issue number, and a placeholder or missing
  reference at merge time degrades the disclosure to unfollowed prose.
- **Extending the bare-`jq` conformance test to `plugins/*/hooks/`.**
  Five sites need adjudicating first, and they split two ways:
  `plugin-semver/hooks/require-plugin-version-bump.sh:54` and
  `npm-semver/hooks/require-npm-version-bump.sh:90` are the documented
  pre-source `declare -F _lib_jq` fallback (legitimate), while
  `require-plugin-version-bump.sh:245`/`:251` and
  `require-npm-version-bump.sh:395` are inconsistent with npm-semver's own
  `_lib_jq` use at `:355`/`:367`/`:373`. Different stow package, different
  `_lib.sh`, and the master plan's own scope warning against letting this
  effort become a catch-all.
- **`enforce-marker-script-shape.sh:598` (`MARKER_SHAPE`) and `:629`
  (`VALID_CHAINED_COMMIT_PATTERN`).** Not command matchers — they validate
  a whole command's permitted grammar, including argument shapes
  (`clear-stale --dry-run`) no tool-plus-subcommand helper can express.
  Declined on correctness, not deferred.
- **Rewriting the master plan's stale numeric citations** (`:346`, "3
  files", "19 bare-`jq`", `block-gh-pr-merge.sh:66`). A committed plan's
  Critical-files text records what was believed when the phase was
  scoped; the file's own convention for correcting itself is a
  supersession note, used three times already. One pointer line lands;
  the numbers stay.
- **A conformance test for the deny-message prefix convention Phase 4
  established.** Same regression-guard argument as the three tests here,
  but a different surface (message text, with a production consumer in
  `transcript-analysis.py`) and a different failure mode. Worth its own
  issue; folding it in would put this PR back into Phase 4's file set.
- **Further chasing a true POSIX-only (non-GNU-compatible) grep
  reproduction of GH-485.** Four attempts this session — two local
  (a broken stdin pipe, then a corrected mounted-file probe against
  busybox) and two remote against an engineer's actual Mac (`PATH`-resolved
  `ugrep`, then the real `/usr/bin/grep`, BSD grep 2.6.0-FreeBSD) — all
  found `\s` GNU-compatible, including on the real target platform. A
  genuinely non-GNU-compatible grep (true FreeBSD/NetBSD base without
  Apple's GNU-compat layer, Solaris, some embedded systems) remains
  theoretical and untested; not worth chasing further given four negative
  results already include the one platform this concern was actually
  about. Not blocking this phase per the master plan's own named fallback
  (row 13, Verification item 4).
