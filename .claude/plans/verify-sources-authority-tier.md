# Plan: add a source-authority (provenance) axis to `verify-sources`

## Context

**Goal:** stop agents from passing off low-authority sources (random
community GitHub repos, individual-engineer blogs) as authoritative when
research grounds a design decision.

The `verify-sources` skill currently prevents exactly one failure mode —
the **scope-strip misread**: a relayed claim losing the qualifier that made
it true. Its "primary source" list is a *document-type* axis (vendor docs vs.
blog vs. spec). It has **no provenance/authority axis**, so it silently
assumes "a primary source" is also "an authoritative source."

Evidence (a recent architecture-research session on where to store
decision-log / ADR documentation): the skill *fired*, ran its own
`WebSearch`/`WebFetch`, fetched a community GitHub repo's README, and
promoted it as *"the canonical community reference… the most-cited ADR
resource."* One search was deliberately shaped to find a repo (query
contained `github`). The user rejected it:

> "Two of your sources were randos on github. The AWS source is good. Find
> some other high quality primary sources and come back to me."

Only on a user-prompted re-run did research reach first-tier sources
(concept originator on their own domain, official vendor docs, a
recognized-org tech-radar publication). The skill treated *reaching a
source* and *popularity/citation-count* as verification — **popularity was
mistaken for provenance.** A second, older episode is preserved as the
`feedback_reputable_sources_only.md` memory (blog-tier sourcing rejected for
a design decision) — same shape, confirming this is a recurring class, not a
one-off.

**Intended outcome:** `verify-sources` gains an explicit authority axis so
that even after an agent "reaches the source," it must judge whether that
source's *author/origin* carries authority, and a widely-starred community
repo is treated as a lead, not a citation.

## Approach

**Surgical fold-in to the existing skill** (chosen over a new dedicated
section). The new content is behavioral, not decorative, but it belongs
inside the skill's existing two-part shape ("The failure mode this prevents"
→ "The rule") so the skill stays tight — it loads on every fire, and
`/skill-review` flags prose that fights the skill's own brevity ethos. A
dedicated `## Source authority` section was considered and set aside: it
would restate the same content at greater length for prominence the
fold-in already achieves.

**Skill-only, no review-gate edits.** `plan-it` Step 5 already says "Consult
`code-review`, `test-conventions`, and `verify-sources` if their domains are
implicated," so the sharpened bar propagates into plan *authoring*
automatically — no second file to keep in sync. (`plan-review` itself does
not consult `verify-sources`; it is reached through plan-it at authoring
time.) Adding a parallel source-authority check to `plan-review`/`code-review`
would create a second site restating one rule (DRY/drift risk) for little
gain.

**Single source of truth.** The `feedback_reputable_sources_only.md` memory
records *why the user weighs source credibility* (personal preference); the
skill body is the *behavioral enforcement surface* distributed to all stow
users. The skill edit must encode the behavior in its own words — not copy
the memory's prose — so the two surfaces stay distinct (personal-why vs.
distributed-rule) rather than duplicated.

**Generic, platform-agnostic wording.** Global skill bodies ship to every
stow user, so the edit names *shapes* of disqualified source (community
aggregation repo, individual blog, SEO/content-farm page, LLM summary), not
specific repos, maintainers, or vendors. No name-and-shame of the actual
repo from the session; no project-identifying content. Keep the split
skill-review confirmed: the **rule** (item 2 disqualifier) stays
product-generic ("community aggregation repo … regardless of stars/citations"),
and the one product-specific illustration ("widely-starred community GitHub
repo") lives only in the narrative failure-mode paragraph, matching that
section's existing concrete-example voice (`library X`, `function Y`). When
wording item 2's disqualifier, keep it distinct from the existing
`Triangulate` paragraph's "not one aggregator restating the others" — that
paragraph governs *source count/independence* for durable decisions; item 2
governs *single-source authority tier*. Reinforce, don't restate.

### Exact edits — `claude/.claude/skills/verify-sources/SKILL.md`

**Edit 1 — add a second failure-mode paragraph** at the end of
`## The failure mode this prevents` (after the "cost is asymmetric"
paragraph, before `## The rule`):

> There is a second shape. An agent reaches a *real* source — a
> widely-starred community GitHub repo, a popular blog post — and cites it as
> "canonical" because it ranks high or is "most-cited." Reaching a source is
> not the same as reaching an authority. Popularity is not provenance: an
> unaffiliated aggregation is a lead to the originator or the first-party
> spec, never the citation itself.

**Edit 2 — extend rule item 2** (`Fetch the primary source and read the
surrounding context.`) to rank by provenance and name the disqualifier.
Reword the existing "Primary, in rough order:" list opener to lead with
provenance, and append the disqualifier + search-shaping note:

> 2. **Fetch the primary source and read the surrounding context.** Rank
>    candidate sources by *provenance*, not popularity: the concept's
>    originator on their own domain, the official first-party vendor /
>    standards-body docs or reference, the project CHANGELOG / release notes
>    / migration guide, the RFC or spec, the source code itself when behavior
>    is in dispute, or an official maintainer announcement. Read enough of the
>    surrounding section to know what the claim is *scoped to* — which
>    version, which configuration, which API surface, which migration path.
>    **Star count and citation count are not provenance.** A community
>    aggregation repo, an individual-engineer blog, an SEO/content-farm page,
>    or an LLM-generated summary is disqualified as an *authority* however
>    popular — treat it as a lead to a qualified source, and do not shape a
>    search toward finding such a repo.

The `Triangulate durable decisions across multiple first-tier sources`
paragraph already says "not one aggregator restating the others" and is left
as-is — it is now reinforced by the sharper item 2 rather than duplicated.

## Critical files

- **`claude/.claude/skills/verify-sources/SKILL.md`** — the only file
  changed (both edits above). Currently 74 lines; the two additions net
  ~+9 lines.

**Reuse / no new abstraction:** no new file, no `REFERENCES.md`, no
`_shared/` extraction (correct — skills do not share partials). The memory
`feedback_reputable_sources_only.md` is *not* edited; it remains the
personal-preference record.

## Verification

1. **Re-read the diff against the skill's own body** — invoke
   `/skill-review` (hook-enforced for `SKILL.md` changes via
   `require-skill-review.sh`) and confirm the additions don't violate the
   skill's brevity/voice rules. This is the repo convention: run the skill on
   its own diff.
2. `.venv/bin/pytest claude/.claude/` — ensure no skill-structure test
   (frontmatter, description-length floor) breaks.
3. `.venv/bin/ruff check claude/.claude/` — lint (no Python changed, but the
   suite is cheap and the repo runs it).
4. `/code-review` — dispatches `/skill-review` automatically for the staged
   `SKILL.md`.
5. **Behavioral spot-check:** re-pose the original ADR/decision-log research
   question in a fresh session and confirm the model now (a) ranks the
   originator/first-party sources above a popular community repo, and (b)
   declines to cite a "most-cited" repo as canonical without a first-tier
   corroborator.

## Out of scope

- Editing `plan-review` / `code-review` to add a parallel authority check
  (rejected above — DRY/drift risk; plan-review already consults the skill).
- Editing the `feedback_reputable_sources_only.md` memory.
- Any hook to mechanically enforce source authority — authority is a
  judgment call, not a pattern match; no hook can gate it.

## Execution note

On `ExitPlanMode` approval: this is a `claude-config` change, so create the
implementation branch per `branch-creation` (suggested slug
`verify-sources-authority-tier`), move this plan to
`.claude/plans/verify-sources-authority-tier.md` on that branch inside a
linked worktree (worktree enforcement is active), then make the two edits.
