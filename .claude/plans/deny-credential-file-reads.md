# Close the Bash-based credential-exposure gap

## Context

Claude's Bash tool has no mechanical gate against reading credential
files, so a command Claude itself issues can pull a live secret value
into the conversation transcript with nothing but a prose CLAUDE.md
instruction standing in the way. This plan is motivated by an incident
in which a per-account GitHub fine-grained PAT, stored in a local
per-account git-token file, was read via a Bash command Claude issued,
and its raw value was echoed into the transcript. (Revoking/rotating
that specific token is the user's own account-side action, outside
this repo and out of scope here.) The two existing credential/data
guard hooks (`deny-env-reads.sh`, `deny-data-file-reads.sh`) both gate
only the `Read` tool and say so explicitly in their own header
comments — `deny-env-reads.sh`: "Scope: Read tool only. Bash(cat
.env.*) is out of scope by design"; `docs/security-hardening.md`
"Limitations": "Bash-based reads (cat, head, grep)... do not cross
that boundary." The incident is exactly that documented gap firing.
The intended outcome is a mechanical, always-on backstop — installed
via this repo's normal stow distribution, so it applies for every
stow user in every repo, not just this one — that closes the Bash gap
and adds a second, path-independent layer that catches a credential
*value* by its own shape wherever it surfaces, rather than trying to
enumerate every path a secret could ever live at.

## Approach

**Root problem:** Claude's Bash tool (and any tool whose output isn't
path-gated) can pull a live credential value into the conversation
transcript, with no mechanical backstop today — only a prose CLAUDE.md
instruction and an unenforced convention of using a human-typed `!`
shell escape for inspection, which the triggering incident did not go
through.

**Assumption ledger**

| # | Row | Tag |
|---|---|---|
| 1 | Existing hooks (`deny-env-reads.sh`, `deny-data-file-reads.sh`) gate `Read` only; both hooks' own header comments state Bash-based access is out of scope by design. | `[verified: claude/.claude/hooks/deny-env-reads.sh, claude/.claude/hooks/deny-data-file-reads.sh]` |
| 2 | `docs/security-hardening.md`'s "Limitations" section independently confirms the same gap for the data-file hook. | `[verified: docs/security-hardening.md]` |
| 3 | Native `permissions.deny` in `settings.json` already blocks `Read` of `**/.env*` and `**/credentials.json` at the harness level, before any hook script runs — this is a stronger, non-hook-bypassable primitive for the two patterns it covers and should stay as-is; new coverage is additive for path shapes it doesn't include (SSH keys, cloud credential stores, `.netrc`, `.git-credentials`, generic per-account token files). | `[verified: claude/.claude/settings.json]` |
| 4 | A `PostToolUse` hook can replace a tool's entire result via `hookSpecificOutput.updatedToolOutput` (a string) before the model's next turn consumes it — the mechanism a credential-*value* redaction layer needs, run after the tool executes but still able to substitute what the model actually sees. | `[verified: code.claude.com/docs/en/hooks, fetched this session]` |
| 5 | GitHub's own docs define fixed literal prefixes per token type: `ghp_` (classic PAT), `github_pat_` (fine-grained PAT), `gho_`/`ghu_`/`ghs_`/`ghr_` (OAuth/App tokens) — grounds the redaction hook's built-in GitHub-token regex. | `[verified: docs.github.com "About authentication to GitHub" — token formats, fetched this session]` |
| 6 | The exact JSON substructure of `tool_result` for a `PostToolUse` Bash/Read/WebFetch call (e.g. whether stdout/stderr are separate sub-fields) was not confirmed against a live payload during this planning session. | `[unverified]` — implementation must dump a real `PostToolUse` invocation's stdin JSON before finalizing the parsing code. |
| 7 | Two structurally different techniques for scanning Bash-adjacent text for a pattern already coexist in this repo: full shlex tokenization (`parse-git-command.py`, built for stateful cwd-tracking) and plain `grep -E` over raw text (`deny-pii-in-commits.sh`, for stateless pattern presence). | `[verified: claude/.claude/hooks/parse-git-command.py, claude/.claude/hooks/deny-pii-in-commits.sh]` |
| 8 | This repo's established deny-message convention never echoes a matched *secret value* back into a deny/redact message (only harmless information the model already had, like a file path from its own tool call) — see `deny-pii-in-commits.sh`'s explicit stated practice. | `[engineer-verified: repo convention]` |
| 9 | `_lib.sh`'s `_lib_jq` 5-second timeout backstop covers the JSON-parsing step of every new hook via the shared bootstrap; it does not need to extend to the `grep -E` match calls in Mechanisms A/B, since those operate on already-extracted, in-memory strings (`tool_input.command`, `tool_input.file_path`) with no filesystem or network I/O — unlike `_lib_capped`'s filesystem-facing callers (NFS `git`, `sha256sum`). | `[verified: claude/.claude/hooks/_lib.sh]` |

**Mechanism A — `deny-credential-bash-reads.sh` (new, `PreToolUse`/`Bash`, always-on).**
Denies whenever the raw command contains a credential-path token from
the shared `_LIB_CREDENTIAL_PATH_REGEX`, with no other condition and
no bypass valve — matching `deny-data-file-reads.sh`'s stance:
credentials, like PHI, are non-negotiable once flagged.

**Why no content-exposing-verb carve-out:** a design requiring a
content-exposing verb (`cat`, `head`, `grep`, etc.) from a fixed list
alongside the path-token match — so `ssh-add ~/.ssh/id_rsa`, `chmod
600 ~/.ssh/id_rsa`, and `ssh -i ~/.ssh/id_rsa host` (commands that
reference the path without exposing content) would pass through —
was considered and rejected. A fixed verb list is fundamentally
unenumerable (`vim`, `tee`, `dd`, `openssl`, `cp ... /dev/stdout`,
`curl --upload-file`, `scp`, `rsync`, and countless one-liners all
expose content via a verb absent from any bounded list), so that
narrowing would trade a small, bounded false-positive cost for an
unbounded false-negative bypass surface — the identical evasion class
this plan's own lighter-primitives analysis already rejects
`permissions.deny` glob rules for (see below). A closed enumeration
cannot close an open-ended set, so this mechanism matches on the
path token alone with no verb condition. The false-positive cost
(`ssh-add`/`chmod`/`ssh -i` also denied) is accepted explicitly, the
same way `deny-data-file-reads.sh` accepts false positives on
legitimately-named non-PHI files sharing a flagged extension — the
human's `!` shell escape (already the prescribed valve for Claude-side
credential-file inspection per this repo's CLAUDE.md) remains
available for these specific legitimate commands.

**Path-token matching is basename-based, not path-qualified**, and
this is a deliberate, documented trade-off: `_LIB_CREDENTIAL_PATH_REGEX`
matches a bare token like `id_rsa` or `credentials.json` anywhere in
the command, not only when directory-qualified. This closes the
`cd ~/.ssh && cat id_rsa` bypass (the bare token still matches) at the
cost of a documented residual false positive: `grep "id_rsa" .`
searching for that literal *string* in file contents (not opening a
file by that name) also matches, since the hook cannot distinguish a
filename argument from a search-pattern argument without full shell
semantics. This residual is accepted and pinned by a test (see
Critical files) rather than solved — matching this repo's existing
"tripwire, not airtight" posture for hook gates.

**Documented residual — symlink/rename bypass.** A command that first
creates a symlink or copy under a non-credential-shaped name
(`ln -s ~/.ssh/id_rsa notes.txt && cat notes.txt`) does not contain
the credential-path token in the command Claude issues, and Mechanism
A — a raw-text match with no filesystem resolution — cannot catch it.
This is the same class of residual as the variable-indirection gap
already accepted below (the hook sees only literal text in the
command string, not what a path ultimately resolves to), pinned as a
known gap rather than solved, consistent with this repo's existing
disclosure convention for hook limitations.

**Accepted gap — behavioral, not hook-logic.** Denying legitimate
non-exposing commands (`ssh-add`, `chmod`, `ssh -i`) with no in-band
alternative inside Claude's own tool call creates repeated friction
that could push Claude toward one of the residuals just above
(variable indirection, symlink/rename) as the path of least resistance
to finish a task, rather than surfacing the block to the human. No
hook-side change closes this — it's a prompt-level concern. Update
CLAUDE.md's existing "give the user a shell command to run via `!`"
convention (currently scoped to *inspecting* credential files) to
also cover this denial class explicitly: on a Mechanism A deny, name
the exact blocked command to the human for a `!` retry rather than
attempting an alternate construction of the same operation. This is a
named, accepted gap, not a test-pinnable one.
Anchors: row 2, row 7, row 9.
Lighter primitives considered and rejected:
1. `permissions.deny` Bash glob rules (e.g.
   `"Bash(cat ~/.config/git/token-*)"`) — rejected: matches only the
   literal command shapes enumerated; trivially evaded by swapping
   `cat` for `head`/`less`, varying quoting, or wrapping in `$(...)`.
   No semantic understanding, false confidence for a high-severity
   control.
2. Full shlex tokenization mirroring `parse-git-command.py` —
   rejected as unnecessarily heavy: tokenization buys no additional
   evasion-resistance over `grep -E` on raw text for this check, since
   variable indirection (`f=~/.config/git/token-x; cat "$f"`) defeats
   both approaches equally — `parse-git-command.py` itself treats a
   `$VAR` target as unresolvable rather than resolving it. The extra
   complexity has no payoff here, unlike the git-write case, which
   genuinely needs stateful cwd tracking.
Chosen: `grep -E` over raw command text, mirroring
`deny-pii-in-commits.sh`'s own established technique for the same
class of problem (stateless text-pattern detection over shell-adjacent
content).

**Mechanism B — `deny-credential-file-reads.sh` (new, `PreToolUse`/`Read`, always-on).**
Same shared regex constant, applied to `tool_input.file_path`. Covers
credential path shapes with no legitimate secret-free variant: SSH
private keys (`id_rsa`, `id_ecdsa`, `id_ed25519`, `id_dsa`, no `.pub`
suffix), `.netrc`/`_netrc`, `.git-credentials`, `~/.aws/credentials`,
`~/.docker/config.json`, `~/.kube/config`, `~/.config/gh/hosts.yml`,
plus `.env*`/`credentials.json` again for consistency (redundant with
the native `permissions.deny` rules, but at a different enforcement
layer — see row 3). Resolves symlinks via `readlink -f` before
matching, fail-closed on an unresolvable or missing target — the same
defense `deny-env-reads.sh` already applies for its own allowlist, so
a symlink named innocuously but pointing at `~/.aws/credentials` or
`.netrc` doesn't pass the raw-path regex and reach Read.
Anchors: row 3.
Lighter primitive considered: extend `deny-env-reads.sh`'s existing
`case` allowlist-with-symlink-defense logic in place, instead of a new
file — rejected because that hook's allowlist/symlink-defense design
exists specifically for `.env.example`-style safe templates, a concept
that doesn't apply to SSH keys or cloud credential files (no
legitimate secret-free "template" variant of an SSH private key).
Folding both policies into one file would conflate an
allowlist-bearing gate with a zero-allowlist gate for no benefit.
Chosen: a new sibling hook file, matching this repo's existing
one-hook-per-policy granularity.

**Mechanism C — `redact-credential-values.sh` (new, `PostToolUse`, always-on, scoped to `Bash`/`Read`/`WebFetch`/`Grep`/`Task`).**
Scans `tool_result` text for credential-*value* shapes (GitHub token
prefixes per row 5; a PEM private-key block header,
`-----BEGIN...PRIVATE KEY-----`) using a shared regex constant, and
replaces matches with `[REDACTED-CREDENTIAL]` via `updatedToolOutput`
— the rest of the output passes through untouched.
Anchors: row 4, row 5.
Lighter primitives considered:
1. Rely solely on Mechanisms A+B (path-based blocking) — rejected: a
   credential can enter context through a path neither mechanism
   anticipates (a `WebFetch` response body, a `Grep` match inside an
   unexpected file, subagent-returned text). Path enumeration can
   never be exhaustive; value-shape detection is the only layer that
   doesn't depend on guessing every possible location.
2. A `PreToolUse`/`permissions.deny`-based approach generally — not
   applicable: the secret value doesn't exist in `tool_input` before
   the tool runs, so only a post-execution mechanism can inspect it.
Matcher includes `Grep` and `Task` alongside `Bash`/`Read`/`WebFetch`
— the plan's own rejected-alternative #1 names a `Grep` match and
subagent-returned text as vectors this mechanism exists to catch, so
the matcher must cover them or the stated justification doesn't hold.
This is still narrower than a wildcard "all tools" — the five tools
most likely to pull external or file content into context — with
broadening to all tools a documented future option once proven stable.

**Size cap.** Before regex-scanning, `redact-credential-values.sh`
checks the byte length of the extracted `tool_result` text against a
shared threshold (reuse/promote `deny-data-file-reads.sh`'s existing
`SIZE_THRESHOLD` = 5 MB precedent to `_lib.sh` as
`_LIB_SIZE_THRESHOLD_BYTES` so both hooks reference one constant).
Content over the threshold is passed through via `updatedToolOutput`
unscanned rather than regex-matched — documented as a known gap
(a credential inside a truncated-past-cap large output is not
redacted), consistent with this repo's existing "tripwire, not
airtight" posture, and avoiding unbounded per-fire latency on a hook
that runs on every matching tool call.

**Emit contract and failure posture.** This hook is `hook-class:
informational` (per this repo's hook-class taxonomy: it has no
`permissionDecision`/deny path) and does not reuse `emit_deny` —
its success path emits `hookSpecificOutput.updatedToolOutput` with
the redacted string, a distinct emit function from the deny-shaped
bootstrap Mechanisms A/B and D use. On a parse failure (`tool_result`
in a shape the hook's extraction logic doesn't recognize, or content
that breaks `jq -r`), the hook fails **open** — passes the original
`tool_result` through completely unmodified — because a `PostToolUse`
hook has no deny primitive to fall back on; there is no safer failure
mode available. This posture is stated explicitly here (row 6 remains
`[unverified]` for the exact JSON shape, but the *posture on failure*
is decided regardless of what that shape turns out to be) and pinned
by a fail-safe test (see Critical files) rather than left as an
undocumented implicit default.

**Internal-leak constraint.** The hook's own implementation must not
leak the value it is redacting while processing it: no `set -x`
tracing, no intermediate temp file holding unredacted content, stdin
piped straight through `jq`/`grep`/`sed` to stdout with no
disk-persisted intermediate — reproducing the triggering incident
inside the very hook meant to prevent it would be worse than not
having the hook at all. Out of this hook's control: the harness's own
`PostToolUse` invocation necessarily hands the pre-redaction
`tool_result` to the hook's stdin, so any harness-level session
transcript or hook-invocation log that separately records raw stdin
would capture the unredacted value regardless of what this hook does
— a limitation of where this hook sits in the pipeline, not something
its own implementation can close.

**Mechanism D — extend `deny-pii-in-commits.sh` with an always-on
credential-value check, independent of that hook's opt-in PII arming.**
Reuses the same credential-value regex constant (row 5) so a secret
matching that shape also fails the commit-time gate. This check runs
**unconditionally** — it does not check for `~/.claude/pii-patterns.md`
before scanning — while the hook's existing SSN/credit-card built-in
patterns and all user-supplied `<label>: <regex>` patterns remain
gated behind that file exactly as today. Splitting the hook's built-in
tier this way (one always-on sub-check, one opt-in sub-check) is
required, not cosmetic: `deny-pii-in-commits.sh`'s domain-specific
PII/PHI patterns carry real false-positive risk in an ordinary repo
and stay opt-in for that reason (see "Always-on vs. opt-in" below),
but credential-value patterns carry the same near-zero false-positive
risk that justifies Mechanisms A–C shipping always-on — gating the
credential check behind the *same* arming file as the domain-specific
patterns would mean the "committing a secret" gap Mechanism D exists
to close stays open for every user who hasn't armed PII scanning,
which is the default. Closes the sibling "committing a secret" gap
alongside the "reading a secret" gap per the audit-structural-siblings
rule.

**Control-flow shape (named explicitly, not left to the implementer
to infer).** `deny-pii-in-commits.sh` currently gates its commit-
detection, `git diff --cached`/`git diff HEAD` extraction, and
`-F <file>` message-source reading entirely behind the
`PII_PATTERNS_FILE` arming check. Making the credential-value check
unconditional means hoisting that shared detection/diff-extraction
machinery *above* the arming gate — run once, unconditionally — then
branching: the credential-value scan always runs against the result;
the SSN/credit-card/user-pattern scan runs only if the arming file
exists. This is the chosen shape specifically to avoid the
alternative — duplicating the git-diff-extraction logic as a second,
independent pre-check — which would double `git` subprocess
invocations per commit on an armed machine and violate this repo's
own single-source-of-truth convention. Three accepted side effects,
named explicitly rather than left for the implementer to discover:
(1) the existing fail-closed behavior on an unreadable `-F <file>`
message source becomes unconditional for every user, not only armed
ones; (2) the existing fail-closed rejection of a pseudo-file `-F`
source (`-F -`/stdin, `/dev/stdin`, `/dev/fd/*` — denied because the
hook cannot statically verify what git will read, a distinct check
from plain unreadability) likewise becomes unconditional; both are
intentional, not a new risk, since fail-closed on content the hook
cannot verify is the correct posture regardless of which scan tier
triggered it — but both are now reachable by users who never armed
PII scanning, a population change worth naming and testing explicitly
(see Critical files), not just asserting. (3) The hoisted
`git diff --cached`/`git diff HEAD`/`git rev-parse` calls, previously
run only for armed users, now run on every commit for every user —
these must be wrapped in `_lib.sh`'s existing `_lib_capped` helper
(the same 5s timeout backstop `_lib_jq` already provides), since an
unwrapped hang (a locked index, an NFS-mounted repo) was an acceptable
opt-in-only cost before and is a default-on availability risk once
unconditional.

**Always-on vs. opt-in.** The two existing PII/PHI guard hooks
(`deny-data-file-reads.sh`, `deny-pii-in-commits.sh`'s PII/PHI patterns)
are opt-in because their patterns are domain- and geography-specific
with real false-positive risk in an ordinary repo (a `.csv` in a
`data/` directory is common and rarely PHI). Credential path/value
patterns here carry near-zero false-positive risk — a file named
exactly `id_rsa` or `~/.aws/credentials`, or a string shaped like
`github_pat_...`, is essentially always a live secret — matching
`deny-env-reads.sh`'s own precedent of shipping always-on with no
arming file required; Mechanism D's credential-value sub-check follows
the same reasoning even though it lives inside an otherwise-opt-in
hook file. Personal/org-specific extensions (e.g. this user's own
per-account token-file naming convention) go in a new optional
user-local config file, `~/.claude/credential-file-guard.md` (same
line-based glob grammar as `data-file-read-guard.md`), checked by
Mechanisms A and B in addition to the built-in list — because a
personal naming convention, unlike the universal built-ins, does
carry real false-positive risk if guessed at broadly.

## Critical files

- `claude/.claude/hooks/_lib.sh` — add the shared constants:
  `_LIB_CREDENTIAL_PATH_REGEX` (POSIX ERE, basename-token match, used
  by Mechanisms A and B) and `_LIB_CREDENTIAL_VALUE_REGEX` (used by
  Mechanisms C and D), plus `_LIB_SIZE_THRESHOLD_BYTES` (5 MB,
  promoted from `deny-data-file-reads.sh`'s existing `SIZE_THRESHOLD`,
  reused by both that hook and Mechanism C). Single source of truth
  per this file's own stated purpose.
- `claude/.claude/hooks/deny-credential-bash-reads.sh` (new) —
  Mechanism A. Reuse `_lib_parse_tool_input_or_deny` /
  `_lib_emit_deny` bootstrap pattern from `deny-env-reads.sh`. The
  `grep -E` match against `_LIB_CREDENTIAL_PATH_REGEX` needs no
  independent timeout beyond `_lib_jq`'s existing 5s backstop (row 9)
  — it operates on an in-memory string, not the filesystem.
- `claude/.claude/hooks/deny-credential-file-reads.sh` (new) —
  Mechanism B. Same bootstrap pattern; sibling to `deny-env-reads.sh`
  and `deny-data-file-reads.sh`. Port `deny-env-reads.sh`'s
  `readlink -f` symlink-resolution block for the fail-closed-on-
  unresolvable-target behavior rather than re-deriving it.
- `claude/.claude/hooks/deny-data-file-reads.sh` — replace its local
  `SIZE_THRESHOLD` constant with the promoted `_lib.sh` one (no
  behavior change, removes the now-duplicated 5 MB literal).
- `claude/.claude/hooks/redact-credential-values.sh` (new) —
  Mechanism C. Verify the real `tool_result` JSON shape (row 6) before
  finalizing the extraction code; support the optional
  `~/.claude/credential-value-patterns.md` additions file using the
  same `<label>: <regex>` grammar `deny-pii-in-commits.sh` already
  parses — reuse that parsing logic rather than re-deriving it. Uses
  its own emit function (not `emit_deny`) that outputs
  `hookSpecificOutput.updatedToolOutput`; on any extraction/parse
  failure, passes `tool_result` through unmodified (fail-open — see
  Mechanism C's "Emit contract and failure posture").
- `claude/.claude/hooks/deny-pii-in-commits.sh` — Mechanism D: add the
  shared value-regex as a second, unconditional built-in check that
  runs whether or not `~/.claude/pii-patterns.md` exists — do not fold
  it into the existing opt-in-gated pattern set, or the "committing a
  secret" gap stays open by default (see Mechanism D's rationale).
  Hoist the commit-detection and `git diff --cached`/`git diff HEAD`
  extraction above the arming-file check per Mechanism D's
  "Control-flow shape" paragraph, and wrap those now-unconditional
  `git` calls in `_lib.sh`'s existing `_lib_capped` helper — they were
  an acceptable unwrapped opt-in-only cost before; unconditional makes
  an unwrapped hang a default-on availability risk.
- `claude/.claude/settings.json` — wire the three new hooks, each
  `command` as `~/.claude/hooks/<name>.sh` matching every existing
  entry's user-settings-scope form: A under the existing
  `PreToolUse`/`Bash` matcher group, B under the existing
  `PreToolUse`/`Read` matcher group (alongside `deny-env-reads.sh` and
  `deny-data-file-reads.sh`), C as a new `PostToolUse` entry matching
  `Bash|Read|WebFetch|Grep|Task`.
- `docs/hooks.md` — required entry for each new hook
  (`test_hook_documented_in_hooks_md` gates this).
- `docs/security-hardening.md` — new section documenting the
  always-on credential guards and the two optional config files,
  contrasted with the existing opt-in PII/PHI section.
- `README.md` — add rows to the hook table (~line 153 area).
- `claude/.claude/tests/helpers.py` — add a `run_hook_updated_output`
  (or similarly named) helper that parses
  `hookSpecificOutput.updatedToolOutput` from a `PostToolUse` payload
  — no existing helper reads this field; `run_hook`/`run_hook_advisory`
  only extract `permissionDecision`, which Mechanism C never emits.
- `claude/.claude/hooks/tests/test_deny_credential_bash_reads.py`,
  `test_deny_credential_file_reads.py` (new) — mirror the structure of
  `test_deny_env_reads.py`/`test_deny_data_file_reads.py`. Four
  required test cases:
  - A regression test asserting `ssh-add`/`chmod`/`ssh -i` referencing
    a credential path are **denied** (`permissionDecision: "deny"` is
    emitted) — the accepted false-positive cost, pinned so it isn't
    silently "fixed" back into a bypass later.
  - A regression test pinning the accepted `grep "id_rsa" .` residual
    false positive the same way (asserts denial).
  - A regression test for the symlink/rename residual (Mechanism A's
    "Documented residual" paragraph) asserting the command is
    **allowed** (no `permissionDecision: "deny"` emitted) — distinct
    from Mechanism C's fail-open assertion below, which checks
    `updatedToolOutput` content, not a deny decision. This pins the
    accepted gap as a known, tracked limitation rather than an
    unnoticed one.
  - A symlink-evasion test for Mechanism B mirroring
    `test_symlink_to_denied_target_denied` in `test_deny_env_reads.py`,
    asserting denial (Mechanism B *does* resolve and deny this case,
    unlike Mechanism A).
- `claude/.claude/hooks/tests/test_redact_credential_values.py` (new)
  — built against the new `run_hook_updated_output` helper and the
  actual confirmed `tool_result` shape (row 6) for each of the five
  matcher-scoped tool types (`Bash`, `Read`, `WebFetch`, `Grep`,
  `Task`) individually — row 6 flags shape as unverified per tool
  type, and a subagent (`Task`) result is plausibly structured
  differently from a `Bash` stdout string, so confirming one shape
  does not confirm the others. Not copied from the `PreToolUse`
  deny-shape templates. Includes a fail-safe test class: malformed/
  partial `tool_result` fixtures per tool type, asserting output
  passes through unmodified rather than crashing or truncating.
  `test_deny_pii_in_commits.py` — extend with three distinct tests, no
  `~/.claude/pii-patterns.md` present for any of them: (a) the
  credential-value sub-check fires (unconditional, per Mechanism D);
  (b) an unreadable `-F <file>` message source still denies
  (fail-closed, now reachable by unarmed users per the hoist); (c) a
  pseudo-file `-F` source (`-F -`, `/dev/stdin`, `/dev/fd/*`) still
  denies (the distinct pseudo-file check, also now reachable by
  unarmed users). (b) and (c) exist in the hook today gated behind the
  arming file, so hoisting is the more subtle half of Mechanism D's
  change — a slip that leaves either under the old `if` would silently
  reopen a fail-closed path for unarmed users with nothing in this
  test set catching it.
  `test_deny_data_file_reads.py` and `test_lib.py` — extend for the
  new/promoted shared constants.
- `claude/.claude/CLAUDE.md` — extend the existing "give the user a
  shell command to run via `!`" convention (Safety section) to also
  name Mechanism A denials explicitly: on a credential-path Bash deny,
  surface the exact blocked command to the human for a `!` retry
  rather than attempting an alternate construction. Per Mechanism A's
  "Accepted gap — behavioral, not hook-logic" paragraph.
- `CHANGELOG.md` — `[Unreleased] / Added` entry (standard practice for
  every merged PR in this repo).

## Verification

- `../../../.venv/bin/pytest claude/.claude/` from the worktree — full
  suite, including the new/updated hook tests and
  `test_hook_alignment.py`'s parametrized doc/wiring checks.
- `../../../.venv/bin/ruff check claude/.claude/`.
- `scripts/list-shell-files.sh | xargs -0 shellcheck` (per repo-root
  `.shellcheckrc` flags) for the three new `.sh` files.
- Manual synthetic-payload checks for each new hook: a `Bash` command
  referencing a fake credential path (Mechanism A denies, including
  via `ssh-add`/`chmod` — the accepted false-positive cost); a `Read`
  on a fake `id_rsa` path (Mechanism B denies) and on a symlink
  pointing at one (Mechanism B denies); a fake `tool_result` string
  containing a `ghp_`-shaped test value for each of the five
  matcher-scoped tool types (Mechanism C redacts, leaving surrounding
  text intact) and a malformed `tool_result` (Mechanism C passes
  through unmodified, per its fail-open posture); a staged diff
  containing the same test value with no `~/.claude/pii-patterns.md`
  present (Mechanism D denies the commit regardless).

## Out of scope

- Revoking or rotating the specific PAT from the triggering incident —
  the user's own GitHub account action, outside this repo.
- Making `deny-data-file-reads.sh` or `deny-pii-in-commits.sh`'s
  domain-specific PII/PHI pattern set always-on — both keep their
  current opt-in posture. Mechanism D's credential-value sub-check
  inside `deny-pii-in-commits.sh` is the one exception, and it is
  in-scope, not out: see Mechanism D's rationale for why it must be
  unconditional rather than riding on the same arming file.
- Adversarial-proof, entropy-based generic-secret detection beyond
  known vendor token-prefix shapes — a known limitation, consistent
  with this repo's existing "tripwire, not airtight" posture for
  these hooks.
- Enterprise/managed-settings rollout changes — already covered
  generically by the existing `docs/security-hardening.md` section;
  no change needed there.
- Aggregate per-fire latency across the full always-on hook stack: two
  more subprocess-spawning `PreToolUse` hooks on every Bash/Read call
  (Mechanisms A/B), plus a `PostToolUse` hook now scoped to five tool
  types including `Grep` (Mechanism C) — `Grep` is typically among the
  highest per-session invocation-count tools in an agentic coding
  session, so this is a materially larger addition to aggregate
  hook-fire count than the two `PreToolUse` hooks alone. Mechanism D's
  hoist also adds in-memory string-parsing (commit/fragment detection)
  to every Bash call for unarmed users, not only `git commit`-shaped
  ones — negligible cost individually (no I/O), noted here only for
  the same accounting consistency this paragraph already applies to
  A/B/C. No single hook here is expensive and no daemon/network
  dependency exists, so this plan doesn't need to solve cross-hook
  cumulative latency — but a future review point for the aggregate
  always-on hook stack's per-fire cost, informed by this plan's own
  contribution to it, may be warranted as more hooks accumulate.
