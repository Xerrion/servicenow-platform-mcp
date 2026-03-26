# Architecture

Deep technical architecture of the `servicenow-devtools-mcp` server - an async Python MCP server for ServiceNow platform introspection, change intelligence, debugging, investigations, and documentation generation.

## Overview

The server is built on:

- **FastMCP** - MCP server framework providing tool registration and transport handling
- **httpx** - Async HTTP client for ServiceNow REST API communication
- **pydantic-settings** - Configuration management via environment variables
- **TOON format** - Serialization format optimized for LLM consumption (not raw JSON)
- **sentry-sdk** - Error tracking for invisible child-process environments

Communication happens over **stdio transport** - the server runs as a child process of an AI agent, meaning the user never sees stdout/stderr directly.

See also: [[Development]] for setup and tooling, [[Telemetry]] for observability.

## Server Bootstrap

The server entry point is `server.py`, which exposes two functions: `create_mcp_server()` (factory) and `main()` (runner).

### `create_mcp_server()` Factory

This function performs a strict initialization sequence:

1. **Create Settings** - `Settings()` loads configuration from environment variables via pydantic-settings (reads `.env` and `.env.local` files)
2. **Create auth provider** - `create_auth(settings)` returns a `BasicAuthProvider` for ServiceNow API authentication
3. **Initialize Sentry** - `setup_sentry(settings)` activates error tracking when a DSN is configured
4. **Set server context** - Attaches instance hostname, environment, production flag, and tool package to Sentry scope
5. **Create FastMCP instance** - `FastMCP("servicenow-dev-debug")`
6. **Create shared state** - `QueryTokenStore()` for query token management, `ChoiceRegistry(settings, auth_provider)` for choice label resolution
7. **Attach state to FastMCP** - `attach_servicenow_state()` stores all shared objects on the FastMCP instance via typed helpers in `mcp_state.py`
8. **Register `list_tool_packages`** - Always-on tool that returns all available tool packages
9. **Load tool groups** - Loops through the active package's tool groups, dynamically importing each module via `importlib.import_module()` and calling its `register_tools()` function
10. **Domain module detection** - Modules with group names prefixed `domain_` receive an additional `choices=choices` keyword argument

### `main()` Runner

```python
def main() -> None:
    mcp = create_mcp_server()
    try:
        mcp.run(transport="stdio")
    finally:
        shutdown_sentry()
```

The `finally` block ensures Sentry flushes pending events even if the server crashes.

## Tool Registration Pattern

Each tool group lives in its own module under `tools/` (or `tools/domains/` for ITSM domain tools). Every module exports a `register_tools()` function.

### Standard Tools

```python
def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    @mcp.tool()
    @tool_handler
    async def my_tool(param: str, correlation_id: str = "") -> str:
        validate_identifier(param)
        check_table_access(param)
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.some_method(param)
        return format_response(data=result, correlation_id=correlation_id)
```

### Domain Tools

Domain modules receive an additional `choices` parameter for label-to-value resolution:

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
        if state and choices:
            state = await choices.resolve("incident", "state", state)
        ...
```

### 21 Tool Group Modules

| Group Name | Module Path | Type |
|---|---|---|
| `table` | `tools.table` | Standard |
| `record` | `tools.record` | Standard |
| `attachment` | `tools.attachment` | Standard |
| `record_write` | `tools.record_write` | Standard |
| `attachment_write` | `tools.attachment_write` | Standard |
| `testing` | `tools.testing` | Standard (disabled in `full`) |
| `metadata` | `tools.metadata` | Standard |
| `artifact_write` | `tools.artifact_write` | Standard |
| `changes` | `tools.changes` | Standard |
| `debug` | `tools.debug` | Standard |
| `investigations` | `tools.investigations` | Standard |
| `documentation` | `tools.documentation` | Standard |
| `workflow` | `tools.workflow` | Standard |
| `flow_designer` | `tools.flow_designer` | Standard |
| `domain_incident` | `tools.domains.incident` | Domain |
| `domain_change` | `tools.domains.change` | Domain |
| `domain_cmdb` | `tools.domains.cmdb` | Domain |
| `domain_problem` | `tools.domains.problem` | Domain |
| `domain_request` | `tools.domains.request` | Domain |
| `domain_knowledge` | `tools.domains.knowledge` | Domain |
| `domain_service_catalog` | `tools.domains.service_catalog` | Domain |

## The `@tool_handler` Decorator

Located in `decorators.py`, this is the central pattern - every tool function uses it. The decorator stacks as `@mcp.tool()` (outer) then `@tool_handler` (inner).

### What It Does

1. **Auto-generates `correlation_id`** - Calls `generate_correlation_id()` which returns a UUID4 string
2. **Sets Sentry tags** - `tool.name` and `tool.correlation_id` for filtering in the Sentry UI
3. **Sets Sentry context** - Structured `"tool"` context with name, correlation_id, and sanitized args (excluding `correlation_id` itself from the args dict)
4. **Wraps in `safe_tool_call()`** - Error handling boundary that catches exceptions and returns serialized error envelopes
5. **Hides `correlation_id` from schema** - Overrides `__signature__` to remove the `correlation_id` parameter so FastMCP does not expose it in the tool schema
6. **Deletes `__wrapped__`** - Removes the attribute set by `functools.wraps` so that `inspect.signature()` uses the overridden `__signature__` instead of following `__wrapped__` back to the original function

### How `correlation_id` Flows

```
MCP Client calls tool (no correlation_id)
  -> @tool_handler generates UUID4
    -> Sets Sentry tags and context
    -> Injects correlation_id as kwarg to original function
    -> Original function uses it in format_response()
    -> Response envelope includes correlation_id for tracing
```

## Error Handling Flow

### Exception Hierarchy

```
ServiceNowMCPError(Exception)       # Root - has status_code attribute
    |-- AuthError                    # HTTP 401
    |-- ForbiddenError               # HTTP 403
    |-- NotFoundError                # HTTP 404
    |-- ServerError                  # HTTP 5xx
    |-- PolicyError                  # HTTP 403 (policy violations)
          |-- QuerySafetyError       # HTTP 403 (unsafe queries)
```

### `safe_tool_call()` Error Boundary

Located at the end of `utils.py`, this function wraps every tool invocation:

```python
async def safe_tool_call(fn, correlation_id) -> str:
    try:
        return await fn()
    except ForbiddenError as e:
        sentry_capture(e)
        return format_response(
            data=None, correlation_id=correlation_id,
            status="error", error=f"Access denied by ServiceNow ACL: {e}",
        )
    except Exception as e:
        sentry_capture(e)
        return format_response(
            data=None, correlation_id=correlation_id,
            status="error", error=str(e),
        )
```

Key properties:

- **Tool functions never raise to MCP** - all exceptions become serialized response envelopes
- **Both paths capture to Sentry** - every error is tracked
- **`ForbiddenError` gets special treatment** - ACL denials produce a specific error message
- **No manual try/except needed** - tool functions rely entirely on this wrapper

## ServiceNow Client

`ServiceNowClient` in `client.py` is an async context manager wrapping `httpx.AsyncClient`:

- **Timeout**: 30 seconds
- **`_ensure_client()`**: Raises `RuntimeError` (not assert) if the client is used outside the context manager
- **`_raise_for_status()`**: Maps HTTP status codes to custom exceptions and sets Sentry `"http"` context with `status_code`, `method`, and `url` before raising
- **An extensive set of async methods** covering: records, attachments, metadata, aggregation, CRUD, CMDB, email, import sets, reports, code search, service catalog, ATF, and more
- **Input validation**: Uses `validate_identifier()` for table and field names

### HTTP Status Mapping

| HTTP Status | Exception |
|---|---|
| 401 | `AuthError` |
| 403 | `ForbiddenError` |
| 404 | `NotFoundError` |
| 500+ | `ServerError` |
| 400+ (other) | `ServiceNowMCPError` |

## TOON Serialization

All tool output uses **TOON format** - not raw JSON. TOON is an LLM-optimized serialization format.

### `serialize(data)` in `utils.py`

```python
def serialize(data: Any) -> str:
    try:
        return toon_encode(data)
    except Exception as e:
        logger.warning("TOON encoding failed, falling back to JSON", exc_info=True)
        sentry_capture(e)
        return json.dumps(data, indent=2)
```

Falls back to JSON if TOON encoding fails, capturing the failure to Sentry.

### `format_response()` Envelope

Builds a standardized response envelope and serializes it:

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

The envelope always contains `correlation_id`, `status`, and `data`. Optional fields (`error`, `pagination`, `warnings`) are included only when provided. Error strings are wrapped as `{"message": error}` dicts.

### Parsing Tool Output

```python
# CORRECT - use toon_decode
from toon_format import decode as toon_decode
result = toon_decode(raw_output)

# WRONG - will fail on TOON-formatted output
import json
result = json.loads(raw_output)
```

## State Management

Located in `state.py`, two token store classes provide in-memory state with automatic expiry.

### Base Class: `_BaseTokenStore`

- **UUID keys** - Every stored payload gets a unique UUID4 token
- **TTL expiry** - Default 300 seconds, using `time.monotonic()` timestamps
- **Max size** - 1000 entries, raises `RuntimeError` when full
- **Lazy cleanup** - Expired entries swept on `create()` calls
- **Methods**: `create(payload) -> str`, `get(token) -> dict | None`

### `PreviewTokenStore` (Single-Use)

Used for the record write preview/apply pattern. Adds a `consume(token)` method that retrieves the payload and deletes the token in one operation.

### `QueryTokenStore` (Reusable)

Used for the `build_query` -> query-consuming tools workflow. Tokens remain valid within their TTL and can be retrieved multiple times via `get()`.

### Token Flow Example

```
build_query tool -> QueryTokenStore.create({"query": "state=1^priority=2"})
                 -> returns token "abc-123"

record_list tool -> resolve_query_token("abc-123", store, correlation_id)
                 -> returns "state=1^priority=2"
                 -> token still valid for reuse
```

## ChoiceRegistry

Located in `choices.py`, provides lazy-loaded choice label resolution backed by the ServiceNow `sys_choice` table.

### Initialization

- Created in `create_mcp_server()` but data is **not** fetched until first use
- Uses `asyncio.Lock` with a double-check pattern for thread-safe initialization
- Fetches all choice records from `sys_choice` on first access
- Falls back to hardcoded `_DEFAULTS` on fetch failure (captured to Sentry)

### 6 Default Table/Field Mappings

| Table | Field |
|---|---|
| `incident` | `state` |
| `change_request` | `state` |
| `problem` | `state` |
| `cmdb_ci` | `operational_status` |
| `sc_request` | `state` |
| `sc_req_item` | `state` |

### Label Normalization

Labels are normalized to lowercase with spaces replaced by underscores:

- `"New"` -> `"new"`
- `"In Progress"` -> `"in_progress"`
- `"On Hold"` -> `"on_hold"`

### API

```python
# Resolve a label to its ServiceNow value (or passthrough if not found)
await choices.resolve("incident", "state", "open")

# Get all choices for a table/field pair
choices.get_choices("incident", "state")  # Returns dict[str, str]
```

## Source Layout

```
src/servicenow_mcp/
    server.py              # Entry point, bootstrap
    config.py              # Settings (pydantic-settings)
    auth.py                # Basic Auth provider
    client.py              # ServiceNow HTTP client
    decorators.py          # @tool_handler decorator
    utils.py               # Serialization, query builder, validation
    errors.py              # Exception hierarchy
    policy.py              # Table access, field sensitivity, query safety
    state.py               # PreviewTokenStore, QueryTokenStore
    choices.py             # Choice label resolution (sys_choice)
    sentry.py              # Sentry error tracking integration
    packages.py            # Tool package registry (14 presets)
    mcp_state.py           # Typed FastMCP state attachment helpers
    types.py               # Type aliases
    investigation_helpers.py  # Shared investigation utilities
    investigations/        # 7 investigation modules
        stale_automations.py
        deprecated_apis.py
        table_health.py
        acl_conflicts.py
        error_analysis.py
        slow_transactions.py
        performance_bottlenecks.py
    tools/                 # Tool group modules
        table.py
        record.py
        record_write.py
        attachment.py
        attachment_write.py
        artifact_write.py
        metadata.py
        changes.py
        debug.py
        investigations.py
        documentation.py
        workflow.py
        flow_designer.py
        testing.py
        domains/           # Domain-specific tools (ITSM)
            incident.py
            change.py
            cmdb.py
            problem.py
            request.py
            knowledge.py
            service_catalog.py
```
