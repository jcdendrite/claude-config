# Post-crash-sessions: widen crash-evidence window, close two actionability gaps

## Context

Fix three confirmed defects in `claude/.claude/scripts/post-crash-sessions.py`
(installed to every stow user as `post-crash-sessions`) surfaced during a
root-cause-analysis session, so the report stops silently dropping real crash
evidence and stops making the user reconstruct actions the script already has
enough information to hand them directly.

Empirical finding that drove this: 11 real, multi-MB, well-formed transcripts
in this repo (1 main session + 10 worktree sessions) with last activity
19:33–21:49 on a day this machine crashed were completely absent from the
report — not under Resumable, not under Crashed-no-transcript, not even under
Unknown. Root cause: `_near_boot_transcript_only_ids()`'s fallback window
(`_NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS = 600.0`, 10 minutes) is sized for
write-latency ("the write lands on disk shortly before a crash") but is used
as a proxy for "was this session still open when the crash happened," which
needs a much longer horizon — the 11 missing sessions' actual gaps ranged
29 minutes to 2h45m before boot, 3–16× past the window. Separately, two report
sections already compute enough to hand the user a direct next step but don't:
the legacy dead-pid list only ever lists ps-confirmed-dead paths, and the
Resumable list has no way to tell a session from an hour ago apart from one
from 12 days ago at a glance.

## Approach

**Root problem:** the near-boot fallback conflates two different quantities
(write latency vs. plausible-still-open duration), causing real crash
evidence to be dropped outright rather than surfaced with appropriate
hedging; two report sections withhold actionable next steps despite already
having computed the underlying facts.

**Givens:**
- G1: multi-account auto-discovery (scanning every
  `~/.config/claude-accounts/<account>/` by default) stays out of scope for
  this repo. Already decided in the merged plan behind PR #582
  (`fix-stow-adoption-and-config-dir-gaps.md`), which explicitly assigned
  account-name-to-config-dir resolution to `workstation-setup` (private,
  owns `accounts.tsv`) rather than this public repo — reach genuinely stops
  here, since this plan cannot unilaterally move that ownership boundary
  from inside `claude-config`. `[verified: claude-config git history, PR #582]`

**Per-mechanism:**

- **Widen `_NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS` from 600 to 14400 (4h)** —
  `anchors: root`. Matches the measured gap range from the 11 missed
  sessions (29min–2h45m) with margin, without adopting an uncapped design
  that would need reconstructing the *previous* boot's timestamp — not
  reliably available on this OS without new plumbing this task doesn't
  need. `[engineer-verified: chose 4h over a 24h or uncapped alternative]`
- **New `CLASS_POSSIBLE_CRASH` classification + its own top-level report
  section** ("Possible crash — transcript only") — `anchors: root`. Two
  lighter alternatives considered: (a) fold into the existing
  `CLASS_UNKNOWN` bucket with clearer per-row wording — rejected, since
  `CLASS_UNKNOWN` already covers ~7 unrelated "can't tell" reasons (ps
  unusable, boot time undeterminable, procStart unparseable, registry entry
  written after boot, …); adding an 8th, more-informative-but-still-lumped-in
  reason keeps the bucket exactly as illegible as today. (b) classify these
  as full `CLASS_RESUMABLE` — rejected, since transcript-only evidence can't
  distinguish "crashed mid-work" from "exited cleanly shortly before an
  unrelated later crash," and folding it into Resumable overclaims
  confidence the evidence doesn't support — the entire point of the
  root-cause finding. `[engineer-verified: chose a new top-level section
  over folding into Unknown]`
- **Extract a shared row-rendering helper for the Resumable and Possible-crash
  sections** — `anchors: row983` (`render_report`'s existing Resumable loop,
  `post-crash-sessions.py:983-1003`). Both sections need an identical per-row
  layout: resume command, cwd-missing warning, meta line (last activity, age,
  branch, entry count), detail line. Extracting one helper instead of
  duplicating it keeps a single source of truth instead of the two sections
  silently drifting apart later (this repo's own DRY rule).
  `[verified: post-crash-sessions.py:983-1003]`
- **Age annotation via an injected `now` parameter on `render_report`, not a
  bare `time.time()` call** — `anchors: root`. Matches this file's existing
  test-seam convention: `build_report` already injects `ps_lstart` and
  `boot_time_fn` specifically so tests can freeze time/liveness
  deterministically (`post-crash-sessions.py:827-833`). A hardcoded
  `time.time()` read would be the file's first non-injectable wall-clock
  dependency and existing tests have no seam to freeze it.
  `[verified: post-crash-sessions.py:827-833]`
- **Legacy dead-pid cleanup command via `shlex.quote` + a leading `--`** —
  `anchors: row986` (the existing Resumable resume-command construction,
  `post-crash-sessions.py:986-990`, already quoted for the same reason: cwd
  and session_id both trace back to not-fully-trusted local strings). Reuses
  the exact justification already established in the file rather than
  inventing a new one. Emitted only when `not redact` — a working `rm`
  command needs real paths, and `--redact` already withholds those (see
  Out of scope: the `--redact` contract stays unchanged).
  `[verified: post-crash-sessions.py:986-990]`

## Critical files

- **`claude/.claude/scripts/post-crash-sessions.py`**
  - `_NEAR_BOOT_TRANSCRIPT_WINDOW_SECONDS`: `600.0` → `14400.0`; update its
    comment to state the new rationale (measured gap range, not a guessed
    round number).
  - Add `CLASS_POSSIBLE_CRASH = "possible-crash"` alongside the other four
    `CLASS_*` constants.
  - `_classify_session`'s final branch (currently reached only for a
    near-boot transcript-only session with no registry/lock entry at all,
    `:811-820`) returns `CLASS_POSSIBLE_CRASH` instead of `CLASS_UNKNOWN`;
    update its `detail` string to name the widened window and the
    weaker-evidence caveat explicitly (transcript existed, no registry/lock
    corroboration — cannot confirm this was still open at crash time).
  - `render_report`: extract the existing Resumable per-row loop
    (`:983-1003`) into a shared helper parameterized by the row list, section
    title, and classification; call it once for `CLASS_RESUMABLE` (unchanged
    behavior + new age annotation) and once for `CLASS_POSSIBLE_CRASH` (new
    section, placed after Resumable and before the `other_groups` loop).
    Remove `CLASS_POSSIBLE_CRASH` from whatever would otherwise iterate it
    into `other_groups`.
  - Add `_fmt_age(seconds: float) -> str` (e.g. `"3h old"`, `"12d old"`,
    `"45m old"` — single unit, floored, no fractional part) and call it from
    the shared row-rendering helper.
  - `render_report(report, *, redact: bool, now: float | None = None)` —
    default `now` to `time.time()` only at the call site in `main()`, never
    inside `render_report` itself, so every test path stays injectable.
    **`now=None` means "omit the age annotation entirely"** — the shared
    row-rendering helper skips `_fmt_age` and the meta line's age segment
    when `now` is `None`, and skips it too for any row whose own
    `last_activity` is `None` or greater than `now` (clock skew between
    `build_report`'s data collection and `render_report`'s `now` capture —
    never render a negative age). This makes `now` fully optional: the 17
    existing `render_report(report, redact=...)` call sites in the test file
    (no `now` argument — grep for `render_report(report, redact=` in
    `test_post_crash_sessions.py` to enumerate them before starting) need
    zero changes and keep passing unmodified; only the new age-specific
    tests below pass `now=` explicitly.
  - Legacy bare-pid section: when `not redact` and `report.legacy_bare_pid_dead`
    is non-empty, emit one `rm -- <quoted paths...>` line after the existing
    path listing, built with `shlex.quote` (already imported) per path,
    space-joined.
- **`claude/.claude/scripts/tests/test_post_crash_sessions.py`**
  - Update `_near_boot_transcript_only_ids` tests (`:807-823`) — their
    fixture offsets (`boot_time=1000.0`, `last_activity=950.0`/`100.0`) are
    scaled to the old 600s window; rescale to the new 14400s window (e.g. an
    in-window case at `boot_time - 14000`, an out-of-window case comfortably
    beyond `boot_time - 14400`).
  - Update the near-boot-only branch's classification test (the one
    currently asserting `CLASS_UNKNOWN` for a transcript-only, no-registry,
    no-lock session) to assert `CLASS_POSSIBLE_CRASH` and the new detail
    wording.
  - New tests:
    - `render_report` emits a "Possible crash" section header with the right
      count and row content; that section sorts by `last_activity`
      descending like Resumable.
    - A `CLASS_POSSIBLE_CRASH` row renders exactly once and is absent from
      the `## Unknown` section body (catches a missed
      exclude-from-`other_groups` filter silently duplicating the row into
      both sections instead of crashing).
    - One `build_report` + `render_report` integration test reproducing the
      original bug shape directly on disk (a transcript file with no
      registry entry and no lock file, last activity within the new 4h
      window before an injected boot time) and asserting it surfaces under
      "Possible crash" — mirroring this file's existing
      `build_report`-level regression pattern (e.g.
      `test_build_report_union_discovers_lock_missed_by_either_method_alone`
      at `:637`). Unit-level coverage of `_near_boot_transcript_only_ids`
      and `_classify_session` alone doesn't prove the
      `_scan_transcripts`→`known_session_ids`→classification wiring stays
      correct end-to-end.
    - Age-annotation rendering at a fixed injected `now`, covering: a few
      ordinary boundaries (minutes/hours/days), `last_activity=None`
      (age omitted, no crash), and `last_activity > now` (clock-skew case,
      age omitted, no negative duration rendered).
    - The legacy-pid cleanup command: present and correctly `shlex.quote`d
      under default (non-redact) rendering with **two or more** dead-pid
      paths (catches a join/separator bug a single-item fixture can't); one
      path contains a shell metacharacter (mirror the existing
      `test_render_report_resume_command_shell_quotes_a_hostile_cwd`
      pattern at `:252`, which already establishes this file's convention of
      testing `shlex.quote` against an actual hostile value, not just "quoting
      occurred"); absent entirely under `--redact`.

## Verification

1. From this worktree: `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/` both clean.
2. Manual smoke test: run `post-crash-sessions` for real and confirm the
   report no longer silently omits sessions — note that the specific 11
   sessions found missing during the original investigation may have gained
   or lost registry/lock evidence in the time since (evidence decays via pid
   reuse), so treat "some transcript-only session from today's activity shows
   up under Possible crash, or under a stronger bucket if it now has
   registry/lock evidence" as the passing condition, not the exact original
   11.
3. Confirm `--redact` output still contains no real paths, ids, or branch
   names anywhere in the new sections or the new cleanup-command line (it
   should be absent entirely under `--redact`).

## Out of scope

- Multi-account auto-discovery for `post-crash-sessions`/`transcript-analysis`
  (G1) — belongs in `workstation-setup`, a separate, already-identified task.
- Any change to the three-evidence-source architecture or their precedence
  (session registry, scheduled-task lock, transcript corpus, and their trust
  ordering) — this plan touches one fallback threshold and adds one
  render-only classification tier on top of the existing architecture, not
  the architecture itself. In reach of this plan/file, deliberately not
  taken.
- Any change to `render_report`'s existing `--redact` contract
  (ordinal-mapped ids/cwds, no real paths, no git branch names) — new output
  (the age annotation, the new section, the cleanup command) composes with
  the existing contract unchanged rather than revisiting it. In reach of
  this plan/file, deliberately not taken.
- Tracking the user's personal, gitignored home-directory CLAUDE.md variant
  (loaded via Claude Code's directory-walk-up, since `$HOME` is an ancestor
  of every project) in `workstation-setup` via a new stow package —
  separate, already-briefed task
  (`~/.claude/briefs/claude-local-md-stow-package-task.md`), different repo.
