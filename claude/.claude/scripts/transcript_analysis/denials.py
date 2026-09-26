"""Hook-denial detection and classification: hook_denial_key, the
label/cause/command-shape classifiers, and toolDenialKind friction
classification -- no dependency on any cmd_* subcommand.

Imports corpus and render by module (attribute access, not by name) --
see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import json
import os
import re
import shlex

from transcript_analysis import corpus, render

# Shared bound for every hook-name/label capture below (detection and
# extraction alike): a name-shaped character class (word chars, spaces, '.',
# '-') capped at this many characters, matching every hook's own static
# "<name> hook/gate" wording — never an unbounded `.+?`, which would echo
# arbitrary denial-message text (a dynamic file path, say) into
# --deny-summary's output if a future hook ever interpolated one into this
# span.
_DENIAL_HOOK_NAME_MAX_CHARS = 40

# Current-format transcripts record a hook denial as an is_error tool_result,
# distinguishable from an ordinary tool error only by the deny message text —
# hook_denial_key deliberately does not read the parent user record's
# toolDenialKind field, a separate friction-class axis classified by
# _is_nongate_friction_kind below. These patterns match the Claude Code
# hook-denial idiom ("Blocked by <hook>", "blocked by <X> gate", "… invocation
# denied", "<name> gate: …" / "<name> hook: …" — a hook stating its own label
# directly, e.g. "Skill length gate: ..."). Detection is therefore best-effort
# in both directions: an atypically worded hook denial is missed, and an
# ordinary tool error whose text happens to contain the idiom is a false
# positive. review-trace is a candidate locator, not an exact counter —
# callers treat denial counts as approximate. Legacy transcripts additionally
# carry an explicit
# hook_blocking_error attachment record, matched separately and exactly.
_HOOK_DENIAL_SIGNATURE = re.compile(
    r"blocked by .{0,80}?\b(?:hook|gate)\b"
    r"|invocation denied\b"
    rf"|[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}\s+(?:hook|gate):",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_blocking_error(raw) -> dict | str:
    """Normalize blockingError — may arrive as a dict or a JSON-stringified dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        if isinstance(parsed, dict):
            return parsed
        return raw
    return raw if raw is not None else {}


def hook_denial_key(item: dict) -> tuple[str, dict | str] | None:
    """Return (tool_use_id, extra) if `item` is a hook denial, else None.

    Detects both denial shapes: a legacy `attachment` record (`item["type"] ==
    "attachment"` with a nested `hook_blocking_error`), or a current-format
    `tool_result` block (`item["type"] == "tool_result"`) carrying `is_error`
    and text matching _HOOK_DENIAL_SIGNATURE. An empty string is a valid
    tool_use_id — a denial whose transcript recorded no tool_use_id; only
    None means "not a denial". `extra` is the already-fetched data this
    predicate needed internally to classify the item — the `attachment`
    dict for the legacy shape, the decoded content text for the tool_result
    shape — returned so callers building an event/message don't re-fetch or
    re-decode it. Callers own the seen-id dedup set and the event/message
    construction; this predicate only classifies.
    """
    item_type = item.get("type")
    if item_type == "attachment":
        att = item.get("attachment") or {}
        if att.get("type") != "hook_blocking_error":
            return None
        return att.get("toolUseID") or "", att
    if item_type == "tool_result":
        if not item.get("is_error"):
            return None
        message = render._content_text(item.get("content"))
        if not _HOOK_DENIAL_SIGNATURE.search(message):
            return None
        return item.get("tool_use_id") or "", message
    return None


# Extracts the hook/gate name from a denial message's own "blocked by <name>
# hook/gate" wording — every hook's emit_deny call already writes this shape
# (e.g. "Blocked by code-review gate: ..."), so the name is read off the
# denial text itself rather than an invented category label.
_DENIAL_HOOK_NAME_RE = re.compile(
    rf"blocked by (?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate)\b", re.IGNORECASE
)

# Two wordings hooks emit that the "blocked by <name> hook/gate" idiom above
# doesn't cover: enforce-marker-script-shape.sh's path-traversal and
# shape-mismatch denials name their own script directly ("marker.sh
# invocation denied ..."), and several hooks state their own label as the
# message's own prefix rather than via "blocked by" (e.g. check-skill-length.sh's
# "Skill length gate: ..."). Both inherit the same bounded character class and
# _DENIAL_HOOK_NAME_MAX_CHARS cap as the pattern above.
_DENIAL_HOOK_NAME_INVOCATION_DENIED_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+invocation denied\b", re.IGNORECASE
)
_DENIAL_HOOK_NAME_COLON_RE = re.compile(
    rf"(?P<name>[\w .-]{{1,{_DENIAL_HOOK_NAME_MAX_CHARS}}}?)\s+(?:hook|gate):", re.IGNORECASE
)

# The hand-maintained set of prose labels hooks/*.sh actually emits. Every
# gate hook declares its label once as DENY_GATE_LABEL, read by both the
# bootstrap emit_deny stub and _lib_emit_deny; this set mirrors that
# declaration. A captured name is trusted only if it's a member of this set;
# anything else falls to _DENY_SUMMARY_UNMATCHED_HOOK rather than being
# echoed verbatim. Examples of "anything else": a coincidental match, an
# unanticipated wording, an unbounded interpolated value that survived the
# character-class bound. Regression coverage: TestDenialHookLabelEnumeration
# in test_transcript_analysis.py drives each hook's real deny-path wording
# and asserts the label it produces is a member here, so a hook's wording
# change or a new hook shows up as a test failure rather than a silently
# stale set.
_DENIAL_HOOK_LABELS: frozenset[str] = frozenset({
    # "blocked by <name> hook/gate" — one entry per hooks/*.sh DENY_GATE_LABEL.
    "gh-pr-merge",  # block-gh-pr-merge.sh
    "CLAUDE.md length",  # check-claude-md-length.sh
    "skill length",  # check-skill-length.sh
    "credential-path Bash",  # deny-credential-bash-reads.sh
    "credential-file read",  # deny-credential-file-reads.sh
    "data-file read",  # deny-data-file-reads.sh
    "env-read",  # deny-env-reads.sh
    "backtick-escape",  # deny-escaped-backticks-in-pr-body.sh
    "network-install",  # deny-network-installs.sh
    "PII commit",  # deny-pii-in-commits.sh
    "redaction",  # deny-private-project-refs.sh
    "repo-relocation",  # deny-repo-relocation.sh
    "reviewer-tree-mutation",  # deny-reviewer-tree-mutation.sh
    "marker-script-shape",  # enforce-marker-script-shape.sh
    "settings session-keys",  # guard-settings-session-keys.sh
    "code-review",  # require-code-review.sh
    "memory-skill",  # require-memory-skill.sh
    "plan-review",  # require-plan-review.sh
    "ready-for-review",  # require-ready-for-review.sh
    "respond-pr",  # require-respond-pr.sh
    "routing-read",  # require-routing-read.sh
    "stow-reminder",  # require-stow-reminder.sh
    "worktree-enforcement",  # require-worktree-for-file-writes.sh, require-worktree-for-git-writes.sh
    "architect-consult",  # require-architect-consult.sh
    "invisible-commit-content",  # deny-invisible-commit-content.sh
    "no-op-dispatch",  # deny-no-op-dispatch.sh
    # Legacy-only: no active hook emits this wording. Each member is kept
    # permanently so an older recorded transcript still classifies.
    "marker.sh",  # enforce-marker-script-shape.sh's "<name> invocation denied" wording, kept for legacy transcripts
    "AGENTS.md length",  # check-claude-md-length.sh's "CLAUDE.md/AGENTS.md length gate:" wording, kept for legacy transcripts
    "Skill length",  # check-skill-length.sh's "Skill length gate:" wording, kept for legacy transcripts
    "ai-instruction-and-memory-files",  # require-memory-skill.sh's behavioral-deny wording, kept for legacy transcripts
    "plan-review routing",  # require-routing-read.sh's behavioral-deny wording, kept for legacy transcripts
})

# --deny-summary's unmatched-hook-name bucket: a denial matched by
# _HOOK_DENIAL_SIGNATURE (e.g. via the "invocation denied" alternative, which
# names no hook) but from which no enumerated hook/gate name can be extracted.
_DENY_SUMMARY_UNMATCHED_HOOK = "unmatched"

# --deny-summary's attempted-command-shape classifier: an allowlist, not a
# free-text sanitizer. A command failing to normalize into one of the
# multiplexer shapes below falls into "other".
_DENY_SUMMARY_OTHER_COMMAND_SHAPE = "other"

# Strips a leading NAME=VALUE environment-assignment prefix (one such prefix
# is observed in the corpus, wrapping a marker.sh invocation with a live
# per-machine token) before any other normalization runs, so that token never
# reaches printed output.
_DENIAL_COMMAND_ENV_PREFIX_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+")

# The multiplexer commands the corpus is dominated by — only these get a
# command+subcommand shape; every other command shape, including an empty
# command, falls to "other". "marker.sh" is matched post-basename, since the
# real invocation is always a tilde or absolute script path.
_DENIAL_COMMAND_MULTIPLEXERS: frozenset[str] = frozenset({"git", "gh", "marker.sh"})

# Per-multiplexer closed allowlist of real subcommands --deny-summary trusts
# in the printed "<multiplexer> <subcommand>" shape — same discipline as
# _DENIAL_HOOK_LABELS: a candidate subcommand token that isn't a member (a
# credential-shaped string, a path, an unenumerated wording) falls to "other"
# rather than being echoed verbatim. Sourced from the corpus's observed
# denied invocations plus _LIB_READONLY_GIT_SUBCMDS in hooks/_lib.sh for the
# git read-only entries; new entries are added deliberately, not accreted.
_DENIAL_COMMAND_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({
        "add", "checkout", "commit", "config", "diff", "fetch", "init", "log",
        "merge", "pull", "push", "restore", "rev-parse", "show", "status",
        "symbolic-ref",
    }),
    "gh": frozenset({"api", "auth", "issue", "pr"}),
    "marker.sh": frozenset({"activate", "clear-stale", "deactivate", "status", "write"}),
}

# Second layer beyond the allowlist above, matching _DENIAL_HOOK_NAME_MAX_CHARS's
# defense-in-depth pattern: bounds a future malformed allowlist entry rather
# than serving as the primary defense, which is allowlist membership itself.
_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS = 20
_DENIAL_COMMAND_SUBCOMMAND_RE = re.compile(rf"^[\w-]{{1,{_DENIAL_COMMAND_SUBCOMMAND_MAX_CHARS}}}$")

# git flags that take their value as the following token — dropping (not
# skipping) both the flag and its value keeps the value from being misread
# as the subcommand, e.g. "git -C <path> commit" would otherwise leave
# <path> at index 1 once "-C" alone is skipped, so a naive scan reads <path>
# as the subcommand instead of "commit" at index 2. -C is
# require-worktree-for-git-writes.sh's own resolution mechanism for a
# compliant worktree write, so it's the dominant separate-token form in the
# worktree-enforcement denial category.
_DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE: frozenset[str] = frozenset({"-C", "-c", "--git-dir", "--work-tree"})

# git flags whose value is glued to the flag by "=" — the value already
# lives inside this one token, so nothing further needs dropping.
_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES: tuple[str, ...] = ("--git-dir=", "--work-tree=")


def _drop_denial_command_flag_values(tokens: list[str]) -> list[str]:
    """Drop (not skip) the values of git's value-taking repo-selection flags.

    A separate-token flag (-C, -c, --git-dir, --work-tree) consumes itself
    and the token after it; an =-attached flag (--git-dir=<path>,
    --work-tree=<path>) consumes only itself, since its value is already
    inside that token. Skipping a flag without dropping its value would
    leave the value in place to be misread as the subcommand.
    """
    kept: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in _DENIAL_COMMAND_FLAGS_WITH_SEPARATE_VALUE:
            i += 2  # drop the flag token and its value token
            continue
        if token.startswith(_DENIAL_COMMAND_FLAG_VALUE_ATTACHED_PREFIXES):
            i += 1  # value is glued to this token; nothing further to drop
            continue
        kept.append(token)
        i += 1
    return kept

# Extraction patterns tried in order against a current-shape denial message;
# the first to yield a name in _DENIAL_HOOK_LABELS wins.
_DENIAL_HOOK_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    _DENIAL_HOOK_NAME_RE,
    _DENIAL_HOOK_NAME_INVOCATION_DENIED_RE,
    _DENIAL_HOOK_NAME_COLON_RE,
)


def _denial_hook_label(hook_name: str, message: str) -> str:
    """Return the originating hook/gate name for one denial event.

    Legacy-shape denials carry the name directly (hook_name, from the
    attachment record's hookName field); current-shape denials carry no
    structured hook identity, so the name is extracted from the denial
    message text via each pattern in _DENIAL_HOOK_NAME_PATTERNS in turn.
    Either source is trusted only if the candidate is a member of
    _DENIAL_HOOK_LABELS — an unenumerated hookName (legacy transcripts predate
    this bound entirely) or an unenumerated extracted candidate both fall to
    _DENY_SUMMARY_UNMATCHED_HOOK rather than being echoed verbatim.
    """
    candidate = (hook_name or "").strip()
    if candidate:
        return candidate if candidate in _DENIAL_HOOK_LABELS else _DENY_SUMMARY_UNMATCHED_HOOK
    for pattern in _DENIAL_HOOK_NAME_PATTERNS:
        m = pattern.search(message)
        if m is None:
            continue
        name = m.group("name").strip().removeprefix("the ")
        if name in _DENIAL_HOOK_LABELS:
            return name
    return _DENY_SUMMARY_UNMATCHED_HOOK


def _denial_cause_kind(message: str) -> str:
    """Return one denial's infra-failure family, or the behavioral fallback.

    Sibling of _denial_hook_label: same substring-cascade mechanism over the
    message text only, never given hook_name. The cause axis is orthogonal
    to the hook axis, so a denial carries exactly one value from each.
    Classification matches a body fragment rather than a full sentence,
    because the surrounding wording differs per hook. A hook that echoes
    agent-controlled command or path text into its deny body can produce a
    false infra classification, so counts are approximate in the same sense
    _HOOK_DENIAL_SIGNATURE already documents.
    """
    lowered = message.lower()
    for marker, kind in _DENIAL_CAUSE_MARKERS:
        if marker in lowered:
            return kind
    return _DENIAL_CAUSE_BEHAVIORAL


def _denial_command_shape(command: str) -> str:
    """Classify a denied Bash command's shape for --deny-summary.

    Normalizes before matching, in order: strips a leading NAME=VALUE
    environment assignment, basenames the first token (an absolute script
    path is a home-rooted path, one of this repo's six always-on structural
    redaction detectors), and drops the values of git's repo-selection flags
    (see _drop_denial_command_flag_values). Only a multiplexer command
    (_DENIAL_COMMAND_MULTIPLEXERS) gets a command+subcommand shape, and only
    when the candidate subcommand token doesn't itself look like a flag — an
    unenumerated flag (one _drop_denial_command_flag_values doesn't know
    about) is left in place rather than dropped, so this guards it from
    being read as, and printed as, the subcommand — and is itself a member of
    that multiplexer's _DENIAL_COMMAND_SUBCOMMANDS allowlist, so an
    unenumerated non-flag token (a credential-shaped string, a raw control
    byte) falls to "other" instead of being echoed verbatim. Anything else,
    including an empty command, falls to "other". Nothing past the
    subcommand token is ever printed, so an argument value — a commit
    message, a path, a control character — never survives to stdout.
    """
    stripped = _DENIAL_COMMAND_ENV_PREFIX_RE.sub("", command)
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        tokens = stripped.split()
    if not tokens:
        return _DENY_SUMMARY_OTHER_COMMAND_SHAPE
    tokens[0] = os.path.basename(tokens[0])
    tokens = _drop_denial_command_flag_values(tokens)
    if len(tokens) > 1 and tokens[0] in _DENIAL_COMMAND_MULTIPLEXERS and not tokens[1].startswith("-"):
        subcommand = tokens[1]
        allowed_subcommands = _DENIAL_COMMAND_SUBCOMMANDS.get(tokens[0], frozenset())
        if subcommand in allowed_subcommands and _DENIAL_COMMAND_SUBCOMMAND_RE.match(subcommand):
            return f"{tokens[0]} {subcommand}"
    return _DENY_SUMMARY_OTHER_COMMAND_SHAPE


# toolDenialKind's gate-axis value — a permission-layer denial (hook denial or
# allowlist miss), already covered by hook_denial_key's message-signature
# match. The four other values (user-rejected, automode-blocked,
# automode-unavailable, interrupted) are friction, not a gate denial.
_GATE_TOOL_DENIAL_KIND = "permission-rule"

# The date toolDenialKind first appears in the corpus (the corpus itself
# starts 2026-06-24), determined by a corpus scan rather than a documented
# Claude Code rollout date — a user/tool_result record timestamped before
# this date structurally cannot carry the field, so --deny-summary's
# friction-kind breakdown must not read a pre-regime record's absent kind as
# zero friction.
_TOOL_DENIAL_KIND_REGIME_START = "2026-07-20"
_TOOL_DENIAL_KIND_REGIME_START_TS = corpus._parse_ts(f"{_TOOL_DENIAL_KIND_REGIME_START}T00:00:00Z")


def _is_nongate_friction_kind(tool_denial_kind: str, already_gate_denied: bool) -> bool:
    """True if a user record's toolDenialKind marks non-gate friction.

    A falsy toolDenialKind means the field is absent from this record — not
    friction. already_gate_denied guards against double-classifying a block
    hook_denial_key already matched via the message-text signature, so a
    record can never produce both a denial event and a friction event.
    """
    if already_gate_denied or not tool_denial_kind:
        return False
    return tool_denial_kind != _GATE_TOOL_DENIAL_KIND


# --deny-summary's/review-trace's printed friction_kind vocabulary — closed,
# so a future harness-added toolDenialKind value prints as _FRICTION_KIND_OTHER
# rather than echoing the raw field verbatim.
_FRICTION_KINDS: frozenset[str] = frozenset({
    "user-rejected",
    "automode-blocked",
    "automode-unavailable",
    "interrupted",
})
_FRICTION_KIND_OTHER = "other-kind"


def _friction_kind_label(tool_denial_kind: str) -> str:
    """Map a friction event's toolDenialKind to its printed label."""
    return tool_denial_kind if tool_denial_kind in _FRICTION_KINDS else _FRICTION_KIND_OTHER


# --deny-summary's/review-trace's denial-cause vocabulary — closed, the same
# shape as _FRICTION_KINDS above. Its tuple order fixes _print_deny_summary's
# printed column order for the hook/gate x cause table.
_DENIAL_CAUSE_BEHAVIORAL = "behavioral"
_DENIAL_CAUSE_KINDS: tuple[str, ...] = (
    _DENIAL_CAUSE_BEHAVIORAL, "lib-source", "input-parse", "helper-proc", "deny-encode",
)

# Ordered (marker, kind) cascade tried against the message in turn; the
# first match wins. deny-encode is checked first because a jq outage also
# fails the input parse and would otherwise be reported as the wrong cause.
_DENIAL_CAUSE_MARKERS: tuple[tuple[str, str], ...] = (
    ("could not encode its deny reason", "deny-encode"),
    ("could not source _lib.sh", "lib-source"),
    ("could not parse tool-input json", "input-parse"),
    ("failing closed", "helper-proc"),
)

