"""Tests for parse_payload_json shared helper."""

from toon_format import decode as toon_decode

from servicenow_mcp.tools._payload import MAX_JSON_DEPTH, parse_payload_json


CID = "test-correlation-id"


def _decode_error(envelope: str) -> dict:
    """Decode a TOON error envelope and return the parsed dict."""
    return toon_decode(envelope)


def _error_message(envelope: str) -> str:
    """Pull the human-readable error message from an envelope.

    ``format_response`` wraps string errors as ``{"message": "..."}`` before
    serialization, so callers must reach into the nested ``message`` key.
    """
    decoded = _decode_error(envelope)
    err = decoded["error"]
    if isinstance(err, dict):
        return str(err.get("message", ""))
    return str(err)


class TestParsePayloadJsonHappyPath:
    """Successful parse returns the dict directly."""

    def test_returns_dict_for_valid_object(self) -> None:
        result = parse_payload_json(
            '{"name": "alice", "age": 30}',
            field_name="data",
            correlation_id=CID,
        )
        assert isinstance(result, dict)
        assert result == {"name": "alice", "age": 30}

    def test_empty_object_is_valid(self) -> None:
        result = parse_payload_json("{}", field_name="data", correlation_id=CID)
        assert result == {}

    def test_nested_dict_within_depth_limit_passes(self) -> None:
        result = parse_payload_json(
            '{"a": {"b": {"c": 1}}}',
            field_name="data",
            correlation_id=CID,
        )
        assert isinstance(result, dict)
        assert result["a"]["b"]["c"] == 1


class TestParsePayloadJsonErrors:
    """Failure paths return serialized error envelopes."""

    def test_oversize_input_returns_error_envelope(self) -> None:
        raw = '{"k": "' + ("x" * 1024) + '"}'
        result = parse_payload_json(
            raw,
            field_name="data",
            correlation_id=CID,
            max_bytes=64,
        )
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        assert "exceeds maximum size" in _error_message(result)
        assert decoded["correlation_id"] == CID

    def test_multibyte_payload_measured_in_utf8_bytes(self) -> None:
        """Size cap is enforced on UTF-8 byte length, not code-point count.

        A string of 70_000 emoji is only 70_000 code points but ~280_000 bytes,
        which must exceed a 256 KiB cap and yield an error envelope.
        """
        raw = '{"k": "' + ("\U0001f600" * 70_000) + '"}'
        # Sanity: well under the byte cap by len(), but well over by encode().
        cap = 256 * 1024
        assert len(raw) < cap
        assert len(raw.encode("utf-8")) > cap

        result = parse_payload_json(
            raw,
            field_name="data",
            correlation_id=CID,
            max_bytes=cap,
        )
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        assert "exceeds maximum size" in _error_message(result)

    def test_invalid_json_returns_error_envelope(self) -> None:
        result = parse_payload_json(
            "{not valid json",
            field_name="changes",
            correlation_id=CID,
        )
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        msg = _error_message(result)
        assert "is not valid JSON" in msg
        assert "changes" in msg

    def test_array_payload_rejected(self) -> None:
        result = parse_payload_json("[1, 2, 3]", field_name="data", correlation_id=CID)
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        assert "must be a JSON object" in _error_message(result)

    def test_string_payload_rejected(self) -> None:
        result = parse_payload_json('"hello"', field_name="data", correlation_id=CID)
        assert isinstance(result, str)
        assert "must be a JSON object" in _error_message(result)

    def test_number_payload_rejected(self) -> None:
        result = parse_payload_json("42", field_name="data", correlation_id=CID)
        assert isinstance(result, str)
        assert "must be a JSON object" in _error_message(result)

    def test_excessive_depth_returns_error_envelope(self) -> None:
        # Build a deeply nested object beyond MAX_JSON_DEPTH
        nested = "1"
        for _ in range(MAX_JSON_DEPTH + 5):
            nested = '{"a": ' + nested + "}"
        result = parse_payload_json(nested, field_name="data", correlation_id=CID)
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        assert "nesting depth" in _error_message(result)

    def test_invalid_identifier_key_rejected_when_validating(self) -> None:
        result = parse_payload_json(
            '{"bad-key!": "value"}',
            field_name="data",
            correlation_id=CID,
        )
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        assert "Invalid key in data" in _error_message(result)

    def test_validate_keys_false_skips_key_validation(self) -> None:
        result = parse_payload_json(
            '{"bad-key!": "value"}',
            field_name="data",
            correlation_id=CID,
            validate_keys=False,
        )
        assert isinstance(result, dict)
        assert result == {"bad-key!": "value"}

    def test_extreme_depth_short_circuits_without_recursion_error(self) -> None:
        """An adversarial deeply-nested payload returns a depth envelope, not RecursionError.

        Without short-circuiting, _depth recurses fully before the guard fires
        and Python raises RecursionError. The fix must surface a clean depth
        error envelope instead.
        """
        # ~2000 levels deep, well under the 256 KiB byte cap
        nested = '{"a":' * 2000 + "1" + "}" * 2000
        result = parse_payload_json(nested, field_name="data", correlation_id=CID)
        assert isinstance(result, str)
        decoded = _decode_error(result)
        assert decoded["status"] == "error"
        msg = _error_message(result)
        assert "nesting depth" in msg
        assert "recursion" not in msg.lower()
