# Tool reference

Your AI app selects tools for your request. Ask it to list the available
tools. The [tool package](Tool-Packages.md) controls which tools are listed.
Your ServiceNow permissions control what each tool can access.

This page describes the tools and their actions. The JSON examples show inputs
for apps that expose tool calls. You can also ask in plain language. Replace
example names and IDs with values from your instance. The [tool schemas](../../tests/fixtures/native_tool_schemas.json) list every input.

## Find records and fields

### `query`

Find records in a table or calculate totals. Provide a table. For a list, also
provide `fields`; `"*"` selects all fields. Use `encoded_query` to filter,
`limit` and `offset` to page, or `order_by` to sort. Use `aggregate` and
`group_by` for totals. Some large tables require a date filter.

Arguments for `query`:

```json
{ "table": "incident", "fields": ["number", "short_description"], "limit": 5 }
```

### `describe`

Find tables and field details. Supply `table` for its fields. Use `list_tables`
to search table names, or `list_script_fields` to find script fields. Set
`include_docs` to request field help and documentation links.

Arguments for `describe`:

```json
{ "action": "list_tables", "name_filter": "incident" }
```

### `record_read`

Read one record by `sys_id` or an exact value in its `name` field. Provide its
`table`. The optional `fields` list selects returned fields. Use `query` for
several records or to find an incident by number.

Arguments for `record_read`:

```json
{
  "table": "incident",
  "sys_id": "00000000000000000000000000000000",
  "fields": ["number", "short_description"]
}
```

### `resolve_choice`

Look up a choice field's stored value. Provide `table`, `field`, and its exact
`label`. Leave out `label` to request the available mapping.

Arguments for `resolve_choice`:

```json
{ "table": "incident", "field": "state" }
```

## Read related information

### `attachment`

Use `list` for a record's attachments. Provide `table` and `table_sys_id`.
Use `get` or `download` with an attachment `sys_id`. Use `download_by_name`
with the parent record and exact `file_name`.

### `analysis`

Use `ritm_variables` for a requested item's answers. Use `journal_history` for
journal entries such as comments and work notes. Supply the record `sys_id` and,
for journal history, its `table`. `describe` lists available actions.

Arguments for `analysis`:

```json
{ "action": "describe" }
```

### `audit`

Use `history` for a record's field changes. Provide `table` and `sys_id`. Use
`check_table`, `check_field`, or `check_fields` to inspect audit settings.
`describe` lists the actions. History defaults to the last 90 days.

Arguments for `audit`:

```json
{ "action": "describe" }
```

### `service_catalog`

Browse catalogs with `catalogs_list`, `catalog_get`, `categories_list`, and
`category_get`. Use `items_list`, `item_get`, or `item_variables` for items and
their questions. Lists accept `limit` and `offset`.

Other read actions are `cart_get`, `delivery_address`, `delegation`,
`wishlist_get`, `wishlist_item_get`, `get_invalid_delegated_users`, and
`variable_display_value`. Inputs depend on the action. For example, an item
lookup needs its `sys_id`.

Arguments for `service_catalog`:

```json
{ "action": "catalogs_list", "limit": 5 }
```

## Inspect the instance

### `flow`

Use `inspect` or `contract` for a flow's saved definition. Identify it by
`sys_id` or `name`. Use `find_by_table` or `list_triggers` to find relevant
triggers. `decode_values` reads an encoded value. `validate` checks saved V2
structure; it does not prove that a flow will run successfully. `validate` and
`status` need the flow `sys_id`. For `status`, add `execution_sys_id` to read
an existing execution. `describe` lists the actions.

This tool cannot change or run flows.

Arguments for `flow`:

```json
{ "action": "describe" }
```

### `investigate`

Use `describe` to discover diagnostics and their inputs. Use `run` with a
`name` and its `params`. Use `explain` to inspect a finding. Treat findings as
leads to check, not a confirmed root cause.

Arguments for `investigate`:

```json
{ "action": "describe" }
```

### `code_search`

Search indexed code with `search` and a `term`. Add `table` to narrow results.
Use `list_tables` to see searchable tables, or `describe` for options. The
search depends on the instance's code index and your access.

### `cmdb`

Use `query` to search a CMDB class and `get` to read one item. Use `meta` for
class details. Provide `class_name` for these actions. `describe` lists the
available actions.

## Prepare and apply changes

Write tools require a selected write group and permission to change the target.
The configured environment must also allow writes. Preview first. Review the
result before applying it; the server does not enforce human approval.

### `record_preview`

Prepare `create`, `update`, or `delete` for a `table`. For an update or delete,
provide the record `sys_id`. For a create or update, provide a `data` object
with the fields and values.

### `attachment_preview`

Prepare `upload` or `delete`. An upload needs the parent `table`,
`table_sys_id`, `file_name`, and `content_base64`. A delete needs the attachment
`sys_id`. The decoded upload limit is 10 MiB.

### `service_catalog_preview`

Prepare catalog requests with `order_now`, `add_to_cart`, `cart_submit`,
`cart_checkout`, or `submit_producer`. Guide actions are `checkout_guide` and
`submit_guide`. Cart and wishlist actions are `add_to_wishlist`,
`cart_item_update`, `cart_item_delete`, and `cart_empty`. Required IDs and
variables depend on the action. Read the item or cart before preparing a change.

### `apply_change`

Pass the reviewed `preview_token` to apply a prepared change. A token expires
after 300 seconds and can be used once, including after a failed attempt. If the
result is `UNKNOWN_OUTCOME`, check the target in ServiceNow before preparing
another change. Do not replay the request automatically.

## List packages

### `list_tool_packages`

List preset names and their tool groups. This tool is available even when no
other groups are selected. See [Tool packages](Tool-Packages.md) to choose a configuration.
