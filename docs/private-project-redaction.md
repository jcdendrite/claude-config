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

### What it permits

A figure pooled across a corpus that mixes private and public sources
is publishable when it carries no per-project, per-account,
per-machine, or per-engagement dimension. Cite the command or script
that produced it.
That command or script must itself be an aggregation boundary. It may
read the mixed corpus internally, but its output to the agent is the
rounded pooled figure only — never per-session or per-project raw
content.

### Scope — two closed lists

Both have to be satisfied, and neither extends by analogy:

- *What may be counted:* this repo's own tooling in use — Claude Code
  tool calls, sessions, and agent dispatches. Nothing else.
- *How it may be reported:* A PR or branch count is freely publishable
  on its own under "Own-history counts were never inside this class"
  below. That exemption covers what a bare count is, not what may
  serve as a rate or granularity unit for the Counts or Cost/Duration
  bullet below.
  - Counts may be reported as a total, a share, or a median. Report
    each at any granularity — per tool call, per session, per
    dispatch, or pooled. A count carries no external reference point
    that converts it into an engagement-value estimate on its own.
    Counts carry no account or machine pooling boundary: report a
    Count total, share, or median at any pooling breadth, including
    one spanning every account on a machine or several machines. This
    governs how wide the pool may be, never how it may be broken
    down. A Count split along the project, account, machine,
    engagement, or calendar-time dimension stays barred by the
    share-split bullet below. A Count published alongside a per-unit
    Cost rate stays barred by "Composition is publication." A pooled
    Count that crosses the boundary can still be subtracted against
    another publication of one account's or one machine's own exact
    Count of the same quantity — the same account-isolation risk
    "Account and machine scope" below names for Cost. The Approval
    gate's disclosure duty for a boundary-crossing Count exists to
    catch this before it publishes.
  - Cost is dollar or token spend on running the tooling. Duration is
    wall-clock time spent running the tooling. Cost and Duration may
    never be reported as a client-billed, engagement-revenue, or
    billable-hours figure.
    - Report each as a rate per tool call, session, or dispatch (e.g.,
      median cost per session) — never as a raw pooled total.
    - Duration may never be reported as a share: a share of pooled
      wall-clock is one hop from billable hours per deliverable.
    - A raw total is barred outright. It scales with pool volume, and
      unlike a rate or a share, it converts to an engagement-value
      estimate via public day-rate references.
  - Both Cost's share-of-spend mode and Counts' share mode may split
    along any dimension except project, account, machine, engagement,
    or calendar time. Opus dollars as a percentage of total is
    permitted; one account's or one machine's share of pooled tool
    calls is not. A share split along an excluded dimension hands a
    reader one side's value the moment it's paired with an
    already-permitted exact figure for that side — see the worked
    rejection under "Composition is publication" below.
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

### Three standing bars

These hold across everything ever published under this carve-out, not
only within one figure read alone.

1. **No time series.** No statistic carries a calendar-time axis. This
   reaches whole-period figures published across successive artifacts
   that together form a time series, not only a single figure with a
   calendar-time axis of its own. A series re-exposes the cadence the
   Cadence bullet above excludes. The one exception is the split
   below.
2. **One split, ever.** This carve-out defines exactly one narrow
   exception to "No time series" above: the split. Across everything
   ever published under this carve-out, at most one split may ever be
   published. Once one lands, in this repository or any other
   artifact, no further split is permitted, regardless of label or
   statistic, same or different. This repository's history offers a
   commit near any date a reader would want, so the split's limit
   isn't arbitrary. A further instance needs a PR amending this
   document, not a fresh proposal under it.

   Only a split published under this carve-out spends this allowance.
   Content predating this carve-out does not, whatever shape it takes.
   Where such content sits beside a new proposal, the composition bar
   below governs instead.
3. **Composition is publication.** This bar reaches any set of
   published figures that together produce a barred result, whether
   they land in one artifact or in separate publications months apart.
   What governs is what the figures together yield, not how any one of
   them is labelled. A permitted rate times a permitted pool-size
   count is the raw pooled total the Cost/Duration bullet bars, for
   instance.

   *Worked rejection.* Account P carries no private-engagement
   records, so its own transcript-analysis.py tool-call count is
   freely publishable under "Own-history counts were never inside
   this class" below, absent any already-published carve-out figure
   it would narrow — say, 4,000 tool calls. If a Counts share could
   split along the account dimension, "Account P: 40% of pooled tool
   calls" would combine with that exact count to solve the pooled
   total (10,000) by division, then Account Q's own exact count
   (6,000) by subtraction, without either published figure naming an
   account's total directly. Barring a share split along the account
   dimension (see "Scope — two closed lists" above, which bars the
   same for machine) is what keeps this reconstruction from starting.

### The one permitted split

A whole-period figure may be reported once as a before value and an
after value either side of a pivot. Every condition below must hold.

- **Public pivot.** The pivot is a commit in this repository's own
  public history, cited by SHA or merge date. Disqualified as a pivot,
  regardless of corpus:
  - a date read off the data;
  - an owner-nominated date;
  - a date tied to an engagement.
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
  `CLAUDE_CONFIG_DIR` account and machine. Neither of "Account and
  machine scope" below's two exceptions — Cost's cross-account/
  cross-machine share mode, or Counts' unscoped reporting — extends to
  a split.
- **Exhaustive partition.** Together, the two sides cover exactly the
  period the whole-period figure covered.
- **No per-side pool size.** Neither side's own pool size is
  published. A count on one side of a pivot is pool volume dated
  against calendar time — the cadence the "No time series" bar
  withholds.

### Own-history counts were never inside this class

The test is content, not account or machine count.

- A count with no private-engagement records anywhere in its scope —
  this repo's own history, or the owner's other personal, non-client
  repositories — was never a mixed-corpus figure. Examples: branch,
  PR, review-finding, hook-denial, and log-line counts.
- This holds however many accounts or machines the scope unions, for
  a count with no per-account or per-machine decomposition. Branch,
  PR, review-finding, hook-denial, and log-line counts have none, so
  unioning more roots discloses more of the same thing, not a new
  one. It does not extend to a `transcript-analysis.py` Cost or
  Duration measurement of tool calls, sessions, dispatches, dollars,
  or duration — that measurement type carries exactly the per-account
  or per-machine decomposition "Cost and Duration scope" above exists
  to govern, and stays inside that machinery regardless of
  `--this-repo` scoping. A `transcript-analysis.py` Count of the same
  tool calls, sessions, or dispatches is different: it carries no
  account or machine default to begin with, since Counts carry none —
  see "Counts are not scoped by this boundary" above. It still answers
  to the closed lists, the time-series bar, the composition bar, and
  the Approval gate below, the same as every other figure this
  carve-out governs.
- Doubt about whether an account, machine, or repository genuinely
  carries no private-engagement data goes to the owner, same as doubt
  about pool diversity below. Doubt is never a reason to publish
  anyway.
- This exemption covers what a figure is, not whether it can combine
  with any figure already published under this carve-out to complete
  a reconstruction. An own-history figure that would narrow the
  residual of any figure already published under this carve-out —
  directly or through a published rate — needs the owner's word
  first. This reaches any own-history figure regardless of its own
  quantity type or scope shape:
  - a same-quantity restatement;
  - a `--this-repo` hook-denial or log-line count pooled across
    several roots;
  - a figure that only narrows the residual once combined with an
    already-published rate.
- Before publishing such a figure, the agent checks this repository's
  own history, prior PR bodies, and other artifacts for an
  already-published figure under this carve-out whose residual it
  would narrow — the same search the Approval gate below already
  requires for a split's own proposals. If one exists, the
  agent tells the owner what the combination would newly disclose and
  asks in the session. An uncited in-session answer satisfies this,
  not the durable citation the Approval gate below requires, since the
  own-history figure was never inside that gate. Absent an answer, the
  agent holds the figure rather than publishing it.

### Account and machine scope

**Cost and Duration scope.** Cost's and Duration's reporting modes
default to a single `CLAUDE_CONFIG_DIR` account and a single machine —
a total, a rate, and a median all stay there. Cost's dimensionless
share-of-spend mode is the one exception and may span accounts or
machines. Duration has no share mode, so Duration never crosses the
boundary at all. A pooled Cost absolute that crosses the boundary can
be subtracted against another publication of one account's or one
machine's own absolute, exposing a private engagement's spend on a
shared account or machine.

**Counts are not scoped by this boundary.** See the Counts bullet
under "Scope — two closed lists" above.

**Account cardinality and machine cardinality are different
questions.** How many accounts or declared roots exist is never
published as a digit or a bounded range, at any pooling breadth. An
account can correspond to a single private engagement, so its
cardinality is the per-account dimension the repo-root `CLAUDE.md`
bars absolutely. How many machines exist may be stated as a digit. A
machine is the operator's own hardware and partitions no engagement.
This repository states its own machine count in ordinary prose.

### Approval gate

An agent never publishes a figure under this carve-out on its own
judgment. It proposes the figure, the exact command or script that
produced it, and the artifact the figure would land in. The owner
approves that figure for that artifact before it ships. Approval for
one artifact does not cover a different one — a changed destination
needs a fresh proposal. Cite the approval as a durable,
independently-checkable record from the owner's own account — e.g., a
link to the approving comment or message. The citation must tie to
the exact figure and artifact it approves. An approval given in
session qualifies only while the owner is an active party to that
session. Cite it by session identifier plus turn index or timestamp,
naming the proposal's turn and the approval's turn separately when
they differ. Before the artifact ships, the owner confirms the
approval that citation points at. That confirmation stands in for a
durable record the citing agent has no write access to produce
itself. The confirmation covers every citation this gate requires.
That includes a split's approval, and the pivot-naming turn it
discloses. A citation to anyone
else's comment, however definitive it reads, does not satisfy this
gate. A narrative claim that approval occurred is not a citation
either. Absent that citation, don't publish.

For a split under "The one permitted split" above, the proposal
additionally discloses two things, both approval-only input:

- the transcript turn where the pivot was named: a session identifier
  plus turn index or timestamp. This must be as locatable and
  independently-checkable as the pivot's own citation;
- each side's window bounds and pool size, so the owner can judge
  whether either side is thin enough to isolate one engagement.

For a pooled Count that spans more than one account or machine, the
proposal additionally discloses which accounts or machines contribute
to the pool, so the owner can judge whether it draws from a thin pool,
and names any already-published or routinely-automated single-account
or single-machine exact figure of the same quantity —
`pr-cost-section.sh`'s per-PR exact session counts, published
automatically on every merged PR once `pr-cost-disclosure` is enabled,
at minimum — so the owner can weigh whether the pooled figure and that
exact figure together isolate one account's or machine's own count by
subtraction.

None of this ever appears in the published figure, and none of it is
ever quoted in any commit message, PR body, issue, or other
public-repo artifact. All of it travels through a non-public channel,
regardless of which channel carries the approval citation.

Doubt about pool diversity goes to the owner as part of the proposal,
not a reason to publish anyway. The proposal also names where the
agent looked and what it found: any prior publication of the same or a
composing statistic, whether or not it was published under this
carve-out. Where one exists, the proposal states what the combination
would newly disclose.

*Worked disclosure.* A new split's before-side window overlaps a
pre-existing publication's own dated activity checkpoints — say, the
pre-existing publication states pooled activity volumes as of two
dated checkpoints, and the new split's before-side window falls
between them. Neither figure states a pool size on its own, but a
reader combining the split's share with the pre-existing publication's
dated checkpoints can narrow the window the split's own before-side
activity falls in more tightly than either figure discloses alone.
That narrowing — not merely the pre-existing publication's existence —
is what the proposal must name.

For a split, it additionally searches this repository's own history
for a prior instance published under this carve-out, and names what
it finds. That search is diligence, not enforcement. The owner is the
one continuous witness, across this repository and any other
publication artifact, to what has already shipped under it. Finding
nothing is not approval to publish.

Absent the durable citation this gate requires, the whole carve-out is
closed by default, the same as the blocklist tier's "if in doubt,
strip it."

### Remediation

A wrongly-scoped figure discovered already published is the owner's
call, not the agent's. Stop and report what was published and where —
do not rewrite history yourself, even if told to. Both stay the
owner's to run: a rewrite is not a retraction once a public repo's
history can be cloned, forked, or cached.

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
