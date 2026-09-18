# Development

Contributor guide for local setup, checks, tests, and CI.

See [[Architecture]] for runtime structure and [[Telemetry]] for observability.
See [refactoring opportunities](../refactoring-opportunities.md) for the reviewed
follow-up work and its validation boundaries.

## Prerequisites

- Python 3.12 or newer.
- `uv`.
- A non-production ServiceNow instance for live integration tests.
- A public OAuth authorization-code PKCE S256 application and a user with the required roles and ACLs for live tests.

## Set up a checkout

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
cp .env.example .env.local
```

Development uses the same OAuth flow as operation. Configure
`SERVICENOW_INSTANCE_URL`, `SERVICENOW_OAUTH_CLIENT_ID`, and the registered
loopback redirect URI in `.env.local` only for live calls. Set:

```dotenv
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

`SERVICENOW_OAUTH_SCOPE` is optional and defaults to `useraccount`. A custom
value must contain valid OAuth scope-token values enabled on the public
application. Process environment variables override dotenv files.

Unit tests isolate dotenv loading and stub authorization. Never put real tokens,
authorization codes, PKCE verifiers, passwords, API keys, client secrets, or
callback query strings in fixtures, logs, or committed configuration.

## Run checks

| Command | Purpose |
| --- | --- |
| `uv run ruff check .` | Run lint checks. |
| `uv run ruff format --check .` | Check formatting. |
| `uv run ruff format .` | Format files. |
| `uv run ty check src/` | Run type checks. |
| `uv run pytest` | Run default offline tests. |
| `uv run pytest tests/test_client.py` | Run one test file. |
| `uv run pytest tests/test_client.py::TestClass::test_method` | Run one test. |
| `uv run pytest -k "keyword"` | Run tests matching a keyword. |
| `uv run pytest --no-cov` | Skip coverage collection. |
| `uv run pytest -m ''` | Run the full suite, including live integration tests. |
| `uv run pytest -m integration` | Run live integration tests. |
| `uv build` | Build the distribution. |

Default pytest options exclude the `integration` marker and collect coverage
for `servicenow_mcp`.

## Test boundaries

Integration tests live in `tests/integration/`, use the `integration` marker,
and require interactive browser authorization on the same machine. Run them
only against a non-production instance.

Tool results are JSON strings. Parse them before assertions:

```python
import json

result = json.loads(await tools["my_tool"](param="value"))
assert result["status"] == "success"
```

For tool listing and schema checks, use the public asynchronous MCP API:

```python
tools = await mcp.list_tools()
```

Tests can construct `MCPServer`, call `register_tools`, and inject the same
named dependencies used by bootstrap: `settings`, `auth_provider`, `choices`,
`dictionary`, `client_factory`, and `telemetry`.

## Coverage and test value

Coverage measures executed source statements, not the number of test cases.
Many parameterized tests can exercise the same few lines, while replacing a
workflow with an `AsyncMock` skips its implementation. For investigation
workflows, exercise the registered tool and real client with mocked HTTP,
then assert findings, query bounds, masking, and failure behavior.

Remove tests only when their production API is removed or their assertions
are redundant. Do not exclude live code from coverage to improve the percentage.
Use `uv run coverage report -m` after pytest to find missed statements.

The supported type check is `uv run ty check src/`; ty targets Python 3.12,
the minimum supported runtime. The dev dependency and lockfile pin its version.

## Code conventions

- Ruff formats and lints Python.
- Line length is 120 characters.
- Target Python version is 3.12.
- Use double quotes and absolute imports.
- Add complete type hints to function signatures, including `-> None`.
- Use `str | None` and lowercase generic types such as `dict[str, Any]`.
- Use `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.

MCP SDK v2 uses `MCPServer` from `mcp.server`. Tools register with `@mcp.tool()`
and the project's `@tool_handler`. The entry point runs with
`mcp.run(transport="stdio")`.

## CI

CI runs on pushes to `main` and pull requests targeting `main`. It runs three
parallel jobs:

- **Lint:** `uv run ruff check .` and `uv run ruff format --check .`.
- **Type check:** `uv run ty check src/`.
- **Test:** `uv run pytest` on Python 3.12, 3.13, and 3.14.

The Python 3.12 test job uploads coverage to Codecov. New pushes cancel an
older run for the same branch. All jobs must pass before merge.

## Releases

Release automation uses `release-please` on pushes to `main`:

1. Conventional commits update a release pull request.
2. Merging the release pull request creates a GitHub Release and version tag.
3. The publish job builds with `uv build` and publishes to PyPI with `uv publish`.

The workflow uses `RELEASE_PLEASE_TOKEN`, `PYPI_TOKEN`, and `CODECOV_TOKEN` as
GitHub secrets. Do not copy secret values into repository files or logs.

## Contribution workflow

Use feature branches, small atomic commits, and conventional commit prefixes:

```text
feat: add a tool
fix: handle an error
docs: update the wiki
test: cover a boundary
```

Do not merge code that breaks existing tests. Use `gh` for GitHub operations.
