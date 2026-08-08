# Exempt env-file loader arguments from the credential-path Bash gate

## Context

**Goal:** stop `deny-credential-bash-reads.sh` from denying a Bash command
solely because it passes a `.env` path to a runner flag that loads the file
into a subprocess environment.

The gate matches `_LIB_CREDENTIAL_PATH_REGEX` (`claude/.claude/hooks/_lib.sh:879`)
against the whole quote-stripped command text. Group 1 matches `.env` when
preceded and followed by a non-`[A-Za-z0-9_.]` character. In a command shaped
like:

```
deno test --config <proj>/deno.json --allow-all \
  --env-file=<proj>/tests/.env <proj>/tests/unit/
```

the `/` before `.env` and the space after both satisfy those boundaries, so the
gate fires on the flag argument no matter what test target follows. Every
invocation of that project's test suite is denied, which removes autonomous
test-run/iterate loops from the agent entirely.

The gate is behaving as designed: `docs/security-hardening.md:71-83` records a
deliberate choice to deny on the path token alone, with no verb carve-out. That
stance is preserved. What changes is narrower: **a `.env`-shaped argument to a
documented env-file loader flag stops counting as a path reference.**

## Approach

**Scan first; strip and re-scan only on a match.** The existing
`_LIB_CREDENTIAL_PATH_REGEX` scan at `deny-credential-bash-reads.sh:44` runs
unchanged. Only when it *matches* does the hook apply a text transform that
removes env-file loader flags and their `.env`-shaped arguments, then re-scan
the result. A surviving match denies; a cleared match allows.

This ordering is load-bearing, not an optimization detail:

- **It is fail-closed by construction.** Any failure of the transform — a
  non-zero `sed` exit, empty output, an undefined helper (exit 127) — leaves
  the original match standing, so the gate denies. A strip that ran *before*
  the scan would hand an empty string to the scan and **allow**. That failure
  is reachable: under `LANG=*.UTF-8`, BSD `sed` exits 1 emitting nothing on an
  invalid UTF-8 byte, where GNU `sed` passes the bytes through (verified on
  BSD `sed` this session).
- **It keeps the transform off the common path.** The hook fires on every Bash
  tool call for every stow user; §7 of the hook-review standard budgets <100ms
  per fire. Under this ordering the extra subprocess runs only on commands that
  already matched a credential token — rare.
- **The transform can only ever downgrade a deny to an allow.** It never
  perturbs a command that was going to be allowed anyway.

### What the strip requires

All four conditions must hold before a span is removed:

1. The token is a documented env-file loader flag, left-anchored to
   start-of-string or whitespace.
2. Its argument's basename is `.env`-shaped — `.env`, optionally with one
   dotted suffix (`.env.production`, or similar).
3. The argument run terminates at whitespace **or a shell metacharacter**
   (`; & | < > ( ) $` and backtick), not at whitespace alone.
4. The flag match is case-sensitive.

Condition 2 is what keeps every other credential family protected even in flag
position: `--env-file ~/.aws/credentials`, `--env-file ~/.netrc`,
`--env-file ~/.kube/config`, `--env-file <p>/credentials.json`, and an
SSH-private-key basename all still deny, because none of those arguments is
`.env`-shaped. Condition 3 keeps the argument from swallowing a following
command: in `--env-file=t/.env;cat </foo/.netrc` the run stops at `;`, so the
`.netrc` still reaches the re-scan and the command still denies.

**Flag list** — three spellings, each grounded at a primary source this session:

| Flag | Source quote | Argument forms |
|---|---|---|
| `--env-file` | Deno: "You can pass multiple `--env-file` flags (e.g. `deno run --env-file=.env.one --env-file=.env.two --allow-env <script>`)"; also shown as `deno run --env-file main.ts` | `=` and space |
| `--env-file` | Docker `run`: "Read in a file of environment variables." | `=` and space |
| `--env-file` | Node CLI, added v20.6.0: "Loads environment variables from a file relative to the current directory, making them available to applications on `process.env`." | `=` only |
| `--env-file-if-exists` | Node CLI, added v22.9.0: "Behavior is the same as `--env-file`, but an error is not thrown if the file does not exist." | `=` only |
| `--envfile` | pytest-dotenv: "You also have the option to run your tests with `py.test --envfile path/to/.env`." | space |

`--env-file` is also the spelling used by `podman run` and `docker compose`.
Both argument forms are accepted for every spelling; over-covering a form a
given CLI does not document costs nothing, since condition 2 gates the argument
regardless.

### Assumption ledger

**Root problem:** the hook sees only raw command text and cannot distinguish
"names this path for a subprocess to load" from "prints this path's contents,"
so it denies both.

**Givens** (fixed, outside this design's reach):

- **G1 — A `PreToolUse` hook receives tool input only; it has no process,
  filesystem, or exec-level view of what a command will do with a path.**
  Reason: the harness's hook contract — a platform boundary, already the
  recorded basis for this family's symlink/rename residual
  (`docs/security-hardening.md:452-456`).
- **G2 — A runner that loads a `.env` can be instructed in the same invocation
  to print its own environment.** Reason: vendor CLI semantics — loading and
  printing are one process.
- **G3 — The `.env` in question holds live test credentials that must reach the
  test process.** `[engineer-verified]` — renaming the file to a non-matching
  form was considered and rejected, since that would also stop the gate
  protecting it from `cat`.

**Mechanisms:**

- **M1 — Scan, then strip and re-scan only on a match.** `anchors: root`. Two
  lighter primitives were read for and rejected:
  - *Narrow `_LIB_CREDENTIAL_PATH_REGEX` so a `.env` preceded by `file=` does
    not match.* Fails twice: the constant is shared with
    `deny-credential-file-reads.sh:53`, so narrowing it weakens the Read gate
    too; and the space-separated form carries no `=` to key on.
  - *A settings.json `if:` condition excluding the runner command.* Fails
    against the repo's rule that hooks filter their own input rather than
    relying on settings.json conditions (CLAUDE.md, "Hook defense-in-depth"),
    and an `if:` matches a command prefix — it would exempt the whole command
    including any unrelated credential token in it.
- **M2 — Argument-shape constraint (condition 2).** `anchors: rows A6, A7`.
- **M3 — Metacharacter-terminated argument run (condition 3).**
  `anchors: row A8`.
- **M4 — Case-sensitive flag match against a case-insensitive scan.**
  `anchors: row A4`.
- **M5 — The re-scan is the only site the strip feeds. The `.ssh`
  deny-by-default check (`:51`) and the personal `credential-file-guard.md`
  glob loop (`:67`) keep reading the un-stripped text.** `anchors: row A2`.
  Two independent reasons, both load-bearing: the un-stripped `:51` scan is
  what still catches a `.ssh` path in flag position if condition 2 is ever
  loosened; and a user who writes a glob into their own guard file has declared
  that shape always-deny, which a flag position must not override.

**Assumptions:**

- **A1** — All three flag spellings load the named file into the subprocess
  environment; none print it. `[verified: Deno env-variables docs; Docker run
  CLI reference; Node CLI docs; pytest-dotenv PyPI page — quotes in the table
  above]`
- **A2** — `deny-credential-file-reads.sh` receives a bare `file_path` from the
  Read tool, never flag text. `[verified: :38 extracts .tool_input.file_path;
  _matches_credential_path at :50-75 takes a single bare path; both call sites
  (:77, :90) pass a path, never command text]`
- **A3** — Stripping cannot create a match that the original text did not have,
  because the flag is left-anchored to whitespace or start-of-string, so the
  character left of a stripped span is always a separator. `[verified: by
  construction; the left anchor, not the space-replacement, is what makes
  joining unreachable]`
- **A4** — The flag spellings are lowercase in all cited docs. `[unverified]` —
  not load-bearing: a case-sensitive strip against a `grep -i` scan errs toward
  denying `--ENV-FILE=t/.env`, the safe direction.
- **A5** — `sed` is line-oriented, so a `\`-continuation between flag and
  argument leaves the argument unstripped and the command denied. `[verified:
  run this session through _lib_strip_shell_quotes then the proposed transform,
  in both = and space forms — the flag and stray backslash are consumed on line
  1, the .env argument survives on line 2, and the gate still denies]`
- **A6** — Accepted residual: `docker run --env-file=t/.env alpine env`, and
  any equivalent that loads then prints. `[verified: by construction]` A
  deliberate print, not the accidental exposure the family targets
  (`docs/security-hardening.md:447`). **There is no value-layer backstop for
  this by default** — `_LIB_CREDENTIAL_VALUE_REGEX` (`_lib.sh:924`) covers only
  GitHub token prefixes, AWS access-key *IDs*, and a PEM header;
  `docs/security-hardening.md:467-478` already states it does not redact a
  `.netrc` password, a `.git-credentials` URL, an AWS *secret* access key, a
  Docker auth blob, or a Kubernetes bearer token. A per-user
  `~/.claude/credential-value-patterns.md` can add shapes, but stow users get
  none by default.
- **A7** — Accepted residual: the flag is inert argv padding to anything that
  does not parse it, so `bash -c 'cat "$2"' _ --env-file <p>/.env` reads the
  file through the gate. `[engineer-verified]` — accepted knowingly after a
  demonstrated proof-of-concept; closing it needs a runner allowlist plus
  shell-segment parsing, rejected as heavier than the guardrail warrants.
  Scope is bounded to the `.env` family by condition 2 — no other credential
  shape is reachable this way.
- **A8** — Condition 3's metacharacter set (`; & | < > ( ) $` backtick) is what
  stops the argument run from consuming a following command. `[verified: the
  argument pattern is a character run, not a shell token — without the
  metacharacter class, `--env-file=t/.env;cat </foo/.netrc` strips the whole
  tail and the .netrc never reaches the re-scan]`
- **A9** — BSD `sed` under a UTF-8 locale exits 1 and emits nothing on an
  invalid byte; GNU `sed` passes it through. `[verified: run on BSD sed this
  session]` Dissolved by M1's ordering — a failed transform leaves the prior
  match standing.
- **A10** — Alternation ordering within the flag regex is **not** load-bearing.
  POSIX ERE is leftmost-longest regardless of alternative order, and truncating
  `--env-file-if-exists` to `--env-file` is impossible anyway because the
  alternation is immediately followed by `=` or whitespace, and `-if-exists`
  satisfies neither. `[verified: reverse ordering run on BSD sed in both
  argument forms — strips --env-file-if-exists correctly]` No comment or test
  may assert an ordering constraint.

## Critical files

**`claude/.claude/hooks/_lib.sh`** — add beside the credential constants
(`:877-885`) and the sibling transform (`:871`):

- `_LIB_ENV_FILE_FLAG_REGEX` — the three spellings. No ordering rationale in
  the comment (A10).
- `_lib_strip_env_file_flag_args()` — reuse `_lib_strip_shell_quotes`'s
  `printf '%s' "$1" | sed -E ...` shape. Two substitutions (`=` form, space
  form), each left-anchored `(^|[[:space:]])` with the anchor re-emitted via
  `\1`, each requiring a `.env`-shaped basename (condition 2) and a
  metacharacter-or-whitespace-terminated run (condition 3), replacing with a
  single space. No `-i`, no `nocasematch` (M4). BSD/GNU parity for `-E`, `\1`
  in the replacement, `[[:space:]]`/`[^…]` classes, and the anchor-inside-
  alternation was verified on BSD `sed` this session.

**`claude/.claude/hooks/deny-credential-bash-reads.sh`** — restructure `:44`
into scan → strip → re-scan → deny (M1). Leave `:51` and `:67` reading
`$COMMAND_UNQUOTED` (M5). Amend the header: the "matches the path token alone,
with no verb condition" sentence stays (still true — no verb was carved out);
add the flag-argument exemption and add the A6 and A7 residuals to the
"Documented residuals" line. That line currently packs two residuals into one
semicolon-joined sentence — restructure it into a one-sentence-per-bullet list
per the hook-review standard's header convention, rather than extending the
run-on.

**Land `_lib.sh` and the hook in the same commit.** Split across commits, a
`git pull` between them leaves the hook calling an undefined helper (exit 127)
— which M1's ordering makes deny-safe rather than allow-safe, but the window is
still avoidable and there is no reason to open it.

**`claude/.claude/hooks/tests/test_lib.py`** — unit tests for the transform,
beside the `_lib_strip_shell_quotes` block (~`:1489`), which is where the
per-spelling × per-form matrix belongs; that block's own comment already sets
this split (transform pinned in isolation, end-to-end in the caller's file).
Cover: each spelling in each argument form; repeated flags; the left-anchor
negative (`/foo/.e--env-file=xnv` unstripped); `--ENV-FILE=` unstripped; the
metacharacter-termination case asserting the second credential token *survives*
the transform (A8); and a non-`.env` argument left unstripped (A6/M2). Do not
write a test asserting an alternation-ordering constraint (A10).

**`claude/.claude/hooks/tests/test_deny_credential_bash_reads.py`** — end-to-end
through the hook, in the file's existing allow/deny helper style. Keep this
layer to the reproducer, token-scoping, and scan-site cases:

- *Allow:* the full `deno test --config … --env-file=<p>/tests/.env <p>/tests/unit/`
  reproducer; the space form; `cat --env-file t/.env`, pinned as the accepted
  A7-class residual with a docstring recording that BSD `cat --env-file f`
  returns `cat: illegal option -- -`, exit 1, file unread (verified this
  session) — the file's `:382` docstring sets the convention that every
  residual named in the docs gets a test.
- *Deny — argument shape (M2), the invariant that actually breaks:*
  `--env-file` and `--env-file=` against each of `.netrc`, `.aws/credentials`,
  `.kube/config`, `credentials.json`, `.config/gh/hosts.yml`, and an
  SSH-private-key basename outside the SSH config directory.
- *Deny — token scoping:* `--env-file=t/.env;cat </foo/.netrc` (A8, no spaces
  around the metacharacter — the spaced `&&` variant passes trivially and does
  not test the boundary); `cat /foo/.env --env-file=/bar/.env`; `--ENV-FILE=`;
  a `\`-continuation between flag and argument (A5).
- *Deny — scan sites (M5), written so a regression is detectable:* each case
  must be one the strip *would* have removed had it fed that site — a
  custom-named key file under the SSH config directory, referenced via the
  space-form flag, must deny via `:51`, and a guard-file glob with a matching
  `--env-file=` argument must deny via `:67`. A same-directory case containing
  no env-file flag passes whether or not the sites read stripped text, and
  pins nothing.
- *Deny — transform failure:* an invalid UTF-8 byte in a command that also
  carries a credential token must still deny (A9, M1's fail-closed ordering).

**`docs/security-hardening.md`** — amend `:71-83` to state that the
no-verb-allowlist stance is unchanged while a `.env`-shaped argument to a named
loader flag is exempt. Add residual bullets to Limitations (`:483`+) for A6
(including that no default value-layer backstop covers it, and that the Bash
gate is now weaker than its Read sibling for this one shape), A7, and A8's
boundary. Name the user-level opt-out explicitly: adding an `--env-file` glob
to `~/.claude/credential-file-guard.md` re-denies, since M5 keeps that loop
un-stripped — this is the only per-user rollback and is otherwise only implicit.

**`CHANGELOG.md`** — one entry in the existing style.

**Deliberately unchanged:** `deny-credential-file-reads.sh` (A2);
`claude/.claude/CLAUDE.md:84`, whose "no bypass and no verb carve-out" claim
stays accurate — `cat`, `ssh-add`, and `chmod` against a `.env` are all still
denied; `README.md:158`, whose "no bypass valve" column still holds since
nothing user-armable was added.

## Verification

From a linked worktree (the `.venv` lives at the main worktree root only;
`.claude/worktrees/<branch>` is exactly three levels deep):

```bash
../../../.venv/bin/pytest claude/.claude/hooks/tests/test_lib.py \
  claude/.claude/hooks/tests/test_deny_credential_bash_reads.py \
  claude/.claude/hooks/tests/test_deny_credential_file_reads.py
../../../.venv/bin/pytest claude/.claude/                 # full suite
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

**CI does not cover BSD `sed`.** `.github/workflows/tests.yml:25` is
`runs-on: ubuntu-24.04`, single-runner, so a BSD divergence in this transform
would ship green to macOS stow users. The constructs used here were verified by
hand on BSD `sed` this session (A5, A9, A10, and the `_lib.sh` parity list).
Either add `macos-latest` to the test-job matrix or accept the gap knowingly —
it must not stay unstated.

End-to-end: `claude/` is stowed, so the hook goes live on `git pull` with no
re-install. Re-run the original reproducer in the project where it was denied,
and confirm `cat` against the same path is still denied.

**Implementer workflow trap — this gate scans the text of every Bash command,
including the ones used to build the change:**

- `git commit -m "…--env-file=…/.env…"` is denied. Use `git commit --file <path>`.
- `gh pr create --body "…"` is denied for the same reason. Use `--body-file`.
- Ad-hoc `bash -c` / `grep` / `sed` prototyping of the transform against real
  fixtures is denied. Iterate through the `test_lib.py` unit tests instead.
- Selecting a single case by pytest node id is denied, because the node id
  carries the token. Use `-k` on a token-free substring.
- Author test fixtures with the Write tool, not shell heredocs.

## Out of scope

- **A runner allowlist to close A7.** Would need shell-segment parsing to avoid
  re-breaking `cd proj && deno test --env-file=…` and `env CI=1 deno test …`,
  on the model of the repo's `parse-git-command.py`. Rejected as heavier than a
  guardrail against accidental exposure warrants; A7 records the acceptance.
- **Built-in JWT-shape detection in `_LIB_CREDENTIAL_VALUE_REGEX`.** Would
  narrow A6's residual, but it is a separate decision about a shape with no
  vendor-fixed prefix.
- **Renaming the project's test env file** to a non-matching form. Rejected
  under G3 — it would trade a false positive for a real coverage loss.
- **A general machine-local path-exemption file.** Strictly more power than the
  problem needs; it would exempt a path for every verb.
