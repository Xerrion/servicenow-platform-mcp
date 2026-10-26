# Agent guide

This repository provides a Python MCP server for ServiceNow. The installed
entry point is `servicenow_mcp.server:main`. Python 3.14 is the minimum
supported version; the local mise configuration selects Python 3.15.

## Start here

- Check the branch and working tree before editing. Preserve changes made by
  others. Work on a feature branch, not `main` or `master`.
- Trace the affected behavior through the code and tests before changing it.
  Keep the change focused and update documentation when behavior changes.
- Use `docs/wiki/Development.md` for the development workflow and
  `docs/wiki/Architecture.md` for the module boundaries.
- For ServiceNow documentation needed during a review, use
  `docs/ServiceNowDocs/`. The local `openapi-specs/` directory contains
  ServiceNow REST API specifications. Check their scope and version before
  relying on them.

## Implementation boundaries

- Put operation validation, orchestration, and result assembly in
  `src/servicenow_mcp/capabilities/`. MCP adapters belong in
  `src/servicenow_mcp/mcp/tools/`; REST clients and shared metadata belong in
  `src/servicenow_mcp/platform/`. See the development
  guide before adding a capability or tool.
- When changing an MCP tool contract, check its inputs, outputs, annotations,
  and `tests/fixtures/native_tool_schemas.json`. Exercise the registered tool
  through the MCP dispatcher when its wire behavior matters. `mise run schemas`
  regenerates the fixture; review the resulting diff.
- For write workflows, preserve the preview and apply safeguards. A preview
  must make no upstream write, and an apply token must permit at most one
  change request. Test relevant failure and stale-state paths.
- Keep credentials, authorization data, and personal data out of source,
  fixtures, and logs. Treat live instance data as sensitive.

## Checks

Run commands from the repository root. Use the existing environment; ask before
installing tools or syncing dependencies.

- Markdown changes: run `mise run check:markdown` and check affected links and
  examples.
- Python changes: run `mise run lint:python` and `mise run typecheck:python`.
- Behavior changes: run focused tests, then `UV_NO_SYNC=1 mise run test` for the
  offline suite.
- Cross-stack changes: run `mise run check` and `UV_NO_SYNC=1 mise run test`.

The test task uses `uv run`, which can sync dependencies. `UV_NO_SYNC=1` uses
the existing environment without syncing; if it is missing or incomplete, ask
before running `mise run deps:python`.

The test suite runs offline. Before connecting to a live ServiceNow instance,
confirm the target and authorization. `SERVICENOW_ENV` controls local write
protection; it does not identify the target instance.

Review formatter and fixture diffs before keeping them. The tasks in
`.mise/conf.d/tooling.toml` are generated; put project-specific overrides outside
generated sections. Report checks that failed or could not run, including the
impact on verification. Do not commit code that breaks existing tests.

## Delivery

Review the final diff and keep unrelated work out of commits. If a commit is
requested, use a conventional prefix such as `fix:`, `feat:`, or `docs:`.
Use `gh` for GitHub operations. Report what changed, what now works, and the
checks actually run. Distinguish offline results from live verification.
