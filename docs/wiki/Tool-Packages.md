# Tool Packages

Tool packages control which tools are loaded when the server starts. Configure the active package via the `MCP_TOOL_PACKAGE` environment variable.

The server has been consolidated from 14 packages down to 4 focused presets. For a complete reference of every tool, see [[Tool-Reference]]. For security guardrails that apply across all packages, see [[Safety-and-Policy]].

---

## Preset Packages

| Package | Tools | Description |
|---|---|---|
| `full` (default) | 11 | All 11 unified tools, including the `build_query` helper |
| `readonly` | 7 | Includes `attachment_write` (runtime write gating blocks in prod) |
| `core_readonly` | 5 | Minimal read-only core: `query`, `describe`, `attachment`, `attachment_write`, `list_tool_packages` |
| `none` | 1 | No tools loaded - only `list_tool_packages` is available |

---

## Package Contents

The `list_tool_packages` tool is always available and returns the active registry at runtime.

### `full` (11 tools)
`query`, `build_query`, `describe`, `record_write`, `record_apply`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `service_catalog`, `list_tool_packages`.

*Note: `build_query` is a stateless helper that returns an encoded query string for the caller to pass straight to `query`. It is the only tool that is exclusive to the `full` package - the read-only presets pass encoded queries to `query` directly.*

### `readonly` (7 tools)
`query`, `describe`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `list_tool_packages`.
*Note: While `attachment_write` is included at the MCP layer, the underlying `gate_write` check will block deletions and uploads if `SERVICENOW_ENV` is set to production.*

### `core_readonly` (5 tools)
`query`, `describe`, `attachment`, `attachment_write`, `list_tool_packages`.
*Note: `attachment_write` is included for symmetry; mutations are blocked in production via write gating.*

---

## Custom Packages

You can create a custom package by setting `MCP_TOOL_PACKAGE` to a comma-separated list of tool names:

```bash
MCP_TOOL_PACKAGE="query,describe,attachment"
```

### Migration from v0.9.x

The previous specialized packages (`itil`, `developer`, `incident_management`, `change_management`, `cmdb`, `problem_management`, `request_management`, `knowledge_management`, `service_catalog`, `analyst`) have been removed.

- If you were using **incident_management** or **itil**, use `full` or `readonly`.
- If you were using **developer**, use `full`.
- If you were using **service_catalog**, note that `service_catalog` is now a single tool name. You can load it specifically: `MCP_TOOL_PACKAGE="query,describe,service_catalog"`.

The unified tools (`query`, `record_write`) are more powerful than the previous domain-specific tools and can handle all ITSM and CMDB workflows when combined with `resolve_choice`.

---

## Next Steps

- [[Tool-Reference]] - Complete tool reference with descriptions and parameters
- [[Safety-and-Policy]] - Security guardrails and write gating
- [Agent Recipes](../../docs/agent-recipes.md) - Learn how to compose these tools for complex workflows
