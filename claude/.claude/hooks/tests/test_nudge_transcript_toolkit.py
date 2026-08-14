"""Tests for nudge-transcript-toolkit.sh.

PostToolUse Edit|Write|MultiEdit hook that reports via `additionalContext`
when the content just written to a `.py`/`.sh` file looks like a hand-rolled
transcript-corpus glob (`projects/*/*.jsonl`-shaped) -- see
`.claude/plans/transcript-parsing-guardrails.md` for the incident this
backstops.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from helpers import (
    HOOKS_DIR,
    REPO_ROOT,
    _build_subprocess_env,
    build_path_without,
    edit_input,
    multiedit_input,
    write_input,
)

HOOK = HOOKS_DIR / "nudge-transcript-toolkit.sh"

INCIDENT_GLOB = 'glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))'
STRING_CONCAT_GLOB = 'pattern = home + "/.claude/projects/*/*.jsonl"'
OS_PATH_JOIN_GLOB = 'os.path.join(home, ".claude", "projects", "*", "*.jsonl")'
F_STRING_GLOB = 'pattern = f"{claude_projects_root}/*/*.jsonl"'
VARIABLE_INDIRECTION_GLOB = 'glob_suffix = build_glob_suffix()\npattern = "projects/" + glob_suffix\nresults = glob.glob(pattern)'


def _run_hook(
    payload: dict, home: Path | None = None, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = _build_subprocess_env(home, extra_env)
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _fired(result: subprocess.CompletedProcess) -> bool:
    return bool(result.stdout.strip())


def _additional_context(result: subprocess.CompletedProcess) -> str:
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class TestFiresOnIncidentShape:
    def test_write_of_the_incident_glob_fires(self):
        """The exact regression case: a `.py` Write whose content is the
        literal glob that understated the cost figure by ~48%. Without this,
        the hook could pass every other case and still miss what happened."""
        result = _run_hook(write_input("/tmp/scan_transcripts.py", content=INCIDENT_GLOB))
        assert _fired(result)

    def test_write_of_the_incident_glob_to_a_sh_file_fires(self):
        """The extension gate accepts `.py` and `.sh` -- every other fire
        assertion in this suite uses `.py`, so this pins the `.sh` arm as a
        positive match rather than leaving it exercised only as a non-match
        (in the suppressed `claude/.claude/hooks/foo.sh` case below)."""
        result = _run_hook(write_input("/tmp/scan_transcripts.sh", content=INCIDENT_GLOB))
        assert _fired(result)

    def test_edit_new_string_alone_fires(self):
        result = _run_hook(
            edit_input("/tmp/scan_transcripts.py", old_string="pass", new_string=INCIDENT_GLOB)
        )
        assert _fired(result)

    def test_multiedit_single_entry_fires(self):
        """The common MultiEdit shape in practice -- untested in either
        direction before this hook existed."""
        result = _run_hook(
            multiedit_input(
                "/tmp/scan_transcripts.py",
                edits=[{"old_string": "pass", "new_string": INCIDENT_GLOB}],
            )
        )
        assert _fired(result)

    def test_multiedit_split_across_entries_does_not_fire(self):
        """Pinned as a known miss, not left unclaimed: only a single-entry
        MultiEdit is inspected, so a glob split across two edits[] entries
        is never reconstructed."""
        result = _run_hook(
            multiedit_input(
                "/tmp/scan_transcripts.py",
                edits=[
                    {"old_string": "a1", "new_string": 'glob.glob(os.path.expanduser("~/.claude/projects/'},
                    {"old_string": "a2", "new_string": '*/*.jsonl"))'},
                ],
            )
        )
        assert not _fired(result)


class TestNeverFiresOnMarkdown:
    def test_real_repo_markdown_corpus_stays_silent(self):
        """Asserted against the real repo's own .md files containing the
        pattern, not one synthetic fixture, so a future narrowing of the
        extension list can't silently start matching them. Asserts the
        discovered corpus is non-empty first -- if those docs are ever
        edited away this test would otherwise go vacuously true."""
        transcript_glob_pattern = re.compile(r"projects/\*.*\.jsonl")
        matches = [
            p for p in REPO_ROOT.rglob("*.md") if transcript_glob_pattern.search(p.read_text(errors="ignore"))
        ]
        assert matches, "expected the repo's markdown corpus to still discuss this glob shape"
        for md_file in matches:
            content = md_file.read_text(errors="ignore")
            result = _run_hook(write_input(str(md_file), content=content))
            assert not _fired(result), f"{md_file} unexpectedly fired the nudge"


class TestQuietOnToolkitOwnTree:
    def _assert_suppressed(self, file_path: str, home: Path | None = None):
        result = _run_hook(write_input(file_path, content=INCIDENT_GLOB), home=home)
        assert not _fired(result), f"{file_path} should have been suppressed"

    def test_worktree_nested_scripts_dir_suppressed(self):
        self._assert_suppressed(
            "/repo/.claude/worktrees/some-branch/claude/.claude/scripts/foo.py"
        )

    def test_worktree_nested_hooks_dir_suppressed(self):
        self._assert_suppressed(
            "/repo/.claude/worktrees/some-branch/claude/.claude/hooks/foo.sh"
        )

    def test_worktree_nested_tests_dir_suppressed(self):
        """This hook's own test file necessarily contains the glob pattern
        as fixture data -- a regression here would fire the nudge on every
        run of its own suite."""
        self._assert_suppressed(
            "/repo/.claude/worktrees/some-branch/claude/.claude/tests/test_nudge_transcript_toolkit.py"
        )

    def test_plain_clone_scripts_dir_suppressed(self):
        self._assert_suppressed("/repo/claude/.claude/scripts/foo.py")

    def test_plain_clone_hooks_dir_suppressed(self):
        self._assert_suppressed("/repo/claude/.claude/hooks/foo.sh")

    def test_plain_clone_tests_dir_suppressed(self):
        self._assert_suppressed("/repo/claude/.claude/tests/test_nudge_transcript_toolkit.py")

    def test_stowed_scripts_dir_suppressed(self, isolated_home):
        self._assert_suppressed(
            str(isolated_home / ".claude" / "scripts" / "foo.py"), home=isolated_home
        )

    def test_stowed_hooks_dir_suppressed(self, isolated_home):
        self._assert_suppressed(
            str(isolated_home / ".claude" / "hooks" / "foo.sh"), home=isolated_home
        )

    def test_stowed_tests_dir_suppressed(self, isolated_home):
        """The stowed shape a naive substring check fails: claude/.claude
        collapses to a single .claude, so a check anchored to the doubled
        segment would miss this."""
        self._assert_suppressed(
            str(isolated_home / ".claude" / "tests" / "test_nudge_transcript_toolkit.py"),
            home=isolated_home,
        )


class TestSuppressionBoundaryDoesNotOverreach:
    """Allow/deny pairing for the suppression boundary itself (test-conventions
    §7's "both allow and deny paths" principle applied to a suppression check
    rather than an auth boundary): TestQuietOnToolkitOwnTree above pins the
    positive direction; these pin that a path merely resembling the toolkit's
    own tree still fires, so a future widened case arm or a bare-substring
    CONFIG_DIR check -- both named as deliberate non-goals in the hook's own
    header comment -- would be caught here rather than passing silently."""

    def test_unrelated_project_with_a_bare_scripts_dir_still_fires(self):
        result = _run_hook(
            write_input("/home/user/other-project/scripts/foo.py", content=INCIDENT_GLOB)
        )
        assert _fired(result)

    def test_claude_scripts_shaped_path_under_a_different_home_still_fires(self, isolated_home):
        """isolated_home is the $HOME the hook resolves _lib_config_dir
        against; the written file sits under a *different* directory that
        merely looks stowed-shaped ("<somewhere>/.claude/scripts/..."). The
        CONFIG_DIR-anchored suppression check must not degrade to a bare
        ".claude/scripts/" substring match."""
        other_home = isolated_home.parent / "not-the-real-home"
        other_home.mkdir()
        result = _run_hook(
            write_input(str(other_home / ".claude" / "scripts" / "foo.py"), content=INCIDENT_GLOB),
            home=isolated_home,
        )
        assert _fired(result)


class TestMessageIsActionable:
    def test_names_the_toolkit_the_default_and_the_config_file(self):
        """A nudge that doesn't say what to use instead reproduces the
        original failure."""
        result = _run_hook(write_input("/tmp/scan_transcripts.py", content=INCIDENT_GLOB))
        ctx = _additional_context(result)
        assert "transcript-analysis.py" in ctx
        assert "unions" in ctx or "union" in ctx
        assert "~/.claude/transcript-config-dirs" in ctx


class TestDocumentedResiduals:
    """Each construction here is a known miss, pinned by a regression test
    per the hook's `# Documented residuals:` header rather than left
    unclaimed -- these are meant to break when the matcher improves, which
    is the signal to update that header."""

    def test_os_path_join_construction_is_a_residual(self):
        result = _run_hook(write_input("/tmp/scan_transcripts.py", content=OS_PATH_JOIN_GLOB))
        assert not _fired(result)

    def test_f_string_construction_is_a_residual(self):
        result = _run_hook(write_input("/tmp/scan_transcripts.py", content=F_STRING_GLOB))
        assert not _fired(result)

    def test_variable_indirection_is_a_residual(self):
        result = _run_hook(
            write_input("/tmp/scan_transcripts.py", content=VARIABLE_INDIRECTION_GLOB)
        )
        assert not _fired(result)

    def test_non_py_sh_extension_is_a_residual(self):
        result = _run_hook(write_input("/tmp/scan_transcripts.txt", content=INCIDENT_GLOB))
        assert not _fired(result)


class TestFireSideVariety:
    def test_string_concatenation_construction_fires(self):
        """At least one construction beyond the incident's exact literal
        string, explicitly asserted as caught: string concatenation still
        leaves the full glob shape as one contiguous literal substring."""
        result = _run_hook(write_input("/tmp/scan_transcripts.py", content=STRING_CONCAT_GLOB))
        assert _fired(result)


class TestJqAbsentFailsOpen:
    """hook-class: informational never denies -- the counterpart of
    test_hook_alignment.py's test_blocks_when_jq_absent pair for gate hooks,
    asserting the opposite disposition (silent allow, not a hard block).
    Without this, a future edit to this hook's jq-call structure could
    regress into a hard failure with nothing catching it, since
    test_hook_alignment.py's Layer-2 auto-parametrization only covers
    hook-class: gate."""

    def _run_without_jq(self, tmp_path: Path, stdin_text: str) -> subprocess.CompletedProcess:
        farm_dir = tmp_path / "farm"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        env = {**os.environ, "PATH": path_without_jq}
        return subprocess.run(
            [str(HOOK)], input=stdin_text, capture_output=True, text=True, env=env, check=False
        )

    def test_jq_absent_with_malformed_input(self, tmp_path):
        result = self._run_without_jq(tmp_path, "not json")
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_jq_absent_with_valid_payload(self, tmp_path):
        """The realistic case: a legitimate write arrives while jq happens
        to be unavailable, not a malformed request that would stay silent
        for an unrelated reason regardless of jq."""
        payload = write_input("/tmp/scan_transcripts.py", content=INCIDENT_GLOB)
        result = self._run_without_jq(tmp_path, json.dumps(payload))
        assert result.returncode == 0
        assert not result.stdout.strip()
