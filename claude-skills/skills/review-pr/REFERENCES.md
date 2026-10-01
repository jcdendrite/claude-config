# References — review-pr

Not loaded at skill runtime. Consult when editing the skill,
`review-pr-acquire.sh`/`review-pr-diff.sh`, or `audit-execution-surface.py`
to verify a `gh` field name, a truncation caveat, or the execution-surface
match list still holds.

## What `review-pr-acquire.sh` fetches (Step 1)

`gh pr view --json title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup`, plus a second REST call, a paginated existing-reviews fetch, and a paginated inline-review-comments fetch — all inside the one script call, not typed out per-run.

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
| `.githooks/**`, `.husky/**` | conventional `core.hooksPath` target directory names; a repo commonly points `core.hooksPath` at one of these via setup instructions or a tool (Husky), and git then executes any file placed under it at checkout. `core.hooksPath` itself is local git config, not something a PR's file list carries directly — this is a heuristic over common target-directory names, not an exhaustive read of the actual configured value. |
| `CLAUDE.md` (any path segment) | loaded as standing instructions by the reviewing harness when it works inside the checked-out tree |
| `CLAUDE.local.md` (any path segment) | concatenated into the same standing-instructions load as CLAUDE.md (`claude/.claude/rules/claude-md-conventions.md`'s precedence list) |
| `.claude/settings.json`, `.claude/settings.local.json` | configures hooks and permissions the harness applies |
| `.claude/hooks/**` | runs on every matching tool call the harness makes |
| `.claude/agents/**` | defines subagent behavior the harness may dispatch |
| `.claude/skills/**/SKILL.md` | loaded as skill instructions by the harness's project-level skill discovery |
| `.mcp.json` | registers an MCP server the harness may launch |
| `.gitmodules` | can point a submodule fetch/checkout at attacker-controlled content |

Out of scope, named explicitly rather than left implicit: the operator's
own editor/IDE auto-run configs (`.vscode/tasks.json` with
`runOn: folderOpen`, `.idea/` run configs) are outside the reviewing
harness's control surface, so this audit does not cover them.

Content-blind by design: the predicate takes a path list, not file
bodies, so a hit means "this path could be an execution-surface file,"
not "its content is malicious." Over-flagging is the accepted direction
— see the script's module docstring.

Every match folds case (`.MCP.json` matches the same as `.mcp.json`)
because a case-insensitive filesystem (macOS default, Windows) resolves
both to the same loaded file.

**Trust classification never removes a stop condition, only widens it.**
`authorAssociation` and `isCrossRepository` describe an account's
standing, not the provenance of the commits under review — a compromised
collaborator account still produces a same-repo, non-first-time-looking
PR that this audit must still flag on a hit. A cross-repo or
first-time-contributor PR is refused by `review-pr-checkout.sh`
unconditionally and routes to `review-pr-diff.sh`, which runs the same
audit predicate and reports a hit as a finding instead of stopping.
`review-pr-checkout.sh` enforces its refusal on every invocation, so that
class never becomes unreviewable.

### Git-tracked symlinks are not defended against

`_classify()` matches on path text alone, so it is blind to a git-tracked
symlink — tree-entry mode `120000` in the tree, vs `100644`/`100755` for a
regular file. A tracked symlink checks out as git checks it out, and the
reviewing agent's `Read` tool follows it. The skill does not defend against
a malicious author of an allowlisted PR, because that author is outside the
accepted threat model.

The backstops are the `author_association` allowlist in
`review-pr-checkout.sh` and `deny-credential-file-reads.sh`, which resolves a
`Read` target with `readlink -f` and denies credential-shaped ones. A Bash
`cat` through a link is not covered, since `deny-credential-bash-reads.sh`
matches command text only (`docs/hooks.md`, `docs/security-hardening.md`).
A third, `redact-credential-values.sh`, replaces credential values in a tool
result, but only vendor-fixed shapes (a GitHub token prefix, a full PEM
private-key block), so it does not close the gap.

## The no-checkout path's reduced coverage (`review-pr-diff.sh`, Step 2)

`review-pr-diff.sh` is what a `FIRST_TIME_CONTRIBUTOR`/`NONE`/cross-repo PR
routes to once `review-pr-checkout.sh`'s trust block refuses it
unconditionally — the common case on a public repo, so this path exists
to keep that PR class reviewable rather than permanently locked out.
What it gives up against the checkout path, named explicitly rather than
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
  `changed_files`. No script checks the completeness of either diff's
  text. The 300-file precheck and the truncation caveat apply to this path
  only: in `checkout` mode, Step 5 takes a local three-dot `git diff`.

## Why the findings-body declaration uses the Write tool, not Bash (Step 7)

A spawned review-only subagent carries no Write tool for this path, so requiring the Write tool makes the step un-completable from a subagent by construction, rather than by convention.

Passing the fixed path as a Bash argument would also put it in the process table and shell history, which the sibling-file design exists to avoid. The findings-body path comes from `review-pr-findings-path.sh`, not from a value the model transcribes, so nothing needs independent verification against `$CONFIG_DIR`/`$SESSION_ID`.

Accepted gap: the Write tool does not open with `O_NOFOLLOW`, so a pre-planted symlink at the fixed body path would be followed. The downstream read path's `O_NOFOLLOW`-guarded hash check (`_lib_sha256_no_follow`, `claude/.claude/hooks/_lib.sh`, called from `marker.sh write review-pr`) rejects the symlink, so the flow fails closed rather than posting unreviewed content. A second, broader accepted consequence of the same followed write: it can overwrite or create an arbitrary file the Claude Code process has write access to. That risk is distinct from, and wider than, the posting-side risk the read-path check above fully mitigates, and it rests on the same local-compromise prerequisite already accepted for that read-side gap.

## Known gaps and operator choices

- **Ignore `.claude/worktrees/` in the target repo.** `review-pr-checkout.sh`
  creates each review worktree under `<main tree>/.claude/worktrees/`, so
  that directory should be listed in the target repository's `.gitignore`
  or in a global gitignore.
- **No automatic reaper reclaims an abandoned review worktree.** A review
  worktree is created `--detach`, with no branch, and
  `cleanup-idle-open-pr-worktrees.sh` classifies worktrees by local branch
  name, so a worktree whose session never ran `review-pr-finish.sh` stays
  until someone removes it. A manual sweep, run from inside the repository,
  is
  `find <main tree>/.claude/worktrees -maxdepth 1 -name 'review-pr-*' -mtime +N -exec git worktree remove --force --force {} \;`.
  Widening `cleanup-idle-open-pr-worktrees.sh` to match by directory-name
  pattern would automate it; that is not built.
  While a review worktree remains, `marker.sh` refuses to write any other
  skill's marker from the main tree under worktree enforcement, and skips
  that refusal only for `write review-pr`. Running `review-pr-finish.sh` or
  removing the worktree clears the refusal.
- **A signal-killed run can leave temporary directories behind.**
  `review-pr-acquire.sh` registers one `EXIT` trap that removes its
  `mktemp -d` assembly directory, and that trap is not proven to run when
  the process is killed by a signal. `review-pr-checkout.sh` registers no
  trap: a signal between its worktree `mktemp -d` and `git worktree add`
  leaves an empty, unregistered directory, which `review-pr-finish.sh`
  does not sweep (`docs/scripts.md`'s `review-pr-finish.sh` entry says why).
  `docs/hooks.md`'s "Gate deadlock recovery" section covers only a
  git lock file stranded by a killed process, not these directories.
  Remove an unregistered `review-pr-<session-id>-<number>-<suffix>`
  directory under `<main tree>/.claude/worktrees/`, or a
  `review-pr-acquire.XXXXXX` directory under `$TMPDIR` (`/tmp` when unset),
  with a plain `rm -r` on that specific directory.
- **`review-pr-finish.sh` leaves a ref and fetched base objects in the
  repository.** `review-pr-checkout.sh` fetches the PR's head into
  `refs/review-pr/pr-<number>`, which keeps that commit and its objects
  reachable until the ref is deleted with
  `git update-ref -d refs/review-pr/pr-<number>`. Step 5's
  `git fetch origin <baseRefOid>` adds the base commit's objects to the
  shared object store. `review-pr-finish.sh` does not remove them, and an
  ordinary `git gc` expires them once unreachable (default prune expiry is
  2 weeks, per `git-gc(1)`).
- **The post gate is a cooperative-mistake check.** Only
  `review-pr-post.sh`'s own unreachability of `--approve` is unbypassable:
  its two `gh pr review` calls carry the literal `--comment` and
  `--request-changes`. A direct `gh pr review --approve` from the session is
  gated by `require-respond-pr.sh`, which a live `respond-pr` bypass marker
  releases, and `marker.sh activate respond-pr` is auto-allowed in
  `settings.json`. The post gate otherwise catches
  cooperative mistakes, and the boundary against a steered session is the
  human (Step 8 approval, plus the permission prompt on the
  non-allowlisted `review-pr-post.sh`, whose strength depends on the
  operator's permission mode). An operator who wants no post to be possible
  from the session can run it with a `gh` token that carries no
  pull-request write permission and post the approved body by hand. That
  choice is not built.
