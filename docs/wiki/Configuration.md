# Configuration

All configuration is handled through environment variables, loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICENOW_INSTANCE_URL` | Yes | - | Full URL (must start with `https://`) |
| `SERVICENOW_API_KEY` | Conditional | - | ServiceNow API key. When set, API-key authentication is used instead of Basic Auth. |
| `SERVICENOW_USERNAME` | Conditional | - | ServiceNow username for Basic Auth. Required when `SERVICENOW_API_KEY` is not set. |
| `SERVICENOW_PASSWORD` | Conditional | - | ServiceNow password for Basic Auth. Required when `SERVICENOW_API_KEY` is not set. |
| `MCP_TOOL_PACKAGE` | No | `"full"` | Tool package (`full`, `readonly`, `core_readonly`, `none`) or comma-separated tools |
| `SERVICENOW_ENV` | No | `"dev"` | Set to `"prod"` or `"production"` to block all write operations |
| `MAX_ROW_LIMIT` | No | `100` | Max records per query (1-10000) |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,...` | Tables requiring date-bounded queries |
| `SCRIPT_ALLOWED_ROOT` | No | `""` | Root directory for `script_path` in `record_write` |
| `HTTPX_TIMEOUT_SECONDS` | No | `30` | ServiceNow HTTP timeout in seconds (1-600) |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | Metadata cache freshness in seconds (1-86400) |
| `SENTRY_DSN` | No | `""` | Sentry DSN for error tracking |
| `SENTRY_ENVIRONMENT` | No | - | Grouping label for Sentry (defaults to `SERVICENOW_ENV`) |

---

## Authentication

Choose one authentication method:

- **API key:** Set `SERVICENOW_API_KEY`. The server sends it in the `x-sn-apikey` request header and does not use `SERVICENOW_USERNAME` or `SERVICENOW_PASSWORD`, even if they are also set.
- **Basic Auth:** Leave `SERVICENOW_API_KEY` unset and set both `SERVICENOW_USERNAME` and `SERVICENOW_PASSWORD`.

Keep credentials and API keys out of version control. Store local values in `.env.local` or in your MCP client's secret or environment-variable configuration.

---

## Tool Package Configuration

The `MCP_TOOL_PACKAGE` variable controls the available tool surface.

### Presets
- `full`: 14 total tools (13 package tools plus always-on `list_tool_packages`; includes `code_search`).
- `readonly`: 10 total tools (excludes all write tools and `service_catalog`; includes `record_read`, `attachment`, `audit`, `flow`, and `code_search`).
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

## Script Path Security

To use the `script_path` feature in `record_write`, you **must** configure `SCRIPT_ALLOWED_ROOT` with an absolute path. The server will reject any `script_path` that resolves outside of this directory.

Example:
`SCRIPT_ALLOWED_ROOT=/Users/dev/projects/servicenow-scripts`

---

## File Loading

The server automatically reads configuration from:
1. `.env.local` (highest priority)
2. `.env`
3. Shell environment variables (override files)

Refer to `.env.example` for a template. **Never commit `.env.local` to version control.**
