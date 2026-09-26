# Plan: apostrophe-thousands-separator false positive in the credit-card/SSN candidate scan (GH-1108)

## Context

Goal: stop `deny-pii-in-commits.sh`'s built-in credit-card and SSN candidate scans from denying a commit that contains an ordinary apostrophe-grouped thousands numeral (the Swiss/Liechtenstein convention, e.g. `N'NNN'NNN`) as if it were a credit-card number or SSN.

`deny-pii-in-commits.sh` builds `SCAN_TARGET` from the raw `$COMMAND` text plus appended staged-diff added lines, HEAD-diff added lines, and `-F`/`--file` message-source content (lines 396, 430, 453, 469). It computes `SCAN_TARGET_UNQUOTED=$(_lib_strip_shell_quotes "$SCAN_TARGET")` (line 480) over that whole concatenation and scans the union `SCAN_TARGET_BOTH` (line 486) for PII. `_lib_strip_shell_quotes` (`claude/.claude/hooks/_lib.sh:2579-2589`) deletes every bare `'`/`"` character unconditionally — a blunt, position-blind strip, deliberately over-approximating for the gates whose threat model needs it. When an apostrophe sits directly between two digit groups anywhere in `SCAN_TARGET`, the stripped copy joins the groups into one contiguous run. At line 526, `CC_CANDIDATES=$(grep -oE '\b[0-9]{13,19}\b' <<< "$SCAN_TARGET_BOTH" | sort -u)`, each candidate Luhn-validated before denying. A joined run that happens to land in the 13-19 digit window and pass Luhn denies an ordinary numeral — an over-deny, not a bypass.

Same root-cause *shape* as GH-826 (fixed and merged via PR #1107, in the sibling gate `deny-private-project-refs.sh`), whose own plan explicitly named this exact bug as its out-of-scope follow-up. GH-826's fix mechanism (narrowing a fixed-grammar Slack-channel-shape regex tail) does not transfer here — this is a different detection shape (an unbounded contiguous-digit-run scan plus a Luhn check), so the fix mechanism below is independently derived.

**Structural-sibling finding (this session, not in the original issue text):** the SSN regex at the same call site (`grep -qE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b' <<< "$SCAN_TARGET_BOTH"`, line 522) reads the identical `SCAN_TARGET_BOTH` and is exposed to the identical apostrophe-join mechanism. Per this repo's "audit structural siblings" rule, this plan's scope includes both the credit-card scan (line 526) and the SSN scan (line 522). The user-pattern scan (lines 537-541, arbitrary regexes from `~/.claude/pii-patterns.md`) shares the same exposure but is user-configured, not a built-in detector, and stays out of scope.

**Tier/threat-model context:** `deny-pii-in-commits.sh` is tiered `cooperative, irreversible` (header line 3; confirmed in `docs/hooks.md`'s per-hook table). `docs/hooks.md` states that repairing a mis-parse — a gate misreading an ordinary shape it should allow — stays in scope for every tier, even one whose over-deny bias is otherwise the "correct trade." An apostrophe-grouped numeral is an ordinary shape a cooperative agent emits, so this is a mis-parse repair, not a tier-barred relaxation.

**Open decision, resolved by the engineer this session:** any fix here also stops denying a value that is *really* card-or-SSN-shaped but happens to be written in apostrophe-grouped thousands form (a routine C++-style digit-separated literal), or deliberately quote-spliced at thousands-form positions in unquoted command text — the raw text alone cannot distinguish these from a legitimate numeral. The engineer chose to accept the relaxation everywhere (staged diff, HEAD diff, `-F` files, and `$COMMAND` alike), reasoning: "Detecting sensitive information is non-trivial and regex only goes so far." This matches `plan-architect`'s own recommendation, reasoned as: a card value written as a literal *string* (contiguous digits) is unaffected and still denies; only a value written in genuine grouped-numeral form, or a quote splice landing at the same positions, is exempted — and the latter is waivable because this gate's threat model is a cooperative agent, not an adversarial one.

Verified this session (settles ledger rows 7 and 8 below, previously unverified):
- Unquoted apostrophe pair in command text splices: `printf '%s\n' 1'23'4` prints `1234`.
- Apostrophe inside a double-quoted string stays literal: `printf "%s\n" "1'23'4"` prints `1'23'4`.

## Approach

Before the SSN and credit-card scans quote-strip the scan target, replace each apostrophe-grouped thousands numeral (`N'NNN'NNN`) with a space — only where stripping quotes would not join its digits to any other digit. Those two scans then read their own raw+stripped union built from the masked text. `_lib_strip_shell_quotes`, `SCAN_TARGET_BOTH`, the credential-value and user-pattern tiers, and the credit-card loop all stay as they are.

Three properties favor this shape over a per-candidate check after extraction:
- **Judged per occurrence.** The mask reads only a numeral's own characters and its nearest bound on each side. A genuine numeral elsewhere in the commit therefore cannot vouch for a differently spliced copy of the same value (row 23).
- **Linear cost.** Armed commits gain three subprocess passes over the scan target, however many numerals or candidates it holds (row 18). A per-candidate check costs two whole-text greps per exempt candidate and cannot stop at one. A hook that overruns the harness timeout lets the commit through unscanned (row 19).
- **No control-flow change.** The SSN and credit-card scans keep their regexes, labels, and loop, including its break on the first Luhn-valid candidate — a masked numeral never becomes a candidate (row 14).

The exemption applies at every append site, per the engineer's decision (row 22), and covers exactly that decision's scope (row 23).

This is the second quote-strip join artifact carved out in this gate family; GH-826 was the first. The shared root is stripping text the shell never parses (diff and `-F` content) alongside text it does (`$COMMAND`). It is not fixed here: fixing it changes the always-on credential-value tier's input under this gate's `irreversible` classification, so it goes to Out of scope as a follow-up.

### Assumption ledger

**Root problem.** `SCAN_TARGET_UNQUOTED` deletes every quote character across all appended content. An apostrophe used as a thousands separator therefore joins digit groups into a contiguous run in the stripped half of `SCAN_TARGET_BOTH`. The SSN regex, and the credit-card regex plus Luhn check, then read that run as PII and deny an ordinary numeral.

**Givens** (rows 1-5 are conditions this design cannot change; rows 6-30 are material assumptions).

1. `_lib_strip_shell_quotes` stays unchanged. Reason: `untrusted-input` gates share it (`deny-credential-bash-reads.sh:68`, `deny-network-installs.sh:89`). `docs/hooks.md`'s dependency invariant says shared `_lib.sh` code is never changed to accept a shape such a caller denies today. `[verified: _lib.sh:2579-2589; docs/hooks.md:22; call sites grepped]`
2. The hook is tiered `cooperative, irreversible`. Reason: the header and `docs/hooks.md` fix these rules:
   - A false-positive-cost argument alone never justifies relaxing the gate.
   - Repairing a mis-parse stays in scope.
   - Allowing an input the merge-base denied is stop-and-ask.
   - A waived routine encoding is recorded in the header's Known gaps, with the rationale in the commit message.

   `[verified: deny-pii-in-commits.sh:3; docs/hooks.md:13, :15, :26, :39, :47]`
3. The existing scans use `grep -E`; GNU `\b` is already in :522 and :526. The new mask uses `sed -E` with only POSIX ERE constructs: bracket expressions, groups, alternation, and intervals. It uses no `\b`, no `\s`, and no backreference in the pattern. `test_hook_alignment.py` bans `\s`. Reason: the hook ships to every stow consumer's platform. `[verified: test_hook_alignment.py:1101; deny-pii-in-commits.sh:522, :526; BSD sed not exercised]`
4. New helpers stay hook-local and are tested at the full-hook layer. Reason: `_lib.sh` is for helpers two or more hooks need. A new `hooks/_*-lib.sh` sidecar fails `test_hook_alignment.py` until `_HELPER_LIBRARY_NAMES` is widened, which is a separate backlog item. `luhn_valid` sits in the same position today. `[verified: .claude/rules/bash-unit-test-seams.md; .claude/plans/lighten-subprocess-tests.md:123-124]`
5. The installed hook scans this branch's commits, not the worktree copy. Its SSN/CC tier arms only when `pii-patterns.md` exists at the resolved config dir or at `~/.claude/`. `[verified: repo CLAUDE.md "Working in this repo"; deny-pii-in-commits.sh:302-309]`
6. Every append site feeds one strip:
   - `$COMMAND` (:396).
   - Staged added lines (:430).
   - HEAD added lines (:453).
   - `-F` content (:469).

   The strip happens at :480 and the union is built at :486. `[verified: those lines]`
7. A bare `'` between digits is a literal character inside a double-quoted `-m`, in a diff line, and in a `-F` file. `[verified: reproduced — printf "%s\n" "1'23'4" prints 1'23'4]`
8. In unquoted command text, one `'` per boundary delimits a single-quoted segment, and the shell joins the neighbors. No raw-text-only test separates a literal thousands separator from a genuine splice in `$COMMAND`. `[verified: reproduced — printf '%s\n' 1'23'4 prints 1234]`
9. The ordinary shapes this fix targets group by three from the right: the Swiss/Liechtenstein thousands separator and conventionally placed C++14 digit separators. `[unverified — if wrong, the fix is narrower than intended, never wider]`
10. The strip deletes exactly these characters:
    - Every `'` and `"`.
    - A `\` directly before another character on the same line. The following character is kept, so `\\` leaves one `\`.
    - A `$` directly before a quote.

    It deletes no newline and no other character, so it preserves the line count. `[verified: _lib.sh:2581-2584]`
11. Call `'`, `"`, `\`, and `$` the joining characters. Two raw digits become adjacent after the strip only if every character between them is a joining character. Any other character survives the strip. `[verified: follows from row 10]`
12. `luhn_valid` never doubles the final digit, so for uniformly random digits roughly one string in ten passes. `[verified: deny-pii-in-commits.sh:489-505]`
13. The 3-digit and 2-digit groups of `NNN-NN-NNNN` are below 1000, so the only thousands spelling that strips to the SSN shape writes the last group as `N'NNN`. A non-thousands join such as `NN'N-NN-NNNN` stays denied. `[verified: :522 regex; arithmetic]`
14. Masking changes a verdict only by removing a stripped-half match whose digits are exactly one masked numeral (credit card) or whose last group is one (SSN). It creates no match: its space token sits between bound characters that survive the strip (row 11). Each `\b`-anchored credit-card match and each SSN digit group is a maximal digit run. `[verified: by construction from rows 11 and 13 and the :522/:526 regexes]`
15. Explicit bounds, not `\b`, are load-bearing. With `\b` bounds, the thousands-shaped tail of a longer apostrophe run would be masked, and a non-thousands grouping would be allowed. Two concrete cases:
    - A four-digit leading group followed by thousands groups: the tail starts after a `'`, where `\b` holds.
    - Thousands groups followed by a four-digit final group.

    The explicit bounds exclude digits and the joining characters. `[verified: by construction; pinned by Test D rows 2-3]`
16. Existing quote handling survives:
    - A word-adjacent card (`x"<card>"`) is a raw-half match.
    - A double-quote splice of a card inside `-m` has no apostrophe numeral to mask, so it still joins and denies.

    No existing test covers the splice case. `[verified: test_deny_pii_in_commits.py:692-708; row 14]`
17. The hook runs `set -uo pipefail`, so the mask's `printf | sed` pipeline returns nonzero if either command fails. Both new call sites capture and check their status, per `_lib_strip_shell_quotes`'s call-site contract. `[verified: deny-pii-in-commits.sh:192; _lib.sh:2569-2574]`
18. The mask and second strip run only when armed, so an unarmed commit gains no process. An armed commit gains three subprocess passes over the scan target (the mask's sed, and the strip's sed and tr), whatever its candidate count. These come on top of the strip, credential, SSN, credit-card, and per-user-pattern passes the armed path already makes. `[verified: :480, :517, :521-541]`
19. A per-candidate exemption check costs two whole-text greps per exempt Luhn-valid candidate, and exempt candidates cannot end the loop. So its cost grows with candidate count times scan-target size. A hook that overruns the harness timeout does not block the tool call, so the commit proceeds unscanned. `[verified: deny-pii-in-commits.sh:141-146, :526-534]`
20. sed evaluates the mask expression in time linear in its input: the pattern has no backreference. `[unverified — Verification step 5 measures scaling against the sed on the machine that runs it, which is GNU sed on this repo's CI and most dev machines; BSD/macOS sed's scaling on this expression is not independently checked]`
21. Two applications of the global substitution mask every qualifying numeral:
    - One pass skips a numeral only when the previous match consumed its leading bound character as its own trailing bound.
    - On the second pass that character is free again, because the previous numeral is now a space token.

    `[verified: by construction; pinned by Test B's shared-line row]`
22. A value present at an occurrence only as its apostrophe-grouped thousands form is allowed at every append site: staged diff, HEAD diff, `-F` files, and `$COMMAND`. That covers two shapes:
    - (a) A card or SSN value written as a thousands-grouped number, such as a C++ digit-separated integer literal.
    - (b) A quote splice at thousands positions in unquoted command text.

    `[engineer-verified: "I think accept the relaxation. Detecting sensitive information is non-trivial and regex only goes so far."]`
23. The exemption is judged per occurrence. An occurrence is masked only when its own raw text is the value's exact thousands form, bounded as in row 15. A genuine copy of the same value elsewhere in the commit, even on the same command line, neither exempts a `'\''`- or `"`-spliced copy nor is exempted by it. The allowed set is therefore row 22's (a) and (b) and nothing wider. `[verified: by construction from rows 11 and 14; pinned by Test E]`
24. `$` and `\` count as joining characters even where the strip keeps them: a `$` not before a quote, or the second `\` of `\\`. A numeral separated from a digit only by such a character is therefore not masked and can still deny. Errors here lean toward denying. `[verified: rows 10-11]`
25. The credential-value tier (:517) and the user-pattern tier (:537-541) keep reading the unmasked `SCAN_TARGET_BOTH`. `[verified: :517, :538]`
26. No existing test is affected:
    - No existing test puts a quote or backslash between two digits.
    - The existing sed-shim tests at :957-1060 run unarmed, or deny before the armed block, so the new code never runs in them.

    `[verified: grepped test_deny_pii_in_commits.py, no hits; :957-1060 read]`
27. Literal PII-shaped values are safe only under `claude/.claude/hooks/tests/**`, which the diff scan always excludes. The hook header, this plan, the commit message, and the PR body are all scanned. Hook line :474 already holds a literal Luhn-valid example. `[verified: deny-pii-in-commits.sh:385, :474; test module docstring]`
28. When this ledger was drafted, no `pii-patterns.md` existed at `~/.claude/` or any per-account config dir on this machine. A clean self-scan therefore says nothing about SSN/CC behavior. `[verified: checked when drafted]`
29. `docs/security-hardening.md` and `docs/hooks.md` describe the built-ins at a level this change does not make false. `[verified: read when drafted]`
30. The header's Known-gaps bullet at :117-120 lists every checked strip call site. `[verified: :117-120]`

**Mechanisms:**

- **M1: `mask_thousands_numerals "<text>"`**, a hook-local helper. It prints the text with each qualifying numeral replaced by one space. A qualifying numeral is `[0-9]{1,3}('[0-9]{3})+` where, on each side, skipping any run of joining characters reaches a non-digit non-joining character or a line edge. The same sed substitution is applied twice. `anchors: row3, row4, row10, row11, row15, row21`
- **M2: an SSN/credit-card-only union.** Inside the armed block, the scan target is masked, then stripped, then joined with the raw text, with each step's status checked and failing closed. `anchors: row1, row6, row17, row18, row25`
- **M3: the SSN (:522) and credit-card (:526) scans read the M2 union.** Their regexes, labels, loop, and `break` are unchanged. `anchors: root, row13, row14, row18`
- **M4: keep the comments true.** Edit :473 alone, and extend :117-120 with the two new checked call sites. `anchors: row25, row27, row30`
- **M5: one Known-gaps bullet** recording the waived shape and its per-occurrence scope. `anchors: row2, row22, row23`
- **M6: full-hook tests:**
  - Allow cases that fail before the fix.
  - Pins for every mask branch.
  - Per-occurrence and distinct-value pins.
  - Fail-closed pins.

  `anchors: row4, row8, row14, row15, row16, row17, row21, row23, row26`
- **M7: placeholders** (`N'NNN`, `NNN-NN-NNNN`) in every scanned artifact. Concrete PII-shaped values appear only in the test module. `anchors: row5, row27, row28`
- **M8: one `code-writer` dispatch** for both files. It is one behavior change, and the red/green check needs both files together. `anchors: root`

**Over-powered-primitive check.** M2 is the heaviest new piece: a whole-text sed pass plus a second fail-closed strip on every armed commit, including one with no numeral. Alternatives rejected:

1. **No code change; reword instead.** A C++ literal or a data file in a diff cannot be reworded without changing the code. `anchors: root, row9`
2. **`exclude:` globs in `pii-patterns.md`.** These are per-user and per-path. They drop a path from every tier, including the always-on credential tier, and cannot reach `-m` or `-F` content. `anchors: row25`
3. **A per-candidate predicate after extraction** that exempts a candidate when the raw text holds it only in thousands form. It runs nothing on a commit with no candidate, but it judges per value, not per occurrence. A genuine numeral anywhere would vouch for a `'\''` or `"` splice of the same value elsewhere. It also costs two whole-text greps per exempt candidate, with no short-circuit. `anchors: row19, row23`
4. **The same predicate scoped per line** (the strip preserves line count). A single `-m` line can carry both copies, and the per-candidate cost remains. `anchors: row10, row19`
5. **Mask before the existing single strip, with no second strip.** This changes the stripped input of the credential-value and user-pattern tiers. A user regex that matches today's joined digits after a prefix would stop matching. `anchors: row2, row25`
6. **Reuse `SCAN_TARGET_UNQUOTED` when the mask changed nothing.** This saves two processes on a commit with no numeral, but adds a branch the tests must cover twice. The passes it saves are linear and run only when armed. `anchors: row18`
7. **Scan SSN/CC against the raw half only.** This reopens the double-quote splice in `-m`, a relaxation far wider than this mis-parse. `anchors: row2, row16`
8. **Strip `$COMMAND` only, leaving diff and file content raw.** It does not cover :396: a double-quoted `-m` carrying a genuine thousands numeral would still deny roughly one time in ten. It also changes the credential tier's input. `anchors: row6, row7, row12, row25`
9. **Parse shell quoting in `$COMMAND`.** Diff and `-F` content need no parse, and a quote parser must also model backslash escapes. `anchors: row7, row8`
10. **Accept any single-apostrophe grouping.** This would also admit a card in its four-digit presentation grouping. `anchors: row2, row9`
11. **A single-versus-adjacent-quote-pair discriminator.** Its premise is false: a single apostrophe in unquoted text already splices. `anchors: row8`
12. **Change `_lib_strip_shell_quotes`.** Barred. `anchors: row1`

## Critical files

Two files change, both in **one `code-writer` dispatch** (M8).

**`claude/.claude/hooks/deny-pii-in-commits.sh`** (modify)
- **M1**, added directly after `luhn_valid` (:489-505), with three comment lines, one fact each:
  - `# Prints $1 with each apostrophe-grouped thousands numeral replaced by a space, unless stripping quotes would join its digits to another digit.`
  - `# The substitution runs twice because one global pass skips a numeral whose leading bound character the previous match consumed.`
  - `# This expression must stay backreference-free: sed's runtime here is linear in input only without one, and a superlinear runtime reopens the fail-open timeout gap at :141-146.`
- M1's body is `printf '%s' "$1" | sed -E -e "<expr>" -e "<expr>"`, with the expression held in a `local`.
  - The ERE sed must receive: `(^|[^0-9'"\$])(['"\$]*)[0-9]{1,3}('[0-9]{3})+(['"\$]*)([^0-9'"\$]|$)`
  - The replacement is `\1\2 \4\5`.
  - A backslash is literal inside POSIX brackets, so each bracket holds `'`, `"`, `\`, and `$` (the negated brackets also exclude digits).
  - Build `local expr=...` with ANSI-C quoting (`$'...'`), not double-quoting: escape each literal `'` as `\'` and each literal `\` as `\\`, and leave `"` and `$` unescaped, since neither is special inside `$'...'`. Double-quoting this literal is a live trap — bash's double-quote backslash-escaping silently drops the backslash from `\$`, narrowing the excluded-boundary set (rows 11, 24) while leaving the resulting sed program syntactically valid, so the corruption produces no error signal of its own.
  - Confirm the bash quoting against Test D's `\` and `$'` rows and against Test H (below) rather than by eye.
- **M2**, inside the armed block (:521), before the SSN check, with two comment lines:
  - `# SSN/credit-card raw+stripped union, masked before the strip so a thousands separator cannot join digit groups.`
  - `# The credential-value and user-pattern scans keep reading the unmasked SCAN_TARGET_BOTH.`
- M2's steps:
  - `SSN_CC_MASKED=$(mask_thousands_numerals "$SCAN_TARGET")`, then capture `SSN_CC_MASKED_EXIT`. On a nonzero status, `emit_deny "Commit — could not mask thousands numerals in the SSN/credit-card scan target (exit ${SSN_CC_MASKED_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than scanning with degraded quote-split coverage."` then `exit 0`.
  - `SSN_CC_UNQUOTED=$(_lib_strip_shell_quotes "$SSN_CC_MASKED")`, reusing the shared strip unchanged, with the same check. The message names "could not quote-strip the SSN/credit-card scan target" and "sed/tr".
  - `SSN_CC_SCAN_TARGET=$(printf '%s\n%s' "$SCAN_TARGET" "$SSN_CC_UNQUOTED")`.
- **M3:** at :522 and :526, replace `"$SCAN_TARGET_BOTH"` with `"$SSN_CC_SCAN_TARGET"`. Keep the regexes, the labels, the credit-card loop, and its `break` byte-identical.
- **M4:**
  - Edit only line :473, so it says the union feeds the credential-value and user-pattern scans rather than "every scan below". Leave :474-479 byte-identical: :474 holds a literal Luhn-valid example, and rewrapping it would make it an added line that the armed self-scan denies (row 27).
  - Extend the :117-120 bullet's call-site list with the SSN/credit-card union's mask and strip.
- **M5:** add these two bullets after the :106-107 bullet in the header's Known gaps:
  ```
  #  - An SSN or credit-card value written as an apostrophe-grouped thousands
  #    numeral (`N'NNN'NNN`, or an SSN whose last group is `N'NNN`) is allowed
  #    when stripping quotes would not join its digits to any other digit:
  #    - This covers a C++ digit-separated integer literal.
  #    - It also covers a quote splice at the same positions in unquoted
  #      command text.
  #    - Each occurrence is judged alone, so another copy of the value
  #      elsewhere in the commit does not change its verdict.
  #  - mask_thousands_numerals's sed -E expression is exercised only against
  #    GNU sed (this repo's CI and most dev machines). BSD/macOS sed's
  #    behavior on this expression is not independently checked.
  ```
- Leave the following unchanged:
  - :396-472 and :480-486.
  - :517-519 and :537-548.
  - Every existing deny message.
  - The tier line.
- Write no literal digits in any new comment (M7).

**`claude/.claude/hooks/tests/test_deny_pii_in_commits.py`** (modify, additions only)
- Beside the fixture block (:35-38), add a one-line-docstring helper `_thousands_grouped(digits)`. It inserts `'` before every third digit counted from the right, and inside each hyphen-separated group for an SSN.
- Build these constants with it, each with a trailing comment naming its source:
  - `CARD_VALID_THOUSANDS`.
  - `CARD_13_THOUSANDS` and `CARD_19_THOUSANDS`, from the :669 and :676 literals, restated as constants.
  - `SSN_LAST_GROUP_THOUSANDS`.
- Insert the new tests after `test_word_adjacent_card_quote_split_denied` (:692-708):
  - Arm them with `pii_patterns("# no user patterns\n")`.
  - Reuse `_stage`, `_modify_unstaged`, `bash_input`, and `run_hook`.
  - `-F` rows follow the `msg_file` pattern at :863-866.
  - Parametrized rows carry `ids=`.
  - Docstrings stand alone and cite GH-1108.
- **Test A** (allow; fails before the fix). Parametrized over append site with `CARD_VALID_THOUSANDS`:
  - A staged line shaped like a C++ literal.
  - The same line as an unstaged modification of the fixture's tracked `file.txt`, committed with `git commit -a -m wip` (the HEAD-diff site, :453).
  - A double-quoted `-m`.
  - A `-F` file.
- **Test B** (allow; fails before the fix). Parametrized, all staged unless noted:
  - `CARD_13_THOUSANDS`.
  - `CARD_19_THOUSANDS`.
  - Both on one line separated by a single space. A single substitution pass leaves the second unmasked (row 21).
  - `CARD_VALID_THOUSANDS` as the entire content of a `-F` file, with nothing before or after it. Exercises the mask's `^`/`$` line-edge boundary alternative (row 15), which no other row reaches — every other append site's content is prefixed (git's diff marker, `git commit`, or an existing `-F` fixture's own leading text).
- **Test C** (allow; fails before the fix). Parametrized over append site with `SSN_LAST_GROUP_THOUSANDS`, mirroring Test A's append-site coverage:
  - Staged.
  - The same value as an unstaged modification of the fixture's tracked `file.txt`, committed with `git commit -a -m wip` (the HEAD-diff site, :453).
- **Test D** (deny; passes before and after). Parametrized with one row per preserved branch of the mask, each built from `CARD_VALID` unless noted:
  1. An apostrophe every four digits.
  2. A four-digit leading group, then thousands groups (row 15, leading side).
  3. Thousands groups, then a four-digit final group (row 15, trailing side).
  4. The first digit, then `"`, then the thousands form of the remaining fifteen digits (row 11).
  5. The same with `\` in place of `"`.
  6. The same with `$'` in place of `"` (row 24).
  7. The thousands form of the first fifteen digits, then `"`, then the last digit (trailing-side join).
  8. A double-quote splice inside a double-quoted `-m` (row 16).
  9. Contiguous `CARD_VALID` plus `CARD_VALID_THOUSANDS` in one staged file (a raw-half match).
  10. `SSN` written `NN'N-NN-NNNN` (row 13).
- **Test E** (deny; passes before and after). `CARD_VALID_THOUSANDS` with each `'` written as the `'\''` idiom inside a single-quoted `-m`. Parametrized over a genuine companion copy of the same value:
  - No companion.
  - The companion staged in a file.
  - The companion in a second, double-quoted `-m` on the same command line.

  Add one SSN row: `SSN_LAST_GROUP_THOUSANDS` staged, with its `'\''`-idiom form in a single-quoted `-m`. The docstring states that a splice spelled any other way denies at each occurrence, whatever copies exist elsewhere (row 23).
- **Test F** (deny; passes before and after). Distinct values in one commit, parametrized:
  - `CARD_13_THOUSANDS` staged, plus a double-quote splice of `CARD_VALID` in the `-m`.
  - `SSN_LAST_GROUP_THOUSANDS` staged, plus a double-quote splice of a second, distinct SSN-shaped value in the `-m`.

  Each second value is visible only in the stripped half. The test pins that masking one occurrence never hides another, and that the new union carries a stripped half for both scans.
- **Test G** (deny; fails before the fix). Fail-closed pins modeled on `test_scan_target_sed_failure_denied` (:1015-1060). Each row stages benign content and runs armed:
  1. A sed shim exits 1 when its arguments contain `{1,3}`, and otherwise `exec`s the real sed. Only the mask's expression contains `{1,3}`; `_lib.sh` has no such token.
  2. For a call whose second argument is `-e`, a sed shim reads stdin and exits 1 when stdin holds `<marker> <marker>`. The staged line is `<marker>N'NNN<marker>`, so only the strip of the masked text sees the marker.
- **Test H** (source-text pin; new, no pre-fix equivalent — fails before the fix, since `mask_thousands_numerals` does not exist yet). Reads `deny-pii-in-commits.sh`'s own text and asserts the assembled mask expression's brackets hold a literal backslash byte immediately before each `$`, not a bare `$`. Catches a bash double-quote transcription of `local expr=...` that silently drops the intended backslash (row 11) while leaving the sed program syntactically valid — Test D rows 5-6 catch this behaviorally too, but Test H pins the exact byte sequence directly, so a failure here points straight at the quoting bug rather than a downstream symptom.
- Do not edit any existing test.

## Verification

Run everything from the worktree root.

1. **Row 7 and row 8 premises** were already verified (see Context). No further action is needed before implementation.
2. **Red, then green.** Run `.venv/bin/pytest claude/.claude/hooks/tests/test_deny_pii_in_commits.py -k "<new test names>"`:
   - Against the unmodified hook, Tests A, B, C, G, and H fail, and Tests D, E, and F pass.
   - After the change, all pass.
3. **Scoped tests:** `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.
4. **Lint:** `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` and `.venv/bin/ruff check claude/.claude/ claude-skills/`.
5. **Scaling check (row 20), throwaway and not committed.**
   - Add a temporary parametrized test that stages a file of `CARD_VALID_THOUSANDS` lines at about 1 MB and about 10 MB. Run it once with `--durations=0`, then delete it. Confirm `git diff --stat` no longer shows it before staging.
   - Pass: the hook's time grows roughly in proportion to input size (about 10×, not about 100×), and both runs finish far inside the harness's default hook timeout. This hook's `claude/.claude/settings.json:291-295` entry sets no `timeout`.
   - Otherwise stop and raise: a super-linear mask reintroduces the fail-open in row 19.
   - This measurement covers only the sed engine actually installed where it's run — GNU sed on this repo's CI and most dev machines (row 20). It does not settle BSD/macOS sed's scaling; that residual is recorded in the header's Known gaps (M5) instead.
6. **Self-scan caveat, at the plan commit, the implementation commit, and PR creation.**
   - The installed hook's SSN/CC tier arms only when `pii-patterns.md` exists (row 5). When this ledger was drafted, it existed nowhere on this machine (row 28).
   - So a clean self-scan exercises only the always-on credential-value tier. Steps 2 and 5 are the evidence for SSN/CC behavior.
   - Re-check the arming state before each commit, and do not create the file just to test.
   - Keep literal Luhn-valid values, SSN shapes, and grouped numerals out of the hook header, comments, this plan, the commit message, and the PR body. Leave :474 byte-identical (row 27).
7. **Commit and PR text.**
   - The implementation commit message states the waiver rationale and quotes the engineer's answer (row 22), as `docs/hooks.md:47` requires.
   - The Known-gaps bullets land in that same commit, and the PR body quotes them.
   - The PR body notes that M1 is tested only at the full-hook layer, citing row 4's backlog item.

## Out of scope

- **`_lib_strip_shell_quotes` behavior** (row 1).
- **The credential-value and user-pattern tiers** (row 25). A user regex can hit the same join, but its exclusion rules belong to the user who wrote it.
- **Scoping the strip to `$COMMAND` only.** This is the shared root of this join artifact and GH-826's. Fixing it changes the always-on credential tier's input under this gate's `irreversible` classification, so raise it as a follow-up issue rather than folding it into this change.
- **Other characters between digits in content** (`"`, or `\` as in a Windows path segment). These share the root above and are not this issue's shape.
- **Multi-character apostrophe spellings in `$COMMAND`** (`\'`, `'\''`, `'"'"'`). These still deny at each occurrence, whether or not the commit holds a genuine copy elsewhere, and Test E pins that. Supporting them needs a per-spelling list — the shape GH-826 rejected. A double-quoted `-m`, a heredoc, or a `-F` file carries a bare apostrophe and is allowed after this change.
- **Non-thousands groupings.** Groups of four, Indian lakh grouping, and arbitrary C++ separator placement still deny when Luhn-valid (row 9).
- **Numerals next to a `$` or `\\` that the strip keeps** (row 24). These still deny, deliberately.
- **The existing under-scan gap at :106-107**: space- or dash-separated card numbers.
- **Direct unit tests for M1, and widening `_HELPER_LIBRARY_NAMES`** (row 4). M1 becomes this hook's second helper tested only through the full hook. The PR body cites the existing backlog item rather than widening the guard here.
- **Edits to `docs/security-hardening.md` and `docs/hooks.md`.** This change does not make either doc's existing description of the built-ins false (row 29).
