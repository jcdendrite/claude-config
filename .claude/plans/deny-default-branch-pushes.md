# Deny agent pushes to the default branch

## Context

Goal: a `PreToolUse` Bash gate hard-denies agent pushes whose destination is a protected branch, and the autonomous-shipping Stop hook stops forcing a continuation on the default branch or a detached HEAD.

Ask: GitHub issue 1407 ("Deny agent pushes to the default branch and name the gates in the autonomous-shipping statement"), read with `gh issue view 1407`. Its Proposal sections 1 and 2 and its acceptance criteria are the scope. Section 3 adds no statement sentence, and the optional "this waives no hook gate" clause is not added because the issue conditions it on a transcript that shows the misreading and cites none. The issue records the engineer's acceptance of the Known-gaps list: "Yes #1 gaps are accepted and I think they were pre-existing. They assume a non-cooperative agent threat model."

Why now: autonomous shipping tells agents to push without asking, so the engineer's permission prompt no longer backstops each push. The only barrier to a default-branch push is one prose clause in `_lib_autonomous_shipping_statement`. `require-ready-for-review.sh` exits 0 on the default branch and never reads a refspec destination, so `git push origin feature:main` lands on `main`.

Intended outcome: every Covered shape in the issue is denied with an allow control beside it, the Stop hook stays silent on the default branch and detached HEAD, and the docs, README, changelog and test selection reflect the new gate.

## Approach

A new always-on `PreToolUse` Bash gate, `deny-default-branch-push.sh`, parses every push-family command and every mutating `gh api` call to a ref-moving endpoint. It denies the ones whose destination is a protected branch of the target remote or cannot be resolved. It judges arguments from quote-masked text only, the issue's literal prescription, so a quoted mention of a push stays inert. A push inside a quoted span that bash executes is an accepted Known gap (rows 9 and 33). The pushes that submodule recursion adds are not judged, a Known gap the engineer accepted as permanent debt (row 31). Wrapped, scripted and HTTP pushes pass, and server-side branch protection is the barrier against them (rows 2, 33, 56 and 74). A new `_lib.sh` parser, `_lib_git_push_targets`, carries `git push`'s grammar and is the contract #1409 consumes. The parser is frozen at the issue's Covered list: any shape found after this plan becomes a header Known gap, not a new parser rule (rows 53 and 54). A correction that makes a Covered shape deny stays inside the freeze (rows 66 and 67). The Stop hook gains one firing condition, HEAD on a non-default branch, through `_lib_head_detached_or_default`, which is extracted from `_lib_review_ledger_path`'s inline test.

**Gate flow.** The hook copies `block-gh-pr-merge.sh`'s boilerplate: `set -uo pipefail`, `DENY_GATE_LABEL="default-branch-push"`, the bootstrap `emit_deny`, `_lib_parse_tool_input_or_deny`, the Bash-only filter and the empty-command exit. `CWD` falls back to `$PWD`. Then:
1. Delete every backslash-newline pair, as the shell does (row 19). Quote-strip the result once with `_lib_strip_shell_quotes`, failing closed on a nonzero status.
2. Split into fragments. Replace each `$(` and each backtick in place with a hook-private cut marker followed by a newline, then call `_lib_split_fragments` and fail closed on its status (row 6). No space goes before the marker, so it stays part of the word the opener touched: `repos/$(…)` becomes the one word `repos/<cut>`, and `body="$(…)"` stays the one flag value `body=<cut>`. A fragment that a substitution cut short ends in the cut marker (row 11).
3. Prefilter over the stripped fragments in pure bash. Exit 0 unless some fragment is a push-family invocation, an alias candidate or a `gh api` call (row 21). An alias candidate is a `git` subcommand that is neither push-family nor in `_LIB_GIT_BUILTIN_SUBCOMMANDS` (row 16).
4. Masked pass, the only pass that judges a push: run `_lib_mask_shell_quotes` on step 1's joined text, failing closed. Split it as in step 2 and judge every fragment in order. Quoted text is inert here, so a commit message or PR body that mentions a push is not a push (row 9).
5. Run the `gh api` check below over every stripped fragment, then exit 0.

**Fragment judge.** The first matching rule decides:
- A `cd`, `pushd` or `popd` word, a `git checkout` or `git switch` fragment, or a `gh pr checkout` fragment sets a moved flag for the rest of the pass. No other fragment sets it, so an earlier fragment that changes the branch, config or remote HEAD the gate reads is a Known gap accepted as permanent debt (rows 55 and 74).
- An alias candidate is looked up with `git <target> config --get alias.<name>` (row 16):
  - A value starting with `!` denies as unresolvable.
  - Any other value replaces the alias word, and the rebuilt fragment is judged again from the first rule (row 37). A rebuilt fragment that is again an alias candidate denies as unresolvable, so each fragment gets one lookup (row 46).
  - No value passes.
- A push-family fragment ending in the cut marker denies as unresolvable. Otherwise `_lib_git_push_targets` parses it.
- A `dry-run` record passes the fragment.
- Any `unresolvable` record denies.
- A `no-refspec` record denies when the moved flag is set. So does a `ref` whose destination is `HEAD` or `@`.
- The target is `git -C <abs>` for forms `cwd` and `C`, and `git --git-dir=<abs>` for form `git-dir`. A relative path resolves against `CWD`.
- A `tags` record with no `ref` record passes, because only tags move (row 32).
- Each `ref` is judged by its destination, and any denying `ref` denies the fragment:
  - A destination in a `refs/` namespace other than `refs/heads/`, such as a tag or `refs/for/main`, passes.
  - `HEAD` or `@` resolves to the target's current branch through `symbolic-ref -q --short HEAD`. A detached HEAD denies as unresolvable.
  - A destination in `_lib_push_protected_branches`' output for the target and the command's remote denies. That output is computed once per target and remote in a fragment, and every `ref` reuses it.
- A `no-refspec` push denies when a `config` record names a `push.` or `remote.` key, or `*` (row 51). Otherwise one `git config --get-regexp` call reads `push.default` and every `remote.*.push` from the target (row 17), and the push denies when `push.default` is `matching` or any `remote.*.push` is set.
- Otherwise a `no-refspec` push resolves `git rev-parse --symbolic-full-name @{push}` (row 15). Its remote is the longest `refs/remotes/<remote>/` prefix among `git remote`'s names. The rest is the branch, judged against that remote's protected set. When `@{push}` errors, the current branch name is judged against the command's remote word, else `origin`.
- A git call that fails with a status other than its documented ones, a cap kill included, denies and names the call.

**`gh api`.** The check applies to a stripped fragment whose command word is `gh`, as `_lib_fragment_invokes_tool <fragment> gh` reads it (row 69), and whose `_lib_tool_argv_from_subcmd <fragment> gh` words start with `api`. It denies when `_lib_gh_api_call_is_mutating` holds for the whole stripped command and either of these holds:
- A word after the fragment's first `api` word, flags and their values included, matches `(^|/)repos/[^/]+/[^/]+/(contents|git/refs|merges)([/?]|$)`. So a boolean flag such as `--paginate` or `-i` cannot hide a literal endpoint (row 61).
- The endpoint word holds `$` or the cut marker anywhere. The endpoint word is the first word `_lib_tool_argv_from_subcmd` emits after `api`. The cut marker stands in for each `$(` and backtick, as step 2 places it (row 68).

Endpoints come from stripped text, because a quoted endpoint holding `{owner}` or `$` masks to `""` (row 13). The header's over-denial list names what this costs. `gh api graphql` stays a known gap, and so does the PR merge endpoint, which the engineer accepted as permanent debt (rows 56 and 74).

**Parser contract: `_lib_git_push_targets FRAGMENT`.** #1409 consumes this contract. FRAGMENT is one fragment of quote-masked or quote-stripped text, and the function forks nothing. It exits 1 with no output when FRAGMENT is none of these: `git push`, `git subtree push`, `git send-pack`, or the dashed `git-push`, `git-send-pack` and `git-subtree push` commands. Otherwise it exits 0 and prints tab-separated records, one per line, with `ref` records in refspec order:
- `repo` `<form>` `<path>`, with one of three forms:
  - `cwd`, with an empty path.
  - `C`, the `-C` values composed in order. A later absolute value restarts the path.
  - `git-dir`, from `--git-dir` or a `GIT_DIR=` prefix, composed onto any `-C` path. It is its own form because a git-dir names the git directory, not the work tree.

  A leading `~/` reads as `$HOME/`.
- `config` `<key>`, once per `-c` or `--config-env` key, with its section and variable name lowercased as git canonicalizes them (row 17). A `GIT_CONFIG_PARAMETERS=` or `GIT_CONFIG_COUNT=` prefix gives the key `*`.
- `remote` `<word>`: the first non-flag word, else the `--repo` value. It is absent when neither is given.
- `ref` `<source>` `<destination>`, one per refspec:
  - A leading `+` is stripped.
  - The source is the text before the first `:`.
  - `--delete`, `-d` and `:<dst>` give an empty source.
  - The destination is the text after the first `:`, or the whole word when there is none.
  - The destination then drops one leading `refs/heads/`, or else one leading `heads/`, because git resolves a remote `heads/<x>` to `refs/heads/<x>` (row 60). Only one prefix goes, so `refs/heads/heads/main` gives `heads/main`.
  - `tag <name>` gives `refs/tags/<name>` for both.
- `no-refspec` when no refspec word is given, whatever the flags.
- `tags` for `--tags`. `--follow-tags` adds no record, because it still pushes the default refspec (row 32).
- `dry-run` for `--dry-run` or `-n`.
- `unresolvable` `<reason>`, one record per cause:
  - `all` for `--all` or `--branches`, and `mirror` for `--mirror`.
  - `glob` for a refspec holding `*`, and `matching` for `:` or `+:`.
  - `prune` for `--prune`.
  - `stdin` for send-pack's `--stdin`, and `no-refs` for send-pack with no ref.
  - `remote` or `refspec` when that word is a masked span (it holds `"` or `'`) or holds `$` or a backtick.
  - `repo-path` for the same shapes in a `-C`, `--git-dir` or `GIT_DIR` value, or a leading `~` other than `~/`.
  - `appended-args` when a word before the command word is `xargs`, `parallel`, `-exec`, `-execdir`, `-ok` or `-okdir`, or a path ending in `/xargs` or `/parallel` (rows 35 and 36).

Word rules:
- `-o`/`--push-option`, `--receive-pack`/`--exec`, `--repo` and `--recurse-submodules` take a separate value. `--recurse-submodules` is parse-only and adds no record (row 43).
- A value-taking flag that ends the fragment takes no value.
- A short bundle is read per letter. `d` sets delete mode, `n` sets dry-run, and `o` takes the rest of the bundle or else the next word.
- Words after `--` are positional.
- The global-option walk reads `_LIB_GIT_GLOBAL_VALUE_FLAGS`. That constant replaces the value-taking flag list `_lib_git_argv_from_subcmd` hardcodes today, so the stowed `_lib.sh` keeps one list. The skill-management plugin's copy of that function keeps its own, an accepted second home (row 20).
- Subtree mode takes values for `-P`/`--prefix`, `-m`/`--message`, `-b`/`--branch`, `--onto` and `--annotate`. The repository and refspec follow `push`.

**Protected set: `_lib_push_protected_branches FORM PATH REMOTE`.** It prints one name per line:
- the target of `refs/remotes/<REMOTE>/HEAD`, when REMOTE is a plain remote name and that symbolic ref resolves;
- `main` and `master`, always;
- `develop`, when the remote's HEAD does not resolve (row 14).

A URL, a path or an empty REMOTE gets no lookup. It exits 0, or 2 with no output when the symbolic-ref read fails with a status other than 0 or 1. It interpolates the remote name, so it never spells the literal `refs/remotes/origin/HEAD` (row 27).

**Mutating `gh api`: `_lib_gh_api_call_is_mutating TEXT`.** The two regexes move verbatim from `deny-private-project-refs.sh:442-446`. It exits 0 when either matches TEXT, and also when `grep` fails, the safe direction for both callers (row 12). `deny-private-project-refs.sh` keeps passing its whole `COMMAND_UNQUOTED`.

**Deny messages.** Each opens with the first 200 characters of the command, as `block-gh-pr-merge.sh:77` does, and names its reason:
- A protected destination names the branch and the remote. It tells the agent to push a feature branch and open a PR, or to ask the engineer to run the push with the `!` prefix.
- Every other push or alias deny names its cause: an `unresolvable` reason, a cut marker, the moved flag, or an alias the gate does not expand. It asks for one literal push as its own Bash call, such as `git push -u origin HEAD` from a feature branch (row 44).
- A moved-flag deny also offers `git -C <dir> push …` in place of `cd <dir> && git push …`.
- A `gh api` deny names its cause: a ref-moving endpoint, or an endpoint word the gate cannot read. It gives the protected-destination alternatives.
- A fail-closed deny names the failed step and its status.

**Hook header.** Lines 2-3 are `# hook-class: gate` and `# tier-threat-model: cooperative, untrusted-input, irreversible`. It carries the one-line sentence the tier row quotes: "Text from a PR or a tool result can steer an agent into this push, so steered input is in scope." It states that a default branch other than `main` or `master` is protected only where its remote's HEAD is set locally, so a push to a URL or path remote protects `main` and `master` only (row 62). Its Known gaps list the six that GH-1407's body accepts (row 2):
- a push from a script file the agent writes;
- an alias defined in the same command;
- `gh api graphql`;
- `gh repo sync` of a fork;
- encoding tricks, with a note naming `gh api`'s bundled `-iXPUT` and attached `-fmessage=m` spellings, which `_lib_gh_api_call_is_mutating` does not read as mutating (row 70);
- the bare `&`.

The Known gaps also carry one class-level gap, accepted as permanent debt (row 33), in this wording: "A push whose text sits inside a quoted span that bash or another program executes: a double-quoted `$(…)` or backtick span, or a shell wrapper's string argument (`bash -c "…"`, `sh -c '…'`, `eval "…"`, `xargs … sh -c '…'`). Quote masking keeps a quoted mention of a push inert and cannot tell these executed spans apart from mentions." The commit message and PR body state its acceptance rationale, as `docs/hooks.md` requires for a later-found gap a cooperative agent can emit (row 34).

The Known gaps also list submodule recursion, in this wording: "A push that also pushes submodules, through `--recurse-submodules`, `push.recurseSubmodules` or `submodule.recurse`: only the superproject's refspecs are judged." The engineer accepted it as permanent debt (row 31). The commit message and PR body state its acceptance rationale, as for the class-level gap (row 45).

The Known gaps also list four later-found gaps the engineer accepted as permanent debt. Row 57 pins the fourth with a test. The commit message and PR body state all four's rationale (rows 59 and 65).
- Beside the same-command alias gap, accepted by rows 55 and 74: "A fragment earlier in the same command that changes the branch, config or remote HEAD the gate reads, such as `git config remote.origin.push HEAD:main && git push`, `git branch -m` or `git remote set-head`."
- Accepted by rows 56 and 74: "A push over HTTP, such as `curl` with a token against the contents API."
- Accepted by rows 56 and 74: "`gh api repos/<owner>/<repo>/pulls/<n>/merge`, tracked as GH-1077."
- Accepted by rows 30 and 64: "On BSD/macOS awk, a blank line inside a quoted span, such as a multi-paragraph commit message, can hide a push chained later in the same command. This is permanent debt, tracked in `_lib_mask_shell_quotes`' Limits." The entry names no issue (row 65).

The Known gaps also list one more later-found gap, in this wording: "A `gh api` endpoint written after a boolean flag such as `--paginate`, `-i` or `--silent`, after a quoted multi-word flag value, or after a flag value holding a command substitution. The first two make the endpoint read take another word as the endpoint, so a computed endpoint after them passes, such as `gh api -X PUT -f message="add readme" $URL`. The third cuts the `gh api` fragment at the substitution, so a computed or literal ref-moving endpoint after it passes, such as `gh api -X PUT -f content="$(base64 …)" repos/o/r/contents/f`." The engineer accepted the boolean-flag shape as permanent debt (rows 72 and 74), and chose to widen the entry's wording with no new rule (row 76). The engineer then accepted the quoted multi-word value and the substitution-holding value as permanent debt (row 78). The commit message and PR body state the gap's rationale (rows 73 and 77).

The header also points to `_lib_mask_shell_quotes`' Limits for the other residuals it inherits (row 30). It names the accepted over-denials:
- an unquoted heredoc body line that reads as a protected push;
- a backtick-wrapped push;
- a computed `-C` path;
- a refspec-less push, or a push whose destination is `HEAD` or `@`, chained after any moved-flag fragment, such as `cd <worktree> && git push -u origin HEAD`;
- a quoted refspec, `-C` path or `--git-dir` path holding a character outside `[A-Za-z0-9._/-]`, such as `git push origin "HEAD:feature"` or `git -C "<dir with space>" push`, which masks to `""`;
- an alias whose value starts with another alias, a git extension, or a builtin `_LIB_GIT_BUILTIN_SUBCOMMANDS` omits;
- a mutating `gh api` call to a ref-moving endpoint, quoted as inline code or at the start of a quoted line (row 69);
- a mutating `gh api` call with any word, a flag value or field text included, that names a ref-moving endpoint path (row 61);
- a mutating `gh api` call with a boolean flag such as `--paginate`, or a quoted multi-word flag value, before its endpoint, when the first later word gh's flag grammar leaves positional holds `$`, `$(` or a backtick. An example is `gh api -X POST -f body="hello $USER" repos/o/r/issues/1/comments`, where the value's tail `$USER` becomes the endpoint word (row 68).

**Stop hook.** `_lib_head_detached_or_default REPO_ROOT` reads HEAD once with `symbolic-ref -q --short`. It prints the branch, empty when detached, and exits with one of three statuses:
- 0 when HEAD is detached or on the default branch: `_lib_default_branch_or_guess`'s answer, else a name in `_LIB_DEFAULT_BRANCH_FALLBACK_NAMES`;
- 1 on any other branch;
- 2 when git could not read HEAD.

`_lib_review_ledger_path` maps 0, 1 and 2 to session scope, branch scope and exit 1, which is its behavior today. `advance-past-commit-stall.sh` calls the helper right after `_commit_stall_work_pending` and exits 0 unless the status is 1, so a git failure stays silent (row 22). The announce and compact hooks are untouched.

**Beyond the issue's literal list.** Each addition is the sibling of a Covered bullet, and the engineer kept it by selection: the `xargs` family by row 35, and the rest by rows 38-40, whose bundles row 41 lists. Submodule recursion is not judged, an accepted Known gap (row 31).

**Alternatives set aside.**
- A git `pre-push` hook injected through `core.hooksPath`, which receives the refs git resolved (rows 53 and 54). `docs/design-decisions/git-native-commit-hooks-declined.md` declined this carrier on 2026-10-03. Its third reconsideration trigger, config-defined hooks, does not hold, because `hook.<name>.command` was ignored on git 2.50.1. The staff-backend-engineer review measured these costs on Apple git 2.50.1, in `agent-reviews/staff-backend-engineer-1794030998-GH-1407-deny-default.md` R1-R4:
  - Consumer `pre-commit`, `commit-msg` and `post-checkout` hooks stopped running. Stubbing every githooks(5) event added about 110 ms per git write.
  - A missing, empty or non-executable hook directory let `HEAD:main` through with no error. This gate's `onFailure: block` fails closed instead.
  - `--no-verify` and its unambiguous abbreviations, `-c core.hooksPath=/dev/null`, a clobbered `GIT_CONFIG_COUNT`, and `git send-pack` each bypassed it.
  - It denied `git push --dry-run origin HEAD:main`, which the issue lists as an allow control.
  - It sees no `gh api` call.
- Extending `require-ready-for-review.sh`: the issue leaves it untouched, it is a marker gate rather than a hard deny, and #1409 owns its fix.
- Stripped text only, the readiness gate's approach: a PR body or issue comment quoting `git push origin main` would hard-deny. In a main checkout, a quoted bare `git push` would resolve to `main` and deny too.
- A second, stripped pass over substitution-opening fragments, and over every fragment when an execution-wrapper token appears: it denies the canonical commit form (row 10). The executed spans it would reach are an accepted Known gap instead (row 33).
- A deny on any unquoted `$` or backtick in the command: it denies `git push -u origin HEAD && echo $?`, and the issue rules out failing closed on the whole command.
- A mode argument on `_lib_split_fragments`: it edits a function every gate calls. Inserting newlines before the call needs no change there (row 6).
- `_lib_default_branch_or_guess` for the protected set: it returns the first match, reads `origin` only, and its text is pinned to the plugin copy (row 5, G3).
- A complete git builtin list: the only correctness requirement is that each listed name is a builtin. A miss costs one config lookup, or an over-deny when an alias's value names the missed builtin (rows 16 and 50).
- The Stop condition inside `_lib_autonomous_shipping_root_for_cwd`: it would break the three-hook eligibility agreement the grant-boundary test pins (issue Proposal §2).

### Assumption ledger

**Root:** an agent told to push without asking can push to a protected branch. No hook reads a push's destination, `require-ready-for-review.sh` exits 0 on the default branch, and the Stop hook forces a continuation on the default branch and on a detached HEAD. `[verified: require-ready-for-review.sh:369-379; advance-past-commit-stall.sh:219-231]`

**Givens:**
- G1. What `git push` updates is git's behavior, including `push.default`, `@{push}`, `remote.<name>.push` and alias expansion. Reason: the vendor defines it, and the gate only models it.
- G2. A hook sees only `tool_input.command`. It sees neither what a script file runs nor what the engineer runs with the `!` prefix. Reason: the harness defines the hook payload. `[unverified]`: GH-1407 Decision 1 cites `docs/config-file.md`, not re-read here.
- G3. `_lib_default_branch_from_origin_head` and `_lib_default_branch_or_guess` stay text-identical to the skill-management plugin's copy. Reason: the plugin ships its own `_lib.sh`, and loosening that pin needs a decision outside this plan. `[verified: claude/.claude/hooks/tests/test_lib_skill_management_plugin_lib.py:98-108]`

**Rows:**
1. The registration (PreToolUse Bash group, no `if`, `onFailure: block`), the header's two lines, the deny message's content, and every Covered shape with an allow control beside it come from the issue. `[verified: GH-1407 Proposal §1 and acceptance criteria]` `anchors: root`
2. The issue's six Known gaps are accepted debt. `[verified: GH-1407 issue body, the Acceptance of the Known gaps paragraph]` That acceptance covers the issue's listed gaps only, not this plan's additions. Rows 31, 33, 64, 74 and 78 accept those as permanent debt.
3. The hook name `deny-default-branch-push.sh`, and leaving out the optional "this waives no hook gate" clause, are the dispatching session's defaults, not the engineer's. `[unverified]`
4. The readiness gate's push classifiers are not reusable: they exempt every deletion, and only `origin` or `upstream` fills the repository slot. `[verified: require-ready-for-review.sh:233-307]`
5. `_lib_default_branch_or_guess` returns the first candidate and reads `origin` only. `[verified: _lib.sh:1418-1432]`
6. `_lib_split_fragments` splits on `;`, `&&`, `||`, `|`, `$(` and backtick with line-based `sed`, never on a bare `&`. A newline inserted before the call therefore becomes a fragment boundary. `[verified: _lib.sh:2243-2261]` `anchors: root`
7. `_lib_mask_shell_quotes` has these properties: `[verified: _lib.sh:4040-4072, :4103]`
   - It reduces a multi-word quoted span to `""`, and emits a single safe word unquoted.
   - It leaves an unclosed quote unmasked, and does not model backslash escapes.
   - On BSD/macOS awk it can invert quote parity after a blank line.
8. Every in-repo push recipe parses to an allowed result: `[verified: git-feature-branch-sync/SKILL.md:82, :91-93, :145; ready-for-review/SKILL.md:150; plan-push-preflight.py:460-471]`
   - The sync recipe's `--force-with-lease="…$(…)"` masks to a flag word.
   - The `/ready-for-review` push is `git push -u origin <branch>`.
   - `plan-push-preflight.py`'s command names `<sha>:refs/heads/<branch>` with `--no-recurse-submodules`.

   `cleanup-merged-branches.sh:968` pushes from inside a script, which the hook never sees.
9. Arguments are judged from masked text only, so a quoted mention of a push is inert. `[verified: GH-1407 Covered list]` `anchors: row7`
10. A second pass over quote-stripped text would deny the canonical commit form, so the plan has none. `[verified: _lib.sh:4030-4036; _lib.sh:2259; deny-invisible-commit-content.sh:280]` `anchors: row9`
    - `_lib_strip_shell_quotes` deletes every quote character, so a `<<'EOF'` body inside `-m "$(…)"` loses its quoting.
    - `_lib_split_fragments` then cuts the body's inline-code backticks into a fragment that parses as a push.
    - The wrapper-token regex matches a bare `eval` word anywhere, which would put every body line under judgment.
11. The cut marker keeps a substitution from shortening a push fragment silently. Without it, the unquoted `git push origin $(git branch --show-current)` splits to `git push origin`, which parses as refspec-less and passes on `feature`. `[verified: _lib.sh:2259 splits on a substitution opener and on a backtick]` `anchors: row6`
12. The mutating `gh api` check scans the whole command and leans toward a scan. Today a `grep` error falls through to "not mutating". Counting it as mutating is new, and it moves both callers toward their safe direction. `[verified: deny-private-project-refs.sh:429-446]` `anchors: root`
13. A quoted endpoint holding `{owner}` or `$` masks to `""`, so endpoints are read from stripped text. The cost is an over-deny of a mutating `gh api` call to a ref-moving endpoint quoted as inline code or at the start of a quoted line, which the header lists. `[verified: _lib.sh:4103; _lib.sh:4030-4036]` `anchors: row7`
14. The issue's "plus `develop` when nothing resolves" is read as "when the target remote's HEAD does not resolve". That is the wider of the two readings. `[unverified]`: the architect's reading.
15. A refspec-less push resolves through `@{push}`, with a current-branch fallback. `[verified: GH-1407 Covered list]` `anchors: root`
    - `git rev-parse --symbolic-full-name @{push}` prints `refs/remotes/<remote>/<branch>`. `[verified: gitrevisions(7), the @{push} entry, Git 2.50.1 man page]`
    - Taking the longest configured remote name as the prefix is correct whether or not a remote name may hold `/`. `[unverified]`: whether git accepts a `/` in a remote name; the prefix rule does not depend on the answer.
16. Git ignores an alias that hides an existing command, so the builtin list needs to hold only true builtins. `[verified: git-config(1), the alias.* entry, Git 2.50.1 man page: "aliases that hide existing Git commands are ignored"]`
    - The list is `_LIB_GIT_BUILTIN_SUBCOMMANDS` in `_lib.sh`, beside `_LIB_GIT_GLOBAL_VALUE_FLAGS`, so its test sources it instead of scraping the hook's text. `[verified: claude/.claude/rules/shell-script-conventions.md bars regex-extracting a script's source at test time; .claude/rules/bash-unit-test-seams.md routes a hook-side extraction into _lib.sh]`
    - It is authored from git 2.43's `git --list-cmds=builtins`, because CI installs git 2.43.0. `[verified: .github/workflows/tests.yml:135]` A name added as a builtin after 2.43 would fail CI on a valid list.
    - Its test requires every listed name in the running git's `--list-cmds=builtins`, which git(1) calls "an internal/experimental option". `[verified: git(1), the --list-cmds entry]`
17. `git config --get-regexp` matches a canonical key name, with section and variable names lowercased and subsection names kept. `[verified: git-config(1), Git 2.50.1 man page: the --regexp entry, which the --get-regexp entry names as its replacement]`
18. The `-c push.*` rule applies to refspec-less pushes only, so `plan-push-preflight.py`'s `-c push.pushOption=` push stays allowed. `[verified: GH-1407 Covered list; plan-push-preflight.py:463-464]`
19. Deleting a backslash-newline pair matches the shell, and 2 of 3 in-repo joins do the same. `[verified: deny-reviewer-tree-mutation.sh:536; enforce-marker-script-shape.sh:823; enforce-marker-script-shape.sh:725 joins with a space]`
20. `_lib_git_argv_from_subcmd` hardcodes the value-taking global flags, so the parser shares one constant with it, and the stowed `_lib.sh` keeps one list. `[verified: _lib.sh:2015-2019]` `anchors: root`
    - The skill-management plugin's `_lib.sh` carries its own copy of the function and its list, and no parity test pins it. `[verified: plugins/skill-management/hooks/_lib.sh:702-725; staff-platform-engineer-1794030998-GH-1407-deny-default.md R4]`
    - The plan names that copy an accepted second home, the single-source rule's small-duplicated-value exception, rather than adding it to the parity pin. Pinning it would edit a plugin file, which then ships only with a plugin version bump, for a list that changes only when git adds a value-taking global flag. `[verified: .claude/rules/review-pipeline-dispatch.md, the plugin-directory bullet]` The choice is the architect's. `[unverified]`
21. The criterion "exits before any git call when the command has no push fragment" is read this way: no git call unless a fragment is push-family, an alias candidate or `gh api`. An unknown subcommand may be an alias, so its one alias lookup is the only call allowed. `[unverified]`: the architect's reading of the criterion.
22. `_lib_review_ledger_path` inlines the detached-or-default test. Extracting it leaves the ledger's behavior unchanged and gives the Stop hook the same definition. `[verified: _lib.sh:5182-5254; GH-1407 Proposal §2]` `anchors: root`
23. The Stop scenarios build on `main` with no origin, so they need a non-default branch. That branch cannot be `feature`: `add_linked_worktree` defaults to it, and two fixtures run `checkout -b feature`. `[verified: test_autonomous_shipping_grant_boundary.py:71, :127; test_advance_past_commit_stall.py:60-61, :87; claude/.claude/tests/helpers.py:919]`
24. A new gate moves these pinned counts and lists: `[verified: test_hook_alignment.py:872-888, :2192-2203, :2237-2255, :3462, :3465; docs/hooks.md:59]`
    - `_SELF_FILTERING_BASH_GATES` and the two tier floors;
    - `_GATE_HOOK_COUNT = 33` and `_SETTINGS_GATE_HANDLER_COUNT = 34`;
    - the docs counts line.
25. An elevated tier row's Why quotes a single-line header sentence. `[verified: docs/hooks.md:18]`
26. `select-tests.py` maps any file under `claude/.claude/hooks/` to the hooks tests and any `.sh` there to the scripts tests, so it needs no edit. `[verified: claude/.claude/scripts/select-tests.py:546-547, :620]`
27. The literal `refs/remotes/origin/HEAD` is barred from every shipped shell file except `_lib.sh`. `[verified: claude/.claude/scripts/tests/test_default_branch_resolution_is_shared.py:23, :66-97]`
28. Where `~/.claude/hooks` is a real directory, a `git pull` leaves a new hook file unlinked while `settings.json` already registers it. A gate that cannot start blocks its own calls. `[verified: docs/hooks.md:444, :446, the "Gate deadlock recovery" section]` This gate has no `if`, so every Bash call blocks until the engineer runs `./install.sh` from a separate terminal.
29. `docs/hooks.md:227` stays accurate, because the extracted helper still calls `_lib_default_branch_or_guess`. `[verified: docs/hooks.md:227]`
30. On BSD/macOS awk, a blank line inside a quoted span can invert masking parity and hide a later push. The plan records it as inherited from `_lib_mask_shell_quotes` and as accepted debt. `[engineer-verified: "Accept as debt (Recommended)"]` This covers the acceptance decision only. Row 64 records the engineer's later acceptance of it as permanent debt, and row 65 what that selection bundles.
    - An example is a multi-paragraph `-m "$(cat <<'EOF' …)"` commit chained with `&& git push origin HEAD && echo "done"` on `main`.
    - The mechanism is reproduced on Darwin 25.6 with `/usr/bin/awk` and a copy of the masker's awk program. A double-quoted `-m` body holding a blank line, chained with a push to `HEAD:main` and a later quoted `echo`, lost the push words. `[verified: staff-platform-engineer-1794030998-GH-1407-deny-default.md R2]` It is not yet reproduced through the hook end to end; row 57's test does that.
31. The pushes that submodule recursion adds are not judged. They are accepted as permanent debt that the header lists as a Known gap. `[engineer-verified: "Accept as permanent debt (Recommended)"]` This covers only the label's answer to one question: whether to accept the submodule-recursion gap as a permanent Known gap.
    - `on-demand` pushes every changed submodule, and `only` pushes all submodules and leaves the superproject unpushed. When `push.recurseSubmodules` is unset, a true `submodule.recurse` means `on-demand`. `[verified: git-push(1), Git 2.50.1 man page: the --recurse-submodules option entry and the push.recurseSubmodules configuration entry]`
    - For `on-demand`, the gap is near-vacuous: the submodule push reuses the superproject's refspec, which the gate already judges. With and without a refspec, it failed inside the submodule with `src refspec 'refs/heads/feature' must name a ref`. `[verified: staff-backend-engineer-1794030998-GH-1407-deny-default.md R7, a scratch run on Apple git 2.50.1]` The `only` mode and other git versions are not checked.
    - Reading the engineer's non-selection of "Submodule recursion" as removing the handling is the dispatching session's instruction, not the engineer's words. `[unverified]`
    - A cooperative agent emits this shape, because a plain `git push origin feature` recurses wherever config sets `submodule.recurse=true`. Row 45 carries the gap's wording and rationale.
32. `--tags` with no refspec pushes only tags, because git consults neither `remote.*.push` nor `push.default` when `--tags` is given. A `tags` record with no `ref` record therefore passes. `--follow-tags` still pushes the default refspec, so it adds no record. `[verified: git-push(1) DESCRIPTION and the --follow-tags entry, Git 2.50.1 man page]` `anchors: root`
33. A push inside a quoted span that bash or another program executes is accepted as permanent debt, recorded as the header's class-level Known gap. `[engineer-verified: "Accept as permanent debt (Recommended)"]` This covers the acceptance decision only. `anchors: row9`
34. The class-level gap's header wording and acceptance rationale are the session's proposal, not the engineer's. `[unverified]` The commit message and PR body state the rationale, because a later-found gap that a cooperative agent can emit counts as debt only with a stated rationale. `[verified: docs/hooks.md:20]` `anchors: row33` The rationale:
    - The alternative, a stripped pass over the whole command, denies the canonical commit and PR-body form (row 10).
    - A narrower trigger, keyed on a wrapper command word in masked text, would not fire on that form, since its body masks to `""` (row 7). The wrapper's string argument masks to `""` too, so judging it needs a new layer that maps a masked word back to its stripped span, which the freeze rules out (row 53). `[unverified]`: derived from row 7, not run.
    - A push from a script file the agent writes, one of the issue's accepted gaps, is the same class: executed text the hook does not read.
    - `block-gh-pr-merge.sh` carries the same wrapper gap, and this repo counts its `bash -c` and `eval` shapes as plausible cooperative mistakes, so the rationale is not a non-cooperative threat model. `[verified: block-gh-pr-merge.sh:28-32; docs/hooks.md:63]`
    - Teaching `_lib_mask_shell_quotes` which quoted spans execute belongs in its own issue, since every caller of that shared helper would need review.
35. A push-family command whose arguments `xargs`, `parallel`, `-exec` or `-execdir` appends at run time is denied by a parser rule. `[engineer-verified: "Parser rule: deny (Recommended)"]` This covers the deny decision only. `anchors: root`
36. The rule emits `unresolvable appended-args` when one of those words precedes the command word, because the words the hook reads are not the push's whole argv.
    - Without it, `git branch --merged | xargs git push origin --delete` parses as a refspec-less push and passes on `feature`, while xargs appends every merged branch name. `[unverified]`: the parse is derived from this plan's contract, not run.
    - `find`'s `-ok` and `-okdir` match `-exec` and `-execdir` apart from a confirmation prompt, so the rule adds them as siblings. `[verified: find(1), macOS man page, the -ok and -okdir entries]`
    - That extension was the architect's, and the engineer then selected the option whose description bundled it (rows 38 and 41).

    Lighter options fail:
    - Denying only a `--delete` with no ref misses `xargs git push origin`, whose refspecs are appended.
    - Treating appended words as absent passes the example above.

    `anchors: row35`
37. An alias value's first word can be a global option such as `-c`, so the hook re-judges the rebuilt fragment instead of matching the value's first word. `[verified: git-config(1), the alias.* entry, Git 2.50.1 man page: "the first word of an alias does not necessarily have to be a command"]` Re-judging from the first rule also sets the moved flag for an alias of `checkout`. A rebuilt fragment that is again an alias candidate denies without a second lookup (row 46), so whether git expands an alias named in another alias's value does not change the gate's answer. `anchors: row16`
38. The engineer kept the alias re-judge loop. `[engineer-verified: "Alias re-judge loop (Recommended)"]` This covers the selection only.
39. The engineer kept the small parser siblings. `[engineer-verified: "Small parser siblings (Recommended)"]` This covers the selection only.
40. The engineer kept the refactors and docs. `[engineer-verified: "Refactors and docs (Recommended)"]` This covers the selection only.
41. Each selected option's description was the session's proposal, so what each selection bundles is `[unverified]`:
    - "Alias re-judge loop": the alias substitution loop (row 37), and `find`'s `-ok` and `-okdir` (row 36).
    - "Small parser siblings": `popd`, the `GIT_CONFIG_*` env forms, `--branches`, `--prune` without a glob, send-pack's `--stdin`, and `--tags` with no refspec judged as tags-only (row 32).
    - "Refactors and docs": the `_LIB_GIT_GLOBAL_VALUE_FLAGS` constant (row 20), the `_LIB_DEFAULT_BRANCH_FALLBACK_NAMES` rename with its test file, and the `docs/commit-stall-block.md` edit.
42. The engineer asked for a review against over-engineering. `[engineer-verified: "Please do a pass on this with the architect to make sure it's not overengineered. This issue is at danger of compoujnding defensive negineering"]` This covers that request only.
43. `git push` accepts the value of `--recurse-submodules` as the next word, so the parser lists that flag with the separate-value flags and emits no record for it. Without the rule, `git push --recurse-submodules check origin` would parse `check` as the remote and `origin` as a refspec, instead of the refspec-less push to `origin` that git makes. `[verified: gitcli(7), the "Separating argument from the option" section: "You can write the mandatory option parameter to an option as a separate word on the command line"; git-push(1), Git 2.50.1 man page, which gives --recurse-submodules no bare form, unlike --[no-]signed]` `anchors: root`
44. The over-engineering pass cut three items that no selection pins and the issue does not require. `[unverified]`: the architect's judgment. `anchors: row42`
    - The `kind` record. The gate never reads it. Per the dispatching session, #1409 needs only sources, destinations, `no-refspec`, an unresolvable flag and the repo path with its form. The protected set already gives a send-pack path or URL no remote lookup.
    - The `missing-value` reason. Git rejects a value-taking flag that has no value, and a substitution that cut the value off already ends the fragment in the cut marker (row 11). A subtree push missing its refspec now yields `no-refspec`.
    - The separate deny messages for a computed `-C` path, `appended-args` and the moved flag. The unresolvable message, which names its cause and asks for one literal push per Bash call, covers all three.

    The engineer's later selections cut three more (rows 46-48): the alias's second lookup, the `alias.` key in the refspec-less `-c` rule, and the gate tests that repeat parser-covered shapes.
45. The submodule gap's header wording and acceptance rationale are the session's proposal, not the engineer's. `[unverified]` The commit message and PR body state the rationale, because a later-found gap that a cooperative agent emits counts as debt only once the engineer accepts it and a stated rationale records it. `[verified: docs/hooks.md:20]` `anchors: row31` The rationale:
    - Submodule recursion lies beyond the issue's Covered list, and its handling was not selected (row 31).
    - Judging the added pushes means reading each submodule's own state: which submodules git would push, and each one's branch, remotes and push config. The command text holds none of that, so the gate would have to model git's recursion rather than parse the command.
46. The engineer capped the alias lookup at one per fragment. `[engineer-verified: "Alias cap 2 to 1 (Recommended)"]` This covers the selection only. Read with row 38, the kept re-judge runs once.
47. The engineer dropped the `alias.` key from the refspec-less `-c` rule. `[engineer-verified: "Drop alias.* -c rule (Recommended)"]` This covers the selection only.
48. The engineer trimmed the gate-level tests that repeat parser-covered shapes. `[engineer-verified: "Trim gate-level duplicate tests (Recommended)"]` This covers the selection only.
49. Each of rows 46-48's option descriptions was the session's proposal, so what each selection bundles is `[unverified]`. As the dispatching session relayed them:
    - "Alias cap 2 to 1": one alias lookup per fragment, and a deny for a rebuilt fragment that is again an alias candidate.
    - "Drop alias.* -c rule": the `alias.` key leaves the refspec-less `-c` rule, and its `-c alias.x=y` gate test goes.
    - "Trim gate-level duplicate tests": each gate test group keeps one or two of the shapes the parser tests already cover.
50. With one lookup, a rebuilt fragment whose subcommand is another alias, a git extension or a builtin `_LIB_GIT_BUILTIN_SUBCOMMANDS` omits denies, whether or not the command pushes. `[unverified]`: derived from the gate flow, not run.
    - The header lists this over-denial.
    - An alias-of-alias gate test pins it.
    - Both are the architect's follow-ons, not part of any label.
    - Over-deny is the trade the `irreversible` tier names. `[verified: docs/hooks.md:15]`

    `anchors: row46`
51. Dropping the `alias.` key loses no coverage. Git ignores an alias that hides an existing command, so no `-c alias.*` key changes what `git push` does. `[verified: git-config(1), the alias.* entry, as row 16 quotes]` An alias that a `-c` defines for a later word of the same command is the issue's accepted "alias defined in the same command" gap (row 2). The rule never judged that word anyway, because it reads `no-refspec` push records only. `anchors: row47`
52. Row 1's acceptance criterion requires every Covered shape to be denied with an allow control beside it, not one gate test per shape. `[verified: staff-sdet-1794030998-GH-1407-deny-default.md R2, which re-read the issue's Acceptance criteria 2]` So the split below meets it.
    - Each shape trimmed from the gate tests keeps one test in `test_lib_push_destinations.py`, of its parse, its protected set or its mutation predicate. Two trimmed shapes have no such test and share a kept shape's gate branch instead: `git switch` with `git checkout`, and `-o $(x)` with the unquoted `$(…)` form (row 71).
    - Each kept gate shape reaches the same gate branch as the shapes trimmed from its group.
    - Every judge branch keeps its own gate-level deny. A parser record does not show the gate's verdict for three of them, so the gate tests add `git push origin feature main` (any `ref` denies), `git push --tags origin main` from `feature` (a `tags` record with a `ref`), and `git push --follow-tags` on `main` (no `tags` record). `[verified: staff-sdet-1794030998-GH-1407-deny-default.md R2]`
    - The parser tests gain a `ref`-record and `dry-run` bullet, the architect's follow-on.

    `anchors: row48`
53. The engineer kept the `PreToolUse` command-text gate the issue prescribes, with its parser frozen at the issue's Covered list. `[engineer-verified: "Keep parser, freeze it (Recommended)"]` This covers the selection only. `anchors: root`
54. What row 53's selection bundles was the session's proposal, so it is `[unverified]`:
    - A git `pre-push` hook goes in "Alternatives set aside", with the staff-backend-engineer review's measured costs.
    - Any shape found after this plan becomes a header Known gap, not a new parser rule.

    `anchors: row53`
55. The engineer accepted a state change made earlier in the same command as a Known gap. `[engineer-verified: "State-change earlier in command: accept gap (Recommended)"]` This covers the selection only. `anchors: root`
56. The engineer accepted direct REST pushes and the `gh api` PR-merge endpoint as Known gaps. `[engineer-verified: "Direct REST and gh api merge: accept gap (Recommended)"]` This covers the selection only. `anchors: root`
57. The engineer chose to pin the macOS awk gap with a test. `[engineer-verified: "Pin the macOS awk gap with a test (Recommended)"]` This covers the selection only. `anchors: row30`
58. What rows 55-57's selections bundle was the session's proposal, so it is `[unverified]`:
    - Row 55: the header gap sits beside the same-command alias gap, with `git config remote.origin.push HEAD:main && git push`, `git branch -m` and `git remote set-head` as its examples. The moved flag is not widened.
    - Row 56: the header lists `curl` with a token against the contents API, and `gh api repos/*/pulls/N/merge`, tracked as GH-1077. The tier row, the `docs/hooks.md` bullet and the README row say first that wrapped, scripted and HTTP pushes pass, and name server-side branch protection as the barrier.
    - Row 57: a Darwin-only gate test reproduces the blank-line mechanism. The header's Known gaps name the shape. The bundle also named a follow-up issue, which row 65 drops. The PR body states that the primary platform is affected.

    GH-1077 tracks the merge endpoint. `[verified: block-gh-pr-merge.sh:24, docs/hooks.md:63]` `anchors: row56`
59. Rows 55 and 56 record later-found gaps a cooperative agent can emit. Such a gap counts as debt only once the engineer accepts it as permanent debt and the commit message or PR body states the rationale. `[verified: docs/hooks.md:20]` Neither row's selected label says "permanent", so both gaps meet that bar through row 74, and the commit message and PR body state the rationale below. The rationale is the session's proposal. `[unverified]` `anchors: row58`
    - State changed earlier in the command is the same class as the issue's accepted "alias defined in the same command": the gate reads repository state before the command runs. Closing it would need every earlier fragment checked against a read-only list of git subcommands, a new layer the freeze rules out (row 53).
    - An HTTP request carries no push grammar for the gate to read, and the `gh api` merge endpoint merges a pull request rather than pushing a ref. Server-side branch protection is the barrier for both. A `pre-push` hook would not see either. `[verified: staff-backend-engineer-1794030998-GH-1407-deny-default.md R6]`
60. Git resolves a remote destination `heads/main` to `refs/heads/main`, so the parser drops a leading `heads/` as it drops `refs/heads/`. `[verified: the dispatching session's scratch run, relayed in this round's dispatch: a push to HEAD:heads/main landed on main]` `anchors: row1`
    - git-push(1) says only "If <dst> unambiguously refers to a ref on the <repository> remote, then push to that ref." `[verified: git-push(1), Apple Command Line Tools man page, line 112]`
    - gitrevisions(7) disambiguates a short name by trying `refs/<refname>`, `refs/tags/<refname>`, `refs/heads/<refname>`, `refs/remotes/<refname>` and `refs/remotes/<refname>/HEAD`, after `$GIT_DIR/<refname>`. `[verified: gitrevisions(7), Apple Command Line Tools man page, lines 85-172]` Only `refs/<refname>` and `refs/heads/<refname>` can place a short name in `refs/heads/`, so `heads/<x>` is the one short form added. That push matching follows this list is `[unverified]`; the scratch run confirms the `heads/` case only.
    - The engineer placed this correction inside the freeze (row 66).
61. `_lib_tool_argv_from_subcmd`'s gh grammar makes every long flag without `=`, and every two-character short flag, consume the next word. So `gh api --paginate repos/o/r/contents/f -X PUT …` yields no endpoint word. `[verified: _lib.sh:2583-2590]` The ref-moving-endpoint match therefore reads every word after `api`. `anchors: row1`

    Lighter options fail:
    - A list of `gh api`'s boolean flags in the walk misses any flag it omits, and a miss hides the endpoint again, failing open.
    - Taking the first non-flag word as the endpoint picks `PUT` in `gh api -X PUT repos/o/r/contents/f`.

    The cost is an over-deny of a mutating call with any word, a flag value or field text included, that names a ref-moving endpoint path, which the header lists. The engineer placed this correction inside the freeze (row 66).
62. The protected set stays keyed to the command's remote word and that remote's local HEAD, as row 14 reads the issue. So a default branch other than `main` or `master` is protected only where its remote's HEAD is set locally, and the header says so. `[verified: ciso-reviewer-1794030998-GH-1407-deny-default.md R4]` A union of every configured remote's HEAD was offered and not selected. Reading that non-selection as declining it is the dispatching session's relay, not the engineer's words. `[unverified]` `anchors: row14`
63. The sync-recipe and `plan-push-preflight.py` allow controls copy their command text into the gate test, and each copy names its source path. `[unverified]`: the architect's judgment. `anchors: row26`
    - Reading them by path needs a markdown-extraction helper and a `CROSS_DOMAIN_EXCEPTIONS` row in `select-tests.py`, as `READY_FOR_REVIEW_SKILL_MD` has, which would contradict row 26. `[verified: select-tests.py:292, :862; test_select_tests.py:332]`
    - A copy that drifts fails toward a visible deny: the next agent to run the changed recipe gets the gate's message, not a silent pass.
64. The engineer accepted the BSD/macOS awk gap as permanent debt. `[engineer-verified: "Accept as permanent debt (Recommended)"]` This covers only the label's answer to one question: whether to accept the gap as permanent debt. `anchors: row30`
65. What row 64's selection bundles, and the reasoning behind it, was the session's proposal, made after the engineer asked "Seriously? That doesn't fall under non-cooperative/adversarial agent overparanoid edge case? If not I want to know why". So it is `[unverified]`: `anchors: row64`
    - A cooperative agent emits this shape, because the canonical commit message has a blank line after its title and chaining a push after the commit is ordinary. This is the session's answer to the engineer's question.
    - The header cites `_lib_mask_shell_quotes`' Limits as the gap's tracker, and no follow-up issue is filed. The Limits already describe the mechanism. `[verified: _lib.sh:4064-4070]`
    - A gap discovered later that a cooperative agent emits counts as debt only once the engineer accepts it as permanent debt and the commit message or PR body states the rationale. `[verified: docs/hooks.md:20]` Row 30's selected label does not say "permanent", so the gap meets that bar through row 64.
    - The rationale the commit message and PR body state: the fix belongs in `_lib_mask_shell_quotes`, a shared helper whose other callers would each need their own review, and row 57's test fails if the allow stops reproducing, which flags the header entry as stale.
    - That test runs only on a developer's macOS machine, because CI runs on `ubuntu-24.04` only. `[verified: .github/workflows/tests.yml:25]` A masker fix is developed on the primary platform, so the pin still runs where the fix lands.
    - Row 57's Darwin-only pin test and the PR-body statement that macOS, the primary platform, is affected both stay.
66. The engineer confirmed that rows 60 and 61 fit within row 53's "Keep parser, freeze it (Recommended)" selection. `[engineer-verified: "Yes, corrections to Covered shapes (Recommended)"]` This covers only that answer. `anchors: row53`
67. What row 66's selection bundles was the session's proposal, so it is `[unverified]`: the freeze bars parser rules for shapes beyond the Covered list, not fixes that make a Covered shape deny. Row 68's match-anywhere rule rests on that reading. `anchors: row66`
68. The computed-endpoint arm reads only the endpoint word, the first word `_lib_tool_argv_from_subcmd` emits after `api`, and fires when that word holds `$` or the cut marker anywhere. `[unverified]`: the architect's design, made in this round after the reviews. `anchors: row61`
    - An arm reading every word would deny `/respond-pr`'s reply recipe whenever a reply's first line holds inline code or a `$` word. Step 1 joins the recipe's backslash-newline, so that line sits in the `gh api` fragment until the body's first newline. `[verified: claude-skills/skills/respond-pr/SKILL.md:75-78, :110-113, for the recipe shape]` `[unverified]`: the deny is derived from steps 1 and 2, not run.
    - Matching anywhere in the endpoint word, not only at its start, denies `gh api repos/$(…)/contents/f -X PUT`, whose substitution cuts the endpoint short. `[verified: ciso-reviewer-1794038557-GH-1407-deny-default.md R1]`
    - `-f body="$(cat f)"` stays allowed, because a field value is not the endpoint word.
    - Platform review R2's `-f body="Status: $STATUS"` and GraphQL `{id: $id}` shapes, written after the endpoint, are allowed for the same reason. `[verified: staff-platform-engineer-1794038557-GH-1407-deny-default.md R2]`

    Lighter options fail:
    - Denying a mutating call whose `gh api` fragment holds the cut marker anywhere also denies `-f body="$(cat <<'EOF' … EOF)"`, the usual way to post a multi-line body.
    - Recording `repos/$(…)/contents` as an encoding trick leaves an ordinary way to name the repository open. `[verified: ciso-reviewer-1794038557-GH-1407-deny-default.md R1, "not an obfuscation"]`

    Three flag shapes before the endpoint defeat the endpoint read. `[verified: _lib.sh:2583-2590, for gh's flag grammar]` `[unverified]`: each trace is derived from steps 1 and 2 and that grammar, not run, as in ciso-reviewer-1794055402-GH-1407-deny-default.md R1 and R2.
    - A boolean flag such as `--paginate` consumes the endpoint (row 61).
    - Step 1 strips a quoted multi-word flag value's quotes, so the flag consumes only the value's first word, and the next word becomes the endpoint word. `gh api -X PUT -f message="add readme" $URL` reads `readme` as its endpoint word.
    - A flag value holding a command substitution ends the `gh` fragment at the cut marker, so the fragment has no endpoint word and no later word for row 61's match. `gh api -X PUT -f content="$(base64 …)" repos/o/r/contents/f` loses its literal endpoint.

    After the first two shapes, a computed endpoint passes and a literal ref-moving endpoint still denies, because row 61's match reads every word after `api`. After the third, both pass. The header lists the three shapes as one Known gap (row 76). The engineer accepted the boolean-flag shape as permanent debt (rows 72 and 74), and the other two as permanent debt (row 78). Closing the boolean-flag shape would need a list of gh's boolean flags, which row 61 sets aside because a miss fails open.

    The multi-word shape also over-denies, which the header lists: `gh api -X POST -f body="hello $USER" repos/o/r/issues/1/comments` reads `$USER` as its endpoint word. A single-word substitution value adds no over-deny: in a flags-first `gh api -X POST -f body="$(cat f)" repos/o/r/issues`, `-f` consumes the whole `body=<cut>` word, and the fragment ends with no endpoint word, so the call passes.
69. The `gh api` check applies only where `gh` is the fragment's command word, so `git commit -m "… gh api -X PUT repos/o/r/contents/f"` is not judged. `_lib_fragment_invokes_tool` skips leading assignments and a closed list of runner words such as `sudo` and `env`. `[verified: _lib.sh:2263-2333]` A quoted mention still denies where it starts a fragment, as inline code or at the start of a quoted line, which the header lists (row 13). `anchors: row13`
70. `_lib_gh_api_call_is_mutating`'s regexes miss gh's bundled `-iXPUT` and attached `-fmessage=m` spellings, so such a call passes. `[verified: deny-private-project-refs.sh:442-446]` The header names them in the issue's encoding-tricks gap, with no code change. Reading them as encoding tricks, since each needs a deliberate non-default spelling, is the reviewer's and the dispatching session's reading. `[unverified]` `anchors: row2`
71. The gate test file keeps one gate-level deny per distinct judge predicate, plus the issue's named allow controls, in the redirecting-config, computed-destination, explicit-destination and moved-flag groups. This applies row 48. `[verified: staff-sdet-1794038557-GH-1407-deny-default.md R1]` `anchors: row48`
    - Each test builds its own origin and clone, so no config or HEAD state crosses tests. `[verified: staff-sdet-1794038557-GH-1407-deny-default.md R3: bare_remote_with_default_branch is a plain function over the function-scoped tmp_path]` That build cost counts against the ceiling.
    - The ceiling is 60 seconds of serial (`-n0`) wall time per new test file. Two files at the ceiling stay under a tenth of the CI job's 25-minute timeout without xdist. `[verified: .github/workflows/tests.yml:34]` `[unverified]`: the number is the architect's choice.
    - Which allow controls the issue names was not re-read here. `[unverified]` The implementing session keeps every control the issue's Covered list names.
72. The engineer accepted row 68's narrowed computed-endpoint rule and the Known gap it leaves, then described as a computed `gh api` endpoint after a boolean flag. `[engineer-verified: "Accept narrowed rule and gap (Recommended)"]` This covers only that acceptance of row 68 and its gap. The gap's real condition is wider than that description (row 68), and row 76 records the engineer's choice to widen the description. `anchors: row68`
73. What row 72's selection bundles was the session's proposal, so it is `[unverified]`: `anchors: row72`
    - `/respond-pr`'s reply recipe and `gh api … -f body="$(cat f)"` stay allowed, because the arm reads only the endpoint word (row 68).
    - The gap is later-found and a cooperative agent can emit it, so the commit message and PR body state its acceptance rationale. `[verified: docs/hooks.md:20]` Row 72's selected label does not say "permanent", so the gap meets that bar through row 74.
    - The rationale, from row 68: closing the gap needs a list of gh's boolean flags in the endpoint read, which row 61 sets aside because a flag the list omits hides the endpoint again.
    - A literal ref-moving endpoint after a boolean flag still denies when nothing cuts the `gh` fragment before it, because row 61's match reads every word after `api` in that fragment. It passes when a flag value before it holds a command substitution, because the cut ends the fragment before the endpoint (row 68). Rows 77 and 78 cover that shape and the quoted multi-word value.
74. The engineer accepted three later-found gaps as permanent debt: a state change earlier in the same command (row 55), direct REST pushes and the `gh api` PR-merge endpoint (row 56), and a computed `gh api` endpoint after a boolean flag (row 72). `[engineer-verified: "Accept all three as permanent debt (Recommended)"]` This covers only that acceptance of those three gaps as permanent debt. `anchors: row55, row56, row72`
75. What row 74's selection bundles was the session's proposal, so it is `[unverified]`: no follow-up issue is filed for any of the three gaps, and the commit message and PR body state each one's rationale, which rows 59 and 73 give. Reading "no follow-up issue" as leaving the header's existing GH-1077 pointer for the merge endpoint in place (row 58) is the architect's reading. `anchors: row74`
76. The engineer, asked how to handle the widened `gh api` gap, chose to widen the accepted gap's description and add no deny rule. `[engineer-verified: "Widen the accepted gap, no new rule (Recommended)"]` This covers only that choice: the description of the gap rows 72 and 74 accepted widens, and no rule denies a mutating `gh api` fragment that a substitution cuts before its endpoint word. `anchors: row72`
77. What rows 76 and 78's selections bundle was the session's proposal, so it is `[unverified]`: `anchors: row76, row78`
    - The widened description names row 68's three flag shapes before the endpoint: a boolean flag, a quoted multi-word flag value, and a flag value holding a command substitution. The header gap entry, the over-denial entry, rows 68 and 73, Verification and Out of scope carry it.
    - Dispatch 3's pinned-gap tests pin the two new shapes as allows, each beside its endpoint-first deny. No deny rule or deny test is added for them.
    - Row 74's acceptance names a computed endpoint after a boolean flag only, and row 76's label does not say "permanent". A later-found gap that a cooperative agent emits counts as debt only once the engineer's answer to a blocking stop-and-ask accepts it as permanent debt, and the commit message or PR body states the rationale. `[verified: docs/hooks.md:20]` Row 78 accepts both new shapes as permanent debt, so both meet that bar once the commit message and PR body state the rationale below.
    - Row 78's question, as the dispatching session relayed it, also proposed "no new rule, rationale in commit message and PR body". That wording is the session's, and the no-rule bullet above and the rationale bullet below carry it.
    - A cooperative agent emits both shapes: a commit message passed as `-f message="…"` is often several words, and the reviewer calls `-f content="$(base64 …)"` the ordinary way to feed the contents API. `[verified: ciso-reviewer-1794055402-GH-1407-deny-default.md R1, for the reviewer's claim]`
    - The rationale the commit message and PR body state:
      - Denying a mutating `gh api` fragment cut before its endpoint word also denies a flags-first call whose single-word field value holds a substitution, such as `gh api -X POST -f body="$(cat f)" repos/o/r/issues`. `[verified: ciso-reviewer-1794055402-GH-1407-deny-default.md R1, which names that cost]`
      - Reading the endpoint from masked text instead keeps a quoted multi-word value as one word, but a quoted endpoint holding `{owner}` or `$` masks to `""` and would pass (row 13). Reading both texts is a new layer, which the freeze rules out (row 53).
78. The engineer accepted as permanent debt the two `gh api` gap shapes that row 76's widening added: a quoted multi-word flag value before the endpoint, and a flag value holding a command substitution. `[engineer-verified: "Accept both as permanent debt (Recommended)"]` This covers only that acceptance of those two shapes as permanent debt. `anchors: row68, row76`
79. Code review of dispatch 1 found `_set_origin_head` copied across two test files and open-coded in a third, and the fix adds a shared helper to `claude/.claude/tests/helpers.py`, a file the plan did not name. `[engineer-verified: "Keep the helpers.py edit"]` This covers only adding that file to dispatch 1's Critical files. `anchors: row22`

## Critical files

Implementation runs as three `code-writer` dispatches in sequence. Dispatches 1 and 2 both edit `_lib.sh`, and dispatch 3 consumes dispatch 2's helpers, so none of them runs in parallel. Each closes with `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` after its own command below.

**Dispatch 1: the Stop hook and the shared detached-or-default helper.** Verification: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib_git_refs.py claude/.claude/hooks/tests/test_lib_reviewer_round_state.py claude/.claude/hooks/tests/test_advance_past_commit_stall.py claude/.claude/hooks/tests/test_autonomous_shipping_grant_boundary.py`.
- `claude/.claude/hooks/_lib.sh`:
  - Add `_lib_head_detached_or_default` beside `_lib_review_ledger_path`, reusing `_lib_default_branch_or_guess` and `_lib_capped`.
  - Rename `_LIB_REVIEW_LEDGER_DEFAULT_BRANCH_NAMES` to `_LIB_DEFAULT_BRANCH_FALLBACK_NAMES`, with its comment naming the new helper.
  - Rewrite `_lib_review_ledger_path`'s inline test (lines 5229-5246) as a call to the helper.
- `claude/.claude/hooks/advance-past-commit-stall.sh`: the new condition after line 231. Header lines 5-7 name it, and lines 38-39 drop the count "three".
- `claude/.claude/hooks/tests/test_lib_git_refs.py`: direct tests of the helper's three statuses and printed branch. Cases: a feature branch, an origin/HEAD default, a fallback name with no origin, a detached HEAD, a directory that is not a repository, and `main` when origin/HEAD names `trunk`, which exits 1 because the fallback names apply only when no default resolves.
- `claude/.claude/hooks/tests/test_lib_reviewer_round_state.py`: the array name at line 829 and its docstring.
- `claude/.claude/hooks/tests/test_advance_past_commit_stall.py`:
  - `_init_repo` (line 61) builds on a branch that is neither a default name nor `feature`.
  - New silent cases: `main` with no origin, an origin/HEAD-named `trunk`, and a detached HEAD. Each repo is dirty, so only the branch decides.
  - A dirty repo on a non-default branch where only the `symbolic-ref` read fails stays silent (row 22). `git_timeout_shim`'s `exit_status` makes that one call fail, and the fixture skips without `timeout` or `gtimeout` (`claude/.claude/hooks/tests/conftest.py:311-312`).
- `claude/.claude/hooks/tests/test_autonomous_shipping_grant_boundary.py`: one module constant names the scenario branch, and every `init_git_repo_with_commit` call in `_build_scenario` passes it. Each denied scenario is then still denied for its root reason, not for the branch.
- `claude/.claude/tests/helpers.py`: a shared `set_origin_head`, imported by the three test files above in place of their private copies (row 79).
- `docs/commit-stall-block.md`: a firing-condition bullet after line 23 naming `_lib_head_detached_or_default`. Line 78's git-call list is reworded without a count.
- `docs/hooks.md`: the `advance-past-commit-stall.sh` bullet (line 220) adds the condition.
- `CHANGELOG.md`: a bullet in the `### Changed` section at line 7.

**Dispatch 2: the push helpers in `_lib.sh` and their existing caller.** Verification: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_lib_push_destinations.py claude/.claude/hooks/tests/test_deny_private_project_refs.py`.
- `claude/.claude/hooks/_lib.sh`:
  - `_LIB_GIT_GLOBAL_VALUE_FLAGS`, with `_lib_git_argv_from_subcmd` reading it.
  - `_LIB_GIT_BUILTIN_SUBCOMMANDS` beside it, authored from git 2.43's `git --list-cmds=builtins` (row 16). The list need not be complete, so a name the implementing session cannot confirm as a 2.43 builtin stays off it.
  - `_lib_git_push_targets`, walking words under `set -f` like `_lib_git_argv_from_subcmd`.
  - `_lib_push_protected_branches`, reusing `_lib_capped`.
  - `_lib_gh_api_call_is_mutating`.

  Every regex is POSIX ERE.
- `claude/.claude/hooks/deny-private-project-refs.sh`: lines 442-446 call `_lib_gh_api_call_is_mutating "$COMMAND_UNQUOTED"`. The comment above keeps its rationale and says where the regexes live.
- `claude/.claude/hooks/tests/test_lib_push_destinations.py` (new) sources `_lib.sh` directly and covers:
  - parser records for each command form and each `unresolvable` reason;
  - `ref` records for `+main`, `refs/heads/main`, `HEAD:main`, `feature:refs/heads/main`, `HEAD:main-fix` and `HEAD:refs/for/main`, the empty source of `:main`, `--delete main` and `-d main`, and `dry-run` for `--dry-run` and `-n` (row 52);
  - destination `main` for `HEAD:heads/main`, `feature` for `HEAD:heads/feature`, `heads/main` for `HEAD:refs/heads/heads/main`, and `feature/main` for `HEAD:feature/main` and `feature/main` (row 60);
  - `--recurse-submodules check origin` giving remote `origin` and `no-refspec`, and no record for `--recurse-submodules=on-demand` or `--no-recurse-submodules` (row 43);
  - `appended-args` for `xargs git push origin --delete`, `find . -exec git push origin {} ;`, the `-execdir` form and `parallel git push origin ::: x`, and no such record for a branch named `xargs`;
  - `tags` for `--tags`, and no `tags` record for `--follow-tags`;
  - `-C` composition (`-C a -C b`, `-C /x -C b`, `-C a -C /y`, `~/`), `--git-dir` and `GIT_DIR=`;
  - `-c`, `--config-env` and env-prefix keys, a mixed-case key's lowercased section and variable name, and `--repo`;
  - short bundles (`-un`, `-fd`, `-oX`, `-o X`), `--`, delete mode and `tag <name>`;
  - masked-span and `$` words, and exit 1 for a fragment that is not a push;
  - the protected set with origin/HEAD set, unset, a URL remote, a second remote with its own HEAD, and a git failure (status 2). Its repositories come from `bare_remote_with_default_branch` (`claude/.claude/tests/helpers.py:1482`), its calls go through `_run_lib_call` (`from ._lib_sh_helpers import`), and its env is `isolated_git_config_env` (`helpers.py:1446`);
  - the mutation predicate for each flag shape, a GET, and a failing `grep` through a PATH shim;
  - every name in `_LIB_GIT_BUILTIN_SUBCOMMANDS` appearing in `git --list-cmds=builtins`, skipping when git does not support that option. CI runs it against git 2.43.0 (row 16).

**Dispatch 3: the gate, its registration, tests and docs.** Verification: `scripts/venv-run.sh pytest claude/.claude/hooks/tests/test_deny_default_branch_push.py claude/.claude/hooks/tests/test_hook_alignment.py`, then the ruff and shellcheck commands in Verification.
- `claude/.claude/hooks/deny-default-branch-push.sh` (new, executable): the gate flow above. It reuses `_lib_strip_shell_quotes`, `_lib_split_fragments`, `_lib_mask_shell_quotes`, `_lib_fragment_invokes_git`, `_lib_fragment_invokes_tool`, `_lib_extract_git_subcmd`, `_lib_tool_argv_from_subcmd`, `_lib_capped`, and dispatch 2's helpers and builtin list. It uses no `xargs -n1` and no bare `jq`.
- `claude/.claude/settings.json`: an entry right after `require-ready-for-review.sh` (lines 364-369) in the same PreToolUse Bash group, with `"onFailure": "block"`, a `statusMessage` and no `if`.
- `claude/.claude/hooks/tests/test_deny_default_branch_push.py` (new). Each deny below is listed with its allow control, and asserts the branch or cause its message names. Each group keeps one gate-level deny per distinct judge predicate, plus the issue's named allow controls, and the parser file keeps the per-key and per-arm pins (rows 52 and 71).
  - Ceiling. The file finishes within 60 seconds serially (`-n0`) on the implementing machine, and so does `test_lib_push_destinations.py` (row 71). A file over its ceiling is trimmed further, or the implementing session reports its measured time instead of raising the ceiling.
  - Fixture. Each test builds its own origin and clone through `bare_remote_with_default_branch(tmp_path)`, so config, HEAD and worktree state never cross tests. Its `branch="trunk"` form builds the `trunk` variant. The test adds a `feature` branch tracking `origin/feature` and a linked feature worktree. Every git call and hook run uses `isolated_git_config_env`, because the gate also reads `push.default`, `remote.*.push` and `alias.*` from global config. The `gh api` group reads no repository state, so it runs from a bare `tmp_path` without the fixture.
  - Explicit destination. Deny `git push origin main`, `git push origin feature main`, and `trunk` in the `trunk` fixture. Allow `git push origin feature`, `HEAD:main-fix`, `feature/main` and `HEAD:refs/for/main`. The parser file pins `feature:refs/heads/main`, `HEAD:heads/main`, `HEAD:feature/main` and `HEAD:heads/feature`.
  - Remote word. With the unquoted remote `git@host:o/r.git`, deny `feature:main` and allow `feature`.
  - HEAD-relative. Deny `git push origin HEAD` and `@` on `main`, `git push origin HEAD` on a detached HEAD, and `git push origin :` on any branch. Allow `git push -u origin HEAD` on `feature`, and `git push origin HEAD:feature` on a detached HEAD.
  - Globs and tags. Deny `refs/heads/*:refs/heads/*`, `--all`, `git push --tags origin main` from `feature`, and `git push --follow-tags` on `main`. Allow `git push origin v1.0.0`, and `git push --tags` on `main`.
  - Deletion. Deny `:main` and `--delete main`. Allow `--delete old-feature`.
  - Refspec-less. Deny `git push` on `main`, on `main` with no upstream (the `@{push}` fallback), and on a feature branch whose upstream is `origin/main` under `push.default=upstream`. Allow `git push` on `feature`.
  - Second push remote. Under `remote.pushDefault=fork`, with a fetched `fork` remote whose HEAD names `dev`, deny a refspec-less `git push` on `dev` and allow it on a branch fork's HEAD does not name (row 15).
  - Redirecting config, each with a refspec-less push on `feature`. Deny `push.default=matching` and a set `remote.origin.push`, both read from config, and `-c push.default=matching`, a `config` record. Allow `git -c push.default=matching push origin feature` and `plan-push-preflight.py`'s command shape, copied from that script (row 63). The parser file pins the `-c remote.origin.push=…` key and the `GIT_CONFIG_PARAMETERS=` prefix's `*`.
  - Computed destination. Deny `git push origin "$(git branch --show-current)"`, a masked span, and its unquoted `$(…)` and backtick forms, which end in the cut marker. The backtick form stays because step 2 replaces each opener separately and no parser test reaches that step. Each message offers `git push -u origin HEAD`. Allow the three sync recipes on `feature`, the backslash-newline form included, copied from `git-feature-branch-sync/SKILL.md` (row 63). The parser file pins the `$BRANCH` word, and `-o $(x)` reaches the unquoted form's cut-marker branch.
  - Repo path. From a CWD on `main`, allow `git -C <feature-worktree> push -u origin HEAD`, with the path relative to that CWD. From the feature worktree, deny `git -C <main-checkout> push origin HEAD` and `--git-dir=<main-checkout>/.git`.
  - Moved flag. Deny `cd <main-checkout> && git push`, a refspec-less push, and `git checkout main && git push origin HEAD` and `gh pr checkout 7 && git push -u origin HEAD`, each a `HEAD` destination. Deny `pushd <feature-worktree> && git push -u origin HEAD`, which pins the header's moved-flag over-denial and keeps the `pushd` arm tested. Allow `cd <feature-worktree> && git push origin feature`. `git switch` shares `git checkout`'s arm (row 52).
  - Aliases. Deny `git p origin main` with `alias.p=push`, `git q origin main` with `alias.q=-c push.default=current push`, `git co main && git push` with `alias.co=checkout`, `git sync` with a `!` alias, and `git pp origin feature` with `alias.pp=p`, an alias of an alias (row 46). Allow `git p origin feature`, `git q origin feature`, and `git st` with `alias.st=status`.
  - Other surfaces. Deny `git subtree push -P dir origin main` and `git send-pack ../origin.git main`. Allow `git send-pack ../origin.git feature`.
  - `gh api`.
    - Deny a contents `PUT`, a `git/refs` `PATCH`, a `merges` `POST`, a mutating call to a `$URL` endpoint, and `gh api --paginate repos/o/r/contents/f -X PUT -f message=m -f content=x`, whose boolean flag precedes the endpoint (row 61).
    - Deny `gh api repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/contents/f -X PUT -f message=m -f content=x` and its backtick form (row 68).
    - Allow a contents `GET`, `gh api --paginate repos/o/r/contents/f`, a mutating call to `repos/o/r/issues`, and `gh api repos/o/r/issues -f body="$(cat f)"`.
    - Allow `git commit -m "deny gh api -X PUT repos/o/r/contents/f"`, where `gh` is not the command word (row 69).
    - Allow, as one parametrized test pinning the endpoint-word arm (row 68): `gh api repos/o/r/issues/1/comments -f body="Status: $STATUS"`, a `gh api graphql` mutation whose query holds `{threadId: $id}`, and a reply shaped like `respond-pr/SKILL.md`'s recipe whose first body line holds inline code.
  - Quoted mentions, all allowed. `gh pr create --body "never git push origin main"`, and `git commit -m "docs: git push origin main"`. On `main`, a canonical `git commit -m "$(cat <<'EOF' … EOF)"` whose body holds `` `git push origin main` `` as inline code and the word `eval`. The body has no blank line, because the blank-line form is the pinned gap below.
  - Pinned gaps, each a structural pair like `test_block_gh_pr_merge.py`'s `test_documented_bypass_structural_pair`:
    - On `main`, `out="$(git push -u origin HEAD 2>&1)"` is allowed and `git push -u origin HEAD` is denied (row 33).
    - BSD/macOS awk (rows 30 and 57). From `feature`, `git commit -m "<title><blank line><body>" && git push origin HEAD:main && echo "done"` is allowed, and the same command with no blank line is denied.
      - The test skips unless the platform is Darwin and `/usr/bin/awk` exists. It runs only on developers' macOS machines, because CI runs on `ubuntu-24.04` only (row 65).
      - It sets its subprocess's `PATH` itself, with a directory holding only an `awk` link to `/usr/bin/awk` first, so a Homebrew awk earlier on the developer's `PATH` cannot decide it.
      - Its failure message says a masker fix that flips the allow makes the header's awk Known gap and `_lib_mask_shell_quotes`' Limits entry stale, not that the gate regressed. The fixer deletes the header entry, updates the Limits entry, and flips the test to a deny.
      - If the allow does not reproduce through the hook, the implementing session reports that instead of reshaping the test.
    - `gh api` flag before the endpoint (rows 68 and 77). From a bare `tmp_path`, `gh api -X PUT -f message="add readme" $URL` and `gh api -X PUT -f content="$(base64 f)" repos/o/r/contents/f` are allowed. Each is denied with its endpoint moved to directly after `api`.
  - Appended arguments. Deny `git branch --merged | xargs git push origin --delete`. Allow `git branch --merged | xargs git branch -d`.
  - Dry run. Allow `git push --dry-run origin main`.
  - No git call. A logging `git` shim first on PATH records nothing for `ls`, `git status`, `git log --oneline` and `gh pr view`, and records one alias lookup for `git st`.
  - Fail closed. One parametrized table maps each fail-closed site to a shim and a command that reaches it, and asserts a deny that names the step:
    - the strip step, through a failing `tr`;
    - the stripped split and the masked split, through `sed_split_stage_shim` with 0 and 1;
    - the mask step, through a failing `awk`, as `test_deny_invisible_commit_content.py:1036` does;
    - the alias lookup, `symbolic-ref HEAD`, `config --get-regexp` and `git remote` calls;
    - a cap kill of `rev-parse @{push}` on a feature branch tracking `origin/main`, where the error fallback would pass;
    - `_lib_push_protected_branches`' status 2.

    The git rows use `git_timeout_shim`, which skips without `timeout` or `gtimeout` (`conftest.py:311-312`), so they run in CI but not on a stock macOS machine. A non-Bash tool passes.
- `claude/.claude/hooks/tests/test_hook_alignment.py`:
  - Add the hook to `_SELF_FILTERING_BASH_GATES`, `_UNTRUSTED_INPUT_FLOOR` and `_IRREVERSIBLE_FLOOR`. The floors' comments change "eight" to "nine" and "15" to "16".
  - `_GATE_HOOK_COUNT` becomes 34, and `_SETTINGS_GATE_HANDLER_COUNT` becomes 35.
- `docs/hooks.md`:
  - The tier row goes between `deny-data-file-reads.sh` and `deny-env-reads.sh`. Its Why says first that wrapped, scripted and HTTP pushes pass, and names server-side branch protection as the barrier against them (row 56). It then quotes the header sentence, says a push to an unprotected default branch can deploy and cannot be retracted, and points to the header's Known gaps.
  - Line 59's counts become 9 elevated gates, with the hook in the pinned-floor list, and 16 `irreversible` gates.
  - A `- **\`deny-default-branch-push.sh\`**` bullet goes after `require-ready-for-review.sh` (line 149). It says first that wrapped, scripted and HTTP pushes pass, and names server-side branch protection as the barrier against them. It then states the protected set with its local-HEAD caveat (row 62), the one masked pass, the `gh api` endpoints and the `!` prefix, and points to the header's Known gaps and over-denials.
  - The "Default branch" known gap (line 253) is rewritten to describe the hook and the Stop condition.
- `README.md`: a Hook → Gates row after line 193. It says first that wrapped, scripted and HTTP pushes pass, and names server-side branch protection as the barrier. It then says the gate reads command text only, so a plain push also passes in three cases: after an earlier fragment in the same command changes the state the gate reads, after a multi-paragraph commit message on BSD/macOS awk, and through submodule recursion. It points to the header's Known gaps.
- `CHANGELOG.md`: a bullet in the `### Added` section at line 518. Where `~/.claude/hooks` is a real directory, it tells the engineer to run `./install.sh` from a separate terminal after pulling, because until then the unlinked gate blocks every Bash call (row 28).

This plan file, `.claude/plans/deny-default-branch-pushes.md`, ships in the same PR. `select-tests.py`, `require-ready-for-review.sh` and `plugins/skill-management/hooks/_lib.sh` are not edited (rows 26, 63, 4, 20 and G3).

## Verification

- After each dispatch, run its named pytest command, then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` from the worktree root.
- After dispatch 3, the `select-tests: running …` line that `select-tests.py` prints to stderr must name `claude/.claude/hooks/tests` and `claude/.claude/scripts/tests`. That meets the criterion "select-tests.py maps the new hook" with no rule-table edit (row 26).
- `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
- `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
- `rg -n '_LIB_REVIEW_LEDGER_DEFAULT_BRANCH_NAMES' claude/` prints nothing. `rg -n -F -e '--raw-field|--input)' claude/.claude/hooks/ --glob '*.sh'` matches only `_lib.sh`, since that string occurs only inside the moved regex.
- Each acceptance criterion maps to a check:
  - Registration and header: `test_hook_alignment.py`'s self-filtering, `onFailure`, tier-table and floor tests.
  - Every Covered shape with its allow control: `test_deny_default_branch_push.py` judges each group end to end, and `test_lib_push_destinations.py` covers each shape the gate tests trimmed, apart from the two that row 52 names.
  - No git call without a push fragment: that file's PATH-shim test.
  - The Stop hook: the new silent cases in `test_advance_past_commit_stall.py`, and the grant-boundary scenarios still firing on their non-default branch.
  - `docs/hooks.md`: `test_hook_documented_in_hooks_md` and the tier-table tests. The README row, the rewritten known gap and the CHANGELOG entry are read in review.
  - Before `/ready-for-review`, check that the commit message and PR body state each later-found gap's acceptance rationale, as `docs/hooks.md:20` requires. The issue's six need none, because their acceptance is on the issue (row 2). The later-found gaps:
    - the class-level gap (rows 33 and 34);
    - submodule recursion (rows 31 and 45);
    - BSD/macOS awk (rows 30, 64 and 65);
    - the earlier state change, HTTP pushes and the `gh api` merge endpoint (rows 55, 56, 59, 74 and 75);
    - the `gh api` endpoint after a boolean flag, a quoted multi-word flag value or a flag value holding a command substitution (rows 68 and 72-78). Row 78 accepts its quoted multi-word and substitution shapes as permanent debt.
  - The PR body states that the BSD/macOS awk gap affects macOS, the primary platform (row 57).
- The PR body records each new test file's serial wall time against its 60-second ceiling (Critical files, dispatch 3).
- After merge, the engineer pulls, re-stows, and starts a fresh session in a scratch clone on `main`. `git push origin HEAD:main` must be denied, and `git push -u origin HEAD` from a feature branch must pass. The runtime check that `test_self_filtering_bash_gate_has_no_if_matcher`'s docstring calls for is this one.

## Out of scope

- The readiness gate's judgment by the current branch, and its `push_fragment_*` classifiers. #1409 tracks that fix, which consumes this plan's parser contract.
- Server-side branch protection, a per-repo opt-out, and pushes made outside the tool-call path. The issue rules these out.
- Any statement wording change, including the optional "this waives no hook gate" clause (issue Proposal §3).
- Closing the accepted Known gaps: the issue's six, the executed quoted span, submodule recursion, state changed earlier in the command, HTTP pushes and the `gh api` merge endpoint (rows 2, 33, 31, 55, 56 and 74).
- Reading a `gh api` endpoint that an earlier boolean flag, quoted multi-word flag value or substitution-holding flag value hides, a Known gap with no new rule (rows 68 and 76). Its boolean-flag shape is accepted as permanent debt (rows 72 and 74), and so are its other two shapes (row 78).
- A git `pre-push` hook carrier, set aside with the engineer's selection (row 53).
- Widening the moved flag (row 55), and a protected set that unions every remote's HEAD (row 62).
- Fixing `_lib_mask_shell_quotes`' BSD/macOS awk limit or `_lib_split_fragments`' bare `&` (GH-1063). Both are shared helpers whose other callers would need their own review (rows 30, 57, 64 and 65).
- Teaching `_lib_mask_shell_quotes` which quoted spans execute, for the same reason. That fix belongs in its own issue (row 34).
- `deny-invisible-commit-content.sh` and its wrapper-token constant, which this plan does not reuse (row 10).
- `plugins/skill-management/hooks/_lib.sh` (G3), including its copy of `_lib_git_argv_from_subcmd` (row 20), and the announce and compact hooks (issue Proposal §2).
- `docs/hooks.md:227`, which stays accurate (row 29).
- `cleanup-merged-branches.sh`, whose push runs inside a script, the first accepted Known gap.
