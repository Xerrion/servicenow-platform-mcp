# Diagnostics and error reporting

If a request is slow or fails, start with the AI app's ServiceNow MCP server logs.
The local server records request timing without requiring a Sentry connection.
An administrator can also enable Sentry to collect error reports.

For connection or permission problems, start with [[Getting-Started]].
This page explains how operators and contributors interpret diagnostic evidence.
The server exposes no telemetry MCP tool.

## Find evidence for a failed request

1. Find the failed action and its sanitized error in the AI app.
2. Locate the ServiceNow MCP server's stderr logs in that app.
3. If the error includes a `trace_id`, find log events with the same value.
4. Identify whether the delay occurred during authorization, metadata, or a record request.
5. Include the relevant sanitized events when reporting the problem.

Stderr is the process's diagnostic output. Stdout carries MCP messages, so the
server keeps diagnostic logs separate from its responses. Log locations depend
on the AI app. Restart the full MCP process after an update to load new
logging behavior.

Include the action, time, error code, and trace ID in a report.
Exclude tokens, passwords, raw headers, browser callback URLs, and record contents.
If a write timed out, check its outcome in ServiceNow before retrying.
The write may have completed even though the server received no response.

## Understand a timeout

A tool call can wait for browser authorization, metadata, and several HTTP requests.
`HTTPX_TIMEOUT_SECONDS` controls individual connection, read, write, and connection
pool waits. It does not bound the total tool duration.

The AI app has its own request deadline. Increasing `HTTPX_TIMEOUT_SECONDS` does
not increase that deadline. Use the local trace to find the delay before changing
settings or retrying the request.

A decorated tool returns these fields when an HTTP request times out:

| Error field | Meaning |
| --- | --- |
| `code` | `UPSTREAM_TIMEOUT`. A ServiceNow HTTP wait exceeded its timeout. |
| `phase` | `connect`, `read`, `write`, `pool`, or `unknown`. |
| `operation` | A fixed operation label, such as `records` or `dictionary`. |
| `trace_id` | The local tool invocation ID used in the diagnostic logs. |

A timeout without a request object reports `operation=unknown`.
The server does not replay timeout errors.
A metadata timeout does not establish that the target record query is slow.
A connection timeout does not establish an ACL denial.

Advisory field checks can fail while the query still runs. The response then
includes a warning. Metadata timeout warnings identify the phase and trace ID.
Do not treat such a response as proof that the server checked every filter field.

## Read local diagnostic events

Each decorated tool invocation has a random `trace_id`. The logs contain:

| Event | Evidence |
| --- | --- |
| Tool start and finish | Tool name, total duration, outcome, and count of HTTP requests started. |
| Authorization start and finish | Duration, including authorization lock and browser waits. |
| HTTP start and finish | Request number, operation, method, duration, response size, and available HTTP status. |
| HTTP failure or cancellation | Duration, outcome, and timeout phase. |

Operation labels identify `dictionary`, `table_metadata`, `choices`,
`documentation`, `records`, `aggregate`, `attachment`, and `other`.
Local request events omit table names, record IDs, URLs, queries, headers,
and record contents. The CLI suppresses raw `httpx2` and `httpcore2` request logs
at INFO and DEBUG levels.

The outbound `X-Correlation-ID` carries the tool's trace ID.
It is not a ServiceNow transaction ID or a Sentry distributed trace ID.

Use these limits when interpreting events:

- `outcome=returned` means the tool returned a response. That response can contain an error.
- `outcome=cancelled` means cancellation reached the server. It does not establish an AI app timeout.
- A start without a finish can mean the process stopped or the logs are incomplete.
- HTTP request counts exclude token-exchange traffic. Authorization duration includes that exchange.
- Shared metadata requests belong to the initiating tool's trace. Other callers can reuse that work.

Concurrent tools have separate trace IDs. A shared metadata load can continue
after its initiating caller cancels the tool request. A caller's HTTP count does not include
requests started under another caller's shared metadata load.

For HTTP failures, `timeout_phase` is `connect`, `read`, `write`, `pool`, or
`unknown`. For failures other than HTTPX timeouts, it is `none`.

## Enable optional Sentry reporting

Sentry receives error reports only when an operator sets `SENTRY_DSN`.
Leave it unset to keep Sentry disabled. `SENTRY_ENVIRONMENT` labels reports.
If omitted, it uses `SERVICENOW_ENV`.

Add the settings to the environment used by your local MCP server:

```dotenv
SENTRY_DSN=https://example.invalid/project
SENTRY_ENVIRONMENT=dev
```

Replace the example DSN with the value for your approved Sentry project.
Restart the MCP process. Changes affect future reports.
Earlier events cannot recover evidence that the previous process did not record.

The stdio application initializes Sentry during `create_mcp_server()`.
At shutdown, `main()` flushes and closes the active client with two-second
limits. Shutdown errors do not propagate.

The SDK is a core dependency, but guarded imports allow operation if it is
unavailable. Public Sentry helpers do nothing when the SDK is unavailable or
Sentry is not initialized.

### What a Sentry event contains

Each decorated tool uses a separate Sentry scope, which isolates evidence for
that invocation. The event transaction name identifies the tool without renaming
a shared MCP span. Tool scopes remove inherited request contexts and breadcrumbs.
Server and aggregate cache contexts remain available.

| Context | Contents |
| --- | --- |
| `server` | Instance hostname, configured environment, production flag, and selected package. |
| `tool` | Tool name and redacted arguments, including positional inputs and declared defaults. |
| `tool_trace` | Local trace ID, elapsed duration, and HTTP request count. |
| `http_request` | Latest completed or failed HTTP attempt. |
| HTTP breadcrumbs | A bounded history of operation, method, request number, timing, status or timeout phase, response size, and pool mode. |

HTTP errors also set the indexed tags `http.status_code` and `http.operation`.
Query values, resolved labels, credentials, and payloads remain redacted.
The local trace ID connects Sentry evidence to stderr and `X-Correlation-ID`.
It is separate from Sentry's distributed trace ID.

Request evidence includes transmitted, allowlisted pagination and display controls.
It distinguishes an omitted offset from an explicit zero.
Query summaries describe length and lexical features, including text search,
JavaScript, OR, NQ, and ordering.

Summaries omit field names, search terms, values, and JavaScript source.
They do not check the query. Duplicate queries and queries over 8192
characters receive an explicit inspection omission.

HTTP error evidence describes content type, size, JSON shape, and reviewed static
`error.message` or `error.detail` phrases. Unknown text receives an omission marker.
The server does not inspect bodies over 8192 bytes or unread streams.

At most one transaction, request, or correlation header enters this evidence.
It must pass hex or UUID validation and checks for reflected credentials,
cookies, queries, or request bodies. Raw bodies, unrestricted headers, and
authentication challenges stay excluded. REST 401 diagnostics follow their
separate existing policy.

### Capture limits

Sentry configuration uses:

- `send_default_pii=False` and no local-variable capture.
- A package-derived release string, with `servicenow-platform-mcp@unknown` as the fallback.
- The MCP integration when the installed SDK provides it.
- `traces_sample_rate=0.1` and profiling disabled.
- At most 50 breadcrumbs per event.

These settings do not authorize new captured fields. Before expanding evidence,
review credential, personal-data, and untrusted-payload exposure.
Keep any new request evidence within the existing allowlists and limits.

## Aggregate runtime measurements

The local shared HTTP client records fixed-size counters and totals:

| Measurement | What it counts |
| --- | --- |
| Started, completed, and failed requests | Separate counts for request progress and transport failure. |
| `http_error_count` | Received HTTP responses with status 400 or higher. |
| Response bytes and duration | Downloaded bytes and total request duration. |
| Shared-pool requests | Requests using the local server's shared connection pool. |

`failed_request_count` includes transport failures and cancellation.
It excludes received HTTP error responses. For example, HTTP 400 counts as a
completed request and an HTTP error. Its bytes and duration count once.

Metadata counters record hits, misses, expirations, reloads, and invalidations
under these fixed names:

```text
choices
dictionary_chains
dictionary_fields
dictionary_script_fields
audit_table_config
audit_field_config
```

These measurements contain no credentials, request contents, URLs, or arbitrary
table names. Metadata caches exclude records, query results, flows, attachments,
preview tokens, and audit row counts. See [[Architecture]] for cache lifetimes.

## Contributor reference

Use the public helpers in `servicenow_mcp.sentry`:

| Helper | Purpose |
| --- | --- |
| `setup_sentry(settings)` | Initialize once when configured. |
| `capture_exception(exc)` | Report an exception when reporting is active. |
| `set_sentry_tag(key, value)` | Add an indexed search tag. |
| `set_sentry_context(key, data)` | Add structured context. |
| `sentry_tool_scope(tool)` | Isolate tool evidence and error attribution. |
| `add_sentry_breadcrumb(category, data)` | Add curated request history. |
| `shutdown_sentry()` | Flush pending reports and close the client. |

The autouse fixture in `tests/conftest.py` resets Sentry initialization state.
Unit tests also block remote Sentry initialization. They do not send real events.
Keep new measurements aggregate or scoped to one invocation.
Use fixed operation labels. Exclude raw bodies, tokens, callback data, and
unrestricted user input.

The diagnostics above describe the local stdio application.

Use [[Configuration]] for settings and [[Development]] for contributor checks.
