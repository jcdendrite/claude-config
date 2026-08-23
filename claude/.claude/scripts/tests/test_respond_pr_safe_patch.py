"""Tests for respond-pr-safe-patch.sh.

The gh CLI is replaced by a PATH shim that answers GET
(`gh api repos/<owner>/<repo>/pulls/comments/<id> --jq '.body'`) and PATCH
(`gh api ... -X PATCH -F body=<value>`) calls against a synthetic PR review
comment, and records every invocation it receives so tests can assert on
call history -- not just the script's exit code -- for the refusal path's
most important property: the PATCH is never even attempted.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from conftest import _shimmed_env

_SCRIPT = Path(__file__).parent.parent / "respond-pr-safe-patch.sh"

_CLAUDE_CODE_BODY = (
    "**[Claude Code]** original body\n\n"
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
)
_HUMAN_BODY = "Please fix this typo."
_GET_ERROR_SENTINEL = "__error__"


def _gh_shim_source(call_log: Path, comment_bodies: dict[str, str], *, patch_exit: int = 0) -> str:
    """gh shim simulating GET/PATCH against a PR review comment.

    comment_bodies maps comment-id (str) -> current body returned by the
    GET. A comment-id with no entry, or the "__error__" sentinel value,
    fails the GET (non-zero exit, no stdout) -- for exercising the
    fetch-failure path (bad id / auth / network failure), distinct from a
    fetched body that simply doesn't start with the Claude Code prefix.
    patch_exit models the PATCH call itself failing (auth/network/rate-limit
    after a successful GET) once the ownership check has already passed.

    Every invocation (GET and PATCH alike) is appended to call_log as one
    JSON object per line ({"args": [...]}), so a test can prove a PATCH was
    never attempted rather than inferring it from the exit code alone.
    """
    payload = json.dumps(comment_bodies)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import re
        import sys

        COMMENT_BODIES = json.loads({payload!r})
        CALL_LOG = {str(call_log)!r}

        args = sys.argv[1:]
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps({{"args": args}}) + chr(10))

        if not args or args[0] != "api":
            sys.exit(1)

        match = re.search(r"pulls/comments/([^/]+)$", args[1])
        comment_id = match.group(1) if match else None

        if "-X" in args and "PATCH" in args:
            sys.exit({patch_exit})

        # GET: `gh api repos/<owner>/<repo>/pulls/comments/<id> --jq .body`
        if comment_id not in COMMENT_BODIES or COMMENT_BODIES[comment_id] == {_GET_ERROR_SENTINEL!r}:
            sys.exit(1)
        print(COMMENT_BODIES[comment_id])
        sys.exit(0)
    """)


@pytest.fixture()
def fake_gh(tmp_path):
    """Factory installing a gh shim plus its call-recording log.

    Usage: env, call_log = fake_gh({"42": "**[Claude Code]** ..."})
    """
    def _make(comment_bodies: dict[str, str], *, patch_exit: int = 0) -> tuple[dict, Path]:
        call_log = tmp_path / "gh_calls.jsonl"
        env = _shimmed_env(tmp_path, _gh_shim_source(call_log, comment_bodies, patch_exit=patch_exit))
        return env, call_log

    return _make


def _read_calls(call_log: Path) -> list[list[str]]:
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line]


def _patch_calls(calls: list[list[str]]) -> list[dict]:
    """Extract {comment_id, body, flag} for every recorded call shaped like a
    PATCH -- flag is whichever of -f/-F immediately precedes the body=...
    argument, letting a test assert the script uses gh's raw-string flag
    (-f), not the typed flag (-F), which applies magic value coercion
    (true/false/null/integer, {owner}/{repo}/{branch} substitution, and a
    leading @ read as a filename) unsafe for an arbitrary PR comment body."""
    result = []
    for args in calls:
        if "-X" in args and "PATCH" in args:
            comment_id = args[1].rsplit("/", 1)[-1]
            body_index = next(i for i, a in enumerate(args) if a.startswith("body="))
            body_arg = args[body_index]
            flag = args[body_index - 1] if body_index > 0 else None
            result.append({
                "comment_id": comment_id,
                "body": body_arg[len("body="):],
                "flag": flag,
            })
    return result


def _run_script(
    cwd: Path, env: dict, args: list[str], *, input_text: str | None = None,
) -> subprocess.CompletedProcess:
    kwargs: dict = {"stdin": subprocess.DEVNULL} if input_text is None else {"input": input_text}
    return subprocess.run(
        [str(_SCRIPT), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        **kwargs,
    )


class TestUsageError:
    """Wrong argc exits non-zero with a usage message and issues no gh calls at all."""

    @pytest.mark.parametrize("args", [[], ["owner/repo"], ["owner/repo", "1", "extra"]])
    def test_wrong_argc_no_gh_calls(self, tmp_path, fake_gh, args):
        env, call_log = fake_gh({})
        result = _run_script(tmp_path, env, args)
        assert result.returncode == 2
        assert "Usage" in result.stderr
        assert _read_calls(call_log) == []


class TestInvalidArgumentShape:
    """A `..`-segment or non-numeric argument is rejected before any gh call
    -- closes the path-traversal gap where an unvalidated $1/$2 could
    redirect the PATCH to a different repo or comment than intended."""

    @pytest.mark.parametrize("repo", ["owner/repo/../../other-org/other-repo", "owner", "owner/repo/extra"])
    def test_invalid_repo_shape_no_gh_calls(self, tmp_path, fake_gh, repo):
        env, call_log = fake_gh({})
        result = _run_script(tmp_path, env, [repo, "42"], input_text="body\n")
        assert result.returncode == 2
        assert _read_calls(call_log) == []

    @pytest.mark.parametrize("comment_id", ["42/../../999", "abc", "-1"])
    def test_invalid_comment_id_shape_no_gh_calls(self, tmp_path, fake_gh, comment_id):
        env, call_log = fake_gh({})
        result = _run_script(tmp_path, env, ["owner/repo", comment_id], input_text="body\n")
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestEmptyStdin:
    """A caller that omits the body (e.g. drops the heredoc) must not fall
    through to a PATCH with an empty body -- exit 2, same class as the argv
    usage errors, with no gh call at all."""

    def test_empty_stdin_exits_two_no_gh_calls(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text=None)
        assert result.returncode == 2
        assert "empty" in result.stderr.lower()
        assert _read_calls(call_log) == []

    def test_explicit_empty_string_stdin_exits_two_no_gh_calls(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="")
        assert result.returncode == 2
        assert _read_calls(call_log) == []

    @pytest.mark.parametrize("whitespace_only_stdin", [" ", "\t"])
    def test_whitespace_only_stdin_exits_two_no_gh_calls(self, tmp_path, fake_gh, whitespace_only_stdin):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text=whitespace_only_stdin)
        assert result.returncode == 2
        assert _read_calls(call_log) == []

    def test_whitespace_only_stdin_exits_two_under_c_locale(self, tmp_path, fake_gh):
        """[[:space:]] narrows to byte-wise ASCII matching under LC_ALL=C --
        confirm the ASCII whitespace this guard exists for is still caught
        even when the pattern's wider Unicode-aware coverage degrades."""
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        env = {**env, "LC_ALL": "C", "LANG": "C"}
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text=" ")
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestGetFailure:
    """A GET failure (bad id / auth / network) aborts before any PATCH, with
    a message distinct from the ownership-mismatch refusal."""

    def test_get_failure_exits_nonzero_no_patch(self, tmp_path, fake_gh):
        env, call_log = fake_gh({})  # no entry for "999" -> GET fails
        result = _run_script(tmp_path, env, ["owner/repo", "999"], input_text="new body\n")
        assert result.returncode != 0
        assert "could not fetch" in result.stderr.lower()
        assert "/replies" not in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []


class TestOwnershipMismatchRefused:
    """The refusal path: target comment is not Claude-authored. Zero PATCH
    calls, not just exit code 1."""

    def test_non_claude_comment_refused_no_patch(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _HUMAN_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="corrected text\n")
        assert result.returncode == 1
        assert "/replies" in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []


class TestReplacementBodyMustPreserveMarker:
    """The replacement body must itself start with the marker when the
    target comment is already Claude-authored -- refuses to let a caller
    silently strip the ownership marker via the PATCH. Zero PATCH calls on
    refusal, not just exit code 1."""

    def test_replacement_body_missing_marker_refused_no_patch(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="corrected text\n")
        assert result.returncode == 1
        assert "does not start with" in result.stderr
        assert "ownership marker" in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []

    def test_replacement_body_exactly_bare_marker_is_allowed(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="**[Claude Code]**")
        assert result.returncode == 0
        patches = _patch_calls(_read_calls(call_log))
        assert len(patches) == 1
        assert patches[0]["body"] == "**[Claude Code]**"

    def test_replacement_body_with_marker_not_at_start_refused_no_patch(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text=" **[Claude Code]** corrected text\n")
        assert result.returncode == 1
        assert "does not start with" in result.stderr
        assert "ownership marker" in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []

    def test_replacement_body_wrong_case_marker_refused_no_patch(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="**[claude code]** corrected text\n")
        assert result.returncode == 1
        assert "does not start with" in result.stderr
        assert "ownership marker" in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []

    def test_marker_prefixed_body_does_not_bypass_ownership_check(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"42": _HUMAN_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "42"], input_text="**[Claude Code]** corrected text\n")
        assert result.returncode == 1
        assert "/replies" in result.stderr
        assert "ownership marker" not in result.stderr
        assert _patch_calls(_read_calls(call_log)) == []


class TestAllowPathPatchesExactCommentWithExactBody:
    """The allow path: target comment is Claude-authored. Exactly one PATCH,
    against the exact comment id, with the exact stdin body."""

    def test_claude_authored_comment_is_patched(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"7": _CLAUDE_CODE_BODY})
        new_body = (
            "**[Claude Code]** corrected text\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        result = _run_script(tmp_path, env, ["owner/repo", "7"], input_text=new_body)
        assert result.returncode == 0
        patches = _patch_calls(_read_calls(call_log))
        assert len(patches) == 1
        assert patches[0]["comment_id"] == "7"
        assert patches[0]["body"] == new_body


class TestPatchFailureAfterOwnershipCheckPropagates:
    """A PATCH failure (auth/network/rate-limit) after the ownership check
    already passed still exits the script non-zero -- `set -e` isn't
    swallowed by the case statement the PATCH call lives inside."""

    def test_patch_failure_exits_nonzero(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"7": _CLAUDE_CODE_BODY}, patch_exit=1)
        result = _run_script(tmp_path, env, ["owner/repo", "7"], input_text="**[Claude Code]** corrected text\n")
        assert result.returncode != 0
        patches = _patch_calls(_read_calls(call_log))
        assert len(patches) == 1


class TestGhMagicValueShapedBodiesUseRawFlag:
    """The script always invokes gh with -f (raw string), never -F (typed),
    regardless of body content -- a regression to -F would type-coerce a
    gh-magic-value-shaped body (leading @, or the literal null/true/false/
    an integer) instead of PATCHing the literal text. The marker prefix
    required by TestReplacementBodyMustPreserveMarker means none of these
    bodies are literal gh -F magic values in production; the values below
    keep that shape suffixed on for illustration, but this class now pins
    the -f flag choice itself, not the magic-value coercion hazard."""

    @pytest.mark.parametrize("tricky_body", [
        "**[Claude Code]** @correction, see below.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        "**[Claude Code]** null",
        "**[Claude Code]** true",
        "**[Claude Code]** 42",
    ])
    def test_magic_value_shaped_body_uses_raw_flag(self, tmp_path, fake_gh, tricky_body):
        env, call_log = fake_gh({"9": _CLAUDE_CODE_BODY})
        result = _run_script(tmp_path, env, ["owner/repo", "9"], input_text=tricky_body)
        assert result.returncode == 0
        patches = _patch_calls(_read_calls(call_log))
        assert len(patches) == 1
        assert patches[0]["flag"] == "-f"
        assert patches[0]["body"] == tricky_body


class TestSpecialCharactersReachPatchUnexpanded:
    """A stdin body containing a backtick, a $VAR-shaped sequence, multi-line
    markdown, and an emoji trailer reaches the PATCH call byte-for-byte --
    the property the call site's quoted heredoc terminator protects, and
    proof the script's own -F body="$BODY" doesn't reintroduce shell
    re-expansion."""

    def test_shell_metacharacters_survive_unexpanded(self, tmp_path, fake_gh):
        env, call_log = fake_gh({"13": _CLAUDE_CODE_BODY})
        tricky_body = (
            "**[Claude Code]** Use `git status` here, not $HOME or $(whoami).\n"
            "\n"
            "Second line of the correction.\n"
            "\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        )
        result = _run_script(tmp_path, env, ["owner/repo", "13"], input_text=tricky_body)
        assert result.returncode == 0
        patches = _patch_calls(_read_calls(call_log))
        assert len(patches) == 1
        assert patches[0]["body"] == tricky_body
        assert "$(whoami)" in patches[0]["body"]
        assert "$HOME" in patches[0]["body"]
