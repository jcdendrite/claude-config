"""Test helpers shared by the skill-group test files (test_transcript_skill_invocation.py,
test_transcript_skill_pair.py, test_transcript_judgment_pair.py) and by test_transcript_analysis.py's
cross-subcommand tables."""
import argparse

from transcript_analysis import review_rounds

from .conftest import _asst, _user_msg

# Shared fixed timestamp: 2026-05-12T12:00:00Z, ISO week 2026-W20.
_TS_FIXED = "2026-05-12T12:00:00Z"


def _skill_pair_args(leader="plan-it", follower="plan-review", *, projects="*", this_repo=False,
                      exclude_projects=None, branches=None):
    return type("A", (), {
        "leader": leader,
        "follower": follower,
        "projects": projects,
        "this_repo": this_repo,
        "exclude_projects": exclude_projects,
        "branches": branches,
    })()


def _skill_inv_args(*, projects="*", branches=None, include_subagents=False):
    """Args object for cmd_skill_invocation. projects='*' takes the explicit-glob
    escape hatch, bypassing git derivation — the default for cases that only
    exercise counting/filtering logic on fake_projects data."""
    return argparse.Namespace(
        projects=projects, branches=branches, include_subagents=include_subagents
    )


def _judgment_pair_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    skills: str | None = None,
    truncate_chars: int = 1000,
    out: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "skills": skills or ",".join(review_rounds.REVIEW_SKILLS),
        "truncate_chars": truncate_chars,
        "out": out,
    })()


def _review_asst(text: str, ts: str, branch: str = "main") -> dict:
    """Main-thread assistant record with plain text content."""
    return _asst("claude-sonnet-4-6", branch=branch, ts=ts, content=[{"type": "text", "text": text}])


def _user_reply(text: str, branch: str = "main") -> dict:
    """Plain user message (fresh user prompt)."""
    return _user_msg(text, branch=branch)
