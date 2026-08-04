"""Tests for advance-past-commit-stall.sh (GH-526).

The hook fires only when ALL hold: no per-session/repo kill switch, main
session (not a subagent), valid session_id, not plan mode, a fresh prompt_id,
the final sentence of the last assistant message asks permission to
commit/push/open a PR without also carrying an exclusion phrase,
_lib_autonomous_shipping_active holds for the resolved repo, and work is
pending (dirty tree or HEAD ahead of its upstream). Every other path must
exit 0 with empty stdout — this is a fail-silent Stop hook, never a gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    TRAVERSAL_SESSION_ID,
    build_path_without,
    plant_traversal_canary,
    run_hook_stop,
    stop_input,
)

ADVANCE_HOOK = HOOKS_DIR / "advance-past-commit-stall.sh"

# Verbatim from .claude/plans/commit-stall-block.md's Context section — the
# issue's own quotes. The first is exactly 64 characters, which pins the
# tail-slice-floor defect the plan's Assumption ledger Row 13 documents:
# a byte-count tail slice (${msg: -600}) returns EMPTY for any message
# shorter than 600 chars, so this exact quote is the regression pin for the
# final-sentence-extraction replacement.
ISSUE_QUOTE_QUESTION = (
    "Want me to commit this, or do you want to review the diff first?"
)
ISSUE_QUOTE_NONQUESTION = "Per your standing instruction, I haven't committed."
assert len(ISSUE_QUOTE_QUESTION) == 64, "pin drifted from the plan's own 64-char quote"

# The plan's own design-history examples (Part 2), also verbatim.
MODAL_CASE = (
    "Fixed the failing test in the parser. "
    "Want me to commit this, or do you want to review the diff first?"
)
DOCUMENTED_MISS = (
    "The push failed with a non-fast-forward error. "
    "Want me to push again after rebasing?"
)


@pytest.fixture
def armed_home(isolated_home):
    """isolated_home plus the machine-level autonomous-shipping sentinel."""
    marker = isolated_home / ".claude" / "autonomous-shipping-required"
    marker.write_text("# machine sentinel\n")
    return isolated_home


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f").write_text("a\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)


@pytest.fixture
def dirty_repo(tmp_path) -> Path:
    """Committed history plus an uncommitted, unstaged edit — the primary
    'want me to commit' trigger shape."""
    repo = tmp_path / "dirty-repo"
    _init_repo(repo)
    (repo / "f").write_text("a\nb\n")
    return repo


@pytest.fixture
def clean_pushed_no_pending_repo(tmp_path) -> Path:
    """Clean tree, upstream tracking configured and matching HEAD exactly —
    the post-PR-open state where no work is pending."""
    repo = tmp_path / "clean-pushed"
    _init_repo(repo)
    # A fake, unreachable remote: `--set-upstream-to` requires refs/remotes/*
    # to belong to a configured remote, not just exist as a bare ref.
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/repo.git"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    (repo / "f").write_text("a\nb\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "--set-upstream-to=origin/feature", "feature"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def ahead_of_upstream_repo(clean_pushed_no_pending_repo) -> Path:
    """Clean tree, upstream configured, but HEAD has a new commit not yet
    reflected on the (faked) upstream ref."""
    repo = clean_pushed_no_pending_repo
    (repo / "f").write_text("a\nb\nc\n")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "more work"], cwd=repo, check=True)
    return repo


@pytest.fixture
def clean_no_upstream_repo(tmp_path) -> Path:
    """Committed history, clean tree, no remote and no upstream tracking
    configured at all — the state of most feature branches before their
    first push. `@{u}` fails to resolve entirely (not '0 commits ahead')."""
    repo = tmp_path / "no-upstream"
    _init_repo(repo)
    return repo


@pytest.fixture
def repo_with_committed_optin(tmp_path) -> Path:
    """A repo that commits .claude/autonomous-shipping-required itself — the
    exact case this design's machine-anchored precedence exists to defeat.
    Paired with an isolated_home that has NOT set the machine sentinel, this
    must stay silent."""
    repo = tmp_path / "hostile-optin"
    _init_repo(repo)
    (repo / ".claude").mkdir()
    (repo / ".claude" / "autonomous-shipping-required").write_text("# hostile\n")
    subprocess.run(["git", "add", ".claude"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "hostile optin"], cwd=repo, check=True)
    (repo / "f").write_text("a\nb\n")  # dirty, so work-pending would be true
    return repo


def _fire(payload: dict, cwd, home, extra_env: dict | None = None) -> dict | None:
    return run_hook_stop(ADVANCE_HOOK, payload, cwd=cwd, home=home, extra_env=extra_env)


# ------------------------------------------------------------------ #
# Fire-predicate corpus                                               #
# ------------------------------------------------------------------ #

CORPUS: list[tuple[str, bool]] = [
    (ISSUE_QUOTE_QUESTION, True),
    (ISSUE_QUOTE_NONQUESTION, False),  # Row 19: non-question stops are uncaught
    (MODAL_CASE, True),  # "must FIRE — the modal case" per the plan
    (DOCUMENTED_MISS, True),  # accepted miss: exclusion window is final-sentence only
    ("I committed and pushed the change. Want me to profile it?", False),
    (
        'This repo\'s Stop hook fires on phrasing like "want me to commit '
        'this, or review the diff first?" — I mention it here for context. '
        "Anyway, the change is up.",
        False,
    ),  # self-referential mid-message quote; final sentence carries no ask
    ("Should I use approach A or approach B for the caching layer?", False),
    ("Want me to merge this PR now?", False),  # merge excluded
    (
        "I ran the tests and they are failing. Want me to commit anyway?",
        False,
    ),  # "anyway" exclusion
    ("Here is a summary of the changes I made to the parser module.", False),
]


@pytest.mark.parametrize("message,expected_fire", CORPUS)
def test_fire_predicate_corpus(
    armed_home, dirty_repo, message, expected_fire
):
    result = _fire(
        stop_input(
            message,
            session_id="s",
            prompt_id="p1",
            permission_mode="default",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    if expected_fire:
        assert result is not None, f"expected a block for: {message!r}"
    else:
        assert result is None, f"expected silence for: {message!r}, got {result!r}"


def test_case_folding_lowercase_verb_fires(armed_home, dirty_repo):
    """nocasematch must be active — a lowercase rephrasing of the verb half
    still fires."""
    result = _fire(
        stop_input(
            "ok, want me to COMMIT this now?",
            session_id="s",
            prompt_id="p1",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Loop guard                                                          #
# ------------------------------------------------------------------ #


def test_absent_prompt_id_silent(armed_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", cwd=str(dirty_repo)
    )
    assert "prompt_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_empty_prompt_id_silent(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_bounded_iteration_fires_exactly_once(armed_home, dirty_repo):
    payload = stop_input(
        ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
    )
    results = [_fire(payload, cwd=dirty_repo, home=armed_home) for _ in range(5)]
    fired = [r for r in results if r is not None]
    assert len(fired) == 1, f"expected exactly one fire across 5 identical calls, got {len(fired)}"
    assert results[0] is not None, "the first call must be the one that fires"


def test_new_prompt_id_rearms(armed_home, dirty_repo):
    first = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    second = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p2", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert first is not None
    assert second is not None, "a new prompt_id must re-arm the guard"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permission checks",
)
def test_state_file_write_failure_stays_silent(armed_home, dirty_repo):
    """Write failure (read-only $HOME/.claude) must not block — the hook
    reads its own write back and only emits when it matches."""
    claude_dir = armed_home / ".claude"
    original_mode = claude_dir.stat().st_mode
    claude_dir.chmod(0o555)
    try:
        result = _fire(
            stop_input(
                ISSUE_QUOTE_QUESTION,
                session_id="s",
                prompt_id="p1",
                cwd=str(dirty_repo),
            ),
            cwd=dirty_repo,
            home=armed_home,
        )
    finally:
        claude_dir.chmod(original_mode)
    assert result is None


# ------------------------------------------------------------------ #
# Opt-in matrix                                                       #
# ------------------------------------------------------------------ #


def test_machine_file_absent_silent(isolated_home, dirty_repo):
    """No autonomous-shipping-required sentinel anywhere — must stay silent
    regardless of an otherwise-firing message."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
    )
    assert result is None


def test_machine_file_present_no_optout_fires(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_machine_file_present_repo_optout_silent(armed_home, dirty_repo):
    (dirty_repo / ".claude").mkdir(exist_ok=True)
    (dirty_repo / ".claude" / "autonomous-shipping-optout").write_text("# optout\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_repo_committed_optin_with_machine_file_absent_stays_silent(
    isolated_home, repo_with_committed_optin
):
    """The redesign's central guarantee: a repo cannot grant this by
    committing anything. Machine sentinel absent, repo commits the
    identically-named file — must stay silent."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(repo_with_committed_optin),
        ),
        cwd=repo_with_committed_optin,
        home=isolated_home,
    )
    assert result is None


def test_config_dir_sentinel_fires(isolated_home, dirty_repo, tmp_path):
    """CLAUDE_CONFIG_DIR relocates the machine sentinel, kill switch, log
    file, and state dir together — all five hardcodes route through the
    same resolved config dir."""
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
    )
    assert result is not None


def test_config_dir_kill_switch_disables(isolated_home, dirty_repo, tmp_path):
    config_dir = tmp_path / "profile"
    config_dir.mkdir()
    (config_dir / "autonomous-shipping-required").write_text("# machine sentinel\n")
    (config_dir / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=isolated_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
    )
    assert result is None


def test_legacy_home_claude_sentinel_inert_once_config_dir_set(armed_home, dirty_repo, tmp_path):
    """The machine sentinel is a swap, not a union: armed_home's legacy
    $HOME/.claude/autonomous-shipping-required does not fire once
    CLAUDE_CONFIG_DIR points at a directory holding no copy of it —
    matches _lib_autonomous_shipping_active's existing swap semantics."""
    empty_config_dir = tmp_path / "empty-profile"
    empty_config_dir.mkdir()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
        extra_env={"CLAUDE_CONFIG_DIR": str(empty_config_dir)},
    )
    assert result is None


# ------------------------------------------------------------------ #
# Work-pending predicate                                              #
# ------------------------------------------------------------------ #


def test_dirty_tree_is_pending(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None


def test_ahead_of_upstream_is_pending(armed_home, ahead_of_upstream_repo):
    result = _fire(
        stop_input(
            "Want me to push this now, or do you want to review it first?",
            session_id="s",
            prompt_id="p1",
            cwd=str(ahead_of_upstream_repo),
        ),
        cwd=ahead_of_upstream_repo,
        home=armed_home,
    )
    assert result is not None


def test_clean_pushed_no_pr_is_not_pending(armed_home, clean_pushed_no_pending_repo):
    """Post-PR-open state: clean tree, HEAD matches upstream exactly — no
    work pending, must stay silent even though the message would fire."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_pushed_no_pending_repo),
        ),
        cwd=clean_pushed_no_pending_repo,
        home=armed_home,
    )
    assert result is None


def test_no_upstream_configured_clean_tree_is_pending(armed_home, clean_no_upstream_repo):
    """The common pre-first-push state: `@{u}` fails to resolve entirely
    (no upstream ref to diff against), not '0 commits ahead'. A naive
    empty-output-means-not-pending read would silently stay quiet on
    exactly the most common real trigger for this hook — 'I finished the
    change, want me to push it?' on a branch that's never been pushed."""
    result = _fire(
        stop_input(
            "Want me to push this now, or do you want to review it first?",
            session_id="s",
            prompt_id="p1",
            cwd=str(clean_no_upstream_repo),
        ),
        cwd=clean_no_upstream_repo,
        home=armed_home,
    )
    assert result is not None


# ------------------------------------------------------------------ #
# Structural gates                                                    #
# ------------------------------------------------------------------ #


def test_home_unset_silent(dirty_repo, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input="{}",
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={k: v for k, v in os.environ.items() if k != "HOME"},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_home_claude_dir_absent_silent(tmp_path, dirty_repo, monkeypatch):
    """Pins the $HOME-only resolution path — CLAUDE_CONFIG_DIR must be unset
    here (unlike isolated_home, this fixture-less $HOME override doesn't
    clear it), or an ambient CLAUDE_CONFIG_DIR on the test machine would
    resolve CONFIG_DIR to a real, populated directory instead of the bare
    sandboxed $HOME this test means to exercise."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    bare_home = tmp_path / "bare-home"
    bare_home.mkdir()
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=bare_home,
    )
    assert result is None


def test_kill_switch_disables(armed_home, dirty_repo):
    (armed_home / ".claude" / ".commit-stall-block-disabled").write_text("")
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_agent_type_present_silent(armed_home, dirty_repo):
    """Subagents are never force-continued — CLAUDE.md's Shipping section
    scopes this authorization to the session the engineer is talking to."""
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            agent_type="general-purpose",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_plan_mode_silent(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION,
            session_id="s",
            prompt_id="p1",
            permission_mode="plan",
            cwd=str(dirty_repo),
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is None


def test_missing_session_id_silent(armed_home, dirty_repo):
    payload = stop_input(ISSUE_QUOTE_QUESTION, prompt_id="p1", cwd=str(dirty_repo))
    assert "session_id" not in payload
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_missing_last_assistant_message_silent(armed_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
    }
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_null_last_assistant_message_silent(armed_home, dirty_repo):
    payload = {
        "hook_event_name": "Stop",
        "session_id": "s",
        "prompt_id": "p1",
        "cwd": str(dirty_repo),
        "last_assistant_message": None,
    }
    assert _fire(payload, cwd=dirty_repo, home=armed_home) is None


def test_traversal_session_id_denies_and_does_not_touch_marker_dir(armed_home, dirty_repo):
    canary = plant_traversal_canary(armed_home)
    payload = stop_input(
        ISSUE_QUOTE_QUESTION,
        session_id=TRAVERSAL_SESSION_ID,
        prompt_id="p1",
        cwd=str(dirty_repo),
    )
    result = _fire(payload, cwd=dirty_repo, home=armed_home)
    assert result is None, "a traversing session_id must not be treated as valid"
    assert canary.read_text() == CANARY_CONTENT, (
        "hook must not have written through the traversal path"
    )


# ------------------------------------------------------------------ #
# Layer-2 equivalents (hand-written — turn-gate hooks inherit none of        #
# test_hook_alignment.py's GATE_HOOKS auto-parametrization)                  #
# ------------------------------------------------------------------ #


def _path_without(binary: str, tmp_path: Path) -> str:
    """A PATH string mirroring the real PATH via a symlink farm, with
    `binary` omitted. Farm construction (full-mirror rationale, dedup,
    unreadable-dir handling) lives in `helpers.build_path_without`, shared
    with `test_hook_alignment.py`'s own session-memoized fixture."""
    farm_dir = tmp_path / f"path-without-{binary}"
    farm_dir.mkdir()
    return build_path_without(binary, farm_dir)


def test_jq_absent_exits_zero_empty_stdout(armed_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home), "PATH": _path_without("jq", tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_git_absent_silent(armed_home, dirty_repo, tmp_path):
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home), "PATH": _path_without("git", tmp_path)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_lib_sh_silent(armed_home, dirty_repo):
    """Turn-gate inversion of GATE_HOOKS's test_missing_lib_sh_denied: a
    gate hook denies when _lib.sh is absent, but this fail-open hook must
    exit 0 with no output instead — a hard failure here would turn an
    infrastructure gap into a stuck turn.

    The hook is COPIED (not symlinked) into a temp directory so that
    dirname($0) resolves to the temp dir, where _lib.sh is genuinely absent.

    Same caveat test_missing_lib_sh_denied documents for itself: this cannot
    structurally distinguish "exited silently because _lib.sh's own guard
    fired" from "exited silently because a downstream `_lib_*` call, now
    undefined, failed and an unrelated `|| exit 0` caught it." Both hooks'
    fail-open-everywhere design makes every downstream gate an equally valid
    exit path for this input shape — this test pins the observable behavior
    (silent exit under a missing _lib.sh), not the specific guard line.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_hook = Path(tmpdir) / ADVANCE_HOOK.name
        shutil.copy2(ADVANCE_HOOK, tmp_hook)
        tmp_hook.chmod(0o755)
        result = subprocess.run(
            [str(tmp_hook)],
            input=stop_input_json(ISSUE_QUOTE_QUESTION, "s", "p1", str(dirty_repo)),
            capture_output=True,
            text=True,
            cwd=dirty_repo,
            env={**os.environ, "HOME": str(armed_home)},
            check=False,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_malformed_json_stdin_silent(armed_home, dirty_repo):
    """Non-JSON stdin must exit 0 silently, not just a well-formed payload
    missing one field (test_missing_session_id_silent exercises that
    narrower case). The six-field _lib_jq read swallows a jq parse failure
    via 2>/dev/null into empty reads — this pins that the empty-read path
    still reaches a silent exit rather than tripping on unset variables."""
    result = subprocess.run(
        [str(ADVANCE_HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        cwd=dirty_repo,
        env={**os.environ, "HOME": str(armed_home)},
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def stop_input_json(message: str, session_id: str, prompt_id: str, cwd: str) -> str:
    return json.dumps(
        stop_input(message, session_id=session_id, prompt_id=prompt_id, cwd=cwd)
    )


# ------------------------------------------------------------------ #
# Emitted JSON shape                                                  #
# ------------------------------------------------------------------ #


def test_emitted_payload_is_exactly_decision_and_reason(armed_home, dirty_repo):
    result = _fire(
        stop_input(
            ISSUE_QUOTE_QUESTION, session_id="s", prompt_id="p1", cwd=str(dirty_repo)
        ),
        cwd=dirty_repo,
        home=armed_home,
    )
    assert result is not None
    # run_hook_stop already asserts the exact {"decision", "reason"} key
    # pair and decision == "block"; this pins the reason's content contract.
    assert isinstance(result["reason"], str)
    assert "/code-review" in result["reason"]
    assert "/ready-for-review" in result["reason"]
