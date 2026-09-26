"""Tests for transcript_analysis/denials.py (hook-denial detection and classification)."""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, bash_input, build_path_without, run_hook_reason

_SCRIPT = Path(__file__).parent.parent / "transcript-analysis.py"
# "transcript_analysis" below never touches sys.modules (module_from_spec + exec_module
# alone doesn't register it), so it can't shadow the real transcript_analysis package --
# switching to the standard importlib recipe (which does register in sys.modules) would.
_spec = importlib.util.spec_from_file_location("transcript_analysis", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


class TestDropDenialCommandFlagValues:
    """Direct unit coverage for _drop_denial_command_flag_values — the CLI-layer
    --deny-summary tests (TestReviewTrace) exercise this only indirectly
    through the full JSONL-fixture-to-stdout path."""

    def test_empty_tokens_returns_empty_list(self):
        assert _mod.denials._drop_denial_command_flag_values([]) == []

    def test_no_flags_returns_tokens_unchanged(self):
        assert _mod.denials._drop_denial_command_flag_values(["git", "commit", "-m", "x"]) == [
            "git", "commit", "-m", "x",
        ]

    def test_separate_token_flag_drops_flag_and_its_value(self):
        assert _mod.denials._drop_denial_command_flag_values(["git", "-C", "/path", "commit"]) == [
            "git", "commit",
        ]

    def test_double_separate_token_flags_both_dropped(self):
        assert _mod.denials._drop_denial_command_flag_values(
            ["git", "-C", "/path", "-c", "user.name=x", "commit"]
        ) == ["git", "commit"]

    def test_attached_equals_flag_drops_only_the_one_token(self):
        assert _mod.denials._drop_denial_command_flag_values(["git", "--git-dir=/path", "status"]) == [
            "git", "status",
        ]

    def test_all_four_named_flag_forms_drop_their_value(self):
        assert _mod.denials._drop_denial_command_flag_values(["git", "-C", "/a", "commit"]) == ["git", "commit"]
        assert _mod.denials._drop_denial_command_flag_values(["git", "-c", "x=y", "commit"]) == ["git", "commit"]
        assert _mod.denials._drop_denial_command_flag_values(["git", "--git-dir", "/a", "commit"]) == [
            "git", "commit",
        ]
        assert _mod.denials._drop_denial_command_flag_values(["git", "--work-tree", "/a", "commit"]) == [
            "git", "commit",
        ]

    def test_separate_token_flag_at_end_of_list_drops_flag_with_no_value_token(self):
        """A value-taking flag as the last token, with nothing after it, still
        drops the flag itself — the unconditional i += 2 skip doesn't require
        a following token to exist."""
        assert _mod.denials._drop_denial_command_flag_values(["git", "commit", "-C"]) == ["git", "commit"]


class TestIsNongateFrictionKind:
    """Direct unit coverage for _is_nongate_friction_kind — the CLI-layer
    --deny-summary/timeline tests (TestReviewTrace) exercise this only
    indirectly through the full JSONL-fixture-to-stdout path."""

    def test_already_gate_denied_returns_false_even_with_nongate_kind(self):
        assert _mod.denials._is_nongate_friction_kind("interrupted", already_gate_denied=True) is False

    def test_falsy_kind_returns_false(self):
        assert _mod.denials._is_nongate_friction_kind("", already_gate_denied=False) is False

    def test_gate_kind_returns_false(self):
        assert _mod.denials._is_nongate_friction_kind(_mod.denials._GATE_TOOL_DENIAL_KIND, already_gate_denied=False) is False

    @pytest.mark.parametrize(
        "kind", ["user-rejected", "automode-blocked", "automode-unavailable", "interrupted"]
    )
    def test_nongate_kind_returns_true(self, kind):
        assert _mod.denials._is_nongate_friction_kind(kind, already_gate_denied=False) is True

    def test_unenumerated_future_kind_still_returns_true(self):
        """A toolDenialKind value outside the four named kinds is still
        non-gate friction as long as it isn't the gate kind — the label
        printed for it is _friction_kind_label's concern, not this
        predicate's."""
        assert _mod.denials._is_nongate_friction_kind("some-future-kind", already_gate_denied=False) is True


class TestFrictionKindLabel:
    """Direct unit coverage for _friction_kind_label — the CLI-layer
    --deny-summary/timeline tests (TestReviewTrace) exercise this only
    indirectly through the full JSONL-fixture-to-stdout path."""

    @pytest.mark.parametrize(
        "kind", ["user-rejected", "automode-blocked", "automode-unavailable", "interrupted"]
    )
    def test_enumerated_kind_returns_itself(self, kind):
        assert _mod.denials._friction_kind_label(kind) == kind

    def test_unenumerated_kind_returns_other_kind_sentinel(self):
        assert _mod.denials._friction_kind_label("some-future-kind") == _mod.denials._FRICTION_KIND_OTHER


# ---------------------------------------------------------------------------
# _denial_hook_label enumeration — pins _DENIAL_HOOK_LABELS against each
# hook's real deny-path wording, one case per hooks/*.sh label.
# ---------------------------------------------------------------------------


class TestDenialHookLabelEnumeration:
    """Feeds each hook's deny-path wording (hand-transcribed verbatim from
    hooks/*.sh, not driven through the real hook) through _denial_hook_label
    and asserts the label it produces is a member of _DENIAL_HOOK_LABELS —
    not _DENY_SUMMARY_UNMATCHED_HOOK. A hook's wording changing without
    updating both this fixture and the enumeration set fails the affected
    case, so drift here is caught rather than silently stale — but a hook
    changing its wording to something this fixture never updates to match
    would not be caught, since no real hook process ever runs.
    TestDenialHookLabelEnumerationRealHooks below closes that gap for all
    but three rows by driving the real hook subprocess instead."""

    @pytest.mark.parametrize("hook_file,message,expected_label", [
        ("block-gh-pr-merge.sh:49",
         "Blocked by gh-pr-merge gate: could not source _lib.sh.",
         "gh-pr-merge"),
        ("check-claude-md-length.sh:42",
         "Blocked by CLAUDE.md length gate: could not source _lib.sh.",
         "CLAUDE.md length"),
        ("check-skill-length.sh:41",
         "Blocked by skill length gate: could not source _lib.sh.",
         "skill length"),
        ("deny-credential-bash-reads.sh:27",
         "Blocked by credential-path Bash gate: could not source _lib.sh.",
         "credential-path Bash"),
        ("deny-credential-file-reads.sh:27",
         "Blocked by credential-file read gate: could not source _lib.sh.",
         "credential-file read"),
        ("deny-data-file-reads.sh:65",
         "Blocked by data-file read gate: could not source _lib.sh.",
         "data-file read"),
        ("deny-env-reads.sh:47",
         "Blocked by env-read gate: could not source _lib.sh.",
         "env-read"),
        ("deny-escaped-backticks-in-pr-body.sh:46",
         "Blocked by backtick-escape gate: could not source _lib.sh.",
         "backtick-escape"),
        ("deny-network-installs.sh:40",
         "Blocked by network-install gate: could not source _lib.sh.",
         "network-install"),
        ("deny-pii-in-commits.sh:127",
         "Blocked by PII commit gate: could not source _lib.sh — hook cannot evaluate the commit safely.",
         "PII commit"),
        ("deny-private-project-refs.sh:180",
         "Blocked by redaction gate: could not source _lib.sh — hook cannot evaluate command detection safely.",
         "redaction"),
        ("deny-repo-relocation.sh:63",
         "Blocked by repo-relocation hook: could not source _lib.sh — hook cannot evaluate relocation discipline safely.",
         "repo-relocation"),
        ("deny-reviewer-tree-mutation.sh:146",
         "Blocked by reviewer-tree-mutation hook: could not source _lib.sh — hook cannot evaluate reviewer discipline safely.",
         "reviewer-tree-mutation"),
        ("enforce-marker-script-shape.sh:68",
         "Blocked by marker-script-shape gate: could not source _lib.sh.",
         "marker-script-shape"),
        ("guard-settings-session-keys.sh:49",
         "Blocked by settings session-keys gate: could not source _lib.sh.",
         "settings session-keys"),
        ("require-code-review.sh:48",
         "Blocked by code-review gate: could not source _lib.sh.",
         "code-review"),
        ("require-memory-skill.sh:59",
         "Blocked by memory-skill gate: could not source _lib.sh.",
         "memory-skill"),
        ("require-memory-skill.sh:125",
         "Memory write blocked by ai-instruction-and-memory-files gate. You are writing to "
         "MEMORY.md, which is part of Claude Code's auto-memory file system.",
         "ai-instruction-and-memory-files"),
        ("require-plan-review.sh:66",
         "Blocked by plan-review gate: could not source _lib.sh.",
         "plan-review"),
        ("require-plan-review.sh:239",
         "Plan presentation blocked by the plan-review gate: an uncommitted or modified "
         "plan file exists in .claude/plans/ but no plan-review marker covering the "
         "current plan set was found.",
         "plan-review"),
        ("require-routing-read.sh:27",
         "Blocked by routing-read gate: could not source _lib.sh.",
         "routing-read"),
        ("require-routing-read.sh:68",
         "Agent spawn blocked by plan-review routing gate: Read the plan-review skill's "
         "ROUTING.md before spawning any specialist agent.",
         "plan-review routing"),
        ("require-ready-for-review.sh:80",
         "Blocked by ready-for-review gate: could not source _lib.sh.",
         "ready-for-review"),
        ("require-respond-pr.sh:69",
         "Blocked by respond-pr gate: could not source _lib.sh.",
         "respond-pr"),
        ("require-stow-reminder.sh:71",
         "Blocked by stow-reminder gate: could not source _lib.sh.",
         "stow-reminder"),
        ("require-worktree-for-file-writes.sh:50",
         "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh.",
         "worktree-enforcement"),
        ("require-worktree-for-git-writes.sh:91",
         "Blocked by worktree-enforcement hook: could not source _lib.sh — hook cannot "
         "evaluate git discipline safely.",
         "worktree-enforcement"),
        ("enforce-marker-script-shape.sh:277",
         "marker.sh invocation denied (path traversal '..' detected). Command "
         "(truncated): ~/.claude/scripts/marker.sh write foo",
         "marker.sh"),
        ("enforce-marker-script-shape.sh:353",
         "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh bogus",
         "marker.sh"),
        # These two rows must land unedited: they pin legacy pre-DENY_GATE_LABEL wording,
        # not today's hooks/*.sh text (see TestDenialHookLabelEnumerationRealHooks for that).
        ("check-claude-md-length.sh:85",
         "CLAUDE.md/AGENTS.md length gate: one or more files grew past the 200-line limit. "
         "Reduce to the limit or fewer lines before committing.",
         "AGENTS.md length"),
        ("check-skill-length.sh:87",
         "Skill length gate: one or more SKILL.md files grew past their per-skill limit. "
         "Reduce to the limit or fewer lines before committing.",
         "Skill length"),
    ])
    def test_hook_wording_produces_enumerated_label(self, hook_file, message, expected_label):
        got = _mod.denials._denial_hook_label("", message)
        assert got == expected_label, (
            f"{hook_file}'s wording produced {got!r}, expected the enumerated "
            f"label {expected_label!r} — either the hook's wording drifted or "
            f"_DENIAL_HOOK_LABELS is stale"
        )
        assert got in _mod.denials._DENIAL_HOOK_LABELS
        assert got != _mod.denials._DENY_SUMMARY_UNMATCHED_HOOK


# Every gate hook bootstraps identically: `set -uo pipefail`, define a raw
# emit_deny stub, then `. "${0%/*}/_lib.sh"` — if that source fails,
# the stub denies with "Blocked by <label> gate/hook: could not source
# _lib.sh." before ever reading stdin. Copying one hook script alone (no
# _lib.sh alongside it, see _isolated_hook_copy) into a fresh directory
# reliably fails that source line, driving this exact wording for real
# rather than hand-typing it — one entry per _DENIAL_HOOK_LABELS member
# reachable through this shared path.
# These 24 rows must land unedited: an edit here would mean the bootstrap
# stub's emitted bytes changed where they should not have. Kept as an
# independently-typed copy — never referenced by _BOOTSTRAP_FALLBACK_HOOKS's
# own definition below — so test_original_24_bootstrap_fallback_rows_are_unedited
# compares two separately-authored literals rather than a tuple against a
# slice of its own construction, which would pass regardless of content.
_BOOTSTRAP_FALLBACK_HOOKS_ORIGINAL_24: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "gh-pr-merge"),
    ("check-claude-md-length.sh", "CLAUDE.md length"),
    ("check-skill-length.sh", "skill length"),
    ("deny-credential-bash-reads.sh", "credential-path Bash"),
    ("deny-credential-file-reads.sh", "credential-file read"),
    ("deny-data-file-reads.sh", "data-file read"),
    ("deny-env-reads.sh", "env-read"),
    ("deny-escaped-backticks-in-pr-body.sh", "backtick-escape"),
    ("deny-network-installs.sh", "network-install"),
    ("deny-pii-in-commits.sh", "PII commit"),
    ("deny-private-project-refs.sh", "redaction"),
    ("deny-repo-relocation.sh", "repo-relocation"),
    ("deny-reviewer-tree-mutation.sh", "reviewer-tree-mutation"),
    ("enforce-marker-script-shape.sh", "marker-script-shape"),
    ("guard-settings-session-keys.sh", "settings session-keys"),
    ("require-code-review.sh", "code-review"),
    ("require-memory-skill.sh", "memory-skill"),
    ("require-plan-review.sh", "plan-review"),
    ("require-routing-read.sh", "routing-read"),
    ("require-ready-for-review.sh", "ready-for-review"),
    ("require-respond-pr.sh", "respond-pr"),
    ("require-stow-reminder.sh", "stow-reminder"),
    ("require-worktree-for-file-writes.sh", "worktree-enforcement"),
    ("require-worktree-for-git-writes.sh", "worktree-enforcement"),
)

# require-architect-consult.sh and deny-invisible-commit-content.sh already
# emitted this exact wording; they were simply unenumerated in
# _DENIAL_HOOK_LABELS until now. Written as its own flat tuple, not built by
# concatenating _BOOTSTRAP_FALLBACK_HOOKS_ORIGINAL_24 — see that tuple's own
# comment for why the two are kept independent.
_BOOTSTRAP_FALLBACK_HOOKS: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "gh-pr-merge"),
    ("check-claude-md-length.sh", "CLAUDE.md length"),
    ("check-skill-length.sh", "skill length"),
    ("deny-credential-bash-reads.sh", "credential-path Bash"),
    ("deny-credential-file-reads.sh", "credential-file read"),
    ("deny-data-file-reads.sh", "data-file read"),
    ("deny-env-reads.sh", "env-read"),
    ("deny-escaped-backticks-in-pr-body.sh", "backtick-escape"),
    ("deny-network-installs.sh", "network-install"),
    ("deny-pii-in-commits.sh", "PII commit"),
    ("deny-private-project-refs.sh", "redaction"),
    ("deny-repo-relocation.sh", "repo-relocation"),
    ("deny-reviewer-tree-mutation.sh", "reviewer-tree-mutation"),
    ("enforce-marker-script-shape.sh", "marker-script-shape"),
    ("guard-settings-session-keys.sh", "settings session-keys"),
    ("require-code-review.sh", "code-review"),
    ("require-memory-skill.sh", "memory-skill"),
    ("require-plan-review.sh", "plan-review"),
    ("require-routing-read.sh", "routing-read"),
    ("require-ready-for-review.sh", "ready-for-review"),
    ("require-respond-pr.sh", "respond-pr"),
    ("require-stow-reminder.sh", "stow-reminder"),
    ("require-worktree-for-file-writes.sh", "worktree-enforcement"),
    ("require-worktree-for-git-writes.sh", "worktree-enforcement"),
    ("require-architect-consult.sh", "architect-consult"),
    ("deny-invisible-commit-content.sh", "invisible-commit-content"),
    ("deny-no-op-dispatch.sh", "no-op-dispatch"),
)


def test_original_24_bootstrap_fallback_rows_are_unedited():
    """The 24 pre-existing _BOOTSTRAP_FALLBACK_HOOKS rows stay exactly as
    they were before the two rows above were added — a genuine check, since
    _BOOTSTRAP_FALLBACK_HOOKS_ORIGINAL_24 is a separately-typed literal, not
    read by _BOOTSTRAP_FALLBACK_HOOKS's own definition."""
    assert _BOOTSTRAP_FALLBACK_HOOKS[:24] == _BOOTSTRAP_FALLBACK_HOOKS_ORIGINAL_24


def test_bootstrap_fallback_hooks_matches_every_hook_declaring_deny_gate_label():
    """Completeness guard: a future gate hook that declares its own
    DENY_GATE_LABEL but is never added here would silently get zero
    TestDenyGateLabelConformance coverage — the same "forgotten declaration"
    failure mode that class exists to catch, one layer up."""
    on_disk = {
        path.name
        for path in HOOKS_DIR.glob("*.sh")
        if _DENY_GATE_LABEL_DECLARATION_RE.search(path.read_text())
    }
    enumerated = {name for name, _label in _BOOTSTRAP_FALLBACK_HOOKS}
    assert on_disk == enumerated, (
        f"hooks declaring DENY_GATE_LABEL but missing from _BOOTSTRAP_FALLBACK_HOOKS: "
        f"{on_disk - enumerated}; enumerated but not on disk: {enumerated - on_disk}"
    )


def _isolated_hook_copy(tmp_path: Path, hook_name: str) -> Path:
    """Copy one hooks/*.sh script alone into an isolated directory, with no
    _lib.sh alongside it, so the hook's own `. "${0%/*}/_lib.sh"`
    bootstrap line genuinely fails to source."""
    dest_dir = tmp_path / "isolated-hook"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / hook_name
    shutil.copy2(HOOKS_DIR / hook_name, dest)
    return dest


def _run_hook_raw_stderr(hook: Path, tool_input: dict) -> str:
    """Invoke `hook` directly and return its raw stderr text.

    Distinct from helpers.run_hook_reason, which parses a JSON stdout
    payload emitted by the fully-sourced _lib_emit_deny — the bootstrap
    source-failure path denies via the pre-source emit_deny stub, which
    writes straight to stderr and exits 2 with empty stdout, so
    run_hook_reason would read that as "allowed silently" (returns None).
    """
    result = subprocess.run(
        [str(hook)], input=json.dumps(tool_input), capture_output=True, text=True, check=False,
    )
    return result.stderr


class TestDenialHookLabelEnumerationRealHooks:
    """Drives each hook's actual deny path via subprocess (the helpers.run_hook
    pattern already established in hooks/tests/test_enforce_marker_script_shape.py)
    and feeds the hook's own real stdout/stderr message through
    _denial_hook_label, rather than a hand-transcribed string —
    TestDenialHookLabelEnumeration above never runs a hook process at all.

    Three of TestDenialHookLabelEnumeration's 31 rows stay fixture-only:
    their real trigger needs machinery (an isolated $HOME with a live
    active-bypass marker or session-keyed state) that belongs in each hook's
    own dedicated test file, not duplicated here — require-memory-skill.sh:125
    (ai-instruction-and-memory-files), require-plan-review.sh:239 (plan-review;
    the label itself is still proven live below via require-plan-review.sh:66's
    bootstrap-failure case), and require-routing-read.sh:68 (plan-review
    routing)."""

    @pytest.mark.parametrize("hook_name,expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_bootstrap_lib_sh_failure_produces_enumerated_label(self, tmp_path, hook_name, expected_label):
        dest = _isolated_hook_copy(tmp_path, hook_name)
        message = _run_hook_raw_stderr(dest, bash_input("echo hi"))
        got = _mod.denials._denial_hook_label("", message)
        assert got == expected_label, (
            f"{hook_name}'s real bootstrap-failure wording {message!r} produced "
            f"{got!r}, expected the enumerated label {expected_label!r}"
        )
        # Same real stderr, second axis: this is the strongest available
        # evidence that the bootstrap-failure wording classifies lib-source.
        assert _mod.denials._denial_cause_kind(message) == "lib-source"

    def test_marker_sh_path_traversal_produces_enumerated_label(self):
        """enforce-marker-script-shape.sh's own path-traversal deny path —
        distinct real wording from the bootstrap-failure case above, which
        shares the same 'marker-script-shape' label. The message now leads
        with 'Blocked by marker-script-shape gate:', so _DENIAL_HOOK_NAME_RE
        wins the label-extraction cascade ahead of the legacy
        'marker.sh invocation denied' pattern."""
        cmd = "../../.claude/scripts/marker.sh write code-review"
        message = run_hook_reason(HOOKS_DIR / "enforce-marker-script-shape.sh", bash_input(cmd))
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "marker-script-shape"
        assert _mod.denials._denial_cause_kind(message) == "behavioral"

    def test_marker_sh_unknown_subcommand_produces_enumerated_label(self):
        """enforce-marker-script-shape.sh's general 'invocation denied'
        wording for an unenumerated subcommand — distinct real wording from
        the path-traversal case above, which shares the same
        'marker-script-shape' label."""
        cmd = "~/.claude/scripts/marker.sh forge code-review"
        message = run_hook_reason(HOOKS_DIR / "enforce-marker-script-shape.sh", bash_input(cmd))
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "marker-script-shape"
        assert _mod.denials._denial_cause_kind(message) == "behavioral"

    def test_agents_md_over_limit_produces_enumerated_label(self, tmp_path):
        """check-claude-md-length.sh's real 'grew past the 200-line limit'
        deny path for a root AGENTS.md, mirroring
        test_check_claude_md_length.py's own git-repo fixture pattern."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        agents_md = repo / "AGENTS.md"
        agents_md.write_text("\n".join(f"line {i}" for i in range(190)) + "\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        agents_md.write_text("\n".join(f"line {i}" for i in range(201)) + "\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        message = run_hook_reason(
            HOOKS_DIR / "check-claude-md-length.sh", bash_input("git commit -m foo"), cwd=repo,
        )
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "CLAUDE.md length"

    def test_skill_md_over_limit_produces_enumerated_label(self, tmp_path):
        """check-skill-length.sh's real 'grew past their per-skill limit' deny
        path, mirroring test_check_skill_length.py's own git-repo fixture
        pattern."""
        repo = tmp_path / "repo"
        skill_dir = repo / "claude-skills" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
        skill_md = skill_dir / "SKILL.md"
        skill_path = "claude-skills/skills/my-skill/SKILL.md"
        skill_md.write_text("\n".join(f"line {i}" for i in range(190)) + "\n")
        subprocess.run(["git", "add", skill_path], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        skill_md.write_text("\n".join(f"line {i}" for i in range(201)) + "\n")
        subprocess.run(["git", "add", skill_path], cwd=repo, check=True)
        message = run_hook_reason(
            HOOKS_DIR / "check-skill-length.sh", bash_input("git commit -m foo"), cwd=repo,
        )
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "skill length"

    def test_claude_md_commit_detection_fail_closed_produces_enumerated_label(self, tmp_path):
        """check-claude-md-length.sh's commit-detection fail-closed path (sed
        absent from PATH, same technique test_check_skill_length.py's
        test_sed_absent_from_path_denies uses) goes through the shared
        _lib_staged_length_gate, whose deny is prefixed by
        check-claude-md-length.sh's own DENY_GATE_LABEL, "CLAUDE.md length" —
        pinning that classification so a future wording change is caught."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        message = run_hook_reason(
            HOOKS_DIR / "check-claude-md-length.sh",
            bash_input("git commit -m foo"),
            cwd=tmp_path,
            extra_env={"PATH": restricted_path},
        )
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "CLAUDE.md length"

    def test_skill_commit_detection_fail_closed_produces_enumerated_label(self, tmp_path):
        """check-skill-length.sh's commit-detection fail-closed path (sed
        absent from PATH, same technique test_check_skill_length.py's
        test_sed_absent_from_path_denies uses) goes through the shared
        _lib_staged_length_gate, whose deny is prefixed by
        check-skill-length.sh's own DENY_GATE_LABEL, "skill length" —
        pinning that classification so a future wording change is caught."""
        farm_dir = tmp_path / "path-without-sed"
        farm_dir.mkdir()
        restricted_path = build_path_without("sed", farm_dir)
        message = run_hook_reason(
            HOOKS_DIR / "check-skill-length.sh",
            bash_input("git commit -m foo"),
            cwd=tmp_path,
            extra_env={"PATH": restricted_path},
        )
        assert message is not None
        assert _mod.denials._denial_hook_label("", message) == "skill length"


# ---------------------------------------------------------------------------
# DENY_GATE_LABEL conformance test — converts a forgotten or unenumerated
# declaration from a silent fall-through-to-unmatched into a CI failure.
# Reads DENY_GATE_LABEL and every deny-message literal straight off each of
# the 26 gate hooks' own source, rather than trusting _DENIAL_HOOK_LABELS or
# _DENIAL_CAUSE_MARKERS to have kept up on their own.
# ---------------------------------------------------------------------------

_DENY_GATE_LABEL_DECLARATION_RE = re.compile(r'^DENY_GATE_LABEL="([^"]*)"', re.MULTILINE)

# Bounds a declared label the same way _DENIAL_HOOK_NAME_MAX_CHARS bounds an
# extracted one (clause (c)) — independent of any "blocked by ... gate"
# framing, since this checks the raw declared value.
_DENIAL_HOOK_NAME_SHAPE_RE = re.compile(r"[\w .-]+")

# Call shapes that carry a deny-message literal: emit_deny, its
# emit_deny_folding_fresh_lock_context wrapper
# (require-worktree-for-git-writes.sh), and _lib_parse_tool_input_or_deny's
# own argument. _lib_staged_length_gate's third (message) argument is
# reached via its distinct three-argument call shape — "$REPO_ROOT", then a
# single-quoted grep -E pattern, then the deny literal.
_DENY_LITERAL_CALL_START_RE = re.compile(
    r"(?<![\w])(?:emit_deny|emit_deny_folding_fresh_lock_context|_lib_parse_tool_input_or_deny)\s+\""
    r"|_lib_staged_length_gate\s+\"\$REPO_ROOT\"\s+'[^']*'\s+\""
)


def _read_balanced_dquoted(text: str, quote_index: int) -> tuple[str, int]:
    """Return (literal_content, index_after_closing_quote) for the bash
    double-quoted string literal whose opening quote is text[quote_index].

    A closing quote is only recognized when it isn't itself
    backslash-escaped, so a literal carrying an embedded \\" (e.g.
    block-gh-pr-merge.sh's self-merge-block message) is read whole rather
    than truncated at the first inner quote. Bash double-quoted strings may
    also span multiple physical lines (several enforce-marker-script-shape.sh
    and require-plan-review.sh literals do), so this scans past newlines
    rather than stopping at end-of-line.
    """
    assert text[quote_index] == '"'
    i = quote_index + 1
    start = i
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == '"':
            return text[start:i], i + 1
        i += 1
    raise ValueError(f"unterminated double-quoted literal starting at index {quote_index}")


def _deny_literals_from_text(text: str) -> list[str]:
    """Every deny-message literal in one hook's source text. Skips a
    literal that is a bare $VAR reference (e.g.
    require-worktree-for-git-writes.sh's wrapper-internal emit_deny "$reason") —
    its real text is the wrapper's own callers' literals, enumerated
    separately as their own call sites."""
    literals = []
    for m in _DENY_LITERAL_CALL_START_RE.finditer(text):
        literal, _end = _read_balanced_dquoted(text, m.end() - 1)
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", literal):
            continue
        literals.append(literal)
    return literals


def _deny_literals(hook_name: str) -> list[str]:
    return _deny_literals_from_text((HOOKS_DIR / hook_name).read_text())


def _deny_gate_label_declarations(hook_name: str) -> list[str]:
    text = (HOOKS_DIR / hook_name).read_text()
    return _DENY_GATE_LABEL_DECLARATION_RE.findall(text)


def _declared_deny_gate_label(hook_name: str) -> str:
    declarations = _deny_gate_label_declarations(hook_name)
    assert len(declarations) == 1, (
        f"{hook_name}: expected exactly one DENY_GATE_LABEL declaration, found {len(declarations)}"
    )
    return declarations[0]


def _marker_kinds_present(text: str) -> list[str]:
    """Every _DENIAL_CAUSE_MARKERS family whose literal marker substring
    appears in `text`, case-insensitively, in cascade-precedence order."""
    lowered = text.lower()
    return [kind for marker, kind in _mod.denials._DENIAL_CAUSE_MARKERS if marker in lowered]


class TestDenyGateLabelConformance:
    """Five clauses (a)-(e), driven against every one of the 26 gate hooks
    named in _BOOTSTRAP_FALLBACK_HOOKS. Clauses (b), (d), and (e) each get
    their own permanent negative-case test below, built by
    deliberately breaking a scratch copy of a real hook — a one-time
    demonstration during authoring would leave nothing guarding the check's
    detection power against a later edit that quietly weakens it."""

    @pytest.mark.parametrize("hook_name,_expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_clause_a_every_gate_hook_declares_exactly_one_label(self, hook_name, _expected_label):
        declarations = _deny_gate_label_declarations(hook_name)
        assert len(declarations) == 1, (
            f"{hook_name} declares {len(declarations)} DENY_GATE_LABEL values, expected exactly one"
        )

    @pytest.mark.parametrize("hook_name,_expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_clause_b_every_declared_label_is_an_enumerated_member(self, hook_name, _expected_label):
        label = _declared_deny_gate_label(hook_name)
        assert label in _mod.denials._DENIAL_HOOK_LABELS, (
            f"{hook_name} declares DENY_GATE_LABEL={label!r}, which is not a _DENIAL_HOOK_LABELS "
            f"member — either the label is a typo or the set is stale"
        )

    def test_clause_b_negative_unenumerated_label_is_detected(self, tmp_path):
        """Permanent negative case: a scratch copy of block-gh-pr-merge.sh
        whose DENY_GATE_LABEL isn't a _DENIAL_HOOK_LABELS member must be
        flagged, matching what clause (b)'s own check above would report."""
        original = (HOOKS_DIR / "block-gh-pr-merge.sh").read_text()
        mutated = original.replace(
            'DENY_GATE_LABEL="gh-pr-merge"', 'DENY_GATE_LABEL="not-a-real-enumerated-label"', 1,
        )
        assert mutated != original, (
            "substitution didn't match — block-gh-pr-merge.sh's DENY_GATE_LABEL declaration wording drifted"
        )
        scratch = tmp_path / "block-gh-pr-merge.sh"
        scratch.write_text(mutated)
        declarations = _DENY_GATE_LABEL_DECLARATION_RE.findall(scratch.read_text())
        assert declarations == ["not-a-real-enumerated-label"]
        assert declarations[0] not in _mod.denials._DENIAL_HOOK_LABELS

    @pytest.mark.parametrize("hook_name,_expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_clause_c_every_declared_label_matches_the_name_shape(self, hook_name, _expected_label):
        label = _declared_deny_gate_label(hook_name)
        assert _DENIAL_HOOK_NAME_SHAPE_RE.fullmatch(label), (
            f"{hook_name}'s DENY_GATE_LABEL {label!r} doesn't match the name-shaped [\\w .-]+ class"
        )
        assert len(label) <= _mod.denials._DENIAL_HOOK_NAME_MAX_CHARS, (
            f"{hook_name}'s DENY_GATE_LABEL {label!r} exceeds _DENIAL_HOOK_NAME_MAX_CHARS"
        )

    def test_deny_literal_extraction_is_non_empty_for_every_gate_hook(self):
        """Vacuity self-check mirroring test_lib.py's builds_path_re
        precedent: a hook yielding zero literals means the extraction regex
        has drifted from that hook's call shape, not that the hook has no
        deny literals — and clause (d) would be silently vacuous for it."""
        for hook_name, _label in _BOOTSTRAP_FALLBACK_HOOKS:
            literals = _deny_literals(hook_name)
            assert literals, (
                f"{hook_name}: no deny literals extracted — the extraction regex has drifted "
                f"from this hook's call shape"
            )

    @pytest.mark.parametrize("hook_name,_expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_clause_d_every_marker_carrying_literal_carries_exactly_one_marker(self, hook_name, _expected_label):
        for literal in _deny_literals(hook_name):
            present = _marker_kinds_present(literal)
            assert len(present) <= 1, (
                f"{hook_name}'s deny literal {literal!r} carries markers for causes {present}, "
                f"which defeats _denial_cause_kind's substring cascade"
            )
            if present:
                assert _mod.denials._denial_cause_kind(literal) == present[0]

    def test_clause_d_negative_marker_collision_is_detected(self, tmp_path):
        """Permanent negative case: a scratch copy of block-gh-pr-merge.sh
        whose parse-failure literal carries both the input-parse and
        helper-proc markers must be flagged by the exactly-one-marker rule —
        a marker collision is the only construction that defeats
        _denial_cause_kind's first-match-wins substring cascade, which is
        exactly why clause (d) forbids it."""
        original = (HOOKS_DIR / "block-gh-pr-merge.sh").read_text()
        collision_literal = (
            "could not parse tool-input JSON, failing closed rather than acting on an unscanned command."
        )
        mutated = original.replace(
            '_lib_parse_tool_input_or_deny "could not parse tool-input JSON."',
            f'_lib_parse_tool_input_or_deny "{collision_literal}"',
            1,
        )
        assert mutated != original, (
            "substitution didn't match — block-gh-pr-merge.sh's parse-failure call site wording drifted"
        )
        scratch = tmp_path / "block-gh-pr-merge.sh"
        scratch.write_text(mutated)
        literals = _deny_literals_from_text(scratch.read_text())
        assert collision_literal in literals
        present = _marker_kinds_present(collision_literal)
        assert present == ["input-parse", "helper-proc"], present
        assert len(present) > 1
        # The cascade's first-match-wins order silently masks the literal's
        # own "failing closed" marker — the exact miscategorization clause
        # (d) exists to keep out of a real hook's source.
        assert _mod.denials._denial_cause_kind(collision_literal) == "input-parse"

    @pytest.mark.parametrize("hook_name,_expected_label", _BOOTSTRAP_FALLBACK_HOOKS)
    def test_clause_e_no_deny_literal_reintroduces_a_hand_written_blocked_by_prefix(
        self, hook_name, _expected_label,
    ):
        for literal in _deny_literals(hook_name):
            assert "blocked by" not in literal.lower(), (
                f"{hook_name}'s deny literal {literal!r} contains a hand-written "
                f"'Blocked by ... gate:' phrase — DENY_GATE_LABEL already supplies "
                f"this prefix via _lib_emit_deny, so a literal carrying its own "
                f"copy renders doubled"
            )

    def test_clause_e_negative_reintroduced_prefix_is_detected(self, tmp_path):
        """Permanent negative case: a scratch copy of check-skill-length.sh
        whose _lib_staged_length_gate message literal has a hand-written
        "Blocked by ... gate:" phrase spliced back in — the exact reversion
        shape a copy-paste-from-history edit would produce — must be flagged
        by clause (e)."""
        original = (HOOKS_DIR / "check-skill-length.sh").read_text()
        original_message = "one or more SKILL.md files grew past their per-skill limit."
        mutated = original.replace(
            f'"{original_message}"',
            f'"Blocked by skill length gate: {original_message}"',
            1,
        )
        assert mutated != original, (
            "substitution didn't match — check-skill-length.sh's over-limit message wording drifted"
        )
        scratch = tmp_path / "check-skill-length.sh"
        scratch.write_text(mutated)
        literals = _deny_literals_from_text(scratch.read_text())
        collision_literal = next(literal for literal in literals if original_message in literal)
        assert "blocked by" in collision_literal.lower()


# ---------------------------------------------------------------------------
# _denial_cause_kind — pins the denial-cause axis against every gate hook's
# current wording. Every fixture literal below is copied from the working
# tree as it stands at authoring time — never re-derived via git show/git
# merge-base, which would resolve to a later rewrite's text. A historical
# transcript keeps its original wording forever, so a fixture reflecting
# only newer wording would prove nothing about the corpus these tests exist
# to classify correctly.
# ---------------------------------------------------------------------------


# Bootstrap source-failure wording, one row per gate hook (all 26) — the same
# "could not source _lib.sh" idiom TestDenialHookLabelEnumeration's fixture
# rows above already carry for 24 of them, plus the two hooks whose labels
# (architect-consult, invisible-commit-content) aren't yet _DENIAL_HOOK_LABELS
# members.
_LIB_SOURCE_FIXTURES: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "Blocked by gh-pr-merge gate: could not source _lib.sh."),
    ("check-claude-md-length.sh", "Blocked by CLAUDE.md length gate: could not source _lib.sh."),
    ("check-skill-length.sh", "Blocked by skill length gate: could not source _lib.sh."),
    ("deny-credential-bash-reads.sh",
     "Blocked by credential-path Bash gate: could not source _lib.sh."),
    ("deny-credential-file-reads.sh",
     "Blocked by credential-file read gate: could not source _lib.sh."),
    ("deny-data-file-reads.sh", "Blocked by data-file read gate: could not source _lib.sh."),
    ("deny-env-reads.sh", "Blocked by env-read gate: could not source _lib.sh."),
    ("deny-escaped-backticks-in-pr-body.sh",
     "Blocked by backtick-escape gate: could not source _lib.sh."),
    ("deny-network-installs.sh", "Blocked by network-install gate: could not source _lib.sh."),
    ("deny-pii-in-commits.sh",
     "Blocked by PII commit gate: could not source _lib.sh — hook cannot evaluate the commit safely."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: could not source _lib.sh — hook cannot evaluate command "
     "detection safely."),
    ("deny-repo-relocation.sh",
     "Blocked by repo-relocation hook: could not source _lib.sh — hook cannot evaluate "
     "relocation discipline safely."),
    ("deny-reviewer-tree-mutation.sh",
     "Blocked by reviewer-tree-mutation hook: could not source _lib.sh — hook cannot evaluate "
     "reviewer discipline safely."),
    ("enforce-marker-script-shape.sh",
     "Blocked by marker-script-shape gate: could not source _lib.sh."),
    ("guard-settings-session-keys.sh",
     "Blocked by settings session-keys gate: could not source _lib.sh."),
    ("require-code-review.sh", "Blocked by code-review gate: could not source _lib.sh."),
    ("require-memory-skill.sh", "Blocked by memory-skill gate: could not source _lib.sh."),
    ("require-plan-review.sh", "Blocked by plan-review gate: could not source _lib.sh."),
    ("require-routing-read.sh", "Blocked by routing-read gate: could not source _lib.sh."),
    ("require-ready-for-review.sh", "Blocked by ready-for-review gate: could not source _lib.sh."),
    ("require-respond-pr.sh", "Blocked by respond-pr gate: could not source _lib.sh."),
    ("require-stow-reminder.sh", "Blocked by stow-reminder gate: could not source _lib.sh."),
    ("require-worktree-for-file-writes.sh",
     "Blocked by worktree-enforcement hook (file-writes): could not source _lib.sh."),
    ("require-worktree-for-git-writes.sh",
     "Blocked by worktree-enforcement hook: could not source _lib.sh — hook cannot evaluate "
     "git discipline safely."),
    ("require-architect-consult.sh", "Blocked by architect-consult gate: could not source _lib.sh."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not source _lib.sh."),
)

# Tool-input parse-failure wording, one row per gate hook (all 26) — each
# hook's own _lib_parse_tool_input_or_deny argument.
_INPUT_PARSE_FIXTURES: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "Blocked: could not parse tool-input JSON for gh-pr-merge gate."),
    ("deny-data-file-reads.sh",
     "Blocked by data-file read gate: could not parse tool-input JSON. Refusing to evaluate "
     "the Read under malformed input."),
    ("check-claude-md-length.sh", "Blocked by CLAUDE.md length gate: could not parse tool-input JSON."),
    ("deny-repo-relocation.sh",
     "Blocked by repo-relocation hook: could not parse tool-input JSON. Refusing to evaluate "
     "relocation discipline under malformed input."),
    ("deny-credential-file-reads.sh",
     "Blocked by credential-file read gate: could not parse tool-input JSON. Refusing to "
     "evaluate the Read under malformed input."),
    ("deny-escaped-backticks-in-pr-body.sh",
     "Blocked by backtick-escape gate: could not parse tool-input JSON. Refusing to evaluate "
     "PR body under malformed input."),
    ("deny-env-reads.sh", "Blocked: could not parse tool-input JSON for env-read gate."),
    ("deny-credential-bash-reads.sh",
     "Blocked by credential-path Bash gate: could not parse tool-input JSON. Refusing to "
     "evaluate the command under malformed input."),
    ("require-code-review.sh", "Blocked by code-review gate: could not parse tool-input JSON."),
    ("deny-network-installs.sh",
     "Blocked by network-install gate: could not parse tool-input JSON. Refusing to evaluate "
     "the command under malformed input."),
    ("enforce-marker-script-shape.sh", "Blocked: could not parse tool-input JSON."),
    ("require-worktree-for-file-writes.sh",
     "Blocked by worktree-enforcement hook (file-writes): could not parse tool-input JSON. "
     "Refusing to evaluate worktree discipline under malformed input."),
    ("deny-pii-in-commits.sh",
     "Blocked by PII commit gate: could not parse tool-input JSON. Refusing to evaluate the "
     "commit under malformed input."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: could not parse tool-input JSON. Refusing to evaluate "
     "redaction under malformed input."),
    ("deny-reviewer-tree-mutation.sh",
     "Blocked by reviewer-tree-mutation hook: could not parse tool-input JSON. Refusing to "
     "evaluate reviewer discipline under malformed input."),
    ("check-skill-length.sh", "Blocked by skill length gate: could not parse tool-input JSON."),
    ("require-architect-consult.sh", "Blocked by architect-consult gate: could not parse tool-input JSON."),
    ("require-routing-read.sh", "Blocked by routing-read gate: could not parse tool-input JSON."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not parse tool-input JSON."),
    ("require-stow-reminder.sh",
     "Blocked by stow-reminder gate: could not parse tool-input JSON. Refusing to evaluate "
     "under malformed input."),
    ("require-plan-review.sh", "Blocked by plan-review gate: could not parse tool-input JSON."),
    ("require-memory-skill.sh", "Blocked by memory-skill gate: could not parse tool-input JSON."),
    ("guard-settings-session-keys.sh",
     "Blocked by settings session-keys gate: could not parse tool-input JSON."),
    ("require-ready-for-review.sh", "Blocked by ready-for-review gate: could not parse tool-input JSON."),
    ("require-worktree-for-git-writes.sh",
     "Blocked by worktree-enforcement hook: could not parse tool-input JSON. Refusing to "
     "evaluate git discipline under malformed input."),
    ("require-respond-pr.sh", "Blocked by respond-pr gate: could not parse tool-input JSON."),
)

# Helper-process ("failing closed") wording — every distinct call site found
# by a total enumeration of "failing closed"/"Failing closed" across
# claude/.claude/hooks/*.sh, covering all thirteen gate hooks that carry one
# (roughly thirty call sites; the shared _lib.sh call site used by
# check-claude-md-length.sh/check-skill-length.sh via _lib_staged_length_gate
# has its wording exercised separately by those hooks' own commit-detection
# fail-closed tests, which assert the reason text but not this module's
# cause classification, and isn't duplicated here). ${...} shell
# interpolations are filled with a plausible concrete value; the classifier
# only needs the literal "failing closed" substring, not the exact exit
# code or command text.
_HELPER_PROC_FIXTURES: tuple[tuple[str, str], ...] = (
    ("deny-credential-bash-reads.sh",
     "Blocked by credential-path Bash gate: could not quote-strip the command text (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned command with no bypass valve."),
    ("block-gh-pr-merge.sh",
     "Blocked: could not determine whether 'gh pr merge 123' invokes gh pr merge (status 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed per this gate's documented "
     "fail-closed posture rather than letting an unscanned command bypass the self-merge block."),
    ("deny-network-installs.sh",
     "Blocked by network-install gate: could not quote-strip the command text (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned command with no bypass valve."),
    ("deny-network-installs.sh",
     "Blocked by network-install gate: could not split the command into fragments (exit 2) — "
     "sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned "
     "command with no bypass valve."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not quote-strip the command text "
     "(exit 2) — sed/tr may be missing, killed, or errored. Failing closed rather than "
     "allowing an unscanned git commit."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not determine whether this command "
     "invokes git commit (status 2) — sed/tr may be missing, killed, or errored. Failing "
     "closed rather than silently allowing an unscanned git commit."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not mask quoted command text (exit 2) — "
     "awk may be missing, killed, or errored. Failing closed rather than allowing an unscanned "
     "git commit chain."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not split the masked command into "
     "fragments (exit 2). Failing closed rather than allowing an unscanned git commit chain."),
    ("deny-invisible-commit-content.sh",
     "Blocked by invisible-commit-content gate: could not split the command into fragments "
     "(exit 2). Failing closed rather than allowing an unscanned git commit."),
    ("deny-pii-in-commits.sh",
     "Commit blocked by PII/credential guard: could not split the command into fragments "
     "(exit 2) — sed may be missing, killed, or errored. Failing closed rather than allowing "
     "an unscanned git commit."),
    ("deny-pii-in-commits.sh",
     "Commit blocked by PII/credential guard: could not quote-strip a command fragment "
     "(exit 2) — sed/tr may be missing, killed, or errored. Failing closed rather than "
     "allowing an unscanned git commit."),
    ("deny-pii-in-commits.sh",
     "Commit blocked by PII/credential guard: could not quote-strip the scan target (exit 2) "
     "— sed/tr may be missing, killed, or errored. Failing closed rather than scanning with "
     "degraded quote-split coverage."),
    ("deny-repo-relocation.sh",
     "Blocked by repo-relocation hook: could not quote-strip the command text (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned mv/rsync."),
    ("deny-repo-relocation.sh",
     "Blocked by repo-relocation hook: could not split the command into fragments (exit 2) — "
     "sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned "
     "relocation command."),
    ("deny-reviewer-tree-mutation.sh",
     "Blocked by reviewer-tree-mutation hook: could not quote-strip the command text (exit 2) "
     "— sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned command for a review-only agent."),
    ("deny-reviewer-tree-mutation.sh",
     "Blocked by reviewer-tree-mutation hook: could not split the command into fragments "
     "(exit 2) — sed may be missing, killed, or errored. Failing closed rather than allowing "
     "an unscanned command for a review-only agent."),
    ("require-code-review.sh",
     "Blocked by code-review gate: could not determine whether this command invokes git "
     "commit (status 2) — sed/tr may be missing, killed, or errored. Failing closed rather "
     "than letting an unscanned git commit bypass the review gate."),
    ("require-ready-for-review.sh",
     "Blocked by ready-for-review gate: could not quote-strip the command text (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned git push/gh pr command."),
    ("require-ready-for-review.sh",
     "Blocked by ready-for-review gate: could not split the command into fragments (exit 2) "
     "— sed may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned git push/gh pr command."),
    ("enforce-marker-script-shape.sh",
     "Blocked by marker-script-shape gate: could not quote-strip the command text (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned Bash write that could reach marker state."),
    ("enforce-marker-script-shape.sh",
     "Blocked by marker-script-shape gate: could not split the command into fragments "
     "(exit 2) — sed may be missing, killed, or errored. Failing closed rather than allowing "
     "an unscanned Bash write that could reach marker state."),
    ("enforce-marker-script-shape.sh",
     "Blocked by marker-script-shape gate: could not determine whether 'marker.sh write "
     "code-review' invokes marker.sh write/activate (sed/tr may be missing, killed, or "
     "errored) — failing closed per this gate's documented fail-closed posture rather than "
     "letting an unscanned command bypass gate-release authority."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: could not quote-strip the command text (exit 2) — sed/tr "
     "may be missing, killed, or errored. Failing closed rather than allowing an unscanned "
     "command."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: could not split the command into fragments (exit 2) — sed "
     "may be missing, killed, or errored. Failing closed rather than allowing an unscanned "
     "command."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: could not quote-strip the scanned content (exit 2) — sed/tr "
     "may be missing, killed, or errored. Failing closed rather than scanning with degraded "
     "quote-split coverage."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: the 'tracker-id' detector failed to scan the gated content "
     "(grep exit 2) — failing closed. Unscanned content is exactly the leak vector this hook "
     "guards against."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: the structural-detector fast-path pre-check matched, but no "
     "individual detector in the follow-up loop confirmed which one — failing closed on this "
     "pattern-composition mismatch between the combined and per-detector regexes."),
    ("deny-private-project-refs.sh",
     "Blocked by redaction gate: the structural-detector fast-path pre-check failed to scan "
     "the gated content (grep exit 2) — failing closed. Unscanned content is exactly the leak "
     "vector this hook guards against."),
    ("require-respond-pr.sh",
     "Blocked by respond-pr gate: could not flatten the command text (exit 2) — awk may be "
     "missing, killed, or errored. Failing closed rather than evaluating an unflattened "
     "command that could hide a gated pattern across a line break."),
    ("deny-escaped-backticks-in-pr-body.sh",
     "Blocked by backtick-escape gate: could not determine whether this command invokes gh "
     "pr create/edit — sed/tr may be missing, killed, or errored. Failing closed rather than "
     "letting an unscanned PR body bypass the backtick-escape scan."),
)

# The single deny-encode preamble (_lib.sh's _lib_emit_deny jq-degrade path),
# wrapping a parse-failure reason to prove deny-encode's cascade precedence
# over an input-parse fragment embedded in the same message.
_DENY_ENCODE_FIXTURE = (
    "Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed "
    "out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an "
    "interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, "
    "which runs outside the tool-call path these hooks gate; in a headless or non-interactive "
    "run, ensure jq is installed in the execution environment beforehand. Underlying gate "
    "reason follows.\nBlocked: could not parse tool-input JSON.\n"
)

# The genuinely-behavioral subset of TestDenialHookLabelEnumeration's fixture
# rows above (duplicated here per this repo's DAMP-test-code convention,
# rather than threaded through a shared constant) — every other row in that
# class's parametrization is bootstrap or parse-failure wording, already
# covered by _LIB_SOURCE_FIXTURES/_INPUT_PARSE_FIXTURES above.
_BEHAVIORAL_FIXTURES: tuple[tuple[str, str], ...] = (
    ("require-memory-skill.sh:125",
     "Memory write blocked by ai-instruction-and-memory-files gate. You are writing to "
     "MEMORY.md, which is part of Claude Code's auto-memory file system."),
    ("require-plan-review.sh:239",
     "Plan presentation blocked by the plan-review gate: an uncommitted or modified "
     "plan file exists in .claude/plans/ but no plan-review marker covering the "
     "current plan set was found."),
    ("require-routing-read.sh:68",
     "Agent spawn blocked by plan-review routing gate: Read the plan-review skill's "
     "ROUTING.md before spawning any specialist agent."),
    ("enforce-marker-script-shape.sh:277",
     "marker.sh invocation denied (path traversal '..' detected). Command "
     "(truncated): ~/.claude/scripts/marker.sh write foo"),
    ("enforce-marker-script-shape.sh:353",
     "marker.sh invocation denied. Command (truncated): ~/.claude/scripts/marker.sh bogus"),
    ("check-claude-md-length.sh:85",
     "CLAUDE.md/AGENTS.md length gate: one or more files grew past the 200-line limit. "
     "Reduce to the limit or fewer lines before committing."),
    ("check-skill-length.sh:87",
     "Skill length gate: one or more SKILL.md files grew past their per-skill limit. "
     "Reduce to the limit or fewer lines before committing."),
)

# ---------------------------------------------------------------------------
# Fixtures pinning today's actual on-disk wording, added alongside — never
# replacing — the frozen fixtures above copied from an earlier rewrite. Both
# eras must classify identically. lib-source needs no separate fixture set
# here: TestDenialHookLabelEnumerationRealHooks's
# test_bootstrap_lib_sh_failure_produces_enumerated_label already drives all
# 26 gate hooks' real, on-disk bootstrap wording through a subprocess and
# asserts lib-source, which is stronger proof of current wording than a
# hand-transcribed string. Only _INPUT_PARSE_CURRENT_WORDING_SHARP_CASES
# below is itself subprocess-verified (via run_hook_reason); the
# helper-proc and deny-encode groups are hand-transcribed snapshots with no
# tripwire against a later body-text edit that leaves the
# classification-relevant substring untouched.
# ---------------------------------------------------------------------------

# block-gh-pr-merge.sh and deny-env-reads.sh are the sharpest cases: each
# hook's own message rewrite deleted a "for <gate> gate" clause from its
# parse-failure sentence, so a test that only checked the input-parse
# fragment would miss a corrected sentence that dropped a clause.
_INPUT_PARSE_CURRENT_WORDING_SHARP_CASES: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh", "Blocked by gh-pr-merge gate: could not parse tool-input JSON."),
    ("deny-env-reads.sh", "Blocked by env-read gate: could not parse tool-input JSON."),
)

# deny-pii-in-commits.sh's action-carrying lead-in folded into the body:
# "Commit blocked by PII/credential guard:" became "Blocked by PII commit
# gate: Commit — ", moving the action into the body rather than deleting it
# a second time. block-gh-pr-merge.sh's helper-proc site is the plain-strip
# case: it carried no gate name at all before the rewrite ("Blocked: ..."),
# so it gained one rather than losing a clause.
_HELPER_PROC_CURRENT_WORDING_FIXTURES: tuple[tuple[str, str], ...] = (
    ("block-gh-pr-merge.sh",
     "Blocked by gh-pr-merge gate: could not determine whether 'gh pr merge 123' invokes gh "
     "pr merge (status 2) — sed/tr may be missing, killed, or errored. Failing closed per "
     "this gate's documented fail-closed posture rather than letting an unscanned command "
     "bypass the self-merge block."),
    ("deny-pii-in-commits.sh",
     "Blocked by PII commit gate: Commit — could not split the command into fragments "
     "(exit 2) — sed may be missing, killed, or errored. Failing closed rather than allowing "
     "an unscanned git commit."),
    ("deny-pii-in-commits.sh",
     "Blocked by PII commit gate: Commit — could not quote-strip a command fragment (exit 2) "
     "— sed/tr may be missing, killed, or errored. Failing closed rather than allowing an "
     "unscanned git commit."),
    ("deny-pii-in-commits.sh",
     "Blocked by PII commit gate: Commit — could not quote-strip the scan target (exit 2) — "
     "sed/tr may be missing, killed, or errored. Failing closed rather than scanning with "
     "degraded quote-split coverage."),
)

# deny-encode's shared preamble wraps whatever underlying reason the caller
# passed; this pins the cascade's precedence still holding when that
# underlying reason is today's on-disk wording rather than the older
# "Blocked: ..." shape _DENY_ENCODE_FIXTURE above wraps.
_DENY_ENCODE_CURRENT_WORDING_FIXTURE = (
    "Hook gate could not encode its deny reason: jq is missing from PATH, failed, or timed "
    "out. Every gate hook blocks until this is fixed — this is deliberate, not a bug. In an "
    "interactive session, install jq (and GNU coreutils timeout) using the ! shell escape, "
    "which runs outside the tool-call path these hooks gate; in a headless or non-interactive "
    "run, ensure jq is installed in the execution environment beforehand. Underlying gate "
    "reason follows.\nBlocked by code-review gate: could not parse tool-input JSON.\n"
)

# _lib.sh's own field-shift deny is a deliberate 0x1f-injection bypass
# attempt, pinned to classify behavioral rather than input-parse — the
# strongest available signal that the agent tripped a gate rather than
# encountering harness noise. Duplicated from test_lib.py's
# _FIELD_SHIFT_DENY_MESSAGE per this repo's DAMP-test-code convention.
_FIELD_SHIFT_DENY_MESSAGE = (
    "a tool-input field contained a Unit Separator (U+001F) byte, which would shift "
    "extracted-field boundaries — refusing rather than acting on values that may not be the "
    "ones the harness sent."
)


class TestDenialCauseKind:
    """Pins _denial_cause_kind's four infra markers plus its behavioral
    fallback against real and hand-transcribed denial wording. No test here
    may call git show/git merge-base — every fixture literal is the hook's
    current wording, copied once from the working tree, so a green run
    here is evidence about today's text specifically."""

    @pytest.mark.parametrize("hook_name,message", _LIB_SOURCE_FIXTURES)
    def test_bootstrap_wording_classifies_lib_source(self, hook_name, message):
        assert _mod.denials._denial_cause_kind(message) == "lib-source", (
            f"{hook_name}'s bootstrap wording {message!r} did not classify lib-source"
        )

    @pytest.mark.parametrize("hook_name,message", _INPUT_PARSE_FIXTURES)
    def test_parse_failure_wording_classifies_input_parse(self, hook_name, message):
        assert _mod.denials._denial_cause_kind(message) == "input-parse", (
            f"{hook_name}'s parse-failure wording {message!r} did not classify input-parse"
        )

    @pytest.mark.parametrize("hook_name,message", _INPUT_PARSE_CURRENT_WORDING_SHARP_CASES)
    def test_real_subprocess_parse_failure_matches_corrected_wording(self, hook_name, message):
        """The sharpest wording-regression case: each hook's own message
        rewrite deleted the "for <gate> gate" clause from
        block-gh-pr-merge.sh's and deny-env-reads.sh's parse-failure
        sentences, so this drives the real hook and asserts the exact
        corrected message — not merely that the input-parse fragment still
        matches somewhere."""
        got = run_hook_reason(HOOKS_DIR / hook_name, {"tool_name": "Bash", "tool_input": "a string"})
        assert got == message
        assert _mod.denials._denial_cause_kind(got) == "input-parse"

    @pytest.mark.parametrize("hook_name,message", _HELPER_PROC_FIXTURES)
    def test_helper_proc_wording_classifies_helper_proc(self, hook_name, message):
        assert _mod.denials._denial_cause_kind(message) == "helper-proc", (
            f"{hook_name}'s failing-closed wording {message!r} did not classify helper-proc"
        )

    def test_helper_proc_fixtures_cover_all_thirteen_census_hooks(self):
        """Thirteen gate hooks carry at least one helper-proc ("failing
        closed") deny; a fixture list that silently dropped one would still
        pass the parametrized test above, so this pins the coverage itself."""
        covered = {hook_name for hook_name, _message in _HELPER_PROC_FIXTURES}
        assert covered == {
            "deny-credential-bash-reads.sh", "block-gh-pr-merge.sh", "deny-network-installs.sh",
            "deny-invisible-commit-content.sh", "deny-pii-in-commits.sh", "deny-repo-relocation.sh",
            "deny-reviewer-tree-mutation.sh", "require-code-review.sh", "require-ready-for-review.sh",
            "enforce-marker-script-shape.sh", "deny-private-project-refs.sh", "require-respond-pr.sh",
            "deny-escaped-backticks-in-pr-body.sh",
        }

    @pytest.mark.parametrize("hook_name,message", _HELPER_PROC_CURRENT_WORDING_FIXTURES)
    def test_current_helper_proc_wording_classifies_helper_proc(self, hook_name, message):
        assert _mod.denials._denial_cause_kind(message) == "helper-proc", (
            f"{hook_name}'s current failing-closed wording {message!r} did not classify helper-proc"
        )

    def test_deny_encode_preamble_classifies_deny_encode(self):
        assert _mod.denials._denial_cause_kind(_DENY_ENCODE_FIXTURE) == "deny-encode"

    def test_deny_encode_precedes_input_parse_when_both_markers_present(self):
        """The deny-encode preamble wraps the underlying reason verbatim, so
        a jq outage during a parse-failure deny carries both markers in one
        message; deny-encode must win because the outage that broke jq's
        deny-envelope encoding is also what broke the input parse."""
        assert "could not parse tool-input json" in _DENY_ENCODE_FIXTURE.lower()
        assert _mod.denials._denial_cause_kind(_DENY_ENCODE_FIXTURE) == "deny-encode"

    def test_current_deny_encode_preamble_classifies_deny_encode(self):
        """The deny-encode preamble's cascade precedence holds when the
        underlying wrapped reason is today's on-disk wording too, not only
        the older shape _DENY_ENCODE_FIXTURE wraps."""
        assert "could not parse tool-input json" in _DENY_ENCODE_CURRENT_WORDING_FIXTURE.lower()
        assert _mod.denials._denial_cause_kind(_DENY_ENCODE_CURRENT_WORDING_FIXTURE) == "deny-encode"

    def test_real_subprocess_input_parse_classifies_input_parse(self):
        """A real .tool_input-is-a-string payload, which _lib.sh's own
        comment documents as the structural-type-error trigger that fails
        the shared jq call before any hook-specific logic runs."""
        message = run_hook_reason(
            HOOKS_DIR / "require-code-review.sh",
            {"tool_name": "Bash", "tool_input": "a string"},
        )
        assert message is not None
        assert _mod.denials._denial_cause_kind(message) == "input-parse"

    @pytest.mark.parametrize("hook_file,message", _BEHAVIORAL_FIXTURES)
    def test_behavioral_wording_classifies_behavioral(self, hook_file, message):
        assert _mod.denials._denial_cause_kind(message) == "behavioral", (
            f"{hook_file}'s wording {message!r} did not classify behavioral"
        )

    def test_field_shift_deny_classifies_behavioral(self):
        """_lib.sh's own field-shift deny is a deliberate 0x1f-injection
        bypass attempt and must classify behavioral, not input-parse — the
        strongest available signal that the agent tripped a gate rather
        than encountering harness noise. Guards against a later edit
        accidentally giving it an infra marker."""
        assert _mod.denials._denial_cause_kind(_FIELD_SHIFT_DENY_MESSAGE) == "behavioral"

    def test_agent_authored_path_with_cause_fragment_misclassifies_helper_proc(self):
        """Recorded limitation, adversarial case: an agent-controlled Read
        path containing a cause-marker substring reclassifies a genuinely
        behavioral env-read denial as helper-proc in this derived aggregate.
        The gate still denies in real time and the raw record is unchanged;
        review-trace's own cause= line recovers the individual event."""
        file_path = "/tmp/failing closed.env"
        message = (
            f"Read of '{file_path}' denied by env-read gate. Dotenv files commonly hold "
            "secrets; reading pulls them into Claude's conversation context. If this is a "
            "non-secret template, rename it to .env.example, .env.template, or .env.sample. "
            f"Otherwise inspect it with a shell command (e.g. `! cat {file_path}`) instead of "
            "the Read tool. (Allowlist: ~/.claude/hooks/deny-env-reads.sh)"
        )
        assert _mod.denials._denial_cause_kind(message) == "helper-proc"

