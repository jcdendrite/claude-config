"""Guards the one mistake re-arming raw API-body capture could make
consequential: writing the `env` block into the committed
claude/.claude/settings.json, which would enable unredacted wire-level
capture for every stow user rather than this machine alone. Capture is
armed only in the gitignored, machine-local settings.local.json.

Backstops guard-settings-session-keys.sh's GUARDED_KEYS_JSON, which does
not cover `env` (a design question of its own -- see the plan's Out of
scope) -- this test pins these three keys specifically rather than
reopening that broader question.
"""
from __future__ import annotations

import json

from helpers import CLAUDE_DIR

SETTINGS_JSON = CLAUDE_DIR / "settings.json"

GUARDED_RAW_BODY_KEYS = (
    "OTEL_LOG_RAW_API_BODIES",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_LOGS_EXPORTER",
)


def test_committed_settings_json_has_no_raw_body_capture_env_keys():
    settings = json.loads(SETTINGS_JSON.read_text())
    env = settings.get("env", {})
    for key in GUARDED_RAW_BODY_KEYS:
        assert key not in env, (
            f"{key} found in committed claude/.claude/settings.json env block -- "
            "this would enable unredacted raw API-body capture for every stow "
            "user. Capture is machine-local only, armed in the gitignored "
            "settings.local.json."
        )
