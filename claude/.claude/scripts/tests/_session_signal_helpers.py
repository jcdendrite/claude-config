"""Test helpers shared by the session-signal test files (test_transcript_buckets.py,
test_transcript_fail_seq.py, test_transcript_user_input.py) and by test_transcript_analysis.py's
per-record attribution classes.
"""

_SONNET = "claude-sonnet-4-6"


def _branch_args(**fields) -> object:
    """Args object for a per-record --branches consumer; `fields` overrides the defaults."""
    base = {"projects": "*", "this_repo": False, "branches": None}
    base.update(fields)
    return type("A", (), base)()


def _ui_user(content, *, branch: str = "main", ts: str | None = None,
             session_id: str | None = None, **extra_fields) -> dict:
    """Fresh-prompt-shaped user record for user-input tests, with optional
    timestamp/sessionId/extra top-level fields the shared _user_msg helper
    doesn't parametrize."""
    rec: dict = {"type": "user", "gitBranch": branch, "message": {"content": content}}
    if ts:
        rec["timestamp"] = ts
    if session_id:
        rec["sessionId"] = session_id
    rec.update(extra_fields)
    return rec


def _user_input_args(
    *,
    projects: str = "*",
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    corrections_only: bool = False,
    truncate_chars: int = 500,
    out: str | None = None,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "branches": branches,
        "since": since,
        "until": until,
        "corrections_only": corrections_only,
        "truncate_chars": truncate_chars,
        "out": out,
        "redact": redact,
    })()
