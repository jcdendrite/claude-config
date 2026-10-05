# References — review-pr

Not loaded at skill runtime. Consult when editing the skill,
`review-pr-acquire.sh`/`review-pr-diff.sh`, or `audit-execution-surface.py`
to verify a `gh` field name, a truncation caveat, or the execution-surface
match list still holds.

## What `review-pr-acquire.sh` fetches (Step 1)

`gh pr view --json title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup`, plus a second REST call, a paginated existing-reviews fetch, and a paginated inline-review-comments fetch — all inside the one script call, not typed out per-run.

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

- The `author_association` allowlist in `review-pr-checkout.sh` refuses every value outside `MEMBER`, `OWNER`, `COLLABORATOR` and `CONTRIBUTOR`, including any value GitHub adds later. It admits those four, so it does not stop a `CONTRIBUTOR` or a compromised collaborator account. A cross-repository PR, and a PR whose base branch is not the base repository's default branch, are each refused by a separate check in the same script.
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
The checkout script also refuses a PR whose base branch is not the base
repository's default branch, because the audit covers only the changed
files while the checkout writes the whole head tree. Step 1 cannot read
the base branch, so that refusal is learned only from the script's exit 3.
The checkout path stays the default for a trusted PR because it gives the
reviewer full-file context, which the diff alone does not.
What the no-checkout path gives up against it, named explicitly rather than
left implicit:

- **No local checks (Step 6 is skipped entirely).** Nothing lands on disk,
  so there is no tree to run the project's test suite, linter, or build
  against. Any finding that would only surface by running code is missed.
- **A stop-shaped signal becomes a mandatory finding instead.** An
  `audit-execution-surface.py` hit halts `review-pr-checkout.sh` before any
  fetch. On this path, the same hit is reported on stderr as an
  `AUDIT_FINDING` line instead, and must be carried into the Step 5
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

## Why the findings-body declaration uses the Write tool, not Bash (Step 7)

A spawned review-only subagent carries no Write tool for this path, so requiring the Write tool makes the step un-completable from a subagent by construction, rather than by convention.

Passing the fixed path as a Bash argument would also put it in the command's argv, which appears in shell history and the process table. The findings-body path comes from `review-pr-findings-path.sh`, not from a value the model transcribes, so nothing needs independent verification against `$CONFIG_DIR`/`$SESSION_ID`.

Accepted gap: the Write tool does not open with `O_NOFOLLOW`, so a pre-planted symlink at the fixed body path would be followed.

The downstream read path rejects that symlink. `_lib_sha256_no_follow` (`claude/.claude/hooks/_lib.sh`), called from `marker.sh write review-pr`, hashes through an `O_NOFOLLOW` open, so the flow fails closed rather than posting unreviewed content.

The same followed write can also overwrite or create an arbitrary file the Claude Code process has write access to. That risk is wider than the posting-side risk the read-path check mitigates. It rests on the same local-compromise prerequisite as the read-side gap.

Both diff writers, `review-pr-checkout.sh` and `review-pr-diff.sh`, write the `.diff` file through `_lib_write_no_follow`, which refuses a symlink at that path. No script reads `.diff` back: `/code-review` reads it with the agent's `Read`, which follows symlinks, so a symlink planted after the write has no read-side backstop.

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
    contains `claude-config`, while `/review-pr` runs in any repository.
  - The control is the Step 8 human approval of the exact body to post.
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
  - `review-pr-checkout.sh` registers no trap, so a signal between its
    worktree `mktemp -d` and `git worktree add` leaves an empty, unregistered
    directory.
  - `review-pr-finish.sh` does not sweep that directory, because it finds
    worktrees through `git worktree list`, which never lists an unregistered
    directory.
  - `docs/hooks.md`'s "Gate deadlock recovery" section covers only a git lock
    file stranded by a killed process, not these directories.
  - Remove an unregistered `review-pr-<session-id>-<number>-<suffix>`
    directory under `<main tree>/.claude/worktrees/`, or a
    `review-pr-acquire.XXXXXX` directory under `$TMPDIR` (`/tmp` when unset),
    with a plain `rm -r` on that specific directory.
- **`review-pr-finish.sh` leaves a ref and fetched base objects in the
  repository.** `review-pr-checkout.sh` fetches the PR's head into
  `refs/review-pr/pr-<number>`, which keeps that commit and its objects
  reachable until the ref is deleted with
  `git update-ref -d refs/review-pr/pr-<number>`. `review-pr-checkout.sh`'s
  `git fetch origin <baseRefOid>` adds the base commit's objects to the
  shared object store. `review-pr-finish.sh` does not remove them, and an
  ordinary `git gc` expires them once unreachable (default prune expiry is
  2 weeks, per `git-gc(1)`).
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
