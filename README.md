# ServiceNow Platform MCP

Use your AI app to work with ServiceNow tickets, understand your instance, and
prepare changes to records or scripts.

MCP (Model Context Protocol) lets an AI app use tools from another system.
This server connects those tools to your ServiceNow instance. You describe the
work in plain language, and the app calls the tools it needs.

You sign in with your ServiceNow account. Your roles, API access policies, and
access control rules (ACLs) still determine what you can read or change.

## Start here

- **Working with tickets and requests?** Start with [ITSM work](#itsm-work).
- **Administering or developing an instance?** Start with [instance administration and development](#instance-administration-and-development).
- **Connecting for the first time?** Follow [connection setup](#connection-setup), or ask your administrator for the configuration.

If your AI app already has this server configured, try:

> Use ServiceNow to show 5 active incidents I can access. Include the incident
> number, short description, priority, and assigned person. Do not change anything.

The first request that needs ServiceNow opens your browser for sign-in and
permission to connect. Use the browser on the computer running the MCP server.

## ITSM work

Read incident, problem, change, and request records. Review ticket history,
inspect attachments, or count records for a status update. You can ask for the
result as a list, table, or summary.

For example:

- "Show 10 active incidents assigned to the Service Desk group, ordered by priority."
- "Summarize INC0012345, including comments and work notes from the last 30 days."
- "Count open incidents by assignment group. Include the filters you used."
- "Show the answers submitted with RITM0012345."

Replace example numbers and group names with values from your instance.
State a time period and limit when you ask for a list or history.
Ask the app to include record numbers so you can check its answer in ServiceNow.

Creating tickets, updating work notes, and changing assignments require write
tools and ServiceNow permissions. See [making changes](#making-changes).

## Instance administration and development

Use the MCP to inspect how your instance works, investigate issues, and prepare
changes to ServiceNow configuration. You can start with a record name or a
question. You do not need to write API requests or know the MCP tool names.

| Work | What you can inspect |
| --- | --- |
| Tables and fields | Field types, choice labels, references, and inherited fields. |
| Scripts | Business Rules, Script Includes, Client Scripts, and code-search matches. |
| Flow Designer | Flow and subflow triggers, inputs, outputs, and configured steps. |
| Troubleshooting | Recent errors, slow transactions, table health, and audit history. |
| Configuration management database (CMDB) | Configuration items, their relationships, and class metadata. |

For example:

- "Read the Business Rule named 'Validate priority'. Explain when it runs and what its script does."
- "Find scripts that call `gs.eventQueue`. Show the matching records."
- "Inspect the 'New starter' flow. Explain its trigger and configured steps."
- "Investigate script errors from the last 24 hours. Show the evidence for each finding."

Replace example rule and flow names with records from your instance.

Diagnostic findings help you investigate a cause. They do not establish a root
cause or automatically repair the instance. Flow Designer tools inspect stored
configuration. They do not edit, publish, or run flows.

With write tools enabled, you can create or update configuration records,
including script fields. Follow the [change workflow](#making-changes) before
applying a script change.

## Connection setup

An administrator prepares the ServiceNow connection. Each user then authorizes
access with their own ServiceNow account.

The supported connection runs locally on your computer. Your AI app starts
the server using **stdio**, a local connection between the two programs.
You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and an AI app that can start local MCP servers.

### 1. Prepare OAuth in ServiceNow

OAuth lets users authorize the connection through their browser.
The public client ID identifies the application. It is not a password or a client secret.

Open **System OAuth > Application Registry**. Select **New**, then
**Create an OAuth API endpoint for external clients**, or select an existing inbound application.
Configure the application to meet these requirements:

| Requirement | Value |
| --- | --- |
| Client type | Public, with no client secret |
| Authorization flow | Authorization code with PKCE |
| PKCE method | `S256` |
| Scope | `useraccount` |
| Redirect URL | `http://127.0.0.1:8765/oauth/callback` |

Save the application. Copy its public client ID for the next step.
If you cannot configure OAuth, ask your ServiceNow administrator to provide
that ID and the instance URL.

Users need API access to the records required for their work. OAuth does not
grant additional table or field permissions.

### 2. Add the connection to your AI app

Replace `https://your-instance.service-now.com` and `your-public-client-id`
with the values from your administrator. Use the instance's HTTPS address
without a page path such as `/nav_to.do`.

These examples explicitly select `readonly` and block writes with
`SERVICENOW_ENV=prod`. The environment setting controls write protection.
It does not select an instance or detect whether that instance is production.

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
4. Complete authorization in the browser with the intended ServiceNow account.

A returned record confirms that the connection can read that table.
If you cannot read incidents, use a table your account can access.
Tool discovery alone does not confirm a working ServiceNow connection.

Tokens stay in server memory. The server renews an expired access token when
it can. Restarting the server requires browser authorization again.

## Making changes

Begin with a development or test instance. Ask your administrator to enable
only the write tools needed for your work and grant the corresponding
ServiceNow permissions.

For example, `MCP_TOOL_PACKAGE=query,describe,record_read,record_write`
enables record inspection and changes. Set `SERVICENOW_ENV=dev` for that
non-production instance. Restart the server after changing the configuration.

Record writes offer previews, but callers can request immediate changes.
The server does not enforce human approval. Use this review process for record changes:

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

Record writes default to a preview. `record_apply` applies its one-time
`preview_token`. A caller can also request an immediate write with
`preview=false`.
Previews expire after five minutes and disappear when the server restarts.

Attachment uploads and deletions, catalog orders, and cart changes apply
directly. They do not use the record preview workflow.
Catalog actions require `full` or a custom package containing `service_catalog`.
That group includes both browsing and ordering. It is absent from `readonly`.

Script updates replace the complete field value. The server does not check
JavaScript syntax. Review and test the script before relying on its behavior.

Set `SERVICENOW_ENV=prod` or `production` for production connections to block
writes through this server. ServiceNow permissions remain the authority for
record access. Tool selection and environment settings are additional local controls.

## Choose the available tools

`MCP_TOOL_PACKAGE` controls which tools your AI app can call:

| Package | Use |
| --- | --- |
| `readonly` | ITSM reads, metadata, attachments, investigations, audits, flows, code search, and CMDB inspection. |
| `core_readonly` | Only queries, table descriptions, and attachment reads. |
| `full` | All tools, including record writes, attachment writes, and catalog actions. |
| `none` | Only `list_tool_packages`, for checking that the server starts. |

**Set this explicitly. The server defaults to `full` when you omit it.**
A package enables tools. It does not grant ServiceNow permissions.
`list_tool_packages` lists available packages and groups, not the active package.
For the full inventory and tool inputs, see the [tool reference](docs/wiki/Tool-Reference.md).

## If something does not work

| Problem | Next action |
| --- | --- |
| The AI app cannot start the server | Check Python, `uv`, the launch command, and the two required ServiceNow settings. |
| No browser opens, or authorization times out | Use a browser on the server's computer. Check the exact registered redirect URL. |
| ServiceNow rejects OAuth | Ask the administrator to check public-client mode, PKCE S256, client ID, scope, and redirect URL. |
| Sign-in works, but a record request returns 401 or 403 | Ask the administrator to check API policies, roles, and record or field access rules. |
| Tools are missing or settings did not change | Check `MCP_TOOL_PACKAGE` and restart the MCP server. |

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
