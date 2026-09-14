# Cross-file anchor links stop tripping the Slack-channel-shape detector

> Every illustrative anchor fragment in this file is written with angle
> brackets around the slug (`other-file.md#<anchor-name>`) so it is
> non-matching under both the current and the replacement pattern. This
> file is scanned by the installed hook when it is committed.

## Context

`deny-private-project-refs.sh` blocks `git commit` when the staged diff,
commit message, or PR body matches one of six structural shape detectors.
One of them, the Slack-channel-shape detector, denies a markdown
**cross-file** anchor link — `[link text](other-file.md#<anchor-name>)` —
as if it were an internal Slack channel mention. It is a false positive:
the shape is ordinary markdown cross-reference syntax that appears
throughout this repo's own docs, and a functional anchor link cannot be
reworded without breaking navigation. Why now: the false positive
currently blocks commits on at least one in-flight branch whose staged
diff merely *touches* a doc file already containing such a link — the hook
scans the whole staged diff, not only added lines, so the blast radius is
wider than commits that introduce one. Intended outcome: cross-file anchor
links inside real markdown link syntax stop being denied, while a bare
`#<anchor-name>`-shaped fragment written outside `[...](...)` syntax stays
denied exactly as today — except for the narrow fabricated-bracket splice
gap documented in the assumption ledger (Row 5).

## Approach

Narrow the position predicate inside `_LIB_SLACK_CHANNEL_SHAPE_REGEX` so
the `#` that opens a candidate channel mention must be reachable from line
start, whitespace, a close-paren, or an open-paren *not* preceded by `]`,
across a run containing no paren and no whitespace. That single change
makes a markdown cross-file anchor link —
`[link text](other-file.md#<anchor-name>)` — unreachable by the detector,
because the run from the link's `(` to the `#` would have to cross the `(`
that `]` already disqualifies. No consumer changes, no dispatcher changes,
and the derived combined-alternation fast path stays correct by
construction.

Replacement pattern:

```
(^|[)#[:space:]]|(^|[^]])\()([^()#[:space:]]*[^(){#[:space:]])?#[a-z0-9_-]*[a-z_-][a-z0-9_-]*
```

The two-alternative structure collapses into one alternation of position
prefixes followed by an optional link-destination run. The
`[^(){#[:space:]]` terminal on that run is what preserves the bash
parameter-expansion-length exclusion (`${#items[@]}`), which the old first
alternative got from `[^({]`.

### Assumption ledger

**Root problem.** The Slack-channel-shape detector's position predicate
admits the `#` inside a markdown cross-file link destination, so
`git commit` is denied for any staged diff, commit message, or PR body
containing such a link — including a diff that merely touches a doc file
already holding one, since the scan covers the whole staged diff rather
than added lines only.

**Givens** (rows 1 and 3 — conditions fixed beyond this design's reach).
Row 2 is a verified fact about current behavior, not a given: the pathspec
is this repo's own file and widening it *is* in reach, so the decision not
to widen it lives in **Out of scope**, not here. Rows 4-9 are material
assumptions. Row numbering is stable across revisions because the
mechanisms below anchor to it.

1. The commit that lands this fix is scanned by the **installed** hook,
   not the worktree copy: `claude/` is stowed into `$HOME`, so the
   installed `_lib.sh` resolves into the main checkout and picks up
   changes on `git pull`, not on a worktree edit. Reason: the stow install
   layout is owned by `install.sh` and the user's home directory;
   re-stowing mid-branch is a separate operation.
   `[verified: repo CLAUDE.md, "Working in this repo" — "claude/ is stowed
   into $HOME. Changes under claude/.claude/** go live on git pull"]`
2. The staged-diff scan's pathspec excludes `claude/.claude/hooks/tests/**`
   and nothing else — `_lib.sh`, `docs/`, and `.claude/plans/` are all
   scanned. This is what makes M5 necessary; widening the pathspec instead
   is in reach and is declined in **Out of scope**.
   `[verified: claude/.claude/hooks/deny-private-project-refs.sh:477]`
3. The engine is POSIX ERE under `grep -Eq` (here-string form, chosen to
   avoid a SIGPIPE/pipefail misreport). No lookaround is available, so a
   "not preceded by X" condition must be spelled as an explicit character
   alternative and the pattern must tolerate being embedded in a derived
   alternation. Reason: the engine and the here-string form are fixed by
   the surrounding script and its portability constraints.
   `[verified: claude/.claude/hooks/deny-private-project-refs.sh:649-653,
   689, 695]`
4. All nine existing pinned assertions keep their current verdicts;
   bare-mention detection and same-page-link handling do not loosen,
   except for the narrow fabricated-bracket splice gap (Row 5).
   `[engineer-verified]`
5. The `](x#<slug>)` splice — a mention written after a fabricated closing
   bracket and a filler destination run — newly evades the detector under
   the new pattern. Its zero-content form, `](#<slug>)`, was already
   unreachable under the old pattern. This diff extends the exemption to
   any filler content between the fabricated bracket and the `#`. It
   stays open rather than closed by a fourth regex branch. Reason:
   closing it requires rejecting a `(` preceded by `]` unless a
   well-formed `[text` opened it first — a negation over a
   variable-length prefix, which POSIX ERE (Given 3) cannot express.
   `[engineer-verified]` `[verified: grep -Eq executed against both regex
   constants this session]`
6. The verification surface is the pinned pytest cases, not a live
   reproduction of the currently-blocked line on another branch. Given 1
   makes such a reproduction meaningless anyway: any hook invocation
   during this work runs the old regex regardless of worktree contents.
   `[engineer-verified]`
7. The other five structural detectors' behavior is untouched.
   `[engineer-verified]`
8. The new pattern is monotone against the old one across a 24-case
   corpus — it never denies anything the old one allowed, and the nine
   pinned shapes keep their verdicts (5 deny, 4 allow). The seven flips
   are all old-DENY to new-ALLOW, matching cases 1, 2, 3, 8, 9, 11, and 12
   in the pinned-case list below.
   `[engineer-verified: corpus executed against both patterns this
   session]` `[verified: grep -Eq re-executed against all 12 pinned
   payloads this session, confirming these 7 flips]`
9. The new constant's own source line is self-non-matching under the
   **old** regex, so `_lib.sh` itself commits cleanly.
   `[engineer-verified: executed]` `[verified: the same property holds for
   the current committed line, which is why it exists in the tree]`
10. The new pattern's link exemption is purely syntactic: it exempts the
    `#` inside any well-formed `[text](destination#<slug>)`, regardless of
    whether `destination` resolves to a real file — generalizing an
    exemption the **old** pattern already grants narrowly for a same-page
    anchor (`[x](#<slug>)`) to arbitrary destination text. Accepted as a
    residual rather than closed, same reasoning as row 5: closing it would
    require verifying the destination resolves to an actual path, which is
    beyond a structural regex detector's reach and would reject legitimate
    links to files that do not yet exist (a plan referencing a doc it also
    creates, for instance). `[engineer-verified]`
11. A third gap was found during the `deny-private-refs-cross-file-anchor-link`
    branch's `/ready-for-review` cumulative code review, not during initial
    planning: a genuine markdown link's destination can hide a second,
    independent `#<slug>`-shaped token past its first, legitimate anchor (e.g.
    `other-file.md#<anchor-one>#<anchor-two>`). The **old** pattern denied
    this shape; this diff's pattern, as first written, newly allowed it.
    The destination run's content classes now exclude `#`, and `#` is
    added as an additional valid reset point, so a second `#<slug>` token
    elsewhere in the same destination is independently caught again —
    except when that second token is itself brace-wrapped
    (`other-file.md#<anchor-one>{#<anchor-two>}`), which shares the
    pre-existing, separately-documented brace-wrap residual regardless of
    position, not a new hole this row opens. This row also tightens
    strictly less than it may
    first appear. It closes what's independently reachable via a *second*
    `#` token. But row 10 already leaves the *first* token's destination
    completely untrusted — `[Docs](notes.md#<secret-slug>)` is already an
    accepted `allow`. So the brace composition above adds no attacker
    capability beyond what row 10 already discloses and accepts.
    `[verified: grep -Eq against the full pinned-test corpus plus new
    attack cases, confirming the fix preserves every existing verdict and
    denies the newly-added attack shapes]`

**Mechanisms:**

- **M1 — Replace the pattern in `_LIB_SLACK_CHANNEL_SHAPE_REGEX` (one
  line).** The single constant is the whole behavior surface; the consumer
  derives its fast-path alternation from `STRUCTURAL_DETECTORS`, so
  nothing downstream needs to know. `anchors: root, row3`
- **M2 — Rewrite the rationale comment block above the constant.** Every
  bullet in it describes the two-alternative mechanism ("the second
  alternative requires…", "the first alternative separately catches…"),
  which stops existing. `anchors: root, row10`
- **M3 — Audit and rewrite the stale test docstrings; add pinned cases for
  the new shapes.** Two docstrings assert the `]`-immediately-before-`(`
  check *as the exclusion*, which becomes one arm of a larger predicate;
  two more name "the first alternative" / "the second alternative" by
  ordinal. Rewrite every docstring that references either the ordinal
  structure or the `]` check as the sole mechanism. `anchors: row4`
- **M4 — Update the two `docs/private-project-redaction.md` sites.** The
  table row's "Catches" column and the paragraph below the table both
  state that the detector deliberately matches markdown anchor links;
  after M1 that is true only for a fragment written outside `[...](...)`
  syntax. `anchors: root`
- **M5 — Write every illustrative anchor shape in the new comment, the
  docs, the plan file, and the commit message so it is non-matching under
  both the old and the new pattern.** Reuse the house convention already
  in the tree rather than inventing a dodge: angle brackets around the
  slug (`other-file.md#<anchor-name>`), matching
  `docs/private-project-redaction.md`'s existing examples, and bracket-
  expression characters in the style the internal-hostname comment uses.
  Non-matching under the *old* pattern is what lets the commit land at
  all; non-matching under the *new* pattern is what stops every future
  commit touching these files from re-tripping the gate.
  `anchors: row1, row2`
- **M6 — One `code-writer` dispatch for all three files.** The constant,
  its comment, its tests, and its docs are one atomic behavior change;
  splitting would force the regex semantics to be restated in each prompt,
  and two agents could resolve the same wording question differently with
  neither self-review seeing the other. `anchors: root`

**Over-powered-primitive check.** The chosen mechanism is the lightest
available — one string literal, no new control flow, no new file. Six
heavier candidates were considered and rejected:

1. **Skip `.md` files in the staged-diff pathspec.** Disables all six
   detectors over the file type that makes up most of this repo's content
   — the largest possible blast radius for the narrowest possible bug.
   `anchors: row2`
2. **Strip markdown link syntax from the scan target before scanning, or
   verify a link's destination resolves to a real file before exempting
   it.** Adds a preprocessing stage to a security gate, and the fast path
   and per-detector loop would then scan different text — the fail-closed
   pattern-composition-mismatch branch exists precisely to catch that
   divergence. Destination verification specifically would also require
   filesystem access from inside a text-pattern detector, a capability
   none of the other five detectors have or need. `anchors: row3, row10`
3. **Add a per-detector "exclusion regex" second field to
   `STRUCTURAL_DETECTORS`.** A schema change to a security-critical array
   (whose label parsing already carries a documented no-colon-in-label
   constraint) to serve one detector's one exception. `anchors: row7`
4. **An env-var or marker-file bypass for this detector.** A bypass on a
   redaction gate is the wrong direction outright, and it would be
   reachable by anything that can set the variable. `anchors: root`
5. **Broaden the charset to stop matching hyphenated words.** Defeats the
   detector's actual purpose; already rejected in the existing pinned
   docstring. `anchors: row4`
6. **No code change — reword around the false positive.** A functional
   anchor link cannot be reworded without breaking navigation, and it does
   not help a diff blocked by a link that already exists in a touched
   file. `anchors: root`

## Critical files

Three files change. All are edited by a **single `code-writer` dispatch** —
see M6 for why this does not split.

**`claude/.claude/hooks/_lib.sh`** *(modify)*

- Line 1945: replace the `_LIB_SLACK_CHANNEL_SHAPE_REGEX` value with the
  pattern in Approach.
- Lines 1922-1944: rewrite the rationale comment. State each fact as its
  own bullet per the repo's comment-length convention — the shape being
  detected; the all-digit exclusion; the reachability predicate that
  excludes a markdown link destination; the parameter-expansion-length
  exclusion; and the residual gaps. Five residuals to name: the
  `](x#<slug>)` splice, the `{#<slug>}` brace wrap, the all-digit
  exclusion, the CommonMark angle-bracket destination form
  `[t](<a b.md#<slug>>)` (which stays denied), and the link exemption
  trusting any link-shaped destination without verifying it resolves to a
  real file. Write each residual in
  self-contained terms — the comment must not cite this plan's row
  numbering, mechanism labels, or any other plan-internal terminology,
  because it has to read correctly to someone who never sees this file.
  Do not turn the block into a rationale essay — design rationale belongs
  in the commit message.
- *Reuse:* the bracket-expression trick this file's internal-hostname
  comment already uses for its own illustrative examples (lines
  1916-1919). Do not invent a second dodge.

**`claude/.claude/hooks/tests/test_deny_private_project_refs.py`**
*(modify)*

- Audit all nine Slack-channel docstrings (lines 2294-2415) for two stale
  claims: the `]`-immediately-before-`(` check described as *the*
  exclusion, and any reference to "the first alternative" / "the second
  alternative" by ordinal. Rewrite each hit to describe the reachability
  predicate. Do not change any assertion — Verification step 1a checks
  that mechanically, because a docstring rewrite that also drifts an input
  literal and its expected verdict together stays green while covering a
  different shape than intended.
- Add pinned cases for these twelve shapes. The first seven cover the
  fix's own scope; the last five pin behavior that changes or is
  load-bearing but would otherwise have no regression signal at all. Write
  each new docstring in self-contained terms, same as M2 and M4 — no plan
  row numbers or plan-internal terminology.
  1. Cross-file anchor link in real link syntax — allow. This is the fix.
  2. Deep path inside a link — allow.
  3. A link preceded earlier in the line by an unrelated `)` — allow.
  4. The same slug both inside a link and bare on one line — deny. This
     is the case that pins the exemption not swallowing the rest of the
     line.
  5. A link destination containing a space — deny.
  6. A parenthesized path fragment with no link text before it — deny.
  7. `[t] (x.md#<slug>)`, a space between `]` and `(` — deny.
  8. Two independent cross-file links on one line — allow. A common
     doc-index and table-of-contents shape; flips deny to allow under
     this change and is currently unpinned.
  9. A link destination carrying a query string before the fragment —
     allow. Also flips deny to allow, also unpinned.
  10. Parameter-expansion-length syntax inside unrelated prose parens —
      allow. Unchanged by this fix, but it sits exactly on the boundary
      between the link exemption and the brace exclusion, so a future
      edit to either could regress it silently.
  11. The accepted splice shape — a fragment written directly after
      link-open syntax with a filler run and no path — allow. Pinning an
      accepted gap gives it the same regression backstop the all-digit
      exclusion already has via
      `test_structural_slack_github_issue_reference_not_flagged_allowed`;
      without it, a later edit could widen or close the gap with no test
      signal in either direction, and the comment block's residual-gap
      list would drift from actual behavior undetected.
  12. A cross-file link whose destination text does not correspond to an
      actual file — allow. Pins the accepted link-destination-trust gap
      the same way case 11 pins the splice gap: the exemption is syntactic
      only and never checks that the destination resolves to a real path.
- Four more cases pin additional shapes: a brace-wrap case (`{#<slug>}` stays
  allowed), and, closing row 11's third gap, a second slug hidden past a
  legitimate anchor in the same link destination (deny), a CommonMark angle-
  bracket destination (deny, already denied pre- and post-fix; this pins the
  previously-untested assertion), and a brace-wrapped second slug in the same
  link destination (allow) pinning row 11's own brace-wrap exception directly
  adjacent to the case it qualifies. Three further cases were added
  during implementation and review: the optional destination-run
  group's zero-length branch (deny), the second-slug form with a
  same-page anchor and no path (deny), and a bare mention immediately
  after a link's closing paren with no whitespace (deny). The pinned
  corpus is nineteen added cases plus the nine existing ones —
  twenty-eight total.
- *Reuse:* the `run_hook` / `bash_input` / `claude_config_repo` shape used
  by all nine existing cases — no new fixtures. The
  `test_structural_detector_in_staged_diff_denied` parametrize list
  (2417-2427) needs no change: its `slack_channel` payload is a bare
  mention, which stays denied.
- This file is the one place a real matching shape can be written
  literally, because the staged-diff pathspec excludes it (row 2).

**`docs/private-project-redaction.md`** *(modify)*

- Table row (~line 58): move the markdown-anchor-link case from "Catches"
  to "Does NOT catch", qualified to *inside* inline-link syntax.
- Paragraph (~lines 64-67): it currently says the detector intentionally
  matches markdown anchor links and to rephrase around a false positive.
  Rewrite: a fragment written outside `[...](...)` syntax is still matched
  deliberately; one inside a link destination is not, and that exemption
  does not verify the destination resolves to a real file. Write this in
  self-contained terms exactly as M2 requires for the `_lib.sh` comment —
  no plan row numbers or plan-internal terminology — since this file ships
  to readers who never see this plan.
- *Reuse:* lines 60-62 already state that every example in this section is
  deliberately non-matching, and line 65 already writes the slug in angle
  brackets — keep both properties in the new text.

**No change** to `claude/.claude/hooks/deny-private-project-refs.sh` (the
consumer reads the constant and derives its own alternation),
`claude/.claude/hooks/tests/test_lib.py` (no reference to this constant),
`claude/.claude/scripts/tests/test_post_crash_sessions.py` (sources
`_lib.sh` live but asserts nothing about this constant), or
`claude/.claude/scripts/select-tests.py` (see Out of scope).

**Plan file** (`.claude/plans/deny-private-refs-cross-file-anchor-link.md`):
authored by the session, not `code-writer`, but subject to M5 — it is
scanned by the installed hook when committed, so every illustrative shape
and every path in it must be non-matching under the old pattern, and paths
must be repo-relative rather than home-rooted.

## Verification

Run from the worktree root. Steps 1-3 are the `code-writer` dispatch's
verification; step 4 is the session's, at commit time.

**1. Regex differential over the pinned-case corpus.** Run this after M1
and M3 are both applied, not before: the new pattern does not exist until
M1 lands, and M3's added cases are the very shapes the differential needs
to see. The test module carries every matching and non-matching shape this
detector has been pinned against, and it is exempt from the staged-diff
scan, so it doubles as the corpus. Run each pattern over it and compare
the line-number sets:

```
grep -nE '(^|[^({])#[a-z0-9_-]*[a-z_-][a-z0-9_-]*|(^|[^]])\(#[a-z0-9_-]*[a-z_-][a-z0-9_-]*' claude/.claude/hooks/tests/test_deny_private_project_refs.py
grep -nE '(^|[)[:space:]]|(^|[^]])\()([^()[:space:]]*[^(){[:space:]])?#[a-z0-9_-]*[a-z_-][a-z0-9_-]*' claude/.claude/hooks/tests/test_deny_private_project_refs.py
```

Accept only if the new pattern's hit set is a strict subset of the old
one (monotonicity — nothing newly denied). Every line that drops out
either carries a markdown cross-file link or matches the
fabricated-bracket splice shape (`](x#<slug>)`, Row 5). That splice
shape is the sole sanctioned non-link drop-out. It appears twice in the
test module: an illustrative docstring literal and the test's own input
literal. Any other line appearing only in the second output is a
regression, not a finding to explain away. This differential does not
independently verify the splice-shape callout above — that callout is
asserted separately, not derived from this run's output.

This differential is a one-time check, not a persisted assertion: it runs
during this change and never again. The pinned cases above are therefore
the *sole* durable regression guard on this constant, which is why the
added set covers shapes beyond the fix's own scope. Do not treat the
differential as standing coverage.

**1a. Assertion byte-identity across the docstring rewrite.** M3 rewrites
docstrings on all nine existing cases while leaving their assertions
alone. Confirm that mechanically rather than by trusting a green run:
extract the `bash_input(...)` literals and the `== "deny"` / `== "allow"`
lines from lines 2294-2415 before and after the edit, and require the two
extractions be byte-identical. A green suite does not catch an input
literal and its expected verdict drifting together.

**2. Scoped test suite.**
`.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the command
repo `CLAUDE.md` requires agents use instead of the full suite. The diff
selects the hooks tests (a change under `claude/.claude/hooks/`) and the
scripts tests (a `.sh` change under that directory), which covers both the
pinned pytest cases and `test_shellcheck.py` /
`test_no_bash4_constructs.py`.

**3. Lint.**
`scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` for the
`_lib.sh` edit and `.venv/bin/ruff check claude/.claude/` for the
test-module edit — both from `CLAUDE.md`'s Commands block.

**4. Commit-time bootstrap check (session, at both commits — the plan
commit and the implementation commit).** The commit lands under the old,
installed pattern, so this is the step most likely to stall the work. Copy
the pathspec verbatim from
`claude/.claude/hooks/deny-private-project-refs.sh:477` rather than
re-deriving it, then pipe `git diff --cached` through each of the two
patterns from step 1. Both must print nothing: the old pattern because the
commit must be allowed to land, the new pattern because otherwise every
future commit touching these files re-trips the gate. Apply the same two
greps to the commit-message text and to the plan file. A hit on the old
pattern is not a reason to escalate — it means an illustrative shape needs
the angle-bracket form per M5.

Match the hook's own added-line filter (`deny-private-project-refs.sh:483`)
rather than grepping the raw diff — the hook scans `+`-prefixed lines with
the `+++` file headers dropped, so a raw-diff grep can fire on a removed
line or a hunk header the hook never sees and send the implementer chasing
a shape that is not actually gated.

This check remains an approximation in the other direction, and
deliberately so: the hook scans `SCAN_TARGET_BOTH`, the union of the raw
text and a shell-quote-stripped copy (`deny-private-project-refs.sh:613-619`),
which these two greps do not reproduce. Quote stripping can change the
character adjacent to a `#` and so change a verdict. A clean step 4 is
therefore evidence the commit will land, not proof — the installed hook is
the authoritative gate, and a denial it raises after a clean step 4 is a
shape needing the angle-bracket form, not a bug in this plan.

## Out of scope

- **The parked in-flight branch** whose commit this false positive
  currently blocks. It unblocks when this fix reaches `main` and the
  affected checkout pulls; nothing on that branch is edited here.
- **The other five structural detectors** — IPv4 literal, SSH key path
  reference, home-rooted path, long hex identifier, internal hostname. No
  behavior change, no comment change. (Row 7.)
- **Bare-mention and same-page-link detection.** Neither loosens except
  for the fabricated-bracket splice gap this diff widens (Row 5); a
  `#<slug>`-shaped fragment outside link syntax otherwise stays denied
  exactly as today. (Row 4.)
- **Closing the `](x#<slug>)` splice gap.** Documented as a residual
  instead. (Row 5.)
- **Residual gaps on this constant**: the fabricated-bracket splice gap,
  the `{#<slug>}` brace wrap, and all-digit runs, plus the CommonMark
  angle-bracket destination form that stays denied. All are documented,
  not fixed. The splice gap's zero-content form (`](#<slug>)`) predates
  this diff; its filler-content form (`](x#<slug>)`) is newly opened by
  it — see Row 5.
- **The generalized link-destination-trust gap.** The new pattern's link
  exemption does not verify the destination resolves to a real file; any
  well-formed `[text](destination#<slug>)` is exempt regardless of
  `destination`'s content. Documented as an accepted widening of the old
  pattern's same-page-anchor exemption, not closed — closing it would
  require destination verification, beyond a structural regex's reach.
  (Row 10.)
- **The staged-diff pathspec at
  `deny-private-project-refs.sh:477`.** Not widened to exclude `docs/` or
  `.claude/plans/` — that would remove real scan coverage to solve an
  authoring inconvenience the angle-bracket convention already solves.
- **The `deny-private-project-refs.sh` dispatcher**: `STRUCTURAL_DETECTORS`
  schema, the derived combined alternation, the per-detector confirm loop,
  and the fail-closed mismatch branch all stay as they are.
- **The fail-open/fail-closed inconsistency between the tracker-ID scan
  and the structural scan**, already noted in that file's own comment as
  unresolved. Untouched here.
- **A `select-tests.py` rule-table comment recording that
  `test_post_crash_sessions.py` sources `_lib.sh` live.** The table's
  selection behavior is already correct for this diff —
  `_is_hooks_dir_shell_script_change` selects the scripts tests — so this
  is a comment-accuracy improvement in a file this change does not
  otherwise scope. It is a real latent fragility (the rule's stated
  justification is `test_no_bash4_constructs.py`; if that dependency were
  ever removed, the live-sourcing dependency would silently lose its
  selection), so it is raised here as a follow-up rather than dropped.
- **A verification step reproducing the specific blocked line from another
  branch.** Ruled out by the engineer, and made meaningless by row 1
  regardless.
