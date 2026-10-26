# Diagnostics and error reporting

The server writes operational messages to standard error. Your AI app may show
these messages in its server logs. Use them to find a failed request and its
trace ID.

## Local timeout diagnostics

If a request times out:

1. Find the trace ID in the error or the server log.
2. Find matching messages for the tool and HTTP request.
3. Note the operation, timeout phase, and duration.
4. Check the connection and ServiceNow response with your administrator.

A timeout may occur while connecting, reading, writing, or waiting for a
connection. A metadata request can fail before the requested operation begins.
If a write times out, check its target before starting another change. The
server may not know whether ServiceNow completed it.

`HTTPX_TIMEOUT_SECONDS` controls the HTTP timeout. Its default is 30 seconds.
Changing it does not fix an access or connection problem. See [Configuration](Configuration.md).

## Optional Sentry reporting

Sentry reporting is disabled when `SENTRY_DSN` is empty. Set it to send error
reports to your Sentry project. `SENTRY_ENVIRONMENT` supplies a label; if left
empty, the server uses `SERVICENOW_ENV`. Restart the server after changing these
settings.

Reports can include error details, tool names, request timing, status codes,
and aggregate counts. The server configures Sentry without local variables or
user information. Review your Sentry project's access and retention settings
before enabling it. Do not assume every possible error is free of sensitive
data.

## Sharing a diagnostic

Share the tool name, time, trace ID, status, and relevant timeout phase. Remove
tokens, instance data, query values, record contents, and personal information
from any log or screenshot before sharing it.
