# Read the evals harness's frontmatter with a YAML parser

## Context

Goal: replace the three hand-rolled frontmatter reads under `evals/` with `yaml.safe_load`, make each one's behavior on degenerate input stated and tested, and point every documented evals command at `scripts/venv-run.sh python3`, which is the interpreter that has PyYAML.

Ask: the owner's review of merged PR #1463 asked that hand-rolled YAML parsing be replaced with a standard parser repo-wide, pre-existing sites included. The engineer selected the scope label "Evals harness + docs + plugin bump" and the option label "Fix defects, justify the rest (Recommended)". The second label's reading is the session's own interpretation, not the engineer's words: fix every site where PyYAML is importable, and keep a hand-rolled site only where PyYAML is unavailable or a parser round trip would rewrite bytes. This is the third of three PRs. The first two are merged, and the second left `plugins/skill-management` at 5.1.0.

Why now: the two earlier PRs covered the test trees and the structural validator. The evals harness is the remaining repo code that parses frontmatter by hand. Its `parse_skill_frontmatter` docstring claims CI installs only pytest and ruff, which `requirements-dev.txt` and `.github/workflows/tests.yml:119` contradict.

Evidence gathered this session, all on `origin/main` at `9ea0b251`:

- Three hand-rolled sites and no others:
  - `evals/run_skill_evals.py`: `parse_skill_frontmatter` (lines 197-241, reads `name` and `description`).
  - `evals/measure_subagent_model_resolution.py`: `_frontmatter_block`, `agent_frontmatter_model` and `agent_frontmatter_tools` (lines 301-360).
  - `evals/review_bench/arms.py`: `_split_frontmatter`, `_parse_tools_field` and `_replace_frontmatter_field` (lines 62-91).
  - Every other evals script reaches frontmatter only through `import run_skill_evals`.
- Today none of the three handles a leading U+FEFF, an unclosed block or a non-mapping block the way `parse_frontmatter` in `plugins/skill-management/scripts/validate_skill_structure.py` does. `arms.py` raises `ValueError` for no or unclosed frontmatter. The other two return a fallback silently.
- Tests exist for the happy paths only: `claude-skills/skills/tests/test_trigger_detector.py` (lines 203-229) for `parse_skill_frontmatter`, `evals/test_measure_subagent_model_resolution.py` (lines 1349-1359) and `evals/test_review_bench_fixtures.py` (lines 1288-1417) for the others. No test covers unclosed, BOM, CRLF or non-mapping input.
- `evals/` is not on the path of `validate_skill_structure` outside pytest, so a bare `python evals/run_skill_evals.py` cannot import it. The evals modules would call `yaml.safe_load` directly.
- Documented bare invocations to rewrite:
  - `CONTRIBUTING.md:57,60`.
  - `plugins/skill-management/skills/skill-review/SKILL.md:62`.
  - `docs/cost-levers-considered.md:774` (path-only, line-continued).
  - 28 lines in `evals/README.md` (43-64, 416-422, 454-458, 549-557, 713-743).
  - `docs/case-studies/plan-mode-model-resolution.md:50` is a dated case study, so it is a preserved record and is not edited.

Defaults the plan adopts, each open to the engineer's correction:

- Each function strips one leading U+FEFF, matching `parse_frontmatter`, so the three evals readers and the validator agree on the same file.
- Each function keeps its existing caller-facing fallback for unreadable frontmatter (`(dir name, "")`, `None`, `ValueError`) and states it in a one-line docstring.
- `arms.py` keeps `_replace_frontmatter_field` as a text edit, because a parser round trip would rewrite the other fields' bytes, and its tests already assert byte identity.

## Approach

Give `evals/run_skill_evals.py` one frontmatter reader. It removes one leading U+FEFF and closes the block at the first `---` after the opener, as the validator does. It parses the block with `yaml.safe_load` and raises `ValueError` for anything it cannot read as a mapping. All three evals sites go through it, each keeping its caller-facing fallback. The two agent-file sites also reject a file whose closer does not start a line, because nothing documents the agent loader's closer rule (row 39). `arms.py` keeps its line-level field edits but parses the frontmatter it renders before returning it. Every documented evals command becomes `scripts/venv-run.sh python3 evals/...`, and `skill-management` goes from 5.1.0 to 5.1.1 for its one edited `SKILL.md` line. The change ships as one PR (row 37).

**The reader**, added to `evals/run_skill_evals.py` beside `parse_skill_frontmatter`, with `import yaml` among the module's imports (row 40):

- `split_frontmatter(text: str) -> tuple[str, str]` removes one leading U+FEFF and requires the text to start with `---`. It finds the closer with `closing = text.find("---", 3)`, the validator's rule (row 11), and returns `(text[3:closing], text[closing + 3:])`. It raises `ValueError("frontmatter does not start with ---")` or `ValueError("frontmatter has no closing ---")`.
- `split_agent_frontmatter(text: str) -> tuple[str, str]` returns `split_frontmatter`'s result. It raises `ValueError("frontmatter closes at a --- that does not start a line")` when the block does not end with `"\n"` (row 39).
- `parse_frontmatter_block(block: str) -> dict` returns the `yaml.safe_load` result, or `{}` when that result is `None` (G1).
  - It raises `ValueError("frontmatter is not a YAML mapping")` for any other non-mapping.
  - It re-raises `yaml.YAMLError` as `ValueError(f"frontmatter is not valid YAML: {exc}")` with `from exc` (row 12).
- `declared_tool_names(tools_value: object) -> frozenset[str] | None` returns the stripped, non-empty names in a comma-separated string, or in a list whose items are all strings. It returns `None` for any other value, and when no name remains (row 13).

**Reader contract.** The four functions are tested once, in a new `TestFrontmatterReader` beside `parse_skill_frontmatter`'s tests in `test_trigger_detector.py`. Those tests pin these behaviors:

- The reader and the validator's `parse_frontmatter` give the same mapping, or both raise, on each of eleven synthetic files (row 41).
  - A mapping for a valid block, for the same block with one U+FEFF prepended, and for a block a mid-line `---` closes (row 11).
  - `{}` for a null block: empty, `null` or comment-only (G1).
  - A raise for an unclosed block, a list block, a scalar block, invalid YAML and an impossible date (G2).
- The four `ValueError` messages above for no opener, no closer, a non-mapping block and invalid YAML.
- `split_agent_frontmatter`'s rejection of a mid-line closer, and its pass-through of a line-start one.
- `declared_tool_names`'s forms above, including `None` for a blank string, `","`, `[]` and an absent field.

**Per-site contract.** "dir name" is the SKILL.md's parent directory name. A dash marks a field the site never reads: `arms.py` overwrites `name` and `model`, and never reads `description`. Each site's tests cover one input the reader rejects, plus the rows that are the site's own.

| Input | `parse_skill_frontmatter` | `agent_frontmatter_model` and `agent_frontmatter_tools` | `render_arm_agent` on a production file |
|---|---|---|---|
| Frontmatter the reader rejects | `(dir name, "")` | `None` | `ValueError` |
| A mid-line `---` in the frontmatter | the block closes there | `None` | `ValueError` |
| Quoted value, or a value then `# comment` | read as YAML reads it | read as YAML reads it | `tools` read as YAML reads it |
| `name` absent, null, blank or not a string | dir name | — | — |
| `description` absent or not a string | `""` | — | — |
| `model` absent, null, empty or not a string | — | `None` | — |
| `tools` absent, blank, `","` or `[]` | — | `None` | `ArmSnapshotError`, missing tools |

No reader sees a CRLF line end, because all three production reads go through `Path.read_text` (row 17).

A string `description` is collapsed with `" ".join(description.split())`, so a block scalar of either style stays one listing line (row 14).

**Site edits:**

- `parse_skill_frontmatter` reads with `read_text(encoding="utf-8")` before the `try`, so a decode error still raises (row 16). Inside `try ... except ValueError` it calls `split_frontmatter`, then `parse_frontmatter_block`. Its old docstring goes, and with it the stale claim that CI "installs only pytest and ruff".
- `measure_subagent_model_resolution.py`:
  - `_agent_frontmatter(text) -> dict | None` replaces `_frontmatter_block` and both `_AGENT_FRONTMATTER_*_RE` constants. It returns `parse_frontmatter_block(split_agent_frontmatter(text)[0])`, or `None` on `ValueError`.
  - `agent_frontmatter_model` returns `model` when it is a non-empty string, and `None` otherwise.
  - `agent_frontmatter_tools` returns `run_skill_evals.declared_tool_names(frontmatter.get("tools"))`.
  - The comment at `:302-304` goes, because the block scoping no longer mirrors `subagent_mix.py`.
  - The section rule at `:301` stays.
  - The module docstring's list of what it reuses from `run_skill_evals.py` (`:15-19`) gains the frontmatter reader.
- `review_bench/arms.py`, in call order:
  - `import run_skill_evals` joins the imports, as `review_bench/runner.py:42` already has it (row 40).
  - `_production_agent_text` reads with `encoding="utf-8"`.
  - `_split_frontmatter` returns `(block.strip("\n"), rest)` from `run_skill_evals.split_agent_frontmatter(text)`, so the reconstruction at `:143` stays byte-identical on every file it accepts (rows 11 and 39).
  - `_parse_tools_field` returns `declared_tool_names(parse_frontmatter_block(frontmatter).get("tools")) or frozenset()`.
  - `_replace_frontmatter_field` is unchanged (row 9).
  - A new `_check_rendered_frontmatter(frontmatter, lens)` runs after the three replacements (rows 10 and 33).
    - It parses the rendered block and checks that `name`, `model` and `tools` read back as `f"bench-{lens}"`, `ARM_MODEL_FRONTMATTER_VALUE` and `", ".join(ARM_TOOLS)`.
    - On a mismatch it raises `ArmSnapshotError(f"{lens}: rendered frontmatter does not read back as the arm's name, model and tools")`.
    - On a `ValueError` from the parse it raises `ArmSnapshotError(f"{lens}: rendered frontmatter does not read back: {exc}")` with `from exc` (row 42).
  - `ArmSnapshotError`'s docstring adds this third failure.

**Prescribed docstrings and comments**, durable one-line facts only. Code writes the character as its Python escape sequence and prose as "U+FEFF", never the raw character.

- `split_frontmatter`: "Return (block, rest) around a leading --- block, as validate_skill_structure.parse_frontmatter splits a SKILL.md.", "One leading U+FEFF is removed first.", "The block closes at the first --- after the opener, even mid-line." and "Raises ValueError when the text does not start with --- or no --- follows the opener."
- `split_agent_frontmatter`: "Return split_frontmatter's (block, rest) for an agent file." and "Raises ValueError when the closer does not start a line, since nothing documents whether Claude Code's agent loader closes the block mid-line."
- `parse_frontmatter_block`: "Return the block's YAML mapping, or {} when YAML reads the block as null." and "Raises ValueError for any other non-mapping and for invalid YAML."
- `declared_tool_names`: "Return the names in an agent's tools: value, given as a comma-separated string or a list of strings." and "Returns None for any other value, and when no name remains, so a blank value reads like an absent one."
- `parse_skill_frontmatter`: "Return (name, description) for the description-fidelity skill listing.", "Falls back to (directory name, "") when the frontmatter cannot be read as a mapping.", "An absent, blank or non-string name reads as the directory name, and an absent or non-string description as an empty string." and "The description is collapsed to one line, since the listing gives each skill one line."
- `_check_rendered_frontmatter`: "Raise ArmSnapshotError unless the rendered frontmatter parses and reads back as this arm's name, model and tools." Its call site carries the one-line comment "The field edits are line-level, so a multi-line, nested or repeated production field can render a value other than the one written."

**Docs.** Each documented evals invocation becomes `scripts/venv-run.sh python3 evals/<script>.py ...` (rows 19-21). The edits:

- 28 lines of `evals/README.md`.
  - Two of them, the `caffeinate -i python` lines at `evals/README.md:553,557`, become `caffeinate -i scripts/venv-run.sh python3`.
- `CONTRIBUTING.md:57,60`.
- `plugins/skill-management/skills/skill-review/SKILL.md:62`.
- The path-only recipe at `docs/cost-levers-considered.md:774` (row 23).
- One sentence added to `evals/README.md` § "Usage", before its first code block, the only place that says why (row 35).
  - Its text: "In every command in this file, `scripts/venv-run.sh python3` runs the contributor `.venv`, which carries the PyYAML these scripts import."
  - Its words "contributor `.venv`" link to `../README.md#tests`, the repository README's Tests section.
  - It names no script path, so Verification step 6's count stays 28.

`docs/case-studies/plan-mode-model-resolution.md:50` stays as it is.

**Plugin and CHANGELOG.** `plugin.json`'s `version` goes from 5.1.0 to 5.1.1 (row 24). One CHANGELOG entry goes first under `[Unreleased]`'s `### Changed`, shaped like the 5.1.0 entry (row 25):

- A bold lead: the evals harness reads SKILL.md and agent frontmatter with PyYAML, and the documented evals commands now run through `scripts/venv-run.sh python3`. It is followed by the sentence "Plugin bumped: `skill-management` 5.1.0 → 5.1.1."
- A quoted `description` no longer keeps its quotes in the description-fidelity listing (row 15).
- `measure_subagent_model_resolution.py` reads a quoted `model:` value and one followed by a comment.
- `snapshot-arms` fails when a lens's rendered frontmatter does not read back as the arm's name, model and tools, and when a lens's frontmatter closes at a `---` that does not start a line.
- `/skill-review`'s eval command is the plugin's only change, hence the patch bump.
- A **Migration:** line: in claude-config, run `claude plugin install skill-management@claude-config --scope project`, then `/reload-plugins`. A consuming repo can skip the refresh, because the edited command runs only in claude-config (row 24).

The entry names no `evals/<script>.py` after `python`, so Verification step 4 stays clean. The PR body also carries the install command, per `plugin-semver`.

**Ordering:**

- D1 runs and passes its own checks before D2 starts (row 28).
- The `SKILL.md` edit is final before `/skill-review` runs, and any later edit to it needs a fresh run (row 26).
- The `plugin.json` bump lands in the same commit as the `SKILL.md` edit, or an earlier one.

**Alternatives set aside:**

- Importing the validator's `parse_frontmatter` (row 5).
- A `yaml.safe_load` call at each site (row 6).
- A new `evals/` module (row 4).
- A lazy `import yaml` and a direct `.venv/bin/python3` (row 20).
- A function-local `import run_skill_evals` in `arms.py` (row 40).
- A `yaml.safe_dump` round trip in `arms.py` (row 9).
- Tests-only or string-only `tools:` checks in place of the readback (row 10).
- Letting a readback parse error surface as a bare `ValueError` (row 42).
- The line-start closer all three sites use today (row 11).
- A readback that re-splits the rendered text, or a golden-string test alone, in place of the agent-file closer check (row 39).
- A real-corpus test that the reader agrees with the validator on every listed SKILL.md (row 34).
- A code PR and a docs PR in place of one PR (row 37).
- New test files (row 29).

**Assumption ledger**

Root — the evals harness reads SKILL.md and agent frontmatter with three hand-rolled splitters, two regexes and a line scanner. They keep a quoted value's quotes, misread a value followed by a comment, and read a U+FEFF-prefixed file as having no frontmatter. Only their happy paths are tested. The docs launch the harness with a bare `python`, which is not known to carry PyYAML. The owner asked that a standard parser replace hand-rolled YAML parsing repo-wide. `[verified: this plan's Context and Ask line; run_skill_evals.py:197-241; measure_subagent_model_resolution.py:306-334; arms.py:62-80]`

Givens:

- **G1.** `yaml.safe_load` returns a block's top-level node whatever its type: `None` for an empty or comment-only block, a `str` for a bare scalar, a `list` for a sequence. The mapping check therefore belongs to the reader. Reason: PyYAML imposes it, and `validate_skill_structure.py:84-88` handles it the same way.
- **G2.** `yaml.safe_load` raises a bare `ValueError`, not a `yaml.YAMLError`, for a date-shaped value that is not a real date, such as `2024-02-30`. Reason: PyYAML imposes it, since `construct_yaml_timestamp` calls `datetime.date` unguarded (`.venv/lib/python3.14/site-packages/yaml/constructor.py:322-330` in the main checkout).
- **G3.** PyYAML keeps the last of two repeated mapping keys and raises nothing. Reason: PyYAML imposes it, since `construct_mapping` assigns each key in turn (`yaml/constructor.py:137-145` in the same `.venv`).
- **G4.** Only the contributor `.venv`, built from `requirements-dev.txt` (`:4` pins `pyyaml==6.*`), is known to carry PyYAML, and a contributor's bare `python` or `python3` may not. Reason: the machine's interpreter belongs to the contributor, and `claude/.claude/rules/python-environment-conventions.md` forbids installing into it.

Rows:

1. `[engineer-verified: "Evals harness + docs + plugin bump"]` The change covers the evals harness, its documented commands, and the `skill-management` plugin bump. *anchors: root.*
2. `[unverified]` "Fix defects, justify the rest (Recommended)" is read as follows: fix every site where PyYAML is importable, and keep a hand-rolled site only where PyYAML is unavailable or a parser round trip would rewrite bytes. That reading is the session's, not the engineer's. *anchors: root.*
3. `[verified: Grep of startswith("---"), find("\n---") and "frontmatter" over evals/ outside test_*.py; Grep of the reader names repo-wide]` The three sites are the only frontmatter reads in the evals sources. `parse_skill_frontmatter` has one caller (`run_skill_evals.py:259`). `agent_frontmatter_model` and `agent_frontmatter_tools` have no caller outside their module and its test. *anchors: root.*
4. `[verified: review_bench/runner.py:41-42; measure_subagent_model_resolution.py:52; select-tests.py:566, :619-627, :673-687, :924, :930, :1043-1060]` Mechanism: the shared reader lives in `run_skill_evals.py`. `measure_subagent_model_resolution.py` and `runner.py` already import that module. `select-tests.py` maps an edit to it to every evals test target and to the skills test tree. A new `evals/` module would match no `select-tests.py` rule, so every later edit to it would fall open to the full suite until that file gains a row, which this plan does not add. *anchors: root.*
5. `[verified: validate_skill_structure.py:58-89; run_skill_evals.py:54-55; pyproject.toml:18]` Importing `validate_skill_structure.parse_frontmatter` is set aside. It takes a `Path` and returns only the mapping, while the `measure_subagent_model_resolution.py` readers take text and `arms.py` needs the block text and the body. Outside pytest it would need a second `sys.path` insert, into a plugin's `scripts/` directory. That would tie a local harness to the commit gate's internals. *anchors: row4.*
6. `[verified: run_skill_evals.py:207-213; measure_subagent_model_resolution.py:310-316; arms.py:66-73]` A `yaml.safe_load` call at each site is set aside. The split, the U+FEFF removal and the null and non-mapping handling would then exist three times, and the three copies would have to agree by hand. CLAUDE.md's single-source-of-truth rule names no exception that fits. *anchors: row4.*
7. `[unverified]` Session default: each reader removes one leading U+FEFF. Its stated reason checks out. `validate_skill_structure.py:75-76` removes one, and `test_agent_roster.py:30` reads agent files through that function, so the repo's tests and the evals readers agree on both kinds of file. *anchors: row4.*
8. `[unverified]` Session default: each site keeps its caller-facing fallback for unreadable frontmatter. Those fallbacks are `(directory name, "")`, `None`, and `ValueError`. *anchors: root.*
9. `[unverified]` Session default: `arms.py` keeps `_replace_frontmatter_field` as a line edit. Its stated reason checks out. The real-file tests (`test_review_bench_fixtures.py:1382-1398`, `:1415-1417`) compare every other frontmatter line with production's, and a `yaml.safe_dump` round trip would reorder or requote those lines. *anchors: row10.*
10. `[verified: arms.py:11, :17-28, :77, :83-91, :146-151, :162-167; run_review_bench.py:1130; test_review_bench_fixtures.py:1388-1403; select-tests.py:981-986; G3]` Mechanism: `render_arm_agent` parses the frontmatter it renders.
    - The line edits rewrite only a field's first line, in its first occurrence. A list-form, repeated or multi-line plain-scalar production field can therefore render a value other than the one written.
    - Today's `tools:` regex rejects a list-form value only because it captures `- Read` as a tool name, and the parsed `_parse_tools_field` accepts it.
    - A repeated `tools:` or `model:` key passes today's checks. `_parse_tools_field` and the line edit take the first occurrence, while YAML keeps the last (G3).
    - A multi-line plain-scalar `tools:`, such as `Read, Grep, Glob,` continued by `Bash, Write`, passes today's checks too. The regex reads only its first line, and the line edit leaves the continuation line in place, where YAML folds it into the value.
    - Without the check, such a production file would render an arm whose `tools:` or `model:` is not the value `arms.py:17-28` sets.
    - Lighter check (a), set aside: the existing real-file tests already parse each lens's rendered frontmatter and run on every agent-file edit. `snapshot-arms` calls `write_arm_snapshot`, which renders the working tree's `claude/.claude/agents/`, and that tree can hold edits no CI run has seen.
    - Lighter check (b), set aside: letting `_parse_tools_field` accept only the string form catches a list-form `tools:` alone, misses a repeated `model:` key, and reports the list as a missing tool.
    *anchors: row9.*
11. `[verified: validate_skill_structure.py:79-84; arms.py:68-73, :140-143; test_review_bench_fixtures.py:1391-1398, :1409-1417; Grep of "^---$" and of a non-leading "---" over claude/.claude/agents/*.md and every SKILL.md]` The split takes the validator's closer, the first `---` after the opener, even mid-line. The validator's comment at `:79` names that rule as Claude Code's skill loader's.
    - The old and new rules differ only on a frontmatter line that holds a non-leading `---`.
    - On any other file, `closing` equals the old `end + 1`, so the body `text[closing + 3:]` equals the old `text[end + 4:]`.
    - The block gains one trailing `\n`, which `arms._split_frontmatter`'s `.strip("\n")` removes. The reconstruction at `arms.py:143` therefore yields the same bytes as before.
    - The byte-identity assertions at `test_review_bench_fixtures.py:1391-1398` and `:1409-1417` put production and the rendered arm through the same `_split_frontmatter`, so a change to the split rule cannot fail them. The golden assertion added to `TestRenderArmAgent` compares rendered bytes without that splitter.
    - Every agent file closes its frontmatter at line 7. The agents' one non-leading `---` is a body table rule at `code-writer.md:160`.
    - Every non-leading `---` in a SKILL.md sits below its file's closer, so no listing entry changes.
    *anchors: row4.*
12. `[verified: G2; test_skills.py:129-132]` `parse_frontmatter_block` turns `yaml.YAMLError` into `ValueError`, so every caller catches `ValueError` alone, and G2's date error needs no second clause. The test tree's `_discovery_frontmatter` catches the same two failures as two types. *anchors: row4.*
13. `[verified: test_agent_roster.py:1011-1024; measure_subagent_model_resolution.py:405-423; Grep of "^tools:" over claude/.claude/agents/]` `declared_tool_names` accepts the two forms the roster test's `_declared_tool_names` accepts, a comma-separated string and a list. All 14 `tools:` lines in `claude/.claude/agents/` use the string form.
    - It differs from that helper on a list holding a non-string item, which reads as `None`. The helper coerces each item with `str(t)`.
    - It drops empty names, which the helper keeps.
    - A result with no name left reads as `None`. An agent with no `tools:` value inherits every tool (`test_agent_roster.py:1016-1018`), and `classify_agent_identity` maps `None` to INCONCLUSIVE (`measure_subagent_model_resolution.py:418-419`).
    - An empty set would instead claim the agent declares no tools. Any observed Bash, WebFetch or WebSearch would then classify as SUBSTITUTED_TO_PLAN (`measure_subagent_model_resolution.py:420-422`).
    *anchors: row4.*
14. `[verified: run_skill_evals.py:233-238, :261; Grep of "^description:\s*[|>]" over every SKILL.md]` Collapsing whitespace gives the same one-line result as the old join. The hand-rolled reader joined block lines with single spaces, and `assemble_skill_listing` writes one line per skill. Every block-scalar description in the repo uses `>`. *anchors: row8.*
15. `[verified: Grep of "^(description|name): *[\"']" over every SKILL.md]` The listing text changes for the skills whose `description` is quoted: `code-review`, `period-cost-report`, `respond-pr`, `review-pr` and `root-cause-analysis`. Their entries lose the quotes. No SKILL.md quotes its `name`. *anchors: root.*
16. `[verified: validate_skill_structure.py:72-73; measure_subagent_model_resolution.py:339]` The two path-reading sites, `parse_skill_frontmatter` and `arms._production_agent_text`, read with `encoding="utf-8"`. The U+FEFF removal sees a single character only under UTF-8 decoding, and `measure_subagent_model_resolution.py` already passes that encoding. *anchors: row7.*
17. `[verified: run_skill_evals.py:205; measure_subagent_model_resolution.py:337-360; arms.py:105-106; pathlib/__init__.py:780-788 and _pyio.py:154-157 in the Python 3.14 stdlib the main checkout's .venv/pyvenv.cfg names]` All three production reads go through `Path.read_text` with no `newline` argument. That opens the file in universal-newlines mode, which turns `\r\n` and `\r` into `\n`. No reader sees a CRLF line end, so no test carries a CRLF case. *anchors: root.*
18. `[verified: Glob of evals/review_bench/conditions.json, results/* and arms/*/*.md, none found; evals/README.md:1012-1015; review_bench/runner.py:41-44]` `freeze` hashes the harness's import closure. Through `runner.py`, that closure holds `arms.py`, `run_skill_evals.py` and `measure_subagent_model_resolution.py`. No `conditions.json` is committed, so no committed campaign is invalidated. *anchors: row4.*
19. `[verified: Grep of "python3?[[:space:]]+(\./)?evals/" repo-wide; Grep of line-start "evals/<script>.py" over *.md; docs/case-studies/plan-mode-model-resolution.md:48]` The bare invocations are 28 lines of `evals/README.md`, `CONTRIBUTING.md:57,60` and `skill-review/SKILL.md:62`, plus the path-only recipe at `docs/cost-levers-considered.md:774`. Outside `.claude/plans/`, the one other is `docs/case-studies/plan-mode-model-resolution.md:50`. It sits under a dated "2026-08-14 follow-up" heading, so it is a preserved record. *anchors: root.*
20. `[verified: CLAUDE.md § "Commands"; scripts/venv-run.sh:22-52; README.md:541-546]` Mechanism: `scripts/venv-run.sh python3`, the form the repo's own documented commands use. It finds the main checkout's `.venv` from any linked worktree.
    - Lighter option (a), set aside: `.venv/bin/python3` fails in a linked worktree, which holds no `.venv`.
    - Lighter option (b), set aside: a lazy `import yaml` inside the readers would let a bare `python` run the modes that read no frontmatter. It would hide the dependency until a run reached its first frontmatter read, such as a description-fidelity run's listing.
    - The eager import's cost: every module that imports `run_skill_evals.py` needs PyYAML at import, including review-bench subcommands that never read frontmatter (row 40). Every documented command goes through `venv-run.sh`, so that cost reaches no documented use.
    *anchors: row19.*
21. `[verified: scripts/venv-run.sh:52; evals/README.md:553-564]` `caffeinate -i python` becomes `caffeinate -i scripts/venv-run.sh python3`. `venv-run.sh` ends in `exec`, so the process `caffeinate` waits on is still the Python run itself. *anchors: row20.*
22. `[verified: scripts/venv-run.sh:51-52; run_skill_evals.py:386; measure_subagent_model_resolution.py:79, :754; claude/.claude/hooks/_config.sh:98-101; Grep of python3 calls over claude/.claude/hooks/*.sh; Grep of "import yaml" over claude/.claude/ outside tests/, none found]` Running the harness through `venv-run.sh` changes only `PATH`, and through it the tools a harness-launched session resolves.
    - `venv-run.sh` prepends `.venv/bin` to `PATH` and changes nothing else.
    - The harness passes its own environment, `PATH` included, to each `claude -p` child. That child's hooks and tool calls therefore find `python3`, and any other tool `.venv/bin` holds, before the contributor's own copies.
    - No non-test module under `claude/.claude/` imports PyYAML, so its presence changes no hook.
    - The venv's Python version can differ from the contributor's own `python3`. The floor probe reads that version (`_config.sh:98-101`). Hooks that run a script with a bare `python3`, such as `require-worktree-for-git-writes.sh:295`, run on it.
    *anchors: row20.*
23. `[verified: docs/cost-levers-considered.md:771-778]` The `docs/cost-levers-considered.md` recipe says how to re-run the gate today. It describes current use rather than recording an event, so it is rewritten. *anchors: row19.*
24. `[verified: plugins/plugin-semver/skills/plugin-semver/SKILL.md:34-40; plugins/skill-management/.claude-plugin/plugin.json:4; .claude/rules/review-pipeline-dispatch.md; .claude/settings.json:3; .claude-plugin/marketplace.json:24]` `skill-management` takes a patch bump, 5.1.0 → 5.1.1.
    - Without the edit, the skill's own eval step would fail on an interpreter without PyYAML, so the edit is a backward-compatible fix.
    - `require-plugin-version-bump.sh` also needs the version strictly above the merge-base's 5.1.0.
    - The edited command runs only in claude-config, which enables the plugin at project scope (`.claude/settings.json:3`). The plugin ships only `plugins/skill-management/` (`marketplace.json:24`), so a consuming repo holds neither `evals/run_skill_evals.py` nor `scripts/venv-run.sh`, and refreshing the plugin there changes nothing it can run.
    *anchors: root.*
25. `[verified: CHANGELOG.md:9, :13]` The CHANGELOG entry follows the 5.1.0 entry's shape: a bold lead, a `Plugin bumped:` sentence naming the plugin and both versions, and a **Migration:** line. *anchors: row24.*
26. `[verified: .claude/rules/review-pipeline-dispatch.md; claude/.claude/CLAUDE.md, the Safety section's marker bullet]` `require-skill-review.sh` blocks the commit until `/skill-review`'s marker matches the reviewed `SKILL.md` state. *anchors: row24.*
27. `[verified: select-tests.py:531-540, :701-714, :921-1016, :1059-1060; test_skills.py:9191]` No `select-tests.py` rule matches `CONTRIBUTING.md`, so a diff that holds it falls open to the full suite. `test_skills.py:9191` reads that file by path, so the missing row is a gap that predates this change. *anchors: row19.*
28. `[verified: row27]` Dispatch split: D1 runs and verifies before D2 starts. The two share one worktree, so with D2's `CONTRIBUTING.md` edit present, D1's scoped `select-tests.py` run would fall open. *anchors: row27.*
29. `[verified: select-tests.py:87-88, :686]` New tests go in the existing test files. `select-tests.py` selects `measure_subagent_model_resolution.py`'s tests by exact path, so a new file would not run on an edit to that module. A new `evals/test_review_bench_*.py` file would be selected, but it would split `TestRenderArmAgent` with no seam beyond when its cases were written. *anchors: row30.*
30. `[verified: docs/design-decisions/code-file-line-limit.md:19, :50-57; Grep line counts this session; Grep for an enforcing test, none found]` Four touched files already exceed the 1,000-line limit: `run_skill_evals.py` (1,422), `measure_subagent_model_resolution.py` (1,085), `test_measure_subagent_model_resolution.py` (1,594) and `test_review_bench_fixtures.py` (1,430). No check enforces the limit yet. By this plan's estimate, `run_skill_evals.py` grows by about 30 lines net and `measure_subagent_model_resolution.py` shrinks by about 10. The two test files grow by about 35 and 60. *anchors: root.*
31. `[engineer-verified: "Yes, both"]` The engineer confirmed selecting both labels on the Ask line, "Evals harness + docs + plugin bump" and "Fix defects, justify the rest (Recommended)". Row 2's reading of the second label stays the session's. *anchors: root.*
32. `[engineer-verified: "Have the architects review those decisions"]` Asked to rule on three items beyond the Ask, the engineer handed the rulings to an architect review. Rows 33-35 record that review's rulings, which are the architect's and not the engineer's. *anchors: root.*
33. `[verified: row10]` Architect ruling under row 32: keep `_check_rendered_frontmatter`. The check's one test is parametrized over a list-form `tools:`, a repeated `model:` key and a multi-line plain-scalar `tools:`, plus a nested-mapping `model:` for its parse-failure branch (row 42). Its premise is row 10: two of the first three shapes pass today's checks, and `snapshot-arms` renders a working tree that can hold edits no CI run has seen. *anchors: row10.*
34. `[verified: row11; row41; validate_skill_structure.py:72-89]` Architect ruling under row 32: drop `listed_skill_md_paths()` and the real-corpus agreement test, and take the validator's closer instead. The reader then removes U+FEFF, checks the opener, finds the closer and calls `yaml.safe_load` in `parse_frontmatter`'s order. It is still a second implementation of that rule, not shared code (row 5), so a synthetic differential test holds the two together (row 41). *anchors: row11.*
35. `[verified: evals/README.md:39-43, :709-713; README.md:541]` Architect ruling under row 32: `evals/README.md` gains the one sentence in § "Usage", and the earlier draft's other two sentences are cut. The sentence opens "In every command in this file" because the review bench's own § "Usage" at `:709` also launches scripts. Its link targets the repository README's `## Tests` heading at `README.md:541`. *anchors: row20.*
36. `[engineer-verified: "Ask the architect"]` Asked to rule on the plan's size, the engineer handed the ruling to an architect consult. *anchors: root.*
37. `[unverified]` Architect ruling under row 36: the change ships as one PR, with its tests trimmed.
    - The reader is tested once, where it lives. Each site keeps one test of an input the reader rejects, plus tests of its own behavior: quoted and commented values, absent and non-string fields, the description collapse, the decode error, a quoted `tools:`, the agent-file closer check and the readback.
    - A split into a code PR and a docs PR is set aside for two stated reasons. Both halves share one rationale. A code PR merged first would break the documented bare-`python` commands for a contributor whose interpreter lacks PyYAML, because `import yaml` runs at module import (row 20).
    - The ruling holds at the projected diff in Critical files. The tests and the agent-file check rows 39-42 add leave both reasons for one PR unchanged.
    *anchors: root.*
38. `[unverified]` Claude Code's agent-file loader closes frontmatter at the first `---` after the opener, as its skill loader does. Nothing read here documents the agent-file rule, and the design does not depend on it: the agent-file split rejects every file on which that rule and the line-start rule differ (row 39). *anchors: row11.*
39. `[verified: arms.py:62-73, :122-143; test_review_bench_fixtures.py:892-899, :1391-1417; row 11]` Mechanism: `split_agent_frontmatter` rejects a file whose closer does not start a line, and both agent-file sites read through it.
    - A mid-line `---` in an agent-file value would close the block there. The rest of the frontmatter would become the body, and the reconstruction at `arms.py:143` would put a newline before that `---`. The rendered arm's body would then differ from production's.
    - The readback (row 10) catches this only when the `---` precedes `tools:`, and then as a misleading missing-tools error, since the tools check reads the truncated block. When the `---` follows `name:`, `model:` and `tools:`, every check passes.
    - Lighter check (a), set aside: a readback that re-splits the full rendered text. The reconstruction puts the `---` at a line start, so the re-split closes there and returns the same wrong body.
    - Lighter check (b), set aside: a golden-string test alone. It covers its own synthetic file, while `snapshot-arms` renders the working tree's agent files.
    - The check sits in `run_skill_evals.py` rather than in `arms._split_frontmatter` alone, because `measure_subagent_model_resolution.py` splits agent files the same way and would read a truncated block's values. Each site maps the `ValueError` to its existing fallback.
    - On every file the check accepts, the first `---` after the opener starts a line, so the line-start rule all three sites use today closes at the same place.
    - SKILL.md reads keep the mid-line rule, to agree with the validator (row 11).
    *anchors: row11.*
40. `[verified: run_skill_evals.py:54; defects.py:21; fixture_repo.py:30; mine_szz.py:17; mine_review_rounds.py:34-35; mine_pr_comments.py:27-28; replay_bench/comment_metrics.py:20; replay_bench/tasks.py:27; run_review_bench.py:2318-2320; select-tests.py:114-118, :924; test_review_bench_mining.py:26; test_review_bench_fixtures.py:21; test_replay_bench_tasks.py:16; test_replay_bench_comment_metrics.py:13; Grep of "__main__|argparse" over evals/replay_bench/, none found]` The top-level `import yaml` reaches every module that imports `run_skill_evals.py`, directly or transitively, and `arms.py`'s new import widens that set.
    - `defects.py:21` imports `arms`. Through it, `fixture_repo.py`, `mine_szz.py`, `mine_review_rounds.py`, `mine_pr_comments.py` and `replay_bench`'s `comment_metrics.py` gain PyYAML and `run_skill_evals.py:54`'s `sys.path` insert at import. `replay_bench/tasks.py` already reaches `run_skill_evals` through `review_bench.runner`.
    - The PyYAML requirement therefore covers the whole `review_bench` package except `identifiers.py` and `local_git.py`, and all of `replay_bench`.
    - No entry point gains it from the `arms.py` import. `run_review_bench.py`'s `main()` imports `review_bench.runner`, which imports `run_skill_evals`, before it parses any subcommand (`:2318-2320`). `replay_bench` has no entry point.
    - `select-tests.py` selects every evals test on an edit to `run_skill_evals.py` (`:114-118`, `:924`). Those tests import each module above at module level, under the venv.
    - A function-local `import run_skill_evals` in `arms.py` is set aside for row 20's reason against a lazy import: it would hide the dependency until a snapshot ran.
    *anchors: row4.*
41. `[verified: validate_skill_structure.py:54-89; pyproject.toml:18; select-tests.py:557-566, :930]` Mechanism: one parametrized differential test holds the reader to the validator's `parse_frontmatter`.
    - It runs both over the same synthetic files, listed in Critical files, and asserts each yields the expected mapping, or that both raise.
    - `pyproject.toml:18` puts `plugins/skill-management/scripts` on the pytest path, so the test imports the validator directly.
    - `select-tests.py:930` selects the skills test tree, which holds `test_trigger_detector.py`, on an edit to `run_skill_evals.py` or to a `.py` file under `plugins/skill-management/scripts/` (`:557-566`). Drift on either side therefore fails it.
    - A file with no opener is left out. The validator returns `{}` there (`validate_skill_structure.py:77-78`), while the reader raises and each site applies its own fallback.
    - It walks no corpus and needs no `listed_skill_md_paths()`, so it is not the test row 34 drops.
    *anchors: row5.*
42. `[verified: arms.py:56-59; run_review_bench.py:1125-1135, :2334-2339]` `_check_rendered_frontmatter` raises `ArmSnapshotError` for a readback parse failure as well as for a mismatch.
    - Both failures mean the line edits broke a production field, so they share the snapshot-check class. An unreadable production file stays a bare `ValueError`, as `_split_frontmatter`'s errors already are.
    - The message names the lens, which a bare `ValueError` from the reader would not. `snapshot-arms` catches neither class (`cmd_snapshot_arms`, and `main()`'s catch list), so the traceback is the operator's only report, and one `snapshot_arm` call renders seven lenses.
    - Letting the parse error surface as a bare `ValueError` was the lighter option, set aside for both reasons.
    - A production `model:` holding a nested mapping exercises the branch. The line edit leaves the nested line under `model: inherit`, and PyYAML rejects the result with "mapping values are not allowed here".
    *anchors: row10.*

## Critical files

**D1, code and tests:** one `code-writer` dispatch. Its helper and the helper's callers are sequenced, so it does not split.

- `evals/run_skill_evals.py`: the four reader functions, `import yaml`, and the rewritten `parse_skill_frontmatter`.
- `evals/measure_subagent_model_resolution.py`: `_agent_frontmatter` and the two rewritten readers. The two regex constants, `_frontmatter_block` and the comment at `:302-304` go. The docstring's reuse list gains the reader.
- `evals/review_bench/arms.py`: the edits listed under Site edits.
- `claude-skills/skills/tests/test_trigger_detector.py`, the test file for `run_skill_evals.py`:
  - Its imports gain `yaml`, the four reader functions, and `validate_skill_structure.parse_frontmatter` as `validator_parse_frontmatter` (row 41).
  - A module-level `_read_frontmatter(text)` returns `parse_frontmatter_block(split_frontmatter(text)[0])`.
  - A new `TestFrontmatterReader` holds the reader's only direct tests.
    - `test_reader_and_validator_read_the_same_frontmatter` is parametrized over `(text, expected)` (row 41).
      - `{"name": "x"}` from `"---\nname: x\n---\nbody\n"`, and from the same text with one U+FEFF prepended.
      - `{"name": "a"}` from `"---\nname: a---b\n---\n"`.
      - `{}` from `"---\n---\n"`, `"---\nnull\n---\n"` and `"---\n# c\n---\n"`.
      - `ValueError` from `"---\nname: x\n"`, `"---\n- a\n---\n"`, `"---\njust text\n---\n"`, `"---\ndescription: a: b\n---\n"` and `"---\ncreated: 2024-02-30\n---\n"`. PyYAML's scanner rejects `description: a: b` with "mapping values are not allowed here" (`yaml/scanner.py:545-579`).
      - The reader's outcome is `_read_frontmatter(text)`, or `ValueError` when it raises.
      - The validator's outcome is `validator_parse_frontmatter` on `tmp_path / "SKILL.md"`, written with `encoding="utf-8"`, or `ValueError` when it raises `ValueError` or `yaml.YAMLError`.
      - Both outcomes must equal `expected`.
    - `test_unreadable_frontmatter_names_the_failed_check` is parametrized over `(text, match)` and runs `_read_frontmatter` under `pytest.raises(ValueError, match=match)`.
      - `"body\n"` with "does not start with ---".
      - `"---\nname: x\n"` with "no closing ---".
      - `"---\n- a\n---\n"` with "not a YAML mapping".
      - `"---\ndescription: a: b\n---\n"` with "not valid YAML".
    - `test_an_agent_file_closer_must_start_a_line` expects `split_agent_frontmatter("---\nname: a---b\n---\n")` to raise `ValueError` matching "does not start a line". It expects `split_agent_frontmatter("---\nname: a\n---\nbody\n")` to return `("\nname: a\n", "\nbody\n")`.
    - `test_declared_tool_names` is parametrized over a value and its expected result.
      - `"Read, Grep"`, `["Read", "Grep"]` and `"Read, , Grep,"` expect `frozenset({"Read", "Grep"})`.
      - `""`, `","`, `[]`, `None`, `3` and `["Read", 3]` expect `None`.
  - `TestParseSkillFrontmatter`, whose docstring drops "minimal stdlib", keeps its three tests and gains five.
    - `test_unreadable_frontmatter_falls_back_to_dir_name`: a block that declares `name: declared-name` beside `description: a: b` reads as `(dir name, "")`.
    - `test_quoted_values_and_trailing_comments_are_read_as_yaml`: `name: "quoted-skill"  # note` and `description: 'Has: a colon.'` read as `("quoted-skill", "Has: a colon.")`.
    - `test_absent_null_blank_or_non_string_fields_fall_back` is parametrized over a block and its expected pair.
      - `description: x` alone reads as `(dir name, "x")`. So does `description: x` beside `name:`, beside `name: ""` and beside `name: 42`.
      - `name: x` alone reads as `("x", "")`, and so does `name: x` beside `description: 42`.
    - `test_literal_block_description_reads_as_one_line` expects `"Line one. Line two."` from a two-line `description: |` block.
    - `test_invalid_utf8_raises_unicode_decode_error` writes `b"---\nname: \xff\n---\n"` with `write_bytes` and expects `UnicodeDecodeError` (row 16).
- `evals/test_measure_subagent_model_resolution.py`, beside `:1349-1359`:
  - `test_frontmatter_model_regex_ignores_prose_mentions` is renamed `test_frontmatter_model_ignores_prose_mentions`.
  - `test_unreadable_frontmatter_reads_as_no_model_and_no_tools` is parametrized over two blocks that each hold `model: sonnet` and `tools: Read`. One adds `description: a: b`, and the other `description: a---b` (row 39). Each gives `None` from both readers.
  - `test_model_is_read_as_yaml`, parametrized over `model: "sonnet"` and `model: sonnet  # pinned`. Each case expects `"sonnet"`.
  - `test_absent_null_empty_or_non_string_model_reads_as_none` is parametrized over a block with no `model:` line, `model:`, `model: ""`, `model: 3` and `model: [a]`.
- `evals/test_review_bench_fixtures.py`, in `TestRenderArmAgent`, with every case rendered for `ARM_CURRENT_RULE`:
  - `_write_production_agent` gains `model: str = "sonnet"`, written where it now writes the fixed `sonnet`.
  - `test_current_rule_arm_differs_from_production_only_in_name_model_and_tools` gains a golden assertion (row 11). `rendered` equals the production text with its `name:`, `model:` and `tools:` lines replaced literally by `name: bench-staff-backend-engineer`, `model: inherit` and `tools: Read, Grep, Glob`.
  - `test_invalid_yaml_production_frontmatter_raises_value_error` uses `extra_frontmatter="description: a: b\n"`. It expects `ValueError` matching "not valid YAML", and asserts the exception is not an `ArmSnapshotError`.
  - `test_a_quoted_production_tools_value_is_read` uses `tools='"Read, Grep, Glob, Bash, Write"'`. It asserts that the rendered frontmatter's `tools` parses as `"Read, Grep, Glob"`.
  - `test_a_mid_line_triple_dash_in_production_frontmatter_raises_value_error` uses `extra_frontmatter="description: a---b\n"`, which puts the `---` after `name:`, `model:` and `tools:`. It expects `ValueError` matching "does not start a line" (row 39).
  - `test_fails_loudly_when_the_rendered_frontmatter_does_not_read_back` is parametrized over four production shapes. Each expects `ArmSnapshotError` matching "rendered frontmatter does not read back" (row 33).
    - A list-form `tools:`: `tools=""`, with `extra_frontmatter` holding one `  - <tool>` line for each of Read, Grep, Glob, Bash and Write.
    - A repeated `model:` key, `extra_frontmatter="model: opus\n"` (G3).
    - A multi-line plain-scalar `tools:`, `tools="Read, Grep, Glob,\n  Bash, Write"`.
    - A nested-mapping `model:`, `model="\n  a: b"` (row 42).

Against today's code, the `TestFrontmatterReader` tests cannot import their functions. These new site cases pass today, so only Verification step 3's mutations hold them:

- The literal-block description and the invalid-UTF-8 read.
- `name` absent or null, and `description` absent.
- `model` absent or null.
- The golden assertion.

Every other new site case fails today. The list-form readback case fails today only through its `match=`, because today's code raises a missing-tools `ArmSnapshotError` for it.

D1 reuses these:

- The `import run_skill_evals` that `review_bench/runner.py:42` already has.
- `_write_production_agent` (`test_review_bench_fixtures.py:892-899`) for every new `TestRenderArmAgent` case.
- `TestParseSkillFrontmatter._write_skill` for the skill cases.

D1 is verified by `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, `scripts/venv-run.sh ruff check evals/ claude-skills/skills/tests/test_trigger_detector.py`, and Verification steps 3 and 9.

**D2, docs, plugin and CHANGELOG:** one `code-writer` dispatch, run after D1 returns.

- `evals/README.md`: 28 rewritten lines and one added sentence in § "Usage".
- `CONTRIBUTING.md`: lines 57 and 60.
- `docs/cost-levers-considered.md`: line 774.
- `plugins/skill-management/skills/skill-review/SKILL.md`: line 62.
- `plugins/skill-management/.claude-plugin/plugin.json`: `version` 5.1.0 → 5.1.1.
- `CHANGELOG.md`: the new entry.

D2 is verified by Verification steps 4-7. Then, in order:

1. The session runs `/code-review`, which dispatches `/skill-review` and `plugin-semver`.
2. Verification steps 1, 2, 8 and 10 run.
3. The session commits.

Projected diff: about 530 changed lines across the twelve files above. About 230 are in the three code files, 225 in the three test files and 75 in the six docs and plugin files. The 28 rewritten `evals/README.md` lines account for 56 of the 75.

## Verification

Run every step from the worktree root.

1. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, apart from failures that also occur at the merge-base.
   - On the final diff, it falls open to the full suite and names `CONTRIBUTING.md` as the unmatched path (row 27).
   - Its evals tests import every module row 40 names under the venv, so this step is also their import check.
2. `scripts/venv-run.sh ruff check evals/ claude-skills/skills/tests/test_trigger_detector.py` is clean.
3. Mutations, each applied in D1 and then restored. Each fails at least the named test:
   - Deleting the U+FEFF removal in `split_frontmatter` fails the differential test's U+FEFF case.
   - Changing the closer to `text.find("\n---", 3)` fails the differential test's mid-line case.
   - Deleting the line-start check in `split_agent_frontmatter` fails `test_an_agent_file_closer_must_start_a_line`.
   - Deleting the `_check_rendered_frontmatter` call fails all four readback cases.
   - Deleting its re-raise of a parse `ValueError` fails the nested-mapping `model:` case.
   - Replacing the description collapse with `.strip()` fails `test_literal_block_description_reads_as_one_line`.
   - Moving the `read_text` call in `parse_skill_frontmatter` inside the `try` fails `test_invalid_utf8_raises_unicode_decode_error`.
   - Reading `frontmatter["name"]` in place of `frontmatter.get("name")` fails the case with `description: x` alone.
   - Reading `frontmatter["model"]` in place of `frontmatter.get("model")` fails the case with no `model:` line.
4. `git grep -nE 'python3?[[:space:]]+(\./)?evals/(run_skill_evals|measure_subagent_model_resolution|measure_explore_recall|measure_main_session_rule_adherence|run_review_bench)\.py' -- . ':!.claude/plans' ':!docs/case-studies/plan-mode-model-resolution.md' | grep -v 'scripts/venv-run\.sh python3 evals/'` prints nothing.
5. `git grep -nE '^[[:space:]]*(caffeinate -i[[:space:]]+)?(\./)?evals/(run_skill_evals|measure_subagent_model_resolution|measure_explore_recall|measure_main_session_rule_adherence|run_review_bench)\.py' -- '*.md' ':!.claude/plans'` prints nothing. That covers path-only invocations of all five scripts. It scans only Markdown because two Python docstrings, `evals/review_bench/analysis.py:9` and `claude-skills/skills/tests/test_skills.py:3056`, open a prose line with a script path.
6. `git grep -cE 'scripts/venv-run\.sh python3 evals/(run_skill_evals|measure_subagent_model_resolution|measure_explore_recall|measure_main_session_rule_adherence|run_review_bench)\.py' -- evals/README.md CONTRIBUTING.md docs/cost-levers-considered.md plugins/skill-management/skills/skill-review/SKILL.md` prints these four counts, in path order:
   - `CONTRIBUTING.md:2`
   - `docs/cost-levers-considered.md:1`
   - `evals/README.md:28`
   - `plugins/skill-management/skills/skill-review/SKILL.md:1`

   `git grep -c 'caffeinate -i scripts/venv-run.sh python3' -- evals/README.md` prints `evals/README.md:2`.
7. `git grep -n 'installs only pytest and ruff' -- . ':!.claude/plans'` prints nothing. `git grep -n 'python evals/measure_subagent_model_resolution.py' -- docs/case-studies/plan-mode-model-resolution.md` still prints line 50.
8. `git grep -nF -e 'find("---", 3)' -e 'startswith("---")' -- 'evals/*.py' ':!evals/test_*.py'` prints two lines, both inside `split_frontmatter` in `evals/run_skill_evals.py`. `git grep -n '_AGENT_FRONTMATTER_' -- evals/` prints nothing.
9. Right after D1, each of these exits 0 without launching a `claude` session:
   - `scripts/venv-run.sh python3 evals/run_skill_evals.py --help`
   - `scripts/venv-run.sh python3 evals/run_review_bench.py --help`
   - `scripts/venv-run.sh python3 evals/measure_subagent_model_resolution.py --list`
   - `scripts/venv-run.sh python3 evals/measure_explore_recall.py --help`
   - `scripts/venv-run.sh python3 evals/measure_main_session_rule_adherence.py --list`
10. `git diff --name-only --merge-base origin/main` lists exactly the twelve Critical files, plus this plan file once it is committed. `git diff --merge-base origin/main -- plugins/skill-management` changes only `SKILL.md:62` and the `version` line of `plugin.json`.

## Out of scope

- `claude/.claude/scripts/transcript_analysis/subagent_mix.py:374-394` has the same quoted-value and trailing-comment misses.
  - It stays a regex because it runs on stow consumers' system `python3`, which carries no PyYAML, as its own comment at `:374` says.
  - It also lies outside the scope label.
  - After this change, it and `measure_subagent_model_resolution.py` can disagree on a quoted or commented `model:`.
- `_replace_frontmatter_field` still cannot rewrite a multi-line, nested, list-form or repeated value. The readback check makes such a production file fail loudly instead (rows 10 and 42).
- An unreadable SKILL.md or agent file still reads as its site's fallback with no message, and the fallback now also covers invalid YAML, which the hand-rolled readers read past. Round-1 review asked for a stderr line or a run-record field. The session declined it, for the engineer to raise:
  - It adds behavior the Ask does not call for.
  - The old readers also fell back silently.
  - The structural validator guards committed SKILL.md files, and `test_agent_roster.py:484-494` fails on any agent file the validator's `parse_frontmatter` cannot read.
- `yaml.safe_load` raises `RecursionError`, which no site catches, on deeply nested flow collections. Every input is a file in this repository's own tree, outside any declared untrusted-input surface, so the session declined handling it.
- Pointing `skill-review/SKILL.md:62` at `evals/README.md` § "Usage" instead of carrying the command. A skill body is instructional prose that must stand alone, CLAUDE.md's named exception to single source of truth.
- A `select-tests.py` row for `CONTRIBUTING.md`, which `test_skills.py:9191` reads (row 27), is not added. `select-tests.py` is itself a global full-suite trigger (`:531-534`) and lies outside this change. Raise the gap to the reviewer.
- Splitting the four touched files that exceed the 1,000-line limit (row 30).
- `docs/case-studies/plan-mode-model-resolution.md:50`, past CHANGELOG entries and `docs/reports/` are preserved records.
- The test tree's `_strip_yaml_frontmatter` (`test_skills.py:9161-9171`) strips a delimited block without reading YAML. It lies outside the scope label.
