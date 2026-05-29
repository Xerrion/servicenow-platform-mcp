# Development

This guide is for contributors working on the MCP server itself - not for consumers integrating it into an AI client.

Related pages: [[Architecture]], [[Telemetry]].

---

## Repository Layout

The project uses a src-layout with hatchling as the build backend and **uv** as the package manager.

```
src/servicenow_mcp/         # Package root
  server.py                 # Entry point (console script servicenow-platform-mcp)
  client.py                 # ServiceNowClient - async HTTP
  config.py                 # Settings (pydantic-settings)
  packages.py               # Package registry and tool group mapping
  tools/                    # One module per tool group
  investigations/           # Investigation modules (7)
tests/                      # Unit and integration tests
  integration/              # Live-instance tests (require .env.local)
.github/workflows/          # CI, CodeQL, release-please, stale
pyproject.toml              # Metadata, dependencies, tool configuration
```

Entry point: `servicenow_mcp.server:main` (registered as the `servicenow-platform-mcp` console script).

Build backend: `hatchling`. Package manager: `uv` (not pip, not poetry).

---

## Setup

```bash
uv sync --group dev
cp .env.example .env.local   # fill in ServiceNow credentials
```

No build step is needed for development. Run the server with `uv run servicenow-platform-mcp`.

---

## Development Loop

### Tests

```bash
uv run pytest                                           # all unit tests (integration excluded)
uv run pytest tests/test_client.py                      # single file
uv run pytest tests/test_client.py::TestClass::test_method  # single test
uv run pytest -k "keyword"                              # keyword filter
uv run pytest -m integration                            # integration tests (needs .env.local)
uv run pytest --no-cov                                  # skip coverage for speed
```

### Lint and Format

```bash
uv run ruff check .            # lint
uv run ruff check --fix .      # auto-fix
uv run ruff format .           # format
uv run ruff format --check .   # verify without writing
```

### Type Check

```bash
uv run mypy src/
```

---

## Ruff Configuration

Configured in `pyproject.toml`. Key settings:

| Setting | Value |
|---------|-------|
| Line length | 120 |
| Quote style | double |
| Target | `py312` |
| Src dirs | `["src", "tests"]` |

Selected rule families:

```
E, F, W, I, UP, B, SIM, RUF, C4, DTZ, T20, PTH, TC, RET, PLW, PT, A, COM, PIE, ISC, G, INP, TID, ERA
```

Ignored rules:

```
E501, COM812, ISC001, TC001, TC002, TC003, RET504, RET505
```

Per-file ignores for `tests/**/*.py`: `T20, ERA001, PT019`.

isort is configured with `known-first-party = ["servicenow_mcp"]`.

---

## Mypy Configuration

| Setting | Value |
|---------|-------|
| `python_version` | `"3.12"` |
| `disallow_untyped_defs` | `true` |
| `ignore_missing_imports` | `true` |
| `warn_return_any` | `false` |
| `warn_unused_configs` | `true` |

Module override: `servicenow_mcp.server` disables the `call-arg` error code. This accommodates FastMCP's dynamic tool registration signature.

---

## Pytest Configuration

| Setting | Value |
|---------|-------|
| `asyncio_mode` | `"auto"` |
| `testpaths` | `["tests"]` |
| Default addopts | `-m 'not integration' --cov=servicenow_mcp --cov-report=xml --cov-report=term-missing` |

The `integration` marker gates tests that run against a live ServiceNow instance. These require credentials in `.env.local` and are excluded by default.

---

## Adding a New Tool Group

Every tool group module exports a `register_tools` function with the canonical 5-argument signature. The server loader calls every group with identical arguments regardless of whether the group needs them all.

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    del choices, dictionary  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def my_tool(table: str, correlation_id: str = "") -> str:
        """Brief description shown to the AI client.

        Args:
            table: The ServiceNow table name.
        """
        validate_identifier(table)
        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.some_method(table)

        return format_response(data=result, correlation_id=correlation_id)
```

Rules:

1. Decorator order is `@mcp.tool()` then `@tool_handler`.
2. `correlation_id: str = ""` is always the last parameter. `@tool_handler` auto-injects it and hides it from the MCP schema.
3. Never raise to the caller. `@tool_handler` wraps every call in `safe_tool_call()` which catches exceptions and returns serialized error envelopes.
4. The docstring `Args:` section becomes the tool's parameter descriptions in MCP.
5. Register your group in `_TOOL_GROUP_MODULES` and add it to the appropriate preset packages in `packages.py`.

The one exception: `list_tool_packages` is registered inline in `server.py` without `@tool_handler`. It has no `correlation_id`, no error envelope, and returns `serialize(list_packages())` directly.

---

## Testing Patterns

### HTTP Mocking with respx

All unit tests mock HTTP via the **respx** library:

```python
@respx.mock
async def test_query_success(self, settings, auth_provider):
    respx.get("https://test.service-now.com/api/now/table/incident").mock(
        return_value=httpx.Response(200, json={"result": [...]})
    )
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["query"](table="incident", encoded_query="active=true")
    result = json.loads(raw)
    assert result["status"] == "success"
```

### Registering Tools in Tests

Use the helper with the same 5-argument signature:

```python
def _register_and_get_tools(settings, auth_provider, choices=None, dictionary=None):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices, dictionary=dictionary)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

### Parsing Tool Output

All tools return a serialized JSON string via `format_response`. Parse with `json.loads`:

```python
raw = await tools["my_tool"](table="incident")
result = json.loads(raw)
assert result["status"] == "success"
assert result["correlation_id"]  # always present
```

### Integration Tests

- Located in `tests/integration/`
- Marked with `@pytest.mark.integration`
- Excluded by default; run with `uv run pytest -m integration`
- Require `.env.local` with real ServiceNow credentials
- Use session-scoped fixtures for the instance connection

---

## CI

Four workflows live in `.github/workflows/`:

### ci.yml

Triggers on push to `main` and PRs targeting `main`. Concurrency groups cancel in-progress runs.

Three parallel jobs on `ubuntu-latest`:

1. **lint** - `uv run ruff check .` and `uv run ruff format --check .`
2. **type-check** - `uv run mypy src/`
3. **test** - matrix over Python 3.12, 3.13, 3.14 (`fail-fast: false`). Coverage uploaded to Codecov on 3.12 only.

### codeql.yml

GitHub CodeQL static analysis for Python. Runs on push, PRs, and a weekly cron (Monday 06:00 UTC).

### release-please.yml

Automated releases driven by conventional commits. See Releases below.

### stale.yml

Marks inactive issues and PRs as stale (daily cron).

---

## Releases

The project uses **release-please** (`googleapis/release-please-action@v5`) with conventional commits.

Workflow:

1. Land changes on `main` using conventional commit messages (`feat:`, `fix:`, `docs:`, etc.).
2. release-please opens or updates a release PR with a generated changelog.
3. Merging that PR creates a GitHub Release and git tag.
4. A conditional **publish** job runs `uv build` then `uv publish` to PyPI.

Version is managed in `pyproject.toml`. release-please handles the bump automatically.

Required repository secrets: `RELEASE_PLEASE_TOKEN`, `PYPI_TOKEN`, `CODECOV_TOKEN`.
