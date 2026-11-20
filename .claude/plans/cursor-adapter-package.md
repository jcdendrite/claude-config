# Convert the cursor-adapter parts into a package

## Context

Goal: replace the entry-plus-seven-parts shared-namespace layout of `claude/.claude/scripts/cursor-adapter/` with a proper Python package, so names resolve through real imports instead of one namespace filled by `exec`.

Ask: "My intuition tells me that what you've implemented is brittle, but that having a giant script like before isn't good either." — the engineer, on the shape merged in #1518. The engineer then asked for "the package-conversion follow-up for sure".

Why now: #1518 split the 3,392-line `cursor-adapter.py` along its section markers and kept one shared namespace to keep the move verbatim. Its PR body names the costs: no part can be imported or linted alone, ruff needs a per-file-ignores row plus `test_cursor_adapter_parts.py` to check names as one source, and editors report undefined names in the parts. Three review findings were deferred for the same reason: the eight-file load is not a snapshot, the part read has no FIFO guard, and `hook-shim.py:33` names the grammar's old file. `[unverified: the contents of #1518's PR body and of its deferred review findings are the session's recollection; neither is in the tree]`

Constraints carried from #1518 and checked this session:
- The adapter runs as `python3 -I -S <stow path>/cursor-adapter.py`. `-I` keeps the script directory off `sys.path`, so a sibling `import` fails unless the entry adds the directory back.
- `load_adapter` in `claude/.claude/hooks/tests/_cursor_payloads.py` builds a fresh `types.ModuleType` per call, with no `sys.modules` entry. Some tests load from a copied directory with one neighbour file broken.
- About 110 distinct names are read off the loaded module. At least thirteen patched names have callers outside their defining part, among them `durable_write`, `LOG_ROTATE_BYTES`, `settings_json_path`, `find_tool`, `run_self_check`, `HOST_WALK_BOUND_SECONDS`, `SANDBOX_WRITABLE_DIRECTORIES`, `plan_sandbox_grant`, `generate_entries`, `apply_sandbox_action`, `classify_hooks`, `load_shim` and the `fcntl` import. An exploration's literal grep found eleven, and a later read of the parametrized patches found two more.
- The install-test split is already merged (#1519). `[verified: git log shows #1519 in this branch's history]` No parallel PR is in flight on these files. `[unverified: the plan cites no open-PR listing]`

Size is unknown. The plan sizes the conversion first and reports where it lands.

## Approach

Turn the seven part files into the modules of a `cursor_adapter` package beside `cursor-adapter.py`, each importing by name what it reads, and make the entry a loader that imports that package from its own directory under the unchanged `-I -S` launch. Tests keep reading and patching one `adapter` object: `load_adapter` returns a namespace over the entry and the package's modules whose patches rebind every module that imported the name, so no existing test body changes.

**Where the sizing lands.** 18 files plus this plan, about 1,380 changed lines in one PR, and no existing test body retargeted. Critical files breaks that estimate down by part. The 48 patches on the adapter namespace, the 13 on `adapter.os` or `adapter.subprocess`, and the 423 lines outside the rewritten parts test that read `adapter.<name>` stay as they are (row 12). No seam splits the change below 400 lines, because the existing patches pass only once the package and the test namespace land together (M9). No lighter shape removes the brittleness the Ask names (row 2):
- Fixing the loader's three deferred findings in place keeps the shared namespace. The parts still carry no imports, ruff still needs its per-file-ignores row and the concatenated check, and the file-order rule stays (row 6).
- Reading names as module attributes (`core.durable_write`) is heavier, not lighter (M2).
- One file is the shape the engineer rejected (row 2).

**The package** (M1). Under `claude/.claude/scripts/cursor-adapter/`, `git mv` moves each part into `cursor_adapter/`: `cursor-adapter-core.py` to `core.py`, `cursor-adapter-hooks-json.py` to `hooks_json.py`, and `tools`, `install`, `sandbox`, `uninstall` and `check` the same way. Two files are new:
- `cursor_adapter/__init__.py` holds the one line `"""The Cursor adapter's code, which cursor-adapter.py beside this directory imports and runs."""`.
- `cursor_adapter/cli.py` holds the entry's `# --- generate ---` and `# --- command line ---` sections (`run_generate`, `build_parser` and `main`, base :65-128), verbatim. The entry then holds no adapter logic, the shape `transcript-analysis.py:7` already has (row 19).

Each module follows these format rules:
- Line 1 is a one-line docstring naming what the module holds. The part's line-1 header and the blank line after it go.
- Standard-library imports come next, then the package imports M2 describes, in the order ruff's isort gives.
- Section markers and every definition stay verbatim and in order, apart from M2's moves and `adapter_directory`.
- No module carries a shebang or an executable bit.

`adapter_directory` moves from the entry into core's paths section, before `checkout_claude_directory`, as `os.path.dirname(os.path.dirname(os.path.realpath(__file__)))` under the comment `# The directory that holds this package, hook-shim.py and the JSON files.` Its six call sites keep reading the same directory (row 16). The package sits inside `cursor-adapter/`, not beside `transcript_analysis/`, because the entry finds it in its own directory and `select-tests.py` and the parity rule already cover that directory (rows 18, 21).

**Names across modules** (M2). Each module imports by name every name it reads that another module defines, in one `from .<module> import (...)` block per source module, and code bodies stay verbatim. By-name imports need an acyclic import graph, and today's partition has two cycles (row 13):
- install and uninstall call each other.
- sandbox and uninstall call each other.

The module order is core, hooks_json, tools, sandbox, install, uninstall, check, cli. In that order uninstall's calls into install and sandbox are forward edges, so only three backward edges close the two cycles. Three functions from uninstall remove them by moving verbatim to the end of core's files section, after `read_bytes_or_refuse`, in this order:
- `json_bytes_or_refusal`, which sandbox calls.
- `sweep_temp_debris`, which install calls.
- `read_manifest`, which sandbox calls.

Each reads only core names and the standard library (row 14). Every module then imports only from modules before it in that order. A later edit that adds a cycle fails at import in every test that loads the adapter. Set aside:
- Reading names as module attributes (`from . import core`, then `core.durable_write`), the `transcript_analysis` discipline (row 19). It is heavier here, for four reasons:
  - About 450 body lines that read a core name today would change (row 30).
  - ruff cannot check a `core.<name>` read, which is why that package carries `test_transcript_package_module_prefixes.py`.
  - The reason that package gives for the rule, reassignable module globals, has no instance here (row 15).
  - M4 gives patches the reach the discipline would.
- Star imports. They raise F403 and F405 and leave no per-file name check, which is what this change removes.
- Keeping the cycles, with module imports at the cyclic edges only. That is a second import style at a few call sites, and an import whose success depends on which module loads first.

**The entry** (M3). `cursor-adapter.py` keeps its path, name, shebang, usage docstring and launch (G1). Its imports become `importlib`, `importlib.machinery`, `importlib.util`, `os` and `sys`, and the rest of the file becomes:

```python
# `-I` keeps this file's directory off sys.path, so the package is found by searching that directory alone.
ADAPTER_PACKAGE_NAME = "cursor_adapter"


def import_adapter_cli():
    adapter_dir = os.path.dirname(os.path.realpath(__file__))
    spec = importlib.machinery.PathFinder.find_spec(ADAPTER_PACKAGE_NAME, [adapter_dir])
    # A directory without a regular __init__.py gets a namespace spec, whose origin is None.
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError(f"no {ADAPTER_PACKAGE_NAME} package in {adapter_dir}", name=ADAPTER_PACKAGE_NAME)
    package = importlib.util.module_from_spec(spec)
    sys.modules[ADAPTER_PACKAGE_NAME] = package
    previous_dont_write_bytecode = sys.dont_write_bytecode
    # The adapter writes no bytecode into the checkout, and a test process keeps its own setting.
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(package)
        return importlib.import_module(f"{ADAPTER_PACKAGE_NAME}.cli")
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode


adapter_cli = import_adapter_cli()

if __name__ == "__main__":
    sys.exit(adapter_cli.main())
```

The docstring's sentence "The module imports only the standard library, so the recorded python3 can run it under `-I -S`." becomes "Its code is the `cursor_adapter` package beside its real path, which imports only the standard library, so the recorded python3 can run it under `-I -S`." The entry binds no `main` of its own, so `_move_fidelity.py nodes` finds `main` once (M8).
- `PathFinder.find_spec` with an explicit path searches that directory alone and leaves `sys.path` as `-I` set it (row 8, row 33).
- A `cursor_adapter/` directory whose `__init__.py` is missing or a FIFO gets a namespace spec, whose `origin` is None, where an absent directory gets no spec at all (row 8). The entry treats that spec as a missing package and raises the same ModuleNotFoundError it raises for an absent directory, so a missing or FIFO `__init__.py` fails like a missing module. M6 relies on this.
- `sys.modules[ADAPTER_PACKAGE_NAME]` is set before `exec_module`, the order a package import needs (row 7). The submodules then import through the package's `__path__`.
- The entry evicts nothing from `sys.modules`, because nothing in production registers the package before it runs (row 17). `load_adapter` owns the eviction a test process needs (M4).
- A failed load leaves what it registered in `sys.modules`, with no unregister path, because a failed load ends the process and `load_adapter` evicts at the next load.
- The bytecode setting covers every package import, because importing `cli` imports the rest. It stops bytecode writes, not reads (row 9), and Out of scope records why a cached file is accepted.

Set aside:
- Keeping the exec of the parts. It is today's shape, which the Ask calls brittle (row 2).
- `sys.path.insert(0, dir)`, then `import cursor_adapter`. It puts the script directory back at the front of `sys.path`, the shadowing `-I` exists to prevent (row 7).
- `sys.path.append(dir)`. The standard library still wins, but in a test process the first directory appended keeps winning, so a load from a copied directory would import the checkout's package. Every load would also lengthen `sys.path`.
- Running the directory or a zipapp. It changes the launch string (G1), and a zipapp needs a build step the stow-link layout lacks (row 7).
- `importlib.util.spec_from_file_location` on `__init__.py`. It works under `-I -S` (row 7), but it opens the file it is given without the finder's regular-file check. `PathFinder.find_spec` returns the same kind of spec through the finder.
- Evicting a registered package in the entry. It mutates the interpreter's module registry on the production path for a test's benefit, though nothing in production registers the package (row 17), and `load_adapter` can evict before it execs the entry.

**Tests read the package through one namespace** (M4). New `claude/.claude/hooks/tests/_cursor_adapter_namespace.py` holds `ADAPTER_PACKAGE_NAME`, equal to the entry's, and `AdapterNamespace`, a `types.ModuleType` subclass over the entry module and each `cursor_adapter.<name>` submodule:
- It holds references to exactly the module objects its own load registered, and never resolves a module through `sys.modules` when a name is read or written. A namespace from an earlier load keeps addressing that load's modules after a later load replaces them in `sys.modules` (row 35).
- A read returns the object the modules binding that name hold. It raises AttributeError when no module binds the name or two bind different objects.
- A write raises AttributeError in the same two cases. Otherwise it rebinds the name in every module whose binding is the current object, and a module that does not bind the name stays without it. `monkeypatch.setattr` and its undo both write through it (row 10), so a patch and its restore reach the owner and every importer (row 11).
- It exposes the modules it spans, which the span rows read.
- Its `__file__` is the entry's.

The renamed test's uniqueness row keeps every name a `def`, `class` or assignment binds at module level in one file only, which is what makes these name-keyed reads and writes unambiguous for every name the adapter defines, so `hasattr(adapter, name)` cannot mistake two conflicting definitions for an absent name. The namespace module's docstring carries the one non-obvious fact: a module that imports a name by name holds its own binding, so a patch rebinds each one.

`load_adapter` (`_cursor_payloads.py:124-130`) keeps its signature and its exec of the entry, evicts the registered package before that exec, and returns the namespace over what the exec registered:

```python
def load_adapter(source: Path = ADAPTER_SOURCE, name: str = "cursor_adapter_under_test") -> AdapterNamespace:
    """cursor-adapter.py executed into a fresh module on every call, over a fresh import of the package beside it, so no
    module state leaks between tests and no bytecode file is written."""
    for registered in [module_name for module_name in sys.modules if module_name.partition(".")[0] == ADAPTER_PACKAGE_NAME]:
        del sys.modules[registered]
    module = types.ModuleType(name)
    module.__file__ = str(source)
    exec(compile(Path(source).read_bytes(), str(source), "exec"), module.__dict__)  # loading the file under test
    return AdapterNamespace.over_load(module)
```

- `AdapterNamespace.over_load` asserts that the entry's `ADAPTER_PACKAGE_NAME` equals the namespace module's, then spans the entry and every `cursor_adapter.<name>` module `sys.modules` holds at that moment, which the eviction guarantees came from this load.
- The eviction reaches `cursor_adapter` and `cursor_adapter.*` only, not the names `load_adapter` gives the entry module, such as `cursor_adapter_under_test`.
- Leaving `cursor_adapter*` registered after a test, a failed load's partial package included, is accepted, because the next `load_adapter` evicts it.
- `_cursor_payloads.py` imports both names as `from ._cursor_adapter_namespace import ADAPTER_PACKAGE_NAME, AdapterNamespace` (row 29). The class gets its own module because `_cursor_payloads.py` is already 969 lines (row 22).

How each kind of test works under the package:
- Fresh per test: each `load_adapter` call evicts the registered package, then runs the entry, which imports it again (M3), so every call returns new module objects, as the exec of one file did.
- Copied directories: `copy_adapter` and `load_adapter_copy_with` copy the whole directory, the package included. Because `load_adapter` evicted the checkout's package first, the entry in the copy imports the copy's package, whose `adapter_directory()` names the copy, so the replaced neighbour file is the one read (row 17). Neither helper changes.
- Patches and reads: the 48 patches, the 13 standard-library patches and the 423 read lines outside the rewritten parts test resolve through the namespace unchanged (row 12). `test_cursor_adapter_generate.py:122`, `not hasattr(adapter, "DEFAULT_DEADLINE_SECONDS")`, still holds, because no module binds that name.
- Subprocess runs under `-I -S`, such as the sessionStart `check --hook-env` and the real self-check, exercise the entry's own import with no namespace involved.

Set aside:
- Reading and patching each name on its owning module, such as `world.adapter.core.durable_write`. 423 read lines and 48 patches change across 14 files, and a patch on the owner still misses each by-name importer unless production also takes M2's module-attribute alternative.
- A plain module holding every package name. Reads work, but a patch on it rebinds no module the code runs in, so every patch would succeed and reach no caller.
- An explicit `patch_adapter(...)` helper, with a namespace that refuses writes. It has the same reach, but all 48 patch sites change, and the usual `monkeypatch.setattr` idiom fails instead of working.

**Lint** (M5). Delete the per-file-ignores row and its comment (`pyproject.toml:16-18`). Each module imports what it reads, so ruff's F821 and F401 run per file under the repo's own config (row 20), and an editor resolves every name. The concatenated-source check goes, and its detectors land elsewhere:
- F821, F401 and F811 run per module in CI's Lint step and in Verification step 5, not in scoped pytest runs (row 36). An import of a name its source module lacks also fails at import, in every test that loads the adapter.
- A name defined in two modules no longer collides, because the modules no longer share a namespace. The renamed test's uniqueness row keeps each module-level definition in one file, the guarantee cross-file F811 gave (row 32).

**`test_cursor_adapter_parts.py` becomes `test_cursor_adapter_package.py`** (M3, M4, M5, M6), by `git mv`. Its module docstring names its subject: how `cursor-adapter.py` loads its package, and the namespace tests read it through. Verification step 10 shows the rows for the guards it lists failing when each guard is removed. No other row is claimed to fail on a removed line. The `cursor_adapter/` isolation row guards the flags its test passes. The test takes `AdapterNamespace` and `ADAPTER_PACKAGE_NAME` from `_cursor_payloads`, which imports both, so it imports no further hooks-tests module (row 21).
- `TestPartList` and `TestNamesAcrossTheFiles` go, with `combined_adapter_source`, `ruff_findings`, `finding_codes` and the constants only they use. The rows below replace their seeded detector controls.
- A module that fails to load: the no-fault row stays. The faulted module is `cursor_adapter/sandbox.py`: missing, unreadable (skipped under root, as now), syntax-broken, or replaced by a FIFO from `os.mkfifo`. Each `check --hook-env` run under `-I -S` exits nonzero with empty stdout and a stderr matching `cursor_adapter[./]sandbox`. The FIFO row ends inside the existing 60-second timeout instead of blocking (M6).
- A package that fails to load: in a `copy_adapter` copy, the `cursor_adapter/` directory removed, its `__init__.py` removed, or its `__init__.py` replaced by a FIFO. Each `check --hook-env` run under `-I -S` exits nonzero with empty stdout and a stderr naming `cursor_adapter`, inside the 60-second timeout (M3, M6).
- `TestALinkToTheEntryAlone` stays as it is.
- Interpreter isolation, modelled on `test_cursor_adapter_shim.py:3030-3055` and :3104-3109 (row 34). Each subprocess row runs `check --hook-env` under `-I -S` in a `copy_adapter` copy:
  - A `json.py` that writes a canary, planted beside the entry: the flagged run exits 0 and writes no canary. Its control, the same run without the flags, writes it.
  - The same `json.py` planted inside `cursor_adapter/`: the flagged run and its control share one environment, with `PYTHONPATH` naming the copy's `cursor_adapter/`, as `test_cursor_adapter_shim.py:3037-3043` does. They differ only in the interpreter flags. An absolute `import json` searches `sys.path` alone, and only that variable puts `cursor_adapter/` on it. The flagged run, with `-I -S`, exits 0 and writes no canary. Its control, without them, writes it.
  - A scan of the entry and every `cursor_adapter/*.py` finds no absolute import of a module outside `sys.stdlib_module_names`, by the rule `_imported_non_standard_names` applies, which skips relative imports. A seeded control finds `requests`. The renamed test holds its own copy of that eight-line rule under the DAMP test-code exception, since no existing test body changes.
- Process state, each row an in-process `load_adapter` of a `copy_adapter` copy's entry:
  - With `sys.dont_write_bytecode` set to False under `monkeypatch` before the load, it is False after, so the row does not depend on the ambient setting.
  - `sys.path` equals what it was before the load.
  - No `__pycache__` directory and no `*.pyc` exists anywhere under the copy's `cursor_adapter/`, searched recursively.
  - A copy whose `cursor_adapter/sandbox.py` is syntax-broken, loaded with `sys.dont_write_bytecode` set to False under `monkeypatch`, raises a SyntaxError whose filename is the copy's `cursor_adapter/sandbox.py`, and leaves `sys.dont_write_bytecode` False and `sys.path` as it was.
- No bytecode from a subprocess: a `check --hook-env` run under `-I -S` in a `copy_adapter` copy leaves no `__pycache__` directory and no `*.pyc` anywhere under the copy's `cursor_adapter/`, searched recursively.
- Fresh loads:
  - Two `load_adapter()` calls give different `durable_write` objects.
  - A checkout load, then a load of a copy's entry, then a checkout load again: the copy's `adapter_directory()` equals the copy's real path, and each checkout load's equals `ADAPTER_DIR`'s real path.
- The namespace over the real package:
  - Span: the modules it spans are the entry plus every `cursor_adapter/*.py` but `__init__.py`, compared by real path, with the expected set built from the directory listing, not from `sys.modules`.
  - Span control: in a copy holding an extra `cursor_adapter/unimported.py` that no module imports, the span lacks exactly that file.
  - Patch reach: after `monkeypatch.setattr(adapter, "durable_write", sentinel)`, every spanned module that bound the original binds `sentinel`, and at least two do.
  - Undo: `monkeypatch.undo()` restores the original in each.
  - Earlier namespaces: after a second `load_adapter()`, a patch and `monkeypatch.undo()` through the first namespace read and restore the first load's modules, and leave the second load's bindings untouched.
- The namespace over two synthetic modules:
  - A read of a name the two bind to different objects raises AttributeError.
  - A write of that name raises AttributeError.
  - A write of a name neither binds raises AttributeError.
  - A write of a name only one binds leaves the other without it.
- Uniqueness, over the entry and every `cursor_adapter/*.py`:
  - Each name a module-level `def`, `class` or assignment binds, dunders excluded, is bound in one file only. Import bindings are not counted, because a by-name import binds the importer to the same object.
  - The scanned files equal the entry plus the directory listing of `cursor_adapter/`.
  - A seeded control over two synthetic sources finds a function defined in both.

**The deferred findings** (M6), each fixed only where the package removes its cause (row 4):
- The part read has no FIFO guard. The package removes the cause. A module file that is not a regular file gets no spec from the finder, so it is never opened and its import fails as a missing module. A package directory whose `__init__.py` is not a regular file gets a namespace spec, which the entry rejects (row 8). The FIFO rows on `sandbox.py` and `__init__.py` pin both.
- `hook-shim.py:33` names the grammar's old file. This PR moves the grammar again, so the line becomes `# The command-line vocabulary. The adapter's copy in cursor_adapter/core.py, which its command grammar reads, must stay equal to it.` (row 27).
- The eight-file load is not a snapshot. The package does not remove the cause: the import system reads each module file as it imports it, in the same window the exec loop has, so the finding stays deferred (Out of scope).

**Docs and config** (M7):
- `docs/scripts.md:575`: the two sentences from "Its code sits in" through "none runs or is imported alone." become "Its code is the `cursor_adapter` package beside it, which it imports from its own real directory, and the generated command grammar is in `cursor_adapter/hooks_json.py`." In the next sentence, "It imports only the standard library" becomes "The package imports only the standard library".
- `docs/cursor-adapter.md:113` and :157: "`cursor-adapter.py` or one of its parts" becomes "`cursor-adapter.py` or a module of its `cursor_adapter` package".
- `docs/cursor-adapter.md:272`: "`cursor-adapter-core.py`" becomes "`cursor_adapter/core.py`".
- `select-tests.py`: :354 names `test_cursor_adapter_package.py`, and `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS` (:286-295) gains `claude/.claude/hooks/tests/_cursor_adapter_namespace.py`, so a change to it selects the scripts tests that load the adapter. A path under the package needs no new rule, because `_is_under` already matches it (:554-555, :977).
- No other mention changes. `README.md:105` and `_lib.sh:3695` name the program, which keeps its name (row 28).

The PR body carries three notes:
- Phase 5a's session learns that `.claude/plans/cursor-adapter.md`'s Critical files bullets name files this PR renames, and that it retargets them to `cursor_adapter/<module>.py` before it starts (row 3).
- The per-file name check (F821, F401 and F811) now runs in CI's Lint step and Verification step 5, not in scoped pytest runs, and the renamed test's rows replace the seeded detector controls (row 36).
- Each guard mutation's failing node ID and the assertion's first line, from Verification step 10, with every absolute path replaced by a placeholder.

**What a user can see change.** Only diagnostics. Generated commands, the install record, entry ownership and every report stay as they are (G1).
- The unexpected-failure line names a module file, such as `install.py:<line>`, where it named a part file.
- A missing module fails as `ModuleNotFoundError: No module named 'cursor_adapter.sandbox'`. A FIFO in a module's place fails the same way, where the part read would block.
- A missing package directory, or an `__init__.py` that is missing or a FIFO, fails as `ModuleNotFoundError: no cursor_adapter package in <directory>`.
- The exit status and the launch guard's `{}` stay.
- Under an interpreter older than the floor, the SyntaxError names the package module that holds the syntax.

**Rollback.** The change persists nothing: only the entry names a part file, so nothing `install` writes can name one (row 31). A revert therefore restores the parts with no `install` re-run, and stays mechanical until a later commit edits a module. Wherever stow links the checkout, a pull is the deploy (row 18), and each direction passes through mixed checkouts:
- The forward pull can leave the old entry with some of its parts deleted, the new entry with no package yet, or the new entry with a partial package.
- The revert pull can leave the new entry with a partial package or none, or the old entry with some of its parts not yet restored.

Each file-presence state this PR's own transition produces ends in an exception: a missing module, an absent name a module imports, or a missing package. The old entry never imports the package, the new entry never reads a part, and the new entry imports every module before `main` runs, which the span rows pin. Two states fall outside that, and both belong to the deferred snapshot finding (Out of scope). One is a module file git has written only partway. The other is two modules from different commits whose imported names all still exist. Either can load without an exception.

Of the installed hook entries, only the informational sessionStart `check --hook-env` entry loads the package (row 31):
- Its launch guard turns a nonzero exit or empty stdout into `{}` with exit 0 and sends stderr to `/dev/null`, so that session start gets no report and shows no error, as today's exec failure does.
- `status`'s flag "the sessionStart entry looks silent", which fires once two or more shim first lines follow the last check line, is the detection route.
- Every fail-closed entry runs `hook-shim.py`, which imports only the standard library and never loads the package.
- The `hook-shim.py:33` edit makes the pull rewrite the shim too. A fail-closed hook that runs during that write can find the shim missing or partial and deny, the partial-pull case `docs/cursor-adapter.md`'s Recovery section already names.

After this PR, by-name imports also turn a later mixed checkout of two modules into an ImportError at load, before any command writes, when the mix adds, removes or renames a name a module imports. Today's shared namespace fails that same mix with a NameError only when the call runs, which can be partway through `install`. A mix that changes only a body, a signature or a constant loads in both layouts.

**Review tier.** Production, with a cooperative threat model (row 25). A hostile module file, or a file swapped between the finder's `stat` and the loader's `open`, is out of scope.

**Assumption ledger.**

**Root:** `cursor-adapter.py` executes seven part files into its own namespace. No part can be imported, linted or read in an editor on its own, ruff needs a per-file-ignores row and a concatenated-source test, and a part's top-level code may read only names earlier parts define. This change makes the parts an ordinary Python package with no change to what any command does, writes or reports, or to how it is launched. `[verified: cursor-adapter.py:35-62; cursor-adapter-core.py:1; pyproject.toml:16-18; test_cursor_adapter_parts.py:1-11]`

**Givens:**
- G1. Installed sessionStart entries and `install`'s self-check run `<python3> -I -S <stow path>/cursor-adapter.py`, and the command parser accepts only that shape. Reason: those commands live in each engineer's own `~/.cursor/hooks.json`, so changing the path, name or flags needs every installer user to re-run `install`. `[verified: cursor-adapter-hooks-json.py:123; cursor-adapter-tools.py:260; the parser's literal -I -S match at cursor-adapter-hooks-json.py:198-207 per this session's exploration]`

**Rows:**
1. The engineer asked for the package conversion. `[engineer-verified: "the package-conversion follow-up for sure"]`
2. The design must remove the brittleness of the shared-namespace shape, and the single file stays rejected. `[engineer-verified: "My intuition tells me that what you've implemented is brittle, but that having a giant script like before isn't good either."]`
3. Phase 5a of `.claude/plans/cursor-adapter.md` waits for this PR to merge. `[engineer-verified: "Phase 5a waits (Recommended)"]`
4. This PR fixes each of the three deferred #1518 findings where the package removes its cause. `[engineer-verified: "Fix them where the package removes the cause (Recommended)"]`
5. Rows 3 and 4 rest on the selected labels alone, read without the questions they answered, and the "(Recommended)" suffix is the session's own marking. The option descriptions under them were the session's proposals and are not relayed here. `[unverified: session's own option descriptions]`
6. Each part starts with a header saying it needs no import. The per-file-ignores row turns off F401 and F821 for the eight files. `test_cursor_adapter_parts.py` runs ruff over their concatenation. The loader's comment requires a part's top-level statements to read only names the entry or an earlier part defines. `[verified: cursor-adapter-core.py:1; pyproject.toml:16-18; test_cursor_adapter_parts.py:44-56; cursor-adapter.py:37-38]`
7. Under `-I`, the script's directory is not on `sys.path`, and `-I` does not imply `-B`. `spec_from_file_location` with `submodule_search_locations`, with `sys.modules` set before `exec_module`, imports a package and its submodules under `-I -S` and leaves `sys.path` unchanged. `sys.path.insert(0, dir)` re-adds the shadowing risk, and running a directory or a zipapp changes the launch string. `[verified: this session's exploration, against docs.python.org/3/using/cmdline.html and a python 3.15.0 run]`
8. `importlib.machinery.PathFinder.find_spec(name, [dir])` searches only `dir`, through the path-hook finder, which tests each candidate file with a `stat`-based regular-file check, so a FIFO is never opened. What it returns: `[verified: CPython 3.14.7 importlib/_bootstrap_external.py:155-166, :1285-1306, :1379-1404, read this session; the namespace fallback for a missing or FIFO __init__.py was run under -I -S on 3.14.7 and 3.15.0 by the backend, CISO and SDET reviews of this plan, not rerun here]`
   - A file-backed spec when `<dir>/<name>/__init__.py` or `<dir>/<name>.py` is a regular file.
   - A namespace spec, whose `loader` and `origin` are None, when `<dir>/<name>/` is a directory with no regular `__init__.py`, the file missing or a FIFO.
   - None when neither exists.
   - For a submodule searched in a regular package's `__path__`, no spec when `<module>.py` is missing or not a regular file, so its import raises ModuleNotFoundError.
9. An import writes bytecode unless `sys.dont_write_bytecode` is set when it builds the module's code. The setting gates only the write: `SourceLoader.get_code` still loads a `__pycache__` file in two cases. One is a timestamp-based file whose recorded source mtime, in whole seconds, and size match the source's. The other is a hash-based file with the unchecked flag, which loads with no source comparison under the default `--check-hash-based-pycs`. Today's loaders compile each source and neither write nor read bytecode. `[verified: CPython 3.14.7 importlib/_bootstrap_external.py:826-895, the read at :849 and :876-879, the unchecked-hash path at :862-867 and :888-890, and the write gate at :895; cursor-adapter-core.py:370-371; _cursor_payloads.py:124-126; cursor-adapter.py:57-59]`
10. `monkeypatch.setattr` reads the old value with `getattr`, then calls `setattr` on the target, and its undo calls `setattr` with each old value in reverse order. `[verified: .venv/lib/python3.14/site-packages/_pytest/monkeypatch.py:243-251, :392-394]`
11. A function resolves a module-level name in its own module's namespace at call time, so a patch reaches only the callers in the patched module. Today's one namespace makes every patch reach every caller. `[verified: cursor-adapter.py:37; cursor-adapter-core.py:450-456]`
12. Tests patch the adapter namespace at 48 `setattr` sites, one of them parametrized over five callees, and patch `adapter.os` or `adapter.subprocess` at 13 more. 428 lines across 15 test files read `adapter.<name>`, patches included, and 423 lines across 14 files once the rewritten parts test is left out. The count leaves out the program name `cursor-adapter.py`. Patched names with callers outside their defining part include `durable_write`, `run_self_check` (in the `fast_world` fixture), `generate_entries`, `classify_hooks`, `load_shim` and `fcntl`. `[verified: Grep of setattr on adapter and world.adapter, this session; Grep count of (^|[^-\w])adapter\.[A-Za-z_] over claude/.claude/**/tests/**/*.py, this session; test_cursor_adapter_install_write_failures.py:433-450; _cursor_install_helpers.py:128]`
13. The cross-part calls at the base, read in M2's module order (core, hooks-json, tools, sandbox, install, uninstall, check, then the entry's command line), form two cycles. Three backward edges close them, and the rest are forward edges. `[verified: Grep of each part's top-level names across the other parts, this session; cursor-adapter-install.py:7, :148-202, :169-170; cursor-adapter-uninstall.py:133, :141, :165, :207, :229, :262-263, :279-282; cursor-adapter-sandbox.py:10, :88, :204; cursor-adapter-check.py:147, :175, :180, :183, :213, :329; cursor-adapter-core.py:144, :332, :387; cursor-adapter-hooks-json.py:307; cursor-adapter.py:69, :109-116; the staff-backend-engineer review's symtable pass over the eight files found the same backward edges]`
   - The install and uninstall cycle:
     - install calls uninstall's `sweep_temp_debris`, a backward edge that moving `sweep_temp_debris` to core removes.
     - uninstall calls install's `pre_image_state`, `failing_file_text` and `post_write_failure`, a forward edge that stays.
   - The sandbox and uninstall cycle:
     - sandbox calls uninstall's `read_manifest` and `json_bytes_or_refusal`, two backward edges that moving both to core removes.
     - uninstall calls sandbox's `sandbox_restore_plan` and `apply_sandbox_action`, a forward edge that stays.
   - The acyclic edges:
     - install calls sandbox.
     - check calls install, uninstall and sandbox, and only the entry's command line calls check.
     - core, hooks-json and tools call no later part, apart from the entry's `adapter_directory`. core's one mention of a hooks-json name is a comment.
14. The three functions M2 moves read only core's `json_bytes`, `AdapterError`, `TEMP_FILE_PREFIX`, `read_json_object` and `MANIFEST_NAME`, and the standard library's `os`, `contextlib` and `stat`. `[verified: read cursor-adapter-uninstall.py:82-105, :318-323; cursor-adapter-core.py:43, :49, :264, :410, :423]`
15. No part rebinds a module-level name at run time, since none holds a `global` statement. Only tests rebind names. `[verified: Grep of the eight files]`
16. `adapter_directory()` is `dirname(realpath(__file__))` in the entry. Core, hooks-json, install, sandbox and check call it at six sites. `[verified: cursor-adapter.py:50-51; cursor-adapter-core.py:332, :387; cursor-adapter-hooks-json.py:307; cursor-adapter-install.py:7; cursor-adapter-sandbox.py:10; cursor-adapter-check.py:189]`
17. `load_adapter` executes the entry into a fresh `types.ModuleType` per call, with no `sys.modules` entry. `copy_adapter` and `load_adapter_copy_with` copy the whole adapter directory, leaving out `__pycache__`. `load_adapter_copy_with`'s 10 call sites replace `hook-shim.py`, `tool-map.json` or `parity-exceptions.json`, never a part. No production code loads `cursor-adapter.py` in-process, so only a test process can find a `cursor_adapter` package already registered when the entry runs. `[verified: _cursor_payloads.py:124-130, :628-639; _cursor_install_helpers.py:138-143; Grep of the call sites; Grep of cursor-adapter.py and cursor_adapter outside the test trees, this session]`
18. `install.sh` creates only `~/.claude` before stow runs, so stow links `~/.claude/scripts` as one directory into the checkout, and a new directory under `cursor-adapter/` is live on `git pull`. A layout that links the adapter's files one by one still reaches the package, because the entry resolves its own real path. `[verified: install.sh:139-143; cursor-adapter.py:51; the folding outcome is inferred from that comment, and stow was not run]`
19. This repo's one other package under `claude/.claude/scripts/` is `transcript_analysis`, whose entry imports `build_parser` and `main` from its `cli` module. Its modules read each other by module attribute. Its architecture doc ties the never-import-by-name rule to reassignable globals, and the package carries a test that every `<module>.<name>` read names a real binding. `[verified: Glob of __init__.py under claude/.claude/scripts, three hits, two of them test trees; transcript-analysis.py:7, :10; docs/transcript-analysis-architecture.md:14, :65-67; test_transcript_package_module_prefixes.py:1-13]`
20. ruff selects E, F, B, I, UP and SIM with a 130-character line, so per-file F821 and F401 and isort ordering apply to every module once the per-file-ignores row goes. `[verified: pyproject.toml:2, :6, :14-18]`
21. Test selection and rule scope for this diff: `[verified: select-tests.py:40-44, :286-295, :340-357, :547-555, :977; test_select_tests.py:2024-2031, :2445-2450, :2514; the rule's glob per this session's exploration]`
   - Any path under `claude/.claude/scripts/cursor-adapter/` selects the two reading hooks tests and the scripts tests.
   - `.claude/rules/cursor-adapter-parity.md` scopes itself to the same directory.
   - `pyproject.toml` and `select-tests.py` are global triggers, so `select-tests.py` selects the full suite for this diff.
   - Two registration rules govern `select-tests.py`'s lists:
     - A scripts test that imports `_cursor_payloads` must be in `SCRIPTS_TESTS_IMPORTING_HOOKS_TESTS_MODULES`.
     - Every listed path must exist.
22. `_cursor_payloads.py` is 969 lines, and the repo's code-file limit of 1,000 lines applies to test files as to code. `[verified: read _cursor_payloads.py:966-969; .claude/rules/transcript-analysis-tests.md "Size" names docs/design-decisions/code-file-line-limit.md as the limit's home]`
23. No existing test reads a part file by name or source except `test_cursor_adapter_parts.py`, and the unexpected-failure test pins a frame in its own file. `[verified: Grep of cursor-adapter- and ADAPTER_SOURCE in the test trees; test_cursor_adapter_install_write_failures.py:456]`
24. `_move_fidelity.py nodes` takes one `--before` file and repeatable `--after` files. It checks only the `--moved` names, requires each to be defined exactly once across the after files, and compares a node not listed in `--verbatim` by AST. Its `test-ids` subcommand takes repeatable `--before` and `--after` junit files and unions each side. `[verified: _move_fidelity.py:264-348, :883-885, :928-930]`
25. Reviews of this plan and its code use the production tier with a cooperative threat model. `[unverified: tier declared by the dispatching session, not quoted from the engineer]`
26. The change projects about 1,380 changed lines, and no seam splits it below 400. `[unverified: an estimate from the counts in rows 12, 14 and 30, the line ranges cited and the renamed test's planned rows; plan-review puts the 400-line question to the engineer]`
27. `hook-shim.py:33` says the command grammar in `cursor-adapter.py` must stay equal to the shim's vocabulary. The grammar is `render_command` and `parse_command` in `cursor-adapter-hooks-json.py`. The adapter's copy of the vocabulary is `SHIM_VALUE_FLAGS`, `SHIM_SWITCH_FLAGS` and `SHIM_MODES` in core, which a test ties to the shim's. `[verified: hook-shim.py:33-36; cursor-adapter-hooks-json.py:118-123, :190; cursor-adapter-core.py:144-148]`
28. The part files or `ADAPTER_PART_FILE_NAMES` are named at `docs/scripts.md:575` and `docs/cursor-adapter.md:113`, :157 and :272. `README.md:105` and `claude/.claude/hooks/_lib.sh:3695` name the program, which keeps its name. `[verified: Grep this session]`
29. In a test tree with an `__init__.py`, a same-directory sibling import is relative, and a test fails CI on a bare one. `[verified: .claude/rules/test-tree-packaging.md; claude/.claude/tests/test_pytest_collection_config.py:469-513]`
30. About 455 lines in the six non-core parts and the entry match a core function name or an upper-case constant shape. The shape also matches standard-library constants such as `os.O_RDONLY`, so the count runs high. `[verified: Grep count this session]`
31. What a mixed checkout reaches at run time: `[verified: Grep of the part file names outside the test trees, this session; cursor-adapter-hooks-json.py:127-128, :135-136, :289, :295-296, :338; cursor-adapter-check.py:619-624; test_cursor_adapter_shim.py:3104; docs/cursor-adapter.md:46, :147]`
   - Only the entry names a part file.
   - The one installed entry that runs `cursor-adapter.py` is sessionStart `check --hook-env`, rendered with the informational tail.
   - That guard prints the command's stdout only on exit 0 with non-empty output, and otherwise prints `{}` and exits 0. It sends stderr to `/dev/null`.
   - Every fail-closed entry runs `hook-shim.py`, which imports only the standard library.
   - `status` flags the sessionStart entry as silent once two or more shim first lines follow its last check line.
   - `docs/cursor-adapter.md` says the check exits 0 whatever it finds, and its Recovery section names a partial pull as a cause.
32. No name is bound by a module-level `def`, `class` or assignment in more than one of the entry and the seven parts at the base. No file holds a module-level compound statement apart from the entry's `if __name__ == "__main__":`. `[unverified: the staff-backend-engineer and staff-sdet reviews' AST scans of the base, not rerun here; Verification step 4 re-derives the first claim]`
33. Under `-I -S`, `sys.path` holds only the standard library's entries, and an absolute import searches `sys.path` alone. A standard-library-named file beside the entry or inside `cursor_adapter/` is therefore never imported, while the entry leaves `sys.path` alone and no relative import names a standard-library module. `[unverified: the CISO review's -I -S run on 3.15.0, not rerun here; the isolation rows pin it]`
34. `_imported_non_standard_names` counts each `import` and each level-0 `from` import, so it skips a relative import. The shim's isolation rows pair each flagged run with an unflagged control that does import the poison. `[verified: read test_cursor_adapter_shim.py:3002-3109]`
35. `fast_world` patches `run_self_check` on its adapter, and `load_adapter_copy_with` replaces `world.adapter` with a second load, so a test's `monkeypatch.undo()` can write through a namespace after a later load replaced its modules in `sys.modules`. `[verified: _cursor_install_helpers.py:128, :143; that one test uses both is per the staff-backend-engineer review, at test_cursor_adapter_install_unusable_inputs.py:637 and test_cursor_adapter_install_write_failures.py:482, not reread]`
36. CI's parallel leg runs pytest with `-m "not timing"`, and its timing leg runs `-m timing -n0` and then the Lint step, `ruff check` over the same trees. `select-tests.py` sizes `PYTEST_XDIST_AUTO_NUM_WORKERS` from the 1-minute load average and prints the count it picked on stderr. `[verified: .github/workflows/tests.yml:220, :223-225, :228-230; README.md:560-562; select-tests.py:1147]`

**Mechanisms:**
- M1: The seven parts move by `git mv` into a `cursor_adapter` package beside the entry, with a new `__init__.py` and a `cli.py` holding the entry's command-line code. `anchors: root, row1, row2, row16, row18, row19`.
- M2: By-name imports over an acyclic graph, made acyclic by three verbatim moves from uninstall into core. `anchors: row6, row13, row14, row15, row19, row20, row30`. The alternatives are under "Names across modules" above.
- M3: The entry finds the package in its own directory with `PathFinder.find_spec`, rejects an absent package or a namespace spec, registers it, and imports `cli` with bytecode writing off. Its isolation, fault and process-state rows pin the properties their own bullets state. `anchors: row7, row8, row9, row16, row17, row18, row31, row33, row34`. The lighter primitives, the current exec, `sys.path.append` and `spec_from_file_location`, fail as stated under "The entry".
- M4: `load_adapter` evicts the registered package before each load, and `AdapterNamespace`, which it returns, holds that load's modules, reads a name from the modules that bind it and writes it into each, so existing test bodies stay unchanged. `anchors: row10, row11, row12, row17, row22, row29, row32, row35`. The lighter primitives, a plain module and owner-addressed patches, fail as stated under "Tests read the package through one namespace".
- M5: The per-file-ignores row and the concatenated-source check go, plain ruff checks each module, and the uniqueness row replaces cross-file F811. `anchors: row6, row20, row32, row36`.
- M6: The FIFO finding, for module files and `__init__.py`, and the `hook-shim.py:33` pointer are fixed, and the snapshot finding stays deferred. `anchors: row4, row8, row27`.
- M7: Two doc edits, two config edits and three PR-body notes. `anchors: row3, row21, row28, row36`.
- M8: The proof: `_move_fidelity.py nodes --verbatim`, a line-multiset diff, the uniqueness scan at both trees, the rename and unchanged-test checks, the selected suite, a test-ID comparison split by the timing marker, and a guard-mutation pass over the guards Verification step 10 lists. `anchors: row12, row23, row24, row32, row36`.
- M9: One `code-writer` dispatch, without a split. `anchors: row12, row21, row26`. The reason is under Critical files.

## Critical files

**Dispatch: one `code-writer`, without a split.** The import blocks depend on the move, the namespace depends on the entry's `ADAPTER_PACKAGE_NAME`, and the existing patches pass only once production and the namespace both land, so neither half verifies alone. The renamed test's new rows exercise the entry, `load_adapter` and the namespace together, so they add no seam either. The brief requires:
- Verification step 1 first, so the base files sit in `<scratch>/base/` and the base tree exists.
- `git mv` for the seven parts and the renamed test, so history follows each file. No other staging and no commit.
- The three moved functions and `cli.py`'s two sections extracted by line range from the files in `<scratch>/base/` (for example with `sed -n`), never retyped and never cut from the working-tree files the same dispatch edits.
- Import blocks built by a scratch script under `<scratch>`, never committed. Per module, it takes ruff's F821 findings (`ruff check --isolated --select F821 --output-format json`) and maps each name to its defining module by the base's node list. Then `scripts/venv-run.sh ruff check --fix --select I,F401` with the repo's config orders and prunes the blocks.
- During development, `scripts/venv-run.sh pytest` on `claude/.claude/scripts/tests/test_cursor_adapter_*.py` and `claude/.claude/hooks/tests/test_cursor_adapter_*.py`. At the end, Verification steps 1-12, with their output in the return. Step 12 runs even when an earlier step fails.
- No edit to a file outside this list, and none to an existing test file other than `_cursor_payloads.py`.

**Rename and modify, production** (under `claude/.claude/scripts/cursor-adapter/`, mode 644): `cursor-adapter-core.py` to `cursor_adapter/core.py`, `cursor-adapter-hooks-json.py` to `cursor_adapter/hooks_json.py`, `cursor-adapter-tools.py` to `cursor_adapter/tools.py`, `cursor-adapter-install.py` to `cursor_adapter/install.py`, `cursor-adapter-sandbox.py` to `cursor_adapter/sandbox.py`, `cursor-adapter-uninstall.py` to `cursor_adapter/uninstall.py`, `cursor-adapter-check.py` to `cursor_adapter/check.py`.

**Create, production** (mode 644): `claude/.claude/scripts/cursor-adapter/cursor_adapter/__init__.py`, `claude/.claude/scripts/cursor-adapter/cursor_adapter/cli.py`.

**Modify, production:** `claude/.claude/scripts/cursor-adapter/cursor-adapter.py`; `claude/.claude/scripts/cursor-adapter/hook-shim.py` (:33 only).

**Create, tests:** `claude/.claude/hooks/tests/_cursor_adapter_namespace.py`.

**Modify, tests:** `claude/.claude/hooks/tests/_cursor_payloads.py` (`load_adapter`, its eviction loop included, and one import only).

**Rename and rewrite, tests:** `claude/.claude/scripts/tests/test_cursor_adapter_parts.py` to `claude/.claude/scripts/tests/test_cursor_adapter_package.py`.

**Modify, configuration:** `pyproject.toml` (delete :16-18); `claude/.claude/scripts/select-tests.py` (:354, and one entry in `HOOKS_TESTS_MODULES_IMPORTED_BY_SCRIPTS_TESTS`, :286-295).

**Modify, docs:** `docs/scripts.md` (:575 only); `docs/cursor-adapter.md` (:113, :157 and :272 only).

**Also in the diff:** `.claude/plans/cursor-adapter-package.md`.

**Read, not changed:** every other test file, `_cursor_install_helpers.py` and `test_select_tests.py` included; `tool-map.json`, `parity-exceptions.json` and `field-table.json`; `install.sh`; `stow-packages.sh`; `README.md`; `claude/.claude/hooks/_lib.sh`; `.claude/rules/cursor-adapter-parity.md`; `.claude/plans/cursor-adapter.md`; `claude/.claude/scripts/transcript-analysis.py`.

**Reuse:** `_move_fidelity.py nodes` and `test-ids`; `copy_adapter`, `ADAPTER_DIR`, `ADAPTER_SOURCE`, `IS_ROOT` and `SHIM_INTERPRETER_FLAGS` from `_cursor_payloads.py`; the renamed test's own `run_check_hook_env`; `load_adapter`'s exec of the entry; the entry-imports-`cli` shape of `transcript-analysis.py:7`; ruff's own F821 output for the import lists; the shim tests' canary writer, flagged-and-control pairing and import-scan rule (`test_cursor_adapter_shim.py:3002-3016`, :3030-3055), copied into the renamed test as DAMP test code. No new committed helper script.

**Diff estimate by part:**
- About 440 import lines across the eight code modules.
- About 70 for the three moved functions, out of uninstall and into core (28 definition lines plus their blank separators, each counted out and in).
- About 135 in `cursor-adapter.py`: about 100 out, about 35 in.
- About 95 for `cli.py`.
- About 545 for the test rename, counted as a delete of the 184-line file and an add of about 360, because the rewrite keeps under half its lines.
- About 55 for the namespace module.
- About 40 across the module docstrings, `__init__.py`, `_cursor_payloads.py`, `hook-shim.py`, `pyproject.toml`, `select-tests.py` and the two docs.

Projected diff: about 1,380 lines.

## Verification

Run from the worktree root. Scratch scripts live under `<scratch>` and are never committed. Two values are spelled out literally once step 1 prints them:
- `<scratch>` is the path one `mktemp -d` call prints, and every later command names that path literally.
- `<base>` is the SHA one `git merge-base HEAD origin/main` call prints, and every later command names that SHA literally. No call captures it with `$(...)` for reuse in the same call.

A base-tree pytest run is `<scratch>/base-tree/scripts/venv-run.sh pytest -c <scratch>/base-tree/pyproject.toml --rootdir <scratch>/base-tree`, with every test path given absolute under `<scratch>/base-tree`. Its header's `rootdir:` line names the base tree, so the cwd does not decide its root. If any step fails, step 12 still runs before the return.

1. Setup:
   - `mktemp -d` prints `<scratch>`, and `git merge-base HEAD origin/main` prints `<base>`.
   - `mkdir <scratch>/base`. For the entry and each of the seven part files, run `git show <base>:claude/.claude/scripts/cursor-adapter/<file> > <scratch>/base/<file>`. Then concatenate the entry and the parts, in `ADAPTER_PART_FILE_NAMES` order, into `<scratch>/base/combined.py`.
   - `git worktree add --detach <scratch>/base-tree <base>`.
2. Moved nodes. List `combined.py`'s named top-level nodes by `_move_fidelity.py`'s own rule (`_load_source(path).nodes`), less `ADAPTER_PART_FILE_NAMES`, `execute_adapter_parts` and `adapter_directory`. Run `scripts/venv-run.sh python3 claude/.claude/scripts/tests/_move_fidelity.py nodes --before <scratch>/base/combined.py` with one `--after` flag for `cursor-adapter.py` and one for each of the nine files in `cursor_adapter/`, then `--moved <names> --verbatim <names>`. It exits 0 and reports `compared-nodes:` equal to the name count, so every moved definition sits in exactly one file, byte for byte as at the base.
3. Every other moved line. Build the before lines from the seven base parts without their line-1 headers, plus the base entry's lines from `# --- generate ---` up to its `if __name__` line. Build the after lines from the nine package files. Drop blank lines from both and sort each with `LC_ALL=C sort`. `diff` of the two prints no `<` line, and its `>` lines are only import lines, docstrings, and `adapter_directory`'s comment, `def` line and body. This covers the section markers and the comments between nodes, which step 2 does not compare.
4. Uniqueness at both trees. A scratch script applies the uniqueness row's rule, each name a module-level `def`, `class` or assignment binds with dunders excluded, once to `<scratch>/base/`'s entry and seven parts and once to `cursor-adapter.py` and the nine package files. Neither run prints a name bound in two files (row 32).
5. `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/ plugins/ evals/` is clean, and `grep -n cursor-adapter pyproject.toml` prints nothing.
6. `wc -l` on `cursor-adapter.py`, the nine package files, `_cursor_adapter_namespace.py`, `_cursor_payloads.py` and `test_cursor_adapter_package.py`: every count is at most 1,000.
7. Renames and unchanged tests:
   - `git diff -M --stat HEAD -- claude/.claude/scripts` reports each of the seven parts as a rename.
   - `claude/.claude/scripts/tests/test_cursor_adapter_parts.py` no longer exists, and `claude/.claude/scripts/tests/test_cursor_adapter_package.py` does. git may report the pair as a delete and an add, since the rewrite keeps under half the file.
   - `git status --porcelain -- claude/.claude/hooks/tests claude/.claude/scripts/tests` lists only `_cursor_payloads.py`, `_cursor_adapter_namespace.py` and the two test paths, so no existing test body changed.
8. `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py` passes, apart from failures that also occur at the merge-base. It selects the full suite on its own (row 21).
   - Record the failing node IDs, then rerun each one on this tree and as a base-tree pytest run, per `CLAUDE.md`'s "Prove your change caused a failing check". A timing-marked ID runs as `-m timing -n0`. An ID that fails only on this tree blocks.
   - The suites that scan `git ls-files` cannot see the new untracked files (`__init__.py`, `cli.py` and `_cursor_adapter_namespace.py`) during this step. `/ready-for-review`'s run, after the parent stages them, re-confirms those suites.
   - The interpreter-flag runs among them: `check --hook-env` under `-I -S` at `test_cursor_adapter_install_session_start.py:470` and :724, the real self-check against a copied stow tree, `generate` under `-S` at `test_cursor_adapter_generate.py:51`, and the renamed test's subprocess rows.
9. Test IDs. In each tree, run `claude/.claude/scripts/tests/test_cursor_adapter_*.py` and `claude/.claude/hooks/tests/test_cursor_adapter_*.py` twice, as CI splits them (row 36), each run with `--junitxml` to its own file under `<scratch>`:
   - Once with `-m "not timing"`, under `PYTEST_XDIST_AUTO_NUM_WORKERS=<n>`, with `<n>` the worker count step 8's `select-tests.py` printed on stderr.
   - Once with `-m timing -n0`.
   - The base tree's two runs are base-tree pytest runs.
   - `_move_fidelity.py test-ids`, with one `--before` per base XML and one `--after` per XML here, reports only `test_cursor_adapter_parts.py`'s IDs leaving and `test_cursor_adapter_package.py`'s arriving. This also catches a pass that turned into a skip.
10. Guard mutations. Before the first mutation, copy every file the mutations below edit, production guard files and test files alike, once to `<scratch>/mutation/`. Each copy's name records the file's repo-relative path with `/` replaced by `__`, and no later step rewrites it. No mutation recopies a file, because `cursor-adapter.py` is mutated five times and a copy taken while an earlier mutation is unrestored would record mutated bytes as the baseline. For each guarded line below, make the one change, run the matching rows by node ID and confirm they fail, then copy the file back from its saved copy and confirm with `cmp`. Each failing run's node ID and the assertion's first line go in the return, for the PR body, with every absolute path (temp roots, scratch paths, home paths) replaced by a placeholder before publishing.
    - Eviction: delete `load_adapter`'s eviction loop. The checkout, copy, checkout row fails.
    - Bytecode flag: delete the entry's `sys.dont_write_bytecode = True`. The subprocess bytecode row fails, and so does the in-process one unless the test process already runs with bytecode writing off.
    - `sys.path` discipline: replace the entry's `PathFinder.find_spec` call with `sys.path.insert(0, adapter_dir)` and `importlib.util.find_spec(ADAPTER_PACKAGE_NAME)`. The `sys.path` row and the beside-the-entry `json.py` row fail.
    - `__init__.py` rejection: delete `or spec.origin is None`. The missing and FIFO `__init__.py` rows fail.
    - Package-directory rejection: delete the `spec is None` half of the entry's absent-package check, leaving the `spec.origin is None` half. The package-directory-removed row fails.
    - Bytecode flag restore: replace the restore in the entry's `finally` with `pass`. The `sys.dont_write_bytecode` row and the syntax-broken `sandbox.py` row fail.
    - Namespace write-through: make a write rebind only the first module that binds the name. The patch-reach row fails.
    - Held references: make the namespace look its modules up in `sys.modules` on each read and write. The earlier-namespaces row fails.
    - Namespace span: drop the last submodule from the modules `over_load` spans. The span row fails.
    - Uniqueness: append a copy of one core function's definition to `cursor_adapter/check.py`. The uniqueness row fails.
11. `find claude/.claude/scripts/cursor-adapter \( -name __pycache__ -o -name '*.pyc' \) -print` prints nothing after steps 8 to 10, whose in-process loads read the package from the checkout.
12. Closing, whether or not an earlier step failed, including step 10 stopping partway:
    - Each file step 10 names, the production guard files and the test files it edits, equals its copy in `<scratch>/mutation/` per `cmp`, whether or not step 10 reached that file's mutation. The result goes in the return.
    - A file that differs is copied back to its recorded path, compared with `cmp` again, and named in the return. A file that still differs after the copy-back is reported as unrestored.
    - `git worktree remove --force <scratch>/base-tree`. Forcing is safe here, because step 1 of this dispatch created that path.

## Out of scope

- **A snapshot load, the third deferred finding.** The import system reads each module as it imports it, as the exec loop read each part, so the package does not remove the cause (M6). Detecting a mixed checkout before any module runs would be new loader behaviour.
- **Retargeting `.claude/plans/cursor-adapter.md`'s Phase 5a Critical files bullets.** Phase 5a waits for this PR (row 3), and its session retargets them when it starts. The PR body says so. The retarget lives only in the PR body by design, so a later session must read the PR body.
- **Re-partitioning beyond M2's three cycle-breaking moves**, such as moving install's `step_reached_text`, `failing_file_text`, `post_write_failure` and `pre_image_state` into core, though uninstall calls them, or `LOG_ROTATE_BYTES` or `HOST_WALK_BOUND_SECONDS` beside their one caller. uninstall's calls into install are forward edges (row 13), and M4 makes a patch's reach independent of where a name lives.
- **Refusing a cached bytecode file.** `sys.dont_write_bytecode` stops writes, not reads, so a `__pycache__` file would be loaded if it is timestamp-based with a recorded source mtime, in whole seconds, and size matching the source's, or hash-based with the unchecked flag, which loads with no source comparison (row 9). That is accepted at the cooperative tier (row 25), because nothing in this repo writes bytecode for the adapter, so a stale file needs a writer outside this repo.
- **A name one module defines and another binds by a standard-library import.** The uniqueness row counts definitions only, so it does not rule this out. The namespace still raises AttributeError when a test reads or writes such a name.
- **Retargeting test reads or patches to owning modules.** M4's alternatives give the cost.
- **`hook-shim.py` beyond line 33**, including its split. `compile_module`, the loader that reads the shim, is defined in `cursor-adapter-core.py` and moves to `cursor_adapter/core.py` verbatim, so its behaviour does not change. Nothing in the package needs the shim changed.
- **A file swapped between the finder's `stat` and the loader's `open`.** It needs a hostile actor, outside the cooperative tier (row 25).
- **A design-decision file for the package.** `__init__.py` and `_cursor_adapter_namespace.py` state their constraints in their docstrings, and this plan, committed with the PR, records the alternatives.
- **New `test_select_tests.py` rows** for a path under the package or for `_cursor_adapter_namespace.py`. `_is_under` already matches nested paths, and the renamed test is already a declared importer (row 21).
