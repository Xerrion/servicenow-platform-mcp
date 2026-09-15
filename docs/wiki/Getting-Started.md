# Getting Started

Use this guide to install the server, connect it to an MCP client, and complete
one read from ServiceNow.

## Prerequisites

- Python 3.12 or newer.
- `uv`.
- An MCP client that supports local stdio servers.
- A ServiceNow instance and permission to create or use an Application Registry entry.
- A ServiceNow user with roles, REST API access, and table and field ACL access for selected tools.

The browser and MCP server must run on the same machine. OAuth uses an IPv4
loopback callback.

## Install

### Source checkout

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

Configure the MCP client to launch the checkout with:

```text
uv run servicenow-platform-mcp
```

### Published package

Confirm that your package index contains the release and behavior required by
your environment before using a published package:

```bash
uvx servicenow-platform-mcp
```

The server uses stdio. The MCP client launches it as a subprocess. Do not
configure it as an HTTP endpoint.

## Configure ServiceNow OAuth

In **System OAuth > Application Registry**, create or select an application:

1. Set **Public Client** to `true`.
2. Enable authorization-code PKCE with **S256**.
3. Enable the `useraccount` scope.
4. Register the exact redirect URL:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

5. Save the application and copy its public client ID.

OAuth identifies the user. It does not grant table or field access. ServiceNow
REST policies, roles, row visibility, and ACLs still apply.

## Configure the server

Pass settings through the MCP client's process environment, or create `.env`
and `.env.local` in the server's working directory:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

`SERVICENOW_OAUTH_SCOPE` is optional. It defaults to `useraccount`. Set it only
when the Application Registry enables another valid OAuth scope-token value.
Multiple scope tokens use one space between tokens.

The instance value must be an HTTPS origin without credentials, path, query, or
fragment. The redirect URI must use
`http://127.0.0.1:<port>/oauth/callback`, with port `1024`-`65535`, and must
match the registered URL exactly. `localhost` is not accepted.

Do not configure `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, or
`SERVICENOW_PASSWORD`. Non-empty values fail startup. A stale
`SERVICENOW_OAUTH_CLIENT_SECRET` is ignored; remove it.

The server reads `.env`, then `.env.local`, then process environment variables.
Later sources override earlier sources. Restart the full MCP process after
configuration changes. Never commit dotenv files.

See [[Configuration]] for defaults, validation, package groups, and limits.

## Configure the MCP client

Client configuration shape varies. Use its equivalent stdio fields for
`command`, `args`, working directory, and `env`:

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "cwd": "/path/to/servicenow-platform-mcp",
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
    "SERVICENOW_OAUTH_REDIRECT_URI": "http://127.0.0.1:8765/oauth/callback",
    "MCP_TOOL_PACKAGE": "readonly"
  }
}
```

`cwd` is a client setting, not an MCP protocol field. Do not put tokens,
authorization codes, PKCE verifiers, callback query strings, passwords, API
keys, or client secrets in client configuration.

## Verify setup

1. Restart the MCP server.
2. Call `list_tool_packages`. It does not contact ServiceNow.
3. Call `query` with a small read against a permitted table:

   ```text
   table="incident", fields="sys_id,number", limit=1
   ```

4. Complete browser authorization when prompted.
5. Confirm a successful response.

Use another table when the user cannot access `incident`.

The first ServiceNow call opens the default browser. REST calls use a Bearer
header. A restart or token expiry requires authorization again. A REST 401 does
not replay the request.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Invalid configuration at startup | Check variable names and values. Check the MCP client's process environment and working directory. |
| `Cannot open the local browser` | Check the browser on the machine running the server. |
| `Cannot bind OAuth loopback port` | Close a known conflicting listener, or configure and register another allowed `127.0.0.1` port. |
| `ServiceNow authorization timed out` | Authorize on the same machine before the timeout. Check the exact redirect URL and retry. |
| ServiceNow rejects the scope | Enable `useraccount`, or set `SERVICENOW_OAUTH_SCOPE` to an enabled valid scope-token value. |
| OAuth token exchange rejected | Check Public Client, PKCE S256, client ID, scope, and exact redirect URL. |
| REST 401 or `User Not Authenticated` | Authorize on the next call. If it persists, ask an administrator to check scopes, REST policy, and user access. |
| HTTP 403 | Check REST resource permissions, roles, table ACLs, and field ACLs. |
| No tools appear | Check `MCP_TOOL_PACKAGE` and call `list_tool_packages`. |
| Writes are blocked | `SERVICENOW_ENV=prod` and `production` block local writes. Use a sub-production instance for write testing. |

For REST policy migration, see [[Configuration]]. Adjust only the affected
policy. Do not add an API key or disable unrelated protection.
