# Account-scoped PR cost disclosure

## Context

**Goal:** move the PR cost-disclosure opt-in from a per-repo, content-addressed
sentinel to a per-Claude-account one resolved against `$CLAUDE_CONFIG_DIR`, whose
content selects a disclosure *mode* rather than proving a repo identity.

`.claude/pr-cost-disclosure` shipped in #597 (`98cfd7d`) as the repo's only
per-repo-only sentinel, and the only content-addressed one. Its content must equal
the repo's own `owner/repo`, checked live against `gh repo view` at gate time
(`claude/.claude/skills/pr-description/SKILL.md:75`). That design exists to close
exactly one threat: a flag file inside `.claude/` travelling silently into an
unrelated repo via the documented copy-paste adoption path.

Using it surfaced that the scope axis itself is wrong. Cost is tracked
**organizationally**, not per repository — and on this machine an organization maps
1:1 to a Claude account, each isolated under its own `CLAUDE_CONFIG_DIR`. Some
accounts care about cost disclosure; for others it makes no sense. Every repo
reached under one account shares that single answer, so per-repo granularity is
finer than the decision it encodes.

Two consequences follow, and the second is the one that makes this a defect rather
than a preference:

1. Per-repo means the same decision is re-entered once per repo, forever, with no
   way to express "this account discloses."
2. The gate reads the sentinel from the working tree, and `git worktree add` checks
   out **tracked** files only. An uncommitted sentinel therefore does not exist in a
   linked worktree — the exact tree from which PRs are authored under this repo's
   worktree enforcement. Enabling the feature in repo X effectively requires
   committing a claude-config artifact into repo X, which in a repo you do not own
   is frequently not permitted at all.

Moving the sentinel to `$CLAUDE_CONFIG_DIR` closes both, and retires the original
threat rather than defending against it: there is no longer a file inside `.claude/`
for a repo-directory copy to carry.

**Retired for that vector, not made copy-proof in general.** One residual travel path
survives and is named here rather than left for a reader to find: bootstrapping a new
Claude account by copying an existing config directory (`cp -r ~/.claude
~/.config/<account-dir>/<new>`) carries the sentinel into the new account — the same
failure shape one layer up, and a plausible operation on a machine that runs several
isolated accounts. A stale `CLAUDE_CONFIG_DIR` still exported from a previous shell
has the same effect. Neither is closed by content-addressing either (an account has no
stable identity string the gate could check the way a repo has `owner/repo`), so this
plan accepts the residual and makes it *auditable* instead: `install.sh`'s account row
prints the path it actually resolved, so an operator inspecting a freshly bootstrapped
account sees an inherited file rather than having to suspect one.

## Approach

Replace the repo-scoped, content-addressed sentinel with an account-scoped mode
file at `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pr-cost-disclosure`.

**This change is net-subtractive.** The repo-identity string, the live `gh repo
view` call in the gate, the three-outcome indeterminate branch, the
`repo-content-addressed` inventory scope, `install.sh`'s
`_report_content_addressed_sentinel`, and the committed
`.claude/pr-cost-disclosure` all go away. Nothing replaces them except a single
local file read.

### Resolution

**Resolution, not union.** `$CLAUDE_CONFIG_DIR` when set *and absolute*, else
`$HOME/.claude` — and the value is read from exactly one path, never two. This is
deliberately *not* the pattern `_lib_worktree_enforcement_active` uses
(`claude/.claude/hooks/_lib.sh:599`, union at `:615` and `:619`), which checks the
resolved config dir and then falls back to `$HOME/.claude`, enforcing on either. That
union is correct for *its* sentinel — a machine-wide worktree guard armed before
`CLAUDE_CONFIG_DIR` adoption must not go dark, and its failure direction is more
enforcement. Applied here it would invert: the personal account's opt-in would
activate disclosure inside a client account whose own file is absent, which is the
precise failure this re-scope exists to prevent.

The `$HOME/.claude` fallback fires only when `CLAUDE_CONFIG_DIR` is unset or invalid
(see grammar requirement 5), which is correct because the personal account *is* bare
`~/.claude`.

The idiom already exists inside a skill body at
`claude/.claude/skills/handoff/SKILL.md:153`. Do **not** copy
`claude/.claude/statusline-command.sh:87`'s `${CLAUDE_CONFIG_DIR:-$HOME}` — that
form appends `.claude.json` and so falls back to the home root, a different shape
that would resolve this sentinel to the wrong path.

### Mode grammar

File content, outer-trimmed and lowercased (exact rules below), is the mode:

| Content | Behavior |
|---|---|
| `dollars` | Regenerate the `## Cost` block (today's behavior, unchanged) |
| file absent, or unreadable | Off |
| empty, whitespace-only, or any unrecognized value | Off |

Unrecognized values fail **closed**, not loud. This is a disclosure gate: publishing
under an uncertain instruction is the harmful direction, and failing closed also
makes the grammar forward-compatible — an `allowance` value written by a newer
config, read by an older checkout, declines to publish rather than publishing the
wrong quantity. A typo is not silent in practice because `install.sh`'s inventory
reports the literal unrecognized value at install time.

**Parsing properties, stated as requirements because the nearest existing idiom is
wrong.** `install.sh:315` reads this same file with `tr -d '[:space:]'` today. Do not
carry that forward: it **deletes all whitespace, including interior**, so a malformed
file containing `dol lars` collapses to `dollars` and *enables* disclosure. On a gate
whose whole design intent is to fail closed, that is fail-open — the one shape in this
change that could publish spend data against the operator's intent. The required
grammar instead:

1. Trim **leading and trailing** whitespace only. Never delete interior whitespace.
2. Lowercase with `tr '[:upper:]' '[:lower:]'`. **Not** `${var,,}` — that is bash ≥4.0
   and this repo targets macOS system bash 3.2 (`install.sh:44-45` documents the 3.2
   constraint). No lowercase idiom exists elsewhere in the repo's shell to copy.
3. Compare the whole trimmed value to `dollars` **exactly** — an anchored equality
   test, never a substring or pattern match. Any residual interior whitespace, any
   second line, any surrounding characters → off.
4. Any read failure — file absent, unreadable (EACCES), a directory where a file is
   expected, a stale network mount — is off. Never fatal: the read must be guarded
   (`|| mode=""`), because `install.sh` runs under `set -e` and an unguarded failed
   command substitution in an assignment aborts the *entire installer*, including the
   plugin registration that runs after the report.

**Pinned implementation, not just properties.** At least three plausible-but-wrong
reaches satisfy the properties above while reintroducing a fail-open, so stating
properties alone is not enough — pin the idiom. Verified on real `/bin/bash` 3.2.57,
not a simulated version:

```sh
mode=$(cat "$sentinel_path" 2>/dev/null) || mode=""
mode="${mode#"${mode%%[![:space:]]*}"}"   # strip leading whitespace
mode="${mode%"${mode##*[![:space:]]}"}"   # strip trailing whitespace
mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
[ "$mode" = "dollars" ]                    # anchored equality, not a pattern match
```

Three reaches this forbids, each a fail-open if taken:

- **`tr -d '[:space:]'`** (the nearest existing idiom, `install.sh:315`) — deletes
  interior whitespace, so `dol lars` → `dollars` → enabled.
- **`IFS= read -r mode < "$sentinel_path"`** — reads only the first line, so
  `dollars\nallowance` → `dollars` → enabled. This one is the trap: it satisfies
  requirement 1 and survives requirement 4's guard, and fails only requirement 3,
  silently.
- **An unanchored compare** (`[[ "$mode" == *dollars* ]]`, an unanchored `grep`) —
  `dollarsx` and `xdollars` → enabled.

`$(cat ...)` reading the *whole* file is what makes requirement 3 enforceable at all;
a first-line read cannot detect a second line it never looked at.
5. `CLAUDE_CONFIG_DIR`, when set, must be **absolute** or it is treated as unset-and-
   invalid → off. `claude/.claude/scripts/_config_dir.py:8-12` raises on a relative
   value and `_lib_config_dir` (`claude/.claude/hooks/_lib.sh:94-101`) treats one as
   unresolvable; a bare `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` in the gate would
   instead resolve against the *current working directory* — the repo root during a
   `pr-description` run — while `transcript-analysis.py`, invoked moments later in the
   same block, raises on the same value. Two resolvers disagreeing at a disclosure
   gate is not acceptable; the gate adopts the stricter of the two.

### The gate drops from three outcomes to two

#597's gate had three outcomes because `gh repo view` could fail, and collapsing
"could not check" into "disabled" would have silently stripped a published
disclosure on a transient network error. A local file read collapses that to:

1. mode is `dollars` → regenerate the block.
2. otherwise → delete the block if one exists.

**Stated precisely, because "a local read cannot fail" would be false.** EACCES, a
stale network mount, and an unreadable `$HOME` are real local-read failures —
`_lib.sh:595-596` names exactly these. The claim is narrower and is a deliberate
trade, not an impossibility: every one of those failures lands on the *under*-
disclosing side, so the risk #597's third outcome existed to prevent (treating "could
not check" as "opted out" and stripping a published block) is accepted here rather
than eliminated. The consequence is bounded and self-healing: a legitimately opted-in
account can lose its `## Cost` block on one sync during a transient read failure, and
the next sync restores it. That is a far smaller exposure than the `gh`-dependent
gate it replaces, where the same failure was network-frequent rather than
disk-rare — but it is not zero, and the plan does not claim it is.

One behavior worth naming rather than discovering: a PR opened under an account with
`dollars` and later synced under an account with the sentinel absent will have its
`## Cost` block **deleted**. That is correct — the account currently doing the work
has not consented to disclose.

### Drafted replacement text for `SKILL.md:75`

The skill body is prose, so the wording *is* the change — drafted here rather than
deferred to execution. The prose replacements go in as **single long lines**, matching
the surrounding style of `:73`, `:75`, and `:84`.

**Line budget, because this one is tight.** The file is at 187 of its 200-line cap
(`claude/.claude/hooks/check-skill-length.sh:66`; the hook denies a commit only when a
staged `SKILL.md` *grew* past its limit — `:74`, `:87`). The fenced snippet below adds
roughly 9 lines, landing near 196. That is deliberate spend, not accident: the snippet
is what closes the drift surface between this gate and `install.sh`'s reporter, and it
is the artifact a test pins. Reflowing the prose passages into wrapped paragraphs
would spend the rest of the headroom for nothing — keep them as single lines. If the
implementation lands above 200, shorten the surrounding prose rather than dropping the
snippet.

> Gate: resolve the config dir as `$CLAUDE_CONFIG_DIR` when it is set **and
> absolute**, else `$HOME/.claude` — a relative `$CLAUDE_CONFIG_DIR` is invalid, not
> a cwd-relative path, and disables the section. Read `<config-dir>/pr-cost-disclosure`,
> trim leading and trailing whitespace only, lowercase via
> `tr '[:upper:]' '[:lower:]'`. Exactly `dollars` → regenerate the block. Anything
> else — absent, unreadable, empty, interior whitespace, a second line, any other
> value — delete the block if one exists. Treating an unreadable file as "off" rather
> than as "leave the block alone" is deliberate: a local read fails rarely and
> self-heals on the next sync, so the gate prefers under-disclosing to guessing. Do
> not reintroduce an indeterminate third outcome here. Resolve that one path only; never also
> check `$HOME/.claude` when `$CLAUDE_CONFIG_DIR` is set, or one account's opt-in
> would activate disclosure under another. The sentinel is per Claude account, not
> per repo: cost is an organizational fact, and each account is its own billing
> entity.
>
> ```bash
> mode=$(cat "$sentinel_path" 2>/dev/null) || mode=""
> mode="${mode#"${mode%%[![:space:]]*}"}"
> mode="${mode%"${mode##*[![:space:]]}"}"
> mode=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
> [ "$mode" = "dollars" ]
> ```
>
> Read the whole file, not the first line — a first-line read cannot see a second line
> and would treat `dollars\nallowance` as opt-in.

And the closing sentence of `:84` becomes:

> Session/turn counts and per-model-ID dollars are not neutral — they signal
> engagement scale and model mix. That is the intended read under an account that
> opted in; it is not a property of the output format, and an account enabling this
> for one engagement should not assume the fields are harmless in another.

### Why a file rather than a settings key or an env var

Two lighter-looking primitives exist in the surrounding system; both fail:

- **A key in `settings.json`.** Rejected because this repo's account-level settings
  file *is* its stow source (`claude/.claude/settings.json`, committed and public).
  An account-specific disclosure mode placed there would be committed to a public
  repo and inherited by every stow user on `git pull` — reintroducing exactly the
  silent-travel failure this re-scope exists to end, one layer up.
  The gitignored per-project local-settings variant is scoped per project rather than
  per account, so it re-creates the per-repo repetition being removed. (Named
  obliquely here on purpose: its literal filename trips this repo's internal-hostname
  redaction detector, `_lib.sh:1073`, since the `.local` segment reads as an internal
  TLD.)
- **An environment variable** (e.g. `CLAUDE_PR_COST_DISCLOSURE=dollars` set per
  account in a shell profile). Rejected because `install.sh`'s inventory could not
  report it except when the install happens to run in an already-exporting shell,
  and a session that did not inherit the export would silently disagree with a
  session that did. All twelve existing sentinels are files; a file is also the only
  form the inventory can state a definite answer about.

### `install.sh` inventory

A new `account` scope value replaces `repo-content-addressed`. It is report-only
(never promptable — a mode string has no Y/n form) and differs from the existing
`machine` scope in two ways that justify a distinct value rather than reuse:

- It resolves **canonically** to one path. `machine` rows deliberately report *both*
  `$HOME/.claude` and `$CLAUDE_CONFIG_DIR` when those diverge
  (`install.sh:256-266`, with `diverged_config_dir` computed at `:339-345` and
  passed only at `:352`), because which copy is authoritative genuinely varies per
  sentinel. For this sentinel it does not vary, so a dual report would be
  misinformation.
- Its **content** determines its state. `_sentinel_state_label`
  (`install.sh:245-251`) never opens the file — it returns `ENABLED` or the row's
  `default-state` and cannot express `ENABLED (mode=dollars)`. A new
  `_report_account_sentinel` reads the content; `_sentinel_state_label` is left
  untouched for the presence-only rows it correctly serves.

Reported states: `disabled` (absent) · `ENABLED (mode=dollars)` · `present but mode
not recognized: "<value>" — treated as disabled`. Enable hint for the absent case:
`echo dollars > <resolved path>`.

**The row must print the path it resolved, and say that it is the only one checked.**
Without that, a user with `CLAUDE_CONFIG_DIR` set reads neighbouring `machine` rows
announcing `DIVERGED` with both paths listed, then an account row naming neither —
which reads as an omission rather than as the deliberate single-path resolution it is.
Printing the resolved path also does double duty as the audit surface for the
config-dir-copy residual named in Context. Follow `_report_repo_sentinel`'s existing
`(%s)` path convention (`install.sh:276-286`) rather than inventing a format.

### Alternatives weighed

**A machine-level repo allowlist** (`~/.claude/pr-cost-disclosure-repos`, one
`owner/repo` per line, optionally with `owner/*` wildcards) — set aside. It fixes
the must-commit problem but keeps repo as the scope axis, so it still asks the
question once per repo (or invents a wildcard grammar to avoid that), and it
introduces a second gate path alongside the committed file. Both are cost paid to
approximate a boundary the account already draws exactly.

**Machine-level presence-only flag with per-repo opt-out**, mirroring
`worktree-required` — rejected. It fails *open*: a freshly cloned client repo has no
opt-out file, so the first PR discloses. Acceptable for an enforcement sentinel whose
failure direction is "more safety"; not for a disclosure one.

**Keeping the per-repo file as an additional route** — rejected by the engineer, and
correctly: two authorities for one fact is the duplication this repo's CLAUDE.md
names as a defect absent a stated exception. The account boundary is the whole
answer.

**A per-repo escape hatch** for "this account discloses, but not this one repo" —
explicitly declined by the engineer. It would be the only fail-open path in the
design.

### Assumption ledger

**Root problem:** the cost-disclosure opt-in is scoped per repository, but the
decision it encodes is per organization — which on this machine is per Claude
account — so the sentinel is both re-entered endlessly and, under worktree
workflows, only functional in repos the engineer may commit config artifacts into.

**Givens** — conditions this plan treats as fixed and does not attempt to change:

| Given | Why it is beyond this plan's reach |
|---|---|
| `rate_limits` is delivered **only** to a statusline command's stdin, and never lands in a transcript record | The harness decides which fields it passes to which extension point. That is its hook contract — a platform boundary no plan can change from inside it. Scoped narrowly on purpose: this given covers the *delivery channel* only. Whether this repo *persists* what that channel already hands it is inside reach (`claude/.claude/statusline-command.sh` is a repo-owned file) and is therefore **not** a given — it is a deliberate deferral, recorded in Out of scope. |
| Local figures are list price, not billed price | Transcripts persist token counts only; no `costUSD` field exists on any record, and subscription billing terms are exposed to no local artifact — no repo-owned file can derive one. Carried forward unchanged from `.claude/plans/pr-cost-disclosure.md`. |

The `CLAUDE_CONFIG_DIR`→Claude-account mapping (owned by `workstation-setup`'s
`accounts.tsv`) is deliberately **not** listed as a given: this design consumes the
resolved env var and asserts nothing about which account it denotes, so the mapping
is not a condition it depends on.

| # | Assumption | Tag |
|---|---|---|
| root | Statement above | — |
| row1 | Cost is tracked organizationally, not per repo, so the Claude account is the correct scope axis | `[engineer-verified]` |
| row2 | The per-repo file is deleted outright, not retained as a second route | `[engineer-verified]` |
| row3 | Account-level `off` is sufficient; no per-repo escape hatch | `[engineer-verified]` |
| row4 | `allowance`-percentage mode is a separate plan, not part of this one | `[engineer-verified]` |
| row5 | `transcript-analysis.py` has no concept of rate limits, weekly allowance, or reset windows | `[verified: grep for rate_limit\|rateLimit\|resetsAt\|weekly\|allowance\|seven_day\|7d across claude/.claude/scripts/transcript-analysis.py returned zero matches this session]` |
| row6 | `rate_limits.{five_hour,seven_day}.used_percentage` reaches this repo only via statusline stdin | `[verified: claude/.claude/statusline-command.sh:9-11]` |
| row7 | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` is an established idiom inside a skill body | `[verified: claude/.claude/skills/handoff/SKILL.md:153]` |
| row8 | `statusline-command.sh` uses a *different* fallback shape (`${CLAUDE_CONFIG_DIR:-$HOME}` + `.claude.json`) that must not be copied here | `[verified: claude/.claude/statusline-command.sh:87]` |
| row9 | The union pattern this sentinel must not follow lives in `_lib_worktree_enforcement_active`, not a function named `_lib_worktree_required` | `[verified: grep of function definitions in claude/.claude/hooks/_lib.sh returned only _lib_worktree_enforcement_active at :599; union arms read at :615 and :619 this session — corrects a name this plan inherited from #597's prose]` |
| row10 | `_sentinel_state_label` is presence-only and cannot express a mode without modification | `[verified: read install.sh:245-251 this session]` |
| row11 | `machine`-scope rows report both paths when `CLAUDE_CONFIG_DIR` diverges; `diverged_config_dir` is computed once and passed only to that arm | `[verified: read install.sh:256-266, :339-345, :352 this session]` |
| row12 | The dispatch `case` has exactly three arms; `repo-content-addressed` is the only consumer of `_report_content_addressed_sentinel` | `[verified: read install.sh:348-364 this session]` |
| row13 | `_report_content_addressed_sentinel` spans `install.sh:288-325` (comment `:288-293`), and the `INSTALL_TEST_FIXTURE: sentinel-inventory` block spans `:198-364` | `[verified: read install.sh:288-296, :348-364 this session; subagent survey for the interior range]` |
| row14 | Exactly three tests assert content-addressed behavior (`:336`, `:348`, `:364`); six others carry a now-unnecessary `_fake_gh` stub (`:259`, `:301`, `:315`, `:328`, `:392`, `:412`) | `[verified: grep of def test_/_fake_gh( across test_install_sh_sentinel_inventory.py this session]` |
| row15 | `test_declares_content_addressed_gate_check` (`test_skills.py:687-688`) asserts the literal `gh repo view` string; its sibling `test_declares_pr_cost_delimiters` (`:682-685`) does not | `[verified: grep of the class body this session]` |
| row16 | `pr-description/SKILL.md` is 187 lines against a 200-line cap, and the gate hook fires only when a staged SKILL.md **grew** past its limit | `[verified: wc -l; claude/.claude/hooks/check-skill-length.sh:66,74,87]` |
| row17 | The `.gitignore` stow-fold block at `:49-60` carries one entry per machine-scope sentinel, in no enforced order | `[verified: read .gitignore:49-60 this session]` |
| row18 | `.claude/pr-cost-disclosure` is tracked at repo root and was added by `98cfd7d` | `[verified: git ls-files and git log -1 on the path this session]` |
| row19 | A stale `.claude/pr-cost-disclosure` left behind in an adopter's repo becomes inert, not dangerous — nothing reads that path after this change | `[verified: reasoning over row12 plus the SKILL.md gate rewrite; no reader remains]` |
| row20 | Breakage surface for other stow users is near-zero: the feature shipped in the immediately preceding commit and this repo is its only known consumer | `[unverified]` — asserted from `git log origin/main -1` showing #597 as the tip; adoption elsewhere is not observable from here |
| row21 | `tr -d '[:space:]'` deletes **interior** whitespace, so reusing `install.sh:315`'s idiom would let `dol lars` collapse to `dollars` and open the gate — fail-open on a fail-closed design | `[verified: staff-sdet finding this round, re-derived against tr(1) delete semantics — corrects this plan's own first draft, which proposed exactly that reuse]` |
| row22 | `${var,,}` is bash ≥4.0 and unavailable in macOS system bash 3.2; no lowercase idiom exists anywhere in this repo's shell to copy | `[verified: staff-platform-engineer finding this round; install.sh:44-45 documents the 3.2 constraint, and a repo-wide grep for a lowercase idiom returned zero hits]` |
| row23 | A relative `$CLAUDE_CONFIG_DIR` makes a bare `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` resolve against cwd, while `transcript-analysis.py` — invoked in the same block — raises on the same value | `[verified: ciso-reviewer finding this round against claude/.claude/scripts/_config_dir.py:8-12 and claude/.claude/hooks/_lib.sh:94-101]` |
| row24 | An unguarded failed command substitution in an assignment aborts all of `install.sh` under `set -e`, not just the report line; `:315` is currently unguarded | `[verified: staff-platform-engineer finding this round; install.sh:2 sets -e]` |
| row25 | Local read failures (EACCES, stale mount) are real, and all land on the under-disclosing side | `[verified: ciso-reviewer finding this round; claude/.claude/hooks/_lib.sh:595-596 names these same modes]` |
| row26 | Copying a config directory to bootstrap a new account carries the sentinel with it, and no account-identity string exists to content-address against the way `owner/repo` did for a repo | `[verified: ciso-reviewer finding this round]` — accepted residual, made auditable rather than closed |
| row27 | `CHANGELOG.md` carries `**Migration:**` clauses for breaking config-shape changes, and `test_skills.py:667-676`'s class docstring documents the removed `gh failure` outcome | `[verified: read CHANGELOG.md's Unreleased/Changed entries and test_skills.py:664-688 this session]` |
| row28 | The pinned `${var#...}`/`${var%...}` trim leaves interior whitespace intact, strips leading and trailing whitespace and newlines, and its guarded `$(cat ...)` survives `set -e` for both a nonexistent path and a `chmod 000` file | `[verified: staff-platform-engineer executed each case on this machine's real /bin/bash 3.2.57 this round, not a simulated version]` |
| row29 | `IFS= read -r mode < "$file"` is a **second** fail-open shape: it satisfies trim and guard requirements but reads only line 1, so `dollars\nallowance` enables disclosure | `[verified: staff-platform-engineer finding this round]` — the reach an implementer takes next once `tr -d` is forbidden |
| row30 | An unanchored compare is a **third** fail-open shape: `dollarsx`/`xdollars` pass every other case in the table | `[verified: staff-sdet finding this round]` |
| row31 | Absent an explicit shared-snippet test, nothing would fail if the SKILL.md gate and `install.sh`'s reporter drifted apart after merge — "both implement the same grammar" is a one-time authoring fact, not an enforced invariant | `[verified: staff-sdet finding this round]` — closed by pinning the same snippet in both sites plus the new `test_skills.py` anchored-compare assertion |

## Critical files

**Reuse:** the resolution idiom at `handoff/SKILL.md:153`;
`_sentinel_index_prompted_this_run` (`install.sh:235-240`) for the new reporter's
enable-hint suppression; the existing `_report_repo_sentinel` (`:276-286`) as the
structural template for `_report_account_sentinel`.

**Explicitly not reused:** `install.sh:315`'s `tr -d '[:space:]'`. It is the nearest
existing idiom and the wrong one — see Mode grammar.

| File | Change |
|---|---|
| `claude/.claude/skills/pr-description/SKILL.md` | Rewrite the gate at `:75` (account-scoped read, two outcomes). Adjust the closing sentence of `:84` from "a repo opting in" to account framing. **Leave untouched:** `:73`, `:77-83`, `:84`'s detached-HEAD and verbatim-embed rules, and `:91-100` — all delimited-block lifecycle, not gate. |
| `install.sh` | Delete row `:228`; delete `_report_content_addressed_sentinel` `:288-325`; replace the `repo-content-addressed` dispatch arm `:357-359` with an `account` arm; add `_report_account_sentinel`; add the `account` row; update the scope enumeration `:204-209` and the presence-only framing at `:210-214`, which stops being true once a row's content determines its state. |
| `.claude/pr-cost-disclosure` | Delete (tracked file). |
| `.gitignore` | Append `claude/.claude/pr-cost-disclosure` to the stow-fold block `:49-60` — the sentinel now lives under `~/.claude/`, so an in-tree copy under directory-fold becomes possible for the first time. |
| `claude/.claude/hooks/tests/test_install_sh_sentinel_inventory.py` | Replace the three content-addressed tests (`:336`, `:348`, `:364`) with the account-scope set below. Drop the six now-dead `_fake_gh` stubs (`:259`, `:301`, `:315`, `:328`, `:392`, `:412`) — verified no other path in the retained fixture range invokes `gh`. Extend `test_non_tty_run_leaves_full_home_snapshot_byte_identical` (`:244-290`) so its isolated `$HOME` *contains* a `dollars` sentinel: today's fixture never creates the file, so it could not catch a reporter that rewrites it in place. |
| `claude/.claude/skills/tests/test_skills.py` | Replace `test_declares_content_addressed_gate_check` (`:687-688`) with an assertion on the new gate text; leave `test_declares_pr_cost_delimiters` (`:682-685`) unchanged. **Add** a case asserting the skill body contains the anchored compare `[ "$mode" = "dollars" ]` verbatim — the line where all three fail-open reaches would surface, and the pin against the skill body and `install.sh` drifting apart. **Also rewrite the class docstring (`:667-676`)** — it currently documents `gh failure -> existing block left untouched` and "the content-addressed gate check", both of which describe the removed design and would ship stale. |
| `CHANGELOG.md` | Add a `## [Unreleased]` → `### Changed` entry, following the file's established shape: what moved, from which path to which, and a bold `**Migration:**` clause carrying the one-line re-enable command. This is the documented discovery surface for a breaking config-shape change; without it a stow user's only signal is a silently vanished `## Cost` section. |
| `docs/hooks.md` | Rewrite `## Non-hook opt-in sentinels` (`:48-55`) — every claim in it is content-addressing mechanics. The section title stays accurate. |
| `README.md` | Rewrite the `### PR cost disclosure` body (`:338-348`); enable snippet becomes a single `echo dollars > "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pr-cost-disclosure"`. TOC entry `:25` unchanged. |
| `docs/transcript-analysis.md` | Phrasing only at `:509` and `:515` — "a repo that opts into publishing" becomes account framing. The non-neutral-fields substance is unchanged and stays canonical here. |

**Not edited:** `.claude/plans/pr-cost-disclosure.md`. It is a merged historical
record of a shipped decision (root CLAUDE.md Axis 3) — this plan supersedes it in
effect, not by rewriting it.

## Verification

From the worktree (the contributor `.venv` lives at the main worktree root only):

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

The inventory tests are the real end-to-end check: they extract the
`INSTALL_TEST_FIXTURE: sentinel-inventory` block and execute it under an isolated
`$HOME`, so the new reporter runs for real rather than being pattern-matched. Required
cases, each a distinct branch of the grammar above:

| Case | Expected |
|---|---|
| sentinel absent | disabled |
| `dollars` | ENABLED (mode=dollars) |
| `dollars\n` (what the documented `echo` enable command actually writes) | ENABLED |
| `" dollars"` (leading whitespace only) | ENABLED — pins that the trim is bidirectional, not right-only |
| `DOLLARS` / `Dollars` | ENABLED — pins the lowercasing |
| empty file | disabled |
| whitespace-only (`" \n"`) | disabled |
| **`dol lars`** | **disabled** — pins that interior whitespace is not deleted (fail-open shape 1) |
| **`dollars\nallowance`** | **disabled** — pins that the whole file is read, not just line 1 (fail-open shape 2) |
| **`dollarsx` / `xdollars`** | **disabled** — pins an anchored equality compare, not a substring match (fail-open shape 3) |
| unrecognized value | disabled, and the literal value echoed in the report |
| `CLAUDE_CONFIG_DIR` set away from `$HOME/.claude`, sentinel in the config dir | ENABLED, and the resolved path printed |
| `CLAUDE_CONFIG_DIR` set away, sentinel present **only** at `$HOME/.claude` | disabled — pins resolution, not union |
| `CLAUDE_CONFIG_DIR` relative | disabled |
| `CLAUDE_CONFIG_DIR` set to a nonexistent directory | disabled, installer does not abort |
| sentinel present but unreadable (mode `000`) | disabled, installer does not abort |

The last two matter beyond their own assertion: they are what prove requirement 4 of
the grammar (a guarded read), and a regression there takes down all of `install.sh`
under `set -e`, not just one report line.

**Scope of the behavioral coverage, and what pins the two halves together.**
`pr-description` is a skill body — prose an agent follows — and this repo has no
harness that executes it, so `test_skills.py`'s Cost-section tests are source
tripwires proving the gate text is present, not that it behaves. `install.sh`'s
reporter is deterministic bash under the extraction harness above, so the case table
genuinely tests *it*.

The tempting claim — "both implement the same grammar, so testing one covers the
other" — would be a one-time authoring fact, not an enforced invariant: nothing would
fail if the two drifted after merge. Rather than record that as an accepted residual,
this plan removes the drift surface. Both sites carry the **same pinned snippet**
(above), and a new `test_skills.py` case asserts the skill body contains the anchored
compare `[ "$mode" = "dollars" ]` verbatim — so a future edit to either side that
changes the comparison shape fails a test instead of silently diverging. That is a
shape check, not a semantic-equivalence proof, but it pins the exact line where all
three known fail-open reaches would show up.

What genuinely remains manual-observation-only is the narrower LLM-driven half —
regenerate the block versus delete it — confirmed on this PR itself, which is a live
instance of the gate.

**Migration, one step, for the engineer:** this PR deletes
`.claude/pr-cost-disclosure`, so disclosure for this repo goes off until
`echo dollars > "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pr-cost-disclosure"` runs on the
authoring machine. Do that before `/ready-for-review` so this PR's own Cost section
demonstrates the new path.

## Out of scope

- **`allowance` mode** — Plan B. It needs a capture mechanism that does not exist:
  `rate_limits` reaches this repo only at statusline-render time and is not
  persisted. It is also a semantically different quantity — a point-in-time,
  account-wide reading, not attributable to a branch — so it needs its own heading
  and caveat, not a mode swap under `## Cost`. **Forward constraint this plan
  honors:** the `pr-cost:start`/`pr-cost:end` delimiters stay mode-agnostic so Plan B
  can reuse the block rather than introduce a second one.
- **Persisting statusline payloads to disk.** The obvious capture route for Plan B,
  deliberately not started here — `statusline-command.sh` writes nothing today
  (`:1-181`), and adding a write on every render is a decision that belongs with the
  plan that needs it.
- **A migration shim** for adopters who enabled the per-repo file. Row19: a leftover
  file becomes inert, not dangerous, and row20 puts the adoption surface at
  effectively this repo alone.
- **`_lib_autonomous_shipping_active` / `_lib_worktree_enforcement_active` union semantics.**
  This plan cites them as the pattern to avoid; it does not change them.
