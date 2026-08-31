# Tool Packages

Tool packages control which tools are loaded when the server starts. Configure the active package via the `MCP_TOOL_PACKAGE` environment variable.

The server has 12 tool groups and 4 focused presets. The public `full` surface contains 14 tools. For a complete reference of every tool, see [[Tool-Reference]]. For security guardrails that apply across all packages, see [[Safety-and-Policy]].

---

## Preset Packages

| Package | Total MCP Tools | Description |
|---|---|---|
| `full` (default) | 14 | All unified tools, including `audit`, `flow`, and `code_search` |
| `readonly` | 10 | Includes `record_read`, `audit`, `flow`, `code_search`, and attachment reads |
| `core_readonly` | 4 | Minimal read-only core: `query`, `describe`, `attachment`, `list_tool_packages` |
| `none` | 1 | No tools loaded - only `list_tool_packages` is available |

---

## Package Contents

The `list_tool_packages` tool is always available and returns the active registry at runtime. The detailed lists below name package tools; add `list_tool_packages` to get the total MCP tool count shown above.

### `full` (13 package tools)

`query`, `describe`, `record_read`, `record_write`, `record_apply`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `service_catalog`, `audit`, `flow`, `code_search`.

### `readonly` (9 package tools)

`query`, `describe`, `record_read`, `attachment`, `investigate`, `resolve_choice`, `audit`, `flow`, `code_search`.

### `core_readonly` (3 package tools)

`query`, `describe`, `attachment`.

---

## Custom Packages

You can create a custom package by setting `MCP_TOOL_PACKAGE` to a comma-separated list of tool names:

```bash
MCP_TOOL_PACKAGE="query,describe,attachment"
```

The `attachment` group registers only the read tool. Add `attachment_write` explicitly to opt in to attachment upload and delete. Runtime write gating still applies to the opt-in group.

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
