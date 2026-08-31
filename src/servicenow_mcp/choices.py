"""Lazy-loaded choice list registry backed by sys_choice."""

import logging
from typing import Any, ClassVar

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.metadata_cache import AsyncMetadataCache
from servicenow_mcp.sentry import capture_exception as sentry_capture
from servicenow_mcp.telemetry import CacheName, HttpTelemetry


logger = logging.getLogger(__name__)


def _normalize_choice_label(label: str) -> str:
    """Normalize a choice label to lowercase with underscores replacing spaces."""
    return label.lower().replace(" ", "_")


def _group_choice_records(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
    """Group sys_choice records into a {(table, field): {label: value}} lookup."""
    grouped: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        name: Any = record.get("name", "")
        element: Any = record.get("element", "")
        label: Any = record.get("label", "")
        value: Any = record.get("value", "")
        if not (name and element and label):
            continue
        key = (str(name), str(element))
        if key not in grouped:
            grouped[key] = {}
        grouped[key][_normalize_choice_label(str(label))] = str(value)
    return grouped


def _merge_with_defaults(
    grouped: dict[tuple[str, str], dict[str, str]],
    defaults: dict[tuple[str, str], dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Merge instance choices with defaults: instance data overlays defaults."""
    merged: dict[tuple[str, str], dict[str, str]] = {}

    for key in defaults:
        base = dict(defaults[key])
        if key in grouped:
            base.update(grouped[key])
        merged[key] = base

    # Include any instance-only choices not in defaults
    for key, value in grouped.items():
        if key not in merged:
            merged[key] = value

    return merged


class ChoiceRegistry:
    """Lazy-loaded choice list cache backed by sys_choice.

    Fetches real choice values from the instance on first access and caches
    them for the configured metadata TTL. Falls back to OOTB defaults on failure.
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
        self._cache: dict[tuple[str, str], dict[str, str]] = {}
        self._metadata_cache = AsyncMetadataCache[str, dict[tuple[str, str], dict[str, str]]](
            name=CacheName.CHOICES,
            ttl_seconds=settings.metadata_cache_ttl_seconds,
            telemetry=telemetry,
        )

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
        cache = await self._ensure_fetched()
        return cache.get((table, field), {})

    async def _ensure_fetched(self) -> dict[tuple[str, str], dict[str, str]]:
        """Return fresh choices, with one shared instance load at a time."""

        async def load() -> dict[tuple[str, str], dict[str, str]]:
            loaded = await self._fetch_from_instance()
            return self._cache if loaded is None else loaded

        try:
            self._cache = await self._metadata_cache.get_or_load("all", load)
        except Exception as e:
            logger.warning(
                "Failed to fetch choice lists from instance; using OOTB defaults",
                exc_info=True,
            )
            sentry_capture(e)
            return {key: dict(value) for key, value in self._DEFAULTS.items()}
        return self._cache

    async def _fetch_from_instance(self) -> dict[tuple[str, str], dict[str, str]] | None:
        """Query sys_choice for all tracked table/field combinations."""
        from servicenow_mcp.utils import ServiceNowQuery

        tracked = list(self._DEFAULTS.keys())
        if not tracked:
            self._cache = {}
            return self._cache

        query = ServiceNowQuery()
        first_table, first_field = tracked[0]
        query = query.equals("name", first_table).equals("element", first_field)
        for table, field in tracked[1:]:
            query = query.new_query().equals("name", table).equals("element", field)

        async with self._client_factory() as client:
            result = await client.query_records(
                table="sys_choice",
                query=query.build(),
                fields=["name", "element", "label", "value"],
                limit=500,
            )

        grouped = _group_choice_records(result.get("records", []))
        self._cache = _merge_with_defaults(grouped, self._DEFAULTS)
        return self._cache
