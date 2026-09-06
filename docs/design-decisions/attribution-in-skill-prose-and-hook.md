# Attribution stays in skill prose and a hook gate rather than the native `attribution` settings key

*2026-09-01. Formerly `docs/design-decisions.md` §40.*

The `attribution` settings object has three properties: `commit`, `pr`,
and `sessionUrl`. Together they reach two surfaces: commit messages and
pull request *bodies*. Neither this repo's `.claude/settings.json` nor
the stow-source `claude/.claude/settings.json` sets it. None of the
attribution prose in `pr-description/SKILL.md` or `respond-pr/SKILL.md`
can be deleted in its favor, for three independent reasons below.

**The largest surface is out of the setting's reach entirely.**
`respond-pr/SKILL.md`'s Attribution section requires every PR or issue
comment reply to open with `**[Claude Code]**` and close with the
disclosure trailer. `require-respond-pr.sh` denies `gh pr comment`,
`gh issue comment`, `gh pr review`, the REST `comments`/`reviews`
endpoints, and the equivalent GraphQL mutations at the tool-call
boundary unless the write is routed through that skill. `attribution`
has no property covering review comments, so no candidate key exists to
replace any of it.

The prefix also does a second job no attribution string could. Replies
post through the user's own GitHub token and appear under the user's
account, so a `user.login` check cannot distinguish a reply Claude wrote
from a comment the user wrote. The prefix is what `respond-pr` checks
before a PATCH edit to avoid overwriting the user's own text
irrecoverably, so deleting it would remove a data-loss guard, not only a
disclosure line.

**On the one surface both cover, the required shapes differ.**
`pr-description/SKILL.md` places the trailer at both the first line and
the last line of the body. `attribution.pr` is a single appended string,
so it could supply the bottom copy at most, leaving the skill to
prescribe the top one regardless.

**The setting sits at the advisory tier [§1](hook-enforced-gates.md) distinguishes from a hook.**
Its effect is mediated by an instruction the model receives and must
then choose to follow, not by post-processing applied to the commit or
body after the fact, so it is exposed to the same reasoned-around
failure that motivated hook enforcement everywhere else here. Two
upstream reports show that tier failing. anthropics/claude-code #65657
reports the system-prompt `Co-Authored-By` trailer taking precedence so
the `attribution.commit` value is never applied. It is closed as not
planned. #77830 reports the `Claude-Session:` trailer being injected
through the Bash tool description and ignoring `attribution`. The legacy
footer *is* correctly suppressed by the same setting — only the newer
trailer ignores it. #77830 is closed, labeled a bug, and marked
reproduced.

The two reports disagree about which half fails. Both were read as
rendered issue pages rather than full comment threads, so the
discrepancy is recorded here rather than resolved. Either report alone
is enough to rule out trading mechanically-gated prose for the setting.

Commit trailers are a separate question from PR bodies. No hook and no
`CLAUDE.md` line prescribes commit-trailer text anywhere in this repo,
so setting `attribution.commit` now would invent a convention rather
than codify an existing one. #65657 above already documents that exact
mechanism as unreliable for the commit case. Setting it in the
stow-source `claude/.claude/settings.json` instead would compound the
problem by imposing that invented choice on every consumer's every
repository — wider than any condition this repo has, the same scoping
reasoning [§39](claudemdexcludes-nested-discovery-duplicate.md) applies to `claudeMdExcludes`.

`sessionUrl` was evaluated separately as the one property that would
have changed behavior, by suppressing the `Claude-Session:` deep link
this public repo's commits and PR bodies otherwise carry. It is left at
its default of `true`. The link resolves against the owner's own
account. Claude Code sessions are private by default regardless of the
surface that started them. They become visible to anyone else only when
their owner explicitly shares that session. The schema documents the
trailer as appended when running from a web or Remote Control session.
Whether it also appears on commits authored from a plain CLI session is
not established. #77830 does not name the surface it reproduced on.

**Revisit** when `attribution` gains a property covering PR or issue
review comments, or when both reports above close with a shipped fix.
Neither condition alone is enough: a fix with no review-comment property
still leaves `require-respond-pr.sh` and `respond-pr`'s prefix doing
work nothing else does.

## Sources

- [Claude Code settings reference](https://code.claude.com/docs/en/settings), Attribution settings section — the `attribution` object, its three properties, and the two surfaces it covers.
- [SchemaStore `claude-code-settings.json`](https://www.schemastore.org/claude-code-settings.json) — machine-readable property list confirming no review-comment property exists, and `sessionUrl`'s documented web/Remote-Control scoping.
- [anthropics/claude-code #65657](https://github.com/anthropics/claude-code/issues/65657) — report that the system-prompt trailer overrides `attribution.commit`; closed as not planned.
- [anthropics/claude-code #77830](https://github.com/anthropics/claude-code/issues/77830) — report that the `Claude-Session:` trailer is injected via the Bash tool description and ignores `attribution`; closed, labeled a bug and reproduced.
- [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web), "Share sessions" — sessions are private by default and become visible to others only on an explicit share.
- `claude-skills/skills/respond-pr/SKILL.md` — the Attribution section's prefix-and-trailer requirement, and the prefix's pre-PATCH self-authorship check.
- `claude-skills/skills/pr-description/SKILL.md` — the top-and-bottom trailer placement rule.
- `claude/.claude/hooks/require-respond-pr.sh` — the tool-call-boundary gate routing every comment write through the skill that applies the prefix.
