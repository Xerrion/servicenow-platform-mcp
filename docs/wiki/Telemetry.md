# Telemetry

Contributor reference for optional Sentry error tracking and bounded runtime
measurements. No telemetry MCP tool is exposed.

## Local timeout diagnostics

A tool call can wait for authorization, metadata, and several HTTP requests.
Its total duration is not bounded by `HTTPX_TIMEOUT_SECONDS`, which controls
HTTPX connection, read, write, and pool waits. The MCP client has a separate
request deadline. Increasing the HTTP timeout does not increase that deadline.

The CLI writes diagnostic events to stderr. Restart the full MCP process to
load this behavior, then inspect its stderr in the agent host's server logs.
No Sentry configuration is needed. Stdout remains reserved for MCP messages.
Raw `httpx2` and `httpcore2` request logs are suppressed at INFO and DEBUG levels.

Each decorated tool invocation has a random `trace_id`. Events include:

- Tool start and finish, total duration, and the number of HTTP requests started.
- Authorization start and finish, including time waiting for the auth lock or browser.
- HTTP start and finish, a request number, a fixed operation label, method,
  duration, response size, and HTTP status when available.
- HTTP failure or cancellation, with `timeout_phase` set to `connect`, `read`,
  `write`, `pool`, or `unknown` for HTTPX timeouts, and `none` for other failures.

Operation labels distinguish `dictionary`, `table_metadata`, `choices`,
`documentation`, `records`, `aggregate`, `attachment`, and `other`.
They do not disclose target table names, record IDs, URLs, query strings,
headers, or record contents. The outbound `X-Correlation-ID` carries the tool's
trace ID. It is not a ServiceNow transaction ID.

`outcome=returned` means the tool returned a response, including error envelopes;
it does not mean the operation succeeded. `outcome=cancelled` means cancellation
reached the server. Cancellation alone does not establish a client timeout.
An unmatched start can also mean the process was stopped or logs are incomplete.

Concurrent tools have separate trace IDs. Shared metadata loads retain the
initiating tool's trace and can continue after that caller is cancelled.
HTTP counts cover requests started under that trace, not requests borrowed from
another caller's in-flight metadata load. Authorization timing includes token
exchange, but token-exchange HTTP traffic is outside the shared HTTP counters.

An HTTPX timeout returned by a decorated tool has `error.code=UPSTREAM_TIMEOUT`,
`error.phase`, `error.operation`, and `error.trace_id`. Use that ID to find its
HTTP event. A timeout without a request object reports `operation=unknown`.
Timeout errors are not replayed. If a write timed out, verify the remote outcome
before retrying. A metadata timeout must not be treated as evidence that the
target record query is slow.

If advisory query-field validation fails, the query can still run, but its
response includes a warning. Metadata timeouts name the phase and trace ID.
Do not treat that response as proof that all filter fields were checked.

## Sentry

`SENTRY_DSN` is the activation gate. Leave it unset when Sentry is not used.
`SENTRY_ENVIRONMENT` labels events; when omitted, it uses `SERVICENOW_ENV`.

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
- At most 50 breadcrumbs per event.

Each decorated tool has a separate Sentry scope. Its error transaction name
identifies that tool, without renaming a shared MCP span. Tool scopes discard
inherited request contexts and breadcrumbs. Server and aggregate cache context
remain available.

`tool` records the tool name and redacted arguments, including positional inputs
and declared defaults. Query values, resolved labels, credentials, and payloads
remain redacted. `tool_trace` records the local trace ID, elapsed duration, and
HTTP request count. The trace ID matches stderr and outbound `X-Correlation-ID`.
It is separate from the Sentry distributed trace ID.

`http_request` describes the latest completed or failed HTTP attempt. Its
breadcrumbs retain a bounded request history with operation, method, request
number, duration, status or timeout phase, response size, and pool mode. HTTP
errors also set indexed `http.status_code` and `http.operation` tags.

Request evidence records transmitted, allowlisted pagination and display
controls. It distinguishes an omitted offset from an explicit zero. A query
summary records length and lexical features: text search, JavaScript, OR, NQ,
and ordering. It does not capture field names, search terms, values, or
JavaScript source. Summaries are not query validation. Duplicate queries and
queries over 8192 characters receive an explicit inspection omission.

HTTP error response evidence includes content type, size, JSON shape, and
reviewed static `error.message` or `error.detail` phrases. Unknown text receives
an omission marker. Bodies over 8192 bytes and unread streams are not inspected.
At most one transaction, request, or correlation header survives hex/UUID
validation and credential, cookie, query, and request-body reflection checks.
Raw bodies, unrestricted headers, and authentication challenges are not added
to this evidence. Existing REST 401 diagnostics retain their separate policy.

The server context includes instance hostname, environment, production flag,
and selected package. Do not widen captured values without reviewing
credential, PII, and untrusted-payload exposure.

Restart the updated MCP process to capture this evidence. Existing Sentry
events cannot recover data that the old process did not record.

## Bounded runtime telemetry

The shared HTTP client records aggregate:

- Started, completed, and failed request counts.
- HTTP error responses (`http_error_count`, status 400 or higher).
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

`failed_request_count` counts transport failures and cancellation, not HTTP
error responses. A received HTTP 400 counts as both a completed request and an
HTTP error. Its response bytes and duration are counted once.

## Contributor guidance

Use the public helpers in `servicenow_mcp.sentry`:

- `setup_sentry(settings)` initializes once.
- `capture_exception(exc)` reports an exception when active.
- `set_sentry_tag(key, value)` sets an indexed tag.
- `set_sentry_context(key, data)` sets structured context.
- `sentry_tool_scope(tool)` isolates tool evidence and error attribution.
- `add_sentry_breadcrumb(category, data)` records caller-curated request history.
- `shutdown_sentry()` flushes and closes the client.

Tests reset Sentry initialization state through the autouse fixture in
`tests/conftest.py`, so unit tests do not send real events. Keep telemetry
aggregate or scoped to an invocation, with fixed operation labels. Do not add
raw request bodies, tokens, callback data, or unrestricted user input.

See [[Configuration]] for environment variables and [[Architecture]] for where
telemetry enters runtime flow.
