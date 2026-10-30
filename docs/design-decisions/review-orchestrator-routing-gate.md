# A `PreToolUse` `Skill` gate routes `/code-review` rounds through `review-orchestrator` while a config key is on

*2026-10-17.*

The decision is a per-account config key, `review_orchestrator_routing`, off by default, and a gate, `require-review-orchestrator-routing.sh`, on `PreToolUse` matcher `Skill`. While the key is on, the gate denies a main-session `Skill(code-review)` call unless the call's args begin with the word `unrouted`. The deny text sends the session through the `review-orchestrator` protocol in `claude-skills/skills/subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven" for that round. The protocol stays in that one home. The deny text carries only the routing instruction.

The problem it solves: the agent's `TRIGGER` and the `subagent-delegation` protocol both require an explicit request, and an engineer who wants every round routed has to remember to ask each time. [review-orchestrator-gate-release-decoupling.md](review-orchestrator-gate-release-decoupling.md) planned a SessionStart hook for the key's delivery. That delivery point was set aside, because the reminder would reach every main session, including ones that never run a review. Delivering at invocation time reaches only the session that is about to run a round.

**Why the gate denies the `Skill` call.** The routine round is the session's own `Skill(code-review)` call, and a `PreToolUse` deny on it is the last point before the skill body loads. Denying the call keeps the body out of the main session, and the old record names the body as one of the three things the agent exists to keep out. Any delivery that allows the call loads the body first.

**The escape is a leading `unrouted` word in the args, not the presence of args.** Routine rounds commonly carry args, as descriptive prose that often embeds a diff-file path, so args cannot mark a round that must stay inline. A path in args fails for the same reason, because `code-review/SKILL.md` tells the skill to reuse its invoker's diff-file path. The word is `unrouted`, not `inline`, because `code-review/SKILL.md` already uses `inline` as a fixed value for the authoring-agent field and the fix-route label. A flow passes args `unrouted`, or args `unrouted <path>` with the path last when the round has a diff-file path. Alternatives set aside:
- A live `ready-for-review` active marker. It is live from step 0 through step 9, so every per-commit fix round inside the loop would run inline too. `/review-pr` has no active marker, and the HALT fallback has none either, so it would still need a token.
- This session's recorded cumulative-review or review-pr artifact. It outlives the pass, so later routine rounds would escape too, and reading it adds a repo-hash computation to every call.
- A re-allow within a time window. No source grounds the timeout, and a retry with no args would get past the gate.
- A marker the HALT step writes. It adds a new `marker.sh` surface for a rare path.

Four main-session rounds must stay inline, and each carries `unrouted`: a `/ready-for-review` cumulative pass, which must not write the code-review marker; a `/review-pr` pass, which runs under a no-fix, no-marker override; the HALT fallback in `subagent-delegation`; and an inline round the user asks for.

**Steps that pass `unrouted`.** Each edit sits inside one existing line, and each reaches every stow consumer, key on or off:
- `subagent-delegation/SKILL.md` § "Exception: gate/review loops stay orchestrator-driven", the HALT step: it invokes `/code-review` with args `unrouted` and carries the word through the rest of that review loop until a clean round writes the marker. The cause of a HALT recurs from round to round, so routing each re-run would pay one deny and one failing dispatch every time. The request sentence in the same section gains a parenthetical naming the routing gate whose denial counts as the explicit request.
- `ready-for-review/SKILL.md` § "3. Code review (halt on findings)": the cumulative pass invokes `/code-review` with args `unrouted <path>`. The fix-commit rounds in steps 3 and 4 stay routed.
- `review-pr/SKILL.md` § "Step 5 — Line-level pass": the pass invokes `/code-review` with args `unrouted <path>`.

The deny text still names both diff-file passes, so a pass that loses the word costs one deny and one retry, provided the session follows the deny text's inline exception for these passes. Only that prose keeps a pass out of the agent, which would dispatch `code-writer` and write the marker against the pass's override.

**`code-review` reads the path after the word.** `code-review/SKILL.md` § "Ripple effect triage", in its diff-artifact note, says that in args beginning with `unrouted` the path is what follows that word, and that `unrouted` alone hands no path. Without that clause a session could hand `comment-discipline-reviewer` the word instead of the path, and that reviewer carries no `Bash` to recover the diff. The clause reaches every consumer's cumulative pass and `/review-pr` pass, key on or off. A pre-merge probe of the unchanged skill would observe one session's reading and leave the risk in place. If a session still hands over the word after merge, the fallback is the revert set: the HALT step's args edit (the request parenthetical stays), the one-line edits in `ready-for-review` and `review-pr`, and their pins, including the span-coupling assertion in `test_require_review_orchestrator_routing.py`. The `code-review` clause and its pin stay. The revert adds a new CHANGELOG line and a dated line here, because both are history. A key-on account also sets the key to `false`.

**The gate's allow rules and fail directions live in the hook header**, which is their one home. In summary, the gate lets through every subagent call (the agent's own included), every call whose args begin with `unrouted`, and every call from an account whose key is off or cannot be read. A malformed payload still denies, as for every gate. The deny label comes from `DENY_GATE_LABEL`, and `denials.py` registers it so the transcript analyses attribute the deny to this gate.

**This is not a hook the old record rejects.** [review-orchestrator-gate-release-decoupling.md](review-orchestrator-gate-release-decoupling.md) rejects a dispatch-target allowlist on the agent's `Agent` calls, because it would deny the `plan-architect` dispatch that `/code-review` makes. It also rejects an identity-keyed deny on `marker.sh write`. This gate acts on the main session's `Skill` calls, allows every call that carries an `agent_id`, and constrains nothing the agent can call, read or write.

**The key counts as the engineer's standing request.** `claude/.claude/CLAUDE.md` § "Shipping" already has a session read one machine-level key, `autonomous_shipping`, as the engineer's standing request to ship, and the old record's "Staged rollout" names the key as the rollout mechanism. So the agent description and its `TRIGGER` stay as they are. The `subagent-delegation` sentence keeps its pinned phrase `only when the user explicitly asks`, and the parenthetical names the gate whose denial is that request.

**Enabling it.**
- Set the exact bare line `review_orchestrator_routing = true` in each account's `<config-dir>/claude-config.toml`. Change the scaffolded `false` row if one exists, or place the new line after it, because the last row for a key wins.
- Confirm that `~/.claude/scripts/config-get.sh review_orchestrator_routing` exits 0, from a shell or session whose `CLAUDE_CONFIG_DIR` is the account being enabled.
- Start a new session after the pull and after `./install.sh`. A session started earlier may not load the gate's registration.
- A key flip in either direction should take effect on the next `Skill` call, because a hook run rereads the key and the key memo lives only for one process. That the vendor starts a new process on every call is unverified (see below).
- Re-run `./install.sh` unless `~/.claude/hooks` is a folded symlink, because `claude/.claude/settings.json` runs the hook from `~/.claude/hooks/` on every account. Also re-run the installer that stows the packages `claude/.claude/scripts/stow-packages.sh` lists into the account's config dir, unless `<config-dir>/agents` is a folded symlink. On the personal account, whose config dir is `~/.claude`, that installer is `./install.sh`. `./install.sh` stows into `$HOME` only, so a non-personal account needs its own installer. Use `readlink -f` to confirm that `~/.claude/hooks/require-review-orchestrator-routing.sh` and `<config-dir>/agents/review-orchestrator.md` each resolve into the checkout. With the key on and the agent unlinked, every routine round stops at the refused dispatch. The exits are re-running the installer, setting the key to `false`, or asking for an inline round.

**Alternatives set aside.**
- SessionStart `additionalContext`, the old record's plan: it reaches every main session.
- A routing step at the top of `code-review/SKILL.md`: the body loads before the step can route, and the file sits at its line cap.
- `PreToolUse` allow with `additionalContext`: the body still loads, and the repo's evidence disagrees on whether the harness delivers that field.
- `UserPromptSubmit`: it sees typed prompts, never the routine `Skill` call.
- A line in `TRIGGER`, the agent description or CLAUDE.md: static text is unenforced, the body still loads, and it needs the remembering that fails.
- `permissionDecision: "ask"`: it prompts the engineer every round.
- Routing a typed `/code-review`: it stays in the main thread by design. The harness expands a typed command without a `Skill` call, so the gate never sees one. `/review-pr` is for reviewing a pull request. A user who types `/code-review` probably wants it in the main thread.
- An inline fallback for a dispatch the harness refuses (an unlinked or unknown agent, or a session with no `Agent` tool): the protocol's existing stop applies. It is rare, one command recovers it, and it fails visibly. A fallback would need a stop-clause exception, a carve-out for hook denials and rejected dispatches, a cause clause in the deny text, and their pins.

## Known gaps

- A typed `/code-review` runs in the main thread by design. `/review-pr` is for reviewing a pull request.
- A fix re-run after a user-asked inline round or a typed `/code-review` is routed on purpose: it reviews the fix as a routine staged-diff round, which is the round the key exists to route.
- After a HALT fallback, the rest of that review loop runs inline until a clean round writes the marker. An agent call that lacks `agent_id` is one cause of a HALT, and it costs each new review loop one denied, HALTing dispatch until the key is off.
- A deny that repeats on the `unrouted` retry means the gate does not read the args as starting with the bare lowercase word `unrouted`, followed by the end of the args or ASCII whitespace. The gate bullet in `docs/hooks.md` gives examples and the recovery.
- A main session run as `claude --agent review-orchestrator` carries `agent_type` alone, so the gate denies it with text that points at itself. Recovery is args `unrouted` or the key set to `false`.
- A plugin skill whose final name component is `code-review` is routed too, the same name collision `activate-handoff-bypass.sh` accepts.
- A routed round follows the protocol's "Stage the review target" step, so on a key-on account a review of unstaged work stages it first. A round with no git diff behind it is routed, as an explicitly requested round is today.
- `/ready-for-review` step 4 should not halt on a gate-denied block, because the reviewer's spawn check is branch-wide and covers completed passes only. If a reviewer misapplies it, step 4's stated escape, stating the deviation with a rationale, clears it. GH-1131 tracks the consumers that cannot tell a gate-denied `Skill(code-review)` block from a real invocation.
- Each loop the gate routes adds one `code-review` round-open with no ledger block, because a denied `Skill` call still leaves its `tool_use` block in the transcript and round detection ignores the result. Until GH-1131 is fixed, check round numbering with `review-ledger.sh show`. On a key-on account the round-number-mismatch counter in `author-outcome` overcounts.
- Every routine round on a key-on account runs through the agent before the dogfood has observed the old record's four unverified claims and round numbering across consecutive dispatches. A failure there surfaces as a `HALT:` entry or a protocol stop.
- With the key on, the old record's accepted residuals (prompt injection, the agent's `Bash` and `Agent` reach, the marker gaps) apply to every routine round, not only explicit requests. The old record's dogfood still requires one round with a fix and a `git commit` that passes on the agent's own marker, which exercises the no-fix marker condition and the commit gate. No criterion inspects each marker write.
- `migrate-legacy-config.sh` writes an explicit `false` row for a key with no legacy file. Flipping the default after the dogfood therefore needs a row migration (`docs/config-file.md` § "Migration"), and that migration cannot tell a scaffolded `false` from a deliberate one, so it must be a consented rewrite or a new key.
- A compaction summary can keep "unless invoked as `unrouted`" and drop the four inline cases, so the compacted session may lead with `unrouted` outside them. The dogfood's escape audit is the only check.
- The cost guard's inputs live in transcripts, which age out after `cleanupPeriodDays` (`docs/pr-cost.md`), and a partly aged-out branch reads low with no warning. Until the guard's capture runs, a branch older than that window is missing, so the key-off arm, worked before the key went on, ages out first.
- A missing or failing `jq` now blocks every `Skill` call on every account, key off included, as it already blocks `Bash`, `Edit`/`Write` and `Read` (`docs/hooks.md` § "Gate deadlock recovery").

## Dogfood

The key ships off, and the old record's dogfood still gates flipping the default. This change adds to it:
- A round the gate routes counts as a round "on explicit request", and the deny text joins the sources of the last criterion, which asks that a session action followed from the agent description and the `subagent-delegation` section alone.
- Every key-on main-thread `Skill(code-review)` block the gate did not deny must have args that begin with `unrouted` and must follow one of the four inline cases. A HALT case covers every later round of its loop until a clean round writes the marker. The user-asked case is scored per round. An un-denied block with no leading `unrouted` means the gate did not run. One that leads with `unrouted` but follows none of the four cases means the escape was misused, including carry past the clean round that ended a loop. A denied block inside a `/ready-for-review` cumulative pass or a `/review-pr` pass is a lost escape: it is subtracted from the routed-round count, not counted as a routed success. A lost escape followed by a `review-orchestrator` dispatch, or any lost escape inside a `/review-pr` pass, fails the dogfood. A lost escape followed by an `unrouted` retry is informational.
- Cost is a guard, not a criterion routing must win. A cost decrease is a bonus. The guard reads the net cost per review loop below, key-on minus key-off. It fails when the lower bound of that net's bootstrap interval at 95%, a chosen level, sits above zero, and it counts as failed until it has run. The interval is a percentile bootstrap of 10,000 replicates, a chosen count, that resamples branches within each arm.
- The net per loop is fixed here, before the guard runs. A loop ends at the clean round that writes the code-review marker. The net is the overhead difference less the carry:
  - Overhead: the sum of `total $` over the loop's `code-review` rounds, as `review-round-cost --skill code-review` prints them per round. A key-on loop's denied round-open counts with its `review-orchestrator` round. Reviewer, `code-writer` and `plan-architect` spend stays in, because the agent makes its own spawn decisions, so equal spend in both arms is not given.
  - Carry: the growth gap, priced at the `pricing.py` cache-read rate of each later turn's model, summed over the key-off session's main-thread turns after the loop that fall outside every round window. The growth gap is the key-off mean minus the key-on mean of main-thread context growth from a loop's first round-open to the first main-thread turn after its last round closes. Each replicate re-estimates it. A cache read is the cheapest way a later turn can re-read a token, so the carry understates the saving.
- The sample is fixed here too, and every sampled branch has merged. Key-on loops come from the branches step 1 lists. Key-off loops come from each key-on account's most recent branches in this repo before its key went on, as many as that account has key-on branches, skipping any branch worked under a different `round_consult_round2_pilot` or `architect_fix_plan_pilot` state. A branch the capture marks missing leaves its arm short, and either arm short of this sample means the guard has not run.
- The guard runs once at least 10 key-on PRs have merged, a chosen minimum.
- Until GH-1312 is fixed, a task notification closes an inline round early, which under-prices the key-off arm and biases the net against routing. A guard pass read before the fix stands. A guard failure read before the fix is re-run after it.

The guard pools key-on accounts by design, so `docs/private-project-redaction.md` § "A wider corpus goes to the owner, never into a public artifact" governs every quantity the guard computes. A public artifact that reports the guard's outcome states only whether the guard passed and whether the default flipped.

The shipped analyses cannot run the escape audit alone, because `review_rounds.py` never extracts args. The recipe is a manual read of this repo's own local transcripts, and it publishes only booleans. The recipe:
1. The engineer notes the branches worked while the key was on, because no transcript records the key's state. The engineer also notes the config dir of the account that worked each branch, for the guard's per-account capture.
2. `transcript-analysis.py skill-invocation --branches <list>` gives the main-thread `code-review` blocks, in its top-level and routed buckets. "Routed" there is the bucket sense: a `Skill` call fired while another skill's body was active (`docs/transcript-analysis.md`). A `/ready-for-review` cumulative pass is therefore `routed` in the bucket sense and `unrouted` in the args sense, and neither means review-orchestrator routing. Write "bucket" or "args" wherever the word could be read either way.
3. `transcript-analysis.py review-trace --this-repo --branches <list> --deny-summary` gives the denies under the `review-orchestrator routing` label.
4. Any excess of step 2's count over step 3's is the un-denied blocks. First subtract every deny that fell inside a `/ready-for-review` cumulative pass or a `/review-pr` pass: it is a lost escape, not a routed round, and a local read of the denied block's parent skill identifies it when `--deny-summary` cannot join a deny to its block's bucket. Read only the un-denied blocks' args, locally, for whether each begins with `unrouted` (the args sense) and follows one of the four inline cases.

No shipped subcommand computes the guard. A follow-up builds one that captures each sampled branch per account and computes the net and its interval by the definitions above, and its section in `docs/transcript-analysis.md` states how it reads each figure.

## Withdrawal form

Withdrawing the agent after a failed dogfood touches every surface this change adds:
- The gate (`claude/.claude/hooks/require-review-orchestrator-routing.sh`), its test, its `settings.json` entry, and the `docs/hooks.md` bullet and tier row: delete them and restore the tier count.
- `denials.py`: move the `review-orchestrator routing` label into `_DENIAL_HOOK_LABELS`' legacy-only block, so older transcripts still attribute their denies.
- `test_transcript_denials.py`: remove the `_BOOTSTRAP_FALLBACK_HOOKS` row and the behavioral-deny case, which fail once the hook is gone.
- `select-tests.py` and `test_select_tests.py`: remove both `subagent-delegation` declarations.
- The skill sentences that pass `unrouted` or name the routing gate (including the request parenthetical), and their `test_skills.py` pins, including the span-coupling assertion in the gate's test.
- `_lib_args_lead_word_is` and its tests in `test_lib_payload_matchers.py` go with the gate, because the gate is its only caller. `_lib_skill_basename` and its test stay, because `activate-handoff-bypass.sh` still calls the helper.
- The `config-keys.psv` row and its count sites stay, with the docs marking the key retired. Removing the row would leave each account's scaffolded `review_orchestrator_routing = false` row printing "skipping line for unrecognized key" on every config read, and nothing prunes those rows. A later change may prune them and then drop the schema row.
- This record, the partial-supersession line on the old record, and the CHANGELOG entry stay as history, and the withdrawal adds its own record line and CHANGELOG entry.

A plain revert of this change's PR must keep the config-key surfaces too, for the same reason, once any consumer has re-run `./install.sh` or the migration.

## Unverified until observed

Observed on the gate's payload shape when this record was written:
- A `PreToolUse` `Skill` payload carries the call's args as the string `tool_input.args`, and the key is absent when no args are given.
- The `review-orchestrator` agent's own `Skill` call reaches a hook with a non-empty `agent_id`, and the main session's call carries none.
- A denied `Skill` call produces only an error tool result and no skill-body record, and still leaves its `tool_use` block in the transcript.
- The agent turns a hook denial it cannot satisfy into a `HALT:` entry.
- No match was found for an earlier `Skill(code-review)` call that led its args with `unrouted`, in the searched local transcripts only.
- A hook entry naming a missing script is reported by the harness as a non-blocking error, and an allowed `Skill` call still ran beside it. What a non-folded account sees on a pulled-but-not-reinstalled checkout was not observed, so the CHANGELOG note hedges it.

Unverified when this record was written:
- Whether the harness resolves skill names case-insensitively, or trims whitespace before resolving them. The gate compares `tool_input.skill` exactly and case-sensitively, so a name that misses routes nothing, with no signal.
- Whether a session that follows each edited step passes `unrouted` in its args.
- Whether every session follows the `code-review` clause and hands the reviewer the path rather than the word.
- Whether a new `settings.json` registration reaches only a session started after the pull and `./install.sh`, and that the vendor runs each hook as a new process on every call.
- Whether a reviewer misapplies its branch-wide spawn check to a gate-denied block at `/ready-for-review` step 4.
- The old record's list of unverified claims, and round numbering across consecutive dispatches.

### Sources

- `claude/.claude/hooks/require-review-orchestrator-routing.sh`, whose header holds the allow rules, fail directions and known gaps.
- `claude-skills/skills/subagent-delegation/SKILL.md`, whose gate/review-loop exception holds the protocol the deny text points at.
- [review-orchestrator-gate-release-decoupling.md](review-orchestrator-gate-release-decoupling.md), the decision this one partially supersedes and whose dogfood it extends.
- `claude/.claude/CLAUDE.md` § "Shipping", for the machine-level key as a standing request.
- `docs/config-file.md` and `claude/.claude/hooks/config-keys.psv`, for the key's schema row and the migration constraint.
- `claude/.claude/scripts/transcript_analysis/denials.py`, for the label registry the gate joins.
