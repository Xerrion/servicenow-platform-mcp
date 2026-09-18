# ServiceNow Platform MCP

Connect your MCP client to an existing ServiceNow instance using public OAuth
and local stdio transport.

Read records, explore metadata, inspect attachments and flows, search ServiceNow
code, and investigate platform issues. With write tools explicitly enabled, you
can also stage and apply record or attachment changes.

The server is built for developers and ServiceNow administrators. Your ServiceNow
roles, REST policies, and ACLs still control what you can access.

## Start here

You'll need Python 3.12+, [`uv`](https://docs.astral.sh/uv/), a ServiceNow instance,
and an MCP client that can run local stdio servers.

1. Create a public OAuth application in ServiceNow using the authorization-code
   flow with PKCE and S256. Register this redirect URL:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

2. Copy the application's public client ID.
3. Add the server to your MCP client's configuration. Replace the instance URL
   and public client ID with your own:

   ```json
   {
     "mcpServers": {
       "servicenow-platform": {
         "command": "uvx",
         "args": [
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
5. Try a small read request. The first request to ServiceNow opens your default
   browser and asks you to authorize access.

Run the browser and MCP server on the same machine. Use
`uvx servicenow-platform-mcp` for the newest available release.

## Install in your AI client

Choose the setup below for your client and replace the instance URL and public
client ID. Each example starts with the `readonly` tool package.

### Claude Code

Add the server to your Claude Code user configuration:

```bash
claude mcp add \
  --scope user \
  --transport stdio \
  servicenow-platform \
  --env SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com \
  --env SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id \
  --env MCP_TOOL_PACKAGE=readonly \
  --env SERVICENOW_ENV=dev \
  -- uvx servicenow-platform-mcp
```

Run `claude mcp list` to check that the server was added.

### GitHub Copilot in VS Code

Create or edit `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "servicenow-platform": {
      "type": "stdio",
      "command": "uvx",
      "args": [
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

For a single project, add the server to `opencode.json` in the project root.
To use it across all projects, add it to `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servicenow-platform": {
      "type": "local",
      "command": [
        "uvx",
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

Keep API keys, Basic Auth credentials, passwords, client secrets, access tokens,
authorization codes, and PKCE verifiers out of your client configuration.

## Configure ServiceNow OAuth

In ServiceNow, open **System OAuth > Application Registry**. Create an application
for this server, or open an existing one, and use these settings:

| Setting            | Value                               |
| ------------------ | ----------------------------------- |
| Public Client      | `true`                              |
| Authorization flow | Authorization code with PKCE        |
| PKCE method        | `S256`                              |
| Scope              | `useraccount`                       |
| Redirect URL       | `http://127.0.0.1:8765/oauth/callback` |

Save the application and copy its public client ID into
`SERVICENOW_OAUTH_CLIENT_ID`. The default scope is `useraccount`. Only set
`SERVICENOW_OAUTH_SCOPE` if your application has a different scope enabled.

When an access token expires, the server renews it using the issued refresh token.
No client secret is needed. Tokens stay in process memory, so you'll need to
authorize again in the browser each time you restart the MCP server.

OAuth authenticates the user. It does **not** grant table access. Every request
still follows the authorized user's REST API policies, roles, table ACLs, field
ACLs, and row visibility rules.

## Choose a tool package

Use `MCP_TOOL_PACKAGE` to choose which tools the server loads:

| Package         | Includes                                                                                    | Recommended use            |
| --------------- | ------------------------------------------------------------------------------------------- | -------------------------- |
| `readonly`      | Records, metadata, attachments, investigations, analysis, audits, flows, code search, and CMDB | Normal read-only work      |
| `core_readonly` | `query`, `describe`, and read-only `attachment`                                               | Minimal inspection access  |
| `full`          | Every tool group, including record and attachment writes                                     | Controlled write workflows |
| `none`          | Only `list_tool_packages`                                                                    | Test client connectivity   |

For more control, list the tool groups you need, separated by commas:

```text
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

Available groups are `query`, `describe`, `record_write`, `record_read`,
`attachment`, `attachment_write`, `investigate`, `resolve_choice`,
`service_catalog`, `analysis`, `audit`, `flow`, `code_search`, and `cmdb`.

A tool package controls which tools are loaded, not what you're allowed to do.
ServiceNow still checks authorization for each request.

## Make your first requests

Use `query` to fetch a limited set of records. This example requests up to 10
active incidents:

```json
{
  "table": "incident",
  "fields": "sys_id,number,short_description,state",
  "encoded_query": "active=true",
  "limit": 10,
  "display_values": true
}
```

Use `record_read` to fetch a single record. Provide either `sys_id` or `name`,
but not both:

```json
{
  "table": "incident",
  "sys_id": "32-character-sys-id",
  "fields": "sys_id,number,short_description,state"
}
```

For an unfamiliar table, start with `describe`:

```json
{
  "table": "incident",
  "include_docs": true
}
```

These examples require read access to the `incident` table. Replace it with a
table the authorized user can read if needed. For a tool's complete input details,
use its `describe` action when available.

## Configure from a local checkout

To work on the server locally or run it from source, clone the repository and
install its dependencies:

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

Configure your client to run `uv run servicenow-platform-mcp` with the checkout
as its working directory.

The server reads `.env` first, then `.env.local`, from that directory. Process
environment variables override values from both files. Keep both dotenv files
out of version control.

## Write safely

To enable writes, use `full` or a custom package that includes `record_write` or
`attachment_write`. The authorized ServiceNow user also needs permission to make
those changes.

Record writes are previewed before they're applied. Pass the one-time
`preview_token` from the preview to `record_apply` to apply the change.

Set `SERVICENOW_ENV=prod` or `SERVICENOW_ENV=production` to block local writes.
For a read-only setup, combine a read-only ServiceNow user with GET-only REST API
policies and `MCP_TOOL_PACKAGE=readonly`.

## Troubleshooting

| Problem                             | What to check |
| ----------------------------------- | ------------- |
| Configuration fails at startup      | Check that the environment variable names match exactly. Make sure the client passes them to the server, or starts it in the directory containing the intended dotenv file. |
| Browser does not open               | Check that a browser is available on the machine running the MCP server. |
| Authorization times out             | Run the browser and server on the same machine. Check that the redirect URL exactly matches the one in the Application Registry. |
| OAuth token exchange is rejected    | Check public-client mode, PKCE S256, the client ID, the enabled scope, and the redirect URL. |
| HTTP 401 or `User Not Authenticated` | Authorize again on the next call. If the error continues, check scopes, REST API policies, and the user's access. |
| HTTP 403                            | Check REST resource permissions, roles, table ACLs, and field ACLs. |
| Configuration changes do not apply  | Restart the MCP server process. |

## Security

- Grant only the access needed through ServiceNow roles, REST policies, table
  ACLs, and field ACLs.
- Use `readonly` or a smaller custom package wherever possible.
- Keep API keys, Basic Auth credentials, passwords, and client secrets out of
  the configuration.
- Never log or commit access tokens, authorization codes, PKCE verifiers, or
  callback URLs containing query strings.
- Treat attachments and other ServiceNow content as untrusted data.

## Reference

- [Installation guide](INSTALL.md): full configuration details, permissions,
  OAuth behavior, and guidance for running the server
- [PyPI package](https://pypi.org/project/servicenow-platform-mcp/)
- [MCP stdio transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)
- [`uv` tool guide](https://docs.astral.sh/uv/guides/tools/)
