# Configuration

The server loads settings from environment variables through
`pydantic-settings`. Settings are validated at startup, even when
`MCP_TOOL_PACKAGE=none`.

## Environment variables

| Variable | Required | Default | Validation or purpose |
| --- | --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | - | HTTPS origin without credentials, path, query, or fragment. One trailing slash is removed. |
| `SERVICENOW_OAUTH_CLIENT_ID` | Yes | - | Non-empty printable ASCII public client ID. Surrounding spaces are removed. |
| `SERVICENOW_OAUTH_SCOPE` | No | `useraccount` | One or more valid printable ASCII OAuth scope-token values separated by single spaces. Each token must be enabled on the public PKCE application. |
| `SERVICENOW_OAUTH_REDIRECT_URI` | No | `http://127.0.0.1:8765/oauth/callback` | Exact `http://127.0.0.1:<port>/oauth/callback`; port `1024`-`65535`. The URL must be registered. |
| `SERVICENOW_OAUTH_TIMEOUT_SECONDS` | No | `180` | Browser authorization wait, `1`-`600` seconds. |
| `MCP_TOOL_PACKAGE` | No | `full` | Preset or comma-separated tool groups. |
| `SERVICENOW_ENV` | No | `dev` | `prod` and `production` block local writes. |
| `MAX_ROW_LIMIT` | No | `100` | `1`-`10000`; cap for bounded paths that use it, not a global response cap. |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,sys_audit,syslog_transaction,sys_email_log` | Comma-separated table names that require date-bounded queries. |
| `HTTPX_TIMEOUT_SECONDS` | No | `30.0` | HTTPX connection/read/write/pool timeout, `1.0`-`600.0` seconds. Not a total MCP request deadline. |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | Metadata freshness, `1`-`86400` seconds. |
| `SENTRY_DSN` | No | Empty | Enables Sentry when non-empty. |
| `SENTRY_ENVIRONMENT` | No | Empty | Sentry environment label. Empty uses `SERVICENOW_ENV`. |

`SERVICENOW_OAUTH_SCOPE` is optional. The default is `useraccount`. A custom
value must contain valid OAuth scope-token values and those scopes must be
enabled on the ServiceNow Application Registry entry. An arbitrary string is
not a valid replacement.

## Authentication

The server supports public ServiceNow OAuth authorization-code PKCE S256 only.
Configure the ServiceNow application separately:

1. Set **Public Client** to `true`.
2. Enable PKCE **S256**.
3. Enable the configured scope, `useraccount` by default.
4. Register the exact loopback redirect URI.

The browser and stdio process must run on the same machine. The first outbound
ServiceNow call starts authorization. The authorizing user's roles and ACLs
control REST access. The public client ID identifies the application, not a
service account.

Access tokens stay in process memory with their expiry. REST requests use
`Authorization: Bearer <access_token>` and never put tokens in URLs. Restart or
expiry requires authorization again. A REST 401 discards the matching token and
does not replay the request.

Do not set `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, or
`SERVICENOW_PASSWORD`. Non-empty values fail startup. There is no Basic Auth or
API-key fallback. `SERVICENOW_OAUTH_CLIENT_SECRET` is ignored; remove stale
values and do not configure a secret.

## REST API access policies

Token issuance does not establish REST access. An API-key-only policy can reject
an OAuth Bearer request after token issuance. An administrator may see HTTP 401
with `WWW-Authenticate: API_KEY` outside the tool response.

For migration:

1. Identify the policy for the failed resource and method.
2. Adjust only that policy to allow the intended OAuth Bearer request.
3. Preserve unrelated policies and restrictions.
4. Test a small read-only request with the intended user.
5. Retire an obsolete API-key requirement only within the approved scope.

Do not disable global protection or add an API key to this server.

## Tool packages

`MCP_TOOL_PACKAGE` selects which tools load. It is not a ServiceNow
authorization boundary. See [[Tool-Packages]] for package contents and custom
group names.

## Safety-related settings

`MAX_ROW_LIMIT` caps bounded reads that use it. Tables in
`LARGE_TABLE_NAMES_CSV` require a structural date filter. The default list is
`syslog`, `sys_audit`, `syslog_transaction`, and `sys_email_log`.

`METADATA_CACHE_TTL_SECONDS` controls choice, dictionary, script-field, and
audit-configuration metadata. It does not cache records, query results, flows,
attachments, preview tokens, or audit row counts. Lower values increase
metadata requests.

## File loading

The server reads configuration in this order:

1. `.env` in the process working directory.
2. `.env.local` in that directory.
3. Process environment variables.

Later sources override earlier sources. Restart the full MCP process after
changes. Never commit `.env` or `.env.local`.

## Troubleshooting

| Observed error or event | Safe action |
| --- | --- |
| `Cannot bind OAuth loopback port` | Close only a known conflicting listener, or configure and register another allowed port. |
| `ServiceNow authorization timed out` | Complete authorization on the same machine before timeout. Check the exact callback URL. |
| ServiceNow rejects the scope | Enable `useraccount`, or set `SERVICENOW_OAUTH_SCOPE` to an enabled valid scope-token value. |
| `Cannot open the local browser` | Check local browser setup. This is a launch failure, not proof of an Application Registry error. |
| Error on the authorization page | Check Public Client, PKCE S256, client ID, scope, and exact redirect URL. |
| `OAuth token exchange rejected (HTTP ...)` | Check the public application and OAuth settings. |
| REST 401 or `User Not Authenticated` | Authorize on the next call. If it persists, check REST policy, scopes, and user access. |
| HTTP 403 | Check REST resource permissions, roles, and table and field ACLs. |
| `UPSTREAM_TIMEOUT` | Use `error.phase`, `error.operation`, and `error.trace_id` to locate the failed HTTP operation in stderr. See [[Telemetry]]. |
| MCP `-32001: Request timed out` | The client stopped waiting. Inspect the server trace and the client's deadline before changing the HTTP timeout. |

Report sanitized evidence and an allowed transaction ID when available. Do not
attach raw headers, tokens, callback data, or query strings.
