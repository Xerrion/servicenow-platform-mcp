"""A measurement failure cannot turn a completed save into UNKNOWN_OUTCOME."""

import pytest

from tests._flow_live_observer import FlowLiveObserver


def test_capture_retains_exact_distinct_request_echo_and_get_in_memory():
    observer = FlowLiveObserver()
    request, echo, fetched = (
        {"id": "synthetic", "value": "requested"},
        {"id": "synthetic", "value": "echo"},
        {"id": "synthetic", "value": "get"},
    )
    observer.capture("flow", request, echo)
    observer.capture("flow/synthetic", None, fetched)
    request["value"] = "later change"
    assert observer.exchanges[0][1] == {"id": "synthetic", "value": "requested"}
    assert observer.exchanges[0][2]["value"] == "echo"
    assert observer.exchanges[1][2]["value"] == "get"
    assert not observer.errors


@pytest.mark.parametrize("exception", [AssertionError, ValueError, OSError])
def test_observation_errors_do_not_escape_into_the_save_result(monkeypatch, exception):
    observer = FlowLiveObserver()

    def failed_copy(value):
        raise exception("synthetic observation failure")

    monkeypatch.setattr("tests._flow_live_observer.copy.deepcopy", failed_copy)
    result = {"id": "synthetic"}
    observer.capture("flow", {}, result)
    assert result == {"id": "synthetic"}
    assert observer.errors == [exception.__name__]
