"""Guard the global `userEmail`-redaction rule against silent removal.

`claude/.claude/CLAUDE.md` stows to `~/.claude/CLAUDE.md` and loads for
every session on this machine regardless of which project repo is open —
it is the only surface that reaches a sibling repo with no `CLAUDE.md` of
its own. A one-line bullet has no other structural signal (no dedicated
section heading, no hook) marking it load-bearing, so a future edit could
drop or reword it away without any mechanism noticing. Nothing else in
this suite re-checks that file's content against a specific rule.

This test does not assert the global and repo-root `CLAUDE.md` files stay
in sync (that is `CLAUDE.md` root's own decision, not this file's) — only
that the global file keeps its own baseline version of the rule.
"""
from __future__ import annotations

from helpers import CLAUDE_DIR

_GLOBAL_CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"


def test_global_claude_md_retains_userEmail_contact_copy_prohibition():
    text = _GLOBAL_CLAUDE_MD.read_text()

    lines_with_user_email = [
        line for line in text.splitlines() if "userEmail" in line
    ]
    assert lines_with_user_email, (
        f"{_GLOBAL_CLAUDE_MD}: no line mentions `userEmail` — the "
        "email-redaction rule appears to have been removed from the "
        "always-loaded global instruction file."
    )

    assert any("contact copy" in line for line in lines_with_user_email), (
        f"{_GLOBAL_CLAUDE_MD}: a `userEmail` line exists, but none "
        "mentions 'contact copy' — the prohibition on using it as "
        "publishable contact copy may have been reworded away."
    )

    assert any(
        "never" in line.lower() for line in lines_with_user_email
    ), (
        f"{_GLOBAL_CLAUDE_MD}: a `userEmail` line exists, but none "
        "contains 'never' — the rule may have been weakened from a "
        "prohibition to a mere suggestion."
    )
