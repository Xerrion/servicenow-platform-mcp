# ServiceNow DevTools MCP Server

A developer and debug-focused MCP server for ServiceNow - platform introspection, change intelligence, debugging, investigations, and documentation generation.

---

## What is this?

**servicenow-devtools-mcp** is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents direct access to your ServiceNow instance. It exposes a comprehensive suite of tools covering schema exploration, record management, debugging, change intelligence, ITSM processes, and more - all through a standardized MCP interface.

The server runs locally via stdio transport and is launched by your MCP-compatible AI client (OpenCode, Claude Desktop, VS Code Copilot, Cursor, etc.). Your AI agent gains the ability to query tables, inspect records, trace debug logs, generate documentation, manage incidents, and much more - without you needing to navigate the ServiceNow UI.

---

## Key Capabilities

- **Schema and table introspection** - Describe table schemas, query records, compute aggregates, and build structured queries
- **Record CRUD** - Create, read, update, and delete records with a preview-then-apply confirmation pattern
- **Attachment operations** - List, download, upload, and delete attachments with base64 content transfer
- **Change intelligence** - Inspect update sets, diff artifact versions, view audit trails, and generate release notes
- **Debug and trace** - Build event timelines, trace field mutations, inspect flow executions, check integration health
- **Investigations** - Run automated analyses: stale automations, deprecated APIs, table health, ACL conflicts, error patterns, slow transactions
- **Documentation generation** - Generate automation maps, artifact summaries, test scenarios, and code review notes
- **Workflow and Flow Designer analysis** - Map workflow structures, inspect executions, analyze migration readiness
- **ITSM domain tools** - Full lifecycle management for Incidents, Changes, Problems, Requests, Knowledge, CMDB, and Service Catalog
- **Artifact write** - Create and update platform artifacts (business rules, script includes, client scripts, etc.) with optional local script file injection

---

## Quick Navigation

| Page | Description |
|---|---|
| [[Getting-Started]] | Installation, MCP client configuration, first steps |
| [[Configuration]] | Environment variables, settings reference |
| [[Tool-Reference]] | Complete tool reference with descriptions |
| [[Tool-Packages]] | Preset and custom tool packages |
| [[Safety-and-Policy]] | Security guardrails, table deny list, write gating |
| [[Development]] | Contributing, testing, CI pipeline |
| [[Architecture]] | Server internals, patterns, data flow |
| [[Telemetry]] | Sentry error tracking setup |

---

## Quick Start

**1. Run the server**

```bash
uvx servicenow-devtools-mcp
```

**2. Set environment variables**

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_USERNAME=admin
SERVICENOW_PASSWORD=your-password
```

**3. Configure your MCP client** to launch the server with those environment variables.

See [[Getting-Started]] for full setup instructions with configuration examples for OpenCode, Claude Desktop, VS Code, Cursor, and more.

---

## Links

- [PyPI](https://pypi.org/project/servicenow-devtools-mcp/)
- [GitHub Repository](https://github.com/Xerrion/servicenow-devtools-mcp)
- [Issues](https://github.com/Xerrion/servicenow-devtools-mcp/issues)
- [License (MIT)](https://github.com/Xerrion/servicenow-devtools-mcp/blob/main/LICENSE)
