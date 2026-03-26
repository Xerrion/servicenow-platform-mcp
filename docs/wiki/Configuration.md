# Configuration

All configuration is handled through environment variables, loaded via [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). No configuration files or CLI flags are needed beyond environment variables.

---

## Environment Variables

| Variable | Type | Required | Default | Description |
|---|---|---|---|---|
| `SERVICENOW_INSTANCE_URL` | string | Yes | - | Full URL of the ServiceNow instance (must start with `https://`, trailing slash stripped automatically) |
| `SERVICENOW_USERNAME` | string | Yes | - | ServiceNow username for Basic Auth |
| `SERVICENOW_PASSWORD` | string (secret) | Yes | - | ServiceNow password (stored as `SecretStr`, masked in logs and debug output) |
| `MCP_TOOL_PACKAGE` | string | No | `"full"` | Which tool package to load. See [[Tool-Packages]] for all preset and custom options |
| `SERVICENOW_ENV` | string | No | `"dev"` | Environment label. Write operations blocked when set to `"prod"` or `"production"` |
| `MAX_ROW_LIMIT` | integer | No | `100` | Maximum records returned per query (valid range: 1-10000) |
| `LARGE_TABLE_NAMES_CSV` | string | No | `"syslog,sys_audit,sys_log_transaction,sys_email_log"` | Comma-separated list of tables considered "large" and requiring date-bounded queries |
| `SCRIPT_ALLOWED_ROOT` | string | No | `""` (disabled) | Root directory for local script file reads via `script_path` in artifact write tools. When set, all script paths must resolve under this directory |
| `SENTRY_DSN` | string | No | `""` (disabled) | Sentry DSN for error tracking. Sentry activates when this is set. See [[Telemetry]] |
| `SENTRY_ENVIRONMENT` | string | No | Falls back to `SERVICENOW_ENV` | Sentry environment label for grouping errors |

---

## Validation Rules

The server validates configuration at startup. Invalid values cause the server to exit with a descriptive error message.

### `SERVICENOW_INSTANCE_URL`

- Must start with `https://` - HTTP connections are not supported
- Trailing slash is stripped automatically (`https://dev12345.service-now.com/` becomes `https://dev12345.service-now.com`)

### `MAX_ROW_LIMIT`

- Must be an integer between 1 and 10000 (inclusive)
- Values outside this range cause a startup validation error
- This sets the upper bound for all query operations - user-supplied `limit` parameters are capped at this value

### `MCP_TOOL_PACKAGE`

- Must be either a valid preset package name or a comma-separated list of valid tool group names
- Invalid package names or group names cause a startup validation error
- See [[Tool-Packages]] for available presets and group names

---

## Computed Properties

These are derived from the configured environment variables and used internally by the server.

### `is_production`

Returns `True` when `SERVICENOW_ENV` is exactly `"prod"` or `"production"` (case-insensitive comparison). This property controls write gating - when `True`, all write operations return an error envelope.

### `large_table_names`

Parsed from `LARGE_TABLE_NAMES_CSV` into a frozen set of table names. Tables in this set require date-bounded query filters to prevent unbounded queries against high-volume system tables.

---

## File Loading

Settings are loaded from environment files in the working directory:

1. `.env` - Base configuration
2. `.env.local` - Local overrides (takes precedence over `.env`)

**Loading behavior:**

- `.env.local` values override `.env` values for the same variable
- Extra or unrecognized variables are silently ignored (no startup errors)
- Actual environment variables (set in the shell or MCP client config) take precedence over both files
- The `.env.example` file in the repository root provides a documented template:

```bash
cp .env.example .env.local
# Edit .env.local with your values
```

> **Important:** Never commit `.env.local` or files containing credentials to version control. The `.gitignore` already excludes these files.

---

## Production Mode

When `is_production` evaluates to `True` (i.e., `SERVICENOW_ENV` is `"prod"` or `"production"`), the following restrictions apply:

- **All write operations are blocked** - record create, update, delete, artifact create, artifact update, attachment upload, and attachment delete all return an error envelope
- **Read operations work normally** - queries, schema introspection, debug traces, investigations, and documentation generation are unaffected
- **There is no override mechanism** - production mode cannot be bypassed with additional configuration. If you need write access, use a sub-production instance (dev, test, staging, etc.)

This is a deliberate safety guardrail. The server is designed for platform introspection and debugging - it should not be the primary mechanism for modifying production data. See [[Safety-and-Policy]] for additional security details.

---

## Sentry Configuration

Error tracking via [Sentry](https://sentry.io/) is available for monitoring the MCP server in production environments. Since MCP servers run as child processes via stdio, the user never sees stderr output - Sentry provides visibility into errors that would otherwise go unnoticed.

**Activation requires:**

1. The `sentry-sdk` package is installed (it is a core dependency and always available)
2. `SENTRY_DSN` is set to a non-empty value

When both conditions are met, Sentry captures exceptions from tool execution, HTTP client errors, and configuration issues.

| Variable | Default | Description |
|---|---|---|
| `SENTRY_DSN` | `""` | Sentry DSN - the presence of this value is the activation gate |
| `SENTRY_ENVIRONMENT` | Falls back to `SERVICENOW_ENV` | Environment label for Sentry event grouping |

See [[Telemetry]] for full details on error tracking integration points and what data is captured.

---

## Example Configuration

A minimal `.env.local` for a development instance:

```bash
SERVICENOW_INSTANCE_URL=https://dev12345.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=my-dev-password
```

A more complete configuration:

```bash
# Connection
SERVICENOW_INSTANCE_URL=https://dev12345.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=my-dev-password

# Server behavior
MCP_TOOL_PACKAGE=developer
SERVICENOW_ENV=dev
MAX_ROW_LIMIT=200

# Error tracking
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=development
```

---

## Next Steps

- [[Getting-Started]] - Installation and MCP client configuration
- [[Tool-Packages]] - Choose the right tool package for your workflow
- [[Safety-and-Policy]] - Security guardrails and write gating details
- [[Telemetry]] - Sentry integration and error tracking
