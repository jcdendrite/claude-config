"""Regression tests for the statusline script.

Covers two invariants: the escape-injection invariant on the PR segment
(code-review item 13, security controls), and the context-size field beside
the percentage bar (context-cost-root-cause plan, mechanism 2) — not the
script's full formatting/truncation/color behavior.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from helpers import CLAUDE_DIR

STATUSLINE_SCRIPT = CLAUDE_DIR / "statusline-command.sh"

# Strips SGR color codes so assertions can match on plain rendered text.
_ANSI_SGR = re.compile(rb"\x1b\[[0-9;]*m")

# Matches the bar, percent, and size as one contiguous segment, so a match
# proves the size is rendered beside the percentage rather than merely
# present somewhere in stdout. Bar length is variable, not fixed at 10: BSD
# seq's `seq 1 0` (macOS) emits "1\n0" rather than GNU seq's empty output,
# so a 0%-filled bar renders 2 extra `#`.
_CONTEXT_SEGMENT = re.compile(r"\[[-#]+\] (\d+)% · (\d+)k")


# OSC 8 always emits `ESC]8;;` twice per link — once opening with the URL,
# once closing with an empty URL (`ESC]8;;ESC\`) — so a bare prefix count
# can't distinguish one legitimate link from an injected second one. Require
# non-empty content between `;;` and the terminator to count only opens that
# carry actual link content.
_OSC8_LINK_OPEN = re.compile(rb"\x1b\]8;;[^\x1b]+\x1b\\")

# Built from chr(27), not a string-literal escape, so no raw control byte
# sits in this source file. A JSON-string value carrying this byte (encoded
# on the wire as the six-character JSON escape for ESC) opens and closes a
# second, attacker-labeled OSC 8 link. `jq -r` decodes that JSON escape into
# a raw byte at parse time, independent of the echo-e/printf assembly fix —
# the vector this suite regression-tests.
_ESC = chr(27)
_INJECTED_OSC8 = _ESC + "]8;;http://evil" + _ESC + "\\CLICK" + _ESC + "]8;;" + _ESC + "\\"


def _run_statusline(payload: dict, env: dict | None = None) -> bytes:
    result = subprocess.run(
        ["bash", str(STATUSLINE_SCRIPT)],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
        env=env,
    )
    return result.stdout


def _isolated_env(tmp_path) -> dict:
    """Point CLAUDE_CONFIG_DIR at an empty dir so account-lookup output
    (real local ~/.claude.json content) can't leak into an assertion.
    """
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}


def _payload(
    pr: dict | None = None,
    context_window: dict | None = None,
    current_usage: dict | None = None,
) -> dict:
    """Build a stdin payload matching the documented statusline schema
    (code.claude.com/docs/en/statusline: model.id, model.display_name,
    context_window.{used_percentage,total_input_tokens,context_window_size}).
    """
    payload = {
        "model": {"id": "claude-sonnet-4-5-20250929", "display_name": "Sonnet"},
        "cwd": "/tmp",
    }
    if context_window is not None:
        payload["context_window"] = context_window
    if current_usage is not None:
        payload["current_usage"] = current_usage
    if pr is not None:
        payload["pr"] = pr
    return payload


def _strip_ansi(data: bytes) -> str:
    return _ANSI_SGR.sub(b"", data).decode()


class TestPullRequestEscapeInjection:
    def test_no_pr_field_renders_no_osc8(self):
        stdout = _run_statusline(_payload())
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 0

    def test_normal_pr_renders_exactly_one_osc8_pair(self):
        stdout = _run_statusline(
            _payload(
                {
                    "number": 1234,
                    "url": "https://github.com/x/y/pull/1234",
                    "review_state": "approved",
                }
            )
        )
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 1

    def test_review_state_json_escape_payload_does_not_inject_second_link(self):
        stdout = _run_statusline(
            _payload(
                {
                    "number": 1234,
                    "url": "https://github.com/x/y/pull/1234",
                    "review_state": "approved" + _INJECTED_OSC8,
                }
            )
        )
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 1

    def test_number_as_string_json_escape_payload_does_not_inject_second_link(self):
        stdout = _run_statusline(
            _payload(
                {
                    "number": "1234" + _INJECTED_OSC8,
                    "url": "https://github.com/x/y/pull/1234",
                    "review_state": "approved",
                }
            )
        )
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 1

    def test_url_json_escape_payload_does_not_inject_second_link(self):
        stdout = _run_statusline(
            _payload(
                {
                    "number": 1234,
                    "url": "https://github.com/x/y/pull/1234" + _INJECTED_OSC8,
                    "review_state": "approved",
                }
            )
        )
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 1

    def test_literal_backslash_e_text_in_url_does_not_become_live_escape(self):
        literal_backslash_e_payload = (
            r"https://github.com/x/y/pull/1\e]8;;http://evil\e\\CLICK\e]8;;\e\\"
        )
        stdout = _run_statusline(
            _payload(
                {
                    "number": 1,
                    "url": literal_backslash_e_payload,
                    "review_state": "pending",
                }
            )
        )
        assert len(_OSC8_LINK_OPEN.findall(stdout)) == 1


class TestContextSizeField:
    """context-cost-root-cause plan, mechanism 2: render the absolute
    context token count beside the percentage bar, since percentage alone is
    ambiguous across models with different window sizes.
    """

    def test_renders_size_beside_percentage_for_documented_schema_payload(self, tmp_path):
        stdout = _strip_ansi(
            _run_statusline(
                _payload(
                    context_window={
                        "used_percentage": 58,
                        "total_input_tokens": 315221,
                        "context_window_size": 200000,
                    }
                ),
                env=_isolated_env(tmp_path),
            )
        )
        first_line = stdout.splitlines()[0]
        match = _CONTEXT_SEGMENT.search(first_line)
        assert match is not None
        assert match.group(1) == "58"
        assert match.group(2) == "315"

    def test_context_window_absent_exits_zero_and_shows_dash_placeholder(self, tmp_path):
        result = subprocess.run(
            ["bash", str(STATUSLINE_SCRIPT)],
            input=json.dumps(_payload()).encode(),
            capture_output=True,
            env=_isolated_env(tmp_path),
        )
        assert result.returncode == 0
        first_line = _strip_ansi(result.stdout).splitlines()[0]
        assert "[----------] --" in first_line
        assert "k" not in first_line

    def test_rebuild_turn_with_cache_read_zero_still_renders_full_context_size(self, tmp_path):
        # A turn that just paid a cache-TTL rebuild: cache_read_input_tokens
        # collapses to 0 while cache_creation_input_tokens carries the
        # rewrite. The field must read total_input_tokens (unaffected by
        # that collapse), not cache_read_input_tokens, so it still shows the
        # full prefix size instead of 0.
        stdout = _strip_ansi(
            _run_statusline(
                _payload(
                    context_window={
                        "used_percentage": 62,
                        "total_input_tokens": 315221,
                        "context_window_size": 200000,
                    },
                    current_usage={
                        "cache_creation_input_tokens": 315221,
                        "cache_read_input_tokens": 0,
                    },
                ),
                env=_isolated_env(tmp_path),
            )
        )
        first_line = stdout.splitlines()[0]
        match = _CONTEXT_SEGMENT.search(first_line)
        assert match is not None
        assert match.group(2) == "315"

    def test_total_input_tokens_legitimate_zero_still_renders_not_absent(self, tmp_path):
        # jq's `//` falls back only on null/false, so a real 0 must stay
        # distinguishable from context_window being absent entirely: the
        # percentage bar renders (not the placeholder) and the size segment
        # shows "0k" instead of being silently dropped.
        stdout = _strip_ansi(
            _run_statusline(
                _payload(
                    context_window={
                        "used_percentage": 5,
                        "total_input_tokens": 0,
                        "context_window_size": 200000,
                    }
                ),
                env=_isolated_env(tmp_path),
            )
        )
        first_line = stdout.splitlines()[0]
        assert "[----------] --" not in first_line
        match = _CONTEXT_SEGMENT.search(first_line)
        assert match is not None
        assert match.group(1) == "5"
        assert match.group(2) == "0"

    def test_folds_into_existing_jq_call_with_no_additional_subprocess_fork(self, tmp_path):
        # CLAUDE_CONFIG_DIR points at an empty dir so the separate account-
        # lookup jq calls (gated on ~/.claude.json existing) don't fire,
        # isolating the count to the script's unconditional per-render jq
        # calls: model, cwd, the folded context_window pair, total_cost,
        # rate_5h, rate_7d, and the three pr.* fields. This count is
        # unchanged from before total_input_tokens was added, since it
        # shares the context_window jq call with used_percentage instead of
        # forking its own.
        result = subprocess.run(
            ["bash", "-x", str(STATUSLINE_SCRIPT)],
            input=json.dumps(
                _payload(
                    context_window={
                        "used_percentage": 58,
                        "total_input_tokens": 315221,
                        "context_window_size": 200000,
                    }
                )
            ).encode(),
            capture_output=True,
            env=_isolated_env(tmp_path),
            check=True,
        )
        jq_forks = len(re.findall(rb"jq -r", result.stderr))
        assert jq_forks == 9
