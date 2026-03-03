
## [2026-03-03] Task 3: Introspection Tools Implementation

### Tool Pattern Variations
- **ServiceNowQuery builder API**: Constructor takes NO arguments - use `.raw(query_string)` to pass user queries
- **Ordering in queries**: Use `.order_by(field, descending=True)` on ServiceNowQuery, NOT `direction` param on client.query_records()
- **Display values**: Pass `display_values=True` to client methods when you need reference field display values (e.g., step_config in atf_get_test)
- **Parallel fetching**: Use `asyncio.gather()` for independent queries (test + steps in atf_get_test)

### Query Complexity Insights
- **Multi-table enrichment**: atf_list_suites pattern - fetch parent records, then enrich each with aggregate count from child table
- **Conditional queries**: atf_get_results validates exactly one ID provided, then branches to different tables/fields based on input
- **Query construction**: Always use `.build()` at the end of ServiceNowQuery chain to get encoded string
- **Empty query handling**: Use `ServiceNowQuery().raw(query).build() if query else ""` pattern for optional user queries

### Field Handling Notes
- **Default fields**: Store as const string, split by comma when passing to client.query_records(fields=...)
- **Field lists per table type**: Test results vs suite results have completely different field schemas - maintain separate field lists
- **Masking application**: Apply mask_sensitive_fields AFTER fetching, BEFORE returning to user
- **Display value fields**: For reference fields that need human-readable names, use display_values=True on query

### Error Handling Patterns
- **Input validation**: Check mutually exclusive params BEFORE any API calls - return error envelope immediately
- **Table access checks**: Call check_table_access() for EVERY table touched, even in multi-table tools
- **safe_tool_call wrapper**: NEVER skip this - it's the contract between tools and MCP framework

### Import Corrections
- `write_blocked_reason` is in `servicenow_mcp.policy`, NOT `servicenow_mcp.utils`
- Always import from correct module to avoid LSP errors

### Async Patterns Observed
- Parallel queries with asyncio.gather() reduce latency for independent operations
- Sequential enrichment (fetch suites, then count members) is necessary when second query depends on first results
- All client methods are async - no sync fallback needed
