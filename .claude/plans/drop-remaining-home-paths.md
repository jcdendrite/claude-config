# Drop the remaining home-rooted paths from tracked files

## Context

Goal: remove the home-rooted-path detector matches from the four tracked files that still carry them outside `claude/.claude/hooks/tests/**`, so a conflicted sync-merge is no longer denied at `git commit` by the redaction gate's home-rooted-path detector because of any of them. The long-hex detector still matches every other line of the three fixtures (70 lines). A merge that adds a fixture wholesale, or edits another of its lines, stays denied by that detector (see Out of scope).

The engineer scoped out the seven `.claude/plans/*.md` files, which leaves four sites: one line in each of three eval fixtures and one hook comment. The engineer asked for the username in the fixtures and the hook comment to be replaced with a placeholder. Editing a fixture line re-scans it in full, so the plan also neutralizes the UUID-shaped values on that line (row 18).

## Approach

In each of the three eval fixtures, only line 5 changes. Line 5 is the `system`/`init` event. Its `memory_paths.auto` username segment becomes the literal `<username>`. Its `session_id` and `uuid` values become `<session_id>` and `<uuid>`. Those two values must change too, because the commit gate re-scans the whole edited line and both match the long-hex detector. In `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`, the traversal example in the line-255 comment becomes `/tmp/../home/<username>/repo/src/x`. No other line in any file changes.

In this plan, `/home/<u>/…` stands for an original path whose real username is never written out. `<username>` is the literal replacement text that gets committed.

Alternatives considered:
- **Delete line 5 from each fixture.** This is the lightest option for the gate: a deletion adds no `+` line, so nothing gets re-scanned. Set aside because the engineer asked to replace the username with a placeholder (row 17), and deletion drops an event the recording carried.
- **Neutralize every UUID in each file**, so `session_id` stays the same on every line. Set aside because it turns 73 lines into re-scanned `+` lines, and each of those carries its own `uuid` too. It gains nothing for any test, because nothing reads `session_id` (row 3).
- **Break the UUID shape by swapping one hex digit for a non-hex letter.** Set aside because a future reader would take it for corruption. A named placeholder reads as deliberate redaction.
- **Edit line 5 with the Edit tool, or with a scratch Python rewrite script.** Set aside in favour of `sed` (M-1).
  - The Edit tool needs the full line Read first. Its `old_string` would then carry the real username into the transcript.
  - A Python rewrite script would work, but it is heavier than an addressed substitution that prints nothing. Verification steps 1 and 3 already provide the assertions such a script would add.

**Root:** Four tracked lines outside the gate's `claude/.claude/hooks/tests/**` exclusion match the redaction gate's home-rooted-path detector: one line in each of three eval fixtures and one hook comment. The gate denies at `git commit` any commit whose staged `+` lines carry one of them, and that includes a conflicted sync-merge. The Root and the rows describe the tree before this change. Verification owns the state after it.

**Givens**

- **G-1**: The gate scans every `+` line of `git diff --cached` outside `claude/.claude/hooks/tests/**` (`deny-private-project-refs.sh:512-519`). It also scans the commit command string (`:520`) and any referenced commit-message file (`:548`). So every detector re-scans an edited fixture line in full. `.claude/plans/merge-aware-review-gates.md:155` records that "narrowing a scanner's input is a security regression". Reversing that is a gate-design decision outside this plan.
- **G-2**: Both detectors this plan runs into are always on and have no allowlist:
  - `_LIB_HOME_ROOTED_PATH_REGEX` (`claude/.claude/hooks/_lib.sh:2939`).
  - `_LIB_LONG_HEX_IDENTIFIER_REGEX` (`_lib.sh:2942`).

  They are the gate's shared contract. Loosening either one is a threat-model decision outside this plan.
- **G-3**: The private-projects blocklist is the engineer's own per-machine file, and this plan cannot inspect it. The gate reads it at commit time (`deny-private-project-refs.sh:797-836`). Its entries could match content already on line 5 besides the username.

**Rows**

1. Each fixture has exactly one line that matches the home-rooted detector. It is line 5, the `"subtype":"init"` line, and the match sits inside its `memory_paths.auto` value, once per line. `[verified: per-file count grep; -o grep of the bare /home/ prefix (line 5, once per file); key-anchored count grep placing it in memory_paths.auto on the init line]`
2. Line 5 of each fixture carries exactly two long-hex matches: the `session_id` value and the `uuid` value, both UUID-shaped. `[verified: key-anchored count grep ("session_id":"<UUID>" … "uuid":"<UUID>" on the init line, 1 per file); count grep for an init line with three or more hex matches (0 per file)]`
3. `detect_trigger_in_lines` skips any line whose top-level `event` is not a dict (`evals/run_skill_evals.py:431-433`). It also skips any line that fails `json.loads` (`:426-429`). Line 5 has no `event` key in any of the three fixtures. So line 5's content cannot change a test outcome. For the same reason, a line 5 that the edit broke would fail no test either. `[verified: run_skill_evals.py:407-467 read; count grep for an init line carrying "event": (0)]`
4. The fixtures' only consumer is `claude-skills/skills/tests/test_trigger_detector.py`:
   - `FIXTURES_DIR` is set at `:47`.
   - `_lines` reads through `read_text().splitlines()` at `:50-52`.
   - The fixtures are used at `:58`, `:65`, `:72`, and `:79`.

   No test globs, hashes, or byte-compares them. `[verified: repo-wide grep of the three fixture names; grep of the test file for glob/iterdir]`
5. All the fixture lines carry a UUID-shaped `session_id`: 18, 34, and 21 lines per file. After the edit, line 5's `session_id` therefore differs from every other line's. Nothing reads `session_id` (row 3), so the mismatch has no effect. Each file has exactly one distinct `session_id` across all its lines, and line 5's `uuid` appears nowhere else in its file. Neither matters, for the same reason. `[verified: key-anchored count grep per file; staff-sdet re-derivation of the distinct-value and single-occurrence counts]`
6. The three fixtures have no match for any of these checks:
   - the tracker-ID scan;
   - the SSH-key-path and internal-hostname detectors;
   - superset patterns of the IPv4 and Slack-channel detectors;
   - an email-shaped string.

   So after rows 1-2's values are replaced, line 5 should pass every structural detector. `[verified: count greps using ripgrep equivalents of _lib.sh:2926, :2936, :2967, :3031 (0 per file); Verification step 5 re-checks with the real gate, including its quote-stripped pass]`
7. Two of the three fixtures share the same home-path username token. In all three files the token occurs as a whole word, case-insensitively, exactly once, on line 5, so replacing that segment leaves no whole-word occurrence. In the misfire fixture the token is also a substring inside longer words on 3 other lines (7, 12, 14), which the whole-word rule (the blocklist matches with `grep -iw`, `deny-private-project-refs.sh:818`) does not count. `[verified: case-insensitive whole-word and substring count greps per file; staff-sdet and staff-platform-engineer re-derivation]`
8. `<username>` is the placeholder that `docs/private-project-redaction.md:69-71` already uses for this detector. That doc notes that `<` falls outside the `[A-Za-z0-9_.-]` class. The detector needs at least one class character right after `/home/`, so `/home/<username>` does not match it. `[verified: _lib.sh:2939; doc lines read]`
9. `<session_id>` and `<uuid>` contain no run of eight or more hex characters, no uppercase-hyphen-digit token, and no hash sign. So they match neither the long-hex detector, the tracker-ID scan, nor the Slack-channel detector. `<session_id>` mirrors the JSON key. It is also the more common spelling of that placeholder outside `.claude/plans/`: 22 files use it, against 11 for the hyphenated form. `[verified: _lib.sh:2942 by inspection; count greps]`
10. The file's only detector match is the comment at line 255 of `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`. That comment's `/tmp/../home/…` traversal example uses the placeholder word `user` as its home segment, and the detector's character class matches `user`. The edit is comment-only, so no stow consumer's runtime behavior changes. `[verified: repo-wide count grep; grep of the traversal example (2 sites); require-worktree-for-file-writes.sh grep]`
    - No code or test reads the comment.
    - The same example at `claude/.claude/hooks/tests/test_deny_reviewer_tree_mutation.py:675` is test data inside the gate's exclusion.
    - The sibling comment it mirrors, `require-worktree-for-file-writes.sh:87-89`, uses `$HOME/…` and does not match.
11. The gate's deny messages differ by branch:
    - A structural match names only the detector label (`deny-private-project-refs.sh:778`).
    - A tracker-ID match echoes the matched tokens (`:711`).
    - A blocklist match echoes the entry plus up to three matching lines, 200 characters each (`:818-831`).
    - `deny-pii-in-commits.sh` names labels only (`:546`).

    So a real commit could put fixture content into a transcript only through the blocklist branch (G-3), or through the tracker-ID branch if row 6 is wrong. `[verified]`
12. The gate can run as a dry-run probe on the staged diff:
    - It runs `git` in its own process working directory.
    - It allows silently unless `remote.origin.url` contains `claude-config` (`:363-366`).
    - It skips the scan when nothing is staged (`:513`).
    - It accepts a bare `{"tool_name":"Bash","tool_input":{"command":…}}` stdin payload, as its own tests do (`test_deny_private_project_refs.py:478-492`).

    Whether this worktree's origin URL contains `claude-config` is `[unverified; Verification step 5.1 checks it first, because otherwise the probe allows vacuously]`. The rest is `[verified]`.
13. `deny-invisible-commit-content.sh` denies any Bash call that carries both a commit-shaped fragment and an execution wrapper such as `python -c` (`:218`). So the probe's payload goes into a file written with the Write tool, and no Bash command line carries commit text. `[verified: :218]`
14. `evals/fixtures/*.jsonl` matches no `DOMAIN_RULES` or `CROSS_DOMAIN_EXCEPTIONS` predicate (`claude/.claude/scripts/select-tests.py:373-382`, `:477-509`). So `select-tests.py` selects the full suite on its own, with reason `unmatched-path` (`:553-554`). This is CLAUDE.md's first legitimate full-suite case. The full suite includes `test_shellcheck.py`, which lints every tracked shell script (`select-tests.py:418-419`). `[verified]`
15. The gate also scans the `gh pr` command string (`:565`) and a `--body-file` body (`:587`). A commit message or PR body that quotes any removed value is denied just like a fixture line would be. That includes the hook comment's old example. `[verified]`
16. The scope is the four sites that remain of the engineer's eleven once the seven plan files are excluded. `[engineer-verified: "merged 1100, let's have the next session fix the other 11 sites"]` `[engineer-verified: "we don't need to change the old plans. They will never trigger the gate because they will never be modified."]`
17. The username is replaced with a placeholder in both the fixtures and the hook comment. `[engineer-verified: "yes replace the username in the fixtures with a placeholder and the hook comment."]` The quote covers only the username. The spelling `<username>` is this plan's choice (row 8).
18. Replacing line 5's `session_id` and `uuid` values is this plan's own design. G-1 and row 2 force it, and the engineer has not stated it. `[unverified; the engineer's instruction names only the username]`

**Mechanisms**

- **M-1: Rewrite line 5 of each fixture with line-addressed, in-place `sed -E`.** Use one Bash call per file with a literal path, in BSD `sed -i ''` form on this darwin machine. The worktree Bash guard refuses a loop or a variable path, and `-i.bak` would leave a username-bearing backup in the tree. This is a one-time edit that CI never runs, so the darwin-only form has no parity impact. Each call prints nothing. On line 5 only, make three substitutions:
  - the home-rooted match becomes `/home/<username>`;
  - the key-anchored `"session_id":"<UUID-shaped value>"` becomes `"session_id":"<session_id>"`;
  - the key-anchored `"uuid":"<UUID-shaped value>"` becomes `"uuid":"<uuid>"`.

  `sed` gives no match count. Verification steps 1 and 3 catch a silent miss or a changed end-of-file newline. `anchors: root, row1, row2, row7, row8, row9, row17, row18`
- **M-2: Leave every other fixture line byte-identical,** and accept that line 5's `session_id` no longer matches the other lines. `anchors: row3, row4, row5`
- **M-3: In the hook's line-255 comment, change only the example's home segment**, from the placeholder word `user` to `<username>`, with the Edit tool. The old text is itself a placeholder, so it is safe to show. No rewrap is needed. `anchors: row8, row10, row17`
- **M-4: Probe the real gate against the staged diff before committing** (Verification step 5). This reuses the gate rather than re-implementing its detectors, its quote-stripped pass, and its blocklist read. `anchors: G-3, row6, row11, row12, row13`
- **M-5: Never print fixture content.** That rules out `Read`, `cat`, and plain `git diff` or `git show` on the three fixtures; use `--numstat`, `-c`, and count-only checks instead. The `-` side of the fixture diff carries the removed values. `code-writer`'s mandatory self-review runs `git diff -- <paths>` on each modified pre-existing file, which would print them. So the dispatch prompt overrides that step for the three fixture paths: Verification step 3's booleans stand in for the diff, and the return must not quote fixture lines. The session tells `/code-review` reviewers to pathspec-exclude `evals/fixtures/` when printing any diff and to use `--numstat` for those paths. `anchors: root, row11`
- **M-6: Describe the change in the commit message and PR body without quoting any removed value.** That includes the hook comment's old example and every UUID. If a path shape must appear, write `/home/<u>/…`. Describe the `session_id`/`uuid` edit as gate-driven replacement of the values on that line. State that the change unblocks the gate and is not an exposure-removal control. The removed values stay in history, and the seven plan files and the test-directory matches still carry the home path. `anchors: row15`

## Critical files

- **Modify** `evals/fixtures/misfire-plan-review-instead-of-code-review.jsonl`, line 5 only (M-1).
- **Modify** `evals/fixtures/no-trigger-typo.jsonl`, line 5 only (M-1).
- **Modify** `evals/fixtures/skill-fired-code-review.jsonl`, line 5 only (M-1).
- **Modify** `claude/.claude/hooks/deny-reviewer-tree-mutation.sh`, the line-255 comment only (M-3).
- **Reuse:**
  - `claude/.claude/hooks/deny-private-project-refs.sh` itself, as the pre-commit probe (M-4).
  - The `<username>` placeholder from `docs/private-project-redaction.md` (row 8).
- **Not created in the repo:** the probe payload, the probe output, and the step-3 check script live in a scratch directory outside the repo. They are never staged, and the implementer deletes them afterwards.
- **Dispatch: one `code-writer` dispatch (`model: sonnet`) covering all four files, in a single phase.** The fixture set and the hook comment are disjoint, but a split still fails the splitting test, for three reasons:
  - Both halves rest on the same background (G-1, G-2, row 11, M-5, M-6), and each prompt would have to restate it.
  - The fixtures alone make `select-tests.py` run the full suite (row 14), so a second dispatch would pay for a second full-suite run.
  - The whole change is four lines.

  The dispatch prompt must carry M-5 and M-6. Its verification command is Verification steps 1-7.

## Verification

A linked worktree has no `.venv` of its own, so use `../../../.venv/bin/<tool>` in place of `.venv/bin/<tool>`. Run steps 1-5 before committing: step 3 compares against `HEAD`, and step 5 needs the four files staged. Every step prints only counts, booleans, or category labels, never fixture content (M-5).

1. `git diff HEAD --numstat -- evals/fixtures claude/.claude/hooks/deny-reviewer-tree-mutation.sh` prints exactly four rows, each showing 1 added and 1 removed, and no other path.
2. `git grep -cE '/(Users|home)/[A-Za-z0-9_.-]+' -- ':(exclude)claude/.claude/hooks/tests/**'` lists exactly the seven `.claude/plans/` files named under Out of scope, with 10 matching lines in total.
3. Run a stdlib Python check from a scratch file, as a single-statement Bash call. For each fixture, it compares `git show HEAD:<path>` with the working-tree file and prints one line of booleans and counts. Every value must come out true or 0:
   - The line count is unchanged, and only line 5 differs. Split on `"\n"`, not `str.splitlines()`, which also splits on U+2028, `\x85`, and `\x0c` and drops the trailing-newline state. Compare the trailing-newline state too.
   - Every working-tree line parses with `json.loads`. pytest cannot catch a broken line, because the detector skips it silently (row 3).
   - The parsed new line 5 equals the parsed old line 5, with three fields set to their expected values:
     - `session_id` is `<session_id>`.
     - `uuid` is `<uuid>`.
     - `memory_paths.auto` is the old value with its home-rooted match replaced by `/home/<username>`.
   - The new line 5 has 0 matches for the home-rooted pattern and 0 for the long-hex pattern.
   - The old username token has 0 case-insensitive, whole-word occurrences anywhere in the working-tree file. Whole-word means `grep -w` semantics: word characters are `[A-Za-z0-9_]`, and the blocklist matches this way (`:818`). The check takes the token in-process from the `HEAD` line's home-rooted match and never prints it. In the misfire fixture the token is also a substring of longer words on 3 other lines (row 7). Those do not count, and the check must not stop on them. A nonzero whole-word count means the token also appears outside the path. In that case, stop and report only the count, and do not edit further lines.
4. `../../../.venv/bin/pytest "claude-skills/skills/tests/test_trigger_detector.py::TestDetectTriggerInLines"` passes. This is a smoke check of the fixtures' one consumer. Two of its four fixture-reading tests assert `fired is None` and pass on a broken fixture, so step 3, not this step, is the evidence that line 5 is intact.
5. Run the gate probe with the plan file and the four edited files staged, each `git add` in its own Bash call. Staging the plan file too makes the probe scan the same added lines the real commit will. Keep every command line free of absolute paths, because the gate scans command strings and its blocklist branch echoes them.
   1. `git config --get remote.origin.url | grep -c claude-config` prints `1` (row 12). If it does not, halt: the gate would allow vacuously.
   2. `git diff --cached --numstat` shows exactly five rows: the four edits at 1 added and 1 removed, and the plan file. This proves the files are staged; step 1's `git diff HEAD` does not.
   3. Create one scratch directory with `mktemp -d` (mode 0700). Remove it on every exit path, including a stop-and-report and a reader crash. The gate's blocklist branch echoes matching lines into the probe output, so the output file may hold fixture content.
   4. Use the Write tool to put `{"tool_name":"Bash","tool_input":{"command":"git commit -m probe"}}` into the scratch directory (row 13). Write a second payload there for the positive control, whose command text carries a synthetic home-rooted path, built at write time as a `/home/` prefix followed by a plain word and one path segment. The plan does not spell that string out, because it would match the detector. Neither payload contains real content.
   5. Run each hook as one statement, with the real payload and the positive-control payload each fed to `claude/.claude/hooks/deny-private-project-refs.sh` and to `claude/.claude/hooks/deny-pii-in-commits.sh`, output to the scratch directory.
   6. A Python reader classifies each output from the parsed stdout JSON, not the exit code, because deny and allow both exit 0. Empty output is `allow`, and any non-empty, non-JSON output is `other`. For a JSON deny it classifies from the fixed message stem, not by searching the whole reason text:
      - structural: `matches the '<label>' pattern` (`deny-private-project-refs.sh:778`), printing the label only;
      - tracker-ID: `contains tracker-ID tokens` (`:711`);
      - blocklist: `matches entries from your` (`:831`).

      The reader prints only the category or label, never the reason text or raw output. It also prints a count-only tally of non-comment, non-blank blocklist entries, so a zero-entry blocklist is visible.
   7. The positive control must come back `deny` with label `home-rooted path` from the redaction gate. If it comes back `allow`, halt: the probe scanned nothing and a real `allow` would mean nothing.

   The expected result for the real payload is `allow` from both hooks. On `blocklist` or `tracker-ID`, stop and report the category only: line 5 carries a private reference besides the username, and that needs the engineer (G-3). Remove the scratch directory on that path too.
6. Run `../../../.venv/bin/python3 claude/.claude/scripts/select-tests.py`, backgrounded with an explicit time bound. Its stderr should report a full-suite run with reason `unmatched-path`, naming the three fixtures (row 14). That is the documented command for this diff, so nobody widens or narrows it by hand. The run also covers the hook's shellcheck lint. If the run stalls or hits the bound, report that and defer to CI's full-suite run on push. Do not re-launch it repeatedly.
7. The real `git commit` passes every commit gate, with a message that follows M-6, run with no absolute path in the command line (use a separate `cd` call if needed). Step 5 already exercised both gates on the same added lines, so this step adds a check of the real commit message.

## Out of scope

- The seven `.claude/plans/` files with 10 matching lines. `[engineer-verified: "we don't need to change the old plans. They will never trigger the gate because they will never be modified."]` Step 2 reproduces this list:
  - `review-skill-procedural-fidelity.md` (3)
  - `update-plugins-across-repos.md` (2)
  - `transcript-cost-subcommand.md` (1)
  - `gitignore-runtime-artifacts.md` (1)
  - `gh558-reviewer-yield-cited-path.md` (1)
  - `transcript-scope-header-zero-match.md` (1)
  - `check-runner-may-have-misbehaved-jiggly-pnueli.md` (1)
- The gate, `_lib.sh`, its detectors, and the gate's exclusion list (G-1, G-2).
- The matches under `claude/.claude/hooks/tests/**`, including the traversal example at `test_deny_reviewer_tree_mutation.py:675`. The gate's exclusion covers them (row 10).
- Git history. The removed values stay in history, the earlier username segments included. Rewriting history, or otherwise dealing with that exposure, is the owner's call. Nothing needs rotating: a scan of every line of the three fixtures for 13 credential shapes (API-key prefixes, GitHub and Slack tokens, AWS key IDs, bearer, PEM, JWT, long base64, email, IPv4, key/token/password assignments) found 0 matching lines, and each fixture was introduced by one commit already reachable from `origin/main`. `[verified: ciso-reviewer count scan]`
- A test that asserts tracked `evals/fixtures/**` carry no home-rooted path or long-hex value, using the shared detector regexes. Nothing enforces that invariant today, so the next merge that touches another fixture line hits the gate again. Raised to the engineer as a follow-up decision.
- Deleting line 5 outright. It would add no `+` line for the gate and would also drop the recording machine's environment fingerprint (MCP server, slash-command, and skill lists). The engineer chose placeholders. `[engineer-verified: "yes replace the username in the fixtures with a placeholder and the hook comment."]`
- Rolling this change back with `git revert`. The revert re-adds the removed values as `+` lines, so the gate denies it. Rollback is forward-fix only. The hook-comment revert alone is safe.
- Making `session_id` the same on every line of a fixture (M-2, row 5).
- The other lines of every `evals/fixtures/*.jsonl` recording. They still carry UUID-shaped values. A future edit to any of those lines, or a merge delta that adds one, runs into the long-hex detector the same way line 5 does here (G-2).
