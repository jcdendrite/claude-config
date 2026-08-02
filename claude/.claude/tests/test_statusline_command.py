"""Regression tests for the statusline PR-link OSC 8 escape-injection fix.

Scope is intentionally narrow: the escape-injection invariant on the PR
segment (code-review item 13, security controls), not the script's full
formatting/truncation/color behavior.
"""
from __future__ import annotations

import json
import re
import subprocess

from helpers import CLAUDE_DIR

STATUSLINE_SCRIPT = CLAUDE_DIR / "statusline-command.sh"


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


def _run_statusline(payload: dict) -> bytes:
    result = subprocess.run(
        ["bash", str(STATUSLINE_SCRIPT)],
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return result.stdout


def _payload(pr: dict | None = None) -> dict:
    payload = {"model": {"display_name": "Sonnet"}, "cwd": "/tmp"}
    if pr is not None:
        payload["pr"] = pr
    return payload


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
