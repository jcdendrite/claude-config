# References — linear-formatting

Source behind the auto-linking and markdown claims in SKILL.md. Not loaded at
skill runtime; read manually when verifying a rule still holds or adding new
guidance.

## Linear Docs — Editor
https://linear.app/docs/editor

Grounds "Comment markdown support": "We support most Markdown elements in
our text editor. Type in Markdown or paste it directly and it will be
converted into rich text automatically," followed by the supported element
list (headings, bold/italic/strikethrough, inline code, code blocks, lists,
checklists, blockquotes, collapsible sections, tables).

Grounds "Issue-ID auto-linking": "Write `@text` to mention a user, issue,
project, date, or document in a description or comment... Pasting an issue
ID will also link it in the editor, or you can mention issues with
`@[team-key]-[number]`. Referenced issues are added as related issues
automatically." The source's own worked example (a specific team key and
number) is replaced by a bracketed placeholder per this repo's redaction
convention. This grounds both the bare-form/`@`-mention auto-link behavior
and the related-issue side effect of an `@`-mention.

## Linear Docs — Comments and reactions
https://linear.app/docs/comment-on-issues

Grounds the comment-posting and comment-editing surface described in the
"MCP tool surface" section's Comment write role (posting a new comment,
editing an existing one) as the underlying product behavior the MCP tool
wraps.

## MCP server-name prefixes

Grounds "MCP tool surface"'s two observed prefixes independently of any
consumer's own configuration:

- `mcp__linear__*` — Anthropic's official `linear` plugin
  (`anthropics/claude-plugins-official`, `external_plugins/linear/.mcp.json`)
  registers the server under the key `linear`:
  `{"linear": {"type": "http", "url": "https://mcp.linear.app/mcp"}}`.
- `mcp__linear-server__*` — Linear's own setup instructions at
  https://linear.app/docs/mcp tell Claude Code users to run
  `claude mcp add --transport http linear-server https://mcp.linear.app/mcp`,
  registering the server under the key `linear-server`.
