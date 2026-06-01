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

`safe_tool_call()` arms, in order: `ACLError`, `ForbiddenError`, `ServiceNowMCPError`, `ValueError` - these four preserve verbose, caller-actionable messages. The final generic `Exception` arm returns an opaque `"Internal error (correlation_id=<uuid>)"` envelope and logs full detail via `logger.exception` + `sentry_capture(e)`.

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
2. Wraps the function call in `safe_tool_call()` which catches `ACLError`, `ForbiddenError`, `ServiceNowMCPError`, and `ValueError` (verbose envelopes) plus generic `Exception` (opaque `"Internal error (correlation_id=...)"` envelope with `logger.exception` + `sentry_capture`).
3. Hides `correlation_id` from the FastMCP tool schema by overriding `__signature__` and deleting `__wrapped__`.
4. Sets Sentry tags (`tool.name`, `tool.correlation_id`) and context with tool name, correlation_id, and redacted args. Sensitive argument keys are replaced with `"***REDACTED***"` before transmission to Sentry (see Sentry Module section).

One exception: `list_tool_packages` (registered directly in the server bootstrap) is NOT wrapped by `@tool_handler`. It returns the raw `serialize(list_packages())` payload with no error envelope and no `correlation_id`. Source: `src/servicenow_mcp/server.py:45-48`.

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

The server bootstrap uses an unconditional 5-argument registration pattern for all tool groups.

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    # Modules that do not require the ChoiceRegistry or DictionaryRegistry
    # explicitly ignore them
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

## ✏️ Platform Artifacts

Script-bearing artifacts (Business Rules, Script Includes, UI Macros, etc.) are written via the `record_write` tool using the standard `table` parameter — there is no `artifact_type` enum. The set of script-bearing fields per table is discovered at runtime from `sys_dictionary` rather than encoded in a hardcoded catalog.

### Script-Field Discovery

`DictionaryRegistry` (in `tools/_dictionary.py`) resolves which fields on a table carry executable script or markup content:

1. Walks `sys_db_object.super_class` child-first, bounded at depth 8, with a cycle guard.
2. Fetches active `sys_dictionary` rows for each table in the chain.
3. Admits a field when its `internal_type` is in `UNAMBIGUOUS_SCRIPT_TYPES` (`script`, `script_plain`, `script_server`, `script_client`, `email_script`, `html_script`, `html_template`, `css`).
4. For ambiguous types (`html`, `xml`), admits the field only when `_parse_attributes(attributes)` yields an exact token-boundary match for `tinymce_allow_all=true` or `html_sanitize=false`. The parser splits on commas, partitions on the first `=`, and lowercases keys and values. Substring matches like `my_tinymce_allow_all=true` no longer admit.
5. Drops anything in `EXCLUDED_ELEMENTS` (`translated_html`, `template_value`, `glide_var`, `json`, `conditions`, `condition_string`, `glide_action_list`, `variable_conditions`, `snapshot_template_value`, `variable_template_value`).
6. Caches per-table results for the registry lifetime; `flush(table=None)` invalidates everything or a single table.

The `looks_like_template(content)` helper (regex `\${[^}]+}`) is exposed for record-level template detection but is not consulted during dictionary discovery.

Discover the script fields for any table at runtime via `describe(action='list_script_fields', table='<table>')`, which returns the resolved super_class chain and a list of `{name, internal_type, inherited_from, via_heuristic}` entries.

### script_path Security

`record_write` accepts an optional `script_path` for any table that has at least one script-bearing field:

- `script_allowed_root` is resolved and `is_dir()`-validated **before** any user-controlled `script_path.resolve()`.
- All user-path rejections (file missing, not regular, outside root, symlink escape) return a single opaque message: `"script_path is not readable or is outside the allowed root"`. Configuration errors (`script_allowed_root` unset or not a directory) and the >1 MiB file-size error remain verbose.
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

### Payload Size Cap

`record_write` enforces `MAX_PAYLOAD_BYTES = 1 MiB` on the `data` parameter (covers both `create.data` and `update.changes`). The check runs in `_validate_action_args` before `_prepare_payload`, before `parse_payload_json`, and before any `PreviewTokenStore.create` call. `record_apply` inherits the cap by construction because it only reads previously-validated tokens. Defence in depth: `parse_payload_json` has its own 256 KiB cap downstream; the 1 MiB outer cap is the entry-point ceiling.

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

Dispatched via the `investigate` tool with `action='run'`, `action='explain'`, or `action='describe'`. The `explain` action trial-dispatches across registered modules. The `describe` action returns the registry without platform I/O. Bare-table forms are not supported.

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

### 6 Flow Actions

| Action | Purpose |
| ------ | ------- |
| `inspect` | Assemble one flow/subflow by `sys_id` or `name`: header, triggers, inputs, outputs, variables, decoded V2 action/logic nodes with resolved datapill refs, canvas tree, published snapshot drift, and warnings. |
| `summary` | Compact projection of `inspect`: single flattened trigger, flat ordered `steps` (no `values_decoded`), structure-only `branches` tree, global `datapill_graph`, `counts`, and the same `warnings`. Same input contract as `inspect`. |
| `find_by_table` | Find flows with record triggers on a given table. |
| `decode_values` | Stateless decode for gzip+base64+JSON `values` blobs from `sys_hub_*_v2` rows. |
| `list_triggers` | List V1 and V2 trigger rows with optional `table`, `trigger_type`, `active`, and `limit` filters. |
| `describe` | Return the action registry without platform I/O. |

- Reads both V1 (`sys_hub_action_instance`, `sys_hub_flow_logic`, `sys_hub_trigger_instance`) and V2 (`sys_hub_action_instance_v2`, `sys_hub_flow_logic_instance_v2`, `sys_hub_trigger_instance_v2`) Flow Designer tables.
- Joins V2 record-trigger conditions through `sys_flow_record_trigger.sys_id == trigger_v2.remote_trigger_id`.
- `sys_hub_flow_variable` is filtered by `model=<flow_sys_id>` (not `flow=`). The `flow` column does not exist on that table; using it makes ServiceNow silently ignore the filter and return every variable declared by every action_type on the platform (hundreds of unrelated rows).
- Pure decoder lives in `tools/_flow_values.py` as `decode_values()` and `looks_compressed()`. Decompression is bounded: `MAX_COMPRESSED_BYTES = 1 MiB` (wire cap, checked before allocating the decompressor) and `MAX_DECOMPRESSED_BYTES = 4 MiB`. Truncated streams and trailing garbage are rejected with `ValueError`.
- Deliberately does not use undocumented `/api/now/processflow/*` endpoints.
- Deliberately skips `sys_hub_flow_snapshot` because it is an opaque compiled cache.
- Per-node decode failures add `decode_error` to that node only; the enclosing `inspect` response still succeeds.

### Datapill resolution

`inspect` builds a `ui_uuid -> {sys_id, name, action_type_name}` map from the canvas, then scans every node's `values_decoded` payload for `{{<uuid>.<field>}}` references (regex `\{\{([0-9a-f-]{36})\.([^}]+)\}\}`). Resolved refs are attached as a `datapill_refs: [{ref, field, producer_ui_uuid, producer_sys_id, producer_name, resolved}]` sibling on each consuming node. `summary` reshapes the same data into a flat `datapill_graph` (one edge per consumer field), keyed by `consumer_step_sys_id`.

### Payload trims (`inspect`)

- The verbatim "Add your code here" `calculation` boilerplate Flow Designer drops into every new input/output/variable is stripped from `inputs[]`, `outputs[]`, and `variables[]`. Customised calculations survive untouched.
- `v1_actions` and `v1_variable_values` are omitted from the response when the underlying lists are empty (the common case on modern V2-only flows).

### Warnings (`inspect` and `summary`)

In addition to the original four (snapshot drift, V1+V2 coexistence, V1-logic presence, spoke-action-type references), the warning list now emits:

- `flow_active_with_inactive_trigger` - flow header is `active=true` but no V2 trigger is `active=true`.
- `missing_record_trigger_condition` - a V2 trigger has a `remote_trigger_id` but the stitched `condition` is empty (the joined `sys_flow_record_trigger` row is missing or has no condition).
- `canvas_order_nonuniform` - some sibling group on the canvas has a non-uniform consecutive order delta (e.g. 100 -> 200 -> 400, or 9 -> 11 elsewhere).
- `unresolved_datapill_ref` - one per unique `producer_ui_uuid` referenced in any decoded payload that does not exist on the canvas. The message names the producer UUID and an example consumer step.
- `step_decode_failure` - aggregate count of canvas nodes whose `values` blob failed to decode (a single warning, not one per node).

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
- `no_audit=true` in the `attributes` blob is an absolute veto over the boolean `audit` column. It is matched at comma boundaries so substrings like `my_no_audit=true` do not false-positive. Trailing whitespace before end-of-string is tolerated (`"no_audit=true "` correctly vetoes).
- Positive control disambiguates "no field activity in window" from "audit not configured": zero field rows with non-zero table rows means `audited_but_inactive`; zero on both means `inconclusive`.
- `AuditRegistry` caches table-level posture and per-(table, field) resolution for the server lifetime; `sys_audit` row counts are NEVER cached.
- Deliberately does not inspect `sys_audit_delete`, `sys_audit_relation`, or `sys_history_line` - those are distinct stores with different schemas.

## 🖥 Server Bootstrap

`create_mcp_server()` performs the following:

1. Creates `Settings` and auth via `create_auth()`.
2. Calls `setup_sentry(settings)` and sets Sentry context.
3. Creates `FastMCP('servicenow-platform-mcp')`.
4. Calls `attach_servicenow_state(...)` to attach shared state.
5. Always registers the `list_tool_packages` tool.
6. Loads tool groups via `importlib` and calls an unconditional 5-argument `register_tools(...)`.
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
| `httpx_timeout_seconds`   | `float`     | `30.0` (range 1.0-600.0)                               | `HTTPX_TIMEOUT_SECONDS`   |
| `large_table_names_csv`   | `str`       | `"syslog,sys_audit,sys_log_transaction,sys_email_log"` | `LARGE_TABLE_NAMES_CSV`   |
| `script_allowed_root`     | `str`       | `""`                                                     | `SCRIPT_ALLOWED_ROOT`     |
| `sentry_dsn`              | `str`       | `""`                                                     | `SENTRY_DSN`              |
| `sentry_environment`      | `str`       | `""`                                                     | `SENTRY_ENVIRONMENT`      |

## 📦 Packages & Tool Groups

The registry contains 4 preset packages. Tool groups are loaded from `servicenow_mcp.tools.*`.

### Preset Packages

| Package | Tools | Description |
|---|---|---|
| `full` | 14 | Every group (full surface, includes `build_query`); `record_write` group registers `record_write` + `record_apply`, `attachment` group registers `attachment` + `attachment_write` |
| `readonly` | 10 | Read tools + investigate + resolve_choice + audit + flow (still loads `attachment_write`, runtime-gated by `write_gate`) |
| `core_readonly` | 5 | Query + describe + attachment (loads `attachment_write` too, runtime-gated by `write_gate`) |
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

### SDK Defaults

- `send_default_pii=False` - no PII in breadcrumbs or request bodies.
- `traces_sample_rate=0.1` - 10% of transactions sampled.
- Both are operator-tunable via standard Sentry SDK env vars.

### Argument Redaction

`@tool_handler` redacts sensitive argument keys before attaching them to Sentry context. The `_SENSITIVE_ARG_KEYS` frozenset (case-insensitive, exact-name match): `data`, `content_base64`, `value`, `script_path`, `encoded_query`, `params`, `password`, `token`, `secret`, `api_key`, `authorization`, `variables`, `conditions`, `text`. Redacted value: `_REDACTED` (`"***REDACTED***"`). Redaction is shallow - JSON-shaped string args are replaced as a whole, not parsed and field-redacted. `correlation_id` is dropped from the args dict (already promoted to top-level context).

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
def _register_and_get_tools(settings, auth_provider, choices=None, dictionary=None):
    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices, dictionary=dictionary)
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
