# Plan: close the TOCTOU commit-content bypass across all git-commit-gating hooks

## Context

`require-code-review.sh`'s PreToolUse gate evaluates `git diff --cached`
before the Bash tool call's shell command actually executes. A single
chained `git add <files> && git commit -m ...` call stages nothing until
after the hook's snapshot is taken, so a currently-empty `git diff --cached`
at hook time exits early via the hook's own "nothing to review" carve-out
(`require-code-review.sh` lines 83-87), skipping the marker check entirely
and letting an unreviewed commit land. This is confirmed via a live
incident on a separate account: a code-writer subagent ran
`git add <files> && git commit -m "..."` as one Bash call, producing an
unreviewed commit; that account's `.review-ledger-compliance.log` has no
entry spanning the commit's timestamp, consistent with the hook's
post-carve-out logging code never executing for that call. The
same snapshot-vs-actual-commit-content mismatch also affects `git commit -a`
/`--all` and bare-pathspec commit forms, which commit unstaged working-tree
content the hook's `git diff --cached` never captured — same file, same
hash-matching mechanism, a different trigger.

The engineer's initial direction was to extend the fix to two sibling hooks
that document the identical gap as an accepted, unclosed limitation
(`deny-pii-in-commits.sh`, `deny-private-project-refs.sh`), explicitly
routed through `plan-architect`'s own judgment on whether three hooks was
the right shape for one PR. `plan-architect` found the bypass is not
specific to those three: **nine** hooks gate `git commit` and all nine
derive their trigger or hash from `git diff --cached` at PreToolUse time —
`require-code-review.sh`, `require-skill-review.sh`, `check-skill-length.sh`,
`check-claude-md-length.sh`, `guard-settings-session-keys.sh`,
`require-plugin-version-bump.sh`, `require-npm-version-bump.sh`, plus the
two `deny-*` scanners above. `require-skill-review.sh` in particular is a
**full binary bypass** of the same incident class: an empty index at hook
time empties `SKILL_DIFF`, which exits the hook before its marker check
ever runs. `plan-architect` also corrected the evidence gathered for
`deny-private-project-refs.sh`: it is a **full** bypass, not a partial one
— with an empty staged diff, neither the diff nor the `-m` message text is
scanned at all.

The engineer confirmed the broader scope: fix the bypass class, not three
individual hooks. This plan builds one new shared gate rather than patching
each hook's logic individually, per the sections below.

## Approach

Close the bypass with **one new command-shape gate** —
`claude/.claude/hooks/deny-invisible-commit-content.sh` — that denies any
Bash call in which a `git commit` cannot be described by the
`git diff --cached` snapshot the PreToolUse gates read. Two command shapes
fail that test and both get denied: a fragment that mutates the index
chained ahead of the commit fragment, and a commit carrying
`-a`/`--all`/a pathspec. No other hook needs a logic change once the shape
gate exists: each hook's "nothing to review" carve-out becomes sound,
because an empty `--cached` at hook time then genuinely means an empty
commit.

Alternatives weighed and set aside:

- **Patch `require-code-review.sh`'s carve-out alone.** Leaves eight
  siblings bypassed, including the full binary bypass in
  `require-skill-review.sh`. Rejected under CLAUDE.md's "audit structural
  siblings before scoping a fix narrowly" rule.
- **A shared `_lib.sh` predicate called from all nine gates.** DRY on the
  detector but not on the response — nine deny messages, nine test
  matrices, and edits to three version-bump-gated plugins for one shape
  invariant.
- **A git `pre-commit` hook.** Sees the real index, but is not
  stow-installable, is defeated by `--no-verify`, and does not exist in the
  consuming repos these hooks protect.
- **`PostToolUse`.** Cannot block — the commit has already landed by the
  time it fires.
- **Folding the rule into `enforce-marker-script-shape.sh`.** Rejected:
  that hook's Stage 2 fast-exits anything not beginning with a `marker.sh`
  path, so the rule would inherit a hole, and that hook's identity is
  marker-invocation shape, not commit-content shape.

Two properties make the shape gate fit rather than merely work. The
sanctioned in-chain marker exception (`require-code-review.sh:89-97`,
`marker.sh write code-review && git commit`) survives untouched by
construction, not by a special case: that chain contains no git fragment
other than the commit and no worktree target, so the new gate never fires
on it. And the `-a`/pathspec arm removes the need to scan non-git
fragments: only git can mutate the index, so the `--cached` arm needs only
a git-fragment walk, while a case where an arbitrary preceding command
matters (`sed -i x && git commit -a`) is already denied by the commit form
itself.

### Assumption ledger

**Root:** a PreToolUse gate's `git diff --cached` snapshot is not the
content the gated `git commit` will record, so every marker, hash, and
content scan built on that snapshot can be satisfied by a state nobody
reviewed.

**Givens:**
- The harness evaluates PreToolUse before the shell command runs, and
  offers no post-execution block. Harness-imposed; dissolving it is outside
  this repo.
- Hooks receive command text, never the shell's post-expansion argv.
  Harness-imposed; every text-matching gate in this repo already inherits
  it.
- Everything under `claude/` installs to every stow consumer, so a new deny
  changes behavior for all of them, not for the session owner alone. Repo
  `CLAUDE.md`, "Plans in this repo affect all stow users."

**Mechanisms:**
- New standalone gate rather than per-hook patches — lighter primitives
  rejected above (patch one hook, shared predicate across nine, git
  `pre-commit`, `PostToolUse`), each named with its failure.
- Reuse `_lib_split_fragments` / `_lib_fragment_invokes_git` /
  `_lib_extract_git_subcmd` / `_lib_readonly_git_subcmds` rather than a new
  git parser. Write no new git command parsing.
- Promote `commit_fragment_has_worktree_target` from
  `deny-pii-in-commits.sh` into `_lib.sh` rather than copying it — two
  consumers is the threshold CLAUDE.md sets for a shared helper.

**Rows:**

1. A chained `git add <files> && git commit` in one Bash call leaves
   `git diff --cached` empty at hook time, taking `require-code-review.sh`'s
   line-85 carve-out and skipping the marker check entirely.
   [engineer-confirmed: live incident on a separate account, an
   unreviewed commit landed with no `.review-ledger-compliance.log`
   entry spanning its timestamp] — corroborated by
   [verified: `require-code-review.sh:83-87, 99-100`].
2. Eight further hooks gate `git commit` and derive their trigger or hash
   from `git diff --cached` at hook time, so all share the bypass.
   [verified: grep of `(^|&&?|;|\|\|?)\s*git\s+commit` across `*.sh` → 7
   hooks; grep of `diff --cached` across hooks → same set;
   `require-skill-review.sh:108-112` confirmed to exit 0 on an empty
   `SKILL_DIFF` before its marker check]
3. `deny-private-project-refs.sh` is a full bypass, not a partial one —
   with an empty staged diff neither the diff nor the `-m` message is
   scanned. [verified: `deny-private-project-refs.sh:443, 455, 462,
   489-493, 577`]
4. `deny-pii-in-commits.sh`'s `commit_fragment_has_worktree_target` (lines
   142-175) is a correct argv walker for `-a`/`--all`/`--`/bare-pathspec
   detection, including short-flag bundles and value-consuming options.
   [verified: `deny-pii-in-commits.sh:142-175`;
   `test_deny_pii_in_commits.py:422, 438, 517`]
5. Only `git` can mutate the index, so a git-fragment-only walk is
   sufficient for the `--cached` arm; arbitrary preceding commands matter
   only for `-a`/pathspec forms, which are denied on their own trigger.
   [verified: reasoning over the two arms; no counterexample found in the
   hook corpus]
6. `_LIB_READONLY_GIT_SUBCMDS`'s complement over-approximates "mutates the
   index" — `push`, `stash list`, and similar non-index writes are treated
   as mutating. Accepted over-denial: the cost is splitting one Bash call
   into two, and the closed-enumeration discipline means an unrecognized
   subcommand defaults to denied. [verified: `_lib.sh:1575-1628`]
7. `_lib_fragment_invokes_git` matches `git` as *any* word, so
   `echo "git add ." && git commit` false-denies. Accepted:
   `deny-pii-in-commits.sh` already uses the identical walk and inherits
   the same property, and `_lib.sh:586-590` documents the any-word choice
   as deliberate for git. [verified: `_lib.sh:525-538, 586-590`;
   `deny-pii-in-commits.sh:183-191`]
8. No skill body prescribes the chained-add-and-commit form, so denying it
   breaks no documented skill workflow. [verified: grep
   `git add.*&&.*git commit` under `claude/.claude/skills` → no matches]
   The grep scope above does not extend to `docs/`: `docs/hooks.md`'s own
   gate-deadlock recovery recipe (the "commit the plan file" option)
   prescribed exactly this chained form, and this gate would have denied
   it. Fixed by splitting that recipe into two separate commands (see
   Critical files below).
9. A new hook must carry `# hook-class: gate` on line 2, hold its own entry
   in `docs/hooks.md`, be wired into a PreToolUse matcher group in
   `claude/.claude/settings.json`, and deny on malformed input, empty
   stdin, non-object `.tool_input`, and a missing `_lib.sh`. The `deny-`
   filename prefix makes the `gate` class mandatory.
   [verified: `test_hook_alignment.py:1-20, 75-107, 114-120`]
10. `test_require_code_review.py:74`
    (`test_chained_add_commit_allowed_when_marker_current`) stays green: it
    runs `require-code-review.sh` in isolation, and that hook's logic is
    unchanged. Its name becomes misleading at the repo level, which the
    one-line carve-out comment addresses.
    [verified: `test_require_code_review.py:74-86`]
11. Running the detector against `_lib_strip_shell_quotes` output rather
    than raw `$COMMAND` closes `bash -c "git add x && git commit"`, which
    the raw walk would miss on the leading-quote token.
    [verified: `_lib.sh:1338-1342`; `enforce-marker-script-shape.sh:452` and
    `deny-pii-in-commits.sh:310` both already do this]
12. Whether `test_doc_counts.py` asserts a hook count that a new hook would
    invalidate. [unverified] — resolve by running it, not by reading a
    number out of a doc.
13. `require-skill-review.sh:83` uses the byte-identical trigger regex
    `(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)` as `require-code-review.sh:64`,
    grounding the assumption that copying one hook's fast-reject regex into
    the new gate gives detection parity across all nine consumers, not just
    the one it was copied from. [verified: `require-skill-review.sh:83`,
    `require-code-review.sh:64`]
14. **Accepted residual risk — operational SPOF:** the new gate is a hard
    single point of failure for the other eight hooks' "empty diff means
    empty commit" soundness. If it's later removed from `settings.json`,
    overridden via a personal `settings.local.json`, or fails at runtime on
    unanticipated input, all eight revert to their pre-fix bypassable state
    with no independent backstop — this is the accepted cost of the
    DRY-on-response design (see Approach's rejected-alternatives list), not
    an oversight. Mitigated by `test_hook_alignment.py`'s PreToolUse-wiring
    check (row 9, catches removal from the canonical `settings.json`) and
    by this hook's own fail-closed-on-malformed-input behavior; not
    mitigated against a per-machine `settings.local.json` override. This
    design also assumes the harness runs every matched `PreToolUse` hook
    for a single tool call and denies on any single hook's deny — confirm
    this against the existing multi-hook `Bash` matcher group's observed
    behavior (multiple gates already coexist there) before implementation
    relies on it as fact rather than inference.

    **Second, distinct residual risk — detection correctness:** a
    present, correctly-wired, cleanly-parsing gate can still fail to close
    the bypass it exists for. The ordered fragment walk (row 1's design,
    "stopping at the first fragment whose `_lib_extract_git_subcmd` is
    `commit`") on its own allows
    `git commit -m x && git commit -a --amend --no-edit`: the walk exits
    unconditionally at the first, clean commit and never inspects the
    second, unreviewed one — a full bypass of `require-code-review.sh` and
    every sibling gate, with the gate itself healthy and correctly
    installed. Neither of row 14's operational mitigations (the wiring
    check, fail-closed-on-malformed-input) would have caught this, since
    both verify the gate is present and running, not that its logic is
    correct. Closed by adding a second, independent check ("Arm 2" in the
    hook's own header) that counts git-commit-invoking fragments over
    quote-masked text and denies more than one anywhere in the command.
    The adversarial multi-commit-chain fixtures in
    `test_deny_invisible_commit_content.py` are this risk category's
    ongoing mitigation going forward.

    **Third, distinct residual risk — false-deny on ordinary commits:**
    the same detection-correctness category cuts both ways. Arm 1 passed
    the quote-*stripped* commit fragment to the promoted worktree-target
    helper, whose `xargs -n1` tokenizer relies on real shell quoting to
    keep a multi-word `-m` value as one token — with quotes already
    stripped, `git commit -m "fix a real bug here"` tokenized into seven
    bare words and false-denied on the first trailing one, a false
    positive on essentially any ordinary multi-word commit message.
    [verified: direct reproduction against the hook — a synthetic
    PreToolUse payload for `git commit -m "fix a real bug here"` denied
    pre-fix and allows post-fix]. Closed
    by capturing arm 2's masked (quote-intact) counterpart of the same
    fragment into `DIRECT_MASKED_COMMIT_FRAGMENT` and passing that to the
    worktree-target check instead, but only when the fragment is a
    confirmed **direct** `git` invocation (`_lib_fragment_invokes_tool
    ... git`) — never a `bash -c`/`eval` wrapper, since masked- and
    stripped-space are byte-identical only outside quotes, and
    substituting a wrapped invocation's masked text risks swapping in an
    unrelated invocation's flags. Pinned by
    `test_wrapped_dirty_commit_then_clean_direct_commit_denied`, which
    confirms the guard prevents exactly that swap.

    **Fourth, distinct, deliberately-accepted residual risk — wrapped-
    invocation and quote-embedded-decoy blind spots:** `_mask_shell_quotes`
    collapses an entire quoted span to nothing, so a real `git commit`
    invoked inside a code-executing wrapper's quoted argument (`bash -c
    "git commit ..."`, `eval "git commit ..."`, and similar) is invisible
    to arm 2's count — a two-commit chain where either commit is wrapped
    this way is not detected by either arm, e.g. `git commit -m "fix" &&
    bash -c "git add secret && git commit -m y"`. Separately, arm 1's
    ordered walk classifies fragments over quote-*stripped* text, so a
    quoted argument to an unrelated command that happens to contain the
    literal text "git commit" becomes an indistinguishable fake commit
    fragment after stripping, e.g. `echo "foo && git commit" && git add
    secret && git commit -m x` — if it's the first commit-shaped fragment
    the walk reaches, the walk stops there and never inspects a real,
    later mutation-then-commit sequence. Both are accepted rather than
    fixed: this repo's hooks assume a cooperative agent, not one
    deliberately constructing shell indirection or a decoy quoted string
    to evade a gate, the same posture `require-respond-pr.sh`'s header
    states explicitly ("Threat model: cooperative, not adversarial").
    Disclosed in the hook's own "Known gaps" header comment rather than
    fixed.

## Critical files

Paths are repo-relative.

**Create**

- **`claude/.claude/hooks/deny-invisible-commit-content.sh`** — the shape
  gate. `# hook-class: gate` on line 2 (mandatory for the `deny-` prefix,
  row 9). Order of operations: filter `TOOL_NAME != Bash` → exit 0; the
  shared raw-`$COMMAND` `git commit` fast-reject grep (copy the regex
  verbatim from `require-code-review.sh:64` so all eight commit gates keep
  one detection shape); then `_lib_strip_shell_quotes` and
  `_lib_split_fragments`; then walk fragments **in order**, stopping at the
  first fragment whose `_lib_extract_git_subcmd` is `commit`. Deny on any
  earlier fragment that `_lib_fragment_invokes_git` and whose subcommand is
  absent from `_lib_readonly_git_subcmds`; deny on the commit fragment
  itself if the promoted worktree-target helper returns true — passing the
  masked (quote-intact) fragment captured during arm 2's walk
  (`DIRECT_MASKED_COMMIT_FRAGMENT`) rather than the quote-stripped one when
  the fragment is a confirmed direct `git` invocation, since the
  worktree-target helper's `xargs -n1` tokenizer needs real quoting to keep
  a multi-word `-m` value as one token (ledger row 14's third paragraph);
  falling back to the stripped fragment for a wrapped/indirect invocation
  (`bash -c "..."`, `eval ...`), where no masked counterpart can be trusted
  to belong to the same invocation.
  Order-sensitivity is load-bearing, not incidental: a `git add` *after*
  the commit fragment is harmless, and quote-stripping a commit message
  containing `&&` can synthesize exactly that. A second, independent check
  (Arm 2) runs over quote-*masked* — not stripped — text: it counts
  git-commit-invoking fragments anywhere in the command and denies if more
  than one appears, closing the gap the ordered walk alone cannot see past
  its own stopping point (ledger row 14's second paragraph).
  - **Reuse:** `emit_deny` bootstrap + `_lib_emit_deny` re-point,
    `_lib_parse_tool_input_or_deny`, `_lib_strip_shell_quotes`
    (`_lib.sh:1338`), `_lib_split_fragments` (`_lib.sh:575`),
    `_lib_fragment_invokes_git` (`_lib.sh:525`), `_lib_extract_git_subcmd`
    (`_lib.sh:547`), `_lib_readonly_git_subcmds` (`_lib.sh:1626`). Copy the
    fragment-walk loop shape from `deny-pii-in-commits.sh:183-191` and add
    the ordering guard. Write no new git parsing.
  - **Deny messages** must name the remedy, not only the rule. Chained
    form: staging must run as its own Bash tool call, with `git commit` as
    a second call, because the commit gates read `git diff --cached`
    before this call executes. Worktree-target form: stage explicitly
    first, then commit with no `-a` and no pathspec.
  - **Header** must document, per this repo's hook convention (one
    sentence each): the six known gaps this gate does not close —
    `git commit --amend` folding HEAD's tree in with no preceding `-a` or
    chained mutation; quote/indirection obfuscation of the commit
    detection itself; `git -C <other-repo> commit`; a `$(...)`/backtick
    substitution inside a commit's own arguments that itself runs a real,
    mutating git command (e.g. `git commit -m "$(git add f; echo x)"`),
    which executes before the commit runs and is not inspected; the
    accepted false-positive class from ledger row 7 (`echo "git add ." &&
    git commit` false-denies because `_lib_fragment_invokes_git` matches
    `git` as any word, matching `deny-pii-in-commits.sh`'s identical
    behavior), distinguished from quote-masking correctness (which
    correctly tracks quote state regardless of an embedded quote of the
    other type or a multi-line span); and the fail-open timeout exposure —
    none of this hook's own forks carries an internal timeout, and per the
    harness's PreToolUse contract a timed-out command-type hook is skipped
    rather than denied, so a wedged fork silently stops the gate rather
    than blocking, a pre-existing exposure shared by every sibling
    always-on commit gate. A reader of the hook file alone, without the
    plan, must see these without re-deriving them.
  - **Subprocess footprint**, once the fast-reject grep matches: forks
    `sed`/`tr` (via `_lib_strip_shell_quotes`), two more `sed` calls (via
    `_lib_split_fragments`), and `xargs`/`awk` per commit fragment (via the
    promoted worktree-target helper) — all pure string processing with no
    filesystem or network access, so none needs the `_lib_capped`/`timeout`
    wrapping `_lib_jq` gets. Not "no subprocess beyond jq": five more
    subprocess kinds fork on a matched call, at negligible but non-zero
    cost. This hook fires unconditionally on every Bash tool call, not
    only commit-shaped ones, and none of its own subprocess forks carries
    an internal timeout. Per the harness's PreToolUse contract
    (`code.claude.com/docs/en/hooks`, fetched 2026-09-01), a timed-out
    command-type PreToolUse hook is canceled with its output discarded,
    and the tool call proceeds through normal permission flow rather than
    being blocked — so a wedged or replaced `grep`/`sed`/`tr`/`xargs`/`awk`
    binary is not bounded by the harness's 600-second default PreToolUse
    hook timeout the way a fail-closed check would be; a timeout here just
    means this gate silently did not run. The masking awk script's
    per-character scan is O(n²) on command length (empirically ~12s at
    500KB input on this machine's `/usr/bin/awk`) — accepted because
    reaching that timeout at this scaling would require a multi-megabyte
    single command, well outside normal usage.
- **`claude/.claude/hooks/tests/test_deny_invisible_commit_content.py`** —
  deny fixtures: `git add f && git commit -m x`;
  `git add f ; git commit -m x`; `git commit -am x`; `git commit -a -m x`;
  `git commit -m x -- file.txt`; `git commit -m x file.txt`;
  `bash -c "git add f && git commit -m x"`;
  `marker.sh write code-review && git commit -am x`;
  `git add f && git commit --amend --no-edit` (chained-add arm denies
  regardless of `--amend`); `git commit -a --amend --no-edit` (worktree-
  target arm denies regardless of `--amend` — the mechanism is
  `--amend`-agnostic; only the bare, unmodified amend form below is
  allowed); `git commit -m x && git commit -a --amend --no-edit` (two
  chained commits — arm 1 alone stops at the first, clean commit and
  would allow this); `git commit -m x && git add secret && git commit
  --amend --no-edit` (a mutation fragment sitting between two commit
  fragments, past the point arm 1's ordered walk stops); `git commit -m x
  && git commit -m y -- file.txt` (second commit carries a pathspec);
  `git commit -m x && git commit -m y && git commit -m z` (three chained
  commits); a deny-message fixture over the two-chained-commits case
  asserting the reason names the fragment count and "own, separate Bash
  tool call"; `git commit -m "hello && git commit -a --amend` (an
  unpaired quote finds no closing match for arm 2's masking regex and is
  left unmasked, leaning the fragment count toward denying); a
  malformed-JSON-input fixture pinning the hook's fail-closed-on-
  subprocess-failure discipline (`_lib_parse_tool_input_or_deny`'s JSON
  parse exiting non-zero denies rather than allowing an unscanned
  commit); `git commit -m 'x"y' && git commit --amend --no-edit -m 'p"q'`
  (cross-quote-type parity: a `"` embedded in one real single-quoted `-m`
  value must not falsely pair with an unrelated `"` in a second, pinning
  the masking fix); `git commit -am "fix a real bug"` (multi-word message
  with a real `-a` — the multi-word-message fix must not suppress this);
  `bash -c "git commit -a -m x" && git commit -m y` (a wrapped, dirty
  first commit followed by a clean direct second commit — pins that the
  masked-fragment substitution's direct-invocation guard never swaps the
  wrapped commit's `-a` for the clean one's flags); one
  `build_path_without`-style deny fixture per fork point (fast-reject
  `grep`, the masking implementation, `_lib_strip_shell_quotes`'s
  `sed`/`tr`, `_lib_split_fragments`, the worktree-target helper's
  `xargs`/`awk`), matching this repo's established missing-binary test
  convention (`test_lib.py`, `test_ask_new_dependency_disclosure.py`,
  and others already use `helpers.build_path_without`). Allow fixtures:
  `git commit -m x`; `git commit --amend --no-edit`;
  `git status && git commit -m x`; `git fetch && git commit -m x`;
  `git diff --cached && git commit -m x`;
  `~/.claude/scripts/marker.sh write code-review && git commit -m x`;
  `git commit -m "fix && git add"` (trailing mutation, allowed by the
  ordering guard); `git commit -m "$(cat <<'EOF'\ngit add\nEOF\n)"`
  (heredoc-built commit message mentioning `git add` as inert text — this
  is the dedicated allow fixture the Out-of-scope section's `$(...)` gap
  references, pinning that gap's tradeoff: closing it would deny this
  standard heredoc-message idiom whenever the message text happens to
  mention a git command); the same heredoc shape with "git commit" (not
  "git add") as the inert mentioned text, distinctly pinning that the
  masking fix — not just the fast-reject/subcommand-classification logic
  — correctly handles the commit-shaped word too; `git commit -m "fix a
  real bug here"` and the same message chained after the sanctioned
  marker prefix (multi-word `-m` values must survive the worktree-target
  check's tokenization); a commit message containing a real embedded
  newline (masking collapses the whole quoted span, newline included,
  before fragment-splitting ever runs); every non-Bash and non-commit
  payload. Mirror
  the fixture/helper conventions in `test_deny_pii_in_commits.py` and
  `test_require_code_review.py`.

**Modify**

- **`claude/.claude/hooks/_lib.sh`** — promote
  `commit_fragment_has_worktree_target` from
  `deny-pii-in-commits.sh:142-175` verbatim as
  `_lib_commit_fragment_has_worktree_target`, placed beside the existing
  git-fragment primitives. Preserve its captured-stage/SIGPIPE comment
  (lines 143-148) — that comment records a live constraint, not a
  rationale.
- **`claude/.claude/hooks/deny-pii-in-commits.sh`** — replace the local
  helper with the `_lib.sh` call at its one call site (line 188).
  Behavior-neutral. Separately, its "Known gap" bullet at lines 92-94
  describes behavior that no longer holds once the shape gate ships;
  rewrite it in one sentence to name the gate that now denies the shape.
- **`claude/.claude/hooks/deny-private-project-refs.sh`** — comment only,
  no logic change. The "preserve their historical 'let git decide' pass"
  note at lines 468-471 and the empty-target note at lines 489-493 both
  now describe a *sound* carve-out rather than an accepted gap; add one
  sentence naming the gate that makes the empty staged diff trustworthy.
- **`claude/.claude/hooks/require-code-review.sh`** — comment only, no
  logic change. One sentence at the line-83 carve-out naming the gate that
  makes an empty `--cached` mean an empty commit, so a future reader does
  not remove either half independently.
- **`claude/.claude/settings.json`** — register the new hook in the
  `PreToolUse` `"matcher": "Bash"` group (the block beginning at line 192),
  with `"command": "~/.claude/hooks/deny-invisible-commit-content.sh"`
  matching the sibling entries in the same block. No `"if"` condition: the
  internal fast-reject is the authoritative filter, matching
  `deny-pii-in-commits.sh`'s own defense-in-depth (its `TOOL_NAME != Bash`
  check at lines 134-136 and git-fragment walk at lines 181-195 — that file
  has no `"if"`-is-a-hint-only header comment) and
  `enforce-marker-script-shape.sh`'s pattern (the "if field is a hint only"
  warning at lines 64-67, backed by the one-line fast-reject filter at line
  501: `printf '%s' "$COMMAND" | grep -qF 'marker.sh' || exit 0`) rather
  than an `"if": "Bash(git commit *)"` entry. Add a `statusMessage`.
- **`docs/hooks.md`** — new entry for the hook (mandatory, row 9). This is
  the single canonical home for the cross-cutting statement that every
  commit gate's `--cached` snapshot depends on this shape gate; the four
  in-hook comments above point here rather than restating it. Also split
  the gate-deadlock recovery recipe's `git add ... && git commit -m ...`
  (row 8) into two separate commands — the new gate now denies that
  chained form.

**Deliberately not modified:** `plugins/skill-management/hooks/require-skill-review.sh`
and the two version-bump plugin hooks
(`plugins/plugin-semver/hooks/require-plugin-version-bump.sh`,
`plugins/npm-semver/hooks/require-npm-version-bump.sh`). They are
protected by the new gate without a code change, and touching a plugin
file would force a `plugin-semver` version bump and pull
`require-plugin-version-bump.sh` into this PR for a comment.

**Dispatch split:** one `code-writer` dispatch. The hook, its test, the
`_lib.sh` promotion, the four comment edits, `settings.json`, and
`docs/hooks.md` are one non-partitionable file set — the deny-message
text, the doc entry, and the test fixtures each restate the others'
decisions, so splitting would make two agents resolve the same open
questions independently.

## Verification

Per this repo's `CLAUDE.md` Commands block, run the scoped selector, not
the full suite:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck
```

`select-tests.py` maps `claude/.claude/hooks/**` changes onto
`claude/.claude/hooks/tests/`, which covers the new test file plus
`test_hook_alignment.py` (docs entry, `hook-class` header, PreToolUse
wiring, and the four fail-closed behavior checks from row 9),
`test_lib.py`, `test_require_code_review.py`, `test_deny_pii_in_commits.py`,
`test_deny_private_project_refs.py`, and `test_shellcheck.py`. Do not widen
to `pytest claude/.claude/` by hand — a path the selector cannot map is a
bug in its rule table, not a licence to widen.

Two checks the selector does not decide for you:

1. **`test_doc_counts.py`** — resolves ledger row 12. If it asserts a hook
   count, update the asserted figure in the same commit; if it does not, no
   action. Determine this by running it, not by reading a number out of a
   doc.
2. **End-to-end bypass reproduction.** Before the fix, in a scratch repo
   with a clean index, confirm `require-code-review.sh` returns allow for
   `git add file.txt && git commit -m x` with no marker present. After the
   fix, confirm the new hook returns deny for that same payload while
   `require-code-review.sh` in isolation still returns allow — the split
   verdict is the design, and asserting only the composite would hide
   which hook is doing the work.

Also confirm by inspection that the sanctioned chain still passes end to
end: `~/.claude/scripts/marker.sh write code-review && git commit -m x`
must be allowed by `enforce-marker-script-shape.sh`, `require-code-review.sh`,
and the new gate simultaneously.

No SKILL.md and no plugin-directory file changes, so `/skill-review` and
`plugin-semver` are not triggered for this diff
(`.claude/rules/review-pipeline-dispatch.md`). `/code-review` still
applies.

## Out of scope

- **Bare `git commit --amend` (no `-a`, no chained mutation) folding
  HEAD's tree into the commit.** An amend records content the `--cached`
  snapshot never described, but that is a property of the marker design,
  not a time-of-check race, and denying it would break the
  amend-message-only flow every commit gate's empty-diff carve-out was
  built to permit. Left as-is deliberately. This exclusion does **not**
  extend to `--amend` combined with `-a` or a chained staging mutation —
  both are already denied by the same mechanism that denies their non-amend
  form (the worktree-target check and the ordering guard are
  `--amend`-agnostic), and both have deny fixtures in the test file above.
- **Quote-and-indirection obfuscation of the commit detection itself**
  (`g"it commit"`, a shell variable holding the git path, a heredoc body
  piped to an interpreter). The new gate's fast-reject inherits exactly the
  surface the seven existing commit gates already have, so this is a
  pre-existing class, not one this change introduces.
  `enforce-marker-script-shape.sh`'s header documents the same class for
  its own arm.
- **`git -C <other-repo> commit`.** The gates hash the session's repo, not
  the `-C` target — documented at `deny-pii-in-commits.sh:97-101`.
  Unchanged here.
- **A `$(...)`/backtick substitution inside a commit's own arguments that
  itself runs a real, mutating git command** (e.g.
  `git commit -m "$(git add f; echo x)"`). The substitution executes
  before the commit runs and is not inspected — this is not the same
  class as the quote/indirection gap above, since the commit itself *is*
  correctly detected here; the gap is specifically that a real, executing
  side effect hidden inside one of the commit's own arguments is invisible
  to the fragment walk. Left open deliberately: closing it would deny the
  standard `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc idiom whenever
  the message text happens to mention a git command, which is a common,
  legitimate pattern. Pinned by a dedicated allow fixture in the test file
  above naming the tradeoff.
- **Removing the empty-staged-diff carve-outs.** Tempting once the shape
  gate lands, but they remain correct for genuine `--allow-empty` and
  amend-message-only commits; the plan makes them sound rather than
  deleting them.
- **Migrating the other eight commit gates onto a shared
  `git diff --cached` accessor.** Real duplication, but a refactor of eight
  security-critical files does not belong in a bypass fix. Raise
  separately.
- **Backfilling review coverage for the incident commit.** Incident
  remediation on the affected account, not a repo change.
- **Closing the wrapped-invocation and quote-embedded-decoy detection
  blind spots** (ledger row 14's fourth paragraph): a real commit wrapped
  in a code-executing quoted argument (`git commit -m "fix" && bash -c
  "git add secret && git commit -m y"`) is invisible to arm 2's masked
  count, and a decoy fragment manufactured from quoted text that merely
  contains the literal string "git commit" (`echo "foo && git commit" &&
  git add secret && git commit -m x`) can make arm 1's ordered walk stop
  before it ever reaches a real, later commit. Both require an agent
  deliberately constructing shell indirection or a decoy string to defeat
  the gate rather than writing an ordinary command, so both are accepted
  under this repo's cooperative-agent threat model rather than closed —
  the same posture `require-respond-pr.sh`'s header states explicitly.
