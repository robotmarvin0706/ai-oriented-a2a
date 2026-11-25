"""ai_oriented_a2a.transport: client, request handler, and import boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"
for _p in (str(REPO), str(EXAMPLES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_oriented_a2a import reject_result, verify_message  # noqa: E402
from ai_oriented_a2a import transport  # noqa: E402
from ai_oriented_a2a.transport import (  # noqa: E402
    A2ARequestHandler,
    TransportError,
    guard_result,
    make_server,
    send_message,
)
import network_dual_end  # noqa: E402

KEY = "test-key"
SECRET_TEXT = "secret-detail-/etc/passwd-boom"


def _absorb(msg):
    return {
        "packet_ref": msg.get("id", "unknown"),
        "status": "absorbed",
        "effect_summary": {"note": "stored as hint"},
    }


@pytest.fixture
def server_url():
    servers = []

    def _start(on_message=_absorb, **kw):
        server = make_server(on_message, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()


def _post_raw(url, body: bytes, path="/a2a/message"):
    req = urllib.request.Request(url + path, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_send_message_returns_valid_absorption_result(server_url):
    pytest.importorskip("jsonschema")
    from ai_oriented_a2a import validate

    url = server_url()
    reply = send_message(url, network_dual_end.make_packet())
    validate(reply, "absorption-result")
    assert reply["status"] == "absorbed"


def test_signed_with_replay_window(server_url):
    url = server_url(sign_key=KEY, replay_window=300)
    reply = send_message(url, network_dual_end.make_packet(), sign_key=KEY, add_replay_fields=True)
    assert reply["status"] == "absorbed"
    assert verify_message(reply, KEY)


def test_replay_window_rejects_missing_fields_and_duplicates(server_url):
    url = server_url(replay_window=300)
    bare = {k: v for k, v in network_dual_end.make_packet().items() if k not in ("nonce", "timestamp")}
    reply = send_message(url, bare)
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("replay_or_stale:")
    msg = dict(bare, nonce="n-fixed", timestamp=int(time.time()))
    assert send_message(url, msg)["status"] == "absorbed"
    again = send_message(url, msg)
    assert again["reason"] == "replay_or_stale: duplicate nonce"


def test_leading_v_version_rejected_before_handler(server_url):
    called = []

    def on_message(msg):
        called.append(msg)
        return _absorb(msg)

    url = server_url(on_message)
    reply = send_message(url, dict(network_dual_end.make_packet(), version="v0.3"))
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("unsupported_version:")
    assert called == []


def test_bad_signature_rejected(server_url):
    url = server_url(sign_key=KEY)
    reply = send_message(url, network_dual_end.make_packet(), sign_key="wrong")
    assert reply["status"] == "rejected"
    assert reply["reason"] == "signature verification failed"


def test_invalid_json_is_400_with_absorption_result(server_url):
    url = server_url()
    status, body = _post_raw(url, b"{not json")
    assert status == 400
    reply = json.loads(body)
    assert reply == reject_result(None, "invalid json")
    assert reply["packet_ref"] == "unknown"


def test_other_path_is_404(server_url):
    url = server_url()
    status, _ = _post_raw(url, b"{}", path="/other")
    assert status == 404


def test_unreachable_host_raises_transport_error():
    with pytest.raises(TransportError):
        send_message("http://127.0.0.1:9", {"id": "x"}, timeout=2)


def test_send_message_options_are_keyword_only():
    with pytest.raises(TypeError):
        send_message("http://127.0.0.1:9", {}, 5)  # type: ignore[misc]


def test_guard_result():
    ok = _absorb({"id": "p1"})
    assert guard_result(ok) is ok
    no_note = {"packet_ref": "p1", "status": "absorbed", "effect_summary": {}}
    assert guard_result(no_note)["status"] == "rejected"
    assert guard_result(no_note)["reason"].startswith("hard-gate violation")
    reserved = {"packet_ref": "unknown", "status": "absorbed", "effect_summary": {"note": "x"}}
    assert guard_result(reserved)["status"] == "rejected"
    ack = {"ack": True, "type": "profile_ack"}
    assert guard_result(ack) is ack


def test_handler_absorbed_without_note_is_downgraded(server_url):
    url = server_url(lambda msg: {"packet_ref": msg["id"], "status": "absorbed"})
    reply = send_message(url, network_dual_end.make_packet())
    assert reply["status"] == "rejected"
    assert reply["reason"].startswith("hard-gate violation")


def test_handler_exception_is_500_internal_error_without_details(server_url):
    def boom(msg):
        raise RuntimeError(SECRET_TEXT)

    url = server_url(boom, sign_key=KEY)
    packet = network_dual_end.make_packet()
    status, body = _post_raw(url, json.dumps(network_dual_end.sign_message(packet, KEY)).encode())
    assert status == 500
    reply = json.loads(body)
    assert reply["status"] == "rejected"
    assert reply["packet_ref"] == packet["id"]
    assert reply["reason"] == transport.INTERNAL_ERROR_REASON
    assert reply["reason"].startswith("internal_error:")
    for leak in (SECRET_TEXT, "RuntimeError", "Traceback", ".py"):
        assert leak not in body.decode()
    assert verify_message(reply, KEY)
    # The client returns the 500 body like any reply.
    assert send_message(url, packet, sign_key=KEY)["reason"] == transport.INTERNAL_ERROR_REASON


def test_base_handler_without_override_is_500(server_url):
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (A2ARequestHandler,), {"seen_nonces": set()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        reply = send_message(url, network_dual_end.make_packet())
        assert reply["reason"] == transport.INTERNAL_ERROR_REASON
    finally:
        server.shutdown()
        server.server_close()


def test_make_handler_gives_each_server_its_own_replay_set():
    a = transport.make_handler(_absorb, replay_window=300)
    b = transport.make_handler(_absorb, replay_window=300)
    assert a.seen_nonces is not b.seen_nonces
    assert a.seen_nonces is not A2ARequestHandler.seen_nonces


def test_demo_handler_subclasses_package_handler():
    assert issubclass(network_dual_end.A2AHandler, A2ARequestHandler)
    assert "do_POST" not in vars(network_dual_end.A2AHandler)


def test_demo_positional_send_message_still_works(server_url):
    url = server_url(sign_key=KEY)
    reply = network_dual_end.send_message(url, network_dual_end.make_packet(), KEY)
    assert reply["status"] == "absorbed"
    assert verify_message(reply, KEY)


def test_top_level_import_does_not_load_http_server():
    code = (
        "import sys, ai_oriented_a2a; "
        "bad = [m for m in ('http.server', 'ai_oriented_a2a.transport') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == ""


def test_transport_not_reexported():
    import ai_oriented_a2a

    for name in ("send_message", "make_server", "serve", "A2ARequestHandler"):
        assert name not in ai_oriented_a2a.__all__


# ---------------------------------------------------------------------------
# 0.3.3: request body limit, wider 500 net, replay cache injection
# ---------------------------------------------------------------------------


def _raw_post(url, content_length, body=b"", timeout=5):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.putrequest("POST", transport.ENDPOINT)
        conn.putheader("Content-Type", "application/json")
        if content_length is not None:
            conn.putheader("Content-Length", content_length)
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode())
    finally:
        conn.close()


def test_max_body_bytes_constant_matches_second_impl():
    from second_impl import a2a_core as core

    assert transport.MAX_BODY_BYTES == 1_048_576 == core.MAX_BODY_BYTES
    assert "MAX_BODY_BYTES" in transport.__all__


@pytest.mark.parametrize(
    "length, reason",
    [
        (str(transport.MAX_BODY_BYTES + 1), transport.PAYLOAD_TOO_LARGE_REASON),
        (None, transport.LENGTH_REQUIRED_REASON),
        ("nope", transport.LENGTH_REQUIRED_REASON),
        ("-3", transport.LENGTH_REQUIRED_REASON),
        ("\u00b2", transport.LENGTH_REQUIRED_REASON),
    ],
    ids=["too-large", "missing", "garbage", "negative", "unicode-digit"],
)
def test_413_before_reading_body(server_url, length, reason):
    calls = []
    url = server_url(on_message=lambda m: calls.append(m) or _absorb(m), sign_key=KEY)
    # Only headers are sent: if the server tried to read the announced body
    # it would block until the client timeout.
    status, reply = _raw_post(url, length)
    assert status == 413
    assert reply["status"] == "rejected"
    assert reply["packet_ref"] == "unknown"
    assert reply["reason"] == reason
    assert reply["reason"].startswith("payload_too_large:")
    assert verify_message(reply, KEY)  # signed like any other reply
    assert calls == []


def test_content_length_parser():
    parse = transport._content_length
    assert parse("0") == 0
    assert parse(" 12 ") == 12
    for bad in (None, "", " ", "-1", "+1", "1.0", "0x10", "1e3", "\u0663"):
        assert parse(bad) is None, bad


def test_check_incoming_exception_is_500_without_leak(server_url, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError(SECRET_TEXT)

    monkeypatch.setattr(transport, "check_incoming", boom)
    url = server_url()
    packet = network_dual_end.make_packet()
    req = urllib.request.Request(
        url + transport.ENDPOINT,
        data=json.dumps(packet).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 500
    body = exc_info.value.read().decode()
    reply = json.loads(body)
    assert reply == {"packet_ref": packet["id"], "status": "rejected", "reason": transport.INTERNAL_ERROR_REASON}
    for leak in (SECRET_TEXT, "RuntimeError", "Traceback", ".py"):
        assert leak not in body


def test_nan_timestamp_over_http_is_rejected_not_crash(server_url):
    url = server_url(replay_window=300)
    packet = network_dual_end.make_packet()
    packet["timestamp"] = float("nan")
    reply = send_message(url, packet)
    assert reply["status"] == "rejected"
    assert reply["reason"] == "replay_or_stale: invalid timestamp"


def test_replay_cache_is_shared_per_class_not_per_request():
    from ai_oriented_a2a import ReplayCache

    assert A2ARequestHandler.seen_nonces is None  # no mutable default shared by subclasses
    handler = transport.make_handler(_absorb, replay_window=300)
    assert isinstance(handler.seen_nonces, ReplayCache)

    a = type("A", (A2ARequestHandler,), {})
    b = type("B", (A2ARequestHandler,), {})
    cache_a = A2ARequestHandler._replay_cache(a.__new__(a))
    cache_b = A2ARequestHandler._replay_cache(b.__new__(b))
    assert cache_a is not cache_b
    assert A2ARequestHandler._replay_cache(a.__new__(a)) is cache_a  # same cache for every request
    assert A2ARequestHandler.seen_nonces is None

    legacy = set()
    c = type("C", (A2ARequestHandler,), {"seen_nonces": legacy})
    cache_c = A2ARequestHandler._replay_cache(c.__new__(c))
    assert cache_c.seen is legacy  # a bare set is wrapped, not copied


def test_replay_window_shared_across_requests(server_url):
    url = server_url(replay_window=300)
    packet = dict(network_dual_end.make_packet())
    assert send_message(url, packet)["status"] == "absorbed"
    assert send_message(url, packet)["reason"] == "replay_or_stale: duplicate nonce"


def test_make_server_listen_backlog():
    server = make_server(_absorb)
    try:
        assert server.request_queue_size >= 64
        assert server.daemon_threads
    finally:
        server.server_close()


def test_demo_rejects_negotiation_instead_of_ack():
    propose = json.loads((EXAMPLES / "negotiation-propose.json").read_text())
    assert network_dual_end.negotiation_rejection(propose) == {
        "packet_ref": propose["offered_packet"]["id"],
        "status": "rejected",
        "reason": network_dual_end.NEGOTIATION_UNSUPPORTED_REASON,
    }
    assert network_dual_end.NEGOTIATION_UNSUPPORTED_REASON.startswith("negotiation_unsupported:")
    bare = {"proposal_id": "prop-1", "target_constraint": "x", "proposed_change": "y"}
    assert network_dual_end.negotiation_rejection(bare)["packet_ref"] == "unknown"
    blank = dict(bare, offered_packet={"id": "  "})
    assert network_dual_end.negotiation_rejection(blank)["packet_ref"] == "unknown"


@pytest.mark.parametrize("bad", [None, ["x"], "string", {"status": "bogus"}, {"ack": False}])
def test_callback_returning_a_non_result_is_a_500(server_url, bad):
    """TRANSPORT promises 500 + internal_error for a handler that returns
    something other than an Absorption Result or an ack (audit P1-11)."""
    url = server_url(on_message=lambda msg, value=bad: value)
    status, raw = _post_raw(url, json.dumps({"id": "p", "type": "failure_mode",
                                             "content": {"a": 1},
                                             "absorption": {"method": "retrieval"}}).encode())
    assert status == 500, raw
    body = json.loads(raw)
    assert body["status"] == "rejected"
    assert body["reason"].startswith("internal_error:")
    assert body["packet_ref"] == "p"


def test_a_legal_ack_still_goes_through(server_url):
    url = server_url(on_message=lambda msg: {"ack": True})
    status, raw = _post_raw(url, json.dumps({"id": "p", "type": "failure_mode",
                                             "content": {"a": 1},
                                             "absorption": {"method": "retrieval"}}).encode())
    assert status == 200 and json.loads(raw)["ack"] is True
