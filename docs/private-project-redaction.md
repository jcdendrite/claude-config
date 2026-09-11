# Private-project redaction

This repo is public, so any project codename, organization name, or tracker-ID
that lands in a commit or PR description ships to the world. The repo-root
[`CLAUDE.md`](../CLAUDE.md) "Redact private-project-identifying content" rule
defines what to keep out; `deny-private-project-refs.sh` is the mechanical
enforcement. For the high-level three-tier overview, see the
[README](../README.md#private-project-redaction).

## The three scans

Each gated operation's scan target reaches beyond the content a reviewer
would think to check. For `git commit`, the target is the staged diff's
added lines plus the commit message (an inline `-m` value or a `-F`/`--file`
path's contents), plus the invoking Bash command's own text — the last of
these only when the staged diff is non-empty. For `gh pr create`/`gh pr edit`
and a mutating `gh api` call, the target is the command's own text plus any
referenced-file contents: `--body-file`/`--template` (or `-F`/`-T`) for
`gh pr`, `--input` or a `-f`/`-F key=@path` field value for `gh api`. For
`gh issue create`/`gh issue comment`/`gh issue edit`, the target is the
command's own text plus any `--body-file` (or `-F`) referenced-file
contents — the same extractor `gh pr` uses, since `gh issue` shares that
flag shape but has no `--template`/`-T` flag. Because the command's own
text is in scope, a `cd`-into-a-home-rooted-path prefix chained into the same
Bash call as the gated command self-matches the home-rooted-path detector
below even when the diff and message are clean — run the `cd` as its own
earlier call and issue the gated command alone.

`gh pr`/`gh issue` surface detection resolves positional words through gh's
own cobra-based subcommand grammar, so a flag interposed before the
subcommand cannot separate a surface word from it; see
`deny-private-project-refs.sh`'s own `fragment_gh_gated_surface` header
comment for that grammar's one residual rather than restating it here.

`deny-private-project-refs.sh` runs three scans, in order:

1. **Tracker-ID scan (always on, no setup).** Matches `[A-Z]{2,}-\d+` tokens
   not on the OSS allowlist. The allowlist also reserves two placeholder
   prefixes — `PROJ-` and `TICKET-` — so skill examples and commit messages can
   use a realistic-looking tracker shape (`PROJ-<digits>`, `TICKET-<digits>`)
   without obfuscating the digits to defeat the scan.
2. **Structural-shape scan (always on, no setup).** Six independent detectors
   for shapes that can identify a specific machine, person, or private
   project without naming it directly — see "The six structural detectors"
   below.
3. **Private-projects blocklist (opt-in).** Reads `<config-dir>/private-projects.md`
   (`<config-dir>` means `$CLAUDE_CONFIG_DIR` when set, else `~/.claude`)
   at hook runtime and blocks commits/PRs whose content contains any
   non-comment, non-blank line from the file as a case-insensitive whole-word
   match.

## The six structural detectors

Unlike the blocklist, these run unconditionally — no `<config-dir>/private-projects.md`
setup required. Each is checked independently, so the deny message names
which one fired. Regexes live in `_lib.sh` as `_LIB_IPV4_LITERAL_REGEX` and
its five siblings, shared with any future consumer that needs the same
definitions.

| Detector | Catches | Does NOT catch |
|---|---|---|
| IPv4 literal | an RFC 1918 private-range (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) or RFC 1122 §3.2.1.3 loopback (`127.0.0.0/8`) address, zero-padded octets included | a public IPv4 address, or an IPv6 address |
| SSH key path reference | a path segment naming the SSH configuration directory, or a filename following the `id_<algorithm>` convention (rsa/dsa/ecdsa/ed25519) | a custom-named key file with no `id_<algorithm>` shape |
| Home-rooted path | a path rooted at `/Users/<username>/` or `/home/<username>/` | a relative or repo-rooted path |
| Long hex identifier | a 32+ character contiguous hex run, or a UUID-shaped four-hyphen-group hex sequence | a shorter hex run (e.g. a short git SHA) |
| Internal hostname | a hostname ending in `.internal`, `.corp`, `.local`, `.lan`, `.intranet`, or `.private` — for `.internal`/`.corp`/`.lan`/`.intranet`/`.private`, also an FQDN shape like `host[.]corp[.]example[.]com` where the TLD word is a subdomain label, not the string end | a hostname on any other TLD, or a filename convention like `settings.local.json` (only `.local`'s boundary excludes a following dot-segment — `.local` doubles as a common per-machine-override filename convention, e.g. `[.]env[.]local`, that the other five words don't) |
| Slack-channel shape | a `#`-prefixed lowercase-hyphenated word written outside markdown link syntax | a plain GitHub issue reference (`#421`, all-digit); bash parameter-expansion syntax (`${var#…}`, `${var##…}`); a markdown anchor-link fragment (see below) |

Every example above is deliberately non-matching — e.g. the `<username>`
placeholder uses `<`, which falls outside the detector's `[A-Za-z0-9_.-]`
charset — so committing this table doesn't trip its own detectors.

The Slack-channel detector still matches a bare anchor fragment like
`docs/skills.md#<heading-slug>`, since it shares the real-channel-name
shape. Rephrase around that false positive rather than loosening the
charset. An anchor fragment inside a real link's destination —
`[text](other-file.md#<anchor-name>)` — is exempted instead, so a
functional cross-file anchor link doesn't need rewording. That exemption is
purely syntactic: it doesn't check that the destination resolves to a real
file. A `#<slug>` inside a compact JSON object with no space after the
colon (e.g. `{"channel":"#<slug>"}`) is also not caught, because the `{`
sits before the `#` with nothing rescuing it; the spaced form
(`{"channel": "#<slug>"}`) still denies. The same content-blind exemption
covers bash parameter-expansion syntax: a real Slack-channel-shaped slug
placed inside `${var#<pattern>}` or `${var##<pattern>}` is not caught
either, because the exemption covers the whole `${...}`-shaped span
regardless of what occupies the pattern position. See
`_LIB_SLACK_CHANNEL_SHAPE_REGEX`'s comment in `_lib.sh` for the
exemption's matching mechanics.

## Publishing a pooled tooling measurement

This is the tier-3 companion to the two mechanical tiers above. It is
reviewer discipline, not a hook. A pooled figure's safety depends on how
it was computed, not what string it contains, and a hook can't see that.
The repo-root
[`CLAUDE.md`](../CLAUDE.md) "Also redact structural fingerprints and
provenance" rule states the carve-out these conditions gate.

**What it permits.** A figure pooled across a corpus that mixes private
and public sources is publishable when it carries no per-project,
per-account, or per-engagement dimension. Cite the command or script
that produced it. That command or script must itself be an aggregation
boundary. It may read the mixed corpus internally, but its output to
the agent is the rounded pooled figure only — never per-session or
per-project raw content.

**Scope — two closed lists.** Both have to be satisfied, and neither
extends by analogy:

- *What may be counted:* this repo's own tooling in use — Claude Code
  tool calls, sessions, and agent dispatches. Nothing else.
- *How it may be reported:*
  - Counts may be reported as a total, a share, or a median. Report
    each at any granularity — per tool call, per session, per
    dispatch, or pooled. A count carries no external reference point
    that converts it into an engagement-value estimate on its own.
  - Cost is dollar or token spend on running the tooling. Duration is
    wall-clock time spent running the tooling. Cost and Duration may
    never be reported as a client-billed, engagement-revenue, or
    billable-hours figure.
    - Report each as a rate per tool call, session, or dispatch (e.g.,
      median cost per session) — never as a raw pooled total.
    - Cost may also be reported as a dimensionless share of pooled
      spend, split along any dimension but project, account,
      engagement, or calendar time (e.g., Opus dollars as a
      percentage of total).
    - Duration may not: a share of pooled wall-clock is one hop from
      billable hours per deliverable.
    - A raw total is barred outright. It scales with pool volume, and
      unlike a rate or a share, it converts to an engagement-value
      estimate via public day-rate references.
  - Cadence (how often releases happen) stays excluded even when
    pooled. It describes the engagements' own schedule, not the
    tooling's behavior.

These two lists govern the mixed-corpus figure "What it permits" opens
with, and nothing else. A figure the CLAUDE.md exclusion above already
rules out is out of scope for every condition below.
`pr-cost-section.sh`'s output is the worked case: it calls
`transcript-analysis.py cost --this-repo --branches <branch>
--summary`, and `--summary` exits non-zero unless the scope resolves to
this repository and one account.

**Three standing bars.** These hold across everything ever published
under this carve-out, not only within one figure read alone. Where any
bullet above permits a share split "along any dimension," calendar
time is never one of them.

1. **No time series.** No statistic carries a calendar-time axis. This
   reaches whole-period figures published across successive artifacts
   that together form a time series, not only a single figure with a
   calendar-time axis of its own. A series re-exposes the cadence the
   Cadence bullet above excludes. The one exception is the split
   below.
2. **One split, ever.** Once any split has been published under this
   carve-out — in this repository or any other artifact — no further
   split is permitted. This holds regardless of pivot, same or
   different, and regardless of statistic, same or different. This
   repository's history offers a commit near any date a reader would
   want, so the limit isn't arbitrary. A further split needs a PR
   amending this document, not a fresh proposal under it.
3. **Composition is publication.** This bar reaches any set of
   published figures that together produce a barred result, whether
   they land in one artifact or in separate publications months apart.
   What governs is what the figures together yield, not how any one of
   them is labelled. A permitted rate times a permitted pool-size count
   is the raw pooled total the Cost/Duration bullet bars. Once a split
   is published, nothing further may be published that recovers either
   side's pool size. Publishing the split statistic's own whole-period
   value alongside both sides and the pooled count is one way this
   happens: doing so solves for both sides' pool sizes exactly.

**The one permitted split.** A whole-period figure may be reported
once as a before value and an after value either side of a pivot.
Every condition below must hold.

- **Public pivot.** The pivot is a commit in this repository's own
  public history, cited by SHA or merge date. A date read off the
  data, owner-nominated, or tied to an engagement is disqualified as a
  pivot, regardless of corpus.
- **Named first.** The pivot is named before any command is run
  against the corpus or date range being split, not merely before the
  split-producing command itself. A pivot named only after an
  exploratory query already revealed the shape of the answer was not
  independently named.
- **Same statistic, already-permitted form.** Both sides report the
  same statistic — a label match, not an algebraically derivable
  equivalent. That statistic is reported in a form the lists above
  already permit for it. A raw total is never a valid split form, for
  any statistic, Counts included, even where the base list permits a
  total outside a split. The split adds a second point in time, never
  a new form.
- **One account, one machine.** Both sides resolve to a single
  `CLAUDE_CONFIG_DIR` account and machine. The cross-account share
  exception in "Account and machine scope" below does not extend to a
  split.
- **Exhaustive partition.** Together, the two sides cover exactly the
  period the whole-period figure covered.
- **No per-side pool size.** Neither side's own pool size is
  published. A count on one side of a pivot is pool volume dated
  against calendar time — the cadence the "No time series" bar
  withholds.

**Own-history counts were never inside this class.** The test is
content, not account count.

- A count with no private-engagement records anywhere in its scope —
  this repo's own history, or the owner's other personal, non-client
  repositories — was never a mixed-corpus figure. Examples: branch,
  PR, review-finding, hook-denial, and log-line counts.
- This holds however many accounts or machines the scope unions. A
  `--this-repo` measurement that pools several roots stays outside
  this class for the same reason: every record it counts is this
  repo's own activity, not an engagement's. Unioning more roots
  therefore discloses more of the same thing, not a new one.
- Doubt about whether an account or repository genuinely carries no
  private-engagement data goes to the owner, same as doubt about pool
  diversity below. Doubt is never a reason to publish anyway.
- This exemption covers what a figure is, not whether it can combine
  with an already-published Count-bin to complete a reconstruction —
  see the Count-bin bullet below on narrowing an outstanding bin's
  residual.

**Account and machine scope.** Every reporting mode in both lists
above defaults to a single `CLAUDE_CONFIG_DIR` account and machine —
a total, a rate, and a median all stay there. A dimensionless share
is the one exception: Cost's share-of-spend mode and Counts' share
mode may span accounts or machines. A pooled absolute that crosses
the boundary can be subtracted against another publication of one
account's own absolute, exposing a private engagement's activity on
a shared account.

**Count bin, a narrow exception to the account/machine default.** A
pooled Count total — tool calls, sessions, or agent dispatches, nothing
else — that crosses the account/machine boundary may publish as an
order-of-magnitude label instead of an exact figure. Cost and Duration
are unaffected: Cost stays a rate or a share, never a total or a
range; Duration never crosses the boundary at all.

- **Fixed bins, never chosen per figure.** Below 10 is "fewer than
  10"; 10–99 is "dozens"; 100–999 is "hundreds"; 1,000–9,999 is
  "thousands"; 10,000 or more is "tens of thousands." Every count maps
  to exactly one label, so a missing one can't itself be a signal.
- **Whether to publish at all is the approval gate's call**, not a
  consequence of which bin a figure falls into.
- **No narrowing an already-published bin.** A quantity and any
  sub-period or sub-scope slice of it are never both binned —
  narrower-first or superset-first leaks the same subtraction either
  way.
- **No pairing a bin with its own exact count in the same artifact.**
  The composition bar above treats cross-artifact composition, however
  far apart in time, as the same violation a single artifact would be.
  This bullet is a deliberate, narrow exception to that bar for
  same-artifact pairing specifically. Pairing a bin with an exact
  own-scope count of the same quantity, in the same proposal or
  artifact, lets a reader subtract one from the other directly.
- **The same exact figure can still surface separately, in an
  unrelated artifact** — `pr-cost-section.sh`'s automated per-PR
  session counts, for one. That exposure is an accepted cost this
  bullet doesn't track. The pool-diversity check below is where it's
  actually weighed: the subtraction only resolves to one engagement
  when the pool is thin, and grows weaker as the pool spans more
  accounts.
- **An own-history figure that would narrow an outstanding bin's
  residual needs the owner's word first.** This reaches any own-history
  figure, regardless of its own quantity type or scope shape — for
  example:
  - a same-quantity restatement,
  - a `--this-repo` measurement pooled across several roots,
  - a figure that only narrows the bin once combined with an
    already-published rate.
- **An outstanding bin is one the owner hasn't since refreshed or
  retracted.**
- **Before publishing such a figure, the agent checks for an
  outstanding bin**, searching this repo's own history, prior PR
  bodies, and other artifacts. That is the same search the Approval
  gate below already requires. If one exists, the agent tells the
  owner what the combination would newly disclose and asks for a
  decision in the session.
- **An ordinary in-session answer is enough**, since own-history
  figures sit outside the Approval gate's durable-citation
  requirement. No answer means no publication.

**Approval gate.** An agent never publishes a figure under this
carve-out on its own judgment. It proposes the figure, the exact
command or script that produced it, and the artifact the figure would
land in. The owner approves that figure for that artifact before it
ships. Approval for one artifact does not cover a different one — a
changed destination needs a fresh proposal. Cite the approval as a
durable, independently-checkable record from the owner's own account —
e.g., a link to the approving comment or message. The citation must
tie to the exact figure and artifact it approves. A citation to
anyone else's comment, however definitive it reads, does not satisfy
this gate. A narrative claim that approval
occurred is not a citation either. Absent that citation, don't publish.

For a split under "The one permitted split" above, the proposal
additionally discloses two things, both approval-only input:

- the transcript turn where the pivot was named: a session identifier
  plus turn index or timestamp. This must be as locatable and
  independently-checkable as the pivot's own citation;
- each side's window bounds and pool size, so the owner can judge
  whether either side is thin enough to isolate one engagement.

Neither ever appears in the published figure, and neither is ever
quoted in any commit message, PR body, issue, or other public-repo
artifact. Both travel through a non-public channel, regardless of
which channel carries the approval citation.

Doubt about pool diversity goes to the owner as part of the proposal,
not a reason to publish anyway. The proposal also names where the
agent looked and what it found: any prior publication of the same or
a composing statistic. For a split under "The one permitted split"
above, it additionally searches this repository's own history for a
prior split published under this carve-out, and names what it finds.
That search is diligence, not enforcement. The owner is the one
continuous witness, across this repository and any other publication
artifact, to what has already shipped under it. Finding nothing is
not approval to publish.
This is closed by default, the same as the blocklist tier's "if in
doubt, strip it."

**Remediation.** A wrongly-scoped figure discovered already published
is the owner's call, not the agent's. Stop and report what was
published and where — do not rewrite history yourself, even if told
to. Both stay the owner's to run: a rewrite is not a retraction once a
public repo's history can be cloned, forked, or cached.

## Why the blocklist can't be armed by default

The blocklist *mechanism* is complete and correct; what's missing by default
is data only the user can supply, so `install.sh`'s
`check_private_projects_file` prints a TIP pointing at this doc rather than
populating `<config-dir>/private-projects.md` itself. Four mechanical
alternatives were considered and rejected, each for reducing to the same
missing-data problem or making a security-relevant choice without user
review:

1. **Widen the always-on structural detectors** — the six above already
   exhaust what's structurally identifiable without a name list; further
   candidates just need a name list too, or deny ordinary PR prose constantly.
2. **Auto-derive a starter blocklist** from SSH config hosts, sibling repo
   names, or shell history — always incomplete, and arms a security-relevant
   list without user review.
3. **Force population at install time** — `install.sh` has no way to know a
   user's private projects, so a forced placeholder only reproduces the
   empty-file state the TIP already reports.
4. **Escalate the install-time nudge** — raises the odds the user arms the
   tier themselves, but doesn't arm anything by default on its own.

No mechanical fix closes this without user-specific data, so the tier stays
opt-in: reachable only through the setup below, never through a hook change.

## Opt-in: enable the blocklist

`install-dev.sh` refuses to run until this file exists (a comment-only file
is enough), so contributor setup enforces the opt-in rather than leaving it
silently skippable.

```bash
# Create the file with a header pointing at this doc for usage
# rules (the hook ignores `#` lines, so the header doesn't affect
# matching):
cat > "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/private-projects.md" <<'EOF'
# Project names blocked from commits / PR titles / PR bodies in
# claude-config (and forks). Match semantics + what to put in this
# file: see docs/private-project-redaction.md in the claude-config
# repo.

EOF

# Append your project names, one per line:
echo "Acme Corp" >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/private-projects.md"
echo "Project Bluebird" >> "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/private-projects.md"
```

## File format

- One project name per line.
- Lines starting with `#` are comments; ignored.
- Blank lines ignored.
- Leading and trailing whitespace stripped.
- Names can contain spaces.
- Match is case-insensitive whole-word literal. No regex. No globs.

## What to put in the file (and what NOT to)

The match is **case-insensitive whole-word**, which is narrower than substring
match — `AcmeCorp` matches `AcmeCorp`, `acmecorp`, `ACMECORP` (any casing as a
standalone word), but NOT `AcmeCorpService` (concatenated — `S` is a word
character so the boundary fails), and NOT `acme` inside `acmebrand` (substring
within a word).

**Worked example.** Suppose your private project is `AcmeCorporation` with
tracker prefix `ACME`:

✅ **Add `AcmeCorporation`** — catches the project name as a standalone word in
commits, PR bodies, or added diff lines. Case variants (`acmecorporation`,
`ACMECORPORATION`) match too — you don't need separate entries.

❌ **Don't add `ACME` alone** — the tracker-ID regex already catches
`ACME-<digits>` patterns automatically; bare `ACME` adds nothing the regex
doesn't already cover, while introducing a small false-positive surface for
legitimate standalone uses of the word.

❌ **Avoid very short or common-word codenames as bare entries.** Whole-word
matching shrinks the false-positive surface compared to substring match, but a
3-letter codename like `ART` would still match commits mentioning the word
`art` or `ART` standalone (`ART department review`, `the art of war`). If your
codename is a common standalone word, use a multi-word form (`ART pipeline`
instead of `ART` alone) — the longer phrase is more selective — or rely on
reviewer discipline instead of mechanical match.

**Rule of thumb:**

- **Tracker prefixes** (`[A-Z]{2,}` + dash + digits): trust the tracker-ID
  regex; don't blocklist the bare prefix.
- **Distinctive project names** (full names, codenames ≥ 5 chars and not common
  English words): blocklist them. Whole-word + case-insensitive handles casing
  variants automatically.
- **Concatenated identifiers** (`AcmeCorpService`, `acmecorp_client`,
  `acme-corp-api`): NOT caught by whole-word match against `AcmeCorp`. If a
  project name commonly appears concatenated AND the concatenated form is
  sensitive to leak, add the concatenated form as its own entry.

## Why user-local, not committed

A committed list of private-project names in this public repo would itself be
the leak — it would hardcode in cleartext the exact strings the rule prevents
from shipping. The file lives at `<config-dir>/private-projects.md` directly,
**not** inside `claude-config/claude/.claude/` (which `stow` symlinks into
`$HOME/`). Creating it in the wrong place risks accidental commit; the
repo-root `.gitignore` has a belt-and-suspenders entry for
`claude/.claude/private-projects.md` as a safety net.

## What the deny message reports

When the blocklist or tracker-ID scan blocks a commit or PR, the deny
message names each matched blocklist entry or tracker-ID token and quotes
the offending line(s) from the staged content — the token is already
present in the staged diff, so quoting it discloses nothing new and lets
the agent fix it in one pass instead of bisecting the diff.

Structural-scan denials name only the detector label, not the matched text,
because a structural match (e.g. a hex ID or hostname) can itself be
sensitive and echoing it would leak it into the session transcript.

## Performance

Measured per-fire wall-clock cost of the full hook (tracker-ID scan, six
structural detectors, private-projects blocklist scan) against a
`gh api -X POST ... -F body=@<file>` call with a representative body file,
run directly against `deny-private-project-refs.sh` with a synthetic
`tool_input` payload on stdin, 5 runs at each size on a loaded development
machine (other concurrent sessions were running on the same machine at
measurement time, which the wide ranges below partly reflect):

| Body size | Median | Range observed |
|---|---|---|
| 5 KB | 640ms | 563–751ms |
| 50 KB | 697ms | 562–1,312ms |
| 500 KB | 894ms | 837–1,017ms |

This still exceeds this repo's stated hook performance budget (<100ms per
fire). The six structural detectors run as one combined-pattern fast-path
spawn, falling through to the original six only when it matches, so a
here-string bash materializes to a temp file before exec at most once per
fire instead of up to six times. Subprocess-spawn overhead still dominates over
byte-scanning cost — the fast path itself, plus the pre-existing tracker-ID
and blocklist scans' own subprocess calls, remain unchanged — which is why
cost still does not scale cleanly with body size. At commit/PR-authoring
time (a human-interactive action, not a hot path), this is tolerable in
absolute terms but is still a measured budget overrun, not a clean pass — a
future revision that needs more headroom should look at collapsing the
remaining tracker-ID and blocklist `grep` spawns into the same fast-path
treatment.

## For fork contributors

Forks of `claude-config` inherit the same hook (the scoping check passes for
any `claude-config` substring in the origin URL). A fork user can drop their
own `<config-dir>/private-projects.md` and contribute back without their project
names ever ending up in a PR they open against the upstream. `install-dev.sh`
requires that file to exist before it will set up a contributor's `.venv`.
