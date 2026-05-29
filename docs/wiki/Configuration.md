# Configuration

## Where settings come from

The server loads configuration through pydantic-settings. Values are resolved from three sources in this order (last wins):

1. `.env` file in the working directory.
2. `.env.local` file in the working directory.
3. Process environment variables.

Use `.env.local` for credentials during development - it belongs in `.gitignore`.

## Settings reference

Every field in the `Settings` class maps to a single environment variable.

| Env var | Type | Default | Range | What it controls |
|---------|------|---------|-------|------------------|
| `SERVICENOW_INSTANCE_URL` | `str` | (required) | - | Full instance URL. Must be HTTPS. Trailing slash is stripped automatically. |
| `SERVICENOW_USERNAME` | `str` | (required) | - | ServiceNow user for Basic Auth. |
| `SERVICENOW_PASSWORD` | `SecretStr` | (required) | - | Password for the above user. Never logged or exposed in responses. |
| `MCP_TOOL_PACKAGE` | `str` | `"full"` | - | Active tool package preset or comma-separated group list. See [[Tool-Packages]]. |
| `SERVICENOW_ENV` | `str` | `"dev"` | - | Environment label. Controls production write-gating (see below). |
| `MAX_ROW_LIMIT` | `int` | `100` | 1-10000 | Maximum rows any single `query` call may return. |
| `HTTPX_TIMEOUT_SECONDS` | `float` | `30.0` | 1.0-600.0 | HTTP timeout for all ServiceNow API calls. |
| `LARGE_TABLE_NAMES_CSV` | `str` | `"syslog,sys_audit,sys_log_transaction,sys_email_log"` | - | Comma-separated tables that require a date-bounded filter in queries. |
| `SCRIPT_ALLOWED_ROOT` | `str` | `""` | - | Filesystem root for `script_path` in `record_write`. Empty disables script-file reads. |
| `SENTRY_DSN` | `str` | `""` | - | Sentry DSN. Empty disables Sentry integration. |
| `SENTRY_ENVIRONMENT` | `str` | `""` | - | Sentry environment tag (e.g. `"staging"`). |

## Production mode (`is_production`)

The `is_production` flag is a cached property derived from `SERVICENOW_ENV`. It evaluates to `true` when the value equals `prod` or `production`.

When production mode is active, **all** write operations are blocked. This includes `record_write`, `record_apply`, `attachment_write` (upload and delete), and write-gated Service Catalog actions (`order_now`, `add_to_cart`, `cart_submit`, `cart_checkout`). Blocked calls return a policy-error envelope rather than executing.

## `SCRIPT_ALLOWED_ROOT`

When `record_write` receives a `script_path` argument, the path is validated against this root:

- The root directory is resolved first (before any user-supplied path). It must exist and be a directory.
- The resolved script path must reside under the root. Symlink escapes are rejected.
- Maximum file size is 1 MB.
- All path-validation failures - file missing, not a regular file, outside root, symlink escape - produce a single opaque error: `"script_path is not readable or is outside the allowed root"`.
- When the target field has `internal_type` of `xml`, the content is validated as well-formed XML before any platform call.

When the setting is empty (the default), any call supplying `script_path` fails immediately.

## Sentry settings

Two environment variables control the optional Sentry integration:

- `SENTRY_DSN` - the DSN string. When empty, the SDK is not initialized.
- `SENTRY_ENVIRONMENT` - the environment tag attached to events.

For SDK defaults, argument redaction, and captured context details, see [[Telemetry]].

## Stale-config warnings

The `.env.example` file shipped with the repository lists example preset names (`itil`, `developer`, `analyst`, `incident_management`, and others) that do not exist in the current package registry. Only `full`, `readonly`, `core_readonly`, and `none` are valid presets. Custom comma-separated group names are also accepted. See [[Tool-Packages]] for the current registry.

## Example `.env.local`

```bash
SERVICENOW_INSTANCE_URL=https://myinstance.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=secret
MCP_TOOL_PACKAGE=full
SERVICENOW_ENV=dev
SCRIPT_ALLOWED_ROOT=/home/user/scripts
```
