# Flow lookup limits

## Flow identity and lookup errors

Use `sys_hub_flow.sys_id` for a flow or subflow definition.
`master_snapshot` and `latest_snapshot` identify snapshots, not the definition.
Step IDs, action type IDs, and execution context IDs are also different identities.
For `inspect` and `contract`, you can supply the definition's `name` or `internal_name` through `name` instead.

When `inspect` or `contract` cannot read a definition, it checks at most two visible owners through current snapshot references.
It does not read snapshot contents or automatically select another target.

| Error code                | Meaning and recovery                                                                                                                                                                      |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FLOW_SNAPSHOT_ID`        | One visible owner references the supplied snapshot ID. The message gives a request for its current saved definition. This request does not inspect the historical snapshot.               |
| `AMBIGUOUS_FLOW_SNAPSHOT` | Multiple visible owners reference the supplied snapshot ID. Select the intended definition from the candidate IDs before another request.                                                 |
| `FLOW_NOT_FOUND`          | Neither the definition nor a current snapshot owner is visible. Check the ID, connected instance, and read access. This error does not prove absence or exclude a historical snapshot ID. |

The canonical-ID request is a read-only recovery step. It does not authorize editing, publishing, activation, or execution.
Snapshot-owner lookup failures remain errors. They do not become a claim that the flow is absent.

## Trigger search limits

A zero-match trigger search does not prove absence when a source read was incomplete. Check `data.is_complete` in `flow(action="list_triggers")` before drawing that conclusion.

- `pagination.total`, `data.v1_count`, and `data.v2_count` count returned matches, not platform-wide totals.
- `data.is_complete=false` and a warning mean the source page, a trigger batch, or the merged per-version result reached a limit. Top-level `truncation` identifies the affected source and gives continuation guidance; it is omitted when empty.
- The `sys_flow_record_trigger` entry includes `returned`, `total`, and `limit`. `total=null` means the total-count header was missing or unusable. A full page with an unknown total is conservatively marked incomplete.
- Trigger-source entries contain `batches` with the encoded query, fetched count, returned count, known total, limit, and continuation. Restart an affected batch with the direct `query` tool, then advance `offset`; the merged limit can omit rows within that batch.

For an incomplete record-trigger source page, continue its ordered query at the reported offset. Join the remaining IDs to V1 `remote_sys_id` and V2 `remote_trigger_id` or `sys_id`. Retain the original trigger-type and active filters. Direct queries require an explicit field projection and remain subject to query safety limits.

Trigger joins and flow-header snapshot lookups use batches of at most 50 unique IDs to avoid oversized URLs. Results are deduplicated by `sys_id`, with the existing merged row caps retained. These reads are not a transaction; concurrent platform changes can affect pagination. Completeness describes bounded trigger reads visible to the caller, not records hidden by ACLs or historical flow-header resolution.
