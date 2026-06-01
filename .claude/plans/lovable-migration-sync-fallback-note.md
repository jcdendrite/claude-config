# Plan: mark `/lovable-cloud-migration-sync` as the engineer-reviewed fallback

## Context

**Goal:** add one clarifying sentence to the public `lovable-cloud-migration-sync`
skill marking it as the *engineer-reviewed fallback* flow, and bump the plugin
version. Nothing else.

**Why this is the whole change (the design story).** The original task framed this
as "add a CI-proof deletion gate to the public skill" so a blind non-engineer
could safely delete a redundant Supabase migration. Working through the design
surfaced a flaw: baking a specific implementing project's CI contract (a named
proof check, a manifest schema, verdict routing, an escalation target) into the
*public, shared* plugin body couples the plugin to one project — the parameters
are inert for everyone else, and the plugin can no longer stand alone. That is a
distributed-monolith shape.

The `code-review` / `plan-review` project-layer glob was considered and rejected as
the wrong idiom here: those layers are **additive refinements of the same
technique** (the base reviews; the layer adds project-specific checks to that same
review). A CI-proof-gated PR flow is a **different, substitutive technique** — it
would *replace* the manual eyeball-diff delete, not refine it. A glob meaning "if a
layer exists, ignore the steps below and do something else" abuses the layering
idiom.

**Resolution.** The public skill stays exactly standalone as the generic,
engineer-reviewed manual cleanup flow — the fallback any Lovable project can use
as-is. The CI-proof gate becomes its own separate skill living entirely in the
**consuming project's private repo**, with its own trigger and all
project-specific machinery (and the security/platform hardening that review
surfaced). That gate work is **out of scope for this repo**. The only change here
is a one-sentence note so the standalone/gated relationship is legible to a future
reader and nobody wires the manual flow into a blind-operator context by mistake.

**Audience note.** `plugins/lovable-cloud/` is public and stow-distributed to every
clone; the note must name no implementing-project specifics and read cleanly for
users who have no gate skill at all (the majority). It is anchored in the trust
assumption the flow already makes (a human reviews the diff), not in any external
artifact.

## Approach

Two edits, both in `plugins/lovable-cloud/`.

**1. Add the fallback note to the skill.** Insert one sentence immediately after
the existing genericization blockquote (currently the line beginning "_This skill
was extracted from a production Lovable Cloud project…_"), at the top of the body
where a scope/trust caveat belongs — not buried in the delete step:

> **This is the engineer-reviewed fallback flow** — its safety rests on a human
> reviewing the diff (steps 4–5) before deletion; contexts that delete without that
> review (e.g. non-engineer operators) need a dedicated, project-provided gated
> procedure that fails closed, not this one.

Why this phrasing is unambiguous: it states the load-bearing assumption (a human
reviews the diff) rather than assuming the reader knows it; "project-provided"
makes clear nothing ships here to do the gating, so no reader hunts for a missing
skill; "fails closed" tells a future implementer what *gated* must guarantee
without leaking any mechanism, check name, or project; it uses no PR-defined terms
and survives the PR being merged.

**2. Bump the plugin version.** `plugin.json` `version` `2.4.0` → **`2.4.1`**
(patch: a backwards-compatible documentation clarification, no behavioral or
trigger change). Confirm the bump level against the `plugin-semver` skill during
implementation.

No glob, no extension point, no gate machinery in this repo.

## Critical files

- **Modify** `plugins/lovable-cloud/skills/lovable-cloud-migration-sync/SKILL.md`
  — insert the one-sentence note after the genericization blockquote. Reuse the
  existing blockquote/tone; change nothing in steps 1–7.
- **Modify** `plugins/lovable-cloud/.claude-plugin/plugin.json` — `version`
  `2.4.0` → `2.4.1`.

## Verification

This is a documentation-only change to a skill body, so verification is
review-gated plus the repo suite:

1. **`/skill-review`** on the diff (hook-enforced before commit via
   `require-skill-review.sh`) — confirm the note is unambiguous, at the right
   altitude, and adds no PR-defined terminology.
2. **`/code-review`** on the staged diff (SKILL.md + plugin.json).
3. **`plugin-semver`** — confirm patch is the correct bump for a doc clarification.
4. **Repo suite from the worktree** (worktree enforcement active):
   `../../../.venv/bin/pytest claude/.claude/` and
   `../../../.venv/bin/ruff check claude/.claude/`.
5. **Redaction grep** — `git grep -nE 'c96ce8be|Daytag|DayTag'` on the staged diff
   must return nothing (trivially true; the note names no project).
6. **`/ready-for-review`** to commit, push, and open the PR. **Do not merge** — an
   AI agent that opens a PR in this repo does not merge it; the engineer merges
   after review.

Plan-review note: this plan is now a single-sentence doc note plus a patch bump,
below the `/plan-review` trigger threshold (trivial config/doc change). No formal
re-review is run; the design reasoning above was settled in conversation.

## Out of scope

- **The entire CI-proof gate** (pair computation, `.sync-manifest.json`, the proof
  check poll, SHA-pinned merge, verdict routing, escalation) — now a separate skill
  in the **consuming project's private repo**, not this public repo.
- **PR A — the consuming project's CI proof workflow** — separate work in that
  project's repo.
- **Override / discovery** (ensuring a blind operator in a gated project doesn't
  fall through to this manual flow) — solved on the consuming-project side
  (disable/override the base skill there, or the gate skill's trigger is primary),
  not by anything in the public plugin.

## Follow-up (after plan mode exits)

Save a `feedback`-type memory: shared/public plugin skills must stand alone — do
not bake a specific implementing project's contract into a shared artifact
(distributed monolith); and the project-layer glob (`code-review-*` /
`plan-review-*`) is for *additive refinements of the same technique*, not for a
*substitutive different technique* (use a separate standalone skill + fallback note
for that). Links: [[feedback_project_layer_skill_location]],
[[feedback_stow_hooks_not_per_project]].
