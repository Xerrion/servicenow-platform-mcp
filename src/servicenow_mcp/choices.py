"""Lazy-loaded choice list registry backed by sys_choice."""

import asyncio
import logging
from typing import Any, ClassVar

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings

logger = logging.getLogger(__name__)


class ChoiceRegistry:
    """Lazy-loaded choice list cache backed by sys_choice.

    Fetches real choice values from the instance on first access and caches
    them for the server's lifetime. Falls back to OOTB defaults on failure.
    """

    # OOTB defaults - consolidated from all domain tools.
    # Keys are (table_name, field_name), values are {label: value} dicts.
    _DEFAULTS: ClassVar[dict[tuple[str, str], dict[str, str]]] = {
        ("incident", "state"): {
            "open": "1",
            "in_progress": "2",
            "on_hold": "3",
            "resolved": "6",
            "closed": "7",
            "canceled": "8",
        },
        ("change_request", "state"): {
            "new": "-5",
            "assess": "-4",
            "authorize": "-3",
            "scheduled": "-2",
            "implement": "-1",
            "review": "0",
            "closed": "3",
            "canceled": "4",
        },
        ("problem", "state"): {
            "new": "1",
            "in_progress": "2",
            "known_error": "3",
            "root_cause_analysis": "4",
            "fix_in_progress": "5",
            "resolved": "6",
            "closed": "7",
        },
        ("cmdb_ci", "operational_status"): {
            "operational": "1",
            "non_operational": "2",
            "repair_in_progress": "3",
            "dr_standby": "4",
            "ready": "5",
            "retired": "6",
        },
        ("sc_request", "state"): {
            "open": "1",
            "in_progress": "2",
            "on_hold": "3",
            "closed_complete": "4",
            "closed_incomplete": "7",
            "closed_cancelled": "8",
        },
        ("sc_req_item", "state"): {
            "open": "1",
            "in_progress": "2",
            "on_hold": "3",
            "closed_complete": "4",
            "closed_incomplete": "7",
            "closed_cancelled": "8",
        },
    }

    def __init__(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        self._settings = settings
        self._auth_provider = auth_provider
        self._cache: dict[tuple[str, str], dict[str, str]] = {}
        self._fetched = False
        self._lock = asyncio.Lock()

    async def resolve(self, table: str, field: str, label: str) -> str:
        """Resolve a human-readable label to its stored value.

        Returns the label itself as passthrough if not found in either
        instance choices or OOTB defaults. This supports users passing
        numeric values directly.

        Args:
            table: ServiceNow table name (e.g. "incident").
            field: Field name (e.g. "state").
            label: Human-readable label to resolve (e.g. "open").
        """
        choices = await self.get_choices(table, field)
        return choices.get(label, label)

    async def get_choices(self, table: str, field: str) -> dict[str, str]:
        """Get the full label-to-value map for a table field.

        Args:
            table: ServiceNow table name.
            field: Field name.
        """
        await self._ensure_fetched()
        return self._cache.get((table, field), {})

    async def _ensure_fetched(self) -> None:
        """Fetch choices from sys_choice on first access.

        Uses an asyncio.Lock to prevent duplicate fetches under concurrency.
        On any failure, populates cache from OOTB defaults and logs a warning.
        """
        if self._fetched:
            return

        async with self._lock:
            if self._fetched:  # Double-check after acquiring lock
                return

            try:
                await self._fetch_from_instance()
            except Exception:
                logger.warning(
                    "Failed to fetch choice lists from instance; using OOTB defaults",
                    exc_info=True,
                )
                self._cache = {k: dict(v) for k, v in self._DEFAULTS.items()}

            self._fetched = True

    async def _fetch_from_instance(self) -> None:
        """Query sys_choice for all tracked table/field combinations."""
        from servicenow_mcp.utils import ServiceNowQuery

        tracked = list(self._DEFAULTS.keys())
        if not tracked:
            self._cache = {}
            return

        # Build the query using new_query for OR across table/field pairs
        q = ServiceNowQuery()
        first_table, first_field = tracked[0]
        q = q.equals("name", first_table).equals("element", first_field)
        for table, field in tracked[1:]:
            q = q.new_query().equals("name", table).equals("element", field)

        query_str = q.build()

        async with ServiceNowClient(self._settings, self._auth_provider) as client:
            result = await client.query_records(
                table="sys_choice",
                query=query_str,
                fields=["name", "element", "label", "value"],
                limit=500,
            )

        # Group results by (name, element)
        grouped: dict[tuple[str, str], dict[str, str]] = {}
        for record in result.get("records", []):
            name: Any = record.get("name", "")
            element: Any = record.get("element", "")
            label: Any = record.get("label", "")
            value: Any = record.get("value", "")
            if name and element and label:
                key = (str(name), str(element))
                if key not in grouped:
                    grouped[key] = {}
                # Normalize label: lowercase, spaces to underscores
                normalized = str(label).lower().replace(" ", "_")
                grouped[key][normalized] = str(value)

        # Merge: instance data takes priority, fill gaps from defaults
        for key in self._DEFAULTS:
            if key in grouped:
                # Start with defaults, overlay instance data
                merged = dict(self._DEFAULTS[key])
                merged.update(grouped[key])
                self._cache[key] = merged
            else:
                self._cache[key] = dict(self._DEFAULTS[key])

        # Also include any instance-only choices not in defaults
        for key, value in grouped.items():
            if key not in self._cache:
                self._cache[key] = value
