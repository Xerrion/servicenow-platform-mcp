# Telemetry

Observability documentation for the `servicenow-platform-mcp` server. Currently, **Sentry** is the sole observability integration.

See also: [[Architecture]] for server internals, [[Development]] for setup.

## Overview

MCP servers run as child processes of AI agents via stdio transport - the user never sees stdout or stderr. This makes traditional logging insufficient for error visibility. Sentry provides structured error capture, contextual data, and alerting for production deployments.

> **Note:** OpenTelemetry was previously available but has been removed from the project. See [Historical Note](#historical-note) at the end of this page.

## Sentry Integration

The Sentry integration lives in `sentry.py` and follows a graceful-degradation pattern - all public functions are safe to call regardless of whether Sentry is active.

### Activation Requirements

`sentry-sdk` is a **core dependency** (always installed, not an optional extra). However, Sentry only activates when the `SENTRY_DSN` environment variable is set to a non-empty value. There is no separate `SENTRY_ENABLED` flag - the DSN presence is the gate.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SENTRY_DSN` | `""` (disabled) | Sentry Data Source Name - activates error tracking when set |
| `SENTRY_ENVIRONMENT` | Falls back to `SERVICENOW_ENV` | Environment label shown in Sentry UI |

Both values are defined in the `Settings` class (`config.py`) and loaded from environment variables or `.env`/`.env.local` files.

## Import Guard Pattern

The module uses a two-level import guard to handle environments where `sentry-sdk` might not be installed and SDK versions that may or may not include the MCP integration:

```python
# Primary guard
HAS_SENTRY: bool
try:
    import sentry_sdk
    HAS_SENTRY = True
except ImportError:
    HAS_SENTRY = False

# Secondary guard for newer SDK versions
_HAS_MCP_INTEGRATION = False
if HAS_SENTRY:
    try:
        from sentry_sdk.integrations.mcp import MCPIntegration
        _HAS_MCP_INTEGRATION = True
    except ImportError:
        pass
```

When `HAS_SENTRY` is `False`, all public functions no-op immediately - zero overhead. The secondary guard allows the optional `MCPIntegration` to be used when available in newer SDK versions without breaking on older ones.

## SDK Configuration

When activated, Sentry is initialized with these settings:

```python
sentry_sdk.init(
    dsn=dsn,
    environment=environment,
    release=_RELEASE,            # e.g. "servicenow-platform-mcp@0.9.0"
    send_default_pii=True,
    integrations=integrations,   # Includes MCPIntegration when available
    traces_sample_rate=1.0,      # All transactions sampled
    profiles_sample_rate=None,   # Profiling disabled
)
```

The release string is dynamically derived from package metadata using `importlib.metadata.version()`:

```python
try:
    _RELEASE = f"servicenow-platform-mcp@{pkg_version('servicenow-platform-mcp')}"
except Exception:
    _RELEASE = "servicenow-platform-mcp@unknown"
```

## Instrumentation Points

Sentry context and exception capture is woven through the codebase at key points:

### `@tool_handler` (decorators.py)

Every tool invocation sets:

- **Tags** (indexed, searchable):
  - `tool.name` - The tool function name (e.g., `"record_list"`)
  - `tool.correlation_id` - UUID4 for request tracing
- **Context** (structured data):
  - `"tool"` context with `name`, `correlation_id`, and `args` (with `correlation_id` excluded from the args dict)

### `safe_tool_call()` (utils.py)

The error boundary that wraps all tool executions:

- Catches `ForbiddenError` - captures to Sentry, returns ACL denial error envelope
- Catches generic `Exception` - captures to Sentry, returns generic error envelope

Both paths call `capture_exception(e)` before returning the serialized error response.

### `serialize()` (utils.py)

Captures TOON encoding failures to Sentry before falling back to JSON serialization.

### `_raise_for_status()` (client.py)

Before raising HTTP-related exceptions, sets `"http"` Sentry context with:

- `status_code` - The HTTP status code
- `method` - The HTTP method (GET, POST, etc.)
- `url` - The request URL

### `ChoiceRegistry` (choices.py)

Captures persistent `sys_choice` fetch failures when the registry cannot load choice data from the ServiceNow instance.

### Server Bootstrap (server.py)

- Sets `"server"` Sentry context with instance hostname (extracted from URL), environment, `is_production` flag, and tool package name
- Captures `ImportError` exceptions when tool group modules fail to load

## Public API

| Function | Signature | Purpose |
|---|---|---|
| `setup_sentry` | `(settings: Settings) -> None` | Initialize SDK with DSN gating. No-ops on re-call. |
| `capture_exception` | `(exc: BaseException \| None) -> None` | Capture exception (or current `sys.exc_info()` if `None`) |
| `set_sentry_tag` | `(key: str, value: str) -> None` | Set indexed tag on current isolation scope |
| `set_sentry_context` | `(key: str, data: dict[str, Any]) -> None` | Set structured context dict on current isolation scope |
| `shutdown_sentry` | `() -> None` | Flush pending events (2s timeout) and close client (2s timeout) |

All functions no-op when either `HAS_SENTRY` is `False` or `_initialized` is `False`.

## Lifecycle

### Startup

`setup_sentry(settings)` is called early in `create_mcp_server()`, before tool registration. The function uses a triple gate to prevent double-initialization:

1. **`_initialized` check** - Short-circuits if already called
2. **`HAS_SENTRY` check** - Short-circuits if `sentry-sdk` is not installed (sets `_initialized = True`)
3. **DSN check** - Short-circuits if `sentry_dsn` is empty (sets `_initialized = True`)

In all three cases, `_initialized` is set to `True` so future calls no-op immediately.

### Shutdown

`shutdown_sentry()` is called in the `finally` block of `main()`:

```python
def main() -> None:
    mcp = create_mcp_server()
    try:
        mcp.run(transport="stdio")
    finally:
        shutdown_sentry()
```

The shutdown sequence:

1. Checks `HAS_SENTRY and _initialized`
2. Gets the active Sentry client
3. If the client is active: flushes pending events (2s timeout), then closes the client (2s timeout)
4. Resets `_initialized = False`
5. Wraps everything in try/except to prevent shutdown errors from propagating

### Testing

Tests use an autouse fixture (`_disable_sentry_capture` in `tests/conftest.py`) that resets `_initialized = False` before and after each test, preventing real Sentry SDK calls during test runs:

```python
@pytest.fixture(autouse=True)
def _disable_sentry_capture():
    import servicenow_mcp.sentry as _sentry_mod
    _sentry_mod._initialized = False
    yield
    _sentry_mod._initialized = False
```

## Historical Note

OpenTelemetry was previously integrated via a `telemetry.py` module that provided distributed tracing with spans for tool invocations and HTTP requests. It supported OTLP and console exporters, W3C `traceparent` header injection, and trace context in response envelopes.

The OTel integration was **removed in v0.9.0** (commit `b72cee3`, PR [#68](https://github.com/Xerrion/servicenow-platform-mcp/pull/68)) to resolve SonarCloud issues and simplify the observability stack. The `.env.example` file may still contain stale OTel-related variables (`OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, etc.) - these are ignored by the server and have no effect.
