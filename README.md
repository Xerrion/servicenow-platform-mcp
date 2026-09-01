# servicenow-platform-mcp

`servicenow-platform-mcp` is an asynchronous Python 3.12+ server that gives AI
tools access to ServiceNow. It uses the Model Context Protocol (MCP) as the
AI and tool access layer. It still uses ServiceNow REST APIs underneath.

Use it to discover schemas, read bounded record data, inspect attachments and
Flow Designer records, inspect audit configuration and history, run
investigations, analyse fulfilled catalog requests, and perform gated writes.

## Contents

- [Capabilities](#capabilities)
- [Architecture and transport](#architecture-and-transport)
- [Install and run](#install-and-run)
- [Configuration and authentication](#configuration-and-authentication)
- [MCP client configuration](#mcp-client-configuration)
- [Tool packages](#tool-packages)
- [Tool reference](#tool-reference)
- [Analysis details](#analysis-details)
- [Query, selection, pagination, and schema discovery](#query-selection-pagination-and-schema-discovery)
- [Writes and safety](#writes-and-safety)
- [ServiceNow permissions](#servicenow-permissions)
- [Flow, audit, investigations, and Service Catalog](#flow-audit-investigations-and-service-catalog)
- [Attachments](#attachments)
- [Responses, errors, and observability](#responses-errors-and-observability)
- [Development and verification](#development-and-verification)
- [Known limitations and non-goals](#known-limitations-and-non-goals)
- [Security](#security)
- [Contributing and license](#contributing-and-license)

## Capabilities

- **Schema discovery.** Describe ServiceNow tables, inherited fields, field
  types, documentation, and dictionary provenance.
- **Bounded reads.** Query records and aggregates with encoded queries,
  explicit projections, pagination, and display values.
- **Generic table support.** The same tools work with Incident, Problem,
  REQ/RITM, `sc_task`, `task_sla`, CMDB relations such as `cmdb_rel_ci`, and
  custom tables and fields. These are examples, not a hardcoded table list.
- **Attachments.** List, inspect, and download attachments. Upload and delete
  operations are in a separate, explicit tool group.
- **Flow inspection.** Read Flow Designer flows and subflows from their table
  records, including triggers, inputs, outputs, variables, actions, logic, and
  warnings.
- **Audit inspection.** Check table and field audit posture and read a masked,
  date-bounded audit trail.
- **Investigations.** Run registered investigations and explain findings.
- **Read-only analysis.** Compose submitted variables for one fulfilled RITM
  and read dictionary-confirmed journal history.
- **Gated writes.** Create, update, delete, and write script-bearing records
  through a preview/apply workflow by default. Service Catalog ordering and
  state-changing cart actions are also gated.
- **Choice resolution.** Map a human-readable choice label to its stored value.
- **Code search.** Search script-bearing artifacts through ServiceNow Code
  Search.

## Architecture and transport

The server uses MCP SDK v2 (`mcp>=2.1.1`) and `MCPServer`. It runs over stdio.
MCP clients launch or connect to the process and call its tools.

ServiceNow calls are asynchronous and use `httpx`. The server shares one HTTP
pool for its lifetime. Choice, dictionary, and audit configuration use shared
metadata registries and a bounded TTL cache. Records, query results, previews,
attachments, and audit row counts are not metadata-cache entries.

Each operational tool wrapped by `@tool_handler` receives a generated
correlation ID and returns a serialized JSON response envelope. The envelope
has a stable `status`, `data`, and `correlation_id` shape. The bootstrap
`list_tool_packages` tool returns the preset-to-group registry directly and is
the only public tool that does not use this envelope.

## Install and run

The current project version is 0.11.0. Supported Python versions are 3.12,
3.13, and 3.14. The project uses `uv`.

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

For local development, run the installed editable entry point:

```bash
uv run servicenow-platform-mcp
```

Run this command with the cloned project as the working directory, unless the
package is installed in another managed environment.

The entry point is `servicenow_mcp.server:main`. To build a distribution:

```bash
uv build
```

The process uses stdio. Do not start it as an HTTP endpoint for an MCP client.

## Configuration and authentication

Settings use environment variables. The process also reads `.env` and
`.env.local` from its working directory. With the current `pydantic-settings`
configuration, later dotenv sources override earlier ones, and process
environment variables override dotenv values. Start the process from the
directory that contains the intended dotenv files. A client that starts the
process with another working directory will not read the files you expect.

| Environment variable | Required | Default | Valid range or values | Purpose |
| --- | --- | --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | None | Must start with lowercase `https://` | ServiceNow instance base URL. Trailing `/` characters are removed. |
| `SERVICENOW_API_KEY` | Conditional | Empty | Must contain a non-whitespace character when used | API-key authentication. Takes precedence over Basic Auth. |
| `SERVICENOW_USERNAME` | Conditional | Empty | Required when API key is empty | Basic Auth username. |
| `SERVICENOW_PASSWORD` | Conditional | Empty | Required when API key is empty | Basic Auth password. |
| `MCP_TOOL_PACKAGE` | No | `full` | Preset or comma-separated groups | Selects loaded tool groups. |
| `SERVICENOW_ENV` | No | `dev` | Any string; `prod` and `production` block writes | Local environment label and write policy input. |
| `MAX_ROW_LIMIT` | No | `100` | `1`-`10000` | Maximum row count for bounded generic and query-oriented tool paths that use this setting. It is not a universal response or egress cap. |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,sys_audit,sys_log_transaction,sys_email_log` | Comma-separated table names | Tables that require date-bounded queries. |
| `SCRIPT_ALLOWED_ROOT` | No | Empty | Filesystem path | Root required for `script_path`; empty disables that file input. |
| `HTTPX_TIMEOUT_SECONDS` | No | `30.0` | `1.0`-`600.0`, finite | ServiceNow HTTP timeout. |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | `1`-`86400` | Metadata freshness window. |
| `SENTRY_DSN` | No | Empty | String accepted by the Sentry SDK as a DSN | Enables optional Sentry error reporting. |
| `SENTRY_ENVIRONMENT` | No | Empty | Any string | Sentry environment; empty uses `SERVICENOW_ENV`. |

The instance URL and usable authentication settings are validated at startup,
even when no selected tool will perform a request. URL validation requires the
literal `https://` prefix; use a complete instance base URL such as
`https://your-instance.service-now.com`. Authentication validation requires
either a usable API key or both a username and password.

For API-key authentication, configure a key placeholder only. The server sends
the exact `x-sn-apikey` header. It does not send an `Authorization` header in
this mode. If the API key is empty, the server sends HTTP Basic Auth from the
username and password.

Never place a real key or password in this README, a committed configuration
file, or a log. Restart the full server process after changing environment
variables. Settings are loaded at startup.

## MCP client configuration

MCP clients normally start the command below and communicate over stdio. The
following generic shape avoids client-specific fields. Use the equivalent
stdio configuration fields supported by your client.

API key variant:

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_API_KEY": "${SERVICENOW_API_KEY}",
    "MCP_TOOL_PACKAGE": "readonly"
  }
}
```

Basic Auth variant:

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_USERNAME": "${SERVICENOW_USERNAME}",
    "SERVICENOW_PASSWORD": "${SERVICENOW_PASSWORD}",
    "MCP_TOOL_PACKAGE": "readonly"
  }
}
```

`${...}` is a placeholder pattern. Whether a client expands it depends on that
client. Prefer its documented environment forwarding feature, or start the
client from a shell where the variables already exist. Do not commit a file
with substituted secrets.

## Tool packages

A **tool group** is a loader module. A **public MCP tool** is a callable tool
registered by a group. The `record_write` group registers two public tools.

The server always registers `list_tool_packages`. The preset package counts
below include that tool.

| Preset | Groups | Public MCP tools | Purpose |
| --- | --- | ---: | --- |
| `full` | All 13 groups | 15 | Complete surface, including all writes. |
| `readonly` | `query`, `describe`, `record_read`, `attachment`, `investigate`, `resolve_choice`, `analysis`, `audit`, `flow`, `code_search` | 11 | Read-only operational and analysis surface. |
| `core_readonly` | `query`, `describe`, `attachment` | 4 | Small read-only core. |
| `none` | No groups | 1 | Only `list_tool_packages`. |

`full` includes both `attachment` and `attachment_write`. `attachment` is
read-only. `attachment_write` is explicit opt-in in custom packages.
`readonly` and `core_readonly` exclude attachment writes. `analysis` is in
`full` and `readonly`, but not `core_readonly`.

Custom packages use comma-separated group names:

```bash
MCP_TOOL_PACKAGE=query,describe,record_read,attachment uv run servicenow-platform-mcp
```

Valid groups are `query`, `describe`, `record_write`, `record_read`,
`attachment`, `attachment_write`, `investigate`, `resolve_choice`,
`service_catalog`, `analysis`, `audit`, `flow`, and `code_search`.
`list_tool_packages` reports the preset-to-group mapping. It does not expand
groups into the public tool names shown below.

## Tool reference

All tools return JSON strings. The `correlation_id` argument is generated by
the server and is not part of the client-facing schema.

| Tool | Purpose and important actions | Essential inputs and behavior | Packages |
| --- | --- | --- | --- |
| `list_tool_packages` | Lists preset packages and their groups. | No inputs. Always available. Returns the registry as JSON without the standard response envelope. | All |
| `query` | Reads records or aggregates. | `table`; list mode needs `fields`; use `encoded_query`, `limit`, `offset`, `order_by`, `display_values`, `aggregate`, `group_by`, and `resolve_labels`. Exact `sys_id` mode is also supported. | `full`, `readonly`, `core_readonly` |
| `describe` | Describes fields, tables, or script fields. | Default table description; `action=list_tables` with optional `name_filter`; `action=list_script_fields` with `table`. Supports `fields`, `verbose`, `include_docs`, `field_offset`, and `field_limit`. | `full`, `readonly`, `core_readonly` |
| `record_read` | Reads one record by `sys_id` or `name`. | `table` and exactly one selector. `fields` is optional; `*` requests all masked fields. Includes discovered `script_fields`. | `full`, `readonly` |
| `record_write` | Creates, updates, or deletes a record. | `action=create|update|delete`, `table`, optional `sys_id`, JSON `data`, optional `script_path` and `script_field`, and `preview` (default `true`). | `full` |
| `record_apply` | Applies a record-write preview. | `preview_token` from `record_write`. The token is single-use. | `full` |
| `attachment` | Reads attachment metadata and content. | `action=list|get|download|download_by_name`; list and name lookup use `table` and `table_sys_id`; direct actions use attachment `sys_id`. | `full`, `readonly`, `core_readonly` |
| `attachment_write` | Uploads or deletes attachments. | `action=upload|delete`; upload uses parent table, record ID, file name, Base64 content, and MIME type; delete uses attachment `sys_id`. | `full` |
| `investigate` | Runs or explains investigations. | `action=run|explain|describe`; run uses `name` and JSON `params`; explain uses `element_id=table:sys_id` and optional `name`. | `full`, `readonly` |
| `resolve_choice` | Resolves choice labels. | `table`, `field`, and optional `label`. An empty label returns the full mapping. | `full`, `readonly` |
| `service_catalog` | Reads catalogs and performs catalog/cart actions. | Actions are listed below. Reads use IDs, filters, and paging. `order_now` and `add_to_cart` accept a JSON `variables` object; all state-changing actions are gated. | `full` |
| `audit` | Inspects audit posture and history. | `action=check_field|check_fields|check_table|history|describe`; table and field selectors are action-dependent. | `full`, `readonly` |
| `flow` | Inspects Flow Designer data. | `action=contract|inspect|find_by_table|decode_values|list_triggers|describe`; flow selection uses `sys_id` or `name`. | `full`, `readonly` |
| `code_search` | Searches ServiceNow script artifacts. | `action=search|list_tables|describe`; search needs `term` and accepts `table`, `search_group`, and `limit`. | `full`, `readonly` |
| `analysis` | Composes RITM variables or reads journal history. | `action=ritm_variables|journal_history|describe`; inputs are detailed below. | `full`, `readonly` |

Use each tool's `describe` action where available for the runtime action
registry. The public tool schemas are the authoritative input contract.

Schema defaults are empty strings for optional string inputs unless stated
otherwise. Important exceptions and effective defaults are:

- `query`: `limit=20`, `offset=0`, and `display_values=false`;
- `describe`: empty `action` selects table description, `field_limit=25`,
  `field_offset=0`, `verbose=false`, and `include_docs=false`;
- `record_write`: `preview=true`;
- `attachment_write`: `content_type="application/octet-stream"`;
- `investigate`: `params="{}"`;
- `service_catalog`: `limit=20`, `offset=0`, and
  `top_level_only=false`;
- `code_search`: `action="search"` and `limit=20`;
- `analysis`: schema values `limit=0` and `window_days=0` select the effective
  defaults described below;
- `audit`: schema values `limit=0` and `window_days=0` select
  `MAX_ROW_LIMIT` and 90 days where the action uses them; and
- `flow`: schema values `limit=0` and `section_limit=0` select effective
  defaults of 100, with section limits still capped by `MAX_ROW_LIMIT`.

All other required inputs and action-specific combinations are shown in the
tool table or the detailed sections below. Optional booleans not listed above
default to `false`.

## Analysis details

### Fulfilled RITM variables

Call `analysis(action="ritm_variables", sys_id="<32-char-sys-id>")`. Optional
`limit` and `offset` are bounded by `MAX_ROW_LIMIT`. The tool first confirms
the `sc_req_item`, then composes submitted answers through:

1. `sc_item_option_mtom` for submitted-answer links;
2. `sc_item_option` for submitted values; and
3. `item_option_new` for variable definitions.

The response contains `data.table`, `data.sys_id`, `entry_count`, and
`entries`. A resolved entry includes answer and definition IDs, `name`,
`label`, `type`, `raw_value`, `display_value`, `reference_target`,
`variable_set`, `multi_value`, `masked`, and `status`. Degraded entries for
missing options or definitions are intentionally sparse and identify their
condition through `status`. The response also contains pagination and
selection metadata.

Variable names and labels that indicate a password, token, secret, credential,
API key, or private key cause masking. If either the name or label is missing,
the affected answer is masked conservatively.

Variable types `21`, `list_collector`, and `List Collector` are all treated as
List Collectors. Unmasked List Collector values retain their raw identifiers.
The response includes a warning, sets `multi_value=true` when a
comma-separated value contains more than one non-empty identifier, and sets
`display_value` to `null`. Reference values also keep raw sys_ids and do not
receive generic display-value resolution.

Every successful `ritm_variables` response contains:

```json
{
  "unsupported_features": {
    "multi_row_variable_sets": {
      "present": false,
      "payload_fields_retrieved": false
    }
  }
}
```

The `present` value reflects a bounded presence query on
`sc_multi_row_question_answer`. MRVS payload fields are not retrieved or
decoded. This metadata does not change answer pagination.

An inaccessible or missing submitted option produces an `orphaned_option`
entry and a warning. An inaccessible or missing definition produces an
`inaccessible_definition` entry, masked values, and a warning. Duplicate
submitted-answer links are preserved and reported. Row ACLs, field ACLs,
missing definitions, and instance data affect completeness.

### Journal history

Call `analysis(action="journal_history", table="incident",
sys_id="<32-char-sys-id>")`. Optional inputs are:

- `fields_csv`: comma-separated `comments`, `work_notes`, and
  `close_notes`. The default is `comments,work_notes`.
- `since`: `YYYY-MM-DD`; it overrides `window_days`.
- `window_days`: non-negative integer. The default is 90 days.
- `limit` and `offset`: bounded pagination. The default limit is
  `MAX_ROW_LIMIT`.

Each requested field must exist in the resolved dictionary and have a journal
type. Entries come from `sys_journal_field` and are ordered by
`sys_created_on`, then `sys_id`, ascending. The response reports the effective
date window, fields, entries, selection, pagination, and an ACL/retention
warning.

This is journal history. It is different from `audit(action="history")`,
which reads field changes from `sys_audit`.

## Query, selection, pagination, and schema discovery

`query` has three modes: exact-record mode when `sys_id` is set, aggregate
mode when `aggregate` is set, and list mode otherwise. List-mode calls require
an explicit `fields` projection. Use
`fields="*"` only when all masked fields are intentional. `sys_id` is always
included. Exact-record mode defaults to `sys_id,sys_updated_on` and accepts an
explicit projection or `*`.

`record_read` with empty `fields` returns compact identity and update fields
plus all discovered script-bearing fields. `fields="*"` returns the full
masked record. `record_read` always includes `script_fields` and `sys_id`.

`describe` walks `sys_db_object.super_class` child-first. Child declarations
override ancestor declarations. Each field includes `inherited_from` where
the response shape supports provenance. Empty `fields` returns an alphabetical
page of 25 fields by default. `field_offset` continues the page and
`field_limit` accepts 1-100. `fields="*"` requests all fields. Use
`action=list_script_fields` to return discovered script fields and their
resolved chain.

The default and verbose describe shapes include a `choice_count`. Choice
counts are read from the queried table first and then from each inherited
field's declaring table when needed. `include_docs=true` adds matching
`sys_documentation` records for the selected fields, with the same fallback to
the declaring table. Choice-count failures produce a warning and zero counts;
documentation failures follow normal tool error handling.

Choice, dictionary, and audit-configuration caches use
`METADATA_CACHE_TTL_SECONDS`. Each metadata cache is limited to 1,000 entries,
uses least-recently-used eviction, shares one in-flight load for the same key,
and permits different keys to load concurrently. Expired entries are reloaded
before the requesting call returns. These caches do not store records, query
results, previews, attachments, or audit row counts.

Encoded queries are passed to ServiceNow. Identifiers are validated and query
safety caps the effective limit at `MAX_ROW_LIMIT`. Tables in
`LARGE_TABLE_NAMES_CSV` require a structural date constraint such as
`sys_created_on>=YYYY-MM-DD`. Aggregate requests use the Aggregate API.

`MAX_ROW_LIMIT` applies only to bounded generic and query-oriented paths that
use it. It is not a universal response or egress cap. Service Catalog actions
have action-specific limits. The attachment list has a fixed maximum of 100
metadata records and no caller-controlled offset or pagination.

Successful bounded reads can include `selection` and `pagination` metadata.
Use `next_offset`, `truncated`, `total`, and returned-field metadata to
continue a read. A tool may add warnings when a platform or local limit caps a
request.

## Writes and safety

The policy layer blocks these tables:

`sys_user_has_password`, `oauth_credential`, `oauth_entity`, `sys_certificate`,
`sys_ssh_key`, `sys_credentials`, `discovery_credentials`, and
`sys_user_token`.

Key-name masking for names containing password, token, secret, credential,
`api_key`, or `private_key` applies only on specific record-oriented paths that
call the local masking helpers. It is not a global output filter. Query
aggregate mode returns Stats API results directly, without local field-value
masking. Code Search, Flow, Service Catalog, and other arbitrary payload
surfaces are not universally masked. Do not group or aggregate sensitive
fields. Enforce ServiceNow field ACLs as the primary control. Audit rows use
the audit field name to mask old and new values.

Writes are blocked when `SERVICENOW_ENV` is `prod` or `production`. This local
gate does not replace ServiceNow ACLs. ServiceNow remains the authority for
authorization.

`record_write` defaults to preview mode. A preview returns a single-use
`preview_token` and a masked preview. `record_apply` consumes the token and
re-checks policy before applying it. Tokens expire after five minutes and are
single-use, are held only in the server process that created them, and are
consumed before the application attempt. A failed attempt cannot be retried
with the same token. Set `preview=false` only when an immediate write is
appropriate.

When `script_path` is used, the resolved file must be under
`SCRIPT_ALLOWED_ROOT`, must be readable as UTF-8, and must be no larger than
1 MiB. Strict path resolution prevents traversal and symlink escapes. The
dictionary registry chooses the first script-bearing field child-first unless
`script_field` selects one of the discovered fields. XML script fields are
validated as well-formed XML before the platform call.

Attachment upload and delete are in `attachment_write`, which is separate from
read-only `attachment` and is gated again at runtime. Attachment transfer size
is limited to 10 MiB.

Service Catalog write actions are `order_now`, `add_to_cart`, `cart_submit`,
and `cart_checkout`. They apply write gates to the relevant request or cart
table. A read of fulfilled RITM variables through `analysis` is read-only and
does not order or change a catalog item.

For a true read-only deployment, combine all of the following:

1. `MCP_TOOL_PACKAGE=readonly`, or a smaller custom package containing only
   read groups;
2. GET-only ServiceNow REST API resources;
3. read-only table and field ACLs; and
4. a production environment label so local writes are blocked.

Package selection is not a replacement for ServiceNow authorization.

## ServiceNow permissions

Authentication and authorization are separate controls. An API key must be
permitted to use the required REST API resources. Table ACLs and field ACLs
then control the records and fields that those resources can return or change.

The registered tools use these ServiceNow APIs and resources as applicable.
API titles match the local OpenAPI specifications:

| API title | Paths and methods used by registered tools | Use |
| --- | --- | --- |
| Table API | `GET/POST /api/now/table/{table}`; `GET/PATCH/DELETE /api/now/table/{table}/{sys_id}` | Query, describe metadata reads, record reads and writes, Flow inspection, analysis composition, and attachment-by-name metadata lookup. |
| Aggregate API | `GET /api/now/stats/{table}` | Query aggregates and audit positive-control counts. |
| Attachment API | `GET /api/now/attachment`; `GET/DELETE /api/now/attachment/{sys_id}`; `GET /api/now/attachment/{sys_id}/file`; `POST /api/now/attachment/file` | Attachment metadata, downloads, uploads, and deletes. |
| Code Search | `GET /api/sn_codesearch/code_search/search`; `GET /api/sn_codesearch/code_search/tables` | `code_search`. |
| Service Catalog API | GET under `/api/sn_sc/servicecatalog/catalogs`, `/categories`, `/items`, and `/cart`; POST to `/items/{sys_id}/order_now`, `/items/{sys_id}/add_to_cart`, `/cart/submit_order`, and `/cart/checkout` | Catalog, item, variable, cart, and order actions. |

For a read-only package, allow GET on the Table, Aggregate, Attachment
metadata/download, Code Search, and read-only Service Catalog paths used by
the selected tools. For writes, add only the POST, PATCH, and DELETE resource
permissions needed by the selected Table, Attachment, and Service Catalog
actions. The client retains methods for some APIs that no registered tool
uses; those endpoints are not required for the tool surface documented here.
The exact API-key REST-resource policy depends on the instance and must be
configured in ServiceNow.

Analysis needs Table API access and applicable read ACLs for `sc_req_item`,
`sc_item_option_mtom`, `sc_item_option`, `item_option_new`,
`sc_multi_row_question_answer`, `sys_journal_field`, `sys_db_object`, and
`sys_dictionary`. General tools also need read access to the target tables and
their selected fields. Flow inspection uses Table API records. It does not
use Workflow Studio APIs or undocumented `processflow` endpoints.

Dynamic table access and instance-specific ACL design must be configured in
ServiceNow. The MCP package cannot grant access that the instance denies.

## Flow, audit, investigations, and Service Catalog

### Flow

`flow` supports `contract`, `inspect`, `find_by_table`, `decode_values`,
`list_triggers`, and `describe`. It reads both V1 and V2 Flow Designer tables.
It joins V2 record-trigger conditions through the remote trigger ID. The
decoder handles gzip plus Base64 plus JSON `values` blobs. A decode failure is
reported on the affected node while the enclosing inspection can still
succeed.

The implementation deliberately does not call undocumented
`/api/now/processflow/*` endpoints. It also skips `sys_hub_flow_snapshot`, an
opaque compiled cache.

### Audit

`audit` supports `check_field`, `check_fields`, `check_table`, `history`, and
`describe`. Audit reads use a default 90-day window because `sys_audit` is a
large table. `since` on `history` overrides `window_days`.

Verdicts include `audited`, `not_audited_field_flag`,
`not_audited_table_flag`, `audited_but_inactive`, and `inconclusive`. Field
configuration is resolved child-first. The `no_audit=true` attribute vetoes a
field audit flag. Positive-control counts distinguish configured but inactive
fields from cases that cannot be determined.

### Investigations

`investigate` supports `run`, `explain`, and `describe`. The seven registered
modules are:

- `stale_automations` - finds unused or stale automation rules;
- `deprecated_apis` - detects deprecated API usage;
- `table_health` - analyses table structure and data quality;
- `acl_conflicts` - finds conflicting ACL rules;
- `error_analysis` - analyses error patterns;
- `slow_transactions` - identifies slow transactions; and
- `performance_bottlenecks` - identifies performance issues.

### Service Catalog

`service_catalog` supports `catalogs_list`, `catalog_get`,
`categories_list`, `category_get`, `items_list`, `item_get`,
`item_variables`, `order_now`, `add_to_cart`, `cart_get`, `cart_submit`, and
`cart_checkout`. List actions support text, catalog/category filters, limits,
offsets, and top-level category selection. `order_now` and cart actions that
change state are write-gated. This surface is separate from read-only
inspection of fulfilled RITM answers through `analysis`.

## Attachments

The read-only `attachment` tool supports:

- `list` - list metadata for a parent table and record;
- `get` - return masked metadata for one attachment;
- `download` - return masked metadata and Base64 content; and
- `download_by_name` - resolve metadata by parent and file name, then download
  the earliest-created match when multiple rows match.

Reads validate parent table access and attachment metadata. Downloads check the
declared and received size. The maximum supported transfer size is 10 MiB.
Attachment content is returned as data and is not content-classified by MCP.
`attachment(action="list")` returns at most 100 metadata records. It has no
caller-controlled offset or pagination, so do not assume that a list is
complete beyond that fixed bound.

The separate `attachment_write` tool supports `upload` and `delete`. Uploads
use Base64 content and a default MIME type of
`application/octet-stream`. Among presets, upload and delete are available
only in `full`; a custom package can opt in with `attachment_write`. Both
actions are subject to write gates and ServiceNow authorization.

## Responses, errors, and observability

The standard success envelope is:

```json
{
  "correlation_id": "generated-id",
  "status": "success",
  "data": {}
}
```

Depending on the tool, the envelope can also contain `pagination`, `selection`,
and `warnings`. An error envelope has `status: "error"`, `data: null`, and an
`error` object with a `message` field:

```json
{
  "correlation_id": "generated-id",
  "status": "error",
  "data": null,
  "error": {"message": "reason"}
}
```

`@tool_handler` generates correlation IDs, records redacted tool context for
Sentry, and routes exceptions through safe tool handling. Tool functions do
not leak Python exceptions to MCP callers. If Sentry is enabled, unexpected
exceptions are captured before the error envelope is returned.

### Troubleshooting

- **Missing instance URL:** set `SERVICENOW_INSTANCE_URL` to a full HTTPS URL.
  Startup validation errors list setting names and constraints without input
  values.
- **401 `User Not Authenticated`:** verify the API key or Basic Auth values,
  the exact instance URL, and the authentication policy on the instance. API
  key mode uses `x-sn-apikey`.
- **API key policy failure:** check API-key REST-resource permissions. A valid
  key does not automatically grant table or field access.
- **Table or field denial:** check the target table ACL and field ACL. The
  selected MCP package only controls which tools are exposed.
- **Changed environment values have no effect:** restart the full MCP process.
- **`-32000`:** this can be a client-level wrapper. Inspect the MCP client's
  stderr and the underlying server process error before choosing a cause.

## Development and verification

```bash
uv sync --group dev
uv run pytest
uv run pytest tests/test_client.py
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv build
```

Integration tests use a live instance and require credentials in `.env.local`.
Do not use production credentials for tests.

Source uses a `src/servicenow_mcp/` layout. Tool groups live in
`src/servicenow_mcp/tools/`. Tests live in `tests/` and use `pytest`,
`pytest-asyncio`, and `respx` for HTTP mocking. The default test command
excludes tests marked `integration`.

## Known limitations and non-goals

- Custom fields require dictionary discovery and suitable ServiceNow ACLs.
- RITM reference and List Collector answers retain raw sys_ids. Generic
  display-value resolution is not provided.
- List Collector display values are not fabricated from raw identifiers.
- MRVS payload fields are not retrieved or decoded.
- RITM results can contain orphaned options or inaccessible definitions.
- Journal and audit completeness depends on row ACLs, field ACLs, and instance
  retention.
- Flow inspection reads documented table records and does not inspect opaque
  compiled snapshots.
- Attachment content is not classified by MCP. Treat downloaded content as
  untrusted.
- ServiceNow instance configuration, API-key resource policy, ACLs, and row
  visibility can limit results beyond the local tool limits.

## Security

Use least-privilege API keys and ServiceNow ACLs. Expose only the tool groups
that operators need. Prefer `readonly` or a smaller custom package for read
workflows. Keep write operations in a non-production environment until they
are understood and tested.

Do not commit `.env`, `.env.local`, credentials, API keys, or generated files
that contain sensitive values. Do not log secrets, tokens, passwords, or PII.
Review attachment content and submitted catalog values before forwarding them
to other systems.

## Contributing and license

Open an issue for a bug or feature request:
<https://github.com/Xerrion/servicenow-platform-mcp/issues>.

The project is licensed under the [MIT License](LICENSE).
