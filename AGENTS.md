# AGENTS.md - servicenow-devtools-mcp

## 📋 Project Overview

- Python 3.12+ async MCP server for ServiceNow schema access, record inspection, attachment operations, debugging, and change intelligence.
- Package manager: **uv** (not pip/poetry). Build system: hatchling.
- Source layout: `src/servicenow_mcp/` (src-layout). Entry point: `servicenow_mcp.server:main`.
- Config via `pydantic-settings` loading env vars from `.env` / `.env.local`.
- Version: 0.6.0. Supported Python: 3.12, 3.13, 3.14.

### Dependencies

| Type          | Packages                                                                   |
| ------------- | -------------------------------------------------------------------------- |
| Core          | `mcp`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `uvicorn`, `starlette` |
| Serialization | `toon-format` (external git dep from `github.com/toon-format/toon-python.git`) |
| Dev           | `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `pytest-cov`                      |

## 🚀 Setup

```bash
uv sync --group dev          # Install all deps including dev tools
cp .env.example .env.local   # Then fill in ServiceNow credentials
```

No build step needed for development. `uv build` creates the distribution wheel.

## 🔧 Lint / Format / Type-check

| Command                      | Purpose                                                                 |
| ---------------------------- | ----------------------------------------------------------------------- |
| `uv run ruff check .`          | Lint (rules: E, F, W, I, UP, B, SIM, RUF; E501 ignored)                 |
| `uv run ruff check --fix .`    | Auto-fix lint issues                                                    |
| `uv run ruff format .`         | Format code                                                             |
| `uv run ruff format --check .` | Verify formatting without changes                                       |
| `uv run mypy src/`             | Type checking (`disallow_untyped_defs=true`, `ignore_missing_imports=true`) |

mypy override: `servicenow_mcp.server` has `call-arg` error code disabled.

## 🧪 Test Commands

| Command                                                    | Purpose                                                        |
| ---------------------------------------------------------- | -------------------------------------------------------------- |
| `uv run pytest`                                              | All unit tests (integration excluded via `-m 'not integration'`) |
| `uv run pytest tests/test_client.py`                         | Single file                                                    |
| `uv run pytest tests/test_client.py::TestClass::test_method` | Single test                                                    |
| `uv run pytest -k "keyword"`                                 | Keyword match                                                  |
| `uv run pytest -m integration`                               | Integration tests (requires `.env.local`)                        |
| `uv run pytest --no-cov`                                     | Skip coverage for speed                                        |

- Default addopts: `-m 'not integration' --cov=servicenow_mcp --cov-report=xml --cov-report=term-missing`
- `asyncio_mode = "auto"` - no manual event loop configuration needed.
- **ALWAYS** test changes before considering a task complete; check console output for warnings/errors.

## 📐 Code Style & Formatting

- Formatter: **Ruff**, line length **120**, **double quotes**, target Python 3.12.
- Lint rules: E (pycodestyle errors), F (pyflakes), W (pycodestyle warnings), I (isort), UP (pyupgrade), B (flake8-bugbear), SIM (flake8-simplify), RUF (ruff-specific).
- E501 (line-too-long) is ignored - the formatter handles wrapping at 120 chars.
- Trailing commas in multi-line constructs (lists, dicts, function args).
- All files end with a single trailing newline.

## 📦 Import Conventions

- Order enforced by ruff/isort: **stdlib -> third-party -> local**.
- **Absolute imports only**: `from servicenow_mcp.client import ServiceNowClient`.
- No wildcard imports.

## 🏷 Type Annotations

- **ALL** function signatures must have full type hints (enforced by mypy `disallow_untyped_defs`).
- Return types always explicit, including `-> None` for void functions.
- Modern union syntax: `str | None` (not `Optional[str]`).
- Lowercase generic types (PEP 585): `dict[str, Any]`, `list[str]`, `set[str]`.
- Primary typing import: `from typing import Any`.
- Regex patterns typed as `re.Pattern[str]`.

## 🏷 Naming Conventions

| Category                    | Convention                 | Examples                                                            |
| --------------------------- | -------------------------- | ------------------------------------------------------------------- |
| Functions/methods/variables | `snake_case`                 | `check_table_access`, `query_store`                                     |
| Classes                     | `PascalCase`                 | `ServiceNowClient`, `BasicAuthProvider`, `ChoiceRegistry`                 |
| Constants                   | `UPPER_SNAKE_CASE`           | `DENIED_TABLES`, `MASK_VALUE`, `PACKAGE_REGISTRY`, `INVESTIGATION_REGISTRY` |
| Private                     | Single underscore `_` prefix | `_table_url`, `_http_client`, `_ensure_client`                            |
| Logger                      | Module-level               | `logger = logging.getLogger(__name__)`                                |
| Test classes                | `Test` prefix + feature      | `TestServiceNowClientGetRecord`, `TestTableDescribe`                    |
| Test methods                | `test_` prefix + descriptive | `test_get_record_success`                                             |

## 📝 Docstrings

- Every module starts with a module-level docstring: `"""Brief description."""`
- Classes and public functions have triple-double-quote docstrings.
- Tool functions use `Args:` section with indented param descriptions (MCP uses these for tool schemas).
- Fixtures have one-line docstrings explaining their purpose.

## ⚠️ Error Handling

Custom exception hierarchy in `errors.py`:

```text
ServiceNowMCPError(Exception)     # Root; has status_code attribute
  ├── AuthError                   # 401
  ├── ForbiddenError              # 403
  ├── NotFoundError               # 404
  ├── ServerError                 # 5xx
  └── PolicyError                 # 403
        └── QuerySafetyError      # 403
```

HTTP status mapping in `client.py:_raise_for_status()`:

| HTTP Status  | Exception          |
| ------------ | ------------------ |
| 401          | `AuthError`          |
| 403          | `ForbiddenError`     |
| 404          | `NotFoundError`      |
| 500+         | `ServerError`        |
| 400+ (other) | `ServiceNowMCPError` |

### Write Gating (Function-Based - No Exception Class)

Write gating uses a function-based approach. There is **no** `WriteGatingError` class.

```python
from servicenow_mcp.policy import write_gate, can_write, write_blocked_reason

# In tool functions - returns error envelope string if blocked, None if allowed
gate = write_gate("incident", settings, correlation_id)
if gate:
    return gate  # Already a serialized error response

# Boolean check
if can_write("incident", settings, override=False):
    ...

# Get human-readable reason (checks denied tables + is_production)
reason = write_blocked_reason("incident", settings)
```

### Tool Error Safety

**Tool functions never raise to MCP.** The `@tool_handler` decorator combined with `safe_tool_call()` catches all exceptions and returns serialized error envelopes automatically. Manual try/except blocks are NOT needed in tool functions.

## 🎯 @tool_handler Decorator - THE CENTRAL PATTERN

This is the most important pattern in the codebase. Located in `decorators.py`.

```python
@mcp.tool()
@tool_handler
async def my_tool(param: str, correlation_id: str = "") -> str:
    # correlation_id is auto-injected, never passed by MCP caller
    ...
    return format_response(data=result, correlation_id=correlation_id)
```

What `@tool_handler` does:

1. Auto-generates `correlation_id` via `generate_correlation_id()` (UUID4).
2. Wraps the function call in `safe_tool_call()` which catches `ForbiddenError` and `Exception`, returning serialized error envelopes.
3. Hides `correlation_id` from the FastMCP tool schema by overriding `__signature__` and deleting `__wrapped__`.
4. Tool functions do **NOT** need manual try/except blocks.

## 📄 TOON Serialization

All tool output uses **TOON format**, not raw JSON. This is a critical difference from typical MCP servers.

- `toon-format` is an external dep from git: `github.com/toon-format/toon-python.git`
- `utils.py:serialize(data)` uses `toon_encode()` with JSON fallback
- `format_response()` returns a serialized TOON string (not a dict) - it calls `serialize()` internally
- Error strings are wrapped as `{"message": error}` dicts before serialization

### Parsing Tool Output

```python
# CORRECT - use toon_decode
from toon_format import decode as toon_decode
result = toon_decode(raw_output)

# WRONG - do NOT use json.loads
import json
result = json.loads(raw_output)  # This will fail on TOON-formatted output
```

## 📊 Response Format

All tools return a serialized TOON string via `format_response()`:

```python
format_response(
    data=...,               # Any serializable data
    correlation_id=...,     # Auto-injected by @tool_handler
    status="success",       # "success" or "error"
    error=None,             # str | dict | None
    pagination=None,        # dict | None
    warnings=None,          # list | None
) -> str                    # Returns serialized TOON string
```

Error response example:

```python
return format_response(data=None, correlation_id=correlation_id, status="error", error="Something failed")
```

### Attachment Payloads

- Attachment tool set: `attachment_list`, `attachment_get`, `attachment_download`, `attachment_download_by_name`, `attachment_upload`, `attachment_delete`.
- `attachment_upload` accepts `content_base64` and decodes it before upload.
- `attachment_download` and `attachment_download_by_name` return attachment metadata plus `content_base64`.
- Attachment transfers are limited by `MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024`.

## 🛡 Policy Layer

### Table Access

- `check_table_access(table)` - raises `PolicyError` for denied tables.
- 8 denied tables: `sys_user_has_role`, `sys_user_grmember`, `sys_user_group`, `sys_user_role`, `sys_security_acl`, `sys_security_acl_role`, `sys_glide_object`, `sys_db_object`.

### Field Sensitivity

- `is_sensitive_field(field_name) -> bool` - 6 regex patterns
- `mask_sensitive_fields(record) -> dict` - masks values with `MASK_VALUE = '***MASKED***'`
- `mask_audit_entry(entry) -> dict` - separate masking for `sys_audit` records

### Query Safety

- `enforce_query_safety(table, query, limit, settings) -> dict` - returns dict with `limit` key, raises `QuerySafetyError`
- `validate_identifier(name)` - regex `^[a-z0-9_]+(\.[a-z0-9_]+)*$` for field/table names
- `INTERNAL_QUERY_LIMIT = 1000`

## 🔑 State Management

Two token store classes built on a common base:

```text
_BaseTokenStore(ttl_seconds=300, max_size=1000)
  ├── PreviewTokenStore    # Single-use tokens (has consume() method)
  └── QueryTokenStore      # Reusable tokens (no consume method)
```

- `create(payload) -> str` - stores data, returns UUID key
- `get(token) -> dict | None` - retrieves data (both stores)
- `consume(token) -> dict | None` - retrieves and deletes (PreviewTokenStore only)
- `_sweep_expired()` - TTL-based cleanup

**Note:** There is no `SeededRecordTracker` in this codebase.

## 🔗 build_query + QueryTokenStore Workflow

The `build_query` tool creates structured queries and stores them in `QueryTokenStore`. Other tools receive the `query_token` and resolve it via:

```python
resolved_query = resolve_query_token(token, store, correlation_id)
# Returns the encoded query string
```

This bridges structured query building with query-consuming tools, allowing complex queries to be built once and reused across multiple tool calls.

## 🏗 Tool Registration

### Standard Tools

```python
def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    query_store = mcp._sn_query_store  # Access shared state from FastMCP instance

    @mcp.tool()
    @tool_handler
    async def tool_name(param: str, correlation_id: str = "") -> str:
        validate_identifier(param)
        check_table_access(param)
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.some_method(param)
        return format_response(data=result, correlation_id=correlation_id)
```

### Domain Tools (extra `choices` kwarg)

Domain tool modules (with `domain_` prefix) receive an additional `choices` parameter:

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    @mcp.tool()
    @tool_handler
    async def incident_list(state: str = "", correlation_id: str = "") -> str:
        # Use choices.resolve() for label-to-value mapping
        if state and choices:
            state = await choices.resolve("incident", "state", state)
        # Use ServiceNowQuery fluent builder
        query = ServiceNowQuery()
        if state:
            query.field("state").equals(state)
        ...
```

### Write Operations in Domain Tools

```python
# Always check write_gate before mutations
gate = write_gate("incident", settings, correlation_id)
if gate:
    return gate  # Pre-formatted error envelope
```

## 🔄 ChoiceRegistry

- `ChoiceRegistry(settings, auth_provider)` - lazy-loaded from `sys_choice` table.
- Uses `asyncio.Lock` with double-check pattern on first access.
- Falls back to `_DEFAULTS` on fetch failure.
- Labels normalized: lowercase, spaces to underscores.

### 6 Default Mappings

| Table          | Field              |
| -------------- | ------------------ |
| `incident`       | `state`              |
| `change_request` | `state`              |
| `problem`        | `state`              |
| `cmdb_ci`        | `operational_status` |
| `sc_request`     | `state`              |
| `sc_req_item`    | `state`              |

### API

```python
await choices.resolve("incident", "state", "open")  # Returns mapped value or passthrough
choices.get_choices("incident", "state")             # Returns dict of all choices
```

## 🔍 Investigation Modules

Located in `investigations/*.py`, registered in `INVESTIGATION_REGISTRY`.

### 7 Available Investigations

| Investigation           | Purpose                                  |
| ----------------------- | ---------------------------------------- |
| `stale_automations`       | Find unused or stale automation rules    |
| `deprecated_apis`         | Detect deprecated API usage              |
| `table_health`            | Analyze table structure and data quality |
| `acl_conflicts`           | Find conflicting ACL rules               |
| `error_analysis`          | Analyze error patterns                   |
| `slow_transactions`       | Identify slow-running transactions       |
| `performance_bottlenecks` | Find performance issues                  |

### Module Contract

Each module exports:

```python
async def run(client, params) -> dict:
    ...

async def explain(client, element_id) -> dict:
    ...
```

### Shared Helpers (`investigation_helpers.py`)

| Helper                                                                           | Purpose                        |
| -------------------------------------------------------------------------------- | ------------------------------ |
| `parse_int_param(params, key, default) -> int`                                     | Safe integer parameter parsing |
| `parse_element_id(element_id, allowed_tables) -> tuple[str, str]`                  | Parse `'table:sys_id'` format    |
| `build_investigation_result(name, findings, **extra) -> dict`                      | Standard result envelope       |
| `fetch_and_explain(client, element_id, allowed_tables, build_explanation) -> dict` | Common explain pattern         |

## 🖥 Server Bootstrap

`create_mcp_server()` performs the following:

1. Creates `Settings` and auth via `create_auth()`.
2. Creates `FastMCP('servicenow-dev-debug')`.
3. Attaches shared state to the FastMCP instance:
   - `mcp._sn_settings` - Settings instance
   - `mcp._sn_auth` - Auth provider
   - `mcp._sn_query_store` - `QueryTokenStore` instance
   - `mcp._sn_choices` - `ChoiceRegistry` instance
4. Always registers the `list_tool_packages` tool.
5. Loads tool groups via `importlib`; `domain_` prefix modules get `choices=choices` kwarg.
6. `main()` runs with stdio transport.

## 🌐 Client

- `ServiceNowClient(settings, auth_provider)` - async context manager.
- `_ensure_client() -> httpx.AsyncClient` - raises `RuntimeError` if not initialized (not assert).
- Timeout: 30s.
- Uses `validate_identifier()` for table/field names.
- 30+ API methods covering: records, attachments, metadata, aggregation, CRUD, CMDB, email, import sets, reports, code search, service catalog, ATF.

## ⚙️ Configuration (Settings)

| Field                   | Type      | Default                                              | Env Var                 |
| ----------------------- | --------- | ---------------------------------------------------- | ----------------------- |
| `servicenow_instance_url` | `str`       | required                                             | `SERVICENOW_INSTANCE_URL` |
| `servicenow_username`     | `str`       | required                                             | `SERVICENOW_USERNAME`     |
| `servicenow_password`     | `SecretStr` | required                                             | `SERVICENOW_PASSWORD`     |
| `mcp_tool_package`        | `str`       | `"full"`                                               | `MCP_TOOL_PACKAGE`        |
| `servicenow_env`          | `str`       | `"dev"`                                                | `SERVICENOW_ENV`          |
| `max_row_limit`           | `int`       | `100` (range 1-10000)                                  | `MAX_ROW_LIMIT`           |
| `large_table_names_csv`   | `str`       | `"syslog,sys_audit,sys_log_transaction,sys_email_log"` | `LARGE_TABLE_NAMES_CSV`   |

### Computed Properties

- `large_table_names` - `@cached_property`, returns `frozenset` from CSV
- `is_production` - `@property`, checks if `servicenow_env` contains `'prod'` or `'production'`

### Validators

- `instance_url` must start with `https://`, trailing slash stripped.
- `mcp_tool_package` validated against `get_package()`.
- `model_config`: `env_file=['.env', '.env.local']`, `extra='ignore'`.

## 📦 Packages & Tool Groups

14 named packages with 19 tool groups total (20 registered modules, but `testing` is disabled in `full`).

### Preset Packages

| Package              | Groups | Description                                     |
| -------------------- | ------ | ----------------------------------------------- |
| `full`                 | 19     | Default - all standard tool groups              |
| `core_readonly`        | 4      | Read-only core tools (table, record, attachment, metadata) |
| `none`                 | 0      | No tools loaded                                 |
| `itil`                 | 15     | ITIL process tools                              |
| `developer`            | 12     | Development-focused tools                       |
| `readonly`             | 10     | Read-only operations                            |
| `analyst`              | 8      | Analysis and reporting                          |
| `incident_management`  | 9      | Incident lifecycle tools                        |
| `change_management`    | 8      | Change request tools                            |
| `cmdb`                 | 6      | CMDB management tools                           |
| `problem_management`   | 9      | Problem lifecycle tools                         |
| `request_management`   | 8      | Request/RITM tools                              |
| `knowledge_management` | 6      | Knowledge base tools                            |
| `service_catalog`      | 6      | Service catalog tools                           |

### Custom Packages

Comma-separated group names are supported as a custom package value. Validated to ensure no collisions with preset package names.

Readonly-style packages include only `attachment`. Write-capable packages include both `attachment` and `attachment_write`.

## 🔀 Async Patterns

- All ServiceNow API calls are `async`.
- `ServiceNowClient` is an async context manager: `async with ServiceNowClient(settings, auth_provider) as client:`.
- Auth `get_headers()` is async for extensibility.
- Tests use `@pytest.mark.asyncio` decorator (`asyncio_mode` is auto).

## 🧪 Testing Patterns

### HTTP Mocking

Use **respx** library with `@respx.mock` decorator on async test methods.

### Fixtures

- `tests/conftest.py` provides `settings` and `prod_settings` using `patch.dict("os.environ", ...)`.
- `tests/domains/conftest.py` provides domain-specific fixtures (same pattern, separate scope).
- Always construct `Settings(_env_file=None)` in tests to avoid loading real env files.

### Standard Tool Test Helper

```python
def _register_and_get_tools(settings, auth_provider):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

### Domain Tool Test Helper

```python
def _register_and_get_tools(settings, auth_provider, choices=None):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

### Parsing Tool Output in Tests

```python
from toon_format import decode as toon_decode

raw = await tools["my_tool"](param="value")
result = toon_decode(raw)
assert result["status"] == "success"
assert result["data"]["field"] == "expected"
```

**Critical:** Use `toon_decode(raw)`, **NOT** `json.loads(raw)`.

### Integration Tests

- Located in `tests/integration/`.
- Marked with `@pytest.mark.integration`.
- Require `.env.local` with real ServiceNow credentials.
- Fixtures are session-scoped: `live_settings`, `live_auth`, discovered sys_ids.

## 🌿 Git Workflow

- **NEVER** work on main/master - always create feature branches.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, etc.
- **Small commits** - atomic, focused changes.
- **release-please** automates versioning and releases on push to main.
- **Use `gh` CLI** for all GitHub operations (PRs, issues, etc.).
- CI runs lint, type-check, and tests on Python 3.12/3.13/3.14 for every PR.
- Always test changes before considering a task complete.
- Always check console output during runs and fix any errors/warnings.
