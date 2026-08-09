# Transcript corpus: multi-account scope

## Context

**Goal: make transcript tooling report the whole declared corpus, and always state what it scanned.**

Agents evaluating claude-config's own features scan one Claude config
directory and report a corpus statistic. In the session that prompted this,
that statistic covered 371 of 1,014 sessions — 37% — and nothing in the output
revealed the gap; the engineer caught it by asking.

**Scope of the fix, stated honestly.** This plan closes the case where a
corpus *is* declared and a scan silently covers part of it, and it makes scope
disclosure unconditional so a scan with nothing declared says so too. It does
not auto-detect undeclared accounts — auto-discovery was rejected twice before
and is rejected again here; no tool can name an account it was never told
about. What it can always do, and now does, is state the root count it
actually used, whether or not that count is one.

Two needs are conflated in the current code, and separating them shapes this
plan:

- **Root resolution** — honor whichever config dir is active and find a named
  session wherever it lives. No union.
- **Corpus union** — scan every declared config dir, because a stowed skill,
  hook, or agent fires in every repo under every account.

**User surface:** every user who clones and stows this repo, not the session
owner alone; `claude/` ships on `git pull`. Threat model is cross-account and
cross-engagement exposure in tool output, on a machine where separate Claude
accounts exist to keep engagements apart, feeding a **public** repo.

## Approach

### Phase A — One resolver

Today, root resolution is four mechanisms: the module global `PROJECTS_DIR`
(`transcript-analysis.py:28`), its reassignment in `main()` for the top-level
`--config-dir` (`:5486-5487`), the per-subcommand repeatable `--config-dir`
that only `cost`/`context-distribution` read (`_resolve_cost_roots:3452`), and
the refusal at `:5471-5485` that exists solely to keep the first two from
diverging. Adding declared roots as a fifth parallel path is what breaks the
wiring. Phase A collapses all of them into one function, keeping
`PROJECTS_DIR` as that function's mutable, monkeypatchable base rather than
retiring it — retiring it outright would silently de-isolate the `fake_projects`
fixture (756 references) plus 21 direct `monkeypatch.setattr` sites (see
Verification §2).

**Step 1 — Roots file parser in `_config_dir.py`.**
Add `declared_transcript_roots()` returning the config dirs listed in
`~/.claude/transcript-config-dirs`, deduped by resolved real path — **not**
including `config_dir()` itself; Step 3's resolver adds that. Returns
**config dirs**, matching the file's contents; callers needing projects dirs
derive `d / "projects"` themselves — this unit distinction is the one thing to
get right, since every downstream site depends on it.

Port `read_configured_roots()` (`cleanup-merged-branches.sh:138-153`)
faithfully; three behaviors do not translate naively:

- **Tilde:** bash does literal `~`/`~/` prefix substitution
  (`cleanup-merged-branches.sh:148-152`), no passwd-database lookup. Use
  explicit `$HOME` prefix replacement, **not** `Path.expanduser()`, which also
  expands `~otheruser`.
- **Comments:** bash `case '#'*` matches a *leading* `#` only (`:147`). Do not
  use `line.split("#")[0]` — it truncates a legitimate path containing `#`.
- **Encoding:** bash is byte-oriented. Read with `errors="replace"`; an
  unhandled `UnicodeDecodeError` here crashes the entire toolkit, since this is
  on the default path. Strip an explicit ASCII whitespace set, not
  `str.strip()`, which also eats NBSP and U+3000.

Do the file I/O **inside the function**, never at import.

A declared path that is not a directory, lacks `projects/`, or raises `OSError`
during validation (a permissions failure, not just a missing path) is skipped
with a warning naming it **by index** (`declared root 3 unreadable`), never by
path or in a propagated traceback — the path identifies an engagement.
Skipping rather than exiting is deliberate: `_resolve_cost_roots:3485` exits 2
for a path typed on the command line this second; a stale line in a config
file must not break every invocation.

`TRANSCRIPT_CONFIG_DIRS_FILE` is a test seam mirroring
`CLEANUP_MERGED_BRANCHES_ROOTS_FILE` (`cleanup-merged-branches.sh:114-118`).

Lighter primitives rejected: a `CLAUDE_CONFIG_DIRS` env var (no in-repo
precedent, does not reach cron) and explicit `--config-dir` only (the status
quo; a forgotten flag is the reported failure).

**Step 2 — Add the `.gitignore` entry.**
Add `claude/.claude/transcript-config-dirs`, mirroring `.gitignore:34`
(`private-projects.md`'s belt-and-suspenders entry). Verified: an anchored
single-file pattern matching that path syntax is correct and
`git check-ignore` currently returns nothing for it.

**Step 3 — One resolver function, used everywhere including `cost`.**
Add `_resolve_scan_roots(parsed: argparse.Namespace) -> list[Path]` in
`transcript-analysis.py`, mirroring `_resolve_cost_roots`'s existing shape.
Precedence: an explicit top-level `--config-dir` **overrides** everything else
(returns `[Path(parsed.config_dir) / "projects"]` alone); absent that, the
base is `[PROJECTS_DIR]` (the module global — still `config_dir() / "projects"`
at import, still reassignable by a test's `monkeypatch.setattr(_mod,
"PROJECTS_DIR", ...)`) plus `d / "projects"` for each of
`declared_transcript_roots()`, deduped by resolved real path.

**This is the fix for the flagship bug.** `_resolve_cost_roots`'s own
`default_dir = config_dir()` (`:3480`) is replaced with
`[config_dir()] + declared_transcript_roots()` — **config-dir units, matching
`_resolve_cost_roots`'s existing `seen_resolved`/`config_dirs` bookkeeping**,
not `_resolve_scan_roots`'s projects-dir output. Substituting the latter would
break the dedup: `seen_resolved` compares `candidate.resolve()` against
config-dir paths, so a projects-dir entry never matches and the active
profile's own root would silently duplicate, tripping the `--no-redact`
refusal at `:3500` for an operator who passed no `--config-dir` extras at all.
`_resolve_cost_roots`'s existing final line
(`[d / "projects" for d in config_dirs]`) still does the one unit conversion,
unchanged. Today `_resolve_cost_roots` calls `config_dir()` directly and never
reads the declared-roots file, so `cost`, the subcommand that produces the
corpus statistic from the original failure, would stay single-root even after
every other step in this plan lands. `--config-dir` extras (cost's own
repeatable flag) still append after the declared-roots entries, unaffected.

Delete the refusal at `:5471-5485` — it existed only to keep the global and
`_resolve_cost_roots` from diverging, and `_resolve_scan_roots` is now the one
place that can diverge. Keep the top-level `--config-dir` reassignment of
`PROJECTS_DIR` in `main()` (`:5486-5487`) — it remains meaningful, since
`_resolve_scan_roots` reads `PROJECTS_DIR` as its base at call time, so
reassigning it still changes every subcommand's single-root default exactly as
today. Update the now-partially-false docstring invariant at `:2044-2052` to
describe the new precedence instead of removing it.

**Ordinal stability, from one shared source, not a local sort inside
`_build_redact_map`.** `_build_redact_map` assigns `account-{root_idx + 1}` by
**position in its `roots` argument** (`:3053`) — but two other sites derive
the same ordinal independently, from their own caller-side list order:
`_root_index_for_path` at `:3760` (used to build `cost`'s per-row redact keys)
and the `account_col` line at `:3886`. If `_build_redact_map` alone sorted its
argument by resolved path, its ordinals would diverge from those two sites'
unsorted order, and every multi-root redacted `cost` row would miss its own
key and fail closed (`AssertionError:3775`) on every run, not just when
profiles differ.

Fix with one shared helper instead: `_redaction_ordinals(roots: Sequence[Path])
-> dict[Path, int]`, resolved-path-sorted once, returning `{resolved_root:
ordinal}`. `_build_redact_map`, `_root_index_for_path` (`:3760`), and the
`account_col` line (`:3886`) all look up the same dict rather than each
computing `root_idx` from local list position — single source of truth for the
ordinal, consumed identically everywhere it's assigned. This is what actually
makes the same physical account read as `account-N` regardless of which
profile is active, without desyncing the three sites that currently derive it
independently. `_resolve_scan_roots`'s own "active profile first" return order
is unaffected and stays correct for scanning and for Step 15's lookup
preference — only the redaction ordinal needs the profile-independent sort,
and now only one function computes it.

**Step 4 — Thread `_resolve_scan_roots`'s output through every scope site.**
`_resolve_project_scope` (`:2003`) funnels 19 `_resolve_project_scope`
call sites; only `cost` (`:3658`) and `context-distribution` (`:3976`) pass
`roots` today. Name every site that needs the resolved list explicitly:

- `_iter_scoped_sessions`'s own `(PROJECTS_DIR,)` fallback (`:1967`)
- `_build_redact_map`'s own fallback (`:3037`)
- `_cost_report`'s own fallback (`:3632`)
- `_context_distribution_report`'s own fallback (`:3955`)
- the `--this-repo` branch inside `_resolve_project_scope` (`:2071`), which
  today forwards `roots=roots` but the caller passes `roots=None` — needs the
  resolved list threaded from the caller side
- **the `--this-repo` fail-closed check at `:2061`**:
  `(PROJECTS_DIR / slug).is_dir()` reads the global directly. This guard only
  fires when a top-level `--config-dir` was passed, and under Step 3's
  precedence that case already makes `PROJECTS_DIR`'s reassignment (`:5486`,
  kept — see below) match `_resolve_scan_roots`'s override branch exactly, so
  no divergence is currently reachable through this path. Change it anyway,
  to `any((root / slug).is_dir() for root in roots for slug in slugs)`: a
  robustness improvement against a future precedence change, not a fix for a
  reachable bug today — and it needs its own deny-case test (Verification §5)
  so an over-broad rewrite (e.g. dropping the `config_dir_arg` gate entirely)
  doesn't silently disable the check.
- **`cmd_skill_invocation`'s two call sites**, `:2146`
  (`iter_sessions(PROJECTS_DIR, projects_arg, …)`) and `:2148`
  (`_iter_scoped_sessions(_repo_scoped_project_slugs(), include_subagents)`
  with no `roots`): `:2146` becomes
  `_iter_glob_scoped_sessions(roots, projects_arg, include_subagents)` when
  `len(roots) > 1`, `iter_sessions(roots[0], projects_arg, …)` otherwise
  (preserving `iter_sessions`' documented flat-sort at single root); `:2148`
  threads `roots=roots` into `_iter_scoped_sessions`. This is the subcommand
  for "did this stowed skill fire" — the question behind the reported bug —
  so it cannot stay outside the funnel.
- **`cmd_skill_invocation`'s own inline header at `:2211`**
  (`print(f"SKILL INVOCATION SOURCES ({...})")`) is not built by
  `_resolved_scope_header`/`_print_resolved_scope` at all. Route it through
  `_print_resolved_scope("skill-invocation", "; ".join(scope_parts))` (Step
  5's shared builder) so this subcommand gets the same provenance line as
  every other — otherwise Step 5's union ships silently, with no disclosure,
  on the exact subcommand the bug was found in.

Its `OUTPUT INVARIANT` comment (`:2130-2144`) states the output is routinely
quoted into public PR descriptions and that the control is scoping to this
repo's own project dirs by name. Unioning the **default** repo-scoped path
preserves that control in kind, not unchanged: `_iter_scoped_sessions`
matches by exact basename (`:1973`), never `Path.glob` (`:1958-1965`), so no
wildcard bleed — but the `[/.]→-` slug collision residual risk (Step 21) now
applies per added root, so collision probability grows linearly with declared
roots, same as it does for the cost family. Update the comment to say both:
the control is preserved, and its residual risk scales with root count. The
`--projects` escape hatch at `:2146` unions too under the `len(roots) > 1`
branch above; its reach — not its publish-unsafe label — changes from one
account's every project to every declared account's every project.

### Phase B — Honest provenance and redaction

**Step 5 — Root count in the scope header, unconditionally, via a required
parameter.** `_resolved_scope_header()`/`_print_resolved_scope()` (`:2082`,
`:2089`) are called from 23 existing sites plus the new `:2211` (Step 4). Add
`roots: Sequence[Path]` as a **required**, not defaulted, parameter to both —
a call site that fails to pass it is a `TypeError` caught at implementation
and test time, not a silently-wrong `"1 root"` line printed while the
subcommand actually scanned N. Every one of the 24 sites must be updated in
this step, not just the ones covered by Verification's format-outlier tests.

Header text: `"1 root (no ~/.claude/transcript-config-dirs declared)"` at
single root, `"N roots"` above one, plus any declared root skipped by index.
Every single-account user's output gains this one line — the change is
deliberate, not accidental: it is what makes the tool self-disclosing at the
exact zero-declared-roots state that produced the original bug, rather than
only once an operator has already declared a second account.

Do **not** put per-root session counts in the header:
`_resolved_scope_header` is a pure string builder invoked before the lazy
iterator is consumed (`cmd_buckets:450-451`); per-root counts would force a
second full scan of every root on every subcommand.

Keep `_resolved_scope_header`'s return a **single line** — `judgment-pair`
writes it into an `--out` file (`:1773`) as a one-line contract. Route the
root-skipped-by-index detail through the existing stderr warning path
(`_print_resolved_scope`, already routed to stderr for `audit-routing-samples`
at `:2089-2096` since its stdout is a JSON stream), not into the header string
itself.

**Step 6 — A per-root stderr progress line above one root.**
Union is now the default for 19 subcommands plus `skill-invocation`. Measured
on this workstation: ~9s per root at top-level scope, ~16s with
`include_subagents`, so a four-root union runs ~35–65s against ~9s today — an
interactive subcommand with no output for over half a minute reads as hung,
which is what drives agents back to hand-rolled globs in the first place.
`_iter_scoped_sessions` and `_iter_glob_scoped_sessions` already loop over
roots (`:1970`, `:1994`); print one stderr line as each root starts, gated on
`len(roots) > 1` so single-root output stays untouched beyond Step 5's header
line.

**Step 7 — Redaction stays cost-family only.**
`_build_redact_map` (`:3005`) has exactly two call sites: `cmd_audit_routing`
(`:3116`, `roots=None` today) and `cost` (`:3689`). `context-distribution`'s
`redact` flag (`:3953`) gates only the DO-NOT-PUBLISH banner and the
`--no-redact` refusal — it prints no project labels and calls
`_build_redact_map` nowhere, so it needs no redaction wiring change at all.

`cmd_audit_routing` needs two changes, not one: pass `roots` from Step 4's
threading (fixes the map's own scope), **and** change how each row looks up
its label. `row["proj_label"]` (`:3230`, from `_derive_proj_label:3130`) is a
flat string; once `roots` has more than one entry, `_build_redact_map`'s keys
become `(root_idx, label)` tuples via the shared `_redaction_ordinals` helper
(Step 3), so a flat-string lookup misses every row and every label collapses
to `_REDACT_MAP_MISS_TOKEN` — the opposite of fixed. `cmd_audit_routing` must
look up each row's ordinal via the same `_redaction_ordinals` mapping cost
uses at `_root_index_for_path:3760`, keyed off which root the row's session
came from, not a bare label.

**No redaction is added to `buckets`, `review-trace`, `fail-seq`, `struggle`,
`duration`, `subagents`, or `pr-link`.** None of them call
`_build_redact_map`, and what they print is not what it maps —
`review-trace:1572` prints an absolute JSONL path, the others print branch
names; neither is a project label. These subcommands stay raw under the
default union. This is an accepted, code-unenforced risk, not a fixed gap:
`--projects '*'` already defaulted to every project on one account, so a
flagless invocation could not cross an account boundary before this change,
and after it every one of these seven can. The alternative — a code-level
opt-in gate on these seven, matching cost's stricter posture — was
considered and set aside because it re-introduces exactly the forgotten-flag
failure the automatic union exists to remove; the disposition instead is
Step 11's rewrite of the skill's publish-safety guidance, which is a real
control only if an agent actually reads and follows it before quoting output.

Add no new `--no-redact` on these seven. The cost family's existing refusal
above one root (`:3500`) stands, but its trigger condition changes: today it
fires only when the operator passes an explicit extra `--config-dir` on the
command line; after Step 3, `len(config_dirs) > 1` is true by default for
anyone with a populated roots file, so `cost --no-redact` becomes unreachable
for that operator with no flag change on their part. State this plainly in
Step 12 rather than leaving it an implicit side effect.

**Step 8 — Document the amplification and the escape hatch, in the same PR
that ships the regression.**
`_build_redact_map`'s docstring (`:3013-3022`) already notes it reads every
project's transcript bytes even under `--this-repo`, and that ordinals are a
structural fingerprint of the operator's other projects — a tradeoff weighed
at one root. Document in `docs/transcript-analysis.md` that the union
multiplies both the byte-read cost and that fingerprint's information content,
alongside the measured per-root cost from Step 6 and the one existing escape
hatch: an explicit single top-level `--config-dir` still overrides the union
back to one root (Step 3's precedence), which is the narrowing control an
operator has today if the default becomes too slow or too wide for a given
invocation. This doc write ships in PR 1 (see "Suggested PR split"), not
deferred to a later documentation pass — a default-path regression should not
ship silently even for one release cycle.

Separately: once ordinals are stable across runs with an unchanged root set
(this fix), two separately published redacted reports from the same
declared-roots file become correlatable by ordinal — a new property, not
merely an amplified old one. State it, and reconcile it against
`transcript-analysis/SKILL.md:53`'s current claim that "redacted labels are
not comparable between reports" (Step 12) — that claim becomes conditionally
false and needs qualifying, not deleting: labels are comparable only when the
same roots file produced both reports.

### Phase C — Affordance and skills

**Step 9 — Add a `sessions --paths` subcommand.**
No subcommand emits transcript paths today (21 subparsers, none list files).
Add `sessions --paths [--this-repo | --projects GLOB] [--include-subagents]`
emitting one absolute transcript path per line, sourced from
`_resolve_scan_roots` plus `_resolve_project_scope`, honoring
`include_subagents` via the existing `_read_session_file` merge
(`:394-407`) rather than a flat glob. Route its own scope header to stderr,
matching `audit-routing-samples`'s existing convention (`:2089-2096`), since
its stdout is meant to be piped to `xargs`/`Read`, not mixed with a header
line.

**Step 10 — `transcript-narrative/SKILL.md`.**
Rewrite Step 2 to call `sessions --paths --include-subagents` and read the
returned files, replacing the current literal instruction to read
`~/.claude/projects/*/` (line 18) — which hardcodes one account. Also rewrite
line 28, which currently says "Read the JSONL directly with the Read tool or
a short inline shell expression": that inline-shell permission is what invites
the next hand-rolled glob. Replace it with an instruction to obtain the path
set from `sessions --paths` and read only those files, keeping the existing
no-script-vendoring rule intact for everything downstream of path discovery.

Line 18's flat-glob defect is narrower than the corpus miss suggests: subagent
records carry `isSidechain: true` (`_read_session_file:379-380`), and this
skill's Step 2 already excludes sidechain turns (`:18-21`) by design — a flat
glob is correct for its stated purpose of capturing main-thread user prompts.
Per the engineer's own account of the originating session, the nested-subagent
undercount came from a different, ad hoc glob written during that session's
live exploration, not from this skill's documented instructions; Step 9's
affordance is what closes that gap generally, and Step 10 closes this skill's
one narrower defect (single-account scope) specifically.

**Step 11 — `transcript-analysis/SKILL.md`.**
Move corpus scope out of the `cost`-only caveat (`:52`) into one governing
every subcommand: what the declared-roots file does, that the header now
always states root count, and that redaction covers `cost`/
`context-distribution`/`audit-routing` only.

**Replace**, not annotate, the existing remedy at `:54`: "Run it with
`--this-repo` before quoting output anywhere public." After Step 21 lifts the
multi-root `--this-repo` refusal, `--this-repo` no longer implies single-account
output — quoting `review-trace --this-repo` output can now span every declared
account. State the actual remedy: no flag currently guarantees single-account
scope on these seven subcommands short of an explicit single `--config-dir`;
name that as the one narrowing control, matching Step 8's documented escape
hatch.

Also add one shipped sentence — not only recorded in the ledger and Out of
Scope, which never ship — noting that `--branches` pools same-named branches
across every declared account with no per-account signal (ledger row 9): an
operator filtering by branch under a multi-root scope is reading a pooled
tally, not one account's history.

Reconcile `:53`'s "redacted labels are not comparable between reports" per
Step 8: true only across differing declared-roots files, not universally now
that ordinals are stable within a fixed root set.

**Step 12 — Interpreter-path fixes and skill-body contract test.**
Fix the hardcoded `~/.claude/scripts/transcript-analysis.py` interpreter path,
wrong under a relocated config dir, in the three in-scope skills: 6 occurrences
in `transcript-narrative`, 7 in `transcript-analysis`, 2 in
`error-mode-analysis`. `ready-for-review/SKILL.md` also contains the literal
but is out of scope for this plan — a `test_skills.py` contract asserting no
`SKILL.md` contains it repo-wide would red-fail on that file for an unrelated
reason; scope the new test to the three files this plan touches by name, not
a repo-wide grep.

**Step 13 — Scope-confirmation rule in `transcript-analysis/SKILL.md`.**
Since Step 5 makes disclosure unconditional and tool-emitted, this step
shrinks to one line: before quoting a corpus-wide statistic derived from this
toolkit's output, the agent must include the resolved-scope header line
verbatim in what it reports, and if that line reads "1 root (no
~/.claude/transcript-config-dirs declared)," ask the user whether other Claude
accounts exist before treating the number as complete. Pin this as a literal
required sentence in the skill body, and extend Step 12's contract test to
assert the sentence's presence — a prose caveat with no enforcement is the
same failure class as the existing `:53` caveat that did not prevent the
original miss.

### Phase D — Siblings

**Step 14 — `evals/run_skill_evals.py:650`.**
Replace `Path.home()/".claude"/"projects"` with `config_dir()`. Resolution
only, no union — it writes and cleans its own sessions. This changes a
**deletion** target; needs a test that cleanup removes only session dirs the
run created, under a `tmp_path` config dir.

**Step 15 — `analyze-context.py`.**
Search `_resolve_scan_roots`'s output to locate one session, then report on
one — active profile (`PROJECTS_DIR`) first, matching that function's
"active-first" convention, which is the right preference for session lookup
even though `_build_redact_map`'s internal sort (Step 3) is deliberately
different for labeling. Three specifics:
- `find_session_jsonl:36` matches by **prefix**
  (`glob(f"{session_id}*.jsonl")`), so a short id can match different sessions
  under two roots. First-match-wins must warn on ambiguity rather than
  silently returning the active profile's match.
- `latest_session_jsonl:52` is keyed on `project_key`; the same cwd exists
  under both roots, so "latest" must compare mtimes across roots, not just
  return the active profile's.
- `SESSION_META_DIR` (`:30`) resolves from the active config dir. Pair
  metadata with the root the transcript actually came from, not the active
  root, or a cross-root lookup gets root A's usage data paired with root B's
  transcript.

**Step 16 — `post-crash-sessions.py`.**
Default its existing repeatable `--config-dir` to
`declared_transcript_roots()`, but keep its own validation: it accepts a dir
containing `sessions/` **or** `projects/` (`:1069-1071`), and Step 1's parser
requires `projects/`. Do not route it through the stricter check — that would
silently drop sessions-only roots, the crashed-fresh-account case the tool
exists for.

**Step 17 — `token-analyzer.py`.**
Take the union: it measures cost and cache efficiency across whatever corpus
exists, the same class of question as `cost`. Also route through
`iter_sessions`/`_read_session_file(include_subagents=True)` rather than
`PROJECTS_DIR.glob("*/*.jsonl")` (`:52`) — that flat shape skips nested
subagent transcripts, so its cache number stays partial across a now-wider
corpus. Same flat-shape fix at `analyze-context.py:40`.

**Step 18 — `install.sh` advisory.**
Add `check_transcript_config_dirs()` mirroring `check_private_projects_file`
(`:228-241`): print-only, no prompt, no write, no TTY guard (that guard exists
at `:158` only because `_prompt_sentinel_opt_in` calls `read`).

Concrete mechanism — `install.sh` has no `realpath`/`readlink -f` today and
runs before stow, so it cannot source `_lib.sh`; the only portable primitive
on macOS's stock toolchain is `cd ... && pwd -P`:

```sh
_resolved_config_dir="$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" 2>/dev/null && pwd -P)"
_resolved_default_dir="$(cd "$HOME/.claude" 2>/dev/null && pwd -P)"
if [ -n "$_resolved_config_dir" ] && [ "$_resolved_config_dir" != "$_resolved_default_dir" ]; then
  # print the TIP — this profile's config dir differs from the default
fi
```

Detect `[ -L "$HOME/.claude" ]` and name the resolved target with
`readlink "$HOME/.claude"` (one level; `install.sh:104` already uses bare
`readlink` for the same case, so this matches existing precedent rather than
introducing `readlink -f`). `install.sh:104-106` proves the tree-folded state
exists in the wild; a user in it would otherwise create the roots file inside
the checkout with no warning at creation time.

Wrap in an `INSTALL_TEST_FIXTURE` marker block so it is testable —
`check_private_projects_file` has no test today, so parity alone means zero
coverage.

**Step 19 — Documentation.**
`docs/transcript-analysis.md` (`:3`, `:11-34`, `:503`), `docs/scripts.md`
(`:5`, `:28`, `:35`), `README.md`: the roots file, the union default, the
cost-family-only redaction boundary, and the lifted `--this-repo` restriction.
The amplification/cost/escape-hatch documentation from Step 8 ships earlier,
in PR 1 — this step covers the remaining, non-time-sensitive documentation.

### Phase E — `--this-repo` across roots

**Step 20 — Add `context-distribution`'s per-root diagnostic in Phase A, not
here.** Moved: `_context_distribution_report:3976-3979` has no
`_scan_root_transcripts`-equivalent pass, unlike `cost:3670-3674`. Once Step 3
lands, `context-distribution` scans multiple roots by default via the declared
file — with no explicit flag and independent of whether Step 21 below lifts
anything — so a multi-root run matching nothing would print an empty report
silently starting in PR 1. This diagnostic must ship with Step 3, in Phase A,
not deferred to this phase.

**Step 21 — Lift the `--this-repo` + explicit-`--config-dir` refusal.**
`_resolve_cost_roots:3472` refuses `--this-repo` when the operator passes an
explicit extra `--config-dir` on the command line, citing "--this-repo cannot
filter a foreign config dir's worktrees." That rationale does not match the
mechanism: `_iter_scoped_sessions:1973` matches `p.name in wanted` — basename
equality, root-independent — and `_path_to_project_slug:1780` derives slugs
from `git worktree list --porcelain` alone, so one checkout yields the same
slug under every config dir. The guard is also net-negative for minimization:
the multi-root path it leaves open, `--projects "*"`, is strictly wider,
disabling what `_repo_scoped_project_slugs:1794` calls "the minimization
control." Note this guard's trigger condition (`extra_config_dirs` truthiness)
is narrower than "multi-root" after Step 3 — a populated roots file already
makes `--this-repo` multi-root without tripping this refusal at all, since no
`--config-dir` flag was passed. Lifting this refusal closes the remaining case:
an operator combining an explicit extra `--config-dir` with `--this-repo` on
the command line.

Residual risk — `[/.]→-` is not injective, so `/a/b.c` and `/a/b/c` collide —
is present identically at single root today and is not addressed by the guard.

### Assumption ledger

**Root problem:** a partial transcript scan is indistinguishable from a
complete one in the tool's output.

**Given:** Claude Code exposes no first-party multi-config-dir concept;
`CLAUDE_CONFIG_DIR` selects exactly one. Vendor-imposed platform boundary.
`[verified: register-marketplace.sh:7-9, citing the .claude directory reference]`

**Mechanisms:**

- Roots file — `anchors: root`. Two lighter primitives rejected in Step 1.
- Single resolver, `PROJECTS_DIR` retained as its mutable base — `anchors:
  root`. Lighter primitives rejected: patch the scope default in place without
  a named resolver function (makes top-level `--config-dir` a silent no-op,
  since a `config_dir()`-derived default never reads the reassignment); retire
  `PROJECTS_DIR` entirely (breaks the `fake_projects`/direct-`setattr`
  monkeypatch seam — 756 fixture references plus 21 direct sites — with no CI
  signal, since CI's `$HOME/.claude` is simply absent rather than wrong).
- Unconditional header — `anchors: root`. `[engineer-verified]`: chosen over a
  conditional header specifically because the conditional form stays silent in
  the zero-declared-roots state that produced the reported bug.
- Cost-family-only redaction — `anchors: row 7`. `[engineer-verified]`: chosen
  over a matching code-level gate on the seven raw subcommands, which would
  reintroduce the forgotten-flag failure the union exists to remove.
- Lifting the `--this-repo` + explicit-`--config-dir` refusal — `anchors: row
  3`.

**Assumptions:**

| # | Assumption | Tag |
|---|---|---|
| 1 | `_resolve_project_scope` funnels 19 call sites; `cmd_skill_invocation` (`:2146`, `:2148`, `:2211`), `_resolve_cost_roots:3480`, and the `--this-repo` fail-closed check (`:2061`) sit outside and are named individually in Step 3–4 | `[verified: staff-backend-engineer + staff-platform-engineer re-review call-site walk]` |
| 2 | Multi-root dedup by resolved real path is correct on both iteration paths | `[verified: :1962-1964, :1988-1992]` |
| 3 | Slug matching is location-independent, so cross-account `--this-repo` is well-defined | `[verified: :1973, :1780; replacement coverage is Verification §5's three new tests]` |
| 4 | `_build_redact_map` covers project labels only, not branch names or transcript paths | `[verified: :2930, :3005-3062]` |
| 5 | No `permissions.allow` entry exists for these scripts, so no settings change is needed | `[verified: claude/.claude/settings.json, grep]` |
| 6 | Non-redacted subcommands print raw branch names and paths and will continue to under multi-root | `[verified: :496-503, :552, :617-621, :1572, :2674]` |
| 7 | The engineer chose automatic union, unconditional header disclosure, cost-family-only redaction (accepted risk on the other seven), one consolidated resolver retaining `PROJECTS_DIR` as its base, and a `sessions --paths` affordance | `[engineer-verified]` |
| 8 | `install.sh` runs once, not per profile; `register-marketplace` is the per-profile entry point | `[engineer-verified]` |
| 9 | `--branches` filters records by `gitBranch` string and never consults project dir or root, so it does **not** narrow a union — same-named branches across accounts pool into one tally. No step scopes or warns on this; it is a live, undocumented gap this plan does not close. Flagging rather than silently deferring: `review-trace --branches`/`fail-seq --branches`/etc. pool cross-account branch data with no signal, and this plan's Step 11 caveat covers project/path exposure, not this. | `[verified: _branch_filter:71-73, :466, :2164 — consequence unaddressed, flagged to engineer]` |
| 10 | `audit-routing-samples` stdout is a JSON stream and `judgment-pair` writes `--out`; both are format contracts Step 5/6 must not corrupt — both stay single-line/stderr-routed | `[verified: :2089-2096, :1773]` |
| 11 | Measured scan cost is ~9s/root, ~16s/root with subagents | `[verified: staff-platform-engineer measurement on this workstation]` |
| 12 | `_build_redact_map` assigns ordinals by position in its `roots` argument; two other sites (`_root_index_for_path:3760`, `account_col:3886`) derive the same ordinal independently from their own caller-side order — sorting only inside `_build_redact_map` would desync from those two and fail closed on every multi-root redacted `cost` run. Fixed via one shared `_redaction_ordinals` helper (Step 3), not a local sort. | `[verified: :3053, :3760, :3886 — corrects an insufficient framing from the prior round]` |
| 13 | `_corpus_fingerprint` hashes raw labels only, stripping root index — a root-count change alone does not change it; only a change to the label *set* does | `[verified: :3073; test_corpus_fingerprint_deterministic_for_same_label_set:4386 pins flat and namespaced label sets hashing equal]` |

**Row 9 is an open flag, not a resolved item** — surfacing it rather than
silently deferring it, per the ledger's own rule that an unaddressed
consequence must be visible, not just verified as fact.

## Critical files

**Reuse, do not reimplement:** `read_configured_roots()`
(`cleanup-merged-branches.sh:138`) is the parsing contract to mirror — bash
source, Python consumer, so conventions transfer and code does not.
`_dedup_new_project_dirs`, `_iter_scoped_sessions`, `_iter_glob_scoped_sessions`,
`_scan_root_transcripts` and `_build_redact_map` are already multi-root; wire
them. `check_private_projects_file` (`install.sh:228`) is the advisory shape.
`_write_cost_root` (`test_transcript_analysis.py:4365`) is the fixture base for
new tests — it seeds sessions, unlike `fake_config_dir_factory:169`.

| Path | Steps |
|---|---|
| `claude/.claude/scripts/_config_dir.py` | 1 |
| `.gitignore` | 2 |
| `claude/.claude/scripts/transcript-analysis.py` | 3–9, 20, 21 |
| `claude/.claude/skills/transcript-narrative/SKILL.md` | 10, 12 |
| `claude/.claude/skills/transcript-analysis/SKILL.md` | 8, 11, 12, 13 |
| `claude/.claude/skills/error-mode-analysis/SKILL.md` | 12 |
| `evals/run_skill_evals.py` | 14 |
| `claude/.claude/scripts/analyze-context.py` | 15, 17 |
| `claude/.claude/scripts/post-crash-sessions.py` | 16 |
| `claude/.claude/scripts/token-analyzer.py` | 17 |
| `install.sh` | 18 |
| `docs/transcript-analysis.md`, `docs/scripts.md`, `README.md` | 8, 19 |

## Verification

1. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/`
   from the main worktree (`../../../.venv/bin/...` from a linked worktree);
   `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`.

2. **Suite isolation — do this first, and pin both env vars.** Add an autouse
   fixture in `claude/.claude/scripts/tests/conftest.py` pinning
   `TRANSCRIPT_CONFIG_DIRS_FILE` to a nonexistent tmp path **and**
   `CLAUDE_CONFIG_DIR` to a tmp directory. Pinning only the roots-file seam is
   insufficient: `_resolve_scan_roots`'s base is `PROJECTS_DIR`
   (`config_dir()/"projects"` at import), and `config_dir()` reads `$HOME` when
   `CLAUDE_CONFIG_DIR` is unset — on a real workstation with a populated
   `~/.claude`, an unpinned test would scan the real corpus; in CI, where
   `$HOME/.claude` is simply absent, the same test passes for an unrelated
   reason. Both must be pinned for the isolation to be real rather than
   CI-only. Because Step 3 retains `PROJECTS_DIR` as the resolver's base (not
   retired), the existing seam keeps working unmodified — the `fake_projects`
   fixture (756 references across the suite) plus 21 sites that call
   `monkeypatch.setattr(_mod, "PROJECTS_DIR", ...)` directly — this was the
   deciding factor against retiring the global.

3. **Single-account non-regression:**
   - `declared_transcript_roots()` returns `[]` when the file is absent, so
     `_resolve_scan_roots` returns exactly `[PROJECTS_DIR]`.
   - A parametrized test across all 19 funnel subcommands plus
     `skill-invocation` asserting the header reads `"1 root (no
     ~/.claude/transcript-config-dirs declared)"` and no per-root stderr lines
     print.
   - The existing ~7000-line suite is the regression baseline for everything
     *except* the new header line, once fixture isolation lands and each
     test's header assertion is updated for the new unconditional text.
   - A merge-base worktree stdout diff for the *data* fields (excluding the
     header line) is e2e-weight — run once at implementation time, record in
     the PR, not added to the suite.

4. **Multi-root coverage — three tests, not one, since the 19+1 sites have
   incompatible output contracts** (`judgment-pair` writes `--out`,
   `audit-routing-samples` is a JSON stream with the header on stderr, `cost`/
   `context-distribution` redact by default):
   - **(a)** One parametrize over all 19 `_resolve_project_scope` call sites
     plus `skill-invocation`, spying on the actual call each site makes and
     asserting per its own signature: a `roots=` kwarg for the 19
     `_resolve_project_scope` sites; for `cmd_skill_invocation`'s two calls,
     a *list* matching `_resolve_scan_roots`'s output when `len(roots) > 1`
     (the `_iter_glob_scoped_sessions` branch) and a single `Path` equal to
     `roots[0]` at one root (the `iter_sessions` branch) — the three call
     shapes are not uniform, so the assertion must branch per shape, not
     assume one. Also assert the header call at each site received the same
     `roots` argument (Step 5's required parameter), so a site that threads
     roots to the iterator but not to the header fails here too. Format-
     independent; catches "missing from Step 4's threading."
   - **(b)** A poisoned-global test: set `_mod.PROJECTS_DIR` to a nonexistent
     path, provide a roots file with one real root, assert sessions are still
     found — proves no site still reads `PROJECTS_DIR` as an unthreaded
     default.
   - **(c)** End-to-end multi-root output tests for the three format-outlier
     subcommands only: `cost` (redacted totals), `audit-routing-samples`
     (stdout still parses as JSON), `judgment-pair` (`--out` file still one
     header line).

5. **`--this-repo` union**, replacing the two lifted pins:
   - `:4099` (`test_this_repo_and_config_dir_mutually_exclusive`) becomes an
     allow-case on `_resolve_cost_roots` asserting two roots returned.
   - `:4485` (`test_this_repo_refused_at_cmd_cost_when_config_dir_given`)
     becomes an end-to-end `cmd_cost` allow-case, not a duplicate of `:4099` —
     its distinct value is proving no path reaches the report bypassing the
     (now-removed) guard. Needs the `_repo_scoped_project_slugs` monkeypatch
     pattern already used at `:4161` (shells out to `git worktree list`), the
     `monkeypatch.setattr(_mod, "config_dir", ...)` root-1 stub used by
     neighboring tests, and an assertion on `account-1`/`account-2` totals
     (not raw slugs, since cost redacts by default).
   - `test_this_repo_unions_same_slug_across_roots`.
   - `test_this_repo_excludes_foreign_project_dirs_under_extra_root` — **the
     minimization guard that replaces the refusal; must not be skipped.**
   - `test_multi_root_slug_match_is_name_only_and_dedupes_across_roots` —
     direct coverage of `_iter_scoped_sessions(roots=[a, b])`.
   - `test_this_repo_fail_closed_across_all_roots` — the `:2061` deny case:
     zero slug matches under **every** resolved root still exits 1. The
     existing `test_this_repo_loud_error_on_zero_matches:243` covers only
     single-root; without a multi-root deny pair, an over-broad rewrite of
     `:2061`'s condition passes every allow-case while silently disabling the
     check.

6. **Redaction — `cost` and `audit-routing` only.** `context-distribution`
   prints no project labels and calls `_build_redact_map` nowhere (Step 7); it
   needs no redaction test beyond its existing banner/`--no-redact`-refusal
   coverage. For `cost`: follow `TestCostMultiRootRedaction:4364`'s four-part
   shape (raw label absent, `str(root)` absent, account-dir basename absent,
   expected `account-N/private-project-N` token present), plus a fail-closed
   test mirroring
   `test_redact_map_miss_raises_instead_of_printing_unmapped_row:4413`. For
   `audit_routing`: a test proving its per-row label lookup now goes through
   the shared `_redaction_ordinals` mapping (Step 7) rather than a flat-string
   key — seed two roots with the same raw label and assert both rows resolve
   to distinct `account-N/label` tokens rather than one colliding or missing;
   `audit_routing`'s `--redact` stays its own opt-in, miss-token semantics, not
   `cost`'s fail-closed one.

7. **Roots-file parsing:** comments (leading `#` only, plus a path *containing*
   `#`), blank lines, CRLF, whitespace padding, bare `~`, `~/`, non-directory
   path, path lacking `projects/`, unreadable file (`OSError` during
   validation, not just `UnicodeDecodeError`), invalid UTF-8, and absent file
   (silent single-root no-op). Assert the skip warning names an index, never a
   path, in every one of these cases including the `OSError` path.

8. **Precedence:** an explicit top-level `--config-dir` overrides a populated
   roots file. Requires `_resolve_scan_roots` to exist as a directly callable,
   unit-testable function (Step 3) reading `parsed.config_dir` via
   `getattr(parsed, "config_dir", None)`, matching `_resolve_project_scope`'s
   existing rationale (`:2044-2052`) for why this file's many hand-built test
   `args` fixtures predate that attribute — a literal `parsed.config_dir`
   access raises `AttributeError` across them. Include a case where
   `config_dir` is absent from `parsed` entirely.

9. **Ordinal stability and its limits:**
   - Same declared-roots file, two different active profiles → identical
     `account-N` assignment for the same physical root (the Step 3 fix).
   - Removing a root that contributes labels **not** present in any surviving
     root changes the corpus fingerprint (`:3065`), detectable via
     `test_corpus_fingerprint_differs_for_different_label_set:4394`'s pattern.
   - Removing a root whose labels are a strict subset of a surviving root's is
     **not** detectable by fingerprint alone (`_corpus_fingerprint` hashes the
     label set, not root count — ledger row 13) — assert this as a documented
     limitation, not as a bug the test should catch.

10. **`analyze-context`:** same id prefix seeded under two roots — active
    profile wins **and** the ambiguity is warned; `latest_session_jsonl`
    cross-root recency comparison; metadata pairs with the transcript's own
    root, not the active root.

11. **`install.sh` Step 18** via the `INSTALL_TEST_FIXTURE` extraction pattern
    (`test_install_sh_project_scope_plugins.py:18-22`): `CLAUDE_CONFIG_DIR` set
    and resolving to a different path than `$HOME/.claude` → TIP;
    `CLAUDE_CONFIG_DIR` set but resolving to the same path → silent; unset →
    silent; roots file present but comments-only → exists-but-empty warning;
    `~/.claude` a symlink → resolved target named via `readlink`. Plus a deny
    assertion that the advisory never prints the roots file's contents.

12. **Skill-body contract test**, scoped to the three files this plan touches
    by name (not a repo-wide grep — `ready-for-review/SKILL.md` also contains
    the literal interpreter path and is out of scope here): none of
    `transcript-narrative/SKILL.md`, `transcript-analysis/SKILL.md`,
    `error-mode-analysis/SKILL.md` contains `~/.claude/scripts/`. A second
    assertion that `transcript-analysis/SKILL.md` contains Step 13's pinned
    required sentence verbatim.

13. **`evals` cleanup** removes only session dirs the run created, under a
    `tmp_path` config dir.

14. Per `.claude/rules/`: `/skill-review` on the three SKILL.md files (needs
    drafted text, so it runs at implementation time) and `/code-review` before
    commit.

## Out of scope

- **Auto-discovering sibling config dirs.** Rejected twice before
  (`.claude/plans/post-crash-session-recovery.md:252`,
  `transcript-cost-multi-profile.md:21`); the file is operator-declared input.
- **Redaction for `buckets`, `review-trace`, `fail-seq`, `struggle`,
  `duration`, `subagents`, `pr-link`.** Deferred deliberately (Step 7); Step 11
  makes the gap explicit where it governs quoting output publicly, and ledger
  row 7 records the engineer's choice not to add a matching code-level gate.
- **A code-level scoping fix for `--branches` pooling cross-account branch
  data under a union.** Ledger row 9 records this as verified and
  unaddressed, not silently dropped — a real gap for a follow-up decision.
- **Population of the roots file by any installer.** `install.sh` runs once and
  sees one config dir; whether `register-marketplace` runs per profile on a
  given workstation is unknown. The external account-provisioning setup is a
  peer repository this plan could change and deliberately does not.
- **Reading the account-provisioning repo's account registry.** Carried forward
  from `.claude/plans/fix-stow-adoption-and-config-dir-gaps.md:153`.
- **Generalizing the two roots files into one mechanism.** Different axes —
  cleanup's lists directories containing git repos, this one lists config dirs;
  merging breaks `cleanup-merged-branches --all-projects`.
- **A `permissions.ask` entry.** No `permissions.allow` entry exists for these
  scripts, so there is nothing to narrow.

## Suggested PR split

**PR 1 — Phase A + Phase B + the skill fixes that change agent behavior**
(Steps 1–13, plus Step 20 moved forward into Phase A as noted). Splitting the
skill fixes (9–13) into a later PR would ship a state where the tool discloses
scope but `transcript-narrative` still hand-rolls a single-account glob — an
agent could still reproduce a version of the original miss on top of PR 1
alone. Bundling them means PR 1 is the one that actually changes behavior, not
just plumbing.

**PR 2 — Phase D + Phase E** (Steps 14–19, 21): siblings, `install.sh`,
remaining docs, and the `--this-repo` guard lift. Step 20's diagnostic ships in
PR 1, not here, since `context-distribution` already goes multi-root by
default as soon as Phase A lands.
