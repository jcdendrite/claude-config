# Generalize `enforce-marker-script-shape` to permit all-marker-op chains (GH-423)

## Context

**Goal:** Replace the narrow same-skill `write↔deactivate` carve-out in
`enforce-marker-script-shape.sh` with one general rule — *a chain composed
entirely of individually-valid `marker.sh` operations is permitted* — so the
94%-false-positive hard-denials the census measured stop firing, without
loosening the gate against non-marker chaining.

**Why now:** The 2026-07-02 denial census (1120 denials / 335 sessions) found
`enforce-marker-script-shape` produced 206 denials, 193 (94%) false positives;
188 of those were commands where **every `&&`-joined segment was an
individually-sanctioned `marker.sh` shape** (e.g. `write ready-for-review &&
deactivate ready-for-review` in one Bash call). Sessions recover only by
splitting and re-running the identical operations. This is the *fourth* round
of evidence (#187, #300, #362, #372 each added one carve-out) — the classic
"compounding defensive layers on one mechanism" tell that the repo's own
CLAUDE.md flags as a signal to fix the foundation, not add a fifth carve-out.

**Intended outcome:** The hook denies a `marker.sh` command **only** when a
segment is not a valid marker operation (non-marker command, invalid
op/target combo, path traversal, redirect, extra args) — never merely because
two-or-more valid marker ops were chained. The blessed `write … && git commit`
chain and all existing denials of genuinely-bad shapes are preserved
byte-for-byte.

### Why this is capability-neutral (the security argument)

- `permissions.allow` lists the **12 single shapes as exact-match strings**
  (`claude/.claude/settings.json:4-15`) — no globs, no chain entries.
- The hook **never emits an explicit `allow`**; on a valid shape it exits 0
  and defers to the permission layer (`run_hook`→`"allow"` means "hook did not
  deny", `claude/.claude/tests/helpers.py:110-111`).
- **The regex admits only marker ops — no non-marker segment can ride a
  matching chain.** Both a CISO and an SDET adversarial pass confirmed this:
  every segment must carry the full path prefix + a valid `(op,target)` shape;
  bare/non-marker RHS, `||`/`;`, redirects, and metacharacters are structurally
  excluded by the character classes. This is the hook's real job — *keep
  `marker.sh` from being a wedge for arbitrary chained commands* — and it is
  fully preserved.
- Therefore a chain of only-valid marker ops has an **end state identical to
  running each op separately**, and each op is already independently
  sanctioned. Permitting the chain grants nothing new — it removes a false
  "forbidden" signal. (This is the exact rationale #362 already used for the
  same-skill pair; the ticket generalizes it.)

**Permission-layer handoff — confirmed against the primary source.** Removing
the hard-deny means these chains reach the permission layer. The hook's
exit-0 set is *slightly larger than the allowlist*: it also defers
`clear-stale`, `clear-stale --dry-run`, and the absolute-path form — none of
which are in `permissions.allow`. Claude Code's official docs settle the
exact compound-command question this plan initially left as an untested
assumption: [Configure permissions — Bash → Compound
commands](https://code.claude.com/docs/en/permissions#compound-commands)
states "a rule must match each subcommand independently" for `&&`/`||`/`;`/
`|`/`|&`/`&`/newline-joined commands — a first-segment match never widens
approval across the separator. So `write plan-review && clear-stale` gets
`write plan-review` auto-approved by its own allow entry and `clear-stale`
falls through to the **same permission prompt a bare `~/.claude/scripts/
marker.sh clear-stale` call already goes through today** (it's valid-shape
but not allowlisted right now too) — no widening, no auto-approval of
anything new. Combined with the regex excluding every non-marker segment, no
escalation or gate-bypass is reachable through a chain that the agent could
not already reach by running the ops singly. The Verification section
records the citation and a targeted spot-check.

## Approach

Generalize the middle pattern. The hook keeps three exemptions but the second
one becomes general instead of an enumerated four-arm special case:

1. `VALID_PATTERN` — single shape (**unchanged**).
2. **`VALID_MARKER_CHAIN_PATTERN` (new, replaces `VALID_CHAINED_MARKER_PATTERN`)**
   — two-or-more valid marker shapes joined by `&&`, any op/target combination,
   optional trailing `2>/dev/null`.
3. `VALID_CHAINED_COMMIT_PATTERN` — `(write <skill> &&)+ git commit …`
   (**unchanged**; it is the one blessed marker+non-marker chain, coordinated
   with `require-code-review.sh` via `_lib_chains_marker_write_before_commit`).

Extract the shared building block into a shell variable and compose all three
patterns from it (removes the ~1.5 KB of copy-pasted path-prefix duplication
that made the current chain pattern unreadable):

```sh
# path-prefix + one valid (op, target) shape — no anchors, no trailing suffix
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill)|clear-stale([[:space:]]+--dry-run)?)'

# 2+ marker shapes joined by && (the `+` = one-or-more repeats = ≥2 segments)
VALID_MARKER_CHAIN_PATTERN="^${MARKER_SHAPE}([[:space:]]*&&[[:space:]]*${MARKER_SHAPE})+([[:space:]]+2>/dev/null)?[[:space:]]*\$"
```

Applied with the **same newline guard** the other patterns use
(`[[ "$TRIMMED" != *$'\n'* ]] && … | grep -qE …`).

### Why this design (and alternatives set aside)

- **Repeatable anchored regex, not an imperative segment-parser.** The ticket
  says "parse the chained segments." A literal split-on-`&&`-and-loop
  implementation is *heavier* and introduces a new risk: the parser's split
  could diverge from how the shell tokenizes the string. The regex expresses
  the identical semantic ("every segment is a valid marker shape") while
  staying in the file's existing idiom and inheriting its proven safety
  property — the restrictive character classes (`[A-Za-z0-9_./~-]`, spaces,
  the literal op/target words, `&&`, and the single trailing `2>/dev/null`
  literal) make it **structurally impossible** for `;`, `|`, a lone `&`, `<`,
  `>`, backtick, `$`, `()`, or quotes to appear anywhere in a matching string.
  Metacharacter smuggling cannot occur; there is no shell/parser divergence to
  reason about. Chosen over the imperative parser.
- **Keep `VALID_PATTERN` (single shape) separate; make the chain pattern
  require ≥2 segments.** Full unification into one `≥1`-segment pattern was
  considered and set aside: it would re-route the single-shape path (the
  common case) through new regex for zero functional gain and more test
  surface to re-verify. Requiring `+` (≥2) means the single-shape and
  commit-chain code paths are provably untouched.
- **No `permissions.allow` change.** Chains cannot be enumerated as
  exact-match entries (combinatorial), and globs are forbidden by repo policy.
  The deny-exemption layer (hook exits 0 → permission layer evaluates) is the
  correct home — the same layer the existing blessed chains already use.
- **ReDoS:** GNU `grep -E` is DFA-based (no backreferences here) and input is a
  bounded shell command; the repeated group cannot backtrack catastrophically.

### What flips, and why each flip is safe

These currently-denied commands become permitted (hook exits 0) — each is the
FP class the census targets, and each is capability-neutral (all segments
individually sanctioned):

| Command (one Bash call) | Was | Now | Safe because |
|---|---|---|---|
| `write plan-review && deactivate ready-for-review` (mixed skill) | deny | allow | both valid marker ops |
| `deactivate ready-for-review && write plan-review` (mixed, reverse) | deny | allow | both valid |
| `write plan-review && deactivate plan-review && write plan-review` (3+) | deny | allow | all valid |
| `activate plan-review && deactivate plan-review` | deny | allow | both valid |
| `activate plan-review && write plan-review` | deny | allow | both valid |
| `write code-review && write skill-review` (multi-write, no commit) | deny | allow | both valid |

### What stays denied (must verify unchanged)

- `write plan-review && git push` — **the ticket's true-positive**: git push
  is not a marker op and not the blessed `git commit` tail → denied.
- `write code-review && deactivate code-review` — `deactivate code-review` is
  an **invalid op/target combo** (deactivate has no `code-review` gate) →
  segment fails `MARKER_SHAPE` → denied. (Proves op/target validation survives
  inside chains.)
- `write plan-review && deactivate plan-review && git commit` — mixes a
  deactivate into a commit chain; commit pattern requires all-`write` segments
  → denied.
- Separators other than `&&` (`||`, `;`), trailing `curl`/redirect/`2>&1`,
  bare RHS without path prefix, RHS path traversal, embedded newline, extra
  args, `$HOME`-literal form, wrapped/relative/env-prefixed forms — all
  unchanged (structurally excluded by the character classes + the pre-existing
  traversal/newline guards + Stage-2 anchor).

## Critical files

- **`claude/.claude/hooks/enforce-marker-script-shape.sh`** — the change:
  - Introduce `MARKER_SHAPE` shared building-block variable; compose
    `VALID_PATTERN`, the new `VALID_MARKER_CHAIN_PATTERN`, and
    `VALID_CHAINED_COMMIT_PATTERN` from it (DRY the duplicated path prefix).
  - Replace `VALID_CHAINED_MARKER_PATTERN` (lines 106-121) with
    `VALID_MARKER_CHAIN_PATTERN` + its guarded `grep`.
  - Rewrite the block comment to state the general rule (self-contained, no
    PR-terminology): "≥2 path-prefixed marker.sh shapes joined by `&&`;
    equivalent to running each separately; non-marker segments and the
    `git commit` tail are handled by the other two patterns." **Preserve** the
    load-bearing note that the line-53 traversal guard is the sole validator
    of non-first segments' paths (Stage-2 anchor only checks position 0).
  - Update the header comment (lines 12-15) and the deny-message help text
    (lines 125-144): "No chains" → "chains of valid marker ops are permitted;
    chaining to any non-marker command (except `git commit`), or with `||`/`;`,
    is denied."
  - **Reuse:** the existing `MARKER_SHAPE` op/target alternation is the same
    canonical set `marker.sh` itself enforces (`marker.sh` lines 124-217) and
    that `_lib_chains_marker_write_before_commit` mirrors — do not invent a new
    list.
- **`claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`** — the
  generalization shifts the entire security burden onto the "every segment =
  full path prefix + valid (op,target)" invariant, so the negative tests that
  defend that invariant must be **explicit assert-unchanged cases**, not
  implicitly "kept."
  - **Convert 5 deny→allow** (verified to match the new pattern by the SDET
    pass): `test_chain_mixed_skill_write_deactivate_denied`,
    `test_chain_mixed_skill_deactivate_write_denied`,
    `test_chain_three_marker_calls_denied`,
    `test_chain_activate_deactivate_pair_denied`,
    `test_chain_activate_write_pair_denied` → move into the **plain** allowed
    list (`test_valid_same_skill_marker_chains_allowed`, *not* the
    `TestDevNullRedirectAllowed` list — none carry `2>/dev/null`), with
    docstrings stating the capability-neutral rationale.
  - **Add positive** tests, each with explicit, self-documenting targets:
    multi-write no-commit (`write code-review && write skill-review`);
    deactivate+deactivate (`deactivate plan-review && deactivate
    ready-for-review`); activate+activate (`activate respond-pr && activate
    memory-skill`); a 4-segment mixed chain; mixed tilde/absolute paths in one
    chain; no-space `&&` (`…plan-review&&~/.claude/…scripts/marker.sh
    deactivate plan-review`); trailing `2>/dev/null` on a mixed chain; and
    **`clear-stale` in a chain** (`write code-review && …marker.sh clear-stale`
    → allow) — intended and capability-neutral; pin it so intent is explicit.
  - **Add negative** tests pinning the generalized boundary: `write plan-review
    && git push` (the ticket's true-positive); `write plan-review && activate
    code-review` (invalid activate target mid-chain); **`write plan-review &&
    …marker.sh write respond-pr`** (invalid *write* target mid-chain — guards
    against a widened write list that every other test would still pass);
    `write plan-review && ls && deactivate plan-review` (non-marker middle
    segment); **`write plan-review && rm -rf /`** (bare non-marker RHS — the
    symmetric partner to the existing bare-RHS test); `write plan-review extra
    && deactivate plan-review` (extra arg in a segment); a **multi-segment**
    `;`/`||` chain (`write code-review && write skill-review; curl x` →
    deny — proves separators still terminate an N-segment chain, not just a
    2-segment one).
  - **Explicit assert-unchanged (deny)** — re-run these existing cases *under
    the new pattern* and confirm they still deny; they are the invariant's load
    bearers and could silently flip on a regex typo:
    - the four `TestDevNullRedirectBoundaryDenied` **mid-chain-LHS `2>/dev/null`**
      cases (a per-segment `2>/dev/null` must still break the `&&` join). Do
      **not** add a new "mid-chain 2>/dev/null" case — re-verify these four.
    - `test_chain_rhs_bare_no_path_prefix_denied` (bare RHS without path
      prefix — the single test proving a non-first segment cannot skip the
      prefix).
    - `test_chain_deactivate_write_only_skill_denied` (`deactivate code-review`
      invalid combo inside a chain).
    - `test_chain_marker_activate_then_git_commit_denied` and
      `test_chain_marker_pair_trailing_git_commit_denied` (marker+`git commit`
      couplings the commit pattern must still reject).
  - Keep every other existing denial test that still denies (verify, don't delete).
- **`docs/hooks.md`** (line 35) — correct "Blocks chains (`&&`, `;`)" to
  reflect that all-marker-op `&&` chains are permitted; non-marker chains,
  `||`, `;`, redirects, and extra args are blocked.

**Not changed (deliberate):** `claude/.claude/settings.json` (no chain
allow-entries — combinatorial + globs forbidden); `VALID_CHAINED_COMMIT_PATTERN`
and `require-code-review.sh` / `_lib_chains_marker_write_before_commit` (the
commit-chain path is untouched, so their coordination is unaffected).

## Verification

1. **Unit tests** (from a linked worktree, per repo convention):
   `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_enforce_marker_script_shape.py -q`
   — all pass, including converted + new cases.
2. **Full hook suite + lint:** `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/`.
3. **Manual metacharacter red-team (hook layer)** — pipe adversarial strings
   through the hook and confirm `deny` for each: `write plan-review && curl x`,
   `write plan-review; curl x`, `write plan-review && deactivate plan-review\ncurl x`,
   `write plan-review && activate code-review`, `write plan-review && git push`,
   and confirm the hook does not deny representative valid chains. *Scope note:*
   this asserts the hook's deny/defer contract — all the hook controls — not the
   final permission decision (step 4 covers that).
4. **Permission-layer handoff — confirmed via primary source, spot-checked
   live.** The unit suite tests the hook in isolation and cannot observe
   Claude Code's permission decision (consistent with this repo's
   no-`claude -p`-in-CI stance), so this was verified two ways instead:
   (a) [Configure permissions — Bash → Compound
   commands](https://code.claude.com/docs/en/permissions#compound-commands)
   states each `&&`-joined subcommand must match `permissions.allow`
   independently — a first-segment match never widens approval across the
   separator; (b) a live spot-check of the *existing production hook* (not
   yet stowed with this change) ran `~/.claude/scripts/marker.sh clear-stale
   --dry-run` — a single command that is hook-valid today but absent from
   `permissions.allow`, the same shape of gap the new chain pattern
   introduces for `clear-stale`-in-a-chain — and it executed with no
   escalation beyond its own scope. Document both in the PR description.
5. **Regression on blessed paths** — confirm the 14 single shapes, the
   `write … && git commit` chain, and the 2>/dev/null suffix cases still pass
   unchanged.
6. **Coordination check** — confirm `require-code-review.sh`'s in-chain
   marker-write handling is unaffected (commit pattern byte-for-byte identical;
   grep the two files' shared `VALID_CHAINED_COMMIT_PATTERN` text still matches).
7. **`/claude-hook-review`** on the diff (hook edit) before `/code-review`.

## Out of scope

- Auto-approving chains silently (would need either unbounded `permissions.allow`
  entries or the hook emitting explicit `allow` — a privilege-role expansion the
  ticket does not ask for). Removing the false hard-deny is the fix; the
  permission layer remains the arbiter for anything not exact-matched.
- `clear-stale` permission coverage (it is intentionally not in
  `permissions.allow`; not this ticket).
- **Pre-existing observations surfaced by the security review — flagged for a
  follow-up, deliberately not fixed here (this change does not widen either):**
  - **Absolute-path prefix breadth.** `/[A-Za-z0-9_./-]+/\.claude/scripts/marker\.sh`
    matches *any* absolute path ending in `.claude/scripts/marker.sh` (e.g.
    `/tmp/x/.claude/scripts/marker.sh`), not just the user's home. The current
    single-shape and same-skill-pair patterns already carry this exact prefix,
    so reusing it in `MARKER_SHAPE` changes nothing; the backstop is the
    permission layer's exact-match allowlist (`~/…` / real home path only).
    Tightening the prefix (to `~`/`$HOME`/`/home/*/`) is a separate hardening
    that risks breaking legitimate resolved-absolute-path invocations — its own
    ticket.
  - **Whitespace-class asymmetry.** The newline guard rejects `\n`, but
    `[[:space:]]` in the separators also matches `\r\v\f\t`. Non-exploitable
    (those are not bash IFS separators, so they yield an invalid target →
    marker.sh exit 2, i.e. denial-of-function, not injection) and pre-existing
    in every current pattern. Noted for a future consistency pass.
