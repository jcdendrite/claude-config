# Document the Bash-redirect gap in enforce-marker-script-shape.sh

## Context

`enforce-marker-script-shape.sh`'s Bash arm only evaluates write-authority
once a command's text contains the literal substring `marker.sh` (Stage 1:
`grep -qF 'marker.sh' || exit 0`). A Bash command that writes directly to a
marker path — `printf`/`tee`/`cat >`, or any other write primitive — without
that substring skips the check entirely, for every agent type, since the
path-based Write/Edit/MultiEdit arm never fires for the `Bash` tool. At the
time this plan was written, neither the hook's own "Known gaps" comment nor
`docs/hooks.md`'s "Marker keying and gate-release authority" section named
this specific gap, even though the repo already has a documented, accepted
precedent for the identical class of gap in `deny-reviewer-tree-mutation.sh`.
The goal is to close that documentation omission so the gap reads as a
deliberate, cited decision rather than something nobody noticed.

**Revision note (post-review, pre-merge):** PR #608 ("Plan mode review
gate") merged to `origin/main` while this branch was in flight and added the
hook-comment half of this fix independently — an equivalent "Known gaps"
bullet plus a strict `xfail` regression test pinning the same fact
(`test_bash_redirect_write_to_planmode_sibling_bypasses_write_authority` in
`test_enforce_marker_script_shape.py`). Rebasing this branch onto the synced
`origin/main` therefore pulled in that bullet; Critical Files' first item
(the hook-comment addition) is withdrawn as duplicate work rather than
implemented — see the updated Critical Files section. The `docs/hooks.md`
addition (second item) is unaffected: PR #608 did not touch that file, so it
remains the only doc-level explanation of this gap and this plan's sole
remaining scope.

The user asked whether disallowing the `Bash` tool for any command
containing `marker.sh` would close this — it would not: `marker.sh` is a
shell script, so `write`/`activate`/`deactivate`/`clear-stale` have no
invocation path other than Bash. Blocking Bash on that substring would
strand every review skill's gate-release call (`/code-review`,
`/plan-review`, `/ready-for-review`, `/respond-pr`) and the documented
`clear-stale` recovery step, with no alternative channel — direct
Write/Edit to a marker path is separately forbidden by policy. That approach
is rejected; see "Out of scope."

## Approach

Document-only fix, no behavior change. The gap is the same "arbitrary Bash
write-target" class `deny-reviewer-tree-mutation.sh` already accepts rather
than mechanically closes (proving where a raw redirect lands requires full
shell-write-target analysis) — so the fix here is parity in documentation,
not new code:

1. ~~Add a "Known gaps" bullet to `enforce-marker-script-shape.sh`~~ —
   withdrawn; PR #608 landed the equivalent bullet independently (see
   Context's Revision note and Critical Files).
2. Extend the `Bash` arm bullet in `docs/hooks.md`'s "Marker keying and
   gate-release authority" section with the same fact, framed the same way
   `deny-reviewer-tree-mutation.sh` frames its own equivalent gap: an
   instruction-following-dependent backstop — `CLAUDE.md`'s existing "never
   write `~/.claude/*-markers/*` by hand" rule — not a mechanical one. No
   change needed to `CLAUDE.md` itself, only a cross-reference to it; its
   wording already describes the mechanically-gated paths accurately and
   isn't being asked to claim more than that.

**Alternatives considered:**

- *Disallow Bash for any command containing `marker.sh`.* Rejected — see
  Context; there is no alternative channel for `marker.sh`'s operations, so
  this strands every review skill.
- *Extend Stage 1 to also fast-catch `printf`/`tee`/`cat >` redirects aimed
  at a marker-shaped path* (mechanical partial fix). Considered and
  presented to the user as an option; the user chose the document-only
  route instead. Noted here rather than silently dropped: `CLAUDE.md`'s
  "compounding defensive layers" guidance and `deny-reviewer-tree-mutation.sh`'s
  own precedent both argue against a text-match layer that closes one named
  shape (`printf`/`tee`/`cat`) while leaving the general class (`python -c`,
  `perl -e`, `dd`, `base64 | tee`, …) open under a false appearance of
  completeness. This gap's consequence if exploited — a forged review
  attestation that downstream commit/PR gates trust as proof a review
  happened — is more severe than `deny-reviewer-tree-mutation.sh`'s own gap
  (a visible tree mutation `git diff` would still surface), so the parity
  with that precedent is about the *shape* of the problem (enumerating every
  Bash write primitive is equally intractable in both cases), not a claim
  that the consequences match; documentation is still the right disposition
  because no amount of Stage-1 regex closes an unenumerable command set, but
  it is a consciously weighed tradeoff, not an inherited one.

### Assumption ledger

**Root problem:** `enforce-marker-script-shape.sh`'s Bash arm cannot see a
write to a marker path unless the command text contains `marker.sh`, and
that gap is currently undocumented.

**Givens:**
- `marker.sh`'s `write`/`activate`/`deactivate`/`clear-stale` operations have
  no invocation path other than Bash — the script must execute to compute
  hashes and walk PIDs, it cannot be replaced by a direct file write.
  [verified: read `claude/.claude/scripts/marker.sh` in full — every
  subcommand runs shell logic, not a static write]
- Direct writes to a marker path via `Write`/`Edit`/`MultiEdit` are already
  fully closed by the path-based arm (resolved-path match, immune to shell
  indirection). [verified: `enforce-marker-script-shape.sh` lines 113-214]
- Mechanically proving where an arbitrary Bash redirect lands requires full
  shell-write-target analysis, which the codebase has already declined to
  attempt for the identical gap class elsewhere. [verified:
  `deny-reviewer-tree-mutation.sh` lines 57-86, "Known gaps"]

| # | Assumption | Tag |
|---|---|---|
| row1 | No skill or agent in this repo legitimately writes a marker path directly via a Bash redirect — every documented write path goes through `marker.sh`. | [verified: grepped all `SKILL.md`/agent files for `-markers/` and `-active.d/` outside `marker.sh` calls; only read/list references found] |
| row2 | Documenting the gap (rather than adding a mechanical check) is the direction the user wants for this change. | [engineer-verified] |
| row3 | `CLAUDE.md`'s "never write markers by hand" rule already applies to every agent type, not only reviewers, so it is a valid citation without modification — cited as an instruction-following-dependent backstop, not a claim that it mechanically closes this gap. | [verified: read the Safety section bullet in `claude/.claude/CLAUDE.md`; its wording describes the mechanically-gated paths accurately and is not itself being changed or overclaimed] |

**Per-mechanism justification:**
- Adding prose to two existing comment blocks (anchors: root) — the
  lightest primitive available for "make an accepted gap legible": no new
  hook logic, no new regex, no new test surface. Heavier alternatives
  (Stage-1 regex extension, a new gate hook) were considered and are the
  rejected "mechanical partial fix" alternative above (anchors: row2) and
  the rejected Bash-block idea (anchors: root).

## Critical files

- ~~`claude/.claude/hooks/enforce-marker-script-shape.sh` — append a fifth
  bullet to the "Known gaps" comment block~~ — **withdrawn, see Revision
  note in Context.** PR #608 already added an equivalent bullet (and a
  stricter `xfail` regression test) to this exact block; this branch's
  rebase onto the synced `origin/main` pulled that bullet in, and adding a
  second one describing the same fact would duplicate it. No hook-file
  change ships from this plan.

- `docs/hooks.md` — append a sentence to the `Bash` arm bullet in "Marker
  keying and gate-release authority" (line 72), after "...so they surface as
  a permission prompt rather than a silent allow.":

  ```
  A command that never mentions `marker.sh` at all — a direct
  `printf`/`tee`/`cat >` redirect to a marker path — skips this arm's
  Stage-1 substring check the same way, for every agent type; this is the
  same class of "arbitrary Bash write-target" gap `deny-reviewer-tree-mutation.sh`
  accepts rather than mechanically closes (see that hook's "Known gaps"
  section) — an instruction-following-dependent backstop for the Bash path,
  not a mechanical one, per `CLAUDE.md`'s "never write markers by hand" rule.
  ```

No source files change behavior; no new tests are needed since this is a
comment/doc-only change with no logic delta.

## Verification

- Confirm `git diff origin/main -- claude/.claude/hooks/enforce-marker-script-shape.sh`
  is empty (the withdrawn item ships no hook-file change) and
  `docs/hooks.md`'s new sentence doesn't duplicate anything PR #608 added.
- `../../../.venv/bin/pytest claude/.claude/` — confirm the existing suite
  still passes (expected no-op, since no logic changed).

## Out of scope

- Extending Stage 1 to mechanically catch `printf`/`tee`/`cat >` redirects
  to marker-shaped paths (Option B, presented to and declined by the user).
- Disallowing the `Bash` tool for any command containing `marker.sh`
  (the originally proposed idea; rejected in Context — it would strand every
  review skill's gate-release path).
