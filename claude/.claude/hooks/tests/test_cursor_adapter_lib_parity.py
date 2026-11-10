"""Behavioral parity between two helpers the Cursor adapter's Python shim copies and their hooks/_lib.sh originals.

The shim cannot call _lib.sh, so each copy needs its own check: the session-id grammar, and path resolution, where the
shim's os.path.realpath stands in for `_lib_realpath_m`. The classes sit under hooks/tests/ so an edit to _lib.sh selects
them."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ._cursor_payloads import (
    LIB_SH,
    build_spec,
    hook_args,
    load_shim,
    make_shim_env,
    recording_probe_script,
    run_shim,
    shim_json,
)

SHIM = load_shim()


def _lib(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB_SH}"\n{script}', "_", *args], capture_output=True, text=True, check=False)


# The corpus of TestSessionIdPatternMatchesLibSh (scripts/tests/test_author_outcome.py), plus an embedded newline,
# a slash, and ids of 256 characters or more.
SESSION_ID_CORPUS = [
    "00000000-0000-0000-000000000001",
    "test_session-1",
    "a.b",
    "abc\n",
    "",
    "a/b",
    "..",
    "sessé",
    "ａbc",
    "ab\ncd",
    "a/b/c",
    "x" * 256,
    "y" * 300,
    "ends-with-newline\n",
    "has space",
    "-leading-dash",
    "UPPER_lower-123",
]


def _corpus_id(value: str) -> str:
    return repr(value if len(value) < 40 else value[:8] + f"...({len(value)} chars)")


class TestSessionIdGrammarParity:
    @pytest.mark.parametrize("session_id", SESSION_ID_CORPUS, ids=_corpus_id)
    def test_the_shims_pattern_and_the_live_function_agree_on_every_id(self, session_id: str) -> None:
        original = _lib('_lib_valid_session_id_component "$1"', session_id)
        assert (original.returncode == 0) == SHIM.is_valid_session_id(session_id)

    def test_the_corpus_holds_both_accepted_and_rejected_ids(self) -> None:
        verdicts = {_lib('_lib_valid_session_id_component "$1"', value).returncode == 0 for value in SESSION_ID_CORPUS}
        assert verdicts == {True, False}

    @pytest.mark.parametrize("session_id", [value for value in SESSION_ID_CORPUS if value], ids=_corpus_id)
    def test_a_payload_id_the_live_function_rejects_never_reaches_a_hook(self, session_id: str, tmp_path: Path) -> None:
        root = tmp_path.resolve()
        env = make_shim_env(tmp_path)
        payload = build_spec("session_start", root).cursor
        payload["conversation_id"] = session_id
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "probe.sh", saved)
        output = shim_json(run_shim(env, payload, hook_args("SessionStart", script=script), cwd=tmp_path))
        accepted_by_original = _lib('_lib_valid_session_id_component "$1"', session_id).returncode == 0
        if accepted_by_original:
            assert saved.exists(), (session_id, output)
        else:
            assert not saved.exists(), (session_id, output)

    @pytest.mark.parametrize("parent_agent_id", [False, True, 0, 7, [], {}, ["a"]], ids=repr)
    def test_a_parent_agent_id_that_is_not_a_string_is_neither_carried_nor_replaced_by_the_conversation_id(
        self, parent_agent_id: object, tmp_path: Path
    ) -> None:
        """`false` is a value that is carried and fails the grammar, the same as any other non-string."""
        root = tmp_path.resolve()
        env = make_shim_env(tmp_path)
        # A preToolUse call, because a refusal on an event with no block channel prints `{}` and would hide the outcome.
        payload = build_spec("shell_pre", root).cursor
        payload["parent_agent_id"] = parent_agent_id
        saved = tmp_path / "saved.json"
        script = recording_probe_script(tmp_path / "probe.sh", saved)
        output = shim_json(run_shim(env, payload, hook_args("PreToolUse", script=script), cwd=tmp_path))
        assert not saved.exists()
        assert output["permission"] == "deny"
        assert "session-id-grammar" in output["agent_message"]


def _gnu_realpath() -> str | None:
    """A `realpath` or `grealpath` whose `-m` works, as `_lib_realpath_m` tries them."""
    for name in ("realpath", "grealpath"):
        found = shutil.which(name)
        if found and subprocess.run([found, "-m", "--", "/"], capture_output=True, check=False).returncode == 0:
            return found
    return None


# Cases whose resolution is expected to differ between `realpath -m` and os.path.realpath's non-strict mode, by id.
# Each is run for the shim's result only: it must return an absolute path and raise nothing.
EXPECTED_TO_DIFFER = ("symlink-loop", "unreadable-part")


class TestRealpathParity:
    @pytest.fixture
    def tree(self, tmp_path: Path) -> Path:
        base = (tmp_path / "tree").resolve()
        (base / "existing" / "inner").mkdir(parents=True)
        (base / "with space").mkdir()
        (base / "link").symlink_to(base / "existing")
        (base / "dangling").symlink_to(base / "nowhere")
        (base / "loop-a").symlink_to(base / "loop-b")
        (base / "loop-b").symlink_to(base / "loop-a")
        return base

    def _agreeing_targets(self, base: Path) -> dict[str, str]:
        return {
            "existing-directory": str(base / "existing" / "inner"),
            "missing-leaf": str(base / "existing" / "missing" / "leaf.txt"),
            "two-missing-levels": str(base / "missing-a" / "missing-b" / "leaf.txt"),
            "missing-leaf-through-a-link": str(base / "link" / "inner" / "new.txt"),
            "space-in-the-name": str(base / "with space" / "new file.txt"),
            "dotdot-after-an-existing-part": str(base / "existing" / ".." / "existing" / "inner"),
            "trailing-slash": str(base / "existing") + "/",
            "dangling-link": str(base / "dangling"),
            "below-a-dangling-link": str(base / "dangling" / "child"),
            "dotdot-after-a-missing-part": str(base / "missing" / ".." / "x"),
            "deep-missing-path": "/" + "/".join(["deep"] * 70),
            "root": "/",
        }

    def _reference(self, target: str) -> str:
        """What the live `_lib_realpath_m` prints for `target`."""
        return _lib('_lib_realpath_m "$1"', target).stdout.rstrip("\n")

    def test_the_shims_resolution_matches_realpath_dash_m_on_every_agreeing_case(self, tree: Path) -> None:
        if _gnu_realpath() is None:
            pytest.skip("absent: neither realpath -m nor grealpath -m works on this host")
        for case_id, target in self._agreeing_targets(tree).items():
            assert SHIM.Roots([], "").canonicalize(target) == self._reference(target), case_id

    def test_a_missing_tail_and_a_dotdot_after_a_missing_part_resolve_the_way_realpath_dash_m_resolves_them(
        self, tree: Path
    ) -> None:
        roots = SHIM.Roots([], "")
        assert roots.canonicalize(str(tree / "missing-a" / "missing-b" / "leaf.txt")) == str(
            tree / "missing-a" / "missing-b" / "leaf.txt"
        )
        assert roots.canonicalize(str(tree / "missing" / ".." / "x")) == str(tree / "x")

    @pytest.mark.parametrize("case_id", EXPECTED_TO_DIFFER)
    def test_a_case_expected_to_differ_still_resolves_to_an_absolute_path_and_raises_nothing(
        self, tree: Path, case_id: str
    ) -> None:
        closed = tree / "closed"
        if case_id == "symlink-loop":
            target = str(tree / "loop-a" / "child")
        else:
            (closed / "inner").mkdir(parents=True)
            closed.chmod(0)
            target = str(closed / "inner" / "file.txt")
        try:
            resolved = SHIM.Roots([], "").canonicalize(target)
        finally:
            if closed.exists():
                closed.chmod(0o700)
        assert resolved.startswith("/")

    def test_a_target_holding_a_nul_raises_the_error_the_shim_catches_before_any_resolution(self) -> None:
        with pytest.raises(ValueError):
            SHIM.Roots([], "").canonicalize("/tmp/a\x00b")
