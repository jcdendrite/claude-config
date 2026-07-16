---
name: verify-sources
description: >
  Confirm claims at the primary source — fetch the official docs, spec, or
  source directly. TRIGGER when: researching a library, API, framework, or
  architecture/design decision; or acting on a documentation claim from a
  subagent, blog post, or other secondary source. DO NOT TRIGGER when: you
  want a multi-source research synthesis (that is a research-harness job, not
  source verification); quick syntax lookups; error decoding; or when the
  cited URL is already the primary source.
user-invocable: true
---

# Verify Sources

## The failure mode this prevents

A subagent reports "library X has deprecated function Y in favor of
Z." You take the claim, plan a migration, present it. On inspection
the source docs are about a narrower migration path — one that
applies only to callers in a specific configuration. For the default
configuration, Y is the right choice and Z is wrong. The plan is
built on a confident-sounding misreading.

The pattern fails the same way every time: an agent (or a secondary
source — a blog post, an LLM summary, a forum answer) presents a
claim *without the context that scoped it*. The claim is technically
derivable from the docs but loses the qualifier that made it true. A
reader who only sees the relayed claim cannot tell.

The cost is asymmetric. Quick lookups that go wrong waste a few
minutes. Strategic conclusions that go wrong waste a migration plan,
a PR, a review cycle — and the user has to push back two or three
times before the primary source is actually fetched.

There is a second shape. An agent reaches a *real* source — a
widely-starred community GitHub repo, a popular blog post — and cites it as
"canonical" because it ranks high or is "most-cited." Reaching a source is
not the same as reaching an authority. Popularity is not provenance: an
unaffiliated aggregation is a lead to the originator or the first-party
spec, never the citation itself.

## The rule

When research will inform a code or design decision:

1. **Treat agent summaries and secondary sources as leads.** A
   subagent's "the docs say X" is a pointer to read the docs
   yourself. A blog post's summary of a deprecation is a pointer to
   the deprecation announcement.
2. **Fetch the primary source and read the surrounding context.**
   Rank candidate sources by *provenance*, not popularity: the
   concept's originator on their own domain, the official
   first-party vendor / standards-body docs or reference, the
   project CHANGELOG / release notes / migration guide, the RFC
   or spec, the source code itself when behavior is in dispute, or
   an official maintainer announcement. Read enough of the
   surrounding section to know what the claim is *scoped to* —
   which version, which configuration, which API surface, which
   migration path. **Star count and citation count are not
   provenance.** A community aggregation repo, an individual-engineer
   blog, an SEO/content-farm page, or an LLM-generated summary is
   disqualified as an *authority* however popular — treat it as a
   lead to a qualified source, and do not shape a search toward
   finding such a repo.
3. **Distinguish "docs say X" from "docs say X in the context of
   Y."** If the docs scope the claim — to a specific version, config
   mode, or migration path — your conclusion has to carry the scope
   through. The unscoped form often points at opposite code from
   the scoped form.
4. **Do this on the first pass.** If you find yourself relaying an
   agent's claim verbatim, or quoting a summary as if it were the
   source, the next user message will ask you to actually fetch it.
   Skip the round trip.

**Triangulate durable decisions across multiple first-tier sources.**
Depth on a single source (items 1–4) is not sufficient for a durable
guideline or architectural standard (any decision committed to a plan,
PR, or config change) — one authoritative source can
still be incomplete or idiosyncratic. Cite two or more independent
first-tier origins (vendor docs, specs, standards bodies), not one
aggregator restating the others however well-staffed. Record a
verbatim quote + URL per source so a reader can re-check what each
claim is scoped to.

If a primary source cannot be located, say so and present the
secondary source as a secondary source. Flag the uncertainty rather
than launder it into a confident claim.
