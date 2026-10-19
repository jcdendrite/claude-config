"""Tests for resume-context-same-session.sh.

Every run builds its environment from scratch: HOME, CLAUDE_CONFIG_DIR,
RESUME_CONTEXT_TMPDIR and TMPDIR all sit under tmp_path, so no test touches the
real config dir or the real shared /tmp. RESUME_CONTEXT_CONSUMER is the
documented seam for stubbing the consume step. The git repository and its
worktrees are built once per module; the script only reads them.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    SKILLS_DIR,
    _make_git_exiting_with_status,
    continuity_resume_section,
    init_git_repo_with_commit,
    read_input,
    write_continuity_body,
    write_input,
)

from .conftest import _make_worktree

_SCRIPT = SCRIPTS_DIR / "resume-context-same-session.sh"
_REAL_CONSUMER = SCRIPTS_DIR / "resume-context.sh"
_FIND_CONSUMED_SCRIPT = SCRIPTS_DIR / "find-consumed-continuity-file.sh"
_ANNOUNCE_HOOK = HOOKS_DIR / "announce-resume-command.sh"
_CONSUME_HOOK = HOOKS_DIR / "consume-durable-continuity-file-on-read.sh"
_LIB_SH = HOOKS_DIR / "_lib.sh"
_SETTINGS_PATH = HOOKS_DIR.parent / "settings.json"
_SKILL_MD = SKILLS_DIR / "resume-context" / "SKILL.md"

# The single list of stdout keys the script emits. This module ties it to the
# script's header contract and to the skill body.
_EMITTED_KEYS = (
    "LOAD",
    "SOURCE",
    "AGE",
    "SESSION_TARGET",
    "FILE_TARGET",
    "SHELL_COMMAND",
    "CANDIDATE",
    "MORE",
    "SKIPPED_UNPRINTABLE",
    "STOP_REASON",
)
_NON_LOAD_TOKEN_KEYS = ("STOP_REASON", "CANDIDATE", "SHELL_COMMAND")
_SECONDS_PER_DAY = 86400
# Ages well inside each bucket: `<1d`, `1-7d` and `>7d`.
_AGE_FRESH_DAYS = 2 / 24
_AGE_MIDDLE_DAYS = 3
_AGE_STALE_DAYS = 10
_UNREADABLE_TARGET = "unknown"
_PENDING_FILES_IN_PARSE_COUNT_TEST = 12

# More bytes than the scanned tail of _lib_continuity_file_resume_cwd (8192).
_FILLER_LONGER_THAN_THE_SCANNED_TAIL = "x " * 5000 + "\n"

# Resume sections whose --cwd the script cannot use. {file} is the pending file's own path.
_UNREADABLE_CWD_BODIES = {
    "tilde": "## §7 Resume command\n`resume-context --cwd ~/some-worktree {file}`\n",
    "relative-parent": "## §7 Resume command\n`resume-context --cwd ../some-worktree {file}`\n",
    "dot": "## §7 Resume command\n`resume-context --cwd . {file}`\n",
    "equals-form": "## §7 Resume command\n`resume-context --cwd=/work/a {file}`\n",
    "quoted": '## §7 Resume command\n`resume-context --cwd "/work/a" {file}`\n',
    "flag-without-token": "## §7 Resume command\n`resume-context {file} --cwd`\n",
    "two-disagreeing-commands": (
        "## §7 Resume command\n`resume-context --cwd /work/a {file}`\n`resume-context --cwd /work/b {file}`\n"
    ),
    "markdown-bold-decoration": "## §7 Resume command\n**resume-context --cwd /work/a {file}**\n",
    "unquoted-space-in-the-directory": "## §7 Resume command\n`resume-context --cwd /work/my tree {file}`\n",
    "cwd-command-before-the-tail-and-bare-line-in-it": (
        "`resume-context --cwd /work/a {file}`\n"
        + _FILLER_LONGER_THAN_THE_SCANNED_TAIL
        + "## §7 Resume command\n`resume-context {file}`\n"
    ),
}


@dataclass
class _Outcome:
    pairs: list[tuple[str, str]]
    stderr: str

    def values(self, key: str) -> list[str]:
        return [value for pair_key, value in self.pairs if pair_key == key]

    def first(self, key: str) -> str:
        found = self.values(key)
        assert found, f"no {key} line in {self.pairs}"
        return found[0]

    @property
    def stop_code(self) -> str:
        return self.first("STOP_REASON").split(":", 1)[0]

    @property
    def candidate_names(self) -> list[str]:
        return [value.split(" | ", 1)[0] for value in self.values("CANDIDATE")]

    def has(self, key: str) -> bool:
        return bool(self.values(key))


def _parse_stdout(stdout: str) -> list[tuple[str, str]]:
    pairs = []
    for line in stdout.splitlines():
        key, separator, value = line.partition(": ")
        assert separator and key in _EMITTED_KEYS, f"stdout line outside the token contract: {line!r}"
        pairs.append((key, value))
    return pairs


def _set_age_days(path: Path, days: float) -> None:
    moment = time.time() - days * _SECONDS_PER_DAY
    os.utime(path, (moment, moment))


def _new_linked_worktree(repo: Path, worktree: Path) -> Path:
    branch = f"branch-{worktree.name.replace(' ', '-')}"
    subprocess.run(["git", "branch", branch], cwd=repo, check=True)
    _make_worktree(repo, branch, worktree)
    return worktree


@dataclass(frozen=True)
class _GitTrees:
    main_checkout: Path
    worktree_a: Path
    worktree_b: Path
    nested_in_worktree_a: Path


@pytest.fixture(scope="module")
def git_trees(tmp_path_factory: pytest.TempPathFactory) -> _GitTrees:
    base = tmp_path_factory.mktemp("git-trees")
    main_checkout = init_git_repo_with_commit(base / "main-checkout", branch="main")
    worktree_a = _new_linked_worktree(main_checkout, base / "worktree-a")
    nested = worktree_a / "nested"
    nested.mkdir()
    return _GitTrees(
        main_checkout=main_checkout,
        worktree_a=worktree_a,
        worktree_b=_new_linked_worktree(main_checkout, base / "worktree-b"),
        nested_in_worktree_a=nested,
    )


class _Sandbox:
    def __init__(self, tmp_path: Path, trees: _GitTrees) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.config_dir = tmp_path / "config"
        self.handoffs = self.config_dir / "handoffs"
        self.briefs = self.config_dir / "briefs"
        self.tmpdir_root = tmp_path / "tmp-root"
        for directory in (self.home, self.config_dir):
            directory.mkdir()
        # _lib_resume_context_index_dir refuses a group- or world-writable root without the sticky bit.
        # So the mode is set explicitly rather than inherited from the umask.
        self.tmpdir_root.mkdir(mode=0o700)
        assert stat.S_IMODE(self.tmpdir_root.stat().st_mode) == 0o700, "the umask altered the temp root's mode"
        self.main_checkout = trees.main_checkout
        self.worktree_a = trees.worktree_a
        self.worktree_b = trees.worktree_b
        self.nested_in_worktree_a = trees.nested_in_worktree_a

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "RESUME_CONTEXT_TMPDIR": str(self.tmpdir_root),
            "TMPDIR": str(self.tmpdir_root),
        }
        env.update(extra or {})
        return env

    def run(
        self, *args: str, cwd: Path | None = None, env_extra: dict[str, str] | None = None
    ) -> _Outcome:
        result = subprocess.run(
            ["bash", str(_SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=cwd or self.main_checkout,
            env=self.env(env_extra),
            check=False,
        )
        pairs = _parse_stdout(result.stdout)
        outcome = _Outcome(pairs, result.stderr)
        assert result.returncode == 0, f"exit {result.returncode} before a verdict was printed: {pairs}"
        printed_non_load_token = any(outcome.has(key) for key in _NON_LOAD_TOKEN_KEYS)
        assert outcome.has("LOAD") != printed_non_load_token, (
            f"a run prints either LOAD or one of {_NON_LOAD_TOKEN_KEYS}, never both or neither: {pairs}"
        )
        return outcome

    def write_pending(
        self,
        slug: str,
        resume_cwd: Path | str | None = None,
        *,
        kind: str = "handoff",
        age_days: float | None = None,
    ) -> Path:
        directory, suffix = (self.handoffs, "handoff") if kind == "handoff" else (self.briefs, "task")
        return self.write_named(directory / f"{slug}-{suffix}.md", resume_cwd, kind=kind, age_days=age_days)

    def write_named(
        self, path: Path, resume_cwd: Path | str | None, *, kind: str = "handoff", age_days: float | None = None
    ) -> Path:
        return self.write_body(path, continuity_resume_section(kind, path, resume_cwd), age_days=age_days)

    def write_body(self, path: Path, body: str, *, age_days: float | None = None) -> Path:
        """Writes `body` with {file} replaced by the file's own path."""
        write_continuity_body(path, body)
        if age_days is not None:
            _set_age_days(path, age_days)
        return path

    def moved_copies(self) -> list[Path]:
        return sorted(self.tmpdir_root.glob("resume-context.*"))

    def assert_nothing_moved(self, *pending: Path) -> None:
        assert self.moved_copies() == []
        for path in pending:
            assert path.exists(), f"{path} should still be pending"


@pytest.fixture
def sandbox(tmp_path: Path, git_trees: _GitTrees) -> _Sandbox:
    return _Sandbox(tmp_path, git_trees)


def _write_consumer_stub(tmp_path: Path, body: str) -> Path:
    stub = tmp_path / "consumer-stub.sh"
    stub.write_text(f"#!/bin/bash\n{body}\n")
    stub.chmod(0o755)
    return stub


def _write_path_shim(bin_dir: Path, command: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / command
    shim.write_text(f"#!/bin/bash\n{body}\n")
    shim.chmod(0o755)


class TestNoArgument:
    def test_loads_the_unique_fresh_target_match_and_leaves_decoys(self, sandbox: _Sandbox) -> None:
        mine = sandbox.write_pending("mine", sandbox.worktree_a)
        other_worktree = sandbox.write_pending("other-worktree", sandbox.worktree_b)
        no_cwd = sandbox.write_pending("no-cwd")
        other_brief = sandbox.write_pending("brief-elsewhere", sandbox.worktree_b, kind="brief")

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("SOURCE") == str(mine)
        assert outcome.first("AGE") == "<1d"
        assert outcome.first("SESSION_TARGET") == str(sandbox.worktree_a.resolve())
        assert outcome.first("FILE_TARGET") == str(sandbox.worktree_a.resolve())
        loaded = Path(outcome.first("LOAD"))
        assert loaded.parent == sandbox.tmpdir_root and loaded.is_file()
        assert not mine.exists()
        assert other_worktree.exists() and no_cwd.exists() and other_brief.exists()
        assert "reload with: claude --append-system-prompt-file" in outcome.stderr

    def test_the_loaded_destination_is_owner_only_and_the_consume_is_in_the_production_index(
        self, sandbox: _Sandbox
    ) -> None:
        mine = sandbox.write_pending("mine", sandbox.worktree_a)

        loaded = Path(sandbox.run(cwd=sandbox.worktree_a).first("LOAD"))

        destination_stat = loaded.stat()
        assert stat.S_IMODE(destination_stat.st_mode) == 0o600
        assert destination_stat.st_uid == os.getuid()
        lookup = subprocess.run(
            ["bash", str(_FIND_CONSUMED_SCRIPT), "mine"],
            capture_output=True,
            text=True,
            env=sandbox.env(),
            check=False,
        )
        assert lookup.returncode == 0, lookup.stderr
        lookup_columns = lookup.stdout.strip().split("\t")
        assert str(loaded) in lookup_columns
        assert str(mine) in lookup_columns

    def test_several_fresh_matches_list_candidates_and_move_nothing(self, sandbox: _Sandbox) -> None:
        first = sandbox.write_pending("first", sandbox.worktree_a)
        second = sandbox.write_pending("second", sandbox.worktree_a)

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert sorted(outcome.candidate_names) == [first.name, second.name]
        sandbox.assert_nothing_moved(first, second)

    def test_a_stale_match_alone_is_listed_not_loaded(self, sandbox: _Sandbox) -> None:
        stale = sandbox.write_pending("stale", sandbox.worktree_a, age_days=_AGE_STALE_DAYS)

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.candidate_names == [stale.name]
        assert ">7d" in outcome.first("CANDIDATE")
        sandbox.assert_nothing_moved(stale)

    @pytest.mark.parametrize(
        ("age_days", "loads"),
        [(6.5, True), (7.5, False)],
        ids=["six-and-a-half-days-loads", "seven-and-a-half-days-is-listed"],
    )
    def test_the_seven_day_freshness_bound_decides_whether_a_sole_match_auto_loads(
        self, sandbox: _Sandbox, age_days: float, loads: bool
    ) -> None:
        sole_match = sandbox.write_pending("sole-match", sandbox.worktree_a, age_days=age_days)

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        if loads:
            assert outcome.has("LOAD")
            assert outcome.first("SOURCE") == str(sole_match)
        else:
            assert not outcome.has("LOAD")
            assert outcome.candidate_names == [sole_match.name]
            sandbox.assert_nothing_moved(sole_match)

    def test_a_fresh_match_loads_even_with_a_stale_match_for_the_same_worktree(self, sandbox: _Sandbox) -> None:
        stale = sandbox.write_pending("stale", sandbox.worktree_a, age_days=_AGE_STALE_DAYS)
        fresh = sandbox.write_pending("fresh", sandbox.worktree_a)

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("SOURCE") == str(fresh)
        assert stale.exists()

    def test_no_match_prints_session_target_and_lists_the_others(self, sandbox: _Sandbox) -> None:
        elsewhere = sandbox.write_pending("elsewhere", sandbox.worktree_b)

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.first("SESSION_TARGET") == str(sandbox.worktree_a.resolve())
        assert outcome.candidate_names == [elsewhere.name]
        sandbox.assert_nothing_moved(elsewhere)

    def test_past_the_cap_target_matches_come_first_then_the_newest_others_and_more_counts_the_rest(
        self, sandbox: _Sandbox
    ) -> None:
        target_older = sandbox.write_pending("target-older", sandbox.worktree_a, age_days=0.5)
        target_newer = sandbox.write_pending("target-newer", sandbox.worktree_a, age_days=0.1)
        others = [
            sandbox.write_pending(f"other-{slug}", sandbox.worktree_b, age_days=0.2 + index * 0.1)
            for index, slug in enumerate(["e", "d", "c", "b", "a"])
        ]

        outcome = sandbox.run(cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.candidate_names == [target_newer.name, target_older.name, others[0].name, others[1].name, others[2].name]
        assert outcome.first("MORE") == "2"

    def test_main_checkout_with_one_file_naming_no_cwd_lists_it_and_loads_nothing(self, sandbox: _Sandbox) -> None:
        only = sandbox.write_pending("only")

        outcome = sandbox.run(cwd=sandbox.main_checkout)

        assert not outcome.has("LOAD")
        assert outcome.candidate_names == [only.name]
        assert outcome.first("SESSION_TARGET") == "none"
        sandbox.assert_nothing_moved(only)

    @pytest.mark.parametrize("present", [(), ("handoffs",), ("briefs",)], ids=["both-missing", "only-handoffs", "only-briefs"])
    def test_nothing_pending_stops_with_nothing_pending(self, sandbox: _Sandbox, present: tuple[str, ...]) -> None:
        for name in present:
            (sandbox.config_dir / name).mkdir()

        outcome = sandbox.run()

        assert not outcome.has("LOAD")
        assert outcome.stop_code == "nothing-pending"
        assert "find-consumed-continuity-file.sh" in outcome.first("STOP_REASON")

    def test_all_pending_names_unprintable_gives_nothing_pending_beside_skipped_count(
        self, sandbox: _Sandbox
    ) -> None:
        sandbox.handoffs.mkdir()
        (sandbox.handoffs / "has space-handoff.md").write_text("x\n")

        outcome = sandbox.run()

        assert outcome.stop_code == "nothing-pending"
        assert outcome.first("SKIPPED_UNPRINTABLE") == "1"
        assert "has a name inside the printable allowlist" in outcome.first("STOP_REASON")

    @pytest.mark.parametrize(
        ("session_directory", "expected_parses"),
        [("worktree_a", _PENDING_FILES_IN_PARSE_COUNT_TEST), ("main_checkout", 5)],
        ids=["linked-session-parses-every-file", "not-linked-session-parses-only-the-listed-files"],
    )
    def test_each_pending_file_is_parsed_once_per_run(
        self, sandbox: _Sandbox, tmp_path: Path, session_directory: str, expected_parses: int
    ) -> None:
        """A linked session classifies every pending file, because the target decides selection and order.
        Elsewhere only the five listed candidates are parsed. Every fixture carries `--cwd`, so each parse
        reaches `tail`."""
        for index in range(_PENDING_FILES_IN_PARSE_COUNT_TEST):
            sandbox.write_pending(f"pending-{index}", sandbox.worktree_b)
        tail_log = tmp_path / "tail-calls.log"
        shim_bin = tmp_path / "counting-tail-bin"
        _write_path_shim(shim_bin, "tail", f'printf x >> "{tail_log}"\nexec "$REAL_TAIL" "$@"')

        outcome = sandbox.run(
            cwd=getattr(sandbox, session_directory),
            env_extra={"PATH": f"{shim_bin}:{os.environ['PATH']}", "REAL_TAIL": shutil.which("tail") or "tail"},
        )

        assert len(outcome.candidate_names) == 5
        assert outcome.first("MORE") == str(_PENDING_FILES_IN_PARSE_COUNT_TEST - 5)
        assert tail_log.read_text() == "x" * expected_parses

    def test_a_file_that_vanishes_mid_listing_keeps_the_newest_first_order_of_the_rest(
        self, sandbox: _Sandbox, tmp_path: Path
    ) -> None:
        oldest = sandbox.write_pending("a-oldest", sandbox.worktree_b, age_days=5)
        vanishing = sandbox.write_pending("b-vanishing", sandbox.worktree_b, age_days=3)
        newest = sandbox.write_pending("c-newest", sandbox.worktree_b, age_days=0.1)
        shim_bin = tmp_path / "vanishing-ls-bin"
        _write_path_shim(
            shim_bin,
            "ls",
            'case "$1" in -t) rm -f -- "$VANISHING_FILE" ;; esac\nexec "$REAL_LS" "$@"',
        )

        outcome = sandbox.run(
            cwd=sandbox.worktree_a,
            env_extra={
                "PATH": f"{shim_bin}:{os.environ['PATH']}",
                "REAL_LS": shutil.which("ls") or "ls",
                "VANISHING_FILE": str(vanishing),
            },
        )

        assert not vanishing.exists()
        assert outcome.candidate_names == [newest.name, oldest.name]

    def test_the_only_pending_file_vanishing_mid_listing_stops_as_nothing_pending(
        self, sandbox: _Sandbox, tmp_path: Path
    ) -> None:
        """A run that loads nothing still prints a STOP_REASON, CANDIDATE or SHELL_COMMAND line and exits 0."""
        vanishing = sandbox.write_pending("only-vanishing", sandbox.worktree_b)
        shim_bin = tmp_path / "vanishing-ls-bin"
        _write_path_shim(
            shim_bin,
            "ls",
            'case "$1" in -t) rm -f -- "$VANISHING_FILE" ;; esac\nexec "$REAL_LS" "$@"',
        )

        outcome = sandbox.run(
            cwd=sandbox.worktree_a,
            env_extra={
                "PATH": f"{shim_bin}:{os.environ['PATH']}",
                "REAL_LS": shutil.which("ls") or "ls",
                "VANISHING_FILE": str(vanishing),
            },
        )

        assert not vanishing.exists()
        assert not outcome.has("LOAD")
        assert outcome.stop_code == "nothing-pending"
        assert not outcome.has("CANDIDATE")

    def test_a_quoting_style_in_the_environment_does_not_drop_the_listed_files(self, sandbox: _Sandbox) -> None:
        """GNU `ls` honors QUOTING_STYLE and prints quoted names, which match no file once re-filtered."""
        first = sandbox.write_pending("first", sandbox.worktree_b)
        second = sandbox.write_pending("second", sandbox.worktree_b)

        outcome = sandbox.run(env_extra={"QUOTING_STYLE": "shell-always"})

        assert sorted(outcome.candidate_names) == [first.name, second.name]
        sandbox.assert_nothing_moved(first, second)

    def test_a_failing_find_reads_a_target_match_as_stale_so_it_is_listed_not_loaded(
        self, sandbox: _Sandbox, tmp_path: Path
    ) -> None:
        match = sandbox.write_pending("match", sandbox.worktree_a)
        shim_bin = tmp_path / "failing-find-bin"
        _write_path_shim(shim_bin, "find", "exit 1")

        outcome = sandbox.run(cwd=sandbox.worktree_a, env_extra={"PATH": f"{shim_bin}:{os.environ['PATH']}"})

        assert not outcome.has("LOAD")
        assert outcome.candidate_names == [match.name]
        assert ">7d" in outcome.first("CANDIDATE")
        sandbox.assert_nothing_moved(match)

    def test_files_that_vanish_below_the_cap_leave_no_more_line(self, sandbox: _Sandbox, tmp_path: Path) -> None:
        """MORE counts the files left unlisted at the cap, not the pending count seen before the vanish."""
        pending_files = [
            sandbox.write_pending(f"pending-{index}", sandbox.worktree_b, age_days=index + 1) for index in range(6)
        ]
        shim_bin = tmp_path / "vanishing-ls-bin"
        _write_path_shim(
            shim_bin,
            "ls",
            'case "$1" in -t) rm -f -- "$VANISHING_FILE" ;; esac\nexec "$REAL_LS" "$@"',
        )

        outcome = sandbox.run(
            cwd=sandbox.worktree_a,
            env_extra={
                "PATH": f"{shim_bin}:{os.environ['PATH']}",
                "REAL_LS": shutil.which("ls") or "ls",
                "VANISHING_FILE": str(pending_files[0]),
            },
        )

        assert not pending_files[0].exists()
        assert len(outcome.candidate_names) == 5
        assert not outcome.has("MORE")


class TestExplicitArgument:
    @pytest.mark.parametrize("form", ["slug", "file-name", "absolute-path"])
    def test_loads_exactly_the_named_file(self, sandbox: _Sandbox, form: str) -> None:
        wanted = sandbox.write_pending("wanted", sandbox.worktree_a)
        decoy = sandbox.write_pending("decoy", sandbox.worktree_a)
        argument = {"slug": "wanted", "file-name": wanted.name, "absolute-path": str(wanted)}[form]

        outcome = sandbox.run(argument, cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("SOURCE") == str(wanted)
        assert not wanted.exists() and decoy.exists()

    def test_brief_loads_by_slug(self, sandbox: _Sandbox) -> None:
        brief = sandbox.write_pending("some-brief", sandbox.worktree_a, kind="brief")

        outcome = sandbox.run("some-brief", cwd=sandbox.worktree_a)

        assert outcome.first("SOURCE") == str(brief)
        assert outcome.first("FILE_TARGET") == str(sandbox.worktree_a.resolve())

    def test_recently_written_file_reports_the_fresh_bucket(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("fresh", sandbox.worktree_a, age_days=_AGE_FRESH_DAYS)

        assert sandbox.run("fresh", cwd=sandbox.worktree_a).first("AGE") == "<1d"

    def test_file_older_than_ten_days_named_by_slug_loads_with_stale_age(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("ancient", sandbox.worktree_a, age_days=_AGE_STALE_DAYS)

        outcome = sandbox.run("ancient", cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("AGE") == ">7d"

    def test_middle_aged_file_reports_the_middle_bucket(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("middling", sandbox.worktree_a, age_days=_AGE_MIDDLE_DAYS)

        assert sandbox.run("middling", cwd=sandbox.worktree_a).first("AGE") == "1-7d"

    @pytest.mark.parametrize(
        ("age_days", "bucket"),
        [(0.5, "<1d"), (1.5, "1-7d"), (6.5, "1-7d"), (7.5, ">7d")],
        ids=["half-a-day", "a-day-and-a-half", "six-and-a-half-days", "seven-and-a-half-days"],
    )
    def test_age_bucket_edges_hold_half_a_day_either_side(
        self, sandbox: _Sandbox, age_days: float, bucket: str
    ) -> None:
        sandbox.write_pending("aged", sandbox.worktree_a, age_days=age_days)

        assert sandbox.run("aged", cwd=sandbox.worktree_a).first("AGE") == bucket

    def test_slug_shared_by_a_handoff_and_a_brief_gives_two_candidates_and_moves_nothing(
        self, sandbox: _Sandbox
    ) -> None:
        handoff = sandbox.write_pending("shared", sandbox.worktree_a)
        brief = sandbox.write_pending("shared", sandbox.worktree_a, kind="brief")

        outcome = sandbox.run("shared", cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert sorted(outcome.candidate_names) == sorted([handoff.name, brief.name])
        sandbox.assert_nothing_moved(handoff, brief)

    @pytest.mark.parametrize(
        ("arguments", "expected_stop_code"),
        [
            (("*",), "argument-not-allowed"),
            (("pending/..",), "not-pending"),
            (("pend.ng",), "not-pending"),
            (("-x",), "argument-not-allowed"),
            (("--cwd",), "argument-not-allowed"),
            (("pending", "extra"), "argument-not-allowed"),
            (("$(id)",), "argument-not-allowed"),
            (("",), "argument-not-allowed"),
        ],
        ids=[
            "glob", "dot-dot", "regex-dot-against-a-real-slug", "dash", "cwd-flag",
            "two-arguments", "command-substitution", "empty",
        ],
    )
    def test_malformed_arguments_stop_with_the_expected_code_and_nothing_moved(
        self, sandbox: _Sandbox, arguments: tuple[str, ...], expected_stop_code: str
    ) -> None:
        """`pend.ng` survives the character allowlist and would match `pending` as a pattern; only
        equality matching leaves it `not-pending`."""
        pending = sandbox.write_pending("pending", sandbox.worktree_a)

        outcome = sandbox.run(*arguments, cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.stop_code == expected_stop_code
        sandbox.assert_nothing_moved(pending)

    def test_cwd_flag_is_refused_with_the_accepted_forms(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("pending", sandbox.worktree_a)

        outcome = sandbox.run("--cwd", cwd=sandbox.worktree_a)

        assert outcome.stop_code == "argument-not-allowed"
        assert "takes no --cwd" in outcome.first("STOP_REASON")

    def test_absolute_path_outside_the_handoffs_directory_is_not_pending(self, sandbox: _Sandbox) -> None:
        outside = sandbox.write_named(sandbox.tmp_path / "elsewhere" / "stray-handoff.md", None)
        pending = sandbox.write_pending("pending")

        outcome = sandbox.run(str(outside))

        assert outcome.stop_code == "not-pending"
        sandbox.assert_nothing_moved(outside, pending)

    def test_a_slug_that_is_a_prefix_of_another_slug_does_not_match(self, sandbox: _Sandbox) -> None:
        longer = sandbox.write_pending("foo-bar", sandbox.worktree_a)

        outcome = sandbox.run("foo", cwd=sandbox.worktree_a)

        assert outcome.stop_code == "not-pending"
        sandbox.assert_nothing_moved(longer)

    def test_naming_the_only_file_again_after_its_load_finds_nothing_pending(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("once", sandbox.worktree_a)
        assert sandbox.run("once", cwd=sandbox.worktree_a).has("LOAD")

        again = sandbox.run("once", cwd=sandbox.worktree_a)

        assert again.stop_code == "nothing-pending"

    def test_naming_a_consumed_file_while_another_stays_pending_is_not_pending_and_moves_nothing_more(
        self, sandbox: _Sandbox
    ) -> None:
        sandbox.write_pending("once", sandbox.worktree_a)
        other = sandbox.write_pending("other", sandbox.worktree_a)
        assert sandbox.run("once", cwd=sandbox.worktree_a).has("LOAD")
        copies_after_load = sandbox.moved_copies()

        again = sandbox.run("once", cwd=sandbox.worktree_a)

        assert again.stop_code == "not-pending"
        assert f"{sandbox.config_dir}/scripts/find-consumed-continuity-file.sh once" in again.first("STOP_REASON")
        assert other.exists()
        assert sandbox.moved_copies() == copies_after_load

    def test_not_pending_names_the_lookup_script_with_the_argument(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("present", sandbox.worktree_a)

        outcome = sandbox.run("absent", cwd=sandbox.worktree_a)

        assert outcome.stop_code == "not-pending"
        assert f"{sandbox.config_dir}/scripts/find-consumed-continuity-file.sh absent" in outcome.first("STOP_REASON")


class TestVerdictsAndTargets:
    def test_file_targeting_another_worktree_gives_a_shell_command_and_stays_pending(
        self, sandbox: _Sandbox
    ) -> None:
        elsewhere = sandbox.write_pending("elsewhere", sandbox.worktree_b)

        outcome = sandbox.run(elsewhere.name, cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.first("SHELL_COMMAND") == f"resume-context --cwd {sandbox.worktree_b.resolve()} {elsewhere}"
        assert outcome.first("FILE_TARGET") == str(sandbox.worktree_b.resolve())
        sandbox.assert_nothing_moved(elsewhere)

    def test_main_checkout_loads_a_file_naming_no_cwd(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("plain")

        outcome = sandbox.run("plain", cwd=sandbox.main_checkout)

        assert outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == "none"
        assert outcome.first("SESSION_TARGET") == "none"

    def test_brief_shaped_file_naming_no_cwd_loads_in_a_main_checkout(self, sandbox: _Sandbox) -> None:
        """A brief carries §7 Out of scope before §7.5 Resume command; the command is still found."""
        sandbox.write_pending("plain-brief", kind="brief")

        outcome = sandbox.run("plain-brief", cwd=sandbox.main_checkout)

        assert outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == "none"

    def test_main_checkout_with_a_file_naming_a_cwd_gives_a_shell_command(self, sandbox: _Sandbox) -> None:
        targeted = sandbox.write_pending("targeted", sandbox.worktree_a)

        outcome = sandbox.run("targeted", cwd=sandbox.main_checkout)

        assert outcome.has("SHELL_COMMAND")
        sandbox.assert_nothing_moved(targeted)

    def test_linked_worktree_with_a_file_naming_no_cwd_gives_a_shell_command_and_stays_pending(
        self, sandbox: _Sandbox
    ) -> None:
        no_cwd = sandbox.write_pending("no-cwd")

        outcome = sandbox.run("no-cwd", cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.first("SHELL_COMMAND") == f"resume-context {no_cwd}"
        assert outcome.first("SESSION_TARGET") == str(sandbox.worktree_a.resolve())
        assert outcome.first("FILE_TARGET") == "none"
        sandbox.assert_nothing_moved(no_cwd)

    @pytest.mark.parametrize("session", ["main-checkout", "linked-worktree"])
    @pytest.mark.parametrize("body_key", sorted(_UNREADABLE_CWD_BODIES))
    def test_file_with_an_unreadable_cwd_never_loads_in_any_session_state(
        self, sandbox: _Sandbox, session: str, body_key: str
    ) -> None:
        pending = sandbox.write_body(sandbox.handoffs / "unreadable-handoff.md", _UNREADABLE_CWD_BODIES[body_key])
        session_cwd = {"main-checkout": sandbox.main_checkout, "linked-worktree": sandbox.worktree_a}[session]

        outcome = sandbox.run("unreadable", cwd=session_cwd)

        assert not outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == _UNREADABLE_TARGET
        assert outcome.first("SHELL_COMMAND") == f"resume-context {pending}"
        sandbox.assert_nothing_moved(pending)

    def test_unreadable_cwd_is_listed_as_target_unknown_and_never_auto_loaded(self, sandbox: _Sandbox) -> None:
        pending = sandbox.write_body(sandbox.handoffs / "unreadable-handoff.md", _UNREADABLE_CWD_BODIES["tilde"])

        outcome = sandbox.run(cwd=sandbox.main_checkout)

        assert not outcome.has("LOAD")
        assert outcome.first("CANDIDATE") == (
            f"{pending.name} | <1d | target unknown | resume-context {pending}"
        )
        sandbox.assert_nothing_moved(pending)

    @pytest.mark.parametrize(
        "body",
        [
            "## §7 Resume command\n`resume-context --cwd /work/a {file}`.\n",
            "## §7 Resume command\n`resume-context --cwd /work/a {file}`\nRun resume-context {file} when ready.\n",
            "## §7 Resume command\n`resume-context --cwd /work/a \\\n  {file}`\n",
            "Run resume-context {file} when ready.\n## §7 Resume command\n`resume-context --cwd /work/a {file}`\n",
        ],
        ids=[
            "punctuation-after-the-closing-backtick",
            "later-prose-mention-without-cwd",
            "backslash-continuation",
            "earlier-prose-mention-without-cwd",
        ],
    )
    def test_file_naming_a_cwd_in_a_decorated_command_never_loads_in_a_main_checkout(
        self, sandbox: _Sandbox, body: str
    ) -> None:
        pending = sandbox.write_body(sandbox.handoffs / "decorated-handoff.md", body)

        outcome = sandbox.run("decorated", cwd=sandbox.main_checkout)

        assert not outcome.has("LOAD")
        assert outcome.first("SHELL_COMMAND") == f"resume-context --cwd /work/a {pending}"
        sandbox.assert_nothing_moved(pending)

    def test_worktree_path_with_a_space_is_unknown_and_never_loads(self, sandbox: _Sandbox, tmp_path: Path) -> None:
        spaced_repo = init_git_repo_with_commit(tmp_path / "spaced-main", branch="main")
        spaced = _new_linked_worktree(spaced_repo, tmp_path / "wt with space")
        no_cwd = sandbox.write_pending("no-cwd")

        outcome = sandbox.run("no-cwd", cwd=spaced)

        assert not outcome.has("LOAD")
        assert outcome.first("SESSION_TARGET") == "unknown"
        sandbox.assert_nothing_moved(no_cwd)

    def test_failing_git_call_is_unknown_and_never_loads(self, sandbox: _Sandbox) -> None:
        no_cwd = sandbox.write_pending("no-cwd")
        stub_bin = sandbox.tmp_path / "failing-git-bin"
        _make_git_exiting_with_status(stub_bin, "--absolute-git-dir", 1)

        outcome = sandbox.run(
            "no-cwd",
            cwd=sandbox.worktree_a,
            env_extra={"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"},
        )

        assert not outcome.has("LOAD")
        assert outcome.first("SESSION_TARGET") == "unknown"
        sandbox.assert_nothing_moved(no_cwd)

    def test_directory_outside_any_repository_prints_none_and_loads_a_file_naming_no_cwd(
        self, sandbox: _Sandbox
    ) -> None:
        plain = sandbox.tmp_path / "plain-dir"
        plain.mkdir()
        sandbox.write_pending("no-cwd")

        outcome = sandbox.run("no-cwd", cwd=plain, env_extra={"GIT_CEILING_DIRECTORIES": str(sandbox.tmp_path)})

        assert outcome.has("LOAD")
        assert outcome.first("SESSION_TARGET") == "none"

    def test_subdirectory_of_a_worktree_prints_the_worktree_root(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("mine", sandbox.worktree_a)

        outcome = sandbox.run(cwd=sandbox.nested_in_worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("SESSION_TARGET") == str(sandbox.worktree_a.resolve())

    def test_cwd_reached_through_a_symlink_still_loads(self, sandbox: _Sandbox) -> None:
        link = sandbox.tmp_path / "worktree-a-link"
        link.symlink_to(sandbox.worktree_a)
        sandbox.write_pending("via-link", link)

        outcome = sandbox.run("via-link", cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == str(sandbox.worktree_a.resolve())

    def test_cwd_symlink_resolving_to_an_unprintable_path_stops_without_printing_the_real_path(
        self, sandbox: _Sandbox
    ) -> None:
        unprintable = sandbox.tmp_path / "real dir with space"
        unprintable.mkdir()
        link = sandbox.tmp_path / "unprintable-link"
        link.symlink_to(unprintable)
        pending = sandbox.write_pending("via-unprintable-link", link)

        outcome = sandbox.run("via-unprintable-link", cwd=sandbox.worktree_a)

        assert not outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == str(link)
        assert str(unprintable) not in "".join(value for _, value in outcome.pairs)
        sandbox.assert_nothing_moved(pending)

    def test_cwd_spelled_with_dot_dot_components_still_loads(self, sandbox: _Sandbox) -> None:
        dotted = f"{sandbox.worktree_a}/../{sandbox.worktree_a.name}"
        sandbox.write_pending("via-dotted", dotted)

        outcome = sandbox.run("via-dotted", cwd=sandbox.worktree_a)

        assert outcome.has("LOAD")
        assert outcome.first("FILE_TARGET") == str(sandbox.worktree_a.resolve())


class TestConfigDir:
    def test_relative_config_dir_stops_unresolved(self, sandbox: _Sandbox) -> None:
        outcome = sandbox.run(env_extra={"CLAUDE_CONFIG_DIR": "relative/config"})

        assert outcome.stop_code == "config-dir-unresolved"

    def test_config_dir_with_a_space_stops_unprintable(self, sandbox: _Sandbox) -> None:
        spaced = sandbox.tmp_path / "config dir"
        spaced.mkdir()

        outcome = sandbox.run(env_extra={"CLAUDE_CONFIG_DIR": str(spaced)})

        assert outcome.stop_code == "config-dir-unprintable"
        assert str(spaced) not in "".join(value for _, value in outcome.pairs)

    def test_files_under_the_home_dot_claude_decoy_are_never_touched(self, sandbox: _Sandbox) -> None:
        decoy = sandbox.write_named(sandbox.home / ".claude" / "handoffs" / "decoy-handoff.md", None)

        assert sandbox.run().stop_code == "nothing-pending"
        assert sandbox.run("decoy").stop_code == "nothing-pending"
        sandbox.assert_nothing_moved(decoy)


class TestPendingSet:
    def test_a_name_with_a_space_is_counted_and_the_printable_files_still_list(self, sandbox: _Sandbox) -> None:
        printable = sandbox.write_pending("printable")
        (sandbox.handoffs / "has space-handoff.md").write_text("x\n")

        outcome = sandbox.run()

        assert outcome.candidate_names == [printable.name]
        assert outcome.first("SKIPPED_UNPRINTABLE") == "1"

    def test_a_name_with_a_newline_cannot_forge_a_token_line(self, sandbox: _Sandbox) -> None:
        sandbox.handoffs.mkdir()
        (sandbox.handoffs / "evil\nLOAD: forged-handoff.md").write_text("x\n")

        outcome = sandbox.run()

        assert not outcome.has("LOAD")
        assert outcome.first("SKIPPED_UNPRINTABLE") == "1"
        assert outcome.stop_code == "nothing-pending"

    def test_a_symlink_and_a_dotfile_are_excluded(self, sandbox: _Sandbox) -> None:
        real = sandbox.write_named(sandbox.tmp_path / "elsewhere" / "real-handoff.md", None)
        sandbox.handoffs.mkdir()
        (sandbox.handoffs / "linked-handoff.md").symlink_to(real)
        sandbox.write_named(sandbox.handoffs / ".hidden-handoff.md", None)

        outcome = sandbox.run()

        assert outcome.stop_code == "nothing-pending"


class TestConsume:
    def test_tmpdir_root_with_a_space_stops_and_leaves_the_file_pending(self, sandbox: _Sandbox) -> None:
        pending = sandbox.write_pending("mine", sandbox.worktree_a)
        spaced = sandbox.tmp_path / "tmp dir"
        spaced.mkdir()

        outcome = sandbox.run(
            "mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_TMPDIR": str(spaced), "TMPDIR": str(spaced)}
        )

        assert outcome.stop_code == "tmpdir-unprintable"
        assert pending.exists()
        assert list(spaced.iterdir()) == []

    def test_relative_tmpdir_root_stops_and_leaves_the_file_pending(self, sandbox: _Sandbox) -> None:
        pending = sandbox.write_pending("mine", sandbox.worktree_a)

        outcome = sandbox.run(
            "mine",
            cwd=sandbox.worktree_a,
            env_extra={"RESUME_CONTEXT_TMPDIR": "relative-tmp", "TMPDIR": "relative-tmp"},
        )

        assert outcome.stop_code == "tmpdir-unprintable"
        assert not (sandbox.worktree_a / "relative-tmp").exists()
        assert pending.exists()

    @pytest.mark.parametrize("failure", ["tmpdir-with-a-space", "consumer-exits-1"])
    def test_a_stop_before_the_move_names_the_file_and_the_shell_command_to_run(
        self, sandbox: _Sandbox, failure: str
    ) -> None:
        pending = sandbox.write_pending("mine", sandbox.worktree_a)
        spaced = sandbox.tmp_path / "tmp dir"
        spaced.mkdir()
        env_extra = {
            "tmpdir-with-a-space": {"RESUME_CONTEXT_TMPDIR": str(spaced), "TMPDIR": str(spaced)},
            "consumer-exits-1": {"RESUME_CONTEXT_CONSUMER": str(_write_consumer_stub(sandbox.tmp_path, "exit 1"))},
        }[failure]

        outcome = sandbox.run("mine", cwd=sandbox.worktree_a, env_extra=env_extra)

        reason = outcome.first("STOP_REASON")
        assert f"for {pending.name}, run: resume-context --cwd {sandbox.worktree_a.resolve()} {pending}" in reason
        assert pending.exists()

    def test_consumer_exiting_1_with_the_file_untouched_is_consume_failed(self, sandbox: _Sandbox) -> None:
        pending = sandbox.write_pending("mine", sandbox.worktree_a)
        stub = _write_consumer_stub(sandbox.tmp_path, "exit 1")

        outcome = sandbox.run("mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_CONSUMER": str(stub)})

        assert outcome.stop_code == "consume-failed"
        assert not outcome.has("LOAD")
        assert pending.exists()

    def test_consumer_that_moves_the_file_then_exits_1_is_consumed_elsewhere_or_partial(
        self, sandbox: _Sandbox
    ) -> None:
        sandbox.write_pending("mine", sandbox.worktree_a)
        stub = _write_consumer_stub(sandbox.tmp_path, f'mv "$2" "{sandbox.tmp_path}/moved-copy"\nexit 1')

        outcome = sandbox.run("mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_CONSUMER": str(stub)})

        assert outcome.stop_code == "consumed-elsewhere-or-partial"
        assert "find-consumed-continuity-file.sh" in outcome.first("STOP_REASON")
        assert not outcome.has("LOAD")

    @pytest.mark.parametrize("moves_file", [False, True], ids=["file-untouched", "file-moved"])
    @pytest.mark.parametrize(
        "stdout_shape",
        [
            "empty",
            "two-lines",
            "outside-tmpdir-root",
            "nonexistent-path",
            "dot-dot-segment",
            "nested-subdirectory",
            "symlink",
            "group-and-world-readable",
            "space-in-basename",
        ],
    )
    def test_exit_0_with_an_unusable_destination_never_loads_and_reports_the_file_state(
        self, sandbox: _Sandbox, stdout_shape: str, moves_file: bool
    ) -> None:
        """Each shape changes one property of an otherwise valid destination.
        `two-lines` also fails the directory rule, and `symlink` also fails the mode rule,
        because `ls -ld` reports a link as `l...`, so the mode leg covers the `! -L` leg and that leg cannot be isolated."""
        pending = sandbox.write_pending("mine", sandbox.worktree_a)
        real_copy = sandbox.tmpdir_root / "resume-context.realcopy"
        real_copy.write_text("x\n")
        real_copy.chmod(0o600)
        outside_copy = sandbox.tmp_path / "outside-copy"
        outside_copy.write_text("x\n")
        outside_copy.chmod(0o600)
        nested_copy = sandbox.tmpdir_root / "nested" / "resume-context.nested"
        nested_copy.parent.mkdir()
        nested_copy.write_text("x\n")
        nested_copy.chmod(0o600)
        (sandbox.tmpdir_root / "resume-context.link").symlink_to(real_copy)
        open_copy = sandbox.tmpdir_root / "resume-context.open"
        open_copy.write_text("x\n")
        open_copy.chmod(0o644)
        spaced_copy = sandbox.tmpdir_root / "resume-context.has space"
        spaced_copy.write_text("x\n")
        spaced_copy.chmod(0o600)
        printed_path = {
            "empty": None,
            "two-lines": f"{real_copy}\n{real_copy}",
            "outside-tmpdir-root": str(outside_copy),
            "nonexistent-path": f"{sandbox.tmpdir_root}/resume-context.nope",
            "dot-dot-segment": f"{sandbox.tmpdir_root}/../outside-copy",
            "nested-subdirectory": str(nested_copy),
            "symlink": f"{sandbox.tmpdir_root}/resume-context.link",
            "group-and-world-readable": str(open_copy),
            "space-in-basename": str(spaced_copy),
        }[stdout_shape]
        printed = "" if printed_path is None else f"cat <<'EOF'\n{printed_path}\nEOF"
        move_line = f'mv "$2" "{sandbox.tmp_path}/moved-copy"' if moves_file else ""
        stub = _write_consumer_stub(sandbox.tmp_path, f"{move_line}\n{printed}\nexit 0")

        outcome = sandbox.run("mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_CONSUMER": str(stub)})

        assert not outcome.has("LOAD")
        assert outcome.stop_code == ("consumed-elsewhere-or-partial" if moves_file else "consume-failed")
        assert pending.exists() is (not moves_file)

    def test_a_valid_stub_destination_loads_proving_the_rejection_shapes_differ_in_one_property(
        self, sandbox: _Sandbox
    ) -> None:
        """Positive control for the rejection shapes above: an owner-only regular file directly under the root loads.
        The `[ -O ]` ownership leg of the check is unpinned, because a foreign-owned fixture needs a second uid."""
        pending = sandbox.write_pending("mine", sandbox.worktree_a)
        good_copy = sandbox.tmpdir_root / "resume-context.good"
        good_copy.write_text("x\n")
        good_copy.chmod(0o600)
        stub = _write_consumer_stub(
            sandbox.tmp_path, f'mv "$2" "{sandbox.tmp_path}/moved-copy"\nprintf "%s\\n" "{good_copy}"'
        )

        outcome = sandbox.run("mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_CONSUMER": str(stub)})

        assert outcome.first("LOAD") == str(good_copy)
        assert not pending.exists()

    def test_a_mode_field_with_a_trailing_acl_marker_still_counts_as_owner_only(
        self, sandbox: _Sandbox, tmp_path: Path
    ) -> None:
        """Twin of the `group-and-world-readable` deny row: a 0600 file whose `ls -ld` mode field ends in `.`
        (the SELinux marker; `+` for an ACL) loads."""
        sandbox.write_pending("mine", sandbox.worktree_a)
        ls_mode_call_log = tmp_path / "ls-mode-calls"
        shim_bin = tmp_path / "marker-ls-bin"
        _write_path_shim(
            shim_bin,
            "ls",
            'case "$1" in\n'
            "  -ld) printf x >> \"$LS_MODE_CALL_LOG\"; \"$REAL_LS\" \"$@\" | sed '1s/^[^ ]*/&./'; exit \"${PIPESTATUS[0]}\" ;;\n"
            "esac\n"
            'exec "$REAL_LS" "$@"',
        )

        outcome = sandbox.run(
            "mine",
            cwd=sandbox.worktree_a,
            env_extra={
                "PATH": f"{shim_bin}:{os.environ['PATH']}",
                "REAL_LS": shutil.which("ls") or "ls",
                "LS_MODE_CALL_LOG": str(ls_mode_call_log),
            },
        )

        assert ls_mode_call_log.read_text() == "x", "the owner-only check should have called `ls -ld` exactly once"
        assert outcome.has("LOAD")

    def test_a_slash_terminated_temp_dir_root_loads_through_the_real_consumer(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("mine", sandbox.worktree_a)

        outcome = sandbox.run(
            "mine", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_TMPDIR": f"{sandbox.tmpdir_root}/"}
        )

        assert outcome.has("LOAD")

    @pytest.mark.parametrize("printed_separator", ["/", "//"], ids=["single-slash", "double-slash"])
    def test_a_trailing_slash_temp_dir_root_still_matches_a_destination_printed_with_either_separator(
        self, sandbox: _Sandbox, printed_separator: str
    ) -> None:
        """The root's trailing `/` and a doubled `/` in the printed destination each need their own strip,
        so together the two rows fail if either strip in `is_usable_destination` is dropped."""
        sandbox.write_pending("mine", sandbox.worktree_a)
        good_copy = sandbox.tmpdir_root / "resume-context.good"
        good_copy.write_text("x\n")
        good_copy.chmod(0o600)
        printed_destination = f"{sandbox.tmpdir_root}{printed_separator}{good_copy.name}"
        stub = _write_consumer_stub(
            sandbox.tmp_path, f'mv "$2" "{sandbox.tmp_path}/moved-copy"\nprintf "%s\\n" "{printed_destination}"'
        )
        slash_terminated_root = f"{sandbox.tmpdir_root}/"

        outcome = sandbox.run(
            "mine",
            cwd=sandbox.worktree_a,
            env_extra={
                "RESUME_CONTEXT_CONSUMER": str(stub),
                "RESUME_CONTEXT_TMPDIR": slash_terminated_root,
                "TMPDIR": slash_terminated_root,
            },
        )

        assert outcome.first("LOAD") == printed_destination

    @pytest.mark.parametrize("exact_source_set", [True, False], ids=["guard-on", "guard-off-control"])
    def test_a_file_that_vanishes_before_the_consume_never_loads_the_legacy_location_copy(
        self, sandbox: _Sandbox, exact_source_set: bool
    ) -> None:
        """With CLAUDE_CONFIG_DIR away from $HOME/.claude, resume-context.sh retries a vanished source under
        $HOME/.claude. The script sets RESUME_CONTEXT_EXACT_SOURCE to refuse that; the control run drops
        the variable and shows the fallback consuming the other copy."""
        pending = sandbox.write_pending("racy", sandbox.worktree_a)
        legacy_copy = sandbox.write_named(sandbox.home / ".claude" / "handoffs" / "racy-handoff.md", sandbox.worktree_a)
        unset_line = "" if exact_source_set else "unset RESUME_CONTEXT_EXACT_SOURCE"
        stub = _write_consumer_stub(
            sandbox.tmp_path, f'rm -- "$2"\n{unset_line}\nexec "{_REAL_CONSUMER}" "$@"'
        )

        outcome = sandbox.run("racy", cwd=sandbox.worktree_a, env_extra={"RESUME_CONTEXT_CONSUMER": str(stub)})

        assert not pending.exists()
        if exact_source_set:
            assert not outcome.has("LOAD")
            assert outcome.stop_code == "consumed-elsewhere-or-partial"
            assert legacy_copy.exists()
            assert sandbox.moved_copies() == []
        else:
            assert outcome.has("LOAD")
            assert not legacy_copy.exists()


class TestRoundTrip:
    def _announced_slash_argument(self, sandbox: _Sandbox, pending: Path, session_cwd: Path) -> str:
        result = subprocess.run(
            [str(_ANNOUNCE_HOOK)],
            input=json.dumps(write_input(str(pending), cwd=str(session_cwd))),
            capture_output=True,
            text=True,
            env=sandbox.env(),
            check=False,
        )
        assert result.returncode == 0, result.stderr
        message = json.loads(result.stdout)["systemMessage"]
        found = re.search(r"/resume-context (\S+?)\. ", message)
        assert found, f"no slash form in {message!r}"
        return found.group(1)

    def test_the_file_name_the_announce_hook_advertises_loads_exactly_that_file(self, sandbox: _Sandbox) -> None:
        pending = sandbox.write_pending("v1.2-step-3.4", sandbox.worktree_a)
        decoy = sandbox.write_pending("v1.2-step-3", sandbox.worktree_a)

        argument = self._announced_slash_argument(sandbox, pending, sandbox.worktree_a)
        outcome = sandbox.run(argument, cwd=sandbox.worktree_a)

        assert argument == pending.name
        assert outcome.first("SOURCE") == str(pending)
        assert decoy.exists()

    def _announced_shell_command(self, sandbox: _Sandbox, pending: Path, session_cwd: Path) -> str:
        result = subprocess.run(
            [str(_ANNOUNCE_HOOK)],
            input=json.dumps(write_input(str(pending), cwd=str(session_cwd))),
            capture_output=True,
            text=True,
            env=sandbox.env(),
            check=False,
        )
        assert result.returncode == 0, result.stderr
        message = json.loads(result.stdout)["systemMessage"]
        found = re.search(r"Resume \(run in your shell\): `([^`]+)`", message)
        assert found, f"no shell-only command in {message!r}"
        return found.group(1)

    @pytest.mark.parametrize(
        ("session", "file_target"),
        [("worktree_a", "worktree_b"), ("main_checkout", "worktree_b")],
        ids=["linked-session-file-names-another-worktree", "main-checkout-file-names-a-worktree"],
    )
    def test_the_shell_command_the_announce_hook_advertises_equals_the_scripts_shell_command(
        self, sandbox: _Sandbox, session: str, file_target: str
    ) -> None:
        """Parity for a canonically spelled target; the script alone canonicalizes a symlinked or dotted one."""
        session_cwd = getattr(sandbox, session)
        pending = sandbox.write_pending("parity", getattr(sandbox, file_target).resolve())

        announced = self._announced_shell_command(sandbox, pending, session_cwd)
        outcome = sandbox.run(pending.name, cwd=session_cwd)

        assert announced == outcome.first("SHELL_COMMAND")
        assert f"--cwd {getattr(sandbox, file_target).resolve()} " in announced

    def test_every_listed_candidate_file_name_resolves_to_exactly_that_candidate(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("alpha-1.0")
        sandbox.write_pending("alpha-1.0-extra")
        sandbox.write_pending("alpha", kind="brief")
        listing = sandbox.run(cwd=sandbox.main_checkout)
        assert len(listing.candidate_names) == 3

        for name in listing.candidate_names:
            outcome = sandbox.run(name, cwd=sandbox.main_checkout)
            assert Path(outcome.first("SOURCE")).name == name


class TestSkillContract:
    def test_the_skill_body_names_every_key_the_script_emits(self) -> None:
        body = _SKILL_MD.read_text()

        missing = [key for key in _EMITTED_KEYS if f"`{key}" not in body]

        assert missing == [], f"resume-context/SKILL.md does not name {missing}"

    def test_the_emitted_keys_equal_the_keys_in_the_script_header_contract(self) -> None:
        header_keys = re.findall(r"^#   ([A-Z_]+): ", _SCRIPT.read_text(), flags=re.MULTILINE)

        assert sorted(header_keys) == sorted(_EMITTED_KEYS)

    def test_the_skill_argument_charset_equals_the_scripts_allowlist(self) -> None:
        """The skill refuses before the script runs, so the two must accept the same characters."""
        found = re.search(r"character outside `(\[[^`]+\])`", _SKILL_MD.read_text())
        assert found, "resume-context/SKILL.md no longer states its argument charset in backticks"
        skill_charset = re.compile(found.group(1))
        printable_ascii = [chr(code) for code in range(0x20, 0x7F)]
        probe = (
            f". {_LIB_SH}\n"
            "while IFS= read -r -d '' char; do\n"
            '  if _lib_passes_path_char_allowlist "a${char}b"; then printf Y; else printf N; fi\n'
            "done\n"
        )

        result = subprocess.run(
            ["bash", "-c", probe],
            input="\0".join(printable_ascii) + "\0",
            capture_output=True,
            text=True,
            check=False,
        )

        script_accepts = [char for char, verdict in zip(printable_ascii, result.stdout, strict=True) if verdict == "Y"]
        skill_accepts = [char for char in printable_ascii if skill_charset.fullmatch(char)]
        assert script_accepts == skill_accepts

    @pytest.mark.parametrize(("argument", "refused"), [("a:b", True), ("a+b", False)], ids=["colon-refused", "plus-passed"])
    def test_the_script_applies_that_allowlist_to_its_argument(
        self, sandbox: _Sandbox, argument: str, refused: bool
    ) -> None:
        sandbox.write_pending("pending")

        outcome = sandbox.run(argument)

        assert (outcome.stop_code == "argument-not-allowed") is refused


def _hook_matcher_applies_to_tool(matcher: str | None, tool_name: str) -> bool:
    """Whether a hook group's matcher selects `tool_name`. An omitted, empty or `*` matcher selects every
    tool; letters, digits, underscores and `|` alone make an exact-name alternation; anything else is a
    regular expression, searched unanchored so that an over-broad pattern errs toward being covered."""
    if matcher in (None, "", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        return tool_name in matcher.split("|")
    return re.search(matcher, tool_name) is not None


def _registered_pre_tool_use_read_hooks() -> list[Path]:
    settings = json.loads(_SETTINGS_PATH.read_text())
    hooks = []
    for group in settings["hooks"]["PreToolUse"]:
        if not _hook_matcher_applies_to_tool(group.get("matcher"), "Read"):
            continue
        for entry in group["hooks"]:
            hook = HOOKS_DIR / Path(entry["command"]).name
            assert hook.is_file(), f"{entry['command']} resolves to no file under {HOOKS_DIR}"
            hooks.append(hook)
    return hooks


# A Read each registered PreToolUse Read hook is built to deny, keyed by hook file name.
_DENIED_READ_PATH_BY_HOOK = {
    "deny-env-reads.sh": "project/.env",
    "deny-credential-file-reads.sh": "home/.netrc",
    "deny-data-file-reads.sh": "export/records.csv",
}


class TestHookMatcherSemantics:
    @pytest.mark.parametrize(
        ("matcher", "applies"),
        [
            ("Read", True),
            ("Read|Grep", True),
            ("Grep|Read", True),
            ("", True),
            (None, True),
            ("*", True),
            ("Read.*", True),
            ("^Read$", True),
            ("Write", False),
            ("Edit|Write|MultiEdit", False),
            ("Reader", False),
            ("Bash", False),
        ],
    )
    def test_matcher_selects_the_read_tool_as_claude_code_would(self, matcher: str | None, applies: bool) -> None:
        assert _hook_matcher_applies_to_tool(matcher, "Read") is applies


class TestReadOfALoadedPathIsNeverDeniedOrConsumed:
    def _read_hook(self, sandbox: _Sandbox, hook: Path, path: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(hook)],
            input=json.dumps(read_input(path)),
            capture_output=True,
            text=True,
            env=sandbox.env(),
            check=False,
        )

    def test_registered_pre_tool_use_read_hooks_are_all_covered_by_a_positive_control(self) -> None:
        names = {hook.name for hook in _registered_pre_tool_use_read_hooks()}
        assert names == set(_DENIED_READ_PATH_BY_HOOK)

    @pytest.mark.parametrize("hook_name", sorted(_DENIED_READ_PATH_BY_HOOK))
    def test_pre_tool_use_read_hooks_allow_the_loaded_path_and_deny_their_own_target(
        self, sandbox: _Sandbox, hook_name: str
    ) -> None:
        hook = HOOKS_DIR / hook_name
        (sandbox.config_dir / "data-file-read-guard.md").write_text("")
        sandbox.write_pending("mine", sandbox.worktree_a)
        loaded = sandbox.run("mine", cwd=sandbox.worktree_a).first("LOAD")

        allowed = self._read_hook(sandbox, hook, loaded)
        denied = self._read_hook(sandbox, hook, str(sandbox.tmp_path / _DENIED_READ_PATH_BY_HOOK[hook_name]))

        assert allowed.returncode == 0 and "deny" not in allowed.stdout
        assert denied.returncode == 2 or '"permissionDecision":"deny"' in denied.stdout.replace(" ", "")

    def test_consume_hook_does_not_move_anything_for_the_loaded_path(self, sandbox: _Sandbox) -> None:
        sandbox.write_pending("mine", sandbox.worktree_a)
        loaded = Path(sandbox.run("mine", cwd=sandbox.worktree_a).first("LOAD"))
        sandbox.config_dir.joinpath("scripts").symlink_to(SCRIPTS_DIR)
        sandbox.config_dir.joinpath("hooks").symlink_to(HOOKS_DIR)
        copies_before = sandbox.moved_copies()

        result = self._read_hook(sandbox, _CONSUME_HOOK, str(loaded))

        assert result.returncode == 0 and result.stdout == ""
        assert loaded.is_file()
        assert sandbox.moved_copies() == copies_before

    def test_consume_hook_does_move_a_pending_file_it_is_built_to_consume(self, sandbox: _Sandbox) -> None:
        """Positive control: the same hook, pointed at a pending path, consumes it."""
        pending = sandbox.write_pending("still-pending", sandbox.worktree_a)
        sandbox.config_dir.joinpath("scripts").symlink_to(SCRIPTS_DIR)
        sandbox.config_dir.joinpath("hooks").symlink_to(HOOKS_DIR)

        result = self._read_hook(sandbox, _CONSUME_HOOK, str(pending))

        assert result.returncode == 0
        assert not pending.exists()
        assert len(sandbox.moved_copies()) == 1
