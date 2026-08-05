# Block agent-initiated downloads and installs

## Context

**Goal:** make it mechanically impossible for an agent to install software or
fetch from an unapproved host, in every permission mode including auto and
`bypassPermissions`.

An agent session installed the wrong `maestro` package from npm (an unrelated
AI tool, not mobile.dev's E2E CLI), removed it, then reached for a vendor
`curl`-piped installer — deciding on its own what software landed on the
machine. **Correction to an earlier draft of this plan:** that draft asserted
as fact that auto mode's classifier saw the incident's "you wanna try Xcode?"
as explicit intent clearing its `curl | bash` soft-deny. That causal claim was
never verified against the transcript — the permission mode in play, and
whether any prompt appeared, are both unknown. What the transcript actually
shows is narrower and doesn't need that story: an agent, mid-task, decided on
its own initiative to install a testing tool, without pausing to ask. Nothing
in this repo's 24 gate hooks or `permissions.deny` list currently stops that.

An earlier draft of this plan enabled Claude Code's OS-level Bash sandbox
(Seatbelt/bubblewrap network isolation) as the foundation. Three independent
specialist reviews (`ciso-reviewer`, `staff-platform-engineer`, `staff-sdet`)
each returned **Request changes**, and each named a *different* gap in that
foundation — a settings.json self-rewrite path, a shell-wrapper bypass of the
companion deny rules, a URL-parsing bug in the companion hook. Three
independent reviewers finding three different holes in one foundation is this
repo's own documented "compounding defensive layers" tell: the fix was
disproportionate to the demonstrated failure. The incident was an agent acting
on unclear latitude, not an agent evading a control. This revision drops the
sandbox and any OS-level mechanism entirely and uses only the primitives the
docs already guarantee apply in every mode.

## Approach

Two mechanical layers plus one prose reinforcement — no OS sandbox, no
settings.json rollout risk, no new platform dependency.

**Why `permissions.deny` is the right foundation, not a fallback:** *"These
controls apply in every mode, including `bypassPermissions`: deny rules and
explicit ask rules"* — evaluated before the auto-mode classifier is consulted,
and *"neither the classifier nor user intent can override it"*
([permission-modes](https://code.claude.com/docs/en/permission-modes),
[auto-mode-config](https://code.claude.com/docs/en/auto-mode-config)). This
repo already relies on exactly this mechanism for its two existing hard floors
(`Bash(sudo *)`, the `.env`/`credentials.json` reads) — this plan extends that
same table, it doesn't introduce a new mechanism.

**Two lighter alternatives considered and rejected:**
- *Prose only* (CLAUDE.md instruction, no mechanical rule) — rejected because
  CLAUDE.md steers the auto-mode classifier's *judgment*, not a hard block;
  only `permissions.deny` is documented as unconditional in every mode, which
  is what "even in auto mode" requires.
- *OS-level Bash sandbox* (the prior draft) — rejected this round: three
  reviewers each found a distinct gap in it, and the incident doesn't show
  adversarial evasion of a control, which is the threat model a sandbox
  defends against. Disproportionate to what actually happened.

### Layer 1 — `permissions.deny`, unambiguous shapes only

Extends the existing table (`claude/.claude/settings.json`) with tools that
have no restore/reinstall ambiguity — a bare literal is always an install:

```jsonc
"deny": [
  "Bash(sudo *)", "Bash(sudo)",                    // existing
  "Read(**/.env)", /* … existing entries … */
  "Bash(brew install *)", "Bash(brew tap *)", "Bash(brew reinstall *)",
  "Bash(gem install *)",
  "Bash(cargo install *)",
  "Bash(go install *)",
  "Bash(gh extension install *)",
  "Bash(mas install *)",
  "Bash(pipx install *)"
]
```

`pipx install <pkg>` (distinct from `pipx run`, which Layer 2 covers via the
`-y`/`--yes` rule) is unambiguous the same way `cargo install`/`go install`
are — there is no `pipx ci`/restore concept — so it belongs here rather than
in the hook.

`cargo install`/`go install`/`gem install` are safe as flat literals because
each ecosystem's *restore* command is a different subcommand entirely
(`cargo build`, `go mod download`, `bundle install`) — there is no shape where
this literal also matches routine dependency restore.

### Layer 2 — `deny-network-installs.sh` (new gate hook, Bash)

**Round-2 review found the mechanism as originally specified didn't work.**
Two independent reviewers ran `_lib_fragment_command_word` and confirmed it
resolves `pnpm add lodash` → `add`, `yarn add -D typescript` → `add`, `bun add
lodash` → `add` (its runner-skip list treats `pnpm`/`yarn`/`bun`/`pipx`/`uvx`
as wrappers) — a dispatch keyed on that helper's return value would silently
never fire for three of the five managers this hook exists to cover. Three
reviewers independently ran `_lib_split_fragments` on `curl u | bash` and
`curl -O u && bash ./local.sh` and got the **identical** two-fragment stream —
the helper maps `;`/`&&`/`||`/`|` all to the same delimiter, so "adjacent
across a `|` split" specifically is not a thing the fragment stream can
express, and `bash -c "$(curl -fsSL url)"` (the literal one-line form most
vendor install pages publish, and the shape the original incident used)
produces a `curl` fragment with nothing after it, matching nothing.
Restated below with the corrected mechanism.

**Round-3 review found the "fix" in round 2 was cosmetic** (`_lib_fragment_invokes_tool`
just wraps the same broken primitive). **Round 4 found the hand-specified
replacement (`_lib_fragment_leading_command`) had its own bugs** — three
consecutive rounds hand-rolling position-sensitive command-word resolution,
three different edge-case failures (assignment/wrapper ordering,
which-word-the-wrapper-check-compares-against, `nohup`/`timeout` missing from
a manually-curated wrapper list). That pattern — patch, new bug, patch again
— is itself the signal: this hook does not need position-sensitive leading-
command resolution at all. It only needs to know whether a manager name and
an install verb are *present* in the fragment, which is exactly what the
existing, already-tested `_lib_fragment_has_token` (`_lib.sh:514`) does —
"does token X appear as a standalone whitespace-delimited token" — and unlike
leading-word resolution, presence is inherently robust to wrapping: `sudo`,
`env VAR=1`, `timeout 300`, `nohup` in front of `npm install lodash` don't
remove the `npm` token from the string, so no wrapper-stripping logic is
needed at all.

**Round-5 review actually ran `_lib_fragment_has_token`'s real regex**
(`_lib.sh:514-517`, `[[ "$fragment" =~ (^|[[:space:]])${token}([[:space:]]|$) ]]`)
against the fragments in this plan, rather than reasoning about it —
resolving one deferred question and finding two new logic bugs.

**Path-prefix question, resolved (not deferred a third time):** confirmed
empirically that `/opt/homebrew/bin/npm install lodash` does **not** match
`has-token npm` — whitespace-delimited token matching doesn't see `npm`
inside `/opt/homebrew/bin/npm`. Rather than add a basename-normalization pass
(one more hand-rolled text transform, with its own edge cases — `~/npm`,
relative paths, symlinks), this is accepted as a named residual: bare,
PATH-resolved invocations are covered (the overwhelming majority of both
human- and agent-generated commands); an explicitly path-qualified invocation
is not, matching the existing "Known gaps" precedent
(`deny-repo-relocation.sh:23-43`) for indirection classes this hook family
already accepts rather than chases.

**Restore-vs-install disambiguation, single coherent rule (the two-rule
version had a bug):** an earlier revision of this rule used "no restore-marker
token present anywhere" as a separate blanket-allow condition alongside the
leftover-token scan — round-5 review found that combination is dead logic
that hides a false-allow: `pip install -r requirements.txt requests` has a
restore marker present (allow, under the old two-rule version) even though
`requests` is a genuine trailing package argument. Restated as **one** rule:
for each fragment, deny when it has-token a family manager name
(`npm`/`pnpm`/`yarn`/`bun`, or `pip`/`pip3`, or both `uv` and `pip`) **and**
has-token that family's install verb (`install`/`i`/`add`), **and**, after
removing every one of the following from the fragment's whitespace-split
token list, at least one token remains:
1. every manager token that fired the condition (for the `uv`+`pip` family,
   both `uv` and `pip` — not just one) and the verb token (their first
   occurrence each);
2. every `VAR=value`-shaped token (`^[A-Za-z_][A-Za-z0-9_]*=`), anywhere —
   this is what makes `env NODE_ENV=1 npm install` (bare, a legitimate
   restore) allow instead of false-denying on the assignment token;
3. every token from the closed pure-wrapper set `sudo`/`doas`/`env`/`command`/
   `time`/`nice`/`nohup`/`timeout`, wherever it appears, plus — only for
   `timeout` — the single token immediately following it if that token is
   purely numeric (its duration argument);
4. every token starting with `-` (the complete definition of "flag" for this
   rule — no separate curated flag list, so there's no ambiguity about what
   counts);
5. for each of the five named restore-marker flags (table below) found in
   step 4 before removal, additionally the token immediately following it in
   the *original* fragment (its value).

There is no separate "restore marker present ⇒ allow" override — a restore
marker's only job is triggering step 5's value-skip. This is what correctly
denies `pip install -r requirements.txt requests` (after removing `-r` and
its value `requirements.txt`, `requests` remains) while still allowing bare
`pip install -r requirements.txt` (nothing remains) and `pnpm i --frozen-lockfile`
(a flag-only marker, removed by step 4, nothing remains).

| Marker | Consumes next token (step 5)? |
|---|---|
| `-r` / `--requirement` | yes — the requirements file |
| `-e` / `--editable` | yes — the path or VCS URL |
| `--frozen-lockfile` | no (already removed by step 4) |
| `--immutable` | no |
| `--quiet` | no |

**Named false-allow, not swept under the false-deny framing:** `pip install -e
git+https://example.com/x#egg=foo` is a genuine fetch of named, VCS-addressed
code that this rule allows, because `-e`'s value is skipped unconditionally
by step 5 regardless of whether it's a local path or a URL. Accepted and
documented in `docs/security-hardening.md` rather than adding VCS-URL
detection to a rule that has already had multiple rounds of scope pressure.

**Any value-taking flag this hook doesn't recognize** (`--registry <url>`,
`--prefix <path>`, `--target <path>`) has its value survive step 4 (it
doesn't start with `-`) and isn't covered by step 5 (not a named restore
marker), so it's misread as a leftover token and **denies** — an accepted
false-deny residual, the safe direction, matching the call already made for
the curl-then-shell rule below.

**Named over-deny, not silently missed — and the trigger is narrower than it
first looks, verified against the real regex.** `_lib_fragment_has_token`
requires a literal space (or line boundary) immediately before and after the
token, so whether a quoted mention of "npm install" denies depends on whether
`npm` sits flush against the opening quote: `git commit -m "denies npm
install lodash"` and `echo "run npm install later"` both deny (a space
separates the quote from `npm` in each), while `grep -rn "npm install
lodash" docs/` and `echo "npm install later"` both allow (`npm` is glued
directly to the `"`, so the token is `"npm`, which doesn't match). This
asymmetry is real, verified, and not worth flattening with quote-aware
parsing — a new hand-rolled primitive with its own edge cases, the same trap
three prior fixes fell into — so it's accepted and named explicitly here and
in `docs/security-hardening.md`, with the `!` escape as the workaround for
the cases that do deny.

| Command | Decision |
|---|---|
| `npm ci` | allow — has-token `npm` but no `install`/`i`/`add` token present, so the verb condition never fires |
| `npm install` | allow — no non-flag token after `install` |
| `npm install --production` | allow — only a flag follows |
| `npm install lodash` | **deny** |
| `npm i -D typescript` | **deny** — `-D` takes no value, `typescript` is the package |
| `npm install --save-dev typescript` | **deny** — long-flag form, same shape |
| `pnpm add lodash` | **deny** — must be an explicit test row; this is the case both prior dispatch attempts silently missed |
| `yarn add -D typescript` | **deny** |
| `bun add lodash` | **deny** |
| `pnpm i --frozen-lockfile` | allow — flag-only marker removed at step 4, nothing remains |
| `pip install -r requirements.txt` | allow — `-r` and its value removed at step 5, nothing remains |
| `pip install -r requirements.txt requests` | **deny** — the bug the two-rule version had: `requests` remains after `-r`/its value are removed; this row exists specifically to pin that fix |
| `env NODE_ENV=1 npm install` | allow — `env` (step 3) and `NODE_ENV=1` (step 2) are both removed, nothing remains; this row exists specifically to pin the fix for the false-deny the earlier position-based drafts had here |
| `.venv/bin/pip install --quiet -r requirements-dev.txt` | allow — **for the path-prefix reason, not the restore-marker reason:** `has-token pip` never matches a path-prefixed token (named residual above), so the manager condition doesn't fire at all and the rule never reaches the restore-marker logic. Still allows, so `install-dev.sh:78` doesn't regress, but not via the mechanism a reader might assume — call this out in the test's docstring so it isn't read as restore-marker coverage it doesn't provide |
| `pip install --quiet -r requirements-dev.txt` (same command, PATH-resolved, no path prefix) | allow, and **this** is the row that actually exercises the restore-marker/leftover-scan logic — required alongside the path-prefixed row above, not instead of it |
| `npm install --prefix /opt lodash` | **deny (accepted false-deny)** — `--prefix` isn't a recognized restore marker, so its value plus the trailing package name both survive step 4/5 as leftover tokens; documented residual, not a bug to chase |
| `grep -rn "npm install lodash" docs/`, `echo "run npm install later"` | **deny (accepted over-deny residual)** — presence-testing can't distinguish a live invocation from a text argument; named explicitly above, workaround is the `!` escape |
| `pip install -e .` | allow — editable-local is a restore-adjacent shape, not a registry fetch of a named package |
| `pip install -e git+https://example.com/x#egg=foo` | **allow — named false-allow, accepted residual, not a bug to chase** (see prose above) |
| `pip install requests` | **deny** |
| `npx -y create-react-app` / `npx --yes <pkg>` | **deny** — explicit skip-confirmation-and-fetch flag, engineer-confirmed addition; bare `npx <local-tool>` (no `-y`/`--yes`) stays allowed since it may resolve to an already-installed local binary with no network call at all |
| `bunx -y <pkg>` / `uvx --yes <pkg>` / `pipx run --yes <pkg>` | **deny**, same rationale |

**Curl/wget-then-execute, restated on the same presence basis:** deny when a
single Bash invocation contains **both** a fragment that has-token `curl` or
`wget` **and** a fragment that has-token one of
`bash`/`sh`/`zsh`/`python3`/`node`/`ruby`/`perl` — regardless of which
operator separates them (`|`, `&&`, `;`, or `bash -c "$(...)"`'s
command-substitution form). Using `_lib_fragment_has_token` here too matters
for the same reason as the install check: any leading-word-resolution
primitive that tries to see through `python3`/`node` as wrappers (the way
`_lib_fragment_command_word`'s runner-skip list does) would silently miss
`curl u | python3`, since that primitive treats `python3` as a wrapper around
whatever follows it — and nothing follows. Presence-testing has no such
failure mode. This is a
deliberate over-deny relative to a "strictly piped" reading (`curl -O f.sh &&
bash ./f.sh` denies exactly like `curl u | bash` does, and so does an
unrelated `curl -sS $API -o data.json && node process.js` batched in one
call) — accepted and stated outright, and named explicitly in the deny
message and in `docs/security-hardening.md` as "any curl/wget fragment
co-occurring with any interpreter fragment," not "download-then-run-the-
download," so the message doesn't misdescribe its own trigger. Chasing
finer-grained precision here would mean re-deriving which operator actually
connects the two fragments — exactly the one-gap-closes-with-one-more-check
pattern this revision exists to avoid. Interpreter set is a named, closed
list (mirroring `_LIB_READONLY_GIT_SUBCMDS`'s "closed enumeration"
convention) covering the shells plus the scripting runtimes vendor
installers commonly hand a downloaded payload to (`get-pip.py` is a `curl |
python3` shape). Plus a substring check for `<(curl` / `<(wget`
(`_lib_split_fragments` does not decompose process substitution, so this is
explicitly a heuristic, not a parser — matching how `_LIB_CREDENTIAL_PATH_REGEX`
is itself substring-based rather than a full grammar).

**Implementation must use `<<< "$(_lib_split_fragments "$COMMAND")"`, not
`< <(_lib_split_fragments "$COMMAND")`** — process substitution drops the
final unterminated fragment, and in `curl u | bash` the `bash` fragment *is*
last, so the wrong form makes the hook silently never fire on the headline
case. `deny-reviewer-tree-mutation.sh:383-386` documents this exact gotcha;
this plan follows the same idiom rather than re-deriving it.

**Explicitly out of scope, named rather than silently dropped:** bare
`npx`/`bunx`/`uvx`/`pipx` (no `-y`/`--yes`) are not covered — `npx eslint .`
against an already-installed local devDependency makes no network call at
all, and distinguishing that general case from a fresh fetch requires reading
`package.json`/lockfiles, real complexity disproportionate to this fix.

**Documented residual, matching existing family precedent:** `sh -c '...'`
with a fully-inline payload, a temp script written then executed as a
separate command, `eval $(echo ... | base64 -d)`, and session-defined aliases
are not detected. `deny-repo-relocation.sh:23-43` already carries a "Known
gaps" section naming exactly this class ("variable/command-substitution
indirection fails open... alias/wrapper/`bash -c "…"` indirection all
undecidable at this level") as an accepted limit of every command-text gate in
this hook family. This plan matches that existing standard rather than
building fragment-tree evasion detection — itself the compounding-layers move
this revision exists to avoid.

**`python3` availability:** the WebFetch hook (Layer 3) is the one that
depends on `python3`, not this hook — see Layer 3 below for the explicit
fail-closed handling required.

### Layer 3 — `deny-unlisted-webfetch-domains.sh` (new gate hook, WebFetch)

Sandbox is dropped, but the WebFetch ask-unless-allowlisted requirement stands
on its own and still needs a hook: permission-rule precedence is deny → ask →
allow, first match wins, and *"a matching ask rule prompts even when a more
specific allow rule also matches"* — so a blanket `WebFetch` ask rule plus
per-domain allow rules can never let the allowlist win
([permissions#manage-permissions](https://code.claude.com/docs/en/permissions)).

Reads `~/.claude/webfetch-allowed-domains.md` via the existing
`_lib_config_lines` helper (`_lib.sh:770`) — the same pattern behind
`credential-file-guard.md`, `data-file-read-guard.md`, `pii-patterns.md`, and
`credential-value-patterns.md`.

| Domain listed? | `permission_mode` | Decision |
|---|---|---|
| yes | any | `allow` |
| no | `default` / `acceptEdits` / `plan` | `ask` |
| no | `auto` / `bypassPermissions` / `dontAsk` | `deny`, naming the domain to add |
| — (field absent, empty, or unrecognized) | — | `deny` — fail closed, never fall through to `ask` |

The mode split is deliberate: a hook-returned `ask` forcing a prompt under
auto/bypass is undocumented, while `deny` is guaranteed everywhere, so the
hook uses the primitive that actually holds in each mode.

**Host extraction uses `python3 -c "import urllib.parse; ..."`**, not a
hand-rolled regex — this is the standard-library-first rule for non-trivial
domains (URL parsing is named in it explicitly). A URL like
`https://github.com@evil.com/x` must resolve to the host `evil.com` via
`urlsplit().hostname`, not to whatever substring precedes an `@`.
`urlsplit(...).hostname` returns `None` for `about:blank`, a `data:` URL, a
bare hostname with no scheme, or an unparseable string — these must be an
explicit test row resolving to `deny` (empty-hostname reaches the same
fail-closed path as an absent `permission_mode`), not a crash.

**`python3` must be treated as this hook's own hard dependency, not inherited
from precedent.** Round-2 review found the "already a dependency" framing
doesn't transfer cleanly: `require-worktree-for-git-writes.sh` only spawns
`python3` on a slow path and denies explicitly when it's absent
(`require-worktree-for-git-writes.sh:167-168`), while a different hook in this
repo (`nudge-error-mode-analysis.sh:117-120`) fails *open* on missing python3
— but that hook is informational, not a gate, so its posture doesn't apply
here. This hook is a gate and runs `python3` on *every* WebFetch call, so it
must explicitly `command -v python3` and `emit_deny` naming python3 by name
when absent, wrapped in the same `timeout 5` convention `_lib_capped` already
uses (`_lib.sh:29`) rather than blocking indefinitely on a hung interpreter —
and a `timeout` exit code of 124 (a hung interpreter, not a clean parse
failure) must resolve to the same `emit_deny` naming python3, not fall
through and be misread as an empty-hostname URL. `TestGateHookBehavior` only
auto-parametrizes jq-absence, not python3-absence — this hook needs its own
explicit tests for both python3-absent and python3-hung.

`*.example.com` matches only strict subdomains, never the bare apex — matching
the sandbox's own documented `domain-pattern` semantics, so anyone who read
that convention elsewhere isn't surprised here.

**File-absent handling:** absent is treated identically to present-but-empty —
normal ask/deny-by-default policy applies to every domain, it does not fail
open. A silent allow-everything default would defeat the point of the hook for
every consumer who never creates the file, which given `claude/.claude/**`
changes go live on `git pull` with no re-install (per this repo's own
CLAUDE.md), is most existing installs. The deny/ask reason names the exact
config path so the first prompt is also the instruction for fixing it.
`install.sh` seeds a starter file for **new** installs only, guarded by
`[ -f "$HOME/.claude/webfetch-allowed-domains.md" ] || …`, placed after the
`continuity-hardening` block closes (after its `chmod 700` at line 75, before
the `machine-level-opt-ins` markers at line 82) so the directory already
exists. The 700 mode on the *parent* directory is what actually blocks other
accounts from resolving into it — the file itself needs no `chmod`, and this
plan does not claim file-mode inheritance, which isn't how `mkdir`/`chmod`
work. The guard itself is the logic under test, not incidental to it — this
plan's earlier claim that "a plain file-seed has no logic to extract" was
wrong: the non-clobber-on-re-run behavior is exactly what `install.sh`'s
existing `# INSTALL_TEST_FIXTURE: continuity-hardening` block tests for its
own `[ -f "$HOME/.claude.json" ]` guard. Wrap this seed in its own
`# INSTALL_TEST_FIXTURE` marker pair, using the parametrized
`_extract_block(start_marker, end_marker)` pattern from
`test_install_sh_repo_relocation_support.py:30` (not the hardcoded pair
`continuity-hardening`'s own test uses), and mirror
`test_install_sh_local_bin_path.py:152-160`'s run-capture-run-again-assert-
unchanged shape for the non-clobber test itself — write both file:line
anchors into the test file's docstring so it reads as a mirror, not a
re-derivation.

**This file must never reach the public repo.** `$HOME/.claude` is a stow
target — on a not-yet-restowed or tree-folded install (the state
`install.sh`'s own line 26–28 comment describes), this seed writes *through*
into the checkout itself as an untracked file, and its content is a
consumer's own curated host list, which can legitimately include internal or
private-project domain names. Add `claude/.claude/webfetch-allowed-domains.md`
to `.gitignore`, mirroring the existing entry for `claude/.claude/private-projects.md`
(same file class: user-curated, potentially private-project-identifying,
belt-and-suspenders alongside `deny-private-project-refs.sh`, which only
fires when that hook's own blocklist is populated).

This also inverts `_lib_config_lines`'s established contract, and that
inversion needs to be named where a future maintainer will see it: every
current consumer (`credential-file-guard.md`, `data-file-read-guard.md`,
`pii-patterns.md`, `credential-value-patterns.md`) treats an absent file as
*no restriction* — this is the first consumer where absent means *maximum
restriction*. Add an inline comment at the call site saying so explicitly.

**Existing consumers get no advance warning, and that needs to ship in the PR
description, not just in code:** on their first `git pull` after this merges,
a WebFetch call to any not-yet-listed domain starts prompting in `default`
mode, or denying outright with no prompt at all in `auto`/`bypassPermissions` —
a silent, mode-dependent behavior change the moment this lands. Call it out by
name in the PR body and in a `docs/security-hardening.md` changelog-style
note, since nothing in code can retroactively seed an existing `$HOME`.

### Layer 4 — Prose (CLAUDE.md Safety section, one sentence)

> A general go-ahead ("try X", "see if Y works") does not authorize installing
> software or fetching a new host — ask explicitly, or point the user to the
> `!` shell escape.

Not enforcement — Layers 1–3 are — but the classifier reads CLAUDE.md, so it
also steers auto mode's own judgment. **Not** placed under "Ground every
choice" — `test_doc_counts.py` re-counts that list's nested bullets against a
literal "Six categories" claim in prose; a seventh bullet fails the suite.

### Assumption ledger

**Root threat:** an agent installs software or fetches an arbitrary host on
its own initiative, without the engineer deciding, and no existing control
constrains it mechanically.

| # | Assumption / mechanism | Tag |
|---|---|---|
| 1 | `permissions.deny` applies in every mode incl. `bypassPermissions`, before the classifier, uncleavable by user intent | `[verified: permission-modes, auto-mode-config]` |
| 2 | Precedence deny → ask → allow; ask beats a more specific allow → rules alone cannot do ask-unless-allowlisted; anchors Layer 3's hook | `[verified: permissions#manage-permissions]` |
| 3 | `cargo install`/`go install`/`gem install` have no restore-collision — each ecosystem's restore command is a distinct subcommand | `[verified: cargo/go/bundler docs — confirm exact subcommand names at implementation time]` |
| 4a | Neither `_lib_fragment_command_word` nor `_lib_fragment_invokes_tool` (which just wraps it) is the right primitive for Layer 2 — both resolve `pnpm add`/`yarn add`/`bun add`/`npx -y x`/`uv pip install` incorrectly. **Correction, twice over, both struck:** an earlier revision of this row asserted `_lib_fragment_invokes_tool` was verified-correct (it was not); the revision after that specified a hand-rolled `_lib_fragment_leading_command` primitive that itself had three more bugs found on the next review pass | `[verified: four reviewer passes across two rounds, each running the actual helpers against the actual fragments]` |
| 4b | The design now avoids position-sensitive command-word resolution entirely, dispatching on `_lib_fragment_has_token` (`_lib.sh:514`) presence-testing instead — no reviewer has found a defect in that primitive itself, because wrapping cannot remove a token from a string the way it can shift which word is "first" | `[verified: round-5 review ran the primitive's actual regex (_lib.sh:514-517) against the fragments in the test matrix]` |
| 4c | `_lib_fragment_has_token` does not match a token that's a basename-suffix of a longer path-prefixed word — `/opt/homebrew/bin/npm` never matches `has-token npm` — confirmed by running the regex, not deferred | `[verified: round-5 review, empirical]` |
| 4d | The two-rule version of the restore-vs-install check (separate leftover-token scan plus a blanket "restore marker present ⇒ allow" override) was dead-logic-masking-a-false-allow: `pip install -r requirements.txt requests` allowed under it. Collapsed into one rule where a restore marker only triggers value-skipping, never a blanket override | `[verified: round-5 review found the contradiction; the collapsed single-rule version is specified inline in Layer 2]` |
| 4e | Presence-testing over-denies *some* text arguments that merely contain manager+verb tokens — but the trigger is narrower than blanket, and quote-adjacency-dependent: a token glued directly to an opening quote (`"npm`) doesn't match, so `grep -rn "npm install lodash" docs/` allows while `echo "run npm install later"` (a space before `npm`) denies. Verified against the real regex, not assumed | `[verified: round-5 and round-6 review, both empirical against the real regex]` — accepted as a named, precisely-scoped over-deny residual, not chased further |
| 5 | `_lib_split_fragments` maps `;`/`&&`/`\|\|`/`\|` all to the same delimiter — "adjacent across specifically a `\|`" is not expressible from its output; the curl-then-shell rule must match on presence-in-either-order-or-operator, not pipe-adjacency | `[verified: three independent reviewers ran it against both `curl u \| bash` and `curl -O u && bash ./local.sh`, got an identical fragment stream]` |
| 6 | `<<< "$(_lib_split_fragments ...)"` is mandatory, not `< <(...)` — process substitution drops the last fragment, and `bash`/`sh` is the last fragment in the headline `curl \| bash` case | `[verified: deny-reviewer-tree-mutation.sh:383-386 documents this gotcha directly]` |
| 7 | `python3` is a *pre-existing* hook-runtime dependency in this repo generally (`parse-git-command.py`), but Layer 3 must still treat it as its OWN hard dependency with an explicit fail-closed check — the existing precedent's fail-open variant (`nudge-error-mode-analysis.sh`) is an informational hook, not a gate, so its posture doesn't transfer | `[verified: require-worktree-for-git-writes.sh:167-168 denies explicitly on python3-absent; nudge-error-mode-analysis.sh:117-120 is the fail-open counter-example, correctly not applicable here]` |
| 8 | Dependency restore (`npm ci`, `pip install -r`, and flag-position variants like `pip install --quiet -r x.txt`) must keep working | `[engineer-verified]` — user's Q2 answer, unchanged by this revision |
| 9 | WebFetch always asks unless the domain is allowlisted, even in auto mode | `[engineer-verified]` — user's explicit requirement |
| 10 | Bare `npx`/`bunx`/`uvx`/`pipx` (no `-y`/`--yes`) disambiguation stays out of scope — needs lockfile/package.json awareness disproportionate to this fix. The explicit `-y`/`--yes` form IS now covered (no such disambiguation needed — the flag itself is the unambiguous signal) | `[engineer-verified: user approved adding the -y/--yes form after the CISO reviewer surfaced it as a low-cost addition; bare form remains out of scope]` |
| 11 | A hook-returned `ask` forcing a prompt under auto/bypass is undocumented either way — Layer 3 degrades to `deny` in those modes rather than assuming | `[unverified]` — design routes around it |

## Critical files

**Reuse, don't reimplement:**

- `_lib_config_lines` (`_lib.sh:770`) — user-config reader, returns 0 silently when absent
- `_lib_parse_tool_input_or_deny` (`_lib.sh:144`) — sets `INPUT`/`TOOL_NAME`/`COMMAND`, fails closed
- `_lib_emit_deny` (`_lib.sh:101`) — canonical deny envelope
- `_lib_split_fragments` (`_lib.sh:450`) — command-chain parsing, same primitive `deny-reviewer-tree-mutation.sh:304-387` already uses
- `_lib_fragment_has_token` (`_lib.sh:514`) — the token-presence primitive Layer 2 dispatches on; **not** `_lib_fragment_command_word` (`_lib.sh:468`) or `_lib_fragment_invokes_tool` (`_lib.sh:504`) — both were tried and rejected during plan review (ledger row 4a). Confirmed not to match path-prefixed tokens (ledger row 4c) — accepted as a named residual, not something implementation needs to re-verify
- Read `.tool_input.url` and `.permission_mode` off `$INPUT` with `_lib_jq`, the way `deny-repo-relocation.sh:88` reads `.cwd`

| File | Change |
|---|---|
| `claude/.claude/settings.json` | Extend `permissions.deny`; add PreToolUse `WebFetch` matcher group (none exists today — current groups are `Bash`, `Edit\|Write\|MultiEdit`, `ExitPlanMode`, `Agent`, `Read`) |
| `claude/.claude/hooks/deny-network-installs.sh` | **New.** `deny-` prefix forces `# hook-class: gate` on line 2; `emit_deny()` defined *before* sourcing `_lib.sh`; `set -uo pipefail` (not `-e`, per repo convention) |
| `claude/.claude/hooks/deny-unlisted-webfetch-domains.sh` | **New.** Same conventions |
| `claude/.claude/hooks/tests/test_deny_network_installs.py` | **New.** Table-driven allow/deny per the restore-vs-install matrix above, a direct fragment→has-token unit table (not only hook-decision-level tests, so a mis-tokenization can't hide behind a coincidentally-correct final decision), plus non-Bash-passthrough and malformed-input cases |
| `claude/.claude/hooks/tests/test_deny_unlisted_webfetch_domains.py` | **New.** URL-authority edge cases (userinfo, port, IPv6, IDN, trailing dot, non-http scheme), `*.example.com` vs bare-apex non-match, all four `permission_mode` rows including absent/unrecognized, non-WebFetch passthrough, python3-absent (PATH-injected via `helpers.build_path_without("python3")`, mirroring the existing jq-absent harness) and python3-hung (a stub `python3` that sleeps past the timeout; hook must resolve `python3` via `PATH` lookup, not a hardcoded interpreter path, for this to be injectable, and the timeout value should be small enough that this row doesn't become the suite's slowest test) |
| `claude/.claude/tests/helpers.py` | Add `webfetch_input(url, prompt="summarize", permission_mode=None, session_id=None)` — `prompt` is a real `tool_input` field on every actual WebFetch call and a fixture omitting it is unrealistic; `permission_mode` mirrors the only current builder that threads it (`stop_input`) |
| `docs/hooks.md` | **Test-enforced.** `test_hook_alignment.py:111` requires a line-start bullet for each new hook name |
| `docs/security-hardening.md` | New guardrail-table rows; explicit residuals section: shell indirection, bare `npx`/`bunx`/`uvx`/`pipx`, process-substitution heuristic limits, the `pip install -e <VCS-URL>` false-allow, unrecognized-value-taking-flags false-deny, curl-then-shell's "any co-occurrence" trigger (not "download-then-run") |
| `docs/auto-mode.md` | Extend "Hard-floor deny rules" table |
| `README.md` | Hook → Gates → Cleared-by table (convention, not test-enforced) |
| `claude/.claude/CLAUDE.md` | Safety bullet — **not** under "Ground every choice" |
| `install.sh` | Seed `~/.claude/webfetch-allowed-domains.md` with a starter list for fresh installs only, guarded and fixture-marked per Layer 3 |
| `.gitignore` | Add `claude/.claude/webfetch-allowed-domains.md`, mirroring the existing `claude/.claude/private-projects.md` entry |

`TestGateHookBehavior` auto-parametrizes both new hooks: deny on malformed
JSON, empty stdin, non-object `tool_input`, missing `_lib.sh`, `jq` absent from
PATH. No opt-in — file presence enrolls them.

## Verification

**Automated** — every row below is unit-testable via `run_hook`/`bash_input`/
`webfetch_input` and belongs in the new test files, including the
`permission_mode` axis (the hook reads that field off its own JSON input, so
`auto` vs `default` needs no live mode switch to test):

| Command / payload | Expected |
|---|---|
| `npm install -g maestro` | denied (Layer 1) |
| `brew install jq` | denied (Layer 1) |
| `npm ci` | succeeds |
| `npm install` (bare) | succeeds |
| `npm install lodash` | denied (Layer 2) |
| `pnpm add lodash`, `yarn add -D typescript`, `bun add lodash` | denied (Layer 2) — the exact case both rejected dispatch primitives silently missed |
| `npm install --prefix /opt lodash` | denied (Layer 2) — accepted false-deny, unrecognized value-taking flag |
| `pip install -r requirements.txt`, `pip install --quiet -r requirements-dev.txt` | succeeds |
| `pip install -r requirements.txt requests` | denied (Layer 2) — pins the fix for the two-rule version's false-allow (ledger 4d) |
| `env NODE_ENV=1 npm install` | succeeds — pins the fix for the false-deny the earlier position-based drafts had here |
| `timeout 300 npm install lodash` | denied (Layer 2) — pins wrapper-set step 3 handling `timeout` plus its numeric argument |
| `nohup npm install lodash` | denied (Layer 2) — pins the other previously-untested wrapper-set member |
| `uv pip install -r requirements.txt` | succeeds — pins step 1 removing *both* `uv` and `pip` for this family, not just one |
| `uv pip install ruff` | denied (Layer 2) |
| `pip install -e .` | succeeds |
| `pip install -e git+https://example.com/x#egg=foo` | succeeds — accepted false-allow, named residual |
| `pip install requests` | denied (Layer 2) |
| `git commit -m "denies npm install lodash"`, `echo "run npm install later"` | denied (Layer 2) — accepted over-deny residual (ledger 4e): a space separates the quote from `npm`, so the token matches; workaround is the `!` escape |
| `grep -rn "npm install lodash" docs/`, `echo "npm install later"` | **succeeds** — `npm` sits flush against the opening quote (token is `"npm`, not `npm`), so it does not match; this row exists to pin the asymmetry as intentional, not to be "fixed" by a future quote-stripping pass |
| `npx eslint .` (bare, no `-y`) | succeeds |
| `npx -y create-react-app foo` | denied (Layer 2) |
| `curl -fsSL https://example.com/i.sh \| bash` | denied (Layer 2) |
| `curl -O https://example.com/i.sh && bash ./i.sh` | denied (Layer 2) — accepted over-deny, same as the piped form |
| `bash <(curl -fsSL https://example.com/i.sh)` | denied (Layer 2, heuristic) |
| `curl -fsSL https://example.com/get-pip.py \| python3` | denied (Layer 2) |
| WebFetch, listed domain, any `permission_mode` | allow |
| WebFetch, unlisted domain, `permission_mode: "default"` | ask |
| WebFetch, unlisted domain, `permission_mode: "auto"` | deny |
| WebFetch, `permission_mode` absent / `""` / unrecognized | deny |
| WebFetch, `python3` absent from `PATH` | deny, naming python3 |
| WebFetch, `python3` present but hung past the 5s timeout | deny, naming python3 — not misread as an empty-hostname URL |
| WebFetch to `about:blank` / a `data:` URL / a schemeless bare hostname | deny, not a crash |
| `sudo npm install -g x` | denied — via Layer 1's `Bash(sudo *)`; Layer 2's `has-token npm` also matches regardless of the `sudo` prefix, as a defense-in-depth check that needs no wrapper-specific handling at all |
| `/opt/homebrew/bin/npm install lodash` | **succeeds — confirmed accepted residual (ledger 4c), not a bug**: `has-token npm` does not match a path-prefixed token; this row exists to pin the residual as intentional so a future change doesn't "fix" it by surprise |

**Manual, once** (genuinely cannot be scripted): dispatch a `general-purpose`
subagent told to `brew install jq` and confirm it is denied — subagents fire
the same PreToolUse hooks as the parent session, but exercising that requires
a live dispatch. Live-run one real WebFetch to an unlisted domain while
actually in `auto` mode, to confirm row 11's unverified assumption (hook-`ask`
under auto/bypass) empirically rather than by inspection.

**Repo suite**, from a worktree three levels deep:

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

Watch `test_doc_counts.py` (Layer 4 placement) and `test_hook_alignment.py`
(gate-class, doc bullet, `emit_deny` ordering, jq-absent).

## Out of scope

- **Shell indirection** (`sh -c`, temp-script-then-execute, base64+eval,
  session aliases) — documented residual matching `deny-repo-relocation.sh`'s
  existing "Known gaps" precedent for this hook family.
- **`npx`/`bunx`/`uvx`/`pipx`** — disambiguating "already-declared local tool"
  from "fresh registry fetch" needs lockfile awareness; not in this incident's
  shape.
- **OS-level sandboxing** — the prior draft's foundation; dropped this round
  as disproportionate to the demonstrated failure mode. Revisit only if a
  future incident demonstrates adversarial evasion of the deny rules above,
  not agent-initiative alone.
- **WebSearch and MCP-connector fetches** (Google Drive, Todoist) — neither
  routes through WebFetch; name as residuals in `docs/security-hardening.md`
  rather than building coverage for them here.
- **`brew`/`gem`/`cargo`/`go`/`gh extension`/`mas` as Layer-2 hook-backed,
  not just Layer-1 glob-denied** — a `cd /tmp && brew install jq` bypasses
  Layer 1's literal-prefix glob (though not Layer 2's install/pip check, since
  that's a different install family). Single-layer coverage for these six
  tools is an accepted gap for this revision, named rather than silently
  dropped, given the incident that motivated this plan was `npm`/`curl`, not
  these.
- **`install.sh`'s starter WebFetch allowlist must exclude user-content hosts**
  (gist/raw-paste/raw-content-style services) — those are simultaneously the
  injection surface (attacker-controlled page content reaching the agent) and
  a potential low-bandwidth query-string egress channel. Document both
  residuals in `docs/security-hardening.md`; this is a docs/config-content
  decision at implementation time, not a design gap.
