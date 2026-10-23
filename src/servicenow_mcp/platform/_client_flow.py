"""Flow Designer API operations."""

from typing import TYPE_CHECKING, Any

from servicenow_mcp.errors import NotFoundError, ServiceNowMCPError
from servicenow_mcp.platform._client_transport import ServiceNowRequestClient
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.query_builder import ServiceNowQuery
from servicenow_mcp.validation import (
    resolve_ref_value,
    sanitize_query_value,
    validate_identifier,
    validate_sys_id,
)

if TYPE_CHECKING:
    import httpx2


class FlowDesignerApiClient(ServiceNowRequestClient):
    """Implement Flow Designer record discovery and bounded joins."""

    async def flow_record(
        self, table: str, sys_id: str, fields: list[str]
    ) -> dict[str, Any]:
        """Read known flow lifecycle and execution records without retries."""
        if table not in {"sys_hub_flow", "sys_flow_context"}:
            raise ValueError("Unsupported flow lifecycle table.")
        validate_sys_id(sys_id)
        response = await self._ensure_client().get(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            params={
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "false",
            },
        )
        self._raise_for_flow_read_status(response)
        row = self._extract_json_result(response)
        if not isinstance(row, dict):
            raise ServiceNowMCPError("Flow lifecycle record response is incomplete.")
        return row

    async def studio_request(
        self,
        resource: str,
        *,
        data: dict[str, Any] | None = None,
        create: bool = False,
        minimal: bool = False,
    ) -> dict[str, Any]:
        """Use the observed, undocumented Studio contract without retries or browser credentials."""
        if (data is not None or create) and resource != "flow":
            raise ValueError("Studio writes are restricted to draft flow definitions.")
        if resource.startswith("flow/"):
            validate_sys_id(resource.removeprefix("flow/"))
        elif resource.startswith("action/action_types/"):
            validate_sys_id(resource.removeprefix("action/action_types/"))
        elif resource != "flow" or data is None:
            raise ValueError("Unsupported Studio resource.")
        method = "GET" if data is None else "POST" if create else "PUT"
        params = (
            {
                "param_only_properties": "true",
                "sysparm_transaction_scope": data["scope"],
            }
            if create and data
            else None
        )
        if minimal:
            if data is not None or create or not resource.startswith("flow/"):
                raise ValueError(
                    "Minimal definitions are available only for Studio flow reads."
                )
            params = {"use_definition": "0", "use_min_flow": "true"}
        response = await self._ensure_client().request(
            method,
            f"{self._settings.servicenow_instance_url}/api/now/processflow/{resource}",
            headers=await self._headers(),
            params=params,
            json=data,
        )
        self._raise_for_status(response)
        result = self._extract_json_result(response)
        if (
            response.status_code != 200
            or not isinstance(result, dict)
            or result.get("errorCode") not in (0, None)
            or result.get("errorMessage")
        ):
            raise ServiceNowMCPError(
                "Studio did not establish success. Remote outcome is unknown. Check the remote flow before retrying.",
            )
        value = (
            result
            if resource.startswith("action/action_types/")
            else result.get("data")
        )
        if not isinstance(value, dict) or not value.get("id"):
            raise ServiceNowMCPError(
                "Studio response is incomplete. Remote outcome is unknown. Check the remote flow before retrying.",
            )
        return value

    async def studio_definitions(self, kind: str) -> list[dict[str, Any]]:
        """Read the flow-logic or trigger catalogs that Workflow Studio offers to authors."""
        paths = {
            "flow_logic": "processflow/flow_logic/types",
            "triggers": "trigger_api/trigger_categories",
        }
        if kind not in paths:
            raise ValueError("Unsupported Studio definition catalog.")
        response = await self._ensure_client().get(
            f"{self._settings.servicenow_instance_url}/api/now/{paths[kind]}",
            headers=await self._headers(),
        )
        self._raise_for_flow_read_status(response)
        result = self._extract_json_result(response)
        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise ServiceNowMCPError(
                "Studio returned an unexpected definition catalog."
            )
        return result

    async def studio_lifecycle(
        self, sys_id: str, operation: str, payload: dict[str, Any] | None = None
    ) -> Any:
        """Effect-only Studio endpoints, never exposed through a read resource.

        publishFlowToServer:97561; deactivateFlowCall:97570;
        sendTestToServer:448105. No retries or redirect following.
        """
        validate_sys_id(sys_id)
        if operation not in {"snapshot", "subflow", "deactivate", "test"}:
            raise ValueError("Unsupported Studio lifecycle effect.")
        if (operation == "deactivate") != (payload is None):
            raise ValueError("Lifecycle payload does not match the endpoint.")
        response = await self._ensure_client().request(
            "GET" if operation == "deactivate" else "POST",
            f"{self._settings.servicenow_instance_url}/api/now/processflow/flow/{sys_id}/{operation}",
            headers=await self._headers(),
            json=payload,
            follow_redirects=False,
        )
        self._raise_for_status(response)
        result = self._extract_json_result(response)
        if (
            response.status_code != 200
            or not isinstance(result, dict)
            or (
                "errorCode" in result
                and (type(result["errorCode"]) is not int or result["errorCode"] != 0)
            )
            or result.get("errorMessage")
            or result.get("error")
            or "data" not in result
        ):
            raise ServiceNowMCPError(
                "Studio lifecycle response did not establish a completed result."
            )
        return result["data"]

    async def flow_test_record(self, table: str, sys_id: str) -> dict[str, Any]:
        """Read one explicitly selected test record identity and revision synchronously."""
        validate_identifier(table)
        validate_sys_id(sys_id)
        response = await self._ensure_client().get(
            self._table_url(table, sys_id),
            headers=await self._headers(),
            params={
                "sysparm_fields": "sys_id,sys_mod_count",
                "sysparm_display_value": "false",
            },
        )
        self._raise_for_flow_read_status(response)
        result = self._extract_json_result(response)
        if not isinstance(result, dict):
            raise ServiceNowMCPError("Test record identity response is incomplete.")
        return result

    async def studio_table_descriptor(self, table: str) -> dict[str, Any]:
        """Read Studio's record-trigger display-field descriptor (bundle:164322)."""
        validate_identifier(table)
        if "." in table:
            raise ValueError("The descriptor requires a single table name.")
        response = await self._ensure_client().get(
            f"{self._settings.servicenow_instance_url}/api/now/processflow/table/{table}/descriptor",
            headers=await self._headers(),
        )
        self._raise_for_flow_read_status(response)
        result = self._extract_json_result(response)
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ServiceNowMCPError("Studio returned an incomplete table descriptor.")
        return result["data"]

    async def studio_catalog_variables(
        self, catalog_id: str, model_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Read native catalog selection metadata (bundle:217777-217791)."""
        validate_sys_id(catalog_id)
        params = {"includeMultiRowVariableSets": "true"}
        if model_id:
            validate_sys_id(model_id)
            params["flow_catalog_model"] = model_id
        response = await self._ensure_client().get(
            f"{self._settings.servicenow_instance_url}/api/now/processflow/flow/{catalog_id}/service_catalog_variables",
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_flow_read_status(response)
        result = self._extract_json_result(response)
        if (
            not isinstance(result, dict)
            or "errorCode" in result
            and (type(result["errorCode"]) is not int or result["errorCode"] != 0)
            or result.get("errorMessage")
            or not isinstance(result.get("data"), list)
        ):
            raise ServiceNowMCPError("Catalog variable schema response is incomplete.")
        return result["data"]

    def _raise_for_flow_read_status(self, response: httpx2.Response) -> None:
        """Require a completed Table API response for a Flow Designer read."""
        self._raise_for_status(response)
        if response.status_code != 200:
            raise ServiceNowMCPError(
                f"Flow Designer Table API read returned HTTP {response.status_code} {response.reason_phrase}; "
                "the response does not contain a completed record result. Retry the read. If it remains accepted, "
                "check the instance REST access policy or intermediary that changed this synchronous Table API "
                "response.",
                status_code=response.status_code,
            )

    async def get_flow_by_sys_id(self, sys_id: str) -> dict[str, Any] | None:
        """Fetch a flow with display values, or return None when it does not exist."""
        try:
            response = await self._ensure_client().get(
                self._table_url("sys_hub_flow", sys_id),
                headers=await self._headers(),
                params={"sysparm_display_value": "all"},
            )
            self._raise_for_flow_read_status(response)
        except NotFoundError:
            return None
        return self._extract_json_result(response)

    async def find_flows_by_name(self, name: str) -> list[dict[str, Any]]:
        """Find flows whose name or internal name matches the supplied name."""
        query = (
            ServiceNowQuery()
            .equals("name", name)
            .or_equals("internal_name", name)
            .build()
        )
        return await self._list_flow_rows("sys_hub_flow", query, limit=25)

    async def list_flow_inputs(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List declared flow inputs."""
        return await self._list_flow_rows(
            "sys_hub_flow_input", f"model={flow_sys_id}^ORDERBYorder", limit=limit
        )

    async def list_flow_outputs(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List declared flow outputs."""
        return await self._list_flow_rows(
            "sys_hub_flow_output", f"model={flow_sys_id}^ORDERBYorder", limit=limit
        )

    async def list_flow_variables(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List flow-scoped variables."""
        return await self._list_flow_rows(
            "sys_hub_flow_variable", f"model={flow_sys_id}^ORDERBYorder", limit=limit
        )

    async def list_flow_stages(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List configured lifecycle stages owned by a flow-base record."""
        return await self._list_flow_rows(
            "sys_hub_flow_stage",
            f"flow={flow_sys_id}^ORDERBYorder",
            fields=(
                "stage_id,label,value,states,type,order,component_indexes,ancestor_component_id,"
                "ancestor_stage_id,ancestral_if_else_logic,always_show"
            ),
            limit=limit,
        )

    async def list_action_instances_v2(
        self, flow_sys_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List V2 action instances for a flow."""
        return await self._list_flow_rows(
            "sys_hub_action_instance_v2",
            f"flow={flow_sys_id}^ORDERBYorder",
            limit=limit,
        )

    async def list_action_instances_v1(
        self, flow_sys_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List V1 action instances for a flow."""
        return await self._list_flow_rows(
            "sys_hub_action_instance", f"flow={flow_sys_id}^ORDERBYorder", limit=limit
        )

    async def list_subflow_instances_v2(
        self, flow_sys_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List persisted V2 subflow calls, including snapshot references and input blobs."""
        return await self._list_flow_rows(
            "sys_hub_sub_flow_instance_v2",
            f"flow={flow_sys_id}^ORDERBYorder",
            limit=limit,
        )

    async def list_logic_instances_v2(
        self, flow_sys_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List V2 flow-logic instances."""
        return await self._list_flow_rows(
            "sys_hub_flow_logic_instance_v2",
            f"flow={flow_sys_id}^ORDERBYorder",
            limit=limit,
        )

    async def list_logic_instances_v1(
        self, flow_sys_id: str, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List V1 flow-logic instances."""
        return await self._list_flow_rows(
            "sys_hub_flow_logic", f"flow={flow_sys_id}^ORDERBYorder", limit=limit
        )

    async def list_trigger_instances_v2(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List V2 trigger instances for a flow."""
        return await self._list_flow_rows(
            "sys_hub_trigger_instance_v2", f"flow={flow_sys_id}", limit=limit
        )

    async def list_trigger_instances_v1(
        self, flow_sys_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List V1 trigger instances for a flow."""
        return await self._list_flow_rows(
            "sys_hub_trigger_instance", f"flow={flow_sys_id}", limit=limit
        )

    async def list_record_triggers(
        self, remote_trigger_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Bulk-fetch record triggers for V2 trigger conditions."""
        if not remote_trigger_ids:
            return []
        return await self._list_flow_rows(
            "sys_flow_record_trigger",
            f"sys_idIN{','.join(remote_trigger_ids)}",
            limit=min(len(remote_trigger_ids), INTERNAL_QUERY_LIMIT),
        )

    async def get_action_type_definitions(
        self, action_type_sys_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Bulk-fetch action-type metadata."""
        if not action_type_sys_ids:
            return []
        return await self._list_flow_rows(
            "sys_hub_action_type_base",
            f"sys_idIN{','.join(action_type_sys_ids)}",
            fields="sys_id,name,internal_name,sys_scope,category,sys_class_name",
            limit=min(len(action_type_sys_ids), INTERNAL_QUERY_LIMIT),
        )

    async def list_action_input_definitions(
        self, action_type_sys_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Bulk-fetch declared inputs for action types."""
        if not action_type_sys_ids:
            return []
        return await self._list_flow_rows(
            "sys_hub_action_input",
            f"action_typeIN{','.join(action_type_sys_ids)}^ORDERBYname",
            fields="action_type,name,label,element_prototype,mandatory,default_value,reference",
            limit=min(len(action_type_sys_ids) * 50, 5000),
        )

    async def list_action_output_definitions(
        self, action_type_sys_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Bulk-fetch declared outputs for action types."""
        if not action_type_sys_ids:
            return []
        return await self._list_flow_rows(
            "sys_hub_action_output",
            f"action_typeIN{','.join(action_type_sys_ids)}^ORDERBYname",
            fields="action_type,name,label,element_prototype,mandatory,reference",
            limit=min(len(action_type_sys_ids) * 50, 5000),
        )

    async def list_v1_variable_values(
        self, action_instance_sys_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Bulk-fetch V1 action instance input values."""
        if not action_instance_sys_ids:
            return []
        return await self._list_flow_rows(
            "sys_variable_value",
            f"document=sys_hub_action_instance^document_keyIN{','.join(action_instance_sys_ids)}",
            limit=min(len(action_instance_sys_ids) * 10, 5000),
        )

    async def _list_flow_rows(
        self,
        table: str,
        query: str,
        *,
        limit: int | None = None,
        fields: str = "",
    ) -> list[dict[str, Any]]:
        params = {"sysparm_query": query, "sysparm_display_value": "all"}
        if limit is not None:
            params["sysparm_limit"] = str(limit)
        if fields:
            params["sysparm_fields"] = fields
        response = await self._ensure_client().get(
            self._table_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_flow_read_status(response)
        return self._extract_json_result(response)

    async def _flow_lookup_page(
        self,
        table: str,
        query: str,
        limit: int,
        fields: str = "",
    ) -> tuple[list[dict[str, Any]], int | None]:
        params = {"sysparm_display_value": "all", "sysparm_limit": str(limit)}
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = fields
        response = await self._ensure_client().get(
            self._table_url(table),
            headers=await self._headers(),
            params=params,
        )
        self._raise_for_flow_read_status(response)
        rows = self._extract_json_result(response)
        try:
            total = int(response.headers["X-Total-Count"])
        except KeyError, ValueError:
            total = None
        if total is not None and total < len(rows):
            total = None
        return rows, total

    @staticmethod
    def _flow_id_queries(ids: list[str], relations: tuple[str, ...]) -> list[str]:
        unique_ids = list(dict.fromkeys(ids))
        for sys_id in unique_ids:
            validate_sys_id(sys_id)
        return [
            "^OR".join(
                f"{relation}IN{','.join(unique_ids[start : start + 50])}"
                for relation in relations
            )
            for start in range(0, len(unique_ids), 50)
        ]

    @staticmethod
    def _merge_flow_lookup_page(
        rows: list[dict[str, Any]],
        page: list[dict[str, Any]],
        seen_ids: set[str],
        limit: int,
    ) -> bool:
        has_omitted_rows = False
        for row in page:
            sys_id = resolve_ref_value(row.get("sys_id", ""))
            if sys_id and sys_id in seen_ids:
                continue
            if len(rows) >= limit:
                has_omitted_rows = True
                continue
            rows.append(row)
            if sys_id:
                seen_ids.add(sys_id)
        return has_omitted_rows

    async def _flow_lookup_batches(
        self,
        table: str,
        queries: list[str],
        limit: int,
        fields: str = "",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        truncation: list[dict[str, Any]] = []
        for query in queries:
            page, total = await self._flow_lookup_page(table, query, limit, fields)
            previous_count = len(rows)
            has_omitted_rows = self._merge_flow_lookup_page(rows, page, seen_ids, limit)
            is_page_incomplete = (
                total > len(page) if total is not None else len(page) >= limit
            )
            if has_omitted_rows or is_page_incomplete:
                truncation.append(
                    {
                        "query": query,
                        "returned": len(rows) - previous_count,
                        "fetched": len(page),
                        "total": total,
                        "limit": limit,
                        "continuation": (
                            f"Use query(table='{table}', encoded_query='{query}^ORDERBYsys_id', "
                            "fields='*', offset=0) and advance offset through all pages. "
                            "Restart this batch because the merged limit can omit rows within it."
                        ),
                    },
                )
        return rows, truncation

    async def find_record_triggers_by_table(self, table: str) -> list[dict[str, Any]]:
        """Find record triggers for a table."""
        validate_identifier(table)
        return await self._list_flow_rows(
            "sys_flow_record_trigger", f"table={table}", limit=INTERNAL_QUERY_LIMIT
        )

    async def list_v2_triggers_by_remote_ids(
        self, remote_trigger_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Find V2 trigger instances linked to record-trigger IDs."""
        if not remote_trigger_ids:
            return []
        rows, _ = await self._flow_lookup_batches(
            "sys_hub_trigger_instance_v2",
            self._flow_id_queries(remote_trigger_ids, ("remote_trigger_id", "sys_id")),
            INTERNAL_QUERY_LIMIT,
        )
        return rows

    async def list_v1_triggers_by_table(self, table: str) -> list[dict[str, Any]]:
        """Find V1 trigger instances through their remote record-trigger relation."""
        validate_identifier(table)
        record_triggers = await self.find_record_triggers_by_table(table)
        remote_ids = [
            resolve_ref_value(row.get("sys_id", "")) for row in record_triggers
        ]
        if not remote_ids:
            return []
        rows, _ = await self._flow_lookup_batches(
            "sys_hub_trigger_instance",
            self._flow_id_queries(remote_ids, ("remote_sys_id",)),
            INTERNAL_QUERY_LIMIT,
        )
        return rows

    async def list_triggers_filtered(
        self,
        *,
        trigger_type: str = "",
        table: str = "",
        active: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        """List V1 and V2 trigger instances with optional bounded filters."""
        capped = max(1, min(limit, INTERNAL_QUERY_LIMIT))
        v2_parts: list[str] = []
        v1_parts: list[str] = []
        if trigger_type:
            safe_type = sanitize_query_value(trigger_type)
            v2_parts.append(f"type={safe_type}")
            v1_parts.append(f"trigger_type={safe_type}")
        if active in {"true", "false"}:
            safe_active = sanitize_query_value(active)
            v2_parts.append(f"active={safe_active}")
            v1_parts.append(f"flow.active={safe_active}")

        truncation: dict[str, Any] = {}
        v2_relations = [""]
        v1_relations = [""]
        if table:
            validate_identifier(table)
            source_query = f"table={table}^ORDERBYsys_id"
            record_triggers, total = await self._flow_lookup_page(
                "sys_flow_record_trigger",
                source_query,
                INTERNAL_QUERY_LIMIT,
            )
            is_source_incomplete = (
                total > len(record_triggers)
                if total is not None
                else len(record_triggers) >= INTERNAL_QUERY_LIMIT
            )
            if is_source_incomplete:
                truncation["sys_flow_record_trigger"] = {
                    "returned": len(record_triggers),
                    "total": total,
                    "limit": INTERNAL_QUERY_LIMIT,
                    "continuation": (
                        f"Use query(table='sys_flow_record_trigger', encoded_query='{source_query}', "
                        f"fields='sys_id', offset={len(record_triggers)}) and advance offset through all pages. "
                        "Join remaining IDs in batches to sys_hub_trigger_instance.remote_sys_id and "
                        "sys_hub_trigger_instance_v2.remote_trigger_id or sys_id, retaining the requested "
                        "trigger_type and active filters. Query safety row limits still apply."
                    ),
                }
            remote_ids = [
                resolve_ref_value(row.get("sys_id", "")) for row in record_triggers
            ]
            v2_relations = self._flow_id_queries(
                remote_ids, ("remote_trigger_id", "sys_id")
            )
            v1_relations = self._flow_id_queries(remote_ids, ("remote_sys_id",))

        result: dict[str, Any] = {}
        for version, source_table, parts, relations in (
            ("v2", "sys_hub_trigger_instance_v2", v2_parts, v2_relations),
            ("v1", "sys_hub_trigger_instance", v1_parts, v1_relations),
        ):
            queries = [
                "^".join(part for part in [*parts, relation] if part)
                for relation in relations
            ]
            result[version], batches = await self._flow_lookup_batches(
                source_table, queries, capped
            )
            if batches:
                truncation[source_table] = {"batches": batches}
        if truncation:
            result["truncation"] = truncation
        return result

    async def get_flows_bulk(self, flow_sys_ids: list[str]) -> list[dict[str, Any]]:
        """Resolve flow headers by identity or current snapshot references."""
        if not flow_sys_ids:
            return []
        rows, _ = await self._flow_lookup_batches(
            "sys_hub_flow",
            self._flow_id_queries(
                flow_sys_ids, ("sys_id", "master_snapshot", "latest_snapshot")
            ),
            INTERNAL_QUERY_LIMIT,
            "sys_id,name,internal_name,type,active,sys_scope,description,master_snapshot,latest_snapshot",
        )
        return rows
