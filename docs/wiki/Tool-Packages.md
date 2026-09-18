# Tool Packages

Tool packages control which MCP tools load at server startup. Set the active
package with `MCP_TOOL_PACKAGE`.

`list_tool_packages` is always available. It lists package definitions and
groups; it does not report the active package.

## Presets

Counts include `list_tool_packages`.

| Package | Public tools | Use |
| --- | ---: | --- |
| `full` | 16 | All groups, including record and attachment writes. |
| `readonly` | 12 | Read, investigation, analysis, audit, Flow, Code Search, and CMDB tools. |
| `core_readonly` | 4 | `query`, `describe`, and read-only `attachment`. |
| `none` | 1 | Only `list_tool_packages`. |

## Custom packages

Use comma-separated group names:

```text
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

Valid groups:

```text
query
describe
record_write
record_read
attachment
attachment_write
investigate
resolve_choice
service_catalog
analysis
audit
flow
code_search
cmdb
```

`record_write` registers both `record_write` and `record_apply`. Do not add
`record_apply` as a group. `attachment` is read-only; add
`attachment_write` explicitly for upload and delete. `service_catalog` is a
tool group, not a preset.

Package selection controls tool exposure. ServiceNow OAuth scopes, REST
policies, roles, and ACLs remain the authorization controls.

For tool actions and parameters, see [[Tool-Reference]]. For local write
blocking and query safety, see [[Safety-and-Policy]].
