# Preserve the ownership marker on `respond-pr-safe-patch.sh` PATCH

## Context

Close a `ciso-reviewer` finding deliberately deferred out of PR #724
(merged): `respond-pr-safe-patch.sh` checks that the *existing* PR comment
body starts with `**[Claude Code]**` before allowing a PATCH, but never
checks that the *replacement* body does too — so a caller can silently
strip the ownership marker (and with it, attribution) from an
already-marked comment by passing a replacement body that omits it. This
is a fresh, single-file follow-up, not a continuation of #724's other work.

## Approach

Add a second marker check, nested inside the existing `'**[Claude Code]**'*)`
arm of the `$CURRENT_BODY` case statement, gating on `$BODY` before the
`gh api ... -X PATCH` call: if `$BODY` does not start with
`**[Claude Code]**`, refuse with a distinct stderr message and exit 1.

**Why exit 1, not exit 2, and why nested here rather than hoisted before the
GET** (revised from this plan's first draft after `staff-platform-engineer`'s
plan-review pass — see below): the file's real exit-2/exit-1 split isn't
"argv-shape vs. content check" (a content check, the stdin-emptiness guard,
already exits 2) — it's *pre-GET, input-shape-only checks that hold
regardless of which comment is being patched* (argc, REPO shape, COMMENT_ID
shape, BODY emptiness → exit 2) vs. *checks whose meaning depends on the
fetched `$CURRENT_BODY`* (GET failure, `$CURRENT_BODY` prefix mismatch →
exit 1). This new check only matters when `$CURRENT_BODY` already carries
the marker — that's the entire premise of the finding it closes
("already-marked comment") — so it belongs in the exit-1 bucket, nested
where it already is. The reviewer's literal suggested diff hoisted the
check before the GET (to skip a network round-trip on provably-bad input)
and switched it to exit 2; that was considered and rejected: hoisting makes
the check fire even when `$CURRENT_BODY` is *not* Claude-authored, showing
the caller a misleading "body missing marker" refusal instead of the
accurate "comment not Claude-authored, use /replies" one, and would force
rewriting `TestOwnershipMismatchRefused`'s fixture to keep exercising that
specific path. Keeping the check nested in the allow-arm — the one place a
marker-carrying `$CURRENT_BODY` is already established — avoids both.

[verified: `claude/.claude/scripts/respond-pr-safe-patch.sh` this session]
current relevant block:
```bash
case "$CURRENT_BODY" in
  '**[Claude Code]**'*)
    # -f (raw string), not -F (typed): ...
    gh api "$COMMENT_PATH" -X PATCH -f body="$BODY"
    ;;
  *)
    echo "... does not start with '**[Claude Code]**' ..." >&2
    exit 1
    ;;
esac
```

New shape — an `if` guard nested inside the existing allow-arm, matching
this file's own idiom for single-condition gates (`if [[ ... ]]; then ...
fi`, as used for the argc/REPO-shape/COMMENT_ID-shape/BODY-emptiness checks
above it) rather than a second `case`, and leaving the existing `-f` comment
in place untouched:
```bash
case "$CURRENT_BODY" in
  '**[Claude Code]**'*)
    # The quoted literal prefix below is required for a literal match: unquoted,
    # "[Claude Code]" is a glob bracket-expression (matches any one char in that
    # set), not the literal string -- same quoting the existing $CURRENT_BODY
    # case pattern above relies on.
    if [[ "$BODY" != '**[Claude Code]**'* ]]; then
      echo "respond-pr-safe-patch.sh: replacement body for comment $COMMENT_ID in $REPO does not start with '**[Claude Code]**' -- refusing to strip the ownership marker from an already-marked comment. No PATCH attempted." >&2
      exit 1
    fi
    # -f (raw string), not -F (typed): ...
    gh api "$COMMENT_PATH" -X PATCH -f body="$BODY"
    ;;
  *)
    ...
esac
```

### Assumption ledger

**Root problem:** `respond-pr-safe-patch.sh` doesn't require the PATCH
replacement body to preserve the `**[Claude Code]**` ownership marker,
so an invocation whose body omits it silently strips attribution from an
already-marked comment.

**Givens:**
- The `**[Claude Code]**` marker convention itself (its exact string, and
  that it's the ownership signal) is fixed by the rest of the PR-response
  system (`respond-pr` skill and any other caller of this script) —
  [reason: redefining the marker format is a cross-cutting change touching
  every caller, far outside this single-script fix].
- `gh api`'s `-f`/`-F` flag semantics (raw string vs. typed coercion) are
  vendor-fixed — [reason: vendor (`gh` CLI) imposes it; already the subject
  of an existing comment in this file].

**Mechanisms** (anchors: root):
- Add a body-marker gate in the existing allow-arm, before the PATCH call.
  Lighter primitives considered and rejected:
  1. Validate the replacement body at the caller (`respond-pr` skill),
     before ever invoking this script — rejected: this script's own header
     states its reason for existing is "the ownership check and the PATCH
     happen in one call so a caller can't skip the check"; pushing the new
     check to the caller reintroduces exactly the bypassable gap the script
     was built to close.
  2. Auto-prepend the marker to `$BODY` instead of refusing — rejected:
     silently rewriting caller-supplied content is more invasive than
     refusing, and mirrors the file's own precedent (the `CURRENT_BODY`
     mismatch path already refuses rather than repairs).
- `[engineer-verified]`: none — no direct engineer utterance this session;
  the exit-code/placement choice above is this session's own judgment call
  (revised once against plan-review feedback), not an engineer decision.

## Critical files

- **`claude/.claude/scripts/respond-pr-safe-patch.sh`** — add the nested
  `if` guard on `$BODY` described above, inside the existing
  `'**[Claude Code]**'*)` arm, with the quoting-rationale comment.
- **`claude/.claude/scripts/tests/test_respond_pr_safe_patch.py`**:
  - Add a new test class alongside `TestOwnershipMismatchRefused` covering
    three scenarios for the new check (all: Claude-authored `CURRENT_BODY`,
    varying `$BODY`; reuse the existing `fake_gh`/`_read_calls`/
    `_patch_calls` fixtures and `_CLAUDE_CODE_BODY` constant — no new
    fixture needed):
    1. `$BODY` missing the marker entirely → exit 1, zero PATCH calls, and
       assert the new check's distinct stderr text (this check's own
       message, not `TestOwnershipMismatchRefused`'s `/replies` text or
       `TestGetFailure`'s `could not fetch` text) — matching this file's
       existing convention of asserting a distinguishing stderr substring
       per refusal class.
    2. `$BODY` equal to exactly `**[Claude Code]**` (the glob's zero-width
       match) → exit 0, one PATCH recorded with that exact body — pins that
       the bare-marker case is currently allowed, so a future tightening of
       the check is a deliberate, test-visible change.
    3. `$BODY` containing the marker string but not at position 0 (e.g.
       leading whitespace before it) → exit 1, zero PATCH calls, same
       refusal class as scenario 1 — pins the check as an anchored prefix
       match, not a substring search, so a future `grep`-shaped
       reimplementation can't silently accept marker-anywhere bodies.
  - Update two existing test fixtures whose `input_text`/`tricky_body`
    values don't start with the marker and will newly hit the new refusal
    branch once it lands (found by `ciso-reviewer` and `staff-sdet`
    independently in plan-review): prepend `**[Claude Code]** ` to
    `TestPatchFailureAfterOwnershipCheckPropagates`'s `input_text` and to
    each of `TestGhMagicValueShapedBodiesUseRawFlag`'s four `tricky_body`
    values, preserving the magic-value-shaped suffix each of those cases
    exists to exercise (e.g. `"**[Claude Code]** null"`, not just
    `"null"`).

Single `code-writer` dispatch covers both files: the script change and its
tests are one indivisible unit (the tests exist to pin the script's new
behavior, and the existing-fixture updates are a direct consequence of the
script change), not a disjoint file set.

## Verification

```
../../../.venv/bin/pytest claude/.claude/scripts/tests/test_respond_pr_safe_patch.py -v
../../../.venv/bin/shellcheck claude/.claude/scripts/respond-pr-safe-patch.sh
```
