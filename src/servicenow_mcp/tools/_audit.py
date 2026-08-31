"""Runtime resolution of ServiceNow audit posture for tables and fields.

The registry composes :class:`DictionaryRegistry` for the ``super_class``
chain walk and adds two audit-specific lookups:

* ``sys_db_object.sys_audit`` resolved child-first along the chain
  (table-level audit switch).
* ``sys_dictionary.audit`` + ``sys_dictionary.attributes`` resolved
  child-first along the chain for a given field (field-level audit
  with the ``no_audit`` attribute veto applied last).

``sys_audit`` row counts (the live signal) are deliberately NOT cached;
configuration is.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Final

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.metadata_cache import AsyncMetadataCache
from servicenow_mcp.telemetry import CacheName, HttpTelemetry
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.utils import ServiceNowQuery


logger = logging.getLogger(__name__)


# ``no_audit`` is encoded inside the comma-separated ``attributes`` blob.
# Match it at a comma boundary so substrings like ``my_no_audit=true``
# never produce a false positive. The right boundary tolerates trailing
# whitespace before EOL (``foo, no_audit = true `` must match).
_NO_AUDIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|,)\s*no_audit\s*=\s*true(?:\s*,|\s*$)",  # NOSONAR(S5850) - alternations are grouped; precedence explicit.
    re.IGNORECASE,
)


def attribute_has_no_audit(attributes: str) -> bool:
    """Return True when ``attributes`` contains a ``no_audit=true`` flag."""
    if not attributes:
        return False
    return bool(_NO_AUDIT_RE.search(attributes))


def _empty_field_audit() -> FieldAudit:
    """Return the canonical ``FieldAudit`` used when no row exists in the chain."""
    return FieldAudit(
        field_audit=None,
        raw_field_audit=None,
        inherited_from=None,
        has_row=False,
        no_audit_attribute=False,
        attributes_raw="",
    )


def _index_dict_rows_by_table(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    """Index ``sys_dictionary`` rows by ``name`` keeping only ``element == field``.

    First row per table wins; subsequent rows for the same table are ignored
    (the caller relies on this for child-first chain resolution).
    """
    rows_by_table: dict[str, dict[str, Any]] = {}
    for row in rows:
        element = str(row.get("element") or "").strip()
        if element != field:
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in rows_by_table:
            continue
        rows_by_table[name] = row
    return rows_by_table


def _pick_chain_match(
    chain: list[str],
    rows_by_table: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """Return the first ``(table, row)`` along ``chain`` (child-first) that has a row."""
    for current in chain:
        row = rows_by_table.get(current)
        if row is not None:
            return current, row
    return None, None


def _build_field_audit(match_row: dict[str, Any], match_table: str, queried_table: str) -> FieldAudit:
    """Assemble a :class:`FieldAudit` from the resolved ``sys_dictionary`` row.

    Applies the ``no_audit=true`` attribute veto over the raw ``audit`` column.
    """
    attributes_raw = str(match_row.get("attributes") or "")
    no_audit = attribute_has_no_audit(attributes_raw)
    raw_field_audit = str(match_row.get("audit") or "").strip().lower() == "true"
    # ``no_audit=true`` in the attributes blob is an absolute veto over
    # the boolean audit column (see AGENTS.md "Audit Inspection").
    field_audit = False if no_audit else raw_field_audit
    inherited_from = match_table if match_table != queried_table else None
    return FieldAudit(
        field_audit=field_audit,
        raw_field_audit=raw_field_audit,
        inherited_from=inherited_from,
        has_row=True,
        no_audit_attribute=no_audit,
        attributes_raw=attributes_raw,
    )


@dataclass(frozen=True, slots=True)
class FieldAudit:
    """Resolved audit posture for a single ``(table, field)`` pair.

    Attributes:
        field_audit: Post-veto boolean: the ``sys_dictionary.audit`` row
            value, forced to ``False`` when ``no_audit_attribute`` is true
            (the ``no_audit=true`` attribute is an absolute veto over the
            audit column). ``None`` when no row exists in the chain.
        raw_field_audit: The unvetoed ``sys_dictionary.audit`` boolean
            exactly as resolved from the chain. Exposed alongside
            ``field_audit`` so callers can see when the attribute veto
            overrode the column. ``None`` when no row exists.
        inherited_from: Table name in the chain that supplied
            ``field_audit``. ``None`` when the queried table itself
            declared the row, or when no row exists.
        has_row: Whether any row was found in the chain.
        no_audit_attribute: Whether the resolved row's ``attributes``
            string contains ``no_audit=true``.
        attributes_raw: The raw ``attributes`` string from the resolved
            row (empty when no row exists).
    """

    field_audit: bool | None
    raw_field_audit: bool | None
    inherited_from: str | None
    has_row: bool
    no_audit_attribute: bool
    attributes_raw: str


class AuditRegistry:
    """Lazy-loaded cache of audit configuration per table and (table, field).

    One instance per server; composes a shared :class:`DictionaryRegistry`
    so the ``super_class`` chain walk is performed exactly once across
    both registries.
    """

    _DICT_FIELDS: ClassVar[list[str]] = ["name", "element", "audit", "attributes"]
    _TABLE_FIELDS: ClassVar[list[str]] = ["name", "sys_audit"]

    _settings: Settings
    _auth_provider: BasicAuthProvider
    _dictionary: DictionaryRegistry

    def __init__(
        self,
        settings: Settings,
        auth_provider: BasicAuthProvider,
        dictionary: DictionaryRegistry,
        client_factory: ServiceNowClientProvider | None = None,
        telemetry: HttpTelemetry | None = None,
    ) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._dictionary = dictionary
        self._client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))
        ttl = settings.metadata_cache_ttl_seconds
        self._table_audit_cache = AsyncMetadataCache[str, bool | None](
            name=CacheName.AUDIT_TABLE_CONFIG, ttl_seconds=ttl, telemetry=telemetry
        )
        self._field_audit_cache = AsyncMetadataCache[tuple[str, str], FieldAudit](
            name=CacheName.AUDIT_FIELD_CONFIG, ttl_seconds=ttl, telemetry=telemetry
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_chain(self, table: str) -> list[str]:
        """Return the resolved super_class chain (child-first)."""
        return await self._dictionary.get_chain(table)

    async def get_table_audit(self, table: str) -> bool | None:
        """Return the resolved table-level audit flag for ``table``.

        Walks the ``super_class`` chain child-first; the first non-empty
        ``sys_audit`` value wins. Returns ``None`` when no row exists
        for any table in the chain (table not registered in
        ``sys_db_object``).
        """

        async def load() -> bool | None:
            chain = await self.get_chain(table)
            if not chain:
                return None

            async with self._client_factory() as client:
                rows = await self._fetch_table_audit_rows(client, chain)

            by_name: dict[str, str] = {}
            for row in rows:
                name = str(row.get("name") or "").strip()
                if name:
                    by_name[name] = str(row.get("sys_audit") or "").strip().lower()

            for current in chain:
                value = by_name.get(current)
                if value is not None and value != "":
                    return value == "true"
            return None

        return await self._table_audit_cache.get_or_load(table, load)

    async def get_field_audit(self, table: str, field: str) -> FieldAudit:
        """Return the resolved field-level audit posture for ``(table, field)``.

        Walks the chain child-first; the first ``sys_dictionary`` row
        whose ``element`` matches wins. Returns a :class:`FieldAudit`
        whose ``field_audit`` is ``None`` when no row exists anywhere
        in the chain.
        """
        cache_key = (table, field)

        async def load() -> FieldAudit:
            chain = await self.get_chain(table)
            if not chain:
                return _empty_field_audit()

            rows = await self._fetch_field_audit_rows(chain, field)
            rows_by_table = _index_dict_rows_by_table(rows, field)
            match_table, match_row = _pick_chain_match(chain, rows_by_table)
            if match_row is None or match_table is None:
                return _empty_field_audit()
            return _build_field_audit(match_row, match_table, table)

        return await self._field_audit_cache.get_or_load(cache_key, load)

    def flush(self, table: str | None = None) -> None:
        """Clear cached entries.

        ``flush()`` clears everything; ``flush('incident')`` clears the
        entry for ``incident`` only plus any ``(incident, *)`` field
        cache entries.
        """
        if table is None:
            self._table_audit_cache.invalidate()
            self._field_audit_cache.invalidate()
            return
        self._table_audit_cache.invalidate(table)
        self._field_audit_cache.invalidate_where(lambda key: key[0] == table)
        # ``DictionaryRegistry`` owns the super_class chain cache; that is
        # structural metadata (sys_db_object.super_class) which changes only
        # on schema reorganisation, not on audit-config edits. Leave it alone -
        # callers needing a full schema refresh should flush the dictionary
        # registry directly.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_table_audit_rows(
        self,
        client: ServiceNowClient,
        chain: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch ``sys_db_object`` rows for every table in ``chain`` in one call."""
        query = ServiceNowQuery().in_list("name", chain).build()
        result = await client.query_records(
            table="sys_db_object",
            query=query,
            fields=self._TABLE_FIELDS,
            limit=max(len(chain), 1),
        )
        records: Any = result.get("records") or []
        return list(records) if isinstance(records, list) else []

    async def _fetch_field_audit_rows(
        self,
        chain: list[str],
        field: str,
    ) -> list[dict[str, Any]]:
        """Fetch ``sys_dictionary`` rows for ``element=field`` across the chain.

        The resolved field configuration cache owns repeat-call suppression.
        """
        query = ServiceNowQuery().in_list("name", chain).equals("element", field).equals("active", "true").build()
        async with self._client_factory() as client:
            result = await client.query_records(
                table="sys_dictionary",
                query=query,
                fields=self._DICT_FIELDS,
                limit=max(len(chain), 1),
            )
        records: Any = result.get("records") or []
        return list(records) if isinstance(records, list) else []

    async def get_table_field_rows(self, table: str) -> list[dict[str, Any]]:
        """Fetch every ``sys_dictionary`` row for ``table`` plus its super_class chain.

        Used by ``check_table`` to assemble the field-by-field posture
        report. Returns rows with ``name``, ``element``, ``audit``, and
        ``attributes`` populated.
        """
        chain = await self.get_chain(table)
        if not chain:
            return []
        query = ServiceNowQuery().in_list("name", chain).is_not_empty("element").equals("active", "true").build()
        async with self._client_factory() as client:
            result = await client.query_records(
                table="sys_dictionary",
                query=query,
                fields=self._DICT_FIELDS,
                limit=10000,
            )
        records: Any = result.get("records") or []
        return list(records) if isinstance(records, list) else []
