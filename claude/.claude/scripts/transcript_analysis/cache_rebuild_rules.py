"""The cache-rebuild family's pure rules: per-call cause classification against
the vendor's 5m/1h cache tiers, subagent idle-gap cause attribution, per-call
priced excess and cacheTtl switch deltas, and the --ttl-verdict per-root
reducers, plus every label constant they emit.

Imports its package dependencies by module (attribute access, not by name) --
see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from transcript_analysis import corpus, pricing

# Idle-gap boundaries mirror the vendor's own 5-minute/1-hour cache tiers
# (_CACHE_WRITE_5M_MULTIPLIER/_CACHE_WRITE_1H_MULTIPLIER in pricing.py, same source).
_CACHE_REBUILD_IDLE_5M_SECONDS = 300
_CACHE_REBUILD_IDLE_1H_SECONDS = 3600

# --ttl-verdict's second boundary point for its two-point sensitivity check
# (.claude/plans/cache-ttl-tuning-analysis.md's Approach section): the
# vendor's own illustrative "about 1 minute" margin a 4-minute-streaming
# response leaves inside a 5-minute TTL, used here as an alternate idle-band
# lower bound. A direction adopts only when its margin clears at both this
# boundary and _CACHE_REBUILD_IDLE_5M_SECONDS.
_CACHE_REBUILD_TTL_SENSITIVITY_BOUNDARY_SECONDS = 60

# --ttl-verdict's own per-root margin requirement: a direction's net savings
# must clear at least this fraction of that root's own dollar-equivalent
# volume to adopt -- a pre-registered decision threshold from the plan's
# Approach section, not a vendor-sourced rate.
_CACHE_REBUILD_TTL_MARGIN_FRACTION = 0.10

# --ttl-verdict's own per-root eligibility test: the dominant tier's share
# of that root's W5m + W1h must clear this fraction to count toward the
# bucket's verdict, else the root is excluded as a near-tie.
# Engineer-set (.claude/plans/cache-ttl-verdict-gate-fix.md's Approach
# section, no vendor grounding): set below observed incidental-fallback
# shares and above genuinely-mixed shares.
_CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN = 0.90

_TTL_VERDICT_ADOPT = "adopt"
_TTL_VERDICT_DECLINE = "decline"
_TTL_VERDICT_ROOTS_DISAGREE = "roots disagree"
_TTL_VERDICT_NO_VERDICT = "no verdict"

# --ttl-verdict's tier-direction discriminator, shared by root["favors"],
# _cache_rebuild_root_verdict_input's positive_favors/negative_favors, and
# the printed Tier/Favors table columns.
_CACHE_REBUILD_TIER_5M = "5m"
_CACHE_REBUILD_TIER_1H = "1h"

# --ttl-verdict's own per-root row labels: a root excluded from a bucket's
# verdict names why in the Clears column, instead of only being absorbed
# into that bucket's lumped exclusion count. Each token stays whitespace-
# free -- _extract_ttl_verdict_root_row maps columns by splitting the row
# on whitespace, so a space in any label would shift every column after it.
_TTL_EXCLUDE_NEAR_TIE = "excluded(near-tie)"
_TTL_EXCLUDE_NO_DATA = "excluded(no-data)"
_TTL_ROW_NOT_APPLICABLE = "--"
_TTL_TIER_NONE = "none"
_TTL_ROW_NA = "n/a"

_CAUSE_SESSION_START = "session start"
_CAUSE_IDLE_5M_1H = "idle 5m-1h"
_CAUSE_IDLE_OVER_1H = "idle >1h"
_CAUSE_MODEL_SWITCH = "model switch"
_CAUSE_UNEXPLAINED = "unexplained"
_CAUSE_TS_ANOMALY = "excluded (timestamp anomaly)"

# _cache_miss_reason's own vendor-emitted type value for a real model
# switch. Compared against the gap-derived idle-5m-1h cause as a
# cross-check, never fed back into any accumulator.
# Named as a constant, not a bare string, so it stays byte-identical to
# _classify_cache_rebuild_cause's own _CAUSE_MODEL_SWITCH trigger.
_CACHE_MISS_REASON_MODEL_CHANGED = "model_changed"

# Print order for the cause-breakdown table: the two TTL-explained idle
# buckets first, then the non-idle tail, then the excluded diagnostic bucket
# last -- a malformed/out-of-order timestamp pair gets its own explicit row
# here rather than silently falling into "unexplained" or an idle bucket.
_CACHE_REBUILD_CAUSES: tuple[str, ...] = (
    _CAUSE_SESSION_START, _CAUSE_IDLE_5M_1H, _CAUSE_IDLE_OVER_1H,
    _CAUSE_MODEL_SWITCH, _CAUSE_UNEXPLAINED, _CAUSE_TS_ANOMALY,
)

# Only these two causes are TTL-expiry rebuilds eligible for priced excess
# and cache_rebuild.py's concurrency split -- session start has no prior cache to
# have hit, and model switch/unexplained are not gap-driven.
_CACHE_REBUILD_IDLE_GAP_CAUSES: tuple[str, ...] = (_CAUSE_IDLE_5M_1H, _CAUSE_IDLE_OVER_1H)

# Origin labels for the main/subagent split -- classified per record via
# isSidechain, never by which source file (group) a record came from, so
# this reconciles against cache-efficiency's own sidechain row and survives
# a subagent-file layout change (see _cache_rebuild_report's docstring).
_CACHE_REBUILD_ORIGINS: tuple[str, ...] = ("main", "subagent")

_ATTR_OWN_BASH = "waiting on own Bash call"
_ATTR_BACKGROUND_TASK = "waiting on background task"
_ATTR_COORDINATOR = "waiting on coordinator message"
_ATTR_UNATTRIBUTED = "unattributed"

# Print order for the subagent idle-gap cause-attribution table -- see
# .claude/plans/subagent-idle-gap-cause-attribution.md's Approach section for
# each cause's lever (or lack of one).
_CACHE_REBUILD_ATTRIBUTIONS: tuple[str, ...] = (
    _ATTR_OWN_BASH, _ATTR_BACKGROUND_TASK, _ATTR_COORDINATOR, _ATTR_UNATTRIBUTED,
)

# Claude Code emits these marker literals, not this repo -- a harness release can change either one without notice.
_BACKGROUND_TASK_MARKER_PREFIX = "[SYSTEM NOTIFICATION - NOT USER INPUT]"
_COORDINATOR_MESSAGE_MARKER_PREFIX = "The coordinator sent a message while you were working"

_BASH_WAIT_SLEEP_POLL = "sleep-poll wait"
_BASH_WAIT_OTHER = "other Bash wait"
_BASH_WAIT_NO_COMMAND = "no command recorded"

# The own-Bash wait-shape table's printed row order follows this tuple's
# definition order.
_OWN_BASH_WAIT_SHAPES: tuple[str, ...] = (
    _BASH_WAIT_SLEEP_POLL, _BASH_WAIT_OTHER, _BASH_WAIT_NO_COMMAND,
)

# Matches `sleep <number>` at string start, after `;`/`&`/`|`/newline, or
# after `do`/`then`/`else` (word-boundary-guarded so it doesn't fire inside
# `sudo`/`docker`).
_SLEEP_POLL_COMMAND_RE = re.compile(r"(?:\A|[;&|\n]|\b(?:do|then|else))[ \t]*sleep[ \t]+[0-9]")


def _cache_rebuild_gap_seconds(prev_ts: float | None, cur_ts: float | None) -> float | None:
    """Seconds since the previous call in this transcript's own turn
    sequence, or None when either endpoint is unparseable or the delta is
    negative (clock skew) -- both must classify as a timestamp anomaly
    (_CAUSE_TS_ANOMALY) rather than a silently computed idle bucket."""
    if prev_ts is None or cur_ts is None:
        return None
    gap = cur_ts - prev_ts
    return gap if gap >= 0 else None


def _cache_rebuild_in_idle_5m_1h_band(
    is_first_call: bool, gap_seconds: float | None,
    *, idle_5m_boundary_seconds: float = _CACHE_REBUILD_IDLE_5M_SECONDS,
) -> bool:
    """Whether one call's own prior-call gap lands in
    [idle_5m_boundary_seconds, _CACHE_REBUILD_IDLE_1H_SECONDS) -- the gap
    test alone, with no cache-write-tier qualifier.

    Deliberately tier-blind: _classify_cache_rebuild_cause's write-tier
    qualifier answers a different question than --ttl-verdict's 1h-to-5m
    direction, which only needs the gap.
    """
    if is_first_call or gap_seconds is None:
        return False
    return idle_5m_boundary_seconds <= gap_seconds < _CACHE_REBUILD_IDLE_1H_SECONDS


def _classify_cache_rebuild_cause(
    is_first_call: bool, gap_seconds: float | None, model_changed: bool, pure_1h_tier_write: bool,
    *, idle_5m_boundary_seconds: float = _CACHE_REBUILD_IDLE_5M_SECONDS,
) -> str:
    """Classify one threshold-crossing cache-write call's cause.

    Idle buckets take priority over model switch -- the latter only applies
    inside the still-warm 5-minute window. pure_1h_tier_write is True when
    the call's cache-write tokens are entirely ephemeral_1h-tier (no
    ephemeral_5m) -- such a write can't have been forced by a <1h gap, since
    the 1h-TTL cache would still be warm, so it falls to "unexplained"
    instead of "idle 5m-1h". idle_5m_boundary_seconds overrides the idle
    band's lower bound and is forwarded to _cache_rebuild_in_idle_5m_1h_band.
    Production callers use the default, vendor-grounded boundary.
    """
    if is_first_call:
        return _CAUSE_SESSION_START
    if gap_seconds is None:
        return _CAUSE_TS_ANOMALY
    if gap_seconds >= _CACHE_REBUILD_IDLE_1H_SECONDS:
        return _CAUSE_IDLE_OVER_1H
    if _cache_rebuild_in_idle_5m_1h_band(
        is_first_call, gap_seconds, idle_5m_boundary_seconds=idle_5m_boundary_seconds
    ):
        return _CAUSE_UNEXPLAINED if pure_1h_tier_write else _CAUSE_IDLE_5M_1H
    if model_changed:
        return _CAUSE_MODEL_SWITCH
    return _CAUSE_UNEXPLAINED


def _classify_bash_wait_shape(command: str | None) -> str:
    """Sub-classify the winning Bash marker's own recorded command text.

    Textual, no shell parsing. A quoted or heredoc-embedded `sleep` counts
    as a match, over-counting sleep-poll waits for text that only mentions
    `sleep` without waiting on it. `sleep $VAR` (no literal leading digit)
    does not match, under-counting sleep-poll waits by missing a real one.
    """
    if not isinstance(command, str):
        return _BASH_WAIT_NO_COMMAND
    if _SLEEP_POLL_COMMAND_RE.search(command):
        return _BASH_WAIT_SLEEP_POLL
    return _BASH_WAIT_OTHER


def _attribute_idle_gap_cause(
    prior_turn: dict, window: list[dict], *, gap_start_ts: float, gap_seconds: float
) -> tuple[str, float | None, str | None]:
    """Sub-classify one subagent-origin idle-gap candidate by the last
    marker record found in `window` (the records strictly between
    `prior_turn` and the rebuild call that closed the gap).

    Last marker wins, scanning `window` forward: the question this answers
    is what released the subagent, and the last marker before the
    gap-closing call is by construction the one nearest that release.

    Each leg is self-scoped rather than filtered by origin:

    - Bash leg: matches only a `tool_use_id` `prior_turn` itself emitted
      via a Bash `tool_use` block. Ids are unique, so another origin's
      `tool_result` can never match.
    - Meta legs (background-task, coordinator): require both `isMeta` and
      `isSidechain` True on the record carrying them.

    Returns (cause, covered_share, bash_shape). The winning marker's own
    cause always wins, even when that marker's timestamp is missing or
    unparseable -- treating a bad timestamp as "no marker at all" would let
    precedence silently fall back to an earlier, unrelated marker's cause
    instead of disclosing the gap via `covered_share`. covered_share is
    (marker_ts - gap_start_ts) / gap_seconds when the winning marker's own
    timestamp parses, None for _ATTR_UNATTRIBUTED or for an attributed
    cause whose winning marker has no parseable timestamp. Not clamped to
    [0, 1] -- a stray clock-skew marker timestamp outside the gap window
    still yields a finite share rather than a silently clamped one.
    bash_shape is `_classify_bash_wait_shape`'s label for the winning
    marker's own recorded command when cause is _ATTR_OWN_BASH, None
    otherwise.
    """
    bash_tool_use_ids: dict[str, str | None] = {}
    for block in (prior_turn.get("message") or {}).get("content") or []:
        if not (isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash"):
            continue
        command = (block.get("input") or {}).get("command")
        bash_tool_use_ids[block.get("id")] = command if isinstance(command, str) else None

    last_cause: str | None = None
    last_marker_ts: float | None = None
    last_command: str | None = None
    for rec in window:
        content = (rec.get("message") or {}).get("content")
        marker_cause: str | None = None
        marker_command: str | None = None
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in bash_tool_use_ids
                ):
                    marker_cause = _ATTR_OWN_BASH
                    marker_command = bash_tool_use_ids[block.get("tool_use_id")]
        elif isinstance(content, str) and rec.get("isMeta") and rec.get("isSidechain"):
            if content.startswith(_BACKGROUND_TASK_MARKER_PREFIX):
                marker_cause = _ATTR_BACKGROUND_TASK
            elif content.startswith(_COORDINATOR_MESSAGE_MARKER_PREFIX):
                marker_cause = _ATTR_COORDINATOR
        if marker_cause is None:
            continue
        last_cause, last_marker_ts, last_command = marker_cause, corpus._parse_ts(rec.get("timestamp")), marker_command

    if last_cause is None:
        return _ATTR_UNATTRIBUTED, None, None
    bash_shape = _classify_bash_wait_shape(last_command) if last_cause == _ATTR_OWN_BASH else None
    if last_marker_ts is None:
        return last_cause, None, bash_shape
    return last_cause, (last_marker_ts - gap_start_ts) / gap_seconds, bash_shape


def _cache_rebuild_excess_dollars(model: str, usage: dict) -> tuple[float | None, int]:
    """Priced excess for one idle-gap rebuild call: the dollar delta between
    what its cache-write tokens were actually billed and what the same
    token count would have cost at the cache-read rate (a warm hit).

    Returns (excess_dollars, unpriced_tokens): excess_dollars is None, and
    unpriced_tokens carries the turn's total token count, when the model has
    no _MODEL_BASE_INPUT_RATES entry -- matching _price_turn's own
    unpriced-model contract.
    """
    dollars_by_class, _context_at_turn, unpriced_tokens = pricing._price_turn(model, usage)
    if dollars_by_class is None:
        return None, unpriced_tokens
    write_dollars = dollars_by_class["cache_write_1h"] + dollars_by_class["cache_write_5m"]
    eph_1h, eph_5m = pricing._cache_write_split(usage)
    rates = pricing._model_rates(model)
    warm_read_dollars = (eph_1h + eph_5m) / 1_000_000 * rates["cache_read"]
    # Mirrors _price_turn's own fast/geo multiplier application so the
    # counterfactual warm read is priced under the same settled infra
    # conditions as the actual write.
    if usage.get("speed") == "fast":
        warm_read_dollars *= pricing._FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        warm_read_dollars *= pricing._INFERENCE_GEO_US_RATE_MULTIPLIER
    return write_dollars - warm_read_dollars, 0


def _cache_rebuild_switch_delta_dollars(
    model: str, usage: dict, *, is_idle_5m_1h_cause: bool
) -> tuple[float | None, int]:
    """One call's signed contribution to the pooled 5m-to-1h cacheTtl switch
    delta -- see .claude/plans/subagent-idle-gap-cache-rebuild-split.md's
    Approach section for the derivation. (2 - r) must be resolved per call
    via _model_rates, never hardcoded as 1.9, since a corpus mixing model
    rates needs the true per-call coefficient. Positive is
    switch-cost-positive; the report negates the accumulated sum before
    printing it as a savings-positive net. Returns (None, unpriced_tokens)
    for a model absent from _MODEL_BASE_INPUT_RATES, matching _price_turn's
    own contract.
    """
    dollars_by_class, _context_at_turn, unpriced_tokens = pricing._price_turn(model, usage)
    if dollars_by_class is None:
        return None, unpriced_tokens
    rates = pricing._model_rates(model)
    _eph_1h, eph_5m = pricing._cache_write_split(usage)
    switch_cost_per_token = rates["cache_write_1h"] - rates["cache_write_5m"]
    delta_dollars = eph_5m / 1_000_000 * switch_cost_per_token
    if is_idle_5m_1h_cause:
        rescue_per_token = rates["cache_write_1h"] - rates["cache_read"]
        delta_dollars -= eph_5m / 1_000_000 * rescue_per_token
    # Mirrors _price_turn's own fast/geo multiplier application -- neither
    # leg above goes through _price_turn's own dollars_by_class, so both
    # need the same multiplier applied here instead.
    if usage.get("speed") == "fast":
        delta_dollars *= pricing._FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        delta_dollars *= pricing._INFERENCE_GEO_US_RATE_MULTIPLIER
    return delta_dollars, 0


def _cache_rebuild_1h_to_5m_delta_dollars(
    model: str, usage: dict, *, is_idle_5m_1h_cause: bool
) -> tuple[float | None, int]:
    """One call's signed contribution to a per-root 1h-to-5m cacheTtl switch
    delta -- the algebraic mirror of _cache_rebuild_switch_delta_dollars for
    the opposite direction (see .claude/plans/cache-ttl-tuning-analysis.md's
    Approach section for the derivation). The 5m/1h-write rate difference
    must be resolved per call via _model_rates, never hardcoded, for the
    same mixed-rate-corpus reason as the sibling. Positive is
    switch-cost-positive, matching the sibling's own convention: the report
    negates the accumulated sum before printing it as a savings-positive
    net. is_idle_5m_1h_cause marks a call whose prior-call gap fell in
    [idle_5m_boundary, 3600) -- under a live 1h tier this call was served as
    a warm read, so its own cache_read_input_tokens are the plan's Z
    contribution, not eph_5m (near-zero by construction on the population
    this function is called for). Returns (None, unpriced_tokens) for a
    model absent from _MODEL_BASE_INPUT_RATES, matching _price_turn's own
    contract.
    """
    dollars_by_class, _context_at_turn, unpriced_tokens = pricing._price_turn(model, usage)
    if dollars_by_class is None:
        return None, unpriced_tokens
    rates = pricing._model_rates(model)
    eph_1h, _eph_5m = pricing._cache_write_split(usage)
    tier_savings_per_token = rates["cache_write_1h"] - rates["cache_write_5m"]
    delta_dollars = -(eph_1h / 1_000_000 * tier_savings_per_token)
    if is_idle_5m_1h_cause:
        read_tokens = int(usage.get("cache_read_input_tokens", 0))
        expiry_cost_per_token = rates["cache_write_5m"] - rates["cache_read"]
        delta_dollars += read_tokens / 1_000_000 * expiry_cost_per_token
    # Mirrors _price_turn's own fast/geo multiplier application -- neither
    # leg above goes through _price_turn's own dollars_by_class, so both
    # need the same multiplier applied here instead.
    if usage.get("speed") == "fast":
        delta_dollars *= pricing._FAST_MODE_RATE_MULTIPLIER
    if usage.get("inference_geo") == "us":
        delta_dollars *= pricing._INFERENCE_GEO_US_RATE_MULTIPLIER
    return delta_dollars, 0


def _cache_rebuild_margin_clears(net_dollars: float, dollar_volume: float) -> bool:
    """Whether a direction's net savings clears --ttl-verdict's own
    _CACHE_REBUILD_TTL_MARGIN_FRACTION of that root's own dollar-equivalent
    volume -- shared by both the 5m-to-1h and 1h-to-5m per-root checks
    (Approach section), since the fraction and comparison are identical;
    only the two inputs' own derivation differs per direction. A
    non-positive volume never clears, rather than dividing by zero or by a
    negative number.
    """
    if dollar_volume <= 0:
        return False
    return net_dollars / dollar_volume >= _CACHE_REBUILD_TTL_MARGIN_FRACTION


def _cache_rebuild_token_tiebreaker_favors_5m(z: int, w1h: int) -> bool | None:
    """Raw-token, zero-price tiebreaker for a 1h-tier root (the vendor
    publishes nothing on how cache writes weigh against subscription rate
    limits, which matters only for a root currently paying the 1h tier):
    True favors dropping to 5m (Z < W1h), False disfavors it (Z > W1h),
    None when Z == W1h -- a wash counts as a disagreement, never a
    favorable tie, so a root whose dollar accounting disagrees with this
    sign, or whose Z and W1h are exactly equal, declines regardless of
    its own dollar margin.
    """
    if z == w1h:
        return None
    return z < w1h


def _cache_rebuild_dominant_tier_share(w5m: float, w1h: float) -> float:
    """Share of a root's own W5m + W1h volume held by its dominant tier --
    the value --ttl-verdict's per-root eligibility test
    (_CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN) compares against. Callers
    must exclude the w5m == w1h == 0 (no-data) case before calling this,
    since that ratio is undefined.
    """
    return max(w5m, w1h) / (w5m + w1h)


def _cache_rebuild_root_is_dominant(share: float) -> bool:
    """Whether a root's dominant-tier share clears
    _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN -- the boolean the print
    loop's own eligibility branch decides on."""
    return share >= _CACHE_REBUILD_TTL_DOMINANT_TIER_SHARE_MIN


def _cache_rebuild_root_verdict_input(
    *, net_primary: float, net_sensitivity: float, volume: float,
    positive_favors: str, negative_favors: str,
    apply_tiebreaker: bool, tiebreaker_favors_5m: bool | None = None,
) -> dict[str, object]:
    """Reduce one consistent root's own net-dollar figures (at both
    sensitivity boundaries) and dollar-equivalent volume into the
    {"favors", "clears"} shape _cache_rebuild_ttl_verdict consumes.
    positive_favors/negative_favors name the direction net_primary's own
    sign resolves to -- "1h"/"5m" for a 5m-tier root, "5m"/"1h" for a
    1h-tier root, since the two directions' savings-positive sign points
    opposite ways. apply_tiebreaker is False for a 5m-tier root, so clears
    is the dollar margin alone. W1h is always 0 for a 5m-tier root by
    construction -- that's what "consistent 5m" means. A raw-token
    comparison against it would therefore be degenerate rather than a
    real tiebreaker. For a 1h-tier root, apply_tiebreaker is True and
    tiebreaker_favors_5m must agree with the dollar accounting's own sign
    -- a None (Z == W1h wash) result never agrees, so clears is forced
    False regardless of margin.
    """
    favors = positive_favors if net_primary > 0 else negative_favors
    margin_ok = (
        _cache_rebuild_margin_clears(net_primary, volume)
        and _cache_rebuild_margin_clears(net_sensitivity, volume)
    )
    if not apply_tiebreaker:
        return {"favors": favors, "clears": margin_ok}
    tiebreaker_agrees = (
        tiebreaker_favors_5m is not None and tiebreaker_favors_5m == (favors == _CACHE_REBUILD_TIER_5M)
    )
    clears = margin_ok and tiebreaker_agrees
    return {"favors": favors, "clears": clears}


def _cache_rebuild_tier_split_agreement(
    net_5m_slice: float, net_1h_slice: float
) -> tuple[str, str, str]:
    """Resolve a mixed root's two slices to their own favored tier, via the
    same savings-positive sign rule _cache_rebuild_root_verdict_input
    applies, then compare the two labels for agreement. Returns
    (favors_5m_slice, favors_1h_slice, agreement) for the two-slice
    cross-check's tier-split print line. Informative only: the result never
    feeds root_inputs, the verdict, or any count.
    """
    favors_5m_slice = _CACHE_REBUILD_TIER_1H if net_5m_slice > 0 else _CACHE_REBUILD_TIER_5M
    favors_1h_slice = _CACHE_REBUILD_TIER_5M if net_1h_slice > 0 else _CACHE_REBUILD_TIER_1H
    agreement = "agree" if favors_5m_slice == favors_1h_slice else "disagree"
    return favors_5m_slice, favors_1h_slice, agreement


def _cache_rebuild_ttl_verdict(root_inputs: Sequence[dict[str, object]]) -> str:
    """Reduce one bucket's own consistent-root inputs to the plan's four-way
    verdict (Approach section's "ship rule"). Roots-disagree is checked
    before clears, so a direction conflict wins even when every root's own
    margin happens to clear.
    """
    if not root_inputs:
        return _TTL_VERDICT_NO_VERDICT
    favored_directions = {root["favors"] for root in root_inputs}
    if len(favored_directions) > 1:
        return _TTL_VERDICT_ROOTS_DISAGREE
    if all(root["clears"] for root in root_inputs):
        return _TTL_VERDICT_ADOPT
    return _TTL_VERDICT_DECLINE


def _negate_switch_delta_for_display(accumulated_delta: float) -> float:
    """Savings-positive negation of an accumulated switch-delta sum, cents-
    rounded. Two per-call contributions that cancel exactly at the rational
    level (a group's own W5m/X sitting exactly at the break-even ratio) can
    leave a +-1e-16 residual after floating-point summation; left
    un-rounded, its sign bit would print as the misleading "-0.00" instead
    of "0.00" once negated."""
    return round(0.0 - accumulated_delta, 2) + 0.0


