# servicenow-platform-mcp

`servicenow-platform-mcp` is an MCP server for controlled access to ServiceNow
through ServiceNow REST APIs. It uses MCP stdio transport. An MCP client starts
the server as a local process.

Use it to read records and metadata, inspect attachments and platform data,
search ServiceNow code, run analyses and investigations, and perform writes
when the selected tool package includes write tools and permissions allow them.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- An MCP client that supports local stdio servers
- A ServiceNow instance
- Permission to create or use a ServiceNow **Application Registry** entry
- A ServiceNow user with roles, REST API access, and table and field ACL access
  for the tools and tables you select

The browser and MCP server must run on the same machine. OAuth uses an IPv4
loopback callback.

## Configure ServiceNow OAuth

This server uses a public OAuth authorization-code flow with PKCE S256. The
server does not receive Basic Auth credentials, API keys, passwords, or client
secrets.

In ServiceNow, open **System OAuth > Application Registry**. Create or select
an application for this server, then set:

1. **Public Client** to `true`.
2. Authorization-code PKCE to **S256**.
3. Scope to `useraccount`.
4. Redirect URL to:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

Save the application. Copy its public client ID.

OAuth identifies and authorizes the user. It does not grant access to
ServiceNow tables or fields. REST API access policies, user roles, table ACLs,
field ACLs, and row visibility still control each request.

For a read-only setup, use a read-only ServiceNow user, GET-only REST API
policies, and `MCP_TOOL_PACKAGE=readonly`. The selected tools can require:

- Table API access for records, metadata, Flow data, and analysis.
- Attachment API GET access for attachment metadata and downloads.
- Aggregate API access for aggregate queries and audit positive-control counts.
- Code Search or Service Catalog API access only when those tools are selected.
- Read access to target tables and fields.

## Configure an MCP client

Pass settings through the client process environment. Do not rely on a client
working directory or dotenv files. Client configuration format varies; use the
equivalent stdio fields for `command`, `args`, and `env`.

Example:

```json
{
  "mcpServers": {
    "servicenow-platform": {
      "command": "uvx",
      "args": [
        "--from",
        "servicenow-platform-mcp==2.0.0",
        "servicenow-platform-mcp"
      ],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
        "SERVICENOW_OAUTH_REDIRECT_URI": "http://127.0.0.1:8765/oauth/callback",
        "MCP_TOOL_PACKAGE": "readonly",
        "SERVICENOW_ENV": "dev"
      }
    }
  }
}
```

The instance value must be an HTTPS origin without credentials, path, query, or
fragment. OAuth scope defaults to `useraccount`; set `SERVICENOW_OAUTH_SCOPE`
only to override it. The redirect value must match ServiceNow exactly. The
server accepts the form `http://127.0.0.1:<port>/oauth/callback` with a port
from `1024` to `65535`.

### Launch with `uvx`

Pinned launch for this release:

```bash
uvx --from 'servicenow-platform-mcp==2.0.0' servicenow-platform-mcp
```

This is normally the command configured in the MCP client. The unpinned form

```bash
uvx servicenow-platform-mcp
```

resolves the newest available release and can change server behavior when a
new release is published.

The server starts without opening a browser. The first tool call that needs
ServiceNow access opens the default browser. Authorize as the ServiceNow user
whose permissions should apply. Access tokens stay in process memory. A server
restart or token expiry requires authorization again.

## Select tools

`list_tool_packages` is always available. Set `MCP_TOOL_PACKAGE` to select
additional tool groups:

| Value | Use |
| --- | --- |
| `readonly` | Read and analysis tools, including `query`, `describe`, `record_read`, `attachment`, `investigate`, `resolve_choice`, `analysis`, `audit`, `flow`, and `code_search` |
| `core_readonly` | `query`, `describe`, and read-only `attachment` |
| `full` | All tool groups, including write tools |
| `none` | Only `list_tool_packages` |

You can also provide comma-separated groups, for example:

```text
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

Package selection controls which tools load. It is not a ServiceNow
authorization boundary.

## Use tools

Call `list_tool_packages` with no arguments to confirm that the MCP client can
reach the server. It lists available package presets and groups. It does not
contact ServiceNow or report the active package.

Use `query` for a small, explicit read:

```json
{
  "table": "incident",
  "fields": "sys_id,number,short_description,state",
  "encoded_query": "active=true",
  "limit": 10,
  "display_values": true
}
```

`query` list mode requires `table` and `fields`. `limit` defaults to `20` and
`offset` defaults to `0`.

Use `record_read` for one record. Provide exactly one of `sys_id` or `name`:

```json
{
  "table": "incident",
  "sys_id": "32-character-sys-id",
  "fields": "sys_id,number,short_description,state"
}
```

Use `describe` to inspect a table and its fields:

```json
{
  "table": "incident",
  "include_docs": true
}
```

Use each tool's `describe` action where available. The runtime tool schema is
the authoritative input contract.

### Writes

Record writes require `full` or a custom package containing `record_write`.
They also require matching ServiceNow REST API permissions and ACLs. `record_write`
previews by default; apply its single-use `preview_token` with
`record_apply`. Set `SERVICENOW_ENV=prod` or `SERVICENOW_ENV=production` to
block local writes.

## Verify setup

1. Restart the MCP server after changing configuration.
2. Call `list_tool_packages`.
3. Call `query` with one small read against a table the authorized user can
   access:

   ```json
   {
     "table": "incident",
     "fields": "sys_id,number",
     "limit": 1
   }
   ```

4. Complete browser authorization when prompted.
5. Confirm a successful tool response.

The `incident` examples require access to `incident`. Use another permitted
table when needed.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Invalid configuration at startup | Check variable names and values. Pass them through the MCP client's `env`. |
| `Cannot open the local browser` | Check the browser on the machine running the MCP server. |
| `ServiceNow authorization timed out` | Authorize on the same machine. Check the exact redirect URL and retry. |
| `Cannot bind OAuth loopback port` | Close a known conflicting listener, or configure and register another allowed `127.0.0.1` port. |
| ServiceNow rejects the scope | Enable `useraccount` in the Application Registry, or set `SERVICENOW_OAUTH_SCOPE` to an enabled scope. |
| OAuth token exchange rejected | Check public client, PKCE S256, scope, client ID, and exact redirect URL. |
| REST 401 or `User Not Authenticated` | Authorize on the next call. If it persists, ask an administrator to check scopes, REST API policies, and user access. |
| HTTP 403 | Check REST resource permissions, roles, table ACLs, and field ACLs. |
| Configuration changes have no effect | Restart the full MCP server process. |

## Security

- Use least-privilege ServiceNow roles, REST policies, table ACLs, and field
  ACLs.
- Prefer `readonly` or a smaller read-only custom package.
- Do not configure API keys, passwords, Basic Auth, or client secrets.
- Do not put access tokens, authorization codes, PKCE verifiers, or callback
  query strings in configuration or logs.
- Sensitive-value masking applies to selected record paths, not every tool
  response. Enforce ServiceNow ACLs for sensitive data.
- Treat attachments and other ServiceNow content as untrusted data.
- Never commit configuration containing credentials or tokens.

## Links

- [PyPI package](https://pypi.org/project/servicenow-platform-mcp/)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
- [`uv` tool guide](https://docs.astral.sh/uv/guides/tools/)
