# Configuration

All configuration is handled through environment variables, loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

---

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | - | HTTPS origin without credentials, path, query, or fragment; one trailing slash is removed |
| `SERVICENOW_OAUTH_CLIENT_ID` | Yes | - | Public ServiceNow OAuth client ID; non-empty printable ASCII, with surrounding spaces removed |
| `SERVICENOW_OAUTH_SCOPE` | Yes | None | Exactly `useraccount`; enable this scope on the public PKCE application |
| `SERVICENOW_OAUTH_REDIRECT_URI` | No | `http://127.0.0.1:8765/oauth/callback` | Exactly `http://127.0.0.1:<port>/oauth/callback`, port 1024-65535; must be registered |
| `SERVICENOW_OAUTH_TIMEOUT_SECONDS` | No | `180` | Browser authorization wait, 1-600 seconds |
| `MCP_TOOL_PACKAGE` | No | `"full"` | Tool package (`full`, `readonly`, `core_readonly`, `none`) or comma-separated groups |
| `SERVICENOW_ENV` | No | `"dev"` | Set to `"prod"` or `"production"` to block all write operations |
| `MAX_ROW_LIMIT` | No | `100` | Cap for bounded paths that use it (1-10000); not a global response cap |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,sys_audit,sys_log_transaction,sys_email_log` | Tables requiring date-bounded queries |
| `HTTPX_TIMEOUT_SECONDS` | No | `30` | ServiceNow HTTP timeout in seconds (1-600, finite) |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | Metadata cache freshness in seconds (1-86400) |
| `SENTRY_DSN` | No | `""` | Sentry DSN for error tracking |
| `SENTRY_ENVIRONMENT` | No | `""` | Grouping label for Sentry; empty uses `SERVICENOW_ENV` |

---

## Authentication

The server supports public ServiceNow OAuth authorization-code PKCE S256 only.
Configure the application in ServiceNow separately from the local server.

### ServiceNow Application Registry

In **System OAuth > Application Registry**, create or select the application:

1. Set **Public Client** to `true`.
2. Enable authorization-code PKCE with **S256**.
3. Enable the `useraccount` scope.
4. Register the exact redirect URL `http://127.0.0.1:8765/oauth/callback`.
5. Save the application and copy its client ID.

User roles, REST-resource permissions, and table and field ACLs still apply.

### Local configuration

Create `.env.local` in the MCP server's working directory:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_SCOPE=useraccount
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
SERVICENOW_OAUTH_TIMEOUT_SECONDS=180
```

The scope must be exactly `useraccount`. Missing or different values fail
startup. The redirect URI must exactly match the registered URL and the format
`http://127.0.0.1:<port>/oauth/callback`, with port `1024`-`65535`.
`localhost`, other paths, query strings, and fragments are not accepted.
If you change the port, register the complete new URL in ServiceNow.

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`
from the environment and dotenv files. Non-empty values fail startup, including
whitespace-only values. There is no Basic Auth or API-key fallback.
`SERVICENOW_OAUTH_CLIENT_SECRET` is ignored, not used or rejected. Remove stale
values.

Settings are validated at startup even for the `none` package. Restart the
full server after changing configuration. See [File Loading](#file-loading)
for source precedence.

### Authorization lifecycle and identity

The first tool call that needs ServiceNow access opens the default browser.
The browser and stdio process must run on the same machine. The temporary IPv4
loopback listener starts before the browser and validates callback state, path,
and Host. It closes with its accepted connections before token exchange and
on denial, timeout, or cancellation. It is not an MCP HTTP endpoint.

Authorization sends an S256 challenge and random `state`. Code exchange sends
the verifier, not state or a client secret. See the
[exact OAuth request fields](../../README.md#exact-oauth-requests).

REST requests run as the ServiceNow user who completed browser authorization.
The public client ID identifies the application, not a separate service account.
The user's roles and ACLs remain in force.

REST calls use `Authorization: Bearer <access_token>`, never tokens in URLs.
Only the access token and its expiry remain in process memory. Restart or expiry
requires browser authorization on the next outbound call. Concurrent calls share one
successful authorization within a server process and reuse its valid token.

A REST 401 discards only the matching token without replaying the request.
The next outbound call authorizes again unless a newer concurrent grant exists.
Never log or persist tokens, authorization codes, callback URLs or query
strings, or PKCE verifiers.

### REST API access policies

Token issuance does not establish REST access. An old API-key-only policy can
reject an OAuth Bearer request. Administrator-side inspection may show
`HTTP 401` with `WWW-Authenticate: API_KEY`. The server's sanitized evidence
omits this unrecognized scheme; it does not reproduce that header value.

Ask the administrator to identify the policy for the failed resource and
method. Adjust or replace only the affected policy to allow the intended
OAuth Bearer requests. Preserve unrelated policies and restrictions. Test a
small read-only request with the intended user. Retire an obsolete API-key
requirement only within the approved migration scope, after checking other
consumers. Do not disable global protection or unrelated policies. Do not add
an API key to the MCP configuration.

### Troubleshooting matrix

| Observed error or event | Safe action |
| --- | --- |
| `Cannot bind OAuth loopback port` | Close only a known conflicting listener, or configure and register another allowed port. |
| `ServiceNow authorization timed out` | Complete authorization on the same machine before the timeout. Check the exact callback URL and retry. |
| Missing or invalid scope | Set exactly `useraccount` locally and enable it on the Application Registry entry. |
| `Cannot open the local browser` | Check the local browser setup. This is a launch failure, not proof of an application error. |
| Error on the ServiceNow authorization page | Check **Public Client=true**, PKCE S256, client ID, scope, and exact redirect URL. |
| `OAuth token exchange rejected (HTTP ...)` | Check the public application and OAuth settings. This is separate from a later REST failure. |
| REST 401 or `User Not Authenticated` | The request was not replayed. Authorize on the next outbound call. If a new token also fails, check REST policy, scopes, and user access with the administrator. |
| REST 401 plus administrator-observed `WWW-Authenticate: API_KEY` | Migrate only the affected policy. This scheme is omitted from sanitized tool evidence. |
| HTTP 403 | Check REST-resource permissions, user roles, and table and field ACLs. Not every 403 proves a table ACL denial. |
| Token expiry or server restart | Complete browser authorization again on the next outbound call. |

Report only sanitized error evidence and an allowed transaction ID when
available.
Do not attach raw headers or callback data.

---

## Tool Package Configuration

The `MCP_TOOL_PACKAGE` variable controls the available tool surface.

### Presets

- `full`: 15 total tools (14 package tools plus always-on `list_tool_packages`; includes `analysis` and `code_search`).
- `readonly`: 11 total tools (excludes all write tools and `service_catalog`; includes `record_read`, `attachment`, `analysis`, `audit`, `flow`, and `code_search`).
- `core_readonly`: 4 total tools (`query`, `describe`, `attachment`, `list_tool_packages`).
- `none`: Only `list_tool_packages`.

### Custom Packages

List group names: `MCP_TOOL_PACKAGE="query,describe,investigate"`.
`service_catalog` selects its single group. The `record_write` group loads both
`record_write` and `record_apply`; do not add `record_apply` as a group.
`build_query` is not a valid group.
The `attachment` group is read-only. Add `attachment_write` explicitly to opt in to attachment upload and delete.

## Metadata Cache

`METADATA_CACHE_TTL_SECONDS` controls the freshness window for the metadata cache. It applies to choice mappings, dictionary table chains and field metadata, script-field discovery, and audit table and field configuration. The default is 300 seconds. Values must be between 1 and 86400; zero is invalid.

The cache uses monotonic TTLs, synchronous reloads, same-key single-flight loading, independent-key concurrency, explicit invalidation, and a 1,000-entry LRU bound. It does not cache records, query results, flows, attachments, preview tokens, or audit row counts. A lower TTL improves freshness at the cost of more metadata requests.

---

## Write Gating & Production Mode

When `SERVICENOW_ENV` is `"prod"` or `"production"`:

- `record_write`, `record_apply`, and `attachment_write` operations are rejected.
- `service_catalog` order/cart operations are rejected.
- All read operations (`query`, `describe`, `attachment` list/get) remain functional.

---

## Inline Record Writes

`record_write` takes all field values through the JSON string `data`. No script
directory configuration is needed. The total input limit is 256 KiB of UTF-8
JSON, including field names and escaping. See [[Tool-Reference]] for field
selection and XML validation.

---

## File Loading

The server reads these sources in increasing precedence:

1. `.env` in the process working directory.
2. `.env.local` in the same directory, overriding `.env`.
3. Process environment variables, overriding both files.

Restart the full MCP process after changing settings. Refer to `.env.example`
for a template. **Never commit `.env` or `.env.local` to version control.**
