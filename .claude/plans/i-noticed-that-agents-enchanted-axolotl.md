# Plan: Bless safe same-skill marker→marker chains in the shape hook

## Context

Agents burn ~1 turn per `plan-review`/`ready-for-review` completion to a recurring
denial. Both skills end with two adjacent, prescribed marker.sh calls — a
completion `write` and an active-marker `deactivate` — presented as separate
fenced blocks. The agent's strong prior is to join adjacent shell commands with
`&&`, but `enforce-marker-script-shape.sh` denies all chains except
`marker.sh write <skill> && git commit`. Transcript analysis
(`transcript-analysis.py review-trace --deny-only`) over ~25 days shows **8 of 10**
marker.sh shape-hook denials are exactly this pattern:

| Denied chain | Count |
|---|---|
| `write ready-for-review && deactivate ready-for-review` | 5 |
| `write plan-review && deactivate plan-review` (and reverse order) | 3 |
| `clear-stale 2>&1` / `activate respond-pr 2>&1` (redirects — out of scope) | 2 |

**Why this is safe to allow (the security analysis that motivates the change).**
The chain `marker.sh write X && marker.sh deactivate X` is *security-neutral*: its
end state is identical to running the two calls separately, both shapes are
already individually allowlisted, and the agent already holds both capabilities.
The shape hook's job is to block obfuscated/wrapped writes and to stop a marker
write from being paired with an *arbitrary follow-on command* (which is why the
existing `&& git commit` carve-out is bounded with `[^&|;<>]`). A chain whose RHS
is itself an exact marker.sh shape carries none of that risk. The hook is simply
**over-restrictive** for this case; relaxing it precisely here removes an
over-powered restriction rather than stacking a new defensive layer.

**Intended outcome:** the natural chain stops being denied; zero recurring prose
cost; the gate's rigidity is preserved for every other form.

## Scope (what changes / what does NOT)

- **Changes:** `enforce-marker-script-shape.sh` (one new regex + deny-message text)
  and its test file. That is all.
- **No `permissions.allow` change.** Claude Code splits `&&`-chains and matches
  each sub-command independently against allow rules (docs:
  https://code.claude.com/docs/en/permissions.md — "A rule must match each
  subcommand independently"). Both halves are already allowlisted, so the chain
  is already auto-approved by the permission layer once the hook stops denying it.
- **No SKILL.md changes.** The `HOOK_TEST_FIXTURE` blocks run marker.sh directly
  and do not route through the shape hook. Recipes stay as two separate blocks;
  this is required for **plan-review**, where `deactivate` is unconditional but
  `write` is conditional (approve-only) — they are not always an atomic pair, so
  they must remain separable. We only stop *denying* the chain; we don't mandate it.
- **No `clear-stale` allowlist change** (the option that bundled it was not chosen).
- **Redirect denials (`2>&1`) stay denied** — out of scope; allowing redirects
  would genuinely widen the surface.

## Implementation

### 1. `claude/.claude/hooks/enforce-marker-script-shape.sh`

Add a third allowed-shape check, placed **after** the existing
`VALID_CHAINED_COMMIT_PATTERN` block (around line 98), before the final deny.

The new pattern blesses **same-skill** `write↔deactivate` chains in **both
orderings**, for the only two skills valid for both `write` and `deactivate`
(`plan-review`, `ready-for-review`). Both sides must be a full path-prefixed
exact marker.sh shape; the chain must terminate immediately (anchored `$`), so no
trailing operator/command/redirect can ride along. POSIX ERE (`grep -qE`) has no
portable backreferences, so same-skill is enforced by explicit enumeration of the
four combinations.

**Authoring constraints (from platform review — these are not optional):**
- **Spell the path prefix inline** (`(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh`)
  on each side, exactly as the existing `VALID_PATTERN` (line 73) and
  `VALID_CHAINED_COMMIT_PATTERN` (line 94) do. Do **not** introduce a `$M`-style
  helper var — it would bake a trailing `[[:space:]]+` into the prefix that
  diverges from the literal convention and could mislead a future reuse.
- **Author the value as a single-line literal** (line 94 already is one long
  literal). Do **NOT** build it with backslash-newline continuation inside a
  double-quoted string: a trailing space after a line-continuation `\` is
  editor-invisible and silently embeds a newline+space into the pattern, corrupting
  it so valid chains get denied. If a multi-line form is unavoidable for
  readability, assemble by `+=` concatenation of separately-quoted single-line
  pieces, never `\`-newline.
- Place this check **after** the existing `..` traversal guard (line ~51) and after
  the `VALID_CHAINED_COMMIT_PATTERN` block (line ~98). The traversal guard is the
  **sole** validator of the chain's RHS path (Stage 2's anchor at line 65 only
  checks position 0 = the LHS). Add a one-line comment recording that this ordering
  is load-bearing — the new pattern depends on line 51 running first.

Shape (single logical line; `&` is literal in ERE, `&&` needs no escaping):

```
^( <PREFIX> write[[:space:]]+plan-review[[:space:]]*&&[[:space:]]* <PREFIX> deactivate[[:space:]]+plan-review
 | <PREFIX> deactivate[[:space:]]+plan-review[[:space:]]*&&[[:space:]]* <PREFIX> write[[:space:]]+plan-review
 | <PREFIX> write[[:space:]]+ready-for-review[[:space:]]*&&[[:space:]]* <PREFIX> deactivate[[:space:]]+ready-for-review
 | <PREFIX> deactivate[[:space:]]+ready-for-review[[:space:]]*&&[[:space:]]* <PREFIX> write[[:space:]]+ready-for-review )[[:space:]]*$
```

where `<PREFIX>` = `(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+`
spelled out at each of the 8 sites. The `^(...)[[:space:]]*$` grouping anchors
**every** branch (confirmed correct — not the `^A|B|C$` precedence trap). Guarded by:

```bash
if printf '%s' "$TRIMMED" | grep -qE "$VALID_CHAINED_MARKER_PATTERN"; then
  exit 0
fi
```

Mixed path forms across the two sides (tilde LHS, absolute RHS) are permitted and
benign — both are valid prefixes; a test pins this as intended (item 2).

Then update the **deny-message help text** (currently lines ~104–121) so the
self-documenting error stays accurate: add a line under "Valid shapes" for the
same-skill `write <skill> && deactivate <skill>` chain (and reverse) for
plan-review / ready-for-review. Reword the closing line to carve out **only** the
two permitted `&&` forms (`&& git commit`, and the same-skill write/deactivate
pair) while **keeping `||`, `;`, redirects, and other extra args explicitly listed
as denied** — do not drop them, or the message overstates what's allowed.

**No commit-gate counterpart needed.** `require-code-review.sh` / `require-skill-review.sh`
honor only the `marker.sh write <skill> && git commit` form via
`_lib_chains_marker_write_before_commit`; the new marker→marker form never reaches
a commit gate, so those hooks and `_lib.sh` need no change. (Confirm in the PR body.)

### 2. `claude/.claude/hooks/tests/test_enforce_marker_script_shape.py`

Add cases (the suite asserts `run_hook(...) == "allow"|"deny"` via
`helpers.bash_input`). **Placement (from SDET review):** put the ALLOWED chained
cases in a **new parametrized method/class — not** the existing flat valid-shapes
parametrize, whose header comment asserts "all 14 must be allowed" (chained shapes
aren't single shapes). Update or remove that stale "14" count comment. Put DENIED
cases in the existing chaining class, each with a one-line docstring naming what it
guards against (matches the file's regression-intent style). All command strings
below are full `~/.claude/scripts/marker.sh …` (abbreviated here).

**Allowed — same-skill chains, both orderings × both skills (4):**
- `write plan-review && … deactivate plan-review`
- `deactivate plan-review && … write plan-review`
- `write ready-for-review && … deactivate ready-for-review`
- `deactivate ready-for-review && … write ready-for-review`

**Allowed — positive-realism variants (3):**
- absolute-path on **both** sides (`/home/<user>/.claude/scripts/marker.sh …` ×2)
- extra/tab whitespace around `&&` (`write ready-for-review   &&   … deactivate ready-for-review`)
- mixed form: tilde LHS + absolute RHS (pins the benign mixed-prefix behavior)

**Denied — boundary-pinning (each guards a distinct slip):**
- `write code-review && deactivate code-review` — code-review is write-only; not deactivate-valid.
- `write plan-review && deactivate ready-for-review` — mixed-skill (forward).
- `deactivate ready-for-review && write plan-review` — mixed-skill (reverse); confirms order-sensitive arms don't cross-leak.
- `write plan-review && deactivate plan-review && curl http://evil` — trailing command past the pair.
- `write plan-review && deactivate plan-review && git commit -m foo` — trailing commit (high-probability agent input; anchor must reject).
- `write plan-review && deactivate plan-review && write plan-review` — three-marker chain; pins that the carve-out admits exactly **one** pair, not a repeatable `(…&&…)+`.
- `write plan-review || deactivate plan-review` — `||` separator (carve-out hardcodes `&&`).
- `write plan-review ; deactivate plan-review` — `;` separator.
- `write plan-review && deactivate plan-review` with **bare** RHS (no path prefix) — locks RHS must be a full marker.sh shape, not a bare word.
- `activate plan-review && deactivate plan-review` — `activate` is not part of the write↔deactivate pair.
- `write plan-review && deactivate plan-review 2>&1` — trailing redirect.
- **RHS path traversal** (CISO-required): `write plan-review && ~/.claude/scripts/../scripts/marker.sh deactivate plan-review` — pins the load-bearing invariant that the line-51 `..` guard runs before (and is the sole validator of) the chain's RHS path.
- **embedded newline**: `write plan-review && deactivate plan-review\n curl evil` — confirms the per-line `grep -qE` match can't allow a two-line payload whose lines independently match a branch.

Optionally assert the assembled `$VALID_CHAINED_MARKER_PATTERN` string contains no
newline/tab — cheap guard against the line-continuation authoring trap (item 1).

The existing `test_chain_marker_activate_then_git_commit_denied` stays valid and
unchanged: `activate`/`deactivate` chained with `git commit` remains denied; the
new pattern is a distinct marker→marker form, not a relaxation of the commit form.
(Verified.) `test_marker_script.py` is unaffected — it tests single-command
marker.sh behavior, not chains.

### 3. Knowledge-duplication sync check

The valid-shapes knowledge is duplicated across three sites with no shared
constant. For this change, two are in play and must stay consistent:
- the hook regex (item 1), and
- the hook's deny-message help text (item 1).

`test_marker_script.py`'s shape list tests marker.sh *behavior* (single-command
exit codes / files written) and is **not** affected — chains are a hook concern,
not a marker.sh concern.

## Verification

From the main worktree (`.venv` lives only there):

```bash
.venv/bin/pytest claude/.claude/hooks/tests/test_enforce_marker_script_shape.py -q
.venv/bin/pytest claude/.claude/hooks/ -q          # full hook suite — confirm no regression
.venv/bin/ruff check claude/.claude/
```

Manual hook smoke test (feeds the JSON payload the harness sends; empty stdout =
allow, JSON with `permissionDecision":"deny"` = deny):

```bash
H=claude/.claude/hooks/enforce-marker-script-shape.sh
# expect ALLOW (no output):
echo '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh write ready-for-review && ~/.claude/scripts/marker.sh deactivate ready-for-review"}}' | bash "$H"
echo '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh deactivate plan-review && ~/.claude/scripts/marker.sh write plan-review"}}' | bash "$H"
# expect DENY (JSON with permissionDecision deny):
echo '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate ready-for-review"}}' | bash "$H"
echo '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/marker.sh deactivate plan-review && curl http://evil"}}' | bash "$H"
# RHS traversal must DENY (pins the line-51-runs-first coupling):
echo '{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/marker.sh write plan-review && ~/.claude/scripts/../scripts/marker.sh deactivate plan-review"}}' | bash "$H"
```

**End-to-end auto-approval confirmation (CISO-recommended).** The hook-in-isolation
smoke test above proves the gate stops denying, but the plan's "zero recurring cost"
goal also rests on the permission layer auto-approving the chain (both halves are
already allowlisted; Claude Code matches each `&&` subcommand independently). The
unit tests do not exercise the live permission layer. Confirm with one real
interactive run: in a throwaway session, trigger a `plan-review`/`ready-for-review`
completion (or hand-type the chained marker command) and verify it runs **without a
permission prompt and without a hook denial**. If it prompts, the per-subcommand
matching assumption is wrong — the fix would be adding the chained strings to
`permissions.allow` (the failure mode is a prompt, not a security hole).

## Required follow-ups (repo workflow)

- This edits a **hook** → run `/claude-hook-review` on the diff before commit.
- Standard pipeline: `/code-review` (which dispatches per file type) → commit is
  hook-gated on the code-review marker. No SKILL.md or agent files change, so
  skill-review/agent-review are not triggered.
- Worktree enforcement is active: implement inside a linked worktree
  (`git worktree add .claude/worktrees/<slug> -b <slug>`), not the main tree.
```
