# Suppress the `Claude-Session:` URL trailer on public commits

## Context

Stop any further Claude Code session URL from reaching a commit message
or PR body from this repository, and record that a prior decision to
allow them is reversed.

Claude Code's harness appends a `Claude-Session:
https://claude.ai/code/session_<id>` trailer to commit messages. On this
**public** repository, 113 of the 116 commits merged to `main` since
2026-08-21 carry one; 533 carry one across all branches. The trailer is
governed by the native `attribution.sessionUrl` setting, which defaults
to `true` and is set in neither `claude/.claude/settings.json` nor
`.claude/settings.json`.

Why now: the engineer found the trailer on the `memory-audit-nudge`
branch, had not intended to publish session pointers, and asked for a
root-cause analysis and a fix.

Intended outcome: `attribution.sessionUrl` — verified to suppress the
trailer at its source — is set to `false` at both settings scopes, and
`docs/design-decisions.md` §40 carries a superseding note recording both
the reversal and a factual correction to its account of the upstream
issue. Already-published history is deliberately left alone.

### Root cause

The harness injects a per-session attribution instruction into the
system prompt. It directs that commit messages end with `Co-Authored-By:`
followed by a `Claude-Session:` trailer carrying the session URL, and
that PR descriptions end with the `🤖 Generated with [Claude Code]`
line followed by a bare session URL on its own line.

The native control is `attribution.sessionUrl`. Its published schema
gives `"type": "boolean"` and `"default": true`, and describes it as
appending "the claude.ai session link as a Claude-Session trailer on
commits and a link in pull request descriptions when running from a web
or Remote Control session."

Setting it to `false` works. In anthropics/claude-code #77830 an
Anthropic collaborator names it as the trailer's control, and a reader
of the shipped 2.1.231 bundle quotes the early return that implements
it. The key has existed since v2.1.182, and this machine runs 2.1.261.

Two related traps are documented in that thread. Setting
`attribution.commit` to an empty string does not suppress the trailer:
the empty value is falsy, so the trailer is emitted as the *sole*
trailer rather than being appended to a `Co-Authored-By:` line. A
separate environment variable,
`CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION`, short-circuits ahead of the
settings check.

Commit messages leak far more consistently than PR bodies, and that
asymmetry is surface-dependent rather than absolute. A scan of all 720
pull requests found no PR body containing a session URL today, but that
reflects manual correction, not systemic prevention: the engineer recalls
(an approximate count, not an audited tally) catching and removing a bare
session-URL line from two or three PR bodies within roughly the past two
weeks `[engineer-reported]`. `claude-skills/skills/pr-description/SKILL.md`
prescribes its own attribution trailer as a literal string with no URL,
and its `## Checks` step validates that trailer's presence and
uniqueness — but no check screens for an *additional* bare URL line the
harness instruction can still append after it, so the skill does not
actually displace that instruction on the PR surface. Nothing prescribes
commit-trailer text either, so on that surface the harness instruction
passes unopposed. The PR body's clean published history is therefore
attributable to the engineer's own review, not to any automated control.
The engineer separately recalls this window coinciding with a recent
increase in their own Remote Control session usage `[engineer-reported]`.
Row 9 weighs that correlation against counter-evidence: a reviewer
subagent dispatched during this plan's own review round, not a web or
Remote Control session by any definition, reported that its own system
prompt carried the identical PR-description instruction unconditionally.
Row 9 does not treat the correlation as settled.

Those commit trailers reach `main` because this repository permits only
squash merges and sets `squash_merge_commit_message` to
`COMMIT_MESSAGES`, so GitHub concatenates each branch commit's message
into the squash commit's body.

Onset is sharp. The first trailer-bearing commit is dated 2026-08-21,
and only three commits on `main` since that date lack one.

### Severity

This is a metadata and provenance leak, not a credential leak. The URL
is not a bearer token: sessions are private by default and the link
resolves only for the owner's authenticated account. The token's entropy
rules out enumeration.

Four exposures remain. Published pointers into a private corpus become
live if a session is ever shared or if an authorization bug appears.
Provenance leaks, because this machine's session store mixes
private-project and public work, which is the condition this
repository's redaction rules exist to address. Third, the 533 pointers
are not 533 independent low-value leaks: together they are a permanent,
public index mapping commit metadata to session identifiers. In any
account-compromise scenario reached by an unrelated channel, an
attacker's search cost for the highest-value private-project sessions is
already zero, because this repository published the target list. Fourth,
the PR-body surface's clean published history rests on manual review
alone — no automated control exists there (Context) — and the engineer
recalls that review catching two or three leaks within roughly the past
two weeks `[engineer-reported]`, an unaudited recollection rather than a
confirmed count.

One further instance is durable rather than a trailer: a real session
identifier appears in tracked prose on `main`, at
`.claude/plans/redact-hook-uuid-detection-gap.md` line 49.

## Approach

Set `attribution.sessionUrl` to `false` in both settings files — the stow-source `claude/.claude/settings.json` and the repo-local `.claude/settings.json` — record the reversal as a new `docs/design-decisions.md` §60 plus a scoped superseding note on §40, ship a `CHANGELOG.md` entry for the consumer-visible default change, and redact the one real session identifier in tracked prose. That setting is the mechanism the vendor ships for this trailer and it is confirmed to work, so it is the fix rather than one layer of several. No hook changes: a content-shape detector was weighed and declined, and every residual that leaves is named rather than papered over.

**§40's account of anthropics/claude-code #77830 is wrong on two counts the thread itself establishes.** The reporter set `attribution.commit: ""`, the wrong key, and the issue is closed as *completed*, with the maintainer naming `attribution.sessionUrl` as the trailer's actual control. `attribution.sessionUrl: false` short-circuits before the trailer is built. Given that correction and the two residual surfaces a content-shape detector would have covered, the decision is to ship the setting alone. `[engineer-verified]`

**Row 11 is the load-bearing unknown, and the fix is to hedge the claim, not to gate the merge.** Whether Claude Code honors `attribution` from a project-scope settings file is unverified, and it is the entire basis for saying the repo-local file protects outside contributors and cloud containers. The risk lives in the claim, not in the key: shipping `attribution.sessionUrl: false` in `.claude/settings.json` costs one line and is harmless if project scope ignores it — that population is then exactly where it is today. What would do damage is publishing "this protects non-stow clones" into a durable record while it is unverified, because a future reader stops looking. So the downgrade is unconditional and lands in every surface at once: Approach, Critical files, §60, and the CHANGELOG all say *expected to, pending verification*, with a §60 Revisit condition for the case where it turns out false. The isolation attempt in Verification is real but not a merge gate, and the reason it cannot be settled cheaply is now itself established: no CLI subcommand on 2.1.261 reports a resolved settings value, so the §49-style config probe that would have answered this in one command does not exist here. §60 must say so plainly — that is the stated reason this answer is weaker than §49's, not an unexplained asymmetry.

**A behavioral check needs a before/after pair, not a single observation.** Confirming that the verifying session's own system prompt carries the attribution instruction is necessary but not sufficient: row 8 establishes that instruction-presence is universal on this machine, including in dispatched subagent sessions, so a clean commit after the fix cannot be distinguished from a session type that never emits. The check must therefore run the same mechanism twice in one scratch worktree — commit once with the key absent and confirm the trailer **does** appear, then set it to `false` and confirm it disappears. A "before" commit with no trailer is the actionable signal that this environment is not an emitting surface, and the result must not be recorded as a pass. When the outcome is written into the PR body and §60, state the positive control's actual epistemic status: instruction-presence confirmed, genuine web or Remote Control surface not independently confirmed.

**No scripted substitute exists, and both CLI candidates have now been examined.** A `claude -p` run is not one of the two emitting surfaces, so it would pass green while proving nothing. `--remote-control` is documented in `claude --help` as starting an *interactive* session, so it is not a headless path to an emitting surface either. The recurring regression signal is therefore a post-hoc audit over public history, and it must be range-scoped to work: unscoped, `git log origin/main --format=%B | grep -c 'claude.ai/code'` returns 403 today and can only grow, because the 113 pre-existing trailers stay in history by design — a counter that starts saturated can never distinguish clean from regressed. §60 records the merge SHA and the scoped form, `git log <fix-sha>..origin/main --format=%B | grep -c 'claude.ai/code'`, with an expected post-merge baseline of 0.

**Why both settings files, and which one does which job.**

- The **stow-source file** installs to user scope on the engineer's machine, covering every repo and every session there. That is the surface that produced the 113 trailers. Coverage here is not conditional on row 11.
- The **repo-local file** ships inside the checkout, so it is the only settings source a clone made by an outside contributor or a cloud container ever sees. Neither has the stow package, so neither has any of this repo's hooks. **Expected** to protect that population, pending row 11.

**Only `sessionUrl` is set.** `attribution.commit` and `attribution.pr` stay unset for the reasons §40 already establishes — §60 cites that reasoning rather than re-deriving it. The one new fact §60 adds is that an empty `commit` value is falsy, so `attribution.commit: ""` makes the session trailer the *sole* trailer instead of suppressing it. `CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION=1`, which short-circuits ahead of the settings check, is not shipped: machine-local, uncommittable for other consumers, redundant with a setting that works, and recorded in §60 as the fallback if the setting regresses.

**Blast radius, weighed against this repo's own test — and the mismatch accepted rather than dissolved.** §39 and §40 apply a "wider than any condition this repo has" test before putting repo-specific policy at stow scope, and §40 declined `attribution.commit` there on exactly that mismatch. This change does not pass that test: the threat model is one public repo, the fix lands at user scope, and it changes behavior in every other repo every consumer works in, including private repos where an internal AI-authorship trail may be wanted. Two things distinguish it from §40's refusal, and only one of them answers the test. Setting `commit` would have imposed an invented trailer format while `sessionUrl: false` removes a link and invents nothing — a real distinction, but about the *kind* of change, not about blast radius exceeding the problem's scope. The leg that actually makes the wider default acceptable is that the removal is per-repo reversible — and that leg is entirely contingent on row 11. §60 must tie it to row 11's own Revisit condition and state what follows if row 11 resolves false: the decision then takes exactly the shape §40 rejected for `commit`, a non-reversible wider-than-the-condition default imposed on every private repo to fix a public-repo-specific problem, and the blast-radius justification itself is what needs revisiting, not only the coverage and opt-back-in claims. The decision to ship in both files is `[engineer-verified]` and is not reopened.

**No opt-back-in path is confirmed, and the plan says so rather than guessing.** The natural candidate — a per-repo `.claude/settings.json` or `.claude/settings.local.json` entry setting `attribution.sessionUrl: true` — is the same project-scope question row 11 leaves open. This repo's CHANGELOG publishes that shape for `disableWorkflows`/`disableArtifact`, which is suggestive but is exactly the cross-key analogy §43 warns against: §43 found `tui`, `theme`, and `agentPushNotifEnabled` *not* honored at a scope engineers assumed worked. So §60 and the CHANGELOG state that the only confirmed recourse today is editing the tracked stow-source file, re-clobbered on the next pull unless carried forward — the same disclosure §49's CHANGELOG entry makes — and that the project-scope override is untested. §60 is the canonical, Revisit-tracked home for this status; the CHANGELOG entry, which this repo treats as an append-only record, points at §60 rather than freezing the claim.

**The rollout window is expected, not established.** Config-adjacent state in this harness is session-start-scoped rather than hot-reloaded, so a consumer with a session already open when they `git pull` is expected to keep emitting the trailer until they start a new one. The supporting evidence (`.claude/plans/engagement-lessons-fixes.md:130`) is about skill registration — a different subsystem, which is the same cross-subsystem analogy §43 cautions against — so the rollout window carries an "expected" qualifier in all three places it appears: the paragraph above, §60, and the CHANGELOG `**Migration:**` line.

**The merge surface is closable by configuration.** This repo's own merge settings are `allow_squash: true`, `allow_merge: false`, `allow_rebase: false`, `squash_message: COMMIT_MESSAGES` — squash is the only permitted method, and GitHub concatenates each branch commit's message into the squash body. That is not a generic default: it is this repository's configured setting, and it is the precise mechanism by which all 113 trailers reached `main`. `squash_merge_commit_message` accepts `PR_BODY`, `COMMIT_MESSAGES`, or `BLANK`, so setting it to `PR_BODY` would stop commit-message bodies — and any trailer they carry — from reaching `main`'s history at all, mechanically and with no human discipline at merge time. That closes the one residual this plan otherwise declares unclosable, and it closes it independently of row 11: even if project scope is not honored and a non-stow contributor's commits carry trailers, they never land. **This is an open decision for the engineer, outside the current `[engineer-verified]` scope — see the section below.**

**Two ungated surfaces, if `PR_BODY` is not adopted.** Agent-authored session identifiers on five publishing surfaces stay ungated — the declined-detector residual, now with sharper evidence behind it: the advisory line at `claude-skills/skills/plan-it/SKILL.md:139` was added 2026-08-10 and the row-16 tracked-file instance was committed 2026-08-23, so the control existed for 13 days and still failed that instance — and it never covered the PR-body surface at all, where the engineer separately recalls catching two or three leaks by manual review (Context). And merge stays a surface no per-commit control reaches, with the only control being the human clearing the concatenated bodies from the squash-message box.

**The redaction is now the only thing removing that identifier from the working tree.** With no detector shipping, nothing flags `.claude/plans/redact-hook-uuid-detection-gap.md:49` later. Delete the parenthetical rather than placeholder-ifying it, following `.claude/plans/redact-terminal-plan-path-leak.md`.

### Open decision for the engineer: `squash_merge_commit_message`

**Recommendation: set it to `PR_BODY`, in a separate change from this one.** It converts the merge residual from "a human must remember to clear a text box" into a configuration fact, and human discipline at a merge boundary is precisely the control class §1 exists to distrust. It is one `gh api -X PATCH` call, reversible in one more. It also covers the two paths this plan cannot: an already-open PR whose commits predate the fix, and every future non-stow contributor's commits if row 11 turns out false.

The cost is real and belongs to the engineer. `main`'s commit bodies would become PR descriptions — long, markdown-heavy, frozen at whatever the body said at merge time — and the per-commit narrative that the current `COMMIT_MESSAGES` concatenation preserves would be gone from history entirely. That is a change to this repo's history format, arguably its own decision rather than a rider on an attribution fix, which is why the recommendation is to make it separately rather than fold it in here. `BLANK` is the third option and is strictly more lossy than `PR_BODY` for the same benefit, so it is not recommended. If the engineer declines, nothing in this plan changes: the merge surface stays documented in §60 with the clear-the-box control, and Verification step 8 stands.

**A second cost, surfaced by this revision's PR-body disclosure: `PR_BODY` relocates an uncaught PR-body leak, it does not only close a surface.** Today, an uncaught bare session-URL line in a PR body stays on a mutable, post-hoc-editable GitHub surface — it never reaches `main`'s squash commit, because that pulls from commit messages (row 20), not the PR body. Under `PR_BODY`, that same uncaught leak would be baked directly into `main`'s immutable git history at merge time — the exact outcome this whole plan exists to prevent, just moved to a different input. The recommendation above is sound only once `attribution.sessionUrl: false` is confirmed to suppress the PR-description injection as reliably as it suppresses the commit-trailer one — which this plan does not establish (row 9's tension, and Verification step 8's disclosed gap). Treat `PR_BODY` adoption as contingent on that confirmation, not as an independent decision to make on this plan's timeline.

### Assumption ledger

**Root problem:** the harness's session-attribution channel has published `Claude-Session: https://claude.ai/code/session_<id>` into 113 of the 116 commits merged to this public repo's `main` since 2026-08-21 (533 across all branches), and the setting that controls it is unset in both of this repo's settings files.

**Givens** — conditions this design treats as fixed because they lie beyond its reach:

- The attribution channel is vendor-owned. Anthropic decides whether a trailer is emitted, which settings key gates it, and on which session surfaces it applies; this repo can only set the documented opt-out.
- Each attribution channel ships enabled by default and gets its own opt-out key. Three trailers have reached histories with attribution turned off (#77830, #82690, #83226). Closing that class upstream is not this repo's decision.
- Claude Code sessions are private by default and a session link resolves only for the owner's authenticated account. That is vendor policy, revocable by the vendor, not a property this repo controls.

Two conditions that look like Givens are not, because this repo can change both: hook registration for non-stow clones, and the merge surface's closability via `squash_merge_commit_message`. Both sit in Out of scope with their reasons.

**Ledger rows:**

| # | Item | Tag | Anchors |
|---|------|-----|---------|
| 1 | `attribution.sessionUrl: false` suppresses the trailer via an early return before the trailer is built | `[verified: anthropics/claude-code#77830 full comment thread — Anthropic collaborator confirmation plus an independent reader of the shipped 2.1.231 bundle]` | root |
| 2 | The key has existed since v2.1.182; this machine runs 2.1.261 | `[verified: same thread]` | row1 |
| 3 | The maintainer reports the trailer fires only from a web or Remote Control session, never a plain local terminal session | `[verified: #77830 maintainer comment]`. That report's own reproduction set both `attribution.commit: ""` and `attribution.pr: ""`, so the maintainer's reply does not itself favor scoping the qualifier to one clause over the other — that reading is row 9's own inference, tagged separately below, not part of this verified quote | root |
| 4 | Two of §40's three claims about #77830 are wrong and the thread proves it: the reporter set `attribution.commit: ""`, not `sessionUrl`, and the issue is closed as *completed* | `[verified: same thread]` | root |
| 5 | §40's third claim — injection through the Bash tool description — is neither confirmed nor refuted by that thread. The reporter asserts it and no maintainer addresses it; this session's system reminder shows a system-prompt-level instruction. Deliberately absent from the §40 note | `[unverified]` | row4 |
| 6 | `attribution.commit: ""` is counterproductive — an empty value is falsy, so the session trailer becomes the sole trailer | `[verified: same thread]` | row1 |
| 7 | `CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION=1` short-circuits ahead of the settings check | `[verified: same thread]` | root |
| 8 | The attribution instruction is present in sessions on this machine, including dispatched subagent sessions that were never opened as web or Remote Control sessions | `[verified: this session's own system reminder]` | root |
| 9 | Rows 3 and 8's tension is not resolved. One hypothesis: row 3's "web or Remote Control" qualifier scopes only the PR-description clause, leaving commit trailers unconditional — consistent with the engineer's recollection that the PR-body leaks they caught coincide with increased Remote Control use. Against it: a `staff-sdet` subagent dispatched in this plan's own review round, not a web or Remote Control session, reported carrying the identical unconditional PR-description instruction in its own system prompt. See Context for the full writeup; this row does not pick between session-surface gating and inconsistent model compliance as the explanation | `[engineer-reported]` for the correlation; `[verified: staff-sdet's report of its own system prompt this round]` for the counter-evidence; the synthesis is `[unverified]` | row3 |
| 10 | The repo-local `.claude/settings.json` has no `hooks` block; every hook is registered in `claude/.claude/settings.json`. A clone without the stow package has the repo-local setting and nothing else | `[verified: both files read this session]` | root |
| 11 | Whether `attribution` is honored from a project-scope settings file is not established | `[unverified]` — load-bearing for the outside-contributor and cloud-container claim, and for the blast-radius argument's reversibility leg; hedged everywhere both appear | row10 |
| 12 | No CLI subcommand on 2.1.261 reports a resolved settings value. The full command set is `agents`, `attach`, `auth`, `auto-mode`, `doctor`, `gateway`, `import`, `install`, `logs`, `mcp`, `plugin`, `project`, `respawn`, `rm`, `setup-token`, `stop`, `ultrareview`, `update`; `claude doctor` reads settings files but never surfaces a key's resolved value | `[verified: claude --help and claude doctor on 2.1.261]` — this is why row 11 cannot be settled the cheap way §49 settled its own scope question | row11 |
| 13 | Whether a settings change applies mid-session or only at next session start is not established. The nearest evidence (`.claude/plans/engagement-lessons-fixes.md:130`) is about skill registration, a different subsystem | `[unverified]` | row1 |
| 14 | No opt-back-in path is confirmed. The project-scope candidate is the same open question as row 11; the only confirmed recourse is editing the tracked stow-source file | `[unverified]` | row11 |
| 15 | `--remote-control` starts an *interactive* session, so it is not a headless route to an emitting surface | `[verified: claude --help]` — with row 12 and the `-p` reasoning, this closes the scripted-check question | row9 |
| 16 | Exactly one tracked file contains a session identifier: `.claude/plans/redact-hook-uuid-detection-gap.md:49` | `[verified: git grep -nE 'session_[A-Za-z0-9]{20,}']` | root |
| 17 | No existing mechanical gate catches a session identifier in committed content; none of the six structural detectors matches the shape | `[verified: all six regexes read at claude/.claude/hooks/_lib.sh:2028-2094]` | root |
| 18 | The advisory line at `claude-skills/skills/plan-it/SKILL.md:139` predates the row-16 leak instance by 13 days — the control existed and still failed | `[verified: added in 0561f77d 2026-08-10; leak committed in 0e1e2647 2026-08-23]` | row16 |
| 19 | The redaction edit cannot self-block: the commit gate scans only added diff lines | `[verified: deny-private-project-refs.sh:518-525]` | root |
| 20 | Squash is the only merge method this repo permits, and its body is the concatenation of the branch's commit messages — the precise mechanism by which all 113 trailers reached `main` | `[verified: gh api repos/:owner/:repo → allow_squash:true, allow_merge:false, allow_rebase:false, squash_message:"COMMIT_MESSAGES", squash_title:"COMMIT_OR_PR_TITLE"]` | root |
| 21 | `squash_merge_commit_message` accepts `PR_BODY`, `COMMIT_MESSAGES`, or `BLANK`. `PR_BODY` would stop commit-message bodies reaching `main` entirely, closing the merge surface by configuration rather than by hook or human discipline | `[verified: same repository-settings surface]` — the basis for the open decision above | row20 |
| 22 | The unscoped audit `git log origin/main --format=%B` piped to `grep -c 'claude.ai/code'` returns 403 today and can only grow, so it can never distinguish clean from regressed; a range-scoped form is required | `[verified: git log origin/main --format=%B | grep -c 'claude.ai/code', returns 403 as of 2026-09-05]` | root |
| 23 | `attribution` is not in `guard-settings-session-keys.sh`'s guarded key set — correctly, since it is shared policy rather than machine-local state | `[verified: guard-settings-session-keys.sh:87-96]` | root |
| 24 | `ask-review-permissions.sh` fires the same static `ask` on **every** `Edit`/`Write`/`MultiEdit` to any `.claude/settings*.json` path. It has no logic detecting whether `permissions.allow` was touched — its own header says that heuristic is too fuzzy — so the relevance judgment is the human's. No `/review-permissions` run is required for this change | `[verified: claude/.claude/hooks/ask-review-permissions.sh, full file read]` | root |
| 25 | No CI schema, key-ordering, or top-level-key-allowlist gate exists for either settings file | `[verified: no schema/allowlist reference to either settings file in .github/workflows/ or any pre-commit config]` | root |
| 26 | `~/.claude/settings.json` on a stow machine *is* this repo's tracked `claude/.claude/settings.json` via a plain file symlink, so there is no consumer-owned copy to clobber | `[verified: install.sh's stow -t call at line 348]` | root |
| 27 | The 533 published pointers are a permanent public index mapping commit metadata to session identifiers, so in any account-compromise scenario the search cost for high-value sessions is already zero. Two owner-side levers remain: auditing whether any published session was ever explicitly shared, and purging transcript content behind published IDs carrying private-project material | `[derived: aggregation-risk reasoning applied to the published-pointer count, which is itself verified]` — the mechanism for performing either is not established | root |
| 28 | This repo's convention is a `CHANGELOG.md` entry with a `**Migration:**` line for a stow-source settings change; §31, §39, §47, and §49 all shipped one, and nothing enforces it mechanically. `select-tests.py` maps a CHANGELOG-only change to an empty test tuple, confirming the convention carries no test coverage | `[verified: CHANGELOG.md read this session; select-tests.py:358]` | root |
| 29 | The 113/116 and 533 figures derive from this repo's own public git history, so publishing them carries no private-corpus provenance | `[verified: CLAUDE.md's carve-out]` | root |
| 30 | §41/§49 is the precedent for a reversal, §33 for correcting a prior entry's misattribution in a new entry, and §60 is the next free number — a manual check, since nothing enforces `## N.` monotonicity. This number drifted three times before this plan reached commit: during authoring, when `main` advanced from 53 headings to 54; during this session's re-review round, when another merged PR claimed §55; and again during the pre-`ready-for-review` sync, when two more merged PRs claimed §56 and §57 in the same rebase. All three drifts are the concrete case the Out-of-scope monotonicity-test bullet argues from, and the third is a sharper instance: it landed during the gap between this plan's own commit and its merge, a window no manual re-check inside this session can close | `[verified: 57 headings accounted for through §57 (`_lib_sanitize_for_terminal`'s bidi/zero-width collapse, merged 2026-09-05), §60 next free, re-derivable via grep -n '## [0-9]\+\.' docs/design-decisions.md]` | root |
| 31 | `.claude/plans/redact-terminal-plan-path-leak.md` is the precedent for diff-only redaction of a merged plan file with history left alone | `[verified: read this session]` | root |
| 32 | Reverse the prior decision and record it; set the key in both settings files knowing the stow-source file reaches every consumer's every repo; ship the setting alone with no content-shape detector; redact line 49 in this PR; no history rewrite | `[engineer-verified]` | root |

**Mechanisms, and the lighter primitives weighed against each:**

- **`attribution.sessionUrl: false` in `claude/.claude/settings.json`** — `anchors: root`, via rows 1, 3, 8. Nothing lighter reaches the harness: no hook, prose rule, or repo convention can stop the trailer being built.
- **`attribution.sessionUrl: false` in `.claude/settings.json`** — `anchors: row10`, conditional on row 11. Two narrower alternatives weighed. *Ship only the stow-source entry:* rejected — it reaches no one who has not run `install.sh`, and with no hook shipping, a non-stow clone would have nothing. *Ship only the repo-local entry,* which would avoid the whole-machine blast radius entirely: rejected because it would leave the population that produced 113 of the 116 trailers uncovered in every repo except this one, and because it is the weaker of the two on evidence — the stow-source entry's effect is not contingent on row 11 while this one's is.
- **`docs/design-decisions.md` §60 plus a scoped note on §40** — `anchors: row32` and `row4`. Lighter alternative weighed: edit §40's `sessionUrl` paragraph in place. Rejected under Axis 3 and against the §41/§49 precedent in row 30.
- **`CHANGELOG.md` entry** — `anchors: row28`. Lighter alternative weighed: let §60 carry it alone. Rejected — §60 is the contributor-facing design record; the CHANGELOG is what a consumer reads after `git pull`, and rows 13 and 14 are exactly what a `**Migration:**` line exists to communicate.
- **Deleting the parenthetical at `redact-hook-uuid-detection-gap.md:49`** — `anchors: row16`. Lighter alternative weighed: a `session_<id>` placeholder. Rejected as the larger diff for no gain, per row 31.

## Critical files

**One `code-writer` dispatch.** Six files, one shared rationale — §60 and the CHANGELOG entry both describe the settings choice, the hedged row-11 claim, and the declined detector together, so any split would restate the whole design in both prompts. That is the case the plan grammar names as do-not-split.

- **`claude/.claude/settings.json`** — add top-level `"attribution": { "sessionUrl": false }`, placed directly after the `disableWorkflows` entry so the diff is deterministic (no lint or CI gate constrains placement — row 25). Set nothing else inside `attribution`. Expect an unconditional permission prompt from `ask-review-permissions.sh`; no `/review-permissions` run is required (row 24).
- **`.claude/settings.json`** — the same top-level `attribution` object. Describe it as *expected* to cover non-stow clones and cloud containers, pending row 11 — never as settled.
- **`claude/.claude/hooks/tests/test_hook_alignment.py`** — add two declared-config pins next to `test_schedulewakeup_stays_denied_in_settings` (line 307), asserting `attribution.sessionUrl is False` in each settings file. **Reuse:** that test's docstring convention — state that the pin proves the *declared* config value, not the harness behavior behind it. `_SETTINGS_PATH` (line 151) resolves to the stow-source file only, so the repo-local pin needs its own module-level constant; name the two after the sibling pair in `test_claude_md_excludes.py` (`SETTINGS_PATH` at line 32 for the repo root, `STOW_SOURCE_SETTINGS_PATH` at line 33), and add a one-line comment saying `_SETTINGS_PATH` is left unrenamed only because it is referenced throughout this module. `select-tests.py` maps `CLAUDE_SETTINGS_JSON` (line 126, rule at 469) to `HOOKS_TESTS_DIR` plus `SKILLS_TESTS_DIR` and `SCRIPTS_TESTS_DIR`, and `ROOT_SETTINGS_JSON` (line 187, rule at 474) to `HOOKS_TESTS_DIR` — both select the directory these pins live in.
- **`docs/design-decisions.md`** — two edits. **Re-check that `## 58.` is still free immediately before committing** (row 30) — this number has already drifted twice (row 30), so treat the re-check as load-bearing, not a formality. Insert the superseding note immediately under the `## 40.` heading at line 593, mirroring §41's placement at line 683. Draft:

  > **Partially superseded by §60 (2026-09-05):** the `sessionUrl` paragraph below is reversed — `attribution.sessionUrl` now ships as `false` in both settings files. This entry's account of anthropics/claude-code #77830 is also wrong. That report set `attribution.commit: ""`, not `sessionUrl`. The issue is closed as completed, with the maintainer naming `attribution.sessionUrl` as the trailer's actual control. The same maintainer addresses a question this entry's body leaves open: the trailer is reported to fire only from a web or Remote Control session, not a plain local terminal one. This repo's own history fits that account only partially — see §60 for the full account, including counter-evidence from a dispatched review subagent that this scoping does not fully explain. Everything else here stands, including the `attribution.commit`/`attribution.pr` decisions and the `respond-pr` reasoning.

  Do not extend the note to §40's claim about the injection mechanism: the thread neither supports nor refutes it (row 5). Then append §60, following §49's structure. It must carry:
  - The decision, both settings scopes, and which population each reaches — with the repo-local file's coverage stated as expected-pending-verification.
  - The corrected #77830 account, and why it matters: it was the stated basis for the conclusion being reversed.
  - Why `commit` and `pr` stay unset — **cite §40's reasoning by section pointer ("as established in §40, unchanged here") rather than re-deriving it**, and add only the new falsy-empty-string fact.
  - Why `CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION` is documented but not shipped.
  - **The blast-radius test, run and its mismatch accepted.** State that `sessionUrl` fails §39/§40's "wider than any condition this repo has" test on the same terms `commit` did; that the invents-versus-removes distinction answers a different question than the test asks; that the reversibility leg is what makes the wider default acceptable and is contingent on row 11; and that **if project scope is not honored, this decision matches the shape §40 rejected for `commit` and the blast-radius justification itself is what needs revisiting** — not only the coverage and opt-back-in claims.
  - **Why row 11 is answered less rigorously than §49 answered its own scope question:** no CLI subcommand on 2.1.261 reports a resolved settings value (row 12), so the cheap probe §49 ran three times does not exist here, and the only route is a live emitting session.
  - **Row 9, named as an unresolved tension rather than a resolution.** One hypothesis: the maintainer's "web or Remote Control" qualifier scopes only the PR-description clause, not the commit-trailer clause — commit trailers stay unconditional (113/116, every session type). That hypothesis fits the engineer's recollection that the manually-caught PR-body leaks coincide with increased Remote Control use. Against it: a dispatched, non-web, non-Remote-Control subagent in this plan's own review round (`staff-sdet`) reported carrying the identical unconditional PR-description instruction. Record both the correlation and the counter-evidence as unresolved, so a future maintainer does not treat this as more rigorously established than it is.
  - **The rollout window, qualified as expected** rather than established, naming that the supporting evidence is about a different subsystem.
  - **No confirmed opt-back-in path**, as the canonical, Revisit-tracked home for that status.
  - **A content-shape detector was considered and rejected — twice.** Name what the repo-wide version would have covered, and name the residual with row 18's evidence: the advisory control existed 13 days before the row-16 tracked-file instance and did not prevent it — and separately name that it never covered the PR-body surface, where the engineer recalls two or three manually-caught leaks (Context). Name the second, narrower option too: extending `pr-description`'s own `## Checks` step to flag an extra bare session-URL line. Declined on the same reasoning — the settings fix is expected to close the PR-description injection at its source, and the disclosed leaks predate that fix.
  - **Merge as a second surface**, with row 20's verified configuration as the delivery mechanism for all 113 trailers, and `squash_merge_commit_message: PR_BODY` named as an available configuration-level closure that this PR does not take.
  - **The 533 published pointers as a permanent public index**, and the two owner-side levers short of the declined history rewrite. For each lever, either name a concrete next step and who owns it, or state explicitly that the residual is accepted as indefinite — a bare "mechanism not established" is not acceptable here, since a future reader would read it as an oversight rather than a decision.
  - **Revisit** if project-scope `attribution` turns out not to be honored — which reopens the coverage claim, the opt-back-in status, *and* the blast-radius justification; if a second agent-authored session identifier reaches a commit; if a fourth attribution channel ships enabled by default; if `attribution` gains a review-comment property; if the range-scoped audit ever returns non-zero; **or if a PR body is ever found to carry a session URL after this fix ships** — the range-scoped `git log` audit cannot detect that (it only sees commit-message content, never PR-description text, per row 20), so this surface has no automated regression signal and depends on continued manual review until row 9's tension is independently resolved. **Record the fix's merge SHA in the entry** so that command is runnable; the unscoped form returns 403 today and can never signal (row 22).
  - Sources: the #77830 thread, the Claude Code settings reference, the SchemaStore schema, §40, §33, §41/§49, §43, both settings files, `claude-skills/skills/pr-description/SKILL.md`, and this plan.
- **`CHANGELOG.md`** — one bullet under `## [Unreleased]` → `### Changed`, following the `ScheduleWakeup`-deny entry's shape. It must state what changed; that the stow-source half **changes behavior in every other repo you work in on this machine, not only `claude-config`**; that the repo-local half's coverage of non-stow clones is expected but untested; that `pr-description`'s own attribution-trailer check (also stow-shipped, so this applies to every consumer) validates only its own trailer text and does not screen for an additional bare session-URL line the harness can still append — so a consumer who has used Remote Control sessions should audit their own recent PR bodies rather than assume this fix alone closes that surface; and a `**Migration:**` line covering (a) live on `git pull` with no re-install, **expected** to take effect from the next `claude` launch rather than a running session, and (b) that no opt-back-in path is confirmed — the project-scope override is untested and editing `claude/.claude/settings.json` directly is the only confirmed recourse, re-clobbered on the next pull. Close the Migration line with **"for the current status of the opt-back-in question, see `docs/design-decisions.md` §60"**: the CHANGELOG is append-only under Axis 3, §60 carries the Revisit mechanism, so the frozen entry must defer to the updatable one rather than assert a status that will go stale.
- **`.claude/plans/redact-hook-uuid-detection-gap.md`** — line 49 only: drop the parenthetical containing the real session identifier, keeping the sentence's meaning ("the authoring session, identified from the commit trailers"). No other line changes, and no placeholder in its place.

**This plan file, the commit message, and the PR body must never contain a real session identifier** — placeholder shapes only (`session_<id>`).

## Verification

1. **The setting suppresses the trailer — a before/after pair, not a single observation.** In one scratch worktree, first confirm the verifying session's own system prompt carries the attribution instruction; a missing instruction is a hard stop, not a soft pass. Then commit once with `attribution.sessionUrl` absent and confirm the trailer **does** appear. Only then set it to `false` and confirm it disappears. If the "before" commit carries no trailer, this environment is not an emitting surface — record that outcome and do not record a pass. When writing the result into the PR body, state the control's actual epistemic status: instruction-presence confirmed, genuine web or Remote Control surface not independently confirmed. If the check cannot be run before merge, the PR body must say so plainly and name why.
2. **Row 11 isolation attempt — best-effort, not a merge gate.** Repeat step 1's before/after pair in a session whose only settings source is a scratch clone's `.claude/settings.json`, with no user-scope `attribution` key present (for example, `CLAUDE_CONFIG_DIR` pointed at an empty temporary directory). A pass attributes suppression to project scope and simultaneously answers the opt-back-in question in row 14. Do not spend time looking for a CLI config-inspection shortcut — row 12 settles that none exists on 2.1.261. Record the outcome, including "could not be arranged," in the PR body and §60; do not upgrade any hedged language without a pass.
3. `.venv/bin/python3 claude/.claude/scripts/select-tests.py` — the repo's documented scoped test command. Both settings files map to `claude/.claude/hooks/tests`, where the new pins live.
4. `.venv/bin/ruff check claude/.claude/ claude-skills/` for the `test_hook_alignment.py` edit. No ShellCheck run is needed — this change touches no shell script.
5. **Negative check on both settings pins, with a mandatory restore proof.** Flip `attribution.sessionUrl` to `true` in each file in turn and confirm the corresponding pin fails with its intended message. Then restore, **re-run step 3 immediately**, and assert both files contain `"sessionUrl": false` before staging anything. A forgotten restore ships the exact inverse of this PR's purpose into a file every consumer receives; `/code-review` reading the diff is not a mechanism.
6. **The implementing session's own commit must not carry the trailer.** That session's system prompt was built before the setting landed, and nothing will block the trailer if it goes in.
7. **Repo-wide sweep.** `git grep -nE 'session_[A-Za-z0-9]{20,}'` returns nothing. It returns exactly one hit today, at `.claude/plans/redact-hook-uuid-detection-gap.md:49`.
8. **This PR's own output is clean, and its merge is too.** `git log -1 --format=%B | grep -c 'claude.ai/code'` returns 0, and the same check against the PR body before posting. Because this repo squash-merges with `squash_message: COMMIT_MESSAGES` (row 20), clear the concatenated commit bodies from the squash-merge message box — the only control on that surface unless the engineer adopts `PR_BODY`. **This step is a content spot-check on one PR body, not a mechanism test:** it cannot distinguish "the setting suppressed the PR-description instruction" from "this author simply didn't include the line," which is exactly the manual-review process that has already missed two or three times (Context, row 9). No step in this plan runs the before/after pair from step 1 against the PR-description surface. Record this gap explicitly in §60 rather than letting step 8's pass imply PR-body suppression is verified.
9. **Record the merge baseline.** After merge, capture the fix's SHA into §60 and run the range-scoped audit `git log <fix-sha>..origin/main --format=%B | grep -c 'claude.ai/code'`, expecting 0. That baseline is what makes §60's Revisit condition able to fire; the unscoped form returns 403 and never can. **This audit is structurally blind to the PR-body surface**: this repo's squash body is the concatenation of commit messages (row 20), never PR-description text, so a regression that reintroduces PR-body leaks specifically would pass this check silently, working fix or not. Name this in §60's Revisit list rather than letting the audit imply full coverage.
10. **`## 58.` is still the free number** at commit time (row 30 — a manual re-check; this number has already drifted twice before implementation began).
11. `/code-review` on the diff before commit, per repo convention.

## Out of scope

- **A mechanical gate on session identifiers in committed content.** Declined by the engineer in favour of the setting alone. `[engineer-verified]` — reaffirmed after this revision's PR-body disclosure (Context). A narrower alternative was also weighed and declined for the same reason: extending `pr-description`'s own `## Checks` step to flag an extra bare `claude.ai/code/session_` URL line, distinct from and much lighter than this repo-wide detector. Declined because `attribution.sessionUrl: false` is expected to suppress the harness's PR-description injection at its source (per the schema text quoted in Context), which is the same mechanism already fixing the commit-trailer surface; the PR-body leaks this revision discloses predate that fix. The residual: a session identifier written by an agent into a commit message, PR body, issue comment, `gh api` payload, or plan file stays ungated, on every surface, in every repo. The only remaining control is the advisory line at `claude-skills/skills/plan-it/SKILL.md:139`, which predates the row-16 tracked-file instance by 13 days and did not prevent it (row 18) — §1's point about the advisory tier, with local evidence. It never covered the PR-body surface at all; the engineer recalls catching two or three PR-body leaks there by manual review, unrelated to this advisory line.
- **Changing `squash_merge_commit_message` to `PR_BODY`.** In this repo's reach and the only mechanism that closes the merge surface without human discipline — recommended, but as its own change: it rewrites this repo's history format, which is a decision on its own terms rather than a rider on an attribution fix. Surfaced above as an open decision.
- **Rewriting git history to purge the 533 published trailers.** `[engineer-verified]`
- **The two owner-side mitigations for the already-published corpus** — auditing whether any published session was ever explicitly shared, and purging transcript content behind published IDs carrying private-project material. Both are actions on the owner's Claude account, not repository changes, so neither belongs in this diff; both are named in §60. The mechanism for performing either is unverified.
- **Arming the hook suite for non-stow clones**, by registering hooks in the repo-local `.claude/settings.json` with repo-relative paths. This repo *could* do it — a deliberate declination, not a Given — but it touches every hook and the install story.
- **Editing `.claude/plans/attribution-setting-design-decision.md`.** A committed plan record under Axis 3; its row 9 is answered by ledger row 3 and superseded by reference from §60.
- **Rewriting §40's body or its Sources line in place.** Axis 3; the correction rides in the superseding note and §60, per the §33 precedent.
- **Setting `attribution.commit` or `attribution.pr`.** §40's reasoning stands, and an empty `commit` would make the session trailer the sole trailer.
- **Shipping `CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION`.** Machine-local, uncommittable for other consumers, redundant with a setting that works.
- **A test enforcing `docs/design-decisions.md` heading uniqueness and monotonicity.** A real pre-existing gap this plan's §60 claim leans on; a regex over `## \d+\.` asserting the sequence is unique and sorted would close it cheaply. Unrelated to attribution, so it belongs in its own change; Verification step 10 is the manual stand-in.
- **A recurring scripted behavioral check on the trailer.** Not available: `-p` is not an emitting surface and `--remote-control` is interactive-only (row 15), so both CLI candidates are ruled out. The range-scoped `git log` audit in Verification step 9 is the substitute.
- **Test coverage for the CHANGELOG entry.** `select-tests.py` maps a CHANGELOG-only change to an empty test tuple, and a regex over changelog prose would assert that text exists rather than that its claims are true. Consistent with the four precedent entries, none of which is mechanically enforced.
- **Changing `pr-description` or `respond-pr` trailer text.** Unaffected by this change, and not a fix for the residual either: `pr-description`'s check validates its own trailer's presence and uniqueness, not the absence of the harness's separate bare-URL line, so it has not prevented every PR-body leak — the engineer caught two or three by manual review (Context; row 9). That gap is the same declined-detector residual named above, not a new one.
- **`docs/hooks.md` line 24 and README.md lines 430-432**, which describe the always-on redaction scan as tracker-ID-only. Pre-existing drift, unrelated to this change.
