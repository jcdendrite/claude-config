# GH-480 — Close the fail-open-on-missing-`jq` hole in every gate hook

## Context

**Goal: make every gate hook block the tool call when it cannot build its deny
JSON, say why, and add a pytest that pins that behavior for each gate.**

Issue #480 asked whether a gate hook that errors because a binary isn't on
`PATH` still blocks the commit, and asked for a pytest asserting
deny-on-hook-error per gate. The concern is well-founded: a gate that silently
degrades to allow is worse than no gate, because it is trusted.

Measured against all 24 `# hook-class: gate` hooks this session (symlink-farm
`PATH` omitting one binary at a time, stdin `not json`):

| Missing binary | Result |
| --- | --- |
| `jq` | **22 of 24 gates emit invalid JSON on exit 0** → no decision → tool proceeds |
| `jq` | `block-gh-pr-merge.sh` emits nothing and allows — a *deliberate*, documented fail-open (`block-gh-pr-merge.sh:28`) |
| `jq` | `require-respond-pr.sh` denies correctly — the only already-hardened gate |
| `sha256sum` | all 24 deny correctly |
| `gh` | all 24 deny correctly |

The mechanism: every gate's `emit_deny` encodes its reason with
`printf '%s' "$reason" | jq -Rs .`. With `jq` off `PATH` that substitution is
empty, and the payload `printf` yields
`..."permissionDecisionReason":}` — malformed. Per
[code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks),
*"Claude Code parses stdout for JSON output fields. JSON output is only
processed on exit 0"*; an unparseable payload leaves no decision, so the
normal permission flow resumes and the tool runs.

`jq` is the only live hole. `sha256sum` and `gh` already fail closed, but by
two different routes — see ledger rows 4a/4b, which the first draft of this
plan conflated.

Intended outcome: the missing-binary path becomes a hard block on every gate
**that names its own cause**, and a test makes that a standing invariant.

## Approach

Replace `emit_deny`'s single JSON path with a two-path one: encode with `jq`
when it works; otherwise write a diagnostic plus the reason to stderr and
`exit 2`. The docs make exit 2 the harness's native blocking primitive —
*"Exit 2 means a blocking error… stderr text is fed back to Claude as an error
message… `PreToolUse` blocks the tool call"* — so the failure path needs **no
JSON encoding at all**. That matters because several reasons interpolate
model-supplied or tool-supplied text (`${COMMAND:0:200}` at
`block-gh-pr-merge.sh:59`; a `cut -c1-80` truncation at
`enforce-marker-script-shape.sh:346`; an **untruncated, multi-line**
`${VALIDATOR_STDERR}` at `plugins/skill-management/hooks/require-skill-review.sh:136`),
which a hand-rolled escaper cannot safely encode.

**The fallback must name `jq`, not the caller's reason alone.** Every gate
calls `_lib_parse_tool_input_or_deny` before any command filtering, and that
helper routes through `_lib_jq`. So with `jq` absent, *every* gate denies
*every* matched tool call — Bash, Edit, Write, MultiEdit, Read, ExitPlanMode —
and the reason it would print is `"…could not parse tool-input JSON."` That
misdiagnoses the cause: the payload is fine, the binary is missing. An agent
reading it will reformat its command and retry forever, and cannot run the
`brew install jq` that would fix it. The fixed diagnostic converts a dead end
into an exit. This is the whole reason the exit-2 branch is not a one-liner.

Alternatives set aside: propagating `require-respond-pr.sh`'s hand-rolled
`${reason//\"/\\\"}` escaper into the other 23 gates — it preserves the
"exit code is always 0" convention, but costs ~14 duplicated lines per hook
and its own comment concedes it is valid only for ASCII reasons with no
control characters, which is false for the three interpolating sites above.
And hoisting `emit_deny` into `_lib.sh` with a literal-JSON bootstrap per hook
— the DRY-correct shape, but it restructures all 24 hooks and invalidates
`test_emit_deny_defined_before_lib_source`, for a bug a 4-line change fixes.

### Target shape

Applied verbatim to all 24 gates.

```bash
emit_deny() {
  local reason="$1"
  local reason_json
  # Defined before _lib.sh is sourced so a failed source can still deny,
  # which means _lib_jq may not exist yet. Prefer it when it does, for its
  # timeout backstop.
  if declare -F _lib_jq >/dev/null 2>&1; then
    reason_json=$(printf '%s' "$reason" | _lib_jq -Rs . 2>/dev/null)
  else
    reason_json=$(printf '%s' "$reason" | jq -Rs . 2>/dev/null)
  fi
  if [ -z "$reason_json" ]; then
    # jq is absent, failed, or was killed by the timeout backstop. Exit 2 is
    # the harness's blocking path for PreToolUse and carries the reason on
    # stderr, so it needs no JSON encoding. Emitting a half-built payload on
    # exit 0 instead would parse as no-decision and let the tool run.
    #
    # The fixed prefix is load-bearing: every gate parses its input with jq
    # before any command filtering, so a missing jq denies every tool call
    # with the parse-failure reason below — which names the wrong cause.
    # Without this line the session has no in-agent route to a fix.
    printf 'Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. Install jq (and GNU coreutils timeout) using the `!` shell escape, which runs outside the tool-call path these hooks gate. Underlying gate reason follows.\n%s\n' \
      "$reason" >&2
    exit 2
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}
```

### Assumption ledger

**Root problem:** a gate hook that cannot encode its deny reason currently
allows the tool call it was installed to block.

| # | Mechanism / assumption | Anchor | Tag |
| --- | --- | --- | --- |
| 1 | `exit 2` blocks a `PreToolUse` call and feeds stderr to Claude | root | `[verified: code.claude.com/docs/en/hooks, "Exit code output" + "Exit code 2 behavior per event" table]` |
| 2 | exit 0 with unparseable stdout yields no decision, so the tool proceeds | root | `[verified-with-inference]` — the "JSON only processed on exit 0" half is literal; the "therefore proceeds" half is inferred from hooks-guide's *"the normal permission flow still applies"*. Docs never state a malformed-JSON rule directly. |
| 3 | 22 of 24 gates emit malformed JSON with `jq` off `PATH`; `require-respond-pr.sh` is correct; `block-gh-pr-merge.sh` allows by design | root | `[verified: measured this session across all 24 gates]` |
| 4a | `sha256sum` absence fails closed **by named mechanism** | root | `[verified: _lib.sh:139 empty-value guard; _lib.sh:186-200 three-outcome contract]` |
| 4b | `gh` absence fails closed **by measurement only** — no structural guarantee | root | `[verified: measured this session]` — no `_lib.sh` mechanism covers `gh`; this is why row 7's pin matters |
| 5 | Every `emit_deny` call site runs in the current shell, so `exit 2` propagates | row 1 | `[verified: no `$(emit_deny`/backtick sites; `check-claude-md-length.sh:80` and `enforce-marker-script-shape.sh:347` are post-loop top-level; both `require-worktree-for-git-writes.sh` loops use `< <(...)` and `<<<`; `plugins/lovable-cloud/hooks/validate-migration-filename.sh:31` is a brace group, not a subshell. Independently re-derived by two reviewers, one empirically.]` |
| 6 | Flipping `block-gh-pr-merge.sh` to fail-closed is wanted, overriding its documented posture | row 3 | `[engineer-verified]` |
| 7 | Test pins `jq`, `sha256sum`, `gh` | root | `[engineer-verified]` — **intent preserved, mechanism corrected**; see "Deviation" below |
| 8 | Exit-2 fallback over hand-rolled JSON escaping | row 2 | `[engineer-verified]` |
| 9 | All 24 gates are registered on `PreToolUse` only, so exit-2 semantics are uniform | row 1 | `[verified: 20 in claude/.claude/settings.json + 4 plugin hooks.json]` |
| 10 | `[ -z "$reason_json" ]` catches only *empty* jq output. A jq that returns non-empty non-JSON (wrong version, a shim) still yields a malformed payload on exit 0 — the original fail-open, unfixed | row 1 | `[unverified]` — judged outside the environment-defect threat model; recorded rather than closed |
| 11 | The `!` shell escape runs outside the gated tool-call path, so it is a real recovery route | root | `[unverified — repo-asserted]`: `docs/hooks.md:14` relies on it ("or via the `!` shell escape"). Confirm before shipping the diagnostic that promises it. |

**Lighter primitives considered** (over-powered-primitive check): (a)
hand-rolled JSON escaping — rejected, cannot safely encode the three
interpolating sites; (b) a pre-flight `command -v jq || <deny>` per hook —
rejected, covers a missing binary but not a hung or crashed one, so the
empty-output check at point of use subsumes it; (c) `exit 0` with a fixed
pre-escaped JSON constant — rejected, discards the reason text the model needs.

**Deviation from ledger row 7 — flagged, not resolved unilaterally.** The
chosen matrix (24 gates × `jq`/`sha256sum`/`gh`, stdin `not json`) does not
achieve its stated intent. Every gate denies inside
`_lib_parse_tool_input_or_deny` before reaching any `sha256sum` or `gh` call
site, so omitting those binaries produces output byte-identical to the plain
malformed-input case — 48 of 72 cases would be renamed duplicates of the
existing `test_malformed_input_denied`, and would pass identically on the
pre-fix tree. The intent (pin `sha256sum`/`gh` fail-closed) is kept; the
mechanism moves to targeted valid-payload cases at the hooks that actually
reach those binaries. **This narrows an engineer-verified row and needs a
yes.**

## Critical files

**Gate hooks — replace `emit_deny` with the target shape (24 files).** All 20
in `claude/.claude/hooks/`: `block-gh-pr-merge.sh`, `check-claude-md-length.sh`,
`check-skill-length.sh`, `deny-*.sh` (×6), `enforce-marker-script-shape.sh`,
`guard-settings-session-keys.sh`, `require-*.sh` (×7),
`require-worktree-for-{file,git}-writes.sh` (×2) — plus the 4 plugin gates
(`plugins/lovable-cloud/hooks/validate-migration-filename.sh`,
`plugins/npm-semver/hooks/require-npm-version-bump.sh`,
`plugins/plugin-semver/hooks/require-plugin-version-bump.sh`,
`plugins/skill-management/hooks/require-skill-review.sh`). 23 replace the naive
jq-only form; `require-respond-pr.sh:53-76` swaps its hand-rolled escaper for
the same fallback. Two files carry no `set` line —
`require-worktree-for-file-writes.sh` and
`plugins/lovable-cloud/hooks/validate-migration-filename.sh`. The new body
neither reads an unset var nor depends on `pipefail`, so leave both as they
are; do not fix one and miss the sibling.

**`claude/.claude/hooks/block-gh-pr-merge.sh`** — additionally delete the
pre-flight `command -v jq >/dev/null 2>&1 || exit 0` (line 28) and rewrite the
"Fail posture" block (lines 11-14). **Correction to state plainly in the
header and the PR body:** this hook has no `if` matcher and fires on every Bash
call, and its `TOOL_NAME != Bash` / empty-`COMMAND` fast paths sit at lines
46-50 — *after* `_lib_parse_tool_input_or_deny` at line 44. So with `jq`
absent it denies every Bash call, not only merge attempts. That is the same
posture the other 23 gates now have, which is the point, but it is not
"confined to genuine merge attempts." The header rewrite must also correct the
lines 11-14 claim that a missing `tool_name` fails open — `_lib.sh:87-90`
already denies on empty `TOOL_NAME`, so that sentence is wrong today,
independent of this change. Leave the non-string-`command` fail-open intact.

**`claude/.claude/hooks/tests/test_hook_alignment.py`** — add to Layer 2, and
extend `_run_hook_raw` (line 297) with an `env` parameter; its current
signature is `(hook, stdin_text, cwd=None)` and cannot express this test.
Do **not** reach for `helpers.run_hook` — see the `helpers.py` entry below.

- `_path_without(binary)` — session-scoped symlink farm over the real `PATH`
  minus one binary, **first-wins on duplicate names** (otherwise shadowing
  order inverts) and skipping unreadable dirs. Assert
  `shutil.which(binary, path=...) is None` against the exact `PATH` string
  placed in the subprocess env. Measured: ~0.10 s to build, ~21 ms per hook
  invocation. Preferred over the suite's minimal-symlink-set idiom
  (`test_require_worktree_for_git_writes.py:665-693`) precisely because
  under-symlinking produces a silent false pass — a hook denying for a missing
  `cat` looks identical to a hook denying correctly.
- `_assert_blocks(result, expected_reason_substring, ...)` — accepts exit 0
  with a valid deny envelope (delegating to `_assert_deny_schema`), **or**
  exit 2 with stderr containing the expected reason substring. Non-empty
  stderr alone is not enough: a bash syntax error and a `set -e` command
  failure both exit 2 with stderr, so a hook mangled during the mechanical
  24-file edit would pass. All 24 parse-failure reasons share the substring
  `parse tool-input JSON`; the new diagnostic adds `jq`.
- `test_blocks_when_jq_absent` — parametrized over `GATE_HOOKS` (24 cases,
  the 4 plugin gates already included — verified by running the collector).
  Stdin `not json`.
- `test_blocks_when_jq_absent_with_valid_payload` — same 24 gates, but a
  well-formed `bash_input(...)` payload. This is the realistic user situation
  and the one that demonstrates the lockout the fix introduces.
- `test_marker_gate_blocks_without_sha256sum` — targeted, not cross-cutting:
  valid payloads that reach the marker check on the hooks that actually hash
  (`require-code-review.sh`, `require-plan-review.sh`,
  `plugins/skill-management/hooks/require-skill-review.sh`), `PATH` minus
  `sha256sum`. This is what pins ledger row 4a.
- `test_ready_for_review_blocks_without_gh` — targeted, `require-ready-for-review.sh`
  only; it is the one gate that executes `gh` (line 94). Pins row 4b.
- `test_blocks_when_jq_hangs` — one representative gate, fake slow `jq` with
  real `timeout` on `PATH`, asserting exit 2 within the 5 s backstop. Reuses
  the idiom at `test_lib.py:127-148`. Without it, the hung-jq claim below is
  untested.

**`claude/.claude/tests/helpers.py`** — `run_hook` (lines 92, 118) maps
"exit 0, empty stdout" to `"allow"` and documents that mapping. An exit-2
block also leaves stdout empty, so post-change `run_hook` silently reports
`"allow"` for a hook that correctly blocked. No existing test breaks (they all
run with `jq` present), but the next author to exercise a broken-`jq` path gets
an inverted assertion. Teach it to return `"deny"` on exit 2 and update the
docstring.

**`plugins/claude-hook-review/skills/claude-hook-review/SKILL.md`** — the
canonical skeleton at lines 44-52 carries the broken `emit_deny`; it is the
template new hooks are copied from, so leaving it re-seeds the defect. Line
63's *"Exit code is always `0`"* becomes the two-path statement, and Section 4
gains the missing-binary case. Route through
`claude-hook-review:claude-hook-review` per
`.claude/rules/review-pipeline-dispatch.md`.

**`docs/hooks.md`** — the `block-gh-pr-merge.sh` entry (line 14) does **not**
currently claim a jq fail-open, so there is nothing there to correct. Instead
add one sentence to the "Gate deadlock recovery" section (around line 60)
covering the new state: with `jq` unavailable every gate hard-blocks by design,
and the route out is the `!` shell escape.

**Plugin version bumps (`plugin-semver`).** Every touched plugin needs its
`.claude-plugin/plugin.json` `version` raised. Current values at HEAD, as
anchors to bump *from*, not targets: `lovable-cloud` 3.2.2, `npm-semver` 1.0.2,
`plugin-semver` 1.1.2, `skill-management` 3.0.0, `claude-hook-review` 2.0.1.
Let the `plugin-semver` skill pick each increment —
`block-gh-pr-merge.sh`'s posture flip is a behavior change, the `emit_deny`
fix is a defect fix.

**Reuse:** `GATE_HOOKS`, `_hook_class()`, `_assert_deny_schema` already exist
in `test_hook_alignment.py`; `bash_input`/`write_input` payload builders and
the `extra_env={"PATH": ...}` idiom in `claude/.claude/tests/helpers.py`. No
new fixture module.

## Verification

1. **Prove the test detects the bug, per case.** On a stashed (pre-fix) tree,
   the expected failing set is exactly the 48 `jq`-absent cases
   (`test_blocks_when_jq_absent` ×24 + `_with_valid_payload` ×24) — they emit
   `"permissionDecisionReason":}}`, which `_assert_deny_schema` rejects. The
   targeted `sha256sum`/`gh`/hung-jq cases pass on both trees by design; that
   is expected, not coverage theater, because they pin behavior the fix does
   not change. Any *other* new case that passes pre-fix needs a stated reason
   to exist.
2. `.venv/bin/pytest claude/.claude/` — full suite. The existing four
   `TestGateHookBehavior` cases are class-parametrized over all 24 gates and
   assert exit 0 + valid schema with `jq` present; they must stay green, which
   is what prevents a regression that takes the exit-2 branch unconditionally.
3. `.venv/bin/ruff check claude/.claude/` and
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`. The target
   shape produces zero shellcheck findings under the repo `.shellcheckrc` — no
   directive needed.
4. **Manual: confirm the escape hatch before shipping the diagnostic that
   promises it** (ledger row 11). In a session, verify a `!`-escape command
   runs without firing PreToolUse gates.
5. Manual reproduction: `PATH` symlink farm without `jq`, pipe `not json` to
   `require-code-review.sh`, confirm exit 2 with the jq diagnostic *and* the
   gate reason on stderr.
6. Live smoke test with `jq` present: stage a change, attempt a commit without
   a review marker — the normal deny envelope must still appear on stdout with
   exit 0, unchanged.
7. `git grep -c 'jq -Rs' claude/.claude/hooks plugins/*/hooks` — no gate
   retains the unguarded form.

## Out of scope

- **The hung-`jq` gap is only closed where `timeout(1)` exists.** `_lib.sh:14-20`
  falls back to bare `jq` when `timeout` is absent (stock macOS without
  coreutils), so a wedged `jq` there still hangs the gate until the harness's
  600 s hook timeout fires — which is a *non-blocking* error, i.e. fail-open.
  Closing that needs a hook-level watchdog, not a change inside `_lib_jq`.
  Keep `_lib.sh:11-13`'s security-implication comment; do not mark it resolved.
- `_lib.sh:11-13`'s *"The harness's own hook timeout (if any)"* hedge is now
  resolvable (600 s default; a timeout is a non-blocking error). Accurate but
  stale-hedged prose in a file this change touches — flag to the reviewer
  rather than bundling. (`install.sh:126-130` **does** warn on missing
  `timeout`, so `_lib.sh:10`'s claim is correct as written; only the hedge on
  line 13 is stale.)
- Informational (`hook-class: informational`) hooks. Their fail-open posture on
  a missing binary is correct by design, and
  `consume-durable-continuity-file-on-read.sh` has a test asserting exactly
  that.
- `install.sh`'s install-time dependency check. Making it PATH-robust for
  macOS `coreutils` is the issue's framing device, not its ask.
- A `jq` that returns non-empty non-JSON still fails open (ledger row 10).
  Outside the environment-defect threat model.
