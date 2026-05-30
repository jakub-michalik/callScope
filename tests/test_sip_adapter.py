"""SIP adapter pure-logic tests (Phase D) — no baresip/Asterisk needed."""
from sip_adapter import parse_netstrings, map_event, SipAdapter


def test_parse_netstrings_full_and_partial():
    payloads, rest = parse_netstrings(b'5:hello,3:abc,')
    assert payloads == [b"hello", b"abc"]
    assert rest == b""
    # a partial trailing netstring stays buffered
    payloads, rest = parse_netstrings(b'5:hel')
    assert payloads == []
    assert rest == b"5:hel"


def test_map_ringing():
    tr = map_event({"event": True, "type": "CALL_RINGING"})
    assert tr["state"] == "RINGING"
    assert tr["msgs"][0][:3] == ("in", "180", "180 Ringing")


def test_map_established_gives_200_and_ack():
    tr = map_event({"event": True, "type": "CALL_ESTABLISHED"})
    assert tr["state"] == "INCALL" and tr["media"] is True
    assert [m[1] for m in tr["msgs"]] == ["200", "ACK"]


def test_map_closed_normal_is_bye():
    tr = map_event({"event": True, "type": "CALL_CLOSED", "param": "Normal Clearing"})
    assert tr["state"] == "TERMINATED"
    assert tr["msgs"][0][:3] == ("out", "BYE", "BYE")


def test_map_closed_failure_code():
    tr = map_event({"event": True, "type": "CALL_CLOSED", "param": "486 Busy Here"})
    assert tr["state"] == "FAILED"
    assert tr["fail_code"] == "486"
    assert tr["msgs"][0][1] == "486"


def test_non_event_ignored():
    assert map_event({"response": True, "ok": True}) is None


def test_adapter_offline_is_unavailable_and_falls_back_cleanly():
    a = SipAdapter(baresip_bin="definitely-not-a-real-binary-xyz").start_stack()
    assert a.available is False
    assert a.error
    # still SipSession-compatible (no crash) — start/tick/conditions work
    a.start(0.0, "112")
    assert a.state == "CALLING"
    assert isinstance(a.tick(0.1), list)
    assert a.conditions(0.1) == []
