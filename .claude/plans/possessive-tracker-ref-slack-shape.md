# Plan: possessive tracker reference false positive in the Slack-channel shape detector (GH-826)

## Context

Goal: stop `deny-private-project-refs.sh` from denying commits, PR bodies, and docs that contain a possessive tracker reference (an issue number followed by `'s`).

`_lib_strip_shell_quotes` deletes the apostrophe, so the possessive becomes an issue number with a lone `s` joined on. The hook scans `SCAN_TARGET_BOTH` (raw text plus the quote-stripped copy). That joined token is not all digits, so it clears the issue-reference exclusion and matches `_LIB_SLACK_CHANNEL_SHAPE_REGEX` (its tail `[a-z0-9_-]*[a-z_-][a-z0-9_-]*` needs one non-digit). The raw possessive does not match, because the apostrophe ends the tail run.

Why now: it surfaced live during GH-815, where six possessive references were reworded to get past the gate. Outcome: possessive tracker references pass, and real Slack-channel-shaped mentions still deny.

The issue's line numbers are stale. The regex is at `claude/.claude/hooks/_lib.sh:3031` and `SCAN_TARGET_UNQUOTED` is at `deny-private-project-refs.sh:694`.

## Approach

Change only the tail of `_LIB_SLACK_CHANNEL_SHAPE_REGEX` so an issue number followed by exactly one final `s` counts as "all-digit", like a bare issue number. The quote-stripped copy of a possessive is exactly that shape, so possessives pass. Every other channel shape stays denied. The hook, `_lib_strip_shell_quotes`, and the raw+stripped union scan do not change, so the fix stays in the regex/exclusion layer the ticket names.

New tail, split by whether the name is letter-led or digit-led (the prefix is unchanged):

```
#([a-z_-]|[0-9]+(s[a-z0-9_-]|[a-rt-z_-]))
```

- **Letter-led** (`[a-z_-]` directly after `#`): identical to the old tail's class, so letter-led names behave exactly as today.
- **Digit-led, first non-digit is not `s`** (`[0-9]+[a-rt-z_-]`): still flagged.
- **Digit-led, `s` followed by another name character** (`[0-9]+s[a-z0-9_-]`): still flagged.

What passes is a tail that is all digits, or digits followed by one `s` and then a non-name character or end of line. The three branches do not overlap, so each pinned test maps to one branch.

This questions the ticket's two suggestions:
- "Special-case a digit-run followed by an apostrophe-stripped lowercase run": narrowed to one `s`. After stripping, "apostrophe-stripped" cannot be observed, and allowing any lowercase run would exempt digit-led channel names.
- "Exclude the quote-stripped variant's apostrophe-adjacent case": that is the heavier pre-strip helper (rejected alternative 2).

### Assumption ledger

**Root problem.** The Slack-channel-shape detector checks its all-digit exemption against the quote-stripped half of the scan union. There, `_lib_strip_shell_quotes` has deleted a possessive's apostrophe and joined its `s` onto the issue number, so an ordinary issue-reference possessive is denied as a channel mention.

**Givens** (rows 1-5 are conditions this design cannot change; rows 6-15 are material assumptions).

1. `_lib_strip_shell_quotes` stays unchanged. Reason: `untrusted-input` gates share it (`deny-credential-bash-reads.sh`, `deny-network-installs.sh`), and `docs/hooks.md`'s dependency invariant says shared `_lib.sh` code is never changed to accept a shape one of those callers denies today. `[verified: _lib.sh:2579-2589; call sites via grep; docs/hooks.md]`
2. The hook is tiered `cooperative, irreversible`. Under that tier a false-positive-cost argument alone does not justify relaxing a denied shape, while repairing a mis-parse stays in scope, and a waived shape is recorded in the gate's header. Reason: the hook header and `docs/hooks.md` fix the tier and the recording rule. `[verified: deny-private-project-refs.sh:3; docs/hooks.md "What a waiver accepts, and how it is recorded"]`
3. The engine is POSIX ERE under `grep -Eq`: no lookaround, no backreferences. The pattern must still work inside the derived combined alternation. Reason: the consumer fixes the engine; `test_hook_alignment.py` enforces POSIX-ERE-only. `[verified: deny-private-project-refs.sh:753-776; repo CLAUDE.md]`
4. The installed hook, running the current pattern, scans every commit and PR on this branch, not the worktree copy. Reason: `claude/` goes live only on a `git pull` of the main checkout. `[verified: repo CLAUDE.md, "Working in this repo"]`
5. The ticket keeps the fix in the regex/exclusion layer and rules out skipping quote-stripping, which would reopen the GH-424 class of bug. Reason: the ticket author owns the issue's constraints. `[verified: issue body, read this session]`
6. The raw possessive does not match; in the stripped copy the `s` joins the digits and the current tail matches. `[verified: reproduced this session with grep against the raw and quote-stripped text; a CISO reviewer reproduced all three apostrophe spellings]`
7. A possessive reaches `SCAN_TARGET` as a plain apostrophe or inside a single-quoted `-m` / `--body` via the `'\''` and `'"'"'` idioms, and every spelling strips to the same digits-plus-`s` bytes. Because the exemption is checked on the post-strip token, one regex change covers all of them. `[verified: deny-private-project-refs.sh:520, 565, 596, 631; _lib.sh:2581-2584]`
8. The stripped copy cannot tell a deleted apostrophe from a typed `s`, so a typed `#<n>s` token is also exempted. `[verified: _lib.sh:2584 deletes every single and double quote]`
9. Admitting a typed `#<n>s` token (a channel named as digits plus one final `s`) is acceptable for this `irreversible` gate. `[engineer-verified: "I think it should accept the relaxation"]` (answer to whether the gate should also stop denying a typed digits-plus-`s` token)
10. Locale variance in `[a-rt-z]` (a non-C collation locale may also admit a digit-led name whose first non-digit collates with `s`) is not a concern for this change. `[engineer-verified: "Don't worry about locale."]` The range is every code point collating between `r` and `t` in a non-C locale, and the new tail confines it to digit-led names, so letter-led non-ASCII names keep their old verdict. `[verified: a CISO reviewer swept all code points in C, C.utf8, en_US, en_GB and es_ES against the new tail; a staff-sdet run over 12 non-ASCII characters agreed]`
11. The new tail flags nothing the old tail did not, and stops flagging exactly digits, `s`, then a non-name character or end of line, apart from row 10's locale residual. `[verified: executed this session, `grep -E` on 20 shapes in the default and C locales; flagged set was exactly the shapes with a tail other than digits or digits-plus-`s`-terminal. A staff-sdet run of an equivalent tail compared 2.88M generated tails with 0 mismatches and a repo-wide raw+stripped corpus differential where every flipped line held a digits-plus-`s` token]`
12. The constant has one gating consumer (`deny-private-project-refs.sh:750`). Its only other reader, `test_post_crash_sessions.py`, asserts script output matches no detector, which narrowing cannot break. `[verified: repo-wide grep; test_post_crash_sessions.py:34-41, 217-221]`
13. No other scan in this hook has a content-based exemption a letter joined on by apostrophe-deletion can defeat. `[verified: by inspection, a CISO reviewer concurred; not executed end to end]`
14. No existing pinned test input contains a token ending in an issue number plus `s`, so no existing verdict changes. `[verified: Grep of claude/.claude/hooks/tests; one hit, a docstring at test_hook_alignment.py:603 that no hook scans]`
15. The staged-diff scan excludes only `claude/.claude/hooks/tests/**`; `_lib.sh`, `docs/`, the hook header, and this plan are scanned, so only the test module may contain a literal matching shape. `[verified: deny-private-project-refs.sh:512]`

**Mechanisms:**

- **M1: replace the tail of `_LIB_SLACK_CHANNEL_SHAPE_REGEX` with the pattern above.** One constant is the whole behavior surface. `anchors: root, row3, row6, row8, row9, row11`
- **M2: rewrite the all-digit bullet in the rationale comment above the constant** to state the true admitted set. `anchors: row9, row11`
- **M3: add a Known-gaps bullet to the `deny-private-project-refs.sh` header** recording the accepted digits-plus-`s` shape and the one-line locale residual. This is the single home `docs/hooks.md` names for a waived finding. `anchors: row2, row9, row10`
- **M4: add three test functions at the full-hook layer.** `anchors: row7, row11, row14`
- **M5: write illustrative text that no version of the pattern matches.** In every scanned artifact (the `_lib.sh` comment, the hook header, this plan, the commit message, the PR body) write possessives and digits-plus-`s` shapes with `#<n>` placeholders, never literally. `anchors: row4, row15`
- **M6: one `code-writer` dispatch for all three files.** The constant, its comment, the header bullet, and the tests are one atomic behavior change. `anchors: root`

**Over-powered-primitive check.** M1 is a single string literal with no new control flow. Alternatives rejected:

1. **No code change: keep rewording.** Status quo; fails the ticket's outcome and cannot help a staged diff that only touches a doc already containing a possessive. `anchors: root`
2. **Pre-strip possessive-drop helper (the ticket's first suggestion).** A new `_lib.sh` sed helper feeding `_lib_strip_shell_quotes`, with a fail-closed exit check. It must list every apostrophe spelling (row 7), changes the pinned `SCAN_TARGET_UNQUOTED=...` definition at `test_hook_command_normalization.py:148`, and breaks the sed-failure shim test's isolation. Its one advantage, keeping a typed `#<n>s` denied, is what row 9 waives. `anchors: row7, row8, row9`
3. **Make `_lib_strip_shell_quotes` keep an apostrophe between a digit and a letter.** Barred by row 1. `anchors: row1`
4. **Scan the Slack detector against the raw text only.** Reopens quote-split channel names; violates the ticket's constraint. `anchors: row5`
5. **A two-case tail with no `s` special case** (letter-led, or digits then any single trailing name character). It has no locale residual, but it admits digits plus any single letter, wider than the relaxation row 9 authorizes. `anchors: row9`
6. **A per-detector exclusion field in `STRUCTURAL_DETECTORS`.** A schema change to a security-critical array to serve one exception on one detector. `anchors: row12`

## Critical files

Three files change, all in a **single `code-writer` dispatch** (M6).

**`claude/.claude/hooks/_lib.sh`** (modify)
- Line 3031: replace the tail of `_LIB_SLACK_CHANNEL_SHAPE_REGEX` with the pattern in Approach; keep the prefix byte-identical.
- Lines 2970-2971: rewrite only the all-digit bullet to say the detector excludes a tail that is all digits, or digits followed by one `s` and then a non-name character or end of line, so an issue reference (example: issue `#421`) still passes once quote-stripping deletes a possessive's apostrophe. Add no separate residual bullet.
- Write no literal issue-number possessive or digits-plus-`s` token anywhere in this file (M5).

**`claude/.claude/hooks/deny-private-project-refs.sh`** (modify, header only)
- Add two bullets to the "Known gaps" list, one fact each, using `#<n>` placeholders (M5):
  - The Slack-channel shape accepts digits, `s`, then end of line or any non-name character (`#<n>s`, `#<n>s.`), because the quote-stripped copy cannot tell it from a possessive.
  - Under a non-C collation locale, a digit-led name whose first non-digit collates between `r` and `t` also passes the Slack-channel shape.
- Leave the tier line and fail posture unchanged; the waiver rationale goes in the commit message (see Verification step 4).

**`claude/.claude/hooks/tests/test_deny_private_project_refs.py`** (modify, additions only)
- Insert after `test_structural_slack_github_issue_reference_not_flagged_allowed` (~line 2632). Reuse `run_hook` / `bash_input` / `claude_config_repo`. Concrete literals belong only in this file (row 15). Docstrings are self-contained.
- Test 1, allow, red before the fix, parametrized over what follows the possessive: end of message, `.`, `,`, `)`. Uses a double-quoted `-m` and a multi-digit issue number.
- Test 2, allow, red before the fix: a typed `#<n>s` token with no apostrophe, pinning the accepted gap the way `test_structural_slack_accepted_splice_after_fabricated_bracket_allowed` pins its own.
- Test 3, one parametrized deny function over the tail alphabet after the digits. Its docstring says these rows pass before and after the change and each pins one member of the class:
  - letter-led: `_` or `-` directly after `#`;
  - the first non-digit after the digits is `-`, `_`, `a`, `r`, `t`, or `z` (numbered-prefix channel names);
  - the character after the digits-plus-`s` is `0`, `9`, `_`, `-`, `a`, or `z` (a digit-led name whose first letter is `s`).
  - Use multi-digit literals, give the parametrized rows `ids=` as `test_structural_detector_in_staged_diff_denied` does, and cite GH-826 in each docstring.
- Do not edit any existing test.

## Verification

Run from the worktree root.

1. **Allow tests red then green.** Tests 1 and 2 fail against the unmodified constant and pass after M1. Test 3 passes both before and after.
2. **Scoped tests:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
3. **Lint:** `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` and `.venv/bin/ruff check claude/.claude/`.
4. **Pre-commit check (session), at the plan commit, the implementation commit, and PR creation.** The installed hook runs the current pattern (row 4) over added lines outside the test module, the commit message, and the PR body, in raw and quote-stripped forms; none may match, so use `#<n>` placeholders. The implementation commit message states the waiver rationale and quotes the engineer's two authorizations from rows 9 and 10 in placeholder form, and the header bullet lands in that same commit. The PR body quotes them too.

## Out of scope

- **`deny-pii-in-commits.sh` credit-card candidates built by joining apostrophe-grouped digits.** Same bug class in a different, opt-in PII gate (an over-deny, not a bypass); raise as a follow-up issue.
- **`_lib_strip_shell_quotes` behavior (row 1).**
- **Locale-independent bracket handling (row 10).** The engineer ruled it out of scope.
- **Extra pins** for non-ASCII continuations, the staged-diff and `'\''` spellings, PR-body scanning, cross-repo `owner/repo#<n>` references, and a possessive plus a real channel in one message. Scan-target assembly and the strip are shared by every spelling, so none adds coverage the listed rows lack.
- **The other five structural detectors and the tracker-ID and blocklist tiers** (row 13).
- **Existing residual gaps on this constant:** the splice, the brace wrap, compact JSON, the content-blind `${...}` exemption, and link-destination trust.
- **Other contractions after an issue number (`'d`, `'ll`) and a Unicode right single quote.** The contractions are not ordinary prose after an issue reference; the Unicode quote is not deleted by the strip, so the raw tail already ends at it.
