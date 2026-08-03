# Private-project redaction

This repo is public, so any project codename, organization name, or tracker-ID
that lands in a commit or PR description ships to the world. The repo-root
[`CLAUDE.md`](../CLAUDE.md) "Redact private-project-identifying content" rule
defines what to keep out; `deny-private-project-refs.sh` is the mechanical
enforcement. For the high-level three-tier overview, see the
[README](../README.md#private-project-redaction).

## The three scans

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
3. **Private-projects blocklist (opt-in).** Reads `~/.claude/private-projects.md`
   at hook runtime and blocks commits/PRs whose content contains any
   non-comment, non-blank line from the file as a case-insensitive whole-word
   match.

## The six structural detectors

Unlike the blocklist, these run unconditionally — no `~/.claude/private-projects.md`
setup required. Each is checked independently, so the deny message names
which one fired. Regexes live in `_lib.sh` as `_LIB_IPV4_LITERAL_REGEX` and
its five siblings, shared with any future consumer that needs the same
definitions.

| Detector | Catches | Does NOT catch |
|---|---|---|
| IPv4 literal | four dot-separated numeric groups (a machine's network address) | an IPv6 address |
| SSH key path reference | a path segment naming the SSH configuration directory, or a filename following the `id_<algorithm>` convention (rsa/dsa/ecdsa/ed25519) | a custom-named key file with no `id_<algorithm>` shape |
| Home-rooted path | a path rooted at `/Users/<username>/` or `/home/<username>/` | a relative or repo-rooted path |
| Long hex identifier | a 32+ character contiguous hex run, or a UUID-shaped four-hyphen-group hex sequence | a shorter hex run (e.g. a short git SHA) |
| Internal hostname | a hostname ending in `.internal`, `.corp`, `.local`, `.lan`, `.intranet`, or `.private` | a hostname on any other TLD |
| Slack-channel shape | a `#`-prefixed lowercase-hyphenated word (also matches a markdown anchor link sharing the same shape, deliberately — see below) | a plain GitHub issue reference like `#421` (all-digit, excluded so this scan doesn't collide with ordinary issue cross-references) |

Every illustrative shape above is written so it does not itself match the
pattern it describes — committing this table must not trip the very
detectors it documents. The angle-bracket placeholder in the home-rooted-path
row (`/Users/<username>/`) is deliberate: `<` falls outside the detector's
character class (`[A-Za-z0-9_.-]`), so the placeholder form never matches.

The Slack-channel detector's markdown-anchor collision is intentional, not an
oversight: this repo's own docs use `#`-anchor links (a heading-derived
fragment appended to a file path, e.g. `docs/skills.md#<heading-slug>`),
which share the identical shape as a real Slack channel name (lowercase,
hyphenated). Loosening the charset to exclude that shape would defeat the
detector's actual purpose — rephrase around a false positive rather than
narrow the pattern.

## Opt-in: enable the blocklist

```bash
# Create the file with a header pointing at this doc for usage
# rules (the hook ignores `#` lines, so the header doesn't affect
# matching):
cat > ~/.claude/private-projects.md <<'EOF'
# Project names blocked from commits / PR titles / PR bodies in
# claude-config (and forks). Match semantics + what to put in this
# file: see docs/private-project-redaction.md in the claude-config
# repo.

EOF

# Append your project names, one per line:
echo "Acme Corp" >> ~/.claude/private-projects.md
echo "Project Bluebird" >> ~/.claude/private-projects.md
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
from shipping. The file lives at `~/.claude/private-projects.md` directly,
**not** inside `claude-config/claude/.claude/` (which `stow` symlinks into
`$HOME/`). Creating it in the wrong place risks accidental commit; the
repo-root `.gitignore` has a belt-and-suspenders entry for
`claude/.claude/private-projects.md` as a safety net.

## What the deny message reports

When the blocklist scan blocks a commit or PR, the deny message names each
matched blocklist entry and quotes the offending line(s) from the staged
content. The matched token is the user's own private-project name, already in
the staged content and in `~/.claude/private-projects.md`; naming it in the
deny discloses it to no new party, while letting the agent locate and remove it
in one pass rather than bisecting the diff. The tracker-ID scan similarly names
matched tokens.

The structural-shape scan is the deliberate exception: its deny message names
only the detector label (e.g. "long hex identifier"), never the matched
text. Unlike a tracker-ID token or a blocklisted project name, a structural
match can itself be sensitive — a long hex identifier could be a live session
ID, an IPv4 literal or internal hostname is network-recon-value data — so
echoing it into the deny message would persist it into the session's
transcript rather than merely repeating content already in the diff.

## Known gaps

`gh issue create` and `gh issue comment` publish content the same way
`gh pr create` and `gh api` do, but the hook's dispatch logic has no branch
recognizing `gh issue` at all — content posted that way is never scanned by
any of the three scans above. Closing this is separate work: `gh issue`
takes its body via `--body` inline text, not the `-f`/`-F` field-value-file
flags the `gh api` scan already resolves.

## For fork contributors

Forks of `claude-config` inherit the same hook (the scoping check passes for
any `claude-config` substring in the origin URL). A fork user can drop their
own `~/.claude/private-projects.md` and contribute back without their project
names ever ending up in a PR they open against the upstream.
