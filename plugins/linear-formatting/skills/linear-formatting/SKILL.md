---
name: linear-formatting
description: >
  Linear comment/link formatting: issue-ID auto-linking, markdown support,
  MCP tool-shape variance, Claude-authorship prefix, real-newline
  formatting, and the repeated-reference auto-linker drop. TRIGGER when:
  posting or editing a Linear issue comment or document, or referencing a
  Linear issue ID in a Linear repo. DO NOT TRIGGER when: the tracker isn't
  Linear (e.g. Jira), or the write only touches a status/assignee field
  with no comment/document body.
user-invocable: false
---

# Linear formatting

Formatting conventions for text written back to Linear — issue comments and
documents — so issue references auto-link and markdown renders as intended.

## Attribution

**CRITICAL:** All Linear writes via MCP tools are posted through the user's
Linear API token and will appear as the user's account. To avoid confusion
(the user may think they wrote the content themselves), **always** prefix
every comment / description / document body authored by Claude with
`**[Claude Code]**` followed by the content.

Applies to:
- Comment write body.
- New issue description.
- New project description.
- New milestone description.
- New Document write content.
- The body of a Document write update when adding Claude-authored sections.

Does NOT apply to:
- Edits that preserve existing user-authored content where Claude is only
  making minor surgical changes (typos, link updates). When in doubt
  whether an edit is minor or substantive, treat it as substantive and add
  the prefix.
- Read tools (no body parameter).

If you forgot the prefix on a write that already happened, edit the
existing record by passing its `id` to the same write tool, prefixing the
corrected body with a brief note that it was originally posted without
attribution and has now been corrected. Do not post a separate "correction"
comment — the note inside the edited record documents that the correction
was made.

**Always verify the prefix landed.** After a write that should carry the
`**[Claude Code]**` prefix, refetch the record and spot-check that the
prefix is actually present — do not assume the write saved as composed.

## MCP tool surface

Two Linear MCP server naming conventions are in observed use:
`mcp__linear__<tool>` and `mcp__linear-server__<tool>`. Name a tool by its
role and check for both prefixes rather than assuming one:

- **Comment write** — post a new comment on an issue. Some server versions
  expose one tool for both create and update (`create_comment` doubling as
  the update path, or an `update_comment` that also creates); others split
  it into two separate tools, `create_comment` and `update_comment`. Either
  shape resolves under both prefixes: `mcp__linear__create_comment` /
  `mcp__linear-server__create_comment`, and likewise for `update_comment`
  where the server splits the verb.
- **Document write** — post or revise a Linear document (e.g. a project
  overview or a longer write-up than a comment fits). Same split-vs-single
  variance as comment write: `mcp__linear__create_document` /
  `mcp__linear-server__create_document`, and `update_document` under either
  prefix where the server splits it out.
- **Issue lookup** — resolve an issue ID to its current title, state, and
  URL before referencing it. `mcp__linear__get_issue` /
  `mcp__linear-server__get_issue`.

Before calling a write tool, list the available tools and match by role —
do not hardcode a specific tool name into calling code, since the shape
varies by the installed server version.

## Issue-ID auto-linking

A Linear issue ID has the shape `[A-Z]+-\d+` — one or more uppercase letters
(the team key), a hyphen, and a number (the issue's sequence number in that
team). The letter count is not fixed; each Linear team picks its own key
length.

Linear auto-links a bare issue ID mentioned in a comment or document body
into a clickable reference, and pasting a full issue URL renders it as an
inline issue-reference card. To get this behavior:

- Write the ID in plain text (`PROJ-123`) or as an `@`-mention (`@PROJ-123`);
  either auto-links.
- Do not wrap the ID in a markdown link yourself (`[PROJ-123](url)`) — Linear
  already linkifies the bare form, and manually linking it produces a
  redundant or conflicting link target.
- Do not put the ID inside a code span or code block (`` `PROJ-123` ``) unless
  the intent is to show the literal text rather than link it — code regions
  are exempt from auto-linking.
- An `@`-mentioned issue is recorded as a related issue on the issue that
  mentioned it, not only rendered as a link — mentioning an ID has a side
  effect beyond formatting, so mention only the issues the comment is
  genuinely about.

## Comment markdown support

Linear's comment and document editor accepts typed Markdown and also
converts pasted Markdown into rich text automatically. Supported elements
relevant to a tracker write-back:

- Headings (`#`, `##`, `###`), bold (`**text**`), italic (`_text_`),
  strikethrough (`~~text~~`), inline code, and fenced code blocks.
- Bulleted, numbered, and checklist (`- [ ]`) lists.
- Blockquotes (`>`) and collapsible sections.
- Tables.

Write plan-back content as plain Markdown using these elements — there is
no need to pre-render to HTML or to avoid Markdown syntax on the assumption
the comment field is plain text.

## Use real newlines, not escape sequences

Linear renders markdown but does NOT interpret `\n` escape sequences. Literal
`\n` appears as visible text in issue descriptions and comments.

**Wrong** — renders as visible `\n\n`:
```
First paragraph.\n\nSecond paragraph.
```

**Correct** — use actual line breaks in the parameter value:
```
First paragraph.

Second paragraph.
```

## Repeated issue references may be silently dropped

Linear's auto-linker wraps plain-text issue-ID mentions in `<issue id="...">`
tags when content is saved. It sometimes drops the second and later
plain-text mentions of the same issue ID, leaving fragments like
`"Tracked in ."` in the rendered doc. Observed triggers:
- A numbered list where the reference sits near a trailing period.
- The same ID already appearing earlier in the document.

First mention in the doc is safe to write as plain form (`PROJ-123`); Linear
will link it. For every subsequent mention of the same ID, write the
explicit tag to guarantee it survives:

```
Tracked in <issue id="<uuid>">PROJ-123</issue>.
```

Retrieve the ID by calling Issue lookup (the `id` field) or by fetching a
doc that already references it and copying the `id` attribute.

**Always verify after a large write-tool call.** Refetch the document
and spot-check the sections that reference issues — especially numbered
lists with repeated references. Do not assume a save succeeded end-to-end.

## Checklist

Before posting a comment or document back to Linear:

```
linear-formatting checklist:
- [ ] Claude-authored content is prefixed with **[Claude Code]**
- [ ] Issue IDs referenced in the body use the bare or @-mention form, not a manual markdown link
- [ ] No issue ID sits inside a code span/block unless showing literal text on purpose
- [ ] The write tool was resolved by role against the installed server's actual tool list, not hardcoded by name
- [ ] Paragraph breaks are real newlines in the parameter value, not literal `\n`
- [ ] Repeated mentions of the same issue ID past the first use the explicit `<issue id="...">` tag
```
