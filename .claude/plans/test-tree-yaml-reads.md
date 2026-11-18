# Replace the remaining hand-rolled YAML reads in the test trees

## Context

Goal: PR 2 of 3 answering the owner's review of merged PR #1463. Replace the remaining hand-rolled YAML frontmatter reads in the test trees with `yaml.safe_load` or `parse_frontmatter`, and give the two hand-rolled readers that stay a one-line in-code reason each.

Ask: the engineer selected "More test-tree YAML reads" and "Fix defects, justify the rest (Recommended)". The merged plan `move-workflow-tests-and-use-yaml-parser.md` records both (`git show origin/main:.claude/plans/move-workflow-tests-and-use-yaml-parser.md`).

The engineer then selected "Trim, then split (Recommended)", which cut the work into three PRs. This is PR 2, and PR 1 merged as #1472.

During `/plan-review` the engineer widened the scope by selecting "Fix parser now". That label answered whether to fix `parse_frontmatter`'s non-mapping and unclosed-block behavior in this PR, with the `skill-management` plugin version bump the fix needs. The other option was to keep a test-side shim until PR 3.

In round 2 of `/plan-review` the engineer selected "Line-anchored closer" and "Literal-boolean flag predicate" to keep two further items in this PR. The engineer added: "Those sound good to me, but what is the architect think".

A later `/code-review` round found the line-anchored closer more permissive than the harness. The engineer then selected "Match the harness (Recommended)", which supersedes "Line-anchored closer" (row 40).

Why now: the owner asked that hand-rolled YAML parsing give way to a standard parser repo-wide, and #1472 handled only the workflow-test reads and one frontmatter read. The evals-harness parsers and their docs are a third PR and out of scope here.

Intended outcome, restated so this plan stands alone:

- `claude-skills/skills/tests/test_skills.py` reads flags, `description` and `when_to_use` through `parse_frontmatter`. One discovery-only fallback maps unreadable frontmatter to `{}` so test collection survives.
- `claude/.claude/hooks/tests/test_agent_roster.py`'s `^tools:` regex becomes `_declared_tool_names`.
- `claude/.claude/scripts/tests/test_transcript_period_cost.py` reads the review-pr `name` with `yaml.safe_load` and adds no validator importer.
- `evals/test_review_bench_fixtures.py` asserts on the parsed `name`, `tools` and `model` of the block `arms._split_frontmatter` returns.
- The two readers that stay hand-rolled, `claude/.claude/scripts/_ci-python-pin-lib.sh`'s `sed` and `claude/.claude/scripts/transcript_analysis/subagent_mix.py`'s `model:` regex, each carry a one-line reason, and the commit message justifies both under `CLAUDE.md`'s "Hand-rolled logic in non-trivial domains" bullet.
- Tests cover discovery surviving a malformed and a frontmatter-less `SKILL.md` in both discovery arms, three malformed frontmatter shapes, and the `when_to_use` path through `_skill_description`.
- `parse_frontmatter` (`plugins/skill-management/scripts/validate_skill_structure.py`) raises one deliberate error for an unclosed block and for a block that is not a YAML mapping, and the plugin version is bumped as `plugin-semver` requires. The test-side `_frontmatter_mapping` shim the draft would otherwise need is not added. How `validate()` handles the new error is the plan's call to make and to justify.
- No new third-party dependency: PyYAML is already declared.

A six-file draft of this work was reviewed rather than started over, and has since grown to ten files that are staged (row 44).

## Approach

Keep the six-file draft and fix the parser it reads through. `parse_frontmatter` keeps ending its block at the first `---` after the opener, mid-line included, which is where Claude Code's skill loader ends it (rows 29 and 41). It raises a new `FrontmatterError`, a `ValueError`, for an unclosed block or a non-mapping one. `validate()` reports that error as a violation. The `skill-management` plugin moves to 5.1.0, because a falsy non-mapping block is now denied (row 25).

The draft's test helpers keep catching `ValueError`, which covers the new error and PyYAML's impossible-date error (G2). Discovery and the two flag asserts read `disable-model-invocation` through one literal-boolean predicate (row 30). No `subagent_mix` parity test is added, because a test reading the real agent files from `test_transcript_subagent_mix.py` needs a `select-tests.py` target this plan does not add (row 12).

**Starting state** (row 44). The `code-writer` brings the staged files, in place, to the finished state the edits below describe for each file. No edit below is a step from `HEAD`.

- An edit the index already holds needs no change.
- A class, test, constant or changelog entry the index already holds is revised, never added a second time.
- Most line anchors predate the staged implementation, so the `code-writer` finds each site by name.

These rules apply because the index already holds an earlier implementation of every Critical file.

**Draft defects.**

- (a) `_skill_description`'s callers after it returns parsed text: keep (row 3).
- (b) `_skill_description` raising on malformed YAML: keep (row 4).
- (c) The scalar-only test admitting three exception types: fix (row 5).
- (d) `split("---", 2)` in `test_transcript_period_cost.py`: keep (row 6).
- (e) The unclosed and scalar-only shapes appearing only in the unit test: keep (row 7).
- (f) Discovery and two asserts reading `disable-model-invocation` by truthiness: fix (row 30).

The draft also changes discovery in two ways the exploration did not flag. A frontmatter-less `SKILL.md` now enters the model-invokable lists, and flags are read as parsed values, not substrings. Neither changes what the real tree collects (rows 8 and 9), and Verification step 3 proves it.

**The parser edit** (`plugins/skill-management/scripts/validate_skill_structure.py`, rows 17 to 19, 29 and 35). This class sits above `parse_frontmatter` (`:54`):

```python
class FrontmatterError(ValueError):
    """A frontmatter block with no closing ``---`` line, or one that is not a YAML mapping."""
```

`parse_frontmatter`'s body after its docstring (`:64-68`) becomes:

```python
    content = skill_file.read_text()
    if not content.startswith("---"):
        return {}
    # Claude Code's skill loader ends the block at the first --- after the opener, even mid-line, so this does too.
    closing = content.find("---", 3)
    if closing == -1:
        raise FrontmatterError("frontmatter has no closing --- line")
    frontmatter = yaml.safe_load(content[3:closing])
    if frontmatter is None:
        return {}
    if not isinstance(frontmatter, dict):
        raise FrontmatterError("frontmatter is not a YAML mapping")
    return frontmatter
```

`parse_frontmatter`'s docstring keeps its summary line and HEAD's opening `Reads ``skill_file``, slices between the opening and closing ``---`` delimiters, and returns the result of ``yaml.safe_load``.` It then states six facts, one sentence each:

- It returns `{}` when the file does not begin with `---` or its block is empty.
- It raises `FrontmatterError` when no `---` follows the opening one or the block is not a mapping.
- The `FrontmatterError` message names no file, because each caller adds the path.
- It raises `yaml.YAMLError` on invalid YAML.
- PyYAML raises a bare `ValueError` for an impossible date such as `2024-02-30`.
- `validate()` reports `FrontmatterError` and `yaml.YAMLError` as violations.

`validate()` carries this arm directly after its `yaml.YAMLError` arm (`:95-102`). Its docstring's check 1 keeps HEAD's first line and ends `must parse with ``yaml.safe_load`` without raising and be a mapping.` Its `# Check 1` comment reads `# Check 1: the block has a closing ---, parses as strict YAML, and is a mapping.` The `yaml.YAMLError` arm's message and hint stay HEAD's (Out of scope).

```python
    except FrontmatterError as exc:
        violations.append(f"{skill_file}: {exc}")
        return violations
```

In `corpus_budget_violations`, the trailing comment leaves the `except` line at `:155`. The `except` body holds `# ValueError covers UnicodeDecodeError, FrontmatterError and an impossible date such as 2024-02-30.` above its `continue` (row 22).

**Consumer edits** (rows 5, 20, 21 and 30).

- `test_skills.py` imports `FrontmatterError` from `validate_skill_structure` (`:83-88`, in ruff's isort order).
- `_discovery_frontmatter` (`test_skills.py:128-132`) returns `parse_frontmatter(skill_file)` inside its `try` and keeps the draft's `except (yaml.YAMLError, ValueError)`. Its `isinstance` line goes, and its docstring stays.
- A new `_disables_model_invocation(frontmatter: dict) -> bool`, placed after `_discovery_frontmatter`, returns `frontmatter.get("disable-model-invocation") is True`. Its docstring reads "Only a YAML boolean true counts, so a quoted or misspelled flag keeps the skill under the TRIGGER sweeps." The draft's four truthiness reads at `:234`, `:256`, `:369` and `:591` call it.
- `_write_flagged_skills` (`:652-664`) gains `("quoted-false", 'disable-model-invocation: "false"')`, and its docstring names the quoted value. `test_discovery_survives_malformed_and_frontmatterless_skills` expects `quoted-false-skill` and `quoted-false-plugin-skill` in the model-invokable list, each in sorted position. Its comment says a quoted `"false"` stays in that list too.
- `_UNCLOSED_BLOCK_SKILL` becomes `"---\nname: unclosed\ndescription: never closed\n\nBody text.\n"`, a forgotten closing line with no `---` anywhere after the opener (row 29).
- `_EMPTY_SEQUENCE_SKILL = "---\n[]\n---\nbody\n"` and `_IMPOSSIBLE_DATE_SKILL = "---\nname: impossible-date\nversion: 2024-02-30\n---\nbody\n"` join the shape constants at `:640-642`.
- `test_discovery_frontmatter_returns_empty_mapping_for_unreadable_frontmatter` (`:702-712`) gains `_IMPOSSIBLE_DATE_SKILL`, with id `impossible-date`.
- In `test_skill_description_raises_for_a_malformed_skill`, the `unclosed-block` and `scalar-only-block` cases each expect `FrontmatterError`. The two comment lines at `test_skills.py:750-751` go.
- `test_rules_frontmatter.py` keeps its catch at `:166`. Its three comment lines at `:167-169` become `# ValueError covers FrontmatterError and an impossible date such as 2024-02-30.` (row 21).

**New tests** (row 27). After `TestValidateContextForkRequiresExplicitBackground` (ends `:2749`), `TestParseFrontmatterRejectsUnclosedAndNonMappingBlocks` holds six tests. Each test writes its file with `_write_skill_file`. "The three shapes" below means `_UNCLOSED_BLOCK_SKILL`, `_SCALAR_ONLY_SKILL` and `_EMPTY_SEQUENCE_SKILL`, with ids `unclosed-block`, `scalar-only-block` and `empty-sequence-block`.

1. `test_parse_frontmatter_raises_frontmatter_error`, parametrized over the three shapes, expects `FrontmatterError`.
2. `test_an_empty_block_reads_as_an_empty_mapping` expects `{}` from `"---\n---\nbody\n"`.
3. `test_a_dash_run_inside_a_value_closes_the_block`, whose docstring reads `A ``---`` inside a value ends the block, as it does in Claude Code's skill loader.`, expects `{"name": "dash-run-skill", "description": "a"}` from `"---\nname: dash-run-skill\ndescription: a --- b\nuser-invocable: false\n---\nbody\n"`. The `user-invocable` flag after that `---` goes unread, as it does in the harness (row 41).
4. `test_validate_reports_one_violation_naming_the_file`, parametrized over the three shapes, expects exactly one violation, naming the file's path exactly once (row 35). Each case also asserts one token of its shape's message: `no closing` for `unclosed-block`, and `not a YAML mapping` for the other two.
5. `test_corpus_budget_skips_a_non_mapping_block` passes a scalar-only file and a valid skill whose `description` is `Counted.` to `corpus_budget_violations` with budget 0. It expects one violation containing `across 1 model-invokable skills`. A non-mapping file counted at zero chars would make that `across 2`.
6. `test_validate_reports_a_quoted_dash_run_as_not_strict_yaml`, whose docstring reads `A quoted ``---`` ends the block inside the quote, which check 1 reports as not strict YAML.`, passes `'---\nname: quoted-dash-run-skill\ndescription: "a --- b"\ndisable-model-invocation: true\n---\nbody\n'` to `validate()`. It expects exactly one violation, and that violation contains `not strict YAML`.

The staged class's three tests after its test 5 are removed (row 27). They cover a block scalar that ends the block, a closing `---` that ends the file, and a closing line with a space after its `---`. Test 6 takes their place.

**Plugin version and changelog** (rows 25, 26 and 31). `plugins/skill-management/.claude-plugin/plugin.json`'s `version` goes from `5.0.2` to `5.1.0`. `CHANGELOG.md` carries one entry, the first bullet under the `### Changed` at `:7`, directly below `## [Unreleased]`. A second `### Changed` sits under the same heading at `:725`, so the entry is placed by reading the file, not by a heading search. The entry states:

- The validator reports an unclosed or non-mapping block as one violation line instead of exiting with a traceback.
- A falsy non-mapping block such as `[]` used to pass and is now denied, hence the minor bump from 5.0.2 to 5.1.0.
- The corpus-budget warning skips a non-mapping file, which used to raise or count as a skill with no description.
- The refresh command, `claude plugin install skill-management@claude-config --scope project`, which the PR body also carries (`plugin-semver` § "Post-merge install command").
- Beside that command, that rolling the change back takes a forward bump past 5.1.0, never a revert of the version. The PR body carries this sentence next to the command too (row 31).

Three edits bring the staged entry to that list:

- Its bullet saying a `---` inside a value no longer ends the block is removed, because the parser reads HEAD's block (row 29).
- Its `**Verdict change:**` sub-bullet is removed for the same reason.
- Its corpus-budget bullet, which says the warning skips an unclosed or non-mapping file, is rewritten to the third bullet above. HEAD's `except` already skipped an unclosed block (row 17).

**Commit shape** (row 31). The work lands as one commit, so the version bump is staged with the validator edit. The `test_agent_roster.py`, `test_transcript_period_cost.py` and `evals/test_review_bench_fixtures.py` edits and the two kept-reader comments are separable. Everything else is one unit with the validator edit. Verification gives the staging and commit order and who runs each step.

**Kept hand-rolled readers** (rows 10 and 11). The draft's comments at `subagent_mix.py:374` and `_ci-python-pin-lib.sh:9` stand as written. Critical files gives the corrected comment for `_ci-python-pin-lib.sh:8`. The commit message justifies both readers under `CLAUDE.md`'s "Hand-rolled logic in non-trivial domains" bullet, citing row 10's install order and row 11's interpreter.

**Alternatives set aside.**

- A plain `ValueError`, the lighter type: `str.index` raises the same type, so the unclosed-block cases could not fail when the parser's own check is removed.
- Returning `{}` for both shapes, the lightest option and what the harness reads for a non-mapping block (row 41): `validate()` and the commit hook would then pass a block the harness silently drops.
- A `yaml.YAMLError` subclass: `validate()`'s existing arm would attach its block-fold hint, which fits neither shape, and a scalar block is valid YAML.
- Keeping `or {}` on the `safe_load` result: a falsy non-mapping block such as `[]` would still read as `{}`, so the error would cover only some non-mapping blocks. The minor bump in row 25 follows from dropping it.
- Letting `FrontmatterError` propagate out of `validate()`: its docstring promises violation messages, and the hook would relay a traceback.
- Narrowing the test-side catches to `(yaml.YAMLError, FrontmatterError)`: PyYAML's impossible-date `ValueError` (G2) would then abort collection of all of `test_skills.py`, and `rule_frontmatter_violations` would raise instead of returning its message.
- The line-anchored closer `find("\n---", 3)`, which the staged diff carries, or a bare-line closer `find("\n---\n", 3)`: either reads past a `---` that sits mid-line before the closing line, where the harness ends the block (row 41). A quoted `description: "a --- b"`, test 6's value, would then pass check 1 while the harness drops the whole block to `{}`. That `{}` holds because the cut value `"a ` holds none of the characters 2.1.325's one parse retry re-quotes (row 41). A cut value the retry re-quotes, such as `"a: `, loads as a mapping without the keys after the cut, which these closers still read. The four sibling splitters that close at `\n---` set no contract for a gate that predicts the harness (row 29).
- Truthiness for `disable-model-invocation`, as the draft reads it: a quoted `"false"` or a misspelled value would exempt the skill from the TRIGGER sweeps (row 30).
- Widening `TestModelInvokableSkillFrontmatterIsStrictYaml`'s filter to the new violations: neither shape is a strict-YAML failure, and the TRIGGER-contract tests already fail under the skill's name (row 4).
- The parity test: row 12.

**Assumption ledger**

Root — four test files read YAML frontmatter values by substring, regex or line prefix. The two readers outside the test trees that stay hand-rolled carry no in-code reason. The test trees read through `parse_frontmatter`, which returns a non-mapping block despite its `-> dict` annotation (row 17). It also fails an unclosed block with `str.index`'s bare `ValueError` (row 17). The owner asked that a standard parser replace hand-rolled YAML parsing repo-wide. `[verified: this plan's Context and Ask line]`

Givens:

- **G1.** `yaml.safe_load` returns a block's top-level node whatever its type: `None` for an empty block, a `str` for a bare scalar, a `list` for a sequence. The mapping check therefore has to be the parser's own. Reason: PyYAML, the library `parse_frontmatter` runs on, imposes it. Verification step 4's first mutation observes it.
- **G2.** `yaml.safe_load` raises a bare `ValueError`, not a `yaml.YAMLError`, for a date-shaped value that is not a real date, such as `2024-02-30`. Every caller that maps a parse failure to `{}` or to a message therefore has to catch `ValueError`. Reason: PyYAML imposes it, since its `construct_yaml_timestamp` calls `datetime.date` with no guard (`.venv/lib/python3.14/site-packages/yaml/constructor.py:322-330` in the main checkout). Verification step 4's ninth mutation observes it.

Rows:

1. `[verified: this plan's Context]` The uncommitted six-file draft is reviewed and kept, not rewritten. *anchors: root.*
2. `[verified: select-tests.py:897, :918, :968]` Mechanism: `parse_frontmatter` in the skills and hooks test trees, raising `FrontmatterError` for an unclosed or non-mapping block (rows 18, 19 and 29), and `yaml.safe_load` on a sliced block in the scripts and evals test trees. A `validate_skill_structure` import from `test_transcript_period_cost.py` or `evals/test_review_bench_fixtures.py` would be a dependency no validator edit selects (row 23). *anchors: root.*
3. `[verified: Grep of "_skill_description(" in test_skills.py; Grep of "^when_to_use:" across every SKILL.md]` (a) Keep. Eight of the ten `_skill_description` call sites (`:312`, `:320`, `:335`, `:357`, `:631`, `:743`, `:2495`, `:2504`) test for `TRIGGER when:` or `DO NOT TRIGGER when:`. The other two, `:763` and `:766`, are the draft's own error-path and empty-path tests. The flag substring tests moved to `_discovery_frontmatter`. No repo `SKILL.md` has a `when_to_use` key, so on the real tree the listing text is the parsed `description`. Parsed text drops the other keys and joins folded lines. It can differ from raw text only where the phrase sits outside `description` or across a line break. There, the parsed text is what `validate()` measures against the listing cap (`validate()`'s Check 2, `validate_skill_structure.py:105-107` before this edit). *anchors: row1.*
4. `[verified: test_skills.py:307, :2490-2508, :2578-2584, :2599, :2619; Read of every parse_frontmatter( call site in test_skills.py]` (b) Keep. Only the class attributes that call `_specialist_skills()` and `_model_invokable_skills()` run at collection, and both read through `_discovery_frontmatter`. Every other `parse_frontmatter` and `_skill_description` call in `test_skills.py` sits inside a test function, so a malformed skill fails under that test's name. Row 20 records the one import-time read in another file. A listed skill read as `{}` fails under its own name in `TestModelInvokableSkillTriggerContracts` (`:2492-2508`), whose `_skill_description` call raises. `TestModelInvokableSkillFrontmatterIsStrictYaml` (`:2580-2584`) keeps only "not strict YAML" violations, so it catches malformed YAML but not the two shapes `validate()` now reports (row 19). *anchors: row1.*
5. `[verified: test_skills.py:745-763]` (c) Fix. `test_skill_description_raises_for_a_malformed_skill` expects `yaml.YAMLError`, `FrontmatterError` and `FrontmatterError`. The parser now raises on the scalar-only block before `_listing_text` runs, so the two comment lines about an incidental `AttributeError` go. The unclosed case narrows from `ValueError` to `FrontmatterError`, so it fails if the parser's check is removed and `str.index` raises again. *anchors: row18.*
6. `[verified: test_transcript_period_cost.py:5994-5997; claude-skills/skills/review-pr/SKILL.md:1-5]` (d) Keep. `split("---", 2)` is bounded by its `maxsplit`. What the draft drops from the six-line scan is the check that the file opens with its frontmatter. If `review-pr/SKILL.md` lost its five-line block, `[1]` would be a body segment, which fails `safe_load`, the `["name"]` lookup or the equality. *anchors: row1.*
7. `[verified: test_skills.py:684, :694, :702-712, :720-738]` (e) Keep. The tree fixture's malformed entry in each arm proves that arm reads through `_discovery_frontmatter`. The parametrized unit test proves that helper maps the three shapes and an impossible date (G2) to `{}`. Adding the other shapes to the tree would retest one helper per arm. *anchors: row1.*
8. `[verified: Grep of "\A---\n" matches 44 of 44 SKILL.md files under claude-skills/skills/ and plugins/*/skills/; the main checkout's pre-draft test_skills.py:120-133, :218-219]` A frontmatter-less `SKILL.md` now enters the model-invokable lists and fails their `TRIGGER` tests under its name. Pre-draft discovery skipped it, because its empty raw text was falsy. The pre-draft `_skill_description` docstring meant an empty return to fail downstream assertions instead. Every repo `SKILL.md` opens with `---`, so this adds no failure today. *anchors: row1.*
9. `[verified: Grep of "^(user-invocable|disable-model-invocation):" and of both "<key>: <value>" substrings across every SKILL.md]` Parsed flags select the same skills as the pre-draft substrings on the real tree. Every value is an unquoted YAML boolean. Each discovered file holds the substring only on its key line. `skill-review`, the one file with an extra occurrence, is in `_NAME_DISPATCHED_NO_TRIGGER`, so discovery skips it before reading its flags. *anchors: row1.*
10. `[verified: install-dev.sh:3, :50-56, :68-83; test_ci_python_pin_lib.py:114-122]` `_ci-python-pin-lib.sh` stays `sed`. `install-dev.sh` sources it in Step 3, before Step 5's health probe checks for PyYAML or builds `.venv`. On a first run no PyYAML exists at that point, and on a rerun none is confirmed yet. The draft's comment says `.venv` does not exist yet, which holds only on a first run or a rebuild, so Critical files corrects it. `test_ci_python_pin_lib.py` already checks the reader against `yaml.safe_load` on the real workflow. *anchors: row1.*
11. `[verified: claude/.claude/scripts/transcript-analysis.py:1; README.md:241; Grep of "import yaml|yaml\." under claude/.claude/scripts/ outside tests/ finds one comment line in _ci-python-pin-lib.sh]` `subagent_mix.py` stays a regex. Its package runs under `transcript-analysis.py`'s `#!/usr/bin/env python3`, the user's own interpreter, which `./install.sh` adds nothing to. No module in `claude/.claude/scripts/` outside `tests/` imports PyYAML. Changing that is declined in Out of scope. *anchors: row1.*
12. `[verified: select-tests.py:500-504, :942-947; test_select_tests.py:385-402, :1243-1251]` The session's proposed `subagent_mix` parity test over `claude/.claude/agents/*.md` is dropped. An agent-file edit selects the hooks and skills trees, the review-bench tests and four named test files, none of them `test_transcript_subagent_mix.py`. Read through a module-level path, the new read fails `TestCrossDomainReadCompleteness`. Read any other way, an agent edit would not select it. Either way it needs a `select-tests.py` target. `select-tests.py` is in `GLOBAL_TRIGGER_PATHS`, so editing it turns every scoped run of this PR into the full suite. A synthetic-input version needs no target, but it says nothing about the real agent files, which is the evidence the test was meant to give. *anchors: row11.*
13. `[verified: .github/workflows/tests.yml:117-119; requirements-dev.txt:4]` The draft's new `import yaml` in `evals/test_review_bench_fixtures.py` is safe in CI, which installs `requirements-dev.txt` before both pytest runs. `evals/run_skill_evals.py:201-202` still says CI "installs only pytest and ruff". That stale docstring belongs to PR 3's evals-harness work. *anchors: row2.*
14. `[verified: test_agent_roster.py:201-206, :1031-1040]` `_declared_tool_names` returns `{""}` for a missing `tools:` key, so the roster canary still fails when the key is absent. Set membership replaces the old substring test, so a tool name merely containing `Write` no longer passes. *anchors: row2.*
15. `[verified: claude-skills/skills/plan-it/SKILL.md "Name the dispatch split"; claude/.claude/agents/code-writer.md:23-30]` One `code-writer` dispatch implements the plan. The validator's tests live in `test_skills.py`, the file that also holds the helper edits consuming the new error, so the files do not partition into non-overlapping sets. The consumer edits also take the parser's error as input, which would sequence any split. The dispatch's scope is the edits plus the unit-level runs Verification assigns it, and it leaves its edits unstaged. Its charter bars staging, committing, the full suite and state-mutating commands, so the parent owns every numbered Verification step, the staging and the commit. *anchors: root.*
16. `[engineer-verified: "Fix parser now"]` `parse_frontmatter` is fixed in this PR. *anchors: root.*
17. `[verified: validate_skill_structure.py:54-68, :93-105, :152-157]` Today, `parse_frontmatter` returns `{}` when the file has no leading `---`. It raises `yaml.YAMLError` on invalid YAML and `str.index`'s `ValueError` on an unclosed block. Through `or {}` it reads a falsy block, such as an empty one or `[]`, as `{}`, and it returns any other non-mapping block as-is. `validate()` catches only `yaml.YAMLError`, so an unclosed block raises out of it and a non-mapping one raises `AttributeError` at Check 2's `.get`. `corpus_budget_violations` catches `ValueError` and so skips an unclosed block, but a non-mapping one raises `AttributeError` at its own `.get`. *anchors: row16.*
18. `[verified: Grep of "^class \w+\(ValueError\)" over *.py finds nine, e.g. evals/review_bench/arms.py:56; row 20's catch sites]` `FrontmatterError`, a `ValueError` subclass in the validator module, is the one deliberate error for both shapes. Every site that catches `ValueError` today still catches it (row 20). Its own type lets a test tell the deliberate raise from `str.index`'s bare `ValueError`, which Verification step 4's second mutation relies on. Nine repo exception classes already subclass `ValueError` for bad input. With the parser raising, a test-side mapping check could never fire, so the `_frontmatter_mapping` shim is not added. *anchors: row16.*
19. `[verified: validate_skill_structure.py:71-102; plugins/skill-management/hooks/require-skill-review.sh:326-328]` `validate()` catches `FrontmatterError` in its own arm, appends the error's message behind the file's path and returns, as its `yaml.YAMLError` arm does (row 35). It keeps its documented contract of returning violation messages. For the two shapes, the CLI exits 1 with one line instead of a traceback. An impossible date still raises out of `validate()` as it does today (G2), which Out of scope records. The hook denies on any non-zero validator exit, so an unclosed or truthy non-mapping block is denied before and after this change. *anchors: row18.*
20. `[verified: Grep of "parse_frontmatter" repo-wide; Grep of "except .*(YAMLError|ValueError)" in its six caller files; test_agent_roster.py:504-528, :1248-1255; Grep of "from .test_agent_roster import" under claude/.claude/]` `parse_frontmatter` has 34 call sites in six files. No hook or script outside the validator module imports it, since `require-skill-review.sh` runs the CLI. Seven sites catch its errors:
    - `validate()` (`validate_skill_structure.py:94`) gets the new arm (row 19).
    - `corpus_budget_violations` (`:154`) changes only its comment (row 22).
    - `test_rules_frontmatter.py:166` changes only its comment (row 21).
    - `test_rules_frontmatter.py:305`, and `test_agent_roster.py:496` and `:570`, stay as they are, and their messages stay true.
    - The draft's `_discovery_frontmatter` (`test_skills.py:129`) loses only its `isinstance` line.

    The six sites other than `validate()` catch `ValueError`, which covers `FrontmatterError` and G2's date error, so none needs a logic change. The other 27 sites catch nothing and read real repo files. Verification step 1 runs every one of them, so a repo file with either shape fails there. One of the 27 runs at import: `SCRATCH_SECTION_AGENTS` (`test_agent_roster.py:1251-1255`). `test_deny_reviewer_tree_mutation.py`, `test_doc_counts.py` and `test_send_back_skipped_self_review.py` import from that module, so an error there aborts collection of four modules. A falsy non-mapping block in a canary agent file reads as `{}` there today and raises `FrontmatterError` after this change. The site is recorded, not changed, because any non-mapping agent file already fails `test_required_fields_present` (`:504-528`). *anchors: row18.*
21. `[verified: test_rules_frontmatter.py:160-170, :436-439]` The comment at `:167-169` says `content.index()` raises the `ValueError` it catches, which stops being true. The rule file is read at `:160`, before the `try`, so a decode error raises there. Inside the `try`, `ValueError` covers `FrontmatterError` and G2's date error, and the one-line comment names both. `test_unterminated_frontmatter_block_fails` (`:436-439`) already pins the unclosed case. *anchors: row20.*
22. `[verified: validate_skill_structure.py:152-157; pyproject.toml:2]` `corpus_budget_violations`' `except` already names `ValueError`, so it skips a non-mapping block with no logic edit, where today it raises `AttributeError`. Its trailing comment moves into the `except` body, because naming `FrontmatterError` and G2's date error beside `UnicodeDecodeError` would push the `except` line past ruff's 130-character limit. *anchors: row20.*
23. `[verified: select-tests.py:289-296, :500-504, :526-535, :668, :897, :918, :921, :968; test_select_tests.py:785-790; Glob of plugins/skill-management/**]` The validator edit selects `SKILLS_TESTS_DIR`, `test_ticket_reference_discipline.py` and the six hooks-tree importers. The `plugin.json` edit selects `SKILLS_TESTS_DIR`. `CHANGELOG.md` selects nothing but counts as matched. None of the three is a global trigger, so the run stays scoped and `select-tests.py` needs no edit. The plugin has no test directory of its own, and the validator's unit tests live in `test_skills.py` (`:2627-2749`). *anchors: row2.*
24. `[verified: test_select_tests.py:372-402, :460-488; claude/.claude/hooks/tests/test_require_skill_review.py:858, :2694, :4836; select-tests.py:660]` No scan that watches validator dependencies needs a change:
    - `TestCrossDomainReadCompleteness` reads module-level path constants in test sources, and the new tests use `tmp_path`.
    - `TestSkillStructureValidatorSelection` requires a validator edit to select every importer, and the one new `FrontmatterError` import sits in `test_skills.py`, under `SKILLS_TESTS_DIR`.
    - `test_require_skill_review.py` runs the hook on frontmatter that fails `yaml.safe_load` and pins "frontmatter is not strict YAML", so its expectations hold. A validator edit alone does not select it, but the draft's `test_agent_roster.py` edit selects the whole hooks tree.

    *anchors: row23.*
25. `[verified: plugins/plugin-semver/skills/plugin-semver/SKILL.md "Bump magnitude", :38-39; the CHANGELOG.md entry bumping skill-management 4.0.1 to 4.1.0; plugins/skill-management/.claude-plugin/plugin.json:4; HEAD's validate_skill_structure.py:67-68 as the main checkout shows them; row 29]` Minor, 5.0.2 to 5.1.0. A falsy non-mapping block (`[]`, `false`, `0`, `''`) passed the commit hook through HEAD's `or {}` and is now denied. `plugin-semver`'s table rates "a trigger broadened to fire in strictly more cases" minor. The changelog entry that bumped `skill-management` from 4.0.1 to 4.1.0 rated a widening of this plugin's gate minor too. The parser reads HEAD's block for every closed file (row 29), so no other pass or deny moves. An unclosed or truthy non-mapping block was denied through a traceback's non-zero exit and is now denied through one violation line (row 19). *anchors: row17.*
26. `[verified: plugins/plugin-semver/hooks/require-plugin-version-bump.sh:225-256; claude-skills/skills/tests/test_plugin_manifests.py:34-44; the CHANGELOG.md entries bumping skill-management 5.0.0 to 5.0.1 and 5.0.1 to 5.0.2]` Only `plugin.json`'s `version` changes. The hook compares it with the version at the branch's merge-base. Marketplace entries carry no `version` key. The two earlier `skill-management` patch bumps each carry a changelog entry naming the version change and whether updating matters, so this bump gets one too. This plan cites those entries by text, because a sync with `origin/main` moves the second one (row 32). *anchors: row25.*
27. `[verified: test_skills.py:640-649, :2627-2749; the staged test_skills.py:2764-2856; validate_skill_structure.py:173-175; the staged validate_skill_structure.py:107-149; the round-6 /plan-review staff-sdet reviewer's probe of two closers keyed on the character after `---`]` The new class sits beside the validator's existing unit tests and reuses `_write_skill_file` and the shape constants. `_EMPTY_SEQUENCE_SKILL` is the one new non-mapping shape. It is a falsy one, which pins that only an empty block reads as `{}`. Test 3 pins the harness's closer (row 29). A line-anchored or bare-line closer reads the flag after the mid-line `---` and fails it. Test 5's valid second skill makes the skip observable through the skill count the violation reports. Test 6 pins the deny row 29 records for a quoted `---`. Its `disable-model-invocation: true` is a key the harness loses with that block. The line-anchored closer fails tests 3 and 6 together. A closer that skips a `---` inside a quoted value fails test 6 alone. That closer still cuts test 3's unquoted value. Under it, test 6's block parses as a mapping that passes checks 2 and 3, so `validate()` returns no violation. Verification step 4's twelfth mutation observes this. A closer keyed on the character after `---`, such as one that needs whitespace or the end of the text there, passes tests 3 and 6, because both fixtures put a space after their `---`. It would pass a quoted value such as `"a ---b"`, whose block the harness drops. That gap gets no test, because the shape is contrived and no repo file has it (row 29). The staged class's three tests after its test 5 are dropped, because each guarded the line-anchored closer and none can fail under `find("---", 3)`:
    - The block-scalar test guarded a slice that ended at the newline before the closing line. The slice now ends at the closing `---` itself, so the line break stays by construction.
    - The end-of-file and padded-closer tests guarded a closer that needed something after its `---`. `find` matches `---` whatever follows it. The newline-requiring closers `"\n---\n"` and `"---\n"` already fail test 3.

    *anchors: row18.*
28. `[verified: requirements-dev.txt:4; plugins/skill-management/requirements.txt:1]` PyYAML is already declared for the test trees and for the plugin, so the plan adds no dependency. *anchors: root.*
29. `[verified: the staged validate_skill_structure.py:70-82; HEAD's validate_skill_structure.py:64-68 as the main checkout shows them; row 41's regex and parse retry; yaml/scanner.py:1228-1239 under the main checkout's .venv/lib/python3.14/site-packages/; evals/review_bench/arms.py:68; evals/measure_subagent_model_resolution.py:313; claude/.claude/scripts/transcript_analysis/subagent_mix.py:388; evals/run_skill_evals.py:210; Grep of "^.+---" and of "^---" over the 47 SKILL.md files under claude-skills/skills/, plugins/*/skills/ and .claude/skills/, the 14 agent files and the 18 rule files]` As on HEAD, the closer is the first `---` after the opener, mid-line included. In a file whose first line is `---`, that is also where row 41's regex ends the harness's block. The block is `content[3:closing]`. Wherever a `---` follows the opener, that slice is the one HEAD's `index("---", 3)` takes, byte for byte, so every closed file's block is HEAD's. A file is unclosed only when no `---` follows its opener, the case row 41's `indexOf` guard tests for. A `---` inside a value ends the block where the harness ends it:
    - An unquoted value reads cut, and the keys after it go unread, as in the harness.
    - A quoted value leaves its quote open. PyYAML's scanner then reaches the end of the stream inside the quoted scalar and raises a `ScannerError`. That error is a `yaml.YAMLError`, so check 1 reports the block as not strict YAML. The harness drops the same block to `{}` when the cut value holds none of the characters its one parse retry re-quotes, as test 6's `"a ` does (row 41, the round-6 staff-platform-engineer's read of 2.1.325). A cut value such as `"a: ` is re-quoted, so the harness loads a block check 1 denies. HEAD's closer makes the same false deny.

    No real file has a mid-line `---` above its closing line. Each real closing line is a bare `---`. The four sibling splitters at the cited lines close at `\n---`, the first later line that starts with `---`. None of them is the validator `require-skill-review.sh` runs. Three of them read agent files, and row 41 did not trace the harness's agent-file parser. `run_skill_evals.py` reads `SKILL.md` files for the evals harness. *anchors: row41.*
30. `[verified: the main checkout's pre-draft test_skills.py:153, :168, :219, :243, :358, :580; the draft's test_skills.py:168, :183, :234, :256, :369, :591; validate_skill_structure.py:115-129; row 9's Grep]` The pre-draft substrings required the literal `disable-model-invocation: true`. The draft reads that flag by truthiness, so a quoted `"false"` or a misspelled value reads as disabled. Such a skill leaves `_model_invokable_skills()` and every TRIGGER sweep over it, and row 9's real-tree check cannot see that. All four sites call `_disables_model_invocation`, which counts only the YAML boolean `true`. The two `quoted-false` fixtures pin the discovery sites at `:234` and `:256`, and Verification step 4's eighth mutation observes the helper through them. The sites at `:369` and `:591` assert over real files, which carry only the literal `true`, so they are covered by construction only. A table test of the helper is not added, because it would pin the helper, which the discovery fixtures already observe, and not those two call sites. The validator's `background` check already demands a literal boolean. `user-invocable`'s comparison stays. Its `is False` already requires a boolean. A skill it misreads still enters the model-invokable sweep. *anchors: row9.*
31. `[verified: claude/.claude/scripts/update-claude-config-plugins.sh:194-202; test_skills.py:83-88; plugins/plugin-semver/hooks/require-plugin-version-bump.sh:225-256; claude/.claude/hooks/require-code-review.sh:17-31, :149-150; row 26]` The work lands as one commit, because `require-plugin-version-bump.sh` denies a commit that stages a `skill-management` file without a raised version. The validator edit, the version bump, the changelog entry and the `test_skills.py` and `test_rules_frontmatter.py` edits form one unit. `test_skills.py` imports `FrontmatterError` at module level, so reverting the validator alone fails that whole module at import. The hook passes a staged bump and changelog without the validator, so only staging every path in one call keeps the unit whole. `require-code-review.sh` matches its marker against the hash of the staged diff, so `/code-review` runs after that call and again after any restage. The edits to `test_agent_roster.py`, `test_transcript_period_cost.py` and `evals/test_review_bench_fixtures.py`, and the two kept-reader comments, revert alone. Rolling the parser fix back takes a forward bump past 5.1.0, never a revert of the version. The update script flags a plugin outdated only when the marketplace version sorts strictly higher, so a machine already on 5.1.0 would keep the new validator after a revert. *anchors: row25.*
32. `[verified: select-tests.py:1060-1068; plugins/plugin-semver/hooks/require-plugin-version-bump.sh:101-132; origin/main as rounds 2 and 3 of /plan-review read it; the round-6 /plan-review staff-platform-engineer reviewer's git log and git diff --stat of HEAD..origin/main, five commits (#1464, #1471, #1470, #1473, #1469 `ae0da0ff`) past the merge base]` The branch needs no sync before this work. `select-tests.py` diffs the merge-base against `HEAD` and adds working-tree changes. `require-plugin-version-bump.sh` also compares against the merge-base. Step 1 runs the branch's own `select-tests.py`, so any edit those commits made to it changes no selection here. At round 2's read, the first three commits' `CHANGELOG.md` lines sat in `[Unreleased]`'s `### Added`. At round 3's read, #1473 added one bullet to the `### Changed` at `:7`, directly below that heading's first bullet (`:9-10`). This plan's entry goes above that first bullet, so two unchanged lines separate the two hunks. Of the Critical files, `plugins/skill-management/` and `select-tests.py`, the five commits change only `CHANGELOG.md`, `test_skills.py` and `select-tests.py` at round 6's read. Upstream's `test_skills.py` hunks sit at `HEAD` lines 7110, 7160, 7805 and 8023-8082, away from this PR's hunks at lines 43-2611. A clean merge is expected but was not run. `plugin.json` on `origin/main` still read 5.0.2 at round 6's read. Verification step 5 fetches and re-reads it, because a bump there would collide with 5.1.0. Step 5 also checks that upstream's `test_skills.py` hunks stay outside this PR's, before a sync and after it. A sync moves the `select-tests.py` line anchors in rows 2, 12, 23 and 24, so a reader finds those rules by name. *anchors: row23.*
33. `[engineer-verified: "Line-anchored closer"]` Superseded by row 40. As selected in round 2 of `/plan-review`: the line-anchored closer stays in this PR. *anchors: root.*
34. `[engineer-verified: "Literal-boolean flag predicate"]` The literal-boolean flag predicate (row 30) stays in this PR. *anchors: root.*
35. `[verified: validate_skill_structure.py:96-97; test_rules_frontmatter.py:166-170, :436-439; test_agent_roster.py:497-502, :571-572; test_skills.py:756-763]` `FrontmatterError`'s messages name no file. `validate()`'s new arm prefixes `{skill_file}: ` itself, as its `yaml.YAMLError` arm does. Every caller that turns the error into a message already names the file, so a path inside the message would print twice at all four:
    - `validate()`'s new arm;
    - `rule_frontmatter_violations` (`test_rules_frontmatter.py:170`);
    - `test_agent_roster.py:497-502`;
    - `test_agent_roster.py:571-572`.

    Test 4 pins the path to exactly one appearance in `validate()`'s output, and Verification step 4's tenth mutation checks that pin. `test_unterminated_frontmatter_block_fails` (`test_rules_frontmatter.py:436-439`) matches the caller's own `invalid or unterminated` text, so it holds. `test_skill_description_raises_for_a_malformed_skill` (`test_skills.py:756-763`) matches the exception type alone, so it holds too. *anchors: row19.*
36. `[verified: git-checkout-index(1)'s "export an entire tree" example and its --prefix entry; git-archive(1) --prefix and --output entries; Glob of .gitattributes finds none; scripts/venv-run.sh:27-33; claude/.claude/tests/helpers.py:31-32; pyproject.toml:17-18; _pytest/config/findpaths.py:113-123 and :200-209 under the main checkout's .venv/lib/python3.14/site-packages/; claude/.claude/hooks/require-worktree-for-file-writes.sh:121-132]` Steps 3 and 4 run in copies under a `mktemp -d` directory, so no verification step can leave a mutated file in the feature tree or an entry in the worktree registry every session shares. `git checkout-index -a --prefix=<dir>/` writes the index's files there, a use its manual calls an "export as tree" function. `git archive` writes `HEAD`'s files, and with no `.gitattributes` it drops none. Step 3's base is `HEAD`, which holds none of this work before the commit, so it needs no merge-base read. The copies have no `.git`, so they run through the feature tree's `venv-run.sh` by its absolute path, because that script finds the shared `.venv` by running `git -C` on its own directory. Pytest takes its rootdir and `pythonpath` from the first `pyproject.toml` above the test file it is given, which is the copy's own. `helpers.py` derives `REPO_ROOT` from its own path, so collection reads the copy's files. An `Edit` to a path inside no repository passes `require-worktree-for-file-writes.sh`. Lighter primitives checked:
    - Mutating the feature tree and restoring it from the index: an interrupted run leaves a mutated gate file in the tree the commit takes, and a check that discards such leftovers cannot tell one from an unstaged edit that step 1 tested.
    - `git worktree add` for step 3's base: it registers in the git directory concurrent sessions share, and a leftover found by its commit can be another session's worktree.
    - `git stash` to set the edit aside while the base collects: the stash stack is shared with the main checkout and every concurrent session.

    *anchors: root.*
37. `[verified: run of a pyproject.toml + helpers.py + validate_skill_structure.py scratch copy under a tmp dir by the round-4 platform reviewer]` Run the way steps 3 and 4 run it, a copy's tests import the copy's code. The feature tree's `venv-run.sh`, run by its absolute path from a feature-tree cwd, starts pytest with the copy's rootdir and `pythonpath`. The copy's `helpers.py` sets `REPO_ROOT` to the copy's root. A test file copied without the modules it imports fails with `ModuleNotFoundError`, so nothing from the feature tree reaches `sys.path`. *anchors: row36.*
38. `[verified: test_skills.py:2768-2773, :2894, :8898-8904, :8920-8928, :9019, :9031, :9040, :9156, :9167, :9176; helpers.py:31-35]` Seven parametrized tests in `test_skills.py` take `str(p)` of an absolute path under `REPO_ROOT` as their ID, so their node IDs embed the tree's root. Collected at two paths, every such ID would read as removed and re-added. Step 3 therefore collects both trees at the one path `<tmp>/tree`, moving the base copy aside before the index copy lands there. *anchors: row36.*
39. `[unverified]` Two parts of the copy flow have not been run: collecting the whole `test_skills.py` in an export of the tracked tree, and `git checkout-index -a --prefix=` creating a prefix directory that does not exist. Step 3's two collects exercise both before any mutation, and step 3's last bullet sends a failure that is the copy's to the engineer. *anchors: row36.*
40. `[engineer-verified: "Match the harness (Recommended)"]` After `/code-review` found that the line-anchored closer passes a block the harness strips (row 41), the engineer chose that the validator's closer match the harness's. This supersedes row 33. The option's description was the session's own wording, so this row covers the label alone. *anchors: root.*
41. `[verified: finding R1 of a round-4 /code-review staff-backend-engineer review, which read the installed Claude Code 2.1.325 bundle; grep -a of the same bundle by the round-5 /plan-review staff-platform-engineer and staff-sdet reviewers; the round-6 /plan-review staff-platform-engineer reviewer's trace in that bundle of the two paths to {}, the parse retry and the second regex's callers; this plan's author read no bundle, and no fixture was run through the harness]` In 2.1.325, the skill loader parses a `SKILL.md` through a shared frontmatter parser. The parser guards on `indexOf("---",3)<0` and matches `/^---\s*\n([\s\S]*?)---\s*\n?/`, so its closer is the first `---` after the opening line, mid-line included. When the YAML parse fails, the parser retries it once through a value-quoting fallback. That fallback double-quotes each `key: value` line whose value holds one of `` {}[]*&#!|>%@` `` or `: ` and does not both start and end with a quote. Only when the retry fails too does the parser log `Failed to parse YAML frontmatter` and return empty frontmatter. A non-mapping block also becomes `{}`. The round-4 and round-6 reviews traced those two paths to `{}`, and only the round-6 review traced the retry. A quoted `---` value therefore drops its block to `{}` only when the cut value holds none of the retry's characters, as test 6's cut value `"a ` does. A cut value such as `"a: ` is re-quoted, and its block loads as a mapping. A second, line-anchored regex in the same parser serves only the memory-rewrite path. It warns when the two closers disagree. *anchors: root.*
42. `[verified: grep -a of the installed Claude Code bundles by the round-5 /plan-review staff-platform-engineer reviewer and by the round-6 staff-platform-engineer and staff-sdet reviewers; the round-5 staff-sdet reviewer's read of 2.1.318 and 2.1.323; this plan's author read no bundle]` The installed versions are 2.1.318, 2.1.319, 2.1.321, 2.1.322, 2.1.323 and 2.1.325. Each holds row 41's guard and closer regex exactly once. The check is `grep -a -c -F 'indexOf("---",3)<0?null:e.match(' <bundle>`, which prints 1 for each. All six also define row 41's second, line-anchored regex once. The round-6 staff-platform-engineer's `grep -a -o -F` of its source and the round-6 staff-sdet's `grep -a -o -E` of its shape each find one match per bundle. *anchors: row41.*
43. `[unverified]` Every version from `plugin.json`'s 2.1.295 floor (`:3`) on, other than row 42's six, closes a block the way row 41 records. None from 2.1.295 to 2.1.317 is installed here. No version but 2.1.325 was traced for row 41's retry or its paths to `{}`. If a version closes line-anchored instead, the validator's verdict there differs from that version's load in two ways, as the round-6 staff-platform-engineer review reads them:
    - A quoted `---` value that version loads is denied, a false deny.
    - An unquoted `description: a --- b` followed by `context: fork` with no `background` passes, a missed deny. That version loads the whole block, while the validator cuts it at the `---` and never reads the later keys.

    No repo file has either shape (row 29). *anchors: row41.*
44. `[verified: the git status this revision was dispatched with, listing this plan as AM and the ten Critical files as M; the round-5 /plan-review staff-platform-engineer's git log of this plan file, which printed nothing, and its git show of the staged plan; the staged validate_skill_structure.py:73, test_skills.py:2764-2856 and CHANGELOG.md:9-14; require-code-review.sh:129-150; _lib.sh:1844-1863, :1892-1906; branch-management/SKILL.md "Plan files go on the implementation branch"]` The index holds an earlier implementation of this plan in all ten Critical files, built on the line-anchored closer `find("\n---", 3)`. It also holds an earlier revision of this plan file. No commit holds either. A plan-only commit would first need the staged implementation set aside. `require-code-review.sh` would still deny it without a `/code-review` marker, because the marker hashes the whole staged diff, plan files included. `branch-management` asks only that plan and code ship in one PR, so this plan goes into the code's one commit. *anchors: row1.*

## Critical files

- `plugins/skill-management/scripts/validate_skill_structure.py`: `FrontmatterError`, plus `parse_frontmatter`'s closer comment, its docstring and its unclosed and non-mapping checks, whose messages carry no path. `validate()`'s new arm, which prefixes the path, and its check-1 wording. `corpus_budget_violations`' comment.
- `plugins/skill-management/.claude-plugin/plugin.json`: `version` from `5.0.2` to `5.1.0`.
- `CHANGELOG.md`: one `[Unreleased]` entry.
- `claude-skills/skills/tests/test_skills.py`: the draft, plus the consumer edits, `_disables_model_invocation`, the shape-constant edits and the new test class. Reuses `parse_frontmatter`, `validate` and `corpus_budget_violations` (imported at `:83-88`), `_write_skill_file` (`:645`) and `_write_flagged_skills` (`:652`).
- `claude-skills/skills/tests/test_rules_frontmatter.py`: the one-line comment that replaces `:167-169`.
- `claude/.claude/hooks/tests/test_agent_roster.py`: the draft as written. Reuses `_declared_tool_names` (`:1031`).
- `claude/.claude/scripts/tests/test_transcript_period_cost.py`: the draft as written.
- `evals/test_review_bench_fixtures.py`: the draft as written. Reuses `arms._split_frontmatter` (`evals/review_bench/arms.py:62`).
- `claude/.claude/scripts/_ci-python-pin-lib.sh`: the draft's line 9 as written. This plan changes the draft's line 8 to `# Read with sed, not a YAML library, because install-dev.sh calls this before it checks or builds .venv, so no PyYAML can be assumed.` (row 10).
- `claude/.claude/scripts/transcript_analysis/subagent_mix.py`: the draft's one comment line as written.

Dispatch: one `code-writer` dispatch (row 15), given this plan's path, `.claude/plans/test-tree-yaml-reads.md` in the feature worktree. It brings the staged draft to this plan in place, with no second changelog entry and no second test class (row 44). It works on the parser and its test class first, then the consumer edits, then the version and changelog edits. It runs only the checks Verification assigns it and leaves its edits unstaged. The parent then stages, verifies and commits in the order Verification gives, and this plan and the code land as one commit (rows 31 and 44).

Projected diff: about 370 changed lines, about 30 under 400. The dispatching session counted the staged diff at 398. This revision removes about 31 added lines with the three dropped tests (the staged `test_skills.py:2826-2856`), about 3 with test 3's one-line expectation, and 2 with the changelog's closer bullet and its verdict-change sub-bullet. Restoring HEAD's wording in the two validator docstrings saves about 3 to 7 more. Test 6 adds about 13. Every other revised line keeps its count. Verification counts the staged diff and says what follows a count of 400 or more.

## Verification

The parent owns every numbered step (row 15). A value one Bash call prints, such as the merge-base SHA or a `mktemp` path, is spelled out literally in later calls, because the harness refuses a bare `$(git ...)` and a `$(...)` assignment used later in the same call (`docs/worktree-bash-guard.md` Triggers A and E). The order is:

- The `code-writer` brings the staged files to this plan (row 44). It runs `scripts/venv-run.sh pytest <file> -n0` on `test_skills.py` and `test_rules_frontmatter.py`, plus `scripts/venv-run.sh ruff check` on the files it changed, and leaves its edits unstaged.
- The parent stages every Critical file and this plan file in one `git add` call, made as its own Bash call, because `deny-invisible-commit-content.sh` denies a chained `git add`. From then on the index holds the finished edit and this plan's current revision. Steps 3 and 4 copy it, `/code-review` reviews it, and no numbered step stages.
- The parent reads the merge base with `git merge-base HEAD origin/main`, the base that `require-plugin-version-bump.sh` (`:101-132`) and `require-diff-size-prompt.sh` compare against. It then sums the first two columns of `git diff-index --cached --numstat -M <merge-base> -- ':(top,exclude).claude/plans/'`, the count `require-diff-size-prompt.sh` takes (`claude/.claude/hooks/_lib.sh:5137-5167`). That gate denies a commit at 400 or more changed lines. At 400 or more, now or after a fix round, the parent asks the engineer, and nothing is trimmed to fit.
- The parent runs step 1 itself with `run_in_background` and acts on its completion notice, because a scoped run that includes the hooks tree can outlast a foreground Bash call. Steps 2 to 5 write nothing in the feature tree, so they may run meanwhile. The parent may hand steps 3 and 4 to one `general-purpose` dispatch with `model: sonnet`. The commit sequence starts once steps 1 to 5 pass.

1. From the feature worktree, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. Expected: a scoped selection, not the full suite (row 23), that includes the hooks, skills and scripts test trees and the review-bench tests. Re-run each failing file alone with `scripts/venv-run.sh pytest <file> -n0`. Reproduce a failure at the merge base before calling it pre-existing (`CLAUDE.md` § "Engineering Judgment"), except for two local-only kinds this work does not cause:
   - five tests in `claude/.claude/scripts/tests/test_reconcile_project_plugins.py`, which need `timeout` on `PATH`;
   - load-flaky tests that pass at `-n0`.

   A reproduction worktree goes at the `base` subdirectory of a fresh `mktemp -d -t test-tree-yaml-reads` directory. `git worktree remove` on that path, with no `--force`, removes it when the reproduction ends, and a refusal goes to the engineer with the path.
2. Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/`.
3. Discovery equivalence (rows 8, 9, 30, 36 and 38), in copies under a temporary directory. Every pytest call in steps 3 and 4 follows three rules (rows 36 and 37):
   - It runs `<feature-tree>/scripts/venv-run.sh` by its absolute path.
   - It gives the copy's test file by its absolute path.
   - It leaves the cwd in the feature tree.

   The collect command is `<feature-tree>/scripts/venv-run.sh pytest --collect-only -q -n0 <tmp>/tree/claude-skills/skills/tests/test_skills.py`.
   - `mktemp -d -t test-tree-yaml-reads` prints the directory, written `<tmp>` here.
   - `git archive --format=tar --prefix=tree/ --output=<tmp>/base.tar HEAD`, then `tar -xf <tmp>/base.tar -C <tmp>`, writes `HEAD`'s files to `<tmp>/tree`. Run the collect command into `<tmp>/base-ids.txt`.
   - `mv <tmp>/tree <tmp>/base`, then `git checkout-index -a --prefix=<tmp>/tree/` writes the index's files to the same path. The trailing `/` is required. Run the collect command into `<tmp>/finished-ids.txt`.
   - `diff <tmp>/base-ids.txt <tmp>/finished-ids.txt`. Expected: apart from the closing count line, only `TestFrontmatterReaders` and `TestParseFrontmatterRejectsUnclosedAndNonMappingBlocks` IDs are added, and none are removed.
   - A pytest call in a copy that errors, or fails where this plan expects a pass, is re-run on the feature tree's own `claude-skills/skills/tests/test_skills.py`. If that run passes, the copy is at fault (row 39). The parent then stops steps 3 and 4, moves no check into the feature tree, and reports both outputs to the engineer. If it fails too, the staged edit is at fault and goes back to the `code-writer`.
4. Mutation checks (G1, G2, rows 5, 18, 27, 29, 30 and 35), in `<tmp>/tree`, the index copy step 3 leaves there. The test command is `<feature-tree>/scripts/venv-run.sh pytest <tmp>/tree/claude-skills/skills/tests/test_skills.py -k "TestFrontmatterReaders or TestParseFrontmatterRejectsUnclosedAndNonMappingBlocks" -n0`.
   - Run the test command unmutated first. It must pass, and step 3's last bullet covers a failure.
   - Make each edit below with `Edit` in the copy's file and run the test command. The first `Edit` in the copy probes whether the copy can be edited at all. A denied `Edit`, or one waiting on a prompt nobody can answer, stops step 4 and goes to the engineer. No mutation is then applied by another route, such as a shell `sed -i` or an edit in the feature tree.
   - A run catches its mutation only when all three hold:
     - pytest exits 1, its code for failed tests, not 2 for a collection error or any other code.
     - The `-ra` summary, which `pyproject.toml`'s `addopts` turns on, lists every test the edit names as `FAILED`, with no `ERROR` line.
     - Each failure comes from the mutated code: an assertion mismatch, `DID NOT RAISE`, or an exception the mutated code raises, such as `str.index`'s `ValueError` or a `FrontmatterError` escaping `validate()`. A `SyntaxError`, `ImportError` or `NameError` from the edit itself does not count.
   - A run that misses a condition because the edit itself is broken, such as a collection error or a `NameError`, is a bad edit, not a catch. Rewrite the file from the index and make the edit again.
   - A run in which a named test passes means a weak test, so stop and report it.
   - After every run, rewrite the edited file from the index with `git checkout-index -f --prefix=<tmp>/tree/ -- <file>`, run from the feature tree with `<file>` relative to it.
   - The edits, each followed by the tests it must fail:
     1. Delete the `isinstance` check: tests 1 and 4 in every `scalar-only-block` and `empty-sequence-block` case, and test 5.
     2. Replace the `find` call and its `-1` check with HEAD's `closing = content.index("---", 3)`: tests 1 and 4 and `test_skill_description_raises_for_a_malformed_skill`, each in its `unclosed-block` case.
     3. Append `or {}` to the `yaml.safe_load(...)` call: tests 1 and 4 in the `empty-sequence-block` case.
     4. Delete the `frontmatter is None` check: test 2.
     5. Change `"---"` to `"\n---"` in the `find` call, the line-anchored closer: tests 3 and 6.
     6. Delete `validate()`'s `FrontmatterError` arm: test 4 in every case.
     7. Make `FrontmatterError` subclass `Exception`: test 5.
     8. Make `_disables_model_invocation` return `bool(frontmatter.get("disable-model-invocation"))`: `test_discovery_survives_malformed_and_frontmatterless_skills`.
     9. Narrow `_discovery_frontmatter`'s catch to `(yaml.YAMLError, FrontmatterError)`: `test_discovery_frontmatter_returns_empty_mapping_for_unreadable_frontmatter` in the `impossible-date` case.
     10. Prefix both `FrontmatterError` messages in `parse_frontmatter` with `{skill_file}: `: test 4 in every case.
     11. Swap the two `FrontmatterError` message strings: test 4 in every case.
     12. Make the closer skip a `---` inside a quoted value, by adding `while closing != -1 and content.count('"', content.rfind("\n", 0, closing) + 1, closing) % 2: closing = content.find("---", closing + 3)` directly after the `find` call: test 6.
   - The temporary directory stays in place, since nothing refers to it, and a re-run makes a fresh one.
5. Plugin version and upstream overlap (rows 25, 26, 31 and 32).
   - `git fetch origin main` runs first, as its own call, so the `origin/main` reads below are current.
   - `git diff --cached <merge-base> -- plugins/skill-management/.claude-plugin/plugin.json` shows only the `version` line, from `5.0.2` to `5.1.0`.
   - `git show origin/main:plugins/skill-management/.claude-plugin/plugin.json` still reads `5.0.2`. If it does not, another PR bumped the plugin, and the parent asks the engineer, because the new version and the changelog text both change (row 32).
   - The overlap check: the old-side `@@` ranges of `git diff <merge-base> origin/main -- claude-skills/skills/tests/test_skills.py` sit outside those of `git diff --cached <merge-base> -- claude-skills/skills/tests/test_skills.py` (row 32). An overlap goes to the engineer, because a sync would then merge upstream edits into this PR's regions.
   - A sync with `origin/main` that moves the merge base, before or after the commit, runs the overlap check before it, against the merge base it leaves. After it, the sync repeats this whole step against the new merge base.

Then the commit sequence, all by the parent:

- `git diff --quiet` exits 0, and `git status --short` lists no path outside Critical files and this plan. `/code-review`'s reviewers read working-tree files and the commit takes the index, so the two must match. A nonzero `git diff --quiet` means an edit made after staging. The parent restages its own fix as a fix round, reports any other edit to the engineer, and discards nothing.
- `/code-review` runs on the staged diff and also runs `plugin-semver` on the two plugin files (`.claude/rules/review-pipeline-dispatch.md`). Each restage needs a fresh review (row 31).
- A fix, or a revision of this plan, is restaged in one call. Before the re-review the parent recounts the staged diff with the order list's `git diff-index` command, and re-runs steps 1 and 2 and this list's first check. Steps 3 and 4 re-run too, in a fresh temporary directory, when the fix touches the validator or `test_skills.py`.
- A clean review ends with a bare `git commit`, with no `-a` and no pathspec.
- This plan file goes into that commit with the code (row 44). Its lines add nothing to the diff-size count, whose command excludes `.claude/plans/`.
- The PR body carries `claude plugin install skill-management@claude-config --scope project` and, next to it, the rollback sentence.

## Out of scope

- The evals-harness readers and their docs, which this plan's Context assigns to PR 3:
  - `evals/run_skill_evals.py:197`'s `parse_skill_frontmatter`, with its stale CI docstring (row 13).
  - `evals/measure_subagent_model_resolution.py:306-311`, a copy of `subagent_mix`'s regex.
  - `evals/review_bench/arms.py:62-80`'s `_split_frontmatter` and `_parse_tools_field`.
- PR 3's own `skill-management` bump for its `skill-review/SKILL.md` command rewrites (`move-workflow-tests-and-use-yaml-parser.md:198-199`), which this PR's 5.1.0 cannot satisfy because `require-plugin-version-bump.sh` compares each branch's version with that branch's own merge-base.
- `validate()`'s traceback for an impossible date (G2). It is neither of the two shapes the engineer's "Fix parser now" covers, and the hook denies on that non-zero exit before and after this change.
- Naming `---` in check 1's not-strict-YAML hint. A quoted value holding `---` is denied with HEAD's hint to block-fold or double-quote a value containing `': '`. Neither fix helps, because the harness ends the block inside the quote (row 29). The hint predates this PR. Row 40's choice restores HEAD's deny for this shape unchanged.
- A new check that denies a mid-line `---` above the closing line, the ambiguity row 41's memory-rewrite regex warns about. An unquoted value cut there passes the validator, as it loads cut in the harness. That deny would move a verdict beyond row 40's "Match the harness". No repo file has the shape (row 29).
- Moving the four sibling splitters to the harness's closer, for the reasons row 29 gives.
- `corpus_budget_violations`' truthiness read of `disable-model-invocation` (`validate_skill_structure.py:157`), the production sibling of row 30's test reads. That read predates this PR, while row 30's test reads are a regression the draft introduces.
- Moving `SCRATCH_SECTION_AGENTS` (`test_agent_roster.py:1251-1255`) behind a function, which row 20 records instead.
- Making PyYAML importable for `subagent_mix.py` under the user's own `python3` (row 11). `./install.sh` is editable, so this is a declined change, not a given. Declined because it adds a runtime package for every stow consumer, which `CLAUDE.md` § Safety leaves to the engineer's explicit confirmation. The merged plan declined the same change as its D1 (`move-workflow-tests-and-use-yaml-parser.md:217`).
- A `subagent_mix` parity test against the real agent files, and the `select-tests.py` target it would need (row 12).
- A table test of `_disables_model_invocation`, declined in row 30.
- The plugin's `description` and `docs/skills.md:196`, which name two of the validator's checks and already omit its `context: fork` check. They claim no full list, so they stay as written.
- Text-boundary reads, which a parser does not replace because they ask where the frontmatter block ends, not what it holds:
  - `test_skills.py`'s `_project_layer_section` (`:2523-2531`), `_blank_frontmatter` (`:3340`) and `_strip_yaml_frontmatter` (`:8907`).
  - `claude/.claude/hooks/tests/test_global_claude_md_groups.py`'s `_rule_body` (`:388`).
  - `evals/test_review_bench_fixtures.py`'s `_frontmatter_lines_other_than_name_model_tools` (`:1382`), which compares raw lines to check byte preservation, something a parse would erase.
- Splitting `test_skills.py`, which the draft leaves at 10,156 lines and this plan grows by about 87. `docs/design-decisions/code-file-line-limit.md` sets a 1,000-line limit. Its "Timing" paragraph defers enforcement until an offender assessment exists. `TestFrontmatterReaders` tests helpers private to this file. The new class joins the validator's existing unit tests there. So neither class moves out.
