# Configuration

All configuration is handled through environment variables, loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

---

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | - | Full URL (must start with `https://`) |
| `SERVICENOW_OAUTH_CLIENT_ID` | Yes | - | ServiceNow OAuth client ID |
| `SERVICENOW_OAUTH_CLIENT_SECRET` | For confidential apps | Empty | Non-empty selects confidential flow without PKCE; empty selects public PKCE S256. Sent only in HTTPS token-endpoint form bodies |
| `SERVICENOW_OAUTH_SCOPE` | Yes | None | Non-empty, space-separated scopes allowed by the application, such as `useraccount`; required in both modes |
| `SERVICENOW_OAUTH_REDIRECT_URI` | No | `http://127.0.0.1:8765/oauth/callback` | Exact loopback path with port 1024-65535; must be registered |
| `SERVICENOW_OAUTH_TIMEOUT_SECONDS` | No | `180` | Browser authorization wait, 1-600 seconds |
| `MCP_TOOL_PACKAGE` | No | `"full"` | Tool package (`full`, `readonly`, `core_readonly`, `none`) or comma-separated tools |
| `SERVICENOW_ENV` | No | `"dev"` | Set to `"prod"` or `"production"` to block all write operations |
| `MAX_ROW_LIMIT` | No | `100` | Max records per query (1-10000) |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,...` | Tables requiring date-bounded queries |
| `HTTPX_TIMEOUT_SECONDS` | No | `30` | ServiceNow HTTP timeout in seconds (1-600) |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | Metadata cache freshness in seconds (1-86400) |
| `SENTRY_DSN` | No | `""` | Sentry DSN for error tracking |
| `SENTRY_ENVIRONMENT` | No | - | Grouping label for Sentry (defaults to `SERVICENOW_ENV`) |

---

## Authentication

Use a ServiceNow OAuth authorization-code client. For confidential apps, supply
`SERVICENOW_OAUTH_CLIENT_SECRET` privately. This disables PKCE; confirm that the app
accepts client credentials in the token-endpoint form body for code exchange and
refresh. Leave it empty only for a confirmed public app with PKCE S256. Public
authorization sends an S256 challenge and code exchange sends its verifier,
without a client secret. Both modes send the same `state` on authorization and
code exchange. Refresh is confidential-only and never sends PKCE parameters. The browser
and stdio process must run on the same machine. Register the exact redirect URI
on the ServiceNow application and configure its allowed scopes and user roles.
The first outbound request opens the browser and starts a temporary loopback
receiver. This is not an MCP HTTP endpoint.

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`.
Non-empty legacy credentials are rejected without fallback. Access and refresh
tokens stay in memory. In confidential mode, expiry triggers refresh on the next outbound request.
A REST 401 invalidates the access token without replaying the call. A tool retry
refreshes it if possible. Missing refresh tokens or HTTP 400 `invalid_grant` require
a new browser flow; other refresh errors do not open a browser. Restarting loses
both tokens. Public clients authorize again after expiry or REST rejection,
even if a refresh token was issued. Never log tokens, authorization codes, or the client secret.
Do not add `offline_access` unless the administrator confirms it is supported.
Set `SERVICENOW_OAUTH_SCOPE=useraccount` when allowed by the application, or use
other administrator-confirmed scopes. Scope is required in both modes. Values
must contain printable ASCII; surrounding spaces are trimmed. Missing, empty,
whitespace-only values and control characters are rejected.

---

## Tool Package Configuration

The `MCP_TOOL_PACKAGE` variable controls the available tool surface.

### Presets

- `full`: 15 total tools (14 package tools plus always-on `list_tool_packages`; includes `analysis` and `code_search`).
- `readonly`: 11 total tools (excludes all write tools and `service_catalog`; includes `record_read`, `attachment`, `analysis`, `audit`, `flow`, and `code_search`).
- `core_readonly`: 4 total tools (`query`, `describe`, `attachment`, `list_tool_packages`).
- `none`: Only `list_tool_packages`.

### Custom Packages

You can list specific tools: `MCP_TOOL_PACKAGE="query,describe,investigate"`. `build_query` is not a valid tool name.
*Note: `service_catalog` and `record_write` are now tool names. `record_write` should typically be paired with `record_apply` for the preview flow.*
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

The server automatically reads configuration from:

1. `.env.local` (highest priority)
2. `.env`
3. Shell environment variables (override files)

Refer to `.env.example` for a template. **Never commit `.env.local` to version control.**
