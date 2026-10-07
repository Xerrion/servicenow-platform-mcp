# Getting started

Open your AI app and try one small ServiceNow read.
You can describe the task in plain language without knowing API requests or tool names.

## 1. Check whether the connection is available

Ask:

> List the available ServiceNow tools. Do not change anything in ServiceNow.

If the app has no ServiceNow tools, follow the
[installation guide](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md)
or ask your administrator to prepare the connection.

An administrator provides the instance address and a public OAuth client ID.
OAuth lets you authorize access through your browser.
Each user signs in with their own ServiceNow account.

For the first connection, use these settings in the app's server configuration:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=prod
```

Replace the first two values with those from your administrator.
This is a settings example, not a complete configuration file for an AI app.
The installation guide gives the server command and the file format for supported apps.

`readonly` exposes read tools. `prod` also blocks writes through this server.
The environment setting does not select an instance or identify a production instance.
Do not put passwords, tokens, API keys, or client secrets in the configuration.

## 2. Read one record

Ask:

> Use ServiceNow to show one active incident I can access. Include its number
> and short description. Do not change anything.

When the browser opens, authorize the connection with the intended ServiceNow account.
The browser must run on the same computer as the server.
The supported setup runs the server locally through your AI app.

A returned record confirms access to that table.
The tool list alone does not confirm a working ServiceNow connection.
If your account cannot read incidents, try a table your administrator confirms you can access.

An empty result can mean that no record matches the filter or that access rules hide matching records.
Ask the app to show the table and filters it used before drawing a conclusion.

## 3. Choose your first useful task

### For ITSM work

Choose a real ticket number from your instance:

> Read INC0012345. Summarize the issue, current state, assignment, and work notes
> from the last 30 days. Include the ticket number. Do not change anything.

Check the summary against the ticket in ServiceNow.
Use [[ITSM-Work]] for queue reviews, request answers, attachments, and ticket updates.

### For instance administration and development

Choose a real Business Rule name from your instance:

> Read the Business Rule named 'Validate priority'. Show its table, conditions,
> and complete script. Explain when it runs. Do not change anything.

If several rules have that name, ask the app to list the matches first.
Select the intended record before continuing.
Use [[Instance-Development]] for table information, script changes, Flow Designer inspection, and troubleshooting.

## When access fails

| What happens | What to check next |
| --- | --- |
| The app has no ServiceNow tools | Check the connection settings and selected tool package. Restart the MCP server after changes. |
| The browser does not open or authorization fails | Follow the [installation troubleshooting](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md#troubleshooting). |
| Sign-in succeeds, but a read returns 401 or 403 | Ask your administrator to check API access policies, roles, and table or field permissions. |
| A field or history entry is missing | Check the requested fields, access rules, time period, and any result limits. |
| A write is unavailable or blocked | Check [[Tool-Packages]] and [[Safety-and-Policy]]. Use a development or test instance for write testing. |

ServiceNow data returned by tools becomes available to your AI app.
Use an app approved for the data you handle.

The server keeps tokens in memory and renews expired access tokens when possible.
Restarting the server requires browser authorization again.

For a problem report, include the action, table or record, and sanitized error.
Exclude passwords, tokens, raw headers, and browser callback URLs.
