# Configuration

All configuration is handled through environment variables, loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SERVICENOW_INSTANCE_URL` | Yes | - | Full URL (must start with `https://`) |
| `SERVICENOW_USERNAME` | Yes | - | ServiceNow username |
| `SERVICENOW_PASSWORD` | Yes | - | ServiceNow password |
| `MCP_TOOL_PACKAGE` | No | `"full"` | Tool package (`full`, `readonly`, `core_readonly`, `none`) or comma-separated tools |
| `SERVICENOW_ENV` | No | `"dev"` | Set to `"prod"` or `"production"` to block all write operations |
| `MAX_ROW_LIMIT` | No | `100` | Max records per query (1-10000) |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,...` | Tables requiring date-bounded queries |
| `SCRIPT_ALLOWED_ROOT` | No | `""` | Root directory for `script_path` in `record_write` |
| `SENTRY_DSN` | No | `""` | Sentry DSN for error tracking |
| `SENTRY_ENVIRONMENT` | No | - | Grouping label for Sentry (defaults to `SERVICENOW_ENV`) |

---

## Tool Package Configuration

The `MCP_TOOL_PACKAGE` variable controls the available tool surface.

### Presets
- `full`: All 12 unified tools (includes the `build_query` helper).
- `readonly`: 8 tools (excludes `record_write`, `record_apply`, `service_catalog`, and `build_query`; includes `record_read`).
- `core_readonly`: 5 tools (`query`, `describe`, `attachment`, `attachment_write`, `list_tool_packages`).
- `none`: Only `list_tool_packages`.

### Custom Packages
You can list specific tools: `MCP_TOOL_PACKAGE="query,describe,investigate"`.
*Note: `service_catalog` and `record_write` are now tool names. `record_write` should typically be paired with `record_apply` for the preview flow.*

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
