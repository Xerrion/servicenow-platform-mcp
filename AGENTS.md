# AGENTS.md - servicenow-platform-mcp

## 📋 Project Overview

- Python 3.12+ async MCP server for ServiceNow schema access, record inspection, attachment operations, debugging, and change intelligence.
- Package manager: **uv** (not pip/poetry). Build system: hatchling.
- Source layout: `src/servicenow_mcp/` (src-layout). Entry point: `servicenow_mcp.server:main`.
- Config via `pydantic-settings` loading env vars from `.env` / `.env.local`.
- Version: 0.10.0. Supported Python: 3.12, 3.13, 3.14.

### Dependencies

| Type          | Packages                                                                   |
| ------------- | -------------------------------------------------------------------------- |
| Core          | `mcp`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `uvicorn`, `starlette` |
| Sentry        | `sentry-sdk>=2.55.0`                                                              |
| Dev           | `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`, `basedpyright`, `pytest-cov`          |

## 🚀 Setup

```bash
uv sync --group dev          # Install all deps including dev tools
cp .env.example .env.local   # Then fill in ServiceNow credentials
```

No build step needed for development. `uv build` creates the distribution wheel.

## 🔧 Lint / Format / Type-check

| Command                      | Purpose                                                                 |
| ---------------------------- | ----------------------------------------------------------------------- |
| `uv run ruff check .`          | Lint (rules: E, F, W, I, UP, B, SIM, RUF, C4, DTZ, T20, PTH, TC, RET, PLW, PT, A, COM, PIE, ISC, G, INP, TID, ERA; E501/COM812/ISC001/TC001-3/RET504-5 ignored) |
| `uv run ruff check --fix .`    | Auto-fix lint issues                                                    |
| `uv run ruff format .`         | Format code                                                             |
| `uv run ruff format --check .` | Verify formatting without changes                                       |
| `uv run mypy src/`             | Type checking (`disallow_untyped_defs=true`, `ignore_missing_imports=true`) |

mypy override: `servicenow_mcp.server` has `call-arg` error code disabled.

## 🧪 Test Commands

| Command                                                    | Purpose                                                        |
| ---------------------------------------------------------- | -------------------------------------------------------------- |
| `uv run pytest`                                              | All unit tests (integration excluded via `-m 'not integration'`) |
| `uv run pytest tests/unified/`                               | All unified tool tests                                         |
| `uv run pytest tests/test_client.py`                         | Single file                                                    |
| `uv run pytest tests/test_client.py::TestClass::test_method` | Single test                                                    |
| `uv run pytest -k "keyword"`                                 | Keyword match                                                  |
| `uv run pytest -m integration`                               | Integration tests (requires `.env.local`)                        |
| `uv run pytest --no-cov`                                     | Skip coverage for speed                                        |

- Default addopts: `-m 'not integration' --cov=servicenow_mcp --cov-report=xml --cov-report=term-missing`
- `asyncio_mode = "auto"` - no manual event loop configuration needed.
- **ALWAYS** test changes before considering a task complete; check console output for warnings/errors.
- `tests/test_packages.py` contains the `test_domain_groups_not_in_unified_registry` guard.

## 📐 Code Style & Formatting

- Formatter: **Ruff**, line length **120**, **double quotes**, target Python 3.12.
- Lint rules: E, F, W, I, UP, B, SIM, RUF, C4, DTZ, T20, PTH, TC, RET, PLW, PT, A, COM, PIE, ISC, G, INP, TID, ERA. Ignored: E501, COM812, ISC001, TC001-TC003, RET504, RET505.
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
| Functions/methods/variables | `snake_case`                 | `check_table_access`, `gate_write`                                      |
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

When Sentry is active, `safe_tool_call()` also calls `sentry_capture(e)` to capture exceptions before returning the error envelope.

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
4. Sets Sentry tags (`tool.name`, `tool.correlation_id`) and context with tool name, correlation_id, and args.

## 📊 Response Format

All tools return a serialized JSON string via `format_response()`:

```python
format_response(
    data=...,               # Any serializable data
    correlation_id=...,     # Auto-injected by @tool_handler
    status="success",       # "success" or "error"
    error=None,             # str | dict | None
    pagination=None,        # dict | None
    warnings=None,          # list | None
) -> str                    # Returns serialized JSON string
```

Error response example:

```python
return format_response(data=None, correlation_id=correlation_id, status="error", error="Something failed")
```

## 🛡 Policy Layer

### Table Access

- `check_table_access(table)` - raises `PolicyError` for denied tables.
- 8 denied tables: `sys_user_has_password`, `oauth_credential`, `oauth_entity`, `sys_certificate`, `sys_ssh_key`, `sys_credentials`, `discovery_credentials`, `sys_user_token`.

### Field Sensitivity

- `is_sensitive_field(field_name) -> bool` - 6 regex patterns
- `mask_record(table, record) -> dict` - masks values with `MASK_VALUE = '***MASKED***'`
- `mask_audit_entry(entry) -> dict` - separate masking for `sys_audit` records

### Query Safety

- `enforce_query_safety(table, query, limit, settings) -> dict` - returns dict with `limit` key, raises `QuerySafetyError`
- `validate_identifier(name)` - regex `^[a-z0-9_]+(\.[a-z0-9_]+)*$` for field/table names
- `INTERNAL_QUERY_LIMIT = 1000`

## 🔑 State Management

Only the `PreviewTokenStore` remains for staging write operations:

```text
_BaseTokenStore(ttl_seconds=300, max_size=1000)
  └── PreviewTokenStore    # Single-use tokens (has consume() method)
```

- `create(payload) -> str` - stores data, returns UUID key
- `get(token) -> dict | None` - retrieves data
- `consume(token) -> dict | None` - retrieves and deletes (PreviewTokenStore only)
- `_sweep_expired()` - TTL-based cleanup

## 🔍 Encoded Queries

Agents pass ServiceNow encoded query strings directly to the `query` tool. Refer to `docs/agent-recipes.md` for the encoded query cheat sheet and worked examples of how the unified surface composes.

## 🏗 Tool Registration

The server bootstrap uses an unconditional 4-argument registration pattern for all tool groups.

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    # Modules that do not require the ChoiceRegistry explicitly ignore it
    del choices  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def tool_name(param: str, correlation_id: str = "") -> str:
        validate_identifier(param)
        check_table_access(param)
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.some_method(param)
        return format_response(data=result, correlation_id=correlation_id)
```

## ✏️ Platform Artifacts

Artifacts (Business Rules, Script Includes, etc.) are written via the `record_write` tool by setting the `artifact_type` parameter.

### WRITABLE_ARTIFACT_TABLES (24 types)

| Artifact Type | ServiceNow Table |
|---|---|
| `business_rule` | `sys_script` |
| `script_include` | `sys_script_include` |
| `ui_policy` | `sys_ui_policy` |
| `ui_action` | `sys_ui_action` |
| `client_script` | `sys_script_client` |
| `scheduled_job` | `sysauto_script` |
| `fix_script` | `sys_script_fix` |
| `scripted_rest_resource` | `sys_ws_operation` |
| `ui_script` | `sys_ui_script` |
| `processor` | `sys_processor` |
| `widget` | `sp_widget` |
| `ui_page` | `sys_ui_page` |
| `ui_macro` | `sys_ui_macro` |
| `script_action` | `sysevent_script_action` |
| `mid_script_include` | `ecc_agent_script_include` |
| `notification_script` | `sysevent_email_action` |
| `email_script` | `sys_script_email` |
| `catalog_client_script` | `catalog_script_client` |
| `catalog_ui_policy` | `catalog_ui_policy` |
| `transform_map_script` | `sys_transform_script` |
| `transform_entry_script` | `sys_transform_entry` |
| `acl` | `sys_security_acl` |
| `dynamic_filter` | `sys_filter_option_dynamic` |
| `decision_question` | `sys_decision_question` |

Discover the catalog at runtime via `describe(action='list_artifact_types')`, which returns the table, `script_fields` list, and `primary_field` per artifact type.

### script_path Security

When `artifact_type` is set, `record_write` accepts an optional `script_path`:

- Path is resolved via `Path.resolve(strict=True)` to prevent symlink/traversal attacks.
- The resolved path must be under the directory defined by the `script_allowed_root` setting.
- File is read as UTF-8; maximum size is 1 MB (`MAX_SCRIPT_FILE_BYTES`).
- Content is written to the primary script field for the artifact type (`SCRIPT_FIELD_MAP[artifact_type][0]`), unless `script_field` overrides it.
- For `ui_macro`, the content is validated as well-formed XML (`xml.etree.ElementTree.fromstring`) before any platform call; malformed content yields a structured error.
- `record_write` uses the `PreviewTokenStore` flow (preview/apply) by default for these operations.

### script_field parameter

`record_write` accepts an optional `script_field` parameter when `artifact_type` and `script_path` are set. It selects which script-bearing field receives the file contents:

- Empty (default): writes to the primary field (`SCRIPT_FIELD_MAP[artifact_type][0]`).
- Non-empty: must be one of `SCRIPT_FIELD_MAP[artifact_type]`; otherwise the call returns a structured error listing the allowed fields.
- Setting `script_field` without `artifact_type` is rejected.

### SCRIPT_FIELD_MAP

`SCRIPT_FIELD_MAP` is `dict[str, list[str]]`. Index 0 is the primary field (the default target); subsequent entries are alternate script-bearing fields callable via `script_field`. Every entry in `WRITABLE_ARTIFACT_TABLES` has a corresponding non-empty list; a module-load assertion enforces this.

| Artifact Type | Script Fields (primary first) |
|---|---|
| `business_rule` | `script`, `condition` |
| `script_include` | `script` |
| `ui_action` | `script`, `condition`, `onclick` |
| `client_script` | `script` |
| `scheduled_job` | `script` |
| `fix_script` | `script` |
| `ui_script` | `script` |
| `processor` | `script` |
| `script_action` | `script` |
| `mid_script_include` | `script` |
| `ui_policy` | `script_true`, `script_false` |
| `widget` | `client_script`, `script`, `template`, `css`, `link` |
| `ui_page` | `html`, `client_script`, `processing_script` |
| `scripted_rest_resource` | `operation_script` |
| `ui_macro` | `xml` |
| `notification_script` | `advanced_condition` |
| `email_script` | `script` |
| `catalog_client_script` | `script` |
| `catalog_ui_policy` | `script_true`, `script_false` |
| `transform_map_script` | `script` |
| `transform_entry_script` | `script` |
| `acl` | `script` |
| `dynamic_filter` | `script` |
| `decision_question` | `condition_script` |

### record_read

Read-only counterpart for the artifact surface. `record_read(artifact_type, sys_id=..., name=...)` returns the masked record plus the `script_fields` list for the artifact type, enabling discovery-driven multi-field edits. Exactly one of `sys_id` or `name` must be supplied; ambiguous names (>1 match) and missing records return structured errors. Included in both `full` and `readonly` packages.

## 🔄 ChoiceRegistry

- `ChoiceRegistry(settings, auth_provider)` - lazy-loaded from `sys_choice` table.
- Exposed as the `resolve_choice` tool for agents to map labels to values.
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

## 🔍 Investigation Modules

Dispatched via the `investigate` tool with `action='run'` or `action='explain'`. The `explain` action trial-dispatches across registered modules. Bare-table forms are not supported.

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

## 🖥 Server Bootstrap

`create_mcp_server()` performs the following:

1. Creates `Settings` and auth via `create_auth()`.
2. Calls `setup_sentry(settings)` and sets Sentry context.
3. Creates `FastMCP('servicenow-platform-mcp')`.
4. Calls `attach_servicenow_state(...)` to attach shared state.
5. Always registers the `list_tool_packages` tool.
6. Loads tool groups via `importlib` and calls an unconditional 4-argument `register_tools(...)`.
7. `main()` runs with stdio transport; `shutdown_sentry()` is called in a finally block.

## 🌐 Client

- `ServiceNowClient(settings, auth_provider)` - async context manager.
- ATF methods have been deleted.
- Retains `list_reports`, `get_email`, `get_import_set_record`, and `sc_*` methods per ADR §2.3.

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
| `script_allowed_root`     | `str`       | `""`                                                     | `SCRIPT_ALLOWED_ROOT`     |
| `sentry_dsn`              | `str`       | `""`                                                     | `SENTRY_DSN`              |
| `sentry_environment`      | `str`       | `""`                                                     | `SENTRY_ENVIRONMENT`      |

## 📦 Packages & Tool Groups

The registry contains 4 preset packages. Tool groups are loaded from `servicenow_mcp.tools.unified.*`.

### Preset Packages

| Package | Tools | Description |
|---|---|---|
| `full` | 11 | Every group (full surface, includes `build_query`) |
| `readonly` | 7 | Read tools + investigate + resolve_choice |
| `core_readonly` | 5 | Query + describe + attachment only |
| `none` | 1 | Only `list_tool_packages` loaded |

- **Custom Packages:** Comma-separated group names are supported (e.g., `MCP_TOOL_PACKAGE=query,describe`).
- **Tool Group Shadowing:** `MCP_TOOL_PACKAGE=service_catalog` resolves via the custom-package path to the single tool group.
- **Write Gating:** The `attachment` group registers both read and write tools; write tools are blocked at runtime in production by `write_gate`.
- **`build_query`:** Stateless helper that complements `query`. Accepts a JSON array of condition objects and returns the encoded query string in `data.query` for the caller to pass straight to `query`. `full` package only - the readonly presets pass encoded queries to `query` directly.

## 🔀 Async Patterns

- All ServiceNow API calls are `async`.
- `ServiceNowClient` is an async context manager: `async with ServiceNowClient(settings, auth_provider) as client:`.
- Auth `get_headers()` is async for extensibility.
- Tests use `@pytest.mark.asyncio` decorator (`asyncio_mode` is auto).

## 🔒 Sentry Module

Opt-in error tracking. `tool.name` tag values were updated in v0.10.0 to reflect the unified tool surface.

### Key Functions

| Function | Purpose |
|---|---|
| `setup_sentry(settings)` | Initializes Sentry SDK |
| `capture_exception(exc)` | Captures exception |
| `set_sentry_tag(key, value)` | Sets indexed tag |
| `set_sentry_context(key, data)` | Sets structured context |
| `shutdown_sentry()` | Flushes and closes client |

## 🧪 Testing Patterns

### HTTP Mocking

Use **respx** library with `@respx.mock` decorator on async test methods.

### Unified Tool Test Helpers

Unified tool tests are located in `tests/unified/`. Standard tool registration for tests uses the 4-argument signature:

```python
def _register_and_get_tools(settings, auth_provider, choices=None):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}
```

### Parsing Tool Output in Tests

```python
import json

raw = await tools["query"](table="incident")
result = json.loads(raw)
assert result["status"] == "success"
```

### Integration Tests

- Located in `tests/integration/`.
- Marked with `@pytest.mark.integration`.
- Require `.env.local` with real ServiceNow credentials.
- Fixtures are session-scoped: `live_settings`, `live_auth`, discovered sys_ids.

## 🌿 Git Workflow

- **NEVER** work on main/master - always create feature branches.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, etc.
- **Small commits** - atomic, focused changes.
- CI runs lint, type-check, and tests on Python 3.12/3.13/3.14 for every PR.
- Always test changes before considering a task complete.
- Always check console output during runs and fix any errors/warnings.
