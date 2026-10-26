# How the server works

The server connects an AI app to a ServiceNow instance. It runs on the same
computer as the AI app. The app starts the server and sends requests through a
local connection called stdio.

1. The AI app selects an available tool and supplies its inputs.
2. The server checks the inputs and any local safety rules.
3. When needed, the server calls ServiceNow using the signed-in user's access.
4. The server returns the result to the AI app.

ServiceNow decides which records the user can access. The server can add local
restrictions, but it cannot grant access. See [Safety and Policy](Safety-and-Policy.md).

## Where to make changes

- `src/servicenow_mcp/mcp/tools/` defines the tools exposed to AI apps.
- `src/servicenow_mcp/capabilities/` carries out the requested work.
- `src/servicenow_mcp/platform/` communicates with ServiceNow and manages
  shared metadata.
- `src/servicenow_mcp/policy/` applies local safety rules.
- `src/servicenow_mcp/bootstrap/` assembles the server and selected tools.

The [Development](Development.md) explains how to check a change. The
[Tool Reference](Tool-Reference.md) lists the available tools.
