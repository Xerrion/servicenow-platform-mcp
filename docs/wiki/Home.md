# ServiceNow Platform MCP Server

`servicenow-platform-mcp` is an async Python 3.12+ MCP server that gives AI assistants structured access to a ServiceNow instance. It communicates over stdio, exposes a composable tool surface for schema discovery, record operations, Flow Designer inspection, audit-trail queries, investigation playbooks, and Service Catalog ordering - all governed by built-in safety controls that deny sensitive tables, mask credentials in responses, enforce query-safety limits on large tables, and gate writes in production environments.

---

## Wiki Directory

Go to the page that matches what you need:

| Page | Go here if you need to... |
|------|---------------------------|
| [[Getting-Started]] | Install the server, configure credentials, and run your first query. |
| [[Configuration]] | Understand every environment variable and its effect on runtime behavior. |
| [[Tool-Packages]] | Choose a preset package or build a custom tool surface from individual groups. |
| [[Tool-Reference]] | Look up a specific tool's actions, parameters, required arguments, or response shape. |
| [[Investigations]] | Run or author investigation playbooks (stale automations, deprecated APIs, ACL conflicts, and more). |
| [[Architecture]] | Understand the server bootstrap sequence, tool registration pattern, client lifecycle, and error hierarchy. |
| [[Safety-and-Policy]] | Learn which tables are denied, how fields are masked, how query safety works, and how write gating protects production. |
| [[Telemetry]] | Configure Sentry integration and understand argument redaction before data leaves the process. |
| [[Development]] | Run the test suite, lint, type-check, and contribute changes. |

---

## Installation

For install instructions, dependency setup, and first-run guidance, see the repository [README](https://github.com/AstreaTech/servicenow-platform-mcp#readme).

---

## Quick Facts

- **Version**: 0.10.0
- **License**: MIT
- **Python**: 3.12, 3.13, 3.14
- **Transport**: stdio (MCP SDK)
- **Build system**: hatchling
- **Package manager**: uv

---

## Project Layout

```
src/servicenow_mcp/
  server.py          # Bootstrap and entry point
  config.py          # Settings (pydantic-settings)
  client.py          # Async HTTP client for ServiceNow REST API
  policy.py          # Table deny-list, field masking, query safety, write gating
  errors.py          # Exception hierarchy
  tools/             # One module per tool group
  investigations/    # Investigation playbook modules
```
