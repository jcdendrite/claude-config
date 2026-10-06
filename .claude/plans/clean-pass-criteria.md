# Redefine a clean cumulative review pass, grounded in a reclassification of past findings

## Context

Goal: change the criteria by which a `/code-review` pass counts as clean, so cumulative passes come back dirty less often. Bounding the number of rounds is GH-1213's job, and this plan feeds it evidence. The new criteria are grounded in a measured reclassification of past BLOCKER, CONCERN and FYI findings in this repo's `agent-reviews/` artifacts.

Ask: this session's invocation was `/plan-it` on the originating brief, `review-clean-pass-criteria-task.md` in the engineer's local briefs directory. That brief is agent-authored. It relays the engineer's words from the originating conversation, cited here to the brief rather than tagged as this session's quotes:
- (brief §2) "No. But we can change the criteria for what defines a clean pass."
- (brief §2) "In order to do that it would require going through and reclassifying the fyi changes (or whatever category we want to potentially defer on permanently) across all branches where they are still available in agent-reviews, and determine whether or not they indeed should be blocking."
- (brief §2) "Let's go with the proxies". The proxy definitions in brief §5 are the prior agent's wording, which the engineer accepted.

Why now: in `ready-for-review` step 3, the `cumulative-review` marker is written only when a full unnarrowed cumulative `/code-review` comes back clean. `code-review` § "Finding disposition" defaults every finding to ADDRESS, and it lists "FYI", "no correctness impact" and "cosmetic"/"non-blocking" as invalid DEFER rationales. So a pass is clean only when no reviewer leaves an unresolved row. On large diffs that state is rarely reached, because each fix adds new reviewable surface. PR #1009 is blocked on this today; fixing it is out of scope here.

Intended outcome: a reclassification study (read-only over the corpus) reports a miss rate for each candidate "permanently non-blocking" rule. The engineer picks the rules. The skill text and its pinning tests change to match. The marker's authorization rule does not change: it still means "a clean pass over this exact content", and only the definition of clean changes.

Engineer answers this session (selected labels):
- Publishing: "Aggregates, you confirm (Recommended)".
- Corpus: "All dirs, read-only (Recommended)".
- Study unit of analysis: "Ask the architect".
- Study script home: "Ask the architect".
- Proxy labeling scope: first "What does fable think" (typed), then, after a Fable consult was relayed, "Sequential census (Recommended)".
- After the first `/plan-review` round, the engineer typed:
  - "Judging exemptions requires Opus."
  - "there's another active session that is going to have Opus review all dispositions so you should coordinate with that one because that feature dovetails with yours and there should only be one opus (architect) dispatch not two."
  - Naming that session: "gh 1213, architect authors fix plan. It's currently parked waiting for another session to trim the code review skill".
- On R3, the engineer first answered "Ask the architect and come back to me". After a `plan-architect` consult was relayed, they selected "Drop R3 (Recommended)".
- Asked whether "Judging exemptions requires Opus" covers R1, R1b, R2 and R4 as well, the engineer selected "Yes, all rules".
- Sequencing against GH-1213: "Architect's order (Recommended)". The option described that order, in the session's wording: merge #1216 now and run Phase 1 now; Phase 2a waits for the code-review trim, then GH-1213; Phase 2b's loop change moves to GH-1213 as input.
- After the second `/plan-review` round, the engineer selected:
  - Scope growth into the `claude/` ledger files: "Keep (Recommended)".
  - How to record item-6 resolutions (blocker 6): "Take B (Recommended)". The option was the session's relay of the architect's proposal, not the engineer's words: record each item-6 resolution as `SETTLED --decided-by plan-architect` with no new criterion value, so Phase 2a edits nothing under `claude/`.
  - The model split: "Take the split (Recommended)". The proposal, in the session's wording: Opus for the runtime judge, Tier A, Tier B and the ground-truth check, and Sonnet for the blind exclusion labels and the second labels. Row 73 records the Sonnet Tier B fallback as part of that proposal.
  - Whether the Ask's "defer on permanently" means the conditional keep: "(a) Conditional keep (Recommended)".
  - Whether to drop proxies P3 and P4: "Confirm the drop (Recommended)".
- After the third `/plan-review` round, the engineer asked what the Sonnet Tier B fallback was, then selected "Keep it (Recommended)". The session's question said the fallback applies only to the one-time study labels and never to the runtime judge.
- After the fourth `/plan-review` round:
  - On how to handle the remaining findings, given that rounds 2-4 kept finding new Phase 1 machinery defects, the engineer typed "Ask the architect".
  - After a `plan-architect` consult was relayed, they selected "Apply it (Recommended)". That option's text was the session's relay of the consult's recommendation, which row 101 records.
  - On keeping `CHANGELOG.md` in the file list, they selected "Keep it".
- After the fifth `/plan-review` round:
  - On how to close out the review, the engineer typed "Ask the architect. Let's not fight the sdlc".
  - On whether the held-out Sonnet dispatches stay within "Judging exemptions requires Opus." given that they use R1's mechanical membership only, they selected "Yes, mechanical only (Recommended)".
  - After a `plan-architect` consult was relayed, they selected "Apply the disposition (Recommended)". That option's text was the session's relay of the consult's disposition of the round-5 findings (fifteen plan-text edits, three items deferred to the pre-Phase-2a revision, three declined, and the script-internal parts sent to the step-1a prompt), not the engineer's words.
  - After the plan code review and the round-6 `/plan-review`, they selected "Keep general-purpose, state why (Recommended)" for the labeling agents and "Add to step 8 and the re-ask (Recommended)" for the steered-orchestrator path (rows 104 to 106). They also asked the architect about both selections, and its consult shaped the second fix pass.

## Approach

The study measures how often each candidate "non-blocking" rule would have exempted a finding that should have blocked. It covers every past disposition row in every `agent-reviews/` dir of this checkout, and the engineer picks rules from that evidence. The picked rules ship as one pinned keep rule in `code-review` § "Finding disposition", outside the closed DEFER list. Only GH-1213's per-round Opus architect may apply it, and only in a cumulative pass where no other finding needs a fix. A missing, hedged or uncertain call leaves the row ADDRESS. Each use is logged as a SETTLED row decided by `plan-architect` (rows 69 and 70). No branch gets any relief until GH-1213 merges, because that dispatch is the rule's only applier. The change makes dirty cumulative passes rarer, but it does not bound the number of rounds. Bounding rounds is the job of GH-1213 and the Cap, and this study's rounds-to-clean evidence goes to GH-1213 as input (rows 50 and 51). Both clean-pass sentences, the `cumulative-review` marker, the review ledger and their pins stay as they are. Only what counts as *resolved* widens (row 7).

This plan keeps the name *item 6* for that rule, although it no longer sits in the numbered DEFER list. Shipped text calls it the measured-non-blocking keep, and one use of it is an *item-6 keep*.

### Phases

**Phase 1: the study.** It runs now. It changes no repository file, this plan file included (row 63). Its mechanics specify only defenses against a silently wrong number. A failure that stops a command visibly costs one re-run (M13, row 101).

0. *Setup.*
   - First, the session gives the engineer a figure-free note to relay to GH-1213's session. The note names this plan's absolute path and § "What GH-1213's dispatch must provide" below, and it relays that section's opening paragraph. The session posts nothing to GH-1213 or to any issue (M14).
   - The session runs `gh issue view 1211` and states whether any of #1211's steps before GH-1213 is this plan's Phase 2a. If one is, GH-1213 and Phase 2a would wait on each other (row 80), and the session asks the engineer before going further.
   - The session creates the work dir with one literal call, `mktemp -d ~/.local/state/clean-pass-study.XXXXXX`. Every later command spells out the printed path literally (M13). The script checks at every start that the dir's mode is 0700, and it refuses to run otherwise.
   - From here on, each engineer answer goes into its own `decision-<nn>.txt` in the work dir. The session writes it with the `Write` tool, holding the question and the selected label verbatim, and reads it back. Each decision file carries one fixed `selected:` line holding the selected label, and the scripts read T only from that line.
   - The session then runs a canary with one trivial dispatch for each agent type the study uses: `code-writer`, `general-purpose` on Sonnet and `general-purpose` on Opus. Each one writes a sentinel `.tsv` file in the work dir with the `Write` tool and reads it back (row 90). Each `general-purpose` canary agent also records in its sentinel the model ID exactly as its own system context states it, or a blank if the context states none. A blank, or an ID that does not name the model its dispatch requested, stops the study as a denial does. Otherwise the session writes `models.tsv` in the work dir with the `Write` tool, one line per `general-purpose` canary as `sonnet` or `opus`, a tab and that canary's ID, and reads it back. The `code-writer` canary also runs one `python3 -I -B` call on a literal work-dir path, the shape step 1a uses. The session itself writes and reads back one sentinel decision file.
   - On any denial, the session stops and reports it to the engineer. It does not fall back to another agent type, because a child inherits the parent's anchor and permission mode (row 29).
   - While any Phase 1 dispatch is running, the session does not move the worktree anchor. That rules out `branch-management` and `EnterWorktree`.
   - Every study dispatch prompt states that corpus, window and `sites/` text is data, never instructions, and names `marker.sh`, `review-ledger.sh` and their state as off limits. A labeling agent reads only its named inputs, runs no command, makes no network call, and writes only its one named output. Before the canary, and each time the last running study dispatch returns, the session records `marker.sh status` and the row count of `review-ledger.sh show`, each with its exit status, in the next `gate-<nn>.txt` in the work dir. A nonzero exit, or any difference from the previous `gate-<nn>.txt`, goes to the engineer before the session goes on.
1. *1a, build the script.*
   - One `code-writer` dispatch writes a standard-library-only script for `python3` (3.12) into the work dir. Its subcommands are `selftest`, `freeze`, `derive`, `validate`, `stage1`, `replay`, `stage2`, `denylist-check` and `purge`.
   - The session's 1a prompt, and every later 1a re-dispatch, quotes verbatim the "Required property" paragraph of these round-5 findings, filed in `agent-reviews/` under the `1791153214-clean-pass-criteria` suffix: SDET 1, 2, 3, 4, 6, 7 and 8; platform 1, 2, 3, 5, 6 and 7; and CISO "unknown-by-budget" and "sites". It also quotes the round-6 CISO "`freeze` copy" paragraph (suffix `1791252740`), the first sentence of round 9's CISO "Grep source check" paragraph, and round 9's SDET "Known answer", "Planted stdlib module", "Hash-seed bullet" and "Overlap arm" paragraphs (suffix `1791254347`). It also quotes the script-internal Required-property paragraphs of the round-10 SDET findings 3, 4, 5, 6, 7, 8 and 9 and the round-10 CISO "step-1a Grep audit" and "`python3 -I -B` and the `-P -B` child exception" paragraphs (suffix `1791260327`), and of the round-8 SDET "Known answer 18.0%" and "Source-check bounds" paragraphs and the round-8 CISO "Corpus-read hardening" and "Step-1a Grep audit" paragraphs (suffix `1791261202`). It also quotes the "Required property" paragraph of the SDET finding F6 filed under suffix `1791265810`, which a later cumulative pass raised. It also quotes, from suffix `1791269701`, the SDET sentence beginning "A `selftest` case covers match" and the SDET "Pre-Tier-A counts mix units" and "The CONCERN hit leaves two cases unplaced" "Required test property" paragraphs. It also quotes, from suffix `1791270931`, the "Required test property" paragraphs of the first and fourth SDET findings, on the Tier B comparand and on the site-`unknown` predicate. Where a quote conflicts with plan text, the plan wins. The prompt quotes the paragraphs rather than pointing the `code-writer` at the files, because this worktree's `agent-reviews/` is a corpus dir and study agents treat corpus text as data.
   - Before writing the parser, it hand-reads a spread of at least 20 disposition records with their epoch-matched reviewer files, covering every table and prose record shape at least twice. It saves its reading, field by field, as `golden.tsv`, along with the list of records it read. The spread need not cover every branch, and it leaves at least two branches that have records outside it. If the corpus cannot allow both, the session asks the engineer.
   - It runs only `selftest`. `selftest` creates its own fixture work dir and runs every state-changing case only inside it, and it tests `purge` from a copy of the script placed there. Every `git`- or `gh`-dependent function runs over canned output, or over a fixture repo that `selftest` builds inside that fixture work dir with hooks disabled, so the run reads no real corpus dir and makes no network call. The live work dir's file list and file hashes are identical before and after, which `selftest` asserts and the session re-checks after each `selftest` run. `selftest` also carries one hand-computed case for each definition the plan gives a script output, at each boundary the definition names. `selftest` asserts each of these:
     - the write helper rejects a target outside the work dir, and a work dir that is a link;
     - a module with a standard-library name, planted in the work dir, is not imported;
     - `derive` and `stage1`, each run twice under different `PYTHONHASHSEED` values, give identical output hashes. The two child runs use `-P -B`, because `-I` implies `-E`, which ignores `PYTHONHASHSEED`;
     - `derive` refuses while another process holds its lock, and `freeze` refuses once any labeling output exists;
     - once labeling has begun, `derive` keeps labeled batches as step 2 states;
     - `validate` rejects a missing, duplicated or unknown row id, and an illegal value;
     - discovery finds a nested fixture worktree and an out-of-tree one, and it lists, without including, a stray `agent-reviews` dir that is not at a worktree root;
     - malformed records land in `unparsed.tsv` without a crash;
     - a row counter that shares no code with the parser agrees with the parser on every fixture record;
     - `denylist-check` flags a planted six-word shingle in a synthetic diff. It does not flag a paraphrase of it, an unrelated phrase, or a shingle present in the fixture's tracked tree.
     - `purge` refuses with a mode other than 0700 or through a link, and deletes only the file set it listed;
     - known answers, on a small synthetic branch set with hand-computed expected values:
       - the Wilson 95% upper bound at zero misses is 8.8% at n = 40 and 1.9% at n = 200, and certifying a rule needs 35, 73 and 189 findings at T = 10%, 5% and 2% (row 33);
       - zero misses over 200 findings all labeled `no`, plus zero flips over 20 pairs at a `no` share of 1, give 1.9% + 16.1% = 18.0%;
       - single-branch deletion picks the worst branch in the claim's direction, on a case where the below-T and above-T directions pick different branches;
       - the window range, including a rebased commit whose author-date order differs from its topological order (row 24);
       - the split into parts at the 2000-line boundary, with its line-count manifest (row 59);
       - the byte-cut batch boundary: a branch over budget splits by pass range, and a row whose own inputs exceed the budget becomes `unknown-by-budget`;
       - the stop pass (M9). The first of two qualifying cumulative passes is the stop. A pass with no unresolved rows is a stop but not a flip. A pass whose unresolved rows all qualify, beside carry-resolved rows, is a stop and a flip. A dirty pass holding a row resolved by a stop-and-ask or a consult verdict, a pass holding an unresolved BLOCKER row, an unknown-kind pass and a staged-kind pass are never stops.
       - the ceiling, `stop-pass-exposed`, `no`-eligibility, the flips, the adjusted upper bound and the early-stop condition.
   - A separate `general-purpose` dispatch (`model: sonnet`) then hand-reads, writing `heldout-1.tsv`:
     - five records that are not on the golden list;
     - two whole branches outside the golden spread. For each one, it derives every pass's kind and ordinal, and the stop pass under M9 with R1's mechanical membership alone (every source labeled FYI). It judges no exclusion, so no Sonnet dispatch judges an exemption (rows 45 and 46).
   - After both dispatches return, the session runs `selftest` once itself (row 29).
   - Before the first `freeze`, and after any 1a re-dispatch, the session (not the 1a author) uses Grep on the script to list every import, every process launch, every call that writes, renames or deletes a path, and every dynamic import, eval or exec. It checks each hit against these bounds: standard-library imports only, with no network module; step 2's `git` subcommands and the `gh` capture; the write helper; and purge's self-discovered target. It records each pattern and its hit count in a decision file. A search with no process-launch hit or no write-helper hit has missed, and it is re-run. A hit outside those bounds re-dispatches 1a. The check catches an honest deviation from those bounds, not a steered or tampered script (row 107).
2. *Freeze and derive.* The session runs each subcommand as one literal `python3 -I -B <work dir>/<script> <subcommand>` call from this worktree, in the background when it may outlast a foreground call (row 88). Each prints one terminal line and exits non-zero on failure, and after a fix it is re-run whole.
   - Every `git` and `gh` subprocess runs under a timeout whose value and basis the 1a prompt states.
   - It runs every corpus-facing `git` call with `--no-optional-locks` and only these read-only subcommands: `log`, `show`, `rev-parse`, `merge-base`, `worktree list --porcelain`, `for-each-ref`, `ls-tree` and `diff`. It never uses `-C`, never changes into another worktree, and never fetches. `selftest`'s fixture builder is a separate path that runs `git` only inside its fixture work dir.
   - `freeze` does these, in order:
     - finds the corpus itself (M3, row 66) and writes `discovery.tsv`;
     - copies every regular file into `snapshot/`, judged by `lstat` and following no link, keeping only files modified at or before its start time, and lists each skipped entry in `discovery.tsv`;
     - records each branch's tip sha, the default branch's sha and the seed, and copies this plan file as `snapshot/plan-baseline.md` with its sha256. That copy is the text `/plan-review` passed, because Phase 1 edits no repository file.
     - saves `gh pr list --state merged --head <branch> --json number,mergeCommit` for each branch that has records, using the branch name rather than the dir name. A failure or timeout records `unknown` with its error, and `freeze` still completes.
     - writes its sha256 manifest last. Every later subcommand refuses without it.

     `freeze` can be re-run from scratch only while no labeling output exists.
   - If `discovery.tsv` lists an excluded `agent-reviews` dir, the session asks the engineer, before `derive`, whether row 1's "All dirs" covers it. A "yes" re-dispatches 1a to include that dir and re-runs `freeze`.
   - `derive` takes an exclusive lock in the work dir, deletes `derive.done` and its own earlier outputs, and regenerates every output below from the snapshot and the recorded shas alone. It writes `derive.done`, holding its outputs' hashes, last, and every later subcommand refuses without it. Once labeling has begun, `derive` instead builds in a scratch dir, never deletes or changes a file a labeled batch read, and installs only new batches. If any labeled batch's inputs changed, it refuses and changes nothing. `derive --check` regenerates into a scratch subdir, keeping the labeled batches the same way, and reports whether the result is identical. It also lists every difference between the live corpus dirs and the freeze manifest, additions included.
     - `rows.tsv`, one line per disposition-record row, with these fields:
       - a row id built from the record's path and row index, which every label is keyed by;
       - branch, record epoch, and the pass's ordinal within the branch;
       - pass kind (cumulative, staged or unknown), read from the record's title, body and filename qualifier;
       - record shape;
       - finding cell, sources, each source's raw severity, raw and normalized disposition, and the Outcome cell. `SETTLED <id>` and `carry <id>` Outcome cells normalize too. A flag marks an Outcome that shows a stop-and-ask or a consult verdict (row 102).
       - label, and the join step that produced it;
       - cited paths and their path class: production logic, test, non-logic prose, or guard path. Guard paths are a sampling frame for the blind exclusion labels and are not shipped. They are:
         - anything under `claude/.claude/hooks/`;
         - `marker.sh`, `review-ledger.sh`, `_review-ledger-lib.sh` and `pr-diff-against-base.sh` under `claude/.claude/scripts/`, with their tests;
         - both `settings.json` files, and `.github/workflows/`;
         - `docs/hooks.md`, `docs/private-project-redaction.md` and `docs/security-hardening.md`;
         - the `code-review`, `ready-for-review` and `plan-review` skill files, and `claude-skills/skills/tests/test_skills.py`;
         - both `CLAUDE.md` files, `.claude/rules/` and `claude/.claude/rules/`.
       - two mechanical exclusion flags: a BLOCKER, Critical or High label from any source, and a `ciso-reviewer` source;
       - membership in each rule: `yes`, `no` or `needs-judgment`;
       - P1's overlap arm, which applies to a row whose `path:line` token is in a production-logic file. It is `yes` when the first window commit touching that file has a hunk whose pre-image covers the cited line, and otherwise `unknown`, never `no`;
       - R3's census-only upper bound: an earlier pass on the branch kept, SETTLED or fixed a block at the same cited path (row 49).
     - `unparsed.tsv`, `dropped-bullets.tsv`, a label-mix table that compares joined rows with the same-epoch bullets, and a field-by-field diff of its own output against `golden.tsv` and the newest held-out file.
     - `windows/<branch>/<pass>/`. A pass's window holds the commits reachable from the recorded tip, but not from the merge-base with the default branch, whose author date falls between this record's epoch and the next. Each commit's `git show` output is saved there in numbered parts of at most 2000 lines, with a line-count manifest (row 59).
     - `sites/<branch>/<pass>/`. For every row with a `yes` or `needs-judgment` membership, this holds each cited file as of the pass's tree, in parts of at most 2000 lines, which is what the runtime judge could read (row 75). A pass's tree is the newest commit, in the order windows use, whose author date is at or before the record's epoch. It also decides whether a cited site existed at the stop pass (row 37). A cited file absent there is recorded `absent-at-pass`.
     - `batches.tsv`. Every judgment tier's batches are cut by the bytes of the files that batch's agent reads, against one per-agent budget. A branch over budget splits by pass range, and the later range's agent gets the earlier range's output file. A row whose own inputs exceed the budget is marked `unknown-by-budget`.
     - `ceiling.tsv`: for each rule and for the union, the most dirty cumulative passes it could turn clean. It counts the passes whose unresolved rows are all `yes` or `needs-judgment` members, with only the two mechanical exclusion flags applied, and it counts separately the passes M9 bars. It also gives each held-out branch's stop pass, computed as the held-out dispatch computes it.
     - `denylist.txt` for Phase 2a: every six-word shingle of every finding cell, plus every corpus branch slug, minus every shingle present in the tracked tree at the recorded default-branch sha. Matching lowercases text, collapses whitespace, joins wrapped lines, and strips Markdown emphasis, code ticks, table pipes and list markers. It reads added lines only.

   **Labeling outputs.** Every labeling dispatch writes one file with the `Write` tool, under the fixed per-batch name `batches.tsv` assigns, and returns only its path (row 90). The file's first line is `model:`, a space and the model ID exactly as the agent's own system context states it, and one line per row id follows. `validate <name>` is read-only. It checks that the `model:` line is present and not blank, that each expected row id appears exactly once and that every value is legal, and it counts rows itself. Every aggregation refuses a batch that does not validate. It also refuses unless all files of a tier it reads hold the same ID from `models.tsv`: the `opus` ID for Tier A and the ground-truth agents, the `sonnet` ID for the blind labels, and, for Tier B and the second labels, two different ones. That refusal goes to the engineer, and the packet header states each tier's ID. The session re-dispatches a batch only after its earlier dispatch has returned, and a batch that fails validation twice goes to the engineer.

   **Halt rule.** The study stops on any mismatch against `golden.tsv` or the newest held-out file in disposition normalization, the Outcome's fix or no-fix reading, the stop-and-ask or consult-verdict flag, pass kind, pass ordinal, or a held-out branch's stop pass. The session reports the mismatch and checks the disputed record against the plan's definitions before it re-dispatches 1a for the parser fix. After any script change, the session runs `selftest` before the next `derive`, and `derive.done` and the packet header record the script's sha256. Then, in this order:
   - a fresh `general-purpose` dispatch (`model: sonnet`) draws a new held-out file from `snapshot/`, as step 1a's held-out dispatch does, from branches outside golden and every earlier held-out file, because the old one has become tuning data;
   - the session runs `derive`;
   - `derive`'s diff compares against the new file.

   A second halt goes to the engineer, and so does a draw with no branch left.

   **Pre-Tier-A question.** Otherwise, the session states these in session only:
   - first, `ceiling.tsv`'s figures for each rule and the union: the branches with a stop pass and the passes after each stop, which would not have run, with the totals of dirty cumulative passes and of branches holding one as denominators, and the passes M9 bars. If the ceiling is zero for every rule, it says so first. It also says whether the ceiling flips PR #1009's latest cumulative pass.
   - the other fields' mismatch counts, the join rate for each join step, and the count of rows forced to `unknown` (M13);
   - `freeze`'s per-dir counts of files copied and files skipped as newer, and `derive --check`'s list of differences between the live corpus dirs and the freeze manifest;
   - the counts of disposition records found, records fully parsed and records with a block in `unparsed.tsv`, the dropped-bullet count, the ambiguous-row count and the `needs-judgment` cell count;
   - the planned Tier A and blind-label dispatch counts, and the total input bytes for Tier A on Opus and for the blind labels on Sonnet;
   - the per-agent byte budget with its basis.

   It then asks through `AskUserQuestion` whether to start Tier A: "Start Tier A (Recommended)" or "Stop". When every rule's ceiling is zero, the options read "Start Tier A" and "Stop (Recommended)". The selected label goes into a decision file.
3. *1b, Tier A (rule membership, on Opus; rows 45, 53, 72 and 73).*
   - `general-purpose` agents (`model: opus`) run one per batch from `batches.tsv`. Each one does four things:
     - it settles every `needs-judgment` cell;
     - it flags the rows that a judged exclusion removes: the enforcement-invariant, guard-surface, security-control and data-exposure exclusions (see Candidate rules);
     - it marks `near-boundary` every row whose exclusion or membership call it would not make with confidence. A `near-boundary` row counts as excluded, as item 6's fail-closed defaults require (row 85), and so does a row that `batches.tsv` marks `unknown-by-budget` for Tier A.
     - it marks as not qualifying a row for which no in-repo file can serve as a keep's `--source` (row 92).
   - Its inputs are limited to what the runtime judge will have (row 75): the finding cell, its sources' bullets in that round's findings files, and the cited files under `sites/`. For R2's first condition only, it also reads the pass's window, which stands in for the diff R2 would need at runtime.
   - Each writes `tierA-<batch>.tsv` and returns only that path.
   - Blind exclusion labels: `general-purpose` agents (`model: sonnet`), batched by branch from `batches.tsv`, label every exclusion for every row with a `yes` or `needs-judgment` membership in any rule (row 98). They get the same inputs and the same exclusion text as Tier A, and they do not see Tier A's labels. Each writes `blind-<batch>.tsv`. A row the blind label excludes and Tier A does not goes on the hand-check list. A row Tier A excludes and the blind label does not is counted only, because Tier A's call was the stricter one.
4. *Aggregate, stage 1* (`stage1`). The script writes:
   - the *union*: every row that at least one candidate rule exempts once the exclusions are applied, with each row's rule memberships;
   - for each rule, its exempted-row count, how many rows only that rule exempts, and how many rows each exclusion removed. `near-boundary` rows, Tier A's `unknown-by-budget` rows and Tier A's no-in-repo-source rows (row 92) are their own counts.
   - for each rule, a `stop-pass-exposed` flag on each union row: the row is an unresolved row of the pass where M9 stops its branch, which is the only row the rule would keep under M6 (row 76);
   - for each rule, its `no`-eligible count: union findings whose branch has a later cumulative pass. This is an upper bound, since `repeat-of` arrives only with Tier B (row 77).
   - flips: how many dirty cumulative passes would turn clean under each rule, under each combination of rules, and under the union, plus the branches that stay dirty under every rule. The passes M9 bars are counted beside them. Every flips figure is labeled an upper bound, because only the runtime dispatch can show whether its verdicts cover a pass.
   - a seeded branch order in three waves, as equal in union-row count as whole branches allow (M11);
   - the M9 manifest for each rule: every BLOCKER row after the counterfactual stop, plus a seeded 20 CONCERN rows after it. Only a cumulative-kind pass is a stop candidate, and passes of unknown kind are listed.
   - the ground-truth population: candidate rows on merged branches, grouped by historical disposition.
5. *Threshold, scope and cost.* The session first states these, in session only:
   - the union count;
   - for each rule: its exempted count, the rows that `near-boundary` and row 92 removed, its `stop-pass-exposed` count, its `no`-eligible count, and its flips as step 4's upper bound, stated in the pre-Tier-A ceiling's form and beside that ceiling, with whether they flip PR #1009's latest cumulative pass;
   - for each rule and each T option, the floor of the adjusted upper bound, computed on the rule's `stop-pass-exposed` stratum with the pooled floor beside it. The floor is the detected worst-case upper bound when the rule's `no`-eligible findings are all labeled `no` and its other exempted findings `unknown`, plus the flip term. The flip term is the flip rate's Wilson upper bound at zero flips over as many pairs as second-labeling every `no`-eligible finding in the union would yield, times the rule's `no`-eligible share. A floor below T is necessary, not sufficient, for a below-T claim, because realized labels and flips only raise the bound. The session also states whether any rule's floor is below the loosest T option, and shows the floors the same way when T is deferred;
   - the never-clean tail;
   - the windows the union touches, and the planned dispatch count and total input bytes for each remaining tier, under each Tier B model option. The second-label figure prices every `no`-eligible finding from `batches.tsv` bytes on that option's second-label model. It is stated as the price of that set, not as a bound on step 6's second-label cost, because step 6 second-labels every finding whose miss label is `no` and every row with an `unsure` P1 or P2, and neither set is known before Tier B. That figure is the same under every T option, under "Decide at the checkpoint" and under "No T", because no T-specific count applies;
   - the ground-truth population, or that it is unknown.

   Then, before any Tier B label exists, it asks through `AskUserQuestion`:
   - *The acceptable miss rate T.*
     - The options are 2%, 5% and 10%. Each option names the fewest findings, all labeled `no`, that certify a rule when the flip-rate term is left out (row 33). It also says that the flip-rate term raises that number, and it names the rules whose floors lie below that T.
     - In session only, each option also states what T means per branch: T times each rule's `stop-pass-exposed` rows on a median branch and on the largest branch.
     - "Decide at the checkpoint" is always offered (row 35). Its text states that all waves then dispatch together with no early stop, and that T is asked at step 8 before any rule's rate is shown.
   - *Rules to drop now.* The engineer may drop candidate rules whose benefit is negligible.
   - *The Tier B model.* The options are "Opus (Recommended)" and "Sonnet, with Opus second labels", each with its dispatch cost and step 5's second-label figure, with step 5's statement that the figure is not a bound. The engineer's sentence "Judging exemptions requires Opus." is quoted beside them (rows 45, 72, 73 and 84).
   - *The ground-truth check.* Whether to run it, given its population.

   Each selected label goes into a decision file. `replay` and `stage2` take T's decision file as their argument and print the T they read. T never enters this plan or any committed file unless row 27 authorizes it as a figure (row 61).
6. *1c, Tier B (proxies), in waves.*
   - Within a wave, `general-purpose` agents run in parallel, one per batch of that wave's branches from `batches.tsv`, on the model chosen at step 5.
   - Each agent labels P1 and P2 for every union row in its branches. Each label is `yes`, `no` or `unknown`, with a confidence of `sure` or `unsure`, `read_complete` (whether the agent read every part of every window it relied on) and an evidence pointer. The pointer is a commit SHA plus path, or a later record's path plus row index.
   - The agent also marks `repeat-of: <row>` when a row restates an earlier row on the same branch.
   - For each M9 CONCERN row, the agent labels P1 and P2 and whether the cited site existed at the stop pass, as `yes`, `no` or `unknown` (row 37).
   - For each M9 BLOCKER row, the agent labels only whether the cited site existed at the stop pass. The script counts the row as an escape when its Outcome shows a fix.
   - The script fills the site-existed field itself when the row carries a `path:line` token.
   - Each agent writes `tierB-<wave>-<batch>.tsv` and returns only that path.
   - If T is on file, the session runs `replay` after waves 1 and 2, which computes each rule's stop check from the validated outputs (row 41). Later waves skip rows that only stopped rules exempt, and `replay` logs each stop with its wave. If no T is given, all three waves dispatch together, because the order then has no function.
   - If the engineer chose the ground-truth check, `general-purpose` agents (`model: opus`) run alongside wave 1, one per batch of merged branches (row 38). Each judges every later default-branch commit, after the merge commit, that touches a path a candidate row cites. For each commit, it records whether the commit fixes the failure mode a candidate row named, as `yes`, `no` or `unknown`, with an evidence pointer.
   - After the last wave, `general-purpose` agents label some rows a second time, batched by branch, without seeing the first labels (row 39). They run on Sonnet when Tier B ran on Opus. They cover:
     - every finding whose miss label is `no` (row 108);
     - every row with an `unsure` P1 or P2.

     Under the Sonnet fallback, the second labels run on Opus instead.
7. *Aggregate, stage 2* (`stage2`). The script builds the results packet described under Verification. The packet's header gives each hand-check category's count, split by direction. Hand-check verdicts go in separate verdict files, each line a row id and a verdict, which `stage2` reads and never writes. `stage2` writes the hand-check list as `handcheck.tsv`, in list order:
   - every exempted row and every M9 escape whose miss label is `yes`;
   - every second-labeled row whose two miss labels disagree;
   - every row the blind exclusion label excludes and Tier A does not;
   - every row whose overlap arm is `yes` and whose Tier B P1 is `no`;
   - every ground-truth hit on an exempted row that Tier B did not label a miss.

   The list is ordered by rule. Within a rule, rows come in the order that most moves that rule's bound. When T is on file, a mark row follows the point after which no unchecked row can move any rule across T.
8. *1d, checkpoint.*
   - If T was deferred at step 5, the session first asks for it, before it shows any rule's rate. The options are step 5's three values plus "No T". The answer goes into a decision file, and `stage2` re-runs to set the mark.
   - The session presents the packet in session. Every flips figure in it carries step 4's upper-bound label. Next to each rule, the packet shows:
     - the recorded decision text the rule would overturn: §16's cost-of-not-fixing rationale and `round3-consult-verdict-routing.md` L24-28 for every rule, and `code-review/SKILL.md` § "Finding disposition"'s "triage signals, not dispositions" sentence for R1 and R1b;
     - the cost each rule was measured against. P1 and P2 detect shipped defects, not §16's cost to future readers.
     - the gate chain that a clean cumulative pass feeds, with that gate's tier (row 79). R1 is marked as resting on a reviewer label alone, with R1b shown beside it.
   - The engineer works `handcheck.tsv` in order, up to the mark, or to the end when no T is on file. Their verdicts go into a verdict file, which they write, or the session writes with the `Write` tool. They may stop at any point, and the rows left count as unchecked. `stage2` then re-runs, reading every verdict file. A row left unchecked keeps its agent label.
   - The session then asks through `AskUserQuestion`:
     - which rules to adopt: R1, R1b, R2 or R4, or none. R1 includes R1b. The option labels are rule names only, and any figure stays in the option descriptions. Each option's description names the gate chain and tier whose prerequisite it widens (row 79). It also states that the measured miss rate bounds honest reviewer misses only and says nothing about a label a reviewed diff steered. It also states: An orchestrator steered by reviewed content can log an item-6 keep with no architect dispatch, as it can log a contradiction-route keep today. Item 6 widens the findings such an unverified row can clear, and the clean pass that follows writes a genuine completion marker.
     - which aggregate figures, if any, to publish, asked one figure at a time (row 27). Handing a figure to GH-1213 counts as publishing it, because GH-1213's plan ships in a public PR.
   - Each selected label goes into a decision file.
   - The session writes `gh1213-input.txt` with the `Write` tool and gives the engineer its path for the GH-1213 session. Its first line says that a figure in it may enter committed text only where its row-27 authorization is cited next to it. It points to § "What GH-1213's dispatch must provide" for the standing requirements, and it holds only the study-dependent parts:
     - which rules were picked, and for each, which of that subsection's inputs it needs. R2 alone needs the fix-commit diff.
     - which way rounds to the first clean pass and the never-clean tail moved under the picks. A figure appears only if the engineer authorized it for hand-off.
     - which way the R3 upper bound pointed, offered only for GH-1213's author to use or drop, next to the carry requirement in that subsection (row 87).
9. *1e, top-up (only if needed).* Tier B labels the rows it has not seen if any of these happens:
   - the picked rules together stop a branch earlier than any single rule did, which brings in new M9 rows;
   - the engineer rewords a rule;
   - the engineer picks a rule that early rejection stopped;
   - the hand-check brings a stopped rule's lower bound back to T or below.

   Where a top-up needs new rows or batches, the session re-runs `derive`, which keeps every existing batch. Stage 2 then runs again. A rule ships only in the form the study measured.
10. *Retention and resume.*
    - The work dir stays whole until one of these happens:
      - Phase 2a merges and GH-1213's plan has read its input;
      - the engineer parks item 6;
      - GH-1213 is abandoned.
    - Then the session lists every `clean-pass-study.*` dir under `~/.local/state/`, names the one whose freeze manifest is the study's, and asks before running that dir's `purge`. `purge` discovers its target from its own location and the freeze manifest, never from an argument, follows no links, and deletes only the whole work dir. `purge --list` prints the file set. `purge --confirm <first 12 hex digits of that list's sha256>` deletes exactly that set, after the engineer confirms it.
    - Any handoff during the study names the work dir and what Phase 2a waits on: the trim's merge and GH-1213's merge. Nothing polls for those merges: the engineer starts the revision.
      - The trim's merge is answered by `gh pr list --state all --head <branch> --json number,state,mergedAt`. The branch is read from the trim worktree's registry entry, which today is `GH-1079/relocate-code-review-content` (row 91). An empty result means unknown, never "not merged".
      - GH-1213's merge is the merged PR that changes the per-round dispatch, found the same way once its implementing branch is known (G6). The issue's state alone does not count.
    - After a compaction or resume, the session lists those dirs and takes the one the handoff names. If no handoff names one and more than one holds a freeze manifest, it asks the engineer. Its first step in that dir is to read every decision file and restate the recorded answers, and it never re-asks a question that already has a decision file. It re-runs `stage1`, `replay` and `stage2` whole before reading their outputs. It then runs `validate` on every batch and re-dispatches only the batches with no valid output, taking Tier B's from the earliest incomplete wave only and running `replay` between waves.

**Pre-Phase-2a revision (after the code-review trim and GH-1213 merge; M12).**
- Once both have merged and this branch is synced to main, the session prepares a `plan-it` Step 5 revision re-dispatch on this plan. That dispatch goes to `plan-architect`, which holds only `Read`, `Grep` and `Glob` (row 75), so the session does these first and passes their results in the prompt:
  - it re-asks the engineer, through `AskUserQuestion`, to confirm each pick recorded in the decision files. The same question shows, verbatim, option B's sentence that the orchestrator writes every ledger row, for the engineer to confirm (row 70). The revision records `[engineer-verified]` rows from that answer. When a rule was picked, it also shows step 8's steered-orchestrator sentences, and asks whether Phase 2a adds an allow test and a deny test for the `plan-architect` successor path, a `claude/` edit under row 68, or ships item 6 without them. The revision records an `[engineer-verified]` row for each answer.
  - it re-runs step 10's merge queries and `gh issue view 1211`.
- The revision stops before any edit and returns to the engineer in any of these cases:
  - a pick the engineer did not re-confirm, or a re-ask that could not be made. Phase 2a never starts with a picked rule tagged `[unverified]`.
  - GH-1213 merged without per-round dispositions authored by `plan-architect`;
  - GH-1213's per-finding return cannot express an item-6 keep that the orchestrator logs `SETTLED --decided-by plan-architect` with the rule token (row 95);
  - GH-1213's merged lib or tests let a `plan-architect` SETTLED row carry without an explicit per-row opt-in made when the row is logged, or let item 6 set that opt-in (rows 65 and 87);
  - GH-1213 was abandoned;
  - #1211's ordering makes GH-1213 and Phase 2a wait on each other (row 80).

  The engineer's choices are then to park item 6 or to reopen row 47. The revision picks neither.
- Otherwise, the revision:
  - checks that GH-1213's merged dispatch supplies each picked rule's inputs and the pinned region's text, and that each per-row verdict names its rule from that text (row 75). A rule whose input is missing goes back to the engineer: drop it, or re-measure it through 1e without the condition that needs that input.
  - re-derives row 65 from GH-1213's merged lib and tests.
  - fits the wording of item 6's application conditions, fail-closed defaults and repeat routing to GH-1213's merged text. That includes the contradiction-route sentences (row 71) and the clean definition (row 9).
  - re-derives the edit sites and the line budget (row 15) against that main, and decides whether item 6 ships as one paragraph or two. It names a recovery site only with the engineer's answer (row 67). If no site exists, it takes row 62 to the engineer and to the trim's owner, stating the conflict with the trim plan's G3.
- On any path that reaches Phase 2a, the revision also settles these deferred review findings. They are grouped by the suffix of the `agent-reviews/` file that holds them, each suffix followed by `-clean-pass-criteria`.
  - `1791151269` (round 4, row 101):
    - SDET 4: run Verification 7's blind concordance at the revision, against the Candidate rules text.
    - SDET 5: add the missing expected dispositions, and resolve the two meanings of "item 6" in Verification.
    - SDET 6: add L392 and L393 to the in-place exception edits and their pins (row 14).
    - CISO F4-3: remove plan-only row cites from text that ships word for word.
    - CISO F4-5: add Verification 7's data-not-instructions clause.
    - CISO F4-4: add its Verification 6 row, a security-control FYI with no cite and no fix.
    - Product finding 3: add the post-ship checks and the `--note` query.
    - Product finding 4: settle the shipped naming.
    - Platform F8: add the heading for a no-pick `CHANGELOG.md` entry.
  - `1791162002` (the plan code review):
    - CISO "security-invariant test coverage": settle it through the re-ask's allow-test and deny-test answer. A yes makes the revision do all of these:
      - rewrite row 13;
      - rewrite row 65;
      - rewrite Phase 2a's "No ledger file and no ledger test changes" bullet;
      - rewrite Out of scope's follow-up line;
      - add a row recording the departure from option B's fourth bullet.
  - `1791153214` (round 5):
    - CISO "denylist": this branch's first push precedes Phase 1 and the revision. The baseline text's pre-push control is this review's `ciso-reviewer` redaction scan (filed under the `1791183767-clean-pass-criteria` suffix) plus the commit-time and `gh pr create` hooks. The revision's `denylist-check` covers its added plan lines (next top-level bullet) and the commit messages and PR title of the push that carries Phase 2a.
    - Product "early exits" item (d): decide whether the code-review trim gates a no-pick revision.
    - Product "early exits" item (e): condition the Approach's benefit sentence on a pick.
  - `1791183767` (the cumulative review before the revision):
    - CISO "settled-site exception": confirm that the L378 edit and Verification 6's two new rows still reach only a same-failure-mode repeat at a token-bearing keep, against GH-1213's merged text, before the code-writer drafts the sentence.
    - CISO "data-not-instructions": carry the clause into item 6's pinned text and Verification 7's reader prompt. The fail-closed default, M14 and Verification 6 already hold it.
    - CISO "quoted evidence per condition": require each verdict to quote the evidence for each condition, which is the per-source label lines for R1 and R1b, and the suggested-fix text and cited path class for R2 and R4. The round report relays that quoted evidence verbatim. What a verdict can carry depends on GH-1213's merged per-finding return.
  - `1791184754` (plan-review round 7, delta of the round-3 fixes). The revision fits these to GH-1213's merged text; none is a new mechanism:
    - SDET: constrain the settled-site fixtures. The different-failure-mode finding must itself qualify under a picked rule, the invariant-class finding must read as a same-failure-mode repeat, and each runs inside a pass with other qualifying rows.
    - SDET: add an uncertain-match row and a retired-keep row, or record why the unedited L378 sentence is trusted for them.
    - SDET: make the steering row discriminate. Add a spoofed-membership row and, if R2 is picked, a diff-borne row. State that the reader sees the clause only through item 6's text, and say how the cited-file content is supplied.
    - SDET: when the quoted-evidence requirement is adopted, add a Verification row where a verdict names the rule and quotes no evidence (expected ADDRESS), or record the requirement as unverifiable at the paper-test layer.
    - CISO: require `SETTLED` and `--decided-by plan-architect` in the exception's predicate, so an engineer or enforcement-invariant row never matches, and have the edited pin assert that the four stop triggers survive.
    - CISO: put the quoted-evidence requirement in the fail-closed defaults, and add a stop case for a merged return that cannot carry quotes.
    - CISO: extend item 6's data clause (L362) to match M14's list.
    - CISO: before the first push of the Phase 2a commits, re-run the redaction scan over the lines added since the baseline, because the `1791183767` scan predates later edits.
  - `1791232710` (plan-review round 8, delta of the architect-endorsed M14 and exception edits). The revision fits these to GH-1213's merged text; none is a new mechanism:
    - SDET: require the drafted exception to name an uncertain match and an invariant-class finding as outside it, and align item 6's Repeats bullet with that.
    - SDET: drop the "trust the unedited sentence" branch of the uncertain-match and retired-keep row item, and point the pin assertion at the exception's predicate.
    - SDET: add the quoted-evidence case to the revision's stop list beside the rule-token case and to its check list, then reduce the two quoted-evidence list items to pointers into it.
    - SDET and CISO: say which copy of the pinned region the judge reads (the base ref, or verbatim in its prompt), so the data clause does not cover the rule text the judge applies.
    - CISO: state a consequence for a keep whose verdict lacks the quoted evidence, which is that the row stays ADDRESS.
    - CISO: bound the data clause by an allow-list of what the judge treats as instruction (the pinned region text), so harness-loaded files from the reviewed tree and cited files outside the branch are data too.
    - SDET: replace plan-line self-cites such as item 6's data clause "(L362)" with heading anchors, merge the two list items that own the data-clause edit, and add steering rows for the ledger digest and a commit subject, or record them as inspection-only.
  - `1791254347` (plan-review round 9, delta of the round-6 fixes). The revision fits these to GH-1213's merged text; none is a new mechanism:
    - CISO: item 6's Logging bullet has each keep's `--rationale` cite the path of the saved return M14 asks for, within row 96's hex-run limit.
    - SDET: each picked rule's positive Verification 6 row has an in-repo `--source` (row 92) and fails every other picked rule's conditions.
- After inserting the revision's return, the session runs `denylist-check` over the plan's lines added relative to `snapshot/plan-baseline.md`. `/plan-review` then passes before any in-repo edit (row 63).
- If the engineer picked no rule, the revision does not wait for GH-1213. It narrows Phase 2a to the decision file and its `CHANGELOG.md` line alone.

**Phase 2a: `code-review` criteria (one `code-writer` dispatch, after the revision).**
- Add item 6 to `code-review/SKILL.md` § "Finding disposition" after L399's smell test, which makes it the section's last content. It sits outside the closed DEFER list (row 13), inside a `DISPOSITION_RULE:code-review-measured-non-blocking` region whose start anchor is inline on its first line and whose end anchor is inline on its last line (row 17). It states, in this order:
  - the scope (Candidate rules, "Application conditions and logging");
  - the evaluation order;
  - the fail-closed defaults;
  - the logging form, the repeat rule and the staged-round route;
  - the exclusions;
  - the definition of production logic;
  - each picked rule word for word from Candidate rules, with the suggested-fix rule.
- Edit these sentences in place, at current line numbers. Each edit is count-neutral.
  - L378, the contradiction-route region: the settled-site stop ("A finding against a site an earlier verdict already settled … goes straight to the human") and "A repeat that does not carry takes the stop at a SETTLED site" each gain an exception that reaches only a same-failure-mode repeat at a live item-6 keep whose rationale carries its rule token, which item 6 routes (row 71). The exception's own wording excludes an uncertain match and an invariant-class finding, so each takes the stop at a keep's site, as a different failure mode does. The trigger-naming sentence stays unedited, and it labels the stop without gating it. A script rejection while logging a routed repeat takes the settled-site stop, and the round report relays the rejection's reason. The revision re-derives row 65 against GH-1213's merged lib to see whether that default still holds.
  - L390's last sentence gains "or the measured-non-blocking keep below". If R1 or R1b is picked, its "triage signals, not dispositions" clause gains the same exception (row 18).
  - L391's "Mechanical fixes are ADDRESS" and L394's "is ADDRESS" each gain "unless the measured-non-blocking keep below applies".

  L390's "the five criteria above" stays true and unedited. L399's smell test stays unedited (row 14).
- Pins (row 16):
  - add a region pin with a position assertion (M7), and register the new anchor in `_EXPECTED_DISPOSITION_RULE_ANCHORS` (row 54);
  - update `_PINNED_CONTRADICTION_ROUTE_CLAUSE` to the edited L378 text, and nothing else in it;
  - add one exact-sentence assertion, bounded to § "Finding disposition", for each edited sentence in L390, L391 and L394, plus L390's "triage signals" clause when R1 or R1b is picked. They go in a sibling parametrized list modeled on `_REVIEW_LEDGER_PROSE_CONTROLS` (row 93).
- No ledger file and no ledger test changes (row 13).
- Line budget: follow row 15's ratchet.
  - Item 6 adds two lines as one paragraph, its paragraph and a blank separator, or four lines as two paragraphs (row 83). It ships as two paragraphs, application conditions and then eligibility, only when the file stays at 500 lines or fewer with them. The revision decides which. The in-place edits add none.
  - If the post-GH-1213 base is 499 lines or more, the commit would end over 500 and longer than HEAD. Phase 2a then needs the recovery site that the revision named with the engineer's answer, and it does not run without one (rows 62 and 67).
  - While the file is over 500 lines, every later fix-loop commit must be net-zero or shorter.
- Before each edit, the dispatch checks whether the site is already in its end-state form. When it stops, it reports each site as done or pending.
- Add the new decision file, the supersession lines and the `CHANGELOG.md` entry listed under Critical files.

**Phase 2b: moved to GH-1213 (rows 50 and 51).** The loop change and the rule for when it is needed belong to GH-1213. This plan hands GH-1213 its evidence through step 0's note and `gh1213-input.txt` (step 8), and asks no Phase 2b question at the checkpoint.

### Candidate rules (item 6 ships the scope, the fail-closed defaults, the exclusions, the definition and the picked rules word for word)

**Exclusions, checked before any rule.** No rule applies to a finding that:
- the enforcement-invariant rule (`code-review-defer-invariant`) covers;
- carries a BLOCKER, Critical or High label from any source;
- has a `ciso-reviewer` source;
- concerns, cites, or would be fixed by changing anything that states, verifies or implements a gate, hook, permission check, marker guarantee, disposition or review-completion rule, threat-model tier, or the private-project redaction rules, whatever kind of file it is;
- concerns, cites, or would be fixed by changing anything that states, verifies or implements a security control, whatever kind of file it is: auth or authz, permission scope or least privilege, input validation or escaping at a trust boundary, secret or token handling, sensitive data in logs or error responses, or third-party data sharing (row 86);
- reports that the diff exposes a secret, a token, or personal data such as a government ID number, payment-card number or health record, or prose that identifies a private project, organization, codename, internal product or tool, person, hostname, internal URL, email address, tracker ID, or a filesystem path embedding a project name (row 86).

**Production logic** is everything except these:
- `REFERENCES.md` files;
- files under `docs/`;
- README files;
- plans under `.claude/plans/`;
- test files (under a `tests/` directory, or named `test_*`) and their fixtures;
- comments or docstrings in code that no tool, test or hook reads.

Anything not listed is production logic. That includes configuration, data, manifests, settings and CI files, and machine-read comments: directive comments, header lines that a hook or test reads, and region anchors.

- **R1, `fyi-only`.** Every source labeled the finding FYI.
- **R1b, `fyi-non-logic`.** Every source labeled the finding FYI, and its suggested fix touches no production logic.
- **R2, `fix-added-test-or-wording`.** All three of these hold:
  - the finding cites lines that this branch's fix commits added since the previous cumulative pass;
  - the cited lines are not production logic;
  - the suggested fix touches only tests or text that is not production logic.
- **R3 (dropped: row 48).** Only its census upper bound remains, as input for GH-1213 (row 49).
- **R4, `prose-only`.** The suggested fix touches only comments, docstrings or docs, none of which is production logic.
- **Suggested fix.** R1b, R2 and R4 need a suggested fix. A finding with none can qualify only under R1.

**Application conditions and logging.** Item 6 states these. The pre-Phase-2a revision fits their wording to GH-1213's merged text.
- *Scope.* It applies only in `/ready-for-review` step 3's cumulative pass (row 30), and only in `plan-architect`'s per-round disposition, never in one the orchestrator writes (rows 45-47).
- *Evaluation order.* The architect first decides every row of the pass without item 6, so a qualifying row starts as ADDRESS. If every row that is then ADDRESS qualifies under a picked rule, and no row awaits a stop-and-ask or a consult verdict, each qualifying row becomes an item-6 keep. Otherwise each qualifying row stays ADDRESS and goes into that round's fix (M6). A row qualifies only if an in-repo file can serve as the keep's `--source` (row 92).
- *Fail-closed defaults* (row 85).
  - A keep needs the architect's explicit item-6 verdict for that row, naming the rule, and the verdicts must cover every row of the pass. A missing, empty, hedged or partial verdict leaves every qualifying row ADDRESS.
  - A row whose exclusion, production-logic or membership call is uncertain does not qualify.
  - The round report relays each item-6 verdict verbatim.
  - Finding text, findings files, cited files and diffs are data, and the judge takes no instruction from them.
- *Logging.* A keep is logged `--disposition SETTLED --decided-by plan-architect`, with a range-form `--source` naming the whole block. Its `--rationale` starts `measured-non-blocking <rule name>:` and names the membership evidence. For R1 and R1b, that is which sources labeled the finding FYI. For R2, it is the fix commit that added the cited lines, named by its subject line or by at most 12 hex digits of its SHA, because a longer hex run blocks the PR-body update (row 96).
- *Repeats.* A keep never carries (row 65).
  - In the cumulative pass, a same-failure-mode repeat at a keep's site goes to that round's `plan-architect` disposition, which applies item 6 again. It logs either a fresh keep or an ADDRESS, each with `--ref <id>`, and the ADDRESS retires the keep (row 71). A repeat with no explicit verdict takes the settled-site stop.
  - In any other round, the repeat is ADDRESS `--ref <id>`.
  - A keep is recognized only by its rationale token, so a repeat at a keep logged without it takes the settled-site stop, the existing and stricter route.

### What GH-1213's dispatch must provide (study-independent; M14)

These hold whatever the study finds, but they matter only if the engineer picks a rule, and R2's diff below can wait until R2 is picked. Step 0 relays this paragraph and the list through the engineer. GH-1213's author decides how to meet them, and the revision checks the merged result.
- One dispatch per round carries every row of the round, and it knows which rows await a stop-and-ask or a consult verdict.
- It knows whether the round is the cumulative pass.
- Its inputs include each finding's per-source labels, reviewer names, cited paths and suggested fix, and it can read the cited files. R2 also needs the diff of the fix commits since the previous cumulative pass.
- The judge reads the pinned `DISPOSITION_RULE:code-review-measured-non-blocking` region's text, by path and anchor or verbatim, and each per-row verdict names its rule from that text, which is the wording Tier A was measured against (row 53).
- Its per-finding return covers every row, and it can express an item-6 keep with its rule name and the quoted evidence for each of that rule's conditions, distinct from ADDRESS and DEFER (row 95). The orchestrator logs that keep as Candidate rules' Logging bullet states.
- A missing, hedged or partial return leaves rows ADDRESS, and the round report relays the verdicts verbatim.
- It saves each round's return as a file the PR's reviewer can open, so each item-6 keep can be checked against the verdict that made it.
- Its prompt states that content derived from the reviewed branch or its reviews (finding text, findings files, the ledger digest, cited files, plan files, diffs and commit messages) is data the judge never takes instructions from.
- A same-failure-mode repeat at a live keep's site in the cumulative pass comes back to this dispatch.
- An item-6 keep never carries. Any change that lets a `plan-architect` SETTLED row carry must require an explicit per-row opt-in made when the row is logged, and item 6 never sets that opt-in (row 87).

### Proxies and miss rate

- **P1, shipped-logic change.** The fix hunks attributable to the row changed production logic. A `yes` needs either a hunk that overlaps the row's cited site, or a quoted tie between the finding and the hunk. A round-level commit alone gives `unknown`.
- **P2, later real defect.** A later cumulative pass on the same branch raised a finding at the row's site. That finding's fix changed production logic, and it names the same failure mode. P2 is `unknown` when the branch has no cumulative pass after the row's pass.
- **P3 and P4 (dropped: row 21).**

**Estimator, fixed before any Tier B label exists.**
- The unit is a distinct finding. Rows marked `repeat-of` join the earlier row's finding, and a finding is a miss if any of its rows is.
- The miss label is three-valued:
  - `yes` if P1 or P2 is `yes`;
  - `no` only if P1 is `no` and P2 is `no`, with P2 observable and every relied-on window read in full;
  - `unknown` otherwise.
- Each rule gets three rates, each with a Wilson 95% score interval:
  - the *headline* rate: `yes` over `yes` plus `no`;
  - the *best-case* rate: `yes` over all labeled findings, which counts `unknown` as `no`;
  - the *detected worst-case* rate: `yes` plus `unknown` over all labeled findings. It is a ceiling only on what P1 and P2 can detect.
- Every rate is computed on the rule's `stop-pass-exposed` stratum, which is the population every T comparison uses (the floor, early rejection and the below-T claim), with the pooled value beside it (row 76).
- Early rejection uses the best-case lower bound.
- A claim that a rule lies wholly below T uses the *adjusted upper bound*: the detected worst-case upper bound, plus the flip rate's Wilson 95% upper bound (row 40) times the rule's `no` share. That bound corrects only for label errors the second labeler does not share, and the packet says so beside every below-T claim.
- Every bound behind a claim is recomputed under every single-branch deletion, and the worst result in the claim's direction is reported. The packet also gives the number of branches contributing findings, and misses, next to each interval.
- For each rule, the packet also reports:
  - the share of findings with an observable P2;
  - P1 and P2 separately for R1b, R2 and R4 (row 36);
  - Tier B's agreement with the overlap arm: the share of overlap-arm `yes` rows that Tier B labeled P1 `yes`, with a Wilson interval, or a statement that the rule has no such rows. The arm is `yes` for a comment-only or unrelated hunk as well, so a low figure is expected for R1b, R2 and R4, whose members' fixes touch no production logic (row 36). The figure is measured on the arm's rows only, so `no` labels outside the arm are unmeasured by it. The packet prints it beside every below-T claim;
  - whether P1 and P2 can produce a rejecting result at all;
  - the rate broken down by join step and by reviewer mix.

### Unit of analysis: disposition rows

This is the session's option (a), with a tighter join. Outcomes, and whether each pass was clean or dirty, are recorded only in disposition records. The rule also acts at the disposition step. So the record row is both the population the study measures and the one the rule will act on.

How a row gets its label:
1. Use the label in the Source cell, if the cell carries one.
2. Otherwise, use the named reviewer's same-epoch findings file, if all of that reviewer's bullets in that round carry one label.
3. Otherwise, match on shared text tokens, searching only that reviewer's bullets for that round.

A row with several sources takes the most severe label. A row that is still unmatched is listed as ambiguous rather than guessed. Dirs that have no disposition records feed only the label-mix cross-check. M1 covers the alternatives.

### Script home and work dir

The script is a one-off and is never committed (M2). It lives in a work dir under `~/.local/state/`, because the work dir has to survive from Phase 1 until Phase 2a and GH-1213's planning (M13).

### Tier B: sequential census (row 31)

Tier B labels every union row rather than a sample, because a 40-row sample cannot certify a rule below 5% even with no misses (row 33). When T is on file, the agents work through the branches in a seeded order, in three waves. Cost follows the commit windows a batch loads more than the rows it labels (row 34). Early rejection only saves cost: it stops labeling a rule whose misses already clearly exceed T, and it never accepts a rule early. Once every exempted row is labeled, label error outweighs sampling error. Four checks bound it:
- the engineer's hand-check;
- the blind second labels, on a different model from Tier B's;
- the blind exclusion labels, over every row a rule could exempt;
- the ground-truth check (row 39).

M11 covers the alternatives.

### Assumption ledger

**Root.** A cumulative `/code-review` pass is clean only when every row is resolved. Today, only a fix, a closed-list DEFER or a SETTLED decision resolves a row. Each fix adds code to review, which brings new rows, so on large diffs the `cumulative-review` marker is rarely reachable. This plan measures which finding categories can be resolved without a fix, and at what miss rate. It then widens what counts as resolved, with three limits:
- only for a cumulative pass in which nothing else needs a fix;
- only as GH-1213's per-round Opus architect applies it;
- each use is logged as a SETTLED keep decided by `plan-architect`.

Bounding the number of rounds stays with GH-1213 and the Cap.

**Givens.**
- G1. Past artifacts have no finding IDs. Labels exist only as bullet text, records come in several shapes, and filenames encode neither the round nor the pass type. Reason: these are past records, and nothing done now can change what earlier sessions wrote.
- G2. Reviews from deleted worktrees are gone, and staged commit-gate rounds wrote no disposition records. Reason: that past state cannot be recovered.
- G3. (moved to Out of scope: the cap is in reach.)
- G4. Branch histories may have been rebased or squash-merged, so linking commits to rounds is approximate. Reason: that history is past state.
- G5. Another session owns PR #1009 and may write into that branch's `agent-reviews/` while the study runs. Reason: another party owns that work (brief §4).
- G6. GH-1213 is parked in another session, and it has no plan file yet. Its merged shape decides how item 6 is applied. Reason: another party owns that work. [verified: two worktrees exist for it.] Their HEAD files name branches `GH-1213/architect-authors-fix-plan` (nested) and `GH-1213/architect-authored-fix-plan`. [verified: this dispatch's search] It found only incidental mentions of 1213 in the latter's plans. [verified: this round's SDET search] It found no GH-1213 plan file in either worktree. Which branch will carry GH-1213's implementing PR is unknown (step 10).
- G7. The `code-review` skill trim merged as #1217 and is in this branch's base. This plan's `code-review/SKILL.md` line numbers predate it, and the revision re-derives them after GH-1213 (M12).
- G8. Another session owns GH-1004 (PR #1218), which routes tier-waived findings to DEFER under criterion 3 in project-layer skills. The study's flips are measured against behavior before GH-1004, and the checkpoint says so. Reason: another party owns that work. [unverified: round 2's platform review read that plan; this dispatch did not]

**Rows.**
1. [engineer-verified: "All dirs, read-only (Recommended)"] The corpus is every `agent-reviews/` dir, and no file in it is modified.
2. [unverified] The session's own description of row 1's option was "use every dir locally, with publishing governed by row 3". That is the session's proposal, not the engineer's words.
3. [engineer-verified: "Aggregates, you confirm (Recommended)"] Study figures may enter the PR only as aggregates the engineer confirms.
4. [unverified] The session's description of row 3's option is its own proposal: no per-branch counts, no quoted finding text, and confirmation against the scope bar. This plan adopts it as a working rule and applies it to this plan file too, because the plan ships in the PR. That is why this file states no corpus counts.
5. [engineer-verified: "Ask the architect"] The unit of analysis was left to this plan. It picks disposition rows.
6. [engineer-verified: "Ask the architect"] The script's home was left to this plan. It picks a one-off.
7. [unverified: the engineer's ruling as relayed by the agent-authored brief §2 ("No. But we can change the criteria for what defines a clean pass.") and §6.5; not re-asked this session] The marker's authorization rule does not change. Only *resolved* widens.
8. [unverified: relayed by brief §2] The census layers and Tier B's census (row 31) together meet the relayed ask to reclassify "across all branches", once discovery reaches every dir (row 66). Two judgment sets stay partial: the M9 CONCERN rows after a stop, and the rows that only an early-stopped rule exempts.
9. [verified: `code-review/SKILL.md` L473; `ready-for-review/SKILL.md` L84, L130; GH-1213's issue body (row 52)] `code-review`'s clean definition says "A finding logged DEFER or SETTLED (by a consult, the human, or a carry) counts as resolved". Step 3's definition in `ready-for-review` defers to it. An item-6 keep is a SETTLED row, and GH-1213 proposes its per-round dispatch as `MODE=consult`. So the keep is resolved in both places without editing either sentence. [unverified] Whether GH-1213's merged text keeps that sentence. The revision re-checks it.
10. [verified: `test_skills.py` L5432-5439, L5541-5546, L6064-6067] Those sentences, and step 7's restatement, are pinned. Leaving them unedited leaves their pins passing.
11. [verified: `code-review/SKILL.md` L378, L395-397] The defer-invariant region bars DEFER "regardless of which criterion above seems to match". The contradiction route makes its *keep current text* verdict "never available to a finding the enforcement-invariant rule below covers". An item-6 keep is neither a DEFER nor that verdict, so neither sentence reaches it on its own. Item 6 therefore names the invariant rule as its first exclusion, and its region sits below that rule (M7).
12. [verified: `code-review/SKILL.md` L416-418; `_review-ledger-lib.sh` L438, L708, L748; `ready-for-review/SKILL.md` L122]
    - The PR body's block is rendered from the branch ledger's live decisions.
    - A SETTLED decision stays live until a later non-carry row's `--ref` retires it.
    - The Settled table has a "Decided by" column.
    - `ready-for-review` step 6 republishes the block.

    So an item-6 keep stays visible to the person who merges, showing `plan-architect` and the rule token in its rationale, and a later round with no keeps does not remove it. A contradiction-route keep shows `plan-architect` too, so only the rationale token tells the two apart. A keep logged without its token is therefore handled as a contradiction-route keep, whose repeat takes the settled-site stop (Candidate rules, Repeats). [unverified] Whether GH-1213 changes how rows are logged.
13. [verified: `review-ledger.sh` L48, L69-72; `_review-ledger-lib.sh` L11-18, L96-100, L164-175; `test_review_ledger_script.py` L1490-1511, L1537]
    - The disposition enum (ADDRESS, DEFER, SETTLED, CLEAN) and the five `--defer-criterion` values do not change.
    - The ledger accepts `--disposition SETTLED --decided-by plan-architect` today, and requires `--source` for it.
    - `TestReviewLedgerEnumParity` derives criterion names only from the text between `**DEFER criteria (closed list).**` and `**Invalid DEFER rationales.**`. So a region placed after that span leaves its count of five and its set equality unchanged.

    Phase 2a therefore edits no ledger file and no ledger test.
14. [verified: `code-review/SKILL.md` L390, L391, L394, L399] These sentences conflict with any picked rule:
    - L390's "produces orchestrator disposition ADDRESS unless one of the criteria genuinely applies";
    - L391's "Mechanical fixes are ADDRESS.";
    - L394's "is ADDRESS".

    L390's "the five criteria above" stays true, because item 6 is not in the closed list. L399's smell test counts DEFER tags only, and item-6 keeps are SETTLED, so it neither fires on them nor limits them. Item 6's volume is limited by its own pass-level condition and by the Settled table the merger reads. [verified: `code-review/SKILL.md` L392-393, re-read in this dispatch] Two more bullets in the same list state a flat ADDRESS: L392's "If the guidance has a durable home (header comment, migration note, runbook line in the file), ADDRESS it there." and L393's "A pre-existing gap that closes a correctness or security-invariant hole is ADDRESS". The revision adds both to the in-place edits and their pins (round 4, SDET finding 6).
15. [verified: the post-#1217 read of `code-review/SKILL.md`, which is 495 lines; `check-skill-length.sh` L13, L111-114; `_lib.sh` L1942-1945]
    - The file is 495 lines against a 500-line cap set for this path.
    - The gate denies a commit only when the staged file is over 500 lines and longer than HEAD's version.
    - So every commit must leave the file at 500 lines or fewer, or no longer than the commit before it.
16. [verified: `docs/design-decisions/ready-for-review-fix-loop-convergence.md` L94] When an edit would push a file past its cap or change a test-pinned clause, `code-writer` stops and reports instead of trimming elsewhere or editing the pin. The Phase 2a prompt therefore names every edit it authorizes:
    - the line recovery that the revision names with the engineer's answer (row 67);
    - the new pin and its position assertion;
    - the registry entry (row 54);
    - the in-place edit to the contradiction-route region and to its pin (row 71);
    - the exception-clause assertions (row 93).
17. [verified: `code-review/SKILL.md` L38, L52, L378; `test_skills.py` L4342-4369, L4545-4552] Inline start and end anchors have precedent. The region extractor uses `str.count` and `str.find` and does not care about lines, so an inline region adds no line beyond its own paragraphs, whether it holds one paragraph or two.
18. [verified: `docs/design-decisions/round3-consult-verdict-routing.md` L24-28; `code-review/SKILL.md` L390] Recorded decisions reject "a sixth DEFER criterion granted by a subagent's prose", and they call reviewer labels "triage signals, not dispositions". Item 6 is not a DEFER criterion. It is, however, a written rule that a subagent applies to resolve a finding with no fix, so any pick partly supersedes the round-3 decision's rationale. R1 and R1b key on a reviewer's label, so picking either also partly supersedes the L390 sentence.
19. [verified: `.claude/plans/code-review-disposition-calibration.md` Approach (c); `docs/design-decisions/finding-disposition-by-review-surface.md`] The earlier change chose not to pin disposition prose. This plan pins the new region and its exception clauses anyway, because they now decide when the marker can be written. That puts them in the same class as the pinned defer-invariant and contradiction-route regions (`test_skills.py` L5577-5725).
20. [verified: repo `CLAUDE.md` "Repo layout"; `.claude/rules/skill-and-agent-self-review.md`; `require-ready-for-review.sh` L3's `# tier-threat-model:` header; `test_skills.py` L2442-2448's anchor registry] The production-logic definition under Candidate rules matches how the repo loads files. It is fail-closed: what is not on its list, machine-read comments included, is production logic. It ships inline in item 6, so the shipped text cites no plan row.
21. [engineer-verified: "Confirm the drop (Recommended)"] P3 and P4 are dropped. The tag covers only that drop. [unverified: this plan's reasoning, not the engineer's words] P1 and P2 are the signs that a row should have blocked, and they alone define the miss rate. No pick consumes P3 or P4, the benefit side is computed mechanically, and P4 was R3's membership.
22. [verified: one round's reviewer file and disposition file under the `pr-cost-forensics` worktree, read in an earlier dispatch] Some Source cells carry the label inline. A reviewer with mixed labels needed a text-token match. [unverified] The join rate across the whole corpus. Step 2 measures it.
23. [verified: session explorer report] Every disposition record has an epoch-matched reviewer file. Filenames encode neither the round nor the pass type. Only a minority of bullets carry a `path:line` token, so tying a fix to one row takes judgment.
24. [unverified] A rebase keeps author date but not committer date, so windows order commits by `%at` within the range unique to the branch. `selftest` carries a known-answer case for this.
25. [unverified] Each worktree dir's branch history resolves locally. If a branch's history does not, its P1 and P2 labels become `unknown` and the branch is listed.
26. [verified: the gitStatus recent-commit list shows #1215, #1216 and #1219 merged; an earlier Glob found records under the `merge-aware-review-gates-phase3` worktree] The brief's list of merged branches is out of date, so merged branches are found at run time.
27. [verified: `docs/private-project-redaction.md` L108-111, L173-202] The two publication routes have different requirements:
    - The single-account route needs a cited command that refuses a wider corpus.
    - The case-by-case route needs an in-session yes for each figure, cited with what was proposed and when, and that route's text assumes there is a command a reader can re-run.

    No command can re-produce a miss rate that came from agent judgment, wherever the script lives. So by default the PR records only which way the results pointed. Any figure needs its own case-by-case yes, and that yes must also accept this committed plan's study procedure as the cited method. The corpus is this checkout's dirs, whichever account's sessions wrote them, so this route applies to every figure anyway.
28. [unverified] Corpus text quotes reviewed diffs and findings verbatim, so treat corpus text as possibly holding private-project terms and tracker-shaped IDs. Worksheet text therefore stays in the work dir and in session context. It never reaches the plan, a commit or the PR.
29. [unverified] Every agent type the study dispatches can write in the work dir, and so can the session's own `Write` and `python3 -I -B` calls. Step 0's canary and step 1a's last bullet check this, and a denial stops the study. A child inherits the parent's anchor and permission mode, so no fallback agent type can clear an inherited denial.
30. [verified: G2] Staged commit-gate rounds left no records, so the study cannot measure them. Item 6 therefore applies only in the cumulative pass.
31. [engineer-verified: "Sequential census (Recommended)"] Tier B is a sequential census, not a fixed sample. The tag covers only that label.
32. [unverified] The option description the engineer read was the session's paraphrase of a Fable consult, not the engineer's words. It said three things:
    - label every exempted row in seeded order;
    - stop a rule early only when its Wilson lower bound exceeds the miss rate the engineer would accept;
    - the engineer hand-checks the rows flagged as should-have-blocked.

    This plan adopts that as M11, and it adds more rows to the hand-check list (step 7).
33. [verified: arithmetic re-derived in an earlier dispatch and again in this one; with z = 1.96, the Wilson 95% upper bound at zero misses is z²/(n+z²)] With no misses, the upper bound is 8.8% at n = 40 and 1.9% at n = 200. Certifying a rule below T with no misses therefore needs n ≥ z²(1−T)/T findings: 35 at 10%, 73 at 5% and 189 at 2%. A rule with fewer than that cannot clear T, whatever its labels. `selftest` uses these figures as known answers.
34. [unverified: Fable consult, unmeasured] Tier B's cost follows the commit windows each branch batch loads more than the rows it labels. The rules overlap: R1b lies inside R1, and R4 overlaps R1. Step 5 states the windows, their bytes and the dispatch counts before Tier B starts.
35. [unverified] The engineer has not given an acceptable miss rate T. Step 5 asks for it once the union is known and before any Tier B label exists, so T is fixed before anyone sees a miss rate. If the engineer defers it, step 8 asks for it before any rule's rate is shown. T is recorded only in its decision file (row 61).
36. [verified: this plan's definitions of R1b, R2, R4 and P1] Membership in R1b, R2 and R4 requires a suggested fix that touches no production logic. P1 asks whether the fix that shipped did. For those rules, P1 is `yes` only when the shipped fix departed from the suggestion. Their miss rate therefore rests almost wholly on P2 and on the ground truth, and the packet says so.
37. [unverified: Fable consult] Some rows after a counterfactual stop cite code that fixes made after the stop added. Under the rule those fixes would not exist. M9 therefore counts a row as an escape only if its site existed at the stop pass, and it reports the other rows separately.
38. [unverified: Fable consult] The post-merge check is the only signal that does not rest on the proxies, and it can confirm a miss but cannot rule one out. It covers commits that touch a candidate row's cited path. The packet reports it by historical disposition, and it states that zero hits do not corroborate Tier B. The engineer decides at step 5 whether to run it.
39. [unverified: Fable consult] With a census, label error outweighs sampling error. The hand-check catches only rows wrongly labeled a miss. A real miss labeled `no` surfaces only through the second labels and the ground truth. The second labels therefore run on a different model from Tier B's, over the seeded draw plus rows enriched toward misses (rows 73 and 74; row 108 supersedes the draw). The packet reports how often only the second label called a row a miss.
40. [unverified: Fable consult] A seeded 10% gives too few pairs for per-proxy agreement rates. The packet reports the full two-by-two discordance table on the miss label, pooled across rules, plus an upper bound on the no-to-yes flip rate from the uniform draw (superseded by row 108: the bound comes from every `no`-labeled finding's pair).
41. [unverified] Waves of whole branches make each interim look a cluster sample, and rows within one branch are correlated. A rule therefore stops only when its best-case lower bound exceeds T under every single-branch deletion. There are only two interim looks. A rule stopped in error comes back through 1e.
42. [engineer-verified: "Yes, still my ask"] At `/plan-review`, the engineer confirmed that the three brief-relayed quotes on the Ask line still state their ask. The tag covers only that the quotes stand. It does not cover the brief's other content.
43. [engineer-verified: "Keep them"] The new decision file and the partial-supersession lines in existing decision files are in scope.
44. [engineer-verified: "Keep it"] Merging L345 into L343 in `code-review/SKILL.md` to recover lines is in scope. Row 67 records that the trim performs that merge itself, so the site is no longer available to Phase 2a.
45. [engineer-verified: "Judging exemptions requires Opus."] An Opus model judges whether a finding is exempt.
46. [engineer-verified: "Yes, all rules"] Asked whether row 45 covers R1, R1b, R2 and R4, the engineer selected this label. The tag covers only that scope.
47. [engineer-verified: "there should only be one opus (architect) dispatch not two"] Item 6 adds no Opus dispatch of its own. GH-1213's per-round architect dispatch applies it.
48. [engineer-verified: "Drop R3 (Recommended)"] R3 leaves the candidate list.
49. [unverified: a `plan-architect` consult's reasoning, relayed by the session; not the engineer's words] R3 belongs to GH-1213's plan, where a lighter change is extending #1216's carry-forward to keeps that `plan-architect` decided. A census-only R3 membership count can go to GH-1213 as an upper bound. Row 87 bounds that suggestion, because an extension that let an item-6 keep carry would undo row 65.
50. [engineer-verified: "Architect's order (Recommended)"] The engineer chose that sequencing option.
51. [unverified: the session's option description, not the engineer's words] The option read: "merge #1216 now and run Phase 1 now; Phase 2a waits for the code-review trim, then GH-1213; Phase 2b's loop change moves to GH-1213 as input." #1216 has merged.
52. [verified: GH-1213's issue body, read in its draft text in an earlier dispatch] GH-1213 proposes three things:
    - to "dispatch `plan-architect` (`MODE=consult`, Opus, fresh context) with: the findings files, the plan file if one exists, and the round-1 diff artifact";
    - that "The ADDRESS default and the closed DEFER list are unchanged; the architect applies them";
    - that the existing architect routes "fold into this one dispatch".

    [unverified] Whether it lands in that shape (G6).
53. [unverified] This plan reads rows 45-47 as governing who applies the rules in review rounds. Study dispatches are not per-round dispatches, so row 47 does not limit them. The split (rows 72 and 73) assigns each study stage its model. Parity with the runtime judge covers both model and inputs: Tier A's prompt is limited to the inputs that § "What GH-1213's dispatch must provide" lists (row 75).
54. [verified: `test_skills.py` L2442-2448, L2631] `test_disposition_rule_anchors_present` asserts the found anchors equal an exact set, so the new anchor has to be registered there.
55. [verified: `claude/.claude/agents/ciso-reviewer.md` L95, L119] `ciso-reviewer` labels severity Critical, High, Medium or Low inline, and BLOCKER, CONCERN or FYI in file mode. The severity exclusion names both scales.
56. [verified: `evals/README.md` L354-357] No disposition-fidelity case ships, and the synthetic seed fixtures were measured as non-discriminating.
57. [unverified] In a dirty pass, a qualifying row is still fixed with the round. So under each rule every pass before the counterfactual stop runs as it did historically, which makes M9's trajectory exact up to the stop.
58. [unverified] `/tmp` is cleared at boot on this machine's distribution, and Phase 2a may start weeks after Phase 1. The work dir therefore lives under `~/.local/state/`, and the canary checks that agents can write there.
59. [verified: the Read tool's own description, "Reads up to 2000 lines by default"] An agent reads a longer window file only partially unless the file is split.
60. [verified: repo `CLAUDE.md` § "Redact private-project-identifying content", "Reviewer discipline only"] No hook catches structural fingerprints or private-corpus provenance. The Phase 2a diff, the PR body and the revision's added plan lines therefore get the denylist check.
61. [unverified] T recorded beside the adopted rules would bound each rule's miss rate and exempted count, using row 33's arithmetic. The wider-corpus bar treats that as a bounded range. So T stays in its decision file unless row 27 authorizes the pairing as a figure.
62. [verified: `docs/design-decisions/ready-for-review-fix-loop-convergence.md` L96-98; the trim plan `relocate-code-review-content.md` L63] The recorded fallback for growth in `code-review/SKILL.md` is to extract the Item ownership table "to a runtime-loaded file". The trim plan's G3 bars "a runtime sibling file for `code-review`". The two conflict, so the fallback goes to the engineer and to the trim's owner with that conflict stated.
63. [verified: `require-plan-review.sh` L161-165, as round 2's platform review reported them; not reopened in this dispatch] Editing the plan after its review re-arms the gate against every in-repo edit. Writes under the user's home dirs are outside that gate. Phase 1 edits no repository file, the plan included, and the revision re-runs `/plan-review` before Phase 2a's first edit.
64. [verified: `claude/.claude/scripts/transcript_analysis/author_outcome.py` L467-475; `docs/transcript-analysis.md` L1214-1220] A round whose rows are all SETTLED, DEFER or CLEAN counts as PASS, and one holding a SETTLED row also counts under "rounds classified PASS with at least one SETTLED row". Item-6 keeps are SETTLED rows, so they raise `code-writer`'s PASS share inside that existing counter. The counter cannot tell them from contradiction-route keeps except by the rationale token. The doc's note explains only a one-time step "at the first corpus session holding a `SETTLED` row". It does not describe the later rise item-6 keeps add, so the decision file states that effect (Critical files).
65.
    - [verified: `_review-ledger-lib.sh` L547-560] The ledger rejects a carry whose decision is a SETTLED row not decided by the engineer ("only an engineer decision logged --carry-forward carries").
    - [verified: `test_review_ledger_lib.py` L1160, `test_a_plan_architect_decision_never_carries`] That test pins the rejection. So an item-6 keep never carries, and every repeat at its site gets a fresh disposition (row 71).
    - [verified: `_review-ledger-lib.sh` L566-575, which restricts a successor only when the referenced decision is an engineer's] A later pass that has an ADDRESS row makes the repeat ADDRESS `--ref <id>`, which retires the keep.
    - [verified: `_review-ledger-lib.sh` L566-575] A fresh `plan-architect` SETTLED `--ref <id>` is accepted too.
    - [verified: this dispatch's search of `test_review_ledger_lib.py` for `_consult_decision(` and of `test_review_ledger_script.py` for `plan-architect`] No test pins that second successor path, through which item 6 routes an all-qualifying repeat. Adding one is under `claude/` (Out of scope).
    - [unverified] Whether GH-1213 changes the carry rule. The revision re-derives this row (row 87).
66. [verified: an earlier dispatch's Glob of `.claude/worktrees/*/*/agent-reviews/` in the main checkout] Worktrees whose branch name holds a slash sit one level deeper under `.claude/worktrees/`, and some of them hold disposition records. A one-level glob misses them. `git worktree list --porcelain` also lists worktrees outside `.claude/worktrees/` (round 2's platform review). So M3 enumerates from the worktree list and cross-checks with a recursive search.
67. [verified: this branch's base contains the trim's merge commit for #1217] The code-review trim merged as #1217. [verified: the trim plan `relocate-code-review-content.md` L44 (its S5), read in an earlier dispatch] The trim plan's S5 performs row 44's L343/L345 merge. So row 44's site is gone. Any other recovery site is an edit the engineer has not seen, and the revision asks before using it.
68. [engineer-verified: "Keep (Recommended)"] Asked about Phase 2a's scope growth into `claude/` scripts, the engineer selected this label. The tag covers only that answer. [unverified] That answer covered the criterion-value edits, which row 69's choice removes, so it authorizes no current edit. Any `claude/` edit the revision finds necessary goes back to the engineer.
69. [engineer-verified: "Take B (Recommended)"] For the round-2 blocker about who adjudicates item 6, the engineer chose option B.
70. [unverified: the session's relay of a `plan-architect` consult's option description, not the engineer's words] Option B read:
    - record item-6 resolutions as `--disposition SETTLED --decided-by plan-architect` with no new criterion value;
    - item 6 leaves the numbered DEFER list and becomes its own pinned region;
    - a repeat at an item-6 keep's site goes to that round's architect dispatch;
    - Phase 2a edits nothing under `claude/`;
    - the engineer accepts by name that the orchestrator still writes every ledger row, so no lib check can prove the architect judged.

    The revision's re-ask shows that last sentence verbatim for the engineer to confirm.
71. [verified: `code-review/SKILL.md` L378; `test_skills.py` L5577-5683] Today a finding at a site "an earlier verdict already settled" goes "straight to the human as a blocking stop-and-ask", and "A repeat that does not carry takes the stop at a SETTLED site". An item-6 keep is such a verdict. Routing its repeats to the architect, or to ADDRESS outside the cumulative pass, therefore needs an in-place edit to that region. The region is pinned whole by exact equality, so its pin changes with it.
72. [engineer-verified: "Take the split (Recommended)"] The engineer chose the model split for the study.
73. [unverified: the session's relay of a `plan-architect` consult's proposal, not the engineer's words] The split read:
    - Opus for the runtime judge, Tier A, Tier B's P1 and P2 labels, and the ground-truth check;
    - Sonnet for the blind exclusion labels and the second labels;
    - step 5 still states the Opus Tier B cost, with the fallback of Sonnet Tier B plus Opus second labels over a larger enriched set (superseded by row 108);
    - never primary labels and their check on the same model;
    - Verification 7's paper-test reader stays Opus.
74. [unverified] The same-model bar applies to the re-labeling checks, which see the primary labeler's evidence: the second labels and the blind exclusion labels. The ground truth judges different evidence. Verification 7 tests whether the shipped wording reproduces Tier A's classification under the runtime model, so it runs on that model by design.
75. [verified: `claude/.claude/agents/plan-architect.md` L4 ("tools: Read, Grep, Glob"); row 52] The runtime judge holds no `Bash` and no git. GH-1213 proposes handing it the findings files, the plan file and "the round-1 diff artifact". From those it can decide R1, R1b, R4 and the exclusions, because the findings files carry labels, reviewer names, cited paths and suggested fixes, and it can read the cited files. R2's first condition needs the lines that fix commits added since the previous cumulative pass, and none of those inputs isolates them. So R2 can ship only if GH-1213's merged dispatch carries that diff. The judge can apply only wording it reads, so the dispatch must also give it the pinned region's text. The same tool list means the revision's `plan-architect` cannot ask, query `gh` or run `denylist-check`, so the session does those.
76. [unverified: round 2's SDET review, re-derived from M6 and M9] Under M6 a rule keeps only the unresolved rows of a branch's stop pass. Its exposure rate is therefore the `stop-pass-exposed` stratum's rate, not the pooled one. A stop pass was historically followed by more passes, so most of those rows have an observable P2.
77. [unverified: round 2's SDET review, re-derived from the Estimator] A finding can be `no` only if its branch has a later cumulative pass. That count is mechanical before Tier B. It bounds what a rule can certify (row 33) more tightly than the union count does.
78. [unverified] An agent's context holds a bounded number of window bytes, and truncation turns labels into `unknown`. So batches are cut by bytes, not rows, against a budget the session states with its basis at the pre-Tier-A question.
79. [verified: `require-ready-for-review.sh` L3 ("tier-threat-model: cooperative, untrusted-input, irreversible"); `ready-for-review/SKILL.md` L129-130; `docs/hooks.md` L7, L28, L35 and L37] A clean cumulative pass feeds step 7's completion marker, which counts a SETTLED finding as resolved, and the push gate checks that marker. That gate's tier is `cooperative, untrusted-input, irreversible`. Item 6 changes no gate, no tier line and no command shape the gate allows. It widens what clears the gate's prerequisite, on cost grounds backed by a measured miss rate. It waives no finding against a gate hook. `docs/hooks.md` scopes its framework to `hook-class: gate` hooks (L7). It records "a waived finding against a … gate" on that gate's header (L28, L35). It scopes `irreversible` "to this table" (L37). The Candidate rules' exclusion on gates and hooks bars a keep for any finding about one. So the engineer accepts each pick against that chain by name (step 8), and re-confirms it at the revision.
80. [verified: GH-1213's issue body, L14 of its draft] GH-1213 says it "is step 5 of #1211 and should wait for those measurements". [unverified] Whether #1211's earlier steps include this plan's Phase 2a, which would make the two wait on each other. The session checks with `gh issue view 1211` at step 0, before any spend, and again before the revision.
81. [unverified: reviewer-yield's metric as `docs/cost-ledger.md` L27 describes it; `reviewer_yield.py` not read for this] An item-6 keep leaves its cited path unedited. So a reviewer whose findings fall in the exempt classes shows a lower cited-path edit rate, which moves `reviewer_gap_pp`, with no change in quality.
82. [engineer-verified: "(a) Conditional keep (Recommended)"] Row 42's second quote speaks of categories "we want to potentially defer on permanently". Asked whether that means the conditional keep (a qualifying finding is kept only in a pass where nothing else needs a fix, and fixed with the round otherwise), the engineer selected this label. The tag covers only that choice.
83. [verified: `code-review/SKILL.md` L397-401, where paragraphs are separated by one blank line] A paragraph added between L399 and the next heading costs two lines: the paragraph and its blank separator.
84. [engineer-verified: "Keep it (Recommended)"] Asked whether the Sonnet Tier B fallback at step 5 stays, the engineer selected this label. The tag covers only that choice. [unverified: the session's framing of the question, not the engineer's words] The question stated that the fallback applies only to the one-time study labels, never to the runtime judge.
85. [verified: `code-review/SKILL.md` L366 ("Relay the return verbatim") and L378 ("A finding with no explicit per-finding verdict from the consult (failed dispatch, empty, hedged, or partial coverage) is likewise a blocking stop-and-ask, never *keep current text*")] The contradiction consult already fails closed on a missing or hedged verdict, and its return is relayed verbatim. Item 6 takes the same shape. A qualifying row starts as ADDRESS, so its default is ADDRESS. A repeat at a live keep, which L378 sends to the stop today, keeps that stop when its verdict is missing.
86. [verified: `docs/hooks.md` L15 for the personal-data clause; `code-review/SKILL.md` L264, the sentence naming when `ciso-reviewer` must spawn, and the Item ownership rows at L448, L451, L460-462, L464, L465 and L468] The two added exclusions take their subjects from those surfaces: auth and authz, secrets and tokens, data exposure including prose that identifies a private project, sensitive data in logs, third-party data sharing, security-control tests, least privilege, input validation at boundaries, error-response leakage and permission scope. [unverified: this round's CISO review] A security finding from a reviewer other than `ciso-reviewer`, with a test-only or prose-only fix, met none of the earlier exclusions.
87. [unverified: a requirement this plan states for GH-1213, whose merged shape is unknown (G6)] Any change that lets a `plan-architect` SETTLED row carry must require an explicit per-row opt-in made when the row is logged, and item 6 never sets that opt-in. Excluding item-6 keeps by their rationale token instead would fail open, because the ledger does not validate the token, and a keep logged without it would then carry. The opt-in leaves row 49's suggestion open, because a contradiction-route keep can carry through it. It stops a keep from carrying by default. It does not stop an orchestrator that opts a row in on purpose, which is the residual option B's description stated (row 70). M14 hands this over at step 0, and the revision re-derives row 65 from the merged lib and tests.
88. [verified: the Bash tool's parameter description, which gives `timeout` "default 120000, max 600000 for a foreground command" (round 5's platform review, FYI 10)] `freeze` and `derive` can outlast a foreground call, so the session runs them in the background. Each runs whole and prints one terminal line, so completion needs no stage marks. The 1a prompt states each subprocess timeout with its basis, as `CLAUDE.md` requires for timeout literals.
89. (removed: engineer answers are written with the `Write` tool, so no shell quoting applies; row 100.)
90. [unverified] A labeling agent that writes its whole output with `Write`, under a fixed per-batch name, needs no rename and no Bash call outside the worktree. A re-dispatch, made only after the earlier one returned, replaces the same file. Step 0's canary performs that shape for each agent type, and `validate` decides whether the file is complete.
91. [verified: `.git/worktrees/GH-1079-relocate-code-review-content/HEAD` names `refs/heads/GH-1079/relocate-code-review-content`; the two GH-1213 worktrees' HEAD files name the branches in G6] The trim's dir name and branch name differ. So step 10's merge query reads the branch from the worktree registry and passes it to `--head`. [unverified: this round's platform review] That `gh pr list --head` with the dir name returns an empty list.
92. [verified: `code-review/SKILL.md` L403: "DEFER and SETTLED require `--source`: cite the file the finding concerns (the nearest in-repo file when it names none), and a finding with no in-repo file takes the blocking stop"] A keep is a SETTLED row, so a qualifying row with no in-repo file cannot be logged as one. It does not qualify, which leaves its pass's other qualifying rows ADDRESS.
93. [verified: `test_skills.py` L5875 (`_REVIEW_LEDGER_PROSE_CONTROLS`) and L6035-6039, which assert a phrase inside a heading's section through `_heading_section_text`] An exact-sentence assertion bounded to § "Finding disposition" can pin each edited bullet sentence without pinning the bullets whole. [verified: this dispatch's search of `test_skills.py` for "Mechanical fixes", "triage signals" and "Invalid DEFER" found none] No test pins those bullets today. Without a pin, a later edit could drop one exception while item 6's pin stays green, leaving two opposite rules in one section.
94. [verified: `CHANGELOG.md` L3 ("All notable changes to `claude-config` are documented here"), L5-7 (`## [Unreleased]`, `### Changed`), and L21-29, which record review-ledger behavior that stow consumers see] Item 6 changes what clears a cumulative pass for every stow consumer, so it gets an entry.
95. [unverified: this round's platform review quotes GH-1213's issue draft as returning, per finding, ADDRESS or DEFER with the closed-list criterion; this dispatch did not find the draft] If GH-1213 lands that way, its return cannot express an item-6 keep. M14 names the need at step 0, and the revision's stop list covers it.
96. [verified: `_review-ledger-lib.sh` L20-22 ("12 digits stay below the PR-body redaction gate's 32-digit hex-run detector"); `code-review/SKILL.md` L378, which says long hex runs block the PR-body update] A full commit SHA in a rationale would block the update. So R2's rationale names the commit by its subject line or by at most 12 hex digits.
97. [verified: `docs/cost-ledger.md` L27-28: `reviewer_gap_pp` comes from `reviewer-yield`, and `note` is "operator-supplied"] No mechanism prompts the `--note` for the week item 6 first applies, so the decision file names who writes it and what prompts it.
98. [unverified: this round's CISO review, re-derived from the Estimator] A row that an exclusion should have removed, but Tier A left in, usually scores P1 `no` and P2 `no`, so Tier B counts it as a correct exemption. Only an exclusion re-label can catch it. So the blind exclusion labels cover every row a rule could exempt, not only guard-path and flagged rows.
99. [engineer-verified: "Keep it"] Asked after round 4 whether `CHANGELOG.md` stays in Critical files, the engineer selected this label. The tag covers only that choice.
100. [engineer-verified: "Apply it (Recommended)"] After round 4, the engineer typed "Ask the architect". After a `plan-architect` consult was relayed, they selected this label on applying its recommendation (row 101). The tag covers only that choice. [unverified: the session's framing of the question, not the engineer's words] The question concerned Phase 1's foundation.
101. [unverified: the session's relay of a `plan-architect` consult's recommendation, not the engineer's words; this row is the plan's one relay of it] The recommendation read:
     - keep the evidence layer whole: the frozen snapshot, golden and held-out reads, Tier A on Opus, Tier B's census, the blind exclusion labels, the second labels, the optional ground truth, M9 and the hand-check list;
     - replace the operational harness with `freeze`, `derive`, `validate`, one decision file per answer, and whole-dir retention;
     - specify only defenses against a silently wrong number, because a loud failure (a crash, a refusal, a failed check) costs one re-run;
     - fix the evidence-layer text gaps, and hand the Phase 2a items to the pre-Phase-2a revision.
102. [unverified] A disposition record's Outcome cell shows when a stop-and-ask or a consult verdict resolved a row. Where it does not, M9 can treat that pass as a possible stop, so the ceiling and the flips stay upper bounds. Step 1a's golden reading shows whether the cell carries it.
103. [engineer-verified: "Yes, mechanical only (Recommended)"] After round 5, asked about the held-out Sonnet dispatches, the engineer selected this label. The tag covers only the selected label. [unverified: the session's framing of the question, not the engineer's words] The question asked whether those dispatches use R1's mechanical membership alone, with no judged exclusion, as step 1a states. This settles round 5's notes on rows 45 and 46.
104. [engineer-verified: "Keep general-purpose; instruction-only containment (Recommended)"] Asked whether Phase 1's labeling agents stay `general-purpose`, the engineer selected this label. The tag covers only that choice. [unverified: the session's framing of the question, not the engineer's words] The question described those agents as holding shell and network tools that only the dispatch instructions bar.
105. [engineer-verified: "Add to step 8 and the re-ask (Recommended)"] After round 6, asked how the plan carries the steered-orchestrator path, the engineer selected this label. The tag covers only that choice. It is not a yes to the steered-orchestrator sentences, which the revision's re-ask records. Option B's fifth bullet (row 70) covered the mechanism but not the steered trigger or the wider target class, which is why it is asked again.
106. [engineer-verified: "Keep general-purpose, state why (Recommended)"] After round 6, asked again about the labeling agents, the engineer selected this label over "Canary a narrower type at step 0". The tag covers only that choice. Verification 10 states the reason.
107. [engineer-verified: "Accept instruction-only containment (Recommended)"] Asked "Do you accept that the Phase 1 work dir's integrity rests on the same instruction-only containment as the labelers (rows 104 and 106)? The residual is that a steered labeler's rewrite of the script or a decision file would run with the session's next study call, with its environment and GH token. A hash check would need a store labelers cannot write and would still race a concurrent wave.", the engineer selected this label. The tag covers only that choice. [unverified: the session's framing of the question, not the engineer's words] The work dir's integrity rests on the same instruction-only containment as the labelers (rows 104 and 106). [verified: `claude/.claude/hooks/deny-credential-bash-reads.sh` L4, L11-13] That gate matches credential-path tokens in command text and lists indirection under an innocuous name as a residual. [unverified: inference from that gate and Verification 10] A labeler steered into rewriting the script or a decision file could instead run its own code as the same user from its own Bash call, which those gates pass alike. Phase 1 therefore adds no tamper check, and `-I -B` and step 1a's source check catch honest mistakes only. [unverified] Whether a per-call permission prompt treats a labeler's Bash call more strictly than the session's.
108. [engineer-verified: "Second-label every `no` (Fable's recommendation)"] Asked "Fable's edit (A) replaces your earlier draw choice with 'second-label every Tier B `no` finding'. Do you want that change, or the smaller definitional fix on the current prefix design?", the engineer selected this label. The tag covers only the choice to second-label every Tier B `no` finding instead of a seeded-order prefix. [unverified: the session's reasoning, not the engineer's words] Second-labeling every `no` finding gives the flip bound its largest possible n. It also removes the seeded order, the prefix, the shortfall rule and step 5's pair-count question. Rows 39, 40, 73 and the round 1 SDET note on second-label power record the earlier draw.

**Mechanisms.**
- M1. Disposition rows as the unit. anchors: row5, row9, row23.
  - (b) Reviewer bullets as the unit: the label would be exact, but outcome and pass status would need the same join run backwards, plus an assumed outcome.
  - (c) A hand-labeled sample with no parser: lighter, but with no census there are no per-rule denominators or pass flips, and it fails row 8.
- M2. A one-off script that is never committed. anchors: row6, row27.
  - Lighter alternative (a), hand-reading every record in session: neither the join nor the stop counterfactual is workable or reproducible by hand.
  - Lighter alternative (b), reusing `transcript-analysis.py reviewer-yield`: it reads session transcripts, by default across every declared account root, and it measures edits after a citation rather than labels or dispositions.
  - Heavier alternative, rejected: a committed tool under `claude/.claude/scripts/`, for three reasons:
    - it would be installed for every stow consumer for a one-time calibration;
    - its parser would have to go through the same cumulative review loop this PR is fixing;
    - it still could not make a judgment-labeled rate re-runnable.
- M3. The script enumerates corpus dirs from `git worktree list --porcelain`, then cross-checks with a recursive search. anchors: row1, row66, G5.
  - The corpus is every listed worktree's own top-level `agent-reviews/`, the main checkout included, plus any `agent-reviews/` the recursive search finds directly under an unlisted dir holding a `.git` entry. The search runs under the main checkout's `.claude/worktrees/` and follows no links.
  - `discovery.tsv` lists, with reasons:
    - every dir that only one method found;
    - every listed worktree with no `agent-reviews/`, or whose dir is gone;
    - every `agent-reviews` dir that sits below a worktree root rather than at it, which is excluded.
  - It takes no path argument. Discovery is a pure function over the porcelain text and the filesystem, which lets `selftest` feed it synthetic input.
  - It freezes the corpus by content: a snapshot with a sha256 manifest, keeping only files modified at or before the freeze's start time.
  - Every corpus read opens files read-only. Every write goes through one helper that resolves real paths and rejects any target outside the work dir, as well as a work dir that is itself a link.
  - Alternative (a), a one-level glob: it misses nested and out-of-tree worktrees (row 66).
  - Alternative (b), dirs passed as arguments: each run restates the list and can drop or widen it without anyone noticing.
  - Alternative (c), a hardcoded list: it goes stale as worktrees come and go, which has already happened to the brief's counts.
- M4. The script pre-extracts window diffs from each branch's own commit range, and cited files as of each pass's tree, split into parts, so the judgment agents need only `Read`. anchors: row24, row25, row59, row75.
  - (a) Agents running `git` against other worktrees themselves: wider access, and each agent may look at different commits.
  - (b) No diffs: P1 cannot be tied to a row.
- M5. Two tiers of agent judgment with evidence pointers, plus four checks: blind second labels on another model, blind exclusion labels over every row a rule could exempt, the ground truth, and the engineer's hand-check. anchors: row21, row23, row32, row39, row53, row72, row73, row74, row98.
  - (a) Mechanical proxies at file level: one fix commit covers every ADDRESS row in a round, so tying fixes to rows would be guesswork presented as measurement.
  - (b) Reusing the earlier single-branch analysis of where findings came from: it is unverified and covers only one branch.
  - (c) Blind exclusion labels only on guard-path and Tier-A-flagged rows: lighter, but a security row Tier A left in outside a guard path would go unchecked (row 98).
  - The engineer does not label rows from scratch, because they accepted proxies instead (brief §2, relayed).
- M6. One pinned keep region outside the closed DEFER list, applied only by GH-1213's per-round architect, only in a cumulative pass where nothing else is ADDRESS, failing closed on any missing or uncertain call, and logged as a `plan-architect` SETTLED row. anchors: root, row9, row11, row12, row13, row45, row47, row65, row69, row70, row71, row85, row86, row92, row96, row105.
  - (a) Redefining clean only in `ready-for-review` step 3: this splits *resolved* into two definitions and means editing two pinned sentences.
  - (b) Telling reviewers to stop emitting FYI: this moves disposition into ten agent bodies and changes the label mix the study measured.
  - (c) A separate consult on each finding as the adjudicator: this adds a second Opus dispatch per round, which row 47 rules out.
  - (d) Keeping every qualifying row, even in a dirty pass: this leaves cheap fixes unfixed while a fix round runs anyway, which §16 rejects. It also makes M9's counterfactual inexact (row 57). The engineer chose the conditional keep over it (row 82).
  - (e) A sixth numbered DEFER item with one criterion value per rule: rejected for three reasons:
    - the ledger parity test derives one name per numbered item (row 13);
    - a fresh DEFER records no adjudicator;
    - a DEFER carry restates its criterion and is not judged again.

    A `plan-architect` SETTLED row records its decider, never carries (row 65), and needs no ledger edit.
  - (f) A ledger check that proves the architect judged each keep: not taken under option B, which the engineer chose. B's description stated that the orchestrator writes every row (row 70).
  - (g) A keep-count tripwire in item 6: its threshold would be a corpus-derived figure in shipped text (row 27). The pass-level condition and the Settled table already bound keeps.
  - Heavier alternative, rejected: a new disposition value. It would change the disposition enum, its tests and the persistence rules for the same effect.
- M7. An exact-match pin on the region, modeled on `TestCodeReviewDeferInvariantRegionPin`, plus exact-sentence pins on the three edited bullets. The region pin comes with an assertion that the region starts after the `code-review-defer-invariant` end anchor and before `## Review-narrative ledger`, which keeps it below the invariant rule and outside the closed list. anchors: row11, row13, row19, row54, row93.
  - (a) No pin, relying on review: rejected for the reason in row 19.
  - (b) A pin on the opening phrase only: it catches deletion but not a narrowed or widened list.
  - (c) Pinning the three bullets whole: it would freeze unrelated wording in them, which the exact-sentence form leaves free.
  - Standing behavioral eval cases: rejected, because synthetic disposition fixtures have measured as non-discriminating (row 56).
- M8. A new decision file plus supersession lines added in place, and a `CHANGELOG.md` entry. anchors: row18, row19, row94, row99.
  - (a) PR body only: readers lose it after merge.
  - (b) A line in `REFERENCES.md`: that file is used only when editing, and it cannot mark §16 as superseded.
  - `.claude/rules/design-decisions.md`'s supersession rule requires the in-place line where a statement is overturned, and only there.
- M9. A stop counterfactual. Under rule R, a branch stops at its first cumulative pass whose unresolved rows are all exempt under R or already resolved. Staged and unknown-kind passes are never stop candidates. Neither is a dirty pass that holds a row resolved by a stop-and-ask or a consult verdict, because item 6 does not apply while such a row awaits its verdict (Candidate rules, Evaluation order), and those passes are counted. Flips and `no`-eligibility count cumulative passes only, because item 6 applies only there (row 30). The rows after the stop pass are what R would have left unreviewed. A row after the stop counts as an escape only if its site existed at the stop pass. anchors: root, row30, row37, row57, row76, row102.
  - (a) Measuring only the exempt-row miss rate: this ignores what an earlier stop leaves unreviewed.
  - (b) Counting rows after the stop by label only: kept as the census layer. Judgment is added only for the BLOCKER rows (site existence) and a seeded 20 CONCERN rows per rule.
- M10. A checkpoint before any criteria text is drafted, with the hand-check before the rule picks. anchors: row7, row32.
- M11. A sequential census in seeded branch waves, with early rejection only against an engineer-set T. anchors: row31, row33, row34, row35, row41, row77.
  - Lighter alternative (a), a fixed seeded 40 per rule: it cannot certify a rule below 5% (row 33), and the engineer picked a census (row 31).
  - Lighter alternative (b), one parallel round: this is what runs when no T is given, and step 5's option text says so. As the default, it would drop the sequential order the engineer picked.
  - (c) A seeded order over rows across all branches: each wave would reload most branches' windows, and cost follows windows (row 34).
  - Early rejection only rejects a rule. Accepting early would mean trusting interim labels the engineer has not hand-checked.
- M12. Phase 2a waits for a plan revision after the trim and GH-1213 merge, and that revision stops for the engineer if GH-1213's shape cannot apply item 6 or a pick is unconfirmed. anchors: row50, row52, row63, row75, row79, row80, row87, row95, G6, G7.
  - (a) Fixing edit sites and line numbers now: both of those changes edit § "Finding disposition", so the numbers would go stale.
  - (b) Shipping item 6 before GH-1213, with the orchestrator applying it: row 45 rules that out.
  - (c) Folding item 6 into GH-1213's PR: GH-1213 is parked and has no plan, and one review would carry two decisions.
- M13. A durable work dir outside the repo, built by two whole-run commands and checked by read-only ones. `freeze` writes the snapshot, the plan baseline and the `gh` capture once, with its manifest last. `derive` regenerates every derived output from the snapshot alone and writes `derive.done` last. Every subcommand that writes several outputs writes a done-marker last, and deletes its own outputs first unless step 2's rule for labeled batches applies. Any lookup, range or join the script cannot resolve unambiguously records `unknown` with its reason, never an empty, zero or `no` value. Labeling agents write fixed per-batch names that `validate` checks. Each engineer answer is its own decision file, which the session writes. `purge` discovers its own target and deletes the whole dir. The plan specifies the script's outputs, its invariants and every check that catches a wrong number, directly or through step 1a's quoted findings. A failure that stops the script visibly is handled by re-running, and its mechanism is the 1a dispatch's to choose. anchors: row27, row29, row58, row61, row63, row88, row90, row100, row101, row104, row106, G5.
  - (a) `/tmp`: row 58.
  - (b) Keeping T and the picks in this plan file: that re-arms the plan-review gate (row 63), and T cannot be committed (row 61).
  - (c) Reading the live corpus at every stage: edits from G5's session and worktree removals would change what a re-run reads.
- M14. A figure-free list of study-independent requirements for GH-1213's dispatch, which the engineer relays at step 0, with step 8's file carrying only study-dependent parts. anchors: row47, row52, row75, row87, row95.
  - (a) Only step 8's file: it arrives after Phase 1, and GH-1213's plan may be written before then without these requirements.
  - (b) The session posting to GH-1213's issue: no authorization exists for that, and the engineer is already the channel between the two sessions.

### Addressed review findings

*Round 1, ciso-reviewer*
- Foundation (independent adjudicator): resolved by rows 45-47. GH-1213's per-round Opus architect is that adjudicator, and item 6 bars a disposition the orchestrator writes. A separate consult would be a second Opus dispatch. Round 2's C2-1 takes this further.
- Finding 1 (severity floor, label-only rule): the floor is accepted as the severity exclusion. The tie-break across sources is R1's "every source". R1 stays a measured candidate, because under-labeling is exactly what its miss rate measures. The packet flags it as label-only, next to the L390 sentence it overturns.
- Finding 2 (invariant bar): accepted. The exclusions are checked first, by cross-reference. The guard-surface exclusion and the `ciso-reviewer`-source exclusion collapse the reviewer's item-number list. Blind exclusion labels cover guard-path, judged-exclusion and near-boundary rows, and the packet reports their agreement beside the exclusion counts.
- Finding 3 (R3): moot (row 48).
- Finding 4 (visibility): re-verified after #1216 (row 12). A keep stays live until `--ref` retires it. The Settled table's Decided-by column and the rationale token carry the rule, and step 6 republishes the block. The brake is item 6's own pass-level condition (row 14).
- Finding 5 (standing test): the position assertion and the in-region exclusions are accepted. Standing allow/deny eval cases are rejected (row 56).
- Finding 6 (home path at Context L7): the session's to fix, because Context is outside this dispatch.
- Finding 7 (worksheet text): accepted. The `code-writer` prompt and the paper test use synthetic rows only. The decision file names no branch and quotes no finding. The denylist check runs before each commit.
- Finding 8 (T paired with the picks): accepted (row 61).
- Finding 9 (agent tools, retention): partly accepted. The checks after the fact cover the work dir and the corpus, and containment elsewhere is stated as instruction-level (round 2, C2-8). Retention gets an end step, and the account scope is stated in row 27. Pointer-only worksheets are rejected, because the copies stay in a local dir that is deleted at the end. A narrowed tool set is rejected as disproportionate for a cooperative labeler. Verification 10 and row 104 replace that premise: the labeler is cooperative, but the corpus it reads is not. Verification 10 names the narrower types and the reason each is set aside.
- Finding 10 (direction of disagreement): accepted. The packet gives the full discordance table, and the second labeler runs on a different model from Tier B's.

*Round 1, staff-sdet*
- Registry: accepted (row 54).
- Estimator: accepted (Proxies and miss rate).
- R3 circularity: moot (row 48).
- Dependence between rows: accepted. `repeat-of` dedupes findings, and bounds are recomputed under every single-branch deletion (round 2, finding 4).
- P1 leakage: accepted. The hunk-overlap rule applies, and the packet breaks the rate down by join step and by reviewer mix.
- M9 estimator: accepted. The packet reports hits out of 20 with an interval and the population N. Passes of unknown kind are excluded. Never-clean branches are treated as censored. BLOCKER rows are judged on site existence only.
- Second-label power: accepted in part. The second-label set is enriched with later citations of the same path and with `unsure` rows (superseded by row 108). The packet gives the discordance table and an upper bound on under-counting, and that bound now enters the below-T claim.
- Ground truth: accepted. It is broken down by disposition, zero hits are stated to be uninformative, it is scoped to cited paths, and the engineer chooses at step 5.
- Parser validity: accepted, with a golden diff, a held-out diff, a halt rule and a pre-Tier-A question (round 2, finding 5).
- Over-elaboration: partly accepted. P3 and P4 are dropped. The waves stay because the engineer picked them (row 31), but they dispatch together when there is no T. Ground truth becomes a step-5 choice.
- Paper test: accepted. A blind concordance check is added, the paper test stays synthetic, and the definitions are inlined so the shipped text matches word for word.
- Leak signal: accepted (decision file).

*Round 1, staff-platform-engineer*
- Stale base: resolved. The branch was fast-forwarded, the cited rows were re-verified, and R3 is dropped.
- Registry: accepted.
- Plan-file edits: accepted (rows 61 and 63, M13).
- Read-only guarantee: accepted (M3, step 2, Verification 10).
- Command shapes: accepted. All git and gh calls live inside the script, Bash calls are literal, the canary runs with no fallback, 1a is split, and the anchor stays put. Renaming outputs to `.txt` is rejected: an armed data-file guard should stop the study at the canary, not be sidestepped.
- Truncated reads: accepted (row 59).
- STATE file, resume and cost: accepted, and tightened in round 2 (B8-1).
- Ratchet: accepted (row 15). The extraction fallback is named (row 62).
- Rollback: partly accepted. The decision file records how to find each use, that markers stay valid after a revert, and the leak signal. Aggregate hashes and the seed are rejected, because nothing can check them once the work dir is deleted, and the committed plan records the method.
- No-rule path: accepted, through the revision narrowing Phase 2a.
- Plan-internal references: accepted (row 20).
- Dependencies (`python3`): accepted.
- Window attribution: accepted (M4, step 2).
- Retention: accepted (step 10).
- Harness-refusal taxonomy (FYI): outside this plan. The session may relay it to the owner of `docs/worktree-bash-guard.md`.
- Decision-file grammar (FYI): accepted (Verification).

*Round 1, staff-product-engineer*
- B8-1 (outcome): accepted. The outcome is restated, flips and the tail are reported for each rule, each combination and the picks, and the Phase 2b decision rule and its evidence go to GH-1213. The revisit trigger is in the decision file.
- B14-1 (unfixed exempt rows): accepted. Qualifying rows are fixed with the round (M6; the engineer chose this conditional keep, row 82), and each rule is shown next to the text it overturns and the cost it was measured against.
- B5-1 (proxies): accepted. The packet states for each rule whether it can be rejected. R2 is tightened. T options are restated per branch in session.
- B8-2 (author-outcome): resolved by round 2's design. Item-6 keeps fall in the existing SETTLED counter (row 64).
- B8-3 (visibility): accepted (rows 12 and 14).
- B16-1 (follow-through): accepted, and corrected in round 2 for the rows that ship kept.
- B8-4 (in-flight branches): accepted. The decision file states that records are not re-derived and that a branch with a cap row still stops on its next dirty pass. Item 6 applies only in cumulative passes.
- B10-1 (checkpoint flow): accepted (`verdicts.tsv`, ordering and stop mark, handoff note).
- B6-1 (benefit first): accepted (step 5).
- B7-1 (R3 scope): moot (row 48). The decision file cites this plan's follow-ups as the levers that would cut how many findings are generated.

*Round 2, ciso-reviewer*
- Foundation and C2-1 (adjudicator unrecorded): resolved as far as the engineer's choice of B goes (rows 69 and 70). An item-6 keep is a SETTLED row whose Decided-by column reads `plan-architect`, and its rationale carries the rule token and evidence. The lib check C2-1 asked for is not taken. Option B, which the engineer chose, stated in its description that the orchestrator writes every row (row 70), and the revision's re-ask shows that sentence for the engineer to confirm. The decision file says so.
- C2-2 (carry re-applies item 6): resolved by B. The ledger rejects a carry of a SETTLED decision not decided by the engineer, so a keep never carries, and each repeat goes to that round's architect (rows 65 and 71). Row 65 is re-derived from the lib. Round 3 corrected it: `test_review_ledger_lib.py` L1160 already pins that rejection.
- C2-3 (decidable from the runtime inputs): accepted. Tier A's inputs are limited to the runtime judge's (step 3, row 75). § "What GH-1213's dispatch must provide" lists the inputs, and the revision checks them. R2 ships only if GH-1213 supplies the fix-commit diff.
- C2-4 (exclusion reach): accepted as one collapsing rule. Exclusion 4 is keyed on subject, whatever the file kind, and adds disposition and review-completion rules. The study's guard-path frame widens to the files that define those rules (step 2). Round 3 widens the blind labels further (R3-2).
- C2-5 (fail-open definition, fragment): accepted. Production logic is defined by an allowlist and is fail-closed. The exclusion bullets are parallel, and the paper test asserts each one.
- C2-6 (`gh1213-input.txt` figures): accepted. The file carries direction only, unless a figure has its own row-27 yes, and its first line says so.
- C2-7 (denylist spec and the plan file): accepted. The matcher is specified and fixture-tested in 1a. The check covers the revision's added plan lines, and step 8's option labels are rule names only.
- C2-8 (write checks overstated): accepted. Corpus file sets are compared at every stage boundary, the mode is checked, and `selftest` keeps the fixture off the real corpus by construction. Containment elsewhere is stated as instruction-level.
- C2-9 (gate classification): accepted. The packet, the adoption question and the decision file state the chain and its tier, the engineer accepts each pick by its label, and R1 is marked label-only (row 79). No gate's tier changes.
- C2-10 (contradicted sentences, supersession trigger): accepted in B's form. L390's count stays true. Its last sentence gains the exception for any pick, and its "triage signals" clause does for R1 or R1b. The round-3 supersession line is added for any pick (row 18).
- C2-11 (stow consumers): accepted (decision file).
- Volume brake (FYI): row 14 is corrected.
- Withhold rule and retention bound (FYI): every one-way disagreement is on the hand-check list. Retention gains fallback events (step 10).
- Reopened rows 45-47: settled by the engineer's split (rows 72 and 73), with row 47's reading unchanged (row 53). Row 3 against the hand-off: C2-6. Row 31: step 5's option text states the no-T consequence.

*Round 2, staff-sdet*
- Foundation (estimand): accepted. The `stop-pass-exposed` stratum is reported next to the pooled rate and is used for the per-branch T statement (row 76).
- Finding 1 (parity test): moot under B. Item 6 sits outside the closed list, and no criterion value is added (row 13).
- Finding 2: accepted, as the foundation item.
- Finding 3: accepted. Step 5 states `no`-eligible counts and the lowest reachable bound (row 77).
- Finding 4: accepted. Every single-branch deletion is used, contributing branches are counted, the adjusted upper bound combines the flip-rate bound, and the worst-case rate is renamed.
- Finding 5: accepted. There is now a halt rule, a fail path, a fresh held-out draw, two whole held-out branches, an independent counter, the pre-Tier-A question, and the reviewer-mix breakdown on the rate.
- Finding 6: accepted (`selftest` assertions).
- Finding 7: accepted. The concordance set is stratified, R2 gets its added-line ranges, there is an acceptance rule and a fresh-seed re-run, and the repeat cases are in the paper test.
- Finding 8: accepted (byte-cut batches for both blind passes, and the count of second-labeled rows with `read_complete` false).
- Reopened: row 31 is handled by step 5's option text, rows 45 and 46 by the split, and row 42's reading by the engineer's answer (row 82).

*Round 2, staff-platform-engineer*
- B1-1 (nested worktrees): accepted (M3, row 66, Verification 1). Row 1's "All dirs" covers nested and out-of-tree dirs on its plain reading.
- B1-2 (parity test): moot under B (row 13).
- B9-1 (ordering): accepted. The trim consumes row 44's site (row 67), the extraction fallback conflicts with the trim's G3 (row 62), the revision has a stop branch, what Phase 2a waits on is recorded (step 10), #1211's ordering is checked (row 80), and GH-1004 is a given (G8).
- B5-1 (who restates a carry): resolved by B (rows 65 and 71).
- B8-1 (two writers, resume): accepted (M13, step 10, step 1a's session-shape check).
- B8-2 (context fit, Tier A gate): accepted (byte-cut batches, the pre-Tier-A question, the free-space check).
- B8-3 (lifecycle, hand-off figures): accepted (step 8's retention question, step 10's fallbacks and dir listing, the hand-off header).
- I2-1 (re-run safety): accepted (write-once freeze, named stages, `gh` output in the snapshot, Phase 2a's done-or-pending report).
- B1-3 (denylist timing): accepted (Verification Phase 2a item 8).
- B5-2 (`/tmp` citation): accepted (row 52 cites GH-1213's issue body).
- B11-1 (rollback facts): accepted (decision file).
- Reopened row 44: no conflict with "Keep it". The trim performs the same merge (row 67), and any other site goes to the engineer.

*Round 2, staff-product-engineer*
- B8-1 (single applier): accepted (the revision's stop branch). The Approach lead says relief waits on GH-1213. Context is the session's to update.
- B2-1 (runtime inputs): accepted (row 75, § "What GH-1213's dispatch must provide", row 53).
- B13-1 (circular condition, awaiting rows): accepted. An explicit evaluation order and the awaiting-stop case are in Candidate rules. Whole-pass cases are in Verification Phase 2a items 6 and 7.
- B8-2 (auditability): issue 1 is resolved by B's Decided-by column. Issue 2 is accepted: the rationale names the evidence. Issue 3 is fixed in row 14.
- B16-1 (shipped keeps): accepted (decision file).
- B2-2 (consumers): partly accepted. Item-6 keeps fall in the existing SETTLED counter (row 64). The decision file names both consumers and a cost-ledger `--note`. Extra sentences in the consumer docs are a follow-up. Round 3 corrected row 64's reading of the doc (product finding 4).
- B10-1 (no-T path): accepted (step 5's option text, step 8's deferred-T question, stop-any-time).
- B5-1 (pick provenance): accepted (the revision re-asks, and since round 3 an unconfirmed pick stops it).
- B2-3 (label coupling): accepted (revisit trigger).
- B7-1 (residual R3): accepted. The R3 bound goes as direction only, for GH-1213's author to use or drop.
- B5-2 (scope row): accepted (row 68).
- Reopened: row 31 is handled by step 5's text, rows 45 and 46 by the split, and row 42's reading by the engineer's answer (row 82).

*Round 3, ciso-reviewer*
- R3-1 (fail-closed defaults): accepted. Item 6 states three defaults: an explicit per-row verdict that covers the pass, otherwise ADDRESS; an uncertain call does not qualify; verdicts relayed verbatim (row 85). A repeat with no verdict takes the settled-site stop. Tier A counts `near-boundary` rows as excluded, so the measured union matches the shipped behavior (step 3). GH-1213 gets the defaults as requirements at step 0 (M14).
- R3-2 (exclusion reach): accepted. Two exclusions are added, worded from the `ciso-reviewer` spawn sentence and the Item ownership rows (row 86): security controls whatever the file kind, and reported data exposure. Tier A and the blind labels apply the same text. The blind labels now cover every row a rule could exempt, because P1 and P2 cannot catch a missed exclusion (row 98). Verification 6 names a row for each added exclusion.
- R3-3 (carry rule): accepted. Row 87 states that any carry extension must be opt-in, M14 hands that over at step 0, and the revision re-derives row 65 from the merged lib, with a changed carry rule on its stop list. Row 49 stays as the consult's relayed words, bounded by row 87.
- R3-4 (unconfirmed picks): accepted. An unconfirmed pick stops the revision, and the re-ask also shows option B's residual sentence verbatim.
- R3-5 (staged-round repeats, tripwire): the staged-round route is accepted: a repeat there is ADDRESS `--ref` (Candidate rules, Verification 6). The keep-count tripwire is declined (M6(g)).
- R3-6 (SHA in the rationale): accepted (Logging, row 96).
- R3-7 (denylist baseline): accepted. `freeze` copies the plan as `snapshot/plan-baseline.md`, and the revision's check runs over lines added relative to it.
- R3-8 (data, not instructions): accepted (step 0).

*Round 3, staff-sdet*
- Finding 1 (freeze and parse): accepted. `census` runs named stages, `census --reparse` re-runs the parse on the frozen snapshot, the halt rule uses it, `selftest` asserts the snapshot stays byte-identical, and Verification 10 names `parse` as `rows.tsv`'s producer.
- Finding 2 (known answers): accepted. `selftest` carries known-answer checks with row 33's figures, and stop candidates, flips and `no`-eligibility are cumulative-kind only (M9, step 4).
- Finding 3 (self-graded paper test): accepted. The paper test gains the four rows plus staged-round, fail-closed and new-exclusion rows. This plan writes the expected dispositions before any run. The blind reader sees the edited L378 text and classifies every synthetic row. The bar is directional and bounded to one fix and one re-run (Verification Phase 2a item 7).
- Finding 4 (unpinned exception clauses): accepted (row 93).
- Finding 5 (row 65): accepted. Row 65 cites `test_review_ledger_lib.py` L1160, and the follow-up is now the consult-successor test.
- Finding 6 (row 82 called open): accepted. M6(d) and the three Addressed lines now cite row 82 as the engineer's choice.
- Previously settled, item 2 (Sonnet fallback): the engineer answered "Keep it (Recommended)" (row 84).
- G6 evidence: corrected.

*Round 3, staff-platform-engineer*
- I2-1 (un-timed census): accepted (step 2's census stages, resume, subprocess timeouts and background call; row 88).
- Shell discipline (`record` arguments): accepted. `record` takes single-quoted arguments with the `'\''` escape (row 89), it echoes what it stored, and the canary's label holds a `'`, a `$` and a backtick. A length cap is not taken: it would reject an answer that must be stored verbatim.
- B8-1 (retention delete): accepted. `purge` discovers its target and keeps the manifest consistent, and the purge-now option keeps `denylist.txt` and the script until Phase 2a merges.
- B9-1 (merge queries, #1211): accepted. Step 10 reads the trim's branch from the registry and treats an empty result as unknown (row 91). GH-1213's merge is its implementing PR. Step 0 runs the #1211 check that row 80 promised.
- B3-1/B13-1 (return vocabulary, revision actor): accepted. The return vocabulary is on the revision's stop list and in M14 (row 95). The session performs the asks, queries and `denylist-check`, because `plan-architect` cannot (row 75).
- Canary coverage: accepted. The completion mark is a last line written with `Write`, with no rename, and the canary performs that shape (row 90).
- Observability: accepted. The decision file states that detection is manual until the token counter ships, names the engineer as its owner, and gives the query, which Phase 2a runs once.
- B5-1 (stale text): accepted (G6, rows 44 and 67, and the row-82 passages).
- B1-3 (denylist baseline): accepted (as R3-7).
- B11-1 (rollback fact): accepted (decision file).
- I3-1 (CHANGELOG): accepted (`CHANGELOG.md`, row 94).

*Round 3, staff-product-engineer*
- Finding 1 (row 82 called open): accepted (as SDET finding 6).
- Finding 2 (late hand-off): accepted (M14, step 0). Step 8's file carries only study-dependent parts.
- Finding 3 (attribution): row 21 gains its coverage clause, and the round-2 CISO entry and M6(f) now relay B's text as the option's description. Moving Context L35 is the session's (session notes).
- Finding 4 (consumer docs): accepted. Row 64 and Out of scope no longer say the doc covers it. The decision file names the effect, who writes the cost-ledger `--note`, and what prompts it (row 97).
- Finding 5 (dense line, unvalidated token): the safe-direction fallback is now stated (Repeats). Item 6 ships as two paragraphs when the line budget allows (Phase 2a).
- Previously settled, item 3 (Sonnet fallback): row 84.

*Round 4, foundation (rows 100 and 101)*
- The engineer asked for a `plan-architect` consult on Phase 1's foundation and chose to apply its recommendation. The evidence layer stays whole. The operational harness is now `freeze`, `derive`, `validate`, one decision file per answer, and whole-dir retention (M13). Earlier entries that cite `census` stages, `--reparse`, `STATE.txt`, `record`, `decisions.tsv`, `verdicts.tsv`, the completion mark, attempt numbers, the free-space check or a partial purge describe resolutions this revision replaced. Rows 29, 88 and 90 are rewritten, row 89 is removed, and M13's alternatives (d)-(h) give way to its scope sentence.

*Round 4, ciso-reviewer*
- Retention against 1e and Verification 7 (CONCERN): resolved by whole-dir retention. Step 8 offers no purge, so step 9, the revision and Verification 7 keep their inputs until step 10.
- Carry requirement (CONCERN): accepted. The requirement and the revision's stop test name an explicit per-row opt-in made when the row is logged, which item 6 never sets, and "or a keep with no rule token" is gone. Row 87 now leaves row 49's suggestion open through that opt-in, and row 65 is unchanged.
- "Concerns" trigger (FYI): accepted for exclusions 4 and 5. Its Verification 6 row, row-number cites in shipped text, and Verification 7's data clause go to the revision.
- `merged-lookup` on reparse (FYI): moot. The `gh` capture is part of `freeze`, which nothing re-runs once labels exist, and `selftest` runs every `gh`-dependent function over canned output.
- `CHANGELOG.md` scope (FYI): the engineer answered "Keep it" (row 99).
- Previously-settled item 1 (stray dirs): step 2 asks the engineer only if `discovery.tsv` lists one.

*Round 4, staff-sdet*
- Finding 1 (no-op reparse assertion): moot. `--reparse` is gone, and `derive` deletes its own outputs before regenerating, so a parser fix cannot leave old rows in place.
- Finding 2 (`git`/`gh` seam): accepted. `selftest` uses canned output or a fixture repo in the work dir, and it adds the window-range, part-split and byte-cut known answers.
- Finding 3 (golden against held-out, halt order): accepted. Golden is a spread with a stated minimum. Held-out branches come from outside golden and every earlier held-out file. The order is draw, then `derive`, then compare, and a second halt or an empty pool goes to the engineer.
- Findings 4, 5 and 6: go to the revision. Row 14 records L392 and L393's verified text.
- Finding 7 (stop-pass fixtures): accepted (step 1a).
- Finding 8 (in-place re-run): moot. `derive --check` regenerates into a scratch subdir.
- Previously-settled item 2 (held-out stop pass against rows 45 and 46): the held-out stop pass uses R1's mechanical membership alone, so no Sonnet dispatch judges an exemption.

*Round 4, staff-platform-engineer*
- F1 (network stage in the snapshot): moot. The `gh` capture is in `freeze`, a failure records `unknown` and `freeze` completes, and `derive` makes no network call.
- F2 (lock, liveness, `STATE.txt` race): moot for `STATE.txt` and `record`, which are gone. `derive` takes an exclusive lock, prints one terminal line and exits non-zero on failure, and `validate` is the resume check.
- F3 (attempt protocol): moot. Fixed per-batch names, labels keyed by row id, and `validate` counting rows itself replace it. A re-dispatch waits for the earlier one to return, and two failed validations go to the engineer. A concurrent-dispatch cap is not taken, because a rate-limit failure is loud and costs one re-dispatch.
- F4 (purge scopes, delete-now): resolved by whole-dir retention with one scope.
- F5 (mid-stage kill): moot. `derive` deletes its outputs first and writes `derive.done` last, and `freeze` writes its manifest last.
- F6 (`record` encoding): moot. Answers are written with the `Write` tool.
- F7 (bytes, list size): accepted. Total input bytes per tier and model are stated before Tier A and at step 5, and the packet header counts each hand-check category by direction. `handcheck.tsv` is the batch verdict file.
- F8: Verification 10 states the expected `??` line. The no-pick `CHANGELOG.md` heading goes to the revision. Row 88 keeps its `[unverified]` tag, because this dispatch cannot read the Bash tool's description.

*Round 4, staff-product-engineer*
- Benefit visibility and fidelity (CONCERN): accepted. The pre-Tier-A question leads with `derive`'s mechanical ceiling. M9 never counts a dirty pass that holds a stop-and-ask- or consult-resolved row as a stop, and it counts those passes (row 102). Row 92's count sits beside the exclusion counts, and steps 4, 5 and 8 label flips as upper bounds on verdict coverage.
- Runtime judge's source (CONCERN): accepted (§ "What GH-1213's dispatch must provide", row 75, the revision's input check).
- Step 0's note (FYI): accepted. That section's opening paragraph says the list matters only if a rule is picked and that R2's diff can wait for an R2 pick.
- Post-ship checks and shipped naming (FYI): go to the revision.

*Round 5 (findings filed under the `1791153214-clean-pass-criteria` suffix)*
- Plan text: SDET 1, 2, 3, 5, 6 and 7; platform 1, 2 (resume half), 3, 4, 5, 6, 7 (reporting half) and FYIs 8-11; CISO "selftest", "unknown-by-budget", "sites" and "stray dir"; product "ceiling", "resume", "early exits" (a) and (b), and "relay"; SDET's and CISO's rows 45-46 notes (row 103).
- 1a pointer (step 1a): SDET 1, 2, 3, 4, 6, 7 and 8; platform 1, 2, 3, 5, 6 and 7; CISO "unknown-by-budget" and "sites".
- Revision: CISO "denylist"; product "early exits" (d) and (e).
- Declined:
  - product "early exits" (c): the engineer is present at that gate, so the session asks then what "Stop" leads to.
  - product "hand-check cost": a cost, not a wrong number, and step 8's stop-at-any-time rule bounds it.
  - CISO's row-1 note: step 2's stray-dir question and its "yes" path make the engineer's answer the ruling.

## Critical files

**Phase 1** changes no repository file, this plan included. Its deliverables are step 0's note to the engineer, for relay to GH-1213's session, and the results packet and `gh1213-input.txt` in the work dir, presented in session.
- **Step 0.** Three trivial canary dispatches: `code-writer`, `general-purpose` with `model: sonnet`, and `general-purpose` with `model: opus`.
- **Step 1a.** One `code-writer` dispatch (`model: sonnet`, no `isolation: "worktree"`) writes the script and runs only `selftest`. One `general-purpose` dispatch (`model: sonnet`) writes `heldout-1.tsv`. The session runs `selftest`, `freeze` and `derive` itself.
- **Steps 1b, 1c and 1e.** `general-purpose` dispatches over the batches in `batches.tsv`. Each writes only its own fixed per-batch output in the work dir with the `Write` tool, keyed by row id, and the session runs `validate` on it.
  - Tier A runs with `model: opus`, and the blind exclusion labels with `model: sonnet`.
  - Tier B runs on the model chosen at step 5, in up to three waves.
  - The ground truth, if the engineer chose it, runs with `model: opus` alongside wave 1.
  - The second labels follow the last wave, with `model: sonnet` when Tier B ran on Opus, and `model: opus` under the Sonnet fallback.
- **Reuse.**
  - The filename grammar of `claude/.claude/scripts/findings-path-suffix.sh`. Reviewer names contain hyphens, so split filenames on the 10-digit epoch. Never call the script itself, because it appends to the shared `info/exclude`.
  - The path-token handling in `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` (`_extract_cited_paths` at L242, `_normalize_cited_path` at L264), copied rather than imported.

**Pre-Phase-2a revision** edits only `.claude/plans/clean-pass-criteria.md`, through `plan-it` Step 5 and `/plan-review`.

**Phase 2a** is one `code-writer` dispatch. Item 6's text must match its pin byte for byte, and the contradiction-route edit and the bullet edits must match their own pins, so splitting the dispatch would make two agents restate the same rule text. Its verification commands are Verification Phase 2a items 2 and 3. It edits nothing under `claude/` (row 69).
- `claude-skills/skills/code-review/SKILL.md`:
  - item 6's region after L399, as one paragraph or two (Phase 2a line budget);
  - the in-place edits to L378's two settled-site sentences, to L390, L391 and L394 (current line numbers);
  - the line recovery the revision names with the engineer's answer, if one is needed.
- `claude-skills/skills/tests/test_skills.py`:
  - a new region pin with the position assertion, modeled on L5686-5725 and reusing `_normalized_anchor_text` (L4545);
  - the new entry in `_EXPECTED_DISPOSITION_RULE_ANCHORS` (L2442-2448);
  - `_PINNED_CONTRADICTION_ROUTE_CLAUSE` (L5577-5653), updated to the edited L378 sentences only;
  - a sibling parametrized list of exact-sentence assertions for the edited L390, L391 and L394 sentences, modeled on `_REVIEW_LEDGER_PROSE_CONTROLS` (L5875) and its test (L6035-6039), reusing `_heading_section_text`.

  All other pins stay as they are.
- `docs/design-decisions/measured-non-blocking-keep.md` (new). It cites item 6 rather than restating it, and it records:
  - the picks, and which way the results pointed. It states no figure, no T and no branch name unless row 27 authorizes it.
  - how the change squares with §16 and `round3-consult-verdict-routing.md`, linked by slug. It also says that `comment-discipline-reviewer-deferred-to-cumulative-pass.md`'s cumulative pass is now the only pass whose prose findings a rule can keep. That decision states nothing item 6 overturns, so it gets no supersession line.
  - the gate chain a clean cumulative pass feeds and that gate's tier, with the engineer's acceptance of each pick by its recorded label, re-confirmed at the revision (row 79). R1 is marked as resting on a reviewer label alone.
  - that item 6 is a justified expansion. "Fixed with the round" is the rule only in a pass that has another ADDRESS row. A row kept in the final pass has only the PR body's Settled table as its record, with no ticket and no in-code marker. That is accepted because a fix round for it would reopen the cumulative pass this change exists to shorten.
  - that the orchestrator writes every ledger row, so only the Decided-by column and the rationale token show that the architect judged, and no check proves it. The engineer's confirmation of that sentence at the revision is cited (row 70). Item 6's other conditions (the cumulative-pass scope, the exclusions and the fail-closed defaults) are likewise skill text that no check enforces, so they hold only for an orchestrator that follows them.
  - the calibration population, in words: this repo's own review history. The exclusions and the architect-only bar do not depend on that population, and the leak-signal revisit applies to every stow consumer.
  - what the change does not alter: existing records are not re-derived, a branch with a cap row still stops on its next dirty pass, and staged rounds are outside item 6;
  - rollback and revisit:
    - find each use by its rationale token in ledgers and PR bodies;
    - existing rows keep rendering after a revert, because the ledger validates only at append;
    - after a revert, a live item-6 keep is an ordinary `plan-architect` SETTLED row, so a repeat at its site takes the settled-site human stop again until the keep is retired;
    - a `cumulative-review` marker written under item 6 stays valid after a revert;
    - the leak signal is an item-6 keep whose site a later pass raises and fixes with a change to production logic;
    - detection is manual until the token-counter follow-up ships. The engineer owns it, and the file gives the exact query for the rationale token, which the Phase 2a dispatch runs once to confirm it executes.
    - revisit when that signal appears, or when a branch with item 6 in force reaches the Cap's consult. If R1 or R1b is picked, also revisit on a change to the label guidance in any reviewer body, or on a reviewer model change.
  - the consumers whose numbers move with no change in quality, each cited at its own home:
    - `author_outcome.py`'s PASS share, through the existing SETTLED counter. `docs/transcript-analysis.md`'s note describes only the one-time step at the first SETTLED row, and item-6 keeps add a later rise it does not describe (row 64).
    - reviewer-yield's cited-path edit rate behind `reviewer_gap_pp` (row 81). The engineer adds a cost-ledger `--note` in the week the first merged PR carrying an item-6 keep lands, and the leak-signal query is what finds that PR (row 97).
  - this plan's Out of scope follow-ups, as the levers that would cut how many findings are generated;
  - if no rule is picked: the rules measured and declined, which way the results pointed, and the corpus freeze date. The committed plan holds the definitions and the method.
- `docs/design-decisions/finding-disposition-by-review-surface.md`: a partial-supersession line under its provenance line.
- `docs/design-decisions/round3-consult-verdict-routing.md`: a partial-supersession line under its provenance line, for any pick (row 18).
- `CHANGELOG.md`: one entry under `## [Unreleased]` › `### Changed` (rows 94 and 99). For a pick, it says a cumulative pass can now be clean with item-6 keeps, which show in the PR body's Settled table with `plan-architect` and the rule token. Only GH-1213's architect dispatch applies item 6, a restriction the skill states and no check enforces. With no pick, it names the decision file only. It cites the decision file and states no figure.

**Phase 2b** has no files here, because it moved to GH-1213.

## Verification

**Phase 1: the study's outputs are the verification.** The session checks each item before the checkpoint.
0. **Canary and setup.**
   - Each agent type wrote and read back its sentinel in the work dir. The `code-writer` canary's `python3 -I -B` call ran.
   - The source-check decision file lists each pattern's hit count, with at least one process-launch hit and one write-helper hit, and no hit outside those bounds.
   - The session wrote and read back its own sentinel decision file, and its own `selftest` call ran.
   - Step 0's note went to the engineer, and the #1211 check was stated.
1. **Coverage.**
   - `discovery.tsv` shows the worktree-list set and the recursive set agree, or it lists each difference with its reason. It also lists every listed worktree with no `agent-reviews/` or with a missing dir.
   - Every disposition record found appears in either `rows.tsv` or `unparsed.tsv`.
   - For each record, parsed rows plus unparsed blocks equal the independent counter's count.
2. **Instrument.** `selftest`'s known-answer checks passed. The halt fields show zero mismatches against `golden.tsv` and the newest held-out file. The ceiling, the other fields' mismatch counts and the join rate were stated at the pre-Tier-A question.
3. **Miss rate for each candidate rule**, for the union and for the picks, pooled and for the `stop-pass-exposed` stratum. Report:
   - how many distinct findings were labeled, how many are `no`-eligible, and whether the rule ran through the last wave or stopped early, and at which wave;
   - how many findings have P1 = `yes`, how many have P2 = `yes`, and how many have either;
   - the headline, best-case and detected worst-case rates with their Wilson intervals. Each also shows its worst result under any single-branch deletion, and the counts of branches contributing findings and misses.
   - when T is on file, where the adjusted upper bound lies against T;
   - the `unknown` count, with truncation-caused and `unknown-by-budget` counts separately;
   - the share of findings with an observable P2, the breakdown by join step and by reviewer mix, and whether P1 and P2 can reject the rule;
   - the counts before and after the hand-check, with how many labels the engineer overturned and how many flagged rows stayed unchecked;
   - how many rows each exclusion removed, how many `near-boundary` removed, and how many row 92 removed;
   - per-branch miss counts, shown in session only.
4. **Benefit.**
   - Report the dirty cumulative passes that would turn clean under each rule, under each combination of rules and under the picks, measured against behavior before GH-1004 (G8). They are labeled upper bounds on verdict coverage, and they sit beside the pre-Tier-A ceiling and the passes M9 bars.
   - Report rounds to the first clean pass, actual against under the rule. A branch that never reached a clean pass is reported as censored, never averaged in.
   - Report the branches that stay dirty under every rule. In session, PR #1009's latest passes are a required line.
   - Anything published uses only pooled totals.
5. **Escapes for each rule.**
   - BLOCKER rows after the stop that were fixed and whose site existed at the stop pass.
   - CONCERN rows as hits out of 20, with a Wilson interval and the post-stop CONCERN population N. A hit is a row whose cited site existed at the stop pass and whose miss label is `yes`. Rows whose miss label is `unknown` are counted beside the hits. Any extrapolation is labeled an estimate.
   - Rows on code added after the stop, whose site-existed label is `no`, reported separately (row 37). A site-existed label of `unknown` counts as existed.
   - Passes of unknown kind, listed.
6. **Ambiguity and agreement.**
   - Each ambiguous item is listed as path plus row index, with its reason: joins that matched nothing or tied, raw dispositions that did not normalize, records that did not parse, and `unsure` labels.
   - The full two-by-two discordance table on the miss label over every `no`-labeled finding, with an upper bound on the no-to-yes rate. Disagreements on `unsure` rows that Tier B did not label `no` are counted separately. Report the number of second-labeled rows with `read_complete` false.
   - The agreement between the blind exclusion labels and Tier A, beside the exclusion counts, split by direction.
7. **Join quality.** Report the label mix of joined rows compared with the same-epoch bullets, and the dropped-bullet count broken down by reviewer label mix.
8. **Ground truth (if run).** Report results by historical disposition, and the length of each post-merge window. The packet states that zero hits do not corroborate Tier B. Every hit on an exempted row either matches a Tier B miss or appears on the hand-check list.
9. **Tier B coverage.** Every union row has a validated Tier B label with `read_complete`, unless only rules that stopped early exempt it. Those rows are listed with the wave each rule stopped at.
10. **Reproducible and read-only.**
    - `derive --check` reports outputs identical to `derive.done`'s hashes.
    - `replay` on the validated Tier B outputs with the same T gives the same stops at the same waves.
    - Every path the write helper logs is inside the work dir, and the work dir's mode is 0700.
    - At the checkpoint, `derive --check`'s list of differences between the live corpus dirs and the freeze manifest, additions included, is named for the session to judge.
    - `git --no-optional-locks status --short` in this worktree prints nothing.
    - Containment outside the work dir and the corpus rests on the dispatch instructions, and this item does not claim to check it. Labeling agents are `general-purpose`, so they hold shell and network tools. Their input is not cooperative: the corpus quotes reviewed diffs, and this repo's diffs include skill and agent text written as instructions to agents. No agent type this repo defines can do the job with fewer tools. `Explore` and `plan-architect` hold no `Write`. `comment-discipline-reviewer` and `skill-fidelity-reviewer` hold `Write` but no shell, and `deny-reviewer-tree-mutation.sh` lets them write only under `/tmp` and `agent-reviews/` (L4-11), so neither can write into the work dir. A dedicated labeler type would be a new file under `claude/`, which Phase 1 does not edit (row 63). The engineer chose to keep `general-purpose` (rows 104 and 106).
11. **Checkpoint.**
    - The engineer worked the hand-check list in order, to the mark or, with no T, to the end or to where they stopped. Unchecked rows are counted.
    - Each decision file holds its question and the selected label verbatim, as the session read it back after writing it. For the adoption question, the file also holds every sentence step 8 requires in each option's description.
    - No repository file changed, this plan included: the plan's sha256 equals `snapshot/plan-baseline.md`'s.
    - `gh1213-input.txt` exists with its header line, and it carries no figure without a cited row-27 authorization. Its path was given to the engineer.

**Phase 2a:**
1. The pre-Phase-2a revision passed `/plan-review` before the dispatch, and every picked rule carries an `[engineer-verified]` row from the revision's re-ask.
2. From the worktree, `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py` passes. That run includes:
   - the new region pin and its position assertion;
   - the registry test;
   - the edited contradiction-route pin;
   - the exception-clause assertions (row 93);
   - `TestReviewLedgerEnumParity` in `claude/.claude/hooks/tests/test_review_ledger_script.py`, passing unedited, which shows the region sits outside the closed list;
   - `test_design_decision_files.py`, which checks the slug grammar, the italic dated provenance with no `Formerly` clause, and that links resolve;
   - the clean-definition, step-3, step-7 and defer-invariant pins, passing unedited.
3. `../../../.venv/bin/ruff check claude/.claude/ claude-skills/` passes, and so does `scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck`.
4. Before each commit, `wc -l` on `code-review/SKILL.md` reports 500 or fewer, or no more than HEAD's version. `check-skill-length.sh` enforces this again at commit.
5. Compared side by side, item 6's scope, fail-closed defaults, exclusions, production-logic definition and picked rules match this plan word for word.
6. `code-writer` runs a paper test of item 6 on synthetic rows only, as its own check. This plan fixes the rows and their expected dispositions here, before any run, and item 7's blind reader classifies the same rows. The rows are:
   - one row for each negated condition of each picked rule;
   - for each picked rule, one row meeting all of its conditions, alone in its pass with an explicit verdict, which gives a keep under that rule;
   - one row for each exclusion, each giving no keep: an FYI-labeled row in the invariant class, a BLOCKER-labeled row, a `ciso-reviewer` row, a guard-surface test row, a prose-only finding against a gate hook's own file, a disposition-rule row in a skill file, a test-only fix for an input-validation check in a script that is not a gate, and a data-exposure finding from a source other than `ciso-reviewer`;
   - a configuration-file row and a machine-read-comment row, both production logic under the allowlist;
   - a mixed FYI and CONCERN row;
   - a SKILL.md edit against a `docs/` edit;
   - an FYI row with no suggested fix, which qualifies under R1 alone;
   - a fix touching one prose file and one code file, which qualifies under no rule that names the fix;
   - a qualifying row with no in-repo file, which cannot be a keep, so its pass gives ADDRESS for all;
   - a `near-boundary` row, which does not qualify;
   - whole passes, judged as dispositions:
     - every row qualifying, with an explicit verdict for each row, which gives keeps;
     - qualifying rows plus one real CONCERN, which gives ADDRESS for all;
     - qualifying rows plus one row awaiting a stop-and-ask, which gives ADDRESS for all;
     - every row qualifying, with the verdict missing, hedged, or covering only some rows, which gives ADDRESS for all;
     - qualifying rows beside a closed-list DEFER row and a carried SETTLED row, with no other ADDRESS row, which gives keeps for the qualifying rows;
     - qualifying rows whose only other ADDRESS row is a non-qualifying repeat at a live keep's site, which gives ADDRESS `--ref` for the repeat and ADDRESS for the rest;
   - a staged-round row, which item 6 must not cover;
   - a repeat at a live keep's site in a staged round, which gives ADDRESS `--ref`;
   - a repeat at a live keep's site in a later cumulative pass that has an ADDRESS row, which gives ADDRESS `--ref`;
   - a repeat at a live keep's site in a later cumulative pass where every row qualifies, which gives a fresh keep `--ref`, never a carry. The carry rejection is pinned by `test_review_ledger_lib.py` L1160 (row 65).
   - a repeat at a live keep's site with no verdict on the repeat, which takes the settled-site stop;
   - a repeat at the site of a keep logged without its rule token, which takes the settled-site stop;
   - a different-failure-mode finding at a live keep's site, which takes the settled-site stop;
   - an invariant-class finding at a live keep's site, which takes the settled-site stop;
   - a row that fails a picked rule, whose finding text and cited file each carry a sentence telling the judge to keep it, which gives ADDRESS.
7. Before commit, the session runs a blind concordance check. `general-purpose` dispatches (`model: opus`, row 74), batched, see item 6, the edited L378 sentences and the edited L390, L391 and L394 bullets. If R2 is picked, they also see each R2 row's added-line ranges. They classify:
   - every judged-exclusion row and every row where Tier A and the blind exclusion label disagreed;
   - every `needs-judgment` row;
   - a seeded 20 exempt rows for each picked rule, and a seeded 20 mechanically excluded rows;
   - every synthetic row in item 6, against its written expected disposition.

   The acceptance rule:
   - Every synthetic row matches its expected disposition.
   - On corpus rows Tier A did not mark `near-boundary`, the reader never keeps a row Tier A did not exempt. A disagreement the other way, where the reader declines a row Tier A exempted, is counted and reported, because it is the stricter reading.
   - `near-boundary` rows are reported separately and are outside the bar, because item 6 already excludes them.
   - A failure is fixed once, as a wording defect or a fixture fix, and the check re-runs once on freshly seeded draws. Whatever still fails then goes to the engineer as a definition question, which may re-enter 1e. There is no third run.
   - The results stay in the work dir.
8. `denylist-check` runs at three points: at each Phase 2a commit over the staged diff, at each PR-body write over the rendered body file, and at the revision over the plan's lines added relative to `snapshot/plan-baseline.md`. It finds zero hits over a non-zero scan, or each hit is named and cleared in session, with its reason recorded in a decision file.
9. Run `/skill-review` on the SKILL.md diff (a hook enforces this), then `/code-review`, then `/ready-for-review`. The PR body, the decision file and the `CHANGELOG.md` entry contain no study figure, and no T, unless that item has its own authorization under row 27, cited next to it.

## Out of scope

- **PR #1009 and branch `review-round-cost-pooled`.** Its worktree's `agent-reviews/` is only read, as part of the corpus.
- **The marker machinery.** `marker.sh`, the hook that enforces `cumulative-review`, and the marker's authorization rule all stay as they are (row 7). No gate's tier line changes (row 79).
- **The review ledger.** The disposition enum, the five `--defer-criterion` values, `review-ledger.sh`, `_review-ledger-lib.sh` and their tests stay as they are (rows 13 and 69).
- **A ledger check that proves the architect judged an item-6 keep, and a test for the successor path item 6 routes repeats through.** That path is a fresh `plan-architect` SETTLED `--ref` to a live `plan-architect` SETTLED. The carry rejection itself is already pinned (`test_review_ledger_lib.py` L1160). Both items are under `claude/`, which the engineer's choice leaves untouched (rows 65 and 70). They are recorded as follow-ups.
- **R3, and any change to settled-site or *keep* handling beyond routing item-6 keeps' repeats (row 71).** These belong to GH-1213 (rows 48 and 49), within row 87's requirement.
- **The `ready-for-review` loop change (former Phase 2b) and its decision rule.** These belong to GH-1213 (rows 50 and 51).
- **The duplicated label line in the ten reviewer agent bodies.** Defining FYI there would change the label mix the study measured, so any such change has to be re-measured first.
- **Follow-up process changes.** These are the other candidates from brief §2. They are recorded as follow-ups for the engineer, and no issue is filed without authorization:
  - spawning the backend reviewer on new production logic;
  - requiring SDET "covered" claims to cite a mutant that was actually run;
  - making reviewer-named mutations and greps code-writer acceptance criteria;
  - scoping guarantees at plan time;
  - reviewing comment discipline in staged rounds;
  - having the Cap count only cumulative records. The session explorer reports it already does, and the follow-up confirms that.
- **Separating item-6 keeps in `author_outcome.py`, and comparability sentences in `docs/transcript-analysis.md` and `docs/cost-ledger.md`.** The existing SETTLED counter counts item-6 keeps as PASS rows, but `docs/transcript-analysis.md` L1220 describes only the one-time step at the first SETTLED row, and `reviewer_gap_pp` has no note at its own home (rows 64 and 97). The decision file names both effects, the owner and trigger of the cost-ledger `--note`, and the detection query. A rationale-token counter and the consumer-doc sentences are follow-ups.
- **A keep-count tripwire in item 6** (M6(g)).
- **Committing the study script.** A tool for re-measuring before and after the change is a follow-up (M2).
- **Raising the 500-line cap on `code-review/SKILL.md`.** The cap is set for this path alone (`check-skill-length.sh` L111-114), so raising it would not affect other skills. It stays because 500 lines is Anthropic's documented SKILL.md ceiling (L13). Phase 2a works within the ratchet instead (row 15).
- **Extracting the Item ownership table.** That table belongs to the trim, whose G3 bars a runtime sibling file. It comes back here only through the pre-Phase-2a revision, and only to the engineer and the trim's owner (row 62).
- **Measuring staged commit-gate rounds, or applying item 6 to them** (G2, row 30). A repeat at a keep's site in a staged round is ADDRESS `--ref`, which is not an application of item 6.
- **GH-1004's interaction with the flips.** The study measures against behavior before GH-1004 and says so (G8).
- **Recording this round's harness Bash refusals in `docs/worktree-bash-guard.md`.**
- **Changing `agent-reviews/` files or past findings.** No file there is written, moved or deleted, and no individual finding is argued again.
