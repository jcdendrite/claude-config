# Decompose cursor-adapter.py into files under the code-file limit

## Context

Goal: split `claude/.claude/scripts/cursor-adapter/cursor-adapter.py` (3,392 lines) into files of at most 1,000 lines each, with no behavior change, before Phase 5a of `.claude/plans/cursor-adapter.md` adds code to it.

Ask: "#3 first. Then #1 and the rest" — the engineer's reply to the session's numbered list of next steps, whose item 1 read "Decompose `cursor-adapter.py`. I recommend this one. It's 3,400 lines and has to land before Phase 5a." The session's list, not the engineer, supplied that wording, and "#3 first" (messaging the peer session) is done. On how the split files load, the engineer answered: "Hmm, not sure, can you let the planning process decide, with the architect and alternatives considered?" On sequencing against the peer session's `test_cursor_adapter_install.py` split, the engineer selected the label "Mine first (Recommended)".

Why now: the repo's code-file limit is 1,000 lines for production and test files alike (`docs/design-decisions/code-file-line-limit.md`). The file is 3.4 times that, and Phase 5a (`.claude/plans/cursor-adapter.md`, Phases section) adds the active-subagent set, `check` state removal, the workspace `sandbox.json` report and the sandbox grant's first install, all in this file. Splitting afterward would move code a Phase 5a review has just read.

Constraints the file's consumers impose, found by two read-only explorations this session:
- The adapter runs as `python3 -I -S <stow path>/cursor-adapter.py` (the generated sessionStart entry and the `self-check` subprocess), so the script's directory is not on `sys.path` and a plain `import` of a sibling fails unless the entry puts the directory there itself. The filename has a hyphen. `hook-shim.py`, the one existing sibling, is loaded by reading its source and executing it into a fresh `types.ModuleType` (`compile_module`, `load_shim`).
- Tests load the adapter the same way (`load_adapter` in `claude/.claude/hooks/tests/_cursor_payloads.py`), once per test, and read about 100 names off that one module. `test_cursor_adapter_install.py` also copies the whole directory and overwrites one sibling to test load failures (10 call sites).
- Tests monkeypatch about 22 distinct names on the adapter module. A patch reaches only callers that look the name up in the patched module's namespace, so a name that moves to another file silently stops being patched for its callers there.
- `hook-shim.py` and the adapter share parity-tested names (`STATE_DIR_NAME`, `LOG_FILE_NAME`, `FIRST_CALL_DIR_NAME`, `PARITY_EXCEPTIONS_FILE_NAME`, `is_valid_tool_path`, the floors), and the shim is itself 1,897 lines.
- A new file under `claude/.claude/scripts/cursor-adapter/` goes live on `git pull` on this machine, where `~/.claude/scripts` is a directory link into the checkout. `select-tests.py` already maps any path under that directory to the adapter's tests.

Sequencing: this PR lands before the peer session's `test_cursor_adapter_install.py` split starts its build, so that session syncs to a tree with the new file names. The peer session wants this plan's class-to-file map.

## Approach

Split `cursor-adapter.py` at its own section markers into seven part files beside it, moving every line verbatim. `cursor-adapter.py` stays the entry: its path, name, `-I -S` invocation, docstring, imports and command-line code stay, and it executes each part's source into its own module namespace in file order. The loaded module then holds exactly today's names plus two loader names, so every existing test, name read and monkeypatch works unchanged, and no existing test file is edited. One new test restores the undefined-name and unused-import checks that ruff can no longer make file by file.

**Loading mechanism** (the engineer left this to the plan, row 2). The entry executes every part into one shared namespace (M1). Set aside:
- Separate modules loaded by path, with cross-file calls through the owning module's attribute, the `transcript_analysis` discipline. A monkeypatch reaches only callers that resolve the name in the patched module (row 7). Every patched name would need retargeting to its owning file, and most patches sit in `test_cursor_adapter_install.py`, the file the peer session splits next. Every cross-file call in the adapter would also become an attribute read. A missed retarget passes silently, and the diff stops being a move.
- A real `import` after the entry puts its own directory on `sys.path`. It undoes what `-I` keeps out (row 9). `sys.modules` caching then breaks the per-test fresh load and the loads from a copied directory (row 6) unless every loader evicts the parts. It also needs `sys.dont_write_bytecode` to keep a `__pycache__/` out of the checkout, and it carries the same patch retargeting as the option above.
- Keeping one file under a structural exception to the limit. The Ask picks decomposition (row 1).

Executing a file's source into a namespace is no heavier than the import it stands in for. The source is the module's own code, read from the entry's own directory, and it is already how this directory loads code, at five sites (row 8). The two alternatives that look lighter, imports and path-loaded modules, fail as stated above.

**The parts, and the class-to-file map for the peer session** (M2, M8). Each part is a run of whole sections in file order, cut verbatim from the base's line ranges below (base 33589d60). Every name stays readable and patchable as `adapter.<name>` or `world.adapter.<name>`, whichever file defines it. A test that moves between test files therefore needs no target change. Paths are under `claude/.claude/scripts/cursor-adapter/`.
- `cursor-adapter.py`, the entry, about 133 lines: :1-34 (docstring and imports), the loader below, then :3325-3392 (the `generate` and `command line` sections: `run_generate`, `build_parser`, `main` and the `__main__` block). It also holds `adapter_directory` (moved from :363-364), `ADAPTER_PART_FILE_NAMES` and `execute_adapter_parts`.
- `cursor-adapter-core.py`, 529 lines: :35-565 less :363-366. Sections: constants; paths and loading; files. Classes `AdapterError`, `PartialWriteError`, `DurabilityUnconfirmed`, `ProbeStalled`, `Unconfirmed`. It holds the whole constants section, `CURSOR_TOOL_NAMES`, the floors and the parity-tested file names included, plus `compile_module`, `load_shim`, `flush_to_disk`, `fsync_directory`, `crash_hook`, `checkpoint`, `durable_write` and `settings_json_path`.
- `cursor-adapter-hooks-json.py`, 703 lines: :568-1268. Sections: registrations and generation; comparing and printing hooks.json entries. Classes `Registration`, `EmittedEntry`, `ParsedCommand`, `HooksReport`. It holds `render_command`, `parse_command`, `generate_entries`, `classify_hooks`, `refuse_unsafe_targets` and `MISSING`.
- `cursor-adapter-tools.py`, 526 lines: :1271-1794. Sections: probes and tools; the self-check subcommand and its parent-side reading; the install record; discovering the tools. Class `SelfCheckResult`. It holds `find_tool`, `run_self_check`, `read_record`, `record_or_none`, `state_directory` and `discover_tools`.
- `cursor-adapter-install.py`, 255 lines: :1797-2049. Section: install. It holds `load_shim_or_refuse`, `run_install` and `write_generated_files`.
- `cursor-adapter-sandbox.py`, 291 lines: :2052-2340. Section: the sandbox grant. Class `SandboxPlan`. It holds `grant_exceptions_path`, `plan_sandbox_grant` and `apply_sandbox_action`.
- `cursor-adapter-uninstall.py`, 354 lines: :2343-2694. Section: uninstall. It holds `entries_for_uninstall`, `run_uninstall` and `remove_runtime_state`.
- `cursor-adapter-check.py`, 628 lines: :2697-3322. Sections: logging, rotation and the first-call marks; check and status. Classes `LogRead`, `TamperComparison`. It holds `rotate_log`, `read_log`, `compare_install`, `settings_link_findings`, `tool_findings`, `run_check`, `hook_env_conversation_id`, `run_hook_env_check` and `run_status`.

This map lives in this plan for the peer session. The part order's durable home is `ADAPTER_PART_FILE_NAMES`.

Each part follows these format rules:
- Line 1 is the header `# cursor-adapter.py executes this file into its own namespace, so names its other parts define need no import here.`, and line 2 is blank.
- The part ends at its last statement with one newline, so the two blank lines that separated it from the next section drop.
- The header is a comment, not a docstring, so no part rebinds the module's `__doc__` (row 24).
- The part carries no shebang, no import and no executable bit.

**The entry's loader** (M3), after the imports under a new `# --- the parts ---` marker:

```python
# Each part executes into this module's namespace, so every name, and a test's patch of one, resolves as in one file.
# The order is file order: a part's top-level statements read only names the entry or an earlier part defines.
ADAPTER_PART_FILE_NAMES = (
    "cursor-adapter-core.py",
    "cursor-adapter-hooks-json.py",
    "cursor-adapter-tools.py",
    "cursor-adapter-install.py",
    "cursor-adapter-sandbox.py",
    "cursor-adapter-uninstall.py",
    "cursor-adapter-check.py",
)


def adapter_directory():
    return os.path.dirname(os.path.realpath(__file__))


def execute_adapter_parts():
    for part_file_name in ADAPTER_PART_FILE_NAMES:
        part_path = os.path.join(adapter_directory(), part_file_name)
        with open(part_path, "rb") as part_file:
            source = part_file.read()
        exec(compile(source, part_path, "exec"), globals())  # the parts are this module's own code


execute_adapter_parts()
```

The loader reads with plain `open()`. `read_regular_bytes` (:400) is defined in a part, so it cannot read the first one. The interpreter itself reads the entry with no such guard (row 23). `adapter_directory()` moves verbatim into the entry rather than being restated, so one definition locates the shim, the JSON files and the parts. It resolves a stow link into the checkout. A stow layout that links the adapter's files one by one therefore finds parts that `git pull` added, with no re-stow (row 10).

**Lint** (M4). One `pyproject.toml` per-file-ignores row, `"claude/.claude/scripts/cursor-adapter/cursor-adapter*.py" = ["F401", "F821"]`, covers the entry and the seven parts and leaves `hook-shim.py` fully checked. A comment of at most two lines above the row names the reason and the replacement: the files share one namespace that per-file analysis cannot follow, and `test_cursor_adapter_parts.py` runs the same checks over the files as one source, so `ruff check` alone no longer proves that names resolve. Set aside:
- A `# ruff: noqa: F821` line in each file. It states one reason eight times.
- An import block in each part. It keeps F401 per file, but adds non-move lines to every part and still needs F821 off.
- Dropping the checks. A mistyped name in an untested branch would then ship.
- A hand-written scan on the standard library's `symtable`. It rebuilds part of pyflakes, carries Python 3.14 edge cases (an inlined module-level comprehension's iteration variable, annotation scopes, a `global` that is only assigned), and cannot see a name defined twice across files.

**`claude/.claude/scripts/tests/test_cursor_adapter_parts.py`** (new; M4) imports `ADAPTER_DIR`, `ADAPTER_SOURCE`, `SHIM_INTERPRETER_FLAGS` and `copy_adapter` from `hooks.tests._cursor_payloads`, the way `test_cursor_adapter_generate.py:18-33` does. It tests:
- The part list: the `cursor-adapter-*.py` file names in `ADAPTER_DIR` equal `ADAPTER_PART_FILE_NAMES` as a set, and the tuple holds no duplicate.
- Names across the files: the test writes one temporary source, made of the entry up to its `execute_adapter_parts()` call, then each part in tuple order, then the rest of the entry. It runs the repo's pinned ruff over that file as `ruff check --isolated --no-cache --select F821,F401,F811` and expects no finding. F821 covers a name no file defines, F401 an import no file reads, and F811 a name defined twice, in one part or across two. The test fails, not skips, when ruff is absent.
- Seeded negatives: the same run over a copy with one seeded unbound name, one seeded unused import and one seeded duplicate definition in a second part reports each of the three.
- A faulted part: in a copy of the adapter directory made by `copy_adapter`, for each of a missing, an unreadable and a syntax-broken part, `check --hook-env` under `[sys.executable, *SHIM_INTERPRETER_FLAGS]` exits nonzero with empty stdout. The generated launch guard then prints the informational `{}`. The unreadable case skips under root, as the existing unreadable-entry case does.
- A link to the entry alone: a temporary directory holds only a symlink named `cursor-adapter.py` to `ADAPTER_SOURCE`. Run as `[sys.executable, *SHIM_INTERPRETER_FLAGS, <link>, "--help"]`, it exits 0 and prints a line starting `usage: cursor-adapter.py`. This pins row 10 under the installed entry's flags.

The import of `_cursor_payloads` needs one entry in `select-tests.py`'s `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, which `test_select_tests.py:2024-2031` enforces (row 13).

**Docs** (M6):
- `docs/scripts.md:490`: after the bullet's first sentence, add "Its code sits in the `cursor-adapter-*.py` files beside it, which it executes into its own namespace in the order its `ADAPTER_PART_FILE_NAMES` lists, so none runs or is imported alone." The rest of the bullet stays true.
- `docs/cursor-adapter.md:270`: "the `CURSOR_TOOL_NAMES` table in `cursor-adapter.py`" becomes "the `CURSOR_TOOL_NAMES` table in `cursor-adapter-core.py`".
- `docs/cursor-adapter.md:111` and `:155`: where each says an older interpreter cannot parse `cursor-adapter.py`, it gains "or one of its parts".
- The PR body tells Phase 5a's session that its Critical files bullets in `.claude/plans/cursor-adapter.md` still name `cursor-adapter.py` for code that now lives in the parts, and that it retargets them before it starts. That plan stays unedited here (Out of scope).
- No other mention changes. Each names the command or the program, which keeps the name `cursor-adapter.py` (row 17). `.claude/rules/cursor-adapter-parity.md` already globs the directory (row 18).

**What a user can see change.** Only diagnostics, and one new fault state. Generated commands, the install record, entry ownership and every report stay as they are (G1).
- `main()`'s unexpected-failure line names the file and line of the failing frame, so a failure inside a part names that part, and line numbers move as under any split (row 16).
- On an interpreter older than the floor, the entry parses and the SyntaxError surfaces from the loader's `exec(compile(...))` line and names the part that holds the unparsable syntax, where today it names `cursor-adapter.py`. The exit status and the absence of side effects stay as they are. `docs/cursor-adapter.md:111` and `:155` say "`cursor-adapter.py`" for this case, so each gains "or one of its parts".
- A part that is missing, unreadable or unparsable fails the way a missing or unparsable entry fails today: a terminal run exits nonzero with a traceback, and the sessionStart launch guard prints the informational `{}`. The new test's faulted-part rows pin it (row 25).
- A `git pull` or branch switch landing between the loader's seven reads can give a mix of old and new parts. That window is new, because one file cannot skew. It needs an honest-mistake overlap, such as running `install` during a pull, and lasts milliseconds. Detecting a missing part before any part runs would be new behavior, so it stays out (Out of scope).

**Rollback.** The change persists nothing, so reverting this PR restores the monolith with no `install` re-run: the installed commands, the install record and the entry's path, name and flags are the same before and after. The revert is mechanical only until a later commit edits a part. After that, rolling back means reverting that later change, because git cannot carry a hunk back across the file split. On a revert pull the parts disappear before the entry is restored, which is the faulted-part case above for that moment.

**`hook-shim.py` stays out of this PR** (M8). The Ask names only `cursor-adapter.py`. Nothing in the split needs the shim changed. The shim imports no sibling. Its five mentions of `cursor-adapter.py` name the program, which keeps that name (row 17). A later split of the shim would first revisit `.claude/plans/cursor-adapter.md` row 345, which records the engineer's choice of one file for the shim.

**Size.** No file reaches 1,000 lines. The part counts above are arithmetic on the base's line numbers: each range's length, plus two header lines, minus the four `adapter_directory` lines from core. Only the entry's count is an estimate. The largest part, `cursor-adapter-hooks-json.py` at 703, leaves about 300 lines for Phase 5a's subagentStart and subagentStop entries (row 21). A later part costs one tuple entry and one file. No exception row exists to update (row 20). Verification step 6 measures every file.

**Review tier.** Production, with a cooperative threat model: a hostile part file, or a FIFO planted in the checkout, is out of scope (row 23).

**Assumption ledger.**

**Root:** `claude/.claude/scripts/cursor-adapter/cursor-adapter.py` is 3,392 lines, 3.4 times the repo's 1,000-line code-file limit, and Phase 5a adds code to it. This change splits it into files under the limit with no change to what any command does, writes or reports. `[verified: read; docs/design-decisions/code-file-line-limit.md:19]`

**Givens:**
- G1. Installed sessionStart entries run the recorded python3 with `-I -S` on `~/.claude/scripts/cursor-adapter/cursor-adapter.py check --hook-env`, and `install`'s self-check runs `<python3> -I -S <stow adapter> self-check --wrapper W`. Reason: those commands live in each engineer's own `~/.cursor/hooks.json`, so changing the path, name or flags needs every installer user to re-run `install`. `[verified: cursor-adapter.py:860, :1518, :1528; docs/scripts.md:492; test_cursor_adapter_install.py:4363, :5373]`
**Rows:**
1. The engineer picked the session's item 1, "Decompose `cursor-adapter.py`", whose wording is the session's. `[engineer-verified: "#3 first. Then #1 and the rest"]`
2. The loading mechanism is this plan's to decide, with the alternatives recorded here. `[engineer-verified: "can you let the planning process decide, with the architect and alternatives considered?"]`
3. This PR lands before the peer session's split of `test_cursor_adapter_install.py` starts its build. `[engineer-verified: "Mine first (Recommended)"]`
4. That the peer session holds its build until this merges and then syncs is the session's option description, not the engineer's words. The peer session replied that it holds its build until it hears whether this PR edits `test_cursor_adapter_install.py`, which the plan says it does not. `[unverified: session's own proposal; the peer's reply is the peer session's own message]`
27. Row 2's request, "with the architect and alternatives considered", is met by `plan-architect` (Opus, `MODE=plan-sections`) authoring Approach, with the alternatives recorded under "Loading mechanism" and "Lint". `[verified: the dispatch this session]`
5. `cursor-adapter.py` is 3,392 lines. Its section markers sit at :35, :321, :429, :568, :952, :1271, :1453, :1584, :1653, :1797, :2052, :2343, :2697, :2818, :3325 and :3341. Each part boundary falls on a marker with two blank lines before it (:566-567, :1269-1270, :1795-1796, :2050-2051, :2341-2342, :2695-2696, :3323-3324). Its top-level statements are:
   - the docstring (:2-14);
   - 18 imports (:16-33);
   - the `__main__` block (:3391-3392);
   - 301 defs, classes and single-name assignments.

   `[verified: read; grep of line starts: 323 begin with a letter, 319 of them def, class, import or NAME =, the rest docstring lines :4, :12, :13 and :3391]`
6. Tests load the adapter by compiling the file into a fresh `types.ModuleType` per test, with `__file__` set and no `sys.path` or `sys.modules` entry. Some load a copy of the whole directory under another module name. They read names off that module and patch them with `monkeypatch.setattr(world.adapter, ...)`. The Context's exploration counts about 22 distinct patched names, and `test_cursor_adapter_install.py:4533-4550` adds `classify_hooks` and `read_log`. `[verified: _cursor_payloads.py:124-130; test_cursor_adapter_install.py:139, :149-154, :4533-4550; the count is the exploration's]`
7. A function resolves a module-level name through its own `__globals__` at call time, so a patch reaches exactly the callers defined in the patched namespace. `checkpoint` reads `crash_hook` that way. `[verified: cursor-adapter.py:486-492]`
8. Five `exec(compile(...))` sites load code in this domain, and the adapter imports no sibling: `cursor-adapter.py:411`, `_cursor_payloads.py:120` and :129, and `test_cursor_adapter_shim.py:1525` and :2665. `[verified: grep of the repo; cursor-adapter.py:16-33]`
9. Under `-I -S`, a module beside the script is not importable, and `compile()` plus `exec` writes no bytecode file. `[verified: test_cursor_adapter_shim.py:3045-3055 and :1894-1908; cursor-adapter.py:407]`
10. `adapter_directory()` is `dirname(realpath(__file__))`, which resolves a stow link into the checkout. Every test that relocates the adapter copies or links the whole directory. `[verified: cursor-adapter.py:363-364; test_cursor_adapter_install.py:152, :1082, :5248-5250; test_cursor_adapter_generate.py:442; _cursor_payloads.py:634]`
11. Top-level statements read at load only names defined above them: `re` at :128, constants in f-strings at :206-207, a base class at :300, and default arguments at :1890 and :2846 that read :255 and :279. Executing whole sections in file order therefore reproduces the base's load. `[verified: read]`
12. ruff selects pyflakes (`F`) for every file and has one per-file-ignores row, with its rationale comment above it. Per-file analysis of a part would report F821 for names other parts define, and of the entry F401 for imports only the parts read. `[verified: pyproject.toml:6, :8-15; the F821 and F401 outcome is inferred, and Verification step 7 runs ruff over the result. Two plan-review reviewers ran the pinned ruff in scratch: it reported both outcomes per file, and over the concatenated source it was clean on the split and reported a seeded duplicate as F811]`
13. A scripts test that imports `_cursor_payloads` must be listed in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, and a test enforces it. `[verified: select-tests.py:339-342; test_select_tests.py:2024-2031]`
14. `pyproject.toml` and `select-tests.py` are global triggers, so `select-tests.py` itself selects the full suite for this diff. A change under the adapter directory alone selects `test_cursor_adapter_gate_matrix.py`, `test_cursor_adapter_lib_parity.py` and the scripts tests. `[verified: select-tests.py:36-44, :529-540, :966]`
15. No existing test reads `cursor-adapter.py`'s source text or pins a frame inside it. Source reads target `hook-shim.py`, and the unexpected-exception test pins its own file's frame. `[verified: grep of the cursor tests; test_cursor_adapter_install.py:4556, :5385; test_cursor_adapter_gate_matrix.py:2084]`
16. `main()`'s unexpected-failure line names the basename and line of the innermost frame. `[verified: cursor-adapter.py:3383-3388]`
17. `docs/cursor-adapter.md:270` places `CURSOR_TOOL_NAMES` "in `cursor-adapter.py`", and the table moves to the core part (:150). `docs/scripts.md:490` lists the directory's files. The other mentions name the command or the program and stay true: `docs/cursor-adapter.md:11`, :111, :138, :155, :305; `README.md:105`; `docs/design-decisions/hook-translation-shim-in-python.md:57`; `hook-shim.py:8`, :33, :107, :1807, :1846. `[verified: grep]`
18. `.claude/rules/cursor-adapter-parity.md` scopes itself to `claude/.claude/scripts/cursor-adapter/**`. `[verified: :2-3]`
19. `_move_fidelity.py nodes` checks that each named top-level node is defined exactly once across the after files and, with `--verbatim`, byte-identical to the base. It names a node by def, class or single-name assignment. Earlier split plans use it as their move proof. `[verified: _move_fidelity.py:146-153, :264-348; .claude/plans/cost-by-size-test-split.md:168]`
20. Nothing enforces the line limit yet, so no exception row exists for any file here. `[verified: code-file-line-limit.md:50-57]`
21. Phase 5a adds matcher-less subagentStart and subagentStop entries to generation and the sandbox grant's first install. `[verified: .claude/plans/cursor-adapter.md:848, :962]`
22. The peer session wants this plan's class-to-file map. `[verified: this plan's Context]`
23. Reviews of this plan and its code use the production tier with a cooperative threat model. The parts sit beside the entry in the engineer's own checkout, at the entry's own trust. `[unverified: tier declared by the dispatching session, not quoted from the engineer]`
24. A module docstring compiled in `exec` mode binds `__doc__` in the target namespace. `[unverified: CPython behavior not run this session; comment headers make the answer moot]`
25. When the adapter is missing, unreadable or broken, the sessionStart entry's launch guard prints the informational `{}`. A terminal run fails with Python's own error and a nonzero status. A part that fails to load fails the same way, which a scratch rebuild of the split confirmed for a missing part (exit 1, empty stdout). `[verified: test_cursor_adapter_generate.py:558-576 for the entry; the part case is inferred from the guard's text at cursor-adapter.py:699-701 and pinned by the new test's faulted-part rows]`
26. The plan's `Projected diff:` stays above 400 changed lines, in one PR. `[engineer-verified: "Proceed anyway"]`

**Mechanisms:**
- M1: One shared namespace, filled by executing each part's source into the entry's globals in file order. `anchors: root, row2, row6, row7, row8, row9`. The alternatives are under "Loading mechanism" above.
- M2: Parts are runs of whole sections, cut verbatim in file order. `anchors: row5, row11, row21`. Set aside: the Context exploration's concern-based partition. Its cycle-breaking moves exist only because separate modules cannot import each other in a cycle, which one namespace never has. Reordering top-level statements also risks a load-time read of a name defined later (row 11).
- M3: The loader reads with `open()` from `adapter_directory()`, which moves into the entry. `anchors: row10, row23, row25`. Set aside: restating `dirname(realpath(__file__))` in the loader, which duplicates `adapter_directory`, and a one-line refusal for a part that fails to load, which would be new behavior.
- M4: One per-file-ignores row and `test_cursor_adapter_parts.py`, which runs the repo's pinned ruff over the files as one source. `anchors: row12, row13, row14`. The alternatives are under "Lint" above. The lighter primitive over a hand-written scan is the checker the repo already pins.
- M5: The proof: `_move_fidelity.py nodes --verbatim`, a line-multiset diff, a namespace snapshot, and the unmodified suites. `anchors: root, row5, row15, row19`.
- M6: Two doc edits. `anchors: row17, row18`.
- M7: One `code-writer` dispatch, without a split. `anchors: row5, row13, row14`. The reason is under Critical files.
- M8: `hook-shim.py` untouched, and the class-to-file map kept in this plan. `anchors: row1, row3, row17, row22`.

## Critical files

**Dispatch: one `code-writer`, without a split.** Every satellite edit depends on the move's output: the loader names the part files, the new test reads the loader's names, and the lint glob and the docs name the files. A second dispatch would restate the same partition and line ranges. The brief requires:
- Verification step 1 first, so the base file sits in `<scratch>/base/`;
- each part built by extracting its line range from `<scratch>/base/cursor-adapter.py` (for example `sed -n '568,1268p'`, never from the working-tree entry, which the same dispatch edits) and prepending the header, never by retyping code, so a retried build rewrites the same bytes;
- during development, `scripts/venv-run.sh pytest` on the eight files Verification step 8 names, then Verification steps 1-8 at the end, with their output in the return: the `nodes` count and exit status, both `diff` outputs, `wc -l` per file, the ruff result, and the `select-tests.py` summary with any failing node IDs;
- no edit to any file outside this list, and none to an existing test file;
- no staging and no commits.

**Modify, production:** `claude/.claude/scripts/cursor-adapter/cursor-adapter.py`.

**Create, production** (under `claude/.claude/scripts/cursor-adapter/`, mode 644): `cursor-adapter-core.py`, `cursor-adapter-hooks-json.py`, `cursor-adapter-tools.py`, `cursor-adapter-install.py`, `cursor-adapter-sandbox.py`, `cursor-adapter-uninstall.py`, `cursor-adapter-check.py`.

**Create, tests:** `claude/.claude/scripts/tests/test_cursor_adapter_parts.py`.

**Modify, configuration:**
- `pyproject.toml` (one per-file-ignores row and its comment);
- `claude/.claude/scripts/select-tests.py` (one entry in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`, :339-342).

**Modify, docs:** `docs/scripts.md` (:490 only); `docs/cursor-adapter.md` (:111, :155 and :270 only).

**Also in the diff:** `.claude/plans/cursor-adapter-decompose.md`.

**Read, not changed:** `hook-shim.py`, `tool-map.json`, `parity-exceptions.json`, `field-table.json`, every existing test file including `_cursor_payloads.py`, `install.sh`, `stow-packages.sh`, `README.md`, `.claude/rules/cursor-adapter-parity.md`, `docs/design-decisions/hook-translation-shim-in-python.md` and `.claude/plans/cursor-adapter.md`.

**Reuse:** `_cursor_payloads.load_adapter`, `ADAPTER_DIR`, `ADAPTER_SOURCE` and `SHIM_INTERPRETER_FLAGS`; the `sys.path.insert(0, str(CLAUDE_DIR))` import shape of `test_cursor_adapter_generate.py:18-33`; the seeded-negative scan test shape of `test_cursor_adapter_shim.py:3104-3109`; `tests/_move_fidelity.py nodes`; the standard library's `symtable`. No new helper module or committed script.

Projected diff: about 6,750 lines. About 3,320 change in `cursor-adapter.py` (3,290 out, about 32 in), 3,286 enter the seven parts verbatim with their headers, about 200 make up the new test, and under 15 change across `pyproject.toml`, `select-tests.py` and the two docs.

## Verification

Run from the worktree root. `<scratch>` is a new directory outside the repository, and `<base>` is the SHA `git merge-base HEAD origin/main` prints (33589d60 when this plan was written). Steps 2 and 4 use scratch scripts under `<scratch>`, never committed.

1. `mkdir -p <scratch>/base`, then `git show <base>:claude/.claude/scripts/cursor-adapter/cursor-adapter.py > <scratch>/base/cursor-adapter.py`.
2. Moved nodes. List the base's named top-level nodes by `_move_fidelity.py`'s own rule (`_load_source(path).nodes`, `tests/_move_fidelity.py:168-176`), which gives 301 names (row 5). Then `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py nodes --before <scratch>/base/cursor-adapter.py` with one `--after <path>` flag per file, eight in all, because `--after` takes one path per flag (`_move_fidelity.py:885`), then `--moved <names> --verbatim <names>`. It exits 0 and reports `compared-nodes: 301`. Each definition then sits in exactly one file, byte for byte as in the base. Any finding blocks.
3. Every other line. Run `git show <base>:claude/.claude/scripts/cursor-adapter/cursor-adapter.py | grep -v '^[[:space:]]*$' | LC_ALL=C sort > <scratch>/before-lines.txt` and `cat claude/.claude/scripts/cursor-adapter/cursor-adapter*.py | grep -v '^[[:space:]]*$' | LC_ALL=C sort > <scratch>/after-lines.txt`. Then `diff <scratch>/before-lines.txt <scratch>/after-lines.txt` prints no `<` line. Its `>` lines are the loader block's non-blank lines that the base lacks, the new `# --- the parts ---` marker and seven copies of the part header, 26 lines in all. The two lines of `adapter_directory` are in both files, so they do not print. This covers the section markers and the comments between nodes, which step 2 does not compare.
4. Namespace snapshot. For the base file and the new entry, load the file the way `load_adapter` does (`_cursor_payloads.py:124-130`) under `PYTHONHASHSEED=0`, in a process started without `-I` or `-E`, which would ignore the variable and change the frozenset reprs. Write one line per top-level name not starting with `__`: the name, its type's name, and the value's `repr()` when it is not callable, not a module and not a bare `object()`. Add one line for `__doc__`, which that filter would hide. Sort both outputs, because `adapter_directory` moves to the front of the namespace and insertion order is not part of the equivalence claim. The `diff` of the two sorted outputs is exactly two added lines, for `ADAPTER_PART_FILE_NAMES` and `execute_adapter_parts`.
5. No existing test changed: `git diff --name-only <base> -- claude/.claude/hooks/tests claude/.claude/scripts/tests ':!claude/.claude/scripts/tests/test_cursor_adapter_parts.py'` prints nothing. With no edit to an existing test, a reviewer has no patch retarget to tell apart from an assertion change.
6. `wc -l claude/.claude/scripts/cursor-adapter/cursor-adapter*.py claude/.claude/scripts/tests/test_cursor_adapter_parts.py`: every count is at most 1,000, and each part's count equals its figure in the parts list. A mismatch means a range was cut differently, and steps 2 and 3 say whether anything was lost.
7. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean. A glob that missed one of the eight files would report F821 there, so this also proves the new row's reach.
8. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, apart from failures that also occur at the merge-base. It selects the full suite on its own (row 14).
   - Record the failing node IDs, then rerun each one on this tree and from `git worktree add --detach <scratch>/base-tree <base>` under the same isolated invocation, per `CLAUDE.md`'s "Prove your change caused a failing check". A timing-marked ID runs as `-m timing -n0`, which is how CI runs that leg. Compare those two reruns, not the loaded full run against an isolated one. An ID that fails only on this tree blocks.
   - Run the eight files below once at the base tree and once here with `--junitxml`, then `_move_fidelity.py test-ids --before <xml> --after <xml>` exits 0 with the new test file's IDs as the only difference. This also catches a pass that turned into a skip.
   - The suites that scan `git ls-files` (`test_ticket_reference_discipline.py`, `test_shell_lib_function_liveness.py`, `test_shellcheck.py`) cannot see the untracked parts during this step. `/ready-for-review`'s full-suite run, after the parent stages the files, re-confirms them.
   - Report pass counts, with zero failed, for `test_cursor_adapter_install.py`, `test_cursor_adapter_generate.py`, `test_cursor_adapter_translate.py`, `test_cursor_adapter_shim.py`, `test_cursor_adapter_parts.py`, `test_cursor_adapter_lib_parity.py`, `test_cursor_adapter_gate_matrix.py` and `test_select_tests.py`.
   - The interpreter-flag runs among them: `check --hook-env` under `-I -S` at `test_cursor_adapter_install.py:3233` and :5442, the real self-check against a copied stow tree in the class at :1079, `generate` under `-S` at `test_cursor_adapter_generate.py:51`, and the new link-only run.
9. `git worktree remove <scratch>/base-tree` if step 8 created it.

## Out of scope

- **Splitting `hook-shim.py` (1,897 lines).** The Ask names only `cursor-adapter.py`, and nothing in this split needs the shim changed (M8).
- **Splitting `test_cursor_adapter_install.py` (6,491 lines) or `test_cursor_adapter_shim.py` (4,366).** The install split is the peer session's, after this lands (row 3), and this PR edits no existing test file.
- **The other items on the session's list, "and the rest" in the Ask.** Each is its own change after this one.
- **Turning the parts into importable modules with explicit cross-file references.** A later change can, at the cost M1 names: rewritten call sites and retargeted patches.
- **A design-decision file for the shared namespace.** The loader's two comment lines state the constraint, and this plan, committed with the PR, records the alternatives.
- **Retargeting `.claude/plans/cursor-adapter.md`'s Phase 5a Critical files bullets from `cursor-adapter.py` to the part files.** That plan stays unedited here, and Phase 5a's session updates its bullets when it starts.
- **Changing the 1,000-line limit** (`docs/design-decisions/code-file-line-limit.md:19`) or the rule that agents run `select-tests.py` and not the full suite (`CLAUDE.md` "Commands"). This plan works within both. The suite still runs in full for this diff, because `select-tests.py` itself selects it (row 14).
- **Checking that every part exists before any part runs.** It would be new loader behavior, and today's single file cannot lack a part. The faulted-part rows pin the current outcome instead.
- **The line limit's enforcement check**, child F1 of `.claude/plans/code-file-size-splits.md` (row 20).
