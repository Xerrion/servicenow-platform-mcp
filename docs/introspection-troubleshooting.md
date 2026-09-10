# Introspection failures and evidence limits

An investigation can be incomplete even when the caller has record access. Treat a failed tool, an unresolved reference, and a truncated answer as different conditions. Do not request more privileges without an explicit access denial.

## REST 401 diagnostics

A successful OAuth grant does not establish REST access. REST 401 errors include
`Safe response evidence`: a parsed `error.message`, selected `WWW-Authenticate`
scheme/error/error_description fields, and at most one `x-transaction-id`,
`x-request-id`, or `x-correlation-id`. Text must match reviewed static phrases;
unknown text gets an omission marker, not a partial quote. Trace values must be
8-64 hex characters or a UUID. Known credential, cookie, and query reflections
are excluded. Bodies over 8192 bytes and challenge headers over 4096 characters
or four challenges are omitted. Bodies, other headers, and query strings are
never copied into these diagnostics.

Restart the full MCP process to load the change. Retry one read-only tool call.
The rejected request is never replayed. A later call refreshes the invalidated
token or opens authorization if no usable refresh grant remains. If the new
token also receives 401, share only the safe evidence with the administrator;
an omitted field is not evidence that ServiceNow sent no diagnostic.

## Corrected server behavior

- **Code Search group:** Both Code Search endpoints now send `sn_codesearch.Default Search Group` when `search_group` is empty. The checked-in Code Search specification requires a group when filtering by `table`. Omitting it allowed a table-specific search to return unrelated tables. An explicit group still takes precedence. The reported `list_tables` error (`"empty" is not defined`) came from the remote endpoint; the default removes the missing-group request, but a live retry is still required to confirm that instance's response.
- **Aggregate grouping:** `query(aggregate="count", group_by="state,active", table="sc_task")` now validates each grouping field separately and sends the normalized CSV to the Stats API. Empty fields and encoded-query injection remain rejected.
- **Flow discovery:** V1 trigger instances do not have a `table` column in the supplied schema. Table discovery now joins `sys_flow_record_trigger` to V1 `remote_sys_id`; V2 uses `remote_trigger_id` or the shared trigger ID. Flow headers can resolve through `sys_id`, `master_snapshot`, or `latest_snapshot`. Canonical and snapshot references to the same flow are de-duplicated.
- **Unresolved flow metadata:** `find_by_table` returns `unresolved_flow_ids`, `metadata_resolved=false`, and `active=null` when a header cannot be resolved. Such entries are not evidence of an inactive flow. Older snapshots and inaccessible headers remain possible; current snapshot resolution does not reconstruct historical flow versions. Internal trigger limits produce a warning.
- **Flow response errors:** Non-JSON HTTP responses now report the request method, endpoint path, and status. Bodies and query values are not included. A parsing error is not an ACL verdict. Invalid node values continue to use the existing per-node decode error behavior.

`find_by_table` finds record triggers, not every flow that can write to a table. Catalog-triggered flows also need a bounded lookup of `sc_cat_item.flow_designer_flow`. Called subflows, legacy workflows, and server-side writers need separate analysis.

## Issues outside the server

The supplied transcript contains `<<ccr:...>>` compression markers, including in tool inputs. This repository does not generate or decode those markers. They are not ServiceNow script syntax. Recover the original text through the agent host's supported expansion/export path. Repeating the same server read or changing permissions is not a reliable remedy. Do not implement a guessed decoder or infer missing code from a marker.

The agent also stopped before reading all workflow pages. Continue `query` with the same projection, filter, and ordering, advancing `offset` until the bounded result set is exhausted. A count or a first page is not proof that all relevant scripts were reviewed.

The transcript's loaded MCP reference described retired tool names. Use the running tool schema and this repository's tool reference. Update external agent skills at their own source; this change does not modify global agent configuration.

## Remaining verification

The transcript does not contain the raw HTTP responses behind three failed flow contracts. Their exact cause remains unknown. Retry with the contextual errors above. Start with `sections="flow,published_state"`, then request `steps`, `triggers`, and `warnings` separately to isolate the failed dependency. Keep errors and correlation IDs; do not interpret a failed section as an empty one.

No live instance calls or record writes were used to validate these changes. Local tests use synthetic data and mocked HTTP responses. The customer transcript is not part of the committed change.
