# Development

Comprehensive development guide for contributing to `servicenow-platform-mcp`.

See also: [[Architecture]] for technical internals, [[Telemetry]] for observability.

## Getting Started

### MCP SDK

The project requires `mcp>=2.1.1`. MCP SDK v2 uses `MCPServer`, imported from `mcp.server`:

```python
from mcp.server import MCPServer

mcp = MCPServer("servicenow-platform-mcp")
```

Tool registration still uses `@mcp.tool()` with the project's `@tool_handler` decorator. Registration functions type the server parameter as `mcp: MCPServer`. The server continues to use `mcp.run(transport="stdio")`.

The direct `httpx` dependency is used by the application and remains independent of the SDK. MCP SDK v2's `httpx2` is transitive.

### Prerequisites

- **Python 3.12+** (tested on 3.12, 3.13, 3.14)
- **uv** - Fast Python package manager (not pip/poetry)
- For live integration tests: a non-production ServiceNow instance, a public
  OAuth PKCE application, and a user with the required roles and ACL access

### Setup

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
cp .env.example .env.local
```

### Local authentication setup

Development uses the same public authorization-code PKCE S256 flow as normal
operation. In **System OAuth > Application Registry**, create or select the
application, set **Public Client=true**, enable PKCE **S256** and `useraccount`,
and register the exact redirect URL `http://127.0.0.1:8765/oauth/callback`.

For live calls, configure `.env.local` in the process working directory:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_SCOPE=useraccount
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
SERVICENOW_OAUTH_TIMEOUT_SECONDS=180
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

The scope must be exactly `useraccount`. The callback must use
`http://127.0.0.1:<port>/oauth/callback`, port `1024`-`65535`, and match the
Application Registry entry. Process environment variables override `.env.local`,
which overrides `.env`. Restart after changing settings.

The first live outbound test or tool call opens browser authorization. The
browser and MCP process must run on the same machine. The REST identity is the
ServiceNow user who authorizes the application; roles and ACLs still apply.
Unit tests remain offline with stubbed authorization and mocked HTTP requests.
Authentication tests exercise the loopback receiver with a mocked browser.

Only the access token and its expiry stay in memory. Restart or expiry requires
browser authorization on the next outbound call. REST calls use Bearer headers, never
tokens in URLs. A REST 401 discards only the matching token without replaying
the request. A newer concurrent grant is not invalidated.

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`.
Non-empty values fail startup. A stale `SERVICENOW_OAUTH_CLIENT_SECRET` is
ignored, not used or rejected.
Never put real tokens, authorization codes, PKCE verifiers, callback query
strings, API keys, Basic credentials, or client secrets in fixtures, logs, or
committed configuration. Test-only values are used by the offline tests.

For REST policy failures, use [[Configuration]]. An API-key-only policy can
block OAuth after token issuance. Migrate only the affected policy; do not
disable global protection or unrelated policies.

No build step is required for development. The server runs directly from source via `uv run servicenow-platform-mcp`.

## Development Commands

| Command | Purpose |
| --- | --- |
| `uv run ruff check .` | Lint (all rules) |
| `uv run ruff check --fix .` | Auto-fix lint issues |
| `uv run ruff format .` | Format code |
| `uv run ruff format --check .` | Verify formatting without changes |
| `uv run mypy src/` | Type checking |
| `uv run pytest` | Run all unit tests (integration excluded) |
| `uv run pytest tests/test_client.py` | Run a single test file |
| `uv run pytest tests/test_client.py::TestClass::test_method` | Run a single test |
| `uv run pytest -k "keyword"` | Run tests matching a keyword |
| `uv run pytest -m integration` | Run integration tests (requires `.env.local`) |
| `uv run pytest --no-cov` | Skip coverage collection for speed |
| `uv build` | Build distribution wheel |

## Code Style

### Formatter

- **Ruff** formats and lints Python
- Line length: **120 characters**
- Quote style: **double quotes**
- Target: **Python 3.12**
- Indent style: **spaces**

### Lint Rules

The project enables an extensive set of ruff lint rules:

| Rule Set | Category |
| --- | --- |
| E | pycodestyle errors |
| F | pyflakes |
| W | pycodestyle warnings |
| I | isort (import sorting) |
| UP | pyupgrade |
| B | flake8-bugbear |
| SIM | flake8-simplify |
| RUF | ruff-specific rules |
| C4 | flake8-comprehensions |
| DTZ | flake8-datetimez |
| T20 | flake8-print (no print statements in production code) |
| PTH | flake8-use-pathlib |
| TC | flake8-type-checking |
| RET | flake8-return |
| PLW | pylint warnings |
| PT | flake8-pytest-style |
| A | flake8-builtins |
| COM | flake8-commas |
| PIE | flake8-pie |
| ISC | flake8-implicit-str-concat |
| G | flake8-logging-format |
| INP | flake8-no-pep420 |
| TID | flake8-tidy-imports |
| ERA | eradicate (commented-out code) |

Notable ignored rules:

- **E501** - Line too long (the formatter handles wrapping at 120 chars)
- **COM812** - Missing trailing comma (conflicts with formatter)
- **ISC001** - Implicit string concatenation (conflicts with formatter)
- **TC001/TC002/TC003** - Type checking imports (would break runtime type resolution)
- **RET504/RET505** - Return-related simplifications (reduces debuggability)

### Import Order

Enforced by ruff/isort:

1. Standard library (`import logging`, `import json`)
2. Third-party packages (`import httpx`, `from pydantic import ...`)
3. Local imports (`from servicenow_mcp.client import ServiceNowClient`)

**Absolute imports only** - relative imports are not used.

## Type Annotations

All function signatures must have full type hints - enforced by mypy with `disallow_untyped_defs = true`.

### Rules

- Return types always explicit, including `-> None` for void functions
- Modern union syntax: `str | None` (not `Optional[str]`)
- Lowercase generic types (PEP 585): `dict[str, Any]`, `list[str]`, `set[str]`
- Primary typing import: `from typing import Any`
- Regex patterns typed as `re.Pattern[str]`

### mypy Configuration

```toml
[tool.mypy]
python_version = "3.12"
warn_return_any = false
warn_unused_configs = true
disallow_untyped_defs = true
ignore_missing_imports = true
```

The `servicenow_mcp.server` module has `call-arg` error code disabled for the dynamically imported tool registration calls.

## Naming Conventions

| Category | Convention | Examples |
| --- | --- | --- |
| Functions/methods/variables | `snake_case` | `check_table_access`, `query_store` |
| Classes | `PascalCase` | `ServiceNowClient`, `ChoiceRegistry` |
| Constants | `UPPER_SNAKE_CASE` | `DENIED_TABLES`, `MASK_VALUE`, `PACKAGE_REGISTRY` |
| Private | Single `_` prefix | `_table_url`, `_http_client`, `_ensure_client` |
| Logger | Module-level | `logger = logging.getLogger(__name__)` |
| Test classes | `Test` prefix + feature | `TestServiceNowClientGetRecord` |
| Test methods | `test_` prefix + descriptive | `test_get_record_success` |

## Testing

### Framework

- **pytest** with **pytest-asyncio** (`asyncio_mode = "auto"` - no manual event loop configuration needed)
- HTTP mocking: **respx** library with `@respx.mock` decorator on async test methods
- Coverage: **pytest-cov**, reports to **Codecov**
- Default addopts: `-m 'not integration' --cov=servicenow_mcp --cov-report=xml --cov-report=term-missing`

### Parsing Tool Output in Tests

All tool output is JSON. Parse with `json.loads()`:

```python
import json

raw = await tools["my_tool"](param="value")
result = json.loads(raw)
assert result["status"] == "success"
assert result["data"]["field"] == "expected"
```

### Standard Test Helper Pattern

```python
from mcp.server import MCPServer


async def _register_and_get_tool_schemas(settings, auth_provider):
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    return {tool.name: tool for tool in await mcp.list_tools()}
```

Test helpers can pass the optional registries needed by the tool under test. For example, a helper that supplies choices uses this pattern:

```python
async def _register_and_get_tool_schemas(settings, auth_provider, choices=None):
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {tool.name: tool for tool in await mcp.list_tools()}
```

For tool listing and schema checks, use the public asynchronous API:

```python
tools = await mcp.list_tools()
```

The private callable registry is reserved for tests that must directly invoke registered handlers. It is not the normal API for listing tools or checking schemas.

### Test Fixtures

Defined in `tests/conftest.py`:

| Fixture | Scope | Description |
| --- | --- | --- |
| `_disable_sentry_capture` | autouse | Resets Sentry `_initialized` flag to prevent real captures during tests |
| `settings` | per-test | Dev environment settings (`SERVICENOW_ENV=dev`) |
| `prod_settings` | per-test | Production environment settings (`SERVICENOW_ENV=prod`) |
| `prod_auth_provider` | per-test | `OAuthPKCEProvider` from production test settings |
| `_isolate_unit_settings` | autouse | Disables real dotenv loading outside integration tests |
| `_stub_user_authorization` | autouse | Stubs authorization except in authentication and integration tests |

The `settings` and `prod_settings` fixtures construct `Settings(_env_file=None)`
with an isolated environment. They do not use real dotenv credentials.

### Integration Tests

- Located in `tests/integration/`
- Marked with `@pytest.mark.integration`
- Excluded from default test runs (via `-m 'not integration'` addopts)
- Require public OAuth settings in `.env.local` or the process environment and
  interactive browser authorization on the same machine
- Run only against a non-production instance with suitable user permissions
- Run with: `uv run pytest -m integration`

## CI Pipeline

The CI workflow (`.github/workflows/ci.yml`) runs on every push to `main` and every pull request targeting `main`.

### Concurrency

Uses GitHub's concurrency groups with `cancel-in-progress: true` - new pushes cancel in-flight runs for the same branch.

### Jobs

Three parallel jobs run on `ubuntu-latest`:

#### 1. Lint

- Installs dependencies with `uv sync --group dev`
- Runs `uv run ruff check .` (lint rules)
- Runs `uv run ruff format --check .` (formatting verification)

#### 2. Type Check

- Installs dependencies with `uv sync --group dev`
- Runs `uv run mypy src/`

#### 3. Test (matrix: Python 3.12, 3.13, 3.14)

- Installs the target Python version via `uv python install`
- Installs dependencies with `uv sync --group dev --python ${{ matrix.python-version }}`
- Runs `uv run --python ${{ matrix.python-version }} pytest`
- Uploads coverage to Codecov on **Python 3.12 only**

All three jobs must pass before a PR can be merged.

## Release Process

Automated via **release-please** (`.github/workflows/release-please.yml`).

### How It Works

1. **Conventional commits** on `main` trigger release-please to create/update a release PR with a generated changelog
2. **Merging the release PR** creates a GitHub Release with the new tag and version
3. The **publish job** runs only when a release is created:
   - Checks out the code
   - Builds the package with `uv build`
   - Publishes to PyPI with `uv publish --token ${{ secrets.PYPI_TOKEN }}`

### Required Secrets

| Secret | Purpose |
| --- | --- |
| `RELEASE_PLEASE_TOKEN` | GitHub token for creating release PRs |
| `PYPI_TOKEN` | PyPI API token for package publishing |
| `CODECOV_TOKEN` | Codecov upload token |

### Commit Convention

Release-please uses conventional commits to determine version bumps:

| Prefix | Version Bump | Example |
| --- | --- | --- |
| `feat:` | Minor | `feat: add attachment upload tool` |
| `fix:` | Patch | `fix: handle empty query results` |
| `docs:` | None | `docs: update README` |
| `chore:` | None | `chore: update dependencies` |
| `refactor:` | None | `refactor: extract query builder` |
| `test:` | None | `test: add client error handling tests` |
| `feat!:` or `BREAKING CHANGE:` | Major | `feat!: remove deprecated API` |

## Git Workflow

- **Never** work directly on `main` - always use feature branches
- **Conventional commits** required (enforced by release-please)
- **Small, atomic commits** - each commit should do one thing
- **Use `gh` CLI** for GitHub operations (PRs, issues, etc.)
- **Never** commit code that breaks existing tests

## Project Dependencies

### Core Dependencies

| Package | Purpose |
| --- | --- |
| `mcp` (>=2.1.1) | MCP SDK v2 server framework |
| `httpx` (>=0.28.1) | Independent async HTTP client for ServiceNow REST API calls |
| `pydantic` (>=2.13.5) | Data validation |
| `pydantic-settings` (>=2.15.0) | Environment-based configuration |
| `python-dotenv` (>=1.2.2) | `.env` file loading |
| `uvicorn` (>=0.52.4) | Declared ASGI server dependency; the entry point uses stdio |
| `starlette` (>=1.6.0) | Declared ASGI framework dependency; the entry point uses stdio |
| `sentry-sdk` (>=2.68.1) | Error tracking |

### Dev Dependencies

| Package | Purpose |
| --- | --- |
| `pytest` (>=9.0.3) | Test framework |
| `pytest-asyncio` (>=1.3.0) | Async test support |
| `respx` (>=0.21.0) | httpx mocking |
| `ruff` (>=0.16.5) | Linter and formatter |
| `mypy` (>=2.3.1) | Type checker |
| `pytest-cov` (>=6.0.0) | Coverage reporting |
| `basedpyright` (>=1.39.9) | Alternative type checker |

### Build System

- **Build backend**: hatchling
- **Wheel packages**: `src/servicenow_mcp` (src-layout)
- **Entry point**: `servicenow-platform-mcp = servicenow_mcp.server:main`
