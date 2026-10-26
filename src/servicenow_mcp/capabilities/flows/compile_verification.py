"""Bounded native serialized snapshots for active-owner runtime preservation.

Release 27800 stores a serialized Flow in sys_hub_snapshot_chunk, not an
engine instruction stream. Preserve the complete document and wire envelope;
do not infer authored-version or engine-plan equality from its identity.
"""

import base64
import json
import zlib
from typing import TYPE_CHECKING, Any

from servicenow_mcp.capabilities.flows import target
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_sys_id

if TYPE_CHECKING:
    from servicenow_mcp.platform.client import ServiceNowClient

MAX_CHUNKS = 32
MAX_ENCODED_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 100_000
SNAPSHOT_FIELDS = ["sys_id", "parent", "payload", "sys_mod_count", "sys_updated_on"]
CHUNK_FIELDS = ["sys_id", "document_id", "field", "position", "length", "data"]


class UnprovenContent(ValueError):
    """A controlled, value-free incomplete preservation read."""

    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require(condition: bool, code: str = "INCOMPLETE_COMPILED_CONTENT") -> None:
    if not condition:
        raise UnprovenContent(code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "MALFORMED_COMPILED_CONTENT")
        result[key] = value
    return result


def _json(text: str) -> Any:
    _require(len(text) <= MAX_DOCUMENT_BYTES, "COMPILED_CONTENT_BOUND")
    # Bound nesting before json.loads allocates/recurses. Brackets inside JSON
    # strings do not contribute to depth. json.loads still validates syntax.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            _require(depth <= MAX_DEPTH, "COMPILED_CONTENT_BOUND")
        elif char in "]}":
            depth -= 1

    def nonfinite(_: str) -> Any:
        raise UnprovenContent("MALFORMED_COMPILED_CONTENT")

    value = json.loads(text, object_pairs_hook=_pairs, parse_constant=nonfinite)
    remaining = [value]
    nodes = 0
    while remaining:
        item = remaining.pop()
        nodes += 1
        _require(nodes <= MAX_NODES, "COMPILED_CONTENT_BOUND")
        if isinstance(item, dict):
            remaining.extend(item.values())
        elif isinstance(item, list):
            remaining.extend(item)
    return value


def _decode(encoded: str) -> Any:
    _require(
        0 < len(encoded) <= MAX_ENCODED_BYTES,
        "COMPILED_CONTENT_BOUND",
    )
    compressed = base64.b64decode(encoded, validate=True)
    decoder = zlib.decompressobj(31)
    decoded = decoder.decompress(compressed, MAX_DOCUMENT_BYTES + 1)
    _require(
        len(decoded) <= MAX_DOCUMENT_BYTES
        and decoder.eof
        and not decoder.unconsumed_tail
        and not decoder.unused_data,
        "COMPILED_CONTENT_BOUND",
    )
    return _json(decoded.decode("utf-8"))


def _id(value: Any) -> str:
    result = target.scalar(value)
    if not isinstance(result, str):
        raise UnprovenContent("INCOMPLETE_COMPILED_CONTENT")
    validate_sys_id(result)
    return result


def _record(row: dict[str, Any], identity: str) -> None:
    _require(isinstance(row, dict) and _id(row.get("sys_id")) == identity)
    revision = target.scalar(row.get("sys_mod_count"))
    _require(
        isinstance(revision, str)
        and revision.isascii()
        and revision.isdecimal()
        and bool(target.scalar(row.get("sys_updated_on")))
    )


async def serialized_snapshot(
    identity: str, owner: str, client: ServiceNowClient
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Read the bounded native chunk envelope and its complete serialized Flow."""
    validate_sys_id(identity)
    validate_sys_id(owner)
    check_table_access("sys_hub_snapshot")
    check_table_access("sys_hub_snapshot_chunk")
    record = await client.get_record(
        "sys_hub_snapshot", identity, fields=SNAPSHOT_FIELDS
    )
    _record(record, identity)
    _require(target.scalar(record.get("parent")) == owner, "COMPILED_VERSION_MISMATCH")
    pointer = _json(target.scalar(record.get("payload")) or "")
    _require(
        isinstance(pointer, dict)
        and set(pointer) == {"table", "id", "name", "type"}
        and pointer.get("table") == "sys_hub_snapshot"
        and pointer.get("id") == identity
        and pointer.get("type") == "java.lang.String"
        and isinstance(pointer.get("name"), str),
        "UNSUPPORTED_COMPILED_SERIALIZATION",
    )
    chunks = (
        await client.query_records(
            "sys_hub_snapshot_chunk",
            f"document_id={identity}^ORDERBYposition",
            fields=CHUNK_FIELDS,
            limit=MAX_CHUNKS,
        )
    )["records"]
    _require(0 < len(chunks) < MAX_CHUNKS, "COMPILED_CONTENT_BOUND")
    encoded: list[str] = []
    seen: set[str] = set()
    total = 0
    # Query order is not trusted. Positions and lengths are checked independently.
    for position, chunk in enumerate(
        sorted(chunks, key=lambda row: int(row["position"]))
    ):
        chunk_id = _id(chunk.get("sys_id"))
        _require(chunk_id not in seen)
        seen.add(chunk_id)
        data = target.scalar(chunk.get("data"))
        if not isinstance(data, str):
            raise UnprovenContent("INCOMPLETE_COMPILED_CONTENT")
        _require(
            target.scalar(chunk.get("document_id")) == identity
            and target.scalar(chunk.get("field")) == "payload"
            and target.scalar(chunk.get("position")) == str(position)
            and target.scalar(chunk.get("length")) == str(len(data)),
        )
        total += len(data)
        _require(total <= MAX_ENCODED_BYTES, "COMPILED_CONTENT_BOUND")
        encoded.append(data)
    # The native Java String is itself JSON-encoded inside the gzip member.
    document = _decode("".join(encoded))
    if not isinstance(document, str):
        raise UnprovenContent("UNSUPPORTED_COMPILED_SERIALIZATION")
    flow = _json(document)
    if not isinstance(flow, dict) or flow.get("id") != identity:
        raise UnprovenContent("INCOMPLETE_COMPILED_CONTENT")
    _require(flow.get("parentFlow") == owner, "COMPILED_VERSION_MISMATCH")
    _require(
        flow.get("authoredOnReleaseVersion") == 27800
        and type(flow.get("authoredOnReleaseVersion")) is int
        # The serialized Java field is unset (0), while the independent native
        # reader reports flowEngineVersion=2. Do not rewrite or conflate them.
        and flow.get("engineVersion") == 0
        and type(flow.get("engineVersion")) is int
        and flow.get("isSnapshot") is True
        and flow.get("isJsonSnapshot") is True,
        "UNSUPPORTED_COMPILED_SERIALIZATION",
    )
    return flow, record, chunks
