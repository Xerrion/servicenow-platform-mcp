"""Retrieve and cache normalized ServiceNow dictionary metadata."""

from typing import Any, Final

import httpx2

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.platform.metadata._dictionary_classification import classify_script_field
from servicenow_mcp.platform.metadata._dictionary_inheritance import resolve_chain
from servicenow_mcp.platform.metadata._dictionary_models import (
    DictionaryField,
    FieldLookup,
    FieldPathResolution,
    ScriptField,
    TableInheritance,
)
from servicenow_mcp.platform.metadata.cache import AsyncMetadataCache
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.telemetry import CacheName, HttpTelemetry, timeout_phase
from servicenow_mcp.validation import validate_identifier


_QUERY_FIELD_BATCH_SIZE: Final[int] = 100
_QUERY_METADATA_ROW_LIMIT: Final[int] = 1000
_MAX_REFERENCE_DEPTH: Final[int] = 8


class DictionaryRegistry:
    """Retrieve and cache dictionary fields and table inheritance chains."""

    _client_factory: ServiceNowClientProvider
    _script_cache: AsyncMetadataCache[str, list[ScriptField]]
    _all_cache: AsyncMetadataCache[str, list[DictionaryField]]
    _selected_cache: AsyncMetadataCache[tuple[str, tuple[str, ...]], list[DictionaryField]]
    _chain_cache: AsyncMetadataCache[str, TableInheritance]
    _query_fields_cache: AsyncMetadataCache[tuple[str, tuple[str, ...]], FieldLookup]

    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
        telemetry: HttpTelemetry | None = None,
    ) -> None:
        self._client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
        ttl = settings.metadata_cache_ttl_seconds
        self._script_cache = AsyncMetadataCache(
            name=CacheName.DICTIONARY_SCRIPT_FIELDS, ttl_seconds=ttl, telemetry=telemetry
        )
        self._all_cache = AsyncMetadataCache(name=CacheName.DICTIONARY_FIELDS, ttl_seconds=ttl, telemetry=telemetry)
        self._selected_cache = AsyncMetadataCache(
            name=CacheName.DICTIONARY_FIELDS, ttl_seconds=ttl, telemetry=telemetry
        )
        self._chain_cache = AsyncMetadataCache(name=CacheName.DICTIONARY_CHAINS, ttl_seconds=ttl, telemetry=telemetry)
        self._query_fields_cache = AsyncMetadataCache(
            name=CacheName.DICTIONARY_FIELDS, ttl_seconds=ttl, telemetry=telemetry
        )

    async def get_script_fields(self, table: str) -> list[ScriptField]:
        """Return cached script-bearing fields, with child declarations first."""

        async def load() -> list[ScriptField]:
            ordered: dict[str, ScriptField] = {}
            for field in await self.get_all_fields(table):
                classified = classify_script_field(field)
                if field.name not in ordered and classified is not None:
                    ordered[field.name] = classified
            return list(ordered.values())

        return list(await self._script_cache.get_or_load(table, load))

    async def get_all_fields(self, table: str) -> list[DictionaryField]:
        """Return cached dictionary fields from a table and its ancestors."""

        async def load() -> list[DictionaryField]:
            collected: dict[str, DictionaryField] = {}
            chain = await self.get_chain(table)
            async with self._client_factory() as client:
                for level, current in enumerate(chain):
                    for row in await _fetch_dictionary_rows(client, current):
                        element = str(row.get("element") or "").strip()
                        if not element or element in collected:
                            continue
                        collected[element] = _dictionary_field(row, None if level == 0 else current)
            return list(collected.values())

        return list(await self._all_cache.get_or_load(table, load))

    async def get_fields(self, table: str, names: list[str]) -> list[DictionaryField]:
        """Resolve and cache only named fields child first."""
        validate_identifier(table)
        for name in names:
            validate_identifier(name)
        if not names:
            return []

        key = (table, tuple(sorted(set(names))))

        async def load() -> list[DictionaryField]:
            remaining = set(key[1])
            fields: list[DictionaryField] = []
            chain = await self.get_chain(table)
            async with self._client_factory() as client:
                for level, current in enumerate(chain):
                    pending = sorted(remaining)
                    for start in range(0, len(pending), 100):
                        batch = pending[start : start + 100]
                        result = await client.query_records(
                            table="sys_dictionary",
                            query=ServiceNowQuery()
                            .equals("name", current)
                            .in_list("element", batch)
                            .equals("active", "true")
                            .build(),
                            fields=["element", "internal_type.name"],
                            limit=len(batch),
                        )
                        for row in result.get("records", []):
                            name = str(row.get("element") or "").strip()
                            if name not in remaining or name not in batch:
                                continue
                            fields.append(_dictionary_field(row, None if level == 0 else current))
                            remaining.remove(name)
                    if not remaining:
                        break
            return fields

        return list(await self._selected_cache.get_or_load(key, load))

    async def get_chain(self, table: str) -> list[str]:
        """Return the cached super-class chain child first."""
        return list((await self.get_inheritance(table)).tables)

    async def get_inheritance(self, table: str) -> TableInheritance:
        """Return observed ancestry without treating missing metadata as a root."""
        validate_identifier(table)

        async def load() -> TableInheritance:
            async with self._client_factory() as client:
                return await resolve_chain(client, table)

        return (await self._chain_cache.get_or_load(table, load)).model_copy(deep=True)

    async def get_query_fields(self, table: str, names: list[str]) -> FieldLookup:
        """Discover selected schema fields, including inactive definitions."""
        validate_identifier(table)
        check_table_access(table)
        for name in names:
            validate_identifier(name)
            if "." in name:
                raise ValueError("Query field lookup requires direct column names.")
        if not names:
            return FieldLookup(fields=[], inheritance=TableInheritance(tables=[], complete=False))
        key = (table, tuple(sorted(set(names))))

        async def load() -> FieldLookup:
            inheritance = await self.get_inheritance(table)
            remaining = set(key[1])
            fields: list[DictionaryField] = []
            limitations: list[str] = []
            async with self._client_factory() as client:
                for level, current in enumerate(inheritance.tables):
                    pending = sorted(remaining)
                    for start in range(0, len(pending), _QUERY_FIELD_BATCH_SIZE):
                        batch = pending[start : start + _QUERY_FIELD_BATCH_SIZE]
                        rows, incomplete = await _fetch_query_dictionary_rows(client, current, batch)
                        if incomplete:
                            limitations.append(
                                f"Dictionary rows for '{current}' were incomplete or reached the read bound."
                            )
                        for row in rows:
                            name = row.get("element")
                            if name not in remaining or name not in batch:
                                continue
                            fields.append(_dictionary_field(row, None if level == 0 else current))
                            remaining.remove(name)
                    if not remaining:
                        break
            return FieldLookup(fields=fields, inheritance=inheritance, limitations=limitations)

        return (await self._query_fields_cache.get_or_load(key, load)).model_copy(deep=True)

    async def get_query_field_paths(self, table: str, paths: list[str]) -> list[FieldPathResolution]:
        """Resolve bounded reference paths with positive evidence or explicit uncertainty."""
        validate_identifier(table)
        for path in paths:
            validate_identifier(path)
        ordered_paths = list(dict.fromkeys(paths))
        pending = [(path, table, path.split("."), 0) for path in ordered_paths]
        resolved: dict[str, FieldPathResolution] = {}
        for _ in range(_MAX_REFERENCE_DEPTH):
            if not pending:
                break
            groups: dict[str, list[tuple[str, list[str], int]]] = {}
            for path, current, parts, index in pending:
                groups.setdefault(current, []).append((path, parts, index))
            pending = []
            for current, entries in groups.items():
                try:
                    lookup = await self.get_query_fields(current, [parts[index] for _, parts, index in entries])
                except (httpx2.HTTPError, ServiceNowMCPError) as exc:
                    reason = (
                        f"HTTP timeout ({timeout_phase(exc)}) during metadata lookup"
                        if isinstance(exc, httpx2.TimeoutException)
                        else "metadata was unavailable or inaccessible"
                    )
                    for path, _, _ in entries:
                        resolved[path] = FieldPathResolution(path=path, status="unverified", reason=reason)
                    continue
                by_name = {field.name: field for field in lookup.fields}
                for path, parts, index in entries:
                    field = by_name.get(parts[index])
                    reason = None
                    if lookup.limitations:
                        reason = " ".join(lookup.limitations)
                    elif field is None:
                        reason = f"'{parts[index]}' was not visible in dictionary metadata for '{current}'"
                        if not lookup.inheritance.complete:
                            reason += f". Inheritance was incomplete ({lookup.inheritance.reason})"
                    elif index == len(parts) - 1:
                        resolved[path] = FieldPathResolution(path=path, status="valid", field=field)
                        continue
                    else:
                        reference = field.metadata.get("reference")
                        if isinstance(reference, dict):
                            reference = reference.get("value")
                        if field.internal_type != "reference":
                            reason = f"'{parts[index]}' has no supported static reference type"
                        elif not isinstance(reference, str) or not reference:
                            reason = f"the reference target for '{parts[index]}' was unavailable"
                        else:
                            try:
                                validate_identifier(reference)
                            except ValueError:
                                reason = f"the reference target for '{parts[index]}' could not be resolved"
                            else:
                                pending.append((path, reference, parts, index + 1))
                                continue
                    resolved[path] = FieldPathResolution(path=path, status="unverified", field=field, reason=reason)
        for path, _, _, _ in pending:
            resolved[path] = FieldPathResolution(
                path=path, status="unverified", reason="reference traversal reached its depth bound"
            )
        return [resolved[path] for path in ordered_paths]

    def flush(self, table: str | None = None) -> None:
        """Invalidate selected table metadata and dependent query discovery."""
        caches = (self._script_cache, self._all_cache)
        for cache in caches:
            cache.invalidate() if table is None else cache.invalidate(table)
        if table is None:
            self._selected_cache.invalidate()
        else:
            self._selected_cache.invalidate_where(lambda key: key[0] == table)
        # Query lookups can depend on ancestors and reference targets from other tables.
        self._query_fields_cache.invalidate()
        self._chain_cache.invalidate()


def _dictionary_field(row: dict[str, Any], inherited_from: str | None) -> DictionaryField:
    return DictionaryField(
        name=str(row.get("element") or "").strip(),
        internal_type=_dictionary_value(row.get("internal_type.name") or row.get("internal_type")),
        attributes=str(row.get("attributes") or ""),
        inherited_from=inherited_from,
        metadata=dict(row),
    )


async def _fetch_query_dictionary_rows(
    client: ServiceNowClient, table: str, names: list[str]
) -> tuple[list[dict[str, Any]], bool]:
    """Read selected definitions with stable pagination and a bounded probe."""
    query = ServiceNowQuery().equals("name", table).in_list("element", names).build()
    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = len(names) + 1
    while offset < _QUERY_METADATA_ROW_LIMIT:
        limit = min(page_size, _QUERY_METADATA_ROW_LIMIT - offset)
        result = await client.query_records(
            table="sys_dictionary",
            query=query,
            fields=["element", "internal_type.name", "reference"],
            limit=limit,
            offset=offset,
            order_by="sys_id",
        )
        page = result["records"]
        if not isinstance(page, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("element"), str) or row["element"] not in names
            for row in page
        ):
            return rows, True
        if len(page) > limit:
            return [*rows, *page[:limit]], True
        rows.extend(page)
        total = result.get("count", 0)
        if len(page) < limit and total <= offset + limit:
            return rows, False
        offset += limit
    return rows, True


async def _fetch_dictionary_rows(client: ServiceNowClient, table: str) -> list[dict[str, Any]]:
    query = ServiceNowQuery().equals("name", table).is_not_empty("element").equals("active", "true").build()
    result = await client.query_records(table="sys_dictionary", query=query, fields=None, limit=1000)
    records: Any = result.get("records") or []
    rows = list(records) if isinstance(records, list) else []
    if any(isinstance(row.get("internal_type"), dict) and not row.get("internal_type.name") for row in rows):
        type_result = await client.query_records(
            table="sys_dictionary",
            query=query,
            fields=["element", "internal_type.name"],
            limit=1000,
        )
        type_by_element = {
            str(row.get("element") or ""): row.get("internal_type.name")
            for row in type_result.get("records") or []
            if isinstance(row, dict) and row.get("element")
        }
        for row in rows:
            row["internal_type.name"] = type_by_element.get(str(row.get("element") or ""), "")
    return rows


def _dictionary_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("display_value") or ""
    return str(value or "").strip().lower().replace(" ", "_")


__all__ = [
    "DictionaryField",
    "DictionaryRegistry",
    "ScriptField",
]
