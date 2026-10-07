# Install ServiceNow Platform MCP

Connect your AI app to ServiceNow, sign in with your account, and complete a
small read before enabling changes.

If your app already has the connection, start with [Getting started](docs/wiki/Getting-Started.md).
For examples of what you can do, see the [README](README.md).

An administrator prepares OAuth and ServiceNow access. Each user then authorizes
the connection in their browser. The public client ID identifies the application.
The signed-in user's ServiceNow permissions control record access.

## Before you start

You need:

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- An AI app that can start local MCP servers, such as VS Code with GitHub Copilot, Claude Code, or OpenCode.
- Your ServiceNow instance's HTTPS address, such as `https://your-instance.service-now.com`.
- A public OAuth application's client ID, prepared by your ServiceNow administrator.
- A ServiceNow account with API access and permission to read the records required for your work.

The AI app starts the server on your computer. They communicate through
**stdio**, a local connection between the two programs. Run the authorization
browser on that same computer.

## 1. Prepare ServiceNow access

If your administrator already supplied the instance URL and public client ID,
continue to [connect your AI app](#2-connect-your-ai-app).

1. Open **System OAuth > Application Registry**.
2. Select **New**.
3. Select **Create an OAuth API endpoint for external clients**.
4. Configure the application to meet the requirements below.
5. Save it and copy its public client ID.

You can use an existing inbound application that meets the same requirements.

| Requirement | Value |
| --- | --- |
| Client type | Public, with no client secret |
| Authorization flow | Authorization code with PKCE |
| PKCE method | `S256` |
| Scope | `useraccount` |
| Redirect URL | `http://127.0.0.1:8765/oauth/callback` |

These are connection requirements. OAuth configuration screens depend on the
ServiceNow release and application type.

OAuth authenticates the user. It does not grant table access.
Ask the administrator to allow the required API resources and record or field
access rules. Begin with access to one table you can use to check the connection.

The [permissions guide](docs/wiki/Safety-and-Policy.md#servicenow-permissions)
explains how access policies, roles, and access control rules (ACLs) work together.

## 2. Connect your AI app

The examples run the [published package](https://pypi.org/project/servicenow-platform-mcp/)
with `uvx`. For a local source installation, use the
[source checkout instructions](#run-from-a-source-checkout).

Replace the two placeholder values with your instance URL and public client ID.
Use the instance's HTTPS address without a page path, query string, or credentials.
Keep any existing server entries when adding this configuration.

Each example selects `readonly` and blocks writes with `SERVICENOW_ENV=prod`.
The server otherwise defaults to `full`, which includes write tools.
The environment setting controls local write protection. It does not select
an instance or detect whether that instance is production.

Do not add passwords, API keys, client secrets, or tokens to the configuration.

### VS Code with GitHub Copilot, or Claude Code

Save this as `.mcp.json` in the top-level folder you open in your AI app:

```json
{
  "mcpServers": {
    "servicenow-platform": {
      "command": "uvx",
      "args": ["servicenow-platform-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
        "MCP_TOOL_PACKAGE": "readonly",
        "SERVICENOW_ENV": "prod"
      }
    }
  }
}
```

In VS Code, open Chat and enable the ServiceNow tools.
In Claude Code, approve the project MCP server when prompted.
Restart the app or its MCP server after saving changes.

For user-wide configuration and server controls, see the
[VS Code guide](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
or [Claude Code guide](https://code.claude.com/docs/en/mcp).

<details>
<summary>OpenCode</summary>

Add this configuration to `opencode.json` in your project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servicenow-platform": {
      "type": "local",
      "command": ["uvx", "servicenow-platform-mcp"],
      "enabled": true,
      "environment": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
        "MCP_TOOL_PACKAGE": "readonly",
        "SERVICENOW_ENV": "prod"
      }
    }
  }
}
```

Restart OpenCode. Its [MCP guide](https://opencode.ai/docs/mcp-servers/)
explains other configuration locations and options.

</details>

<details>
<summary>Another local MCP app</summary>

Configure the app to start `uvx servicenow-platform-mcp`.
Pass the same four environment variables from the examples above.

This is a server entry, not a complete client configuration:

```json
{
  "command": "uvx",
  "args": ["servicenow-platform-mcp"],
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
    "MCP_TOOL_PACKAGE": "readonly",
    "SERVICENOW_ENV": "prod"
  }
}
```

Use your app's documented format for a local stdio server.

</details>

## 3. Sign in and try one read

1. Restart the ServiceNow MCP server in your AI app.
2. Ask: "List the available ServiceNow tools."
3. Ask: "Use ServiceNow to show one active incident. Include its number and short description."
4. Complete browser authorization with the intended ServiceNow account.
5. Check the returned record in ServiceNow.

If your account cannot read incidents, ask for a record from a table you can access.
A returned record confirms access to that table. Tool discovery alone only
shows that the MCP connection starts.

The first request that needs ServiceNow opens the browser.
Tokens stay in server memory. The server renews an expired access token when
it can. Restarting the server requires browser authorization again.

Continue with [ITSM work](docs/wiki/ITSM-Work.md) or
[instance administration and development](docs/wiki/Instance-Development.md).

## 4. Choose the tools needed for your work

Keep `MCP_TOOL_PACKAGE=readonly` for ordinary record inspection and investigation.
Use a smaller custom package when you need fewer tools.

| Need | Package |
| --- | --- |
| ITSM reads, ticket history, metadata, investigations, flows, code search, and CMDB inspection | `readonly` |
| Queries, field descriptions, and attachment reads | `core_readonly` |
| Record changes, attachment changes, or catalog actions | A custom package with the required groups, or `full` |
| Check that the server starts without contacting ServiceNow | `none` |

**Set the package explicitly. The default is `full`.**
Package selection does not grant ServiceNow access.
Catalog browsing and ordering share the `service_catalog` group, which is
absent from `readonly`.

The [package guide](docs/wiki/Tool-Packages.md) lists each group and its purpose.
The [tool reference](docs/wiki/Tool-Reference.md) describes exact actions and inputs.

## 5. Prepare changes on a test instance

For changes, use a development or test instance and the required ServiceNow permissions.
An example package for record work is:

```dotenv
MCP_TOOL_PACKAGE=query,describe,record_read,record_write
SERVICENOW_ENV=dev
```

Confirm that `SERVICENOW_INSTANCE_URL` identifies the intended non-production instance.
Changing `SERVICENOW_ENV` does not change that URL. Restart the server after changing settings.

Record writes offer previews, but callers can request immediate changes.
The server does not enforce human approval. Ask the app to show a preview and
wait for your approval before applying a record change.

Attachment changes and catalog orders apply directly.
Review the [change guidance](docs/wiki/Instance-Development.md) and
[safety limits](docs/wiki/Safety-and-Policy.md) before enabling those tools.

## Run from a source checkout

Use a checkout when you need repository changes or want to contribute to the MCP server.
The published-package setup above is sufficient for normal use.

1. Clone the repository:

   ```bash
   git clone https://github.com/Xerrion/servicenow-platform-mcp.git
   cd servicenow-platform-mcp
   ```

2. Install the dependencies from that folder:

   ```bash
   uv sync --group dev
   ```

3. Configure your AI app to run `uv run servicenow-platform-mcp` from the checkout.

This server entry illustrates the command, working directory, and settings.
Adapt the entry to your app's configuration format:

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "cwd": "/absolute/path/to/servicenow-platform-mcp",
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
    "MCP_TOOL_PACKAGE": "readonly",
    "SERVICENOW_ENV": "prod"
  }
}
```

Replace `cwd` with the checkout's absolute path. The working-directory setting
is client-specific. Use your app's equivalent or documented command options.

You can also store settings in `.env.local` in the server's working directory.
The server reads `.env` first, then `.env.local`. Process environment variables override both.
A client that starts the process elsewhere does not read the checkout's dotenv files.
Never commit `.env` or `.env.local`.

For checks and contribution steps, see [MCP server development](docs/wiki/Development.md).

## Troubleshooting

### The server does not start

Check Python, `uv`, the configured command, and the two required ServiceNow values.
If the app cannot find `uvx`, use the executable path recognized by your app.
For a source checkout, check the working directory and installed dependencies.

Remove `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD`, and `SERVICENOW_API_KEY`
from the app environment and dotenv files. Non-empty values fail startup.
Remove stale `SERVICENOW_OAUTH_CLIENT_SECRET` values. The server ignores them.

### The browser does not open or authorization times out

Use a browser on the computer running the server.
Check that the registered redirect URL exactly matches the configured value.
The default wait is 180 seconds.

For `Cannot bind OAuth loopback port`, close a known conflicting listener or
configure and register another port. The callback must use
`http://127.0.0.1:<port>/oauth/callback`, with a port from `1024` to `65535`.
The server rejects `localhost` and other callback paths.

### ServiceNow rejects OAuth

Ask the administrator to check the public application, PKCE S256, client ID,
enabled scope, and exact redirect URL. The default scope is `useraccount`.
Token exchange errors are separate from later record-access errors.

### Sign-in succeeds, but a read returns 401 or 403

Ask the administrator to check the API policy, user roles, and table or field access rules.
A valid OAuth token does not prove permission to use a specific API resource.
A rejected REST request is not replayed automatically. Retry the read after
correcting access. The next request attempts token renewal or authorization when needed.

For an old policy that requires API keys, follow the
[narrow policy migration](docs/wiki/Configuration.md#rest-api-access-policies).
Do not add an API key or disable unrelated policies.

### Tools or settings do not match your configuration

Check `MCP_TOOL_PACKAGE` and restart the full MCP server.
`list_tool_packages` lists available packages and groups, not the active package.
Settings in the process environment override dotenv values.

For write protection, check `SERVICENOW_ENV` and the instance URL separately.
Keep `prod` or `production` for production connections.

Include the attempted action and sanitized error when reporting a problem.
Exclude passwords, tokens, raw headers, and browser callback URLs.

## Reference

- [Configuration](docs/wiki/Configuration.md): all settings, defaults, OAuth behavior, and API policy troubleshooting.
- [Safety and permissions](docs/wiki/Safety-and-Policy.md): access controls, masking, write protection, and query limits.
- [Tool reference](docs/wiki/Tool-Reference.md): complete actions and input contracts.
- [Agent recipes](docs/agent-recipes.md): technical query and record-change examples.
