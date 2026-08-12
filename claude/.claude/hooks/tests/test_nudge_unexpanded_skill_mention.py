"""Tests for nudge-unexpanded-skill-mention.sh.

The hook reports one condition: a prompt names an installed skill
(`/<name>`) outside the leading position the harness auto-expands. Every
other path — no candidate, an unresolved candidate, a candidate confined to
a fence/inline-span/leading-run, a missing dependency, malformed input — must
stay silent, and every path must exit 0.

Fixture skill names are prefixed `zzz-fixture-` so a broken resolver that
accidentally reaches the real repo's `.claude/skills/` cannot pass by
resolving a genuine skill instead of the sandboxed fixture.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, build_path_without

NUDGE_HOOK = HOOKS_DIR / "nudge-unexpanded-skill-mention.sh"

FIXTURE_SKILL = "zzz-fixture-skill"
FIXTURE_SKILL_A = "zzz-fixture-skill-a"
FIXTURE_SKILL_B = "zzz-fixture-skill-b"


def _make_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n")
    return skill_dir


@pytest.fixture
def personal_skill(isolated_home):
    """A fixture skill under the sandboxed home's personal skills root
    ($CONFIG_DIR/skills/<name>/SKILL.md, the hook's first resolution root)."""
    _make_skill(isolated_home / ".claude" / "skills", FIXTURE_SKILL)
    return isolated_home


@pytest.fixture
def project_workspace(tmp_path):
    """A synthetic project root with .claude/skills/, and a cwd two levels
    below it -- proves the ancestor walk climbs past the immediate cwd
    rather than only checking it directly."""
    project_root = tmp_path / "project"
    skills_root = project_root / ".claude" / "skills"
    _make_skill(skills_root, FIXTURE_SKILL_A)
    _make_skill(skills_root, FIXTURE_SKILL_B)
    cwd = project_root / "src" / "nested"
    cwd.mkdir(parents=True)
    return project_root, cwd


def _run(
    prompt: str,
    cwd,
    home,
    permission_mode: str | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    payload: dict = {"prompt": prompt, "cwd": str(cwd)}
    if permission_mode is not None:
        payload["permission_mode"] = permission_mode
    env = {**os.environ, "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(NUDGE_HOOK)],
        input=json.dumps(payload),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def _context(result: subprocess.CompletedProcess) -> str | None:
    """The advisory string, or None when the hook stayed silent."""
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


class TestFiresOnMidPromptMention:
    def test_fires_and_names_the_skill(self, personal_skill, tmp_path):
        result = _run(f"do X /{FIXTURE_SKILL}", tmp_path, personal_skill)

        assert result.returncode == 0
        context = _context(result)
        assert context is not None, "a mid-prompt mention should be reported"
        assert f"/{FIXTURE_SKILL}" in context

    def test_declares_the_userpromptsubmit_event(self, personal_skill, tmp_path):
        """The harness routes additionalContext by hookEventName; a
        mismatched name silently drops the advisory."""
        result = _run(f"do X /{FIXTURE_SKILL}", tmp_path, personal_skill)

        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


class TestSilentOnLeadingPosition:
    def test_leading_mention_is_silent(self, personal_skill, tmp_path):
        """The harness already expanded a leading mention -- nothing to do."""
        result = _run(f"/{FIXTURE_SKILL} do X", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_leading_whitespace_is_silent(self, personal_skill, tmp_path):
        result = _run(f"  /{FIXTURE_SKILL} do X", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_leading_chained_run_is_silent(self, project_workspace, isolated_home):
        """As of v2.1.199 the harness expands a leading chain of up to six
        skills -- matching only the first token would nag on this shape."""
        project_root, cwd = project_workspace
        result = _run(
            f"/{FIXTURE_SKILL_A} /{FIXTURE_SKILL_B} do X", cwd, isolated_home
        )

        assert result.returncode == 0
        assert _context(result) is None


class TestSilentOnUnresolvedMention:
    def test_nonexistent_skill_name_is_silent(self, isolated_home, tmp_path):
        result = _run("see /not-a-real-skill", tmp_path, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None

    def test_path_shaped_noise_is_silent(self, isolated_home, tmp_path):
        """Path-shaped text fails resolution with no special-casing --
        each '/'-delimited segment is just another unresolved candidate.
        Non-home-rooted per this repo's private-project-refs commit gate."""
        result = _run("check /opt/pkg/bin and /or", tmp_path, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None


class TestFenceAndInlineSpanStripping:
    def test_inline_code_span_is_silent(self, personal_skill, tmp_path):
        result = _run(f"run `/{FIXTURE_SKILL}` later", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_mention_inside_fenced_block_is_silent(self, personal_skill, tmp_path):
        prompt = f"before\n```\nmention /{FIXTURE_SKILL} here\n```\nafter"
        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_unfenced_occurrence_wins_alongside_a_fenced_one(
        self, personal_skill, tmp_path
    ):
        prompt = f"outside /{FIXTURE_SKILL}\n```\nfenced /{FIXTURE_SKILL}\n```\n"
        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        context = _context(result)
        assert context is not None
        assert f"/{FIXTURE_SKILL}" in context

    def test_unclosed_fenced_block_is_silent(self, personal_skill, tmp_path):
        """An unclosed fence drops everything from the opener onward --
        defined behavior, not a bug."""
        prompt = f"before\n```\nmention /{FIXTURE_SKILL} with no closer"
        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_crlf_line_endings_around_fence_still_toggle_it(
        self, personal_skill, tmp_path
    ):
        prompt = f"before\r\n```\r\nmention /{FIXTURE_SKILL} here\r\n```\r\nafter"
        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None, "CRLF fence markers must still be detected"


class TestTokenGrammar:
    def test_exact_match_only_no_prefix_collision(self, personal_skill, tmp_path):
        """A longer name sharing FIXTURE_SKILL as a prefix must not resolve
        against it -- substring matching would let an attacker's tail ride
        along with a legitimate skill name."""
        result = _run(f"do X /{FIXTURE_SKILL}-longer", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_trailing_punctuation_is_stripped(self, personal_skill, tmp_path):
        result = _run(f"do X /{FIXTURE_SKILL}.", tmp_path, personal_skill)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert f"/{FIXTURE_SKILL}" in context

    def test_trailing_comma_is_stripped(self, personal_skill, tmp_path):
        result = _run(f"do X /{FIXTURE_SKILL},", tmp_path, personal_skill)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert f"/{FIXTURE_SKILL}" in context

    def test_case_variant_exits_zero(self, personal_skill, tmp_path):
        """Case-sensitivity of the [[ -f ]] resolution test tracks the
        underlying filesystem (case-insensitive APFS vs case-sensitive
        Linux) -- this only pins that the hook stays well-behaved (exit 0,
        parseable-or-empty stdout) under either outcome, not which outcome."""
        capitalized = FIXTURE_SKILL.title()
        result = _run(f"do X /{capitalized}", tmp_path, personal_skill)

        assert result.returncode == 0
        if result.stdout.strip():
            json.loads(result.stdout)  # must still be well-formed if non-empty

    def test_metacharacter_token_never_resolves_or_is_echoed(
        self, personal_skill, tmp_path
    ):
        """The capture regex truncates at the first disallowed character, so
        this legitimately fires on the clean "/zzz-fixture-skill" prefix --
        what must never appear is the raw metacharacter span itself, proving
        only the validated token is ever echoed, not a span of the prompt."""
        prompt = f"see /{FIXTURE_SKILL};rm -rf /tmp and /$(whoami) and /`id`"
        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        context = _context(result)
        assert context is not None
        assert f"/{FIXTURE_SKILL}" in context
        assert ";rm" not in context
        assert "$(whoami)" not in context
        assert "`id`" not in context

    def test_traversal_canary_never_named(self, personal_skill, tmp_path):
        """A real SKILL.md reachable only via a '../' traversal from the two
        resolution roots must never be named -- token capture stops at the
        first '.' or '/', so a traversal-shaped mention can only ever
        resolve to the bare trailing token, not the full path."""
        canary_root = tmp_path / "elsewhere" / "skills"
        _make_skill(canary_root, "canary-skill")

        result = _run("see /../elsewhere/skills/canary-skill", tmp_path, personal_skill)

        assert result.returncode == 0
        # "canary-skill" IS extracted as a bare candidate (the regex stops at
        # each "/"), but it must not resolve: resolution only ever joins the
        # two canonical roots with a validated token, never the traversal
        # path components ("elsewhere", "skills") captured alongside it.
        assert _context(result) is None


class TestDedup:
    def test_same_skill_named_twice_is_reported_once(self, personal_skill, tmp_path):
        prompt = f"mention /{FIXTURE_SKILL} twice /{FIXTURE_SKILL} here"
        result = _run(prompt, tmp_path, personal_skill)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert context.count(f"/{FIXTURE_SKILL}") == 1


class TestMultipleDistinctSkills:
    def test_fires_naming_both(self, project_workspace, isolated_home):
        project_root, cwd = project_workspace
        prompt = f"do X /{FIXTURE_SKILL_A} /{FIXTURE_SKILL_B}"
        result = _run(prompt, cwd, isolated_home)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert f"/{FIXTURE_SKILL_A}" in context
        assert f"/{FIXTURE_SKILL_B}" in context


class TestDiscussionProseFalsePositive:
    def test_fires_on_a_question_that_merely_discusses_a_skill(
        self, project_workspace, isolated_home
    ):
        """Accepted false positive, documented in the hook's own header:
        the hook cannot distinguish intent to invoke from mere discussion."""
        project_root, cwd = project_workspace
        prompt = f"should I use /{FIXTURE_SKILL_A} here or /{FIXTURE_SKILL_B}?"
        result = _run(prompt, cwd, isolated_home)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None


class TestPlanModeSentence:
    def test_fires_with_the_precedence_sentence_in_plan_mode(
        self, personal_skill, tmp_path
    ):
        result = _run(
            f"do X /{FIXTURE_SKILL}", tmp_path, personal_skill, permission_mode="plan"
        )

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert "plan mode" in context

    def test_no_plan_mode_sentence_outside_plan_mode(self, personal_skill, tmp_path):
        result = _run(f"do X /{FIXTURE_SKILL}", tmp_path, personal_skill)

        context = _context(result)
        assert result.returncode == 0
        assert context is not None
        assert "plan mode" not in context


class TestExitsZeroOnDegenerateInput:
    """A non-zero exit from UserPromptSubmit risks disrupting prompt
    submission, so every degenerate input must still exit 0 and stay quiet."""

    def test_empty_stdin(self, isolated_home, tmp_path):
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input="",
            cwd=str(tmp_path),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_malformed_json_stdin(self, isolated_home, tmp_path):
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input="{not json",
            cwd=str(tmp_path),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_prompt_field(self, isolated_home, tmp_path):
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input=json.dumps({"cwd": str(tmp_path)}),
            cwd=str(tmp_path),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_multi_megabyte_prompt_is_bounded(self, isolated_home, tmp_path):
        """Bounded by the fixed byte cap -- must complete quickly and exit 0
        regardless of whether the truncated remainder happens to fire."""
        huge_prompt = "x " * (3 * 1024 * 1024)  # ~6 MB
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input=json.dumps({"prompt": huge_prompt, "cwd": str(tmp_path)}),
            cwd=str(tmp_path),
            env={**os.environ, "HOME": str(isolated_home)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_jq_absent_is_silent(self, personal_skill, tmp_path):
        farm_dir = tmp_path / "path-without-jq"
        farm_dir.mkdir()
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input=json.dumps({"prompt": f"do X /{FIXTURE_SKILL}", "cwd": str(tmp_path)}),
            cwd=str(tmp_path),
            env={
                **os.environ,
                "HOME": str(personal_skill),
                "PATH": build_path_without("jq", farm_dir),
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unreadable_lib_sh_is_silent(self, isolated_home, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from
        its real location; running a copy with no adjacent _lib.sh exercises
        the "could not source _lib.sh" exit-0 path directly."""
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / NUDGE_HOOK.name
            shutil.copy2(NUDGE_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                ["bash", str(tmp_hook)],
                input=json.dumps({"prompt": "do X /whatever", "cwd": str(tmp_path)}),
                cwd=str(tmp_path),
                env={**os.environ, "HOME": str(isolated_home)},
                capture_output=True,
                text=True,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_unresolvable_config_dir_is_silent(self, tmp_path):
        """Empty $HOME and no $CLAUDE_CONFIG_DIR: _lib_config_dir fails, and
        CONFIG_DIR=$(_lib_config_dir) || exit 0 must catch it."""
        env = {**os.environ, "HOME": "", "PATH": os.environ.get("PATH", "")}
        env.pop("CLAUDE_CONFIG_DIR", None)
        result = subprocess.run(
            ["bash", str(NUDGE_HOOK)],
            input=json.dumps({"prompt": "do X /whatever", "cwd": str(tmp_path)}),
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestIsolationFromTheRealRepo:
    def test_real_skill_name_from_a_non_repo_cwd_is_silent(self, isolated_home, tmp_path):
        """A real installed skill (plan-it), a cwd outside any repo, and a
        sandboxed $HOME with no skills/ subtree of its own: the ancestor
        walk must not climb past tmp_path and find the real repo's
        .claude/skills by accident."""
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        result = _run("do X /plan-it", outside, isolated_home)

        assert result.returncode == 0
        assert _context(result) is None


class TestLeadingChainBoundary:
    """The hook skips a leading run of at most six mentions. Without these,
    an edit changing that bound passes the whole suite unnoticed."""

    def test_six_chained_leading_mentions_are_all_skipped(
        self, personal_skill, tmp_path
    ):
        chain = " ".join(f"/{FIXTURE_SKILL}" for _ in range(6))
        result = _run(f"{chain} do X", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None

    def test_seventh_chained_mention_fires(self, personal_skill, tmp_path):
        """The harness expands six; a seventh is genuinely unexpanded."""
        filler = " ".join(f"/zzz-fixture-filler-{i}" for i in range(6))
        result = _run(f"{filler} /{FIXTURE_SKILL} do X", tmp_path, personal_skill)

        assert result.returncode == 0
        context = _context(result)
        assert context is not None, "a 7th chained mention is not auto-expanded"
        assert f"/{FIXTURE_SKILL}" in context


class TestPluginSkillColonFormStaysSilent:
    def test_plugin_qualified_mention_is_silent(self, personal_skill, tmp_path):
        """`plugin:skill` mentions never resolve -- a documented gap. Pinned
        so widening the token grammar to include ':' cannot regress it
        unnoticed."""
        result = _run(f"do X /zzz-plugin:{FIXTURE_SKILL}", tmp_path, personal_skill)

        assert result.returncode == 0
        assert _context(result) is None


class TestMultiByteTruncationBoundary:
    def test_multibyte_character_split_by_the_byte_cap_is_handled(
        self, personal_skill, tmp_path
    ):
        """A 4-byte emoji straddling _MAX_SCAN_BYTES must not abort a text
        pass: under a UTF-8 locale tr/awk/sed/grep each emit no stdout on a
        partial sequence, which would discard the whole scanned prompt --
        including a mention that appeared long before the cut point."""
        max_scan_bytes = 65536
        emoji = "\U0001f600"
        prefix = f"do X /{FIXTURE_SKILL} "
        # Place the emoji so the cap falls inside it: two of its four bytes
        # survive truncation, leaving a partial sequence in the scanned text.
        filler = "x" * (max_scan_bytes - len(prefix.encode()) - 2)
        prompt = f"{prefix}{filler}{emoji} trailing"
        assert len(prompt.encode()) > max_scan_bytes, "cap must actually bite"

        result = _run(prompt, tmp_path, personal_skill)

        assert result.returncode == 0
        assert result.stderr == "", f"unexpected stderr: {result.stderr!r}"
        context = _context(result)
        assert context is not None, "a mention before the split must survive"
        assert f"/{FIXTURE_SKILL}" in context


class TestInjectionPayloadIsNeverExecuted:
    def test_metacharacter_payload_produces_no_side_effect(
        self, personal_skill, tmp_path
    ):
        """Proves non-execution, not merely non-echo: a payload whose shell
        expansion would create a canary file must leave no canary behind."""
        canary = tmp_path / "pwned"
        payload = (
            f"do X /{FIXTURE_SKILL}; touch {canary} "
            f"$(touch {canary}) `touch {canary}`"
        )
        result = _run(payload, tmp_path, personal_skill)

        assert result.returncode == 0
        assert not canary.exists(), "payload was executed as shell"
        context = _context(result)
        assert context is not None
        assert "touch" not in context, "raw prompt span leaked into context"
