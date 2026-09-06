# Verify Sources — References

Edit-time reference for `SKILL.md`. Not loaded at runtime. Read this
file manually when updating skill rules to verify the rationale still
holds or to add new guidance.

## The failure mode this prevents

A relayed claim (e.g., "library X deprecated Y in favor of Z") often
drops the qualifier that scoped it — check the primary source before
acting on it.

The pattern fails the same way every time: an agent (or a secondary
source — a blog post, an LLM summary, a forum answer) presents a
claim *without the context that scoped it*. The claim is technically
derivable from the docs but loses the qualifier that made it true. A
reader who only sees the relayed claim cannot tell.

The cost is asymmetric — a wrong quick lookup wastes minutes, but a
wrong strategic conclusion wastes a migration plan or a review cycle,
so verify before committing to durable decisions.

There is a second shape. An agent reaches a *real* source — a
widely-starred community GitHub repo, a popular blog post — and cites it as
"canonical" because it ranks high or is "most-cited." Reaching a source is
not the same as reaching an authority. Popularity is not provenance: an
unaffiliated aggregation is a lead to the originator or the first-party
spec, never the citation itself.
