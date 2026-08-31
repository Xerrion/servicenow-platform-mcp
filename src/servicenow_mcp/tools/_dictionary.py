"""Runtime discovery of script-bearing fields from ``sys_dictionary``.

Replaces the hardcoded artifact catalog with a registry that asks ServiceNow
which fields are script-bearing for a given table. The registry walks the
``sys_db_object.super_class`` chain so inherited fields (e.g. ``catalog_script_client``
inheriting from ``sys_script_client``) are discovered automatically.

Public surface mirrors the ``ChoiceRegistry`` pattern: one instance per server,
threaded through ``register_tools`` alongside ``ChoiceRegistry``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Final

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.metadata_cache import AsyncMetadataCache
from servicenow_mcp.telemetry import CacheName, HttpTelemetry
from servicenow_mcp.utils import ServiceNowQuery


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScriptField:
    """A field on a table that carries executable script or markup content.

    Attributes:
        name: The column name (``element`` in ``sys_dictionary``).
        internal_type: The dictionary ``internal_type`` (``script``, ``html``, ``xml``,
            ``css``, ``script_plain``, etc.).
        inherited_from: Table name where the field is defined; ``None`` when the
            field is declared directly on the queried table.
        via_heuristic: True iff this field was admitted by the html/xml
            attribute heuristic rather than by an unambiguous ``internal_type``.
    """

    name: str
    internal_type: str
    inherited_from: str | None
    via_heuristic: bool


@dataclass(frozen=True, slots=True)
class DictionaryField:
    """A raw dictionary entry: every field on a table, regardless of type.

    Used internally to drive the script-field filter; surfaced by
    ``DictionaryRegistry.get_all_fields`` for the ``describe`` tool.
    """

    name: str
    internal_type: str
    attributes: str
    inherited_from: str | None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)


# ---------------------------------------------------------------------------
# Type-detection policy
# ---------------------------------------------------------------------------


# Unambiguous script-bearing internal_types. Fields with these types are always
# admitted regardless of attributes.
UNAMBIGUOUS_SCRIPT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "script",
        "script_plain",
        "script_server",
        "script_client",
        "email_script",
        "html_script",
        "html_template",
        "css",
    }
)

# Ambiguous types - admitted only when the attribute heuristic fires.
_HEURISTIC_TYPES: Final[frozenset[str]] = frozenset({"html", "xml"})

# Element names that are excluded from script-field detection even when the
# type check would admit them. These hold data, not executable content.
EXCLUDED_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "translated_html",
        "template_value",
        "glide_var",
        "json",
        "conditions",
        "condition_string",
        "glide_action_list",
        "variable_conditions",
        "snapshot_template_value",
        "variable_template_value",
    }
)

# Attribute flags that flip an html/xml field into "script-bearing".
# Encoded inside the comma-separated ``attributes`` blob as ``key=value``;
# matched at token boundaries so substrings like ``my_tinymce_allow_all=true``
# never produce a false positive. Mirrors the boundary discipline used by
# ``tools._audit._NO_AUDIT_RE``.
_HEURISTIC_ATTR_FLAGS: Final[tuple[tuple[str, str], ...]] = (
    ("tinymce_allow_all", "true"),
    ("html_sanitize", "false"),
)

# Bound on super_class chain depth - generous ceiling above the deepest OOTB
# extension (4-5). Exceeding this logs a warning and returns the accumulated
# fields; protects against cycles and runaway recursion.
_MAX_CHAIN_DEPTH: Final[int] = 8

# Module-level template regex: ``${...}`` style ServiceNow template syntax.
_TEMPLATE_RE: Final[re.Pattern[str]] = re.compile(r"\$\{[^}]+\}")


def _parse_attributes(attributes: str) -> dict[str, str]:
    """Parse the comma-separated ``key=value`` ``attributes`` blob.

    Strips whitespace around both the token and the ``=`` separator. Keys
    are lowercased; values are stripped and lowercased (heuristic flags
    only compare against the literals ``true``/``false``). Splits on the
    first ``=`` only so values containing ``=`` survive. Duplicate keys
    follow last-wins semantics. Empty strings and tokens without ``=``
    are skipped.
    """
    if not attributes:
        return {}
    parsed: dict[str, str] = {}
    for raw_token in attributes.split(","):
        token = raw_token.strip()
        if not token or "=" not in token:
            continue
        key, _, value = token.partition("=")
        parsed[key.strip().lower()] = value.strip().lower()
    return parsed


def _attributes_admit_heuristic(attributes: str) -> bool:
    """Return True when ``attributes`` carries a heuristic-admission flag.

    Parses ``attributes`` as comma-separated ``key=value`` tokens (the
    ServiceNow convention) and admits only when one of the configured
    flag keys is present with the matching literal value. Substring
    matches like ``my_tinymce_allow_all=true`` are rejected.
    """
    parsed = _parse_attributes(attributes)
    if not parsed:
        return False
    return any(parsed.get(key) == value for key, value in _HEURISTIC_ATTR_FLAGS)


def _classify(field: DictionaryField) -> ScriptField | None:
    """Apply the type filter to a dictionary field.

    Returns a ``ScriptField`` when the field is admitted, ``None`` otherwise.
    Exclusion list takes precedence over both the unambiguous-type list and the
    attribute heuristic.
    """
    if field.name in EXCLUDED_ELEMENTS:
        return None

    if field.internal_type in UNAMBIGUOUS_SCRIPT_TYPES:
        return ScriptField(
            name=field.name,
            internal_type=field.internal_type,
            inherited_from=field.inherited_from,
            via_heuristic=False,
        )

    if field.internal_type in _HEURISTIC_TYPES and _attributes_admit_heuristic(field.attributes):
        return ScriptField(
            name=field.name,
            internal_type=field.internal_type,
            inherited_from=field.inherited_from,
            via_heuristic=True,
        )

    return None


def looks_like_template(content: str) -> bool:
    """Return True when ``content`` contains ``${...}`` template syntax.

    Used at read/write time as a content-level corroboration of the dictionary
    heuristic. The dictionary module itself never inspects record content; this
    helper exists so callers handling a particular record can widen the
    script-field set for that record only.
    """
    if not content:
        return False
    try:
        return bool(_TEMPLATE_RE.search(content))
    except (TypeError, re.error):
        return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class DictionaryRegistry:
    """Lazy-loaded cache of script-bearing fields per table.

    One instance per server. Threads through ``register_tools`` alongside
    ``ChoiceRegistry``. Cached values expire after the configured metadata TTL;
    explicit flushing remains available.
    """

    _settings: Settings
    _auth_provider: BasicAuthProvider

    def __init__(
        self,
        settings: Settings,
        auth_provider: BasicAuthProvider,
        client_factory: ServiceNowClientProvider | None = None,
        telemetry: HttpTelemetry | None = None,
    ) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
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
        """Return the script-bearing fields for ``table``.

        Walks the ``super_class`` chain child-first, applies the type filter,
        and dedupes by element name (child wins on collision). Result is
        cached until the configured metadata TTL expires.
        """

        async def load() -> list[ScriptField]:
            all_fields = await self.get_all_fields(table)
            ordered: dict[str, ScriptField] = {}
            for field in all_fields:
                if field.name in ordered:
                    continue
                classified = _classify(field)
                if classified is not None:
                    ordered[field.name] = classified
            return list(ordered.values())

        return list(await self._script_cache.get_or_load(table, load))

    async def get_all_fields(self, table: str) -> list[DictionaryField]:
        """Return every dictionary field for ``table`` plus its super_class chain.

        Child fields come before parent fields. Used by ``describe`` for
        diagnostics and by ``get_script_fields`` as its input stream.
        """

        async def load() -> list[DictionaryField]:
            chain = await self.get_chain(table)
            collected: dict[str, DictionaryField] = {}
            async with self._client_factory() as client:
                for level, current in enumerate(chain):
                    inherited_from = None if level == 0 else current
                    rows = await self._fetch_dictionary_rows(client, current)
                    for row in rows:
                        element = str(row.get("element") or "").strip()
                        if not element or element in collected:
                            continue
                        collected[element] = DictionaryField(
                            name=element,
                            internal_type=self._dictionary_value(
                                row.get("internal_type.name") or row.get("internal_type")
                            ),
                            attributes=str(row.get("attributes") or ""),
                            inherited_from=inherited_from,
                            metadata=dict(row),
                        )
            return list(collected.values())

        return list(await self._all_cache.get_or_load(table, load))

    async def get_chain(self, table: str) -> list[str]:
        """Return the resolved super_class chain for ``table`` (child-first)."""

        async def load() -> list[str]:
            async with self._client_factory() as client:
                return await self._resolve_chain(client, table)

        return list(await self._chain_cache.get_or_load(table, load))

    def flush(self, table: str | None = None) -> None:
        """Clear cached entries.

        ``flush()`` clears everything; ``flush('incident')`` clears the entry
        for ``incident`` only.
        """
        if table is None:
            self._script_cache.invalidate()
            self._all_cache.invalidate()
            self._chain_cache.invalidate()
            return
        self._script_cache.invalidate(table)
        self._all_cache.invalidate(table)
        self._chain_cache.invalidate(table)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_chain(self, client: ServiceNowClient, table: str) -> list[str]:
        """Resolve the super_class chain for ``table`` (child-first, parent last).

        Bounded by ``_MAX_CHAIN_DEPTH``; cycles are detected via a visited set.
        Tables that don't exist in ``sys_db_object`` yield a single-element
        chain containing just the queried table (the dictionary query will then
        return zero rows, which is the correct empty answer).
        """
        chain: list[str] = []
        visited: set[str] = set()
        current = table
        for _ in range(_MAX_CHAIN_DEPTH):
            if current in visited:
                logger.warning(
                    "super_class cycle detected at table=%s; truncating chain at %s",
                    current,
                    chain,
                )
                break
            visited.add(current)
            chain.append(current)
            parent = await self._lookup_super_class(client, current)
            if not parent:
                break
            current = parent
        else:
            logger.warning(
                "super_class chain for table=%s exceeded depth %d; truncated at %s",
                table,
                _MAX_CHAIN_DEPTH,
                chain,
            )

        return list(chain)

    async def _lookup_super_class(self, client: ServiceNowClient, table: str) -> str:
        """Resolve the parent table name via ``sys_db_object.super_class``.

        Returns an empty string when there is no parent (root table or table
        not registered in ``sys_db_object``). ``super_class`` is a reference
        field; the platform returns the parent's ``name`` under display values
        and a sys_id under value mode - we request display values for the
        single field we care about.
        """
        result = await client.query_records(
            table="sys_db_object",
            query=ServiceNowQuery().equals("name", table).build(),
            fields=["super_class.name"],
            limit=1,
        )
        records = result.get("records", [])
        if not records:
            return ""
        super_class: Any = records[0].get("super_class.name") or records[0].get("super_class") or ""
        if isinstance(super_class, dict):
            super_class = super_class.get("display_value") or super_class.get("value") or ""
        return str(super_class).strip()

    async def _fetch_dictionary_rows(self, client: ServiceNowClient, table: str) -> list[dict[str, Any]]:
        """Fetch the active, named-element rows from ``sys_dictionary`` for ``table``."""
        query = ServiceNowQuery().equals("name", table).is_not_empty("element").equals("active", "true").build()
        result = await client.query_records(
            table="sys_dictionary",
            query=query,
            fields=None,
            limit=1000,
        )
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
        type_rows: Any = type_result.get("records") or []
        type_by_element = {
            str(row.get("element") or ""): row.get("internal_type.name")
            for row in type_rows
            if isinstance(row, dict) and row.get("element")
        }
        for row in rows:
            row["internal_type.name"] = type_by_element.get(str(row.get("element") or ""), "")
        return rows

    @staticmethod
    def _dictionary_value(value: Any) -> str:
        """Normalize dictionary references to their stable display value."""
        if isinstance(value, dict):
            value = value.get("display_value") or ""
        return str(value or "").strip().lower().replace(" ", "_")
