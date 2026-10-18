"""The skill-invocation command: cmd_skill_invocation's per-skill top-level,
routed, and /slash invocation tally, the _normalize_skill_name display-label
helper, and its subparser registration.

Imports corpus, render, review_rounds, and scope by module (attribute access,
not by name) -- see scope.py's own top-of-file comment for why.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from transcript_analysis import corpus, render, review_rounds, scope


def _normalize_skill_name(raw: str) -> str:
    """Strip a directory qualifier from a transcript skill name.

    input["skill"] is a display label, not a stable identifier. Claude Code
    qualifies project-scoped skills by the directory they were found in, so the
    same skill is recorded under several spellings depending on the invoking
    session's working directory:

        plan-it
        claude:plan-it
        .claude/worktrees/some-branch/claude:plan-it
        claude/.claude/worktrees/some-branch/nested/claude:plan-it

    Collapsing these spellings stops one skill from splitting across several rows.
    This is row-hygiene, not the security control: within a repo-scoped read the
    only path fragment that can appear is *this* repo's own worktree branch (its
    project dirs are the only ones read), which is public. Cross-project
    minimization is enforced upstream by _repo_scoped_project_slugs, not here.

    A remaining ``plugin:skill`` or ``dir:skill`` prefix is deliberately kept: it
    carries no path, and dropping it would need this script to hardcode the stow
    package name, which varies per installation. Resolving such a prefix to a
    skill body is the reviewer agent's job, not the extractor's.
    """
    return raw.rsplit("/", 1)[-1]


def cmd_skill_invocation(args: argparse.Namespace) -> None:
    """Per-skill invocation-source tally across the full corpus.

    Three invocation buckets are counted per skill:
    - top-level: Skill tool_use on a main-thread assistant turn with no attributionSkill.
    - routed: Skill tool_use on a main-thread assistant turn where attributionSkill is
      non-empty (the call was fired while another skill's body was active).
    - user-slash: user record whose message content contains a
      <command-name>/skillname</command-name> tag (the /slash invocation path, which
      injects the skill body directly without a Skill tool_use).

    Identifies routed-only candidates (zero top-level or slash) and slash-only candidates
    (zero top-level or routed) for skill-description budget analysis.

    Two consumers ask different questions of this data, so subagent turns are opt-in:

    - Skill-description budget analysis asks whether a skill's *description* draws
      auto-triggers on the main thread. Sidechain turns are noise there, so they are
      excluded by default.
    - Procedural-fidelity review asks which procedures a branch's work committed to.
      A skill invoked inside a spawned agent binds exactly as much as a main-thread
      one, so --include-subagents folds those in and adds a thread column keeping the
      two distinguishable rather than silently merged.

    --branches scopes to named branches, matching each record's attributed branch
    (scope._attributed_branch): a subagent record counts under the branch its
    dispatching main thread was on, never its own gitBranch. --projects scopes to
    project dirs and defaults to every project on the machine, which is rarely what
    a branch-scoped caller wants (branch names are not unique across repos).
    """
    branch_filter = scope._branch_filter(args)
    include_subagents = bool(getattr(args, "include_subagents", False))

    # OUTPUT INVARIANT — provenance, not shape. This output is routinely quoted
    # into public PR descriptions. Its safety rests on WHAT records are read, not
    # on scrubbing names after the fact: skill names are user-defined strings and
    # can themselves be private-project identifiers (a plugin namespace with no
    # path separator at all). So the control is to scope the read to this repo's
    # own project dirs (_repo_scoped_project_slugs) — the default when --projects
    # is unset. An explicit --projects is an escape hatch for corpus analysis;
    # the caller then owns that the output is no longer publish-safe.
    #
    # Skill names from --include-subagents can come from a cross-repo subagent,
    # so treat that output as local-only (docs/transcript-analysis.md, Limits).
    #
    # Supporting rules: only input["skill"] is extracted (never input["args"],
    # which holds absolute paths even for in-scope sessions); and
    # _normalize_skill_name collapses this repo's own worktree-qualified spellings
    # for row-hygiene. Neither is the security boundary — scoping is.
    roots = scope.resolve_scan_roots(args)

    projects_arg = getattr(args, "projects", None)
    if projects_arg:
        if len(roots) > 1:
            # Same one-level restriction as _resolve_project_scope's own
            # multi-root branch; see _single_level_projects_glob's docstring
            # for why.
            try:
                projects_arg = scope._single_level_projects_glob(projects_arg)
            except argparse.ArgumentTypeError as exc:
                print(f"skill-invocation: --projects: {exc}", file=sys.stderr)
                sys.exit(2)
            session_iter = scope._iter_glob_scoped_sessions(roots, projects_arg, include_subagents)
        else:
            session_iter = corpus.iter_sessions(roots[0], projects_arg, include_subagents=include_subagents)
    else:
        session_iter = scope._iter_scoped_sessions(scope._repo_scoped_project_slugs(), include_subagents, roots=roots)

    # Counters are keyed by (skill, thread). Without --include-subagents every
    # thread is "main", which keeps the default output shape unchanged.
    skill_top: dict[tuple[str, str], int] = defaultdict(int)     # -> top-level count
    skill_routed: dict[tuple[str, str], int] = defaultdict(int)  # -> routed count
    skill_slash: dict[tuple[str, str], int] = defaultdict(int)   # -> user-slash count
    routed_pairs: dict[tuple[str, str], int] = defaultdict(int)  # (parent, child) -> count

    for _jsonl, records in session_iter:
        # Unfiltered runs never read a branch, so the context is built only for a filter.
        branch_context = scope._session_branch_context(records) if branch_filter else None
        for rec in records:
            sidechain = bool(rec.get("isSidechain"))
            if sidechain and not include_subagents:
                continue
            # Unfiltered runs must still count records that carry no gitBranch,
            # so the branch test applies only when a filter was requested.
            if branch_context is not None and (scope._attributed_branch(rec, branch_context) or "") not in branch_filter:
                continue
            thread = "sidechain" if sidechain else "main"
            rtype = rec.get("type")
            if rtype == "assistant":
                for block in ((rec.get("message") or {}).get("content") or []):
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Skill":
                        continue
                    skill = _normalize_skill_name((block.get("input") or {}).get("skill") or "")
                    if not skill:
                        continue
                    attribution = _normalize_skill_name(rec.get("attributionSkill") or "")
                    if attribution:
                        skill_routed[(skill, thread)] += 1
                        routed_pairs[(attribution, skill)] += 1
                    else:
                        skill_top[(skill, thread)] += 1
            elif rtype == "user":
                content_raw = (rec.get("message") or {}).get("content", "")
                content_str = content_raw if isinstance(content_raw, str) else render._content_text(content_raw)
                for m in review_rounds._SLASH_COMMAND_RE.finditer(content_str):
                    skill_slash[(_normalize_skill_name(m.group(1)), thread)] += 1

    keyed = set(skill_top) | set(skill_routed) | set(skill_slash)
    all_skills: set[str] = {skill for skill, _thread in keyed}

    scope_parts = [
        "explicit --projects (not repo-scoped)" if projects_arg else "this repo",
        "main+subagents" if include_subagents else "main thread",
    ]
    if branch_filter:
        scope_parts.append(f"branches: {','.join(sorted(branch_filter))}")
    # Printed above the zero-match return, not after it: "found nothing" is only
    # interpretable alongside the corpus that was searched.
    scope.print_resolved_scope("skill-invocation", "; ".join(scope_parts), roots)

    if not all_skills:
        print("No skill invocations found.")
        return

    def _thread_total(s: str, thread: str) -> int:
        return skill_top[(s, thread)] + skill_routed[(s, thread)] + skill_slash[(s, thread)]

    # Sort by total descending, then alphabetically for ties.
    def _skill_total(s: str) -> int:
        return sum(_thread_total(s, thread) for thread in ("main", "sidechain"))

    sorted_skills = sorted(all_skills, key=lambda s: (-_skill_total(s), s))

    if include_subagents:
        header = (
            f"{'skill':<40} {'thread':<10} {'top-level':>10}  "
            f"{'routed':>6}  {'user-slash':>10}  {'total':>7}"
        )
    else:
        header = f"{'skill':<40} {'top-level':>10}  {'routed':>6}  {'user-slash':>10}  {'total':>7}"
    print(header)
    print("-" * len(header))

    for skill in sorted_skills:
        # The skill label repeats on every row rather than blanking on
        # continuation rows: consumers grep individual lines out of this table,
        # and a blanked label makes a grepped line unattributable.
        threads = ("main", "sidechain") if include_subagents else ("main",)
        for thread in threads:
            if include_subagents and not _thread_total(skill, thread):
                continue
            top = skill_top[(skill, thread)]
            routed = skill_routed[(skill, thread)]
            slash = skill_slash[(skill, thread)]
            total = top + routed + slash
            if include_subagents:
                print(
                    f"{skill:<40} {thread:<10} {top:>10}  "
                    f"{routed:>6}  {slash:>10}  {total:>7}"
                )
            else:
                print(f"{skill:<40} {top:>10}  {routed:>6}  {slash:>10}  {total:>7}")

    if routed_pairs:
        print("\nROUTED PAIRS (parent -> child : count)")
        for (parent, child), count in sorted(routed_pairs.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {parent} -> {child} : {count}")

    # Classification summary: load-bearing, routed-only, slash-only. Counts
    # aggregate across threads — the classification answers a per-skill
    # question ("is this description load-bearing?"), not a per-thread one.
    def _agg(counter: dict[tuple[str, str], int], s: str) -> int:
        return sum(counter[(s, thread)] for thread in ("main", "sidechain"))

    load_bearing = [s for s in sorted_skills if _agg(skill_top, s) > 0 or _agg(skill_slash, s) > 0]
    routed_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_slash, s) == 0 and _agg(skill_routed, s) > 0
    ]
    slash_only = [
        s for s in sorted_skills
        if _agg(skill_top, s) == 0 and _agg(skill_routed, s) == 0 and _agg(skill_slash, s) > 0
    ]

    print("\nCLASSIFICATION SUMMARY")
    print("  Load-bearing (any top-level or slash invocations):")
    if load_bearing:
        for s in load_bearing:
            print(f"    {s} ({_agg(skill_top, s)} top, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")

    print("  Routed-only candidates (zero top-level and zero slash — name-only eligible):")
    if routed_only:
        for s in routed_only:
            print(f"    {s} (0 top, {_agg(skill_routed, s)} routed, 0 slash)")
    else:
        print("    (none)")

    print("  Slash-only candidates (zero top, zero routed — disable-model-invocation eligible):")
    if slash_only:
        for s in slash_only:
            print(f"    {s} (0 top, 0 routed, {_agg(skill_slash, s)} slash)")
    else:
        print("    (none)")


def _add_skill_invocation_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the skill-invocation subparser on build_parser()'s subparsers action,
    binding cmd_skill_invocation through set_defaults(func=...).
    """
    p_skill_inv = subparsers.add_parser(
        "skill-invocation",
        help=(
            "Per-skill invocation-source tally: top-level (description-dependent auto-trigger),"
            " routed (fired while another skill's body was active), and user /slash commands."
            " Identifies name-only and disable-model-invocation candidates for budget relief."
        ),
    )
    p_skill_inv_scope = p_skill_inv.add_mutually_exclusive_group()
    p_skill_inv_scope.add_argument(
        "--projects", default=None, metavar="GLOB",
        help="Project-dir glob. Default: this repo's own worktrees only (publish-safe without --include-subagents). "
             "Passing an explicit glob is an escape hatch — output is then not scoped to this repo.",
    )
    p_skill_inv_scope.add_argument(
        "--this-repo", action="store_true",
        help=(
            "Explicit no-op: skill-invocation already defaults to this repo's own "
            "worktrees. Kept for flag uniformity with every other --projects subcommand."
        ),
    )
    p_skill_inv.add_argument("--branches", metavar="B1,B2,...", help="Branch name filter (default: all)")
    p_skill_inv.add_argument(
        "--include-subagents", action="store_true",
        help="Count skill invocations inside spawned subagents too, split by a thread column. "
             "Output can carry another project's skill names (docs/transcript-analysis.md, Limits).",
    )
    p_skill_inv.set_defaults(func=cmd_skill_invocation)
