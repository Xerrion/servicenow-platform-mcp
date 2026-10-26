# Configuration

Settings tell the server which ServiceNow instance to use and which tools to
make available. Add them to your AI app's server configuration. The server can
also read `.env` and `.env.local` in its working folder. Environment variables
take precedence. Restart the server after changing a setting.

For connection examples, see the [installation guide](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md).

## Required settings

- `SERVICENOW_INSTANCE_URL`: the HTTPS address of your instance, such as
  `https://example.service-now.com`. Leave out page paths and credentials.
- `SERVICENOW_OAUTH_CLIENT_ID`: the public client ID from your ServiceNow OAuth
  application.

Sign in through the browser when asked. Username and password settings and API
keys are not supported.

## Choose access

- `MCP_TOOL_PACKAGE` chooses the available tools. Its default is `readonly`.
  See [Tool Packages](Tool-Packages.md) for the choices.
- `SERVICENOW_ENV` controls the server's write protection. Its default is `prod`.
  Use `dev` or `test` only after confirming that the instance URL points to the
  intended non-production instance. The setting does not identify the instance.

ServiceNow still checks your account's access. See [Safety and Policy](Safety-and-Policy.md).

## Sign-in settings

- `SERVICENOW_OAUTH_SCOPE` defaults to `useraccount`. Enable each requested scope
  on the OAuth application.
- `SERVICENOW_OAUTH_REDIRECT_URI` defaults to
  `http://127.0.0.1:8765/oauth/callback`. It must match the registered redirect
  URL. Another port must be between 1024 and 65535.
- `SERVICENOW_OAUTH_TIMEOUT_SECONDS` sets the sign-in wait. The default is 180
  seconds; the allowed range is 1 to 600 seconds.
- `SERVICENOW_OAUTH_CACHE` selects `memory` or `os`. The default is `memory`.

## Optional OS OAuth cache

The default cache keeps tokens in server memory. Select `os` to store a refresh
token in the native credential store. The server can reuse it for up to 30 days.
An expired entry is cleared when the server next loads it. Access tokens stay
in memory. The store must be available and unlocked. Supported stores are
macOS Keychain, Windows Credential Manager, and Linux Secret Service.

Use cache-enabled server processes one at a time. If the store is unavailable,
the server reports an error; it does not use a different store.

To clear a saved entry, first stop the server processes. In your terminal, set
`SERVICENOW_INSTANCE_URL` and `SERVICENOW_OAUTH_CLIENT_ID` to the same values
the app uses. Also set `SERVICENOW_OAUTH_SCOPE` and
`SERVICENOW_OAUTH_REDIRECT_URI` if the app uses values other than the defaults.

For the published package, run:

```bash
uv run --no-project --with servicenow-platform-mcp python -m servicenow_mcp.credential_cache clear
```

For a source checkout, run this command in its root folder:

```bash
uv run python -m servicenow_mcp.credential_cache clear
```

You may need to sign in again. Clearing this entry does not revoke a token in
ServiceNow.

## Limits and diagnostics

- `MAX_ROW_LIMIT`: maximum records returned by a query. Default: 100. Range:
  1 to 10,000.
- `LARGE_TABLE_NAMES_CSV`: tables that require a date filter. The default is
  `syslog,sys_audit,syslog_transaction,sys_email_log`.
- `HTTPX_TIMEOUT_SECONDS`: time allowed for HTTP operations. Default: 30 seconds.
  Range: 1 to 600 seconds.
- `METADATA_CACHE_TTL_SECONDS`: how long metadata stays cached. Default: 300
  seconds. Range: 1 to 86,400 seconds.
- `SENTRY_DSN`: enables optional error reporting when set. `SENTRY_ENVIRONMENT`
  labels those reports and defaults to `SERVICENOW_ENV`. See [Telemetry](Telemetry.md).

## REST API access policies

A successful browser sign-in does not guarantee API access. If a read returns
401 or 403, ask your ServiceNow administrator to check the account, OAuth
scope, REST API access policies, and table and field permissions. Provide the
status code and request context without sharing tokens.
