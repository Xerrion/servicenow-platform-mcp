# Work on the server

This page is for people changing the server itself. To use the server with an
AI app, start with [Getting Started](Getting-Started.md).

## Set up a checkout

Install Git and [mise](https://mise.jdx.dev/getting-started.html). The project
supports Python 3.14 and later. Its local setup selects Python 3.15.

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
git switch -c your-change
mise trust
mise install
mise run deps:python
```

The last two commands install tools and project dependencies. The dependencies
come from `uv.lock`.

## Make a change

1. Find the affected code and tests. [Architecture](Architecture.md) shows where to look.
2. Change the behavior and add tests for the result and relevant failures.
3. Update the user guide or tool reference if users will see a difference.
4. Run the checks below and review the diff.

## Run checks

Run these commands from the repository root.

- `mise run check`: check code, types, and Markdown.
- `mise run test`: run the offline test suite.
- `mise run check:markdown`: check Markdown formatting and lint.
- `mise run build`: build the distributable package.

The tests do not need a ServiceNow account. They block remote network access.
A passing offline test does not prove a live instance works.

To run one test file, use `uv run pytest tests/test_query.py`. The full suite
uses coverage. CI also tests Python 3.14 and 3.15.

## Change a tool

Tool definitions live in `src/servicenow_mcp/mcp/tools/`. Their work belongs
in `src/servicenow_mcp/capabilities/`. Check the registered tool through the
MCP dispatcher when changing its inputs or outputs.

Run `mise run schemas` after changing a tool contract. Review the changes to
`tests/fixtures/native_tool_schemas.json`. For write operations, test that a
preview makes no remote write and that an apply token cannot be used twice.

Keep real credentials and personal data out of tests and logs. Never commit a
local `.env.local` file.

## Submit your work

Use a feature branch. Review the changed files before opening a pull request.
Explain the user-visible change and list the checks you ran. Use conventional
commit prefixes such as `fix:` or `docs:`.
