# AGENTS.md - servicenow-platform-mcp

## 📋 Project Overview

- Python 3.12+ async MCP server for ServiceNow schema access, record inspection, attachment operations, debugging, and change intelligence.
- Package manager: **uv** (not pip/poetry). Build system: hatchling.
- Source layout: `src/servicenow_mcp/` (src-layout). Entry point: `servicenow_mcp.server:main`.
- Config via `pydantic-settings` loading env vars from `.env` / `.env.local`.
- The MCP dependency floor is `mcp>=2.1.1`. MCP SDK v2 uses `MCPServer` from `mcp.server`; direct `httpx` remains an independent application dependency, while MCP's `httpx2` is transitive.
- Version: 0.10.0. Supported Python: 3.12, 3.13, 3.14.

### Dependencies

| Type          | Packages                                                                   |
| ------------- | -------------------------------------------------------------------------- |
| Core          | `mcp>=2.1.1`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `uvicorn`, `starlette` |
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
3. Hides `correlation_id` from the MCPServer tool schema by overriding `__signature__` and deleting `__wrapped__`.
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

The server bootstrap uses one registration signature for all tool groups.

```python
from mcp.server import MCPServer


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    # Modules that do not require the registries explicitly ignore them
    del choices, dictionary  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def tool_name(param: str, correlation_id: str = "") -> str:
        validate_identifier(param)
        check_table_access(param)
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.some_method(param)
        return format_response(data=result, correlation_id=correlation_id)
```

The SDK v2 tool decorators remain `@mcp.tool()` and `@tool_handler`. The server is created with `MCPServer("servicenow-platform-mcp")` and runs over stdio with `mcp.run(transport="stdio")`.

Public protocol model fields use snake_case Python names, including `input_schema` and `structured_content`.

## ✏️ Platform Artifacts

Script-bearing artifacts (Business Rules, Script Includes, UI Macros, etc.) are written via the `record_write` tool using the standard `table` parameter — there is no `artifact_type` enum. The set of script-bearing fields per table is discovered at runtime from `sys_dictionary` rather than encoded in a hardcoded catalog.

### Script-Field Discovery

`DictionaryRegistry` (in `tools/_dictionary.py`) resolves which fields on a table carry executable script or markup content:

1. Walks `sys_db_object.super_class` child-first, bounded at depth 8, with a cycle guard.
2. Fetches active `sys_dictionary` rows for each table in the chain.
3. Admits a field when its `internal_type` is in `UNAMBIGUOUS_SCRIPT_TYPES` (`script`, `script_plain`, `script_server`, `script_client`, `email_script`, `html_script`, `html_template`, `css`).
4. For ambiguous types (`html`, `xml`), admits the field only when `attributes` contains `tinymce_allow_all=true` or `html_sanitize=false`.
5. Drops anything in `EXCLUDED_ELEMENTS` (`translated_html`, `template_value`, `glide_var`, `json`, `conditions`, `condition_string`, `glide_action_list`, `variable_conditions`, `snapshot_template_value`, `variable_template_value`).
6. Caches per-table results with the configured metadata TTL; `flush(table=None)` invalidates everything or a single table.

The `looks_like_template(content)` helper (regex `\${[^}]+}`) is exposed for record-level template detection but is not consulted during dictionary discovery.

Discover the script fields for any table at runtime via `describe(action='list_script_fields', table='<table>')`, which returns the resolved super_class chain and a list of `{name, internal_type, inherited_from, via_heuristic}` entries.

### script_path Security

`record_write` accepts an optional `script_path` for any table that has at least one script-bearing field:

- Path is resolved via `Path.resolve(strict=True)` to prevent symlink/traversal attacks.
- The resolved path must be under the directory defined by the `script_allowed_root` setting.
- File is read as UTF-8; maximum size is 1 MB (`MAX_SCRIPT_FILE_BYTES`).
- Content is written to the first script-bearing field detected by `DictionaryRegistry` (child-first, sys_dictionary row order), unless `script_field` overrides it.
- When the resolved field has `internal_type == 'xml'`, the content is validated as well-formed XML (`xml.etree.ElementTree.fromstring`) before any platform call; malformed content yields a structured error.
- `record_write` uses the `PreviewTokenStore` flow (preview/apply) by default for these operations.

### script_field parameter

`record_write` accepts an optional `script_field` parameter when `script_path` is set. It selects which script-bearing field receives the file contents:

- Empty (default): writes to the first field returned by `DictionaryRegistry.get_script_fields(table)`.
- Non-empty: must match a field name returned by the registry for that table; otherwise the call returns a structured error listing the allowed fields.
- Setting `script_field` without `script_path` is rejected.

### record_read

Read-only counterpart. `record_read(table, sys_id=..., name=...)` returns the masked record plus the `script_fields` list resolved by `DictionaryRegistry` for the table, enabling discovery-driven multi-field edits. Exactly one of `sys_id` or `name` must be supplied; ambiguous names (>1 match) and missing records return structured errors. Included in both `full` and `readonly` packages.

## 🔄 ChoiceRegistry

- `ChoiceRegistry(settings, auth_provider)` - lazy-loaded from `sys_choice` table and governed by the metadata cache TTL.
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

## 🔎 Flow Designer Inspection

Dispatched via the read-only `flow` tool. Available in the `full` and `readonly` packages, or through custom packages such as `MCP_TOOL_PACKAGE=flow,query,describe`.

### 5 Flow Actions

| Action | Purpose |
| ------ | ------- |
| `inspect` | Assemble one flow/subflow by `sys_id` or `name`: header, triggers, inputs, outputs, variables, decoded V2 action/logic nodes, canvas tree, published snapshot drift, and warnings. |
| `find_by_table` | Find flows with record triggers on a given table. |
| `decode_values` | Stateless decode for gzip+base64+JSON `values` blobs from `sys_hub_*_v2` rows. |
| `list_triggers` | List V1 and V2 trigger rows with optional `table`, `trigger_type`, `active`, and `limit` filters. |
| `describe` | Return the action registry without platform I/O. |

- Reads both V1 (`sys_hub_action_instance`, `sys_hub_flow_logic`, `sys_hub_trigger_instance`) and V2 (`sys_hub_action_instance_v2`, `sys_hub_flow_logic_instance_v2`, `sys_hub_trigger_instance_v2`) Flow Designer tables.
- Joins V2 record-trigger conditions through `sys_flow_record_trigger.sys_id == trigger_v2.remote_trigger_id`.
- Pure decoder lives in `tools/_flow_values.py` as `decode_values()` and `looks_compressed()`.
- Deliberately does not use undocumented `/api/now/processflow/*` endpoints.
- Deliberately skips `sys_hub_flow_snapshot` because it is an opaque compiled cache.
- Per-node decode failures add `decode_error` to that node only; the enclosing `inspect` response still succeeds.

## 🔬 Read-Only Analysis

The read-only `analysis` tool is available in `full` and `readonly` packages. It has three actions:

- `ritm_variables`: Composes submitted answers for one `sc_req_item` through `sc_item_option_mtom`, `sc_item_option`, and `item_option_new`. It masks answers when the variable name or label indicates password, token, secret, credential, API key, or private key, and it masks conservatively when either field is unavailable. Reference and List Collector values retain raw sys_ids with an explicit warning; comma-separated List Collector identifiers set `multi_value`. A bounded `sc_multi_row_question_answer` presence query reports stable `data.unsupported_features.multi_row_variable_sets` metadata without retrieving or decoding payload fields. This metadata does not affect answer entries or pagination.
- `journal_history`: Reads `sys_journal_field` for one record. It permits only dictionary-confirmed `comments`, `work_notes`, and `close_notes`, including inherited fields. It defaults to 90 days and supports bounded `limit` and `offset` pagination.
- `describe`: Returns the action registry without platform I/O.

Both read actions require Table API access to the target and composition tables. ServiceNow row ACLs, field ACLs, and retention govern completeness.

## 🛡 Audit Inspection

Dispatched via the read-only `audit` tool. Available in the `full` and `readonly` packages, or through custom packages such as `MCP_TOOL_PACKAGE=audit,query,describe`. Backed by `AuditRegistry` (in `tools/_audit.py`), which composes `DictionaryRegistry` for the `super_class` chain walk rather than reimplementing it.

### 5 Audit Actions

| Action | Purpose |
| ------ | ------- |
| `check_field` | Resolve the combined audit verdict for one `(table, field)` pair: chain-walked `sys_db_object.sys_audit`, chain-walked `sys_dictionary.audit`, `no_audit` attribute veto, and a positive-control count from `sys_audit`. |
| `check_fields` | Batch variant of `check_field`. Accepts a comma-separated `fields_csv` (max 50) and returns one verdict per field plus a single shared `table_change_count`. |
| `check_table` | Table-level posture: table default, super_class chain, and the list of fields whose resolved audit flag differs from that default. |
| `history` | Masked, date-bounded audit trail for one record. Queries `sys_audit` with the real column names (`tablename`, `documentkey`, `fieldname`) and masks entries via `mask_audit_entry`. |
| `describe` | Return the action registry without platform I/O. |

- `verdict` enum: `audited`, `not_audited_field_flag` (with `reason` of `audit_flag` or `no_audit_attribute`), `not_audited_table_flag`, `audited_but_inactive`, `inconclusive`.
- The `sys_audit` table is one of the largest tables on the platform. Every action that reads it applies a default 90-day window. Callers MAY override the window via `window_days` (or an explicit `since` on `history`) but SHOULD keep the default - wider windows cause slow queries and risk timeouts. Responses include both the `window_days` actually used and a `window_note` describing it.
- Field-level audit is resolved child-first along `super_class`; the first `sys_dictionary` row found wins, and `inherited_from` names the source table (or is `null` when the queried table declared its own row).
- `no_audit=true` in the `attributes` blob is an absolute veto over the boolean `audit` column. It is matched at comma boundaries so substrings like `my_no_audit=true` do not false-positive.
- Positive control disambiguates "no field activity in window" from "audit not configured": zero field rows with non-zero table rows means `audited_but_inactive`; zero on both means `inconclusive`.
- `AuditRegistry` caches table-level posture and per-(table, field) resolution with the metadata cache TTL; `sys_audit` row counts are NEVER cached.
- Deliberately does not inspect `sys_audit_delete`, `sys_audit_relation`, or `sys_history_line` - those are distinct stores with different schemas.

## 🖥 Server Bootstrap

`create_mcp_server()` performs the following:

1. Creates `Settings` and auth via `create_auth()`.
2. Calls `setup_sentry(settings)` and sets Sentry context.
3. Creates `MCPServer('servicenow-platform-mcp')`.
4. Calls `attach_servicenow_state(...)` to attach shared state.
5. Always registers the `list_tool_packages` tool.
6. Loads tool groups via `importlib` and passes the shared registries and client factory to `register_tools(...)`.
7. `main()` runs with stdio transport; `shutdown_sentry()` is called in a finally block.

## 🌐 Client

- `ServiceNowClient(settings, auth_provider)` - async context manager.
- ATF methods have been deleted.
- Retains `list_reports`, `get_email`, `get_import_set_record`, and `sc_*` methods per ADR §2.3.

## ⚙️ Configuration (Settings)

| Field                   | Type      | Default                                              | Env Var                 |
| ----------------------- | --------- | ---------------------------------------------------- | ----------------------- |
| `servicenow_instance_url` | `str`       | required                                             | `SERVICENOW_INSTANCE_URL` |
| `servicenow_api_key`      | `SecretStr` | `""` (replaces Basic Auth when set)                  | `SERVICENOW_API_KEY`      |
| `servicenow_username`     | `str`       | `""` (required without API key)                      | `SERVICENOW_USERNAME`     |
| `servicenow_password`     | `SecretStr` | `""` (required without API key)                      | `SERVICENOW_PASSWORD`     |
| `mcp_tool_package`        | `str`       | `"full"`                                               | `MCP_TOOL_PACKAGE`        |
| `servicenow_env`          | `str`       | `"dev"`                                                | `SERVICENOW_ENV`          |
| `max_row_limit`           | `int`       | `100` (range 1-10000)                                  | `MAX_ROW_LIMIT`           |
| `large_table_names_csv`   | `str`       | `"syslog,sys_audit,sys_log_transaction,sys_email_log"` | `LARGE_TABLE_NAMES_CSV`   |
| `script_allowed_root`     | `str`       | `""`                                                     | `SCRIPT_ALLOWED_ROOT`     |
| `httpx_timeout_seconds`   | `float`     | `30.0` (range 1.0-600.0)                              | `HTTPX_TIMEOUT_SECONDS`   |
| `metadata_cache_ttl_seconds` | `int`    | `300` (range 1-86400)                                  | `METADATA_CACHE_TTL_SECONDS` |
| `sentry_dsn`              | `str`       | `""`                                                     | `SENTRY_DSN`              |
| `sentry_environment`      | `str`       | `""`                                                     | `SENTRY_ENVIRONMENT`      |

## 📦 Packages & Tool Groups

The registry contains 4 preset packages and 13 tool groups. Tool groups are loaded from `servicenow_mcp.tools.*`.

### Preset Packages

| Package | Tools | Description |
|---|---|---|
| `full` | 15 | Every tool group |
| `readonly` | 11 | Read tools + investigate + resolve_choice + analysis |
| `core_readonly` | 4 | Query + describe + attachment only |
| `none` | 1 | Only `list_tool_packages` loaded |

- **Custom Packages:** Comma-separated group names are supported (e.g., `MCP_TOOL_PACKAGE=query,describe`).
- **Tool Group Shadowing:** `MCP_TOOL_PACKAGE=service_catalog` resolves via the custom-package path to the single tool group.
- **Attachment split:** The `attachment` group is read-only. `attachment_write` is a separate opt-in group and remains gated at runtime by `write_gate`.
- **Query construction:** Pass ServiceNow encoded query strings directly to `query(encoded_query=...)`. Callers can copy filter breadcrumbs from ServiceNow or construct the strings directly. Query safety still runs in `query`.

### Compact Reads and Selection Metadata

- `query` list mode requires an explicit `fields` projection. `fields="*"` requests all fields; `sys_id` is always included. Exact `sys_id` mode defaults to `sys_id,sys_updated_on` and also accepts an explicit projection or `*`. Aggregate mode is unchanged.
- `record_read` accepts `fields`. Empty selection returns compact identity/update fields plus discovered script-bearing fields; `*` returns the full masked record. `script_fields` remains in the response and `sys_id` is always included.
- `describe` resolves the bounded `super_class` chain child-first, de-duplicates child overrides, preserves `inherited_from`, and then returns an alphabetical page of 25 fields when `fields` is empty. `field_offset` and `field_limit` (1-100) continue the page; `fields="*"` requests all fields.
- Affected success responses include `selection` metadata. Use it, together with truncation metadata where present, to continue bounded reads.

### HTTP Pool and Metadata Cache

- Repeated tool calls reuse one server-lifetime shared HTTP connection pool. Direct `ServiceNowClient` instances still own and close their own transport.
- `METADATA_CACHE_TTL_SECONDS` controls monotonic TTL freshness for choices, dictionary chain/field/script-field metadata, and audit table/field configuration. It defaults to 300 seconds and accepts 1-86400 seconds. The cache uses synchronous fresh reload, same-key single flight, independent-key concurrency, explicit invalidation, and a 1,000-entry LRU bound.
- The metadata cache does not cache records, query results, flows, attachments, previews, or audit row counts. Bounded telemetry reports fixed-label HTTP and metadata-cache counters; it does not add a public tool.

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

### Tool Test Helpers

Tool tests live alongside the rest of the suite in `tests/`. Standard tool registration for tests uses the 5-argument signature:

```python
from mcp.server import MCPServer


async def _register_and_get_tool_schemas(settings, auth_provider, choices=None):
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return {tool.name: tool for tool in await mcp.list_tools()}
```

Use the public asynchronous `await mcp.list_tools()` API for tool listing and schema checks. Existing test helpers may access the private callable registry only when a test must directly invoke a registered handler.

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
