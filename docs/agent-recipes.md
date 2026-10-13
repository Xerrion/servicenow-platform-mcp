# Agent recipes

These examples are MCP argument objects, not a Python client library.
Use the tool descriptions and schemas for supported inputs.
Read successful results from `structuredContent.data`.
Check `isError`, warnings, and truncation before relying on a result.

## Find open incidents

Arguments for `query`:

```json
{
  "table": "incident",
  "resolve_labels": [
    {"field": "state", "label": "open"},
    {"field": "priority", "label": "high"}
  ],
  "fields": ["number", "short_description", "priority", "state"],
  "limit": 20
}
```

Use `resolve_choice` to inspect the supported choice keys before selecting labels.
Choice resolution does not grant record access.

## Review and create an incident

Arguments for `record_preview`:

```json
{
  "action": "create",
  "table": "incident",
  "data": {
    "short_description": "Email service unavailable",
    "state": "1"
  }
}
```

Review the returned summary and limitations.
After the required approval, call `mutation_apply` with its `preview_token`.
The token expires after 300 seconds and is single-use.
A token does not prove human approval.

## Find recent script changes

Arguments for `query`:

```json
{
  "table": "sys_script",
  "encoded_query": "active=true^sys_updated_on>=javascript:gs.daysAgoStart(7)",
  "fields": ["name", "collection", "sys_updated_on", "sys_updated_by"],
  "order_by": "-sys_updated_on",
  "limit": 50
}
```

Read the complete script with `record_read`.
Script changes supply complete field values to `record_preview`.
Follow the instance's application or update-set delivery process.

## Read record history

Arguments for `audit`:

```json
{
  "action": "history",
  "table": "incident",
  "sys_id": "<32-character record sys_id>",
  "window_days": 7
}
```

The result is bounded and subject to ACLs and retention.
For journal entries, use `analysis` with `action="journal_history"` and a native `fields` array.
For submitted requested-item answers, use `analysis` with `action="ritm_variables"`.

## Inspect a flow contract

Arguments for `flow`:

```json
{
  "action": "contract",
  "name": "New starter",
  "sections": ["flow", "published_state", "inputs", "outputs", "triggers", "steps", "warnings"],
  "section_limit": 50
}
```

Configured bindings are not evidence of executed steps.
Follow top-level truncation guidance before treating a section as complete.
V1 limitations, inaccessible definitions, and snapshot differences remain visible in warnings.

## Browse and prepare a catalog order

Read `service_catalog` with `action="items_list"` and a bounded text search.
Then read `item_variables` for the selected item.
Arguments for `service_catalog_preview`:

```json
{
  "action": "order_now",
  "item_sys_id": "<32-character item sys_id>",
  "variables": {
    "business_justification": "Replace a damaged laptop"
  }
}
```

Review the item, variables, and limitations before application.
Preview does not simulate fulfillment or guarantee prices.
Cart submission and checkout also require preview followed by `mutation_apply`.

## Inspect and change attachments

Use `attachment` to inspect metadata and ownership before downloading.
Use `attachment_preview` for upload or deletion.
Upload accepts complete base64 content with a decoded limit of 10 MiB.
Its summary contains metadata, size, and digest, not file contents.
Apply the reviewed preview with `mutation_apply`.

## Count without reading every record

Arguments for `query`:

```json
{
  "table": "incident",
  "encoded_query": "active=true",
  "aggregate": [{"operation": "count"}],
  "group_by": ["assignment_group"]
}
```

Other operations use objects such as `{"operation":"avg","field":"reopen_count"}`.
Aggregates can scan many records even when the output is small.
Large tables require a recognized date constraint.

## Preserve evidence limits

Use native arrays for fields, sections, and grouping.
Use native objects for data, investigation parameters, and catalog variables.
Encoded query strings remain ServiceNow filter expressions.
Keep projections small, paginate supported reads, and report incomplete evidence.
If a mutation fails with an unknown remote outcome, check ServiceNow before preparing another mutation.
