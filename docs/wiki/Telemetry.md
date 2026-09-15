# Telemetry

Contributor reference for optional Sentry error tracking and bounded runtime
measurements. No telemetry MCP tool is exposed.

## Sentry

`SENTRY_DSN` is the activation gate. Empty `SENTRY_DSN` disables Sentry.
`SENTRY_ENVIRONMENT` labels events; an empty value uses `SERVICENOW_ENV`.

```dotenv
SENTRY_DSN=https://example.invalid/project
SENTRY_ENVIRONMENT=dev
```

The `sentry-sdk` package is a core dependency, but import and SDK integration
guards let the server run when Sentry is unavailable. Public Sentry helpers
no-op when the SDK is unavailable or not initialized.

Initialization occurs during `create_mcp_server()`. Shutdown runs in `main()`'s
`finally` block and flushes and closes the active client with two-second
timeouts. Shutdown errors do not propagate.

Sentry configuration includes:

- `send_default_pii=False`.
- No local-variable capture.
- A package-derived release string, or `servicenow-platform-mcp@unknown` when package metadata is unavailable.
- An MCP integration when the installed SDK provides it.
- `traces_sample_rate=0.1`.
- Profiling disabled.

Tool context records the tool name and redacted arguments. HTTP errors add
bounded request context. The server context includes instance hostname,
environment, production flag, and selected package. Do not widen captured
values without reviewing credential, PII, and untrusted-payload exposure.

## Bounded runtime telemetry

The shared HTTP client records aggregate:

- Started, completed, and failed request counts.
- Downloaded response bytes.
- Total request duration.
- Requests using the shared connection pool.

The metadata cache records hits, misses, expirations, reloads, and
invalidations for fixed cache domains:

```text
choices
dictionary_chains
dictionary_fields
dictionary_script_fields
audit_table_config
audit_field_config
```

Telemetry does not record credentials, request contents, URLs, or arbitrary
table names. It does not cache records, query results, flows, attachments,
preview tokens, or audit row counts.

## Contributor guidance

Use the public helpers in `servicenow_mcp.sentry`:

- `setup_sentry(settings)` initializes once.
- `capture_exception(exc)` reports an exception when active.
- `set_sentry_tag(key, value)` sets an indexed tag.
- `set_sentry_context(key, data)` sets structured context.
- `shutdown_sentry()` flushes and closes the client.

Tests reset Sentry initialization state through the autouse fixture in
`tests/conftest.py`, so unit tests do not send real events. Keep telemetry
fixed-size and aggregate. Do not add raw request bodies, tokens, callback data,
or unrestricted user input.

See [[Configuration]] for environment variables and [[Architecture]] for where
telemetry enters runtime flow.
