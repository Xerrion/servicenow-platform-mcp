# Tool reference

Start with the task you want to complete. You can ask your AI app in plain
language. The app chooses the tools and supplies their inputs.

| Your task                                         | Tools                                                    |
| ------------------------------------------------- | -------------------------------------------------------- |
| Find tickets, read a record, or count records     | `query`, `record_read`                                   |
| Understand tables, fields, or choice values       | `describe`, `resolve_choice`                             |
| Read comments, request answers, or field changes  | `analysis`, `audit`                                      |
| Inspect attachments or configuration items        | `attachment`, `cmdb`                                     |
| Understand scripts, flows, or instance issues     | `code_search`, `flow`, `investigate`                     |
| Browse the catalog or place an order              | `service_catalog`                                        |
| Create, update, or delete records and attachments | `record_preview`, `mutation_apply`, `attachment_preview` |

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

| Term          | Meaning                                                                                                                     |
| ------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Table         | A collection of ServiceNow records, such as `incident`.                                                                     |
| Field         | A value on a record, such as `short_description` or `assigned_to`.                                                          |
| `sys_id`      | A record's internal identifier, containing 32 hexadecimal characters. It differs from a ticket number such as `INC0012345`. |
| Encoded query | A ServiceNow filter string, such as `active=true^priority=1`. Copy one from a ServiceNow list filter.                       |
| Native object | A JSON object supplied directly to `data`, `params`, or `variables`.                                                        |

Omit optional inputs you do not need. Top-level JSON `null` uses the same
behavior as omission. Explicit `false`, `0`, and empty strings retain each
tool's documented behavior. A `null` inside `record_preview.data` remains a field value.

Common ITSM tables:

| Record                            | Table                         |
| --------------------------------- | ----------------------------- |
| Incident                          | `incident`                    |
| Problem                           | `problem`                     |
| Change request                    | `change_request`              |
| Catalog request or requested item | `sc_request` or `sc_req_item` |
| Catalog task                      | `sc_task`                     |

Common administration and development tables:

| Configuration             | Table                |
| ------------------------- | -------------------- |
| Business Rule             | `sys_script`         |
| Script Include            | `sys_script_include` |
| Client Script             | `sys_script_client`  |
| UI Policy                 | `sys_ui_policy`      |
| Access control rule (ACL) | `sys_security_acl`   |

## Find and read records

> Show 10 active incidents. Include number, short description, priority, and
> assigned person. Order them by priority. Do not change anything.

### `query`

Use `query` to list records, read one record by `sys_id`, or calculate totals.

| Input            | Use                                                                                                                           |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `table`          | Required table name.                                                                                                          |
| `encoded_query`  | Optional encoded filter. Omitted, null, and empty filters mean no filter.                                                     |
| `fields`         | Native array of field names. Required for lists. `*` requests all masked fields. `sys_id` is always included.                 |
| `sys_id`         | Read one exact record. Without `fields`, returns `sys_id,sys_updated_on`.                                                     |
| `limit`          | Rows per list page. Default 20. Range 1 to `MAX_ROW_LIMIT`.                                                                   |
| `offset`         | Starting row for a list page. Default 0.                                                                                      |
| `order_by`       | Field to sort by. Prefix `-` for descending order, such as `-sys_created_on`.                                                 |
| `display_values` | Default `false`. Set `true` to request ServiceNow display values for reference and choice fields.                             |
| `aggregate`      | Native array of `{operation:"count"}` or `{operation:"avg",field:"reopen_count"}` objects. Returns totals instead of records. |
| `group_by`       | Native array of grouping fields. Requires `aggregate`.                                                                        |
| `resolve_labels` | Native array of `{field, label}` objects. Resolves supported choices and adds them to the filter. See `resolve_choice`.       |

Arguments for `query`:

```json
{
  "table": "incident",
  "encoded_query": "active=true",
  "fields": ["number", "short_description", "priority", "assigned_to"],
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
  "fields": ["number", "short_description", "state", "sys_updated_on"],
  "limit": 1
}
```

> Count active incidents by assignment group. Include the filter used.

Arguments for `query`:

```json
{
  "table": "incident",
  "encoded_query": "active=true",
  "aggregate": [
    {
      "operation": "count"
    }
  ],
  "group_by": ["assignment_group"]
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

Query-field discovery checks complete field paths from supported encoded-query clauses.
It includes inactive dictionary definitions because ServiceNow still permits queries on those fields.
Static reference paths resolve each segment against the referenced table.
The lookup batches selected columns and bounds inheritance, pagination, and reference depth.

Missing or inaccessible definitions, unsupported reference types, and bounded metadata reads produce an unverified warning.
Missing dictionary visibility does not prove that a field is invalid.
Field discovery does not establish ACL permission or validate every encoded-query expression.
The query continues with its original filter. Check warnings before relying on its results.

### `record_read`

> Read the Business Rule named 'Validate priority'. Explain its script and when it runs.

| Input    | Use                                                                                                                                                    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `table`  | Required table name.                                                                                                                                   |
| `sys_id` | Exact record identifier. Supply this or `name`, never both.                                                                                            |
| `name`   | Matches the record's actual `name` field. Missing or ambiguous matches return an error.                                                                |
| `fields` | Optional native array of field names. Default returns compact identity/update fields and discovered script fields. `*` returns the full masked record. |

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

| Action               | Required input | Result                                                      |
| -------------------- | -------------- | ----------------------------------------------------------- |
| Omit `action`        | `table`        | Field definitions, including inherited fields.              |
| `list_script_fields` | `table`        | Script fields and the resolved parent-table chain.          |
| `list_tables`        | None           | Tables matching optional `name_filter`. `table` is ignored. |

| Input          | Use                                                                                           |
| -------------- | --------------------------------------------------------------------------------------------- |
| `fields`       | Optional native array of field names. Omit for an alphabetical page. `*` requests all fields. |
| `field_offset` | Starting field for default pages. Default 0.                                                  |
| `field_limit`  | Fields per default page. Default 25. Range 1 to 100.                                          |
| `verbose`      | Default `false`. Set `true` for dictionary rows with specific noisy metadata keys removed.    |
| `include_docs` | Default `false`. Set `true` for matching field labels, help, hints, and documentation URLs.   |
| `name_filter`  | Substring matched against table name or label for `list_tables`.                              |

Arguments for `describe`:

```json
{
  "table": "incident",
  "fields": ["state", "assigned_to"],
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

| Input   | Use                                                                     |
| ------- | ----------------------------------------------------------------------- |
| `table` | Required table name.                                                    |
| `field` | Required choice field.                                                  |
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

| Action            | Required inputs                     | Optional inputs                                               |
| ----------------- | ----------------------------------- | ------------------------------------------------------------- |
| `ritm_variables`  | `sys_id` of an `sc_req_item` record | `limit`, `offset`                                             |
| `journal_history` | `table`, `sys_id`                   | `fields`, `since`, `window_days`, `limit`, `offset`           |
| `describe`        | None                                | None. Returns action contracts without contacting ServiceNow. |

`limit` defaults to `MAX_ROW_LIMIT` and is capped by it. `offset` defaults to 0.
For journal history, `fields` defaults to `["comments","work_notes"]`.
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

| Action         | Required inputs   | Optional inputs and result                                            |
| -------------- | ----------------- | --------------------------------------------------------------------- |
| `check_field`  | `table`, `field`  | `window_days`. Checks field/table settings and recent audit activity. |
| `check_fields` | `table`, `fields` | `window_days`. Checks 1 to 50 fields with shared activity counts.     |
| `check_table`  | `table`           | Returns table settings and fields with different audit settings.      |
| `history`      | `table`, `sys_id` | `since`, `window_days`, `limit`. Returns masked field-change entries. |
| `describe`     | None              | Returns action contracts without contacting ServiceNow.               |

Arguments for `audit`:

```json
{
  "action": "check_fields",
  "table": "incident",
  "fields": ["state", "priority", "assigned_to"],
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

| Action             | Required inputs                      | Result                                                |
| ------------------ | ------------------------------------ | ----------------------------------------------------- |
| `list`             | `table`, `table_sys_id`              | Attachment metadata for the parent record.            |
| `get`              | Attachment `sys_id`                  | Metadata for one attachment.                          |
| `download`         | Attachment `sys_id`                  | File content encoded as base64.                       |
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
Downloads are limited to 10 MiB. Uploads and deletions use `attachment_preview`.

## Inspect configuration items

> Find 10 server configuration items. Show their names, then inspect the relationships for the server I select.

### `cmdb`

| Action     | Required inputs        | Optional inputs and result                                                          |
| ---------- | ---------------------- | ----------------------------------------------------------------------------------- |
| `query`    | `class_name`           | `encoded_query`, `limit`, `offset`. Lists configuration item names and identifiers. |
| `get`      | `class_name`, `sys_id` | Returns attributes and inbound/outbound relationships.                              |
| `meta`     | `class_name`           | Returns class metadata. The ServiceNow Meta API requires the `itil` role.           |
| `describe` | None                   | Returns action contracts without contacting ServiceNow.                             |

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

| Action             | Required input | Optional inputs                                               |
| ------------------ | -------------- | ------------------------------------------------------------- |
| `search` (default) | `term`         | `table`, `search_group`, `limit`, `extended_matching`         |
| `list_tables`      | None           | `search_group`                                                |
| `describe`         | None           | None. Returns action contracts without contacting ServiceNow. |

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

| Action          | Required inputs                   | Optional inputs and result                                                    |
| --------------- | --------------------------------- | ----------------------------------------------------------------------------- |
| `contract`      | Exactly one of `sys_id` or `name` | `sections`, `section_limit`. Concise declared fields and configured V2 steps. |
| `inspect`       | Exactly one of `sys_id` or `name` | `sections`, `section_limit`. Detailed flow metadata and configuration.        |
| `find_by_table` | `table`                           | Finds V1/V2 record-triggered flows for that table.                            |
| `list_triggers` | None                              | `table`, `trigger_type`, `active`, `limit`. Lists V1/V2 record triggers.      |
| `decode_values` | `value`                           | Decodes a complete gzip, base64, and JSON `values` blob.                      |
| `describe`      | None                              | Returns action contracts without contacting ServiceNow.                       |

`name` matches a flow's name or `internal_name` and must identify one flow.
For `list_triggers`, `active` is the string `"true"` or `"false"`, not a JSON
boolean. `trigger_type` can be a value such as `record_update`.
`limit` defaults to 100 when omitted or set to 0.

Arguments for `flow`, after replacing the example name:

```json
{
  "action": "contract",
  "name": "New starter",
  "sections": [
    "flow",
    "published_state",
    "inputs",
    "outputs",
    "stages",
    "triggers",
    "steps",
    "warnings"
  ],
  "section_limit": 50
}
```

Both `inspect` and `contract` default to
`flow,published_state,structural_summary,warnings`.
`sections="*"` requests every section for that action.
`section_limit` defaults to 100 and is capped by `MAX_ROW_LIMIT`.

`validate` requires `sys_id` and performs local semantic/schema/reference checks on a saved draft.
It never sends a compile or execution request. Check `valid`, `complete`, and safe `diagnostics` together.
`INCOMPLETE_SEMANTICS` reports the first failed parser invariant with a schema path and list index, without stored values.
Local validation requires explicit inactive V2 draft fields and complete step identities, bindings, orders, and parent links.
A missing trigger activation field does not reject local validation. The flow's `active` field is a separate prerequisite.
Unknown or unsupported Studio representations remain `valid=null`, `complete=false`. The validator does not repair them.
`status` requires `sys_id`; optional `execution_sys_id` reads one exact existing execution context.
Draft/published status, active state, version references, and exact execution context are separate results.
Status does not read update sets, Customer Updates, or session selection.
Unknown state never authorizes replay. Explicit lifecycle effects use `flow_preview` and `mutation_apply`, not this read-only tool.
Subflow publish, flow activate/deactivate, and saved-draft test have source-derived offline adapters.
Independent remote validation and production run remain unavailable. Test is not a run alias.

| Sections                                                                                                            | Available in             |
| ------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| `flow`, `published_state`, `structural_summary`, `inputs`, `outputs`, `variables`, `stages`, `triggers`, `warnings` | `inspect` and `contract` |
| `canvas`, `v1_actions`, `v1_variable_values`                                                                        | `inspect`                |
| `steps`                                                                                                             | `contract`               |

Interpret the selected sections as follows:

- `published_state` compares `master_snapshot` with `latest_snapshot`. `drift=true` means the published and latest authored snapshots differ.
- `stages` contains stored lifecycle configuration and its source records. It does not show stage execution history.
- `canvas` contains the nested V2 action/logic/subflow tree with decoded values. Children follow stored order.
- `steps` contains configured V2 action and subflow inputs, logic conditions, and available output assignments. Action definitions list declared inputs and outputs.
- `warnings` appears inside `data.warnings` when that section is selected. Check it for V1 limitations, snapshot differences, and inaccessible definitions.

Action definitions include `name`, `label`, and `required`. Where available,
they also include `type`, input `default`, and `reference_table`.
Missing or inaccessible definitions appear in contract warnings and the
affected action's `definition.limitations`.

V2 subflow calls retain the persisted `subflow` snapshot reference, `version="v2"`, order, parent, and wait setting.
Structural counts include `subflows_v2`. Unreadable dataset counts are null, with details in `truncation`.
Malformed input blobs or parent links also mark the affected canvas or steps section incomplete.

V2 triggers use stored `trigger_type`, `trigger_inputs`, and `trigger_outputs`, with legacy column support.
Service Catalog triggers do not require a record-trigger table, condition, or remote ID.
An unreadable trigger activation value returns `active=null`. The flow header does not establish trigger activation.
Trigger `warnings`, `metadata_complete`, and top-level `truncation` report missing or malformed metadata.
When `triggers` is selected, one shared instance catalog read joins exact V2 definition IDs.
`definition.inputs` and `definition.outputs` describe declared schemas separately from stored configuration and runtime output values.
Definition fields include required status, defaults, choices, and reference tables when available.
An optional empty choice remains empty. The tool does not replace it with a default runtime value.
Required empty values, null values, invalid nonempty choices, and unreadable definitions produce warnings.
Schema fields use `section_limit`. Missing definitions, access failures, dynamic schemas, and caps retain incomplete diagnostics.
Stored output references appear in contract `output_configuration.reference_table`. No output table is inferred from a trigger name.

In `structural_summary`, `complete` covers structural counts only. `completeness_scope` states this limit.
`metadata_complete=false` reports incomplete selected `metadata_sections`. Otherwise it is null because full metadata completeness is not established.

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

Inspection reads Table API records and the observed Workflow Studio trigger catalog.
The trigger catalog contract can vary by release. Unsupported or unreadable catalog shapes remain unknown.
Inspection does not read the opaque compiled snapshot cache or execute flows.

## Investigate instance issues

> Investigate errors from the last 24 hours. Show the evidence for each finding and explain what needs checking next.

### `investigate`

| Action     | Required inputs | Optional inputs and result                                                             |
| ---------- | --------------- | -------------------------------------------------------------------------------------- |
| `describe` | None            | `name` selects one investigation's parameter contract. Omit it to list investigations. |
| `run`      | `name`          | `params`, a native object. Default `{}`. Runs the named investigation.                 |
| `explain`  | `element_id`    | `name` directly selects an investigation. Explains a finding from a previous run.      |

| Investigation             | Parameters inside `params`                                                                |
| ------------------------- | ----------------------------------------------------------------------------------------- |
| `error_analysis`          | `hours` (default 24), optional `source` substring, `limit` (default 100 log entries).     |
| `slow_transactions`       | `hours` (default 24), `limit` (default 20 per table), optional native `categories` array. |
| `table_health`            | Required `table`. Optional `hours`. Omitted `hours` includes all history.                 |
| `acl_conflicts`           | Required `table`.                                                                         |
| `performance_bottlenecks` | Optional `hours`, omitted for all history. `limit` defaults to 20 per category.           |
| `stale_automations`       | `stale_days` (default 30), `limit` (default 20 per category).                             |
| `deprecated_apis`         | `limit` (default 20 per pattern).                                                         |

Arguments for `investigate`:

```json
{
  "action": "run",
  "name": "error_analysis",
  "params": {
    "hours": 24,
    "limit": 50
  }
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

Catalog reads are available in `readonly` and the `service_catalog` group.

| Action                        | Required inputs                          | Optional inputs or result                                                                                                  |
| ----------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `catalogs_list`               | None                                     | `text`, `limit`, `view`.                                                                                                   |
| `catalog_get`                 | Catalog `sys_id`                         | `view`. Reads one catalog.                                                                                                 |
| `categories_list`             | `catalog_sys_id`                         | `limit`, `offset`, `top_level_only`, `view`.                                                                               |
| `category_get`                | Category `sys_id`                        | `view`. Reads one category.                                                                                                |
| `items_list`                  | None                                     | `text`, `catalog`, `category`, `limit`, `offset`, `item_type`, `view`.                                                     |
| `item_get`                    | Item `sys_id`                            | `view`. Reads one item.                                                                                                    |
| `item_variables`              | Item `sys_id`                            | `view`. Reads variable definitions.                                                                                        |
| `cart_get`                    | None                                     | Reads the current cart.                                                                                                    |
| `delivery_address`            | `user_sys_id`                            | Returns a masked address result.                                                                                           |
| `delegation`                  | `item_sys_id`, `user_sys_id`             | Checks acquisition rights.                                                                                                 |
| `wishlist_get`                | None                                     | Reads the current wish list.                                                                                               |
| `wishlist_item_get`           | `cart_item_id`                           | Reads one wish list item.                                                                                                  |
| `get_invalid_delegated_users` | `item_sys_id`, `also_request_for`        | Sends user sys_ids as a comma-separated string. Masks each returned name while preserving the `invalidUsers` array length. |
| `variable_display_value`      | Variable `sys_id`, record `value` sys_id | Returns a masked display value.                                                                                            |

`limit` defaults to 20. `offset` defaults to 0.
Omitted, null, and empty text or identifier filters are not sent upstream.
All actions accept `api_version`, such as `v1`. Omit it for the default endpoint URL.
`view` accepts `desktop`, `mobile`, or `both`. `item_type` maps to `sysparm_type` unchanged.
The two lookup actions use documented read-only POST endpoints. They do not require a mutation preview.
Catalog masking checks sensitive keys, common personal fields, and variable names and labels before returning answer fields.
Variable maps use observed labels for the associated item. Answers without usable label metadata are masked.
Preview summaries and apply responses use masked copies. Apply sends the original submitted values.
It cannot identify personal data embedded in arbitrary free text.

```json
{ "action": "items_list", "text": "laptop", "limit": 10 }
```

### `service_catalog_preview`

The `service_catalog_write` group enables catalog mutation previews.
Read `item_variables` before preparing an order.

| Action                         | Required inputs              | Optional inputs                                                         |
| ------------------------------ | ---------------------------- | ----------------------------------------------------------------------- |
| `order_now`, `add_to_cart`     | `item_sys_id`                | `variables`, `quantity`, `requested_for`, `also_request_for`.           |
| `submit_producer`              | Producer `item_sys_id`       | `variables`, `view`. Executes the producer during apply only.           |
| `add_to_wishlist`              | `item_sys_id`                | `quantity`.                                                             |
| `submit_guide`                 | Guide `item_sys_id`          | `variables`. An empty result list is valid.                             |
| `checkout_guide`               | Guide `item_sys_id`, `items` | Each item accepts `sys_id`, `sysparm_quantity`, and native `variables`. |
| `cart_item_update`             | `cart_item_id`, `quantity`   | `variables`.                                                            |
| `cart_item_delete`             | `cart_item_id`               | Deletes one cart item.                                                  |
| `cart_empty`                   | `cart_sys_id`                | Can empty and delete the cart, subject to ServiceNow roles.             |
| `cart_submit`, `cart_checkout` | None                         | No item or variables. Uses the current cart.                            |

```json
{
  "action": "order_now",
  "item_sys_id": "<32-character catalog item sys_id>",
  "variables": { "business_justification": "Replace a damaged laptop" }
}
```

Preview reads item definitions or the cart without changing ServiceNow.
It cannot simulate fulfillment, producer scripts, routing, prices, or every server-side validation.
Apply the reviewed token with `mutation_apply`.
Detectable item or cart changes require a new preview.
All actions accept `api_version`. `quantity` is a non-negative integer.
`also_request_for` accepts 1-50 user sys_ids. Guide quantities are positive integer strings.
The existing empty `order_now` and `add_to_cart` payloads remain unchanged when no inputs are supplied.
Supply `quantity` explicitly when the instance requires it.

For record producers, use `submit_producer`. `order_now` orders catalog items.
Preview also reads the producer's target table, update timestamp, scripts, and template.
Those reads require Table API access to `sc_cat_item_producer`. Producer scripts stay out of the preview summary.
The target table must pass local policy at preview and apply.
Missing target metadata blocks submission. No direct record creation occurs.
Guide checkout requires both request-table and cart-item-table policy to permit writes.

See [Service Catalog API coverage](../service-catalog-api.md) for endpoint coverage and documentation gaps.

## Change records and attachments

Every mutation requires a preview followed by `mutation_apply`.
The intended non-production instance, local policy, enabled group, and ServiceNow permissions must permit the operation.
The server does not enforce human approval.

### `flow_preview`

`create` accepts `edits` for one complete flow request and optional `flow_type="subflow"` for an inactive triggerless subflow.
Omitting `edits` retains empty-header creation. Complete creation validates before the native property POST and complete-definition PUT.
Apply returns aliases mapped to persisted IDs and a fresh revision. Partial failures report `error.outcome` without automatic retry.
Ordered edits support `insert_action` and `insert_subflow` with independently read published snapshots and branch placement.
Moving Try moves its linked Catch and both subtrees. Catch cannot move independently.

Lifecycle actions require `expected_scope` and `expected_revision`:

- `activate`: publishes a flow snapshot and enables triggers. Activation can start real work.
- `publish`: publishes a callable subflow. Flow publish-only is unavailable because the flow snapshot operation activates it.
- `deactivate`: disables new trigger starts for an active published flow. Existing executions continue.
- `test`: executes the saved inactive draft with real side effects and explicit static scalar inputs. Returns an exact execution context and observed state.

Preview sends no effect. Only `mutation_apply` dispatches it once. Unknown outcomes never permit automatic replay.
Follow a test through `flow.status` with the returned `execution_sys_id`. Runtime and snapshot content remain unverified.
`validate_remote` and `run` return an exact unsupported-contract error. Neither saving nor testing is an alias for those operations.

Experimental authoring through the undocumented Workflow Studio `/api/now/processflow` API.
Enable `flow_write` or `full`. Preview performs reads only. Apply the returned token with `mutation_apply` after review.

| Action        | Required inputs                                          | Optional inputs                     |
| ------------- | -------------------------------------------------------- | ----------------------------------- |
| `create`      | `name`, explicit `scope` sys_id or `global`              | `description`, `flow_type`, `edits` |
| `add_action`  | `sys_id`, published action snapshot `action_type_sys_id` | Native `inputs` object              |
| `add_subflow` | `sys_id`, published subflow definition `subflow_sys_id`  | Native `inputs` object              |
| `edit`        | `sys_id`, native `edits` array of 1-200 objects          | None                                |

`add_action` and `add_subflow` support only inactive, unpublished, linear drafts. Their inputs accept static string,
choice, boolean, or integer values.

`edit` applies ordered structural changes to an inactive draft in one save. Each edit has an `op`:
`set_trigger`, `insert_logic`, `move_step`, `update_inputs`, or `delete_step`. It supports triggers,
the supported families in `flow.describe`, branch placement, and typed trigger/step/variable/interface pills.
Trigger edits support Service Catalog, record created/updated/created-or-updated, and classic daily/weekly/monthly/run-once/repeat families.
`update_trigger` changes static inputs while preserving identity. `remove_trigger` removes the instance with native tombstones.
Used trigger references block source replacement/removal. Classic schedules use instance timezone and never activate a flow.
Saved Recurrence, business-calendar, and timezone override contracts remain unavailable.
`set_error_handler(enabled=boolean)` creates or toggles native draft error handling. Disabled populated handlers retain their contents as read-only.
`remove_error_handler` requires an empty handler. `main` and `error_handler` select native parent regions for supported logic insertion and step moves.
`create_stage(type="error", step=...)` creates a conditional Error state with derived standard-stage ancestry.
Error-stage updates preserve identity. Detaching or deleting an error stage removes it. These operations do not activate or execute a flow.
The preview shows the flow outline before and after. See `docs/flow-authoring.md` for fields and limits.
Saved action/logic deletion requires explicit child handling and connected-branch cascade choices.
Scalar flow-variable and subflow input/output declarations support `create_field`, `update_field`, and `delete_field`.
New declarations can supply typed pills to later edits in the same request. Used declarations cannot be removed implicitly.
Interface CRUD requires an unpublished triggerless subflow. Object and Array.String/Integer/Boolean/Object schemas support nested CRUD.
Use `parent` with a local name when creating children. Update or delete children by their full declaration path.
Array item schemas register automatically. Used descendants block destructive changes. Complex flow variables remain unavailable.
`assign_outputs` creates a native Assign Subflow Outputs step or updates selected rows with `step`.
`clear_output_assignment` removes a named assignment row. Output declarations remain unchanged.
Scalar values, native Objects, and Array.String/Integer/Boolean/Object values are supported, including nested scalar leaf pills.
Each named output replaces its complete value. Object keys are local child names. Unmentioned output rows remain unchanged.
Preview shows ordered native values before and after. Clear assignments before deleting their declarations.
Whole Object/Array.String/Integer/Boolean/Object pills are supported when recursive source and target schemas match.
Use one exact expression without child values. Producer snapshots are independently read and bound to preview.
Source-field projection, dynamic schemas, and output-to-output pills remain unavailable.
Set Flow Variables is supported. Standard stages support create, update,
attach, detach, reorder, and removal after explicit detachment.
Conditional error stages attach at creation and preserve their derived ancestry. Detach removes the error state, matching Studio.
See [experimental flow authoring](../flow-authoring.md) for native edit arguments and offline verification limits.
Unverified family serializers return unavailable.
Optional `expected_scope` and existing-flow `expected_revision` independently bind the target application and revision.
Lifecycle effects require both. Creation uses an explicit scope and forbids an existing revision.
The selected session application is not the target scope. No session switch is attempted.
`expected_update_set` is removed. Omit it. Flow operations do not read or validate update sets or Customer Updates.
ServiceNow performs normal capture. Capture metadata cannot block a verified save and is absent from success results.

Draft authoring actions never publish, activate, or execute a flow.
Complete creation needs one preview and one apply. Later edits can reorder, insert, update, and delete without complete-flow resubmission.
`add_subflow` resolves the current published snapshot. Calls wait for completion.
Appending a call never executes it or changes the called subflow.
Publication, read access, entitlement, scope access, and mandatory inputs are checked before staging.
The saved call ID is returned as `subflow_instance_sys_id`.
OAuth access to this undocumented API is instance-dependent. Browser success does not prove OAuth access.
Verify saved configuration with `flow.inspect`. ServiceNow owns update-set capture and selection.
State checks are not atomic. After an uncertain write, inspect the target before another preview.

### `record_preview`

| Action   | Required inputs                               | Review summary            |
| -------- | --------------------------------------------- | ------------------------- |
| `create` | `table`, native `data` object. Omit `sys_id`. | Proposed field values.    |
| `update` | `table`, `sys_id`, native `data` object       | Field differences.        |
| `delete` | `table`, `sys_id`. Omit `data`.               | Masked deletion snapshot. |

```json
{
  "action": "update",
  "table": "incident",
  "sys_id": "<32-character incident sys_id>",
  "data": { "work_notes": "Waiting for the caller to confirm the fix." }
}
```

The compact UTF-8 JSON representation is limited to 256 KiB and depth 32.
Null, false, zero, empty arrays, and empty objects retain their meanings.
Omitted update fields stay unchanged.
Supply complete script or markup values.
The tool does not read local script files or check JavaScript syntax.

Inherited dictionary metadata identifies XML fields and mandatory create fields.
XML values must be non-empty, well-formed strings.
Child declarations take precedence. Metadata request errors block mutations.
Apply refreshes relevant metadata and repeats the checks.
Dictionary fields hidden by ACLs cannot receive local validation.

### `attachment_preview`

| Action   | Required inputs                                        | Optional inputs                                     |
| -------- | ------------------------------------------------------ | --------------------------------------------------- |
| `upload` | `table`, `table_sys_id`, `file_name`, `content_base64` | `content_type`, default `application/octet-stream`. |
| `delete` | Attachment `sys_id`                                    | None.                                               |

Upload content must contain complete base64 file bytes.
The decoded content limit is 10 MiB.
The summary includes metadata, size, and digest, never file contents.
Deletion resolves the actual owning record from attachment metadata.
Apply checks ownership and detectable metadata changes again.

### `mutation_apply`

Every enabled mutation group registers this shared tool once.
Supply the reviewed preview's token:

```json
{ "preview_token": "<token returned by the preview>" }
```

Preview results contain `family`, `action`, `preview_token`, `expires_in_seconds`, `summary`, and `limitations`.
Tokens remain in memory for 300 seconds and disappear on restart.
The store permits 1,000 entries and 64 MiB of retained payloads.
An apply attempt consumes a token on success, rejection, cancellation, or failure.
Application checks the enabled family, environment, instance, authorization, policy, and detectable state changes.
State checks and the upstream mutation are not atomic.
Browser reauthorization requires a new preview.
An unknown remote outcome requires checking ServiceNow before another mutation.
No mutation is automatically replayed.
A preview token does not establish human approval.

## Interpret results and incomplete reads

Successful native structured results contain `status` and `data`.
Failures set MCP `isError=true` and include `error.code` and a safe `error.message`.
Responses can also contain `pagination`, non-empty `warnings`, and non-empty `truncation`.
`list_tool_packages` returns its registry in `data`.

Check the specific tool's continuation contract:

| Read                                              | Continuation and count meaning                                                                |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `query` lists and `analysis`                      | Use `limit`/`offset`. Pagination includes `offset`, `limit`, and `total`.                     |
| Default `describe` field pages                    | Use `field_limit`/`field_offset`. Response pagination uses `limit`, `offset`, and `total`.    |
| `cmdb.query`                                      | Use `limit`/`offset`. Returned count is the page size. No global total.                       |
| `attachment.list`, `code_search`, `audit.history` | Bounded results without an offset continuation input. Attachment total means returned count.  |
| `flow.inspect` and `flow.contract`                | Follow top-level `truncation` instructions. Selected flow warnings appear in `data.warnings`. |

Access rules, limits, retention, and incomplete metadata can affect results.
An empty or short result alone does not prove that matching records do not exist.
Ask the app to report filters, warnings, and incomplete sections when the
answer informs an operational or development decision.

For more complete workflows, see
[Agent recipes](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/docs/agent-recipes.md).
