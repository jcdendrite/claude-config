"""Per-session, per-UTC-day transcript pricing, the one transcript_analysis module with a
stability promise for importers outside this repository. The session_pricing.py section of
docs/transcript-analysis-architecture.md states the import path, the promise, the error and
stderr contracts, and what the output can disclose.
"""
from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from transcript_analysis import corpus, pricing

__all__ = ["SessionPricingError", "SessionPricingResult", "SessionSpend", "price_sessions_by_utc_day"]

_USAGE_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_CACHE_CREATION_TOKEN_KEYS = ("ephemeral_1h_input_tokens", "ephemeral_5m_input_tokens")
_MICRO_DOLLARS_PER_DOLLAR = 1_000_000


class SessionPricingError(Exception):
    """One session could not be priced. str() names only session_index. The session_pricing.py section of
    docs/transcript-analysis-architecture.md states what __cause__ may carry."""

    def __init__(self, session_index: int) -> None:
        super().__init__(session_index)
        self.session_index = session_index

    def __str__(self) -> str:
        return f"session_paths[{self.session_index}] could not be priced"


@dataclass(frozen=True, kw_only=True)
class SessionSpend:
    """One input session's in-window spend. A day is the UTC date of a turn's timestamp, and a merged
    requestId run, contiguous or not, takes its first record's.

    main_file_readable: False when the main transcript could not be opened or read, a missing file included;
        the maps are then empty and the count is zero. True does not mean every line or subagent file was
        read, since undecodable lines and unreadable subagent files are skipped.
    micro_dollars_by_day: priced spend per day in integer micro-dollars. Each class amount enters as the exact
        decimal of its repr, and each day's exact sum rounds half-up. Days that round to zero are absent, and
        keys ascend.
    unpriced_tokens_by_day: tokens per day on turns whose model has no rate. Days with zero tokens are absent.
        Keys ascend.
    undated_turn_count: turns with nonzero priced spend or unpriced tokens whose timestamp is missing,
        unparseable, has no UTC offset, or falls outside the representable UTC date range. They enter no
        day map and no run-level model list. A truthy non-string timestamp on an assistant turn with a
        non-empty usage object is not undated: it fails the session with SessionPricingError.
    """

    main_file_readable: bool
    micro_dollars_by_day: Mapping[date, int]
    unpriced_tokens_by_day: Mapping[date, int]
    undated_turn_count: int


@dataclass(frozen=True, kw_only=True)
class SessionPricingResult:
    """Every session's spend plus the facts that span the whole call. In-window means a turn's day lies in
    [start, end].

    sessions: one SessionSpend per input path, in input order.
    total_micro_dollars: the sum of every session's micro_dollars_by_day values.
    total_unpriced_tokens: the sum of every session's unpriced_tokens_by_day values.
    unpriced_models: sorted model IDs of in-window turns whose model has no rate, except "<synthetic>" at zero
        tokens. An absent, null, or non-string model is "". Before publishing or rendering entries, see the
        doc section's disclosure paragraph.
    stale_models: sorted model IDs priced in-window, zero-token turns included, whose re-verify-by date is
        before as_of.
    pricing_fetch_date: the date the rate table was read from the vendor's pricing page.
    pricing_reverify_by: the earliest re-verify-by date among models priced in-window, or None when no
        in-window turn was priced.
    usage_drift_detected: a requestId run merged during this call, in or out of the window, had non-identical
        input or cache usage across its records.
    subagent_format_drift_detected: this call's sessions recorded subagent spawns but no sidechain assistant
        turns, counting every record read regardless of window.
    request_id_collision_count: distinct non-empty requestIds on assistant records in more than one session,
        regardless of window; their turns are priced in every such session.
    format_drift_detected: usage_drift_detected or subagent_format_drift_detected.
    """

    sessions: tuple[SessionSpend, ...]
    total_micro_dollars: int
    total_unpriced_tokens: int
    unpriced_models: tuple[str, ...]
    stale_models: tuple[str, ...]
    pricing_fetch_date: date
    pricing_reverify_by: date | None
    usage_drift_detected: bool
    subagent_format_drift_detected: bool
    request_id_collision_count: int

    @property
    def format_drift_detected(self) -> bool:
        return self.usage_drift_detected or self.subagent_format_drift_detected


class _PricedSession(NamedTuple):
    spend: SessionSpend
    usage_drift: bool
    subagent_spawns: int
    sidechain_assistant_turns: int
    request_ids: set[object]
    priced_models: set[str]
    unpriced_tokens_by_model: dict[str, int]


def _is_token_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_token_counts(container: dict, keys: Sequence[str], key_prefix: str) -> None:
    for key in keys:
        if key in container and not _is_token_count(container[key]):
            raise ValueError(f"usage token field {key_prefix}{key} is not a non-negative integer")


def _validate_usage_token_fields(usage: object) -> None:
    """Raise ValueError unless usage is a dict whose present token fields, nested cache_creation ones included,
    are non-negative ints."""
    if not isinstance(usage, dict):
        raise ValueError(f"usage is not an object (got {type(usage).__name__})")
    _require_token_counts(usage, _USAGE_TOKEN_KEYS, "")
    cache_creation = usage.get("cache_creation")
    if cache_creation is None:
        return
    if not isinstance(cache_creation, dict):
        raise ValueError(f"usage cache_creation is not an object (got {type(cache_creation).__name__})")
    _require_token_counts(cache_creation, _CACHE_CREATION_TOKEN_KEYS, "cache_creation.")


def _exact_dollars(amount: float) -> Fraction:
    return Fraction(repr(amount))


def _micro_dollars_half_up(total_dollars: Fraction) -> int:
    return math.floor(total_dollars * _MICRO_DOLLARS_PER_DOLLAR + Fraction(1, 2))


def _utc_day(timestamp: str | None) -> date | None:
    """UTC date of an ISO-8601 timestamp, or None when it is missing, unparseable, carries no UTC offset, or
    falls outside the representable UTC date range."""
    parsed = corpus._parse_iso_datetime(timestamp)
    if parsed is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(UTC).date()
    except OverflowError:
        return None


def _price_session(path: Path, start: date, end: date) -> _PricedSession:
    groups = corpus._read_session_file_partitioned(path, include_subagents=True)
    if not groups:
        return _PricedSession(
            spend=SessionSpend(
                main_file_readable=False, micro_dollars_by_day={}, unpriced_tokens_by_day={}, undated_turn_count=0
            ),
            usage_drift=False,
            subagent_spawns=0,
            sidechain_assistant_turns=0,
            request_ids=set(),
            priced_models=set(),
            unpriced_tokens_by_model={},
        )
    records = [record for group in groups for record in group]
    for record in records:
        if not isinstance(record, dict):
            raise TypeError(f"transcript record is not a JSON object (got {type(record).__name__})")

    deduped = pricing._dedup_session(records)
    request_ids = {
        turn["requestId"] for turn in deduped.turns if turn.get("type") == "assistant" and turn.get("requestId")
    }

    exact_dollars_by_day: dict[date, Fraction] = {}
    unpriced_tokens_by_day: dict[date, int] = {}
    priced_models: set[str] = set()
    unpriced_tokens_by_model: dict[str, int] = {}
    undated_turn_count = 0
    for turn in deduped.turns:
        if turn.get("type") != "assistant":
            continue
        message = turn.get("message") or {}
        usage = message.get("usage")
        if not usage:
            continue
        _validate_usage_token_fields(usage)
        model = message.get("model")
        if not isinstance(model, str):
            model = ""
        day = _utc_day(turn.get("timestamp"))
        if day is not None and not start <= day <= end:
            continue

        dollars_by_class, _, unpriced_tokens = pricing._price_turn(model, usage)
        if day is None:
            has_spend = unpriced_tokens != 0 or (
                dollars_by_class is not None and any(amount != 0 for amount in dollars_by_class.values())
            )
            if has_spend:
                undated_turn_count += 1
        elif dollars_by_class is None:
            unpriced_tokens_by_model[model] = unpriced_tokens_by_model.get(model, 0) + unpriced_tokens
            unpriced_tokens_by_day[day] = unpriced_tokens_by_day.get(day, 0) + unpriced_tokens
        else:
            priced_models.add(model)
            turn_dollars = sum((_exact_dollars(amount) for amount in dollars_by_class.values()), Fraction(0))
            exact_dollars_by_day[day] = exact_dollars_by_day.get(day, Fraction(0)) + turn_dollars

    micro_dollars_by_day: dict[date, int] = {}
    for day, exact_dollars in sorted(exact_dollars_by_day.items()):
        micro_dollars = _micro_dollars_half_up(exact_dollars)
        if micro_dollars != 0:
            micro_dollars_by_day[day] = micro_dollars
    return _PricedSession(
        spend=SessionSpend(
            main_file_readable=True,
            micro_dollars_by_day=micro_dollars_by_day,
            unpriced_tokens_by_day={day: tokens for day, tokens in sorted(unpriced_tokens_by_day.items()) if tokens != 0},
            undated_turn_count=undated_turn_count,
        ),
        usage_drift=deduped.usage_drift,
        subagent_spawns=deduped.subagent_spawns,
        sidechain_assistant_turns=deduped.sidechain_assistant_turns,
        request_ids=request_ids,
        priced_models=priced_models,
        unpriced_tokens_by_model=unpriced_tokens_by_model,
    )


def _require_plain_date(name: str, value: object) -> None:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a datetime.date, not a datetime.datetime (got {type(value).__name__})")


def _can_be_passed_to_os(raw_path: str) -> bool:
    """Whether the operating system can open raw_path: it has no NUL and encodes in the filesystem encoding."""
    if "\x00" in raw_path:
        return False
    try:
        os.fsencode(raw_path)
    except ValueError:  # UnicodeEncodeError, for a lone surrogate, is a ValueError subclass.
        return False
    return True


def _session_paths_as_paths(session_paths: Sequence[str | os.PathLike[str]]) -> list[Path]:
    if isinstance(session_paths, str | bytes | os.PathLike):
        raise TypeError(f"session_paths must be a sequence of paths, not one path (got {type(session_paths).__name__})")
    paths: list[Path] = []
    for index, element in enumerate(session_paths):
        try:
            raw_path = os.fspath(element)
        except TypeError:
            raw_path = None
        if not isinstance(raw_path, str):
            raise TypeError(f"session_paths[{index}] must be a str or os.PathLike[str] (got {type(element).__name__})")
        if not _can_be_passed_to_os(raw_path):
            raise ValueError(f"session_paths[{index}] cannot be passed to the operating system as a path")
        paths.append(Path(raw_path))
    return paths


def price_sessions_by_utc_day(
    session_paths: Sequence[str | os.PathLike[str]],
    *,
    start: date,
    end: date,
    as_of: date,
) -> SessionPricingResult:
    """Price every session's in-window assistant turns by UTC day, plus the facts that span the whole call.

    session_paths: main transcript paths, each once. Each is also read with its <stem>/subagents/*.jsonl, so
        passing a subagent file or one path twice double-counts.
    start, end: inclusive UTC dates.
    as_of: the date stale_models is judged against.

    Raises ValueError or TypeError for a bad argument before reading anything, and SessionPricingError for
    the first session that cannot be priced. A session still being written gives a lower bound for its
    newest turn. Prints nothing itself. Memory grows with the call's unique requestIds plus one fully-read
    session.
    """
    for name, value in (("start", start), ("end", end), ("as_of", as_of)):
        _require_plain_date(name, value)
    if start > end:
        raise ValueError("start is after end")
    paths = _session_paths_as_paths(session_paths)

    spends: list[SessionSpend] = []
    usage_drift_detected = False
    total_spawns = 0
    total_sidechain_turns = 0
    first_session_index_by_request_id: dict[object, int] = {}
    colliding_request_ids: set[object] = set()
    priced_models: set[str] = set()
    unpriced_tokens_by_model: dict[str, int] = {}
    for index, path in enumerate(paths):
        try:
            priced_session = _price_session(path, start, end)
        except Exception as exc:
            raise SessionPricingError(index) from exc
        spends.append(priced_session.spend)
        usage_drift_detected = usage_drift_detected or priced_session.usage_drift
        total_spawns += priced_session.subagent_spawns
        total_sidechain_turns += priced_session.sidechain_assistant_turns
        for request_id in priced_session.request_ids:
            if first_session_index_by_request_id.setdefault(request_id, index) != index:
                colliding_request_ids.add(request_id)
        priced_models |= priced_session.priced_models
        for model, tokens in priced_session.unpriced_tokens_by_model.items():
            unpriced_tokens_by_model[model] = unpriced_tokens_by_model.get(model, 0) + tokens

    subagent_format_drift_detected = pricing._warn_if_subagent_format_drift(total_spawns, total_sidechain_turns)
    return SessionPricingResult(
        sessions=tuple(spends),
        total_micro_dollars=sum(sum(spend.micro_dollars_by_day.values()) for spend in spends),
        total_unpriced_tokens=sum(sum(spend.unpriced_tokens_by_day.values()) for spend in spends),
        unpriced_models=tuple(sorted(pricing._reportable_unpriced_model_ids(unpriced_tokens_by_model))),
        stale_models=tuple(sorted(model for model in priced_models if pricing._rate_expired(model, as_of))),
        pricing_fetch_date=pricing._PRICING_FETCH_DATE,
        pricing_reverify_by=min((pricing._MODEL_RATE_EXPIRES[model] for model in priced_models), default=None),
        usage_drift_detected=usage_drift_detected,
        subagent_format_drift_detected=subagent_format_drift_detected,
        request_id_collision_count=len(colliding_request_ids),
    )
