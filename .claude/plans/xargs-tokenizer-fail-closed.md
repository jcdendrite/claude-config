# Plan: scan the files named after a value xargs cannot tokenize

## Context

Goal: make the three gates that extract body, message, input, and field-value file paths with `xargs -n1` scan those files even when `xargs` fails to tokenize the command, instead of silently dropping every flag after the failure point.

Ask: "And sure the xargs thing can be scheduled as a follow up PR" and "let's do the follow up PR now ... Plan-it yes". For the failure behavior, the engineer selected the label "Tolerant fallback, then deny (Recommended)" on a question about what the extractors should do when `xargs -n1` fails to tokenize a command.

Why now: PR #1262 closed the pipe-into-early-exit-grep fail-open and deferred this sibling gap. Six path extractors run `printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk …` and discard xargs's exit status. A probe on GNU xargs 4.9.0 shows it exits 1 and prints only the words before the failing one when the input has a double-quoted argument spanning a newline, a lone apostrophe, a heredoc body with an apostrophe inside a `--body "$(cat <<'EOF' …)"`, or a single 200 KB word. A 40 KB word and a trailing-backslash continuation do not fail it. A `--body-file`, `-F`, `--file`, `--input`, or `@path` that comes after the failure point is never scanned.

Intended outcome: the file is scanned when a tolerant tokenizer can recover the flag, and the command is denied only when neither tokenizer can split it, so ordinary multi-line `-m "…"` and `--body "…"` commands are not newly denied.

## Approach

Keep `xargs -n1` as the fast path. Only when it exits nonzero, retry with a new stdlib-`shlex` script that first drops heredoc bodies and joins backslash-newline continuations. A command the retry also cannot split is denied. One new `_lib.sh` helper, `_lib_command_words`, runs both tokenizers, and all six extractors read their words from it. The two duplicated extractor pairs become two shared `_lib.sh` functions. The two `gh api` extractors stay in `deny-private-project-refs.sh`.

After a retry, the helper prints the words xargs produced before it failed, then the retry's words. So no extractor can find fewer paths than it finds at the merge-base, and the change only adds scans and denies (M3). The dominant `-m "$(cat <<'EOF' … )"` and `--body "$(cat <<'EOF' … )"` shapes always take the retry path (row 2). That makes `python3` a runtime need of `deny-pii-in-commits.sh` and `deny-escaped-backticks-in-pr-body.sh` on that path in every repo. `python3` is already a declared prerequisite (row 5). The last commit adds a lint that keeps a bare `xargs -n1` tokenizer out of every hook.

Alternatives weighed (M2 holds the lighter-primitive check for the tokenizer itself):
- **Deny on any nonzero xargs exit.** Rejected: it denies every multi-line `-m`/`--body` value (row 2).
- **The Python tokenizer as the only tokenizer.** Rejected for two reasons. It adds a python3 spawn and dependency to every gated single-line command. It also changes the words the fast path emits today.
- **A raw-text flag scan as the fallback**, the prior plan's candidate direction. Rejected for two reasons. It cannot tell flag-shaped prose inside a quoted value from a real flag, which is why the extractors tokenize at all (`deny-private-project-refs.sh:431-433`). It also has no deny arm, which the engineer's choice requires (row 14).
- **A raw-text pre-check for any flag spelling before retrying.** Rejected: it creates a second copy of each extractor's flag grammar that must track the awk programs. The gain is one python spawn and a narrow over-deny residual (row 7).
- **Tokenizing once per hook.** Rejected. In `deny-private-project-refs.sh` it would tokenize, and could deny, an empty-staged-diff commit that the hook deliberately leaves to git (`:601-607`). Per-extractor calls deny only where a path lookup is needed. The cost is a second python spawn on a `gh api` command that uses both extractors.
- **Reusing `parse-git-command.py`.** Rejected; see M2 and row 4.

### Assumption ledger

**Root:** The six path extractors pipe the whole command through `xargs -n1 2>/dev/null | awk`. They discard xargs's exit status and treat a tokenizer abort as the end of the command (row 3). xargs aborts on a newline inside a quoted argument, an unmatched quote, or an over-long argument, and prints only the words before the failing one (row 1). Every flag after that point is dropped, and the file it names is never scanned. Two of the three hooks are `irreversible` gates, and the dominant multi-line commit and PR shape always triggers the abort (row 2).

**Givens:**
- G1. xargs's input grammar has three limits: a quoted argument cannot span a newline, quotes must balance, and one argument has a length cap. On a violation, xargs exits 1 after running its command only for the words before the failing one. Reason: the xargs implementation imposes this, and the hooks cannot change it.
- G2. `COMMAND` is the raw tool-input command and can carry newlines, heredocs, and nested substitutions. Reason: the agent owns it, and `_lib_parse_tool_input_or_deny` reads it NUL-delimited so newlines survive (`_lib.sh:529-531`).
- G3. A new file under `claude/.claude/hooks/` is live on `git pull` only where the consumer's hooks directory is one folded symlink. Elsewhere it is linked only when `./install.sh` re-stows. Reason: GNU Stow's linking model (`CHANGELOG.md:117`, `install.sh:47-51`). The window this opens is closed by M1's one-hop `readlink` fallback wherever `_lib.sh` itself is a stow link; M6's first clause covers the rest.
- G4. Hooks run under macOS `/bin/bash` 3.2 with BSD userland as well as on Linux. Reason: consumer machines impose it.
- G5. CPython's `shlex` handles quotes and backslash escapes but not heredocs, `$(…)` nesting, or `$'…'` escapes. Reason: this is stdlib behavior, and `parse-git-command.py` adds its own heredoc pre-pass for the same reason (`:100-133`).

**Mechanisms:**
- **M1.** Add `_lib_command_words CMD` to `_lib.sh`, next to `_lib_commit_fragment_has_worktree_target`.
  - The fast path is unchanged: `printf '%s\n' "$1" | xargs -n1 2>/dev/null`, captured into a variable, with the status captured on the next line. On status 0 it prints the words exactly as today.
  - On a nonzero status it runs `_lib_capped python3 -I "$(_lib_command_words_tokenizer_path)" <<< "$1"` with stderr discarded. `-I` keeps `PYTHONPATH` and user-site modules from shadowing `shlex`.
    - If the retry succeeds, it prints the xargs words, one blank line, then the retry's words (M3).
    - If the retry fails, it prints nothing and returns the retry's status.
  - `_lib_command_words_tokenizer_path` prints `$(dirname "${BASH_SOURCE[0]}")/split-command-words.py` when that file is readable. Otherwise it resolves `_lib.sh` through one `readlink` hop (no `-f`, which older macOS lacks) and prints the sibling of the link target. That closes the per-file stow window (G3) wherever `_lib.sh` is a link. It runs only on the retry path, so no hook pays a fork on the fast path. `_config.sh:31` is the precedent for locating a sibling file through `BASH_SOURCE`.
  - Declare `local` variables separately from their command-substitution assignment, so `$?` is not masked (`_lib.sh:1755-1757`).
  - The xargs call stays uncapped, as today.

  anchors: root, row1, row2, row6, row21
- **M2.** Add `claude/.claude/hooks/split-command-words.py`, a stdin-to-stdout script with stdlib imports only. It works in six steps:
  1. Read stdin as bytes and decode them as UTF-8 with `surrogateescape`. Write stdout the same way, so every byte round-trips into the hooks' path checks.
  2. Drop heredoc bodies. Split lines on `\n` only, never `str.splitlines()`, which also breaks on U+2028, U+0085, and `\x0b`/`\x0c`/`\x1c`-`\x1e`.
     - An opener is `<<` or `<<-` that is not part of `<<<`, then optional blanks, then a delimiter. The delimiter is bare, single-quoted, double-quoted, or backslash-prefixed (`<<\EOF`).
     - Match openers regardless of quote context, unlike `parse-git-command.py` (row 4).
     - For each opener on a line, in order, drop the following lines through the first line equal to the delimiter. For `<<-`, remove leading tabs before comparing.
     - Keep the opener line whole.
     - An opener with no terminator line is not a heredoc, and nothing is dropped for it.
  3. Drop every line whose first non-blank character is `#`, as bash discards a comment through the newline. Do this before step 4, so a comment ending in a backslash does not continue onto the next line.
  4. Delete every backslash-newline pair, as bash does for a line continuation.
  5. Tokenize with `shlex.shlex(text, posix=True)`, `whitespace_split = True`, `commenters = ''`, and no `punctuation_chars`. The retry diverges from xargs on purpose: it honors `\"` inside double quotes, which xargs does not, and it joins backslash-newline. A `#` that starts a word mid-line stays an ordinary word. That over-scans an inline comment, which can only add a scan or a deny, never drop a flag.
  6. Exit codes. Decoding or tokenizing failure (`ValueError`), or stdin over 2 MiB (the cap `parse-manifest-dependencies.py:76` uses), exits 3 with no stdout. Any other uncaught exception is caught in `main()` and exits 2. CPython's own uncaught-exception status 1 is never used, so a script bug or a Python 3.11 `SyntaxError` cannot read as "cannot be tokenized". Otherwise print each word on its own line, with every embedded newline replaced by a space, so no multi-line word can equal a flag word.
  7. The script imports only `re`, `shlex`, and `sys`, and stays Python 3.11-compatible (no f-string that reuses its own quote type, no PEP 695), because the README floor is 3.11 while CI and ruff run 3.12.
  8. The module docstring states three things:
     - The exit-status contract: 0 is success, 3 means the input cannot be tokenized, and any other status is a tool failure.
     - The residual misses (row 8) and over-denies (row 7), as probed, and that the divergence from xargs is intended.
     - One line saying `parse-git-command.py` keeps its own stripper, which skips an opener that follows an odd quote count.

  Over-powered check. The heavier choice is a python3 spawn plus a new file under hooks, compared with the rejected plain deny (row 2). Each lighter primitive fails:
  - **Status-checked xargs alone:** G1's grammar rejects every multi-line quoted value (row 2).
  - **A bash-native split:** `eval` and `compgen -W` perform command substitution, so the gate would execute the command. `read -a` ignores quotes.
  - **An awk quote state machine:** it hand-rolls quote and escape parsing where stdlib `shlex` exists, and it has no test seam except a subprocess.
  - **Embedded `python3 -c` in `_lib.sh`:** it avoids G3's re-stow window, but `claude/.claude/rules/shell-script-conventions.md` bars embedded Python with control flow of its own (row 11).
  - **A new mode on `parse-git-command.py`:** it also avoids G3. But it puts a general word splitter inside the git-records parser just to dodge a deployment window, and that parser's stripper has the wrong semantics here (row 4).

  anchors: root, row2, row4, row5, row7, row8, row11
- **M3.** After a retry, xargs's partial words come first, then a blank line, then the retry's words.
  - The blank line closes any pending flag in each awk program, and every caller already skips an empty path (row 13).
  - An extractor therefore never finds fewer paths than at the merge-base.
  - A path that appears in both streams is read twice, which is harmless.

  anchors: row9, row13
- **M4.** Add two shared extractors to `_lib.sh`:
  - `_lib_gh_body_source_paths CMD` holds the awk program from `deny-escaped-backticks-in-pr-body.sh:90-95`, moved byte for byte. That hook calls it at `:107`, and `deny-private-project-refs.sh` calls it at `:620` and `:655`.
  - `_lib_git_commit_message_source_paths CMD` holds the awk program from `deny-pii-in-commits.sh:422-427`. That hook calls it at `:505`, and `deny-private-project-refs.sh` calls it at `:579`.
  - Each function gets its words from `_lib_command_words` and returns that function's nonzero status unchanged. Otherwise it runs its awk program on a here-string and returns awk's status, so a missing awk denies instead of reading as no paths.
  - `extract_gh_api_input_paths` and `extract_gh_api_field_at_paths` stay in `deny-private-project-refs.sh`, since only one hook uses them. They take the same shape.

  anchors: row10, row11
- **M5.** Each call site captures the status on the line after the substitution. On nonzero it denies, and the reason does four things:
  - It contains "could not be tokenized", a phrase no current deny uses.
  - It names the surface and its flags.
  - It inserts `_lib_command_words_failure_clause "$status"`.
  - It gives the remedy: write multi-line or nested-quoted content to a file in an earlier Bash call and pass it with the flag, or split the command into separate Bash calls.

  The reason is built once, by `_lib_command_words_deny_reason SURFACE FLAGS STATUS` in `_lib.sh`, which all three hooks call (three hooks need it, the `bash-unit-test-seams.md` threshold). It echoes no part of the command or the interpreter's stderr, because an untokenizable `-m` value can hold the PII `deny-pii-in-commits.sh` guards. Each hook emits the deny with its own `_lib_emit_deny` and exits. The deny fires whether or not a flag appears in the command (see the pre-check alternative above).

  In `deny-private-project-refs.sh` the two `gh api` extractors take already-split words instead of the command. The `IS_GH_API` branch tokenizes once with `_lib_command_words`, checks that one status, and passes the words to both extractors. Otherwise the second status check (`:718`) is unreachable, because the first (`:690`) denies on the same input. The deny sites are then four: commit (`:579`), `gh pr` (`:620`), `gh issue` (`:655`), and the `gh api` branch.

  anchors: row3, row12, row14, row15
- **M6.** `_lib_command_words_failure_clause STATUS` in `_lib.sh` prints one clause, in the shape of `capped_read_failure_clause` (`deny-private-project-refs.sh:535-544`). It picks the clause by case:
  - **The tokenizer path is not a readable file:** `split-command-words.py` is not linked into the hooks directory. The human runs `./install.sh` from the claude-config checkout, and the agent stops and asks instead of working around the gate.
  - **`_lib_status_consistent_with_cap_kill` accepts the status:** the fallback tokenizer was killed (exit N) by the scan cap or a signal.
  - **Status 3:** neither xargs nor the fallback tokenizer could split the command. This happens on an unbalanced quote or on nested quotes inside `$(…)` or backticks.
  - **Any other status:** the fallback tokenizer failed (exit N), which is a tool fault, not the command's quoting. `python3` is a hard requirement of this repo (`README.md:96,100`, checked by `install.sh:30`), so the clause only names it as the first thing to check.

  anchors: row12, row18
- **M7.** Fix the text this change makes false or incomplete.
  - `deny-private-project-refs.sh`:
    - Delete the Known-gaps bullet at `:134-135`.
    - Add one Scope-and-limits bullet after `:75` (the end of the mutating-`gh api` bullet, `:63-75`) saying the hook fails closed when neither tokenizer can split the command.
    - Record the row 7 over-denies and row 8 misses in this header's Known-gaps list, since `docs/hooks.md:43,55` makes the header the place a residual is recorded. Do the same in the other two hooks' headers.
    - Replace the comment blocks at `:424-441` and `:452-460`, whose functions move to `_lib.sh`.
    - Update the "Same xargs tokenization" sentences at `:478-479` and `:501-503` to name `_lib_command_words`.
    - The "fail-open for unparseable input" sentence is superseded and does not move.
  - `deny-pii-in-commits.sh`: delete the comment at `:418-420` together with its function. Add one sentence to `:70-74` saying that a command the tokenizer cannot split is rejected.
  - `deny-escaped-backticks-in-pr-body.sh`: delete `:81-96`, and add the same fail-closed case to the Scope-and-limits bullet at `:26-29`.
  - The new `_lib.sh` comments state durable one-line facts only. Drop the whitespace-path sentence at `deny-private-project-refs.sh:436-438` unless it is re-verified, since xargs keeps a quoted path that contains a space as one word.
  - `docs/hooks.md:140`: add the untokenizable command to the hook's "Fails closed when" list. The entries at `:135` and `:136` list no fail-closed cases and stay unedited.
  - `docs/memory-audit-nudge.md:137-142`: it cites the deleted header bullet and `extract_body_source_paths` as the reason for its flag-order advice. Reword it to name `_lib_command_words`.
  - `test_deny_private_project_refs.py:1710, 3763, 3801, 3818, 3836`: update the docstrings that name the moved functions.

  anchors: row16
- **M8.** Add a lint, `test_hooks_tokenize_through_lib_command_words`, in `test_hook_alignment.py` as the last commit. It bans the bare tokenizer idiom in every hook outside `_lib.sh` (Critical files, Dispatch 3).

  anchors: row17
- **M9.** Add one CHANGELOG bullet that carries the re-stow instruction and the create-then-pass behavior change.

  anchors: row18, row20

**Rows:**
1. `xargs -n1` exits 1 and prints only the words before the failing one in four cases [verified: session probe `/tmp/xargs-probe-fc1/probe.sh` on GNU xargs 4.9.0, run by the session; the architect read the script but could not re-run it]:
   - a double-quoted argument spanning a newline;
   - a lone apostrophe;
   - a heredoc body with an apostrophe inside `--body "$(cat <<'EOF' … )"`;
   - a single 200 KB word.

   It succeeds on a 40 KB word and on a trailing-backslash continuation. A path placed before the failing word is still printed. Dispatch 2's red-at-merge-base tests re-confirm each shape in place.
2. A newline inside a double-quoted argument is enough to fail xargs, so the dominant heredoc-in-substitution shape fails it every time. [verified: probe case path-after-multiline-dquote; reads] The common case therefore takes the retry path, and a plain deny on an xargs failure would deny every one of these commands:
   - `deny-pii-in-commits.sh:505` runs the extractor on every detected commit.
   - `deny-escaped-backticks-in-pr-body.sh:107` runs it on every `gh pr create` or `gh pr edit`.
3. Sites and tiers. [verified: reads]
   - `deny-escaped-backticks-in-pr-body.sh:88-96`, called at `:107`. Tier `cooperative` (`:3`).
   - `deny-pii-in-commits.sh:421-428`, called at `:505`. Tier `cooperative, irreversible` (`:3`).
   - `deny-private-project-refs.sh:442-450, 461-469, 480-488, 504-518`, called at `:579, 620, 655, 690, 718`. Tier `cooperative, irreversible` (`:3`).
4. Why the retry must drop heredoc bodies, and why `parse-git-command.py` cannot do it:
   - In the dominant shape, the heredoc body sits inside one double-quoted region, so `shlex` reads each `"` in the body as closing or reopening that region.
   - A body with an odd count of `"`, or with an apostrophe in a gap between two closed `"` regions (an apostrophe inside a reopened region is harmless), leaves a quote open, and `shlex` raises. [unverified: reasoning from shlex's posix quote rules; Dispatch 1's tests pin it] Dropping the body first removes the problem.
   - `parse-git-command.py`'s stripper skips any opener that follows an odd quote count on its line (`:111-113`), and the `"$(cat <<'EOF'` opener is exactly that case.
   - It also truncates the opener line at the opener (`:119`), which drops any flag after the opener on that line. [verified: read]
5. The `python3` dependency and its precedent:
   - python3 >= 3.11 is a declared prerequisite, and `install.sh` checks it (`README.md:96`, `:100`). [verified]
   - `require-worktree-for-git-writes.sh` already runs `parse-git-command.py` through `_lib_capped` and denies when python3 is missing (`:279-294`). [verified]
   - The python spawn latency on the retry path is [unverified]; Verification step 6 records it.
6. `_lib_commit_fragment_has_worktree_target` (`_lib.sh:1746-1791`) is the precedent for capturing xargs's status separately and failing closed. [verified]
7. Residual over-deny: some valid bash still defeats the retry, so the hook denies it. [unverified: reasoning; Dispatch 1 pins each shape's actual outcome]
   - Known shapes:
     - a `"$(…)"` or backtick substitution whose inner quotes leave the flat quote parity open, such as `--title "$(echo "it's")"`;
     - a `$'…'` string with an escaped apostrophe.
   - In `deny-pii-in-commits.sh` this applies to every detected commit, with or without `-F`.
   - A chained command whose unrelated tool takes a flag the extractors read (`ssh -T host`, `tar -T list`, `grep -F pat`) newly reaches the retry stream once xargs's failure is recovered, and the gate denies when the argument is not a readable file. Merge-base dropped that flag with the rest.
   - None of these is the dominant shape (row 4).
   - `docs/hooks.md:13` keeps over-deny on shapes a cooperative agent does emit in scope for every tier, so the retry must accept the dominant shape. `docs/hooks.md:15` makes over-deny the correct trade for an `irreversible` gate.
8. Residual miss: in one shape the retry drops words bash would keep. It applies only past the xargs failure point (M3).
   - A quoted `<<WORD` mention followed later by a line equal to `WORD` makes the retry drop the lines between them.

   A cooperative agent does not emit it, so recording it needs no stop-and-ask (`docs/hooks.md:20`). [unverified: reasoning]
   - A mid-word `#` (`--title Fix#12 --body-file /b.md`) is a shape a cooperative agent does emit. M2 therefore sets `commenters = ''` and drops only comment lines in a pre-pass, so that miss does not exist. [verified: `shlex.py:230-238` in CPython 3.12 ends the token and discards the rest of the line when a commenter appears in the word state, read by two reviewers and probed by one: `-m fix#12 -F /x/msg` yields `fix` and nothing after.] Dispatch 1 pins the retry's outcome for `Fix#12` and for a `#` inside a path.
9. `docs/hooks.md:26-31` defines a regression and requires a deny test for any altered fail-open path. [verified: read; the example below is reasoning]
   - A regression is an input denied at the merge-base and allowed after the change, or a new fail-open path.
   - A fail-open path the change adds or alters needs a deny test.
   - Without M3, the retry's heredoc and comment stripping could drop a flag word that xargs printed before failing. One example is a `-F /path` line inside a heredoc body ahead of an apostrophe. That would allow an input the merge-base denies.
10. The duplicated extractors are byte-identical. [verified: reads]
    - `deny-escaped-backticks-in-pr-body.sh:90-95` matches `deny-private-project-refs.sh:444-449`.
    - `deny-pii-in-commits.sh:422-427` matches `deny-private-project-refs.sh:463-468`.
11. Placement rules and precedents. [verified]
    - `.claude/rules/bash-unit-test-seams.md` puts a helper in `_lib.sh` once two or more hooks need it.
    - `test_hook_alignment.py:93-118` sweeps only `*.sh` files under the hooks directories, so a `.py` there is not treated as a hook.
    - `parse-git-command.py` and `parse-manifest-dependencies.py` are the precedent for a `.py` beside the hooks. `test_parse_git_command.py:21-30` loads one through `importlib`.
    - `claude/.claude/rules/shell-script-conventions.md` bars embedded `python3 -c` that has control flow of its own.
12. Every call site captures its extractor in `$(…)`. `_lib_emit_deny` writes its decision to stdout (`_lib.sh:440-441`). A deny inside the extractor would therefore be captured instead of emitted, and its `exit` would end only the subshell. The status has to come back to the call site. [verified]
13. Every loop already skips an empty path, at `deny-escaped-backticks-in-pr-body.sh:110`, `deny-pii-in-commits.sh:508`, and `deny-private-project-refs.sh:582, 623, 658, 693, 721`. [verified]
14. The engineer's decision. [engineer-verified: "Tolerant fallback, then deny (Recommended)"] It covers two things: a tolerant fallback runs when xargs fails, and the hook denies when that fallback also fails.
15. The selected option's description was the session's proposal, not the engineer's words. [unverified] It had three parts: xargs as the fast path, a quote-aware tokenizer that accepts multi-line quotes, and one shared helper in place of the six copies. This plan adopts each part on its own grounds (M1, M2, M4). The plan applies the deny to all six extractors, including the `cooperative`-only `deny-escaped-backticks-in-pr-body.sh`. [engineer-verified: "Yes, all six extractors (Recommended)"] The quote answers the plan-review question on whether the final deny applies to that gate too, and covers only that.
16. The current header and doc text. [verified]
    - `deny-private-project-refs.sh:438-441` calls the dropped words "fail-open for unparseable input", and `:134-135` lists the drop as a Known gap.
    - That bullet carries no human-acceptance rationale, so it is not accepted debt (`.claude/plans/gate-hook-sigpipe-followup.md` row 13; `docs/hooks.md:20`).
    - Neither of the other two hook headers mentions the drop.
    - `docs/hooks.md:140` lists `deny-escaped-backticks-in-pr-body.sh`'s fail-closed cases. The entries at `:135` and `:136` list none.
17. The basis for the lint. [verified: Grep and reads]
    - After M4, only two `xargs -n1` sites remain under the hooks directories: `_lib_command_words` and `_lib_commit_fragment_has_worktree_target` (`_lib.sh:1756`). Both are in `_lib.sh`, and both capture their status.
    - A lexical lint cannot tell a pipeline whose status is tested from one whose status is ignored (the prior plan's Out of scope), so the lint bans the tokenizer idiom outside `_lib.sh` instead.
    - `_pipe_into_early_exit_grep_hits` (`test_hook_alignment.py:1351-1391`) holds the statement-joining loop the new detector needs.
18. `CHANGELOG.md:117` holds the wording for a new file in the hooks directory. `### Fixed` starts at `:330`, and the bullet for PR #1262 is first, at `:332`. [verified]
19. The Ask. [engineer-verified: "And sure the xargs thing can be scheduled as a follow up PR"] and [engineer-verified: "let's do the follow up PR now ... Plan-it yes"]. These cover doing this fix now, as its own PR, through `/plan-it`.
20. Some commands write a body or message file and pass it in the same call.
    - Today such a command already denies when xargs tokenizes it, because the file is not readable at hook time (`deny-escaped-backticks-in-pr-body.sh:115-118` and the matching sites). [verified: reads]
    - The same command with an apostrophe in its heredoc body is allowed today, because xargs drops the flag. [unverified: reasoning from row 1]
    - After this change, both deny. This is a user-visible behavior change named in the CHANGELOG bullet, and a hook-level test pins both arms. No engineer quote covers it beyond the Ask. [unverified]
21. HEAD at dispatch is the `main` tip `fa2f352a` with a clean tree, so the unedited hooks are the merge-base hooks. [verified: session git status at dispatch] Dispatch 1 adds `_lib.sh` functions but changes no existing one. The three hooks therefore still behave as at the merge-base when Dispatch 2 writes its red tests.
22. `LARGE_MULTILINE_TAIL` (`claude/.claude/tests/helpers.py:46`) has no quotes and 10,000-character lines, so xargs tokenizes it, and it cannot drive this failure. The new tests build their own inputs. [verified: read; row 1 for the 40 KB success]
23. `select-tests.py`'s blanket hooks-domain rule (`:549`) maps the new `.py` and its test. [verified]
24. On Linux, a single argv string is capped at 128 KiB, so a 200 KB command cannot reach `bash -c` as an argument. [verified: a reviewer's probe, where a 200 KB argument failed exec with E2BIG and 100 KB worked] The lib-level tests pass the long command through a file.
25. The lint in Dispatch 3 (M8) is kept in this PR. [engineer-verified: "Keep the lint (Recommended)"] The quote answers the plan-review question on whether Dispatch 3's lint stays in scope.

## Critical files

Three `code-writer` dispatches run in sequence. The session commits after each one, following `/code-review`. Each dispatch's output is the next one's input: Dispatch 2's hooks call Dispatch 1's helpers, and the lint passes only after Dispatch 2 removes the six sites. Paths are repo-relative, and line numbers are merge-base (`fa2f352a`) lines.

**Before Dispatch 1:** the session commits `.claude/plans/xargs-tokenizer-fail-closed.md` (plan-it Step 7).

**Dispatch 1, commit 1: the tokenizer.** Verify with Verification step 3.
- New `claude/.claude/hooks/split-command-words.py` (M2).
- New `claude/.claude/hooks/tests/test_split_command_words.py`.
  - Load the script through `importlib`, as `test_parse_git_command.py:21-30` does, and add one subprocess test of the stdin/stdout entry point.
  - Each case asserts the exact word list or the exit status:
    - A double-quoted value spanning lines, then `-F /x`. The value comes out as one flattened line, followed by `-F` and `/x`.
    - `--body "$(cat <<'EOF'` with a body holding an odd count of `"`, apostrophes, and backticks, then `--body-file /x`. No body word appears, and `/x` does.
    - Two heredoc-in-substitution commands chained with `&&`.
    - `cat <<EOF && gh pr create --body-file /x` with the body on later lines. The tail of the opener line survives.
    - `<<-EOF` with a tab-indented terminator, `<<\EOF`, and `<<"EOF"`.
    - A here-string `<<< "x"` is not an opener.
    - A quoted `<<EOF` mention with no terminator line. Nothing is dropped.
    - A backslash-newline continuation with no indentation before `--body-file`.
    - A comment line containing an apostrophe before the command.
    - A single-line word of at least 200 KB, then `-F /x`.
    - A non-UTF-8 byte in a path round-trips.
    - `-m it's -F /x`, `--title "$(echo "it's")"`, and `$'it\'s'`, each exiting 1 with empty stdout.
    - A mid-word `#`: `-m Fix#12 -F /x/msg` yields `-m`, `Fix#12`, `-F`, `/x/msg`, and `--body-file` with a path whose middle component contains a `#`, then `--repo r`, keeps the whole path and `--repo r`.
    - A comment line ending in a backslash does not join the next line, so `gh pr create --body-file /x` after it survives.
    - A multi-line quoted body holding a line that is exactly `-F` and a next line naming a path: no word equals `-F` (step 6 flattening).
    - A heredoc body holding U+2028 or `\x0c` is dropped whole (LF-only splitting).
    - `cat <<A <<B` with both bodies on later lines drops both.
    - `--body-file=/x` and `-F=/x` forms, and a quoted path containing a space, split the same as on the xargs path.
    - Valid multibyte UTF-8 (curly quotes, an em dash) round-trips. The non-UTF-8 byte case runs through the subprocess entry point.
    - Exit codes: untokenizable input and stdin over 2 MiB exit 3; a forced internal exception exits 2, never 1.
    - A parity table over inputs xargs can split, naming the intended divergences: backslash-newline (xargs two words, retry one), `\"` inside double quotes (xargs fails, retry succeeds), and `-e`/`-n`/`-E` words (xargs's echo swallows them).
    - The script's imports are exactly `re`, `shlex`, `sys`.
- `claude/.claude/hooks/_lib.sh`: add `_lib_command_words_tokenizer_path`, `_lib_command_words`, `_lib_command_words_failure_clause`, `_lib_command_words_deny_reason`, `_lib_gh_body_source_paths`, and `_lib_git_commit_message_source_paths` after `_lib_commit_fragment_has_worktree_target` (M1, M4, M5, M6). The comment on `_lib_command_words` states three facts:
  - It prints one word per line.
  - After an xargs failure, it prints xargs's words, a blank line, then the fallback's words.
  - It returns nonzero when neither tokenizer can split the command, and every caller must capture that status at once and deny.
- `claude/.claude/hooks/tests/test_lib.py`, using `_run_lib_call` (`:153`) and `build_path_without` (`helpers.py:2026`):
  - A single-line command's words equal plain `xargs -n1` output, and stay equal with `python3` removed from PATH. This proves the fast path never needs python3.
  - After an xargs failure, `_lib_git_commit_message_source_paths` prints a `-F` path that comes after the failure point.
  - M3's monotonicity, as a property over every xargs-failing fixture in the suite: the helper's output starts with plain `xargs -n1`'s partial output, then the blank line, then the retry's words. Each fixture also asserts its precondition (the early flag is in plain xargs's output), so the example cannot pass vacuously. Use a bare `cat <<'EOF'` opener for the early-flag fixture, because in the `-m "$(cat <<'EOF'` shape xargs dies at the first newline and never prints it.
  - A 200 KB single-line word followed by `-F /x` prints `/x`. Pass the command through a file, not inside the `bash -c` string (row 24).
  - A command that neither tokenizer can split returns status 3 with empty stdout.
  - Missing tools fail closed, mirroring `:3600-3612`. Parametrize `{xargs, python3, awk}` over {single-line, xargs-failing} for `_lib_gh_body_source_paths`, with each cell's expected status stated. Use a grammar-level failure (a newline inside a quoted argument, or a lone apostrophe) for every cell that needs the retry path, because BSD xargs may accept a 200 KB word.
  - The failure clause and reason:
    - The clause names `./install.sh`, the claude-config checkout, and "stop and ask" when `_lib.sh` is a real copy in a temp directory that holds `_config.sh` and `config-keys.psv` but no tokenizer.
    - With `_lib.sh` a symlink into a directory that holds the tokenizer, the readlink fallback finds it and the call succeeds.
    - It says "killed" for status 124, and reports a tool fault for any status other than 3.
    - `_lib_command_words_deny_reason` echoes no part of a command that holds a distinctive marker string.
  - A cap-engaged test: a `python3` shim that sleeps past the cap on an xargs-failing command returns the killed status. Follow `test_F_file_cat_timeout_denied` and `assert_cap_engaged`, marked `timing`.

**Dispatch 2, commit 2: wire the three hooks.** Do Verification step 1 for the three test files below, then step 4.
- Hook edits (M3, M4, M5, M7):
  - `claude/.claude/hooks/deny-escaped-backticks-in-pr-body.sh`: at `:107`, replace `extract_body_source_paths` with `_lib_gh_body_source_paths`, capture the status, and deny on nonzero.
  - `claude/.claude/hooks/deny-pii-in-commits.sh`: at `:505`, replace `extract_commit_message_source_paths` with `_lib_git_commit_message_source_paths`, capture the status, and deny on nonzero.
  - `claude/.claude/hooks/deny-private-project-refs.sh`:
    - At `:579`, `:620`, and `:655`, replace the two duplicated extractors with the shared functions.
    - Tokenize once in the `IS_GH_API` branch with `_lib_command_words`, and rewrite the two `gh api` extractors to take those words (M5).
    - Capture each status at `:579`, `:620`, `:655`, and the `gh api` branch, and deny with `_lib_command_words_deny_reason`.
- `docs/hooks.md`: line 140 (M7).
- `docs/memory-audit-nudge.md`: lines 137-142 (M7).
- The five stale docstrings in `test_deny_private_project_refs.py` (M7).
- `CHANGELOG.md`: one bullet, first under `### Fixed` (`:330`).
  - Lead clause: `deny-escaped-backticks-in-pr-body.sh`, `deny-pii-in-commits.sh`, and `deny-private-project-refs.sh` now scan a body, message, input, or field-value file named after a value that `xargs` cannot split. Examples are a multi-line quoted value, a heredoc in a command substitution, and a lone apostrophe.
  - When `xargs` fails, the hooks retry with the new `claude/.claude/hooks/split-command-words.py`. It uses stdlib `shlex` and needs `python3` on the hook's PATH. A command that neither tokenizer can split is denied.
  - A command that writes a body or message file and passes it in the same call is now denied regardless of what its heredoc holds (row 20).
  - The CHANGELOG states that `python3` now sits on the path of every heredoc-shaped commit in every repo and every heredoc-shaped PR, not only worktree-enforced repos.
  - The CHANGELOG re-stow sub-bullet keeps the wording of `CHANGELOG.md:117`, naming the deny a consumer sees when `_lib.sh` is not a stow link and the tokenizer is unlinked.
  - Include no size figures, no tracker-shaped tokens, and no backslash-backtick pair.
- New tests. One xargs-failing shape per hook proves the wiring; the shape matrix lives in Dispatch 1's tokenizer and lib tests. The hook layer covers one deny arm and one allow arm per call site.
  - Every deny test uses `run_hook_reason` and asserts the reason is not `None`. It then asserts a substring unique to that gate's match-arm deny, or "could not be tokenized" for the tokenizer deny. The code-writer names each substring in its report.
  - The merge-base outcome is in parentheses.
  - The scan prefix is the dominant `"$(cat <<'EOF' … )"` heredoc whose body holds an apostrophe, unless a test says otherwise.
  - The fail-closed fixture is input bash itself cannot parse: an unterminated quote (`gh pr create --title "unterminated --body-file <clean file>`), not valid bash. Valid-bash residuals (row 7) stay pinned only in the tokenizer tests, so a later tokenizer improvement does not break a hook test.
  - Every allow-guard body must make a no-heredoc-strip tokenizer raise: an odd count of `"`, or an apostrophe in a gap between closed `"` regions (row 4). A guard whose body keeps the quote parity balanced would pass with the strip step deleted.
  - `claude/.claude/hooks/tests/test_deny_escaped_backticks_in_pr_body.py`:
    - `test_body_file_after_xargs_failure_is_scanned`: a body file holding a backslash-backtick sequence is passed with `--body-file` after the prefix. A second case chains `git commit -m "$(cat <<'EOF' … )" && gh pr create --body-file <file>`. Expect the backtick deny (merge-base: allow).
    - `test_untokenizable_pr_command_denied`: expect "could not be tokenized" (merge-base: allow).
    - `test_heredoc_pr_body_with_quotes_and_apostrophes_allowed`: the dominant shape with a clean, discriminating body. Expect allow (merge-base: allow).
    - `test_create_then_pass_body_file_denied_with_and_without_apostrophe`: a heredoc that writes a body file followed by `--body-file` to that path, with and without an apostrophe in the heredoc. Both arms deny (merge-base: the no-apostrophe arm denies and the apostrophe arm allows; row 20).
    - `test_tokenizer_file_not_linked_denies`: the hook plus `_lib.sh`, `_config.sh`, and `config-keys.psv` copied into a temp directory with no tokenizer. A dominant-shape command denies with a reason naming `./install.sh`, and a single-line command still allows. (merge-base: allow.)
  - `claude/.claude/hooks/tests/test_deny_pii_in_commits.py`, reusing the setup of `test_F_file_with_pii_denied` (`:1225-1230`):
    - `test_F_file_after_xargs_failure_is_scanned`: the prefix is chained before `git commit -F <file>`. Expect the PII deny (merge-base: allow).
    - `test_untokenizable_commit_command_denied`: expect "could not be tokenized", and assert the reason holds no part of the command text (merge-base: allow).
    - `test_heredoc_commit_message_with_quotes_and_apostrophes_allowed`: a discriminating body. Expect allow (merge-base: allow).
  - `claude/.claude/hooks/tests/test_deny_private_project_refs.py`, using `claude_config_repo` and a synthetic non-allowlisted tracker token written the way this file's existing tests write one:
    - `test_source_file_after_xargs_failure_is_scanned`, parametrized over five surfaces. Each comes after a double-quoted value spanning lines. Expect a deny naming tracker-ID tokens (merge-base: allow).
      - `<prefix> && git commit -F`, chained (git rejects `-m` with `-F`), with a clean staged file as at `:3946-3947`
      - `gh pr create --body-file`
      - `gh issue create --body-file`
      - `gh api -X POST --input`
      - `gh api -X POST -f body=@<path>`
    - `test_untokenizable_gated_command_denied`, parametrized over the four deny sites (commit with a non-empty staged diff, which `:575-600` requires, `gh pr`, `gh issue`, and the `gh api` branch). Expect "could not be tokenized", and a reason that names that site's flags (merge-base: allow).
    - `test_heredoc_body_with_quotes_allowed`, parametrized over the same four sites, each with a discriminating body. Expect allow (merge-base: allow).
    - `test_chained_heredoc_commit_then_pr_body_file_scanned`: `git commit -m "$(cat <<'EOF' … )" && gh pr create --body-file <file with token>`, with a clean staged file. Expect the tracker deny (merge-base: allow).
  - A hook-level `python3` shim that sleeps past the cap is not repeated per hook; Dispatch 1's cap-engaged test covers the shared helper.

**Dispatch 3, commit 3, the last commit: the lint.** Do Verification steps 2 and 4. All edits are in `claude/.claude/hooks/tests/test_hook_alignment.py`:
- **Shared helper:** extract the statement-joining loop of `_pipe_into_early_exit_grep_hits` (`:1371-1391`) into a helper that both detectors call. That detector's hits must not change.
- **Detector:** `_bare_xargs_tokenizer_hits(hook)` returns hit statements. Its anchor: `xargs` as the command word of a statement, meaning at statement start or after `|`, `&&`, `||`, `;`, `$(`, or a `command`/`time` prefix. It flags such a statement when `xargs` is followed, after any other options, by `-n1`, `-n 1`, or `--max-args` with the value 1. The here-string spelling `xargs -n1 <<< "$cmd"` is flagged too, since the anchor does not need a pipe.
- **Test:** `test_hooks_tokenize_through_lib_command_words`, parametrized over `ALL_HOOKS` with repo-relative ids and no exemption dict.
- **Docstring:** a hook that tokenizes a command with a bare `xargs -n1` loses every word after an xargs failure, so the file named by a later flag goes unscanned. Tokenize through `_lib_command_words` instead. `_lib.sh` is outside the sweep because it holds the two status-checked xargs sites (row 17).
- **Meta-test, positive fixtures (each must be flagged):**
  - `printf '%s\n' "$c" | xargs -n1 2>/dev/null | awk`
  - `| xargs -n 1`
  - `| xargs --max-args=1`
  - `command xargs -n1`
  - `xargs -n1 <<< "$cmd"`
  - a pipeline split across lines with `|` and with `\`
- **Meta-test, negative controls (each must stay unflagged):**
  - `| xargs -0 rm`
  - a `case` pattern line such as `time|nice|xargs|npx)`, as at `deny-repo-relocation.sh:137`
  - an ERE string containing `xargs`, as at `deny-invisible-commit-content.sh:281`
  - `xargs -n1` in a full-line comment and in a trailing comment
- **Blind-spot pin:** these stay unflagged, each pinned by name:
  - `xargs` behind a wrapper other than `command`/`time` (`env xargs -n1`, `nice xargs -n1`)
  - an option held in a variable
  - a tokenizer other than xargs
  - other spellings of the same failure: plain `xargs` with no `-n`, `-L1`/`--max-lines`, `-n2`, and bundled forms such as `-rn1` or `-0n1`
- **Failure message:** the test's remedy text says to tokenize through `_lib_command_words` in the main hooks. Plugin hooks have their own `_lib.sh`, so the text says to use that plugin's own tokenizer helper there.
- **Module docstring:** add a bullet for the new check to the static-check list.

**Plan file:** `.claude/plans/xargs-tokenizer-fail-closed.md`, committed by the session before Dispatch 1.

**Reuse:**
- From `_lib.sh`:
  - `_lib_capped` (`:49-51`)
  - `_lib_status_consistent_with_cap_kill` (`:148-151`)
  - the status-capture shape of `_lib_commit_fragment_has_worktree_target` (`:1755-1789`)
- The shape of `capped_read_failure_clause` (`deny-private-project-refs.sh:535-544`).
- From `helpers.py`: `run_hook`, `run_hook_reason`, `bash_input`, and `build_path_without`.
- Each hook test file's existing fixtures.
- From `test_hook_alignment.py`: `_strip_comment` (`:1019`), `ALL_HOOKS` (`:159`), and the meta-test and blind-spot-pin structure that follows `:1428`.

## Verification

Run every venv tool from the worktree root as `../../../.venv/bin/<tool>`.

1. **Show that each new hook test fails without its fix, in place (Dispatch 2).** Row 21 keeps the three hooks at merge-base behavior after Dispatch 1.
   - Add the new tests first, leaving the three hooks unedited.
   - Run `../../../.venv/bin/pytest <the three test files> -k "<new test names>"`. Expect exactly the new deny tests to fail, each with the merge-base outcome given in Critical files, and the three over-deny guards to pass.
   - Report each failure message, so a reader can see it is the stated miss.
   - Apply the hook edits and rerun. Every test in the three files should pass.
2. **Check the lint's recall.** This check is throwaway and not committed.
   - Run `_bare_xargs_tokenizer_hits` over each hook's merge-base text, taken from `git show fa2f352a:<path>` and written to a temp directory.
   - The detector returns statement text, not line numbers, so expect per-file counts of 1 for `deny-escaped-backticks-in-pr-body.sh` (`:90`), 1 for `deny-pii-in-commits.sh` (`:422`), and 4 for `deny-private-project-refs.sh` (`:444,463,482,506`).
   - Expect zero hits on the branch.
   - The helper extraction is guarded by the existing flagged-shape meta-tests of `test_no_pipe_into_early_exit_grep`, which fail if its detector changes. Comparing the detector's hits before and after would compare empty to empty, because that test already requires zero hits.
3. **Unit tests (Dispatch 1).** `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_split_command_words.py claude/.claude/hooks/tests/test_lib.py` passes.
   - Report the actual outcome for each row 7 and row 8 shape.
   - If any outcome differs from this plan, report it and stop for the session's call before Dispatch 2.
4. **Scoped suite:** `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. It may widen itself to the full suite because `_lib.sh` changed; that is its decision to make.
5. **Lint:** `../../../.venv/bin/ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck` are both clean.
6. **Timing:** measure the python spawn directly, for example 20 runs of `python3 -I -S -c pass` with the median reported, and label it Linux-only. Also record `--durations` for the dominant-shape allow guards and the 200 KB lib case. Each should be well under the 5 s `_lib_capped` cap. `--durations` includes bash, jq, git, and pytest overhead, so it does not isolate the spawn.
7. **PR body content** (the session writes it through `/pr-description`):
   - the red-at-merge-base failure message for each new deny test;
   - that `python3` now sits on the path of every heredoc-shaped commit (`deny-pii-in-commits.sh`, in every repo) and every heredoc-shaped PR (`deny-escaped-backticks-in-pr-body.sh`), with the step 6 timings;
   - the re-stow window (G3), the readlink fallback that narrows it, and the deny a consumer sees when `_lib.sh` is a real copy; keep the literal word `install.sh` or `stow` in the body, which `require-stow-reminder.sh` checks;
   - the residual over-denies (row 7, including the chained-unrelated-flag shape) and the quoted-`<<WORD` miss (row 8), as Dispatch 1 probed them. The residual list is the session's judgment, not an engineer-accepted exception;
   - the create-then-pass behavior change (row 20);
   - the revert path (`git revert` plus `git pull`) and the in-session escape (write the message or body to a file in an earlier call, then pass `-F` or `--body-file`);
   - that the tests ran on GNU xargs and bash 5.x only, since CI is Ubuntu, so BSD xargs and bash 3.2 are unverified;
   - the commit that carries the plan file.

## Out of scope

- **`_lib_commit_fragment_has_worktree_target`'s own `xargs -n1` (`_lib.sh:1756`).** It already fails closed on an xargs failure, toward "target found", and it reads single-line fragments.
- **`parse-git-command.py`'s heredoc stripper skipping an opener inside `"$(…)"` (row 4).**
  - In worktree-enforced repos, this may make `require-worktree-for-git-writes.sh` deny a heredoc commit whose body leaves the flat quote parity open. [unverified]
  - That is a different gate, and there, dropping more text could hide a git write. Converging the two strippers needs its own plan.
  - No issue is filed without the engineer's approval.
- **Fast-path quirks of `xargs -n1` that this change keeps:**
  - `echo` swallows `-n`, `-e`, and `-E` words.
  - The pipeline is uncapped.
  - It forks one `echo` per word on a long command.
- **Heredoc-body words the fast path still emits as candidate paths when xargs succeeds.** This is unchanged from today.
- **The residual over-denies (row 7) and misses (row 8).** They are documented in the tokenizer's docstring, not closed.
- **No tracking issue.** This PR closes the gap the prior plan deferred to one.
- **Rejected outright, not deferred:**
  - a plain deny on any nonzero xargs exit;
  - the Python tokenizer as the only tokenizer;
  - a raw-text flag scan as the fallback;
  - a raw-text flag pre-check;
  - embedded `python3 -c`;
  - a new mode on `parse-git-command.py`.

  The reasons are in Approach and M2.
