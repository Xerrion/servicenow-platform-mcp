# Choose the available tools

Start with `MCP_TOOL_PACKAGE=readonly` for ITSM reads and instance investigation.
A tool package selects which tools your AI app can call.
It does not grant ServiceNow permission to read or change records.

**Set the package explicitly. The server defaults to `full`, which includes write tools.**
Restart the MCP server after changing the package.

## Preset packages

| Package | Available work |
| --- | --- |
| `readonly` | Records, metadata, attachments, ticket history, submitted request answers, investigations, audits, flows, code search, and CMDB inspection. |
| `core_readonly` | Queries, table and field descriptions, and attachment reads. |
| `full` | All tools, including record changes, attachment changes, catalog browsing, and catalog orders. |
| `none` | Only `list_tool_packages`, for checking that the server starts. |

`list_tool_packages` is always available. It lists package definitions and group names.
It does not report the active package or contact ServiceNow.
Use your AI app's tool list to check which tools actually loaded.

Catalog browsing and ordering share the `service_catalog` group.
That group is absent from `readonly`.
A custom package containing it exposes both read and write actions.

## Choose groups for a specific task

A custom package uses comma-separated group names:

```dotenv
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

This example supports record and field inspection plus attachment reads.
For ticket history and submitted request answers, include `analysis`.

For record changes on a development or test instance:

```dotenv
MCP_TOOL_PACKAGE=query,describe,record_read,record_write
SERVICENOW_ENV=dev
```

Verify the instance URL separately. The environment label does not select the instance.
Keep `SERVICENOW_ENV=prod` or `production` on production connections to block local writes.
See [[Safety-and-Policy]] for the change and approval limits.

## Record and metadata groups

| Group | Work |
| --- | --- |
| `query` | Find records, read exact IDs, and request counts or grouped results. |
| `describe` | Inspect tables, fields, inherited metadata, and script fields. |
| `record_read` | Read one record by its internal ID or actual `name` field. |
| `resolve_choice` | List known choice keys or resolve a supported key to a stored value. |

A ticket number such as `INC0012345` is not a record's `name` field.
Find it with `query`, then use the returned `sys_id` for other record tools.

## Investigation groups

| Group | Work |
| --- | --- |
| `analysis` | Read comments and work notes, or compose submitted requested-item answers. |
| `audit` | Inspect audit configuration and field-change history. |
| `investigate` | Run bounded diagnostic checks and explain candidate findings. |
| `code_search` | Search code and inspect which tables Code Search covers. |

## Platform inspection groups

| Group | Work |
| --- | --- |
| `attachment` | List metadata or read and download attachments. |
| `flow` | Inspect Flow Designer configuration, triggers, contracts, and stored values. |
| `cmdb` | Read configuration items, relationships, and class metadata. |

These groups provide inspection tools. The dedicated Flow Designer tool does
not edit, publish, or run flows.

## Groups that include changes

| Group | Work |
| --- | --- |
| `record_write` | Create, update, or delete records. Registers both `record_write` and `record_apply`. |
| `attachment_write` | Upload or delete attachments directly. |
| `service_catalog` | Browse catalogs and carts, order items, and submit or check out carts. |

Use `record_write` as the group name. `record_apply` is a tool, not a group.
`attachment` and `attachment_write` are separate groups.

Record writes default to previews, but immediate writes are possible.
Attachment and catalog changes apply directly.
The server does not enforce human approval.
ServiceNow still checks API policies, roles, and record or field access rules.

## Custom package rules

Use preset names alone, or combine valid group names with commas.
Do not combine a preset such as `readonly` with group names.
Empty group names and unknown names fail startup. The server ignores duplicate group names.
`service_catalog` is a group, not a preset package.

For exact actions and inputs, see [[Tool-Reference]].
For connection settings, see [[Configuration]].
