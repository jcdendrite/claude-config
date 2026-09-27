"""Tests for ask-review-permissions.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    _forced_fallback_path_env,
    _sourced_value,
    bash_input,
    edit_input,
    multiedit_input,
    read_input,
    run_hook,
    run_hook_reason,
    write_input,
)

REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"

_REALPATH_M_FALLBACK_MAX_DEPTH = int(_sourced_value("_LIB_REALPATH_M_FALLBACK_MAX_DEPTH"))

# Reads ask-review-permissions.sh's config-dir escape-class sed expression
# out of _lib.sh at test time, rather than a hand-copied duplicate that could
# drift from the hook's own class.
_CONFIG_DIR_ESCAPE_SED_SCRIPT = _sourced_value("_LIB_CONFIG_DIR_ESCAPE_SED_EXPR")


def _filesystem_is_case_sensitive() -> bool:
    """Probes the filesystem backing the default temp dir for case sensitivity.
    A `pytest.mark.skipif` condition is evaluated at collection time, before any
    `tmp_path` fixture exists, so this probes via `tempfile` instead."""
    with tempfile.TemporaryDirectory() as probe_dir:
        (Path(probe_dir) / "case-probe").touch()
        return not (Path(probe_dir) / "CASE-PROBE").exists()


class TestAskReviewPermissions:
    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        """The hook also checks the resolved config-dir root (gap (c) — see
        docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
        Known gaps list), so every test needs an ambient CLAUDE_CONFIG_DIR
        that can't coincide with a test fixture path like /some/project/...
        A test that needs its own CLAUDE_CONFIG_DIR passes extra_env, which
        overrides this default."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "unused-ambient-config-dir"))

    @pytest.mark.parametrize(
        "tool_input",
        [
            edit_input("/some/project/.claude/settings.json"),
            edit_input("/some/project/.claude/settings.local.json"),
            write_input("/some/project/.claude/settings.json"),
            multiedit_input("/some/project/.claude/settings.json"),
        ],
        ids=["edit-settings", "edit-settings-local", "write-settings", "multiedit-settings"],
    )
    def test_settings_edits_ask(self, tool_input):
        assert run_hook(REVIEW_PERMS_HOOK, tool_input) == "ask"

    @pytest.mark.parametrize(
        "path",
        [
            "/some/project/package.json",
            "/some/project/.claude/CLAUDE.md",
            "/some/project/.claude/skills/foo.md",
            "/some/project/.claude/mysettings.json",
            "/some/project/.claude/settings/x.json",
        ],
    )
    @pytest.mark.parametrize(
        "build_input",
        [edit_input, write_input, multiedit_input],
        ids=["edit", "write", "multiedit"],
    )
    def test_non_settings_paths_allowed(self, build_input, path):
        assert run_hook(REVIEW_PERMS_HOOK, build_input(path)) == "allow"

    def test_bash_tool_allowed(self):
        assert run_hook(REVIEW_PERMS_HOOK, bash_input("cat /some/project/.claude/settings.json")) == "allow"

    def test_read_tool_on_settings_path_allowed(self):
        """Isolates the tool-name filter: unlike `test_bash_tool_allowed`
        (whose input has no `file_path` and would pass regardless), this uses
        an actual settings path so only the tool-name check can produce the
        allow."""
        assert run_hook(REVIEW_PERMS_HOOK, read_input("/some/project/.claude/settings.json")) == "allow"

    def test_settings_file_at_config_dir_root_with_no_claude_segment_asks(self, tmp_path):
        """gap (c): a config dir with no `.claude/` segment (e.g. a
        non-personal CLAUDE_CONFIG_DIR account root under
        ~/.local/state/claude-accounts/<account>/) still holds its own
        settings.json directly at its root."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    def test_settings_file_nested_under_config_dir_root_not_matched(self, tmp_path):
        """A settings file nested below the config-dir root, rather than
        directly at it, is outside gap (c)'s fixed shape and stays allowed.
        The same `[^/]*` boundary the `.claude/settings*.json` match uses
        excludes it."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "nested" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "file_path",
        [
            "/some/project/.claude//settings.json",
            "/some/project/.claude/./settings.json",
            "/some/project/.claude/sub/../settings.json",
            "/some/project/.CLAUDE/SETTINGS.json",
        ],
        ids=["double-slash", "dot-segment", "dotdot-segment", "case-variant"],
    )
    def test_aliased_or_case_varied_settings_path_asks(self, file_path):
        """Pins that the regex is case-insensitive and alias-normalized
        (gap (h)). The fixtures decorate the path between `.claude` and
        `settings` so the raw string doesn't already contain the literal
        substring; a decoration elsewhere in the path would already pass
        without normalization and isn't covered here."""
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(file_path)) == "ask"

    def test_aliased_nested_settings_path_stays_allowed(self):
        """A `.`-decorated path that normalizes to a nested `settings/x.json`
        still falls outside the `[^/]*` boundary. See
        `test_non_settings_paths_allowed` for the same exclusion on the raw
        path."""
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input("/some/project/.claude/./settings/x.json"),
            )
            == "allow"
        )

    def test_settings_path_through_symlinked_claude_ancestor_asks(self, tmp_path):
        """Regression control: this fixture's undecorated `file_path`
        already matches the unconditional top-of-script literal check
        (`\\.claude/settings[^/]*\\.json$`) before the case arm -- and its
        `_lib_realpath_m` call -- is ever reached, so this pins that a
        symlinked `.claude` ancestor doesn't defeat that pre-existing
        literal match. It does not exercise `_lib_realpath_m` at all; see
        test_aliased_settings_path_through_symlinked_claude_ancestor_asks
        for the fixture shape that forces control into the case arm's
        raw-vs-normalized comparison under symlink resolution."""
        real_target = tmp_path / "dotfiles" / "claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = project_dir / ".claude" / "settings.json"
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(str(file_path))) == "ask"

    def test_aliased_settings_path_through_symlinked_claude_ancestor_asks(self, tmp_path):
        """Unlike test_settings_path_through_symlinked_claude_ancestor_asks,
        this decorates `file_path` with a `.` segment between `.claude` and
        `settings`, breaking the unconditional top-of-script literal check
        and forcing control into the case arm. The symlink target is itself
        named `.claude` (unlike that sibling test's target, named `claude`
        with no dot) so the case arm's normalized-path match still has a
        `.claude` segment to find after `_lib_realpath_m` both follows the
        symlink and collapses the `.` decoration -- the only fixture shape
        that exercises the case arm's `_lib_realpath_m` call together with
        symlink resolution."""
        real_target = tmp_path / "dotfiles" / ".claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = f"{project_dir}/.claude/./settings.json"
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(file_path)) == "ask"

    def test_aliased_settings_path_through_symlinked_claude_ancestor_asks_under_forced_realpath_fallback(
        self, tmp_path
    ):
        """Same symlinked-ancestor + `.`-decorated-leaf combination as
        test_aliased_settings_path_through_symlinked_claude_ancestor_asks above,
        but forces `_lib_realpath_m`'s manual fallback loop via
        `_forced_fallback_path_env` instead of the host's native
        `realpath -m`/`grealpath`. Pins the gap-(h) invariant on the
        BusyBox/no-`grealpath` host class this repo documents as
        supported, which the sibling test never exercises since it runs
        with no PATH override."""
        real_target = tmp_path / "dotfiles" / ".claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = f"{project_dir}/.claude/./settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(file_path),
                extra_env={"PATH": _forced_fallback_path_env(tmp_path)},
            )
            == "ask"
        )

    def test_settings_path_through_symlinked_claude_ancestor_allow_control(self, tmp_path):
        """`old-settings-archive.json` passes the `*settings*.json` prefilter
        but fails the anchored `settings[^/]*\\.json$` match both before and
        after symlink resolution."""
        real_target = tmp_path / "dotfiles" / "claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = project_dir / ".claude" / "old-settings-archive.json"
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(str(file_path))) == "allow"

    def test_aliased_path_at_config_dir_root_asks(self, tmp_path):
        """Combines gap (h) and gap (c): an aliased path reaching a settings
        file at a config-dir root (no `.claude/` segment) needs the
        config-dir-root arm to also compare against the normalized path, not
        just the raw one."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / "sub" / ".." / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    @pytest.mark.parametrize(
        "filename",
        ["SETTINGS.json", "settings.local.json"],
        ids=["case-variant", "settings-local"],
    )
    def test_config_dir_root_settings_variants_ask(self, tmp_path, filename):
        """The config-dir-root arm (gap (c)) covers the same filename variants
        as the `.claude/settings*.json` arm: a case variant and
        settings.local.json, not just the bare lowercase settings.json
        already covered by test_settings_file_at_config_dir_root_with_no_claude_segment_asks."""
        config_dir = tmp_path / "claude-accounts" / "work"
        file_path = config_dir / filename
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )

    def test_config_dir_dot_is_escaped_not_treated_as_wildcard(self, tmp_path):
        """Pins that the config-dir path's `.` escaping in the built grep -E
        pattern is real: an unescaped `.` matches any character, so a
        same-length sibling directory differing only at the dot's position
        would wrongly ask too if the escape were broken."""
        config_dir = tmp_path / "work.2024"
        exact_match_path = config_dir / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(exact_match_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )
        near_miss_path = tmp_path / "workX2024" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(near_miss_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    @pytest.mark.parametrize(
        "config_dir_name, near_miss_dir_name",
        [
            ("work+2024", "workk2024"),
            ("work*2024", "wor2024"),
            ("work?2024", "wor2024"),
            ("work{1,2}2024", "workk2024"),
            ("work|2024", "workx2024"),
            ("work[2024]", "work2"),
        ],
        ids=["plus", "star", "question-mark", "brace-interval", "pipe", "left-bracket"],
    )
    def test_config_dir_other_ere_metacharacters_are_escaped_not_treated_as_operators(
        self, tmp_path, config_dir_name, near_miss_dir_name
    ):
        """Extends the dot-escaping test above to the escape class's other
        members (`s/[].[\\*^$(){}+?|]/\\&/g`): `+`, `*`, `?`, `{}` interval
        syntax, `|` alternation, and `[`/`]` bracket-expression start/end.
        Each near-miss directory name is the string an unescaped
        interpretation of the metacharacter would incorrectly match against
        the fixed config-dir pattern. For `[`, an unescaped `[2024]` reads as
        an ERE bracket expression matching exactly one of `0`/`2`/`4`, which
        is what makes `work2` the near miss. `]` and `}` are excluded here:
        both are ordinary (non-special) ERE characters unless preceded by an
        unescaped `[` or `{` respectively, and this class always escapes `[`
        and `{` too, so no input can construct a near-miss shape that
        discriminates escaped from unescaped for either character."""
        config_dir = tmp_path / config_dir_name
        exact_match_path = config_dir / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(exact_match_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "ask"
        )
        near_miss_path = tmp_path / near_miss_dir_name / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(near_miss_path)),
                extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
            )
            == "allow"
        )

    def test_config_dir_escape_class_backslash_escapes_bracket_and_brace_directly(self):
        """Pins the sed transformation's own output for `]` and `}`, not an
        ask/allow side effect: unlike the near-miss tests above, no fixture
        can discriminate escaped from unescaped for either character (see the
        docstring on test_config_dir_other_ere_metacharacters_are_escaped_not_treated_as_operators),
        so this asserts directly on what the mirrored sed script does to a
        string containing each."""
        escaped = subprocess.run(
            ["sed", _CONFIG_DIR_ESCAPE_SED_SCRIPT],
            input="work]2024\nwork}2024\n",
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert escaped == "work\\]2024\nwork\\}2024\n"

    def test_config_dir_resolution_failure_falls_through_to_allow(self, tmp_path):
        """Pins the hook's own `CONFIG_DIR=... || CONFIG_DIR=""` fallback when
        `_lib_config_dir` itself fails (CLAUDE_CONFIG_DIR empty and HOME
        empty, per `_lib_config_dir`'s own documented contract)."""
        file_path = tmp_path / "claude-accounts" / "work" / "settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(str(file_path)),
                extra_env={"CLAUDE_CONFIG_DIR": "", "HOME": ""},
            )
            == "allow"
        )

    def test_partial_realpath_failure_falls_back_to_raw_vs_raw_and_still_asks(self, tmp_path):
        """Pins that when only one of the two `_lib_realpath_m` calls
        succeeds, the hook compares raw-vs-raw rather than mixing a
        normalized side with a raw one (the both-succeed guard)."""
        config_dir_real = tmp_path / "claude-accounts" / "work"
        config_dir_real.mkdir(parents=True)
        config_dir_raw = f"{config_dir_real.parent}/./work"
        file_path_raw = f"{config_dir_raw}/settings.json"
        # dangling: _lib_realpath_m fails on FILE_PATH only.
        # Path(file_path_raw) collapses the "/./" segment on construction, but the
        # kernel resolves it identically, so the symlink still lands at the real
        # location config_dir_real names.
        Path(file_path_raw).symlink_to(config_dir_real / "does-not-exist")
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(file_path_raw),
                extra_env={
                    "CLAUDE_CONFIG_DIR": config_dir_raw,
                    "PATH": _forced_fallback_path_env(tmp_path),
                },
            )
            == "ask"
        )

    @pytest.mark.skipif(
        not _filesystem_is_case_sensitive(),
        reason="dangling_config_dir's 'WORK' would collide with config_dir_real's "
        "'work' as the same filesystem entry on a case-insensitive filesystem "
        "(default macOS APFS/HFS+), making the symlink_to() below raise FileExistsError.",
    )
    def test_partial_config_dir_realpath_failure_falls_back_to_raw_vs_raw_and_still_asks(self, tmp_path):
        """Symmetric to test_partial_realpath_failure_falls_back_to_raw_vs_raw_and_still_asks,
        forcing the failure on the CONFIG_DIR side instead of the FILE_PATH side. The
        file path's own ancestor carries a "/./" segment so its normalized form
        textually diverges from its raw form. That divergence is what makes the
        both-succeed guard's raw-vs-raw fallback — not a
        normalized-FILE-vs-raw-CONFIG_DIR mix — load-bearing for the match."""
        config_dir_real = tmp_path / "claude-accounts" / "work"
        config_dir_real.mkdir(parents=True)
        file_path_raw = f"{config_dir_real.parent}/./work/settings.json"
        config_dir_raw = f"{config_dir_real.parent}/./WORK"
        # dangling: _lib_realpath_m fails on CLAUDE_CONFIG_DIR only. `WORK` is a
        # distinct filesystem entry from `work` on a case-sensitive filesystem, so
        # the dangling symlink never touches config_dir_real.
        # The hook's own case-fold (tr) is what makes the raw match still fire
        # despite the case difference between the two.
        dangling_config_dir = config_dir_real.parent / "WORK"
        dangling_config_dir.symlink_to(config_dir_real.parent / "does-not-exist")
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(file_path_raw),
                extra_env={
                    "CLAUDE_CONFIG_DIR": config_dir_raw,
                    "PATH": _forced_fallback_path_env(tmp_path),
                },
            )
            == "ask"
        )

    def test_partial_realpath_failure_with_non_concatenated_alias_stays_allowed(self, tmp_path):
        """Pins the disclosed residual, not a bug to fix: a doubled-slash decoration
        reaches the same settings.json without literally concatenating the raw
        config-dir string, so the both-or-neither guard's raw-vs-raw fallback misses
        the anchored pattern and no ask fires. See
        docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
        Open residuals and re-review triggers section, the
        `_lib_config_dir`/`_lib_realpath_m` failure bullet."""
        config_dir_real = tmp_path / "claude-accounts" / "work"
        config_dir_real.mkdir(parents=True)
        config_dir_raw = str(config_dir_real)
        file_path_alias = f"{config_dir_raw}//settings.json"
        # dangling: _lib_realpath_m fails on FILE_PATH only. Multiple slashes are
        # filesystem-nonsemantic, so Path()'s own slash-collapsing here still
        # lands the symlink at the real location config_dir_real/settings.json.
        Path(file_path_alias).symlink_to(config_dir_real / "does-not-exist")
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(file_path_alias),
                extra_env={
                    "CLAUDE_CONFIG_DIR": config_dir_raw,
                    "PATH": _forced_fallback_path_env(tmp_path),
                },
            )
            == "allow"
        )

    def test_dot_segment_aliased_path_asks_under_forced_realpath_fallback(self, tmp_path):
        """Regression pin: `_lib_realpath_m`'s manual fallback must drop a
        `.` component rather than reattach it, or the hook's
        `.claude/settings` match misses an aliased path. Forces the fallback
        via the PATH shim (no native `realpath -m`/`grealpath`)."""
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input("/some/project/.claude/./settings.json"),
                extra_env={"PATH": _forced_fallback_path_env(tmp_path)},
            )
            == "ask"
        )

    def test_dotdot_segment_aliased_path_stays_allowed_under_forced_realpath_fallback(self, tmp_path):
        """Pins the disclosed residual, not a bug to fix: `_lib_realpath_m`
        deliberately fails closed on a `..` component in the manual
        fallback's unresolved suffix, since a `..` there could defeat
        another caller's same-prefix boundary check. On a host that takes
        the manual fallback, a `..`-segment alias to a nonexistent settings
        path normalizes to nothing and the raw-path comparison doesn't match
        the literal `../` segment either. See gap (h) in
        docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
        Known gaps list."""
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input("/some/project/.claude/sub/../settings.json"),
                extra_env={"PATH": _forced_fallback_path_env(tmp_path)},
            )
            == "allow"
        )

    def test_double_slash_aliased_path_asks_under_forced_realpath_fallback(self, tmp_path):
        """Completes the gap (h) alias-normalization matrix under forced
        fallback: a doubled slash is collapsed by `dirname`/`basename`
        independent of the `.`/`..` case arms, so this test only needs to
        pin that it still asks."""
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input("/some/project/.claude//settings.json"),
                extra_env={"PATH": _forced_fallback_path_env(tmp_path)},
            )
            == "ask"
        )

    def test_depth_cap_defeats_dot_only_alias_under_forced_realpath_fallback(self, tmp_path):
        """Pins the disclosed residual, not a bug to fix: on a fallback-only
        host, a `.`-decorated alias (no `..` component) whose ancestor chain
        exceeds `_LIB_REALPATH_M_FALLBACK_MAX_DEPTH` defeats both the
        normalized match (the fallback fails closed on the depth cap) and
        the raw-path fallback comparison (the decoration hides the literal
        `.claude/settings*.json` substring), so no ask fires -- the same
        alias-blind outcome as the pre-gap-(h)-fix hook, reached through a
        different trigger than the `../`-segment shape gap (h)'s own
        writeup already discloses. See gap (h) in
        docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
        Known gaps list and the depth-cap alias gap under Open residuals."""
        levels = "/".join(f"level{i}" for i in range(_REALPATH_M_FALLBACK_MAX_DEPTH + 6))
        file_path = f"{tmp_path}/{levels}/.claude/./settings.json"
        assert (
            run_hook(
                REVIEW_PERMS_HOOK,
                edit_input(file_path),
                extra_env={"PATH": _forced_fallback_path_env(tmp_path)},
            )
            == "allow"
        )

    def test_symlinked_config_json_leaf_bypasses_cheap_prefilter_stays_allowed(self, tmp_path):
        """Pins the disclosed residual, not a bug to fix: the cheap
        `*settings*.json` prefilter runs on FILE_PATH's own literal string
        before any `_lib_realpath_m` call, so a symlinked leaf whose name
        never contains "settings" exits allow before the symlink could ever
        be resolved. Here `.claude/config.json` is itself a symlink to a
        real `settings.json`. See gap (i) in
        docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
        Known gaps list."""
        config_dir = tmp_path / "project" / ".claude"
        config_dir.mkdir(parents=True)
        real_settings = config_dir / "settings.json"
        real_settings.write_text("{}")
        symlinked_leaf = config_dir / "config.json"
        symlinked_leaf.symlink_to(real_settings)
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(str(symlinked_leaf))) == "allow"

    def test_ask_reason_names_allow_deny_default_mode_and_review_skill(self):
        """Pins the ask-reason wording — it must name permissions.allow,
        permissions.deny, permissions.defaultMode and /review-permissions."""
        reason = run_hook_reason(
            REVIEW_PERMS_HOOK, edit_input("/some/project/.claude/settings.json")
        )
        assert reason is not None
        assert "permissions.deny" in reason
        assert "permissions.defaultMode" in reason
        assert "permissions.allow" in reason
        assert "/review-permissions" in reason

    def test_unreadable_lib_sh_fails_open_with_stderr_diagnostic(self, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from
        its real location; running a copy with no adjacent _lib.sh exercises
        the "could not source _lib.sh" exit-0 path directly. Fail-open is
        correct here (a broken _lib.sh must not block Edit/Write), but the
        silent-allow on a security-relevant gate must leave a stderr trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / REVIEW_PERMS_HOOK.name
            shutil.copy2(REVIEW_PERMS_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                ["bash", str(tmp_hook)],
                input=json.dumps(edit_input("/some/project/.claude/settings.json")),
                cwd=str(tmp_path),
                env={**os.environ},
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
        assert "[ask-review-permissions]" in result.stderr
        assert "could not source _lib.sh" in result.stderr
