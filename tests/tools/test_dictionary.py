"""Tests for ``servicenow_mcp.tools._dictionary``.

Covers the registry's super_class walk, type filter, attribute heuristic,
exclusion list, cycle/depth guards, and per-table cache. The registry talks to
``sys_db_object`` (super_class chain) and ``sys_dictionary`` (field rows); both
are mocked with respx routed by URL.
"""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.telemetry import CacheName, HttpTelemetry
from servicenow_mcp.tools._dictionary import (
    DictionaryField,
    DictionaryRegistry,
    _attributes_admit_heuristic,
    looks_like_template,
)


BASE_URL = "https://test.service-now.com"
DB_OBJECT_URL = f"{BASE_URL}/api/now/table/sys_db_object"
DICTIONARY_URL = f"{BASE_URL}/api/now/table/sys_dictionary"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for dictionary-registry tests."""
    return BasicAuthProvider(settings)


def _row(element: str, internal_type: str, attributes: str = "") -> dict[str, str]:
    """Build a ``sys_dictionary`` row stub with the columns the registry reads."""
    return {"element": element, "internal_type": internal_type, "attributes": attributes}


def _mock_root_table(dictionary_rows: list[dict[str, str]]) -> None:
    """Mock a root table (empty super_class) returning ``dictionary_rows``."""
    respx.get(DB_OBJECT_URL).mock(return_value=httpx.Response(200, json={"result": [{"super_class": ""}]}))
    respx.get(DICTIONARY_URL).mock(return_value=httpx.Response(200, json={"result": dictionary_rows}))


# ---------------------------------------------------------------------------
# Type filter & exclusion list
# ---------------------------------------------------------------------------


class TestTypeFilter:
    """The unambiguous-type list admits fields regardless of attributes."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_unambiguous_script_types_admitted(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        _mock_root_table(
            [
                _row("script", "script"),
                _row("template", "html_template"),
                _row("css", "css"),
                _row("name", "string"),
                _row("active", "boolean"),
            ]
        )
        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("sys_script")

        names = [f.name for f in fields]
        assert names == ["script", "template", "css"]
        assert all(f.via_heuristic is False for f in fields)
        assert all(f.inherited_from is None for f in fields)

    @respx.mock
    @pytest.mark.asyncio()
    async def test_excluded_elements_dropped(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_root_table(
            [
                _row("script", "script"),
                _row("translated_html", "html", "tinymce_allow_all=true"),
                _row("conditions", "script"),
            ]
        )
        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("some_table")

        assert [f.name for f in fields] == ["script"]


class TestHeuristicAdmission:
    """``html`` and ``xml`` require a heuristic flag in attributes to admit."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_html_with_tinymce_flag_admitted(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_root_table([_row("layout", "html", "tinymce_allow_all=true,html_sanitize=false")])
        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("sys_email_layout")

        assert len(fields) == 1
        assert fields[0].name == "layout"
        assert fields[0].via_heuristic is True

    @respx.mock
    @pytest.mark.asyncio()
    async def test_html_without_flag_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_root_table([_row("description", "html", "edge_encryption_enabled=true")])
        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("incident")

        assert fields == []

    @respx.mock
    @pytest.mark.asyncio()
    async def test_html_sanitize_false_alone_admits(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_root_table([_row("body", "html", "html_sanitize=false")])
        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("custom_table")

        assert [f.name for f in fields] == ["body"]
        assert fields[0].via_heuristic is True


class TestAttributesAdmitHeuristic:
    """The heuristic parses tokens at comma boundaries, not by substring."""

    def test_empty_string_not_admitted(self) -> None:
        assert _attributes_admit_heuristic("") is False

    def test_legitimate_flag_admitted(self) -> None:
        assert _attributes_admit_heuristic("tinymce_allow_all=true") is True

    def test_legitimate_flag_with_other_tokens_admitted(self) -> None:
        assert _attributes_admit_heuristic("foo=bar,tinymce_allow_all=true,baz=qux") is True

    def test_html_sanitize_false_admitted(self) -> None:
        assert _attributes_admit_heuristic("html_sanitize=false") is True

    def test_substring_key_rejected(self) -> None:
        """``my_tinymce_allow_all=true`` must not false-positive."""
        assert _attributes_admit_heuristic("my_tinymce_allow_all=true,other=x") is False
        assert _attributes_admit_heuristic("not_html_sanitize=false") is False

    def test_wrong_value_rejected(self) -> None:
        assert _attributes_admit_heuristic("tinymce_allow_all=false") is False
        assert _attributes_admit_heuristic("html_sanitize=true") is False

    def test_whitespace_tolerated(self) -> None:
        assert _attributes_admit_heuristic("foo=bar, tinymce_allow_all = true ") is True

    def test_case_insensitive(self) -> None:
        assert _attributes_admit_heuristic("TINYMCE_ALLOW_ALL=TRUE") is True

    def test_value_containing_equals_survives(self) -> None:
        """Split on first ``=`` only; spurious ``=`` in values does not break parsing."""
        assert _attributes_admit_heuristic("other=a=b,tinymce_allow_all=true") is True


# ---------------------------------------------------------------------------
# super_class walk
# ---------------------------------------------------------------------------


class TestSuperClassChain:
    """Inherited fields from parent tables are walked and attributed correctly."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_inherited_fields_merge_with_parent_attribution(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        # Child returns parent="sys_script_client"; parent returns "" (root).
        respx.get(DB_OBJECT_URL).mock(
            side_effect=[
                httpx.Response(200, json={"result": [{"super_class": "sys_script_client"}]}),
                httpx.Response(200, json={"result": [{"super_class": ""}]}),
            ]
        )
        respx.get(DICTIONARY_URL).mock(
            side_effect=[
                # child sys_dictionary rows
                httpx.Response(
                    200,
                    json={"result": [_row("ui_type", "integer")]},
                ),
                # parent sys_dictionary rows
                httpx.Response(
                    200,
                    json={"result": [_row("script", "script")]},
                ),
            ]
        )

        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("catalog_script_client")

        assert [f.name for f in fields] == ["script"]
        assert fields[0].inherited_from == "sys_script_client"

        chain = await registry.get_chain("catalog_script_client")
        assert chain == ["catalog_script_client", "sys_script_client"]

    @respx.mock
    @pytest.mark.asyncio()
    async def test_child_wins_on_field_collision(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(DB_OBJECT_URL).mock(
            side_effect=[
                httpx.Response(200, json={"result": [{"super_class": "parent_table"}]}),
                httpx.Response(200, json={"result": [{"super_class": ""}]}),
            ]
        )
        respx.get(DICTIONARY_URL).mock(
            side_effect=[
                httpx.Response(200, json={"result": [_row("script", "script")]}),
                httpx.Response(200, json={"result": [_row("script", "script_plain")]}),
            ]
        )

        registry = DictionaryRegistry(settings, auth_provider)
        fields = await registry.get_script_fields("child_table")

        assert len(fields) == 1
        # Child's row wins: internal_type from child, inherited_from is None.
        assert fields[0].internal_type == "script"
        assert fields[0].inherited_from is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_missing_super_class_yields_single_table_chain(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        # sys_db_object returns no rows for an unknown table.
        respx.get(DB_OBJECT_URL).mock(return_value=httpx.Response(200, json={"result": []}))
        respx.get(DICTIONARY_URL).mock(return_value=httpx.Response(200, json={"result": []}))

        registry = DictionaryRegistry(settings, auth_provider)
        chain = await registry.get_chain("unknown_table")
        assert chain == ["unknown_table"]
        assert await registry.get_script_fields("unknown_table") == []


class TestCycleAndDepthGuards:
    """The chain walker bails out on cycles and at the depth ceiling."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cycle_detected_and_truncated(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        # a -> b -> a (cycle). Each call returns the cyclic parent.
        responses = {
            "a": httpx.Response(200, json={"result": [{"super_class": "b"}]}),
            "b": httpx.Response(200, json={"result": [{"super_class": "a"}]}),
        }

        def _route(request: httpx.Request) -> httpx.Response:
            query = request.url.params.get("sysparm_query", "")
            if "name=a" in query:
                return responses["a"]
            if "name=b" in query:
                return responses["b"]
            return httpx.Response(200, json={"result": []})

        respx.get(DB_OBJECT_URL).mock(side_effect=_route)
        respx.get(DICTIONARY_URL).mock(return_value=httpx.Response(200, json={"result": []}))

        registry = DictionaryRegistry(settings, auth_provider)
        chain = await registry.get_chain("a")
        # Cycle is broken once "a" is re-seen.
        assert chain == ["a", "b"]


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


class TestCache:
    """The registry caches per-table results to avoid re-querying."""

    @respx.mock
    @pytest.mark.asyncio()
    async def test_second_call_does_not_refetch(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        db_route = respx.get(DB_OBJECT_URL).mock(
            return_value=httpx.Response(200, json={"result": [{"super_class": ""}]})
        )
        dict_route = respx.get(DICTIONARY_URL).mock(
            return_value=httpx.Response(200, json={"result": [_row("script", "script")]})
        )

        registry = DictionaryRegistry(settings, auth_provider)
        first = await registry.get_script_fields("sys_script")
        second = await registry.get_script_fields("sys_script")

        assert first == second
        assert db_route.call_count == 1
        assert dict_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_flush_invalidates_cache(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        db_route = respx.get(DB_OBJECT_URL).mock(
            return_value=httpx.Response(200, json={"result": [{"super_class": ""}]})
        )
        dict_route = respx.get(DICTIONARY_URL).mock(
            return_value=httpx.Response(200, json={"result": [_row("script", "script")]})
        )

        registry = DictionaryRegistry(settings, auth_provider)
        await registry.get_script_fields("sys_script")
        registry.flush("sys_script")
        await registry.get_script_fields("sys_script")

        assert db_route.call_count == 2
        assert dict_route.call_count == 2

    @pytest.mark.asyncio()
    async def test_chain_cache_hit_precedes_client_creation(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A fresh chain hit does not construct or enter another client context."""
        entered = 0

        class ClientContext:
            async def __aenter__(self) -> object:
                nonlocal entered
                entered += 1
                return object()

            async def __aexit__(self, *args: object) -> None:
                return None

        client_factory = cast("ServiceNowClientProvider", ClientContext)
        registry = DictionaryRegistry(settings, auth_provider, client_factory)
        registry._resolve_chain = AsyncMock(return_value=["incident"])  # type: ignore[method-assign]

        assert await registry.get_chain("incident") == ["incident"]
        assert await registry.get_chain("incident") == ["incident"]
        assert entered == 1

    @pytest.mark.asyncio()
    async def test_same_table_field_load_is_single_flight(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Concurrent calls for one table share dictionary metadata loading."""
        registry = DictionaryRegistry(settings, auth_provider)
        gate = asyncio.Event()
        calls = 0

        async def load(table: str) -> list[DictionaryField]:
            del table
            nonlocal calls
            calls += 1
            await gate.wait()
            return []

        registry.get_all_fields = load  # type: ignore[method-assign]
        first = asyncio.create_task(registry.get_script_fields("incident"))
        second = asyncio.create_task(registry.get_script_fields("incident"))
        await asyncio.sleep(0)
        gate.set()

        assert await asyncio.gather(first, second) == [[], []]
        assert calls == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_cache_telemetry_uses_fixed_dictionary_names(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Dictionary cache counters omit the requested table name."""
        respx.get(DB_OBJECT_URL).mock(return_value=httpx.Response(200, json={"result": [{"super_class": ""}]}))
        telemetry = HttpTelemetry()
        registry = DictionaryRegistry(settings, auth_provider, telemetry=telemetry)

        await registry.get_chain("sensitive_customer_table")
        await registry.get_chain("sensitive_customer_table")

        context = telemetry.cache_sentry_context()
        assert context[CacheName.DICTIONARY_CHAINS.value]["hits"] == 1
        assert "sensitive_customer_table" not in str(context)


# ---------------------------------------------------------------------------
# Template-syntax helper
# ---------------------------------------------------------------------------


class TestLooksLikeTemplate:
    """Content-level helper: ``${...}`` detection."""

    def test_simple_template(self) -> None:
        assert looks_like_template("Hello ${name}") is True

    def test_multiple_templates(self) -> None:
        assert looks_like_template("${a} and ${b}") is True

    def test_no_template(self) -> None:
        assert looks_like_template("plain text") is False

    def test_empty_string(self) -> None:
        assert looks_like_template("") is False

    def test_dollar_without_braces(self) -> None:
        assert looks_like_template("price is $5") is False
