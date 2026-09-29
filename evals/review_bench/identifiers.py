"""Grammars for identifiers that reach a filesystem path, a glob, or a git
argument: a campaign ID, a session ID, and a base ref."""
from __future__ import annotations

import re
import uuid

_CAMPAIGN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
# No leading "-", so a ref can never be read as a git option.
_BASE_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./~^-]{0,127}")


class InvalidIdentifierError(ValueError):
    pass


def validate_campaign_id(campaign_id: str) -> str:
    """A campaign ID names one path component under the run-store and records
    directories, so it must not carry a separator or start with a dot."""
    if not _CAMPAIGN_ID_PATTERN.fullmatch(campaign_id):
        raise InvalidIdentifierError(
            f"invalid campaign ID {campaign_id!r}: use 1-64 characters from [A-Za-z0-9_.-], "
            "starting with a letter or digit"
        )
    return campaign_id


def validate_base_ref(base_ref: str) -> str:
    if not _BASE_REF_PATTERN.fullmatch(base_ref) or ".." in base_ref:
        raise InvalidIdentifierError(
            f"invalid base ref {base_ref!r}: use 1-128 characters from [A-Za-z0-9_./~^-], "
            "starting with a letter or digit, with no '..'"
        )
    return base_ref


def validate_session_id(session_id: str) -> str:
    """A session ID is interpolated into a glob, so only the canonical
    lowercase UUID form (or "" for no session yet) is accepted."""
    if session_id == "":
        return session_id
    try:
        canonical = str(uuid.UUID(session_id))
    except ValueError:
        canonical = None
    if canonical != session_id:
        raise InvalidIdentifierError(f"invalid session ID {session_id!r}: not a canonical UUID")
    return session_id
