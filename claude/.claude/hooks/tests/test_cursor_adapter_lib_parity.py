"""Parity between what the Cursor adapter's Python files hold and the originals they stand in for or must equal.

The shim cannot call _lib.sh, so two helpers need their own checks: the session-id grammar, and path resolution, where the
shim's os.path.realpath stands in for `_lib_realpath_m`. cursor-adapter.py holds its own Python and bash floor constants, which
further checks tie to hooks/_config.sh, and its own copy of the shim's tool-path rule, which a check ties to the shim's.
The classes sit under hooks/tests/ so an edit to _lib.sh or _config.sh selects them."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ._cursor_payloads import (
    LIB_SH,
    build_spec,
    hook_args,
    load_adapter,
    load_shim,
    make_shim_env,
    recording_probe_script,
    run_shim,
    shim_json,
)
from .test_bash_floor import _CONFIG_SH, _floor_constants
from .test_git_floor import python_floor_fields
from .test_python_floor import _run_predicate

SHIM = load_shim()


def _lib(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB_SH}"\n{script}', "_", *args], capture_output=True, text=True, check=False)


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
# Each is run for the shim's result only: it does not raise and returns the lexical path.
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
    def test_a_case_expected_to_differ_does_not_raise_and_returns_its_lexical_path(self, tree: Path, case_id: str) -> None:
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
        assert resolved == target

    def test_a_target_holding_a_nul_raises_the_error_the_shim_catches_before_any_resolution(self) -> None:
        with pytest.raises(ValueError):
            SHIM.Roots([], "").canonicalize("/tmp/a\x00b")


class TestBashFloorParity:
    """cursor-adapter.py judges the hooks' bash by one constant, and _config.sh holds the toolkit's floor."""

    def test_the_adapters_bash_floor_constant_equals_the_floor_config_sh_holds(self) -> None:
        major, minor = _floor_constants(_CONFIG_SH)
        floor = load_adapter(name="cursor_adapter_bash_floor_parity").BASH_FLOOR
        assert floor == (major, minor), (
            f"cursor-adapter.py's BASH_FLOOR is {floor} but _config.sh's _BASH_FLOOR_MAJOR and _BASH_FLOOR_MINOR are "
            f"{major} and {minor}. Change them together."
        )

    def test_the_refusal_text_renders_the_same_constant(self) -> None:
        module = load_adapter(name="cursor_adapter_bash_floor_text")
        major, minor = _floor_constants(_CONFIG_SH)
        assert f"{major}.{minor} or later" in module.floor_text("bash", "/b", module.BASH_FLOOR, "below", (4, 0))


class TestSharedNameParity:
    """cursor-adapter.py reads, rotates and removes what the shim writes, under names each file holds for itself."""

    @pytest.mark.parametrize("name", ["STATE_DIR_NAME", "LOG_FILE_NAME", "FIRST_CALL_DIR_NAME"])
    def test_the_adapters_name_equals_the_shims(self, name: str) -> None:
        adapter = load_adapter(name="cursor_adapter_shared_name_parity")
        assert getattr(adapter, name) == getattr(SHIM, name)

    def test_the_adapters_install_record_path_equals_the_shims(self) -> None:
        adapter = load_adapter(name="cursor_adapter_shared_name_parity")
        assert f"{adapter.RECORD_RELATIVE_DIR}/{adapter.INSTALL_RECORD_NAME}" == SHIM.INSTALL_RECORD_RELATIVE_PATH


# Values on each side of every tool-path condition: absolute or not, a `..` component, and each list character.
TOOL_PATH_CORPUS = [
    "/usr/bin/python3",
    "/",
    "/a b/it's",
    "relative/path",
    "",
    "/a/../b",
    "/a/..",
    "/..a/b",
    "/a/.../b",
    "/a:b",
    "/a\nb",
    "/a\0b",
    "//a",
    "/a//b",
    "/a/",
]


class TestToolPathRuleParity:
    """cursor-adapter.py holds its own copy of the shim's tool-path rule, because `uninstall` must read the install record
    when the shim does not load. The two must judge every value alike."""

    @pytest.mark.parametrize("value", TOOL_PATH_CORPUS, ids=repr)
    def test_the_adapters_predicate_judges_a_value_as_the_shims_does(self, value: str) -> None:
        adapter = load_adapter(name="cursor_adapter_tool_path_parity")
        assert adapter.is_valid_tool_path(value) is SHIM.is_valid_tool_path(value)

    @pytest.mark.parametrize("value", [None, 7, b"/a", ["/a"]], ids=repr)
    def test_both_predicates_refuse_a_value_that_is_not_a_string(self, value: object) -> None:
        adapter = load_adapter(name="cursor_adapter_tool_path_parity")
        assert adapter.is_valid_tool_path(value) is False
        assert SHIM.is_valid_tool_path(value) is False

    @pytest.mark.parametrize("value", [value for value in TOOL_PATH_CORPUS if "\0" not in value], ids=repr)
    def test_home_directory_accepts_exactly_the_values_the_shims_rule_accepts(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = load_adapter(name="cursor_adapter_home_parity")
        monkeypatch.setenv("HOME", value)

        if SHIM.is_valid_tool_path(value):
            assert adapter.home_directory() == value
        else:
            with pytest.raises(adapter.AdapterError):
                adapter.home_directory()

    def test_home_directory_refuses_an_unset_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = load_adapter(name="cursor_adapter_home_parity")
        monkeypatch.delenv("HOME", raising=False)
        with pytest.raises(adapter.AdapterError):
            adapter.home_directory()


class TestPythonFloorParity:
    """cursor-adapter.py judges both python3 interpreters by one constant, and _config.sh holds the toolkit's floor."""

    def test_the_adapters_floor_constant_equals_the_floor_config_sh_holds(self) -> None:
        major, minor = python_floor_fields()
        floor = getattr(load_adapter(name="cursor_adapter_floor_parity"), "PYTHON_FLOOR", None)
        assert floor is not None, (
            "cursor-adapter.py defines no PYTHON_FLOOR. It must equal _config.sh's _PYTHON_FLOOR_MAJOR and _PYTHON_FLOOR_MINOR."
        )
        assert floor == (int(major), int(minor)), (
            f"cursor-adapter.py's PYTHON_FLOOR is {floor} but _config.sh's _PYTHON_FLOOR_MAJOR and "
            f"_PYTHON_FLOOR_MINOR are {major} and {minor}. Change them together."
        )

    def test_the_refusal_text_renders_the_same_constant(self) -> None:
        module = load_adapter(name="cursor_adapter_floor_text")
        major, minor = python_floor_fields()
        assert f"{major}.{minor} or later" in module.floor_text("python3", "/p", module.PYTHON_FLOOR, "below", (2, 0))


class TestProbeVersionFieldParity:
    """The adapter reads a probe's version fields as `_config.sh` does: one to nine digits, and no more."""

    @pytest.mark.parametrize("digits", [1, 9, 10, 20])
    @pytest.mark.parametrize("position", ["major", "minor"])
    def test_a_field_of_each_length_is_read_or_refused_by_the_adapter_as_the_floor_predicate_reads_or_refuses_it(
        self, digits: int, position: str
    ) -> None:
        adapter = load_adapter(name="cursor_adapter_probe_field_parity")
        field = "9" * digits
        major, minor = (field, "0") if position == "major" else ("1", field)

        predicate_refused_the_field = _run_predicate(major, minor).returncode == 2
        adapter_read_the_field = adapter.parse_version_fields(f"{major} {minor}") is not None

        assert adapter_read_the_field is not predicate_refused_the_field
        assert adapter_read_the_field is (digits <= 9)
