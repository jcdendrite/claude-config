# Fix redirect false-positive in the network-install gate

## Context

`deny-network-installs.sh` is meant to allow a bare dependency restore
(`pnpm install`, `npm install`, `yarn install` with no named package) while
denying a named-package install — that split is the hook's whole design
point, and CLAUDE.md's Safety section states the same carve-out. It
currently denies a bare restore anyway whenever a shell redirection token
(`2>&1`, `> out.log`, `&>/dev/null`, …) is glued to the same fragment,
because its leftover-token classifier reads the redirection syntax as an
unrecognized word and treats it as a package name. This was hit for real: a
session regenerating ten repos' lockfiles after a defensive `overrides`
pin (no version change, no new package) got denied on `pnpm install 2>&1 |
tail -30` — an ordinary way to capture output — and had to defer all ten
installs to the engineer via the `!` escape instead of running them itself.
Fix the false positive so the gate's documented allow/deny split actually
holds for commands using ordinary output-capture syntax.

## Root cause (verified this session)

Direct testing against the live hook (`~/.claude/hooks/deny-network-installs.sh`,
confirmed byte-identical to this repo's tracked copy and to a second,
project-scoped Claude Code account's copy — same `sha256sum`) reproduces
the false deny:

| Command | Result |
|---|---|
| `pnpm install` | allow |
| `cd <dir> && pnpm install` | allow |
| `pnpm install 2>&1` | **deny** ("installs a named package") |
| `pnpm install \| tail -30` (no `2>&1`) | allow |
| `pnpm install 2>&1 \| tail -30` | **deny** |

Pulled the actual denied `tool_input.command` from the originating
session's transcript to confirm the exact shape (not a paraphrase):
`cd /path/to/worktree && pnpm install 2>&1 | tail -30`.

`_lib_split_fragments` (`_lib.sh`) splits a command into fragments only on
`;`, `&&`, `||`, `|`, `$(`, and backtick — never on `>`/`<`. So
`pnpm install 2>&1` (everything up to the first `|`) reaches
`_install_has_leftover_token` as one fragment. That function walks the
fragment's whitespace-separated words looking for a package name; it
already knows how to skip the manager, the verb, recognized flags, and a
flag's value, but has no case for a redirection operator or its target, so
`2>&1` falls through to `leftover=true` — the same code path a real
argument like `left-pad` would hit.

## Approach

Teach `_install_has_leftover_token` to recognize shell redirection syntax
as syntax, not as a candidate package name — the same per-word skip
mechanism it already uses for flags (`skip_next_value`) and the `timeout`
duration (`skip_next_if_numeric`), extended with one more case:

1. **Self-contained fd duplication/close** (`2>&1`, `>&2`, `<&-`) — consumes
   no further word.
2. **Bare operator** (`>`, `>>`, `<`, `<>`, `>|`, `&>`, `&>>`, each optionally
   preceded by a leading fd digit for the non-`&`-forms) — consumes the next
   word as its target, reusing the existing `skip_next_value` flag.
3. **Operator glued to its target** (`>out.log`, `2>err.log`, `&>/dev/null`)
   — consumes no further word.

Three local regexes, one `[[ =~ ]]` check per case, checked as three new
branches inside the function's existing per-word loop, ahead of the
generic `-*` flag case (redirection words never start with `-`, so
ordering relative to it doesn't matter, but grouping them near the other
per-word classifiers keeps the function's shape readable). Verified
against a standalone harness that all nine forms above classify correctly
and that a real leftover token (`evil-package`, a bare digit) still falls
through to `leftover=true` regardless of where a redirection appears in
the same fragment — order-independent, so `pnpm install evil-package >
out.log` still denies. Case 2 (bare operator, exact `^...$` match on the
whole word) and case 3 (operator plus one-or-more trailing non-space
characters) are mutually exclusive by construction — a word either has
content after the operator prefix or it doesn't — confirmed empirically
in the same harness, not just by the pattern shapes.

**Anchoring invariant (required, not optional):** all three regexes must
match only at the *start* of the word — optionally preceded by bare
digits (an fd number), never by arbitrary leading text — and the
glued-target case (3) requires that same start-anchored operator prefix
before its `[^[:space:]]+` tail. This is what keeps a real leftover
token directly abutting an operator with no space (`evil-package>out.log`,
`foo&>bar`, `left-pad2>&1`) from being misread as pure redirection
syntax: none of those strings begin with a digit-then-operator prefix,
so all three regexes correctly fail to match and the word still falls
through to `leftover=true`. Verified empirically against six such
glued-no-space adversarial words, all six denying correctly — see the
regression tests below, which pin this as a permanent property rather
than a one-time check.

**New false-allow surface this introduces, and why it's inert:** the
hook already quote-strips the command (`_lib_strip_shell_quotes`, line
53, pre-existing, for a different purpose — closing an adjacent-quote
manager-name split) before fragment splitting. So a *quoted* argument
that becomes redirect-shaped after stripping — `npm install ">pkg"` →
bare word `>pkg` — is read by the new case-3 branch as syntax, not a
leftover token, and now allows where it used to deny (as an ordinary
unrecognized word). This is a real new gap, in the opposite direction
from every existing residual in the hook's header (all of which are
false-*denies*), and it's what the header update in Critical Files
documents. It has no exploit value: neither npm's nor PyPI's
package-name grammar permits a literal `>`/`<` character, so no string
this shape can ever resolve to a real installable package — the
"allowed" command still fails at the registry, it just fails there
instead of at this gate.

**Alternatives considered:**

- **Make `_lib_split_fragments` split on `>`/`<` too**, so redirection never
  reaches the per-word loop of *any* hook that consumes it. Rejected:
  that helper is shared by seven other hooks
  (`deny-pii-in-commits.sh`, `deny-private-project-refs.sh`,
  `deny-reviewer-tree-mutation.sh`, `deny-repo-relocation.sh`,
  `require-worktree-for-git-writes.sh`, `require-ready-for-review.sh`,
  plus this one) — changing its splitting behavior is a repo-wide
  blast-radius change to fix a defect in exactly one of them. The
  per-word fix stays inside `deny-network-installs.sh`, the only file
  with the bug.
- **Broaden the fix to also close the "unrecognized value-taking flag"
  residual** (`--registry <url>` etc., documented in the hook's own header
  and in `docs/security-hardening.md`) while in the neighborhood. Rejected
  per scope discipline: that's a separate, already-named, accepted
  residual with no reported failure behind it — bundling it in here isn't
  minimal and isn't what broke.

### Assumption ledger

**Root problem:** the network-install gate's leftover-token classifier
misreads shell redirection syntax as a package-name argument, false-denying
an otherwise-permitted bare restore.

**Givens:**
- Bash's own `for word in $fragment` unquoted word-splitting (IFS-based,
  not real shell tokenization) is the substrate every per-word classifier
  in this function already runs on; the fix works within that primitive
  rather than replacing it with real shell parsing. [platform/language-
  runtime boundary: bash's word-splitting behavior is not something this
  repo's code can change]

Not touching `_lib_split_fragments`'s broader splitting behavior or the
hook's other named residuals are both choices inside this plan's own
reach (both live in this repo, editable by this same PR) that the plan
deliberately declines — see **Out of Scope** below, not a given.

**Mechanisms:**
- Three per-word regex classifiers added to `_install_has_leftover_token`.
  anchors: root. Lighter-primitive check: the only two alternatives with a
  plausible claim to solving this are (a) extend `_lib_split_fragments` to
  split on `>`/`<` — rejected above as a shared-helper, repo-wide change
  for a single-file bug; (b) parse the fragment with a real shell
  tokenizer (e.g. shell in Python) — rejected as introducing a new
  runtime dependency and a materially heavier parsing mechanism than the
  word-list scan every other classifier in this function already uses,
  for a defect that a same-shape regex check fully closes.

**Assumptions:**
- The false deny reproduces on the live, installed hook, not just the
  git-tracked source. [verified: ran the hook binary from
  `~/.claude/hooks/deny-network-installs.sh` directly against
  `pnpm install 2>&1 | tail -30`; separately confirmed via `sha256sum`
  that both the personal (`~/.claude`) and a second, project-scoped
  account's (`~/.config/claude-accounts/<account>`) copies resolve to
  this repo's tracked file, so the fix in this one file reaches both.]
- The denied command was exactly `cd /path/to/worktree && pnpm install
  2>&1 | tail -30`, not a paraphrase. [verified: read the
  literal `tool_input.command` and `permissionDecisionReason` fields from
  the originating session's own transcript JSONL, not the session's
  narration of them]
- The nine redirection forms in the Approach section (`2>&1`, `>&2`,
  `<&-`, `>`, `>>`, `<`, `&>`, `&>>`, plus each glued-to-target variant)
  cover the shapes that matter for this fix. [verified: ran each through a
  standalone classifier harness and confirmed correct routing; heredoc/
  here-string forms (`<<`, `<<<`) are explicitly out of scope below, not
  silently assumed handled]
- A real leftover package-name token glued directly to a redirection
  operator with no space (`evil-package>out.log`, `foo&>bar`,
  `left-pad2>&1`) still falls through to `leftover=true` and denies,
  rather than being misread as pure redirection syntax. [verified:
  `ciso-reviewer`'s `/plan-review` pass raised this as the specific
  attack shape that would matter if the regexes weren't start-anchored;
  ran all six of its named adversarial forms through the same
  standalone classifier harness and confirmed every one still denies]

## Critical files

- `claude/.claude/hooks/deny-network-installs.sh` — add the three
  redirection-recognizing branches to `_install_has_leftover_token`
  (~line 63-117). Reuses the existing `skip_next_value` flag; no new
  function-level state needed. Also add two bullets to the header's
  existing "Known gaps" list (lines 8-26), matching its one-sentence-per-
  bullet convention: (1) a heredoc/here-string redirect (`<<`, `<<<`)
  glued to a bare install fragment still denies, since its target is a
  multi-line delimited body, not a simple next word, and none of the
  three new branches recognize it; (2) a quoted argument that becomes
  redirect-shaped after this hook's pre-existing quote-stripping (e.g.
  `npm install ">pkg"` → bare word `>pkg`) now allows instead of denying,
  accepted since neither npm's nor PyPI's package-name grammar permits a
  literal `>`/`<` character. Both are safe-direction residuals named in
  this plan's Out of Scope, now surfaced where a future reader of the
  hook itself (not just this plan) will see them.
- `claude/.claude/hooks/tests/test_deny_network_installs.py` — add a
  regression-pin test (matching this file's existing
  `test_..._is_a_regression_pin` naming convention, e.g. the
  timeout-duration test at line 104) covering: `pnpm install 2>&1`,
  `pnpm install 2>&1 | tail -30`, `npm install > out.log 2>&1`,
  `yarn install &>/dev/null` all allowed; `pnpm install evil-package
  2>&1` and `npm install left-pad > out.log` still denied (redirection
  recognition must not swallow a real leftover token appearing elsewhere
  in the fragment). Also add the glued-no-space adversarial forms
  (`ciso-reviewer` finding, `/plan-review`): `npm install
  evil-package>out.log`, `pnpm install foo&>bar`, `yarn install
  left-pad2>&1` — all still denied, pinning that the anchoring invariant
  above (operator prefix only after optional digits, never after
  arbitrary leading text) holds and isn't a one-time property.
- `CHANGELOG.md` — add one `### Fixed` bullet under `[Unreleased]`,
  matching this file's existing convention (bold lead sentence, root
  cause, before/after behavior, no "migration" note since no caller-
  visible contract changes): the false deny on a bare install with a
  redirection glued to it, and what specifically was misread.

`docs/security-hardening.md`'s residuals list already doesn't mention
this shape (it wasn't a named/accepted gap), so there's nothing there to
reconcile or remove.

## Verification

- `../../../.venv/bin/pytest claude/.claude/hooks/tests/test_deny_network_installs.py`
  (per this repo's three-levels-deep worktree venv path) — new tests pass,
  existing tests (in particular the `-*` flag and leftover-token tests)
  still pass unchanged.
- `../../../.venv/bin/shellcheck claude/.claude/hooks/deny-network-installs.sh`.
- Manual re-run of the standalone repro harness used during investigation
  (the exact denied command, plus the six bare-install variants already
  confirmed allowed) to confirm the fix closes the originally reported
  failure end-to-end, not just the isolated unit cases.

## Out of scope

- The curl+interpreter denial from the same session (querying npm
  registry versions via `curl | python3`) — that's the hook's documented,
  deliberate, accepted over-deny (`docs/security-hardening.md`'s "curl/wget
  co-occurring with an interpreter *anywhere* in one Bash call denies"),
  not a bug; the `!` escape remains the correct workaround for it.
- The unrecognized-value-taking-flag residual (`--registry <url>`, etc.) —
  separate, already-documented, not implicated in the reported failure.
- Heredoc/here-string redirection (`<<`, `<<<`) glued to an install
  command — not reported, and this hook has no heredoc-body-aware parsing
  elsewhere to extend consistently.
- Closing the quoted-argument/quote-stripping interaction (`npm install
  ">pkg"` now allows) by making the redirection regexes quote-aware —
  not chased further since it has no exploit value (see Approach's "New
  false-allow surface" note) and closing it properly means threading
  quote-position tracking through a function that currently only ever
  sees post-strip text, a materially heavier change than this fix.
- Extending `_lib_split_fragments` to split on `>`/`<` so no downstream
  hook ever sees a redirection token glued to a word — in this plan's own
  reach (same repo, same file family) but declined here: it's a shared
  helper consumed by seven hooks (see Approach's "Alternatives
  considered"), and this bug is reported in exactly one of them.
