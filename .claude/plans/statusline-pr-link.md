# Statusline: show a reliable PR link

## Context

Goal: make the statusline reliably show a link to the current branch's open
PR, instead of leaving that to Claude Code's built-in footer hint, which
inconsistently shows either a PR link or an unrelated "gh auth login for PR
status" message. The user noticed the inconsistency in a screenshot and asked
for a dependable PR link at the bottom of the terminal. This lives in
`claude-config` (`claude/.claude/statusline-command.sh`), the same script
that recently gained the account (email/plan) segment.

Investigation established that the "auto mode on ... gh auth login for PR
status ... for agents" line the user saw is **not** produced by
`statusline-command.sh` — that script only ever emits two lines (model/usage/
cost/account, then cwd/branch). That third line is a Claude Code **built-in
footer badge row**, confirmed by the primary docs: "The status line renders
in its own row above the built-in footer badges and does not replace them"
[verified: code.claude.com/docs/en/statusline.md]. There is no documented
setting to configure or force that row's content, so it cannot be fixed
directly.

However, the same docs show Claude Code already pipes PR data to our own
script's stdin, unused today: `pr.number`, `pr.url` ("Open pull request for
the current branch. Mirrors the PR badge in the bottom status bar. Absent
until a PR is found, when not in a git repository, or once the PR merges or
closes") and `pr.review_state` (`approved`, `pending`, `changes_requested`,
or `draft`) [verified: code.claude.com/docs/en/statusline.md, "Available
data" table and full JSON schema example]. Since this mirrors the same
underlying detection as the built-in badge, rendering it ourselves gives the
user a reliable link on the row they already control, wherever the built-in
badge would show one too.

## Approach

**Data source and rendering.** Extend `statusline-command.sh`'s existing
`jq`-per-field extraction (used today for `model`, `cost`, `rate_limits`,
etc.) to also read `.pr.number`, `.pr.url`, and `.pr.review_state` with the
same `// empty` null-coalescing idiom already used for `rate_5h`/`rate_7d`.
When `pr.url` is absent (no open PR, not a git repo, or merged/closed —
per the doc quote above), render nothing, matching how `git_branch` is
omitted entirely rather than shown as a placeholder when there's no git repo.

**Placement.** Append the PR segment to line 2, after the git branch segment
(`${short_cwd}${git_branch}${pr_display}`), rather than adding a third line.
The PR text is short and bounded (see width note below), so it fits
comfortably alongside cwd/branch without needing the wider layout a new line
would imply.

**Clickable link.** Wrap the PR number/label in an OSC 8 hyperlink escape
sequence pointing at `pr.url`, per the documented mechanism: "Links: use OSC
8 escape sequences ... Requires a terminal that supports hyperlinks like
iTerm2, Kitty, or WezTerm" [verified: code.claude.com/docs/en/statusline.md,
"What your script can output"]. This makes the badge itself Cmd/Ctrl-clickable,
not just a visible URL.

**Color by review state.** `pr.review_state` is a closed set of four
documented values, so map it the same way the script already color-codes
context/rate bars: `approved` → `GREEN`, `changes_requested` → `RED`,
`draft` → `GRAY`, `pending` (or absent, since the docs note it "may be
independently absent even when `pr` is present") → `YELLOW`. Reuses the
existing color constants; no new palette.

**Sanitization.** `pr.url` gets embedded inside a raw OSC 8 escape sequence —
a context where a stray control byte could break out of the escape and
inject further terminal control sequences. Strip control characters before
embedding it (`tr -d '[:cntrl:]'`), mirroring the mitigation already used
for the account-segment fields in this same file.

That byte-level strip is necessary but not sufficient on its own: both
final-assembly lines (162 and 163) print via `echo -e`, which independently
re-interprets literal two-character backslash-escape *text* (e.g. the two
characters `\` and `e`) back into a live ESC byte at print time — a vector
`tr -d '[:cntrl:]'` cannot catch, since neither character is a control byte
on its own. GitHub repo/owner names can't contain a backslash today, so
`pr.url` can't carry that text through the current GitHub URL shape, but
that safety is incidental to GitHub's naming rules, not enforced by the
mitigation — and the identical `echo -e` mechanism already governs the
pre-existing account-segment and git-branch-name fields on these same two
lines, which have the same latent gap independent of this plan.

Since this plan is already touching both assembly lines' shared mechanism
(line 163 directly, to append `pr_display`), close the class at the root
instead of adding a second field-specific patch: switch the color constant
definitions (`RESET`, `CYAN`, `GREEN`, etc., currently single-quoted literal
text like `RESET='\033[0m'`) to ANSI-C-quoted literal bytes
(`RESET=$'\033[0m'`), so escape sequences are resolved once at definition
time. Then change both final-assembly `echo -e` calls (lines 162–163) to
`printf '%s\n'`, which never reinterprets escapes in its arguments. Every
existing intermediate `printf`-built segment (`ctx_display`, `rate_display`,
`git_branch`, the new `pr_display`) already resolves to real bytes before
reaching the final assembly line, so this is a drop-in swap with no
behavior change for the existing colored output — it just removes the
runtime reinterpretation step that made the injection path possible in the
first place, for every field on both lines, not only the new one. This is
the CLAUDE.md "audit structural siblings" case: the fix is identical across
both `echo -e` sites, so it applies to both rather than only the line this
plan otherwise needed to touch.

**Width/truncation.** No ellipsis-truncation logic needed — unlike the
account segment (arbitrary-length email, needed a truncation budget), the
PR segment's worst case is schema-bounded: `PR #<number> <review_state>`
tops out around "PR #123456 changes_requested" (~29 chars) since
`review_state` is one of four known words, not free text. That bound isn't
reserved in the width budget the way the account segment's was, though:
line 2's `_max_branch_name` computation only accounts for `short_cwd` and
`git_branch`, so on a narrow terminal (e.g. 40 columns) a fully-populated
cwd + branch + PR segment can together exceed `_available` and wrap. Since
the PR segment is short and bounded, accept wrapping on narrow terminals
rather than adding a third width reservation for a rare combination
(narrow terminal + long-ish branch name + open PR) — but the Verification
section below explicitly checks this combination so it's a documented,
observed tradeoff rather than an untested gap.

**Alternatives considered:**
- *Fix the built-in footer badge's "gh auth login" hint directly* — rejected:
  confirmed no configuration surface exists for that row; it's an internal,
  unscriptable UI element separate from the statusline command
  [verified: code.claude.com/docs/en/statusline.md].
- *Shell out to `gh pr view --json url,number,reviewDecision` inside the
  script* — rejected (over-powered-primitive check): this would duplicate
  detection Claude Code already performs and hands us for free, add a
  network call on every statusline refresh (debounced at 300ms, and "If a
  new update triggers while your script is still running, Claude Code
  cancels the in-flight script" — so a slow network call risks never
  completing) [verified: code.claude.com/docs/en/statusline.md, "How status
  line updates"], and introduces exactly the kind of auth/detection surface
  where the built-in mechanism is already inconsistent. Reading the
  already-computed `pr.*` field is synchronous, zero-network, and needs no
  new dependency (`jq` is already used throughout the script).
- *Add the PR segment as a third status line instead of appending to line
  2* — rejected: adds a line for what is normally a short, often-empty
  segment; appending to the existing cwd/branch line keeps the layout
  compact and consistent with how the account segment was appended to line 1
  rather than given its own line.

**Assumption ledger**

- Root problem: the PR link the user wants shows unreliably because it's
  driven by Claude Code's internal, unconfigurable footer-badge logic; the
  fix is to render the already-available `pr.*` stdin field on our own
  scriptable row instead of trying to alter that internal row.
  `anchors: root`
- `pr.number`, `pr.url`, `pr.review_state` are already sent to the
  statusline script's stdin and mirror the built-in PR badge.
  `[verified: code.claude.com/docs/en/statusline.md, "Available data" table
  + full JSON schema example]` `anchors: root`
- The built-in footer hint row is a separate, non-scriptable UI element with
  no documented config surface — confirmed by fetching the primary
  statusline doc directly (not inferred from a secondary summary).
  `[verified: code.claude.com/docs/en/statusline.md — "renders in its own
  row above the built-in footer badges and does not replace them"]`
  `anchors: root`
- `pr` is absent from the JSON (so the new segment correctly renders
  nothing) when there's no open PR for the branch, outside a git repo, or
  once the PR merges/closes — not a bug case to special-case further.
  `[verified: code.claude.com/docs/en/statusline.md]` `anchors: row above`
- OSC 8 is the documented mechanism for clickable statusline text, and is
  terminal-support-gated (iTerm2, Kitty, WezTerm named explicitly; others may
  just show plain text with no link).
  `[verified: code.claude.com/docs/en/statusline.md, "What your script can
  output" → Links]` `anchors: root`
- Lighter-primitive check: shelling out to `gh pr view` directly (a heavier,
  network-calling mechanism) was considered and rejected in favor of the
  already-provided `pr.*` field — see Alternatives above for the two
  specific failure modes that ruled it out (duplicated detection + cancelled
  in-flight script under the 300ms debounce). `anchors: root`
- `pr.review_state`'s four values are closed-set per the docs, so the color
  mapping needs no default/unknown-value fallback beyond treating absence
  the same as `pending`. `[verified: code.claude.com/docs/en/statusline.md]`
  `anchors: root`
- Control-character stripping on `pr.url` before OSC 8 embedding reuses the
  same mitigation and rationale already present in this file's account-
  segment code comment (control bytes flowing into the final `echo -e`
  could inject terminal escape sequences). `[verified: existing inline
  comment in claude/.claude/statusline-command.sh]` `anchors: root`
- No project-specific plan-it layer exists for this repo.
  `[verified: glob for .claude/skills/plan-it-*/SKILL.md under
  claude-config found no matches]` `anchors: root`
- `tr -d '[:cntrl:]'` alone does not close the injection class for fields
  embedded in an OSC 8 sequence and printed via `echo -e`: `echo -e` separately
  re-interprets literal two-character backslash-escape text (not just raw
  control bytes) back into live escape bytes at print time. Not exploitable
  today via `pr.url` (GitHub owner/repo names can't contain a backslash),
  but the same `echo -e` mechanism already governs the pre-existing
  account-segment and git-branch fields on the same two lines.
  `[verified: ciso-reviewer spawn during /plan-review, empirical
  reproduction of the `echo -e` re-interpretation in a sandboxed shell]`
  `anchors: root`

## Critical files

- `claude/.claude/statusline-command.sh` —
  1. Add a PR-info extraction block (reads `.pr.number`/`.pr.url`/
     `.pr.review_state` via `jq`, same `// empty` idiom as
     `rate_5h`/`rate_7d`), a review-state-to-color mapping (reuse
     `GREEN`/`YELLOW`/`RED`/`GRAY`), an OSC 8-wrapped `pr_display` builder
     (reuse the printf-into-a-variable pattern already used for
     `git_branch` and `ctx_display`, and the `tr -d '[:cntrl:]'`
     sanitization already used for the account fields).
  2. Change the color constant definitions (`RESET`, `CYAN`, `GREEN`,
     `YELLOW`, `BLUE`, `MAGENTA`, `RED`, `WHITE`, `GRAY`) from single-quoted
     literal text (`RESET='\033[0m'`) to ANSI-C-quoted literal bytes
     (`RESET=$'\033[0m'`).
  3. Change both final-assembly lines (currently `echo -e "..."` at what is
     today lines 162 and 163) to `printf '%s\n' "..."`, and append
     `pr_display` to the line-2 assembly after `git_branch`.
- `README.md` — the "Statusline" section (around line 175) enumerates
  exactly which fields the script renders ("model, context usage
  percentage, ... working directory, and git branch"); update that sentence
  to include the PR segment so the doc stays accurate (single source of
  truth for what the statusline shows).

## Verification

- Run the script by hand with representative stdin payloads covering: `pr`
  absent entirely; `pr` present with each of the four `review_state` values;
  `pr` present with `review_state` absent. Confirm the link renders (or is
  cleanly omitted) with no `jq` stderr noise, e.g.:
  `echo '{"model":{"display_name":"Sonnet"},"cwd":"'"$HOME"'","pr":{"number":1234,"url":"https://github.com/jcdendrite/claude-config/pull/1234","review_state":"pending"}}' | ./claude/.claude/statusline-command.sh`
- Visually confirm in a real terminal that supports OSC 8 (e.g. iTerm2) that
  the PR badge is Cmd-clickable and opens the correct URL.
- Confirm in a terminal without OSC 8 support that the segment still renders
  as plain (non-broken) text.
- `../../../.venv/bin/pytest claude/.claude/` and
  `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`
  (per this repo's contributor commands) to catch any regressions in the
  existing hook/skill test suite and shell lint.
- Confirm the existing cwd/branch truncation behavior on a narrow terminal
  is unaffected when the PR segment is present, and separately confirm
  what happens on a narrow terminal (e.g. `COLUMNS=40`) with a long branch
  name *and* an open PR: the line should wrap, not error or corrupt colors.
- Confirm colored output (line 1 and line 2) renders identically before and
  after switching the color constants to `$'...'` and the final assembly to
  `printf '%s\n'` — this is a mechanism change with no intended visual diff.
- Construct a synthetic `pr.url` containing the literal two-character text
  `\e` (e.g. `.../pull/1\e]8;;http://evil\e\\CLICK\e]8;;\e\\` as the URL
  value) and confirm the rendered output does not produce a second,
  attacker-controlled clickable link — this is the case `tr -d
  '[:cntrl:]'` alone does not catch, closed instead by dropping `echo -e`'s
  reinterpretation step.
- Separately, construct a `pr.number` or `pr.review_state` value containing
  a JSON string escape for the ESC control character (0x1B) — not the
  literal two-character `\e` text above — and confirm it does not
  produce a second link either. This is a distinct vector from the
  literal-`\e` case: `jq -r` decodes that JSON escape into a genuine ESC
  byte at parse time, independent of the `echo -e`/`printf` assembly
  mechanism, so it requires `tr -d '[:cntrl:]'` on `pr_number` and
  `pr_review_state` specifically (not just `pr_url`) to close. Automated
  as `claude/.claude/tests/test_statusline_command.py`
  (`TestPullRequestEscapeInjection`): the JSON-escaped-ESC vector is
  exercised on all three PR fields (`pr.number`, `pr.url`,
  `pr.review_state`), and the literal-`\e`-text vector on `pr.url` (the
  field this session manually tested) — run via
  `../../../.venv/bin/pytest claude/.claude/tests/test_statusline_command.py
  -v` for a re-runnable regression check instead of re-deriving one by hand.
