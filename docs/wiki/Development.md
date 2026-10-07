# Contributing to the MCP server

Use this guide to change the Python software that provides the ServiceNow MCP
tools. Examples include fixing a tool, adding a capability, or improving tests.

For scripts, Business Rules, and other changes on a ServiceNow instance, use
[[Instance-Development]]. For tickets and requests, use [[ITSM-Work]].
You do not need a Python checkout for either task.

## Prepare a local copy

You need Git, Python 3.12 or later, and `uv`. The default tests run offline.
They do not need a ServiceNow account or OAuth configuration.

Run these commands in a terminal:

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
git switch -c codex/your-change
uv sync --group dev
```

Replace `your-change` with a short name for your work. A feature branch keeps
unfinished work separate from `main`. `uv sync` prepares the project's Python
environment and installs its development dependencies.

If you already have a local copy, check its branch and existing changes before
starting. Preserve changes that belong to other work.

## Make and check a change

1. Find the tool or behavior in [[Architecture]].
2. Make one focused change.
3. Add or adjust tests for the behavior that changed.
4. Run the checks below from the repository's top-level folder.
5. Review the diff before creating a pull request.

| Command | What it checks |
| --- | --- |
| `uv run ruff check .` | Python style and common code errors. |
| `uv run ruff format --check .` | Whether Python files use the project's formatting. |
| `uv run ty check src/` | Whether source code uses types consistently. |
| `uv run pytest` | The default offline tests, with coverage. |
| `uv build` | Whether the package can be built for distribution. |

Use `uv run ruff format .` to format changed Python files.
If you already use `mise`, `mise run check` runs the lint, type, and offline test
checks. It does not run the package build or live integration tests.

A successful test run shows no failed tests. Read failures before changing code
or test expectations. Do not commit code that breaks existing tests.

For a documentation-only change, check links, examples, and agreement with the
implementation. Python tests do not check whether instructions are accurate.

### Run a smaller set of tests

Start with the file closest to your change. Run the default suite before
submitting a code change.

| Command | Scope |
| --- | --- |
| `uv run pytest tests/test_query.py` | One test file. |
| `uv run pytest tests/test_query.py::TestQueryMode::test_query_mode_returns_records` | One existing test. |
| `uv run pytest -k "keyword"` | Tests whose names match the keyword. |
| `uv run pytest --no-cov` | Offline tests without coverage collection. |
| `uv run coverage report -m` | Lines missed by the previous coverage run. |

Coverage shows which source statements the tests executed. A high percentage
does not prove that behavior meets requirements. Test meaningful results, failure behavior,
query limits, masking, and other boundaries affected by your change.

For investigation workflows, call the registered tool and real client with
mocked HTTP responses. Replacing the whole workflow with `AsyncMock` leaves
that workflow untested. Remove tests only when their API disappears or their
assertions duplicate other tests. Do not exclude live code to improve coverage.

## Test a live ServiceNow connection

Use a development or test instance. The tests in `tests/integration/` make
real ServiceNow calls and require browser authorization on the test machine.
The default `uv run pytest` command excludes them.

1. Prepare the public OAuth application in the [installation guide](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md#1-prepare-servicenow-access).
2. Copy `.env.example` to `.env.local` in the repository's top-level folder.
3. Configure the instance URL, public client ID, and registered redirect URL.
4. Select read tools and the development environment as shown below.
5. Run the integration tests and complete browser authorization.

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

```bash
uv run pytest -m integration
```

These values are placeholders. `SERVICENOW_ENV` controls write protection.
It does not select the instance or detect whether the instance is production.
The account still needs the roles, API access, and ACLs required by each test.

`SERVICENOW_OAUTH_SCOPE` defaults to `useraccount`. Each scope you configure must
be enabled on the OAuth application. Process environment variables override
dotenv files.

To run both offline and integration tests, use `uv run pytest -m ''`.
This command also requires the live setup above.

Keep `.env.local` uncommitted. Never add real tokens, authorization codes,
PKCE verifiers, passwords, client secrets, callback URLs, or personal data to
fixtures or logs. Unit tests isolate dotenv loading and stub authorization.
Their network guard rejects remote connections and browser access.

## Follow the existing tool pattern

Tool groups live in `src/servicenow_mcp/tools/`. Each group has a
`register_tools` function. Bootstrap supplies the dependencies that function
declares by name. See [[Architecture]] for the available names.

The project uses MCP SDK v2's `MCPServer` from `mcp.server`. Register a tool
with `@mcp.tool()` and the project's `@tool_handler`. The handler adds
diagnostics and returns a JSON error response when a tool fails.

Tool functions return JSON strings. Parse the result before asserting its
contents. This test fragment assumes `tools` contains the registered functions:

```python
import json

result = json.loads(await tools["my_tool"](param="value"))
assert result["status"] == "success"
```

Use `await mcp.list_tools()` for tool listing and schema checks.
Tests can construct `MCPServer`, register a group, and inject the same named
dependencies as bootstrap. Existing tool tests show that pattern.

The supported live connection uses stdio, the local connection between the AI
app and server.

### Python conventions

Ruff controls formatting and lint rules. The source targets Python 3.12, uses
120-character lines, double quotes, and absolute imports.

Add type hints to function signatures, including `-> None` where appropriate.
Use forms such as `str | None` and `dict[str, Any]`. Function and variable names
use `snake_case`. Class names use `PascalCase`. Constants use `UPPER_SNAKE_CASE`.
The supported type check is `uv run ty check src/`, with Python 3.12 as its target.
The development dependencies and lockfile select the tool version.

## Submit the change

Use small commits, each with one purpose. Use a conventional commit prefix
such as `feat:`, `fix:`, `docs:`, `refactor:`, or `test:`.
For example, `docs: clarify browser authorization` describes a documentation change.
Use `gh` for GitHub operations.

In the pull request, explain the behavior before and after the change.
State which checks you ran. Distinguish offline checks from live ServiceNow
tests. Update the affected user guide or tool reference when behavior changes.

GitHub's continuous integration (CI) repeats checks on pushes to `main` and
pull requests targeting `main`:

| Job | Checks |
| --- | --- |
| Lint | Ruff lint and formatting. |
| Type check | `ty` against `src/`. |
| Test | Offline tests on Python 3.12, 3.13, and 3.14. |

The Python 3.12 job uploads coverage to Codecov. A new push cancels the older
CI run for that branch. All checks must pass before merge.

Release automation uses `release-please`. Conventional commits update a release
pull request. Merging that pull request creates a GitHub Release and version tag.
The publish job builds the package and publishes it to PyPI.
`RELEASE_PLEASE_TOKEN`, `PYPI_TOKEN`, and `CODECOV_TOKEN` are GitHub secrets.
Keep their values out of files and logs.

For reviewed follow-up work, see
[refactoring opportunities](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/docs/refactoring-opportunities.md).
For diagnostics, use [[Telemetry]].
