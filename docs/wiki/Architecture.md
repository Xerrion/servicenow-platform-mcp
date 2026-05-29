# Architecture

How the servicenow-platform-mcp server is built, from process shape down to the moving parts inside a single tool call.

## High-Level Shape

The server is a Python 3.12+ async process that speaks the Model Context Protocol over stdio. It is built on FastMCP, uses httpx for all ServiceNow HTTP traffic, and ships as a src-layout package (`src/servicenow_mcp/`). The console entry point is `servicenow_mcp.server:main`.

There is no HTTP listener, no multi-tenant routing, and no background scheduling. The MCP client (an AI assistant runtime) launches the process and owns its lifecycle. All I/O is cooperative async on a single thread.

## Server Bootstrap

`create_mcp_server()` in `server.py` assembles the runtime in this order:

1. `Settings()` - loads configuration from environment variables via pydantic-settings (`.env` then `.env.local`).
2. `create_auth(settings)` - returns a `BasicAuthProvider`.
3. `setup_sentry(settings)` - initializes the Sentry SDK (no-op when DSN is empty or the SDK is missing).
4. Sets Sentry server context (instance hostname, environment, production flag, tool package).
5. `FastMCP("servicenow-platform-mcp")` - creates the protocol server instance.
6. Creates shared registries: `ChoiceRegistry(settings, auth_provider)` and `DictionaryRegistry(settings, auth_provider)`.
7. `attach_servicenow_state(mcp, settings, auth_provider, choices, dictionary)` - injects shared state onto the MCP instance.
8. Registers the `list_tool_packages` tool inline (see below).
9. Resolves the active package via `get_package(settings.mcp_tool_package)`, then for each group calls `importlib.import_module` and invokes the module's `register_tools`.

`main()` runs `mcp.run(transport="stdio")` in a try/finally that calls `shutdown_sentry()` on exit.

## Tool Registration

Every tool group module exports a `register_tools` function with this exact signature:

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    ...
```

Inside, each tool is defined as a closure decorated with `@mcp.tool()` then `@tool_handler`:

```python
@mcp.tool()
@tool_handler
async def my_tool(param: str, correlation_id: str = "") -> str:
    validate_identifier(param)
    check_table_access(param)
    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.some_method(param)
    return format_response(data=result, correlation_id=correlation_id)
```

The one exception is `list_tool_packages`, registered directly in `server.py` as a bare closure. It returns `serialize(list_packages())` with no `@tool_handler` wrapper, no `correlation_id`, and no error envelope. It is the only tool that is not wrapped by `@tool_handler`.

## The `@tool_handler` Decorator

Defined in `decorators.py`, this decorator is the central safety and observability layer:

1. **Injects `correlation_id`** - generates a UUID4 via `generate_correlation_id()` and passes it as a keyword argument to the wrapped function.
2. **Hides `correlation_id` from the schema** - overrides `__signature__` and deletes `__wrapped__` so FastMCP does not expose it to callers.
3. **Attaches Sentry telemetry** - sets tags (`tool.name`, `tool.correlation_id`) and a context blob with tool name, correlation ID, and redacted arguments. Sensitive argument keys are replaced wholesale with `"***REDACTED***"` before transmission (see [[Telemetry]] for the full key list).
4. **Wraps execution in `safe_tool_call()`** which catches exceptions in this order:

| Caught | Envelope behavior |
|---|---|
| `ACLError` | Verbose message |
| `ForbiddenError` | Verbose message |
| `ServiceNowMCPError` (all subclasses) | Verbose `str(e)` |
| `ValueError` | Verbose `str(e)` |
| Generic `Exception` | Opaque `"Internal error (correlation_id=...)"` - full detail goes to `logger.exception` and Sentry |

Tool functions never raise to the MCP layer.

## Response Envelope

Every tool (except `list_tool_packages`) returns a serialized JSON string built by `format_response()` in `utils.py`:

```json
{
  "correlation_id": "...",
  "status": "success | error",
  "data": "<any>",
  "error": {"message": "..."} | null,
  "pagination": {"limit": 0, "offset": 0, "total": 0} | null,
  "warnings": ["..."] | null
}
```

When `error` is passed as a plain string it is wrapped into `{"message": str}`. When passed as a dict it is used verbatim. The keys `error`, `pagination`, and `warnings` are omitted when their value is `None`; `correlation_id`, `status`, and `data` are always present (`data` is serialized as `null` when it is `None`).

## Async Client

`ServiceNowClient(settings, auth_provider)` is an async context manager over `httpx.AsyncClient`. The timeout is configured via `HTTPX_TIMEOUT_SECONDS` (default 30 seconds, range 1-600).

```python
async with ServiceNowClient(settings, auth_provider) as client:
    record = await client.get_record("incident", sys_id)
```

Auth headers are produced by `BasicAuthProvider.get_headers()` (async for future OAuth2 extensibility). Each request also carries an `X-Correlation-ID` header (a fresh UUID4).

`_raise_for_status()` maps HTTP status codes to the exception hierarchy defined in `errors.py`:

| HTTP | Exception |
|---|---|
| 401 | `AuthError` |
| 403 | `ACLError` (when body matches ACL indicator regex) or `ForbiddenError` |
| 404 | `NotFoundError` |
| 500+ | `ServerError` |
| Other 4xx | `ServiceNowMCPError` |

## State

The only in-memory mutable state is `PreviewTokenStore` in `state.py`:

- TTL: 300 seconds (5 minutes).
- Capacity: 1000 entries.
- `create(payload) -> str` stores a dict and returns a UUID token.
- `consume(token) -> dict | None` retrieves and deletes in one step (single-use).
- All mutations are serialized through an `asyncio.Lock`.
- `_sweep_expired()` reclaims stale entries before capacity checks.

This backs the preview-then-apply write flow: `record_write` (with `preview=True`) creates a token containing the validated payload; `record_apply` consumes that token and executes the write.

## Choice and Dictionary Registries

**ChoiceRegistry** maps human-readable labels to internal values for state-like fields. It ships 6 default table-field mappings (incident, change_request, problem, cmdb_ci, sc_request, sc_req_item) and lazily fetches instance-specific overrides from `sys_choice`. Labels are normalized to lowercase with spaces replaced by underscores. See [[Tool-Reference]] for the `resolve_choice` tool surface.

**DictionaryRegistry** resolves which fields on a given table carry executable script or markup content. It walks the `sys_db_object.super_class` chain child-first (bounded at depth 8, cycle-guarded), classifies fields by `internal_type` and attribute heuristics, and caches per-table results for the server lifetime. See [[Tool-Reference]] for the `describe` tool's `list_script_fields` action.

## Where Things Live

| Path | Role |
|---|---|
| `src/servicenow_mcp/server.py` | Bootstrap, `main()`, `list_tool_packages` |
| `src/servicenow_mcp/tools/` | Tool group modules (one per group) and underscore-prefixed helpers |
| `src/servicenow_mcp/investigations/` | Investigation modules (7 files) |
| `src/servicenow_mcp/policy.py` | Table access, field masking, query safety, write gating |
| `src/servicenow_mcp/client.py` | `ServiceNowClient` async HTTP layer |
| `src/servicenow_mcp/config.py` | `Settings` model (pydantic-settings) |
| `src/servicenow_mcp/decorators.py` | `@tool_handler` and Sentry arg redaction |
| `src/servicenow_mcp/errors.py` | Exception hierarchy |
| `src/servicenow_mcp/state.py` | `PreviewTokenStore` |
| `src/servicenow_mcp/utils.py` | `format_response`, `safe_tool_call`, `ServiceNowQuery`, validators |
| `src/servicenow_mcp/sentry.py` | Opt-in Sentry SDK setup and helpers |

---

See also: [[Safety-and-Policy]] for guardrails and write gating, [[Telemetry]] for Sentry integration details, [[Tool-Packages]] for the full package registry.
