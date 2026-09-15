# ServiceNow Platform MCP Server

`servicenow-platform-mcp` gives an MCP client controlled access to ServiceNow
through ServiceNow REST APIs. It runs as a local child process over MCP stdio.

## Choose a path

### Operators

1. [[Getting-Started]] - install the server, configure OAuth, connect an MCP client, and verify access.
2. [[Configuration]] - set environment variables and choose a tool package.
3. [[Tool-Packages]] - choose the smallest tool surface that meets the use case.
4. [[Tool-Reference]] - find tool actions, inputs, limits, and response behavior.
5. [[Safety-and-Policy]] - understand table restrictions, masking, query limits, and write gates.

### Contributors

1. [[Development]] - set up a checkout, run checks, and understand CI.
2. [[Architecture]] - follow bootstrap, authentication, tool registration, state, and error flow.
3. [[Telemetry]] - configure or change Sentry and bounded runtime telemetry.

## Operational facts

- An MCP client launches the server. It is not an HTTP service.
- Authentication uses public ServiceNow OAuth authorization-code PKCE S256.
- The first ServiceNow call opens the browser on the machine running the server.
- The authorizing ServiceNow user's roles and ACLs apply to requests.
- Access tokens stay in process memory. They are not persisted or placed in URLs.
- `MCP_TOOL_PACKAGE` controls loaded tools. It does not grant ServiceNow access.
- `SERVICENOW_ENV=prod` or `SERVICENOW_ENV=production` blocks local writes.

## Project links

- [Repository](https://github.com/Xerrion/servicenow-platform-mcp)
- [PyPI](https://pypi.org/project/servicenow-platform-mcp/)
- [Issues](https://github.com/Xerrion/servicenow-platform-mcp/issues)
- [License](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/LICENSE)
