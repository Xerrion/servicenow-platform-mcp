# Tool reference

Start with the task you want to complete. You can ask your AI app in plain
language. The app chooses the tools and supplies their inputs.

| Your task | Tools |
| --- | --- |
| Find tickets, read a record, or count records | `query`, `record_read` |
| Understand tables, fields, or choice values | `describe`, `resolve_choice` |
| Read comments, request answers, or field changes | `analysis`, `audit` |
| Inspect attachments or configuration items | `attachment`, `cmdb` |
| Understand scripts, flows, or instance issues | `code_search`, `flow`, `investigate` |
| Browse the catalog or place an order | `service_catalog` |
| Create, update, or delete records and attachments | `record_write`, `record_apply`, `attachment_write` |

The configured tool package determines which tools your app can use.
See [[Tool-Packages]] for availability and [[Safety-and-Policy]] for access and write controls.
`list_tool_packages` is always available. It takes no inputs and lists package contents without contacting ServiceNow.
`MAX_ROW_LIMIT` caps the paths that use it. Some tools have separate fixed limits.
See [[Configuration]] for its setting.

## Read the input examples

The JSON examples are arguments for the named MCP tool. Your AI app makes
these calls. They are not terminal commands or a Python client library.
Replace example ticket numbers, names, and every value in angle brackets with
values from your instance.

| Term | Meaning |
| --- | --- |
| Table | A collection of ServiceNow records, such as `incident`. |
| Field | A value on a record, such as `short_description` or `assigned_to`. |
| `sys_id` | A record's internal identifier, containing 32 hexadecimal characters. It differs from a ticket number such as `INC0012345`. |
| Encoded query | A ServiceNow filter string, such as `active=true^priority=1`. Copy one from a ServiceNow list filter. |
| JSON string | Text containing JSON. Inputs such as `data`, `params`, and `variables` need this text, not a nested JSON object. |

Omit optional inputs you do not need. Top-level JSON `null` uses the same
behavior as omission. Explicit `false`, `0`, and empty strings retain each
tool's documented behavior. A `null` inside `record_write.data` remains a field value.

Common ITSM tables:

| Record | Table |
| --- | --- |
| Incident | `incident` |
| Problem | `problem` |
| Change request | `change_request` |
| Catalog request or requested item | `sc_request` or `sc_req_item` |
| Catalog task | `sc_task` |

Common administration and development tables:

| Configuration | Table |
| --- | --- |
| Business Rule | `sys_script` |
| Script Include | `sys_script_include` |
| Client Script | `sys_script_client` |
| UI Policy | `sys_ui_policy` |
| Access control rule (ACL) | `sys_security_acl` |

## Find and read records

> Show 10 active incidents. Include number, short description, priority, and
> assigned person. Order them by priority. Do not change anything.

### `query`

Use `query` to list records, read one record by `sys_id`, or calculate totals.

| Input | Use |
| --- | --- |
| `table` | Required table name. |
| `encoded_query` | Optional encoded filter. Omitted, null, and empty filters mean no filter. |
| `fields` | Comma-separated field names. Required for lists. `*` requests all masked fields. `sys_id` is always included. |
| `sys_id` | Read one exact record. Without `fields`, returns `sys_id,sys_updated_on`. |
| `limit` | Rows per list page. Default 20. Range 1 to `MAX_ROW_LIMIT`. |
| `offset` | Starting row for a list page. Default 0. |
| `order_by` | Field to sort by. Prefix `-` for descending order, such as `-sys_created_on`. |
| `display_values` | Default `false`. Set `true` to request ServiceNow display values for reference and choice fields. |
| `aggregate` | Comma-separated `count`, `avg:<field>`, `sum:<field>`, `min:<field>`, or `max:<field>`. Returns totals instead of records. |
| `group_by` | Comma-separated grouping fields. Requires `aggregate`. |
| `resolve_labels` | Comma-separated `field=choice_key` pairs. Resolves supported choices and adds them to the filter. See `resolve_choice`. |

Arguments for `query`:

```json
{
  "table": "incident",
  "encoded_query": "active=true",
  "fields": "number,short_description,priority,assigned_to",
  "order_by": "priority",
  "display_values": true,
  "limit": 10
}
```

For a ticket number, query its `number` field first:

```json
{
  "table": "incident",
  "encoded_query": "number=INC0012345",
  "fields": "number,short_description,state,sys_updated_on",
  "limit": 1
}
```

> Count active incidents by assignment group. Include the filter used.

Arguments for `query`:

```json
{
  "table": "incident",
  "encoded_query": "active=true",
  "aggregate": "count",
  "group_by": "assignment_group"
}
```

You cannot combine `sys_id` with `aggregate` or `group_by`. Exact-record mode
uses only the record selector, `fields`, and `display_values`. Aggregate mode
does not use list pagination inputs. A row limit bounds returned rows, not the
database work needed to filter or count them.

Lists and aggregates on configured large tables require a recognized date
filter. See [[Safety-and-Policy]]. Exact-record reads do not require that filter.
`display_values` passes ServiceNow's display-value option through and preserves
the returned field structure. The server does not add a `_display` object.

### `record_read`

> Read the Business Rule named 'Validate priority'. Explain its script and when it runs.

| Input | Use |
| --- | --- |
| `table` | Required table name. |
| `sys_id` | Exact record identifier. Supply this or `name`, never both. |
| `name` | Matches the record's actual `name` field. Missing or ambiguous matches return an error. |
| `fields` | Optional comma-separated field names. Default returns compact identity/update fields and discovered script fields. `*` returns the full masked record. |

Arguments for `record_read`:

```json
{
  "table": "sys_script",
  "name": "Validate priority"
}
```

The response includes `script_fields` discovered from dictionary metadata,
including inherited fields. Tables without script fields return an empty list.
The record always includes `sys_id`.
To read a ticket, use `query` with `encoded_query="number=INC0012345"` to find its `sys_id` first.
`record_read.name` does not search ticket numbers.

## Understand tables and choice values

> Show the incident state and assigned-to fields. Explain their types and available help text.

### `describe`

| Action | Required input | Result |
| --- | --- | --- |
| Omit `action` | `table` | Field definitions, including inherited fields. |
| `list_script_fields` | `table` | Script fields and the resolved parent-table chain. |
| `list_tables` | None | Tables matching optional `name_filter`. `table` is ignored. |

| Input | Use |
| --- | --- |
| `fields` | Optional comma-separated field names. Omit for an alphabetical page. `*` requests all fields. |
| `field_offset` | Starting field for default pages. Default 0. |
| `field_limit` | Fields per default page. Default 25. Range 1 to 100. |
| `verbose` | Default `false`. Set `true` for dictionary rows with specific noisy metadata keys removed. |
| `include_docs` | Default `false`. Set `true` for matching field labels, help, hints, and documentation URLs. |
| `name_filter` | Substring matched against table name or label for `list_tables`. |

Arguments for `describe`:

```json
{
  "table": "incident",
  "fields": "state,assigned_to",
  "include_docs": true
}
```

To find a table, use `describe` with:

```json
{
  "action": "list_tables",
  "name_filter": "request"
}
```

Default field pages follow the bounded parent-table chain. Child definitions
override parent definitions. Each field includes `inherited_from`, which is
`null` for fields defined directly on the table. Pagination follows this merge.
Explicit field lists and `fields="*"` do not use default field pagination.

`list_tables` returns at most 500 tables. If the result reaches that limit,
narrow `name_filter`. The tool has no table-list continuation input.
`list_script_fields` returns `name`, `internal_type`, `inherited_from`, and
`via_heuristic` for each discovered script field.

### `resolve_choice`

> List the supported incident state choices. Use the returned key for 'In Progress' before filtering incidents.

| Input | Use |
| --- | --- |
| `table` | Required table name. |
| `field` | Required choice field. |
| `label` | Optional exact key from the returned choices. Omit to list the mapping. |

Arguments for `resolve_choice`:

```json
{
  "table": "incident",
  "field": "state"
}
```

Choice keys use lowercase words with underscores, such as `in_progress`.
Use the exact returned key when supplying `label` or `query.resolve_labels`.
Labels such as `New` and `In Progress` can remain unresolved. An unresolved
value returns unchanged with a warning, so inspect the warning before using it.

The registry supports `state` for `incident`, `change_request`, `problem`,
`sc_request`, and `sc_req_item`. It also supports `operational_status` for
`cmdb_ci`. It reads instance choices and can fall back to built-in defaults
when instance choices cannot be fetched. It is not a general resolver for
every choice field on every table.

## Read ticket history and request answers

> Summarize comments and work notes on INC0012345 from the last 30 days.
> Separately, show changes to its state and assigned person.

Find the ticket's `sys_id` with `query` first. Journal entries and field changes
come from different tools.

### `analysis`

| Action | Required inputs | Optional inputs |
| --- | --- | --- |
| `ritm_variables` | `sys_id` of an `sc_req_item` record | `limit`, `offset` |
| `journal_history` | `table`, `sys_id` | `fields_csv`, `since`, `window_days`, `limit`, `offset` |
| `describe` | None | None. Returns action contracts without contacting ServiceNow. |

`limit` defaults to `MAX_ROW_LIMIT` and is capped by it. `offset` defaults to 0.
For journal history, `fields_csv` defaults to `comments,work_notes`.
It accepts only `comments`, `work_notes`, and `close_notes` that dictionary
metadata confirms are journal fields. `window_days` defaults to 90.
`since` must use `YYYY-MM-DD` and overrides `window_days`.

Arguments for `analysis`, after replacing the identifier:

```json
{
  "action": "journal_history",
  "table": "incident",
  "sys_id": "<32-character incident sys_id>",
  "window_days": 30,
  "limit": 50
}
```

Journal entries use chronological order and `limit`/`offset` pagination.
Access rules and journal retention can make history incomplete.

> Show the answers submitted with RITM0012345.

For `analysis.ritm_variables`, find the requested item's `sys_id` in
`sc_req_item`. The result joins submitted answers to variable definitions.
Answers use `raw_value`. References retain their raw identifiers, and List
Collectors can contain multiple identifiers marked `multi_value`.

Sensitive variable names or labels cause masking. Missing name or label
metadata also causes masking. Multi-row variable sets (MRVS) appear only as
presence metadata in `data.unsupported_features.multi_row_variable_sets`.
The tool does not retrieve or decode their answer payloads.

Reading these results requires access to the target record, dictionary tables,
and the supporting journal or catalog-answer tables.

### `audit`

> Check whether incident state changes are audited. Explain any inconclusive result.

| Action | Required inputs | Optional inputs and result |
| --- | --- | --- |
| `check_field` | `table`, `field` | `window_days`. Checks field/table settings and recent audit activity. |
| `check_fields` | `table`, `fields_csv` | `window_days`. Checks 1 to 50 fields with shared activity counts. |
| `check_table` | `table` | Returns table settings and fields with different audit settings. |
| `history` | `table`, `sys_id` | `since`, `window_days`, `limit`. Returns masked field-change entries. |
| `describe` | None | Returns action contracts without contacting ServiceNow. |

Arguments for `audit`:

```json
{
  "action": "check_fields",
  "table": "incident",
  "fields_csv": "state,priority,assigned_to",
  "window_days": 30
}
```

Activity checks and history use a 90-day default window. `since` accepts
`YYYY-MM-DD` for `history` and overrides `window_days`. Wider windows can
time out on large audit tables. Batch activity counts are live and are not cached.

History returns the newest entries within the window, bounded by `limit`.
The default limit is `MAX_ROW_LIMIT`, which also caps the result.
This action has no `offset` input. A result is a bounded history, not a
guarantee that every change appears.

The audit check follows inherited dictionary settings and table settings.
A field's `no_audit=true` attribute overrides its audit flag.
`audited` confirms activity within the window. `audited_but_inactive` means
the field is configured for auditing but has no activity in that window.
`inconclusive` means metadata is missing or table activity is insufficient to
confirm the result. It does not establish that auditing is disabled.

## Inspect attachments

> List the attachments on INC0012345. Show each file name and size before downloading one.

Find the parent record's `sys_id` with `query` first.

### `attachment`

| Action | Required inputs | Result |
| --- | --- | --- |
| `list` | `table`, `table_sys_id` | Attachment metadata for the parent record. |
| `get` | Attachment `sys_id` | Metadata for one attachment. |
| `download` | Attachment `sys_id` | File content encoded as base64. |
| `download_by_name` | `table`, `table_sys_id`, `file_name` | Downloads the earliest matching attachment as base64. |

Arguments for `attachment`, after replacing the identifier:

```json
{
  "action": "list",
  "table": "incident",
  "table_sys_id": "<32-character incident sys_id>"
}
```

`list` returns at most 100 attachments. It has no continuation input.
Its reported pagination total is the number returned, not a global count.
Downloads are limited to 10 MiB. Uploads and deletions use `attachment_write`.

## Inspect configuration items

> Find 10 server configuration items. Show their names, then inspect the relationships for the server I select.

### `cmdb`

| Action | Required inputs | Optional inputs and result |
| --- | --- | --- |
| `query` | `class_name` | `encoded_query`, `limit`, `offset`. Lists configuration item names and identifiers. |
| `get` | `class_name`, `sys_id` | Returns attributes and inbound/outbound relationships. |
| `meta` | `class_name` | Returns class metadata. The ServiceNow Meta API requires the `itil` role. |
| `describe` | None | Returns action contracts without contacting ServiceNow. |

Arguments for `cmdb`:

```json
{
  "action": "query",
  "class_name": "cmdb_ci_server",
  "limit": 10
}
```

`limit` defaults to 20 and is capped by `MAX_ROW_LIMIT`. `offset` defaults to 0.
Configured large classes require a date filter. `data.count` is the returned
page size. The response has no global total. Continue by advancing `offset`
by the effective limit. Access rules can produce short pages.

These actions use the CMDB Instance and Meta APIs. They are read-only and mask
sensitive nested fields. You can also use `query` and `describe` for ordinary
CMDB table reads and dictionary discovery.

## Understand scripts and flows

> Find scripts that call `gs.eventQueue`. Show the matching records so I can review them.

### `code_search`

| Action | Required input | Optional inputs |
| --- | --- | --- |
| `search` (default) | `term` | `table`, `search_group`, `limit`, `extended_matching` |
| `list_tables` | None | `search_group` |
| `describe` | None | None. Returns action contracts without contacting ServiceNow. |

Arguments for `code_search`:

```json
{
  "term": "gs.eventQueue",
  "table": "sys_script",
  "limit": 20
}
```

The default search group is `sn_codesearch.Default Search Group`.
`limit` defaults to 20 and is capped by `MAX_ROW_LIMIT`.
`extended_matching` defaults to `false`. Set it to `true` for additional
context fields. This tool uses ServiceNow's Code Search API. It has no `offset`
input. Use `list_tables` to check which tables the selected group covers.
Use `record_read` to read a matching record's complete script fields.

### `flow`

> Inspect the 'New starter' flow. Explain its trigger, inputs, configured stages, and configured steps. Show any incomplete sections.

Flow tools inspect stored Flow Designer configuration. They do not run,
edit, publish, or test flows. A configured step is not evidence of an executed step.

| Action | Required inputs | Optional inputs and result |
| --- | --- | --- |
| `contract` | Exactly one of `sys_id` or `name` | `sections`, `section_limit`. Concise declared fields and configured V2 steps. |
| `inspect` | Exactly one of `sys_id` or `name` | `sections`, `section_limit`. Detailed flow metadata and configuration. |
| `find_by_table` | `table` | Finds V1/V2 record-triggered flows for that table. |
| `list_triggers` | None | `table`, `trigger_type`, `active`, `limit`. Lists V1/V2 record triggers. |
| `decode_values` | `value` | Decodes a complete gzip, base64, and JSON `values` blob. |
| `describe` | None | Returns action contracts without contacting ServiceNow. |

`name` matches a flow's name or `internal_name` and must identify one flow.
For `list_triggers`, `active` is the string `"true"` or `"false"`, not a JSON
boolean. `trigger_type` can be a value such as `record_update`.
`limit` defaults to 100 when omitted or set to 0.

Arguments for `flow`, after replacing the example name:

```json
{
  "action": "contract",
  "name": "New starter",
  "sections": "flow,published_state,inputs,outputs,stages,triggers,steps,warnings",
  "section_limit": 50
}
```

Both `inspect` and `contract` default to
`flow,published_state,structural_summary,warnings`.
`sections="*"` requests every section for that action.
`section_limit` defaults to 100 and is capped by `MAX_ROW_LIMIT`.

| Sections | Available in |
| --- | --- |
| `flow`, `published_state`, `structural_summary`, `inputs`, `outputs`, `variables`, `stages`, `triggers`, `warnings` | `inspect` and `contract` |
| `canvas`, `v1_actions`, `v1_variable_values` | `inspect` |
| `steps` | `contract` |

Interpret the selected sections as follows:

- `published_state` compares `master_snapshot` with `latest_snapshot`. `drift=true` means the published and latest authored snapshots differ.
- `stages` contains stored lifecycle configuration and its source records. It does not show stage execution history.
- `canvas` contains the nested V2 action/logic tree with decoded values. Children follow stored order.
- `steps` contains configured V2 action inputs, logic conditions, and available output assignments. Action definitions list declared inputs and outputs.
- `warnings` appears inside `data.warnings` when that section is selected. Check it for V1 limitations, snapshot differences, and inaccessible definitions.

Action definitions include `name`, `label`, and `required`. Where available,
they also include `type`, input `default`, and `reference_table`.
Missing or inaccessible definitions appear in contract warnings and the
affected action's `definition.limitations`.

Bindings preserve configured values. `data_pills` lists stored `{{...}}`
references without resolving runtime values or inferring action behavior.
V1 actions and logic cannot be reconstructed into the ordered V2 contract.
A bad node blob adds `decode_error` to that node while other inspection can succeed.

`find_by_table` resolves snapshot references to canonical flow identifiers.
Unresolved headers appear in `unresolved_flow_ids` with `metadata_resolved=false`
and `active=null`. This does not establish that those flows are inactive.

Successful inspections include top-level `truncation` only when results or
dependencies are incomplete. Follow its continuation instructions. Below the
configured maximum, this can mean increasing `section_limit`. At the maximum,
use the supplied table, field, and filter details with paginated `query` calls.
`flow` itself has no offset-based continuation for these sections.

The tool reads documented Table API records, including V1/V2 flow artifacts
and record-trigger conditions. It does not use the undocumented process-flow
endpoint or the opaque compiled snapshot cache.

## Investigate instance issues

> Investigate errors from the last 24 hours. Show the evidence for each finding and explain what needs checking next.

### `investigate`

| Action | Required inputs | Optional inputs and result |
| --- | --- | --- |
| `describe` | None | `name` selects one investigation's parameter contract. Omit it to list investigations. |
| `run` | `name` | `params`, a JSON object string. Default `"{}"`. Runs the named investigation. |
| `explain` | `element_id` | `name` directly selects an investigation. Explains a finding from a previous run. |

| Investigation | Parameters inside `params` |
| --- | --- |
| `error_analysis` | `hours` (default 24), optional `source` substring, `limit` (default 100 log entries). |
| `slow_transactions` | `hours` (default 24), `limit` (default 20 per table), optional comma-separated `categories`. |
| `table_health` | Required `table`. Optional `hours`. Omitted `hours` includes all history. |
| `acl_conflicts` | Required `table`. |
| `performance_bottlenecks` | Optional `hours`, omitted for all history. `limit` defaults to 20 per category. |
| `stale_automations` | `stale_days` (default 30), `limit` (default 20 per category). |
| `deprecated_apis` | `limit` (default 20 per pattern). |

Arguments for `investigate`:

```json
{
  "action": "run",
  "name": "error_analysis",
  "params": "{\"hours\":24,\"limit\":50}"
}
```

Use `describe` before a new investigation to check its available parameters.
Results contain bounded candidate findings. Findings do not establish a root
cause or measure the performance impact of a proposed fix.

For `explain`, `element_id` usually takes the form `table:sys_id`.
With a named investigation, `table_health` and heavy-automation findings from
`performance_bottlenecks` accept a table name. `acl_conflicts` also accepts an
ACL `sys_id`. Without `name`, the server tries registered investigations until
one accepts the identifier.

The run identifies its module in `data.investigation`. Non-empty run warnings
appear at the top level. `slow_transactions` reports attempted tables only.
Missing or inaccessible optional tables produce warnings and `complete=false`.
Timeouts and unexpected failures return errors.

## Browse and order from the catalog

> Find catalog items matching 'laptop'. Show the selected item's variables before preparing an order.

### `service_catalog`

This tool requires `full` or a custom package containing `service_catalog`.
It is absent from `readonly`. The group combines browsing and ordering.
Order and cart mutations apply directly, without record previews.

| Action | Required inputs | Optional inputs or result |
| --- | --- | --- |
| `catalogs_list` | None | `text`, `limit`. Lists catalogs. |
| `catalog_get` | Catalog `sys_id` | Reads one catalog. |
| `categories_list` | `catalog_sys_id` | `limit`, `offset`, `top_level_only`. Lists categories in that catalog. |
| `category_get` | Category `sys_id` | Reads one category. |
| `items_list` | None | `text`, `catalog`, `category`, `limit`, `offset`. Lists items. |
| `item_get` | Item `sys_id` | Reads one item. |
| `item_variables` | Item `sys_id` | Reads the item's variable definitions. |
| `cart_get` | None | Reads the current cart. |
| `order_now` | `item_sys_id` | Optional `variables` JSON object string. Places an order directly. |
| `add_to_cart` | `item_sys_id` | Optional `variables` JSON object string. Changes the cart directly. |
| `cart_submit` | None | Submits the cart directly. |
| `cart_checkout` | None | Checks out the cart directly. |

For list actions, `limit` defaults to 20. Category and item lists use `offset`,
which defaults to 0. `top_level_only` defaults to `false`.
`catalog` and `category` are identifier filters for item lists.
Omitted, null, and empty `text`, `catalog`, and `category` filters are not sent
to ServiceNow.

Arguments for `service_catalog`:

```json
{
  "action": "items_list",
  "text": "laptop",
  "limit": 10
}
```

Before ordering, read `item_variables` and use the returned variable names.
The following is an argument template for a direct order. Replace the item
identifier and example variable with actual values. Instance rules determine
which answers the item requires.

```json
{
  "action": "order_now",
  "item_sys_id": "<32-character catalog item sys_id>",
  "variables": "{\"business_justification\":\"Replacement for a damaged laptop\"}"
}
```

## Change records and attachments

Start on a development or test instance. Write tools, local write settings,
and ServiceNow permissions must allow the requested change.
See [[Safety-and-Policy]] before enabling writes.
Record writes default to previews, but callers can request immediate writes.
The server does not require human approval. State your review requirement in
the request before the app prepares a change.

> Preview adding this work note to INC0012345: 'Waiting for the caller to
> confirm the fix.' Show the proposed change and wait for my approval.

### `record_write`

| Action | Required inputs | Result with the default preview |
| --- | --- | --- |
| `create` | `table`, `data`. Omit `sys_id`. | Stages a new record. |
| `update` | `table`, `sys_id`, `data` | Stages changes to supplied fields. Omitted fields stay unchanged. |
| `delete` | `table`, `sys_id` | Stages deletion. |

`data` is a JSON object string mapping field names to values.
It can contain multiple script or markup fields. The maximum is 256 KiB of
UTF-8 JSON, including field names and escaping. Supply complete script values.
The tool does not read local script files or check JavaScript syntax.

`preview` defaults to `true`. Omit it to receive `data.preview_token`.
Setting `preview=false` requests an immediate write.

Arguments for `record_write`, after finding and replacing the incident identifier:

```json
{
  "action": "update",
  "table": "incident",
  "sys_id": "<32-character incident sys_id>",
  "data": "{\"work_notes\":\"Waiting for the caller to confirm the fix.\"}"
}
```

The tool checks supplied XML fields against inherited dictionary metadata.
These fields require non-empty strings containing well-formed XML.
It checks mandatory fields for creates before staging or writing, and again
when applying. Child definitions take precedence. Metadata request errors
block writes. Fields hidden by dictionary access rules cannot receive local
validation. These checks do not replace testing on the instance.

### `record_apply`

After reviewing a record preview, supply its `preview_token` to `record_apply`:

```json
{
  "preview_token": "<token returned by record_write>"
}
```

Previews remain in server memory for five minutes. Restarting the server
removes them. Each token is consumed once, even if applying the change fails.
Request a new preview after expiration, restart, or a failed apply.

A preview does not reserve the record or check for intervening changes when
applied. Read the current record before reviewing a change. After applying it,
check the saved result in ServiceNow and test changed behavior.

### `attachment_write`

| Action | Required inputs | Optional inputs |
| --- | --- | --- |
| `upload` | `table`, `table_sys_id`, `file_name`, `content_base64` | `content_type`, default `application/octet-stream`. |
| `delete` | Attachment `sys_id` | None. |

Uploads are limited to 10 MiB of decoded file content.
`content_base64` must contain complete base64-encoded file bytes.
`content_type` is the file's MIME type, such as `application/pdf`.
Both actions apply directly. They do not use `record_write` previews or
`record_apply`. Identify the attachment or parent record before requesting a change.

## Interpret results and incomplete reads

Operational tools return JSON strings with `status` and `data`.
Errors include `error`. Responses can also contain `pagination`, non-empty
`warnings`, and non-empty `truncation`. They omit `selection` metadata.
`list_tool_packages` returns the package registry directly.

Check the specific tool's continuation contract:

| Read | Continuation and count meaning |
| --- | --- |
| `query` lists and `analysis` | Use `limit`/`offset`. Pagination includes `offset`, `limit`, and `total`. |
| Default `describe` field pages | Use `field_limit`/`field_offset`. Response pagination uses `limit`, `offset`, and `total`. |
| `cmdb.query` | Use `limit`/`offset`. Returned count is the page size. No global total. |
| `attachment.list`, `code_search`, `audit.history` | Bounded results without an offset continuation input. Attachment total means returned count. |
| `flow.inspect` and `flow.contract` | Follow top-level `truncation` instructions. Selected flow warnings appear in `data.warnings`. |

Access rules, limits, retention, and incomplete metadata can affect results.
An empty or short result alone does not prove that matching records do not exist.
Ask the app to report filters, warnings, and incomplete sections when the
answer informs an operational or development decision.

For more complete workflows, see
[Agent recipes](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/docs/agent-recipes.md).
