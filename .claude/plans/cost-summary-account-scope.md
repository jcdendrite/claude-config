# Scope `cost --summary` to one account, and make single-root scope self-disclosing

## Context

**Goal:** make `cost --summary` single-account by construction, so populating
the declared-roots file cannot silently publish one account's spend inside
another account's PR — and make the remaining tools state, in their own output,
when they scanned one config directory because no roots file was declared.

A session reported a cost figure caveated with "no additional accounts declared
in `~/.claude/transcript-config-dirs`." That caveat was accurate and the
disclosure machinery worked. Investigating it surfaced two things.

**First, the drift question.** There is no hard-coded config-dir list anywhere
in this repo. One mechanism (`claude/.claude/scripts/_config_dir.py`) reads one
user-local declared-roots file, and four tools funnel through it. The drift is
not a duplicated list — it is that **the roots file is a derived artifact with
no generator**, and on this machine it does not exist, so
`declared_transcript_roots()` returns `[]` through its documented fail-open and
every tool silently resolves to one account. The authoritative roster lives in a
separate private account-provisioning repo that this public repo must not read;
generating the file there is a companion PR (see **Out of scope**).

**Second — and this is why the plan leads with it — that companion PR is
currently unsafe to land.** `pr-description/SKILL.md` gates its `## Cost` block
on a **per-account** sentinel, and says so explicitly: *"The sentinel is per
Claude account, not per repo: cost is an organizational fact, and each account
is its own billing entity,"* and *"never also check `$HOME/.claude` when
`$CLAUDE_CONFIG_DIR` is set, or one account's opt-in would activate disclosure
under another."* But the command it then runs —
`cost --this-repo --branches "$branch" --summary` — resolves roots through
`_resolve_cost_roots`, which **unions `config_dir()` with every
`declared_transcript_roots()` entry**, and `--summary` **refuses `--config-dir`**
outright ("it is a fixed, aggregate-only output mode"). There is no flag that
scopes a summary back to one account.

So the moment the roots file is populated, a PR authored under an opted-in
account publishes a figure that includes sessions from accounts that never
opted in — defeating a consent gate the skill went to deliberate lengths to
scope per-account. It is latent only because the file is absent today.

A PR is created under exactly one Claude account and is never authored across
several, so the union is not merely a disclosure problem to label — it is the
wrong scope for this mode. Fixing the scope makes the figure correct by
construction and makes the companion PR safe to land.

**Also found, and deliberately not fixed here:** `cost-ledger --record` has the
identical shape — a per-account opt-in sentinel (`.cost-ledger-enabled`) gating
a figure that unions every declared account — but writes that figure into
`docs/cost-ledger.md`, a **git-tracked file this repo commits**, not an editable
PR body. `docs/cost-ledger.md`'s own schema doc says the union is intended
("one row per week per **machine**"), so the fix is not a scope narrowing like
mechanism 1 — the engineer's direction is to keep the union but stop committing
the ledger to this public repo, which is a storage-location redesign of a
recently-merged, separately-designed feature (`.claude/plans/cost-trend-ledger.md`,
PR #617). That redesign is out of scope here; see **Out of scope**.

What is *not* out of scope: mechanism 7 (below) nudges exactly the operator who
could trigger this — a diverged profile is told to populate the roots file, and
`cost-ledger --record` would then union and commit that account's spend on the
next run. Landing the nudge without a guard would make this PR the thing that
arms a hole it also documents as unsafe. Mechanism 8 closes that without
touching storage.

## Approach

**Root problem:** `cost --summary` unions accounts with no way to scope back,
so populating the roots file would publish non-consenting accounts' spend in a
PR body; and elsewhere a single-root run is indistinguishable from a complete
one.

**Givens** (fixed beyond this plan's reach):

- The authoritative account roster lives in a private repo this public repo
  must not read. *Reason: `claude/` stows to every user who clones this repo;
  depending on one operator's private repo would break every other consumer.*
- `declared_roots_matching()` stays fail-open on a missing or unreadable roots
  file. *Reason: documented contract — a config-file problem must never break
  every invocation.*
- A PR is authored under exactly one Claude account; PR cost must be
  single-account. `[engineer-verified]`
- No auto-discovery of accounts. *Reason: scanning a conventional sibling
  layout would bake one machine's directory scheme into a repo shipped to every
  stow user.* `[engineer-verified]`
- `cost-ledger --record`'s union figure and its git-committed storage stay as
  they are in this plan; the storage redesign is a separate effort.
  `[engineer-verified]` Mechanism 8 refuses the multi-root `--record` call
  shape without touching either — it is a narrow interlock against mechanism
  7 arming that path this round, not a step toward the redesign.

**Mechanisms:**

| # | Mechanism | Anchors | Why |
|---|---|---|---|
| 1 | `cost --summary` resolves roots to the active config dir only, enforced at both `_resolve_cost_roots` and `_cost_report` | root | Closes the cross-account consent hole; makes the companion PR safe |
| 2 | `_root_count_desc()` distinguishes *file absent* from *file present, contributed nothing* | root | The current string affirmatively denies a file that exists |
| 3 | Label + file-state accessors move into `_config_dir.py` | row 2 | `post-crash-sessions.py` imports only `_config_dir`; the label lives in the wrong module today |
| 4 | `post-crash-sessions.py` qualifies its scanned-dirs line with roots-file state | root | `N` alone cannot distinguish absent / declared-nothing / overridden |
| 5 | `analyze-context.py` prints scope before both not-found exits | root | "Not found" is the answer whose meaning depends on how many roots were searched |
| 6 | `transcript-narrative`, `transcript-analysis` SKILL.md, `README.md`, `docs/transcript-analysis.md` report observed scope instead of asserting union/unconditional coverage | root | Multiple public-repo surfaces assert coverage mechanism 1 makes false for `--summary`, or that was already false for the two lazy-header subcommands |
| 7 | install.sh gains one conditional clause inside the existing `CLAUDE_CONFIG_DIR`-divergence TIP | root | Nothing currently tells a diverged-profile user the file should exist; a second TIP block would be redundant with what that block already says |
| 8 | `cost-ledger --record` refuses (exit 2) when more than one root is in scope | root | Mechanism 7 nudges the exact action that would let `--record` union and commit another account's spend to a public file before the storage redesign lands |

**Assumptions:**

- `_resolve_cost_roots` unions `config_dir()` with every
  `declared_transcript_roots()` entry before applying `--config-dir` extras.
  `--summary` (checked in `_cost_report`, not `_resolve_cost_roots` — `summary`
  is read from `args` in both places) requires `--this-repo` and refuses any
  `--projects` value **other than** `None`/`*` (i.e. `--projects '*'` is
  accepted — the gate is `--this-repo`, not the absence of `--projects`), and
  separately refuses `--by-project`, `--no-redact`, and `--config-dir` in
  combination. `[verified: read of _resolve_cost_roots and cmd_cost's summary
  refusals this session; corrected from a round-1 misstatement]`
- `cost --summary` already publishes the root count: the per-root
  `cost: account-N: scanned N transcripts` print (redaction is always on under
  `--summary`, since `--no-redact` is refused, so the label is always an
  ordinal, never a path) goes to stdout, once per root, outside the
  `if not summary_mode:` suppression. Mechanism 1 removes the multi-root case
  rather than relabeling it. `[verified: read this session]`
- `_resolve_cost_roots` is shared by six subcommands
  (`_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`: `cost`, `context-distribution`,
  `edit-format`, `read-scope`, `subagents`, `subagent-mix`); `summary` is
  defined only on `cost`'s argparser, so `getattr(args, "summary", False)` is
  `False` for the other five and mechanism 1 cannot affect them.
  `[verified: staff-backend-engineer this session]`
- `_UNCONDITIONAL_HEADER_CASES` omits exactly four subcommands — `edit-format`,
  `read-scope`, `cost-ledger`, `sessions`; `review-trace` and `skill-invocation`
  are deliberately deferred. Its own header comment claiming "18 subcommands …
  the 19th and 20th funnel sites" is stale and must be corrected in this change.
  `[verified: staff-sdet count this session]`
- **Line numbers in `transcript-analysis.py` proved unreliable across reads this
  session** (multiple reviewers cited anchors off by up to 135 lines, and by
  round 2 the file had shifted further). Every anchor below is a **symbol
  name**; treat any line number as approximate and re-locate by symbol.
  `[verified: repeated drift observed this session]`
- `docs/cost-ledger.md`'s data table is empty today (schema only, no appended
  rows) and `_compute_cost_trend_data`/`cost-trend` do not read `summary_mode`,
  so mechanism 1 has no effect on `cost-ledger` or `cost-trend`.
  `[verified: read this session]`

**Corrections carried from review, round 2:**

- An earlier draft justified suppressing `scope_label` under `--summary` as
  identity-keyed because `this repo (N project dirs)` counts accounts holding
  the repo. False — the count comes from `git worktree list` and is local
  worktrees. The real identity-keyed input is a raw `--projects` value, already
  refused under `--summary`. Do not write the false rationale into the code
  comment.
- An earlier draft of the `--summary` refusal assumption said `--projects` is
  refused "including the default `*`" — backward. `*` and `None` are both
  *allowed*; `--this-repo` is the actual gate. Corrected above.
- An earlier draft's install.sh mechanism proposed a second TIP block gated by
  a suppression flag. Its own firing set was shown to be a strict subset of the
  existing divergence TIP's firing set, making the flag permanently dead and
  the "exactly one TIP" test requirement unsatisfiable against two sibling
  blocks. Redesigned per mechanism 7 below.

### Design calls

**Mechanism 1 is a scope change, not a disclosure change, and is enforced at
two points.** `_resolve_cost_roots` narrows the roots list under
`summary_mode`, gated on **both** `summary_mode` and `subcommand == "cost"` —
`_resolve_cost_roots` is shared by six subcommands
(`_SUBCOMMANDS_WITH_OWN_CONFIG_DIR`) and only `cost`'s argparser defines
`--summary` today, so a bare `summary_mode` check would silently narrow a
future subcommand that happens to add a same-named flag. `_cost_report` *also*
refuses (exit 2) if it is ever called directly with `summary_mode` true and
more than one resolved root. This mirrors an existing precedent in the same
function: `_cost_report` already re-asserts the `--no-redact` × multi-root
refusal despite `_resolve_cost_roots` enforcing it too, with the documented
reason "every direct caller of `_cost_report` (including this module's own
tests) bypasses that boundary." The same bypass applies to `summary_mode` — the
module's own test suite calls `_cost_report` directly with a `roots=` list, not
through `cmd_cost` — so the single-point fix leaves that call path trusting an
unenforced invariant. Two enforcement points, not one, closes it. The new
guard's test must assert its own distinct stderr message, with a fixture that
clears `_cost_report`'s two earlier summary-mode exit-2 paths (the
`--this-repo`/`--projects` refusal and the `--by-project`/`--no-redact`/
`--config-dir` refusal) — an exit-code-only assertion would pass even if the
new guard were never implemented, since either earlier path also exits 2.

Alternatives weighed: (a) permit `--config-dir` under `--summary` so the caller
scopes manually — rejected, correctness would depend on every caller
remembering a flag; (b) keep the union and disclose it — rejected per the
engineer's account-per-PR framing, it would publish non-consenting accounts'
spend and merely label it.

**Do not add a gate to `pr-description/SKILL.md`.** A review round proposed
making it ask before publishing a one-root figure. One root *is* the correct
scope for a PR; the gate would fire on the correct case forever. Mechanism 1
removes the defect at its source instead.

**Do not thread roots-file state through `render_report()` as a parameter.**
Call the accessor inside it. A new required argument means editing ~39
`render_report(` call sites in the test module for a one-line disclosure. Tests
already control the answer through the `TRANSCRIPT_CONFIG_DIRS_FILE` seam that
`conftest.py`'s package-scoped autouse fixture wires globally (and defaults to a
nonexistent path, i.e. the absent state), and `declared_roots_matching()` reads
`os.environ` at call time with no module-level cache, so the direct call
introduces no order-dependence.

**Derive file state from the same read mechanism `_config_dir` already uses for
parsing — not `Path.exists()`.** `.exists()` re-raises `OSError` on Python 3.12
but swallows it on 3.14 (this repo's CI runs 3.12), so a test for that catch
would pass on CI and be vacuous locally; and it returns `True` for a
present-but-unreadable file, which the parser fails open on, so the "present,
added nothing" state would be reported for a state the operator must actually
fix. Expose a tri-state (absent / unreadable / present) via its own `read_text`
call — a separate call, not shared plumbing with the parser, since sharing
would require restructuring `declared_roots_matching`'s signature. The tri-state
collapses "comments-only" and "present but every entry rejected" into one
"present, contributed nothing" state — both already produce their own per-index
stderr warning from the parser; do not add a fourth state to re-split them.

**Do not re-implement the roots-file parser in bash.** Hand-rolling tilde
expansion and path resolution in shell to test whether a *specific profile* is
declared would be a second source of truth for the file format — the exact
drift class this plan closes — and the fixture-marker test harness extracts the
block standalone, so such a helper could never be checked against
`_config_dir.py`. Mechanism 7 sidesteps this entirely: it adds no new parsing,
only one conditional clause to text already inside the existing divergence
block.

**Mechanism 7's shape, after review: nest inside the existing TIP, not a
second block.** The existing divergence TIP (fires when this profile's
`CLAUDE_CONFIG_DIR` differs from `~/.claude`) already ends with "add this
profile there, from the default profile, to include it in the union." A
diverged profile is the one case where "you should populate this file" is
actionable; a default-profile (non-diverged) user on a genuinely single-account
machine has nothing to add and would see a permanent, unsilenceable nag with no
action to take. Add one line to the existing TIP body, conditioned on the roots
file being absent (`[ ! -e "$roots_file" ]`), inside the same `if` that already
tests divergence — no second gate *on divergence itself* (a nested `if` for the
added line, inside that same block, is the same design, not a second block; do
not read "no sibling `if`" as forbidding it), no new suppression flag. Use a
nested `if`/`fi` for the added line, not a `[ ! -e "$roots_file" ] && echo …`
chain — the `&&` form's `set -e` safety today is positional (it happens to sit
non-final in the enclosing block) and would silently break if a later edit
reordered the block's statements; a proper `if` has no such dependency. This
means: no new test-count assertion, no existing test inverts (`roots_file` is
declared as `local roots_file=…` *after* the divergence block in the current
`check_transcript_config_dirs` and must be hoisted above it — settled, not
conditional), and the accepted miss is explicit: a multi-account operator
running install from the *default* profile (not diverged) gets no nudge. State
that miss in the TIP's own surrounding comment, not just here.

**Keep the scope-header formatter in `transcript-analysis.py`; move only the
label and file-state accessors down.** `_config_dir.py` deliberately returns
*config dirs*, never *projects dirs*, while the header is always called with
`projects/`-based paths — a formatter there would blur the distinction its
docstring draws. The label moves because the file it names is read exclusively
by `_config_dir.py`; `token-analyzer.py` and `analyze-context.py` already import
`_print_resolved_scope` from `transcript-analysis.py` via `importlib`, so the
boundary argument rests on the type distinction, not on consumer count.

**Contract-test the label, not the full sentence, against install.sh.**
`_root_count_desc()`'s rendered sentence (`1 root (no … declared)`) is a
scan-result string install.sh has no reason to echo. Pin
`TRANSCRIPT_CONFIG_DIRS_LABEL` (the bare file path, which the fixture block
already references multiple times) against the fixture block instead, and
assert it via the block's subprocess **stdout** in a diverged+absent fixture —
not a source-text scan, which would pass on dead code. Keep the SKILL.md arms
of the contract test as source-text pins (a SKILL.md has no executable form to
run).

## Critical files

**Reuse, don't reimplement:** `_root_count_desc()` (new, extracted from
`_resolved_scope_header`) becomes the single producer of the `1 root (no …
declared)` literal that `transcript-analysis/SKILL.md` pins verbatim. Every new
call site goes through it; the literal is never re-spelled.

- `claude/.claude/scripts/_config_dir.py` — add `TRANSCRIPT_CONFIG_DIRS_LABEL`
  (a display literal that stays `~/.claude/transcript-config-dirs` even under
  the `TRANSCRIPT_CONFIG_DIRS_FILE` test seam; name and docstring must say so,
  since the sibling accessor *does* honor the seam), `declared_roots_file()`,
  and a tri-state `declared_roots_file_state()` (absent / unreadable / present)
  via its own `read_text`. Rewrite the inline path expression in
  `declared_roots_matching` to use `declared_roots_file()`.
- `claude/.claude/scripts/transcript-analysis.py` —
  **(1)** in `_resolve_cost_roots`, when `summary_mode` is set (read via
  `getattr(args, "summary", False)`, matching the read already done in
  `_cost_report`), resolve to `[config_dir() / "projects"]` only, skipping the
  declared-roots union; **and**, in `_cost_report`, add a defense-in-depth
  refusal (exit 2) mirroring the existing `--no-redact` × multi-root guard —
  `summary_mode and len(scan_roots) > 1` refuses, with a comment naming the
  same "direct callers bypass `_resolve_cost_roots`" rationale the neighboring
  guard already states. **(2)** extract `_root_count_desc()` from
  `_resolved_scope_header`, making its one-root branch consume the roots-file
  tri-state so it never claims "no … declared" about a file that exists. Do
  **not** name the roots file in the `>1` branch — extra roots may come from
  `--config-dir`. **(3)** state the active-account scope on the `--summary`
  `Scope:` line, with the exact wording pinned by a test (a reader who drops
  `--summary` from the printed command gets a different, larger total — the
  wording must make that legible, not just present). **(4)** amend the
  `if not summary_mode:` suppression comment without the false `scope_label`
  rationale — name `--projects`/`_projects_glob` as the actually identity-keyed
  input and the existing `--projects` refusal as the enforcement point.
- `claude/.claude/scripts/post-crash-sessions.py` — in `render_report`, qualify
  the scanned-dirs line with roots-file state. **Attach the clause to both
  branches of `show_raw_config_dirs`** — the default single-account no-redact
  run takes the raw-paths branch, so a clause on the count branch alone would
  never fire in the headline case. Never name a declared path.
- `claude/.claude/scripts/analyze-context.py` — emit the scope header to
  **stderr** before both not-found exits, naming the subcommand token and
  `scope_label` the existing pins expect. The third early exit already prints
  its header. Note in the commit message that this makes one script print the
  header to stdout on success and stderr on failure, deliberately.
- `claude/.claude/skills/transcript-narrative/SKILL.md` — replace the
  multi-account guarantee at **both** sites with an instruction to record the
  observed `SESSIONS SOURCES` line (stderr) and say the corpus covers one
  account when it reads the one-root literal. Pin the instruction to the
  skill's own unglobbed invocation (it passes no `--projects`, so its
  `scope_label` is always `*`).
- `claude/.claude/skills/transcript-analysis/SKILL.md` — its scope-confirmation
  rule names a header that `--summary` suppresses; qualify it so the
  `--summary` carrier is the new `Scope:` clause. Separately correct the claim
  that "every subcommand's default scan corpus is a union" and that the header
  states the root count "unconditionally" — both untrue for `--summary` after
  mechanism 1, and the second was already untrue for the two lazy-header
  subcommands.
- `README.md:75` — carries the identical "states that root count
  unconditionally" / "all default to scanning the union" claims. Same
  correction, same file the plan would otherwise leave stale while fixing the
  SKILL copy — two independent reviewers converged on this site.
- `docs/transcript-analysis.md` — correct the top-of-doc union claim (~:3), the
  Corpus-scope section's "always states the root count" claim (~:40, in the
  section this plan already edits), the `--summary` refusal rationale (~:565,
  currently framed as "a code path `--summary` structurally never reaches" —
  after mechanism 1 the refusal is load-bearing for scope correctness, not
  incidental), and the `--summary` sample output. Note that `post-crash-sessions.py`
  reads the same file **with a looser `sessions/`-or-`projects/` predicate**, so
  its root count can legitimately differ from `transcript-analysis.py`'s, and
  that mechanism 1 narrows `--summary`'s root set, so the `account-N` ordinal it
  prints for the active dir is not guaranteed to match the ordinal a full
  (non-`--summary`) report on the same machine assigns the same physical root.
  **This ordinal-comparability claim exists at two sites, not one** — `~:45`
  ("Two redacted reports built from the same declared-roots file assign the
  same `account-N` to the same physical root") and `~:567` (the same claim
  restated) — both must scope the exception to exclude `--summary`; also note
  `~:47`'s "none of them narrows the union to one account short of the
  `--config-dir` escape hatch" now has a second narrowing path. `docs/scripts.md`
  gets a one-clause addition naming the `--summary` exception. Every corrected
  sentence, including README.md's, must scope the exception to `cost --summary`
  specifically, not to "the tools" generally — `cost-ledger`'s corpus stays a
  union and must not read as corrected by association.
- `install.sh` — inside the existing `INSTALL_TEST_FIXTURE:
  transcript-config-dirs` markers, add **one line**, as a nested `if`/`fi`
  (never a `[ ! -e "$roots_file" ] && echo …` chain — see Design calls), to the
  existing `CLAUDE_CONFIG_DIR`-divergence TIP body, conditioned on
  `[ ! -e "$roots_file" ]`, inside the same outer `if` that already gates
  divergence — no second gate on divergence itself, no new suppression flag,
  no new local variable beyond hoisting `roots_file`'s declaration (currently
  after the divergence block) above it.

- `claude/.claude/scripts/transcript-analysis.py` `_cost_ledger_report` —
  **(8)** after the `.cost-ledger-enabled` sentinel and `--machine-label`
  checks, refuse (exit 2) when `record` is set and `len(roots) > 1`, mirroring
  the same defense-in-depth shape as `_cost_report`'s `--no-redact` ×
  multi-root guard, with a message naming that the roots file must be scoped
  to one account for `--record` until the storage redesign lands. This is not
  the storage redesign — `docs/cost-ledger.md`'s union semantics and its
  git-tracked location are both unchanged; this only refuses the one call
  shape mechanism 7's nudge would newly make reachable. Non-`--record` reads
  are unaffected.

## Critical files (out of scope, flagged only)

- `claude/.claude/scripts/transcript-analysis.py` `cmd_cost_ledger`'s union
  semantics and `docs/cost-ledger.md`'s storage location — see **Out of
  scope**. Mechanism 8 above is the one narrow exception: it refuses a call
  shape, not a redesign.

**Tests** (`test-conventions` applies):

- `_config_dir.py`: the tri-state accessor across absent / unreadable /
  present; and that the label stays the `~/.claude` literal *when the seam
  points elsewhere*.
- **Mechanism 1 (the security-relevant one) — driven through `cmd_cost`/`main()`,
  not `_cost_report` directly**, since the module's own existing
  `TestCostSummary` tests call `_cost_report` directly and would exercise
  nothing of `_resolve_cost_roots`'s narrowing:
  - With two declared roots and matching project dirs (and nonzero-cost
    transcripts) under both, `cost --this-repo --branches <branch> --summary`
    totals equal the active-root-only total exactly (numeric equality, not a
    string-absence check), and the per-root `cost: account-N: scanned …` line
    appears once.
  - **Allow-path counterpart:** the same fixture, same command *without*
    `--summary`, still returns both roots' totals — proves the narrowing is
    `--summary`-specific, not a blanket regression.
  - `_cost_report(args_with_summary_true, roots=[a, b])` called directly, with a
    fixture that clears `_cost_report`'s two earlier summary-mode exit-2 paths
    (`this_repo=True`, `projects` in `(None, "*")`, no `by_project`/`no_redact`/
    `extra_config_dirs`), exits 2 via the new guard **and** asserts the guard's
    own distinct stderr message — exit-code-only would pass even with the new
    guard unimplemented, since either earlier refusal also exits 2.
  - `--summary` still exits 2 for `--config-dir`, `--no-redact`, `--by-project`,
    and any `--projects` value other than `None`/`*`.
  - One of `context-distribution`/`edit-format`/`read-scope`/`subagents`/
    `subagent-mix` still returns a multi-root result with a populated roots
    file — pins that mechanism 1 is `cost`-only.
- **Mechanism 8:** `.cost-ledger-enabled` set, two declared roots, `--record`
  exits 2 with a distinct message and appends no row; single root still
  records; a non-`--record` read with two declared roots still returns the
  union, pinning that mechanism 8 does not touch read-mode semantics.
- `_root_count_desc` unit tests including the **populated-but-additive-nothing**
  case, where the file exists and the text must not claim it is undeclared.
- A **contract test** deriving `TRANSCRIPT_CONFIG_DIRS_LABEL` and asserting it
  appears in the install.sh fixture block's subprocess stdout (diverged +
  absent fixture) — and separately deriving `_root_count_desc()`'s output and
  asserting it appears in both SKILL.md files' source text (a tripwire, not a
  guarantee — the verbatim positive pin on each skill's own scope-confirmation
  sentence is the real contract).
- **Shape-level redaction guard** over the full `--summary` stdout: no
  `/Users/`, no `/home/`, no `tmp_path` substring — a denylist of specific known
  strings would not catch a new line added to that path. Note the summary
  path's `WARNING: cost: account-N: no transcripts found …` line also reaches
  stdout and must pass the same guard.
- Extend `_UNCONDITIONAL_HEADER_CASES` with the four omitted subcommands and fix
  its stale header comment. `cost-ledger` can join by requesting the existing
  `cost_ledger_file` / `cost_ledger_enabled` fixtures alongside the
  parametrization.
- `post-crash-sessions`: absent / comments-only / populated / `--config-dir`
  explicit, exercising **both** `show_raw_config_dirs` branches; and the
  `--config-dir` provenance case driven through `main()`, not `render_report`
  directly.
- `analyze-context`: both not-found exits emit the header to stderr *before*
  the message; stdout stays empty.
- Skills: pin the new narrative sentence verbatim, plus a negative grep as a
  **tripwire** (catches re-adding the exact sentence, not a reworded one — the
  verbatim positive pin is the real contract, state this explicitly rather than
  implying the pair is complete).
- install.sh: **re-derive the affected-test list against the nested-clause
  design, not the earlier two-block design** — with the new line living inside
  the existing divergence `if`, none of the 8 existing tests changes
  assertions, **except** `test_diverged_config_dir_prints_tip`, which silently
  gains a second line in its TIP body (still passes its current `"TIP" in
  stdout` assertion, but add a case asserting the new line's content is present
  too, so the addition is actually tested). New case: non-diverged + absent
  stays silent (proves the miss for a default-profile multi-account operator is
  real and accepted, not accidental). The "a roots file naming a nonexistent
  directory does not abort" case belongs to `_config_dir.py`'s own suite, not
  install.sh's — `check_transcript_config_dirs` only tests `[ -e "$roots_file" ]`
  and greps its content, it never resolves a directory named inside the file,
  so this fixture can't exercise that path.

## Verification

```bash
.venv/bin/pytest claude/.claude/                             # full suite
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

Checkpoint after the `_root_count_desc` extraction and before its one-root
branch changes: the suite must be green with zero changed assertions, proving
the extraction alone is behavior-preserving — the existing direct unit tests
pinning the rendered header at 1 and 3 roots are that proof, and this holds
because `conftest.py`'s seam points at a nonexistent path (the absent state,
which mechanism 2 leaves unchanged).

End-to-end, the decisive manual check for mechanism 1 — create a throwaway
second config dir with a `projects/` subdirectory holding a transcript for this
repo, declare it in `~/.claude/transcript-config-dirs`, then:

```bash
python3 claude/.claude/scripts/transcript-analysis.py cost --this-repo --branches <branch> --summary
python3 claude/.claude/scripts/transcript-analysis.py cost --this-repo --branches <branch>
```

The first must scan one root and exclude the second dir's transcript; the second
(non-summary) must still union both. Then confirm the remaining surfaces name
the file:

```bash
python3 claude/.claude/scripts/post-crash-sessions.py
python3 claude/.claude/scripts/analyze-context.py --session-id no-such-session
CLAUDE_CONFIG_DIR=<a diverged dir> bash install.sh
```

Finally re-run install.sh with `CLAUDE_CONFIG_DIR` unset (non-diverged) and
confirm the new line does **not** appear. With the same two-root fixture,
`touch $CLAUDE_CONFIG_DIR/.cost-ledger-enabled &&
python3 claude/.claude/scripts/transcript-analysis.py cost-ledger --record
--machine-label test` must exit 2 and append no row.

## Out of scope

- **`cost-ledger --record`'s storage location.** ciso-reviewer identified the
  same shape as the PR-cost hole — a per-account sentinel gating a
  multi-account-union figure — but the figure is committed to
  `docs/cost-ledger.md`, a git-tracked file, permanently, rather than an
  editable PR body. Per engineer direction, the union figure is correct and
  intended (the ledger doc's own schema already frames a row as "one row per
  week per machine"); what needs to change is that the ledger stops being
  committed to this public repo. That is a storage-location redesign of a
  recently merged, separately designed feature
  (`.claude/plans/cost-trend-ledger.md`, PR #617) and does not belong in this
  diff — track as a dedicated follow-up plan. Mechanism 8 in this plan refuses
  the reachable-today call shape (`--record` with more than one root in scope)
  so mechanism 7's own nudge cannot arm it before that follow-up lands; the
  storage redesign itself remains the real prerequisite for the companion
  roots-file PR, same as mechanism 1 is.
- **Generating the roots file from the authoritative roster.** A companion PR in
  the private account-provisioning repo: enumerate its account rows, resolve each
  to a config dir via the mapping helper that repo already owns (it warns
  against a third copy of that mapping, so call the helper rather than restate
  it), and write `~/.claude/transcript-config-dirs` during account setup.
  **Mechanisms 1 and the cost-ledger storage follow-up above are both
  prerequisites** — landing the generator before the storage redesign lands
  would leave `--record` refusing outright on any machine with more than one
  declared account, rather than recording safely.
- Auto-discovery in `_config_dir.py` — explicitly rejected.
- Changing `declared_roots_matching()`'s fail-open behavior.
- A gate in `pr-description/SKILL.md` — mechanism 1 removes the need.
- `review-trace` / `skill-invocation` lazy headers — documented long-standing
  behavior, unrelated to root count.
- Reconciling `post-crash-sessions.py`'s looser validity predicate with
  `declared_transcript_roots()` — deliberate divergence; this plan documents it
  rather than changing it.
- A test guarding against a symlinked project directory re-widening
  `--summary`'s single root beyond the active config dir (ciso-reviewer, Low
  severity, no demonstrated exploit path beyond a hypothetical stale migration
  script) — accepted as out of scope; the existing dedup-by-realpath in
  `_config_dir.py` already collapses most symlink aliasing at the root level,
  and this plan does not change project-dir globbing.
