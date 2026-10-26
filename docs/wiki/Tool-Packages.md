# Choose tools

Your AI app can use only the tools enabled in its server configuration. Set
`MCP_TOOL_PACKAGE` to a preset or a list of groups. Restart the server after a
change.

## Presets

- `readonly`: all read groups. This is the default.
- `core_readonly`: `query`, `describe`, and `attachment`.
- `full`: all read and write groups.
- `none`: no groups. The package list remains available.

A preset stands alone. Do not add a preset name to a list of groups.

## Select groups

For example, this selection reads records and prepares record changes:

```text
MCP_TOOL_PACKAGE=query,describe,record_read,record_write
```

Read groups are `query`, `describe`, `record_read`, `attachment`,
`investigate`, `resolve_choice`, `service_catalog`, `analysis`, `audit`,
`flow`, `code_search`, and `cmdb`.

Write groups are `record_write`, `attachment_write`, and
`service_catalog_write`. Each adds its preview tool and `apply_change`. Select
only the groups you need.

`list_tool_packages` is always available. It lists the presets and their groups.
See the [Tool reference](Tool-Reference.md) for tool names and actions.

## Before enabling writes

The configured environment must allow writes. The signed-in account also needs
permission in ServiceNow. `SERVICENOW_ENV` is a local setting; it does not tell
the server whether the selected instance is production.

Use a test instance first. Review each preview before asking the app to apply
it. See [Safety and policy](Safety-and-Policy.md) for the limits and recovery steps.
