# Plan-it: templatizing the ticket-workflow entry point across downstream Claude Code setups

## Context

Decide how to reuse the ticket-assignment-and-planning workflow (fetch a
ticket, claim it, run `/plan-it`, post the plan back as a comment) across
private downstream repos instead of hand-writing a bespoke `plan-issue` skill
per project. This matters now because a third project is being onboarded and
its ticket tracker (Atlassian/Jira) is structurally different from the
Linear-based trackers both existing projects use, and the two existing
`plan-issue` implementations have already drifted from each other (one
claims the ticket before planning, the other doesn't) and duplicate a
near-identical `linear-formatting` skill almost verbatim. The intended
outcome is an architecture decision — generic templatized skill,
private-repo scaffolding playbook, or a hybrid — plus enough initial
implementation to act on it, so the third project's setup doesn't repeat the
ad hoc duplication of the first two.

### Evidence gathered

**The inner customization axis is already solved and needs no new design.**
`/plan-it` Step 2.5, `/code-review`'s equivalent, and `skillOverrides:
name-only` already let a project layer on design-doc conventions, tier-gating
checks, or reliability rules without touching the base skill. Both existing
projects use this successfully: one has project layers for `plan-it`,
`plan-review`, `code-review`, and `test-conventions`; the other has one for
`code-review` only (it hasn't needed the others yet, and runs base
`/plan-it`/`/plan-review` unmodified).

**The outer ticket-workflow entry point is not templatized, and the two
existing copies have already diverged.** Both projects have a `plan-issue`
skill that fetches a ticket, invokes `/plan-it`, and posts the plan back as
a comment. Only one of the two claims the ticket first (checks
assignee/status, claims if eligible, bumps status, flags anomalies instead
of touching someone else's or a closed ticket). The other skips claiming
entirely — confirmed with the engineer to be an oversight, not a deliberate
per-project choice.

**A near-identical support skill is duplicated almost verbatim.** Both
projects' `linear-formatting` skill (issue-link formatting conventions for
tracker writes) differ only in a handful of tokens: the tracker MCP server's
tool-name prefix and the issue-ID prefix baked into examples/regex. This is
a value-level difference, not a structural one — the underlying tracker
(Linear) is the same shape for both.

**The third project breaks the value-level-only assumption.** The third
project's tracker is Atlassian/Jira, not Linear. Jira's assignee/status/
comment model and MCP surface differ structurally from Linear's — Jira
workflow/status schemes are commonly defined per-project rather than as one
global vocabulary — so the swap that worked between the two Linear-based
downstream repos (tool-name prefix + ID-prefix substitution) does not
obviously extend to a third, differently-shaped tracker.

**claude-config already has a precedent for generic-but-narrow-audience
content.** `plugins/lovable-cloud/` is a public, generic plugin scoped to
per-project install (not loaded in every session) — CLAUDE.md's own
guidance is that skills applying to "one or a few private projects — not
broadly to all sessions" live under `plugins/<name>/`, not in
`claude/.claude/skills/`.

**The private consulting playbook repo has no onboarding artifact for this
yet.** It holds `methodology/`, `tools/`, `templates/`, and `playbooks/`
directories for reusable consulting process and scripts, but nothing today
walks through standing up a new project's ticket-workflow skill — this would
be new content there under any option that uses it.

### Engineer's decisions (this session)

- Claiming the ticket (assignee + status transition) becomes the **default**
  behavior of the shared shape, not an optional per-project behavior.
  [engineer-verified]
- The third project's tracker is Atlassian/Jira, confirmed structurally
  different from Linear. [engineer-verified]
- Where the generic piece lives — a public claude-config plugin, a private
  cs-playbook scaffold, or a hybrid — is explicitly left to the architecture
  design step, informed by the tracker-diversity finding above.
  [engineer-verified: defers this call]

## Approach

Split the problem by what actually varies, and stop treating `plan-issue` as one templatizable artifact. The tracker-*invariant* part is procedure, not code — it becomes a template plus an onboarding playbook in the private consulting-playbook repo that mints a complete, self-contained `plan-issue` skill per project. The tracker-*specific but project-invariant* part is the Linear comment-formatting conventions — that becomes a public `plugins/linear-formatting/` plugin in claude-config that both Linear-based downstream repos install, deleting their two duplicated copies. Claude-config's only other change is one sentence recording why a `plan-issue-*` project-layer glob was the wrong reach, so a future onboarding doesn't re-derive it.

**Why not a public `plan-issue` base skill plus a `plan-issue-*` project-layer glob** (the option that most naturally extends the proven `plan-it-*`/`code-review-*` convention). Every one of the five base skills carrying that glob treats the layer as an *additive refinement*: the base performs a complete review or a complete plan on its own, and the layer adds project-specific checks to that same flow. All five explicitly "proceed without a layer" when none matches (`docs/skills.md:182`). A `plan-issue` base has no such standalone flow — fetch, claim, and post are each a tracker tool call the base cannot name, so a layerless run does nothing at all, and the "zero matches → proceed" degradation is incoherent. Worse, the Jira project does not want a *refinement* of the Linear flow; it wants a different mechanism (a workflow transition rather than a settable status field), which is a substitutive technique, not an additive one. Layering a substitution onto a base that cannot run alone produces a shared artifact functional only when wired to a specific private system.

**Why not extend `/plan-it` itself with a ticket-ingress step** (the lightest option of all — zero new skills, reusing the Step 2.5 glob and layer files both projects already have). This resolves against itself on its own strongest point: the claim must happen *before* branch creation, because `branch-management` wants the ticket ID in the branch name and `/plan-it` Step 1 creates the branch. Ticket ingress is therefore upstream of `/plan-it`, not inside it. It also fails `.claude/rules/skill-and-agent-self-review.md`'s platform-agnosticism rule — `/plan-it` is stowed to every consumer, most of whom have no tracker MCP server at all.

**Why not a hybrid that also publishes the invariant doctrine in claude-config.** The doctrine's only reader is a consultant standing up a new engagement, and the playbook that mints the skill has to restate the same steps to be usable. Two sites, one of them the wrong audience. The doctrine gets one home, in the repo whose readers need it.

The residual this accepts: three minted `plan-issue` skills can still drift. The template buys two things: the claim gate ships in every mint, so the second project's omission cannot recur. A future divergence is also diagnosable against a named source instead of against a sibling copy.

### Assumption ledger

**Root problem.** Three downstream repos need the same ticket → claim → plan → comment workflow, but only its *policy* is shared; every executable step is tracker-specific. Copy-and-edit has already lost the claim step in one of two copies and produced a near-verbatim duplicate support skill, and a third, structurally different tracker makes the copy source no longer applicable.

**Givens** (conditions the design treats as fixed, beyond its own reach):

- **G1.** The per-project `plan-issue` and `linear-formatting` skills live in private downstream repos this PR cannot touch — the projects own those repos, so every change there ships as its own separate commit on its own schedule.
- **G2.** `SKILL.md` has no `includes:`/`import:`/`extends:` field and `@path` is CLAUDE.md-only, so shared skill text is either duplicated or distributed as a plugin — there is no third option. [verified: `.claude/rules/skill-and-agent-self-review.md`, "No shared partials across skills"]
- **G3.** A plugin skill is reachable only after the consuming repo runs `claude plugin install <name>@claude-config --scope project` against a machine that has registered the marketplace. Claude Code's plugin resolution imposes this; nothing in claude-config can push a plugin into a downstream repo. [verified: `README.md:202-204`, `docs/skills.md:144-156`]
- **G4.** Jira models a claim as a workflow transition under a per-project workflow scheme rather than as a directly-settable status field, so "claimable" and "terminal" states are not a fixed global enum the design can hardcode. The vendor's workflow model imposes this.

**Material assumptions:**

1. Claiming the ticket (assignee + status transition) is the **default** behavior of the shared shape, not per-project optional; the second project's omission is a bug to fix at mint time. `[engineer-verified]`
2. The third project's tracker is Atlassian/Jira and is structurally, not just lexically, different from Linear. `[engineer-verified]`
3. The two existing projects' `linear-formatting` bodies differ only in three tokens: the MCP tool-name prefix, the issue-ID prefix used in examples and regexes, and a one-verb-vs-two-verb document tool split on one server. `[verified: read of both projects' linear-formatting/SKILL.md files during plan authoring]`
4. Those two MCP registrations differ only by server-name prefix (`mcp__linear__*` vs `mcp__linear-server__*`), so a body naming the tool by role and listing both observed prefixes is complete for both consumers without placeholders. `[verified: same read]`
5. All five base skills carrying a project-layer glob treat the layer as additive and proceed without one when absent. `[verified: plan-it/SKILL.md:33, plan-review/SKILL.md:73, code-review/SKILL.md:25, pr-description/SKILL.md:28-29, test-conventions/SKILL.md:15; docs/skills.md:176-182; test_skills.py:1248]`
6. A newly added plugin is exempt from the commit-time version-bump gate — the hook `continue`s when the plugin's manifest has no blob at the merge-base. `[verified: plugins/plugin-semver/hooks/require-plugin-version-bump.sh:235-238]`
7. A new `plugins/*/skills/*/SKILL.md` is already covered by the existing skill suite and by `select-tests.py`'s plugin-skills rule, but a new plugin manifest and the root marketplace edit are unmapped paths that hit the unmatched-path fallback and widen the run. `[verified: select-tests.py:51-57, 317; test_plugin_manifests.py:26-27]`
8. `disable-model-invocation: true` blocks `Skill()` invocation, so a plugin skill meant to be reached by name from a project's `plan-issue` must stay model-invokable and therefore must carry `TRIGGER when:` / `DO NOT TRIGGER when:` blocks. `[verified: test_skills.py:1238-1246 and 1219-1235]`
9. Which Jira MCP server the third project will use, and whether it exposes a workflow-transition tool at all, is unknown. `[unverified]` — the template handles this by degradation (row M2), not by assuming a capability.
10. `/plan-it` itself needs no change: its `argument-hint` already accepts a ticket id, Step 1 already handles a `<TICKET-ID>/<slug>` branch name, and Step 7 already routes a non-committed plan through "the project's own tracker or documentation tool." `[verified: claude/.claude/skills/plan-it/SKILL.md:10, 23, 133-135]`
11. The private consulting-playbook repo holds `methodology/`, `tools/`, `templates/`, and `playbooks/`, with no existing artifact for standing up a project's ticket-workflow skill. `[verified: read of that repo's top-level layout during plan authoring]`
12. Nothing mechanically prevents the three minted `plan-issue` skills from drifting again; G1 puts them outside any shared CI. `[unverified]` — accepted as a residual, see Out of scope.

**Mechanisms:**

- **M1 — a new public `plugins/linear-formatting/` plugin holding one `linear-formatting` skill, replacing both projects' local copies.** `anchors: row3, row4` — the duplication is value-level only, and rows 3 and 4 establish that the three varying tokens can be written generically rather than as placeholders, so the shared body stands alone.

  *Over-powered-primitive check.* A new marketplace plugin is heavier than a file edit, so three lighter primitives were weighed against it. (i) **Leave the duplication in place** — fails: this is the status quo that already produced the divergence in row 1's sibling skill, and it has no shared source to diff a future divergence against. (ii) **A consulting-playbook template both projects copy from** — fails: it still yields two derived copies, and adds a third site for content that carries no project identity at all, so a private home buys nothing it doesn't also cost. (iii) **A stowed user-scope skill under `claude/.claude/skills/`** — fails: it would load for every stow consumer on every machine regardless of tracker, and `.claude/rules/skill-and-agent-self-review.md` bars exactly this (platform tokens such as Linear MCP tool names in the stowed tree). The plugin is the primitive claude-config already has for "generic content, narrow audience, per-project install," and its supporting machinery (marketplace entry, semver gate, `select-tests.py` mapping, skill test coverage) costs nothing marginal.

- **M2 — a template plus an onboarding playbook in the private consulting-playbook repo that mints a complete, self-contained `plan-issue` skill per project.** `anchors: root, row1, row2, row9, row11` — the invariant content is procedure with no executable tracker-agnostic core (root), the claim gate must be non-optional in every mint (row 1), the third tracker is structurally different so no code factors out (row 2), the Jira capability is unknown so the template must carry a degradation clause (row 9), and the playbook repo has no such artifact yet (row 11). This is documentation, the lightest mechanism available; the heavier alternatives it displaces are argued in the Approach lead.

- **M3 — one-sentence amendment to `.claude/rules/skill-and-agent-self-review.md`'s "Global skill bodies stay platform-agnostic" bullet, adding the additive-vs-substitutive boundary on the project-layer glob.** `anchors: row5` — that bullet currently states the unqualified "put stack-specific checks in a project-layer skill," which is exactly the sentence that would send a future onboarding session down the rejected path. The rule file is auto-loaded when editing a `SKILL.md`, which is the moment the mistake would be made; `docs/skills.md` §Project-specific layers stays mechanism-only so the normative rule has one home.

- **M4 — no change to `/plan-it`, `/plan-review`, or any stowed skill body.** `anchors: row10` — the seam between a project's `plan-issue` and `/plan-it` is already documented on both sides.

**Dispatch split.** The claude-config file set below is one coherent change with a single verification command — one `code-writer` dispatch, not split. The consulting-playbook work (M2) is a different repository and cannot share this worktree; it is a separate effort, sequenced *after* M1 lands so the minted template can cite the installed plugin by its final `linear-formatting:linear-formatting` name.

## Critical files

**In this repo (claude-config) — the scope of this PR:**

- **Create** `plugins/linear-formatting/.claude-plugin/plugin.json` — manifest with `name`, `description`, `author`, and a `version`. `plugin-semver`'s skill body documents no initial-version rule, so confirm the literal via `/plugin-semver` during `/code-review` rather than fixing one here. Note: an unrelated plugin also named `linear` is already registered at `linear@claude-plugins-official`, but it only registers the Linear MCP server and carries no skills. Because the namespaces differ (`@claude-plugins-official` vs. `@claude-config`), there is no functional collision. Write this plugin's `description` field to name the formatting-conventions skill specifically, not the MCP server, so an installer can tell the two apart.
- **Create** `plugins/linear-formatting/skills/linear-formatting/SKILL.md` — the deduplicated body. Requirements derived from rows 3, 4, and 8:
  - Model-invokable, with `TRIGGER when:` and `DO NOT TRIGGER when:` blocks. Do **not** set `disable-model-invocation: true` — it would block the `Skill()` call a project's `plan-issue` makes.
  - Name each MCP tool by its role plus both observed server prefixes (`mcp__linear__<tool>` and `mcp__linear-server__<tool>`), rather than picking one.
  - Write the issue-ID regex generically (`[A-Z]+-\d+`-shaped), not against any specific project's ID-prefix length.
  - Describe the document-write surface as both the single-verb and the split create/update shapes, since which one a consumer sees depends on their server version.
  - No project name, repo name, or ticket prefix anywhere — this file is public.
- **Create** `plugins/linear-formatting/skills/linear-formatting/REFERENCES.md` — canonical Linear documentation URLs backing the auto-linking and comment-formatting claims. Required by CLAUDE.md's ground-every-choice rule and by the repo's citation-placement convention (URLs live here, never in `SKILL.md`).
- **Modify** `.claude-plugin/marketplace.json` — add the `linear-formatting` entry. **No `version` key** — `test_marketplace_entries_have_no_version_field` enforces this.
- **Modify** `README.md:206-213` — add a `linear-formatting` bullet to the **Current plugins** list, matching the existing one-line role-plus-install-command shape.
- **Modify** `docs/skills.md:142` — that paragraph currently reads "Two more plugins also live in `plugins/` but are not part of this repo's own authoring workflow" and names `lovable-cloud` and `npm-semver`. The count and the enumeration both go stale; update both.
- **Modify** `.claude/rules/skill-and-agent-self-review.md` — amend the "Global skill bodies stay platform-agnostic" bullet per M3. One sentence, stating that the project-layer glob is for additive refinements of the base flow, and that a flow with no standalone base belongs in the consuming repo as its own skill.
- **Modify** `CHANGELOG.md` `[Unreleased]` — an `### Added` entry for the new plugin. Note this file selects no tests.

**Reuse opportunities:** no new hook, script, or test is needed. `test_plugin_manifests.py` already globs every plugin manifest by path, `test_skills.py` already globs `plugins/*/skills/*/SKILL.md`, and `select-tests.py`'s `_is_plugin_skills_change` predicate is plugin-generic — a new plugin is picked up by all three without an edit. Model the manifest and directory layout on `plugins/npm-semver/` (a single-skill plugin) rather than `plugins/lovable-cloud/` (three skills plus hooks).

**Not in this repo — named for sequencing, no path in this PR:**

- **The private consulting-playbook repo:** a new template under `templates/` and a new onboarding playbook under `playbooks/`. The template's mandatory sections, in order:
  1. Frontmatter — skill named `plan-issue`, an `argument-hint` and TRIGGER matching the project's ticket-ID shape.
  2. Resolve the ticket.
  3. **The claim gate** — an invariant decision table, with per-tracker blanks for the state vocabulary and for the claim mechanism (settable field versus workflow transition):
     - Claim when unassigned, or already assigned to the user, and in a not-started state.
     - Stop and flag when assigned to someone else, or in a terminal/in-review state.
     - Degradation clause for row 9: when the tracker's MCP surface exposes no way to perform the claim, the skill reports the ticket's current state and asks the user to claim it manually — it never silently skips the step.
  4. Invoke `/plan-it` with the ticket as topic, noting the branch carries the ticket ID.
  5. The approval gate — post nothing before the user approves.
  6. Post the plan as a comment, naming the formatting skill to consult and forbidding any re-touch of status or assignee in this step.

  The playbook is the interview that fills the per-tracker blanks above.
- **Each Linear-based downstream repo:** delete the local `linear-formatting` skill and add the project-scope plugin install *in the same commit* — the plugin skill deliberately keeps the same name, so a window where both exist is a listing collision. Migrate both, not one; leaving a copy behind reintroduces the drift the plugin exists to end.
- **The Jira-based downstream repo:** a new `plan-issue` skill in its `.claude/skills/`, minted from the template. The second Linear project's `plan-issue` also gets the missing claim step, minted the same way (row 1).

## Verification

Nothing in this PR is executable, so verification is the repo's own suite plus the review pipeline.

1. `.venv/bin/python3 claude/.claude/scripts/select-tests.py`, then run the targets it prints. Expect it to widen to the full suite (`claude/.claude/ plugins/`): the new `plugins/linear-formatting/.claude-plugin/plugin.json` and the root `.claude-plugin/marketplace.json` are both unmapped paths that hit `select-tests.py`'s unmatched-path fallback (only `plugins/lovable-cloud/.claude-plugin/plugin.json` has an explicit exception, `select-tests.py:51-57`). That is CLAUDE.md's documented case 1 — `select-tests.py` widened on its own — not a licence to widen by hand, and not a rule-table bug to fix in this PR.
2. No `ruff` or `shellcheck` run is needed — this diff adds no Python and no shell file.
3. `/skill-review` is hook-enforced on the commit adding `plugins/linear-formatting/skills/linear-formatting/SKILL.md` (`require-skill-review.sh`); `/plugin-semver` is hook-enforced for any file under a plugin directory, though the version-bump gate itself no-ops here because a new plugin has no merge-base manifest to compare against (`require-plugin-version-bump.sh:235-238`); `/code-review` dispatches both automatically per `.claude/rules/review-pipeline-dispatch.md`.
4. Confirm by hand that the new `SKILL.md` and `REFERENCES.md` contain no project name, repo name, or ticket-ID prefix. The redaction hook catches tracker-ID shapes and blocklisted names, but the illustrative-regex and example-comment content in this skill is exactly the shape that carries a fingerprint past it.

The end-to-end behavior — a minted `plan-issue` claiming a ticket, planning, and posting back — is verifiable only in a downstream repo against a live tracker, and only after the consulting-playbook work lands. Do not claim it here.

## Out of scope

- **A Jira sibling plugin (`plugins/jira/`).** One consumer, and row 9 leaves the variability uncharacterized — the MCP server is not yet chosen. Revisit once that project's `plan-issue` exists and its actual tool surface is known; the Linear plugin's structure is the template.
- **Cross-repo drift detection, or a provenance header pinning each minted skill to a template version.** Three consumers across three repos with no shared CI (G1). Row 12's residual is accepted deliberately: the template fixes mint-time fidelity, and a divergence stays diagnosable against a named source. Adding machinery here would be a second defensive layer over a problem the first layer has not yet been shown to leave open.
- **Adding a sixth base skill with a `plan-issue-*` project-layer glob.** Rejected in the Approach lead and recorded here so it is not re-litigated during implementation.
- **Any change to `/plan-it`, `/plan-review`, or another stowed skill body** (row 10).
- **Adding `linear-formatting` to claude-config's own `.claude/settings.json` `enabledPlugins`.** `install.sh` installs this repo's own enabled plugins at project scope when run from claude-config's checkout; claude-config is not a Linear-tracked project, so the entry would install a skill nothing here uses.
- **Migrating either Linear project's other project-layer skills** (`plan-it-*`, `code-review-*`, and siblings). That axis already works and needs no design.
