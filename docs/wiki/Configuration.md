# Configuration

Use these settings to select your instance, available tools, and local write protection.
For the complete connection procedure, see the
[installation guide](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md).

This page describes the live local stdio connection.

## Minimal read-only configuration

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=prod
```

Replace the instance address and public client ID with your administrator's values.
The client ID identifies the OAuth application. It is not a client secret.

Set the package explicitly. Its default is `readonly`.
`SERVICENOW_ENV=prod` blocks local writes even when those tools load.
The instance URL selects the instance. The environment label does not detect
whether that instance is production.

## Where settings come from

The server reads values in this order:

1. `.env` in the directory where the process starts.
2. `.env.local` in that directory.
3. Environment variables passed to the process by your AI app.

Later sources override earlier values. The server does not search other folders
for dotenv files. Restart the full MCP server after changing settings.
Never commit `.env` or `.env.local`.

The server requires the instance URL and public client ID even with `MCP_TOOL_PACKAGE=none`.
The server validates settings at startup. It contacts ServiceNow only when a
tool needs instance data.

## ServiceNow connection settings

| Variable                           | Default                                | Accepted value and purpose                                                                                         |
| ---------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `SERVICENOW_INSTANCE_URL`          | Required                               | An HTTPS instance address with no credentials, page path, query, or fragment. The server removes a trailing slash. |
| `SERVICENOW_OAUTH_CLIENT_ID`       | Required                               | A non-empty public client ID using printable ASCII. The server removes surrounding spaces.                         |
| `SERVICENOW_OAUTH_SCOPE`           | `useraccount`                          | One or more enabled OAuth scope tokens, separated by single spaces.                                                |
| `SERVICENOW_OAUTH_REDIRECT_URI`    | `http://127.0.0.1:8765/oauth/callback` | The exact registered callback URL. Use `127.0.0.1`, port `1024` to `65535`, and `/oauth/callback`.                 |
| `SERVICENOW_OAUTH_TIMEOUT_SECONDS` | `180`                                  | Browser authorization wait in seconds. Allowed range: `1` to `600`.                                                |
| `SERVICENOW_OAUTH_CACHE`           | `memory`                               | `memory` or `os`. Opt-in native OS refresh-token storage, without plaintext fallback.                              |

The callback cannot use `localhost`, another path, query parameters, or fragments.
If you change its port, register the complete new URL in ServiceNow too.
The browser and server must run on the same computer.

Keep the default scope unless the public application enables another required scope.
A custom scope must use valid printable ASCII OAuth tokens. An arbitrary label
is not a valid scope value.

## Tools and write protection

| Variable           | Default    | Accepted value and purpose                                                                                        |
| ------------------ | ---------- | ----------------------------------------------------------------------------------------------------------------- |
| `MCP_TOOL_PACKAGE` | `readonly` | A preset package or comma-separated group names. See [[Tool-Packages]].                                           |
| `SERVICENOW_ENV`   | `prod`     | `dev` and `test` permit changes. `prod` and `production`, ignoring case, block them. Unknown values fail startup. |

Use `readonly` or a smaller read package for ordinary ITSM work.
Enabling a tool does not grant permission to use its ServiceNow resources.

For a development connection, verify the instance URL before setting `SERVICENOW_ENV=dev`.
That value permits local writes when the tools and ServiceNow permissions allow them.
See [[Safety-and-Policy]] before changing these settings.

## Read limits

| Variable                | Default                                             | Accepted value and purpose                                                                             |
| ----------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `MAX_ROW_LIMIT`         | `100`                                               | A row cap for paths that use this setting. Allowed range: `1` to `10000`.                              |
| `LARGE_TABLE_NAMES_CSV` | `syslog,sys_audit,syslog_transaction,sys_email_log` | Comma-separated large-table names for date-filter checks on query lists, aggregates, and CMDB queries. |

`MAX_ROW_LIMIT` is not a global response-size limit or a database scan limit.
Some tools have separate fixed limits. Large date windows can still be expensive.
Exact `sys_id` reads bypass the large-table date check.
Internal investigation reads use their own filters and limits.
See [[Tool-Reference]] for each tool's pagination and limits.

## Request timeouts and metadata freshness

| Variable                     | Default | Accepted value and purpose                                                                   |
| ---------------------------- | ------- | -------------------------------------------------------------------------------------------- |
| `HTTPX_TIMEOUT_SECONDS`      | `30.0`  | Connection, read, write, and pool timeout in seconds. Must be finite, from `1.0` to `600.0`. |
| `METADATA_CACHE_TTL_SECONDS` | `300`   | How long cached metadata stays fresh, in seconds. Allowed range: `1` to `86400`.             |

The HTTP timeout applies to ServiceNow operations. It is not a total MCP request deadline.
Your AI app may stop waiting before the server completes a request.
Use [[Telemetry]] to distinguish an upstream timeout from a client deadline.

The metadata cache covers choices, dictionary data, script-field discovery, and
audit configuration. It does not cache records, query results, flows,
attachments, preview tokens, or audit row counts.
Lower cache durations increase metadata requests.

## Optional error reporting

| Variable             | Default | Purpose                                                            |
| -------------------- | ------- | ------------------------------------------------------------------ |
| `SENTRY_DSN`         | Unset   | Enables Sentry error reporting when configured.                    |
| `SENTRY_ENVIRONMENT` | Unset   | Error-reporting environment label. Falls back to `SERVICENOW_ENV`. |

Leave both settings unset when your organization does not use Sentry for this server.
See [[Telemetry]] for reporting behavior and data limits.

## How sign-in works

The server supports public OAuth authorization-code flow with PKCE S256.
It uses no client secret. The installation guide describes the required
ServiceNow application and callback.

The first outbound ServiceNow request opens the browser unless an enabled OS cache supplies a usable refresh grant.
Users authorize with the account whose roles and access rules should apply.
The public client ID identifies the application, not a separate service account.

Access tokens, refresh tokens, and expiry stay in process memory by default.
Requests send the access token in a Bearer header, never in the URL.
The server renews an expired token when it can. In `memory` mode, a restart loses the tokens
and requires browser authorization again.

A rejected REST request is not replayed automatically.
A REST 401 expires the matching access token. The next outbound request
attempts token renewal or authorization as needed.
A rejected refresh grant removes the matching observed cache entry, if enabled, before browser authorization on a read.
Held previews cannot trigger a different browser grant or load a different cached account.
Connection or malformed-response failures preserve the refresh token for a later attempt.

## Optional OS OAuth cache

Set `SERVICENOW_OAUTH_CACHE=os` in the environment of the server you start, or in its local configuration file.
Complete one normal OAuth browser login with that setting enabled. An already running memory-only server does not populate the cache.
Later local stdio processes can refresh through the normal public-client token endpoint without another browser login.
There is no token export tool, file cache, cookie reuse, or copying from an existing process.

The server selects these native backends directly through `keyring`:

- macOS: the current OS user's default Keychain.
- Windows: Credential Manager (`WinVaultKeyring`), with local-machine persistence under the current OS user.
- Linux: an existing, unlocked default Secret Service collection, with the D-Bus session and Secret Service available.

Automatic backend discovery, third-party plugins, chainers, and `KEYRING_PROPERTY_*` overrides are not used.
No plaintext backend is accepted. Missing, locked, denied, or unavailable stores return a sanitized `AuthError` without fallback.
Linux never creates or unlocks a collection. macOS may require normal Keychain access permission for the Python executable.

Only the refresh token and bounded record metadata are stored. Access tokens and monotonic expiry remain in memory.
Entries expire locally 30 days after browser authorization. Refresh rotation preserves that deadline.
ServiceNow can revoke or expire a refresh grant sooner. Tokens over 1000 characters are rejected to stay within native Windows storage limits.
Malformed cache records fail closed and require clearing. Expired records are removed before a new login.

Each entry is partitioned by canonical HTTPS instance, public client ID, OAuth scope set, and redirect URI.
The cache belongs to the OS user. One configured identity holds one ServiceNow account's refresh grant.
To change accounts, stop cache-enabled processes, clear the entry, then authorize as the intended account.
Configuration names and scopes are hashed in the entry name. Secret values never appear in diagnostics.

Use cache-enabled processes sequentially. Keyring APIs have no atomic compare-and-swap or cross-process refresh lock.
Read-before-write checks reject observed entry changes, including before clearing a rejected refresh token.
They cannot eliminate a simultaneous read/write race. Do not run concurrent refreshes against the same cache entry.
A process that detects a change discards its access token and blocks further use. Start a new session and create new previews.
Fresh cache reuse never restores a previous process's preview tokens.

To remove only the entry for the current configuration, stop cache-enabled processes and run from the repository root:

```bash
uv run python -m servicenow_mcp.credential_cache clear
```

The command uses normal environment/local-file configuration. It does not log in, contact ServiceNow, or print credentials.
It also works when the cache setting is `memory`. Opting out does not delete OS credentials or revoke the remote grant.
Clearing does not erase tokens already held in another process. Close those processes separately.

## REST API access policies

A successful OAuth sign-in does not establish access to every REST resource.
An administrator must permit the intended OAuth bearer authorization on each required resource and HTTP method.
Table permissions, field permissions, and record visibility remain separate checks.
Preserve unrelated policies and restrictions.
Do not disable global protection.
Do not store tokens, authorization codes, or browser callback data in configuration files.

## Troubleshooting

Use the installation guide's
[troubleshooting steps](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md#troubleshooting)
for startup, browser authorization, and record-access failures.

For `UPSTREAM_TIMEOUT`, inspect `error.phase`, `error.operation`, and `error.trace_id`.
For MCP `-32001: Request timed out`, check the AI app's deadline and server trace.
Increasing the HTTP timeout does not change the app's deadline.
See [[Telemetry]] for the fields available in diagnostic logs.

Report the attempted action and sanitized error. Do not attach passwords,
tokens, raw headers, callback URLs, or query strings.
