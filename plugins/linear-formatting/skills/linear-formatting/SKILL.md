---
name: linear-formatting
description: >
  Linear issue-comment and issue-link formatting conventions for tracker
  writes: issue-ID auto-linking, comment markdown support, and the
  create/update tool-shape variance across Linear MCP server versions.
  TRIGGER when: posting or editing a Linear issue comment or document,
  or writing text that references a Linear issue ID for a Linear-tracked
  repo. DO NOT TRIGGER when: the tracker is not Linear (e.g. Jira, GitHub
  Issues), or the write touches only a status/assignee field with no
  comment or document body.
user-invocable: false
---

# Linear formatting

Formatting conventions for text written back to Linear — issue comments and
documents — so issue references auto-link and markdown renders as intended.

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

## Checklist

Before posting a comment or document back to Linear:

```
linear-formatting checklist:
- [ ] Issue IDs referenced in the body use the bare or @-mention form, not a manual markdown link
- [ ] No issue ID sits inside a code span/block unless showing literal text on purpose
- [ ] The write tool was resolved by role against the installed server's actual tool list, not hardcoded by name
```
