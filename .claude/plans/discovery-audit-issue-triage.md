# Discovery-audit issue triage — 2026-08-22 findings

## Context

File GitHub issues for the 2026-08-22 discovery audit's confirmed-untracked findings and mark the dormant remediation plan superseded, so audit coverage is actually trackable instead of resting on a plan nobody is executing.

The 2026-08-22 discovery audit (`docs/reports/2026-08-22-discovery-audit/findings.md`, 85 findings) produced a remediation plan (`.claude/plans/discovery-audit-remediation-plan.md`, 10 phases, merged via closed issue #742) that is not being actively executed — only two of its dispatches ever landed as its own work. A full classification against current repo and GitHub state, followed by an independent architectural re-verification this session, found: 17 findings fixed, 10 closed without a fix (7 explicitly out of scope/N/A, plus 3 more confirmed no-fix-needed), 1 tracked only by a pre-existing issue (C16, via #537), and 57 findings with no tracking beyond a dormant plan phase — the confirmed gap set this plan turns into new GitHub issues, following the structural precedent of `.claude/plans/repo-quality-audit-issue-triage.md` (the 2026-08-10 audit's triage, merged PR #1144: filed issues #1137 through #1142, rewrote/closed #619, added a `## Status` section to that audit's own findings.md). This plan's job is triage and tracking only — filing issues and marking the dormant plan superseded — not remediation. No code fixes are in scope.

## Approach

This plan files **19 GitHub issues**, each covering one root cause. Together they cover the 57 findings from the 2026-08-22 discovery audit that are still open, plus two detection gaps the dormant remediation plan found on its own. It also adds a dated `## Status` section to `docs/reports/2026-08-22-discovery-audit/findings.md`, which becomes the authoritative per-finding index. Finally, it puts a dated "Superseded in part" note at the top of `.claude/plans/discovery-audit-remediation-plan.md`. No finding gets fixed here. The deliverable is tracker state plus those two doc edits.

**Corrections to the classification this plan inherits.** This session re-read the sources behind four rows of the 85-row gap table:

- **C3 and C12 were moved, not fixed.** The gap table marked both Fixed because their text is gone from `docs/design-decisions.md`. The text actually moved into the `docs/design-decisions/` directory with the defect still in it:
  - `docs/design-decisions/check-runner-charter-scoping.md:19` still says `check-runner-bash-guard.sh` "is kept as a reference implementation". `docs/case-studies/check-runner.md:88` records that file as deleted.
  - `docs/design-decisions/specialist-reviewer-roster.md:9` still cites `code-review/SKILL.md:328, 347, 351, …` for `ciso-reviewer`'s co-owned rows. `ciso-reviewer` now appears at `claude-skills/skills/code-review/SKILL.md:264-337` and `:439-477`, and none of the cited lines is among them.
  - Both findings move to issue 11.
- **S11 and S20 were fixed as a side effect of other work.** They belong under Fixed, not under closed-without-a-fix.
  - S11: root `CLAUDE.md:107-112` now defers to `docs/hooks.md`'s entry for `block-gh-pr-merge.sh`. That hook's tier-table row (`docs/hooks.md:55`) discloses the `gh api .../pulls/N/merge` gap.
  - S20: the Provenance subsection now ends with "If in doubt, don't." (`CLAUDE.md:173-174`).
- **SC6 is closed by work that already landed, so there is nothing to fold into a live plan.** `.claude/plans/claude-md-audience-restructure.md` shipped the growth ratchet SC6 said was missing: `check-claude-md-length.sh:44` sets a 25,600-byte limit, and `:122` applies it to `claude/.claude/CLAUDE.md`. That plan also recorded the offload path it declined, in `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md`. SC6 is therefore closed without a fix, citing both.
- **The `cooperative` tier does not waive `bash -c`/`eval` wrapping.** `docs/hooks.md:55` records this repo's own ruling for `block-gh-pr-merge.sh`: "the `bash -c`/`eval` wrapper shapes are plausible cooperative mistakes and stay live findings under the gate's plain-`cooperative` component." `git -C <path> commit` stays the lead defect in issue 1, because agents in this worktree-heavy repo write that shape routinely. The wrapper shapes stay in scope as secondary cases.

Net result:

- 17 Fixed: the gap table's 17, minus C3 and C12, plus S11 and S20.
- 10 closed without a fix: the 7 N/A findings, plus I5, C25, and SC6.
- 1 tracked only by a pre-existing issue: C16, by #537.
- 57 tracked by the new issues.

17 + 10 + 1 + 57 = 85. S2 is the one finding split across two issues: #1137 covers four of its lines, and issue 2 covers the rest.

**Why the issues follow root causes, not the dormant plan's phases.** The 2026-08-10 precedent filed one issue per backlog phase, because that report's phases were independent PRs. The dormant plan's 10 phases were drafted the same way, but they no longer have that property:

- Phase 1 has 8 of its 15 findings fixed, 5 of them through work outside the plan (#820, #1064, and two fixes with no traced commit). Its stow-tree regex swap was overtaken by #814.
- Phase 7 lost 4 of its 17 findings to fixes made by other work.
- Phases 3–5 now straddle `transcript-analysis.py` and the `transcript_analysis/` package that #1116 is extracting code into.

So each issue follows one of the report's root causes, and the boundaries fall where a change needs its own review:

- **Plugin tree vs. stow tree.** Plugin hooks carry their own trimmed `_lib.sh` copies, and each plugin's version bump is hook-enforced. `.claude/plans/merge-aware-review-gates.md` already splits its own work this way: its Phase 3 is "stow tree only" and its Phase 4 is "the plugin arm".
- **Review type.** A `SKILL.md` edit triggers hook-enforced `/skill-review`, and an agent-file edit triggers `/agent-review` (`.claude/rules/review-pipeline-dispatch.md`). Bundling those with unrelated edits forces one behavioral-equivalence review over unrelated changes.
- **One plugin per issue where possible,** so each issue pays for one version bump. Issue 13 groups all three `lovable-cloud` findings for this reason.

Each issue body cites the dormant plan's phase by heading as reference design. Its per-finding fix specs are the most valuable thing it holds.

**The 19 issues.** They are listed in filing order. Each body opens with "Covers: 2026-08-22 discovery audit, findings …; baseline `6291b343`; reference design: `.claude/plans/discovery-audit-remediation-plan.md`, Phase N" and gives a priority line. Bodies cite the report by section heading and do not restate finding text.

Priority rules:

- **High:** the issue carries one of the report's High findings (S2, S4–S7, S9, S10, S12, C1). Issue 1 is also High: S14 is only Medium in the report, but the verified `git -C` miss affects two hook-enforced review-pipeline gates on a routine command shape.
- **Medium:** the issue carries a Medium finding. Issue 8 is the one exception, explained in its row.
- **Low:** everything else.

| # | Pri | Draft title | Covers | Notes for the body |
|---|---|---|---|---|
| 1 | High | `Commit gates miss git -C <path> commit: drop the Bash(git commit *) prefilter and move the plugin gates onto the shared matcher` | S14, SC1, D8 | Lead defect: `git -C <path> commit` gets past all three plugin gates (`require-skill-review.sh`, `require-plugin-version-bump.sh`, `require-npm-version-bump.sh`). It passes both their `hooks.json:10` `if` prefilter and their in-body `grep -qE` regex. The four stow-tree gates already detect it in-body via `_lib_command_invokes_git_subcmd` (#814). They still carry the same `if` at `claude/.claude/settings.json:240,246,291,297`. Include them unless the pre-filing check finds an open issue that already tracks that half. Prescribe the no-`if`, self-filtering shape that `require-ready-for-review.sh` adopted (`.claude/plans/pr-771-deferred-findings.md`, Approach, Finding 1). `.claude/plans/merge-aware-review-gates.md` Phases 3–4 already specify the same shape. That plan says to leave the plugin-semver/npm-semver `if` alone, but its reasoning covers `--continue` only, not `-C`. `bash -c`/`eval` wrappers stay in scope, since `docs/hooks.md`'s `block-gh-pr-merge.sh` tier row rules them plausible cooperative mistakes. The plugin libs have no git helpers, so duplicate the shared matcher into them, with a parity test in the pattern of merge-aware Phase 4. Tests for each converted gate: a `git -C . commit` case and a `bash -c` case. Also add D8's wrapper tests for `require-code-review.sh` and `guard-settings-session-keys.sh`. Needs three plugin version bumps and a `CHANGELOG.md` entry, because gate dispatch changes for every stow consumer. |
| 2 | High | `Route the remaining unguarded hook external calls through _lib_capped` | S2 (every site except #1137's four), D3, C19, S23 | Re-derive the list of bare calls by grep at filing time. Exclude the four lines #1137 covers (`advance-past-commit-stall.sh:184`, `require-stow-reminder.sh:113`, `deny-private-project-refs.sh:370`, `require-worktree-for-file-writes.sh:131`), and cross-reference #1137. Hard constraint, from Phase 1b's bullet on content-bearing sites: wrapping a content read inside `if [ -z "$(…)" ]` makes a timeout read as "nothing staged", so the gate fails open. Capture the exit status and deny on 124 before the emptiness check. `_lib_jq`-only sites are exempt, for the reason that bullet gives. Plugin arm: `require-skill-review.sh`'s bare git calls, S23's bare `jq -r` in `require-plugin-version-bump.sh`, and a `gtimeout` probe in each plugin `_lib_jq` copy. This arm shares files with issue 1, so whichever lands second rebases. Also carries Phase 1b's `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` stripping at `_lib_capped_for`. That item came from the plan, not the report. First confirm that no caller relies on an inherited `GIT_DIR`. Add one fake-git-sleep timeout test per newly wrapped git site (D3). The new fail-closed denials need a `CHANGELOG.md` entry and a Known-gaps line in `docs/security-hardening.md`, as Phase 1b specifies. |
| 3 | High | `transcript-analysis: redact by default across every multi-root subcommand, with one shared refusal helper` | S4, S5, S6, S7, S15, I1, SC5 | Name the open design choice without settling it. S4–S7 can either get an opt-in `--redact` flag (Phase 3's text) or redact by default with a `--no-redact` opt-out (the sibling convention S15 itself cites). Redacting by default would route S4–S7 through SC5's helper as well. SC5's helper returns the resolved `redact` value; Phase 3's SC5 bullet explains why. S15 removes `--redact`, a breaking flag change that needs a `CHANGELOG.md` Migration note. Phase 3's S15 bullet lists the SKILL.md, docs, and test-helper sites. Carry Phase 3's out-of-scope note on S5's limit on message-text redaction. Cross-reference #1116, but do not wait on it. Re-resolve every citation by symbol, and if #1116 has already moved a subcommand into `transcript_analysis/`, edit it there. May land as more than one PR. The SKILL.md edits need hook-enforced `/skill-review`. |
| 4 | High | `subagent-mix: dedupe multi-record turns before pricing` | C1 | This is a real dollar overcount, not a consistency nit. Phase 5 explains how both the ascending output tokens and the repeated cache tokens get counted more than once. Reuse the existing way `_dedup_turns_by_request_id` is called. Add a fixture with shared requestIds to `TestSubagentMixDollars`. Cross-reference #1116, and re-resolve `_dispatch_usage_summary` by symbol. |
| 5 | High | `SECURITY.md and security-hardening.md: make scope and enforcement claims match the shipped guards` | S8, S9, S10, S18, S24 | S9 is the High finding: SECURITY.md states the always-on tracker-ID regex and the opt-in blocklist as one claim. For S8, have SECURITY.md defer to `docs/security-hardening.md` as the authoritative guard list instead of enumerating the guards a second time. State S24's reviewer-mutation residual wherever that list names the guard. For S10, re-derive the list of package managers from `claude/.claude/settings.json` at filing time. For S18, state that the guard is opt-in and off by default. |
| 6 | High | `Route permissions.deny, defaultMode, and skillOverrides changes to /review-permissions; trim review-permissions/SKILL.md under its 200-line cap` | S12, C4, D5, SC7 | Quote the engineer's decision: "Yes review-permissions shouldn't be so lengthy anyways. I'm surprised that's the one that needs to be trimmed." Trim the file; do not add a `limit_for()` exception. This replaces the dormant plan's Phase 6 decision A5. The file is at 197 of 200 lines, so size the trim by what S12, C4, and D5 add. S12 edits `code-review/SKILL.md` and `plan-review/SKILL.md`, both of which already have 500-line exceptions. C4 edits `review-permissions/SKILL.md`'s Step 1, and D5 edits its frontmatter. All three files need hook-enforced `/skill-review`. No `CHANGELOG.md` entry: the only one Phase 6 planned was for the cap relaxation, which is dropped. |
| 7 | Medium | `PR-cost ledger hygiene: 0600 on every write, GIT_* stripping in ledger git calls, one ledger lock` | S16, S17, SC4 | S16 and S17 now live in `transcript_analysis/pr_cost_ledger.py`, `gh_cli.py`, and `pr_cost.py` (moved by #1136). SC4's cost-ledger half is still in `transcript-analysis.py`, so cross-reference #1116. S16's fix only affects future writes, so pair it with a one-time `chmod 600` note in `docs/pr-cost.md`, as Phase 4 specifies. Keep Phase 4's escape clause: if the write-file extraction doesn't come out clean, leave the duplication. |
| 8 | Medium | `deny-pii-in-commits: document the bare-& and inline-alias detection gaps and pin every alias/splitter gap with a test` | D10, plus the two gaps from Phase 1b | Quote the engineer's decision: "Record as known gaps, yes. My prior decision when the plan was made predated the hook tiers." Current code: `_lib_split_fragments` (`_lib.sh:1440-1444`) never splits on a bare `&`. `_lib_git_argv_from_subcmd` (`_lib.sh:1346`) consumes the values of `-c` and `--config-env`, so `git -c alias.ci=commit ci` yields `ci`. Add both gaps to the Known-gaps list beside the persistent-alias bullet (`deny-pii-in-commits.sh:202-203`, which says "both hooks allow it"). Reuse `require-ready-for-review.sh:68-75`'s wording for the bare-`&` gap. Add pinning tests for D10's persistent alias plus the `&`, `-c alias.`, `--config-env`, and `GIT_CONFIG_KEY_*` forms, following `test_reviewer_quoted_command_name_bypass_allowed`'s pattern. A future fix flips these tests instead of deleting them. Also check every other gate that uses the shared matcher and keeps a Known-gaps list. Medium even though D10 is Low: the list belongs to a `cooperative, irreversible` gate, and it misses two shapes the remediation plan already publishes. |
| 9 | Medium | `CI: collect the deterministic evals/ tests, lint evals/, and make the test/lint docs match tests.yml` | D1, I4, C17, C26, I2 | D1 carries an engineer-confirmed decision and mechanism from Phase 9's D1 bullet: the explicit file path, the `evals/fixtures` collection guard, and the `evals/README.md` clarification. Cite it rather than reopening it. C26 renames `id: detect`, so every `steps.detect` reference must change in the same commit. I2 has a matching site: root `CLAUDE.md`'s Commands block, which is under a 200-line cap, so make it a same-line edit. After changing the workflow, push and confirm CI runs clean. |
| 10 | Medium | `Dependabot: add pip ecosystem entries` | S21 | Add two directory-scoped entries, as Phase 9 specifies: `/` for `requirements-dev.txt` and `/plugins/skill-management` for its `requirements.txt`. Check GitHub's Dependabot docs on how it handles wildcard pins like `==8.*` before calling the gap closed. |
| 11 | Medium | `Docs accuracy: add a case-studies index completeness test; fix stale design-decision, eval, and reference claims` | D4, C3, C11, C12, C23, C24 | D4: the index has gone stale again after hand fixes (2 of 18 files missing at the latest check). Add a completeness test beside `claude/.claude/hooks/tests/test_doc_counts.py` instead of a fourth hand fix. C3 and C12 moved and are still stale, at `docs/design-decisions/check-runner-charter-scoping.md:19` and `docs/design-decisions/specialist-reviewer-roster.md:9`. Both files are decision records, so correct the false present-tense claim with a dated note rather than rewriting the decision. For C12, drop or anchor the line-number citations, which have gone stale twice. C23: cite `cache_rebuild_rules.py`'s constant by symbol. C24: retitle `docs/rules-references.md` to name its GitHub Actions scope, for the reasons Phase 8 gives. |
| 12 | Medium | `Path-scoped rules in dispatched subagents: settle whether they load, then cite or retire the SQL-conventions duplication` | D6, C14 | Before writing any prose, D6 needs Phase 8's empirical check: dispatch a throwaway `code-writer` against a file that matches a rule's `paths`. If the check is inconclusive, say so. The result decides C14. If rules don't reach subagents, the duplication in `staff-data-engineer.md` and `staff-analytics-engineer.md` is load-bearing and gets the SSOT-exception cross-reference. If they do, removing the duplication becomes an option. Root `CLAUDE.md` is under a 200-line cap. The agent-file edits need `/agent-review`. |
| 13 | Medium | `lovable-cloud: eval cases for migration-sync, drop its dead TRIGGER prose, cite the PostToolUse-success assumption` | D7, C18, D14 | One plugin, so one version bump. Scope D7 to `lovable-cloud-migration-sync`, because that skill runs `git rm` (Phase 9). D7's cases must suit a `disable-model-invocation: true` skill once C18 removes its TRIGGER prose. For D14, link the existing citation at `.claude/plans/warn-read-consumes-handoff.md:58` instead of re-running `verify-sources` (Phase 10). C18 needs hook-enforced `/skill-review`. |
| 14 | Low | `Enable ruff's S (bandit) rules with scoped suppressions` | S25 | Carry Phase 9's rescoped S25(a) and its engineer-confirmed `S103` mechanism. That mechanism exists because `per-file-ignores` can only add suppressions. Re-measure the finding counts first (10,547 at `eb8317ad`). S25(b)'s per-file triage stays outside this issue. Every suppression carries its rationale comment. |
| 15 | Low | `Extract the 30-day state-file eviction sweep into a _lib.sh helper` | SC2 | The four sites differ: two of them add `-type f` and a symlink guard. Make both explicit parameters, and call out any site that gains the guard as hardening (Phase 10). `review-ledger.sh`'s sweep stays out. Add a boundary test at 29 and 31 days. |
| 16 | Low | `marker.sh clear-stale: check the recorded PID start time, not bare kill -0` | C10 | Related to #502 but distinct, so don't conflate them: #502 is about scoping a PID to the session vs. the skill, while this is about PID reuse. Include Phase 10's third site, `nudge-handoff-near-context-cap.sh`, and its check on the `env TZ=UTC` invocation. Test the helper directly with a live PID, a dead PID, and a recycled PID. |
| 17 | Low | `Hook test and comment gaps` | C8, D11, D13, D15, S22 | Five small, independent items from Phase 10: (1) `test_hook_alignment.py`'s docstring for informational hooks should cover the PreToolUse+`ask` shape. (2) Add a flag-hoisted `gh --repo … pr create` test for `deny-escaped-backticks-in-pr-body.sh`. (3) Add a `$HOME`-relative budget-exhaustion test for `enforce-marker-script-shape.sh`. (4) Add a test with the marker only in `--title` for `require-stow-reminder.sh`. (5) Add a 2 MB stdin cap to `parse-git-command.py`, mirroring `parse-manifest-dependencies.py`. |
| 18 | Low | `Script hygiene: _config_dir skip reasons, non-numeric plugin version segments, one fetch per cleanup run` | C20, C21, SC3 | SC3 is related to #1110 but distinct, so don't conflate them: #1110 is about which direction branch classification checks ancestry, while this is about fetching once per branch. C21's code no longer imports `packaging`, so Phase 10's `packaging` guidance is stale. Re-derive the fix against today's snippet. |
| 19 | Low | `skill-review: point its length target at check-skill-length.sh's limit_for() exceptions` | C15 | The file is at 199 of 200 lines and belongs to the `skill-management` plugin, so it needs a version bump and hook-enforced `/skill-review`. Trim to fit rather than add a cap exception — the engineer confirmed extending the `review-permissions` trim decision to this file as well (selected "Extend it to skill-review too"). Point at `limit_for()` rather than listing its entries: Phase 6's own list has already gone stale (it gives `pr-description/SKILL.md` as 210 lines; `limit_for()` says 250 today). |

**If the pre-filing checks move findings.** If a check shows a finding is already fixed, move it to Fixed. If a pre-existing issue already covers it, move it to the pre-existing bucket. Either way, drop it from its issue, and drop the issue if it ends up empty. One case changes an issue's scope rather than just moving a finding: finding an open issue that already tracks the stow-tree `if` half of issue 1. In that case, issue 1 narrows to the plugin gates plus D8's tests. S14 and SC1 then get recorded as split across two issues, the way S2 is. Report this narrowing to the engineer before filing.

**Status section and supersession note.** The report's new `## Status` section groups finding IDs under four statuses:

- Fixed, each with evidence.
- Closed without a fix, each with its reason.
- Tracked by a pre-existing issue.
- Tracked by an issue filed on 2026-09-27, one line per issue.

Every ID appears exactly once, except S2. The dormant plan gets a dated note that sends readers to that section and names which of the plan's own decisions no longer hold. The two plan-originated detection gaps are recorded only in that note, because they are not report findings and the report's Status should not create new IDs. **Critical files** specifies both edits.

**Implementation sequence and who does what.** The `gh` operations stay in the parent session, because they publish to a public repo and sit behind a confirmation gate. After filing, one `code-writer` dispatch writes both doc edits: they share a single input, the finding-to-issue-number map, so splitting them would mean restating that map twice. The sequence is:

1. `/plan-review`
2. Run the pre-filing checks.
3. Present the final list and get the engineer's confirmation.
4. File the issues and record their numbers.
5. Run the `code-writer` dispatch.
6. `/code-review`
7. Commit.
8. `/ready-for-review`
9. Open the PR.

The one open call this design left to the user — extending "trim, don't raise the cap" to `skill-review/SKILL.md` (issue 19, row 22) — is settled: the engineer confirmed extending it.

**Assumption ledger**

Root: The 2026-08-22 audit's open findings are named only in a remediation plan that nobody is executing. They have no tracking issue, and the report has no status record, so no reader can tell what remains. The fix is one issue per root-cause unit of work, a per-finding Status index in the report, and a dated note that retires the plan as a tracker.

Givens:
- **G1 — The report's findings are frozen; only a dated `## Status` section near the top may be added.** `docs/reports/README.md:8-14` sets this as repo-wide policy, and changing it is outside this plan.
- **G2 — GitHub issue state keeps changing under other work.** This covers #1137, #1116, #537, #502, #1110, and whatever follow-up issues the pr-771 or merge-aware plans produced. Other work owns those issues, so this plan re-reads them at filing time rather than trusting what it read while planning.
- **G3 — #1116's decomposition runs on epic #1113's schedule.** That epic owns the work. Issues that touch `transcript-analysis.py` are scheduled loosely around it rather than made to wait on it.
- **G4 — Issues filed here are public the moment they are created.** The root `CLAUDE.md` says: "This repository is **public**". Confirmation therefore has to come before filing, not after.

| # | Assumption | Tag | Anchors |
|---|---|---|---|
| 1 | The unit of work is one root cause within one file-ownership area, not a dormant-plan phase. Phase 1 has 8 of its 15 findings fixed, Phase 7 has 4 of 17, and Phases 3–5 straddle the #1116 extraction. Two lighter alternatives fail. One umbrella issue fails because the 2026-08-10 triage had to retire #619, an umbrella over a partly-landed audit that tracked nothing. Reviving the dormant plan as written fails because its phases are stale in exactly the ways just listed. | `[verified: /tmp/discovery-audit-gap-table.md per-finding rows cross-checked against the plan's phase headings this session]` | root |
| 2 | C3 and C12 moved into `docs/design-decisions/` and are still stale | `[verified: docs/design-decisions/check-runner-charter-scoping.md:19; docs/design-decisions/specialist-reviewer-roster.md:9 vs. ciso-reviewer at claude-skills/skills/code-review/SKILL.md:264-337,439-477, grepped this session]` | root |
| 3 | S11 is fixed: root `CLAUDE.md` defers to `docs/hooks.md`, which discloses the `gh api` merge gap | `[verified: CLAUDE.md:107-112; docs/hooks.md:55]` | root |
| 4 | S20 is fixed: the Provenance subsection restates the doubt default | `[verified: CLAUDE.md:173-174]` | root |
| 5 | SC6 is closed without a fix. Its growth concern is now gated by landed work from the audience-restructure plan. | `[verified: check-claude-md-length.sh:44,122; docs/design-decisions/declined-sessionstart-additionalcontext-injection.md:41-42 cites that plan]` | root |
| 6 | I5 and C25 are closed without a fix, based on the remediation plan's own text: I5 is "informational, no fix needed", and C25 is "candidate to drop". C25 is also frontmatter ordering, which `yaml.safe_load` ignores. | `[verified: remediation plan Phase 9 I5 bullet and Phase 10 C25 bullet, read this session]` | root |
| 7 | C16 is tracked by #537 and needs nothing more | `[verified: dispatching session's read of #537, relayed]` | G2 |
| 8 | #1137 covers exactly four S2 lines and says it does not cover the rest | `[verified: dispatching session's read of #1137, relayed]` | G2 |
| 9 | None of #1116's landed relocations touched S4–S7, S15, I1, SC5, SC4, or C1 | `[verified: dispatching session's verification pass, relayed]` | G3 |
| 10 | #502 is distinct from C10, and #1110 is distinct from SC3 | `[verified: dispatching session, relayed; gap-table C10/SC3 rows]` | G2 |
| 11 | All three plugin commit gates miss `git -C <path> commit` in both their `hooks.json` `if` and their in-body regex. All three are tagged `cooperative`. | `[verified: dispatching session's empirical test, relayed; hooks.json:10 in all three plugins and each hook's tier line read this session]` | root |
| 12 | The four stow-tree commit gates carry the identical `"if": "Bash(git commit *)"` | `[verified: claude/.claude/settings.json:240,246,291,297]` | row 11 |
| 13 | The harness also skips those four stow-tree gates for `git -C <path> commit` | `[unverified]`: inferred only from the identical `if` text. `.claude/plans/pr-771-deferred-findings.md` row 14 recorded the same inference as unverified. | row 12 |
| 14 | The `cooperative` tier does not waive the `bash -c`/`eval` wrapper shapes | `[verified: docs/hooks.md:55]` | row 11 |
| 15 | Two plans already merged into this repo prescribe deleting the `if`, and neither deletion has landed. Merge-aware Phase 3 covers the four stow-tree gates, and Phase 4 covers skill-management. | `[verified: merge-aware-review-gates.md:279,289; _lib_command_concludes_commit has no hook caller (grep); settings.json:240 still carries the if]` | row 11 |
| 16 | It is unknown whether pr-771's planned "Issue 2", an audit of the `if` prefilters, was filed and is still open | `[unverified]`: this is a pre-filing check | G2 |
| 17 | Record the bare-`&` and inline-alias gaps as documented known gaps with a pinning test, not as a fix | `[engineer-verified: "Record as known gaps, yes. My prior decision when the plan was made predated the hook tiers."]` | root |
| 18 | Both gaps exist in current code | `[verified: _lib.sh:1440-1444 (no bare & split); _lib.sh:1346 (-c/--config-env value skip)]` | row 17 |
| 19 | Existing entries supply the wording to reuse: `require-ready-for-review.sh` already lists the bare-`&` gap, and `deny-pii-in-commits.sh` already lists the persistent alias. `deny-pii-in-commits.sh` is tiered `cooperative, irreversible`. | `[verified: require-ready-for-review.sh:68-75; deny-pii-in-commits.sh:3,202-203]` | row 17 |
| 20 | Trim `review-permissions/SKILL.md` under its 200-line cap rather than add a `limit_for()` exception | `[engineer-verified: "Yes review-permissions shouldn't be so lengthy anyways. I'm surprised that's the one that needs to be trimmed."]` | root |
| 21 | `review-permissions/SKILL.md` is 197 lines and `skill-review/SKILL.md` is 199, both against a 200-line cap | `[verified: dispatching session's wc -l, relayed]` | row 20 |
| 22 | Applying "trim, don't raise the cap" to `skill-review/SKILL.md` (C15) as well | `[engineer-verified: "Extend it to skill-review too (Recommended)"]` | row 20 |
| 23 | C15's fix should point at `limit_for()` instead of listing its entries, because the dormant plan's own list has already gone stale | `[verified: remediation plan Phase 6 C15 bullet ("210") vs. gap-table SC7 row citing check-skill-length.sh:106-115 ("250")]` | row 22 |
| 24 | The Status section lists IDs grouped by status rather than as an 85-row table. Each ID appears once, and the section stays scannable. Two lighter alternatives fail. Having no Status section fails because the only other status home would be the dormant plan this edit retires. Committing the /tmp gap table fails because it carries two wrong Fixed verdicts (row 2) and would compete with the report as a second status home. | — | G1 |
| 25 | The dormant plan gets a dated note, not a rewrite. Doing nothing leaves its phase text reading as a live obligation, the failure `docs/reports/README.md:12-14` names for reports. Rewriting or deleting the plan is heavier and destroys the reference design the issues cite. | `[verified: docs/reports/README.md:12-14, applied by analogy]` | root |
| 26 | Priority goes in a line in each issue body plus the filing order. No labels, milestones, project board, or sub-issues. | `[verified: repo-quality-audit-issue-triage.md Out of scope filed bare]` | root |
| 27 | Issue bodies cite the dormant plan's phases by heading, not by line number, because the supersession note shifts every line number in that file | — | row 25 |
| 28 | `repo-quality-audit-issue-triage.md:313` says the 2026-08-22 audit has "30 findings"; the true count is 85 | `[verified: 85 bold finding IDs in findings.md, grepped this session]` | root |

## Critical files

- **`.claude/plans/discovery-audit-issue-triage.md`**: this plan, committed as provenance.
- **`docs/reports/2026-08-22-discovery-audit/findings.md`**: insert `## Status — updated 2026-09-27` between the header block (`:3-5`) and `## Relationship to the prior report` (`:7`). Contents, one fact per bullet:
  - The precedent's opening sentence: the findings record the repo at `6291b343` and are not revised as fixes land; `docs/reports/README.md` states why. Add that this section is the one exception and is the authoritative per-finding status.
  - A re-check line pinned to the SHA of `main` at execution time, not to `6291b343`.
  - **Fixed (17).**
    - Where the fix was traced, give the landing PR, plus the commit where the gap table records one: S1 (#790); S3, C7, D2 (#863); S13 (`bb24a0cc`, #1064); C6, D12 (`25e18978`, #820).
    - Otherwise, give the current `file:line` that shows the fix: S19, C2, C5, C9, C13, C22, D9, D16, plus S11 and S20 with the evidence from rows 3–4.
    - Cite C7's evidence by its current helper name, `_lib_default_branch_or_guess` (`guard-settings-session-keys.sh:88`), not the name the gap table recorded.
  - **Closed without a fix (10).**
    - S26–S30, I3, SC8: reviewed sound at the baseline, per this report's N/A sections.
    - I5: informational only.
    - C25: cosmetic, with no behavioral effect.
    - SC6: growth is now gated by `check-claude-md-length.sh`'s byte limit, and the offload path was declined in `docs/design-decisions/declined-sessionstart-additionalcontext-injection.md`.
  - **Tracked by pre-existing issues.** C16: #537. S2's four `show-toplevel` sites: #1137.
  - **Tracked by issues filed 2026-09-27.** One line per issue: its issue number, then the IDs it covers.
  - One line stating that S2 is the only ID split across two issues.
  - One line stating that `.claude/plans/discovery-audit-remediation-plan.md` no longer tracks these findings, and its phases remain as reference design that the issues cite.
  - Order IDs within each group as the report does: S, I, C, SC, D. Keep every `§` citation on one line, per `.claude/rules/citation-grammar.md`. Do not use plan-internal labels such as "Dispatch 1b" in this durable doc.

  Reuse the 2026-08-10 report's Status section (`docs/reports/2026-08-10-repo-quality-audit/findings.md:17-81`) as the model for voice, dated bullets, and naming commits alongside PRs.
- **`.claude/plans/discovery-audit-remediation-plan.md`**: add a dated blockquote directly under `# Discovery-audit remediation plan` (`:1`), before `## Context` (`:3`). Open with **Superseded in part — 2026-09-27.**, then one fact per sentence:
  - The plan was not executed as phased. Phase 1's Dispatch 1a landed as #863, and Phase 2's single finding was fixed by #790.
  - Per-finding status and tracking issues live in `docs/reports/2026-08-22-discovery-audit/findings.md` § "Status — updated 2026-09-27", which is authoritative.
  - The phase text below stays as reference design, and each tracking issue cites the phase it draws from.
  - Phase 1b's bare-`&` and inline-alias prerequisites are now recorded as documented known gaps with pinning tests instead of being fixed (see issue 8's number above).
  - Phase 6's `limit_for()` exception for `review-permissions/SKILL.md` is replaced by trimming that file under its 200-line cap (see issue 6's number above), and the same trim rule extends to `skill-review/SKILL.md` (see issue 19's number above), replacing A9's proposed exception for that file too.
  - Phase 1b's other items that came from the plan rather than the report go to issue 2 (see its number above): `GIT_DIR` stripping at `_lib_capped_for`, the exit-124 handling at content-bearing sites, and the `gtimeout` probe in the plugin `_lib_jq` copies.

  Nothing else in the file changes.
- **No source code changes.** The rest of the deliverable is GitHub state: 19 new issues.

**Dispatch split.** There is one `code-writer` dispatch, covering the two doc edits above, and it runs only after the issues exist, since both edits name their numbers. Its prompt carries the final finding-to-issue map and the execution-time SHA. Verification command: `.venv/bin/python3 claude/.claude/scripts/select-tests.py`.

## Verification

No pytest or ruff assertion can prove this work correct, because the deliverable is tracker state plus two doc edits. Verification comes in three layers.

**Pre-filing checks. Run them before presenting the list, so the list the engineer confirms is final.**

1. **Duplicate search per issue.** Run `gh issue list --search "<query>" --state all` with at least two queries per issue: one finding ID (e.g. `S14`) and one subject keyword. A hit that already tracks a finding moves that finding to the pre-existing bucket.
2. **Scope of issue 1:**
   - Find out what pr-771's planned "Issue 2" became. Search `gh issue list --search "if-dispatch" --state all` and `--search "Bash(git commit"`.
   - Check whether any open issue or PR tracks `merge-aware-review-gates.md` Phases 2–5.
   - Confirm that the earlier `git -C` test exercised the harness's `if` layer, not only the script regex.
   - If it did not, or to settle row 13 for the stow-tree gates, run the live-probe procedure at `.claude/plans/fragment-matcher-quote-bypass.md:239-244`. Run it against one plugin entry and one stow-tree `Bash(git commit *)` entry. Record the result in issue 1's body.
3. **Pre-existing issues.** Re-confirm that #1137 (four lines), #1116 (open, with remaining groups), #537, #502, and #1110 are still in the state and scope this plan records.
4. **Freshness.** For every finding in every issue, re-run the gap table's evidence command against current `main` and use the fresh figure in the body. If a finding turns out to be fixed, move it to Fixed with its evidence.
5. **Relocation check for Fixed verdicts based on absence.** Run `git grep` for each such finding's distinctive text across the whole repo, not just the one file the gap table searched. At minimum this covers C2 (`handoff-ratio`) and C13 (`only checks that python3`). C3 and C12 failed exactly this check this session. A match inside a preserved record, such as a case study, plan, or report, does not reopen the finding.
6. **Re-measure line counts.** Measure `review-permissions/SKILL.md`, `skill-review/SKILL.md`, root `CLAUDE.md`, and `claude/.claude/CLAUDE.md` again.
7. **Path resolution.** Every path in every issue body must resolve on today's `main`. The skills now live under `claude-skills/skills/`, and `transcript_analysis/` has absorbed several relocations.

**Hard precondition.** Present the final list to the engineer, with titles, finding IDs, and priority, plus the Status outline. Get explicit confirmation before the first `gh issue create` (G4).

**Post-filing checklist**

1. Every issue on the confirmed list exists and is open, and each title matches the list.
2. The coverage arithmetic holds after any moves made by the pre-filing checks. As planned, it is 85 = 17 Fixed + 10 closed without a fix + 1 pre-existing only + 57 new-issue IDs.
   - The 57 IDs partition across the new issues.
   - Every ID appears in exactly one Status group, with S2 as the one ID listed under two issues (plus S14 and SC1, if issue 1 narrowed).
3. Every body carries the "Covers" line, including the `6291b343` baseline, and a priority line. Every body cites the report and the dormant plan by heading and restates no finding text.
4. These cross-references are present:
   - Issue 1 references `merge-aware-review-gates.md` Phases 3–4, `pr-771-deferred-findings.md` Finding 1, and pr-771's follow-up issue if it exists.
   - Issue 2 references #1137.
   - Issues 3, 4, and 7 reference #1116, as soft links rather than dependencies.
   - Issue 16 references #502, and issue 18 references #1110, each marked "related but distinct".
5. Issues 6 and 8 quote the engineer's words verbatim and present them as the engineer's. Issue 19 states that the engineer confirmed extending the review-permissions trim decision to `skill-review/SKILL.md` as well.
6. `git diff` of `findings.md` shows hunks only inside the new Status section. `git diff` of the remediation plan shows only the new note.

**Repo test command**, scoped per this repo's `CLAUDE.md`:

```bash
.venv/bin/python3 claude/.claude/scripts/select-tests.py
```

If the selector picks them up, two tests matter most: `claude/.claude/hooks/tests/test_doc_counts.py`, because the Status section adds numeric claims, and `claude-skills/skills/tests/test_skills.py`'s citation-grammar tests, because the plan note adds a `§` citation. Run `/code-review` before the commit and `/ready-for-review` before pushing.

## Out of scope

- **Fixing any finding.** This plan files issues and makes two doc edits; it lands no remediation.
- **Editing `findings.md` outside the new `## Status` section.** G1 rules this out, and it includes correcting stale citations inside the findings themselves.
- **Rewriting, restructuring, or deleting `discovery-audit-remediation-plan.md` beyond the dated note.** Its phase text is the reference design the issues cite.
- **Correcting `repo-quality-audit-issue-triage.md:313`'s "30 findings" miscount.** That plan is a merged record; the new Status section states the 85 total (row 28).
- **Editing `merge-aware-review-gates.md`, `pr-771-deferred-findings.md`, or `claude-md-audience-restructure.md`.** Issue 1 cross-references the first two, and SC6's Status entry cites what the third shipped.
- **Commenting on, editing, or closing #1137, #1116, #537, #502, or #1110.** The new issue bodies cross-reference them, and that is all.
- **Committing `/tmp/discovery-audit-gap-table.md`.** The Status section is the durable home, and the table carries two wrong Fixed verdicts.
- **Re-triaging any finding's severity.** Priority tiers order the filing; they do not re-score findings.
- **The dormant plan's own out-of-scope items.** S25(b)'s per-file bandit triage, the limit on S5's message-text redaction, `review-ledger.sh`'s sweep, and C13's aside about the Python version floor all stay out of scope. Issues 3, 14, and 15 name them as exclusions by citation.
- **Creating labels, milestones, a project board, or a sub-issue hierarchy** (row 26).
