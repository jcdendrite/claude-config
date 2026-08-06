# Multi-profile cost attribution for `transcript-analysis.py cost`

> **Public-repo disclosure rules for this file.** This plan ships in the same PR
> as its implementation, so it is subject to the repo's redaction rules. It
> carries **shares and ratios only — no absolute dollar figures, no dated spend
> windows, and no second person's usage data.** The absolute figures behind
> every share below were delivered to the engineer directly and deliberately
> kept out of this file: per-seat spend with a date attached is commercially
> confidential, the redaction hook cannot detect it, and a public commit
> timestamp plus the repo owner's identity makes the engagement inferable.
> `<client>` denotes one consulting client's account profile; `PROJ-<digits>`
> denotes a tracker ID.

## Context

A client seat's Claude Code spend was far higher than expected, and the working
hypothesis was that this repo's configuration (skills, hook pipeline, reviewer
fan-out) was making sessions inefficient. Investigating that hypothesis surfaced
a defect in the analysis toolkit itself: **`transcript-analysis.py` reads only
`$CLAUDE_CONFIG_DIR` (default `~/.claude`), so work performed under a separate
account profile is reported as `$0.00` rather than as an error.** This machine
runs additional profiles under `~/.config/claude-accounts/<account>/`, and the
client's entire recent work lives in one of them.

That silent zero produced a confidently wrong answer — "this client spent
nothing" — one step from being carried into a client billing conversation.
Closing the blind spot is the priority; the spend diagnosis is the secondary
deliverable.

Intended outcome: (1) `cost` cannot silently omit a profile; (2) it answers
"which client cost what" in one invocation without a caller-side loop; (3) a
written diagnosis whose every claim names the command or source that produced it.

## Findings (deliverable — no code changes proposed for these)

Shares below are from one client profile over a 6-day window, via
`CLAUDE_CONFIG_DIR=<profile> transcript-analysis.py cost`. Rates are the repo's
own table (`transcript-analysis.py:2688`), which this review re-verified against
the vendor pricing page.

### 1. ~91% of the bill is context re-transmission, not generated work

| Token class | Share of spend |
|---|---|
| `cache_read` | 48.4% |
| `cache_write_5m` | 42.9% |
| `output` | 8.6% |
| `cache_write_1h` | 0.0% |
| `input` | 0.1% |

Output — the tokens constituting the actual work product — is 8.6%. The other
~91% is the cost of re-sending conversation context. **The client is not paying
for Claude to think; they are paying to re-upload the conversation.**

### 2. Mechanism: large contexts crossed with long idle gaps

- `duration` reports ~79% of session wall-clock as idle gaps.
- 66.4% of dollars land on turns whose context already exceeds 200k tokens.
- `cache_write_1h` is **0.0%** on this seat — every cache write is 5-minute TTL,
  so each resumption after a >5-minute gap re-writes the whole context at 1.25x
  base input rate, 12.5x the 0.1x cost of a cache read.

**The 200k figure is a diagnostic split, not a pricing tier.** The vendor
pricing page states: "Claude 4.6 and later models … include the full 1M token
context window at standard pricing. (A 900k-token request is billed at the same
per-token rate as a 9k-token request.)" There is no long-context premium — so
the lever is sending fewer tokens, not staying under a threshold.

### 3. The reviewer fan-out is not the driver

Main-thread vs subagent spend, measured by walking transcript files directly and
pricing each with the toolkit's `_price_turn`: **main ~78%, subagents ~22%**,
across 29 main sessions and 185 subagent transcripts — roughly 212 spawns in the
window at a low per-spawn cost. The staff-reviewer panel is real spend but a
fifth of the bill. (This split totalled within 0.13% of `cost`'s own figure for
the same window, which corroborates that subagent records are counted once, not
double-counted.)

### 4. One genuine repo-rule compliance gap

Subagent Opus spend **exceeds** main-thread Opus spend on this seat. The
reviewer and `code-writer` agents all carry `model: sonnet` frontmatter, so they
cannot account for it. `general-purpose` is the one routinely-dispatched agent
with no model of its own — it inherits the parent, which is Opus under
`"model": "opusplan"`. `subagent-mix` shows ~18 `general-purpose` spawns in the
window. This is the exact failure the "Always dispatch `general-purpose` with an
explicit `model`" rule in `claude/.claude/CLAUDE.md` exists to prevent.

### 5. Resident instruction footprint is a modest multiplier, not the cause

~56,600 chars (~14,000 tokens) load every turn: `claude/.claude/CLAUDE.md`
(23,381 B, machine-wide), the repo `CLAUDE.md` (12,062 B), ~13,150 chars of
skill descriptions (doubled in this repo because `~/.claude/skills` symlinks to
the tracked copy, so each skill lists once unscoped and once `claude:`-scoped),
and 7,060 chars of agent descriptions with no `skillOverrides`-equivalent
suppression. Against a 200k+ context that is ~7% — it inflates every cache
write, but it is not what makes a session expensive.

### 6. Post-2026-08-31 price exposure is published, not speculative

The vendor pricing page states: "Introductory pricing of $2/$10 per million
input/output tokens is in effect through August 31, 2026, after which the
standard pricing of $3/$15 per million input/output tokens will take effect."
All Sonnet 5 rates scale by **1.5x** (base, cache write, cache read, output).
Sonnet 5 is 84.1% of this seat's spend, so the same workload costs **~+42%
overall** from 2026-09-01. `_SONNET_5_PROMO_EXPIRES` already encodes the date;
the successor rate should be recorded alongside it.

### 7. The 5m-vs-1h TTL gap is a vendor escalation, not a repo fix

The prompt-caching docs specify the caller selects TTL per request via
`cache_control: {"type": "ephemeral", "ttl": "1h"}`, and list **no plan-tier,
beta-header, or special-access requirement**. So the TTL is chosen by Claude
Code per request, not by anything this repo can set — and plan tier is ruled out
as the explanation for why one seat gets 1h and another 0%. That makes the gap a
support question worth raising with the vendor, since it is the single largest
line item on this seat.

## Approach

One code change, to the `cost` subcommand in
`claude/.claude/scripts/transcript-analysis.py`. Everything in Findings ships as
findings only.

**The mechanism is: thread roots as a parameter, do not widen a global.**
`iter_sessions` (line 409) already accepts `projects_dir` as a parameter, and
`post-crash-sessions.py:1067-1111` already implements this exact feature — a
repeatable `--config-dir` append flag that keeps `config_dir() -> Path`
untouched, assembles the root list at the CLI boundary, dedups by `.resolve()`,
rejects roots lacking a `projects/` subdir, and exits 2. Mirror that contract
rather than inventing one.

Rejected, each heavier or wrong:
- **Extend `config_dir()` to return a collection** — breaks four importers
  (`analyze-context.py:26`, `post-crash-sessions.py:50`, `token-analyzer.py:9`,
  `tests/test__config_dir.py`) and the module-global `PROJECTS_DIR` that **19 of
  20 subcommands** resolve through, for a feature only `cost` needs. Widening
  that global silently changes the corpus for publish-safe paths like
  `skill-invocation` — a redaction regression, not a feature.
- **A shell wrapper looping the existing binary per profile** — each invocation
  re-parses the full corpus; that is what made a 7-profile loop time out.
- **Post-hoc `--redact` scrubbing as the disclosure control** — the file itself
  documents the opposite at lines 1589-1601: safety "rests on WHAT records are
  read, not on scrubbing names after the fact … scoping is" the boundary. Scope
  the read; do not scrub the output.

**Assumption ledger.** Root problem: `cost` silently under-reports by omitting
profiles it does not scan, and cannot attribute spend per client in one pass.

| # | Claim | Tag |
|---|---|---|
| root | Reads only `$CLAUDE_CONFIG_DIR`; other profiles report `$0.00` silently | `[verified: _config_dir.py:6-14; transcript-analysis.py:23,25]` |
| 1 | Sibling profiles exist under `~/.config/claude-accounts/`; **operator input, not a documented Claude Code location — never a default** | `[verified: filesystem listing on one machine]` |
| 2 | ~91% of spend is cache read + cache write; output 8.6% | `[verified: cost]` |
| 3 | ~79% of wall-clock is idle gaps | `[verified: duration]` |
| 4 | Seat receives only 5m cache TTL | `[verified: cost shows cache_write_1h at 0.0%]` |
| 5 | TTL is caller-selected per request; no plan-tier/beta gate | `[verified: prompt-caching docs, quoted in Findings §7]` |
| 6 | Whether 1h TTL would reduce cost **on this workload** | `[unverified]` — depends on the gap-length distribution, which `duration` reports only against a 30-min threshold. Gaps >60 min expire both TTLs, and a 1h write costs 1.6x a 5m write, so 1h TTL is **worse** if most gaps exceed an hour. Not actionable without measuring. |
| 7 | Why the TTL differs between seats | `[unverified]` — plan tier ruled out by row 5; residual cause is Claude Code runtime behavior. Vendor escalation, not a repo change. |
| 8 | Sonnet 5 goes to $3/$15 on 2026-09-01; all rates 1.5x; ~+42% on this mix | `[verified: vendor pricing page, quoted in Findings §6]` |
| 9 | No long-context premium; 1M window at standard rates | `[verified: vendor pricing page, quoted in Findings §2]` |
| 10 | Subagent Opus > main-thread Opus, consistent with `general-purpose` inheriting an Opus parent | `[verified: subagent-mix; agents carry model: sonnet; settings model=opusplan]` |
| 11 | Subagent records are merged into the parent session and counted once | `[verified: _read_session_file:375-405; iter_sessions:427]` |

## Implementation steps

**Two independently shippable phases.** Steps 1–5 and 8 change *what is read*
and touch redaction and per-root keying — this is the phase that can produce a
wrong billing number, and it lands with its own tests first. Steps 6–7 and 9–10
add aggregation columns and docs, deriving from data already iterated. Ship and
verify the first phase before the second, so a reconciliation mismatch localizes
to one of them. Step 7 (the main-vs-subagent column) is independently
verifiable and could ship alone if the first phase stalls.

**Step 1 — Add a multi-root scope seam, `cost`-only.**
`transcript-analysis.py`. Add `--config-dir` (`action="append"`,
`dest="extra_config_dirs"`) to `cost`'s parser only. Assemble
`roots = [config_dir(), *extras]` at the CLI boundary; dedup by `.resolve()`;
reject a root that is not a directory or whose `projects/` subdir is absent,
exit 2. Do **not** touch `_config_dir.py`, and do **not** reassign
module-global `PROJECTS_DIR`. Print a per-root `scanned N transcripts, M
skipped (unreadable)` line so a permission error or empty profile is visible in
the output rather than silently absorbed into the total — `_read_session_file`
already swallows per-file `OSError`, and a `PermissionError` on globbing a root
is currently uncaught.
*Why:* the global is read by 19 subcommands; widening it is a redaction
regression. Scanning outside the default config dir hits the same silent-zero
failure mode this plan exists to fix if a bad root degrades quietly.

**Step 1a — State and enforce the wall-clock budget.**
The corpus at review time was ~1.19 GB / 39 project dirs by default, parsing at
~75 MB/s warm; three account profiles add ~340 MB. A `--redact` run (the
default) re-scans the full matched dir set unconditionally
(`_build_redact_map`), so a 4-root run does two full passes over ~1.5 GB, not
one. State the measured warm-run wall-clock for the widest realistic invocation
(all known profiles, `--redact` on) in the PR description, and if it exceeds
whatever timeout the operator's shell tooling defaults to, document the
explicit override needed — a killed run produces no output and no progress
signal, which is silent-failure-shaped in exactly the way this plan is trying
to eliminate.

**Step 2 — Thread roots through the scope resolver.**
Give `_resolve_project_scope` and `_iter_scoped_sessions` an explicit
`roots: Sequence[Path]` parameter defaulting to `(PROJECTS_DIR,)`, so every
existing caller is unchanged. Extend `_iter_scoped_sessions`' "visit each
directory at most once" guard to span roots, including a root nested inside
another.
*Why:* overlapping roots would otherwise double-count a project — a client
billed twice off a billing report.

**Step 3 — Make `--this-repo` and multi-root mutually exclusive.**
`--this-repo` resolves slugs from `git worktree list` against one root; it
cannot filter foreign roots. Reject the combination at argparse with a message
naming both flags.
*Why:* an operator would otherwise believe output is repo-scoped while it reads
three other clients' profiles.

**Step 4 — Build the redact map over the union of roots, account-namespaced.**
`_build_redact_map` currently calls `iter_sessions(PROJECTS_DIR, "*")` and fails
closed to `private-project-unmapped` on a miss. Build it over all scanned roots,
and namespace labels as `account-<K>/private-project-N`, where `<K>` is an
ordinal assigned by scan order — **not** the config-dir path or its basename,
which would leak the account/client identifier that names the profile
directory. Treat any unmapped row as a **hard error**, not a printed row.
Route the resolved-scope header (currently prints the raw glob/dir count) through
the same map so it never prints a config-dir path under default redaction.
Emit a corpus-fingerprint line (label-set hash) in the redacted header, and
state in the per-project section that labels are not comparable across reports.
*Why:* otherwise `private-project-3` denotes a different client in each block of
one table — the same silent-wrong-number class this plan exists to fix — and the
scope header would otherwise leak exactly the identifier the rest of the map is
protecting.

**Step 5 — Refuse `--no-redact` when more than one root is in scope.**
Argparse-level refusal; keep `--no-redact` for the single-profile case. Stamp
every `--no-redact` run with a `DO NOT PUBLISH` banner on stdout and stderr.
*Why:* `--no-redact` plus multi-root is the one command that puts client B's real
project names into a document produced for client A; docs prose is not a control.

**Step 6 — Add the per-project section, keyed on `(root, project-dir)`.**
`_derive_proj_label` derives from `jsonl.parent.name` alone, so the same repo
under two profiles collides. Key aggregation on the pair and print the account
root as its own column. `--by-project` is an output-shape flag: it **composes**
with `--projects` and `--this-repo` rather than joining their mutually exclusive
group. Group by worktree family (`iter_sessions:417-419` documents that one repo
yields many sibling `--claude-worktrees-*` dirs); define the family key in the
docstring.
*Why:* unstated composition invites an implementation that breaks
`--this-repo --by-project`, the most useful pairing.

**Step 7 — Add the main-vs-subagent dollar split using `isSidechain`.**
Discriminate on the per-record `isSidechain` flag (the existing convention —
`transcript-analysis.py:1618`, `cmd_subagents:709`, documented at 375-377). A
path-parts check on `"subagents"` would attribute **100%** of spend to the main
thread, because `_read_session_file` appends subagent records into the parent's
list and `iter_sessions` yields only the main `.jsonl` path. If any path check
remains, use `SUBAGENT_SUBDIR` (line 757), not a literal.

**Step 8 — Distinguish the empty states, and emit the warning per root.**
Today `_cost_report` prints `(no priced turns in range)` (line 2911) for every
empty case, and `test_empty_corpus_renders_clean_zero_state` (tests line 2742)
already asserts it — so "must not print `total 0.00`" would pass against
unchanged code and guard nothing. Four states are currently collapsed into one:

| State | Meaning | Emit |
|---|---|---|
| (a) no dir matched the requested scope | scanned nothing | warning |
| (b) dirs matched but contain no `*.jsonl` | scanned nothing | warning |
| (c) transcripts exist, no priced turn in `--since` window | a real zero | existing zero-state line |
| (d) turns exist, model has no rate entry | a real zero | existing `unpriced_tokens` line |

Define the warning predicate as **zero transcripts opened for a requested
scope**, covering (a) and (b) only, and emit it **per root** — otherwise one
empty profile in a four-profile run is masked by the others' non-zero total,
which is the original bug wearing a different hat. Keep it distinct from the
existing unpriced-tokens line.
*Why:* (a)/(b) are the bug; (c)/(d) are legitimate zeros. Conflating them is the
defect.

**Step 9 — Record the successor rate beside `_SONNET_5_PROMO_EXPIRES`.**
Add the published post-2026-09-01 Sonnet 5 base rate with the pricing-page URL
already in `_PRICING_SOURCE_URL`, so the table does not silently under-price
after the promo ends.

**Step 10 — Document and update the skill.**
`docs/transcript-analysis.md`, beside "Scoping to this repo": document
`--config-dir`, the `--this-repo` and `--no-redact` refusals, and label
non-comparability. `claude/.claude/skills/transcript-analysis/SKILL.md` — add
one table row, drafted here so `/skill-review` has text to review:

> `| Which client or profile does spend belong to? | `cost --by-project --config-dir <dir>` |`

and one caveat bullet:

> - `cost --config-dir` unions extra account profiles into one report; `--this-repo` and `--no-redact` are refused in that mode, and redacted labels are not comparable between reports (each run prints a corpus fingerprint).

## Critical files

- `claude/.claude/scripts/transcript-analysis.py` — `cmd_cost`, `_cost_report`,
  `_resolve_project_scope`, `_iter_scoped_sessions`, `_build_redact_map`,
  `_derive_proj_label`, `_add_project_scope_args`, and `cost`'s argparse block.
  Reuse, do not reimplement: `_price_turn`, `_model_rates`, `_cache_write_split`,
  `_TOKEN_CLASSES`, `SUBAGENT_SUBDIR`, and the shared `--since Nd` parser.
- `claude/.claude/scripts/post-crash-sessions.py:1067-1111` — the flag contract
  to mirror. Read before writing Step 1.
- `claude/.claude/scripts/_config_dir.py` — **unchanged.** Named here so the
  implementer knows it is deliberately out of scope.
- `claude/.claude/scripts/tests/test_transcript_analysis.py` — `TestCost`
  (:2602-2996) and the `fake_projects` fixture (:152), which monkeypatches the
  single `PROJECTS_DIR` scalar and cannot express two roots as-is.
- `docs/transcript-analysis.md`, `claude/.claude/skills/transcript-analysis/SKILL.md`
  — Step 10. The SKILL.md edit makes `/skill-review` hook-enforced on commit in
  addition to `/code-review`.

## Verification

Unit-first: the two headline features reduce to aggregation identities testable
over synthetic fixtures in milliseconds. Live-corpus figures are a one-time
sanity check for the PR body, not acceptance criteria — they drift within hours,
exist on one machine, and embed names the repo forbids publishing.

1. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/`
   from the main worktree root, or `../../../.venv/bin/...` from a linked
   worktree (the `.venv` exists only at the main root, exactly three levels up).
2. `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck` — unaffected by
   this change; run to confirm no regression. Same worktree caveat as item 1:
   from a linked worktree this is `../../../.venv/bin/shellcheck`, since `.venv`
   exists only at the main worktree root.
3. **Extend `fake_projects` to build two roots**, then assert as unit tests:
   - `sum(per_project rows) == grand_total`, hand-computed.
   - `main + subagent == total`, hand-computed, over a fixture with both a main
     record and an `isSidechain` record.
   - Root list honours order, dedupes by `.resolve()`, and skips a nonexistent
     root — the plural analogue of `tests/test_analyze_context.py:13`'s
     fresh-reimport test.
4. **Three empty-state tests**, one per Step 8 message: root resolved to nothing;
   transcripts present but zero priced turns; priced spend. Assert the three
   messages are distinct.
5. **Overlapping-root double-count test**: same project slug in two roots, and
   one root nested in another — grand total must not double.
6. **Redaction deny-case tests** (not a manual "confirm"): no raw project label
   in any new section under default redaction; no config-dir or account
   substring anywhere in redacted stdout; `--no-redact` refused under
   multi-root; `--this-repo` refused under multi-root; per-project rows
   reconcile to the grand total with **no unmapped row present**.
7. **Rollback pin**: every existing `fake_projects`-based `cost` test must pass
   **unmodified**. If any needs editing, the single-root default path moved and
   the change is no longer additive.
8. Assert rate-invariant relationships (sums, shares) in new tests rather than
   new absolute-dollar literals; monkeypatch `_model_rates` where rate
   independence is wanted. The table carries a 2026-08-31 expiry.

## Out of scope

- Cache-TTL changes — caller-selected per request, not repo-settable (ledger rows
  5, 7). The vendor escalation in Findings §7 is the action, and it is the
  engineer's to make.
- Trimming resident instruction footprint (`CLAUDE.md`, agent descriptions) —
  ~7% multiplier, and `ai-instruction-and-memory-files` should govern that edit.
- Changing reviewer fan-out or `/handoff` cadence — ~22% of spend, not the driver.
- Enforcing `model: sonnet` on `general-purpose` dispatches — a real finding
  (Findings §4) but a session-behaviour change, not a tooling one. Own pass.
- Multi-root support for `analyze-context.py` and `token-analyzer.py`, which
  carry the identical single-root import binding and keep silently
  under-reporting. Deliberately deferred; noted so the omission is visible.
