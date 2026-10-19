# References — review-pr

Not loaded at skill runtime. Consult when editing the skill,
`review-pr-acquire.sh`/`review-pr-diff.sh`/`review-pr-checkout.sh`,
`_review-pr-lib.sh`, or `audit-execution-surface.py` to verify a `gh` field
name, a truncation caveat, or the execution-surface match list still holds.

## What `review-pr-acquire.sh` fetches (Step 1)

`gh pr view --json title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,url,files,changedFiles,commits,reviewDecision,mergeable,mergeStateStatus`, plus a separate `gh pr view --json headRefOid,statusCheckRollup` call, a second REST call, a `gh api user` call, a paginated existing-reviews fetch, and a paginated inline-review-comments fetch — all inside the one script call, not typed out per-run.

- **The printed document overwrites `gh pr view`'s `files` and `commits`
  keys with different shapes.** `files` is an array of path strings and
  `commits` an array of commit SHA strings, not `gh`'s own objects, so
  fields such as per-file additions and deletions and each commit's message,
  authors, and dates are dropped. `filesComplete` and `commitsComplete`
  lead the document so a cut at the end of stdout keeps the flags.
- **`authorAssociation` is not a valid `--json` field on `gh pr view`.**
  Including it makes the whole call error rather than degrade gracefully.
  Author association comes from a second call:
  `gh api repos/{owner}/{repo}/pulls/{number}`, whose REST payload exposes
  `author_association` and, as a same-call bonus, the PR's own true
  `commits` integer (see the 100-entry note below).
- **`files` truncates silently at 100 entries, with no `--paginate`
  equivalent on `gh pr view`.** This is a security interaction, not a
  completeness nit: `files` is exactly what step 2's passive-execution
  audit reads (self-fetched independently there, never trusted from this
  step's own read), so a `.mcp.json` at position 101 would be invisible to
  the gate if `files` were trusted alone. The script always requests
  `changedFiles` too, compares it against `files`' length, and on any
  mismatch re-fetches the full list via
  `gh api repos/{owner}/{repo}/pulls/{number}/files --paginate`, which
  paginates correctly.
- **`commits` shares the same 100-entry truncation** as `files`. Reconcile
  it against the REST payload's own `.commits` integer (the true total)
  instead of a `changedCommits`-shaped field, since `gh pr view --json`
  exposes no such count directly.
- **`statusCheckRollup` entries come in two shapes.** A `CheckRun` entry
  (Actions/Checks API) carries `status`/`conclusion`; a `StatusContext`
  entry (the legacy Commit Status API, used by some external CI systems)
  carries `state`. The script passes the entries through raw, reads a null
  value as an empty list, and branches on neither shape.
- **`statusCheckRollup` comes from its own `gh pr view` call.** Under a
  fine-grained PAT, `gh pr view --json statusCheckRollup` fails with
  `GraphQL: Resource not accessible by personal access token
  (repository.pullRequest.statusCheckRollup...)` (observed; the same call
  without that field succeeds). A shared call would therefore abort the whole
  acquire step. A failed or head-mismatched fetch gives `null` with
  `statusCheckRollupAvailable: false`, never an empty list. For a PR with no
  checks, `gh pr view --json headRefOid,statusCheckRollup` exits 0 with
  `"statusCheckRollup": []` (observed), so an absent key is treated as
  unavailable.
- **`selfAuthored` comes from a `gh api user` call on the ambient token.**
  That token is the account `review-pr-post.sh` posts as, so the lookup never
  uses `CI_CHECKS_GH_TOKEN`. A failed lookup yields `null` rather than
  aborting. A PR author that `review_pr_author_login` (`_review-pr-lib.sh`)
  cannot read also yields `null`. `review_pr_authorship`
  (`_review-pr-lib.sh`) owns the comparison.
  `review-pr-post.sh` repeats it for `request-changes` before it consumes the
  marker, so the document's value is advisory.
- **`mergeable` and `mergeStateStatus` are frequently `UNKNOWN`.** GitHub
  computes mergeability asynchronously; a first request routinely returns
  `UNKNOWN` with no signal that a retry would resolve it. The skill never
  branches a stop decision on these two fields — this is guidance for how
  the model reasons about the script's JSON output, not fetch mechanics
  the script itself can enforce, so it stays as prose in SKILL.md.
- **`gh` exit codes are generic.** Distinguishing not-found from
  rate-limited from a network failure means parsing stderr text, which is
  version-fragile across `gh` releases. The script prefers aborting on any
  non-zero exit over branching on a parsed failure cause.
- **The script's worst-case wall time exceeds the Bash tool's default
  timeout.** Its eight capped `gh` calls sum to 160 seconds: the two
  `gh pr view` calls, the REST call, and the `gh api user` call at 10 seconds
  each, and the four paginated calls (files, commits, reviews, inline
  comments) at 30 seconds each. The tool's default timeout is 120000 ms and
  its documented maximum is 600000 ms, so SKILL.md Step 1 sets
  `timeout: 600000` for it.
  The usage text of `review-pr-checkout.sh` and `review-pr-diff.sh` each state a
  "Worst-case wall time" that exceeds the default, so Step 1 sets the same
  timeout for them.

## Execution-surface file list (Step 2 / `audit-execution-surface.py`)

The categories `_classify()` matches, and why each is a passive-execution
vector — fires with no explicit test run, on checkout or on the harness
loading a project directory:

| Pattern | Vector |
|---|---|
| `.gitattributes` (any depth) | git executes clean/smudge filter drivers named in it at checkout |
| `.githooks/**`, `.husky/**` | conventional `core.hooksPath` target directory names; git runs a hook-named file there (such as `post-checkout`) when its event fires |
| `CLAUDE.md` (any path segment) | loaded as standing instructions by the reviewing harness when it works inside the checked-out tree |
| `CLAUDE.local.md` (any path segment) | concatenated into the same standing-instructions load as CLAUDE.md (`claude/.claude/rules/claude-md-conventions.md`'s precedence list) |
| `AGENTS.md` (any path segment) | may load as standing instructions through a CLAUDE.md `@AGENTS.md` import, which resolves relative to the importing file; the predicate flags the name at any depth rather than modelling which CLAUDE.md imports it |
| `.claude/settings.json`, `.claude/settings.local.json` | configures hooks and permissions the harness applies |
| `.claude/hooks/**` | runs on every matching tool call the harness makes |
| `.claude/agents/**` | defines subagent behavior the harness may dispatch |
| `.claude/skills/**/SKILL.md` | loaded as skill instructions by the harness's project-level skill discovery |
| `.claude/rules/**` | may load as standing instructions, unconditionally when a rule has no `paths:` key (`docs/rules-references.md`); flagged under the content-blind over-flagging policy, not a confirmed load from a nested review worktree |
| `.claude/output-styles/**` | documented as loaded from project directories (see the note below), and a selected output style changes the harness's system prompt |
| `.claude/commands/**` | loads as a project slash command, which works like a skill |
| `.mcp.json` | registers an MCP server the harness may launch |
| `.gitmodules` | can point a submodule fetch/checkout at attacker-controlled content |

Notes on the `.githooks/**`, `.husky/**` row:

- A repo commonly points `core.hooksPath` at one of these directories through setup instructions or a tool (Husky).
- `core.hooksPath` is local git config, which a PR's file list does not carry.
- The two directory names are a heuristic over common targets, not an exhaustive read of the configured value.
- `review-pr-checkout.sh` runs its PR-ref `git fetch` and its `git worktree add` with `-c core.hooksPath=/dev/null`, so no hook runs during that checkout whatever the configured path.

Notes on the `.claude/output-styles/**` and `.claude/commands/**` rows:

- The Claude Code output-styles documentation (https://code.claude.com/docs/en/output-styles) describes project output styles as loaded from every `.claude/output-styles/` directory between the working directory and the repository root, so a review worktree's own directory is in that set. That sentence does not say a loaded style is active, and this document has not verified whether selecting one is required.
- The same documentation says `.claude/commands` files work like skills in the project directory.
- Nested `.claude/commands/` discovery is undocumented, so that row is flagged under the same content-blind over-flagging policy as `.claude/rules/**`.

Out of scope, named explicitly rather than left implicit: the operator's
own editor/IDE auto-run configs (`.vscode/tasks.json` with
`runOn: folderOpen`, `.idea/` run configs) are outside the reviewing
harness's control surface, so this audit does not cover them.

Known audit gap: a file reached only through some other `@path` import from
an unchanged CLAUDE.md is not matched, because the predicate reads paths,
not import targets.

The predicate is content-blind and folds case on every match; the
script's module docstring owns both rationales.

**Trust classification never removes a stop condition, only widens it.**
`authorAssociation` and `isCrossRepository` describe an account's
standing, not the provenance of the commits under review — a compromised
collaborator account still produces a same-repo, non-first-time-looking
PR that this audit must still flag on a hit. The no-checkout path's
section below covers the PRs the checkout script refuses outright.

### Git-tracked symlinks are not defended against

`_classify()` matches on path text alone, and path text cannot show a
git-tracked symlink's tree-entry mode (`120000`, vs `100644`/`100755` for a
regular file). A symlink is flagged only when its own path happens to carry a
flagged name, and one at any other path, or one replacing a flagged directory
such as `.claude/commands` (listed with no trailing slash), classifies clean
whatever it points at. A tracked symlink checks out as git checks it out, and
the reviewing agent's `Read` tool follows it.

Checking out with `core.symlinks=false` would flatten every tracked symlink
into a plain text file, and it was not adopted, for two reasons:

- It costs legitimate repositories (inferred, not run): checks or manifests
  that rely on tracked symlinks fail or read a bare path, and the permanent
  ` T` status breaks any clean-tree assertion.
- It holds only until a later `git checkout`, `restore` or `reset` in the
  worktree re-creates the symlinks (a reading of git-config(1)'s
  `core.symlinks`, not run), so it would not deliver the property it was
  adopted for.

The residual is accepted by decision, not by a threat-model exclusion. Both backstops are partial:

- The `author_association` allowlist in `review-pr-checkout.sh` refuses every value outside `MEMBER`, `OWNER`, `COLLABORATOR` and `CONTRIBUTOR`, including any value GitHub adds later. It admits those four, so it does not stop a `CONTRIBUTOR` or a compromised collaborator account. A cross-repository PR is refused by a separate check in the same script. For a PR whose base branch is not the base repository's default branch, the script also audits every path the head changes from the default branch, by the same path predicate.
- `deny-credential-file-reads.sh` resolves a `Read` target with `readlink -f` only when the target's last path component is itself a symlink (`[ -L ]`). A symlinked directory earlier in the path is not resolved, so a `Read` through such a link to a credential file is checked against its raw path text alone.

Two further controls do not close the gap:

- `deny-credential-bash-reads.sh` matches command text only, so a Bash `cat` through a link is not covered (stated in the hook's own header comment).
- `redact-credential-values.sh` replaces credential values in a tool
  result, but only vendor-fixed shapes (a GitHub token prefix, an AWS access
  key ID, a PEM private-key header or full block) plus any patterns an operator
  adds in `credential-value-patterns.md`.

## The no-checkout path's reduced coverage (`review-pr-diff.sh`, Step 2)

`review-pr-diff.sh` is what a `FIRST_TIME_CONTRIBUTOR`/`NONE`/cross-repo PR
routes to once `review-pr-checkout.sh`'s trust block refuses it
unconditionally — the common case on a public repo, so this path exists
to keep that PR class reviewable rather than permanently locked out.
For a PR whose base branch is not the base repository's default branch, the
checkout script audits every path the head changes from the default branch as
well as the PR's changed files, because the checkout writes the whole head tree
and a stacked base can carry files the PR's own file list never names. A
trusted PR whose delta audits clean gets a worktree. Step 1 cannot read an
audit verdict, so a stop is learned only from the script's exit 3.
The checkout path stays the default for a trusted PR because it gives the
reviewer full-file context, which the diff alone does not.
What the no-checkout path gives up against it, named explicitly rather than
left implicit:

- **No local checks (Step 6 is skipped entirely).** The script creates no
  worktree and checks nothing out, so there is no checkout to run the
  project's test suite, linter, or build against. Any finding that would only
  surface by running code is missed.
- **A stop-shaped signal becomes a mandatory finding instead.** An
  `audit-execution-surface.py` hit on the file list halts
  `review-pr-checkout.sh` before it fetches the PR's ref. On this path, the
  same hit is reported on stderr as an `AUDIT_FINDING` line instead, and must
  be carried into the Step 5
  synthesized review as a blocking finding — there is no checkout for a
  hard stop to protect.
- **The diff itself may be truncated on an unusually large PR.** `gh pr
  diff`'s server-side truncation behavior on an unusually large PR is not
  confirmed against production. `review-pr-diff.sh` stops (exit 2) when the
  REST `changed_files` count exceeds 300, a limit third parties report for
  GitHub's diff endpoint and that no GitHub reference confirms, and aborts
  (exit 2) when the paginated file list's length differs from
  `changed_files`. No script checks the completeness of the `gh pr diff`
  text. The 300-file precheck and the truncation caveat apply to this path
  only: in `checkout` mode, `review-pr-checkout.sh` writes a local three-dot `git diff`.
- **The diff text is not verbatim.**
  - `review-pr-diff.sh` does not pass `--allow-escape-sequences`, so `gh pr
    diff` neutralizes terminal escape sequences in the diff it captures
    (`gh pr diff --help`).
  - A changed line holding an escape sequence therefore reads differently
    in diff-only mode than in the repository.
  - The checkout-mode `.diff` keeps the raw terminal-escape bytes of the
    PR's file content and has no byte cap.
  - Diff-only mode is bounded by the 300-file precheck.
  - `review-pr-checkout.sh` holds the checkout-mode diff in a shell
    variable, which drops any NUL byte and normalizes the trailing newline,
    so the `.diff` is not byte-identical to git's output.
- **The checkout-mode diff follows some local git config.**
  `review-pr-checkout.sh` pins submodule visibility, path prefixes, and
  context size on the command line, so `diff.ignoreSubmodules`,
  `diff.noprefix`, `diff.mnemonicPrefix`, and `diff.context` do not change
  the artifact. It also unsets `GIT_DIFF_OPTS`, which would otherwise
  override the context size. Rename and copy detection (`diff.renames`), the diff
  algorithm, file ordering, and the submodule display format still follow
  the operator's git configuration.
  Per gitattributes(5), the main tree's attributes
  (`.gitattributes`, `.git/info/attributes`, and `core.attributesFile`)
  also decide rendering, because the script runs the diff there even when
  invoked from a review worktree. A path they mark `-diff` or `binary`
  appears as "Binary files ... differ"; SKILL.md Step 5 says what the
  reviewer does with such a path.
- **No head content beyond the diff's hunks.** SKILL.md Step 5 makes the diff
  file the only route to the PR's file contents, and the only head lines that
  file carries are each hunk's added and context lines. A finding about
  unchanged lines of a changed file therefore rests on hunk context alone.
  Base content is readable (see the base-reads section below).
- **The base commit can be unavailable.** `review-pr-diff.sh` fetches the
  base commit and prints it as a full-hex line only when the fetch succeeded
  and the commit is present locally. Otherwise it writes
  `review-pr-diff.sh: BASE_UNAVAILABLE <token>` on stderr, with the token
  `missing`, `not-hex`, `fetch-failed`, or `fetch-timed-out`, and still writes
  the diff and provenance and exits 0. An operator whose `gh` works while git
  over SSH does not would otherwise lose the whole diff-only path. Git's own
  output is never echoed, because it can carry remote URLs into a posted body.
  A missing `headRefOid` still exits 2.
- **Stdout carries a fixed token when the base is the default branch.**
  - The usage text of `review-pr-checkout.sh` and `review-pr-diff.sh` states
    which line each prints the token on and when it prints it.
  - The session matches the line's text on the script's own stdout, so a diff
    or PR body that contains the text cannot supply it.
  - `review-pr-checkout.sh` takes the base branch name from the REST payload.
    It requires `gh pr view`'s `baseRefName` to equal that name beside
    `baseRefOid` and again after the file list, and exits 2 on a mismatch, so a
    one-way retarget between the reads cannot skip the stacked delta audit. The
    attacker left in that window is the one the "Known gaps and operator
    choices" section accepts.
  - `review-pr-diff.sh` reads the base branch name from the same `gh pr view`
    response as `baseRefOid`, and only the default branch name from the REST
    payload. No later read that a PR retarget can change feeds the token, so it
    has no recheck after the file list. The diff file is not bound to the base
    the token describes: `gh pr diff` renders against the base the PR has when
    it runs. That GitHub serves the two fields of one response
    consistently is unverified, and `review-pr-checkout.sh` relies on the same.
  - A token printed in error lets a non-default branch's content count as
    settled code, because SKILL.md makes the token the only gate for that. A
    token missing in error only leaves claims open.

## Base reads by object ID (Step 5)

SKILL.md Step 5 reads base content with `git show '<baseRefOid>:<path>'` and
`git --literal-pathspecs grep -n -F -e '<literal>' <baseRefOid> -- '<path>'`.

- **Neither command runs a configured driver.** `git show` prints a blob's
  plain contents and `git grep` applies no textconv by default (git-show(1),
  git-grep(1); read from the manual, not run).
- **Single quotes keep a PR-derived string literal.** Inside them the shell
  treats every character literally, and a single quote cannot occur inside them
  (POSIX Shell Command Language, quoting; recalled, not reopened). SKILL.md
  Step 5's "Base reads" paragraph defines which strings never reach Bash.
- **`--literal-pathspecs` and `-F` make the PR-derived path and `<literal>`
  match verbatim.** Without `--literal-pathspecs`, `git grep` treats the path as a
  pathspec, so glob characters or a leading `:` select other files
  (gitglossary(7), "pathspec"). `-F` makes the `<literal>` a fixed string.
  `git show <oid>:<path>` names one tree entry and needs neither.
- **The scripts fetch the base commit, not the session.** A fetch the session
  writes copies a model-transcribed object ID with no hex validation, no
  `core.hooksPath` pin, and no time cap. The scripts validate the ID with
  `review_pr_is_full_hex_oid`, disable hooks, and cap the fetch. The cap holds
  only when `timeout` or `gtimeout` is on `PATH`.
- **Every network `git fetch` in `review-pr-checkout.sh` and `review-pr-diff.sh`
  runs with `GIT_TERMINAL_PROMPT=0`.**
  Git's own prompts, such as an HTTP(S) credential request, then fail the fetch
  instead of waiting for terminal input. An ssh passphrase or host-key prompt
  comes from ssh itself, so it is not suppressed. An ssh origin can still wait
  until the fetch time cap. `GIT_ASKPASS` and `SSH_ASKPASS` stay untouched, so
  a configured GUI askpass helper still works.
- **Stacked-base content is not precedent.** SKILL.md Step 5's "Base reads"
  paragraph states the rule. A stacked base is unmerged and may be written by
  the PR's author. The session cannot tell it from a base the script could not
  verify, so without the token line it treats the base as unsettled. The
  session works out nothing about the base itself: the token line is its only
  signal.
- **Per-file `gh api repos/<owner>/<repo>/contents/<path>?ref=<oid>` was not
  adopted.** It costs one API call per file, returns base64, and cannot grep
  across the tree.
- **A base worktree for diff-only PRs was not adopted.** A diff-only PR's
  head is never checked out, because the audit exists to keep PR-supplied
  `CLAUDE.md`, settings, and hooks out of any tree the harness reads. For the
  base, object-ID reads give the same read access without a worktree to create,
  name, clean up, or audit. The cost is that each read is a Bash call that may
  prompt.
- **Two fixed-shape alternatives are heavier and were not adopted.**
  - A `review-pr-base-read.sh <path>` script: the path still reaches the
    script's argv through a model-written command line, so the quoting exposure
    moves instead of closing.
  - A request file written with the Write tool and read by `git cat-file
    --batch` or `git grep -F -f`: this closes the exposure, but costs a Write
    and a scratch-file convention per read.
- **Residual.** The quoting rule is prose. A command that double-quotes a name
  would expand `$(...)`. The harness shows the full command only in a
  permission mode that prompts, not under an allowlist or auto mode.
- **Numeric and hex placeholders are validated by instruction.** SKILL.md
  Steps 5 and 7 state the digits-only and full-hex rules and the CI-log
  control-character strip. The model transcribes each placeholder from a
  document it read, so nothing mechanical checks the transcription. The human
  approves the displayed body.

## Step 7's controls

- **Protected findings.** SKILL.md Step 7 defines the rule. The mark must exist
  before the passes that could lower it, because tiering is the list's fifth
  item. Without the rule, a pre-existing check could drop a path the audit
  flagged because that path also exists at the base, and the consult would never
  see the finding.
- **Claims about code close only on code evidence.** SKILL.md Step 7, item 3
  states the rule. CI-log text, ticket text, and assertions in
  PR-authored text are written or editable by the PR's author, which is why
  they never close or downgrade a claim about code. "Verified" means observed
  code behavior in the diff file, base objects, or worktree. Step 8 prints the first 20 `AUDIT_FINDING` matched
  paths, each cut at 80 characters, so a human can see an omission among them.
  A later match is not listed there.
- **Per-finding `Edit`s are the record of the consult.** The transcript then
  holds the pre-consult `Write`, the consult dispatch, and every change as an
  old/new pair. Two weaker records fail: cuts recalled from memory leave nothing
  tying each cut to a visible change, and an annotation-only consult cannot
  correct a body.
- **The `Read` after the `Write` is deliberate.** `Edit` fails unless the file
  was read in the conversation (quoted in `docs/case-studies/hashline-edit-format.md`).
  Whether the session's own `Write` counts as that read is unverified, so the
  skill reads the body explicitly.
- **Up to three consults run before step 8 presents the body, and the
  session runs none after.** A consult after every round of edits can loop when each consult
  proposes another change the session can verify, so the third consult is
  final. Its verified corrections are applied, so the human never approves a
  body missing a correction the session already verified. A change after
  step 8 presents the body gets no consult, so no consult can reverse a
  change the human asked for. Such a change also voids any earlier approval,
  so the human approves the exact bytes that post. Once the third consult
  has run or step 8 has presented the body, a fix that clears a
  `marker.sh write` refusal is the only change the session may make on its
  own besides the third consult's corrections, because without it a refused
  write would leave the body unpostable.
  The protected-finding rule binds that fix too. A re-check or consult the
  human asks for after step 8 presents only proposes changes, which keeps
  the refusal fix the session's only change of its own.
- **Residual.** The record is the session's own transcript, and every edit is
  session-authored, because `plan-architect` holds no write tool. Nothing
  mechanical detects a finding missing from the body. `marker.sh`'s body check
  and `review-pr-post.sh`'s hash check prove only that the posted text equals
  the marked text. The human's approval of the exact body is the backstop,
  helped by step 8's listing of dropped claims and `AUDIT_FINDING` paths. For
  any `Edit` made after the last consult, the human's reading of the presented
  text is the only content review. `marker.sh`'s attribution check and
  credential scan check the body's shape, not its content, so step 8 presents
  a fresh `Read` of the file and shows each such `Edit` as its verbatim old and
  new strings.
- **`plan-architect`, `comment-discipline-reviewer`, and `skill-fidelity-reviewer`
  get no Bash.** These three hold no Bash, so
  the main session resolves the claims they mark as unchecked.

## Why the findings-body declaration uses the Write tool, not Bash (Step 7)

A spawned review-only subagent carries no Write tool for this path, so requiring the Write tool makes the step un-completable from a subagent by construction, rather than by convention.

Passing the fixed path as a Bash argument would also put it in the command's argv, which appears in shell history and the process table. The findings-body path comes from `review-pr-findings-path.sh`, not from a value the model transcribes, so nothing needs independent verification against `$CONFIG_DIR`/`$SESSION_ID`.

## Why spawned reviewers return findings inline

`/code-review` and `/plan-review` give each reviewer a relative `findings_path` under `agent-reviews/`, which resolves in the tree the session stands in.

A `/review-pr` session may stand in the main tree, where `require-worktree-for-file-writes.sh` denies that `Write` in a repo with worktree discipline on.

A denied reviewer is meant to fall back to inline output, but a reviewer can report a write that never happened, and its findings are then lost.

With no `findings_path`, no reviewer writes, and no findings file lands in a review worktree.

The cost is each reviewer's 2K-token inline budget, a larger session context, and no by-path citation of a findings file in the step-7 consult. It applies to every `/review-pr` session, including one where the file write would have succeeded.

Two other locations fail here:

- A path under `/tmp` passes both write gates, but `review-pr-finish.sh` does not remove it, so a PR's findings outlive the session. A world-readable directory is also open to a symlink planted in advance.
- A path beside the session's other `<config-dir>/.review-pr-active.d/` files is denied, because `deny-reviewer-tree-mutation.sh` allows a reviewer `Write` only under `/tmp` or an `agent-reviews/` directory.

## Known gaps and operator choices

- **The stop-message escaper was run on jq 1.7 only.** On another jq version a
  document the filter cannot format makes `review_pr_audit_match_report` print
  its fixed count line instead of the matches.
- **Ignore `.claude/worktrees/` in the target repo.** `review-pr-checkout.sh`
  creates each review worktree under `<main tree>/.claude/worktrees/`, so
  that directory should be listed in the target repository's `.gitignore`
  or in a global gitignore.
- **No automatic reaper reclaims an abandoned review worktree.**
  - A review worktree is created `--detach`, with no branch, and
    `cleanup-idle-open-pr-worktrees.sh` classifies worktrees by local branch
    name.
  - A worktree whose session never ran `review-pr-finish.sh` therefore stays
    until someone removes it.
  - To remove one, run `git worktree list`, choose an abandoned `review-pr-*`
    entry, and run `git worktree remove <path>` without `--force`.
  - Without `--force`, git refuses a locked worktree and one with modified
    tracked files or untracked files.
  - `claude/.claude/scripts/worktree-removal-status.sh` reports whether an
    unforced remove would succeed and whether a live process works inside
    the worktree.
  - No script automates this. Widening `cleanup-idle-open-pr-worktrees.sh` to
    match review worktrees would.
  - A leftover review worktree does not trigger `marker.sh`'s main-tree
    refusal or `nudge-worktree-anchor.sh`.
  - `_lib_first_live_linked_worktree` (`claude/.claude/hooks/_lib.sh`)
    defines which worktrees that covers.
- **Abandoned-session artifacts accumulate until `marker.sh clear-stale` runs.**
  - A session that ends without `review-pr-finish.sh` leaves its `.diff`,
    `.context.json`, `.body`, and `.provenance` files under
    `<config-dir>/.review-pr-active.d/`.
  - The `.diff` and `.context.json` files are sized by the PR.
  - Nothing runs `marker.sh clear-stale` automatically, and `settings.json`
    does not allowlist it, so it prompts.
  - These files gate nothing, so no gate refusal sends the operator to that
    command.
  - `marker.sh clear-stale --dry-run` lists what the sweep would evict, and
    `marker.sh clear-stale` evicts it.
  - The sweep evicts a session's artifacts when the PID in its `.provenance`
    file is dead and no live session file names the session id.
  - A missing `.provenance` file, or one with no `pid=` line, counts as dead
    and evicts.
  - A `.provenance` file whose first line is not `schema=1`, including an
    empty one, is kept indefinitely.
  - Remove that file by hand, and the next sweep then evicts the session's
    other artifacts.
- **The standing override's no-marker clause is prose only.** Nothing
  mechanical stops a completion-marker write for a skill other than
  `review-pr` while a review-pr session is in flight, because the main-tree
  refusal skips review worktrees.
- **The Step 6 confirmation is prose only.**
  - Step 6 is the one step that runs the PR's code with the operator's
    environment.
  - Its stop for confirmation is an instruction in SKILL.md, not a mechanism.
  - An exact `permissions.allow` entry for the discovered command lets the
    call run with no harness prompt, so only the model's own pause remains.
- **The findings-body scan checks credential shapes only.**
  - It does not check for home-rooted paths, hostnames, IP addresses, tracker
    IDs, or entries in the operator's private-projects blocklist.
  - `deny-private-project-refs.sh` does not gate `gh pr review`, and it
    never sees the `gh` call inside `review-pr-post.sh`.
  - Its redaction policy also short-circuits unless the clone's `origin.url`
    contains `claude-config` or the clone commits
    `.claude/private-projects-optin`, while `/review-pr` runs in any repository.
  - The control is the Step 8 human approval of the exact body to post.
- **The ticket read in Step 4 trusts the engineer's confirmation.**
  - The session guesses the ticket from PR-authored text, and the PR author
    controls that text.
  - The engineer's answer to the `AskUserQuestion` is the only binding on which
    ticket is read.
  - A confirmation given on reflex weakens that control.
  - Every tracker gets the same confirmation, because a connected tool shows
    what the operator can read, not which tracker the repo uses.
  - The `gh issue view` read is pinned by `-R` to `origin`'s repo. The lookup
    for any other tracker, including a GitHub repo other than `origin`'s, has
    no equivalent pin and trusts the operator's connector to point at the right
    project's workspace.
  - Ticket text can reach the findings body, and its tracker's audience can be
    narrower than the PR's. Step 8 names each body finding that rests on ticket
    text, so the human approves the exact body knowing which text came from the
    ticket.
  - The lookup is matched by role, never by tool name, per
    `linear-formatting/SKILL.md`.
- **Symlinks at the session-scoped paths are not refused.**
  - Neither the Write tool nor the review-pr scripts refuse a symlink at
    `.body`, `.diff`, `.context.json`, `.provenance`, or the completion
    marker.
  - Planting one takes same-user write access to the config directory.
  - Any process with that access can already write this skill's own state.
  - A followed write can overwrite or create any file the Claude Code process
    can write.
  - `/code-review` reads `.diff` with `Read`, which follows symlinks too.
- **`review-pr-finish.sh` does not check the caller's working directory.**
  - It removes every review worktree of the session, including one the
    caller's Bash working directory is inside.
  - Run it from the main tree or from a worktree that is not a review
    worktree.
  - Whether the harness recovers a later Bash call whose working directory
    was removed is unverified.
- **A signal-killed run can leave temporary directories behind.**
  - `review-pr-acquire.sh` registers one `EXIT` trap that removes its
    `mktemp -d` assembly directory.
  - That trap is not proven to run when a signal kills the process.
  - `review-pr-checkout.sh` registers one `EXIT` trap only on the stacked-base
    path, to remove the `mktemp -d` directory (`review-pr-checkout.XXXXXX`
    under `$TMPDIR`) holding its name listing, and that trap is not proven to
    run on a signal either.
  - No trap covers the worktree directory, so a signal between its
    `mktemp -d` and `git worktree add` leaves an empty, unregistered
    directory.
  - `review-pr-finish.sh` does not sweep that directory, because it finds
    worktrees through `git worktree list`, which never lists an unregistered
    directory.
  - `docs/hooks.md`'s "Gate deadlock recovery" section covers only a git lock
    file stranded by a killed process, not these directories.
  - Remove an unregistered `review-pr-<session-id>-<number>-<suffix>`
    directory under `<main tree>/.claude/worktrees/`, or a
    `review-pr-acquire.XXXXXX` or `review-pr-checkout.XXXXXX` directory under
    `$TMPDIR` (`/tmp` when unset), with a plain `rm -r` on that specific
    directory.
- **`review-pr-finish.sh` leaves refs and fetched base objects in the
  repository.**
  - `review-pr-checkout.sh` fetches the PR's head into
    `refs/review-pr/pr-<number>`, which keeps that commit and its objects
    reachable until the ref is deleted with
    `git update-ref -d refs/review-pr/pr-<number>`.
  - For a PR whose base branch is not the default branch, it also fetches the
    default branch into `refs/review-pr/pr-<number>-default`, deleted the same
    way. Both refs stay behind, including after a stacked delta stop.
  - Those two ref fetches pass `--no-tags`, `--no-write-fetch-head` and an
    empty `--refmap=`, so they leave no tag, no `FETCH_HEAD` and no
    remote-tracking ref update behind.
  - Both scripts' fetch of `<baseRefOid>` adds the base commit's objects to
    the shared object store.
    `review-pr-finish.sh` does not remove them, and an ordinary `git gc`
    expires them once unreachable (default prune expiry is 2 weeks, per
    `git-gc(1)`).
  - Each of these fetches is capped only when `timeout` or `gtimeout` is on
    `PATH`.
- **The stacked-base delta is a three-dot diff.** A file in the head that sits
  in default-branch history but was removed at the default tip is not listed
  by `<default>...<head>`. A two-dot `<default-oid>..<head-oid>` diff would
  list it and was not adopted. A default-base PR carries the identical
  residual in its own file list, so a stacked PR gains no new exposure.
- **A stacked PR's exit 2 has no automatic fallback.** A failed default-branch
  fetch, a failed or capped name listing, a count mismatch, and an audit with
  no verdict all exit 2, and exit-2 messages never name `review-pr-diff.sh`.
  SKILL.md Step 2 tells the human they may re-run the script or ask for the
  diff-only path.
- **The NUL-split name decode depends on the `jq` version.** README.md's
  `jq` version bullet names the verified version. A jq that
  truncates at a NUL byte fails at the decode ("unterminated final name"), so
  every stacked PR with a non-empty delta exits 2 on it. An empty delta still
  passes.
- **A retarget away from the default branch and back skips the stacked delta
  audit.**
  - It must land while the paginated file list is being read, so all three
    base-name reads stay equal and the file list can describe another base.
  - Closing it needs an audit keyed only on object IDs, such as an
    unconditional `DEFAULT_OID...FETCHED_SHA` audit.
  - That audit was not adopted. The attacker is a same-repo author with write
    access, who already has the quieter bypass in the "Git-tracked symlinks are not defended against" section above.
  - An unconditional audit would also add a default-branch fetch and the NUL
    decode dependence to every PR.
  - The paginated listing must still match `changed_files` in length
    (`review_pr_file_count_matches`). A skipped audit lets flagged
    execution-surface paths that the head shares with the stacked base reach
    the checkout unaudited.
- **The post gate is a cooperative-mistake check.** The checks `review-pr-post.sh`
  makes are listed in its usage text.
  - `review-pr-post.sh` consumes the completion marker with `unlink(1)`, an
    external requirement: a host without it fails closed, refusing before any
    post.
  - A post whose outcome is unknown is not retried from session state:
    `review-pr-finish.sh` removes the provenance and findings body a new
    completion marker needs. Until `finish` runs, only SKILL.md's instruction
    stops a re-arm; no mechanism does. Recovery is a hand-post of the approved
    body or a fresh `/review-pr`.
  - Both `request-changes` refusals, own-PR and could-not-tell, run before
    the marker is consumed, so it stays armed. The could-not-tell refusal
    also covers an unreadable PR author. Both refusals carry the phrase
    "Nothing was posted and the completion marker was not consumed", which
    SKILL.md keys on. SKILL.md Step 8 owns the recovery for each.
  - `selfAuthored` is advisory, so any body can end up posted as `comment`,
    including after either refusal. Changing the verb never edits the body,
    which is why SKILL.md Step 7's blocking-count line does not depend on who
    authored the PR.
  - `review-pr-post.sh` never emits `--approve`: its two `gh pr review` calls
    carry the literal `--comment` and `--request-changes`. This is the only
    unbypassable property of the gate.
  - A direct `gh pr review --approve` from the session is gated by
    `require-respond-pr.sh`.
  - A live `respond-pr` bypass marker releases that gate, and
    `marker.sh activate respond-pr` is auto-allowed in `settings.json`.
  - Otherwise the post gate catches cooperative mistakes. The boundary
    against a steered session is the human: Step 8 approval, plus the
    permission prompt on the non-allowlisted `review-pr-post.sh`, whose
    strength depends on the operator's permission mode.
  - An operator who wants no post to be possible from the session can run it
    with a `gh` token that carries no pull-request write permission and post
    the approved body by hand. The skill ships no setting for this.
