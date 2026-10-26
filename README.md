# ServiceNow Platform MCP

Connect your AI app to ServiceNow to read tickets, inspect configuration, and
prepare changes to records or scripts. The server uses the Model Context Protocol (MCP).

Sign in with your ServiceNow account. Your roles, API access policies, and
access control rules (ACLs) determine what you can read or change.

## Start here

- [Connect your AI app](#connection-setup).
- [Work with tickets and requests](#itsm-work).
- [Inspect or develop an instance](#instance-administration-and-development).

If the server is already configured, ask your AI app:

> Use ServiceNow to show 5 active incidents I can access. Include the incident
> number, short description, priority, and assigned person. Do not change anything.

If you need to sign in, the server opens a browser on its computer.

## ITSM work

Read incident, problem, change, and request records. Review history, inspect
attachments, or count records for a status update.

For example:

- "Show 10 active incidents assigned to the Service Desk group, ordered by priority."
- "Summarize INC0012345, including comments and work notes from the last 30 days."
- "Count open incidents by assignment group. Include the filters you used."
- "Show the answers submitted with RITM0012345."

Use record numbers and group names from your instance. Include a time period
and limit for lists and history.

Creating tickets, updating work notes, and changing assignments require write
tools and ServiceNow permissions. See [making changes](#making-changes).

## Instance administration and development

Inspect configuration, investigate issues, and prepare changes. Start with a
record name or a question. You do not need to know the MCP tool names.

| Work                                     | What you can inspect                                                      |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Tables and fields                        | Field types, choice labels, references, and inherited fields.             |
| Scripts                                  | Business Rules, Script Includes, Client Scripts, and code-search matches. |
| Flow Designer                            | Flow and subflow triggers, inputs, outputs, and configured steps.         |
| Troubleshooting                          | Recent errors, slow transactions, table health, and audit history.        |
| Configuration management database (CMDB) | Configuration items, their relationships, and class metadata.             |

For example:

- "Read the Business Rule named 'Validate priority'. Explain when it runs and what its script does."
- "Find scripts that call `gs.eventQueue`. Show the matching records."
- "Inspect the 'New starter' flow. Explain its trigger and configured steps."
- "Investigate script errors from the last 24 hours. Show the evidence for each finding."

Replace example rule and flow names with names from your instance.
Diagnostic findings help you investigate. They do not establish a root cause or repair the instance.
The `flow` tool inspects configuration, checks saved V2 structure locally, and reads status.
It cannot change or execute a flow.

With write tools enabled, you can create or update configuration records,
including script fields. Follow the [change workflow](#making-changes) before
applying a script change.

## Connection setup

An administrator prepares OAuth. Each user authorizes access with their own
ServiceNow account. Your AI app starts the server locally using **stdio**.
You need Python 3.14 or later, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and an AI app that can start local MCP servers.

### 1. Prepare OAuth in ServiceNow

OAuth lets users authorize the connection in a browser. The public client ID
identifies the application and is not a password or client secret.

Open **System OAuth > Application Registry**. Select **New**, then
**Create an OAuth API endpoint for external clients**, or select an existing inbound application.
Configure the application to meet these requirements:

| Requirement        | Value                                  |
| ------------------ | -------------------------------------- |
| Client type        | Public, with no client secret          |
| Authorization flow | Authorization code with PKCE           |
| PKCE method        | `S256`                                 |
| Scope              | `useraccount`                          |
| Redirect URL       | `http://127.0.0.1:8765/oauth/callback` |

Save the application and copy its public client ID. If you cannot configure
OAuth, ask your administrator for the client ID and instance URL.
Users still need API access and table and field permissions.

### 2. Add the connection to your AI app

Replace `https://your-instance.service-now.com` and `your-public-client-id`
with the values from your administrator. Use the instance's HTTPS address
without a page path such as `/nav_to.do`.

These examples select `readonly` and block writes with `SERVICENOW_ENV=prod`.
The environment setting controls write protection. It does not select the
instance or detect whether the instance is production.

Keep existing server entries when adding this configuration.
Do not add passwords, API keys, client secrets, or tokens.

#### VS Code with GitHub Copilot, or Claude Code

Save this configuration as `.mcp.json` in the top-level folder you open in your AI app:

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
For user-wide configuration and connection controls, see the
[VS Code guide](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
or [Claude Code guide](https://code.claude.com/docs/en/mcp).

<details>
<summary>OpenCode configuration</summary>

Add this to `opencode.json` in your project:

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

Restart OpenCode. See its [MCP guide](https://opencode.ai/docs/mcp-servers/)
for other configuration options.

</details>

For another local MCP app, use the command `uvx servicenow-platform-mcp`
with the same four environment variables. Its configuration format may differ.
The [installation guide](INSTALL.md#2-connect-your-ai-app) includes a server
entry you can adapt.

### 3. Sign in and check access

1. Restart your AI app or its ServiceNow MCP server after saving the configuration.
2. Ask: "List the available ServiceNow tools."
3. Ask: "Use ServiceNow to show one active incident. Include its number and short description."
4. If prompted, sign in through the browser with the intended ServiceNow account.

A returned record confirms read access to that table. If you cannot read
incidents, use a table your account can access. Tool discovery alone does not
confirm a working connection.

Tokens stay in server memory by default. The server renews expired access tokens when possible.
Optional `SERVICENOW_OAUTH_CACHE=os` stores only refresh tokens in the native OS credential store,
so a new local process can reuse a previous login. See [OAuth cache configuration](docs/wiki/Configuration.md#optional-os-oauth-cache).

## Making changes

Start with a development or test instance. Ask your administrator to enable
only the write tools you need and grant the corresponding ServiceNow permissions.

For example, `MCP_TOOL_PACKAGE=query,describe,record_read,record_write`
enables record inspection and changes. Set `SERVICENOW_ENV=dev` for that
non-production instance. Restart the server after changing the configuration.

Record, attachment, and catalog changes require a preview before `apply_change`.
The server does not enforce human approval. Review each change before applying it:

1. Ask the app to read the current record.
2. Ask for a preview of the proposed change.
3. Review the target instance, record, field values, and script differences.
4. Tell the app to apply that preview only when you approve it.
5. Check the result in ServiceNow. Test any changed behavior on the instance.

For a ticket update:

> Preview adding this work note to INC0012345: "Waiting for the caller to confirm
> the fix." Show the proposed change and wait for my approval.

For a script update:

> Read the Business Rule named 'Validate priority'. Suggest a correction and
> explain it. Show a preview with the complete replacement script. Wait for my
> approval before applying it.

The preview tools return single-use tokens. `apply_change` checks policy,
authorization, and detectable state changes. Tokens expire after 300 seconds
and disappear on restart. If the result is `UNKNOWN_OUTCOME`, read the target
state before another preview. Do not automatically replay the operation.

The `readonly` package includes catalog browsing. Catalog changes require the
`service_catalog_write` group. A preview does not simulate fulfillment or guarantee prices.

Script updates replace the complete field value. The server does not check
JavaScript syntax. Review and test the script before relying on its behavior.

Set `SERVICENOW_ENV=prod` or `production` to block writes through this server.
ServiceNow permissions still control record access. Tool selection and environment settings add local controls.

## Choose the available tools

`MCP_TOOL_PACKAGE` controls which tools your AI app can call:

| Package         | Use                                                                                                 |
| --------------- | --------------------------------------------------------------------------------------------------- |
| `readonly`      | ITSM reads, metadata, attachments, investigations, audits, flows, code search, and CMDB inspection. |
| `core_readonly` | Only queries, table descriptions, and attachment reads.                                             |
| `full`          | All tools, including reviewed record, attachment, and catalog changes.                              |
| `none`          | Only `list_tool_packages`, for checking that the server starts.                                     |

The package defaults to `readonly` and the environment defaults to `prod`.
A package enables tools. It does not grant ServiceNow permissions.
`list_tool_packages` lists available packages and groups, not the active package.
For the full inventory and tool inputs, see the [tool reference](docs/wiki/Tool-Reference.md).

## If something does not work

| Problem                                                | Next action                                                                                       |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| The AI app cannot start the server                     | Check Python, `uv`, the server command, and the two required ServiceNow settings.                 |
| No browser opens, or authorization times out           | Use a browser on the server's computer. Check the exact registered redirect URL.                  |
| ServiceNow rejects OAuth                               | Ask the administrator to check public-client mode, PKCE S256, client ID, scope, and redirect URL. |
| Sign-in works, but a record request returns 401 or 403 | Ask the administrator to check API policies, roles, and record or field access rules.             |
| Tools are missing or settings did not change           | Check `MCP_TOOL_PACKAGE` and restart the MCP server.                                              |

For a failed request, include the action, record or table, and sanitized error
in your report. Exclude passwords, tokens, raw headers, and browser callback URLs.
See [installation troubleshooting](INSTALL.md#troubleshooting)
for more detailed checks.

ServiceNow data returned by tools becomes available to your AI app.
Use an app approved for the data you handle.

## More help

- [ITSM work](docs/wiki/ITSM-Work.md): ticket queues, history, request answers, and ticket changes.
- [Instance administration and development](docs/wiki/Instance-Development.md): tables, scripts, flows, investigations, and reviewed changes.
- [Installation and configuration](INSTALL.md): connection setup, source installation, and troubleshooting.
- [Complete wiki](docs/wiki/Home.md): settings, tool reference, safety limits, and diagnostics.
- [Contributing to this MCP server](docs/wiki/Development.md): local development, tests, and releases.

[Report an issue](https://github.com/Xerrion/servicenow-platform-mcp/issues) ·
[PyPI package](https://pypi.org/project/servicenow-platform-mcp/) · [MIT license](LICENSE)
