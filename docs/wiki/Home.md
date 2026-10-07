# ServiceNow Platform MCP

Use your AI app to find tickets, review requests, understand scripts, and prepare
changes to your ServiceNow instance. Describe the work in plain language.
The app chooses the ServiceNow tools it needs.

MCP (Model Context Protocol) connects an AI app to tools from another system.
This server uses your ServiceNow account. Your roles and access control rules
still determine what you can read or change.

## Start with your task

| What you want to do | Where to start |
| --- | --- |
| Connect for the first time or check an existing connection | [[Getting-Started]] |
| Find tickets, read work notes, review request answers, or prepare a ticket update | [[ITSM-Work]] |
| Understand tables, scripts, flows, or instance issues | [[Instance-Development]] |
| Install the server or prepare the connection for other users | [Installation guide](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md) |

If the connection is already available in your AI app, try:

> Use ServiceNow to show 5 active incidents I can access. Include their number,
> short description, priority, and assigned person. Do not change anything.

The first request that needs ServiceNow opens your browser for authorization.
Use the browser on the computer running the server.

## Choose what the app can do

Start with `MCP_TOOL_PACKAGE=readonly`. This package includes ticket reads,
table information, script inspection, Flow Designer inspection, and investigations.
The server defaults to `full` if you omit the setting.

Write tools need separate configuration and ServiceNow permissions.
Record changes offer previews, but the server does not enforce human approval.
Attachment changes and catalog orders apply directly.
Read [[Safety-and-Policy]] before enabling writes.

For production connections, set `SERVICENOW_ENV=prod` or `production` to block
writes through this server. This setting does not detect the instance type.

## Find a specific answer

- [[Configuration]]: connection settings, defaults, and limits.
- [[Tool-Packages]]: choose the tools available to your AI app.
- [[Tool-Reference]]: exact tool inputs, actions, and response details.
- [[Safety-and-Policy]]: access restrictions and how changes work.
- [[Telemetry]]: optional monitoring and what it sends.

## Contribute to the MCP server

Developing your ServiceNow instance uses [[Instance-Development]].
Changing this Python server uses [[Development]] and [[Architecture]].
Server contributions need a repository checkout and the project's checks.
You do not need that workflow to use the MCP on your instance.

[Repository and README](https://github.com/Xerrion/servicenow-platform-mcp) ·
[Report an issue](https://github.com/Xerrion/servicenow-platform-mcp/issues) ·
[PyPI package](https://pypi.org/project/servicenow-platform-mcp/) ·
[MIT license](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/LICENSE)
