# Telemetry

## What is instrumented - and what is not

Sentry is the only telemetry channel. The server does not expose a metrics endpoint, does not emit Prometheus scrape targets, and does not integrate OpenTelemetry. When Sentry is not configured, every telemetry function is a no-op and nothing leaves the process.

## Enabling Sentry

Set the `SENTRY_DSN` environment variable to a valid Sentry project DSN. Optionally set `SENTRY_ENVIRONMENT` to override the environment tag (falls back to `SERVICENOW_ENV`). Leave `SENTRY_DSN` empty - the default - to disable entirely.

When disabled the server logs `"Sentry disabled (no DSN configured)"` at debug level. If the `sentry-sdk` package is missing at runtime it logs `"Sentry disabled (sentry-sdk not installed)"` instead.

See [[Configuration]] for the full settings table.

## SDK defaults

| Setting | Value | Notes |
|---|---|---|
| `send_default_pii` | `False` | No cookies, IPs, or user data in breadcrumbs |
| `traces_sample_rate` | `0.1` | 10% of transactions sampled for performance tracing |

Both values are operator-tunable via standard Sentry SDK environment variables (e.g. `SENTRY_TRACES_SAMPLE_RATE`). The server does not override SDK-level env var configuration.

## What gets captured

Exceptions are captured by the opaque generic `Exception` arm of `safe_tool_call`. This arm fires for unclassified failures - `RuntimeError`, `OSError`, httpx transport errors, and anything else not covered by the four verbose arms. When it fires, the caller receives only `"Internal error (correlation_id=<uuid>)"`. The full detail is logged via `logger.exception` and sent to Sentry via `sentry_capture(e)`.

The four verbose arms (`ACLError`, `ForbiddenError`, `ServiceNowMCPError`, `ValueError`) also call `sentry_capture(e)` but return caller-actionable messages in the error envelope rather than an opaque string.

## Argument redaction

Before attaching tool arguments to Sentry context, `@tool_handler` replaces values for keys in `_SENSITIVE_ARG_KEYS` with `"***REDACTED***"`. The full set (case-insensitive, exact-name match):

```
data, content_base64, value, script_path, encoded_query, params,
password, token, secret, api_key, authorization, variables, conditions, text
```

`correlation_id` is dropped from the args dict entirely - it is already promoted to a top-level context field.

Redaction is shallow. JSON-shaped string arguments are replaced as a whole, not parsed and field-redacted. A `data` parameter containing a serialized record is replaced wholesale; nested keys inside that JSON are never inspected by this pass.

## Tags and context

Each tool invocation sets:

- **Tags**: `tool.name`, `tool.correlation_id` - indexed and searchable in Sentry.
- **Structured context** (`tool`): `{name, correlation_id, args}` where `args` has sensitive keys redacted per the rules above.

Additionally:

- **HTTP context** (set by `_raise_for_status` on error): `{status_code, method, url}` with query parameters stripped from the URL.
- **Server context** (set once at boot): `{instance_url (hostname only), environment, is_production, tool_package}`.

## MCPIntegration conditional load

If the installed Sentry SDK exposes `sentry_sdk.integrations.mcp.MCPIntegration`, it is included in the integrations list passed to `sentry_sdk.init`. Otherwise the integrations list is empty. No error is raised when the integration is absent - newer SDK versions add it; older ones skip it silently.

## Operational concerns

**Flush on shutdown.** `shutdown_sentry()` is called in the `finally` block of `main()`. It flushes pending events with a 2-second timeout and closes the client with a second 2-second timeout. Safe to call multiple times.

**Sampling tuning.** The default `traces_sample_rate=0.1` means 90% of performance transactions are dropped. For high-volume deployments this is appropriate. For low-volume development instances, set `SENTRY_TRACES_SAMPLE_RATE=1.0` to capture everything.

**Idempotent init.** `setup_sentry` is idempotent - only the first call initializes the SDK. Subsequent calls return immediately.

**Graceful degradation.** All public functions in `sentry.py` are safe to call when `sentry-sdk` is not importable. The module sets `HAS_SENTRY = False` and every function becomes a no-op.

## What is NOT sent

- **Secrets.** `_SENSITIVE_ARG_KEYS` redaction removes tool arguments that could carry credentials before any Sentry context is attached.
- **Denied-table values.** Policy enforcement (`check_table_access`) raises before any data is fetched from restricted tables; there is no path where denied-table content reaches a logging or telemetry call.
- **Sensitive-field values.** Response masking (`mask_sensitive_fields`, `mask_audit_entry`) replaces field values with `***MASKED***` before the response envelope is built. Even if the envelope were inadvertently logged, masked values are all that remain.
- **Default PII.** `send_default_pii=False` instructs the Sentry SDK to omit cookies, IP addresses, and user identifiers from breadcrumbs and event payloads.

---

See also: [[Configuration]] for the env var table, [[Safety-and-Policy]] for field masking and denied-table policies, [[Architecture]] for the full request flow and `@tool_handler` contract.
