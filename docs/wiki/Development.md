# Development

Comprehensive development guide for contributing to `servicenow-platform-mcp`.

See also: [[Architecture]] for technical internals, [[Telemetry]] for observability.

## Getting Started

### Prerequisites

- **Python 3.12+** (tested on 3.12, 3.13, 3.14)
- **uv** - Fast Python package manager (not pip/poetry)
- A ServiceNow instance with admin or developer credentials

### Setup

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
cp .env.example .env.local  # Fill in ServiceNow credentials
```

The `.env.local` file needs at minimum:

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=your-password
```

No build step is required for development. The server runs directly from source via `uv run servicenow-platform-mcp`.

## Development Commands

| Command | Purpose |
|---|---|
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

- **Ruff** is the sole formatter and linter
- Line length: **120 characters**
- Quote style: **double quotes**
- Target: **Python 3.12**
- Indent style: **spaces**

### Lint Rules

The project enables an extensive set of ruff lint rules:

| Rule Set | Category |
|---|---|
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

The `servicenow_mcp.server` module has `call-arg` error code disabled due to FastMCP's dynamic tool registration.

## Naming Conventions

| Category | Convention | Examples |
|---|---|---|
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

All tool output uses TOON format. Always parse with `toon_decode()`:

```python
from toon_format import decode as toon_decode

raw = await tools["my_tool"](param="value")
result = toon_decode(raw)
assert result["status"] == "success"
assert result["data"]["field"] == "expected"
```

**Never** use `json.loads()` to parse tool output - it will fail on TOON-formatted strings.

### Standard Test Helper Pattern

```python
def _register_and_get_tools(settings, auth_provider):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

Domain tools use the same pattern with an extra `choices` parameter:

```python
def _register_and_get_tools(settings, auth_provider, choices=None):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

### Test Fixtures

Defined in `tests/conftest.py`:

| Fixture | Scope | Description |
|---|---|---|
| `_disable_sentry_capture` | autouse | Resets Sentry `_initialized` flag to prevent real captures during tests |
| `settings` | per-test | Dev environment settings (`SERVICENOW_ENV=dev`) |
| `prod_settings` | per-test | Production environment settings (`SERVICENOW_ENV=prod`) |
| `prod_auth_provider` | per-test | `BasicAuthProvider` from production settings |

All fixtures construct `Settings(_env_file=None)` with `patch.dict("os.environ", ...)` to avoid loading real env files.

### Integration Tests

- Located in `tests/integration/`
- Marked with `@pytest.mark.integration`
- Excluded from default test runs (via `-m 'not integration'` addopts)
- Require `.env.local` with real ServiceNow credentials
- Run with: `uv run pytest -m integration`

## CI Pipeline

The CI workflow (`.github/workflows/ci.yml`) runs on every push to `main` and every pull request targeting `main`.

### Concurrency

Uses GitHub's concurrency groups with `cancel-in-progress: true` - new pushes cancel in-flight runs for the same branch.

### Jobs

Three parallel jobs run on `ubuntu-latest`:

**1. Lint**
- Installs dependencies with `uv sync --group dev`
- Runs `uv run ruff check .` (lint rules)
- Runs `uv run ruff format --check .` (formatting verification)

**2. Type Check**
- Installs dependencies with `uv sync --group dev`
- Runs `uv run mypy src/`

**3. Test** (matrix: Python 3.12, 3.13, 3.14)
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
|---|---|
| `RELEASE_PLEASE_TOKEN` | GitHub token for creating release PRs |
| `PYPI_TOKEN` | PyPI API token for package publishing |
| `CODECOV_TOKEN` | Codecov upload token |

### Commit Convention

Release-please uses conventional commits to determine version bumps:

| Prefix | Version Bump | Example |
|---|---|---|
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
|---|---|
| `mcp` (>=1.0.0) | MCP server framework |
| `httpx` (>=0.27.0) | Async HTTP client |
| `pydantic` (>=2.0.0) | Data validation |
| `pydantic-settings` (>=2.0.0) | Environment-based configuration |
| `python-dotenv` (>=1.0.0) | `.env` file loading |
| `uvicorn` (>=0.30.0) | ASGI server (SSE transport) |
| `starlette` (>=0.38.0) | ASGI framework (SSE transport) |
| `toon-format` | LLM-optimized serialization (git dependency) |
| `sentry-sdk` (>=2.55.0) | Error tracking |

**Note:** `toon-format` is an external git dependency sourced from `https://github.com/toon-format/toon-python.git`. It is not available on PyPI.

### Dev Dependencies

| Package | Purpose |
|---|---|
| `pytest` (>=8.0.0) | Test framework |
| `pytest-asyncio` (>=0.24.0) | Async test support |
| `respx` (>=0.21.0) | httpx mocking |
| `ruff` (>=0.9.0) | Linter and formatter |
| `mypy` (>=1.14.0) | Type checker |
| `pytest-cov` (>=6.0.0) | Coverage reporting |
| `basedpyright` (>=1.29.0) | Alternative type checker |

### Build System

- **Build backend**: hatchling
- **Wheel packages**: `src/servicenow_mcp` (src-layout)
- **Entry point**: `servicenow-platform-mcp = servicenow_mcp.server:main`
