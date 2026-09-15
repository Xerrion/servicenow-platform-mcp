# ServiceNow Platform MCP

Connect an MCP client to ServiceNow with public OAuth and local stdio transport.
Use the server to inspect records, metadata, attachments, flows, and ServiceNow
code; investigate platform issues; and, when explicitly enabled, stage and apply
record or attachment changes.

**Best for:** developers and ServiceNow administrators who want an MCP client to
work with an existing ServiceNow instance while ServiceNow roles, REST policies,
and ACLs remain the authorization boundary.

## Start here

You need Python 3.12+, [`uv`](https://docs.astral.sh/uv/), a ServiceNow instance,
and an MCP client that can start local stdio servers.

1. In ServiceNow, create a public OAuth application that uses authorization-code
   PKCE with S256. Register this redirect URL:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

2. Copy the application's public client ID.
3. Add the server to your MCP client configuration. Replace the two placeholders:

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
           "MCP_TOOL_PACKAGE": "readonly",
           "SERVICENOW_ENV": "dev"
         }
       }
     }
   }
   ```

4. Restart the MCP client and call `list_tool_packages`.
5. Make a small read request. The first request that contacts ServiceNow opens
   your default browser for authorization.

The browser and MCP server must run on the same machine. This example pins
version 2.0.0. Use `uvx servicenow-platform-mcp` only when you want the newest
available release.

## Install in your AI client

Use the native configuration for your client. Replace the instance URL and
public client ID in each example. All examples use `readonly` by default.

### Claude Code

Run this command to add the server to your user configuration:

```bash
claude mcp add \
  --scope user \
  --transport stdio \
  servicenow-platform \
  --env SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com \
  --env SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id \
  --env MCP_TOOL_PACKAGE=readonly \
  --env SERVICENOW_ENV=dev \
  -- uvx --from servicenow-platform-mcp==2.0.0 servicenow-platform-mcp
```

Run `claude mcp list` to confirm that Claude Code added the server.

### GitHub Copilot in VS Code

Create or edit `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "servicenow-platform": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "servicenow-platform-mcp==2.0.0",
        "servicenow-platform-mcp"
      ],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
        "MCP_TOOL_PACKAGE": "readonly",
        "SERVICENOW_ENV": "dev"
      }
    }
  }
}
```

Restart VS Code, then trust and start the server when Copilot prompts you.

### OpenCode

Add the server to `opencode.json` in your project root, or to
`~/.config/opencode/opencode.json` for all projects:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servicenow-platform": {
      "type": "local",
      "command": [
        "uvx",
        "--from",
        "servicenow-platform-mcp==2.0.0",
        "servicenow-platform-mcp"
      ],
      "environment": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
        "MCP_TOOL_PACKAGE": "readonly",
        "SERVICENOW_ENV": "dev"
      }
    }
  }
}
```

Restart OpenCode to load the server.

Do not add API keys, Basic Auth credentials, passwords, client secrets, access
tokens, authorization codes, or PKCE verifiers to any client configuration.

## Configure ServiceNow OAuth

Open **System OAuth > Application Registry**, then create or select the
application for this server. Configure it as follows:

| Setting | Value |
| --- | --- |
| Public Client | `true` |
| Authorization flow | Authorization code with PKCE |
| PKCE method | `S256` |
| Scope | `useraccount` |
| Redirect URL | `http://127.0.0.1:8765/oauth/callback` |

Save the application and use its public client ID for
`SERVICENOW_OAUTH_CLIENT_ID`. The default OAuth scope is `useraccount`; set
`SERVICENOW_OAUTH_SCOPE` only when your application enables a different scope.

OAuth proves the user's identity. It does **not** grant table access. The
authorized user's REST API policies, roles, table ACLs, field ACLs, and row
visibility still apply to every request.

## Choose a tool package

Set `MCP_TOOL_PACKAGE` to load only the tools you need:

| Package | Includes | Recommended use |
| --- | --- | --- |
| `readonly` | Records, metadata, attachments, investigations, analysis, audits, flows, and code search | Normal read-only work |
| `core_readonly` | `query`, `describe`, and read-only `attachment` | Minimal inspection access |
| `full` | Every tool group, including record and attachment writes | Controlled write workflows |
| `none` | Only `list_tool_packages` | Test client connectivity |

You can also select individual comma-separated tool groups:

```text
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

Valid groups are `query`, `describe`, `record_write`, `record_read`,
`attachment`, `attachment_write`, `investigate`, `resolve_choice`,
`service_catalog`, `analysis`, `audit`, `flow`, and `code_search`.

Tool packages determine which tools the server loads. They do not replace
ServiceNow authorization.

## Make your first requests

Use `query` for a bounded list of records:

```json
{
  "table": "incident",
  "fields": "sys_id,number,short_description,state",
  "encoded_query": "active=true",
  "limit": 10,
  "display_values": true
}
```

Use `record_read` for one record. Provide exactly one of `sys_id` or `name`:

```json
{
  "table": "incident",
  "sys_id": "32-character-sys-id",
  "fields": "sys_id,number,short_description,state"
}
```

Use `describe` before working with an unfamiliar table:

```json
{
  "table": "incident",
  "include_docs": true
}
```

The `incident` examples require access to the `incident` table. Substitute a
table the authorized user can read. Use a tool's `describe` action, when
available, for its complete input contract.

## Configure from a local checkout

For local development or a source-based installation:

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

Create `.env.local` in the directory where the MCP client starts the server:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

Then configure the client to run `uv run servicenow-platform-mcp` with its
working directory set to the checkout. The server reads `.env`, then
`.env.local`, from its working directory. Process environment variables take
precedence over both files. Do not commit either dotenv file.

## Write safely

Write tools require `full` or a custom package that includes `record_write` or
`attachment_write`, as well as matching ServiceNow permissions. Record writes
are previewed first. Apply the resulting one-time `preview_token` with
`record_apply`.

Set `SERVICENOW_ENV=prod` or `SERVICENOW_ENV=production` to block local writes.
For a read-only setup, use a read-only ServiceNow user, GET-only REST API
policies, and `MCP_TOOL_PACKAGE=readonly`.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Configuration fails at startup | Use the exact environment variable names. Make sure the client forwards them or starts in the directory with the intended dotenv file. |
| Browser does not open | The browser must be available on the machine running the MCP server. |
| Authorization times out | Use the same machine for the browser and server. Confirm the redirect URL exactly matches the Application Registry. |
| OAuth token exchange is rejected | Confirm public-client mode, PKCE S256, the client ID, enabled scope, and redirect URL. |
| HTTP 401 or `User Not Authenticated` | Authorize on the next call. If it persists, review scopes, REST API policies, and user access. |
| HTTP 403 | Review REST resource permissions, roles, table ACLs, and field ACLs. |
| Configuration changes do not apply | Restart the MCP server process. |

## Security

- Use least-privilege ServiceNow roles, REST policies, table ACLs, and field ACLs.
- Prefer `readonly` or a smaller custom package.
- Do not configure API keys, Basic Auth credentials, passwords, or client secrets.
- Do not log or commit access tokens, authorization codes, PKCE verifiers, or
  callback URLs with query strings.
- Treat attachments and other ServiceNow content as untrusted data.

## Reference

- [Installation guide](INSTALL.md) - full configuration reference, permissions,
  OAuth behavior, and operating guidance
- [PyPI package](https://pypi.org/project/servicenow-platform-mcp/)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
- [`uv` tool guide](https://docs.astral.sh/uv/guides/tools/)
