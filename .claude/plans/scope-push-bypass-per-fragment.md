# Scope require-ready-for-review.sh's git-push bypass checks to a single fragment

## Context

`require-ready-for-review.sh` gates `git push` / `gh pr ready` / `gh pr create`
commands behind a completion marker, but bypasses the gate for git-push
shapes that don't publish a reviewable artifact change (`--dry-run`,
`--delete`/`-d`, colon-refspec deletion, tags-only push). That bypass block
greps the *whole* `$COMMAND` string for these flags instead of scoping the
check to the specific `git push` fragment that triggered it. When a
`git push --dry-run` is chained ahead of a real trigger (`gh pr create`, or a
second real `git push`) via `&&`, the whole-string `--dry-run` match lets the
entire command bypass the gate before the later fragment is ever evaluated —
a documented, deliberately-pinned gap
(`test_gh_pr_create_chained_after_dry_run_push_bypasses_known_gap` asserts
`"allow"` as the known-bad case, per GH-773). The `--delete`,
refspec-source-empty, and `--tags` checks share the identical whole-`$COMMAND`
shape and are in scope too, not fixed piecemeal. The intended outcome is to
scope all four checks to the fragment that matched `git push`, and flip the
pinned test to `"deny"` — per GH-773, "any future fix must flip that test
rather than delete it."

## Approach

Move the four `git push` exemption checks (`--dry-run`, `--delete`/`-d`, colon-refspec, tags-only) out of the post-loop block that greps the whole `$COMMAND` and into the fragment loop that already detects gated commands, so each check judges only the fragment that invoked `git push`. A push fragment that publishes nothing reviewable simply stops counting as a gated command, instead of short-circuiting the whole hook — so a `gh pr create`, `gh pr ready`, or second real `git push` chained after it is still evaluated on its own terms. In the same change, the `--tags` arm stops deriving its argument list from a `sed` that requires `git` and `push` to be textually adjacent, and derives it from a word walk in `_lib.sh` instead — closing a second fail-open where `git -C <path> push --tags <branch>` is exempted today despite publishing a branch ref.

### What changes in `claude/.claude/hooks/_lib.sh`

The `--tags` arm needs the words a fragment passes to `git push`, with git's own global flags and the values they absorb already consumed. `_lib_extract_git_subcmd` (lines 547–569) already performs exactly that walk to find the subcommand word, then discards everything after it. Rather than write the walk a second time, factor it once and give it two thin consumers.

**1. New `_lib_git_argv_from_subcmd`, inserted immediately above `_lib_extract_git_subcmd`** (between `_lib_fragment_invokes_git` at 525–538 and the subcommand extractor). It holds the walk verbatim from today's extractor — same `past_git` scan, same value-consuming flag list, same `-*` catch-all — and prints from the subcommand onward instead of stopping at it:

```bash
# Print a git fragment's argv from the subcommand onward, one word per line.
# Walks words to the `git` command word (same scan as
# _lib_fragment_invokes_git), then consumes git's global flags and the values
# the value-taking ones absorb, so the first line is the subcommand and each
# later line is one of that subcommand's own arguments.
# Prints nothing when no subcommand word follows the flags (`git --version`).
# The `--git-dir=<path>` form carries its value in the same word, so the
# catch-all `-*` arm skips it rather than the value-consuming arm.
# Globbing is disabled so a wildcard in the command text is not expanded.
_lib_git_argv_from_subcmd() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local past_git=false skip_next=false past_subcmd=false word
  for word in $fragment; do
    if $past_subcmd; then
      printf '%s\n' "$word"
      continue
    fi
    if ! $past_git; then
      if [[ "$word" == "git" || "$word" == */git ]]; then
        past_git=true
      fi
      continue
    fi
    if $skip_next; then skip_next=false; continue; fi
    case "$word" in
      -C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)
        skip_next=true ;;
      -*) ;;
      *) printf '%s\n' "$word"; past_subcmd=true ;;
    esac
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
}
```

**2. `_lib_extract_git_subcmd` becomes a thin first-line wrapper.** Its four call sites (`require-ready-for-review.sh:105`, `deny-reviewer-tree-mutation.sh:310`, `deny-pii-in-commits.sh:186`, `deny-private-project-refs.sh:251`) consume its stdout as a single word compared against a literal, so the observable contract — one word, no trailing newline, trailing non-alnum stripped, empty when no subcommand exists — is preserved exactly:

```bash
# Extract the git subcommand from a fragment like "git -C path push -u origin"
# or "GIT_DIR=x git push". Strips trailing non-alnum characters so that `push)`
# from paren-group splitting yields `push`.
_lib_extract_git_subcmd() {
  local subcmd
  subcmd=$(_lib_git_argv_from_subcmd "$1")
  subcmd="${subcmd%%$'\n'*}"
  printf '%s' "${subcmd%%[^a-zA-Z0-9_-]*}"
}
```

**3. New sibling `_lib_extract_git_subcmd_args`, inserted immediately below it.** The trailing-punctuation strip stays subcommand-only: an unstripped `feature)` fails the caller's exact-match allowlist and therefore gates, which is the fail-closed direction for a caller asking "is a branch ref present here?"

```bash
# Print the arguments a git fragment passes to its subcommand, one per line, so
# `git -C /wt push --tags origin` yields `--tags` and `origin`.
# Words are printed verbatim, so an unstripped trailing character (`feature)`)
# fails a caller's exact-match allowlist rather than passing it.
_lib_extract_git_subcmd_args() {
  local argv
  argv=$(_lib_git_argv_from_subcmd "$1")
  [[ "$argv" == *$'\n'* ]] || return 0
  printf '%s\n' "${argv#*$'\n'}"
}
```

`${VAR%%$'\n'*}` / `${VAR#*$'\n'}` is the existing idiom for splitting a delimited capture in this hooks tree (`redact-credential-values.sh:23-24` uses the same pair on `$'\x1f'`). Nothing here trips `test_no_bash4_constructs.py`, whose ban list is `declare -A`, `mapfile`/`readarray`, and `sort -V`.

Cost: `_lib_extract_git_subcmd` now forks one extra subshell per call, since the wrapper's `$(...)` nests inside the caller's. That is bounded by git-fragment count within a single command, and every invocation of these hooks already pays a `timeout`+`jq` exec pair in `_lib_parse_tool_input_or_deny` before the fragment loop is reached — the added fork is noise against that. The alternative that avoids it is duplicating the ten-line walk skeleton, weighed and rejected below.

### What changes in `claude/.claude/hooks/require-ready-for-review.sh`

**1. New file-local predicate, defined immediately above the detection loop** (after the `SESSION_ID`/`CWD` preamble, before the `# Detect gated commands by tokenizing fragments…` comment). It holds the four checks with the subject string changed from `$COMMAND` to the fragment, and with the tags arm's argument extraction routed through the new `_lib.sh` helper:

```bash
# True when a `git push` fragment publishes a branch ref a reviewer would see.
# --dry-run pushes nothing.
# --delete/-d removes every listed ref rather than publishing one.
# The colon refspec form (`origin :branch`) removes a ref only when every
# refspec in the fragment is delete-shaped; a real refspec alongside it
# publishes, so this arm also needs the exhaustive-remaining-args check.
# --tags with no other refspec publishes only tags.
push_fragment_publishes_reviewable_change() {
  local fragment="$1"
  local remaining
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)--dry-run(\s|$)'; then
    return 1
  fi
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)(-d|--delete)(\s|$)'; then
    return 1
  fi
  if printf '%s\n' "$fragment" | grep -qE '\s:[A-Za-z0-9._/-]+(\s|$)'; then
    # Delete-only holds only when every refspec is a deletion form, since a
    # real refspec alongside one is reviewable.
    # A literal $( or backtick anywhere in $COMMAND disqualifies the
    # delete-only bypass, since a runtime branch ref can hide inside a
    # substitution that _lib_split_fragments treats as a fragment boundary.
    if printf '%s\n' "$COMMAND" | grep -qE '\$\(|`'; then
      return 0
    fi
    remaining=$(_lib_extract_git_subcmd_args "$fragment" \
      | grep -vE '^(--force(-with-lease)?(=.*)?|--force-if-includes|-u|--set-upstream|origin|upstream|:[A-Za-z0-9._/-]+)$' \
      | grep -v '^$' || true)
    if [[ -z "$remaining" ]]; then
      return 1
    fi
  fi
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)--tags(\s|$)'; then
    # Tag-only holds only when --tags is the sole refspec hint, since a
    # branch ref alongside it is reviewable.
    # A literal $( or backtick anywhere in $COMMAND disqualifies the
    # tags-only bypass, since a runtime branch ref can hide inside a
    # substitution that _lib_split_fragments treats as a fragment boundary.
    if printf '%s\n' "$COMMAND" | grep -qE '\$\(|`'; then
      return 0
    fi
    remaining=$(_lib_extract_git_subcmd_args "$fragment" \
      | grep -vE '^(--tags|--force(-with-lease)?(=.*)?|--force-if-includes|-u|--set-upstream|origin|upstream)$' \
      | grep -v '^$' || true)
    if [[ -z "$remaining" ]]; then
      return 1
    fi
  fi
  return 0
}
```

Four deltas from the moved code. The `sed` and its `| head -1` are gone, replaced by the helper call — which also removes the `tr ' ' '\n'` step, since the helper already emits one word per line, and removes the `push_args` intermediate. The long `[[:space:]]`-vs-`\s` comment is deleted rather than rewrapped: with no `sed` left there is no BSD/macOS `-E` portability hazard to document. `remaining` becomes `local` per `.claude/rules/shell-script-conventions.md`, and new conditionals use `[[ ]]` per the same rules file; the file's untouched `[ ]` tests stay as they are. The `--dry-run` and `--delete`/`-d` `grep` bodies carry over character-for-character; the colon-refspec check gains the same `remaining`-args computation the `--tags` arm already had, closing a presence-only gap the security review for this PR found. The `--tags` arm's own flag/remote allowlist is unchanged.

The tags arm walks the fragment a second time (the loop already called `_lib_extract_git_subcmd` on it). That second walk runs only for fragments that literally contain `--tags`, so threading the first walk's output through the loop would add a parameter for a case that rarely fires.

**2. Rename the boolean and fold the exemption into the loop.** `is_git_push` becomes `is_gated_git_push`, because after this change it means "a push fragment that publishes a reviewable change," not "any push." Inside the loop:

```bash
  if _lib_fragment_invokes_git "$frag"; then
    subcmd=$(_lib_extract_git_subcmd "$frag")
    if [[ "$subcmd" == "push" ]] && push_fragment_publishes_reviewable_change "$frag"; then
      is_gated_git_push=true
    fi
  fi
```

The loop's leading comment gains one sentence: a push fragment that publishes nothing reviewable is not a gated command. The early exit becomes `if ! $is_gated_git_push && ! $is_gh_pr_ready && ! $is_gh_pr_create; then exit 0; fi`. The deny-message selector at the bottom needs no change — its `else` arm is reached only when `is_gated_git_push` is the true one.

**3. Delete the whole `if $is_git_push; then … fi` bypass block (current lines 119–147).** Its content now lives in the predicate; nothing else referenced it. Net effect is one fewer exit path, not one more.

**4. Header comment.** In the `Bypass cases` list, the three push bullets keep their wording and gain one line beneath them recording that they are judged per git-push fragment, so a bypassable push chained ahead of a gated fragment does not exempt it. The `Known gaps` paragraph loses the `--dry-run`-greps-`$COMMAND` sentence entirely, keeping only what stays true — the default-branch bypass running before any command-type check, and (unrelated to this fix, carried forward from the branch's rebase onto a since-merged sibling PR that made the hook's `settings.json` dispatch unconditional) the `gh pr ready`/`gh pr create` arms' plain-text-regex detection missing a full-path invocation. The PR-relative framing in the original paragraph ("unchanged by this diff", "not yet filed as a follow-up issue") goes with it; the replacement describes current behavior only.

### Directional guarantee

For `--dry-run` and `--delete`/`-d`, safety rests on the exempted operations themselves, not on substring containment alone: whenever one of these patterns matches — whether against the whole `$COMMAND` or a single fragment — the matched flag applies to the whole push invocation by its own git semantics (`--dry-run` pushes nothing at all; `--delete`/`-d` deletes every listed ref), so bypassing on a match is always correct regardless of what else is in the fragment. Fragment-splitting and paren-stripping can shift where the anchors (`^`/`\s`/`$`) land relative to the matched text. `(cd /wt; git push --dry-run)` fails the old whole-`$COMMAND` check because the trailing `)` blocks the `(\s|$)` anchor, but the paren-stripped fragment `git push --dry-run` matches cleanly, moving that shape from over-gated to correctly-exempted. That shift can only correct an over-gate, never manufacture a false bypass: paren-stripping and fragment-splitting only ever remove surrounding syntax, they never insert a `--dry-run`/`-d`/`--delete` sequence that wasn't already present in `$COMMAND`.

The colon refspec does not share that global-applicability property. `git push` accepts multiple space-separated refspecs per invocation, so a fragment can carry a deletion refspec (`:old-branch`) alongside a genuine branch-publishing one (`new-feature:new-feature`) in the same command — git's documented rename-on-remote idiom. A presence-only match on `\s:token` would exempt the whole fragment on the deletion refspec alone, missing the real push sitting next to it. The colon-refspec arm therefore gets the same exhaustive-remaining-args check as the `--tags` arm below: every subcommand argument must be a known-safe token (the existing flags/remotes, or a pure deletion-refspec token) before the fragment is treated as delete-only. It also gets the same command-substitution guard the `--tags` arm needs, for the identical reason: `_lib_split_fragments` treats `$(...)`/backticks as fragment boundaries, so `git push origin :old-branch $(echo new-feature:new-feature)` hands the arm a fragment showing only `:old-branch` — the real refspec is chopped off by the splitter and never enters the `remaining`-args computation.

The `--tags` arm is the one place where the answer moves both ways, and it now moves for three independent reasons. First, per-fragment scoping replaces "the last `git push` in the whole string wins" (the old `sed`'s greedy `.*git[[:space:]]+push`) with "each push fragment judged on its own." Second, the word walk replaces textual `git`-then-`push` adjacency with the same flag-skipping scan the subcommand extractor uses. Third, a fragment can genuinely be tag-only while still containing a command substitution whose runtime output adds a branch ref the fragment-scoped text never shows. Concretely:

- `git push origin feature && git push --tags origin` — **allow today, deny after**. The last push's args look tag-only, which exempts the real branch push in front of it.
- `git -C /wt push --tags origin feature` — **allow today, deny after**. The `sed` finds no adjacent `git push`, so it emits nothing, `remaining` is empty, and the tag-only exemption fires despite the `feature` branch ref. This is the only single-fragment shape whose verdict moves under the first two reasons, and it moves toward gating.
- `git push --tags origin && git push --tags origin2` — **denies both before and after this change**, for different reasons, so it does not demonstrate a directional movement on its own. Before: the greedy `sed` reads `origin2` as part of a single push's args and gates because `origin2` fails the pre-existing remote allowlist (`origin`/`upstream` only). After: each fragment is judged independently — the first (`--tags origin`) is genuinely tag-only and bypasses on its own, but the second (`--tags origin2`) still fails the same remote allowlist and gates on its own. The overall verdict is unchanged; only which fragment (and which reason) drives it differs.
- `git push --tags $(echo origin feature)` (or the backtick form) — per-fragment scoping alone, without a further guard, would flip this shape to **allow**: `_lib_split_fragments` treats `$(...)`/backticks as fragment boundaries, so the tag-only check never sees the `feature` branch ref hidden inside the substitution, even though that substitution's output becomes a real push argument at execution time. A coarse guard closes this: the `--tags` arm refuses the bypass whenever `$COMMAND` contains a literal `$(` or backtick anywhere, regardless of which fragment it belongs to. That keeps this shape denied, matching today's verdict, and is covered by `test_tags_only_push_with_command_substitution_still_gated`.

The colon-refspec arm moves the same way, for the same reason: `git push origin :old-branch new-feature:new-feature` — **allow today, deny after**. Before: the presence-only check exempts the fragment on the `:old-branch` match alone. After: the exhaustive-remaining-args check leaves `new-feature:new-feature` unaccounted for, so the fragment gates. Covered by a dedicated regression test alongside the `--tags` arm's own.

The colon-refspec arm's command-substitution guard moves the same way the guard does for `--tags`: `git push origin :old-branch $(echo new-feature:new-feature)` (or the backtick form) — **allow today, deny after**. Without the guard, the exhaustive-remaining-args check only ever sees the fragment `_lib_split_fragments` hands it, which stops at the `$(` boundary and never sees the real refspec hidden inside the substitution. The guard refuses the delete-only bypass whenever `$COMMAND` contains a literal `$(` or backtick, matching the `--tags` arm's own guard.

A fragment combining both arms' exemption shapes also moves: `git push origin :feature --tags` — **allow today, deny after**. Before the exhaustiveness fix, the colon-refspec arm's presence-only check matched `:feature` and exited before the `--tags` block was ever reached. After: the colon-refspec arm's own allowlist doesn't recognize `--tags` as safe, so `remaining` is non-empty and it falls through; the `--tags` arm's own allowlist likewise doesn't recognize `:feature` as safe, so it falls through too. Neither arm's bypass fires, so the fragment gates.

The first two `--tags` movements and the colon-refspec exhaustiveness movement are accuracy corrections rather than policy changes: they close a fail-open the old presence/adjacency-dependent checks had. The third `--tags` movement demonstrates the fix does not create a new gap on its own (same verdict, different reasoning — no test needed since nothing observable moves). The fourth `--tags` movement and the colon-refspec arm's own command-substitution guard are both deliberate fail-closed guards against a shape per-fragment scoping would otherwise regress, each shipped with its own test rather than left to a later discovery. The colon-refspec-and-`--tags`-combined movement is a byproduct of the exhaustiveness fix rather than a guard of its own, also shipped with its own test. Every shape currently covered by a test in `test_require_ready_for_review.py` keeps its verdict.

### Alternatives weighed

- **Record the matched push fragment and keep the post-loop block** (smallest diff: set `push_fragment="$frag"` alongside the boolean, then grep that instead of `$COMMAND`). Rejected as incorrect: with more than one push fragment it tests only the last one, so `git push origin feature && git push --dry-run` would still be exempted — the same bug mirrored rather than fixed.
- **Inline the four checks in the loop body with no helper.** Rejected: the loop body would run ~25 lines and mix "which fragments are gated commands" with "which push shapes publish nothing," and the block needs a name to be read at all — CLAUDE.md's extract-functions-when-you-must-explain-a-fragment rule applies.
- **Promote `push_fragment_publishes_reviewable_change` into `_lib.sh`.** Rejected: seven hooks source that file, and there is exactly one consumer for this policy. The word walk is a different case — it has two real consumers, one of which already lives in `_lib.sh` — which is why the mechanism moves there and the policy does not.
- **Rewrite the `--tags` arg extraction as a word walk.** **Adopted in this change** (see the `_lib.sh` section above). Previously set aside as a separate defect with a separate mechanism; the engineer's call this session is to fix it here rather than file it, so the latent `git -C <path> push --tags <branch>` fail-open closes in the same PR as the fragment-scoping fix.
- **Duplicate the walk in a hook-local helper, leaving `_lib.sh` untouched.** Rejected: two copies of the skip list plus its state machine, in the same repo, drift silently — CLAUDE.md's single-source-of-truth rule names duplication a defect absent a listed exception, and none applies here.
- **Share only the flag list, via a `_lib_git_global_flag_takes_arg` predicate both loops call.** Rejected: the list is the small half. Getting `git -C /wt push` right also needs the `past_git` scan, the `skip_next` state, and the `-*` catch-all — the ten-line skeleton, which this variant still writes twice.
- **A mode argument on `_lib_extract_git_subcmd` (`… "$frag" args`).** Rejected: four hooks read its stdout as one word compared against a literal, so putting a second return *kind* behind the same command substitution makes the name false in one of its two modes and gives every existing call site a second meaning to reason about.

### Assumption ledger

**Root:** the gate's four `git push` exemption checks read the whole `$COMMAND` (lines 119–147), so an exemption token anywhere in a chained command releases the gate for every fragment — including a `gh pr create`, `gh pr ready`, or real `git push` fragment that must be gated. The `--tags` check carries a second, independent fail-open: its argument extraction requires `git` and `push` to be textually adjacent.

**Givens** (fixed beyond this plan's reach):

- **G1.** `_lib_split_fragments`'s splitting grammar (`;`, `&&`, `||`, `|`, `$(`, backticks, with leading/trailing parens stripped) is the only fragment boundary available, and its known limits (no quote awareness, splits inside quoted strings) stay as they are — it is shared by seven hooks, so changing it is a decision outside this plan. `[verified: claude/.claude/hooks/_lib.sh:571-579; seven call sites via grep for _lib_split_fragments]`
- **G2.** The hook only runs for command shapes the `settings.json` prefix-glob dispatch matches; a shape the dispatcher misses never reaches this code at all. That wiring is owned by `settings.json`, not this file. `[verified: claude/.claude/hooks/require-ready-for-review.sh:55-60]`
- **G3.** `test_gh_pr_create_chained_after_dry_run_push_bypasses_known_gap` must flip to `"deny"` rather than be deleted — that flip is GH-773's definition of "fixed," inherited from the prior SDET review's decision. `[engineer-verified: GH-773 constraint relayed in this dispatch]`
- **G4.** `parse-git-command.py`'s copy of the value-consuming flag list stays duplicated rather than converging on `_lib.sh`. That duplication is a documented, named exception owned by `require-worktree-for-git-writes.sh`'s "Scope boundary" header, on the grounds that the two parsers serve independently-evolving purposes; unwinding it is a decision outside this plan. `[verified: claude/.claude/hooks/parse-git-command.py:69-84]`

**Mechanisms:**

1. Evaluate push exemption per fragment inside the existing detection loop; `is_git_push` becomes `is_gated_git_push`. — `anchors: root`. The loop already holds the one thing the old block lacked (which fragment matched `git push`), so the fix is to consume it rather than to re-derive scope from the raw string. `[verified: claude/.claude/hooks/require-ready-for-review.sh:94-117]`
2. The exemption logic lives in a file-local `push_fragment_publishes_reviewable_change`, not inline and not in `_lib.sh`. — `anchors: row1`. Two lighter primitives were checked before adding a function: inlining the four greps in the loop (fails — the loop body then carries two unrelated concerns and an unnamed 25-line policy block, which is exactly the comprehension signal CLAUDE.md's extract-functions rule names), and capturing the matched fragment for the existing post-loop block (fails — it tests only the last push fragment, leaving `git push origin feature && git push --dry-run` exempt). A file-local bare-named predicate matches `deny-pii-in-commits.sh`'s `commit_fragment_has_worktree_target`. `[verified: claude/.claude/hooks/deny-pii-in-commits.sh:142]`
3. The `--tags` arm's adjacency-dependent `sed` is replaced by a flag-aware word walk **in this PR**, rather than deferred to a follow-up issue. — `anchors: root`. Both the defect and the scoping bug live in the same four-check block and the same PR rewrites that block, so closing one and documenting the other would leave a second known-bad pin behind in the file this change exists to clean up. `[engineer-verified: decision relayed in this dispatch, overriding the prior draft's file-a-follow-up recommendation]` The underlying fail-open is real: `sed -nE 's/.*git[[:space:]]+push[[:space:]]+(.*)/\1/p'` emits nothing for `git -C /wt push --tags origin feature`, leaving `remaining` empty and firing the tag-only exemption despite the branch ref. `[verified: claude/.claude/hooks/require-ready-for-review.sh:139]`
4. The walk is factored once as `_lib.sh`'s `_lib_git_argv_from_subcmd`, with `_lib_extract_git_subcmd` refactored into a thin wrapper over it and `_lib_extract_git_subcmd_args` added alongside. — `anchors: row3`. Three lighter primitives were checked before touching a file seven hooks source: duplicating the walk in a hook-local helper (fails — two copies of the skip list and its state machine drift, which CLAUDE.md's single-source-of-truth rule calls a defect absent a listed exception), sharing only the flag list through a `_lib_git_global_flag_takes_arg` predicate (fails — `git -C /wt push` needs the `past_git` scan, `skip_next` state, and `-*` catch-all too, so the ten-line skeleton is still written twice), and a mode argument on `_lib_extract_git_subcmd` (fails — four hooks read its stdout as a single word compared to a literal, so a second return kind behind the same command substitution makes the name false in one mode). What moves to `_lib.sh` is mechanism, not policy: the push-exemption rules stay file-local per row 2. `[verified: claude/.claude/hooks/_lib.sh:547-569; four call sites at require-ready-for-review.sh:105, deny-reviewer-tree-mutation.sh:310, deny-pii-in-commits.sh:186, deny-private-project-refs.sh:251]`
5. `_lib_extract_git_subcmd` gains direct characterization tests in `test_lib.py` **before** its body is refactored, and those tests must be green against the unmodified `_lib.sh`. — `anchors: row4`. It has four hook call sites and zero direct test coverage today, so nothing currently pins the contract the refactor must preserve; `test_lib.py`'s own precedent is exactly this — `_lib_fragment_command_word` and its siblings were given direct tests when a second consumer appeared, because the first consumer's black-box tests "stopped being sufficient." `[verified: no matches for `extract_git_subcmd` under claude/.claude/hooks/tests/; claude/.claude/hooks/tests/test_lib.py:1051-1058,1091-1118]`
6. Regex bodies for `--dry-run` and `--delete`/`-d`, and the tags arm's flag/remote allowlist, are carried over unchanged; only the subject string changes. The colon-refspec check is not in this group — see row 7/9 — it gains the `--tags` arm's exhaustive-remaining-args computation and its command-substitution guard, not just a subject-string swap. — `anchors: row1`. Preserving the unchanged checks is what keeps every currently-tested single-fragment case (`git push --dry-run`, `git push origin :feature`, `git push origin --tags`, `git push --tags origin feature`) at its current verdict. `[verified: claude/.claude/hooks/tests/test_require_ready_for_review.py:124-176]`
7. Narrowing the subject cannot release a command that is denied today, except in the `--tags` and colon-refspec arms, where per-fragment scoping and the exhaustive-remaining-args checks each move the answer in one direction only (toward gating, never away from it). — `anchors: root, row3`. A fragment is a substring of `$COMMAND`, so the two globally-applicable checks (`--dry-run`, `--delete`/`-d`) can only lose matches; the tags arm's and colon-refspec arm's movements are enumerated with worked examples in "Directional guarantee," and every single-fragment movement (`git -C /wt push --tags origin feature`, `git push origin :old-branch new-feature:new-feature`) is allow-to-deny, never the reverse. `[verified: claude/.claude/hooks/_lib.sh:575-579 (fragments are substrings of the split input) and require-ready-for-review.sh:139 (the greedy `.*git[[:space:]]+push` capture)]`
8. The header's `Known gaps` paragraph is edited, not deleted: the whole-`$COMMAND` sentence goes, the default-branch-ordering and dispatch-glob gaps stay as an explicit list. — `anchors: root`. The paragraph describes current behavior rather than recording an event, so it is in-scope prose the fix must keep true. `[verified: claude/.claude/hooks/require-ready-for-review.sh:46-60]`
9. Test coverage flips the pinned case and adds the sibling shapes, including the global-flag tags shapes and the colon-refspec exhaustiveness case, in the same PR. — `anchors: G3, row1, row3`. `--dry-run` and `--delete`/`-d` are globally-applicable checks needing no further test beyond the existing pinned cases; `--tags` and colon-refspec share the identical exhaustive-remaining-args shape and GH-773 scopes them together, and this repo's convention is that a fixed behavior ships with the test that enforces it. `[verified: claude/.claude/hooks/tests/test_require_ready_for_review.py:599-620]`
10. No `docs/hooks.md` edit. — `anchors: row8`. Its entry for this hook is a one-line role summary that names the marker mechanics only, never the push-exemption shapes or the gap, so nothing there becomes false. `[verified: docs/hooks.md:19,131]`

## Critical files

Single `code-writer` dispatch. `_lib.sh`'s new helper is the direct input to the hook's predicate, and both test files pin the same walk semantics from opposite sides — splitting would force the walk's contract to be restated in every prompt, which `plan-it`'s dispatch-split rule names as the case not to split.

**`claude/.claude/hooks/_lib.sh`** (modify)
- Above line 547 (between `_lib_fragment_invokes_git` and the subcommand extractor): add `_lib_git_argv_from_subcmd`, holding today's walk with the loop continuing past the subcommand instead of breaking at it.
- Lines 547–569: replace `_lib_extract_git_subcmd`'s body with the first-line wrapper. Keep the trailing-non-alnum strip and the `printf '%s'` (no trailing newline) — four hooks compare its output to a literal.
- Below it: add `_lib_extract_git_subcmd_args`, the tail-lines wrapper.
- Do not touch: `_lib_split_fragments`, `_lib_fragment_invokes_git`, or any other helper in the file.

**`claude/.claude/hooks/require-ready-for-review.sh`** (modify)
- Header lines 35–44: add the one-line per-fragment note under the three push bypass bullets.
- Header lines 46–60: rewrite `Known gaps` as a two-item list, dropping the closed `--dry-run`/`$COMMAND` gap and the PR-relative framing.
- Above line 94: add `push_fragment_publishes_reviewable_change`.
- Lines 94–117: comment sentence, `is_git_push` → `is_gated_git_push`, predicate call in the push branch, renamed variable in the early exit.
- Lines 119–147: delete.
- Reuse, do not reimplement: `_lib_split_fragments`, `_lib_fragment_invokes_git`, `_lib_extract_git_subcmd` (already called at lines 101–105), the new `_lib_extract_git_subcmd_args`; `emit_deny`.

**`claude/.claude/hooks/tests/test_lib.py`** (modify)
- Add a `# --- _lib_git_argv_from_subcmd / _lib_extract_git_subcmd / _lib_extract_git_subcmd_args ---` section with a banner comment naming why direct coverage arrives now: a second consumer of the walk means the subcommand extractor's contract can no longer be inferred from its callers' black-box tests. Place it after the `_lib_fragment_command_word` / `_lib_fragment_invokes_tool` / `_lib_fragment_has_token` block, whose banner (lines 1051–1058) states the same rationale for the same reason.
- Add two thin `subprocess` wrappers modeled on `_fragment_command_word` (lines 1061–1068): `_extract_git_subcmd(fragment) -> str` and `_extract_git_subcmd_args(fragment) -> list[str]` (splitlines).
- `TestExtractGitSubcmd` — **characterization tests that must pass against the unmodified `_lib.sh`**: `git push` → `push`; `git -C /wt push` → `push`; `git -c user.name=x commit` → `commit`; `git --git-dir=/wt/.git push` → `push`; `git --git-dir /wt/.git push` → `push` (space-separated form, exercising `skip_next` where the `=`-attached case above exercises the `-*` catch-all instead); `git --work-tree /wt push` → `push`; `git --namespace ns push` → `push`; `git --super-prefix /pre push` → `push`; `git --config-env foo=BAR push` → `push` (the four value-consuming flags beyond `-C`/`-c`/`--git-dir`, otherwise untested by this suite); `git -c push.default=simple push origin feature` → `push` (a `-c` value containing the literal token `push`, pinning that the walk consumes exactly one word after `-c` rather than zero or two); `GIT_DIR=x git push` → `push`; `/usr/bin/git status` → `status`; `git push)` → `push` (the paren-group strip); `git --version` → `""`; `""` → `""`.
- `TestExtractGitSubcmdArgs` — new behavior: `git push --tags origin` → `["--tags", "origin"]`; `git -C /wt push --tags origin feature` → `["--tags", "origin", "feature"]`; `git -c user.name=x push origin feature` → `["origin", "feature"]`; `git -c push.default=simple push origin feature` → `["origin", "feature"]` (same lookalike-substring pin as above); `git --git-dir=/wt/.git push --tags origin feature` → `["--tags", "origin", "feature"]` (the `=`-attached form must not consume the following word); `git push` → `[]`; `git --version` → `[]`; `""` → `[]`.
- Reuse the module-level `_LIB_SH` constant and the existing `bash -c '. $_LIB_SH; <fn> "$1"'` argv shape; no new fixture is needed.

**`claude/.claude/hooks/tests/test_require_ready_for_review.py`** (modify)
- Rename `test_gh_pr_create_chained_after_dry_run_push_bypasses_known_gap` → `test_gh_pr_create_chained_after_dry_run_push_denies`, flip the assertion to `"deny"`, and replace the known-gap docstring with the current behavior: a `--dry-run` push exempts only its own fragment, so the chained `gh pr create` still gates (GH-773). Keep the `fake_gh_no_pr` fixture and its position next to `test_gh_pr_create_chained_after_push_denies`.
- Add, after it, `test_bypassable_push_shapes_chained_before_pr_create_deny` (`fake_gh_no_pr`), parametrized over `"git push origin --delete feature && gh pr create"`, `"git push origin -d feature && gh pr create"`, `"git push origin :feature && gh pr create"`, `"git push origin --tags && gh pr create"` — all `"deny"`.
- Add `test_bypassable_push_does_not_exempt_a_chained_gated_fragment` (`fake_gh_pr_exists`), parametrized over `"git push --dry-run && git push origin feature"`, `"git push origin feature && git push --tags origin"`, `"git push origin --delete feature && git push origin feature"`, `"git push --dry-run && gh pr ready"` — all `"deny"`. The second case is the greedy-`sed` shape from "Directional guarantee"; the fourth covers the `gh pr ready` arm, which inherits the same fix.
- Add `test_bypass_token_outside_the_push_fragment_does_not_exempt` (`fake_gh_pr_exists`), parametrized over `"echo --dry-run && git push origin feature"` and `"echo :note && git push origin feature"` — both `"deny"`. These pin the core claim directly: an exemption-shaped token in a non-push fragment no longer releases the gate.
- In the `-- Bypass shapes` block, immediately after `test_tags_with_branch_still_gated` (line 164), add `test_tags_with_branch_behind_a_git_global_flag_still_gated` (`fake_gh_pr_exists`), parametrized over `"git -C /wt push --tags origin feature"`, `"git -c user.name=x push --tags origin feature"`, `"git --git-dir=/wt/.git push --tags origin feature"` — all `"deny"`. This is the fail-open mechanism 3 closes: a global flag between `git` and `push` defeated the old `sed`, so the tag-only exemption fired despite the branch ref. The three cases cover the value-consuming flag arm and the `=`-attached form separately.
- Add, beside it, `test_tags_only_push_behind_a_git_global_flag_allowed` (`fake_gh_pr_exists`), parametrized over `"git -C /wt push --tags origin"`, `"git -c user.name=x push --tags origin"`, and `"git --git-dir=/wt/.git push --tags origin"` — all `"allow"`. The don't-over-gate counterpart: the walk must still recognize a genuinely tag-only push once the global flag is consumed; the `-c` case pairs with the deny-side `-c` case above so both directions of that flag are covered.
- Add `test_all_push_fragments_bypassable_still_allowed` (`fake_gh_pr_exists`) in the same block: `"git push --dry-run && git push origin :feature"` → `"allow"`.
- Add `test_colon_refspec_with_real_branch_ref_still_gated` (`fake_gh_pr_exists`): `"git push origin :old-branch new-feature:new-feature"` → `"deny"`. This is the colon-refspec arm's own exhaustive-remaining-args gap: git's documented rename-on-remote idiom pairs a deletion refspec with a real one in a single fragment, and the old presence-only check exempted the whole fragment on the deletion refspec alone.
- Add, beside it, `test_colon_refspec_multiple_deletes_allowed` (`fake_gh_pr_exists`): `"git push origin :old-branch :another-old-branch"` → `"allow"`.
- Add `test_colon_refspec_with_command_substitution_still_gated` (`fake_gh_pr_exists`), parametrized over `"git push origin :old-branch $(echo new-feature:new-feature)"` and the backtick form — both `"deny"`. The colon-refspec arm's own version of the `--tags` arm's command-substitution guard, for the identical reason.
- Add `test_colon_refspec_and_tags_in_one_fragment_gated` (`fake_gh_pr_exists`): `"git push origin :feature --tags"` → `"deny"`. **Allow today, deny after**: the old presence-only colon-refspec check exits on `:feature` before the `--tags` block is ever reached; the exhaustiveness fix leaves both arms' allowlists unable to recognize the other's token, so neither bypass fires.
- Reuse the existing `run_hook` / `bash_input` helpers and the `isolated_home`, `repo_on_feature_branch`, `fake_gh_pr_exists`, `fake_gh_no_pr` fixtures; no new fixture is needed. `fake_gh_pr_exists` is required wherever `gh pr create` is *not* in the command, because the PR-existence check runs there and fails open when `gh` reports nothing.
- Leave `test_destructive_or_dry_push_shapes_allowed`, `test_tags_only_push_allowed`, `test_tags_with_branch_still_gated`, `test_gh_pr_create_chained_after_push_denies`, and the `test_gh_pr_create_wrapper_shapes_denied` block untouched — they are the preservation evidence that single-fragment semantics did not move.

## Verification

1. Targeted loop while implementing: `.venv/bin/pytest claude/.claude/hooks/tests/test_lib.py claude/.claude/hooks/tests/test_require_ready_for_review.py -q`
2. Scoped gate before commit: `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — for this diff its rule table selects `claude/.claude/hooks/tests` (domain rule for anything under `claude/.claude/hooks`, which covers `_lib.sh` and both hook test files), `claude/.claude/scripts/tests` (the `_is_hooks_dir_shell_script_change` exception, for `test_no_bash4_constructs.py`), and the transcript-analysis test glob (`_is_hooks_or_skills_change`); the plan file under `.claude/plans/` maps to no targets. Adding `_lib.sh` widens nothing — it matches the same three rules the hook script already did. Per this repo's CLAUDE.md, agents run this rather than the full suite.
3. `_lib_extract_git_subcmd`'s other three consumers (`deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, `deny-reviewer-tree-mutation.sh`) are covered by step 2, since their test files sit in the same `claude/.claude/hooks/tests` target. Their results are the refactor's regression evidence — a failure there means the wrapper changed the extractor's contract, not that the test drifted.
4. Lint: `.venv/bin/ruff check claude/.claude/` for the two test files, and `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` for `_lib.sh` and the hook. Step 2 also runs the repo's own ShellCheck test, so this is a fast double-check rather than the only coverage.
5. Ordering checks, run rather than assumed — a test whose result does not move across the change proves nothing about the thing it is named for:
   - Must **fail** against the unmodified tree and pass after: `test_gh_pr_create_chained_after_dry_run_push_denies`, `test_bypass_token_outside_the_push_fragment_does_not_exempt`, `test_tags_with_branch_behind_a_git_global_flag_still_gated`, `test_colon_refspec_with_real_branch_ref_still_gated`, `test_colon_refspec_with_command_substitution_still_gated`, and `test_colon_refspec_and_tags_in_one_fragment_gated`.
   - Must **pass** against the unmodified tree and still pass after: every `TestExtractGitSubcmd` characterization case. These exist to catch a contract change in the refactor, so a red one before the refactor means the test is wrong, not `_lib.sh`.
6. `/code-review` before the commit, per the repo's hook-enforced pipeline.

## Out of scope

- **`_lib_split_fragments`.** `_lib.sh` is edited, but only the git-argv walk: the shared fragment splitter and its known limits (no quote awareness, splitting on any literal `|`) are untouched. Seven hooks depend on it, and G1 puts that decision outside this plan.
- **`parse-git-command.py`'s duplicate flag list.** It stays duplicated rather than converging on the new `_lib.sh` helper — that duplication is a named, documented exception owned by `require-worktree-for-git-writes.sh`'s "Scope boundary" header, not an oversight this change should tidy up (G4).
- **The two remaining header gaps.** The default-branch bypass running ahead of any command-type check, and the `settings.json` prefix-glob dispatch versus the in-script fragment detection, stay documented and unfixed.
- **The tags arm's remote allowlist** (`origin|upstream` only, so `git push --tags fork` gates). Pre-existing, and it errs toward gating for a remote name outside that allowlist.
- **A repeated `origin`/`upstream` token in a refspec position.** This is a separate case from the tags-arm allowlist above; `push_fragment_args_after_repo`'s position-aware exclusion handles it correctly in both directions.
- **`is_gh_pr_ready` / `is_gh_pr_create` detection.** The `echo gh pr create` false-positive is deliberately pinned as fail-closed by `test_gh_pr_create_echo_false_positive_denies`; leave it.
- **Whole-file `[ ]` → `[[ ]]` conversion.** New and edited conditionals use `[[ ]]`; converting the other ~15 tests in the hook, or `_lib.sh`'s, would be churn unrelated to the bug.
- **`docs/hooks.md`, `settings.json`, and `claude/.claude/skills/ready-for-review/SKILL.md`.** Nothing in them states the push-exemption shapes, so nothing in them becomes false.
