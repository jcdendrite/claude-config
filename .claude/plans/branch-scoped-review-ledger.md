# Branch-scoped review ledger with a SETTLED disposition (GH-1182)

## Context

Goal: key the `/code-review` ledger (`review-ledger.sh`) to the branch instead of the session, and add a recorded `SETTLED` disposition for human and consult decisions, so earlier findings and dispositions survive a handoff, a next-day session, or `/clear`.

Why now: on a multi-day branch that crossed many handoffs, specialists re-raised findings the engineer believed were already settled. The ledger file is `$LEDGER_DIR/$REPO_HASH.$SESSION_ID.jsonl`, so a new session sees an empty ledger. Round counting restarts, `--source` anchors for keep-current-text and DEFER sites are lost, pre-PR deferred findings live only in chat, and three other readers (dashboard, compliance log, `author_outcome.py`) are session-keyed.

Intended outcome (issue acceptance):
- (a) After a handoff, next-day session, or `/clear`, `review-ledger.sh show` lists the branch's earlier findings and dispositions.
- (b) A repeat finding at a settled site reaches the settled-site route in the new session, including DEFER and keep-current-text sites.
- (c) An engineer's decision recorded in one session suppresses a same-failure-mode re-raise in a later session and appears in the PR body.

Engineer answers (Step 4 and plan review):
- Scope: "All phases, sequenced (Recommended)". The Phase 3 answer below confirmed the fold-in as a follow-up, so this plan ships phases 0–2 in sequence.
- Kill switch: "Remove it (Recommended)".
- Branch-name reuse: "I don't know. Ask plan-architect" (delegated; row 13).
- Phase 3, folding the `/ready-for-review` disposition record into the ledger: first "Ask plan-architect" (delegated), then "Yes, follow-up is fine (Recommended)" when asked to confirm that 2b ships the canonical-store statement and the fold-in is a follow-up (row 24).
- Carry-forward: "I think A makes sense" (option A), with a request that plan-architect first verify `/ready-for-review` and the ledger script. Row 18 records the resulting design.
- Carry range: "Add the cited-line check (Recommended)" (row 41). The option's description was the orchestrator's proposal; only the label is the engineer's.
- Failure scenarios: the engineer's quoted words say scenarios 1–3 occur and defer 4–6 (rows 13, 15, 21, 33, 38 and Out of scope). That 1–3 get the mechanisms the Approach names is this plan's reading (row 29).

## Approach

The review ledger is keyed by branch, using the round-3 gate's `<repo-hash>.<branch-hash>` key. It falls back to a per-session file on a detached HEAD or the default branch. Every row carries its `session_id`, so a new session on the branch reads the branch's history.

On top of that storage, the design adds:
- a script-validated `SETTLED` disposition;
- one-hop `--ref` links between rows;
- a script-computed hash of each decided block;
- an affirmative `--carry-forward` attribute on engineer decisions.

Together these let a repeat finding be logged as a carry, with no new stop, when the script accepts it. Acceptance needs three things: the decision is a live DEFER or a live engineer SETTLED logged `--carry-forward`; the reviewer's cited line lies inside the carry's range; and that range's text hashes the same as the decided block. `review-ledger.sh render` then builds the PR-body block from the ledger rather than from chat output.

### Failure scenarios weighed

The engineer's quoted words refer to these by number:
1. **Site drift.** A decision is applied to text that changed after the decision was made. **Handled by:** the site hash and the cited-line check (rows 38, 39, 41).
2. **Decision bookkeeping.** An earlier decision's words are presented as fresh speech, decisions are miscounted, or a decision cannot be retracted. **Handled by:** `--decided-by` and ids with `--ref` (rows 15, 33).
3. **PR-block replacement.** One round's rewrite of the delimited block drops rows that other rounds or sessions wrote. **Handled by:** `render --pr-json` (rows 21, 42).
4. **Append-time content validation.** Control characters, comment tokens, or a credential appear in free text. **Deferred** (Out of scope).
5. **Branch-name reuse.** A deleted branch's rows resurface under a recreated branch of the same name. **Deferred** (row 13, Out of scope).
6. **Two-branch sessions.** The analysis's round join has to place a session that reviews on two branches. **Deferred** (row 11, Out of scope).

### Phases and PR boundaries

- **Phase 0: budget.** It has no dispatch of its own; it constrains every commit.
  - In each commit, `code-review/SKILL.md` must be net ≤ 0 lines against its parent commit (row 30).
  - 2b pays for its additions by consolidating DEFER persistence (`:414-428`) into three paragraphs (row 14).
  - `ready-for-review/SKILL.md` stays ≤ 200 lines and `pr-description/SKILL.md` ≤ 250, and every edit to them is line-neutral.
  - `code-review/SKILL.md` grows by at most 3 KB across 1c and 2b (Verification).
  - **Fallback:** if a commit cannot be net ≤ 0 lines, or 2b's cumulative byte delta measures over 3 KB, the dispatch stops before committing and returns. The engineer then chooses among the issue's offsetting cuts, its extraction option, moving more rules into script messages, or a cap change. The dispatch never improvises cuts.
- **Phase 1: branch-scoped storage and every reader.** Dispatches 1a → 1b → 1c.
  - Satisfies acceptance (a).
  - Makes DEFER and keep rows visible to later sessions. Routing a repeat finding to them lands in phase 2.
  - **1a does not stand alone.** Its writer moves to branch files while `require-code-review.sh` and the dashboard still read session files. Their outputs then go `absent` or silent until 1b. 1a and 1b ship in the same PR, never separately.
- **Phase 2: `SETTLED`, row links, site hash, `render`.** Dispatches 2a → 2b.
  - Satisfies (b) routing and (c).
  - 2b also records which store is canonical (row 24).
  - 2a and 2b must ship together: 2a's script requires a grammar-valid `--source` and `--defer-criterion` on DEFER, which today's skill text does not pass.
- **Order and PRs:** strictly serial, one commit per dispatch (row 37), one PR on this branch.

### Carry-forward: option A, guarded by a block hash and the cited line (rows 18, 38–41)

- **The engineer favored A:** "I think A makes sense". Their answer asked for the `/ready-for-review` and ledger-script verification first; this design commits to A after it.
- **A diff-based guard fails in both review timelines.**
  - A commit-gate round reviews only the staged delta, so it cannot see an edit an earlier commit made to the block after the decision.
  - An RFR cumulative pass diffs the whole branch against its base. Every block the branch wrote sits inside that diff, so nothing would ever carry.
- **The guard.** When a DEFER or SETTLED row names a line range, the script hashes that block's text. It accepts a carry only when two things hold:
  - the reviewer's cited line lies inside the carry's own range;
  - that range hashes the same as the decided block.

  The guard only removes carries that A would otherwise make; it never adds a suppression.
- **Carry eligibility fails closed.**
  - An engineer decision carries only when logged `--carry-forward`. The orchestrator passes that flag only for a non-invariant finding whose carry-forward the engineer did not decline, so a forgotten flag costs a stop.
  - A DEFER carry restates its criterion. A forgotten or changed criterion sends the repeat to a fresh disposition (rows 19, 40).
- **Carries are the orchestrator's decisions.**
  - They are logged `--decided-by carry`, with the shared defect as rationale and never with the engineer's words.
  - Each carry renders under the decision it applies, marked orchestrator-matched.
  - The round report relays the script's carry line, which names the decision and "reopen <id>".
- **Why a content hash is sound here.**
  - The orchestrator names the block once, at decision time (row 39). At a carry, the reviewer's cited line must sit in text that hashes the same (row 41).
  - The hash reads the working tree, so staged-but-uncommitted settled text hashes the same after it is committed and after a rebase. Under partial staging it also binds unstaged edits (row 38).

### Decisions delegated to this design

**Branch-name reuse is an accepted residual, not filtered (row 13).**
- **Ancestry filtering fails.** This repo's default-branch sync is a rebase, which `/ready-for-review` step 1 runs whenever the branch is behind. Every row recorded at a pre-rebase commit would fail the ancestor test.
- **A reflog creation filter is not worth its cost.** It needs a helper, four call sites (`show`, `render`, `--ref` validation, the dashboard), and reflog behavior this plan has not verified. The engineer judged reuse very unlikely and deferred it.
- **Its reach.** Old rows reach `show`, `render`, `--ref` validation and the dashboard's counts. Round numbering continues from the old branch's maximum.
- **Mitigations.** Each rendered row carries its date, `show`'s header names the oldest row's date, and `docs/hooks.md` carries a reset line.

**The canonical-store statement ships in 2b; folding the RFR record into the ledger is a follow-up (row 24).** The engineer confirmed the follow-up: "Yes, follow-up is fine (Recommended)".
- **Why not fold in now.** The RFR record still carries four things the ledger lacks:
  - which rounds were cumulative passes;
  - cap rows;
  - which reviewer raised a row;
  - the per-row Outcome column.
- **What folding in would cost:** three schema additions, plus a rewrite of the test-pinned Cap.
- **What it would gain:** ordering by a script-stamped `event_time`, and an exact branch key. That payoff is real, so the follow-up is named rather than rejected.
- **What would change this call:** evidence that duplicated rows drift between the two stores in practice.

### Alternatives considered

- **A prose-only design**, where skill text alone judges site identity and supersession. It leaves scenario 1 to model judgment (row 38).
- **Per-session round numbering plus `session_id`.** Each session on a branch would restart at round 1, so the round shown beside a decision in `render` and at the stop would not place it on the branch. Branch-wide numbering costs the analysis nothing under rank mapping (row 11).
- **A time-window analysis join** (row 11).
- **Ancestry or reflog-creation filtering** for branch-name reuse (above).
- **Append-time free-text validation.** `render`'s cell escaping covers the table and delimiter risks (row 21), and the row-size check covers the atomic-write bound (row 36). The rest is scenario 4, which is deferred.
- **A diff-based "site untouched" guard** for carry-forward (row 18).
- **A whole-file hash, a commit anchor, a whole-file search, or hashing the index** for the site check (row 38).
- **Deriving the site range mechanically** (row 39), or **a minimum range size** (row 39).
- **Carryable by default, closed by a prose class test or a per-carry attestation flag** (row 40).
- **Re-quoting the engineer on each carry** (row 33).
- **Moving carry validation inside the append lock** (row 33).
- **Rendering the block in skill prose** (row 21).
- **A shell redirect for the PR-body file, `gh pr view -q .body`, or a script-internal `gh pr edit`** (row 42).
- **Folding the RFR record in now** (above).
- **Extracting the Item-ownership table** (row 14).

### Assumption ledger

**Root:** the `/code-review` ledger is keyed per session. A new session on a long branch therefore sees none of the branch's earlier findings, dispositions or `--source` anchors, and a human's keep decision has no recorded disposition at all. Settled findings get re-raised and re-decided.

**Givens:**
- **G1. The harness owns session identity.** This repo cannot carry a session id across `/clear`, a new start, or a next-day session.
- **G2. Ledger rows are self-attested by the orchestrator.**
  - The cooperative threat model in `CLAUDE.md` § "Hook threat model" is a repo-wide policy for every gate, outside this plan's reach.
  - The harness gives the orchestrating agent write access to its own config dir, so nothing in this repo can make a row unforgeable by the agent that writes it.

  [verified: `CLAUDE.md` "Hook threat model"; design doc `ready-for-review-fix-loop-convergence.md:27`]

**Rows:**

1. **The ledger file key is `_lib_reviewer_round_state_key`'s `<repo-hash>.<branch-hash>`.** This gives one "this branch" key shared with the round-3 gate. The branch half is a sha256 of the full branch name, so there are no slug collisions. anchors: root. [verified: `_lib.sh:3458-3469`]

2. **The repo half is worktree-path identity.** The same branch at a different path starts a fresh ledger.
   - The worktree flow keeps one worktree per branch, at a path that embeds the branch name.
   - For a consumer without worktree enforcement, the repo half is the checkout path, so only the branch name tells branches apart (row 13).

   anchors: row1. [verified: `_lib.sh:509-514`] [unverified: that re-checking a branch out at another path is rare]

3. **Fallback to `<repo-hash>.<session-id>.jsonl`.** It applies in three cases:
   - on a detached HEAD, which includes mid-rebase;
   - on the branch `_lib_default_branch_or_guess` resolves;
   - when no default resolves, on a branch named like one of that function's candidates (main, master, develop). This keeps a local-only `main` from growing one unbounded file.

   The candidate list moves into one `_lib.sh` array constant that both callers use. `_lib_default_branch_or_guess` sits on gate paths, so its existing tests run unchanged.

   Residuals:
   - A branch named like a candidate is still branch-keyed when `origin/HEAD` names a different branch.
   - Long-lived non-default branches (`release/*`, `staging`) are branch-keyed like any feature branch.

   anchors: row1. [verified: `_lib.sh:925-939`, `:932`, `:3458-3469`]

4. **Mid-rebase rows land in the session file,** visible only to that session.
   - While HEAD is detached, `show` also reads only that session file, so the branch's rows are hidden until the rebase ends.
   - This is accepted and pinned by a test.
   - Reading `rebase-merge/head-name` instead would make the ledger's key diverge from the gate's shared key.

   anchors: row3. [verified: `_lib.sh:3452-3457`, `:3494-3495`]

5. **Every row carries `session_id`, and the dedup key includes it** (schema v3). Readers compare it only for equality. A row whose id fails `_lib_valid_session_id_component`'s pattern is ignored. anchors: root. [verified: `review-ledger.sh:310-316`, `:329-330`; `_lib.sh:2125-2128`]

6. **Round numbers become branch-scoped and stay caller-supplied.**
   - At a session's first round, and after compaction or resume, `/code-review` reads `show`'s header and continues from one past its max round.
   - The analysis maps round-opens to round blocks by rank, not by value (row 11).

   anchors: row1. [verified: `code-review/SKILL.md:25`; the script keeps no counter; `review-ledger.sh:18` describes the counter as session-scoped]

7. **`show` reads a deduplicated file set.**
   - The set is the resolved file plus this worktree's own `$REPO_HASH.$SESSION_ID.jsonl`. Deduplication matters because the resolved file is itself a session-file match in session scope.
   - Rows go to stdout. One header line on stderr names:
     - the scope;
     - the file or files read;
     - the row count;
     - the oldest and newest `event_time`;
     - the max round.
   - A session's rows in other branches' files are not shown: they are those branches' history. `author_outcome.py` does read them, because it measures sessions.
   - The all-repo-hash session glob goes.

   anchors: row3. [verified: `review-ledger.sh:340-383`, `:346-347`]

8. **A `_lib.sh` resolver prints `<scope> <path>`.**
   - The path comes last, so a config dir containing a space still parses (`${out%% *}` for the scope, `${out#* }` for the path).
   - Its callers are `review-ledger.sh` (append, show, render) and `session-marker-dashboard.sh`. The compliance log does not need it (row 10).

   anchors: row3. Lighter options, and why each fails:
   - Restating key-plus-fallback in the dashboard breaks single source of truth, and the default-branch rule would drift.
   - A `review-ledger.sh path` subcommand called from the hook costs a process on every SessionStart. It also reverses the repo's direction, where scripts source the hooks' `_lib.sh`.
   - **Why `_lib.sh` here:** a hook (the dashboard) needs the resolver, and `_lib.sh` already holds the ledger's shared path and lock helpers. That is the seam rule's item 1. The validation and render bodies go elsewhere, because no hook calls them (row 34). [verified: `.claude/rules/bash-unit-test-seams.md` item 1; `_lib.sh:3688`, `:3755`]

9. **The dashboard summarizes the resolved file.**
   - It shows addressed, deferred and settled counts "on this branch" (or "this session" in session scope), plus the date span.
   - It counts rows by disposition, so carries and id-less rows from before schema v4 count too.
   - SETTLED joins the `> 0` gate.
   - It resolves from the payload cwd, so a session that later anchors in another worktree gets no branch summary. The summary is advisory; acceptance (a) rests on `show`.

   anchors: row8. [verified: `session-marker-dashboard.sh:91-113`, `:107`]

10. **The compliance log needs no resolver.** It reports `ledger=present` when either:
    - this worktree's session file exists; or
    - a fixed-string match on `"session_id":"<id>"` hits a `$REPO_HASH.*.jsonl` file.

    That means zero git calls on a security gate's path. jq escapes an embedded `"` as `\"`, so the literal cannot match inside a finding's text; a precision test pins this. anchors: row5. [verified: `require-code-review.sh:149-157`; `docs/hooks.md:114`; rows are built with `jq -nc` at `review-ledger.sh:310`]

11. **`author_outcome.py` attributes rows by `session_id` and maps round-opens to round blocks by rank.**
    - **Attribution.** A row belongs to session S when its `session_id` is S, or when it has no `session_id` and sits in `*.S.jsonl`.
    - **Blocks.** Take S's rows that have an integer, non-bool `round`. Concatenate them in filename order and stable-sort them by `event_time`, as `:236-243` does today. Then group maximal runs of one (file, round) pair, which is today's `groupby` at `:282`.
    - **Mapping.** The k-th round-open classifies from the k-th block's rows. `_classify_round` takes that round's own rows instead of matching `round == k` (`:348-351`).
    - **Mismatch.** S is excluded from the headline, as today, when S has a round-keyed row and either:
      - the block count differs from the round-open count; or
      - block round values are not strictly increasing.
    - **Consequences of that predicate:**
      - Every `TestRoundNumberMismatch` case keeps its expected result, so `_round_number_mismatch` keeps its name, its signature and its tests.
      - A round with no rows makes the count short and excludes the session. That is today's behavior (`test_gap_in_sequence_is_a_mismatch`, `:353-357`), not a new cost.
      - Two files claiming one round always repeat a round value, so today's cross-worktree race guard holds. A new unit case pins the strictly-increasing arm, which no current case discriminates.
      - A session whose round values fall when it moves to a second branch (A:5, then B:1) is excluded. That is scenario 6, deferred.
    - **Mismatched sessions are not classified.**
      - Their dispatches are already off the headline.
      - Their rounds still get entries for transcript-side counting, but `_classify_round` does not run for them.
      - Two counters change for mismatched sessions:
        - `_DQ_KILL_SWITCH_INFERRED_CLEAN` stops counting their marker-write rounds;
        - `_DQ_AUTHORING_AGENT_INCONSISTENT` stops reading their rows.
      - Both read the round-to-row join that the mismatch marks untrusted. Classifying a mismatched session's rounds as row-less would count every marker-write round as "no ledger row", whether it had rows or not.
      - `_DQ_UNDECIDABLE`, `_DQ_CO_AUTHORED_ROUNDS` and `_DQ_MALFORMED_DISPATCH_ID` are unchanged.
    - **Positional-join residual.** Suppose one round's rows are missing and a stray block with a higher round value appears. Count and order still match, so the join misattributes silently where today's value join flagged a mismatch. This is accepted and documented in `docs/transcript-analysis.md`.
    - **Index.** It is kept per config root and refreshed on each session lookup: re-list the directory, and re-parse only files whose `(mtime_ns, size)` changed. The directory listing is its own function, which is the seam for the vanished-file test.

    anchors: row5. Lighter options, and why each fails:
    - Today's value join (`round == k`): a later session on a branch starts at round N+1 (row 6), so every later session would mismatch.
    - A time-window join, which places each row by its `event_time` between consecutive round-opens. It would admit two-branch sessions and confine a dropped round to itself. In exchange, it assumes transcript timestamps and `event_time` read one clock, needs tie rules at 1-second resolution, and adds three placement conditions. Those gains serve a case the engineer judged unlikely (scenario 6) and narrow a behavior today's analysis already has.
    - Counting a mismatched session's marker-write rounds by value (`round == k`) alone: two join semantics in one module, and under branch numbering the value rarely matches the ordinal.
    - For the index, a snapshot taken once would regress the freshness guard at `test_author_outcome.py:818-847`.
    - Also for the index, re-reading every file per session costs sessions × ledger bytes.

    [verified: `author_outcome.py:66`, `:164-244`, `:247-284`, `:329-364`, `:461-554`; `test_author_outcome.py:343-501`, `:622-677`, `:706-848`, `:1425-1450` (one round-2 row against one open, asserting a mismatch at `:1444`), `:1452-1480`, `:1703` (patches `_ledger_files_for_session`)]

12. **Remove the kill switch.**
    - It touches:
      - `review-ledger.sh:177-181` and usage `:41`;
      - `session-marker-dashboard.sh:92` and header `:28-31`;
      - `docs/hooks.md:175`;
      - the KillSwitch tests.
    - `author_outcome.py`'s kill-switch wording (`:66`, `:173-177`, `:344-346`, `:357-362`) becomes "every append for the round failed". The `:66` label becomes "rounds with a marker write but no ledger row (every append for the round failed)".
    - While the old sentinel file exists, the dashboard prints one line. It says:
      - `.review-narrative-ledger-disabled` is no longer honored, and the review ledger always records;
      - engineer quotes logged at review stops now reach PR bodies;
      - there is no replacement opt-out;
      - deleting the file silences the line.
    - `docs/hooks.md` and the PR body say the same.

    anchors: root. [engineer-verified: "Remove it (Recommended)"] [verified: no `config-keys.psv` row exists for it]

13. **Branch-name reuse is an accepted residual, not filtered.**
    - A branch deleted and recreated under the same name at the same path resolves to the same file.
    - Its old rows therefore appear in `show`, `render`, `--ref` validation and the dashboard's counts. Round numbering continues from the old maximum.
    - Mitigations the design already carries:
      - each rendered row's date (row 21);
      - `show`'s oldest-row date (row 7);
      - the reset line in `docs/hooks.md`, which tells the engineer to delete the file `show`'s header names.

    anchors: row1. [engineer-verified: "I don't know. Ask plan-architect" — a delegation only] [engineer-verified: "#5 is very unlikely because branch names have auto incremented ticket numbers in them usually and would be pretty easy to detects. I'd say defer those scenarios"] [verified: rebase default at `git-feature-branch-sync/SKILL.md:46,49`, invoked from `ready-for-review/SKILL.md:44`]

14. **Phase 0 extracts nothing.**
    - The DEFER-persistence rules move into `render` (row 21). 2b therefore cuts `code-review/SKILL.md:414-428` from 7 paragraphs to 3 (−8 lines) in the same commit as its additions. Every other edit stays inside an existing paragraph.
    - Extraction exists only as ROUTING.md's last-resort exception, which needed two compensating read-gate hooks. [verified: `docs/skills.md:145`]
    - Co-located files may not route around a length cap. [verified: `.claude/rules/skill-and-agent-self-review.md`]
    - The fallback is named in "Phases and PR boundaries".

    anchors: row30. [engineer-verified: "All phases, sequenced (Recommended)". This covers shipping every phase in sequence, not how phase 0 is delivered.] [unverified: that the issue offers offsetting cuts or extraction for phase 0, per the issue text as relayed]

15. **`SETTLED` disposition with `--decided-by engineer|plan-architect|carry`** (schema v4).
    - **Deciders.**
      - `engineer` requires `--engineer-quote`. The quote is verbatim, or a verbatim excerpt that keeps its qualifying clauses. It is capped at 200 characters and rejected, not truncated, when over.
      - `plan-architect` forbids a quote.
      - `carry` is the orchestrator's decision (row 18).
        - It requires `--ref`, a range-form `--source`, `--cited-line` (row 41), and a `--rationale` naming the shared defect.
        - It forbids a quote, `--enforcement-invariant` and `--carry-forward`.
        - A DEFER carry requires `--defer-criterion` (row 19); a SETTLED carry forbids it.
      - DEFER accepts only `carry`. ADDRESS and CLEAN accept no `--decided-by`, and `--engineer-quote` without `--decided-by engineer` is rejected everywhere.
    - **Finding and rationale** stay required on every non-CLEAN disposition, SETTLED included.
    - **Source.** Required on DEFER and SETTLED, as `<repo-relative path>[:<start>[-<end>]]`.
      - `<start>` and `<end>` are 1 to 9 digits with no leading zero, and `<start>` ≤ `<end>`.
      - A source containing `:` must end in such a range. `path:`, `path:0`, `path:5-3`, `path:08-09`, `path:1-` and `path:abc` are rejected.
      - An absolute path under the repo root is stored repo-relative.
      - Any other absolute path, or a `..` segment, is rejected, because the site hash reads the file.
      - Today's 200-character cap applies.
      - A range-form source gets a site hash (row 38). A path-only source is accepted, but its decision never carries.
      - ADDRESS keeps today's free-text source; no reader parses it.
    - **Criterion.** DEFER requires `--defer-criterion`, one of the closed list's five names, carries included.
    - **Free text is not filtered at append.** `render` escapes every cell (row 21), and the row-size check bounds the line (row 36).

    anchors: root. Lighter options, and why each fails:
    - Today's ADDRESS plus rationale: `author_outcome` counts it as FAILURE, and `show` cannot tell a keep from a fix. [verified: `code-review/SKILL.md:378`; design doc `:82`]
    - A "settled" DEFER criterion: DEFER is a closed list for real defects, and enforcement-invariant findings are never DEFER-eligible. [verified: `code-review/SKILL.md:380-396`]
    - The quote inside `--rationale`: this mixes agent prose with the engineer's words, which `CLAUDE.md`'s attribution rule forbids.

    [engineer-verified: "I'm pretty sure I've seen #2 scenarios as well #3 scenarios as well". This covers only that scenario 2 occurs; the mechanism is this design's.] [verified: `review-ledger.sh:186` (`--source` defaults to `n/a`), `:241-255` (per-disposition flag rules), `:268-281` (caps); no reader parses `source` (grep of `author_outcome.py` and `session-marker-dashboard.sh`)]

16. **`--enforcement-invariant` is a label with local checks, not a gate.**
    - It records the orchestrator's `:396` classification at disposition time.
    - **Check 1:** the label is accepted only on `SETTLED --decided-by engineer`, which always carries a quote.
    - **Check 2:** the label is rejected beside `--carry-forward` (row 40).
    - **Check 3, the successor rule for every engineer decision:**
      - a non-carry row whose `--ref` names an engineer decision must be an ADDRESS or an engineer SETTLED;
      - that successor must carry the label when the decision does.

      A DEFER or a consult SETTLED therefore cannot retire an engineer's words from the PR body.
    - Labelled decisions render under their own heading.
    - Omitting the label does not make a decision carryable, because carrying needs `--carry-forward` (row 40). Omitting it only moves the row out of the invariant heading.

    anchors: row15. Lighter options, and why each fails:
    - Classifying rows from finding text at render time is an unvalidated judgment made in a later session.
    - Having the raising reviewer classify would change every reviewer's return format, and `:396` already places classification at disposition. [verified: `code-review/SKILL.md:396`]

17. **Today a human's keep at a blocking stop-and-ask has no resolved disposition.**
    - `:481`'s resolved list becomes "a finding logged DEFER or SETTLED, by a consult, the human, or a carry".
    - **What the script checks about invariant findings, exactly.**
      - It accepts `--enforcement-invariant` only on an engineer SETTLED.
      - It rejects `--carry-forward` beside the label.
      - It rejects a successor that drops the label.
    - **What it does not check.** It never checks a finding's class. An invariant finding logged as a consult SETTLED, or as an engineer SETTLED without the label, passes the script, and `:481` counts it resolved. Only the prose of `:378` and `:396` forbids a consult keep of an invariant finding. That is also true today: `:481` already counts a consult *keep current text* as resolved.
    - RFR counts a pass clean by that same `:481` rule, and the Cap counts only records. A pass whose findings were all carried is therefore clean and adds nothing toward the Cap. A narrower wording would leave carried passes dirty, which blocks the review marker and feeds the Cap.
    - The RFR record's Outcome column gains `SETTLED <id>` and `carry <id>`, because a SETTLED row has no fix route, verdict or criterion to record.

    anchors: row15. [verified: `code-review/SKILL.md:378`, `:396`, `:481`; `ready-for-review/SKILL.md:84`, `:86`]

18. **Engineer carry-forward: option A, with a hash guard.**
    - **Carry.** It applies when a finding names the same failure mode as a live engineer decision logged `--carry-forward` (row 40).
      - First apply `:396` to the new finding. An invariant-class finding never carries; it takes `:396`'s route.
      - The carry is logged `SETTLED --decided-by carry --ref <decision id>`. The row holds:
        - the new finding;
        - `--cited-line` from the reviewer's finding (row 41);
        - a `--source` naming the block that contains that line;
        - a `--rationale` naming the shared defect.
      - The script accepts it only under row 33's carry rule. It then prints one carry line: the decision's id, date, round and quote, the carried finding, and the reopen command. The round report relays that line, with no new stop.
    - **Stop instead.** The settled-site stop fires on any of four triggers:
      - a script rejection;
      - a different failure mode;
      - an uncertain match;
      - an invariant-class new finding.

      The stop names which trigger applies, relaying the script's reason for a rejection. It shows the earlier decision's words, date and round.
    - **Every stop whose keep is logged as an engineer SETTLED** tells the engineer two things before they answer. These stops are the settled-site stop, *cannot choose*, the no-verdict stop, the heavier-mechanism rule's disagreement stop, and `:396`'s stop. The two things:
      - the answer will be quoted in the public PR body, where tracker IDs, UUIDs or long hex runs, home paths and blocklisted names will block the PR update;
      - except at `:396`'s stop, the keep will apply without asking to repeats of the same failure mode on this unchanged block, unless the engineer declines. At `:396`'s stop, every repeat asks again.
    - **How a keep is logged.** A keep is logged as an engineer SETTLED with:
      - the quote;
      - a fresh range;
      - `--ref` to any earlier decision;
      - `--carry-forward` unless the engineer declined or `:396` covers the finding.

      The script prints the stored quote, and the orchestrator relays it.
    - **Never carried:**
      - consult-decided, superseded or closed decisions;
      - decisions with a path-only source;
      - engineer decisions without `--carry-forward`, which include every invariant-labelled one.

      The script enforces this through rows 33 and 40.
    - **Disclosure.**
      - The round report relays each carry line with "reopen <id>". The engineer's reopen is logged as `ADDRESS --ref <id>`.
      - `render` prints each carry under its decision, marked orchestrator-matched (row 21).
    - **Reviewers still raise the finding.** "Suppresses" in (c) means no new stop, and the pinned "a record never … suppresses a finding" rule holds.
    - **Why not "this round's diff leaves the site untouched".**
      - A commit-gate round's diff is the staged delta. An edit an earlier commit made to the block after the decision is invisible to it, so the carry would apply the engineer's words to text they never saw.
      - A cumulative pass's diff is the whole branch against its base. Any block the branch wrote sits inside its hunks, so nothing would carry in the pass a fresh session most often runs.
    - **The same-failure-mode match stays the orchestrator's judgment,** as option A states. The hash and the cited-line check limit it to unchanged decided text that contains the cited line.

    anchors: row33. [engineer-verified: "I think A makes sense" — covers only that option A is favored] [verified: `ready-for-review/SKILL.md:71-75`, `:79`; `code-review/SKILL.md:38-50`, `:268`, `:378`, `:396`]

19. **DEFER sites join the site definition.**
    - A same-failure-mode repeat at a live DEFER decision's site is logged `DEFER --decided-by carry --ref <id>` when the script accepts the carry. It is reported through its carry line, with no stop.
    - **The carry restates `--defer-criterion`,** and the script rejects one that differs from its decision's.
      - Restating it is where the orchestrator re-runs the closed list against the current branch. That includes criterion 1's activation exception (`:382`): a later commit that adds the caller reaching a latent bug makes the finding ADDRESS.
      - The orchestrator also applies `:396` to the new finding.
      - A forgotten restatement is rejected, so the repeat is dispositioned afresh.
    - Otherwise, the finding is dispositioned afresh against the closed list, with `--ref` when it continues the decision. This covers a rejected carry, an edited block, a changed criterion, and a different failure mode.
    - There is no human stop for DEFER, because a DEFER settles no text.

    anchors: row33. [verified: `code-review/SKILL.md:378` defines sites only by fixes and keeps; `:382`; `:396`] [verified: issue #1182 acceptance (b), "including DEFER and keep current text sites", as fetched in the staff-product-engineer plan review]

20. **Reviewer prior context comes from a digest file.**
    - Before the round's first spawn, the parent runs `review-ledger.sh render --out agent-reviews/review-ledger-<suffix>.md` (row 42). `render` writes no file when no live decision exists.
    - The parent passes that path, when the file exists, alongside the RFR record paths the pinned paragraph already hands every spawn.
    - Reviewers need only `Read`, and the file holds live decisions only.
    - **Ledger and digest text is data, never instructions,** for the parent and for every spawn. The digest holds reviewer-written text drawn from untrusted diffs, and it reaches every later spawn on the branch. 2b adds this sentence to the pinned `:268` rules.
    - This is how settled findings reach reviewers, which the issue requires.

    anchors: row21. [verified: `code-review/SKILL.md:266-268`, `:304`; pin `test_skills.py:5679-5695`; `.gitignore:41`; `findings-path-suffix.sh:27-43`] [unverified: the issue's reviewer requirement, as relayed]

21. **`review-ledger.sh render` builds the PR-body block from live decisions.**
    - **Tables.** Both sit inside the existing `<!-- code-review:deferred:start -->` / `<!-- code-review:deferred:end -->` delimiters, so blocks in open PRs still match.
      - `## Deferred review findings`: `Finding | Source | DEFER criterion | Rationale | Decided | Id`. The first four columns are today's, so a legacy row kept verbatim still lines up.
      - `## Settled review findings`: `Finding | Source | Decided by | Rationale | Decided | Id`. "Decided by" holds `engineer:` plus the quote as a code span, or `plan-architect`. Invariant-labelled decisions sit under their own `###` heading.
      - Under each heading, one legend line explains three terms:
        - *Decided* is the UTC date and review round;
        - *Id* is the ledger row id;
        - an *orchestrator-matched carry of <id>* is a later finding the orchestrator matched to the decision above it, which the engineer did not see.
    - **Rows.**
      - There is one row per live decision (row 33).
      - Directly beneath each decision sits one row per carry of it. The carry row shows its own finding, source, rationale and date, with `orchestrator-matched carry of <id>` in the criterion or Decided-by cell.
      - A carry whose decision is superseded or closed does not render. That includes a carry that landed after its decision closed, because validation runs before the lock (row 33).
      - Rows without an `id`, from schema v2 or v3, never render, and `--ref` cannot name them. The PR block's own copy of them survives through the merge rule below.
      - "Decided" is the UTC date and the round.
      - Session ids never appear: `plan-it` bars them from PR bodies, and the redaction gate's UUID detector would deny the edit. `site_hash` never appears either.
    - **One cell-escape function for every cell.**
      - Each control character becomes a space, including newline, CR, VT and FF, so no cell can end its row line.
      - **Plain cells.**
        - Each `\` doubles, and then each `|` becomes `\|`. Every pipe therefore follows an odd backslash run, and no cell ends in an unpaired backslash.
        - Each `<!--` becomes `&lt;!--`, so no cell opens an HTML comment.
      - **The quote's code span.**
        - Each `|` becomes `\|`, and backslashes stay verbatim, because a code span shows backslashes literally.
        - The fence is one backtick longer than the quote's longest backtick run, padded with one space inside each side. The quote therefore renders verbatim, except that control characters show as spaces.
      - The renderer writes a space before every separator pipe.
      - No escape creates a backslash-backtick pair that was not already in the text.
    - **`render --pr-json <file|->`** prints a whole PR body (input and output handling in row 42).
      - Every byte outside the block stays identical, including any trailing newlines.
      - **Delimiter recognition.** A delimiter counts only as a whole line outside a fenced code block, ignoring a trailing carriage return. A fenced example that quotes the delimiters stays outside-body text. A cell always sits inside a `|`-led row line, so it can never end the block.
      - **The one merge rule:** a data row in the old block whose last cell is not the id of a ledger row is kept in the table it sat under, with each `<!--` neutralized. This keeps legacy rows, swept rows, and rows a session-scope read cannot see. Only a hand edit of the PR body retires such a row.
      - Re-rendering `render`'s own output is a fixed point: it adds no duplicate rows.
      - The block is replaced, or appended when absent. It is dropped when it would hold no rows.
      - `render` exits non-zero with no output file on any of: zero bytes of input, a failed ledger read, an unpaired delimiter, or two blocks.
    - **When it runs.**
      - `/code-review` runs it when a round logged a DEFER, SETTLED or `--ref` row, or when the body already holds the block.
      - `gh pr edit` runs only after `render` reports `changed:` (row 42).
      - With no PR open, RFR step 5 runs `render` itself, which also covers a step-3 cache hit.

    anchors: row33. Lighter options, and why each fails:
    - Today's chat-returned block is lost on a step-3 cache hit or a handoff, and each round replaces it with that round's DEFERs only.
    - Rendering in skill prose cannot be tested, costs lines `code-review/SKILL.md` does not have, and invites paraphrasing a quote.
    - Failing on any delimiter inside a fence still needs fence tracking, so it costs as much as ignoring fenced delimiters while blocking a legitimate body.

    [engineer-verified: "I'm pretty sure I've seen #2 scenarios as well #3 scenarios as well". This covers only that scenario 3 occurs.] [verified: `code-review/SKILL.md:416-428`, delimiters at `:422`; `ready-for-review/SKILL.md:68`, `:112-113`; `pr-description/SKILL.md:124`, `:131-135`; `plan-it/SKILL.md:144`; `deny-private-project-refs.sh:29-36`; `deny-escaped-backticks-in-pr-body.sh:4-9`; `_lib.sh:3128`] [unverified: how GFM splits a table cell holding `\|` inside a code span, or an even backslash run before `\|`; that GFM pads a 4-cell row under a 6-column header with empty cells; CommonMark's fence rules (three or more backticks or tildes, indented at most three spaces, closed by the same character at least as long), as recalled; that `gh pr view` can return CRLF line endings; that every block in an open PR puts each delimiter on its own line]

22. **Engineer quotes reach a public PR body.** Two controls apply:
    - the stop's notice that the answer will be quoted, which names what the redaction gate rejects: tracker IDs, UUIDs or runs of 32+ hex digits, home paths, and blocklisted names (row 18);
    - the redaction gate on `gh pr create|edit`. It catches those shapes, plus IPv4 literals, SSH paths, internal hostnames and Slack references, and project names when the blocklist is populated.

    That gate does not run `_LIB_CREDENTIAL_VALUE_REGEX`; only `deny-pii-in-commits.sh` does, and only on commits. A credential check at append is deferred (Out of scope).

    **Recovery depends on whether a gate saw the text.**
    - **Text a PR-body gate catches.** The `gh pr edit` denial comes before publication, and row 35's superseding row recovers.
    - **A credential no gate catches.** The body is already published.
      - `ADDRESS --ref` only stops later renders.
      - The row stays in the ledger file and in `show`, and GitHub keeps the prior body revisions.
      - The session stops and tells the owner. The owner rotates the credential and deletes the revision. `CLAUDE.md` § "Secrets, tokens, credentials" keeps both steps with the owner.
      - The `docs/hooks.md` runbook line and the PR-body disclosure say this.

    Running the redaction detectors at append as well is set aside:
    - the PR-body gate already runs them on the same text;
    - its denial is visible and comes before publication.

    anchors: row21. [engineer-verified: "Defer it too" — covers deferring a credential check on `--engineer-quote`] [verified: `deny-private-project-refs.sh:4-54`; `_LIB_CREDENTIAL_VALUE_REGEX` referenced only at `_lib.sh:2958`, `:2993` and `deny-pii-in-commits.sh:558`] [unverified: that GitHub lets a PR's author or a maintainer delete a description revision from its edit history]

23. **SETTLED counts as a pass, through a `_DISPOSITION_SETTLED` constant.**
    - **Truth table** for a round's rows. Carry rows count by their disposition.
      - `{SETTLED}`, `{SETTLED, DEFER}`, or a round of SETTLED carries only: PASS, and counted by the new counter.
      - `{DEFER}`, or DEFER carries only: PASS, not counted.
      - Any ADDRESS row, `ADDRESS --ref` included: FAILURE.
        - That is intended for a reopen, where the engineer now requires the fix.
        - A retract of published text (row 22) also classifies as FAILURE. That rare over-count is accepted and documented.
    - **The counter,** "rounds classified PASS with at least one SETTLED row", measures exactly the cutover. Before it, a consult keep logged as ADDRESS made such a round a FAILURE. `docs/transcript-analysis.md` records the cutover date.
    - Tests pair each truth-table line with its classification and counter value. The counter test fails before the change.

    anchors: row15. [verified: `author_outcome.py:352-355`]

24. **2b documents which store is canonical; folding the RFR record in is a follow-up** (reasoning under "Decisions delegated"). anchors: row15. [engineer-verified: "Ask plan-architect" — a delegation only] [engineer-verified: "Yes, follow-up is fine (Recommended)" — covers the fold-in being a follow-up while 2b ships the canonical-store statement] [verified: `ready-for-review/SKILL.md:84-86`; `code-review/SKILL.md:268`; the ledger has no reviewer field (`review-ledger.sh:310-316`); Cap pin at `test_skills.py:5412-5438`; design doc `:3`, `:29`, `:33`]

25. **Branches in flight start with an empty branch ledger.**
    - The first new session on such a branch re-decides its earlier findings once.
    - `render --pr-json` keeps the existing PR block's rows, because they carry no ledger id.
    - The PR block can then list a finding twice: its kept legacy row and its re-decided row. Only a hand edit of the PR body retires the legacy row.
    - This is disclosed in `docs/hooks.md` and in the PR body.

    anchors: row1. [verified: `require-architect-consult.sh:30-32`]

26. **The parent's routine read is bounded.**
    - Step 0.1 reads only `show`'s stderr header, sending the rows to `/dev/null`.
    - The disposition step reads `render`'s live decisions, and full rows are read only on demand.
    - Summary or filter flags stay out of scope.

    anchors: row6. [verified: the same `> /dev/null` shape at `ready-for-review/SKILL.md:99`] [unverified: row counts on long branches]

27. **Concurrent sessions can claim the same round number.** Their rows stay distinct through `session_id`, and the analysis attributes by `session_id` (row 11). anchors: row5, row6. [unverified: how often this happens]

28. **Issue #997 can read "this branch's last N rounds"** from the same file and round numbers. anchors: row1. [unverified: #997's text is not in the repo]

29. **Design choices made here, not by the engineer.** These are surfaced for confirmation when the plan is approved:
    - every mechanism row: 1–11, 13–26, and 33–42, including row 25's choice not to backfill;
    - the specific mechanisms for scenarios 1–3, since the engineer's quotes on rows 15, 21 and 38 cover only that those scenarios occur;
    - row 18's hash guard and `--decided-by carry`, since the engineer's quote covers only that option A is favored;
    - row 40's `--carry-forward` attribute, its opt-out consent at the stop, and the prose class test;
    - row 41's flag name, its cited-span form, and row 38's blank-only rejection. The engineer's label covers adding the cited-line check;
    - the mismatched-session counter change (row 11) and row 23's truth table;
    - the 3 KB byte ceiling (Phase 0, Verification);
    - treating scenario 6 as deferred, from the engineer's judgment that it is unlikely;
    - the dispatch split, carrying the canonical-store statement in 2b, and one PR;
    - the kill-switch touch points and notice text (row 12).

    anchors: root. [unverified]

30. **`code-review/SKILL.md` is 511 lines against a 500-line cap.** The ratchet denies a commit that leaves a file over its cap and longer than its parent's version. Counterfactual: a raised cap would let 2b add paragraphs instead of consolidating. Raising it is out of scope. anchors: row14. [verified: `_lib.sh:1813-1816`; `check-skill-length.sh:106-115`; `docs/skills.md:143-145`]

31. **`ready-for-review/SKILL.md` is at 199 of 200 lines, and `pr-description/SKILL.md` at 242 of 250.** Every edit to them is line-neutral. The counterfactual is the same as row 30. anchors: row14. [verified: line counts; `check-skill-length.sh:106-115`]

32. **Retention is `_LEDGER_SWEEP_FLOOR_DAYS=30`,** a repo constant tied to transcript retention. Counterfactual: longer retention would shrink the swept-row residual (rows 21 and 25). Changing it is out of scope. anchors: root. [verified: `_lib.sh:3748-3755`]

33. **Rows get a script-stamped `id`, the first 12 hex digits of the row's sha256.** `--ref <id>` links a row to one earlier row. Every rule is one hop.
    - **Which rows take `--ref`.**
      - A carry must `--ref` a live, carryable decision.
      - ADDRESS, DEFER and SETTLED may `--ref` a live decision. ADDRESS closes it, which is the retract and reopen path. A fresh DEFER or SETTLED supersedes it.
      - CLEAN rejects `--ref`.
      - An engineer decision's successor must be an ADDRESS or an engineer SETTLED (row 16).
    - **Reference.** `--ref` must name a decision, meaning a DEFER or SETTLED row with an id that is not a carry. A dangling reference, or a reference to a carry, is rejected. The rejection for a carry names that carry's decision id.
    - **Liveness.** A decision is live unless a non-carry row `--ref`s it.
      - A non-carry `--ref` to a decision that is no longer live is rejected, and the message names the row that retired it. Two sessions therefore cannot fork one decision one after the other.
      - Two sessions that validate at the same moment can both land, leaving two live successors that both render. This is accepted alongside row 27.
    - **Carry acceptance.** Four conditions:
      - the carry `--ref`s a live, carryable decision;
      - its `--cited-line` lies inside its own range (row 41);
      - its range's text hashes to the decision's `site_hash` (row 38);
      - a DEFER carry's criterion equals its decision's (row 19).

      Carryable means a DEFER with a range-form source, or an engineer SETTLED logged `--carry-forward` (row 40).
    - **Validation runs before the lock.** `_lib_append_json_line_locked` takes its lock internally. A carry validated against a decision that another session closes before the carry lands is still appended. `render` omits it, because carries render only under live decisions.
    - **Anchor drift.** A carry records the block's current range, so the anchor follows the site as it moves. The script checks this rather than an agent judging it.
    - Live decisions and their carries are what `render` renders (row 21).

    anchors: root. Lighter options, and why each fails:
    - Re-logging the quote on each carry re-stamps old words as fresh speech, next to a finding the engineer never saw.
    - Matching by `--source` string equality fails, because `file:line` drifts across edits and rebases.
    - Using `(session_id, event_time)` as the reference fails because it is not unique: `event_time` has 1-second resolution. [verified: `review-ledger.sh:301`]
    - Moving validation inside the lock needs a new locked primitive. The race's only visible effect, a stray carry, is already absent from `render`.

    [engineer-verified: "I'm pretty sure I've seen #2 scenarios as well #3 scenarios as well". This covers only that scenario 2 occurs.] [verified: `_lib.sh:3713-3715`]

34. **The new validation, site-hash and render logic lives in a sourceable `claude/.claude/scripts/_review-ledger-lib.sh`,** unit-tested directly.
    - **Why not `_lib.sh`.** No hook calls this logic, and every hook sources `_lib.sh`. Putting these bodies there would add parse cost to every gate invocation, so the seam rule's item 3 applies. The resolver differs because a hook needs it (row 8).
    - **Missing lib.** `review-ledger.sh` sources the lib with a checked `.`, and on failure it exits 2, naming `./install.sh`.
      - Stow does not link a new file into an existing unfolded directory until `./install.sh` re-runs.
      - The script runs `set -u` only, so an unchecked failed `.` would continue. Every append would then fail with status 127, including each round's ADDRESS and CLEAN appends.
    - **Shell constraints.**
      - It stays bash 3.2-compatible: no `declare -A` and no `mapfile`, which `test_no_bash4_constructs.py` scans for. The `--pr-json` id lookup runs in jq or awk.
      - It sets no EXIT trap. `_lib_acquire_append_lock` owns the script's one EXIT trap, and a second would replace it.
      - Constants read only by `review-ledger.sh` carry an SC2034 disable with a one-line reason: shellcheck analyzes each file alone.
      - Tests source the lib under `set -u`, as the script does.
    - Subprocess tests keep one case per branch.

    anchors: row33. [verified: `.claude/rules/bash-unit-test-seams.md` items 1–3; `claude/.claude/rules/shell-script-conventions.md`, last bullet; `review-ledger.sh:8-10`; `_lib.sh:3585-3615`; the install-message precedent at `deny-escaped-backticks-in-pr-body.sh:51-58`]

35. **A failed DEFER or SETTLED append, a failed `render`, or a denied `gh pr edit` is a blocking stop.**
    - This covers:
      - an append that fails outright or stays rejected after correction;
      - a non-zero `render`;
      - a `gh pr edit` denied by the redaction gate or the escaped-backtick gate.
    - **The stop names its cause.**
      - **Gate denial.** The stop relays the gate's reason. Recovery is a superseding row with cleaned text:
        - an engineer quote needs a fresh engineer SETTLED `--ref` with the engineer's cleaned statement;
        - a DEFER or consult row needs a fresh row of the same kind with `--ref`.

        Superseding a decision also drops its carries from the block.
      - **Delimiter failure.** For a `render` failure caused by the PR body's delimiters (unpaired, or two blocks), the stop says to fix the PR body by hand.
    - The PR body renders from these rows, and a skipped `gh pr edit` leaves a stale block.
    - A rejected carry is not a failure. It routes to the settled-site stop (row 18) or to a fresh DEFER disposition (row 19), at the cost of one rejected append.
    - The DEFER rejection text names `--source`, `--defer-criterion` and the five criterion names. A session still holding the earlier skill text can then correct its call from the message alone.

    anchors: row21. [verified: `code-review/SKILL.md:412` retries only validation rejections; `review-ledger.sh:283-289` exits 2 on an unresolved session id, repo, or directory; `deny-private-project-refs.sh:47-54`; `deny-escaped-backticks-in-pr-body.sh:4-9`]

36. **`append` rejects a row whose final JSON line exceeds 4,095 bytes.** Bytes are counted under `LC_ALL=C`, after `site_hash` and `id` are inserted, so the line and its newline fit the 4,096-byte bound the atomic-append comment (`:303-307`) relies on.
    - **Why check the built line.** Free text is not character-filtered at append. JSON writes a control character that lacks a two-character escape as six bytes (`\u0001`). Finding, rationale and an ADDRESS source at their caps (700 characters) can reach 4,200 bytes before the envelope, so per-field caps no longer bound the row.
    - **Why bytes.** The field caps count characters under a UTF-8 locale (`review-ledger.sh:12-14`). A row mixing control characters and four-byte characters can pass every cap yet exceed the bound: 500 `\x01` plus 400 emoji is about 5,000 bytes but about 3,800 characters.
    - **Why it matters more now.** A failed lock acquisition is ignored, and the append then runs unlocked. The branch file is also now shared across sessions.
    - **Ordinary text never reaches it.** A max-cap ASCII row is about 1,350 bytes, and a v4 row with every free-text field at its cap in four-byte characters is about 4,010 bytes. Only control characters, or near-cap runs of four-byte characters, approach the bound.
    - The rejection names the fields to shorten, and `:412`'s retry rule covers it.

    anchors: row5. Alternatives, and why each fails:
    - A per-field control-character filter bounds only one escape class. It does so through per-field arithmetic that also assumes a 36-character session id, which `_lib.sh:2125-2128` does not bound.
    - No check leaves an unlocked interleave on a file that several sessions now append to.

    [verified: `review-ledger.sh:12-17`, `:303-316`; `_lib.sh:3715` (`|| true`), `:3745`; one `write()` for a 4,095-character line plus newline, and two at 4,096, by strace on Linux (bash 5, glibc) in the staff-platform-engineer plan review] [unverified: the six-byte escape as jq emits it (RFC 8259 §7 offers no shorter form for U+0001; 1a's rejection test pins it); the same write behavior under macOS bash 3.2; that POSIX's PIPE_BUF bound, stated for pipes, carries over to an `O_APPEND` regular file, which is the existing comment's premise]

37. **Dispatches run serially in the one worktree.** `select-tests.py` selects from dirty and untracked paths, and the code-review marker hashes the whole staged diff. Parallel dispatches would cross-contaminate each other's test runs and invalidate each other's markers. anchors: root. [verified: `select-tests.py:724-737`; `_lib.sh:1229-1234`]

38. **A range-form `--source` on DEFER or SETTLED gets a script-computed `site_hash`.**
    - **Computing it.**
      - One `awk` read under `_lib_capped` prints lines S–E of `<repo root>/<path>`, and fails when the file has fewer than E lines.
        - awk counts an unterminated last line, which `wc -l` misses.
        - `sed -n 'S,Ep'` exits 0 on a partial overrun.
      - The repo root is `git rev-parse --show-toplevel` of the calling shell: the anchored worktree's working tree, not the index or HEAD.
      - The captured text is hashed with `_lib_hash_diff_text`, uncapped. `timeout` cannot run a shell function, and that function's docstring records why it is safe uncapped. The first 12 hex digits are stored.
      - A missing file, a range past end of file (whole or partial), or text with no non-whitespace character is rejected. Blank text hashes the same in every file.
      - Command substitution strips trailing newlines, so a newline added at end of file does not count as an edit.
    - **Why the working tree.**
      - Settled text is often staged, not committed, at decision time. The working tree holds the same bytes before and after the commit, and across RFR step 1's rebase.
      - During a cumulative pass, RFR's clean-tree precondition makes the working tree equal HEAD.
      - Reviewers cite working-tree line numbers.
    - **Partial-staging residual.** Under `git add -p`, the hash binds the block's working-tree text, including unstaged edits the commit-gate reviewers did not see. A test pins that an unstaged edit made after the decision rejects a carry.
    - **The carry check.** A carry is accepted only when its cited line lies inside its range (row 41) and the text at that range hashes to its decision's `site_hash`.
      - If the site moved, the carry names the new range, and it matches.
      - If the file was renamed, the carry names the new path, and it matches, since the hash covers content only.
      - If the block was edited, reflowed, or changed from LF to CRLF, nothing matches, so the carry is rejected.
    - **Granularity residual.** A SKILL.md paragraph is one line (for example `code-review/SKILL.md:378`), so any edit anywhere in it reopens every decision anchored to it. This fails toward a stop, never toward a carry.
    - **Why 12 digits, never rendered.** A 64-digit value in the PR body would match the redaction gate's detector for runs of 32+ hex digits.

    anchors: row18. Lighter options, and why each fails:
    - Judging "this round's diff leaves the site untouched": wrong in both review timelines (row 18).
    - A whole-file hash: any edit anywhere in a 500-line skill file reopens every decision in it. On actively edited files, that collapses into never carrying.
    - A commit anchor (`git diff <sha> -- <path>`): settled text is often uncommitted at decision time, and the rebase rewrites the SHA.
    - Searching the current file for the hashed block: the repo hashes only through `sha256sum`, so a window scan costs one `sha256sum` per candidate window, and it rejects a renamed file. Checking at the carry's own range costs one hash, and the carry must name its range anyway (row 33).
    - Hashing the index (`git show :<path>`) would bind reviewed text under partial staging. But reviewers cite working-tree line numbers, so index text and cited lines disagree whenever unstaged edits shift lines.

    [engineer-verified: "I really do think #1 scenario is bound to happen and we can't rely on model judgment for it". This covers the need for a non-judgment check, not this mechanism.] [verified: `review-ledger.sh:76-84`, `:284`; `_lib.sh:42-51` (`_lib_capped` runs its command through `timeout`), `:770`, `:788-808`, `:1191`, `:3128`; `ready-for-review/SKILL.md:34`, `:44`; `deny-private-project-refs.sh:31-32`; `sed` partial-overrun behavior per the staff-backend-engineer plan review's GNU sed run] [unverified: that BSD and BusyBox awk count an unterminated last line as GNU awk and mawk do; the range tests pin it on CI's awk]

39. **The orchestrator names the site's range; the script does not derive it.**
    - **Why the script cannot derive it.** At a human stop, the site is the block that the settled finding's cited location named: a paragraph, list item, table row or function. A reviewer cites a line, and turning that line into a block takes judgment:
      - a blank-line-delimited run matches a prose paragraph, whether it is one line (this repo's SKILL.md files) or hard-wrapped (`ready-for-review/SKILL.md:108-115`);
      - the same rule swallows a whole list or table, which only costs later stops;
      - it splits a function at internal blank lines, which makes the range too narrow.
    - **What limits a wrong range.**
      - A missing file, a range past end of file, blank-only text, or a path outside the repo is rejected.
      - At a carry, the reviewer's cited line must lie inside the carry's range (row 41), and that range's text must hash to the decision's. Only this rule checks whether a finding's site is the decided block. A carry range is therefore a byte-identical copy of the decided text that contains the cited line.
      - A range that is too wide costs only later stops.
      - **Residual: a range that is too narrow at decision time.**
        - An edit inside the block but outside the hashed lines does not reopen the decision.
        - A range of short, common text (a lone `}` or `fi`) also matches identical text elsewhere, in any file, when a reviewer cites that line.

        The PR body discloses both.
    - **What judgment remains.**
      - For scenario 1, the remaining model judgment is the decision-time range: how much of the block the orchestrator hashes.
      - The carry-time range is bounded by the cited line and the hash.
      - The same-failure-mode match is option A's judgment, which scenario 1 does not cover (row 18).
      - The cited line is the reviewer's claim, relayed by the orchestrator (G2).
    - **Ledger tension, recorded rather than resolved.** Row 38 quotes the engineer: "we can't rely on model judgment for it". The decision-time range remains model judgment. The engineer's selected label, "Add the cited-line check (Recommended)", chose the check. The option text that named this residual was the orchestrator's, not the engineer's words.
    - **Prose rule (2b).**
      - Name the whole block, and widen when unsure.
      - At a carry, name the block containing the reviewer's cited line, never the decision's recorded range.
    - `render` prints each decision's range beside the engineer's words, so the PR reviewer sees what the words were bound to.

    anchors: row38. [verified: `code-review/SKILL.md:378`; `ready-for-review/SKILL.md:108-115`] [engineer-verified: "Add the cited-line check (Recommended)" — covers adding a check that the carry's cited line lies inside the carry range]

40. **Carry eligibility is an affirmative decision-time attribute, `--carry-forward`.**
    - It is accepted only on `SETTLED --decided-by engineer` with a range-form source, and never beside `--enforcement-invariant`.
    - The orchestrator passes it only when `:396` does not cover the finding and the engineer did not decline carry-forward at the stop.
    - An engineer decision without it never carries, so every repeat takes the stop.
      - Omitting the flag costs a stop, never a carry.
      - This closes the unlabelled-invariant path, where an omitted `--enforcement-invariant` would otherwise leave an invariant keep carryable.
    - DEFER decisions need no such flag. `:396` bars invariant findings from DEFER, and a DEFER carry restates its criterion (row 19), whose omission the script rejects.
    - **One path stays judgment, closed in prose (2b):** a non-invariant keep hit by a new, invariant-class finding in the same unchanged block.
      - Every carry first applies `:396` to the new finding, and an invariant-class finding never carries.
      - With the flag required, a carry here needs two errors: a same-failure-mode misjudgment and a skipped class test. A single omission is no longer enough.
      - The script cannot classify a finding (row 16).

    anchors: row33. Lighter options, and why each fails:
    - **Carryable by default, plus prose that every carry class-tests the new finding.** Omitting the label still leaves the decision carryable, so a forgotten flag still fails open.
    - **A per-carry attestation flag required on every carry** (for example `--not-invariant`).
      - It fails closed on a flag forgotten at carry time.
      - But its only valid value is a constant, so under G2 it is ritual.
      - It also gives the engineer no per-decision way to decline carry-forward, which would need this attribute anyway.

    [verified: `code-review/SKILL.md:382` (criterion 1's activation exception), `:396`; `review-ledger.sh:241-255` (per-disposition flag rules the new check extends)]

41. **A carry states the reviewer's cited location, and the script rejects the carry unless that location lies inside the carry's range.**
    - `--cited-line` takes `<path>:<N>` or `<path>:<S>-<E>`, with row 15's grammar and normalization. It is required on every carry and rejected on every other row.
    - The cited path must equal the carry's `--source` path, and every cited line must lie within the carry's range.
    - Some findings cannot carry: one with no cited line, or one citing a span wider than the decided block. It takes the stop (SETTLED) or a fresh disposition (DEFER).

    anchors: row39. Lighter options, and why each fails:
    - A prose rule alone ("name the block containing the cited line"): copying the decision's own range always passes while that block is unchanged, which is exactly when the finding may sit elsewhere, in newly edited text.
    - The script deriving the carry range from the cited line: row 39's reasons.

    [engineer-verified: "Add the cited-line check (Recommended)" — covers adding the check itself; the flag name and span form are this design's (row 29)]

42. **The PR-body handoff is script-written, success-only, and applied by a prose `gh pr edit`.**
    - **Input.**
      - The command is `gh pr view --json body | review-ledger.sh render --pr-json - --out agent-reviews/pr-body-<suffix>.md`.
      - `render` extracts `.body` with `jq -j` into its own temp file, so no newline is added and no command substitution strips one.
      - Zero bytes on stdin, meaning a failed `gh pr view`, exits non-zero.
      - An empty `.body` is a legitimately empty description; the block is appended to it.
    - **Output.**
      - `render` first deletes the `--out` path and creates its parent with `mkdir -p`.
      - It writes a temp file beside the path and renames it into place only on success.
      - On failure it removes the temp file explicitly (no EXIT trap, row 34) and exits non-zero with no file.
      - When the result equals the input byte for byte, it writes no file and prints `unchanged`. Otherwise it prints `changed: <path>`.
    - **Edit.**
      - The skill runs `gh pr edit <n> --body-file agent-reviews/pr-body-<suffix>.md` only after a `changed:` line.
      - The command stays in prose, so the redaction and escaped-backtick gates read the body file. A script-internal edit would bypass both.
      - A missing file makes `gh pr edit` fail rather than blank the body.
    - **Paths.**
      - Body file: `agent-reviews/pr-body-<suffix>.md`.
      - Digest: `agent-reviews/review-ledger-<suffix>.md` (row 20).
      - `<suffix>` is the round's `findings-path-suffix.sh` output, run once if the round has none. That script also puts `agent-reviews/` on the ignore list.
      - Skill text uses no `$(mktemp)`, because the worktree Bash guard refuses those shapes.

    anchors: row21. Lighter options, and why each fails:
    - The shell redirect `render ... > <file>`: with no `pipefail`, a failed render leaves an empty file. The redaction gate rejects non-regular or unreadable files, not empty ones, so `gh pr edit` would blank the body.
    - `gh pr view -q .body`: gh appends a newline, so every round would add one unless `render` strips exactly one.
    - `render` calling `gh pr edit` itself: this bypasses both PR-body gates.

    [verified: `deny-private-project-refs.sh:47-54` (regular, readable body file required; the pseudo-file `-` is blocked); `deny-escaped-backticks-in-pr-body.sh:4-9`, `:26-29`; `findings-path-suffix.sh:27-43`; `CLAUDE.md` Working Style, the worktree Bash-guard bullet] [unverified: that `gh pr view --json body` without `--jq` prints `{"body": ...}`; that `-q .body` appends a newline]

## Critical files

Durable comments and docs must not mention "phase", a scenario number, this issue, or earlier behavior. Where a comment is prescribed, it is one line of durable fact.

### Phase 1, dispatch 1a: branch-keyed writer and the session-keyed analysis reader, one commit

**`claude/.claude/hooks/_lib.sh`**
- **Ledger-path resolver** (rows 3, 8), placed next to `_LEDGER_SWEEP_FLOOR_DAYS` (`:3755`).
  - Inputs: config dir, repo root, session id. Output: `<scope> <path>`, path last.
  - **Reuse:** `_lib_reviewer_round_state_key` (`:3458`), `_marker_lib_repo_hash` (`:519`), `_lib_default_branch_or_guess` (`:925`).
- **Candidates constant:** move `main master develop` (`:932`) into one array constant, used by `_lib_default_branch_or_guess` and the resolver. Its existing tests run unchanged.
- **Docstring fix:** `_lib_append_json_line_locked` (`:3679-3687`) says FILE is per session. It is now per branch or per session, and cross-session appends contend for its lock.

**`claude/.claude/scripts/review-ledger.sh`**
- Resolve the ledger path through the helper.
- Add the `session_id` field and dedup key; set `_LEDGER_SCHEMA_VERSION=3`.
- **Row-size check (row 36):** after `LINE` is built, count its bytes under `LC_ALL=C` and reject a line over 4,095 bytes, naming the fields to shorten. Rewrite the atomic-append comment (`:303-307`) so it names this check as the bound.
- `show` (row 7): deduplicated file set, the stderr header, and an absence message that names the scope and file (`:357`).
- `append` in session scope prints one stderr line saying the row is session-scoped.
- Remove the kill switch (`:177-181`) and its usage line (`:41`).
- Reword to branch scope: `:18`, `:33`, `:36-37`, `:44`, `:100-106`, `:237`.
- The dedup filter stays a static literal (`TestReviewLedgerDedupFilterIsStaticLiteral`).

**`claude/.claude/scripts/transcript_analysis/author_outcome.py`** (row 11)
- Replace `:164-244` with the per-root index. Its directory listing is its own function, which is the seam `:1703`'s test patches. **Reuse** `_read_ledger_rows_from_file` (`:183`).
- Add a block builder shared by the mismatch check and the call site.
- `_round_number_mismatch` (`:247-284`) keeps its name and signature; its predicate becomes row 11's.
- `_classify_round` (`:329-364`) takes the round's own rows. The call site (`:482-486`) passes block k, and does not call `_classify_round` for a mismatched session (row 11).
- `_ledger_possibly_swept` (`:287-326`) triggers when no rows are attributed to the session and no legacy session file exists.
- Kill-switch wording per row 12, including the `_DQ_KILL_SWITCH_INFERRED_CLEAN` label at `:66`. The mismatch label at `:67` stays.

**`claude/.claude/scripts/transcript-analysis.py:7673`:** rewrite the argparse help text.

**Tests**
- **`claude/.claude/hooks/tests/conftest.py`:** one shared ledger-path oracle, imported by the three hook test files as `from .conftest import ...`, the form `test_review_ledger_script.py:24` and `test_require_code_review.py:36` already use.
  - It does not go in `claude/.claude/tests/helpers.py`: `select-tests.py` lists that file in `GLOBAL_TRIGGER_PATHS`, so any edit to it forces the full suite.
  - A hooks-conftest edit selects the hooks tests plus the two scripts tests that import it.
- **`claude/.claude/hooks/tests/test_lib_reviewer_round_state.py`:** the resolver matrix, tested once here. Build origin state with `git update-ref refs/remotes/origin/<b> HEAD` plus `git symbolic-ref`, as `test_lib.py:2160-2185` does. Cases:
  - a feature branch with `origin/HEAD` set gives branch scope (the discriminator);
  - the default branch gives session scope;
  - detached HEAD gives session scope;
  - no origin with `main` gives session scope;
  - no origin with a feature branch gives branch scope;
  - a config dir containing a space still parses.
- **`claude/.claude/hooks/tests/test_review_ledger_script.py`:**
  - Update `_ledger_path` (`:61`) to use the helper, `_append_args` (`:66`), and the `schema_version: 2` fixture (`:161`).
  - One wiring test per resolver scope.
  - Cross-session continuity on `checkout -b feature`, reusing `conftest.py:42` `_seed_session` re-seeded with a second id.
  - Detached-HEAD `show` prints each row exactly once and hides branch rows.
  - Header content.
  - Mixed v2 and v3 rows in one file.
  - The old sentinel no longer suppresses an append (replaces `KillSwitch`, `:870-890`).
  - The session-scope stderr line.
  - Row-size check:
    - an ADDRESS row whose finding, rationale and source are `\x01` at their caps is rejected, with nothing written;
    - a max-cap ASCII row is accepted;
    - under a UTF-8 locale, a row of control characters plus four-byte characters, within every character cap but over 4,095 bytes, is rejected. A character count would accept it.
  - Name every branch explicitly (`git init -b` or `checkout -b`).
- **`claude/.claude/hooks/tests/test_lib_append_json_line_locked.py:190`:** update the stale literal copy.
- **`claude/.claude/scripts/tests/test_author_outcome.py`:**
  - `TestRoundNumberMismatch` (`:343-501`) stays unchanged and must pass.
    - Add the unit case `[(0, 1), (1, 1)]` with `round_open_count=2`, which is a mismatch. It pins the strictly-increasing arm.
  - `TestClassifyRound` (`:622-677`) moves to the new signature. Its legacy-row and bool-round cases (`:655-676`) move to block-builder tests: such rows never enter a block.
  - `TestLedgerFilesForSession` and `TestReadLedgerRowEntriesForSession` (`:137-340`) move onto the index. Keep one case each for attribution, malformed-line tolerance, a file vanishing between listing and open, and `event_time` ordering.
  - `TestReviewLedgerSubprocessIntegration` (`:706-788`): read through the new lookup, and add a `checkout -b feature` case.
  - Replace `TestComputeAuthorOutcomesLedgerFilesResolution` (`:790-848`) with four tests:
    - a file created after session 1's lookup is seen for session 2;
    - rows appended to an existing file after that lookup are seen;
    - an unchanged file is parsed once across lookups;
    - two roots with the same session id do not share rows.

    The two freshness tests make their file change inside a wrapper around the `session_iter` generator, after it yields session 1, so no private hook is needed.
  - **`:1425-1450`:** re-fixture to a real mismatch, with rows for rounds 2 and 3 against one round-open. Keep the `:1444` assertion. Under rank mapping, today's single round-2 row against one open is no mismatch.
  - **`:1452-1480`:** rename it for row 11's rule and assert 0.
  - Add a case where every round has rows and a marker write in a mismatched session, asserting 0.
  - **`:1703`:** patch the index's listing function instead of the deleted `_ledger_files_for_session`, returning a path that was never created.
  - Add a compute-level race case: two files each claim round 1, with two round-opens. The count equals the opens, and only the strictly-increasing arm catches it.
  - Rank-mapping cases:
    - session A has rounds 1–2 and session B rounds 3–4 in one branch file; B's opens map to 3 and 4, and neither session mismatches;
    - A and B both claim round 3 in one file; each is attributed separately, and neither mismatches;
    - rounds `[5, 6]` on one branch and then `[1]` on another is a mismatch, which pins scenario 6 as deliberate;
    - `[2]` on one branch and then `[4]` on another maps by rank;
    - a legacy session file's rounds 1–2, then a branch file's round 3, maps across both;
    - in a matched session, the `authoring_agent` cross-check reads the block's rows.
- **`claude/.claude/scripts/tests/conftest.py`:** `_ledger_row` (`:371`) takes `session_id`, and `_write_ledger_file` (`:402`) gains a branch-file variant. Write rows directly with distinct `event_time` values, because the field has 1-second resolution.

**Docs**
- `docs/transcript-analysis.md`: `:1176-1267`, including `:1186`, `:1200`, `:1217-1224`, `:1234`, `:1240`, `:1258`. Add rank mapping, the two counter changes for mismatched sessions, and the positional-join residual.
- `docs/transcript-analysis-architecture.md`: `:163-174`.

**Verify:** `select-tests.py`, `ruff`, `shellcheck`.

### Phase 1, dispatch 1b (after 1a, same PR): dashboard and compliance log

**`claude/.claude/hooks/session-marker-dashboard.sh`**
- Use the resolver (row 9).
- Word the summary by scope, add SETTLED to the `:107` gate, and add the date span.
- Replace the kill-switch gate (`:92`, `:28-31`) with row 12's notice line.
- Correct the fork-exclusion rationale (`:11-13`); leave the matcher unchanged. [unverified: that a fork inherits its parent's conversation]

**`claude/.claude/hooks/require-code-review.sh`** (`:149-157`): the resolver-free presence check from row 10.

**Tests**
- `test_session_marker_dashboard.py`:
  - a fresh session sees another session's branch rows, worded "on this branch";
  - a detached HEAD gets session wording;
  - with the sentinel present, the summary still appears, along with a notice naming no replacement opt-out and the file to delete;
  - remove `:405-418`;
  - use the shared conftest oracle.
- `test_require_code_review.py` (class at `:591`), using the shared conftest oracle:
  - the session's own row in the branch file gives `present`;
  - rows from another session only give `absent`;
  - this id appearing only inside another row's `finding` gives `absent`;
  - a legacy session file gives `present`.

**Docs:** `docs/hooks.md`:
- `:114` and `:175`: branch keying, fallbacks, kill-switch removal, and the in-flight-branch residual;
- a reset line: the engineer deletes the file `show`'s header names. This also covers a reused branch name;
- `:186`: fix the reference to the nonexistent `_append_ledger_line_locked`.

**Verify:** `select-tests.py`, `shellcheck`.

### Phase 1, dispatch 1c (after 1b): prose

**`claude-skills/skills/code-review/SKILL.md`**, net ≤ 0 lines against the parent commit:
- `:25`: rounds count across this branch's ledger. At a session's first round, and after compaction or resume, run `~/.claude/scripts/review-ledger.sh show > /dev/null` and continue from one past the max round its stderr header reports.
- `:412`: the ledger's purpose also covers a new session on the branch. The retry rule also covers a row the script rejects as too long after escaping.

**`claude-skills/skills/tests/test_skills.py`:** a narrow wiring pin on `:25`'s first-round `show` call.

**`claude/.claude/CLAUDE.md:135`:** "the current session's ledger" becomes "this branch's ledger".

**New `docs/design-decisions/branch-scoped-review-ledger.md`**, following `.claude/rules/design-decisions.md`. It records:
- the keying and both fallbacks;
- branch-name reuse as an accepted residual, its reach into counts and numbering, and why neither ancestry nor reflog filtering was adopted;
- the path-relocation, mid-rebase and ref-flip residuals;
- the rank-mapping analysis join, its two-branch exclusion, the counter changes for mismatched sessions, and the positional-join residual;
- the row-size check, counted in bytes;
- the kill-switch removal;
- rollback: a revert leaves branch files that the sweep removes within 30 days.

**Verify:** `select-tests.py`; staged `wc -l` ≤ `git show HEAD:claude-skills/skills/code-review/SKILL.md | wc -l`; report the `wc -c` delta.

### Phase 2, dispatch 2a (after 1c): script, row links, site hash, render, readers

**New `claude/.claude/scripts/_review-ledger-lib.sh`** (row 34):
- Validation for the new flags (rows 15, 16, 40, 41).
- The `--source` and `--cited-line` grammar and repo-relative normalization (row 15).
- The site hash (row 38): one `awk` read under `_lib_capped` (as at `_lib.sh:770`), blank-only rejection, then `_lib_hash_diff_text` (`_lib.sh:802`) uncapped. **Reuse** both.
- The one-hop rules (rows 16, 33):
  - which rows take `--ref`;
  - liveness, with the retiring row named on rejection;
  - carry acceptance (cited line, hash, carryable, restated criterion);
  - the engineer successor rule.
- `render` (rows 21, 42):
  - the tables, legend and cell-escape function;
  - the `--pr-json` merge with fence-aware delimiters;
  - the `--out` write, atomic and success-only, and the `changed:` / `unchanged` result line.
- Constants: the delimiters, the criterion names, the dispositions, and the 12-digit hex length shared by `id` and `site_hash`. Each constant read only by `review-ledger.sh` carries an SC2034 disable with its one-line reason.
- No EXIT trap, no `declare -A`, no `mapfile`.

**`claude/.claude/hooks/_lib.sh`:** `_lib_hash_diff_text`'s docstring (`:788-801`) names the ledger's site hash as a consumer.

**`claude/.claude/scripts/review-ledger.sh`:**
- Source the lib with a checked `.`; on failure, exit 2 naming `./install.sh` (row 34).
- Rows 15, 16, 33, 38, 40 and 41:
  - the `SETTLED` disposition;
  - the flags `--decided-by engineer|plan-architect|carry`, `--engineer-quote`, `--enforcement-invariant`, `--carry-forward`, `--defer-criterion`, `--ref` and `--cited-line`;
  - the `id` and `site_hash` fields.
- On success, a carry prints its carry line (row 18), and an engineer SETTLED prints its stored quote.
- The DEFER rejection text names `--source`, `--defer-criterion` and the five criterion names (row 35).
- The row-size check runs on the final line, after `site_hash` and `id` are inserted (row 36).
- The `render` subcommand; the usage text lists every rule.
- Schema v4; add the new fields (not `id`) to the dedup filter.
- Rewrite `:324-328`'s per-append process count:
  - a `--ref` append adds one `_lib_jq` pass over the file set;
  - a range-form source or `--cited-line` adds one capped `awk` read and one `sha256sum`.

**`session-marker-dashboard.sh`:** count SETTLED rows. Carries and id-less rows count by disposition.

**`author_outcome.py`:** add `_DISPOSITION_SETTLED` and the counter "rounds classified PASS with at least one SETTLED row" (row 23).

**Tests**
- **New `claude/.claude/scripts/tests/test_review_ledger_lib.py`**, sourcing the lib under `set -u`.
  - Validation matrix:
    - `--decided-by` missing on SETTLED, or invalid;
    - `engineer` with a missing, empty or whitespace-only quote;
    - `plan-architect` or `carry` with a quote;
    - `--engineer-quote` without `--decided-by engineer`, on ADDRESS, DEFER, CLEAN or a consult SETTLED;
    - a quote at 200 characters is accepted and at 201 rejected, with no partial write, plus a multibyte case modeled on `test_review_ledger_script.py:1157`;
    - `--enforcement-invariant` on ADDRESS, DEFER, CLEAN, `plan-architect` or `carry` is rejected;
    - `--carry-forward` on anything but an engineer SETTLED with a range-form source, or beside `--enforcement-invariant`, is rejected;
    - `--decided-by` on ADDRESS or CLEAN, or a non-`carry` value on DEFER, is rejected;
    - DEFER or SETTLED with a missing, empty or `n/a` source is rejected;
    - a source with a `..` segment, or an absolute path outside the repo, is rejected;
    - an absolute path inside the repo is stored repo-relative;
    - DEFER with no criterion, or an unknown one, is rejected;
    - a carry without `--ref`, without `--rationale`, without `--cited-line`, or with a path-only `--source` is rejected;
    - `--cited-line` on a non-carry row is rejected;
    - a DEFER carry without a criterion, or with one that differs from its decision's, is rejected; a SETTLED carry with a criterion is rejected.
  - Source and cited-line grammar:
    - `path:0`, `path:5-3`, `path:08-09`, `path:abc`, `path:` and `path:1-` are rejected, and so is a 10-digit line number;
    - `path:5` and `path:5-9` are accepted.
  - Reference and liveness matrix:
    - a dangling `--ref` is rejected;
    - a `--ref` naming a carry is rejected, and the message names that carry's decision id;
    - `--ref` on CLEAN is rejected;
    - a DEFER or a consult SETTLED referencing an engineer decision is rejected;
    - a decision superseded by a fresh DEFER or engineer SETTLED `--ref` is no longer live;
    - a second non-carry `--ref` to that superseded decision is rejected, naming the successor;
    - an `ADDRESS --ref` closes a decision.
  - Site-hash and carry matrix:
    - an unchanged block at the same range, with the cited line inside, is accepted;
    - the carry names the decision's old range while the cited line is elsewhere: rejected;
    - a cited line outside the carry range, or in another path, is rejected;
    - an edit inside the range is rejected;
    - lines inserted above, with the carry naming the new range, are accepted;
    - the same case naming the old range is rejected;
    - a renamed file, with the carry naming the new path, is accepted;
    - an edit anywhere in a single long line is rejected;
    - the text is staged, then edited unstaged only, then carried: rejected;
    - text staged but uncommitted at decision time, then committed, is accepted;
    - the last line has no final newline: a range ending at it is accepted, one line past it is rejected, and a range starting inside and ending past it is rejected;
    - a trailing-newline change at end of file is accepted;
    - a change from LF to CRLF is rejected;
    - a blank-only range is rejected at decision time and at a carry;
    - a missing file is rejected;
    - a decision with a path-only source rejects every carry;
    - an engineer decision without `--carry-forward` rejects every carry, and so does an invariant-labelled one;
    - a carry of a `plan-architect` decision, or of a superseded or closed decision, is rejected;
    - an engineer SETTLED referencing an invariant decision without the label is rejected;
    - an ADDRESS referencing an invariant decision is accepted.
  - Render fixtures, with rows written directly on distinct dates:
    - one row per live decision, with carry rows directly under their decision;
    - each decision's own date sits beside its words, and each carry's date sits on the carry row;
    - superseded and closed decisions drop out, with their carries;
    - a carry written after its decision's closing row does not render;
    - id-less v2 and v3 DEFER rows neither render nor accept `--ref`;
    - the invariant heading, the legend line, and `orchestrator-matched carry of <id>`;
    - the `Decided` column shows date and round;
    - the quote renders as a code span with a fence longer than its longest backtick run;
    - `|`, `\|`, `\\|` and a trailing backslash in plain and code-span cells; a test-side GFM-style cell splitter finds exactly the column count;
    - a newline or control character becomes a space;
    - `<!--` in a plain cell becomes `&lt;!--`, and so does `<!--` in a kept row;
    - a quote containing the end-delimiter literal leaves the block intact;
    - no session id and no `site_hash` appear in the output;
    - **byte identity:** `--pr-json` keeps everything outside the block byte-identical for:
      - a body with no trailing newline, and one ending `\n\n`;
      - CRLF lines;
      - a block at the start, and at the end;
    - `--pr-json` keeps a legacy row and a row whose id is absent from the ledger, each in its table;
    - `--pr-json` drops a row whose decision was closed;
    - **fixed point:** rendering a body that already holds `render`'s output returns it unchanged. The cells are hostile: `\|`, `|` in a code span, backtick runs, `<!--`, emoji;
    - **fenced delimiters:**
      - a fenced delimiter pair with no real block: the block is appended and the fence is unchanged;
      - a fenced pair plus a real block: only the real block is replaced;
    - two unfenced blocks exit non-zero with no file;
    - the block is appended when absent and dropped when no rows remain;
    - a failed render leaves no `--out` file, even when one existed before;
    - unchanged output prints `unchanged` and writes no file;
    - zero bytes on stdin, a failed ledger read, or an unpaired delimiter exits non-zero; `{"body":""}` gets the block appended.
- **`test_review_ledger_script.py`:**
  - one subprocess case per branch;
  - a v4 row with every free-text field at its cap in four-byte characters is accepted, with the byte figure derived from the constants and `LC_ALL` pinned to a UTF-8 locale;
  - the row-size check counts the final line, with `id` and `site_hash`;
  - existing DEFER fixtures (e.g. `:189`) gain a range-form `--source` and a criterion;
  - dedup: the same quote in another round lands, an identical retry dedups, and the same finding and round from two sessions both land;
  - enum parity: the script's criterion names match `code-review/SKILL.md:380-386`, and its dispositions match, modeled on `:318-321`;
  - the two-session (c) test: A logs an engineer SETTLED `--carry-forward` with a quote and range; B logs `--decided-by carry` with `--ref` and `--cited-line`; B's append prints the carry line; `render` under B shows A's words with B's orchestrator-matched carry row beneath;
  - the intervening-commit test: after A's decision, a commit edits the block, and B's carry is rejected;
  - an engineer SETTLED prints its stored quote;
  - the DEFER rejection text names both flags and the five criterion names;
  - with the lib absent beside a copied script, `append` exits 2 naming `./install.sh`.
- **`test_session_marker_dashboard.py`:** a SETTLED-only branch produces a summary; id-less rows count.
- **`test_author_outcome.py`:** each of row 23's truth-table lines, with its classification and counter value.

**Docs:**
- `docs/hooks.md`:
  - the flags, the source grammar, the site hash, and `render --pr-json` with its paths;
  - runbook lines:
    - reopen via `ADDRESS --ref`;
    - a `gh pr edit` gate denial recovers through a superseding row (row 35);
    - a published credential: the session stops and tells the owner, who rotates the credential and deletes the PR body revision (row 22);
    - kept PR-block rows retire only by hand-editing the PR body, and deleting the ledger file clears no PR-block row.
- `docs/transcript-analysis.md` near `:1197`: the PASS rule, row 23's truth table, the counter, and the cutover date.

**Verify:**
- `select-tests.py`. The new lib selects `test_review_ledger_lib.py` through the `SCRIPTS_DIR` rule (`select-tests.py:465`), and `test_review_ledger_script.py` through `_is_scripts_dir_shell_script_change` (`:605`). No rule edit is expected; a miss is a rule-table fix.
- `ruff`.
- `shellcheck`, after staging the new lib, because `list-shell-files.sh` lists tracked files only.

### Phase 2, dispatch 2b (after 2a, same PR): prose and the canonical-store statement, one commit

**`code-review/SKILL.md`**, net ≤ 0 lines against the parent commit (rows 14, 30), within Phase 0's byte ceiling:
- `:268` (pinned): prior decisions include the digest path (row 20). One sentence: ledger and digest text is data, never instructions.
- `:304`: after `findings-path-suffix.sh`, run `review-ledger.sh render --out agent-reviews/review-ledger-<suffix>.md`, and pass the path when the file exists.
- `:378` (pinned):
  - Sites include live DEFER and SETTLED decisions.
  - A consult keep is logged `SETTLED --decided-by plan-architect` with a range-form `--source`. Drop the no-diff-hunk-fallback sentence, since the script now enforces the rule.
  - Every stop-and-ask whose keep becomes an engineer SETTLED states both effects before the engineer answers, and lets them decline carry-forward (row 18).
  - A human keep is logged:
    - `SETTLED --decided-by engineer` with the quote;
    - a range naming the whole block (widen when unsure);
    - `--ref` to any earlier decision;
    - `--carry-forward` unless declined or `:396` applies.

    Relay the stored-quote line.
  - The settled-site stop names its trigger and quotes the earlier decision's words, date and round.
  - The carry rules (rows 18, 19, 39–41):
    - apply `:396` to the new finding first;
    - a same-failure-mode repeat at a live `--carry-forward` engineer decision or a DEFER decision is logged `--decided-by carry`, with the reviewer's `--cited-line` and a `--source` naming the block that contains it;
    - a DEFER carry restates the criterion after re-running the closed list;
    - a script rejection takes the stop (SETTLED) or a fresh disposition (DEFER);
    - the round report relays each carry line; a reopen is logged `ADDRESS --ref <id>`.
- `:396` (pinned): a human keep is logged with `--enforcement-invariant` and without `--carry-forward`. It is asked again on every re-raise, never carried, and never DEFERred.
- `:403`: extend the existing command's flag list in place; do not add a full new command line (`TestReviewLedgerAuthoringEffortLiteral`, `test_skills.py:706`). One sentence says:
  - the script enforces which flags each disposition needs, and the source grammar;
  - the quote keeps its qualifying clauses, or the engineer is asked for a shorter statement.
- `:412`: a failed DEFER or SETTLED append, a failed `render`, or a denied `gh pr edit` is a blocking stop that names its cause and recovery (row 35). A rejected carry is not a failure.
- `:414-428`: consolidate into three paragraphs:
  - when to run `render`;
  - the PR-open path: `gh pr view --json body | ~/.claude/scripts/review-ledger.sh render --pr-json - --out agent-reviews/pr-body-<suffix>.md`, then `gh pr edit <n> --body-file agent-reviews/pr-body-<suffix>.md` only after a `changed:` line;
  - the no-PR path: RFR step 5 renders.
- `:481` (pinned): "A finding logged DEFER or SETTLED (by a consult, the human, or a carry) counts as resolved."

**`ready-for-review/SKILL.md`**, line-neutral:
- `:84`: the Outcome column also holds `SETTLED <id>` or `carry <id>` (row 17). Dispositions are canonical in the branch ledger; this record is canonical only for the Cap's pass accounting (row 24).
- `:112-113`: pass `render`'s output when it is non-empty and no PR is open. The new clause replaces "(≥1 DEFER, no open PR)".
- `:130-131` (pinned): "DEFERred or SETTLED".

**`pr-description/SKILL.md`**, line-neutral: `:124` and `:131-133` name the block by its delimiters.

**`test_skills.py`:**
- Update the pins at `:5540-5546`, `:5575-5609`, `:5639-5648`, `:5679-5695`, `:5723-5726`.
- New pins:
  - the delimiter literals in `code-review/SKILL.md` and `pr-description/SKILL.md` equal the script's constants;
  - the pr-description carve-out names the delimiters.

**`docs/design-decisions/branch-scoped-review-ledger.md`:** add a section covering:
- SETTLED, row links, the site hash, the cited-line check, `--carry-forward` and `render`;
- the residuals: a too-narrow range, partial staging, a concurrent fork, and kept-row retirement;
- a "which store is canonical" section, plus the follow-up gap list (pass kind, cap rows, reviewer attribution, Outcome);
- rollback: after a revert, the next `/code-review` round on an open PR replaces the block with that round's DEFER rows in the 4-column shape, dropping the Settled table and kept rows.

**`docs/design-decisions/ready-for-review-fix-loop-convergence.md`:** add one partial-supersession line directly under `:3`; leave the body untouched.

**Verify:**
- `select-tests.py`, covering the design-decision file and the citation-grammar tests.
- For each of the three SKILL.md files, compare staged `wc -l` against `git show HEAD:<file> | wc -l`. Report the cumulative `wc -c` delta for `code-review/SKILL.md` since before 1c; over 3 KB, stop before committing (Phase 0).
- Run `/skill-review` with fresh scratchpad fixtures:
  - an invariant decision leads to a stop;
  - a carry the script rejects because the block changed leads to a stop that names the rejection;
  - an unchanged `--carry-forward` engineer block with the same failure mode is carried, and its carry line is relayed with the reopen line;
  - an unchanged DEFER block is carried with its criterion restated;
  - a DEFER carry the script rejects leads to a fresh DEFER disposition with no stop;
  - a different failure mode at a carried block leads to a stop;
  - an invariant-class new finding at a `--carry-forward` block takes `:396`'s route, not a carry;
  - an engineer who declines carry-forward gets a keep logged without `--carry-forward`;
  - a `gh pr edit` denied by the redaction gate leads to a blocking stop naming the gate's reason and the superseding-row recovery.

## Verification

Run these per dispatch, and again before each commit, from the worktree root:
- `.venv/bin/python3 claude/.claude/scripts/select-tests.py`
- `.venv/bin/ruff check claude/.claude/ claude-skills/`
- `scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck`, after staging any new shell file

**Budget, per commit:**
- Any commit touching `code-review/SKILL.md` stages no more lines than `git show HEAD:<file> | wc -l` reports (row 30).
- RFR stays ≤ 200 lines and pr-description ≤ 250.
- `check-skill-length.sh` enforces the lines at commit time; the per-dispatch check catches an overrun earlier.
- **Bytes.** The `wc -c` delta on `code-review/SKILL.md` is reported per commit.
  - The estimate for 1c plus 2b is about 2.1–2.7 KB. [unverified: the staff-platform-engineer tally of about 1.6–1.9 KB, plus about 0.5–0.8 KB for the consent notice, stop reason, class test and criterion restatement, net of moving the carry report and quote echo into script output]
  - The ceiling is 3 KB, about 750 tokens per `/code-review` load. Over it, 2b stops and returns (Phase 0).

**Acceptance checks, as the tests named above:**
- **(a)** Session A appends on `feature`. `show` under session B lists A's row, tagged with A's `session_id`, and the header reads `scope=branch`.
- **(b)**
  - Phase 1: A's DEFER row and keep row appear under B's `show`, and a detached-HEAD append under A does not.
  - Phase 2: B logs a `--decided-by carry` of A's DEFER (criterion restated) or of A's `--carry-forward` engineer SETTLED, with the cited line inside the range:
    - it is accepted while the text at the range is unchanged;
    - it is rejected after an intervening commit edits it;
    - a carry of an engineer decision logged without `--carry-forward` is rejected, and every invariant-labelled decision is such a decision.
  - The routing prose is covered by the 2b `/skill-review` fixtures.
- **(c)**
  - The two-session subprocess test shows A's quote verbatim, and B's orchestrator-matched carry row beneath it, in the block that `/code-review` and RFR persist.
  - The lib-layer render fixture, with rows written on distinct dates, shows the decision's own date beside its words and the carry's date on its row. The subprocess test makes no date claim, because `review-ledger.sh:301` stamps the real clock.
  - `render --pr-json` preserves a legacy row.

**Pre-PR dry-run.**
- **Script level.** Work in a scratch git repository outside this repo, on a scratch feature branch. Call this worktree's `claude/.claude/scripts/review-ledger.sh` by absolute path, because the stowed `~/.claude/scripts` resolves to the main checkout's older script.
  1. Log a DEFER and an engineer SETTLED `--carry-forward`, each with a block range.
  2. In a second session (or after `/clear`), log a carry of each with `--cited-line` inside the block. Confirm each carry prints its carry line, and that `render` shows each decision once with the carry rows beneath.
  3. Edit the kept block, commit, and confirm that a carry of the keep is rejected while the untouched DEFER still carries.

  Use the live session's config dir. `review-ledger.sh` finds its session through `$CONFIG_DIR/sessions/<pid>`, which a scratch `CLAUDE_CONFIG_DIR` would not hold. The scratch repo's rows land in their own repo-hash file, which the sweep removes within 30 days. [verified: `_lib.sh:2328-2354`] [unverified: that `/clear` issues a new session id]
- **GFM check.**
  - Send a rendered block through `gh api` to GitHub's `/markdown` endpoint in `gfm` mode. The block holds:
    - a quote with `|`, `\|`, backtick runs and `<!--`;
    - plain cells with the same characters;
    - a 4-cell legacy row.
  - Confirm each cell renders in its own column, and that the quote shows verbatim.
  - [unverified: that the endpoint renders tables as PR bodies do]
- **Prose steps.** They are covered before merge only by the 2b `/skill-review` fixtures. The first real multi-session branch after merge is the smoke test.

**Review gates for each commit:**
- `/code-review`, invoking:
  - `claude-hook-review` for the hooks and `_lib.sh`;
  - `ai-instruction-and-memory-files` for `CLAUDE.md`;
  - `staff-sdet` for the tests.
- `staff-backend-engineer` on 1a, 1b and 2a, because shared `_lib.sh` changes.
- `ciso-reviewer` on 2a and 2b: quotes go to public PR bodies, and pinned stop regions change.
- `/skill-review` on every SKILL.md commit (hook-enforced).

Run `/ready-for-review` before the PR opens or is pushed.

**The PR body discloses:**
- **In-flight branches.** Branches in flight start with an empty ledger. The first new session re-decides earlier findings, so the PR block can list a finding twice: a kept legacy row and the re-decided row. Kept rows retire only by hand-editing the PR body, and deleting the ledger file clears none of them.
- **Kill switch.** `.review-narrative-ledger-disabled` is no longer honored, and there is no replacement opt-out.
- **Metrics.** The SETTLED-as-PASS metric cutover, and the two counters that no longer count mismatched sessions.
- **What a carry needs.** The text at the orchestrator-named range must be unchanged, and the reviewer's cited line must lie inside it. Only that range is hashed, so an edit outside it but inside the block does not reopen the decision.
- **DEFER carries.** A DEFER repeat carries without a stop when its criterion is restated and its text is unchanged.
- **Reviewers still re-raise.** A carried finding is still raised; "suppresses" in the acceptance criteria means no new stop.
- **Failure-mode reading.** How "a different failure mode" is read (row 18).
- **Published quotes.** Engineer quotes are published in the PR body. A credential pasted into one needs the owner to rotate it and delete the PR body revision; a closing row only stops later renders.
- **Rollback.** A revert leaves branch files that the sweep removes within 30 days. The next round on an open PR then rewrites the block in the DEFER-only 4-column shape.
- **Deferred residuals:**
  - a reused branch name is not filtered, though rows are dated; its rows also reach dashboard counts, and round numbering continues from the old maximum;
  - free text, engineer quotes included, is not scanned for credentials before it reaches the PR body;
  - a session reviewing on two branches drops out of the author-outcome headline.

## Out of scope

- **Scenario 4, append-time content validation.** Deferred.
  - Residual: free text is stored unfiltered, and ledger files keep the default umask, as today.
  - The table and delimiter risks are covered by `render`'s escaping (row 21), and the atomic-write bound by the row-size check (row 36).

  [engineer-verified: "#4 is low likelihood agreed I think we can defer those scenarios"]
- **A credential check on `--engineer-quote` and other free text.** Deferred.
  - Residual: a pasted token reaches the ledger file and the public PR body with no mechanical check. The PR-body redaction gate does not run `_LIB_CREDENTIAL_VALUE_REGEX` (row 22).

  [engineer-verified: "Defer it too"]
- **Scenario 5, branch-name reuse.** Deferred.
  - Residual: rows from a deleted branch of the same name, at the same worktree path, appear in `show`, `render`, `--ref` validation and the dashboard's counts. Round numbering also continues from the old maximum. An old engineer decision can be carried onto byte-identical text in the new branch and render into its PR body.
  - Mitigations are in row 13.

  [engineer-verified: "#5 is very unlikely because branch names have auto incremented ticket numbers in them usually and would be pretty easy to detects. I'd say defer those scenarios"]
- **Scenario 6, two-branch sessions in the analysis.**
  - Residual: a session whose round values fall across branches is excluded from the author-outcome headline and counted under the round-number-mismatch counter (row 11).
  - The engineer judged it unlikely; treating it as deferred is listed in row 29.

  [engineer-verified: "#6 is unlikely because sessions typically focus on one branch at a time"]
- **Folding `/ready-for-review` disposition records into the ledger.** This is the follow-up; 2b records its gap list. [engineer-verified: "Yes, follow-up is fine (Recommended)"]
- **Moving the round-3 gate's `.reviewer-round-state.d` state onto the ledger.**
- **Backfilling session-keyed rows into branch files** (row 25).
- **Resolving the branch name mid-rebase** (row 4), and **ledger continuity across worktree paths** (row 2).
- **`show` filter or summary flags** beyond the stderr header (row 26).
- **Extracting the Item-ownership table, or raising any skill's line cap** (rows 14, 30, 31). These reach the engineer only through phase 0's fallback.
- **Changing `_LEDGER_SWEEP_FLOOR_DAYS`** (row 32).
- **Deriving a site's range mechanically from a cited line** (row 39).
- **Restricting a carry's path to its decision's path unless that path is gone.** This would narrow row 39's short-common-text residual to one file. It is not adopted in this plan.
- **A count of accepted versus rejected carries,** to measure how often the hash guard forces a re-ask.
- **A transcript audit that checks each engineer quote against the session's user turns.** Rows now carry `session_id`, so this is possible as a follow-up.
- **Reviewer-side invariant classification** (row 16), and **redaction detectors at append** (row 22).
- **Editing the body of `round3-plan-architect-consult-gate.md:34` or `ready-for-review-fix-loop-convergence.md`** beyond the one supersession line. They are preserved records.
- **Issue #997 itself.**
- **A pre-existing inaccuracy, left alone:** `ready-for-review-fix-loop-convergence.md:37` calls the Completion restatement unpinned, but `test_skills.py:5723-5726` pins it.
