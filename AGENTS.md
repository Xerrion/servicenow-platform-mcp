# AGENTS.md — servicenow-devtools-mcp

## Project Overview

- Python 3.12+ async MCP server for ServiceNow platform introspection, debugging, and change intelligence.
- Package manager: **uv** (not pip/poetry). Build system: hatchling.
- Source layout: `src/servicenow_mcp/` (src-layout). Entry point: `servicenow_mcp.server:main`.
- Config via `pydantic-settings` loading env vars from `.env` / `.env.local`.
- Key deps: `mcp`, `httpx`, `pydantic`, `pydantic-settings`, `uvicorn`, `starlette`.

## Setup

- `uv sync --group dev` — install all dependencies including dev tools.
- Copy `.env.example` → `.env.local` and fill in ServiceNow credentials.
- Supported Python versions: 3.12, 3.13, 3.14.
- No build step needed for development; `uv build` creates the distribution wheel.

## Lint / Format / Type-check

- `uv run ruff check .` — lint (rules: E, F, W, I, UP, B, SIM, RUF; E501 ignored).
- `uv run ruff check --fix .` — auto-fix lint issues.
- `uv run ruff format .` — auto-format code.
- `uv run ruff format --check .` — verify formatting without changes.
- `uv run mypy src/` — type checking (`disallow_untyped_defs = true`).
- mypy override: `servicenow_mcp.server` has `call-arg` error code disabled.

## Test Commands

- `uv run pytest` — run all unit tests (integration excluded by default via `-m 'not integration'`).
- `uv run pytest tests/test_client.py` — run a single test file.
- `uv run pytest tests/test_client.py::TestServiceNowClientGetRecord` — run a single test class.
- `uv run pytest tests/test_client.py::TestServiceNowClientGetRecord::test_get_record_success` — run a single test method.
- `uv run pytest -k "keyword"` — run tests matching a keyword expression.
- `uv run pytest -m integration` — run integration tests (requires `.env.local` with real credentials).
- `uv run pytest --no-cov` — skip coverage for faster iteration.
- Default addopts: `-m 'not integration' --cov=servicenow_mcp --cov-report=xml --cov-report=term-missing`.
- `asyncio_mode = "auto"` — no manual asyncio event loop configuration needed.
- **ALWAYS** test changes before considering a task complete; check console output for warnings/errors.

## Code Style & Formatting

- Formatter: **Ruff**, line length **120**, **double quotes**, target Python 3.12.
- Ruff lint rule sets: E (pycodestyle errors), F (pyflakes), W (pycodestyle warnings), I (isort), UP (pyupgrade), B (flake8-bugbear), SIM (flake8-simplify), RUF (ruff-specific).
- E501 (line-too-long) is ignored — the formatter handles wrapping at 120 chars.
- Use trailing commas in multi-line constructs (lists, dicts, function args).
- All files end with a single trailing newline.

## Import Conventions

- Import order enforced by ruff/isort: **stdlib → third-party → local**.
- **Absolute imports only**: `from servicenow_mcp.client import ServiceNowClient`.
- Group specific imports: `from servicenow_mcp.errors import AuthError, NotFoundError, ServerError`.
- No wildcard imports.
- Third-party: `httpx`, `mcp`, `pydantic`, `pydantic_settings`, `python-dotenv`, `uvicorn`, `starlette`.
- Dev-only: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `pytest-cov`.

## Type Annotations

- **All** function signatures must have full type hints (enforced by mypy `disallow_untyped_defs`).
- Return types always explicit, including `-> None` for void functions.
- Modern union syntax: `str | None` (not `Optional[str]`).
- Lowercase generic types (PEP 585): `dict[str, Any]`, `list[str]`, `set[str]`.
- Primary typing import: `from typing import Any`.
- Regex patterns typed as `re.Pattern[str]`.

## Naming Conventions

- Functions/methods/variables: `snake_case`.
- Classes: `PascalCase` (e.g., `ServiceNowClient`, `BasicAuthProvider`).
- Constants: `UPPER_SNAKE_CASE` (e.g., `DENIED_TABLES`, `MASK_VALUE`, `PACKAGE_REGISTRY`).
- Private methods/attributes: single underscore prefix `_` (e.g., `_table_url`, `_http_client`).
- Module-level logger: `logger = logging.getLogger(__name__)`.
- Test classes: `Test` prefix + feature (e.g., `TestServiceNowClientGetRecord`, `TestTableDescribe`).
- Test methods: `test_` prefix with descriptive name (e.g., `test_get_record_success`).

## Docstrings

- Every module starts with a module-level docstring: `"""Brief description."""`
- Classes and public functions have triple-double-quote docstrings.
- Tool functions use `Args:` section with indented param descriptions (MCP uses these for tool schemas).
- Fixtures have one-line docstrings explaining their purpose.

## Error Handling

- Custom exception hierarchy in `errors.py`, rooted at `ServiceNowMCPError(Exception)`.
- HTTP-mapped errors: `AuthError` (401), `ForbiddenError` (403), `NotFoundError` (404), `ServerError` (5xx).
- Policy errors: `PolicyError`, `QuerySafetyError`, `WriteGatingError`.
- HTTP status mapping in `client.py:_raise_for_status()` — always call after API responses.
- **Tool functions never raise to MCP** — they catch all exceptions and return JSON error envelopes:
  ```python
  except Exception as e:
      return json.dumps(format_response(data=None, correlation_id=correlation_id, status="error", error=str(e)))
  ```
- Use `assert self._http_client is not None` to guard against uninitialized client state.

## Async Patterns

- All ServiceNow API calls are `async`.
- `ServiceNowClient` is an async context manager: `async with ServiceNowClient(settings, auth_provider) as client:`.
- Auth `get_headers()` is async for extensibility.
- Tests use `@pytest.mark.asyncio` decorator (asyncio_mode is auto).

## Architecture & Key Patterns

- **Tool registration**: Each `tools/*.py` exports `register_tools(mcp, settings, auth_provider)` registering `@mcp.tool()` async functions.
- **Response format**: All tools return `json.dumps(format_response(...))` with a `correlation_id` via `utils.format_response()`.
- **Policy layer**: Call `check_table_access(table)` before table access; `enforce_query_safety()` for queries; `mask_sensitive_fields()` on returned records; `can_write()` before mutations.
- **Investigation modules**: `investigations/*.py` export `async def run(client, params) -> dict` and `async def explain(client, element_id) -> dict`; registered in `INVESTIGATION_REGISTRY`.
- **Tool packages**: Bundled in `packages.py:PACKAGE_REGISTRY` — `full` (default), `introspection_only`, `none`.
- **State management**: `PreviewTokenStore` for preview/apply workflows (UUID tokens with TTL); `SeededRecordTracker` for test data cleanup by tag.
- **Config**: `Settings(BaseSettings)` loads from env vars — `servicenow_instance_url`, `servicenow_username`, `servicenow_password`, `mcp_tool_package`, `servicenow_env`, `max_row_limit`.

## Testing Patterns

- HTTP mocking: **respx** library with `@respx.mock` decorator on async test methods.
- Fixtures in `tests/conftest.py` provide `settings` and `prod_settings` using `patch.dict("os.environ", ...)`.
- Always construct `Settings(_env_file=None)` in tests to avoid loading real env files.
- Tool tests use a helper to register and extract tool callables:
  ```python
  def _register_and_get_tools(settings, auth_provider):
      mcp = FastMCP("test")
      register_tools(mcp, settings, auth_provider)
      return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
  ```
- Test classes group related tests (e.g., `TestServiceNowClientGetRecord`).
- Integration tests in `tests/integration/` marked `@pytest.mark.integration`; require `.env.local`.
- Integration fixtures are session-scoped: `live_settings`, `live_auth`, discovered sys_ids.
- Parse tool JSON output with `json.loads()` and assert on response envelope fields (`status`, `data`, `error`).

## Git Workflow

- **NEVER work on main/master** — always create feature branches.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, etc.
- **Small commits** — atomic, focused changes.
- **release-please** automates versioning and releases on push to main.
- **Use `gh` CLI** for all GitHub operations (PRs, issues, etc.).
- CI runs lint, type-check, and tests on Python 3.12/3.13/3.14 for every PR.
- Always test changes before considering a task complete.
- Always check console output during runs and fix any errors/warnings.
