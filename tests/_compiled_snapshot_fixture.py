"""Synthetic native chunk envelope; no captured inputs or credentials."""

import base64
import copy
import gzip
import json
from typing import Any


def snapshot_storage(
    flow: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Encode the observed Java String/gzip/JSON storage contract."""
    document = copy.deepcopy(flow)
    document.pop("flowEngineVersion")
    document["engineVersion"] = 0
    document["isSnapshot"] = document.pop("snapshot")
    document["isJsonSnapshot"] = document.pop("jsonSnapshot")
    encoded = base64.b64encode(
        gzip.compress(json.dumps(json.dumps(document)).encode(), mtime=0)
    ).decode()
    record: dict[str, str] = {
        "sys_id": flow["id"],
        "parent": flow["parentFlow"],
        "payload": json.dumps(
            {
                "table": "sys_hub_snapshot",
                "id": flow["id"],
                "name": "payload",
                "type": "java.lang.String",
            }
        ),
        "sys_mod_count": "1",
        "sys_updated_on": "2026-10-23 10:00:00",
    }
    chunks: list[dict[str, str]] = [
        {
            "sys_id": "fa" * 16,
            "document_id": flow["id"],
            "field": "payload",
            "position": "0",
            "length": str(len(encoded)),
            "data": encoded,
        }
    ]
    return record, chunks
