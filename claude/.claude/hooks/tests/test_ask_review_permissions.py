"""Tests for ask-review-permissions.sh."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    bash_input,
    edit_input,
    multiedit_input,
    read_input,
    run_hook,
    run_hook_reason,
    write_input,
)

REVIEW_PERMS_HOOK = HOOKS_DIR / "ask-review-permissions.sh"

# Forces _lib_realpath_m's native `-m` fast path to fail (so a call falls
# through to the manual ancestor-walk fallback) by shadowing `realpath` on
# PATH -- same shim test_lib.py's TestLibRealpathM uses for the same
# purpose. A non-`-m` invocation still execs the real binary, so the
# fallback loop's own `realpath --` lookups keep working.
_FORCED_FALLBACK_REALPATH_SHIM = textwrap.dedent("""\
    #!/bin/bash
    if [ "$1" = "-m" ]; then
      echo "realpath: illegal option -- m" >&2
      exit 1
    fi
    exec /bin/realpath "$@"
""")


def _forced_fallback_path_env(tmp_path: Path) -> str:
    """Build a PATH whose `realpath` is the forced-fallback shim above,
    ahead of /usr/bin:/bin. The shim dir is placed first specifically to
    exclude any `grealpath` the host might also have on a wider PATH --
    `command -v grealpath` succeeding would skip the fallback branch this
    exists to force."""
    shim_dir = tmp_path / "realpath_shim"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "realpath"
    shim.write_text(_FORCED_FALLBACK_REALPATH_SHIM)
    shim.chmod(0o755)
    return f"{shim_dir}:/usr/bin:/bin"


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
        """Regression control: `_lib_realpath_m` follows symlinks even under
        `-m`, so resolving a symlinked `.claude` away would defeat a
        normalized-only match. The raw-path check (run alongside, not
        instead) is what still catches this case."""
        real_target = tmp_path / "dotfiles" / "claude"
        real_target.mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".claude").symlink_to(real_target, target_is_directory=True)
        file_path = project_dir / ".claude" / "settings.json"
        assert run_hook(REVIEW_PERMS_HOOK, edit_input(str(file_path))) == "ask"

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
        members (`s/[.[\\*^$()+?{|]/\\&/g`): `+`, `*`, `?`, `{}` interval
        syntax, `|` alternation, and `[` bracket-expression start. Each
        near-miss directory name is the string an unescaped interpretation
        of the metacharacter would incorrectly match against the fixed
        config-dir pattern. For `[`, an unescaped `[2024]` reads as an ERE
        bracket expression matching exactly one of `0`/`2`/`4`, which is
        what makes `work2` the near miss."""
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

    def test_partial_realpath_failure_with_non_concatenated_alias_stays_allowed(self, tmp_path):
        """Pins the disclosed residual, not a bug to fix: `file_path` reaches
        the same settings.json via a doubled-slash decoration rather than a
        literal concatenation of the raw config-dir string, so the
        both-or-neither guard's raw-vs-raw fallback (forced here by a
        one-sided `_lib_realpath_m` failure) misses the anchored pattern and
        no ask fires. See
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
