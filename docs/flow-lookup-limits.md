# Flow lookup limits

A zero-match trigger search does not prove absence when a source read was incomplete. Check `data.is_complete` in `flow(action="list_triggers")` before drawing that conclusion.

- `pagination.total`, `data.v1_count`, and `data.v2_count` count returned matches, not platform-wide totals.
- `data.is_complete=false` and a warning mean the source page, a trigger batch, or the merged per-version result reached a limit. Top-level `truncation` identifies the affected source and gives continuation guidance; it is omitted when empty.
- The `sys_flow_record_trigger` entry includes `returned`, `total`, and `limit`. `total=null` means the total-count header was missing or unusable. A full page with an unknown total is conservatively marked incomplete.
- Trigger-source entries contain `batches` with the encoded query, fetched count, returned count, known total, limit, and continuation. Restart an affected batch with the direct `query` tool, then advance `offset`; the merged limit can omit rows within that batch.

For an incomplete record-trigger source page, continue its ordered query at the reported offset. Join the remaining IDs to V1 `remote_sys_id` and V2 `remote_trigger_id` or `sys_id`. Retain the original trigger-type and active filters. Direct queries require an explicit field projection and remain subject to query safety limits.

Trigger joins and flow-header snapshot lookups use batches of at most 50 unique IDs to avoid oversized URLs. Results are deduplicated by `sys_id`, with the existing merged row caps retained. These reads are not a transaction; concurrent platform changes can affect pagination. Completeness describes bounded trigger reads visible to the caller, not records hidden by ACLs or historical flow-header resolution.
