# ServiceNow Platform MCP Server

A comprehensive MCP server for ServiceNow - platform introspection, change intelligence, debugging, investigations, and documentation generation.

---

## What is this?

**servicenow-platform-mcp** is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI agents direct access to your ServiceNow instance. It exposes a comprehensive suite of tools covering schema exploration, record management, debugging, change intelligence, ITSM processes, and more - all through a standardized MCP interface.

The server runs locally via stdio transport and is launched by your MCP-compatible AI client (OpenCode, Claude Desktop, VS Code Copilot, Cursor, etc.). Your AI agent gains the ability to query tables, inspect records, trace debug logs, generate documentation, manage incidents, and much more - without you needing to navigate the ServiceNow UI.

---

## Key Capabilities

- **Schema and table introspection** - Describe table schemas, query records with encoded queries, and compute aggregates
- **Record CRUD** - Create, read, update, and delete records with a preview-then-apply confirmation pattern
- **Attachment operations** - List, download, upload, and delete attachments with base64 content transfer
- **Audit inspection** - Check table and field audit configuration and read bounded audit history
- **Read-only analysis** - Inspect submitted RITM variables and dictionary-confirmed journal history
- **Investigations** - Run automated analyses: stale automations, deprecated APIs, table health, ACL conflicts, error patterns, slow transactions
- **Flow Designer inspection** - Read flow and subflow configuration from V1/V2 table records
- **Generic ITSM records and Service Catalog** - Use table tools for ITSM and CMDB records; browse catalogs and perform gated order/cart actions
- **Artifact write** - Create and update platform artifacts (business rules, script includes, client scripts, etc.) with complete inline field values in `record_write.data`

---

## Quick Navigation

| Page | Description |
| --- | --- |
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

### 1. Configure the public ServiceNow application

In **System OAuth > Application Registry**, create or select the application:

- **Public Client**: `true`
- Authorization-code PKCE: **S256**
- Scope: `useraccount`
- Exact redirect URL: `http://127.0.0.1:8765/oauth/callback`

Save the application and copy its client ID.

### 2. Configure the local server

Create `.env.local` in the MCP server's working directory, or forward these
values through the MCP client's environment settings:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_SCOPE=useraccount
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
MCP_TOOL_PACKAGE=readonly
```

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`;
non-empty values fail startup. A stale `SERVICENOW_OAUTH_CLIENT_SECRET` is
ignored. Remove it rather than configuring a secret. Never commit dotenv files.

### 3. Launch through the MCP client and authorize

Use [[Getting-Started]] to install and configure the stdio command. The first
tool call that needs ServiceNow opens the default browser. The browser and MCP
server must run on the same machine. Authorize as the ServiceNow user whose
roles and ACLs should apply to tool calls; the client ID identifies the application.

Only the access token and its expiry stay in memory.
Restart or expiry requires browser authorization on the next outbound call. REST calls
use Bearer headers, never tokens in URLs. A REST 401 is not replayed.

An API-key-only REST policy can block OAuth even after token issuance. See
[[Configuration]] for narrowly scoped policy migration and troubleshooting.

---

## Links

- [PyPI](https://pypi.org/project/servicenow-platform-mcp/)
- [GitHub Repository](https://github.com/Xerrion/servicenow-platform-mcp)
- [Issues](https://github.com/Xerrion/servicenow-platform-mcp/issues)
- [License (MIT)](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/LICENSE)
