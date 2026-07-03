# Fix require-worktree-for-git-writes precision: parse argv/segments instead of regexing the command string

GH-421

## Context

**Goal:** eliminate the false-positive flood from `require-worktree-for-git-writes.sh` by replacing raw-command-string regex/sed parsing with real shell tokenization and completing the read-only allowlist — while preserving the ~30 true positives that make the gate worth keeping and keeping the gate **fail-closed** on every uncertainty.

A full census of hook denials (1120 denials / 335 sessions, 2026-07-02) found this hook produced 391 denials — the largest of any hook — of which **361 (92%) were false positives**. The root cause is the mechanism: the hook judges git discipline by regexing/sed-splitting the raw Bash command string, which cannot distinguish live command words from prose inside quotes, heredoc bodies, or arguments, and cannot see the effective cwd a leading `cd` produces. The problem is specific to command-string-regex hooks; the census puts the state-checking gates (review markers, push gate, routing read) at 0–10% FP.

This misparse reproduced **three times live during planning** — Bash commands whose `echo` prose contained the literal `git subcommands` / `git -C` were denied as if they invoked git.

**Threat model (sets the design budget):** this is a *developer-machine guardrail* against the agent *accidentally* writing to the main working tree while a concurrent session is staged there. It is not an adversarial security boundary — the agent is cooperative, not an attacker probing for bypasses. So the design target is: kill the accidental-friction FPs, keep the genuine main-tree-write catches, and when a command is genuinely ambiguous, **deny** (the agent's fallback is trivial). It is explicitly *not* a target to model every exotic shell construct precisely — ambiguity resolves to deny, which is both safe and simple.

The 361 FPs break into three classes, and each maps to a piece of the fix:
- **157 read-only operations** (`status`, `log`, `merge-base`, `symbolic-ref`, `diff-tree`, `grep`, `ls-files`, `branch`) blocked by allowlist gaps or chain-misparse. Killed by completing the allowlist + not misparsing chains. **No cwd logic needed — reads are allowed regardless of cwd.**
- **17 data-as-code misparses** — git-adjacent prose inside heredocs/quotes/args extracted as bogus subcommands (`'git git'`, `'git state'`, `'git 4'`). Killed by quote/heredoc-aware tokenization: shlex won't find `git` inside a quoted string or heredoc body.
- **186 sanctioned-pattern chains** — `cd <worktree> && git <write>`, currently blanket-denied. Killed by resolving effective cwd for the write **only in the simple literal case**.

## Approach

Replace the hook's command-analysis core with a **quote- and heredoc-aware Python tokenizer** (stdlib `shlex` only) and keep the bash hook as orchestrator (marker gate, worktree detection via `rev-parse`, cwd resolution for writes). `python3` becomes a hard precondition for this hook (see §Dependency & rollback).

**Why Python, not bash:** the root cause is that bash word-splitting and sed operator-splitting cannot respect shell quoting or heredoc bodies. `shlex` is a stdlib call that *removes* the hand-rolled regex/sed — it is the simplification, not added complexity. The only hand-rolled piece is a small heredoc-body strip (no stdlib shell parser handles heredocs, and we cannot safely `eval`); justified in the commit message.

### The decision model (deliberately small)

The parser emits, per operator-split segment, whether it invokes git and (if so) the subcommand + any literal global `-C`, plus any leading literal `cd` target. The bash hook then judges:

| Case | Verdict |
|---|---|
| Segment has no `git`/`*/git` token | ignore |
| git subcommand is on the read-only allowlist | **allow** (cwd irrelevant — reads are allowed on the main tree today and stay so) |
| git **write** subcommand, effective cwd resolves cleanly to a **linked worktree** | **allow** |
| git **write** subcommand, effective cwd is the **main tree** | **deny** |
| git **write** subcommand, effective cwd **not cleanly resolvable** | **deny** |
| any parser/toolchain failure (see below) | **deny** |

"Effective cwd resolves cleanly" means: start from the JSON `.cwd`; apply leading literal `cd <path>` segments joined by `&&`/`;`; honor a single literal global `-C <path>` on the write itself (resolved against that cwd). **Anything outside this simple shape is treated as not cleanly resolvable → deny for writes:** a `cd` target needing expansion (`~`, `$VAR`, `$(…)`, glob), a git write inside a subshell/substitution group `(…)`/`$(…)`, a write reached via `||`, or multiple global `-C` flags. No paren-depth stack, no conditional-operator modeling, no cumulative-`-C` logic — every one of those collapses into the single "not cleanly resolvable → deny" branch. This is safe (writes the parser can't place are denied) and cheap (deny is the trivial branch); the agent's fallback is to `cd` in its own Bash call or run in a worktree.

Because reads are allowed by allowlist alone, **cwd resolution runs only for write subcommands** — the common case (any read, or a plain write already in a worktree session) needs no threading at all.

### Parser (`parse-git-command.py`)

Input: raw command string on **stdin** (avoids argv quoting / `ARG_MAX`). Steps:

1. **Strip heredoc bodies** with a simple line scan: a line whose redirection position contains `<<[-]?\s*["']?WORD["']?` opens a heredoc; drop lines until a whole-line match of `WORD` (leading tabs allowed for `<<-`). If a heredoc never terminates → emit the deny sentinel (do not drop to EOF). Kept intentionally small; malformed → deny, not clever recovery.
2. **Tokenize** with the `shlex` **class** (not `shlex.split()`, which rejects `punctuation_chars`), configured `posix=True, punctuation_chars=True, whitespace_split=True, commenters=''`. `whitespace_split=True` is required alongside `punctuation_chars` for shell-like word splitting; verify this flag combination against https://docs.python.org/3/library/shlex.html at implementation time and cite it in the docstring. `ValueError` (unbalanced quotes) → deny sentinel.
3. **Segment** on operator tokens (`;`, `&&`, `||`, `|`, `&`, `(`, `)`), tagging each with the preceding operator and whether it sits inside a `(`/`$(` group.
4. **Classify** each segment: strip leading `VAR=val` assignments; if **any** token is `git`/`*/git` the segment is a git invocation (this preserves the current all-words guarantee — no wrapper allowlist, so `time git commit` / `timeout 30 git push` are still caught), and the subcommand is the first bare word after that token (skipping git global flags that consume the next word: `-C -c --git-dir --work-tree --namespace --super-prefix --config-env). Capture a single literal global `-C` value if present. If the first word is `cd`, record its literal target.
5. **Emit** newline records with a pinned grammar: `CD\t<target>\t<preceding-op>\t<in-group:0|1>` (empty target for bare `cd`/`cd -`/`cd ~`/expansion targets), `GIT\t<subcmd>\t<C-path>\t<preceding-op>\t<in-group:0|1>` (empty `C-path` when absent), `SENTINEL\t<reason>`. Non-git/non-cd segments emit nothing.

### Bash hook flow

Preserve the cheap fast-path exits, then spawn python3 only when the command can actually reach a main-tree write:
1. git-word boundary regex (line 128) — no git word → exit 0.
2. `_lib_worktree_enforcement_active` — not opted in → exit 0.
3. **Relocation-aware worktree fast path.** Determine the session-cwd worktree status via the existing `--absolute-git-dir` ≠ `--git-common-dir` check. If the session cwd is a linked worktree **and** the command contains no `cd` token, no global `-C`, and no `(`/`$(` group (a cheap regex pre-check) → exit 0: nothing in the command can relocate a write to the main tree, so it is safe without the parser. **This guard is load-bearing** — the old blanket `cd … && git` deny used to catch a worktree session doing `cd <main> && git write`; an unconditional "in a worktree → exit 0" would reopen exactly that hole. Any `cd`/`-C`/subshell present → fall through to the parser regardless of session cwd.
4. Shell out to the parser (stdin). Walk records: accumulate literal `cd` targets over `&&`/`;` (any `cd` that is in-group, expansion-target/empty, or preceded by `||` marks the running cwd **not-cleanly-resolvable**), starting the running cwd at the session cwd. For each `GIT` write record, compute effective cwd (running cwd, or its literal `-C` resolved against running cwd) and apply the decision table via the same `--absolute-git-dir` ≠ `--git-common-dir` check — so a worktree session whose `cd <main>` relocates the write is denied, and a main session whose `cd <worktree>` relocates it is allowed, symmetrically. Reads → allow by allowlist. `SENTINEL` → `emit_deny` (carry the `could not determine the git subcommand` phrase where applicable, preserving the test-L120 reason contract).

### Fail-closed on toolchain failure

The gate must fail closed on its own tooling, mirroring the existing `_lib.sh`-absent deny (hook lines 39–42): `python3` absent from PATH, `parse-git-command.py` missing, nonzero exit, empty stdout, or an unrecognized record line all route to `emit_deny`, never allow. (The nudge hook fails *open* on missing python3 — correct for an advisory nudge, wrong for a gate.)

### Allowlist completion (closed enumeration)

Extend `_LIB_READONLY_GIT_SUBCMDS` in `_lib.sh` with exactly this closed set (no "etc." — the allowlist is a security surface): `merge-base`, `symbolic-ref`, `diff-tree`, `diff-index`, `diff-files`, `grep`, `show-ref`, `cherry`, `whatchanged`, `show-branch`, `range-diff`. All read-only; `cherry` (comparison) is distinct from the still-denied `cherry-pick`. `symbolic-ref` is dual-mode (`symbolic-ref HEAD <ref>` repoints HEAD but touches neither working tree nor index, so it cannot clobber uncommitted work) — include with an inline annotation mirroring the existing `branch`/`tag` "acceptable risk" notes. Unknown subcommands still deny (fail-closed). Consumed only by the git-writes hook (verified: `grep` returns only `_lib.sh` + the hook).

### Dependency & rollback

- **python3 hard precondition:** add an `install.sh` preflight warning when `python3` is absent (mirroring `_lib.sh`'s `timeout` soft-dep treatment); name the minimum version — `punctuation_chars` requires **Python 3.6+** — in the parser docstring and preflight.
- **Rollback escape hatch:** a wrong parser could mass-deny, and `git pull` to fetch a revert is itself denied (`merge` not allowlisted). Document the non-git escape in the hook header/PR: write `.claude/worktree-optout` (a file write, not a git op) or remove the machine sentinel, then pull. Revert the CLAUDE.md line-65 deletion **atomically with the hook**.

### Scope boundary (named DRY exception)

The shared `_lib_split_fragments` / `_lib_extract_git_subcmd` / `_lib_fragment_invokes_git` stay as-is — still used by `deny-pii-in-commits.sh`, `deny-private-project-refs.sh`, `require-ready-for-review.sh`, all at 0–10% FP, on commit-message/PR content where the heredoc-prose misparse rarely bites. Their boolean-fragment I/O differs from the records parser; retrofitting all three multiplies risk for hooks not in pain. The git-writes hook stops calling them; its local `extract_git_subcmd`, `command_chains_cd_then_git`, `cwd_anchor_note_if_chained`, `git_C_note_if_present` are deleted. Two parsers coexist deliberately — documented in the hook header and commit message.

### Obsolete instructions to remove

- `claude/.claude/CLAUDE.md` line 65 — the "never chain `cd <worktree-path> && git <op>` … never rely on `git -C`" bullet. **Delete** atomically with the hook (a literal `cd <path> && git` now works; the note stands only for expansion/subshell forms, which the deny message itself explains). 
- The hook header "Known limitation" paragraph (heredoc/quote misparse) — now fixed. **Rewrite** to describe the parser and residual limits (git reached via alias/variable/script indirection stays undecidable → fail-closed for writes where a git token is present but cwd is unresolvable; allow where no git token is detected).

**Not** removed: CLAUDE.md line 64 (Edit/Write worktree-path rule — governs the *file*-writes hook) and `feedback_use_worktrees.md` (worktree-usage strategy).

## Critical files

- **`claude/.claude/hooks/parse-git-command.py`** *(new)* — shlex tokenizer; small heredoc strip; `CD`/`GIT`/`SENTINEL` records per the pinned grammar. Pure string→records, independently unit-testable.
- **`claude/.claude/hooks/require-worktree-for-git-writes.sh`** — after the fast-path exits, shell out to the parser; accumulate literal `cd`; judge writes via the decision table; reuse `--absolute-git-dir`/`--git-common-dir`, `_lib_worktree_enforcement_active`, `emit_deny`. Delete the four local parsing/note functions. Rewrite header.
- **`claude/.claude/hooks/_lib.sh`** — extend `_LIB_READONLY_GIT_SUBCMDS` (closed set) with the `symbolic-ref` annotation.
- **`claude/.claude/hooks/tests/test_require_worktree_for_git_writes.py`** — rewrite affected cases + add regressions (below).
- **`claude/.claude/hooks/tests/test_parse_git_command.py`** *(new)* — parser unit tests.
- **`install.sh`** — python3 preflight warning.
- **`claude/.claude/CLAUDE.md`** — delete line-65 bullet.
- **`README.md`** — line 227 is a one-line role summary; add at most a clause that read-only ops and literal-`cd`-into-worktree writes are allowed. Caveats stay in the hook header.

### Reuse opportunities

`_lib_worktree_enforcement_active`, `_lib_parse_tool_input_or_deny`, `emit_deny`, `_lib_readonly_git_subcmds`, and the `--absolute-git-dir` vs `--git-common-dir` comparison — reused unchanged.

## Verification

**Parser unit tests** (`test_parse_git_command.py`, assert emitted records): heredoc body containing `git commit`; `<<-` tab-stripped heredoc; heredoc body containing the delimiter word; **unterminated** heredoc → SENTINEL; `echo "git history"` and bare `echo git commit`; quoted `git push` as an argument; multiline/CRLF command (pin the line-based strip does not fail open); `cd a && cd b && git`; relative `cd sub && git`; bare `cd`/`cd -`/`cd ~` and `cd "$VAR"`/`cd ~/x` (→ empty/expansion target); `git` inside `$()` and backticks (in-group flag set); `time git commit` / `timeout 30 git push` (git token still detected); pipe-into-git; trailing `&`; unbalanced quote → SENTINEL; empty `-C` field grammar.

**Hook integration tests** — enumerate every flipped/kept/deleted existing test:
- **Flip deny→allow:** `test_worktree_cwd_chained_cd_readonly_also_denied` (L550, `cd <main> && git status` — read-only is now allowed regardless of cwd; note intentional).
- **Flip, specify verdict:** `test_git_dash_C_flag_stripped` (L98, `git -C /tmp commit`) → **deny** as *not-cleanly-resolvable / outside-repo write*; stays deny.
- **Delete:** the anchor-note and `-C`-note tests (L377–491) and the `command_chains_cd_then_git` blanket-deny cases superseded by the model.
- **Keep, still deny:** worktree-session `cd <main> && git merge/push/reset` (L501–548, including `test_worktree_cwd_chained_cd_or_denied` at L538 — now denies via the `||`/not-cleanly-resolvable branch rather than the old blanket chained-cd deny); `git status && git commit`; `test_parse_failure_denies` (L120) with reason substring preserved.
- **New deny regressions (invariant catches, phrased as accidental misses not exploits):** worktree-session `cd "$REPO" && git reset --hard` and `cd ~/repo && git commit` → deny (expansion target); `git -C "$VAR" reset` → deny; `time git push` / `timeout 30 git commit` on main → deny; `(cd /worktree) && git commit` from a main-tree session → deny (in-group cd doesn't resolve); `cd /bad || git commit` → deny; `$(git reset --hard)` on main → deny; unterminated-heredoc-then-`git push` → deny; python3-absent → deny (PATH scrub).
- **New allow regressions:** main-tree-session `cd <worktree> && git commit` → allow; `git worktree add .claude/worktrees/<slug> -b <slug>` on main → allow (bootstrap preserved); `git merge-base`, `git symbolic-ref HEAD`, `git diff-tree`, `git grep foo` → allow; a **read with an out-of-repo global `-C`** from a main-tree session (`git -C /tmp log`) → allow (replaces the deleted L448 coverage — pins that reads never enter cwd resolution); `echo "git history"` / heredoc prose → allow.
- **Fixtures:** reuse `opted_in_with_worktree` (real `git worktree add`, conftest.py:103) so the git-dir/common-dir check resolves for both cd-into-worktree and cd-into-main directions; do not mock worktree paths.

**Suite + lint from a worktree:** `../../../.venv/bin/pytest claude/.claude/hooks/tests/ -q` and `../../../.venv/bin/ruff check claude/.claude/`.

**Live smoke:** in an opted-in worktree confirm `cd <worktree> && git commit` allowed and bare main-tree `git commit` denied; confirm an `echo` with git-word prose no longer denies; confirm the `shlex` flag combination against the official docs.

## Out of scope

- The three shared-`_lib` hooks (`deny-pii`, `deny-private-project-refs`, `require-ready-for-review`) — low FP, untouched.
- `require-worktree-for-file-writes.sh` — operates on `FILE_PATH`, no misparse class.
- Inverting the allowlist to a write-denylist — considered and rejected; fail-closed is the correct default.
- Precise modeling of subshell/`||`/multi-`-C` cwd semantics — deliberately collapsed into "not cleanly resolvable → deny for writes" rather than adding machinery for constructs that are rare and safe to deny.
- **Read-cwd confusion** (an agent `cd`s into the main tree and reads its state while believing it is still looking at its worktree, drawing wrong conclusions from a real but misattributed result) — raised and set aside. This is a correctness risk, not the data-loss invariant this gate protects; reads are intentionally cwd-independent here (blocking them by cwd would reintroduce part of the 157-FP read-only bucket this fix removes), and the hook only ever sees Bash commands containing `git` — the same confusion with `cat`/`ls`/the Read tool is invisible to it regardless. Mitigation lives at the session-discipline layer (treat the remote as source of truth; re-`fetch`/`gh pr view` rather than trusting local checkout state), not in this gate.
