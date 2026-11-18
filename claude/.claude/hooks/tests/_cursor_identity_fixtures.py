"""Process-tree, `ps` stub, PID-grammar and environment-scrub fixtures for the Cursor identity tests.

Shared by the hooks and scripts test trees.

A non-test module, like _cursor_payloads.py. It holds:
- a fake process-tree builder, whose nearest ancestor takes the test process's own PID, because a bash child
  started by the test reads that PID as `$PPID`;
- a `ps` stub that answers from such a tree, and can fail, garble or cap-kill the one request a row names;
- the PID-grammar edge table that every consumer of the host-PID grammar drives;
- the adapter-environment scrub both conftests apply, and the nested pytest run that tests it.

The stub answers three request forms and fails loudly on any other. It logs each request with its designed answer,
so a row asserts the exact request sequence it expects (see PsStub.requests):
- the name read `ps -o comm= -p <pid>`, which is also the host walk's liveness test;
- the PPID read `ps -o ppid= -p <pid>`;
- the start-time read `ps -o lstart= -p <pid>`, which `_lib_registered_live_sid` makes.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import REPO_ROOT

# Every fake ancestor reports this start time, so a registry entry seeded with it validates.
FAKE_LIVE_START_TIME = "Tue Feb  2 03:04:05 2021"

# Fake ancestors other than the nearest take PIDs from here up.
# The stub answers for them, so a real process holding one is harmless.
FAKE_PID_BASE = 900_000

# Executable-name forms `ps -o comm=` reports, as placeholders that carry the real forms' shapes.
MACOS_CURSOR_MAIN_PATH = "/Applications/Cursor.app/Contents/MacOS/Cursor"
MACOS_CURSOR_HELPER_PATH = (
    "/Applications/Cursor.app/Contents/Frameworks/Cursor Helper (Plugin).app/Contents/MacOS/Cursor Helper (Plugin)"
)
RETITLED_HELPER_TITLE = "Cursor Helper (Plugin): extension-host (user) workspace [1-1]"
CLI_INVOKED_PATH = "/home/placeholder-user/.local/bin/agent"
CLI_WORKER_GLOBAL_STORAGE_PATH = (
    "/Users/placeholder-user/Library/Application Support/Cursor/User/globalStorage/"
    "anysphere.cursor-agent/versions/1.0.0/cursor-agent"
)

# Names that must never match a host or `claude`.
HOST_NAME_LOOKALIKES = (
    "Cursor.bak",
    "cursor",
    "/x/Cursor/bin/node",
    "agent.bak",
    "Agent",
    "ssh-agent",
    "/x/agent/bin/node",
    "node",
)

# What a stand-in `ps` answers when it shows a `Cursor` host above every caller.
# Any pid but the host's has `bash` as its name and the host as its parent.
# The host's parent is pid 1, which ends a walk that goes past the host.
# It serves a walk that another process starts, which the pid-keyed tree stub below cannot.
HOST_PROCESS_ID = "424242"
HOST_SHOWING_PS_BODY = (
    'case "$2" in\n'
    f'  comm=) if [ "$4" = {HOST_PROCESS_ID} ]; then echo {MACOS_CURSOR_MAIN_PATH}; else echo bash; fi ;;\n'
    f'  ppid=) if [ "$4" = {HOST_PROCESS_ID} ]; then echo 1; else echo {HOST_PROCESS_ID}; fi ;;\n'
    "esac\n"
)


def write_stub_tool_directory(base: Path, directory_name: str, tool_name: str, body: str) -> Path:
    """A new `base/directory_name` holding an executable POSIX `sh` stand-in named `tool_name`. Returns the directory."""
    directory = base / directory_name
    directory.mkdir()
    stand_in = directory / tool_name
    stand_in.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    stand_in.chmod(0o755)
    return directory


# Request kinds the stub knows, in `ps -o <kind>=` form.
PS_REQUEST_KINDS = ("comm", "ppid", "lstart")

# Real `ps -o ppid=` right-aligns its number in a fixed-width column, so a PPID read carries leading spaces.
# The stub pads to this width, and a width of 0 prints the bare number.
DEFAULT_PPID_PAD_WIDTH = 7

_FIELD_SEPARATOR = "\x1f"


@dataclass(frozen=True)
class FakeProcess:
    pid: int
    comm: str
    ppid_output: str


@dataclass(frozen=True)
class FakeProcessTree:
    """Ancestors of the process the test starts, nearest first. `processes[0].pid` is that process's `$PPID`."""

    processes: tuple[FakeProcess, ...]

    @property
    def nearest_pid(self) -> int:
        return self.processes[0].pid

    def pid_at_depth(self, depth: int) -> int:
        """The PID `depth` hops above the nearest ancestor, which sits at depth 0."""
        return self.processes[depth].pid


def build_fake_process_tree(
    names_from_top: tuple[str, ...] | list[str],
    *,
    top_ppid_output: str = "0",
    nearest_pid: int | None = None,
) -> FakeProcessTree:
    """Builds a tree from `comm` strings listed outermost first. The last name is the caller's `$PPID`.

    `top_ppid_output` is what the outermost process's PPID read prints: `0`, `1` or an empty string end the
    walk without a failure.
    """
    assert names_from_top, "a fake tree needs at least one ancestor"
    nearest = os.getpid() if nearest_pid is None else nearest_pid
    count = len(names_from_top)
    pids = [FAKE_PID_BASE + index for index in range(count)]
    pids[-1] = nearest
    processes = []
    for depth in range(count):
        index_from_top = count - 1 - depth
        outer_pid = pids[index_from_top - 1] if index_from_top > 0 else None
        processes.append(
            FakeProcess(
                pid=pids[index_from_top],
                comm=names_from_top[index_from_top],
                ppid_output=str(outer_pid) if outer_pid is not None else top_ppid_output,
            )
        )
    return FakeProcessTree(processes=tuple(processes))


@dataclass(frozen=True)
class PsRequestRule:
    """Alters the answer to every request matching `kind` and `pid`, where `*` matches any.

    `mode` is `fail` (exit 1, no output), `output` (exit 0, printing `output`) or `cap-kill` (a stub `timeout`
    exits 124 without running `ps`, as a cap kill does).
    """

    kind: str
    pid: int | str
    mode: str
    output: str = ""

    def __post_init__(self) -> None:
        assert self.kind == "*" or self.kind in PS_REQUEST_KINDS, self.kind
        assert self.mode in ("fail", "output", "cap-kill"), self.mode


_PS_STUB_SOURCE = """#!/bin/bash
# Fake ps. Known requests: ps -o comm= -p PID, ps -o ppid= -p PID, ps -o lstart= -p PID.
tree_file=%(tree_file)s
rules_file=%(rules_file)s
log_file=%(log_file)s
ppid_pad_width=%(ppid_pad_width)d
separator=$'\\x1f'
log_request() { printf '%%s\\t%%s\\t%%s\\n' "$1" "$2" "$3" >> "$log_file"; }
if [ "$#" -ne 4 ] || [ "$1" != "-o" ] || [ "$3" != "-p" ]; then
  log_request unknown "$*" -
  exit 99
fi
case "$2" in
  comm=) kind=comm ;;
  ppid=) kind=ppid ;;
  lstart=) kind=lstart ;;
  *) log_request unknown "$*" -; exit 99 ;;
esac
pid="$4"
while IFS="$separator" read -r rule_kind rule_pid rule_mode rule_output; do
  [ "$rule_mode" = "cap-kill" ] && continue
  if { [ "$rule_kind" = "*" ] || [ "$rule_kind" = "$kind" ]; } && { [ "$rule_pid" = "*" ] || [ "$rule_pid" = "$pid" ]; }; then
    case "$rule_mode" in
      fail) log_request "$kind" "$pid" fail; exit 1 ;;
      output) log_request "$kind" "$pid" output; printf '%%s\\n' "$rule_output"; exit 0 ;;
    esac
  fi
done < "$rules_file"
while IFS="$separator" read -r tree_pid tree_ppid tree_comm tree_lstart; do
  if [ "$tree_pid" = "$pid" ]; then
    case "$kind" in
      comm) printf '%%s\\n' "$tree_comm" ;;
      ppid) printf '%%*s\\n' "$ppid_pad_width" "$tree_ppid" ;;
      lstart) printf '%%s\\n' "$tree_lstart" ;;
    esac
    log_request "$kind" "$pid" ok
    exit 0
  fi
done < "$tree_file"
log_request "$kind" "$pid" absent
exit 1
"""

_TIMEOUT_STUB_SOURCE = """#!/bin/bash
# Fake timeout. Exits 124 for a `timeout -k 2 N ps -o KIND= -p PID` request that a cap-kill rule names, and
# otherwise runs the real timeout.
rules_file=%(rules_file)s
log_file=%(log_file)s
separator=$'\\x1f'
if [ "$#" -eq 8 ] && [ "$4" = "ps" ] && [ "$5" = "-o" ] && [ "$7" = "-p" ]; then
  kind="${6%%=}"
  pid="$8"
  while IFS="$separator" read -r rule_kind rule_pid rule_mode rule_output; do
    [ "$rule_mode" = "cap-kill" ] || continue
    if { [ "$rule_kind" = "*" ] || [ "$rule_kind" = "$kind" ]; } && { [ "$rule_pid" = "*" ] || [ "$rule_pid" = "$pid" ]; }; then
      printf '%%s\\t%%s\\t%%s\\n' "$kind" "$pid" cap-kill >> "$log_file"
      exit 124
    fi
  done < "$rules_file"
fi
exec %(real_timeout)s "$@"
"""


@dataclass(frozen=True)
class PsStub:
    env: dict[str, str]
    log_path: Path
    tree: FakeProcessTree

    def requests(self) -> list[tuple[str, str, str]]:
        """Every request the stub saw, as (kind, pid, answer) in order. `answer` is `ok`, `fail`, `output`,
        `cap-kill` or `absent`. An unknown request form is (`unknown`, its arguments, `-`)."""
        if not self.log_path.exists():
            return []
        return [tuple(line.split("\t")) for line in self.log_path.read_text().splitlines()]  # type: ignore[misc]

    def assert_no_unknown_requests(self) -> None:
        unknown = [request for request in self.requests() if request[0] == "unknown"]
        assert not unknown, f"the ps stub saw request forms it does not know: {unknown}"

    def clear_rules(self) -> None:
        """Makes every later request answer from the tree. The stubs read their rules file on each request."""
        (self.log_path.parent / "rules.txt").write_text("")


def install_ps_stub(
    directory: Path,
    tree: FakeProcessTree,
    rules: tuple[PsRequestRule, ...] | list[PsRequestRule] = (),
    ppid_pad_width: int = DEFAULT_PPID_PAD_WIDTH,
) -> PsStub:
    """Writes a `ps` stub, and a `timeout` stub when a rule is `cap-kill`, into a new `directory/ps-stub-bin`.

    The returned env holds the PATH that puts them first. `directory` must not hold a `ps-stub-bin` already.
    A tree's PPID answers are left-padded to `ppid_pad_width`. A rule's `output` is printed as given.
    """
    bin_dir = directory / "ps-stub-bin"
    bin_dir.mkdir()
    tree_file = bin_dir / "tree.txt"
    rules_file = bin_dir / "rules.txt"
    log_file = bin_dir / "requests.log"
    tree_file.write_text(
        "".join(
            _FIELD_SEPARATOR.join((str(p.pid), p.ppid_output, p.comm, FAKE_LIVE_START_TIME)) + "\n"
            for p in tree.processes
        )
    )
    rules_file.write_text(
        "".join(
            _FIELD_SEPARATOR.join((rule.kind, str(rule.pid), rule.mode, rule.output)) + "\n" for rule in rules
        )
    )
    ps_stub = bin_dir / "ps"
    ps_stub.write_text(
        _PS_STUB_SOURCE
        % {
            "tree_file": shlex.quote(str(tree_file)),
            "rules_file": shlex.quote(str(rules_file)),
            "log_file": shlex.quote(str(log_file)),
            "ppid_pad_width": ppid_pad_width,
        }
    )
    ps_stub.chmod(0o755)
    if any(rule.mode == "cap-kill" for rule in rules):
        real_timeout = shutil.which("timeout") or shutil.which("gtimeout")
        if real_timeout is None:
            pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
        timeout_stub = bin_dir / "timeout"
        timeout_stub.write_text(
            _TIMEOUT_STUB_SOURCE
            % {
                "rules_file": shlex.quote(str(rules_file)),
                "log_file": shlex.quote(str(log_file)),
                "real_timeout": shlex.quote(real_timeout),
            }
        )
        timeout_stub.chmod(0o755)
    return PsStub(env={"PATH": f"{bin_dir}:{os.environ['PATH']}"}, log_path=log_file, tree=tree)


@dataclass(frozen=True)
class PidGrammarRow:
    """One row of the PID-grammar edge table.

    `value` is the whole `CLAUDE_CONFIG_ADAPTER_HOST_PID` value that `_lib_adapter_session_pair` (`_lib.sh`) reads.
    It is also the text that `host_walk_pid` (`hook-shim.py`) takes from the walk's output once one trailing newline is off.
    `recognize_pin_prefix` (`hook-shim.py`), which `pin-check` mode and the pin strip both use, reads a value as the text
    between `=` and the line's end, so a row with `applies_to_line_forms` False has no such form.
    """

    row_id: str
    value: str
    accepted: bool
    applies_to_line_forms: bool = True


PID_GRAMMAR_EDGE_TABLE = (
    PidGrammarRow("accepted-two", "2", True),
    PidGrammarRow("accepted-ten-digits", "9999999999", True),
    PidGrammarRow("empty", "", False),
    PidGrammarRow("zero", "0", False),
    PidGrammarRow("one", "1", False),
    PidGrammarRow("leading-zero", "0123", False),
    PidGrammarRow("eleven-digits", "10000000000", False),
    PidGrammarRow("twenty-digits", "12345678901234567890", False),
    PidGrammarRow("negative", "-5", False),
    PidGrammarRow("plus-sign", "+5", False),
    PidGrammarRow("underscore", "1_0", False),
    PidGrammarRow("trailing-letter", "12a", False),
    PidGrammarRow("inner-space", "12 34", False),
    PidGrammarRow("leading-space", " 12", False),
    PidGrammarRow("trailing-space", "12 ", False),
    PidGrammarRow("single-trailing-newline", "12\n", False, applies_to_line_forms=False),
    PidGrammarRow("two-lines", "12\n34", False),
    PidGrammarRow("arabic-indic-digits", "١٢", False),
    PidGrammarRow("command-substitution", "$(id)", False),
)

REJECTED_PID_GRAMMAR_ROWS = tuple(row for row in PID_GRAMMAR_EDGE_TABLE if not row.accepted)


# --- The adapter-environment scrub -------------------------------------------------------------------------

ADAPTER_ENVIRONMENT_PREFIX = "CLAUDE_CONFIG_ADAPTER_"

# What the nested run starts with. The last name is invented, so a scrub that names variables one by one misses it.
LEAKED_ADAPTER_ENVIRONMENT = {
    f"{ADAPTER_ENVIRONMENT_PREFIX}SESSION_ID": "leaked-adapter-session-id",
    f"{ADAPTER_ENVIRONMENT_PREFIX}HOST_PID": "424242",
    f"{ADAPTER_ENVIRONMENT_PREFIX}INVENTED_VARIABLE": "leaked-invented-value",
}


def clear_adapter_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes every variable named `CLAUDE_CONFIG_ADAPTER_*` for the test, by prefix."""
    for name in [name for name in os.environ if name.startswith(ADAPTER_ENVIRONMENT_PREFIX)]:
        monkeypatch.delenv(name)


def run_nested_probe(
    probe_file: Path, probe_class_name: str, report_path: Path, *extra_arguments: str
) -> subprocess.CompletedProcess:
    """Runs `probe_class_name` from `probe_file` in a nested pytest run that starts with LEAKED_ADAPTER_ENVIRONMENT
    set, and writes its JUnit report to `report_path`. The class is collected only here, because its name does not
    match the default `Test*` pattern."""
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", f"{probe_file}::{probe_class_name}",
            "-o", f"python_classes={probe_class_name}",
            "-c", str(REPO_ROOT / "pyproject.toml"), "--rootdir", str(REPO_ROOT),
            "-n0", "-p", "no:cacheprovider", f"--junitxml={report_path}", "-q",
            *extra_arguments,
        ],  # fmt: skip
        cwd=REPO_ROOT,
        env={**os.environ, **LEAKED_ADAPTER_ENVIRONMENT},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def only_probe_case(report_path: Path) -> ElementTree.Element:
    cases = list(ElementTree.parse(report_path).getroot().iter("testcase"))
    assert len(cases) == 1, [case.attrib for case in cases]
    return cases[0]
