# Choose the available tools

`MCP_TOOL_PACKAGE` defaults to `readonly`.
`SERVICENOW_ENV` defaults to `prod`, which blocks mutation previews and application.
Tool selection does not grant ServiceNow permissions.
Restart the application after changing settings.

## Preset packages

| Package | Available work |
| --- | --- |
| `readonly` | Records, metadata, attachments, history, submitted answers, investigations, audits, flows, code search, CMDB, and catalog browsing. |
| `core_readonly` | Queries, table descriptions, and attachment reads. |
| `full` | All read tools, all mutation previews, and shared application. |
| `none` | Only `list_tool_packages`. |

`list_tool_packages` lists package definitions and groups without contacting ServiceNow.
It does not report the active package.
Use the AI app's tool list to check the actual registered surface.

## Read groups

| Group | Tool |
| --- | --- |
| `query` | List records, read an exact record, or calculate aggregates. |
| `describe` | Inspect tables, inherited fields, and script fields. |
| `record_read` | Read a record by `sys_id` or its actual `name`. |
| `resolve_choice` | List or resolve choice keys. |
| `attachment` | List, inspect, or download attachments. |
| `analysis` | Read journals and submitted requested-item answers. |
| `audit` | Inspect audit configuration and field history. |
| `investigate` | Run bounded checks or explain findings. |
| `flow` | Inspect Flow Designer configuration and stored values. |
| `code_search` | Search scripts or inspect search coverage. |
| `cmdb` | Read CIs, relationships, and class metadata. |
| `service_catalog` | Browse catalogs, categories, items, variables, and the cart. |

## Mutation groups

| Group | Preview tool |
| --- | --- |
| `record_write` | `record_preview` for create, update, and delete. |
| `attachment_write` | `attachment_preview` for upload and delete. |
| `service_catalog_write` | `service_catalog_preview` for orders and cart changes. |

Selecting any mutation group registers `mutation_apply` exactly once.
Application rejects commands from families that are not enabled.
Every mutation requires preview followed by apply.
A preview token does not prove human approval.

For a development instance:

```dotenv
MCP_TOOL_PACKAGE=query,describe,record_read,record_write
SERVICENOW_ENV=dev
```

Verify the instance URL before permitting changes.
`SERVICENOW_ENV` controls local protection and does not select or detect the instance.

## Custom selections

A custom selection uses comma-separated group names:

```dotenv
MCP_TOOL_PACKAGE=query,describe,service_catalog
```

Use a preset alone or combine group names.
Unknown names, empty names, and a preset combined with groups fail startup.
Repeated groups are deduplicated in their first-occurrence order.

See [[Tool-Reference]] for current inputs and [[Safety-and-Policy]] for mutation limits.
