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

## Publishing a tooling measurement

This is the tier-3 companion to the two mechanical tiers above. It is
reviewer discipline, not a hook. A measurement's safety depends on
which corpus produced it, not what string it contains, and a hook
can't see that. The repo-root
[`CLAUDE.md`](../CLAUDE.md) "Also redact structural fingerprints and
provenance" rule states the bar this section gates.

### This repository, one account

A measurement of this repo's own tooling in use is publishable when it
is computed over this repository's own corpus on a single
`CLAUDE_CONFIG_DIR` account. That corpus holds no private-engagement
record, so a figure drawn from it carries no engagement's fingerprint
to redact.

Scope it with a command that *refuses* a wider corpus, and cite that
command beside the figure. Avoiding a wider corpus by choosing flags
carefully is not the same thing: the refusal is what a reader can
re-run and check.

- `transcript-analysis.py cost --this-repo --summary` is the worked
  case. `--summary` requires `--this-repo`, refuses every other scope
  flag, resolves to the active config dir alone, and exits 2 if more
  than one root is ever in scope.
- `pr-cost --record` refuses differently, to the same effect. It
  unions every declared root, then exits 2 rather than write or report
  a blended total when more than one resolves. `--all-accounts` opts
  into a per-account loop, whose output publishes nothing — see the
  next section.
- A subcommand with no scope refusal of its own is not a publication
  instrument. Route it through the next section instead.

`pr-cost-section.sh` is a standing pre-cleared instance of this bar,
not a per-publication judgment call. Gated by the opt-in
`<config-dir>/pr-cost-disclosure` sentinel, off by default so a fork
contributor publishes nothing until they enable it. It embeds two
scope-fixed-by-construction blocks in every merged PR's body: a dollar
total from `cost --this-repo --branches <branch> --summary`. The other
is a review-round/subagent-spawn count from `cost-counts --this-repo
--branches <branch>`, which always resolves to a single hardcoded root
regardless of any flag. Neither call can be pointed at a wider corpus,
so there is nothing for a reviewer to approve per PR — the review
question is answered once, here, rather than re-litigated on every
merge. A diff that widens either call's scope, or weakens a refusal a
publication instrument depends on, is a P1 finding.

Within that scope nothing further is withheld. A total, a rate, a
median, a share, a pool size, a per-day series, a before/after split
at any pivot — all publishable. This repository's own commit and PR
history is already public, so neither a calendar axis nor a volume
count over it discloses anything the repository does not already
disclose, and the dollars are the owner's own spend on public work.

Doubt about whether a given repository or account genuinely carries no
private-engagement record goes to the owner. Doubt is never a reason
to publish anyway.

### A wider corpus goes to the owner, never into a public artifact

Machine-wide, multi-account, and cross-machine questions are worth
asking. They are not worth publishing. Measure them with the same
tools at their wider scope — `cost` without `--summary` (add
`--share-only` to keep raw absolutes out of the agent's context),
`pr-cost --all-accounts`, or any subcommand at its default machine-wide
scope. Report the figures to the owner in session or through another
non-public channel.

Publish nothing computed from such a read: no total, no rate, no
share, no count, no bounded range, in any artifact — commit message,
PR body, issue, decision record, case study, or illustrative example.
A public artifact may record that the read happened and what it
decided: which lever was adopted or declined, and which way the
reading pointed. It names no figure from it. The withholding sites in
`docs/cost-levers-considered.md` and `docs/design-decisions/` are the
worked shape.

### The owner can authorize one figure, case by case

The bar above holds by default: a wider read stays with the owner
and publishes nothing. The owner can release it for one specific
figure. Propose the figure, the exact command that produced it, and
the artifact it would appear in. Publish only after an explicit,
in-session yes. Cite that authorization beside the figure, naming
what was proposed and the timestamp of the yes. A bare claim that
approval occurred, with nothing to check it against, is not a
citation. A recalled yes from another transcript or artifact does
not count; only a live answer in the current session does. This
matches the standard set by "New figures against the grandfathered
set" below. The command is cited so a reader can re-run the
measurement. Unlike the single-account case above, authorization
guarantees nothing about scope by itself; it is the whole control.
That is why it is granted per figure and left as a citable trace.
The mechanical backstop is this repository's own human-only merge
gate: the owner reviews every PR before it merges and can catch a
citation for a yes that was never given.

An authorization covers the figure, the command, and the artifact it
named. A second figure, the same figure in a second artifact, or a
re-run over a grown corpus is a fresh ask. Cite each authorized
figure on its own — one citation spanning several figures does not
establish that each was individually proposed and approved. Before
citing an authorized figure, check whether it composes with an
already-published rate or count to reconstruct a calendar-time
series or narrow a boundary. That comparison source can be the
grandfathered set below or an earlier authorization under this
section. If it composes, name that composition in the proposal.
"New figures against the grandfathered set" below is the mechanical
half of that check; it does not cover composition against a prior
authorization on its own.

An authorization releases the corpus-scope bar and nothing else. Four
things stay barred alongside it:

- A figure carrying a per-project, per-account, or per-engagement
  dimension — barred absolutely by the repo-root `CLAUDE.md`, since
  this section only relaxes scope, not dimension.
- A count of accounts or declared config-dir roots — see "Account
  cardinality" below.
- `--share-only` output — it keeps a wider read's absolutes out of
  the agent's context, not a publication instrument.
- A figure with its own calendar-time axis (per-week, per-month, or a
  two-point before/after split) drawn from a wider corpus — barred
  even as a single authorized figure, since no split mechanism
  exists here to sanction one.

### Own-history counts were never inside this class

The test is the scope's content, not the account or machine count, and
not the quantity's type. Two classes fall outside the wider-corpus bar
rather than being exceptions to it:

- A count whose scope holds no private-engagement record anywhere —
  this repo's own history, or the owner's other personal, non-client
  repositories. Examples: branch, PR, review-finding, hook-denial, and
  log-line counts. This holds however many accounts or machines the
  scope unions, for a count with no per-account or per-machine
  decomposition: unioning more roots discloses more of the same thing,
  not a new one.
- It does not extend to a `transcript-analysis.py` Cost, Duration, or
  activity measurement — tool calls, sessions, dispatches, dollars, or
  wall-clock — at a scope wider than the bar above. Those read
  whatever private work the roots in scope contain, which is what the
  bar keeps out of a published figure.

This exemption covers what a figure is, not what it combines with —
see "New figures against the grandfathered set" below for the
composition check every new figure clears, own-history or not.

### Account cardinality

How many accounts or declared config-dir roots exist is never
published as a digit or a bounded range, at any pooling breadth. An
account can correspond to a single private engagement, so its
cardinality is the per-account dimension the repo-root `CLAUDE.md`
bars absolutely. `docs/transcript-analysis.md`'s sample outputs elide
the root count for this reason.

How many machines exist may be stated as a digit. A machine is the
operator's own hardware and partitions no engagement. This repository
states its own machine count in ordinary prose. Doubt about whether an
operator's own machine boundary correlates with an engagement boundary
— as it could for a fork contributor running client-dedicated hardware
— goes to that operator, the same as every other content-purity
judgment call in this section.

### New figures against the grandfathered set

Figures published before this bar stay published (see "Remediation"
below). A new figure — own-history, drawn from this repository on one
account, or an owner-authorized wider-corpus figure — can still newly
disclose something private if it lets a reader subtract it from one of
those down to its non-this-repo remainder. Before publishing, check
the new figure against Remediation's named list. If it could narrow
one of those figures' residual, tell the owner what the combination
would newly disclose and ask in session; an uncited in-session answer
settles it.

This check runs once per new figure, forward from here. It does not
require re-auditing the grandfathered set against this repository's
own accumulating totals — the owner has weighed that once (see
"Remediation") and it is not re-litigated per publication.

### Remediation

Content published before this bar took effect stays as published. The
grandfathered set, computed over a machine-wide, multi-account corpus,
predates this section's current single-account bar:

- `docs/case-studies/handoff-threshold-impact.md`, entirely
- `docs/case-studies/handoff-hard-block-position.md`, entirely
- `docs/case-studies/cold-cache-attribution.md`, entirely
- `docs/case-studies/check-runner.md`'s Retirement section, entirely —
  its 649-session corpus window, byte-size distribution, dispatch
  count, and percentage breakdown all derive from the same pooled
  measurement
- `docs/case-studies/check-runner.md`'s "Re-grounding: verdict quality,
  not context cost" section, for its "median of roughly 100 tokens"
  inline-test-run figure, drawn from an unscoped local-transcript-corpus
  measurement
- `docs/case-studies/worktree-enforcement.md`'s "The general
  philosophy" section, for its 785-session/387-denial/243-session
  `review-trace` corpus measurement
- `docs/cost-levers-considered.md`'s machine-wide pooled review-round
  share and machine-wide rows
- `docs/case-studies.md`'s index blurbs
- `docs/case-studies/effort-estimation-review-surface.md`,
  `hashline-edit-format.md`, `targeted-read-discipline.md`,
  `delegate-instrument-authoring.md`, `plan-mode-model-resolution.md`,
  `opus-frontload-review-rounds.md`, `markdown-context-ingestion.md`,
  and `review-vs-babysitting.md`

The bar above governs what ships next, not what already shipped:
rewriting a published figure is not a retraction once a public repo's
history can be cloned, forked, or cached — it only adds a second
version.

Whether this repository's own now-accumulating single-account totals
(`pr-cost-section.sh`'s per-PR figures) already compose against this
grandfathered set closely enough to matter is the owner's call, made
once here: no further action. The grandfathered figures predate this
bar and their relevance decays as the corpus ages; this is not
re-audited per PR.

A wrongly-scoped figure discovered already published is the owner's
call, not the agent's. Stop and report what was published and where —
do not rewrite history yourself, even if told to.

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
