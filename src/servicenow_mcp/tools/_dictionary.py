"""Retrieve and cache normalized ServiceNow dictionary metadata."""

from typing import Any

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.metadata_cache import AsyncMetadataCache
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.telemetry import CacheName, HttpTelemetry
from servicenow_mcp.tools._dictionary_classification import (
    EXCLUDED_ELEMENTS,
    UNAMBIGUOUS_SCRIPT_TYPES,
    classify_script_field,
    looks_like_template,
)
from servicenow_mcp.tools._dictionary_classification import (
    attributes_admit_heuristic as _attributes_admit_heuristic,
)
from servicenow_mcp.tools._dictionary_inheritance import resolve_chain
from servicenow_mcp.tools._dictionary_models import DictionaryField, ScriptField
from servicenow_mcp.validation import validate_identifier


class DictionaryRegistry:
    """Retrieve and cache dictionary fields and table inheritance chains."""

    _client_factory: ServiceNowClientProvider
    _script_cache: AsyncMetadataCache[str, list[ScriptField]]
    _all_cache: AsyncMetadataCache[str, list[DictionaryField]]
    _chain_cache: AsyncMetadataCache[str, list[str]]

    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
        telemetry: HttpTelemetry | None = None,
    ) -> None:
        self._client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
        ttl = settings.metadata_cache_ttl_seconds
        self._script_cache = AsyncMetadataCache[str, list[ScriptField]](
            name=CacheName.DICTIONARY_SCRIPT_FIELDS, ttl_seconds=ttl, telemetry=telemetry
        )
        self._all_cache = AsyncMetadataCache[str, list[DictionaryField]](
            name=CacheName.DICTIONARY_FIELDS, ttl_seconds=ttl, telemetry=telemetry
        )
        self._chain_cache = AsyncMetadataCache[str, list[str]](
            name=CacheName.DICTIONARY_CHAINS, ttl_seconds=ttl, telemetry=telemetry
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
        """Resolve only named fields child first, without populating the broad cache."""
        validate_identifier(table)
        for name in names:
            validate_identifier(name)
        if not names:
            return []

        remaining = set(names)
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

    async def get_chain(self, table: str) -> list[str]:
        """Return the cached super-class chain child first."""

        async def load() -> list[str]:
            async with self._client_factory() as client:
                return await resolve_chain(client, table)

        return list(await self._chain_cache.get_or_load(table, load))

    def flush(self, table: str | None = None) -> None:
        """Clear all cached metadata, or only metadata for one table."""
        caches = (self._script_cache, self._all_cache, self._chain_cache)
        for cache in caches:
            cache.invalidate() if table is None else cache.invalidate(table)


def _dictionary_field(row: dict[str, Any], inherited_from: str | None) -> DictionaryField:
    return DictionaryField(
        name=str(row.get("element") or "").strip(),
        internal_type=_dictionary_value(row.get("internal_type.name") or row.get("internal_type")),
        attributes=str(row.get("attributes") or ""),
        inherited_from=inherited_from,
        metadata=dict(row),
    )


async def _fetch_dictionary_rows(client: ServiceNowClient, table: str) -> list[dict[str, Any]]:
    query = ServiceNowQuery().equals("name", table).is_not_empty("element").equals("active", "true").build()
    result = await client.query_records(table="sys_dictionary", query=query, fields=None, limit=1000)
    records: Any = result.get("records") or []
    rows = list(records) if isinstance(records, list) else []
    if not any(isinstance(row.get("internal_type"), dict) and not row.get("internal_type.name") for row in rows):
        return rows

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
    "EXCLUDED_ELEMENTS",
    "UNAMBIGUOUS_SCRIPT_TYPES",
    "DictionaryField",
    "DictionaryRegistry",
    "ScriptField",
    "_attributes_admit_heuristic",
    "looks_like_template",
]
